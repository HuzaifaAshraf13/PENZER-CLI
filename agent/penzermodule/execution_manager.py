"""PENZER — ExecutionManager

Extracted from the monolithic agent.py. Methods here take an explicit
`agent` (the owning PenzerAgent) as their second parameter and read/write
its state directly — state ownership did not change, only where the
behavior lives. PenzerAgent keeps every original method name as a thin
delegate (e.g. `agent._transition(...)` still works), so nothing calling
the agent needs to change.
"""

import time, asyncio, inspect, json, re, logging
from tools.plugins import create_plugin_tool, load_plugin_tools
from session.memory import get_skill_metric, kv_store, kv_get, kv_list, kv_delete

logger = logging.getLogger(__name__)

TOOL_LABELS = {
    "browser": "\U0001F310", "terminal": "\u26A1", "run_python": "\U0001F40D",
    "run_bash": "\U0001F4DC", "file_editor": "\U0001F4C1", "memory": "\U0001F9E0", "planning": "\U0001F4CB",
}
FALLBACKS = {
    "terminal": "run_bash", "run_bash": "run_python",
    "run_python": "terminal", "file_editor": "terminal",
}
TOOL_TIMEOUT = 30

from agent.penzermodule.belief_manager import Phase, PHASE_TRANSITIONS, PHASE_TO_GOAL_PROGRESS


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
        if key in agent._cache:
            score -= 0.3  # cached = already tried
        for skill in agent._skills_for_tool(tool_name):
            m = get_skill_metric(skill.name)
            score += m.get("success_rate", 0) * 0.1
        return round(min(1.0, max(0.0, score)), 6)

    async def _execute_single_tool(self, agent, call: dict) -> tuple[str, float]:
        """Runs exactly one tool call: memory short-circuit, availability
        check, status update, timeout wrapping. Shared by `_run_parallel`
        and `_run_race` so both stay in sync automatically."""
        name  = call["name"]
        args  = call.get("arguments", {})
        start = time.time()
        if name == "memory":
            agent.on_status(f"🧠 {agent._fmt_action(name, args)}")
            raw = agent._run_memory_tool(args)
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
        agent.on_status(f"{TOOL_LABELS.get(name, name)} {agent._fmt_action(name, args)}")
        try:
            raw = await asyncio.wait_for(agent._run(name, args), timeout=TOOL_TIMEOUT)
        except asyncio.TimeoutError:
            raw = f"Timeout after {TOOL_TIMEOUT}s"
        return raw, round(time.time() - start, 2)

    async def _run_speculative(self, agent, calls: list) -> list[tuple[str, float]]:
        """
        For independent tool calls: race them and take the first success.
        For dependent calls (share file/env): run sequentially.
        Otherwise: run in parallel.
        """
        if len(calls) <= 1:
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
        """Launch all calls, cancel losers when first succeeds."""
        results = [("(cancelled)", 0.0)] * len(calls)

        async def run_and_report(idx: int, c: dict, done_event: asyncio.Event):
            raw, elapsed = await agent._execute_single_tool(c)
            results[idx] = (raw, elapsed)
            if not agent._is_error(raw):
                done_event.set()

        done  = asyncio.Event()
        tasks = [asyncio.create_task(run_and_report(i, c, done)) for i, c in enumerate(calls)]
        try:
            await asyncio.wait_for(done.wait(), timeout=TOOL_TIMEOUT)
        except asyncio.TimeoutError:
            pass
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        return results

    def _fallback_tool(self, agent, tool_name: str) -> str | None:
        return FALLBACKS.get(tool_name)

    async def _run_with_fallback(self, agent, call: dict) -> tuple[str, float]:
        name = call.get("name")
        if not name:
            return "No tool name provided", 0.0
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
        return list(await asyncio.gather(*[agent._execute_single_tool(c) for c in calls]))

    async def _run(self, agent, name: str, args: dict) -> str:
        tools = getattr(agent, "tools", {}) or {}
        if name == "memory" or tools.get(name) == "builtin":
            return agent._run_memory_tool(args)
        if name == "plugin_tool":
            return agent._run_plugin_tool(args)
        if name in agent._plugin_tools:
            try:
                return str(agent._plugin_tools[name](**args))
            except Exception as e:
                return f"Plugin error: {e}"

        key = f"{name}:{json.dumps(args, sort_keys=True)}"
        if key in agent._cache:
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
                out = await fn(**kw) if is_async else fn(**kw)
                agent._cache[key] = s = str(out)
                return s
            except Exception as e:
                logger.debug("%s attempt %d: %s", name, attempt + 1, e)
                if attempt == 1:
                    fb = FALLBACKS.get(name)
                    if fb and fb in agent.tools:
                        agent._record_step("tool_call", f"{name} errored — falling back to {fb}",
                                           tool=fb, fallback_from=name)
                        cmd = args.get("command") or args.get("query") or args.get("code") or ""
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
            return handler()
        return f"Unknown memory action '{action}'. Use: get, store, list, delete"

    def _maybe_auto_create_plugin(self, agent) -> bool:
        """Reuse an existing plugin when possible; otherwise create one for a repeated terminal workflow."""
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
        # A truncated 40-char slug alone isn't unique — two different
        # commands that share the same first 30ish characters would
        # collide, and the `if name in existing_tools: return True` below
        # would then silently report success for the WRONG command's
        # plugin. The hash suffix guarantees distinct commands get
        # distinct names.
        slug = re.sub(r"[^a-z0-9]+", "_", command.lower()).strip("_") or "terminal_command"
        name = f"{slug[:30]}_{abs(hash(command)) % 10000}"
        existing_tools = getattr(agent, "_plugin_tools", {}) or {}
        if name in existing_tools:
            return True
        description = f"Reusable helper for: {command[:80]}"
        # `command` is a default arg, not hardcoded into the call, so the
        # LLM can override it later with a similar-but-different command
        # via `command=...` instead of getting a frozen one-off replay of
        # the exact string that happened to succeed twice.
        code = (
            "import subprocess\n\n"
            f"def {name}(command: str = {command!r}, **kwargs):\n"
            f"    {description!r}\n"
            f"    return subprocess.check_output(command, shell=True, text=True)"
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

    def _run_plugin_tool(self, agent, args: dict) -> str:
        action = (args.get("action") or "").strip().lower()
        if action == "create":
            name = str(args.get("name", "")).strip()
            description = str(args.get("description", "")).strip()
            code = str(args.get("code", "")).strip()
            if not name or not code:
                return "Plugin creation requires a name and code"
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