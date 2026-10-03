"""Reproducible Penzer agent-loop evaluations.

Replay mode exercises the real loop with scripted model turns and mocked tools.
Live mode is explicit and runs only the read-only tasks declared below.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import re
import sys
import time
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from agent.agent import PenzerAgent
import agent.agent as agent_module
from agent.penzermodule import execution


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    goal: str
    expected_tools: tuple[str, ...]
    expected_answer_terms: tuple[str, ...]
    evidence_terms: tuple[str, ...]
    max_tool_calls: int
    max_iterations: int
    turns: tuple[dict[str, Any], ...] = ()
    tool_results: tuple[str, ...] = ()
    live: bool = False


CASES = (
    EvalCase(
        case_id="browser-grounded-answer",
        goal="Search NASA Artemis II official updates and summarize the result.",
        expected_tools=("browser",),
        expected_answer_terms=("artemis ii", "nasa.gov/artemis-ii-news-and-updates"),
        evidence_terms=("artemis ii", "nasa.gov/artemis-ii-news-and-updates"),
        max_tool_calls=3,
        max_iterations=4,
        turns=(
            {"tool_calls": [{"id": "search-1", "name": "browser", "arguments": {"action": "search", "query": "NASA Artemis II official updates"}}]},
            {"content": "NASA's Artemis II updates are available at https://www.nasa.gov/artemis-ii-news-and-updates/.", "tool_calls": []},
        ),
        tool_results=(
            json.dumps({
                "status": "success",
                "message": "Searched the web",
                "data": {
                    "results": [{
                        "title": "Artemis II News and Updates - NASA",
                        "url": "https://www.nasa.gov/artemis-ii-news-and-updates/",
                        "snippet": "Latest NASA Artemis II mission updates.",
                    }],
                    "content": "Latest NASA Artemis II mission updates.",
                },
            }),
        ),
        live=True,
    ),
    EvalCase(
        case_id="terminal-grounded-answer",
        goal="Use the terminal tool to inspect a short local diagnostic and report its status.",
        expected_tools=("terminal",),
        expected_answer_terms=("diagnostic", "green"),
        evidence_terms=("diagnostic", "green"),
        max_tool_calls=1,
        max_iterations=2,
        turns=(
            {"tool_calls": [{"id": "terminal-1", "name": "terminal", "arguments": {"command": "printf 'Penzer diagnostic: green'"}}]},
            {"content": "The Penzer diagnostic is green.", "tool_calls": []},
        ),
        tool_results=(json.dumps({
            "status": "success",
            "data": {"stdout": "Penzer diagnostic: green", "exit_code": 0},
        }),),
    ),
    EvalCase(
        case_id="browser-no-results-stop",
        goal="Search for a source about a fictional object named QZX-991 and report if none is found.",
        expected_tools=("browser", "browser"),
        expected_answer_terms=("no relevant results", "twice"),
        evidence_terms=("no relevant search results found",),
        max_tool_calls=2,
        max_iterations=2,
        turns=(
            {"tool_calls": [{"id": "search-1", "name": "browser", "arguments": {"action": "search", "query": "QZX-991"}}]},
            {"tool_calls": [{"id": "search-2", "name": "browser", "arguments": {"action": "search", "query": "QZX-991"}}]},
        ),
        tool_results=(
            json.dumps({"status": "error", "message": "No relevant search results found", "data": {"no_relevant_results": True}}),
            json.dumps({"status": "error", "message": "No relevant search results found", "data": {"no_relevant_results": True}}),
        ),
    ),
)


class _ReplayTransport:
    async def close(self) -> None:
        return None


class _ReplayLLM:
    def __init__(self) -> None:
        self.token_estimate = 0
        self.call_count = 0
        self.model = _ReplayTransport()


def _case_metrics(agent: PenzerAgent, case: EvalCase, answer: str, llm_calls: int, mode: str) -> dict[str, Any]:
    trace = agent._trace
    observed_tools = [str(item.get("tool", "")) for item in trace]
    observed_evidence = " ".join(str(item.get("result", "")) for item in trace).lower()
    answer_lower = (answer or "").lower()
    expected_terms_present = all(term.lower() in answer_lower for term in case.expected_answer_terms)
    evidence_grounded = all(term.lower() in observed_evidence for term in case.evidence_terms)
    citations = re.findall(r"https?://[^\s)\]>]+", answer or "")
    citations_supported = all(url.rstrip(".,") in observed_evidence for url in citations)
    expected_tools_present = all(tool in observed_tools for tool in case.expected_tools)
    constraints_met = (
        agent._done
        and
        expected_terms_present
        and evidence_grounded
        and citations_supported
        and expected_tools_present
        and len(trace) <= case.max_tool_calls
        and agent._iteration + 1 <= case.max_iterations
        and not agent._failed
    )
    return {
        "id": case.case_id,
        "mode": mode,
        "passed": constraints_met,
        "answer": answer,
        "done": agent._done,
        "failed": agent._failed,
        "completion_status": "failed" if agent._failed else "done" if agent._done else "incomplete",
        "active_skills": list(agent._matched_skills),
        "iterations": agent._iteration + 1,
        "llm_calls": llm_calls,
        "tool_calls": len(trace),
        "tool_sequence": observed_tools,
        "tool_successes": sum(bool(item.get("success")) for item in trace),
        "tool_failures": sum(not bool(item.get("success")) for item in trace),
        "tokens": max(0, int(getattr(agent.llm, "token_estimate", 0) - agent._tokens_before_run)),
        "elapsed_seconds": round(sum(float(item.get("elapsed_sec", 0.0)) for item in trace), 3),
        "expected_terms_present": expected_terms_present,
        "evidence_grounded": evidence_grounded,
        "citations_supported": citations_supported,
        "expected_tools_present": expected_tools_present,
        "within_budgets": len(trace) <= case.max_tool_calls and agent._iteration + 1 <= case.max_iterations,
    }


async def _run_replay(case: EvalCase) -> dict[str, Any]:
    with patch.object(agent_module, "LLM", _ReplayLLM):
        agent = PenzerAgent()
    turn_index = 0
    result_index = 0
    llm_calls = 0
    start_tokens = int(getattr(agent.llm, "token_estimate", 0))
    agent.llm.token_estimate = start_tokens

    async def scripted_model(step: int, max_attempts: int = 2) -> dict | None:
        nonlocal turn_index, llm_calls
        del step, max_attempts
        llm_calls += 1
        agent.llm.token_estimate += 100
        if turn_index >= len(case.turns):
            return {"content": "", "tool_calls": []}
        response = dict(case.turns[turn_index])
        turn_index += 1
        return response

    async def replay_tools(_agent, calls: list) -> list[tuple[str, float]]:
        nonlocal result_index
        results = []
        for _call in calls:
            if result_index >= len(case.tool_results):
                results.append(("Eval fixture exhausted", 0.0))
            else:
                results.append((case.tool_results[result_index], 0.01))
            result_index += 1
        return results

    agent._goal = case.goal
    agent._run_start_time = time.time()
    agent._max_iter = case.max_iterations
    agent._tokens_before_run = start_tokens
    agent._resume_boundary_trace_len = 0
    agent._resume_state = {"goal": case.goal, "completed_steps": [], "blocked_steps": []}
    agent.history = [{"role": "user", "content": case.goal}]
    agent._bootstrap_plan(case.goal)
    agent._active_skills = agent._match_core_skills(case.goal)
    agent._matched_skills = [skill.name for skill in agent._active_skills]
    if agent._active_skills:
        agent._orchestrate_skills()

    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(agent, "_llm_with_retry", new=scripted_model))
            stack.enter_context(patch.object(agent, "_pre_iteration_tasks", new=AsyncMock()))
            stack.enter_context(patch.object(agent, "_persist_all"))
            stack.enter_context(patch.object(agent, "_flush_steps"))
            stack.enter_context(patch.object(agent, "_check_stop_conditions", wraps=agent._check_stop_conditions))
            stack.enter_context(patch.object(agent_module, "append_event"))
            stack.enter_context(patch.object(agent_module, "update_skill_metric"))
            stack.enter_context(patch.object(execution, "tool_confidence", return_value=1.0))
            stack.enter_context(patch.object(execution, "maybe_auto_create_plugin", new=AsyncMock(return_value=False)))
            stack.enter_context(patch.object(execution, "run_speculative", new=replay_tools))
            answer = await agent._loop()
        return _case_metrics(agent, case, answer, llm_calls, "replay")
    finally:
        await agent.llm.model.close()


async def _run_live(case: EvalCase) -> dict[str, Any]:
    with contextlib.redirect_stdout(io.StringIO()):
        agent = await PenzerAgent().async_init()
    before = int(getattr(agent.llm, "token_estimate", 0))
    started = time.monotonic()
    original_run_speculative = execution.run_speculative
    safe_browser_actions = {"search", "open", "snapshot", "content", "metadata", "wait", "tabs", "diagnostics"}

    async def read_only_tools(current_agent, calls: list) -> list[tuple[str, float]]:
        rejected = []
        allowed = []
        allowed_indexes = []
        for index, call in enumerate(calls):
            arguments = call.get("arguments") or {}
            if call.get("name") == "browser" and arguments.get("action") in safe_browser_actions:
                allowed.append(call)
                allowed_indexes.append(index)
            else:
                rejected.append((index, json.dumps({
                    "status": "error",
                    "message": "Live eval blocked a tool/action outside the read-only browser allowlist",
                }), 0.0))
        executed = await original_run_speculative(current_agent, allowed) if allowed else []
        combined = {index: value for index, value in zip(allowed_indexes, executed)}
        combined.update({index: (value, elapsed) for index, value, elapsed in rejected})
        return [combined[index] for index in range(len(calls))]

    try:
        with contextlib.redirect_stdout(io.StringIO()), patch.object(execution, "run_speculative", new=read_only_tools):
            answer = await agent.run(case.goal)
        metrics = _case_metrics(agent, case, answer, int(agent.llm.call_count), "live")
        metrics["tokens"] = max(0, int(getattr(agent.llm, "token_estimate", 0) - before))
        metrics["elapsed_seconds"] = round(time.monotonic() - started, 3)
        return metrics
    finally:
        await agent.llm.model.close()


async def run_evaluations(mode: str, case_ids: set[str] | None = None) -> dict[str, Any]:
    selected = [case for case in CASES if case_ids is None or case.case_id in case_ids]
    if not selected:
        raise ValueError("No matching eval cases selected")
    if mode == "live" and any(not case.live for case in selected):
        raise ValueError("Selected eval case is not approved for live execution")
    results = []
    for case in selected:
        results.append(await (_run_replay(case) if mode == "replay" else _run_live(case)))
    return {
        "mode": mode,
        "passed": sum(bool(result["passed"]) for result in results),
        "total": len(results),
        "pass_rate": round(sum(bool(result["passed"]) for result in results) / len(results), 3),
        "average_iterations": round(sum(result["iterations"] for result in results) / len(results), 2),
        "average_tool_calls": round(sum(result["tool_calls"] for result in results) / len(results), 2),
        "average_tokens": round(sum(result["tokens"] for result in results) / len(results), 2),
        "evidence_grounding_rate": round(sum(bool(result["evidence_grounded"]) for result in results) / len(results), 3),
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic or live Penzer agent-loop evaluations")
    parser.add_argument("--mode", choices=("replay", "live"), default="replay")
    parser.add_argument("--case", action="append", dest="case_ids", help="case ID; repeat to select several")
    parser.add_argument("--output", type=Path, help="write JSON report to this path instead of stdout")
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(run_evaluations(args.mode, set(args.case_ids) if args.case_ids else None))
    except Exception as exc:
        parser.error(str(exc))
    rendered = json.dumps(report, indent=2, ensure_ascii=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    sys.exit(main())