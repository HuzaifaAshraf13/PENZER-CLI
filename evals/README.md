# Agent Loop Evaluations

Run the deterministic, offline replay suite:

```bash
PYTHONPATH=. python -m evals.agent_loop --mode replay
```

Replay mode calls the real agent loop with scripted model turns and mocked
tool outcomes. It performs no external requests and does not write reports
unless `--output` is supplied. It currently checks:

- A browser search result is cited in a concise grounded answer.
- A terminal result is used in the final answer.
- Two consecutive no-result searches stop instead of looping.

Metrics include pass/fail, iterations, model calls, tool calls and sequence,
tool outcomes, estimated tokens, elapsed tool time, required-answer terms,
evidence grounding, citation support, and budget compliance.

Write a JSON report explicitly:

```bash
PYTHONPATH=. python -m evals.agent_loop --mode replay --output evals/latest.json
```

Live mode is opt-in, calls the configured model and real tools, and is limited
to cases marked read-only in the harness:

```bash
PYTHONPATH=. python -m evals.agent_loop --mode live --case browser-grounded-answer
```

Live metrics are not deterministic: model responses, search results, latency,
and token use can change between runs. Never add mutating tasks to the live
case list without an explicit approval strategy.