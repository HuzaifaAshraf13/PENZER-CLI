# ReAct Framework - Architectural Overview

## Agent Decision Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                      USER INPUT                                  │
│                  (e.g., "Scan network")                         │
└─────────────────────────────────┬───────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────┐
│         INITIALIZE (process_input)                              │
│  1. Auto-Fetch: mem_get_short() → CURRENT CONTEXT             │
│  2. System Prompt injected with context                         │
│  3. Initialize messages[] = [{"role": "user", ...}]           │
└─────────────────────────────────┬───────────────────────────────┘
                                  │
                    ┌─────────────▼─────────────┐
                    │   AUTONOMOUS LOOP         │
                    │  (Max 10 iterations)      │
                    └─────────────┬─────────────┘
                                  │
                    ┌─────────────▼──────────────┐
                    │ 1. BUILD CONVERSATION     │
                    │ _build_conversation_...() │
                    │ Format: role: content     │
                    └─────────────┬──────────────┘
                                  │
                    ┌─────────────▼──────────────────┐
                    │ 2. LLM CALL                   │
                    │ llm.generate_content(        │
                    │   system=with_context,       │
                    │   prompt=conversation_text)  │
                    └─────────────┬──────────────────┘
                                  │
        ┌─────────────────────────▼──────────────────────────┐
        │ 3. PARSE DECISION (_parse_llm_decision)           │
        │ Expected JSON format:                             │
        │ {                                                 │
        │   "thought": "...",                              │
        │   "tool": "...",  [OPTIONAL]                     │
        │   "args": {...},  [OPTIONAL]                     │
        │   "final_answer": "..."  [OPTIONAL]              │
        │ }                                                 │
        └─────────────────────────┬──────────────────────────┘
                                  │
              ┌───────────────────┴──────────────────┐
              │                                      │
              ▼                                      ▼
    ┌─────────────────────┐          ┌──────────────────────────┐
    │  FINAL ANSWER?      │          │   HAS TOOL TO RUN?       │
    │  (final_answer set) │          │   (tool key present)     │
    └────────┬────────────┘          └──────────┬───────────────┘
             │                                   │
             │ YES                               │ YES
             │                                   │
             ▼                                   ▼
    ┌─────────────────────┐          ┌──────────────────────────┐
    │ APPEND TO MESSAGES: │          │  EXECUTE TOOL            │
    │ [FINAL ANSWER]      │          │  run_tool(tool_name,     │
    │                     │          │           args)          │
    │ PRINT RESULT        │          └──────────┬───────────────┘
    │ RETURN              │                      │
    └─────────────────────┘          ┌───────────▼────────────┐
                                     │  CHECK RESULT          │
                                     └───┬──────────────┬──────┘
                                         │              │
                           ┌─────────────┘              └────────────┐
                           │                                         │
                    HAS ERROR?                                HAS ERROR?
                           │                                         │
                           │ YES                                     │ NO
                           │                                         │
                           ▼                                         ▼
                ┌──────────────────────────┐      ┌────────────────────────┐
                │ APPEND ERROR OBSERVATION │      │ APPEND SUCCESS         │
                │ messages.append({        │      │ OBSERVATION            │
                │   "role": "user",        │      │ messages.append({      │
                │   "content":             │      │   "role": "user",      │
                │   "[OBSERVATION - ERROR] │      │   "content":           │
                │    Tool failed: ..."     │      │   "[OBSERVATION]       │
                │ })                       │      │    Tool returned:..." │
                │                          │      │ })                     │
                │ Continue loop            │      │                        │
                │ → Next iteration         │      │ AUTO-SAVE MEMORY       │
                │ LLM tries different tool │      │ _auto_save_to_memory() │
                └──────────────────────────┘      │                        │
                                                  │ Continue loop          │
                                                  │ → Next iteration       │
                                                  └────────────────────────┘
                                                         │
              ┌─────────────────────────────────────────┘
              │
              └──► [Check max iterations]
                  ├─ If < 10: Loop again
                  └─ If ≥ 10: Print "Max iterations reached" & RETURN
```

---

## Message History Evolution

Each iteration builds on prior context:

### Iteration 1
```python
messages = [
    {"role": "user", "content": "Scan network 192.168.1.0/24"}
]
```

### Iteration 2 (After LLM Output)
```python
messages = [
    {"role": "user", "content": "Scan network 192.168.1.0/24"},
    {"role": "assistant", "content": "[THOUGHT] User wants network scan. I'll use nmap."}
]
```

### Iteration 2 (After Tool Execution)
```python
messages = [
    {"role": "user", "content": "Scan network 192.168.1.0/24"},
    {"role": "assistant", "content": "[THOUGHT] User wants network scan. I'll use nmap."},
    {"role": "user", "content": "[OBSERVATION] Tool 'execute_system_command' returned:\n{...nmap results...}"}
]
```

### Iteration 3 (LLM Response with Final Answer)
```python
messages = [
    {"role": "user", "content": "Scan network 192.168.1.0/24"},
    {"role": "assistant", "content": "[THOUGHT] User wants network scan. I'll use nmap."},
    {"role": "user", "content": "[OBSERVATION] Tool 'execute_system_command' returned:\n{...nmap results...}"},
    {"role": "assistant", "content": "[THOUGHT] Scan complete. Found live hosts."},
    {"role": "assistant", "content": "[FINAL ANSWER] Network scan discovered 5 live hosts at..."}
]
```

---

## Memory Auto-Save Mechanism

### Timing
- **WHEN**: After every successful tool execution (no error)
- **WHERE**: `_auto_save_to_memory()` method
- **WHAT**: Tool name, timestamp, result summary (first 200 chars)

### Implementation
```python
async def _auto_save_to_memory(workspace_id, tool_name, result):
    """Auto-save runs in background after tool execution."""
    try:
        await self.run_tool("mem_set_short", {
            "workspace_id": workspace_id,
            "data": {
                "last_execution": {
                    "tool": tool_name,
                    "timestamp": "2026-02-10 14:30:45.123456",
                    "result_summary": "nmap scan found 5 hosts..."
                }
            }
        })
    except Exception as e:
        # Fail silently - don't interrupt main loop
        print(f"[DEBUG] Auto-save failed: {e}")
```

### Key Properties
- ✅ **Transparent**: LLM doesn't need to call it
- ✅ **Non-blocking**: Doesn't slow down loop
- ✅ **Resilient**: Fails silently
- ✅ **Context-aware**: Next iteration reads updated memory

---

## ReAct Components Mapping

| ReAct Phase | Implementation | Code Location |
|------------|---------------|----|
| **THOUGHT** | `decision.get("thought")` | agent.py:237 |
| **ACTION** | `decision.get("tool")` + `run_tool()` | agent.py:247-254 |
| **OBSERVATION** | Tool result appended to `messages[]` | agent.py:256-268 |
| **Loop** | `while iteration < max_iterations` | agent.py:206 |

---

## Key Design Decisions

### 1. **Messages List vs. Chain Context**
| Aspect | Chain Context (Old) | Messages List (New) |
|--------|---------------------|-------------------|
| Structure | Single string | List of dicts |
| History | Rebuilt each iteration | Accumulated |
| Context Size | Grows linearly | Grows with interactions |
| Debuggability | String concat issues | Clear role/content pairs |

### 2. **Autonomous vs. Manual Categorization**
| Aspect | Manual (Old) | Autonomous (New) |
|--------|-------------|-----------------|
| Tool Selection | Hard-coded lists | LLM reasoning |
| Extensibility | Add new tool → update code | Add tool → LLM uses it |
| Flexibility | Limited patterns | Unlimited combinations |
| Control | Agent developer | LLM |

### 3. **Error Handling Strategy**
| Case | Before | Now |
|------|--------|-----|
| Tool fails | Print error & return | Append error observation & retry |
| Recoverable errors | N/A | LLM decides different approach |
| Max iterations | N/A | Graceful exit message |

---

## Current Context Injection

### Timing
- **WHEN**: At `process_input` start, before loop begins
- **WHERE**: `system_prompt_with_context` variable
- **HOW**: Append to system prompt after formatting

### Example
```
SYSTEM_PROMPT (original)
+
# === CURRENT CONTEXT ===
{
  "last_scan": "192.168.1.0/24",
  "open_ports": [22, 80, 443],
  "vulnerabilities": ["CVE-2024-1234"]
}
=
system_prompt_with_context (used for all LLM calls in this session)
```

### Benefits
- ✅ LLM aware of session history from start
- ✅ Prevents redundant scans
- ✅ Enables intelligent follow-ups
- ✅ Single fetch per session (efficient)

---

## Iteration Limits & Safeguards

```python
max_iterations = 10

while iteration < max_iterations:
    iteration += 1
    # ... process
```

### Protection Against
- ✅ Infinite loops (LLM stuck in cycle)
- ✅ Resource exhaustion (unlimited tool calls)
- ✅ Timeout issues (agent always terminates)

### Graceful Exit
```python
if iteration >= max_iterations:
    print(f"Agent: Reached maximum iterations (10). Ending session.")
    return
```

---

## JSON Parsing Robustness

### Handling Markdown Code Blocks
```python
def _parse_llm_decision(raw: str):
    txt = raw.strip()
    # Remove markdown-style code blocks
    if txt.startswith("```") and txt.endswith("```"):
        lines = txt.split("\n")
        txt = "\n".join(lines[1:-1]).strip()
    return json.loads(txt)
```

### Error Fallback
```python
try:
    return json.loads(txt)
except Exception:
    print("\nLLM decision parse failed. Raw output:\n", raw)
    return None  # Process_input exits gracefully
```

---

## Next Steps for Testing

1. **Unit Test ReAct Loop**
   - Mock LLM responses with various JSON outputs
   - Verify message history accumulation
   - Test error observation appending

2. **Integration Test Memory**
   - Verify auto-save writes to mem_set_short
   - Verify next session reads CURRENT CONTEXT
   - Test memory persistence

3. **End-to-End Test**
   - Full workflow: user input → multiple tools → final answer
   - Error recovery scenario
   - Max iterations scenario

4. **Load Test**
   - Large tool outputs (verify truncation)
   - Long message histories
   - Many iterations

---

## Troubleshooting Guide

| Issue | Cause | Solution |
|-------|-------|----------|
| "Invalid LLM output" | JSON parsing failed | Check system prompt, LLM may not follow format |
| "No tool specified and no final answer" | LLM omitted both | Adjust system prompt with clearer examples |
| "Max iterations reached" | Agent looping | LLM may be stuck; check tool errors in messages |
| Memory not persisted | _auto_save failed silently | Check workspace_id, mem_set_short availability |
| Large output truncated | Observation too verbose | Expected; designed for readability |

---

**Architecture Version:** 2.0 - ReAct Framework  
**Last Updated:** February 10, 2026  
**Status:** ✅ Production Ready
