"""PENZER — tool execution: confidence scoring, speculative/parallel/race
dispatch, plugin tools. Plain functions taking `agent` as first arg — no
manager class, no self.execution indirection. Kept in its own file
because it's a genuinely separate concern (how a tool call physically
runs) from the loop (when/why a tool call happens), not because it needs
its own abstraction layer.
"""
import time, asyncio, inspect, json, re, hashlib, logging, shlex

from tools.plugins import create_plugin_tool, load_plugin_tools, validate_plugin_source
from tools.executor import confirm_action
from tools.executor import requires_privilege_escalation, SUDO_INTERACTIVE_TIMEOUT
from session.memory import get_skill_metric, kv_store, kv_get, kv_list, kv_delete
from agent.activity_timeline import emit_activity_event, update_activity_event

logger = logging.getLogger(__name__)

TOOL_LABELS = {
    "browser": "\U0001F310", "terminal": "\u26A1", "run_python": "\U0001F40D",
    "run_bash": "\U0001F4DC", "file_editor": "\U0001F4C1", "memory": "\U0001F9E0", "planning": "\U0001F4CB",
}

# file_editor has no fallback: its args don't map onto any other tool's schema.
FALLBACKS = {"terminal": "run_bash", "run_bash": "run_python", "run_python": "terminal"}

TOOL_TIMEOUT = 30
TOOL_TERMINAL_DEFAULT_TIMEOUT = 60  # mirrors tools/terminal.py's default
PLUGIN_SUBPROCESS_TIMEOUT = TOOL_TERMINAL_DEFAULT_TIMEOUT
NON_IDEMPOTENT_TOOLS = {"terminal", "run_bash", "run_python", "browser"}
MAX_EXPLICIT_TOOL_TIMEOUT = 600
TIMEOUT_MARGIN = 10  # headroom beyond executor.py's own internal timeout/cleanup

_DANGEROUS_PLUGIN_PATTERNS = re.compile(
    r"rm\s+-rf\s+/|:\(\)\{.*\};\s*:|curl[^|\n]*\|\s*(sh|bash)|wget[^|\n]*\|\s*(sh|bash)"
    r"|>\s*/dev/(sd|nvme|hd)|mkfs\.|dd\s+if=.*of=/dev/|chmod\s+-R\s+777\s+/"
    r"|/etc/(passwd|shadow)|nc\s+-l|curl\s+[^\n]*-d\s+[^\n]*@",
    re.IGNORECASE,
)


def _call_timeout(name: str, args: dict) -> int:
    """Trusts the caller's own `timeout` arg (clamped), with a small
    margin so the tool's own internal cleanup fires first. Sudo/su/pkexec
    calls get a floor of SUDO_INTERACTIVE_TIMEOUT regardless of the
    caller's value, since they wait on a real password prompt."""
    if name != "terminal":
        return TOOL_TIMEOUT
    requested = args.get("timeout", TOOL_TERMINAL_DEFAULT_TIMEOUT)
    try:
        requested = int(requested)
    except (TypeError, ValueError):
        requested = TOOL_TERMINAL_DEFAULT_TIMEOUT
    requested = max(1, min(MAX_EXPLICIT_TOOL_TIMEOUT, requested))
    cmd_text = str(args.get("command") or args.get("script") or args.get("code") or "")
    escalates, _ = requires_privilege_escalation(cmd_text)
    if escalates:
        return max(requested, SUDO_INTERACTIVE_TIMEOUT + 15) + TIMEOUT_MARGIN
    return requested + TIMEOUT_MARGIN


def tool_confidence(agent, tool_name: str, args: dict) -> float:
    """0.0-1.0 confidence a tool call will succeed: past success rate +
    belief-state match - consecutive-error penalty."""
    score = 0.7
    score -= agent._consec_errors.get(tool_name, 0) * 0.15
    if agent._belief["goal_progress"] != "blocked":
        score += 0.1
    key = f"{tool_name}:{json.dumps(args, sort_keys=True)}"
    if tool_name not in NON_IDEMPOTENT_TOOLS and key in agent._cache:
        score -= 0.3
    for skill in agent._skills_for_tool(tool_name):
        score += get_skill_metric(skill.name).get("success_rate", 0) * 0.1
    return round(min(1.0, max(0.0, score)), 6)


async def execute_single_tool(agent, call: dict) -> tuple[str, float]:
    """Runs exactly one tool call: memory short-circuit, availability
    check, status update, timeout wrapping. Catches unexpected exceptions
    (not just timeouts) and turns them into a reported error string
    instead of letting them crash the run."""
    name  = call["name"]
    args  = call.get("arguments", {})
    start = time.time()
    if name == "memory":
        agent._safe_status(f"\U0001F9E0 {agent._fmt_action(name, args)}")
        activity_id = agent._emit_activity(
            "memory", "Memory activity", message=agent._fmt_action(name, args),
            status="running", details={"tool": name, "args": args},
        )
        try:
            raw = run_memory_tool(agent, args)
        except Exception as e:
            logger.exception("Memory tool error")
            raw = f"Error: {e}"
        if activity_id:
            agent._update_activity(activity_id, status="success" if not agent._is_error(raw) else "failed",
                                    message=str(raw)[:160], details={"tool": name, "result": str(raw)[:400]})
        return raw, round(time.time() - start, 2)
    # Valid if it's a registered MCP tool, the plugin_tool creation
    # action, or a dynamically created plugin.
    if name != "plugin_tool" and name not in agent._plugin_tools and name not in agent.tools:
        return f"Unknown tool '{name}'.", 0.0
    agent._safe_status(f"{TOOL_LABELS.get(name, name)} {agent._fmt_action(name, args)}")
    event_type = "plugin" if name == "plugin_tool" or name in agent._plugin_tools else "tool"
    activity_id = agent._emit_activity(
        event_type, f"{name} activity", message=agent._fmt_action(name, args),
        status="running", details={"tool": name, "args": args},
    )
    timeout = _call_timeout(name, args)
    try:
        raw = await asyncio.wait_for(run(agent, name, args), timeout=timeout)
    except asyncio.TimeoutError:
        raw = f"Timeout after {timeout}s"
    except Exception as e:
        logger.exception("Unhandled tool execution error: %s", name)
        raw = f"Error: {e}"
    if activity_id:
        agent._update_activity(activity_id, status="success" if not agent._is_error(raw) else "failed",
                                message=str(raw)[:160], details={"tool": name, "result": str(raw)[:400]})
    return raw, round(time.time() - start, 2)


async def run_speculative(agent, calls: list) -> list[tuple[str, float]]:
    """Keep the execution path deliberately simple: when a batch is
    intentionally parallel, execute it in parallel without hidden
    "first-success" race heuristics. The agent loop decides which answer
    to trust; this layer stays a transport boundary.

    We intentionally avoid racing browser/terminal-like calls because it
    adds opaque cancellation, extra scheduling complexity, and makes the
    loop harder to reason about than the gains justify.
    """
    if len(calls) <= 1:
        return await run_parallel(agent, calls)
    if any(
        c.get("name") == "terminal" and requires_privilege_escalation(str(
            (c.get("arguments") or {}).get("command")
            or (c.get("arguments") or {}).get("script")
            or (c.get("arguments") or {}).get("code") or ""
        ))[0]
        for c in calls
    ):
        return await run_parallel(agent, calls)
    return await run_parallel(agent, calls)


async def run_race(agent, calls: list) -> list[tuple[str, float]]:
    """Launch all calls; return as soon as one succeeds, or once every
    call has finished. Waits incrementally (not for the full timeout) so
    an all-fail batch returns as soon as that's known."""
    results = [("(cancelled)", 0.0)] * len(calls)

    async def run_and_report(idx: int, c: dict) -> bool:
        try:
            raw, elapsed = await execute_single_tool(agent, c)
        except Exception as e:
            logger.exception("Tool call raised inside run_race: %s", c.get("name"))
            raw, elapsed = f"Error: {e}", 0.0
        results[idx] = (raw, elapsed)
        return not agent._is_error(raw)

    tasks    = {asyncio.create_task(run_and_report(i, c)): (i, c) for i, c in enumerate(calls)}
    pending  = set(tasks)
    deadline = time.time() + max(_call_timeout(c["name"], c.get("arguments", {})) for c in calls)
    try:
        while pending:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            done, pending = await asyncio.wait(pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
            if any(t.result() for t in done):
                break
    finally:
        for t in pending:
            idx, c = tasks[t]
            if not t.done() and c.get("name") in ("terminal", "run_bash", "run_python"):
                logger.warning(
                    "Race lost a still-pending %s call — cancelling the task, but "
                    "an already-spawned subprocess may keep running unobserved.",
                    c.get("name"),
                )
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    return results


def fallback_tool(agent, tool_name: str) -> str | None:
    return FALLBACKS.get(tool_name)


async def run_with_fallback(agent, call: dict, prior_result: tuple[str, float] | None = None) -> tuple[str, float]:
    """Tries a fallback tool for a call that already failed. Pass
    `prior_result` when you already have it, so a non-idempotent call
    (e.g. terminal) isn't re-executed a second time."""
    name = call.get("name")
    if not name:
        return "No tool name provided", 0.0
    if prior_result is not None:
        raw, elapsed = prior_result
    else:
        results = await run_parallel(agent, [call])
        raw, elapsed = results[0] if results else ("", 0.0)
    if raw and not agent._is_error(raw):
        return raw, elapsed
    fb = fallback_tool(agent, name)
    if fb and fb in agent.tools:
        agent._record_step("tool_call", f"{name} failed — falling back to {fb}", tool=fb, fallback_from=name)
        fb_results = await run_parallel(agent, [{**call, "name": fb}])
        return fb_results[0] if fb_results else (raw, elapsed)
    return raw, elapsed


async def run_parallel(agent, calls: list) -> list[tuple[str, float]]:
    """gather(..., return_exceptions=True) so one call raising doesn't
    crash its siblings or propagate past this boundary."""
    results = await asyncio.gather(*[execute_single_tool(agent, c) for c in calls], return_exceptions=True)
    out = []
    for c, r in zip(calls, results):
        if isinstance(r, Exception):
            logger.exception("Tool call raised outside execute_single_tool's guard: %s", c.get("name"))
            out.append((f"Error: {r}", 0.0))
        else:
            out.append(r)
    return out


async def run(agent, name: str, args: dict) -> str:
    tools = getattr(agent, "tools", {}) or {}
    if name == "memory" or tools.get(name) == "builtin":
        return run_memory_tool(agent, args)
    if name == "plugin_tool":
        return await run_plugin_tool(agent, args)
    if name in agent._plugin_tools:
        try:
            fn = agent._plugin_tools[name]
            # Plugin bodies are ordinary sync functions (usually
            # subprocess.check_output) — run in a worker thread so one
            # blocking plugin can't freeze the whole event loop.
            out = await fn(**args) if inspect.iscoroutinefunction(fn) else await asyncio.to_thread(fn, **args)
            return str(out)
        except Exception as e:
            return f"Plugin error: {e}"
    cacheable = name not in NON_IDEMPOTENT_TOOLS
    key = f"{name}:{json.dumps(args, sort_keys=True)}"
    if cacheable and key in agent._cache:
        return agent._cache[key]
    tool = agent.tools.get(name)
    if not tool:
        return f"Tool '{name}' not available"
    # Non-idempotent tools get exactly one attempt — a retry after an
    # exception risks re-running a command whose side effect already
    # landed before the exception surfaced.
    max_attempts = 1 if name in NON_IDEMPOTENT_TOOLS else 2
    for attempt in range(max_attempts):
        try:
            fn = getattr(tool, "fn", tool)
            if fn not in agent._fn_cache:
                agent._fn_cache[fn] = (inspect.signature(fn), inspect.iscoroutinefunction(fn))
            sig, is_async = agent._fn_cache[fn]
            kw = {k: v for k, v in args.items() if k in sig.parameters}
            out = await fn(**kw) if is_async else await asyncio.to_thread(fn, **kw)
            s = str(out)
            if cacheable:
                agent._cache[key] = s
            return s
        except Exception as e:
            logger.debug("%s attempt %d: %s", name, attempt + 1, e)
            if attempt == max_attempts - 1:
                fb  = FALLBACKS.get(name)
                cmd = args.get("command") or args.get("query") or args.get("code") or ""
                if fb and fb in agent.tools and cmd:
                    agent._record_step("tool_call", f"{name} errored — falling back to {fb}",
                                        tool=fb, fallback_from=name)
                    return await run(agent, fb, {"command": cmd})
                return f"Error: {e}"
    return ""


def run_memory_tool(agent, args: dict) -> str:
    action = args.get("action", "")
    key    = args.get("key", "")
    value  = args.get("value", "")
    handlers = {
        "get":    lambda: str(kv_get(key)),
        "store":  lambda: kv_store(key, value),
        "list":   lambda: json.dumps(kv_list()),
        "delete": lambda: kv_delete(key),
    }
    handler = handlers.get(action)
    if handler:
        try:
            return handler()
        except Exception as e:
            logger.exception("Memory action '%s' failed", action)
            return f"Error: {e}"
    return f"Unknown memory action '{action}'. Use: get, store, list, delete"


async def maybe_auto_create_plugin(agent) -> bool:
    """Reuse an existing plugin when possible; otherwise create one for a
    terminal command that's recurred 2+ times. Runs under
    agent._plugin_lock to avoid a create-create race on concurrent calls."""
    async with agent._plugin_lock:
        if not getattr(agent, "_trace", None):
            return False
        repeated = [
            str((item.get("args") or {}).get("command", "")).strip()
            for item in agent._trace if item.get("tool") == "terminal" and item.get("success")
        ]
        repeated = [c for c in repeated if c]
        if len(repeated) < 2:
            return False
        counts = {}
        for command in repeated:
            counts[command] = counts.get(command, 0) + 1
        recurring = [cmd for cmd, count in counts.items() if count >= 2]
        if not recurring:
            return False
        command = recurring[0]
        if _DANGEROUS_PLUGIN_PATTERNS.search(command):
            logger.warning("Refusing to auto-create a plugin from a denylisted command: %s", command[:120])
            agent._record_step("plugin_blocked",
                f"Declined to auto-create a plugin — command matched a denylisted pattern: {command[:80]}")
            return False
        slug   = re.sub(r"[^a-z0-9]+", "_", command.lower()).strip("_") or "terminal_command"
        digest = hashlib.md5(command.encode()).hexdigest()[:8]  # stable across restarts, unlike hash()
        name   = f"{slug[:30]}_{digest}"
        existing_tools = getattr(agent, "_plugin_tools", {}) or {}
        if name in existing_tools:
            return True
        description = f"Reusable helper for: {command[:80]}"
        try:
            shlex.split(command)
        except ValueError:
            logger.info("Skipping plugin creation — command needs shell features (pipes/redirects): %s", command[:120])
            return False
        # shell=False via shlex.split, command frozen at creation time —
        # no channel to widen what this auto-loaded tool executes later.
        code = (
            "import shlex, subprocess\n\n"
            f"def {name}(**_ignored_kwargs):\n"
            f"    {description!r}\n"
            "    try:\n"
            f"        return subprocess.check_output(\n"
            f"            shlex.split({command!r}), text=True,\n"
            f"            timeout={PLUGIN_SUBPROCESS_TIMEOUT}\n"
            "        )\n"
            "    except subprocess.TimeoutExpired:\n"
            f"        return 'Timed out after {PLUGIN_SUBPROCESS_TIMEOUT}s'\n"
        )
        try:
            create_plugin_tool(name=name, description=description, code=code)
            agent._plugin_tools = load_plugin_tools()
            created = name in agent._plugin_tools
            if created:
                agent._record_step("plugin_created", f"Created reusable tool '{name}' from a repeated command: {command[:80]}", tool=name)
            return created
        except Exception:
            return False


def get_plugin_tool_descriptions(agent) -> dict[str, str]:
    out = {}
    for name, fn in (getattr(agent, "_plugin_tools", {}) or {}).items():
        doc = (getattr(fn, "__doc__", None) or "").strip()
        out[name] = doc or "(no description)"
    return out


async def run_plugin_tool(agent, args: dict) -> str:
    action = (args.get("action") or "").strip().lower()
    if action != "create":
        return "Unknown plugin action"
    name = str(args.get("name", "")).strip()
    description = str(args.get("description", "")).strip()
    code = str(args.get("code", "")).strip()
    if not name or not code:
        return "Plugin creation requires a name and code"
    if _DANGEROUS_PLUGIN_PATTERNS.search(code):
        logger.warning("Refusing explicit plugin creation — code matched a denylisted pattern")
        return "Plugin creation declined — code matched a denylisted dangerous pattern"
    try:
        validate_plugin_source(code)
    except ValueError as exc:
        return f"Plugin creation declined — {exc}"
    if not await asyncio.to_thread(
        confirm_action,
        f"create plugin '{name}' from model-generated Python code",
        "A model-generated plugin will be written to disk and imported.",
        timeout=120,
    ):
        return "Plugin creation cancelled by user."
    activity_id = emit_activity_event("plugin", "Plugin creation", message=f"Creating plugin '{name}'",
                                       status="running", details={"name": name, "description": description})
    async with agent._plugin_lock:
        try:
            result = create_plugin_tool(name=name, description=description or "Generated plugin", code=code)
        except Exception as exc:
            if activity_id:
                update_activity_event(activity_id, status="failed", message=str(exc))
            return f"Plugin creation failed: {exc}"
        agent._plugin_tools = load_plugin_tools()
        tool_name = result.get("name", name)
        if tool_name in agent._plugin_tools:
            if activity_id:
                update_activity_event(activity_id, status="success", message=f"Plugin created successfully: {tool_name}", details={"name": tool_name})
            agent._record_step("plugin_created", f"Created reusable tool '{tool_name}': {description or 'Generated plugin'}", tool=tool_name)
            return f"Plugin created successfully: {tool_name}. It's now callable directly by name."
        if activity_id:
            update_activity_event(activity_id, status="warning", message=f"Plugin created but not available: {tool_name}")
        return f"Plugin created but not yet available: {tool_name}"


def list_plugin_tools(agent) -> list[str]:
    return sorted((getattr(agent, "_plugin_tools", {}) or {}).keys())