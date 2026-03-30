# Penzer Agent Refactoring - Reason → Plan → Act Architecture

## Overview

The Penzer agent has been completely refactored to implement a modern **Reason → Plan → Act (ReAct)** cycle with clear separation of concerns and AI-friendly design for autonomous pentesting operations.

## Architecture

### Core Components

```
┌─────────────────────────────────────────────────────────────┐
│                    PenzerAgent (Orchestrator)               │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐    │
│  │  Reasoning   │→ │  Planning    │→ │  Action      │    │
│  │  Engine      │  │  Engine      │  │  Executor    │    │
│  └──────────────┘  └──────────────┘  └──────────────┘    │
│         ↑                                      ↓           │
│         └──────────────────────────────────────┘           │
│                  ↓                                         │
│         ┌──────────────────────────┐                      │
│         │  Observation Engine      │                      │
│         └──────────────────────────┘                      │
│                  ↓                                         │
│  ┌────────────────────────────────────────────────┐      │
│  │         Memory Manager                         │      │
│  │  (Short-term + Long-term Learning)             │      │
│  └────────────────────────────────────────────────┘      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Execution Flow

```
User Request
    ↓
[ITERATION LOOP - Max 10]
    ├→ REASONING PHASE
    │   ├ Analyze context & goals
    │   ├ Identify constraints
    │   └ Review memory
    │
    ├→ PLANNING PHASE
    │   ├ Generate strategy
    │   ├ Select tools
    │   └ Create step-by-step plan
    │
    ├→ ACTION PHASE
    │   ├ Execute tool
    │   ├ Validate result
    │   └ Auto-save to memory
    │
    ├→ OBSERVATION PHASE
    │   ├ Interpret results
    │   ├ Extract findings
    │   └ Decide if goal achieved
    │
    └→ [REFLECTION]
       ├ Is goal achieved? → YES: Return answer
       └ Continue loop or hit max iterations
    
Final Answer + Consolidated Learning
```

## Component Details

### 1. AgentState (agent_state.py)

**Purpose**: Centralized state management for the entire execution cycle.

**Key Classes**:
- `AgentState`: Main state container tracking all phases
- `ReasoningOutput`: Structured reasoning results
- `PlanningOutput`: Plan and strategy details
- `ActionOutput`: Tool execution results
- `ObservationOutput`: Action interpretation

**Features**:
- Clear phase tracking
- Memory integration
- AI-friendly context generation
- State persistence

```python
# Example usage
state = AgentState(session_id="xyz", user_request="scan network")
state.add_reasoning(reasoning_output)
state.add_planning(planning_output)
state.add_action(action_output)

# Get AI-friendly context
context = state.get_context_for_llm()
```

### 2. ReasoningEngine (reasoning_engine.py)

**Purpose**: Analyzes current context and formulates understanding.

**Key Responsibilities**:
- Parse user request in context of previous actions
- Identify constraints and limitations
- Review relevant memory
- Generate clear goal analysis

**AI-Friendly Design**:
- Explicit JSON-formatted prompts
- Clear required fields (context_summary, goal_analysis, constraints)
- Integration with short/long-term memory
- Structured for LLM comprehension

```python
# Example usage
reasoning_engine = ReasoningEngine(llm)
reasoning_output = await reasoning_engine.reason(
    user_request="Find SQL injection vulnerabilities",
    current_state=state,
    short_term_memory={...},
    long_term_memory={...}
)

# Returns ReasoningOutput with:
# - context_summary: Current situation
# - goal_analysis: What needs to be done
# - constraints: Limitations
# - relevant_memory: Applicable past knowledge
```

### 3. PlanningEngine (planning_engine.py)

**Purpose**: Creates step-by-step action plans.

**Key Responsibilities**:
- Develop overall strategy
- Select appropriate tools
- Create sequential action steps
- Define success criteria
- Assess risks

**Tool Selection Logic**:
- Analyzes available tools
- Considers previous failures
- Plans tool chains
- Provides rationale for choices

```python
# Example usage
planning_engine = PlanningEngine(llm, available_tools)
plan = await planning_engine.plan(
    user_request="...",
    reasoning_output=reasoning,
    current_state=state
)

# Returns PlanningOutput with:
# - overall_strategy
# - step_by_step_plan: [{"step": 1, "description": "...", "tool": "..."}]
# - tool_selection_rationale
# - success_criteria
# - risk_assessment
```

### 4. ActionExecutor (action_executor.py)

**Purpose**: Executes planned actions safely and reliably.

**Key Responsibilities**:
- Execute tools from plan
- Handle errors gracefully
- Validate results against success criteria
- Determine retry worthiness
- Provide detailed action output

**Error Recovery**:
- Tool failure detection
- Intelligent retry logic
- Error logging and reporting
- Fallback strategies

```python
# Example usage
executor = ActionExecutor(llm, tool_executor)
action_output = await executor.execute(plan)

# Returns ActionOutput with:
# - action_type: "tool_execution"
# - success: bool
# - result: Tool result data
# - error_message: If failed
```

### 5. ObservationEngine (observation_engine.py)

**Purpose**: Interprets action results and guides reflection.

**Key Responsibilities**:
- Interpret tool results
- Extract key findings
- Check goal achievement
- Suggest next steps
- Provide confidence levels

**Goal Achievement Detection**:
- Validates against success criteria
- LLM-based interpretation
- Progress tracking
- Completion decision

```python
# Example usage
observation = await observation_engine.observe(
    action_output=action,
    success_criteria=["SQL injection found"],
    current_state=state,
    user_request="..."
)

# Returns ObservationOutput with:
# - interpretation: What does result mean?
# - key_findings: Important discoveries
# - goal_achieved: bool
# - next_steps_suggested: [...] if not achieved
```

### 6. MemoryManager (memory_manager.py)

**Purpose**: Manages short and long-term learning.

**Key Responsibilities**:
- Load/save short-term memory
- Persist long-term learning
- Search memory for relevance
- Consolidate learnings after session
- Provide memory context for reasoning

**Memory Types**:
- **Short-term**: Current session context, recent actions
- **Long-term**: Persistent knowledge, lessons learned, patterns

```python
# Example usage
memory = MemoryManager(tool_executor)
await memory.load_memory(workspace_id)

# Save learning
await memory.save_short_term(workspace_id, "finding_1", data)
await memory.save_long_term(workspace_id, "vulnerability_pattern", data)

# Search memory
results = await memory.search_memory(workspace_id, "SQL injection")

# Get context for LLM
short_context = memory.get_short_term_context(max_items=10)
```

## Execution Cycle Details

### Iteration 1: Learning Phase
```
1. REASON: "User wants to scan network. I see nmap is available."
2. PLAN: "Use nmap for network scan. Then analyze results."
3. ACT: Execute nmap scan
4. OBSERVE: "Found 3 open ports. Need vulnerability scan next."
5. SAVE: Store port information in memory
```

### Iteration 2: Building on Previous
```
1. REASON: "Previous scan found ports. Now check vulnerabilities."
2. PLAN: "Run vulnerability scanner on found ports."
3. ACT: Execute vulnerability scanner
4. OBSERVE: "Found 2 CVEs. Goal partially achieved."
5. SAVE: Store vulnerability data
```

### Iteration 3: Completion
```
1. REASON: "Have vulnerability list. Can provide final analysis."
2. PLAN: "Compile findings into report."
3. ACT: Format and prepare report
4. OBSERVE: "Goal achieved. Ready for final answer."
5. RETURN: Final answer with all findings
```

## AI-Friendly Design Principles

### 1. Clear Phase Separation
Each phase has explicit input/output contract:
```python
# Input: Reasoning phase expects these fields
{
    "user_request": str,
    "current_context": dict,
    "memory": dict
}

# Output: Always JSON with known fields
{
    "context_summary": str,
    "goal_analysis": str,
    "constraints": [str],
    "relevant_memory": dict
}
```

### 2. Explicit Decision Points
Agent decisions are clear and logged:
- "Tool selected: nmap because..."
- "Goal achieved: Yes, based on criteria..."
- "Next step: Execute vulnerability scan"

### 3. Memory Integration
- Every tool execution is saved
- Previous findings inform next decisions
- Long-term patterns guide strategy
- Search finds relevant past knowledge

### 4. Structured Prompting
All LLM calls use:
- Clear phase labels
- Explicit task description
- Available options listing
- Expected JSON format
- Example responses

## Configuration

### In config.py:
```python
MAX_ITERATIONS = 10           # Max Reason→Plan→Act cycles
REQUEST_TIMEOUT = 300         # Max seconds per request
LLM_TEMPERATURE = 0.7         # LLM sampling temperature

MEMORY_SHORT_TERM_MAX_ITEMS = 10
MEMORY_LONG_TERM_MAX_ITEMS = 50
AUTO_SAVE_MEMORY = True
```

## Logging Output

When running, you'll see:
```
============================================================
ITERATION 1/10
============================================================
→ REASONING PHASE
  Goal: Scan network for open ports
  Constraints: Limited to local subnet
→ PLANNING PHASE
  Strategy: Use nmap for fast network discovery
  Steps planned: 2
→ ACTION PHASE
  ✓ Tool executed: execute_system_command
  Findings: Found 5 open ports
→ OBSERVATION PHASE
  ✓ Goal achieved!
```

## Testing

```bash
# Test individual engines
python3 -c "
from agent.agent_state import AgentState, ReasoningOutput
state = AgentState(session_id='test')
print('AgentState working')
"

# Test full agent
python3 -c "
import asyncio
from agent.agent import PenzerAgent

async def test():
    agent = await PenzerAgent().async_init()
    result = await agent.execute_user_request('list available tools')
    print(result)

asyncio.run(test())
"
```

## Migration from Old Agent

**Old Agent** (agent_legacy.py):
- Monolithic architecture
- Mixed concerns
- Implicit state management

**New Agent** (agent.py):
- Modular components
- Clear separation of concerns
- Explicit state tracking
- AI-friendly prompting
- Better testability

The `Agent` class now extends `PenzerAgent` for backward compatibility.

## Future Enhancements

- [ ] Parallel tool execution
- [ ] Skill learning from failures
- [ ] Dynamic prompt optimization
- [ ] Tool combination strategies
- [ ] Context window optimization
- [ ] Failure pattern recognition
- [ ] Knowledge base integration
- [ ] Team collaboration features

## Performance Metrics

The agent tracks:
- Iterations per request
- Tools used per phase
- Success/failure rates
- Findings discovered
- Memory efficiency
- Total execution time

## Debugging

Enable debug logging:
```bash
export DEBUG_MODE=true
export LOG_LEVEL=DEBUG
python3 cli.py
```

Check logs:
```bash
tail -f logs/penzer.log
```

Export state for analysis:
```python
state_dict = state_manager.export_state("/tmp/agent_state.json")
```

---

**Version**: 2.0 (Refactored)  
**Date**: March 30, 2026  
**Status**: Production Ready
