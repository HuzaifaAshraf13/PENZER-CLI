"""PENZER — ExecutionManager
Extracted from the monolithic agent.py. Methods here take an explicit
`agent` (the owning PenzerAgent) as their second parameter and read/write
its state directly — state ownership did not change, only where the
behavior lives. PenzerAgent keeps every original method name as a thin
delegate (e.g. `agent._transition(...)` still works), so nothing calling
the agent needs to change.
"""
import time, asyncio, inspect, json, re, hashlib, logging
from tools.plugins import create_plugin_tool, load_plugin_tools
from tools.executor import requires_privilege_escalation, SUDO_INTERACTIVE_TIMEOUT
from session.memory import get_skill_metric, kv_store, kv_get, kv_list, kv_delete

logger = logging.getLogger(__name__)

TOOL_LABELS = {
    "browser": "\U0001F310", "terminal": "\u26A1", "run_python": "\U0001F40D",
    "run_bash": "\U0001F4DC", "file_editor": "\U0001F4C1", "memory": "\U0001F9E0", "planning": "\U0001F4CB",
}
# file_editor deliberately has no fallback entry: its args (action/
# filepath/content) don't map onto any other tool's schema. The old
# "file_editor": "terminal" entry silently ran `terminal` with an empty
# command on every file_editor failure (see _run's fallback dispatch —
# it only knows how to extract "command"/"query"/"code" keys), wasting a
# tool call and polluting _trace/_consec_errors with a bogus result.
FALLBACKS = {
    "terminal": "run_bash", "run_bash": "run_python",
    "run_python": "terminal",
}
TOOL_TIMEOUT = 30
# Mirrors tools/terminal.py's `timeout: int = 60` default. Used as the
# fallback in _call_timeout when a terminal call omits `timeout` — using
# the generic TOOL_TIMEOUT (30) here would silently disagree with what
# the tool itself documents and actually passes to execute().
TOOL_TERMINAL_DEFAULT_TIMEOUT = 60

# Tools whose results depend on live, mutable state (filesystem, network,
# running processes) and must never be served from the per-run result
# cache in `_run` — a second identical call is very often intentional
# ("did the exploit land yet") rather than a redundant retry.
NON_IDEMPOTENT_TOOLS = {"terminal", "run_bash", "run_python", "browser"}

# Hard denylist checked before any command is allowed to graduate into an
# auto-created plugin (see _maybe_auto_create_plugin). This is a coarse
# safety net, not a substitute for real sandboxing/approval — it exists
# to stop the most obviously destructive/exfiltrating patterns from ever
# being persisted as a reusable, auto-loaded tool.
_DANGEROUS_PLUGIN_PATTERNS = re.compile(
    r"rm\s+-rf\s+/|:\(\)\{.*\};\s*:|curl[^|\n]*\|\s*(sh|bash)|wget[^|\n]*\|\s*(sh|bash)"
    r"|>\s*/dev/(sd|nvme|hd)|mkfs\.|dd\s+if=.*of=/dev/|chmod\s+-R\s+777\s+/"
    r"|/etc/(passwd|shadow)|nc\s+-l|curl\s+[^\n]*-d\s+[^\n]*@",
    re.IGNORECASE,
)


# Absolute ceiling on the terminal tool's own `timeout` argument, so a
# bad/malicious value (e.g. an LLM asked to justify an arbitrarily long
# wait) can't defeat the timeout mechanism entirely. Should stay well
# under MAX_RUNTIME_SECONDS so one call can't eat the whole run's time
# budget.
MAX_EXPLICIT_TOOL_TIMEOUT = 600

# Extra headroom given to the OUTER asyncio.wait_for beyond whatever
# timeout tools/executor.py's execute() is using internally. execute()
# needs a few seconds after ITS OWN timeout fires to run _kill_proc_group
# + proc.wait(timeout=5) and return cleanly — if the outer wrapper's
# deadline is identical to execute()'s, the outer one can win the race
# and cancel the asyncio.to_thread wrapper before execute() finishes its
# own cleanup, which is exactly the orphaned-process failure mode this
# margin exists to avoid.
TIMEOUT_MARGIN = 10


def _call_timeout(name: str, args: dict) -> int:
    """
    Per-call timeout for _execute_single_tool's asyncio.wait_for.

    This used to independently guess how long a `terminal` call should
    be allowed to run — first a flat 30s, then (briefly) a regex over
    the command text trying to spot "probably slow" command families.
    Both were wrong in the same way: they ignored the fact that
    `terminal()`'s own `timeout` parameter (tools/terminal.py) is
    already the authoritative value — it's what the caller explicitly
    asked for, and it's what actually gets passed to
    tools/executor.py's `execute(timeout=timeout, ...)`. Re-deriving a
    second, independent guess here meant this OUTER wait_for could
    (and did) fire before execute()'s own INNER timeout ever got a
    chance to run — at which point execute()'s careful cleanup
    (subprocess.TimeoutExpired handling, _kill_proc_group, process-group
    kill) never happens, because asyncio.to_thread can't forcibly stop
    the thread it's running in; the process is simply orphaned from the
    agent's point of view while cancellation returns immediately with a
    "Timeout after Ns" message that has nothing to do with the actual
    command's progress.

    Now: trust the caller's own `timeout` arg (clamped to a sane range),
    and give it a small margin so execute()'s own timeout fires and
    cleans up FIRST, with this outer wrapper only as a last-resort
    backstop in case that inner cleanup itself somehow hangs.

    Privilege escalation (sudo/su/pkexec/doas) still gets special
    handling: tools/executor.py's execute() runs a confirmed
    privilege-escalation command interactively (real password prompt,
    real terminal — see _run_privileged_interactive there) with its own
    SUDO_INTERACTIVE_TIMEOUT (300s), sized for "a human has to notice
    the prompt and type a password," not for a normal command — and
    that can be longer than whatever timeout the caller happened to
    pass (the caller has no way to know a command will end up needing
    sudo before it runs). The outer wait must be at least that long
    regardless of the caller's own value.
    """
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


class ExecutionManager:
    def _tool_confidence(self, agent, tool_name: str, args: dict) -> float:
        """
        Score 0.0-1.0 confidence that this tool will succeed.
        Factors: past success rate + belief state match + consecutive error penalty
        """
        score = 0.7
        consec = agent._consec_errors.get(tool_name, 0)
        score -= consec * 0.15
        if agent._belief["goal_progress"] != "blocked":
            score += 0.1
        key = f"{tool_name}:{json.dumps(args, sort_keys=True)}"
        if tool_name not in NON_IDEMPOTENT_TOOLS and key in agent._cache:
            score -= 0.3  # cached = already tried
        for skill in agent._skills_for_tool(tool_name):
            m = get_skill_metric(skill.name)
            score += m.get("success_rate", 0) * 0.1
        return round(min(1.0, max(0.0, score)), 6)

    async def _execute_single_tool(self, agent, call: dict) -> tuple[str, float]:
        """Runs exactly one tool call: memory short-circuit, availability
        check, status update, timeout wrapping. Shared by `_run_parallel`
        and `_run_race` so both stay in sync automatically.
        Catches unexpected exceptions (not just asyncio.TimeoutError) from
        the actual tool invocation and turns them into a reported error
        string instead of letting them propagate. Previously only timeouts
        were caught here — any other exception (a bug in a manager, a
        malformed argument causing a TypeError deep in an MCP tool, a disk
        error in a memory action) propagated straight up through
        asyncio.gather and could crash the entire run before it ever
        reached _finalize()."""
        name  = call["name"]
        args  = call.get("arguments", {})
        start = time.time()
        if name == "memory":
            agent._safe_status(f"🧠 {agent._fmt_action(name, args)}")
            try:
                raw = agent._run_memory_tool(args)
            except Exception as e:
                logger.exception("Memory tool error")
                raw = f"Error: {e}"
            return raw, round(time.time() - start, 2)
        # A call is valid if it's a registered MCP tool, the plugin_tool
        # creation action, or a dynamically created plugin (auto- or
        # explicitly-created). This used to check `agent.tools` (the MCP
        # registry) only — so a plugin tool could be created successfully
        # and still get rejected as "Unknown tool" the moment anything
        # tried to actually call it, since `_run()`'s own plugin-dispatch
        # branch (`if name in agent._plugin_tools`) was never reached.
        if name != "plugin_tool" and name not in agent._plugin_tools and name not in agent.tools:
            return f"Unknown tool '{name}'.", 0.0
        agent._safe_status(f"{TOOL_LABELS.get(name, name)} {agent._fmt_action(name, args)}")
        timeout = _call_timeout(name, args)
        try:
            raw = await asyncio.wait_for(agent._run(name, args), timeout=timeout)
        except asyncio.TimeoutError:
            raw = f"Timeout after {timeout}s"
        except Exception as e:
            logger.exception("Unhandled tool execution error: %s", name)
            raw = f"Error: {e}"
        return raw, round(time.time() - start, 2)

    async def _run_speculative(self, agent, calls: list) -> list[tuple[str, float]]:
        """
        For independent tool calls: race them and take the first success.
        For dependent calls (share file/env): run sequentially.
        Otherwise: run in parallel.

        A batch containing a privilege-escalation terminal call (sudo/su/
        pkexec/doas) never races, regardless of the checks below — it
        always goes through _run_parallel instead. _run_race cancels
        every still-pending task the instant any call in the batch
        succeeds; a sudo call sitting on a live human password prompt
        (or the interactive command itself, mid-execution) is exactly the
        kind of in-flight work that must never get cancelled out from
        under it just because an unrelated sibling call finished first.
        """
        if len(calls) <= 1:
            return await agent._run_parallel(calls)
        if any(
            c.get("name") == "terminal"
            and requires_privilege_escalation(str(
                (c.get("arguments") or {}).get("command")
                or (c.get("arguments") or {}).get("script")
                or (c.get("arguments") or {}).get("code")
                or ""
            ))[0]
            for c in calls
        ):
            return await agent._run_parallel(calls)
        def get_target(c):
            args = c.get("arguments", {})
            return args.get("filepath") or args.get("command", "")[:20]
        targets = [get_target(c) for c in calls]
        unique  = len(set(t for t in targets if t)) == len([t for t in targets if t])
        if unique and all(c["name"] in ("browser", "terminal", "run_bash") for c in calls):
            return await agent._run_race(calls)
        return await agent._run_parallel(calls)

    async def _run_race(self, agent, calls: list) -> list[tuple[str, float]]:
        """Launch all calls; return as soon as one succeeds, or once every
        call has finished — whichever comes first.
        Previously this only woke up on a success signal (`done_event`),
        so when every call in the race failed — which typically happens
        fast — the function still sat blocked until the full TOOL_TIMEOUT
        elapsed, even though every result was already known. Now it waits
        incrementally via asyncio.wait(FIRST_COMPLETED) and returns the
        moment either a success lands or nothing is left pending.
        """
        results = [("(cancelled)", 0.0)] * len(calls)
        async def run_and_report(idx: int, c: dict) -> bool:
            # _execute_single_tool already catches everything it
            # reasonably can, but this is defense-in-depth: if anything
            # still slips through, catching it here means t.result() in
            # the wait loop below never re-raises it — a raised exception
            # in one raced call previously could propagate straight out
            # of _run_race and crash the whole run.
            try:
                raw, elapsed = await agent._execute_single_tool(c)
            except Exception as e:
                logger.exception("Tool call raised inside _run_race: %s", c.get("name"))
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
                done, pending = await asyncio.wait(
                    pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
                )
                if any(t.result() for t in done):
                    break
        finally:
            for t in pending:
                idx, c = tasks[t]
                if not t.done() and c.get("name") in ("terminal", "run_bash", "run_python"):
                    logger.warning(
                        "Race lost a still-pending %s call — cancelling the asyncio "
                        "task, but the underlying subprocess (if already spawned) may "
                        "keep running unobserved; treat any of its side effects as "
                        "unverified. args=%s",
                        c.get("name"), {k: str(v)[:80] for k, v in (c.get("arguments") or {}).items()},
                    )
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        return results

    def _fallback_tool(self, agent, tool_name: str) -> str | None:
        return FALLBACKS.get(tool_name)

    async def _run_with_fallback(
        self, agent, call: dict, prior_result: tuple[str, float] | None = None
    ) -> tuple[str, float]:
        """Tries a fallback tool for a call that already failed.
        `prior_result` should be the (raw, elapsed) the caller already
        obtained for `call` — pass it whenever you have it, so this
        doesn't re-execute an already-failed tool a second time. That
        used to happen unconditionally: agent.py only calls this after
        already confirming `call` errored, yet this method re-ran it from
        scratch anyway. For a read-only tool that's wasted time; for
        `terminal` — the exact tool this fallback path exists to protect
        — it means re-running a shell command that already failed, which
        is a real problem for anything with a side effect (partial file
        write, network call, state mutation). If `prior_result` is
        omitted, falls back to running it once here.
        """
        name = call.get("name")
        if not name:
            return "No tool name provided", 0.0
        if prior_result is not None:
            raw, elapsed = prior_result
        else:
            results = await agent._run_parallel([call])
            raw, elapsed = results[0] if results else ("", 0.0)
        if raw and not agent._is_error(raw):
            return raw, elapsed
        fallback = agent._fallback_tool(name)
        if fallback and fallback in agent.tools:
            agent._record_step("tool_call", f"{name} failed — falling back to {fallback}",
                               tool=fallback, fallback_from=name)
            fb_call = {**call, "name": fallback}
            fb_results = await agent._run_parallel([fb_call])
            fb_raw, fb_elapsed = fb_results[0] if fb_results else ("", 0.0)
            return fb_raw, fb_elapsed
        return raw, elapsed

    async def _run_parallel(self, agent, calls: list) -> list[tuple[str, float]]:
        """asyncio.gather(..., return_exceptions=True) so one tool call
        raising doesn't crash its siblings or propagate past this
        boundary. _execute_single_tool already catches everything it
        reasonably can, but this is the last line of defense for
        anything that still slips through (e.g. `call["name"]` itself
        raising KeyError on a malformed call dict, before
        _execute_single_tool's own try/except even starts)."""
        results = await asyncio.gather(
            *[agent._execute_single_tool(c) for c in calls],
            return_exceptions=True,
        )
        out = []
        for c, r in zip(calls, results):
            if isinstance(r, Exception):
                logger.exception("Tool call raised outside _execute_single_tool's own guard: %s", c.get("name"))
                out.append((f"Error: {r}", 0.0))
            else:
                out.append(r)
        return out

    async def _run(self, agent, name: str, args: dict) -> str:
        tools = getattr(agent, "tools", {}) or {}
        if name == "memory" or tools.get(name) == "builtin":
            return agent._run_memory_tool(args)
        if name == "plugin_tool":
            return await agent._run_plugin_tool(args)
        if name in agent._plugin_tools:
            try:
                fn = agent._plugin_tools[name]
                # Plugin bodies (auto- or explicitly-created) are ordinary
                # synchronous functions — commonly subprocess.check_output
                # under the hood. Calling them directly here blocks the
                # entire event loop for as long as they run: the
                # asyncio.wait_for(timeout=...) wrapping this call in
                # _execute_single_tool can only fire at an await point, and
                # a synchronous call never yields one, so the "timeout"
                # never actually cancels anything — it just delays firing
                # until the blocking call finally returns on its own. Every
                # other coroutine sharing this loop (resource monitor,
                # checkpointing, sibling calls in the same race/parallel
                # batch) stalls for the same duration. Running it in a
                # worker thread doesn't make it truly cancellable, but it
                # stops one blocking tool from freezing everything else.
                if inspect.iscoroutinefunction(fn):
                    out = await fn(**args)
                else:
                    out = await asyncio.to_thread(fn, **args)
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
        for attempt in range(2):
            try:
                fn = getattr(tool, "fn", tool)
                if fn not in agent._fn_cache:
                    agent._fn_cache[fn] = (inspect.signature(fn), inspect.iscoroutinefunction(fn))
                sig, is_async = agent._fn_cache[fn]
                kw  = {k: v for k, v in args.items() if k in sig.parameters}
                # Same event-loop-blocking concern as the plugin branch
                # above applies to any registered MCP tool whose fn isn't
                # a coroutine function.
                out = await fn(**kw) if is_async else await asyncio.to_thread(fn, **kw)
                s = str(out)
                if cacheable:
                    agent._cache[key] = s
                return s
            except Exception as e:
                logger.debug("%s attempt %d: %s", name, attempt + 1, e)
                if attempt == 1:
                    fb = FALLBACKS.get(name)
                    cmd = args.get("command") or args.get("query") or args.get("code") or ""
                    if fb and fb in agent.tools and cmd:
                        agent._record_step("tool_call", f"{name} errored — falling back to {fb}",
                                           tool=fb, fallback_from=name)
                        return await agent._run(fb, {"command": cmd})
                    return f"Error: {e}"
        return ""

    def _run_memory_tool(self, agent, args: dict) -> str:
        """
        kv_store/kv_delete return confirmation strings (not None/bool),
        so the LLM sees something meaningful rather than the literal
        text "None" or "True".
        """
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

    async def _maybe_auto_create_plugin(self, agent) -> bool:
        """Reuse an existing plugin when possible; otherwise create one
        for a repeated terminal workflow.
        Runs under agent._plugin_lock: without it, two qualifying
        terminal calls executed concurrently (e.g. in the same
        _run_speculative/_run_parallel batch) could both pass the
        "recurring command, not yet in existing_tools" check before
        either has finished creating the plugin, and both attempt to
        write the same generated plugin file — a race with no defined
        winner. Holding the lock for the whole check-and-create section
        (not just the write) makes the second caller see the first
        caller's result and short-circuit on the existing-tools check.
        """
        async with agent._plugin_lock:
            if not getattr(agent, "_trace", None):
                return False
            repeated = [
                str((item.get("args") or {}).get("command", "")).strip()
                for item in agent._trace
                if item.get("tool") == "terminal" and item.get("success")
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
            # SECURITY: never let a command matching an obviously
            # destructive/exfiltrating pattern graduate into a persisted,
            # auto-loaded tool. This is a coarse net, not a substitute for
            # real review — auto-created plugins are still a meaningful
            # trust boundary (see the code-generation notes below) and
            # should ideally require human approval before being wired
            # into agent._plugin_tools at all. Flagging that as a
            # follow-up outside what this module alone can guarantee.
            if _DANGEROUS_PLUGIN_PATTERNS.search(command):
                logger.warning(
                    "Refusing to auto-create a plugin from a command matching "
                    "a denylisted pattern: %s", command[:120],
                )
                agent._record_step(
                    "plugin_blocked",
                    f"Declined to auto-create a plugin — command matched a "
                    f"denylisted pattern: {command[:80]}",
                )
                return False
            # A truncated 40-char slug alone isn't unique — two different
            # commands that share the same first 30ish characters would
            # collide, and the `if name in existing_tools: return True` below
            # would then silently report success for the WRONG command's
            # plugin. The digest suffix guarantees distinct commands get
            # distinct names.
            #
            # Uses a stable hash (md5), not Python's builtin hash(). str
            # hashing is randomized per-process by default (PYTHONHASHSEED),
            # so hash(command) produced a different suffix every restart —
            # the same recurring command would never match an existing
            # plugin name across process lifetimes, silently defeating the
            # "reuse an existing plugin when possible" behavior this method
            # claims and accumulating duplicate plugins for the same command.
            slug   = re.sub(r"[^a-z0-9]+", "_", command.lower()).strip("_") or "terminal_command"
            digest = hashlib.md5(command.encode()).hexdigest()[:8]
            name   = f"{slug[:30]}_{digest}"
            existing_tools = getattr(agent, "_plugin_tools", {}) or {}
            if name in existing_tools:
                return True
            description = f"Reusable helper for: {command[:80]}"
            # SECURITY FIX: previously generated `shell=True` with an
            # overridable `command` kwarg — i.e. a permanent, auto-loaded
            # tool that would shell-execute whatever string it was called
            # with, seeded from a (possibly attacker-influenced) command
            # that merely happened to run twice. Now: shell=False via
            # shlex.split, and the exact recurring command is frozen —
            # **kwargs is accepted for forward-compat call shape but
            # deliberately ignored, so there is no channel to widen what
            # this plugin actually executes after creation. If the
            # command can't be tokenized (e.g. it uses shell features like
            # pipes/redirects that genuinely require a shell), skip
            # plugin creation entirely rather than falling back to
            # shell=True.
            try:
                import shlex
                shlex.split(command)
            except ValueError:
                logger.info(
                    "Skipping plugin creation for a command that requires "
                    "shell features (pipes/redirects/quoting) rather than "
                    "widening to shell=True: %s", command[:120],
                )
                return False
            code = (
                "import shlex, subprocess\n\n"
                f"def {name}(**_ignored_kwargs):\n"
                f"    {description!r}\n"
                f"    return subprocess.check_output(shlex.split({command!r}), text=True)"
            )
            try:
                create_plugin_tool(name=name, description=description, code=code)
                agent._plugin_tools = load_plugin_tools()
                created = name in agent._plugin_tools
                if created:
                    agent._record_step(
                        "plugin_created",
                        f"Created reusable tool '{name}' from a repeated command: {command[:80]}",
                        tool=name,
                    )
                return created
            except Exception:
                return False

    def get_plugin_tool_descriptions(self, agent) -> dict[str, str]:
        """
        name -> description for every currently loaded plugin tool, read
        from each function's docstring (which `create_plugin_tool` calls
        set, and which `_maybe_auto_create_plugin` now embeds directly in
        the generated code). Used to make plugin tools visible in the
        system prompt — see the note on `list_plugin_tools` below.
        """
        out = {}
        for name, fn in (getattr(agent, "_plugin_tools", {}) or {}).items():
            doc = (getattr(fn, "__doc__", None) or "").strip()
            out[name] = doc or "(no description)"
        return out

    async def _run_plugin_tool(self, agent, args: dict) -> str:
        action = (args.get("action") or "").strip().lower()
        if action == "create":
            name = str(args.get("name", "")).strip()
            description = str(args.get("description", "")).strip()
            code = str(args.get("code", "")).strip()
            if not name or not code:
                return "Plugin creation requires a name and code"
            if _DANGEROUS_PLUGIN_PATTERNS.search(code):
                logger.warning("Refusing explicit plugin creation — code matched a denylisted pattern")
                return "Plugin creation declined — code matched a denylisted dangerous pattern"
            async with agent._plugin_lock:
                try:
                    result = create_plugin_tool(name=name, description=description or "Generated plugin", code=code)
                except Exception as exc:
                    return f"Plugin creation failed: {exc}"
                agent._plugin_tools = load_plugin_tools()
                tool_name = result.get("name", name)
                if tool_name in agent._plugin_tools:
                    agent._record_step(
                        "plugin_created",
                        f"Created reusable tool '{tool_name}': {description or 'Generated plugin'}",
                        tool=tool_name,
                    )
                    return f"Plugin created successfully: {tool_name}. It's now callable directly by name."
                return f"Plugin created but not yet available: {tool_name}"
        return "Unknown plugin action"

    def list_plugin_tools(self, agent) -> list[str]:
        """Return sorted available plugin tool names."""
        return sorted((getattr(agent, "_plugin_tools", {}) or {}).keys())