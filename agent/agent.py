"""
Penzer Autonomous Pentesting Agent
Simple ReAct loop (Reason → Act → Observe) using skills
"""

import json
import asyncio
import logging
from typing import Dict, Any, List, Optional
from enum import Enum

from agent.core import mcp
from agent.llm import LLM
from agent.skills import load_all_skills, PentestPhase

logger = logging.getLogger(__name__)


class LoopPhase(Enum):
    """Current phase in ReAct loop"""
    REASON = "reason"
    ACT = "act"
    OBSERVE = "observe"


class PenzerAgent:
    """
    Autonomous pentesting agent with Reason → Act → Observe loop.
    Uses skills to guide decision-making.
    """
    
    def __init__(self):
        """Initialize agent"""
        self.llm = LLM()
        self.mcp_client = mcp
        
        # Load skills by phase
        self.skills = load_all_skills()
        self.available_tools: Dict[str, Any] = {}
        self.tool_schema: Dict[str, Any] = {}
        
        # State tracking
        self.iteration = 0
        self.max_iterations = 10
        self.action_history: List[Dict[str, Any]] = []
        self.reasoning_history: List[str] = []
        
        logger.info("PenzerAgent initialized")
    
    async def async_init(self) -> 'PenzerAgent':
        """Async initialization - load tools"""
        try:
            await self._load_tools()
            logger.info(f"Agent ready: {len(self.available_tools)} tools loaded")
            return self
        except Exception as e:
            logger.error(f"Async init failed: {e}")
            raise
    
    async def _load_tools(self) -> None:
        """Load MCP tools"""
        try:
            if hasattr(self.mcp_client, "get_tools"):
                tools = await self.mcp_client.get_tools()
            else:
                tools = getattr(self.mcp_client, "tools", {})
            
            self.available_tools = tools
            self.tool_schema = self._serialize_tools(tools)
            logger.info(f"Loaded {len(self.tool_schema)} tools")
        except Exception as e:
            logger.error(f"Tool loading failed: {e}")
            self.available_tools = {}
            self.tool_schema = {}
    
    def _serialize_tools(self, tools_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Extract tool signatures for LLM"""
        serial = {}
        for name, tool_obj in tools_dict.items():
            try:
                import inspect
                fn = getattr(tool_obj, "fn", tool_obj)
                if hasattr(fn, "__wrapped__"):
                    fn = fn.__wrapped__
                sig = inspect.signature(fn)
                params = list(sig.parameters.keys())
                serial[name] = {"args": params}
            except Exception:
                serial[name] = {"args": []}
        return serial
    
    async def execute_user_request(self, user_request: str) -> Dict[str, str]:
        """
        Execute user request through ReAct loop.
        
        Returns:
            {"status": "success|error", "response": "..."}
        """
        logger.info(f"Executing: {user_request}")
        self.iteration = 0
        self.action_history = []
        self.reasoning_history = []
        
        try:
            # Main ReAct loop
            while self.iteration < self.max_iterations:
                self.iteration += 1
                logger.info(f"\n{'='*60}")
                logger.info(f"ITERATION {self.iteration}/{self.max_iterations}")
                logger.info(f"{'='*60}")
                
                # ===== REASON =====
                logger.info("→ REASON")
                reasoning = await self._reason_phase(user_request)
                self.reasoning_history.append(reasoning)
                logger.info(f"  Reasoning: {reasoning[:100]}")
                
                # Check if done reasoning
                if "goal_achieved" in reasoning.lower() or "complete" in reasoning.lower():
                    logger.info("  Goal marked as achieved in reasoning")
                    return {"status": "success", "response": reasoning}
                
                # ===== ACT =====
                logger.info("→ ACT")
                action = await self._act_phase(user_request, reasoning)
                
                if not action:
                    logger.warning("  No action to take")
                    break
                
                self.action_history.append(action)
                logger.info(f"  Tool: {action.get('tool_name')}")
                
                # ===== OBSERVE =====
                logger.info("→ OBSERVE")
                observation = await self._observe_phase(action, user_request)
                logger.info(f"  Observation: {observation[:100]}")
                
                # Check if goal achieved
                if "goal_achieved" in observation.lower() or "complete" in observation.lower():
                    logger.info("  Goal achieved!")
                    final_answer = await self._synthesize_answer(user_request)
                    return {"status": "success", "response": final_answer}
            
            # Max iterations reached
            logger.warning(f"Max iterations ({self.max_iterations}) reached")
            final_answer = await self._synthesize_answer(user_request)
            return {"status": "success", "response": final_answer}
        
        except Exception as e:
            logger.error(f"Request execution failed: {e}")
            return {"status": "error", "response": str(e)}
    
    async def _reason_phase(self, user_request: str) -> str:
        """
        Reason phase: Analyze goal and constraints.
        Consider available skills and tools.
        
        Returns reasoning string.
        """
        # Build available skills summary
        skills_summary = self._build_skills_summary()
        tools_summary = json.dumps(list(self.tool_schema.keys())[:15], indent=2)
        
        prompt = f"""
You are an autonomous pentesting agent. Analyze the user request and reasoning.

## User Request
{user_request}

## Available Pentesting Skills (by phase)
{skills_summary}

## Available Tools
{tools_summary}

## Previous Actions
{json.dumps([a.get('tool_name') for a in self.action_history[-3:]], indent=2) if self.action_history else "None yet"}

## Task
Reason about:
1. What is the goal?
2. What constraints exist?
3. What skills are relevant?
4. What's the next step?
5. Have we achieved the goal?

Respond with clear reasoning. If goal is achieved, say "GOAL_ACHIEVED: [summary]"
"""
        
        system = "You are a pentesting agent reasoning engine. Be precise and tactical."
        response = await self._llm_call(system, prompt)
        return response
    
    async def _act_phase(self, user_request: str, reasoning: str) -> Optional[Dict[str, Any]]:
        """
        Act phase: Select and execute a tool or skill.
        
        Returns action dict or None if no action.
        """
        available_actions = self._build_available_actions()
        
        prompt = f"""
You are selecting which tool/skill to execute next.

## Current Goal
{user_request}

## Current Reasoning
{reasoning[:500]}

## Available Actions
{json.dumps(available_actions, indent=2)}

## Your Task
Select ONE action to execute next. Respond with JSON:
{{"tool_name": "tool_name", "args": {{}}, "description": "why we're doing this"}}

Important: tool_name MUST be from the available list above.
Only respond with JSON, no other text.
"""
        
        system = "You are selecting actions to execute. Respond only with valid JSON."
        response = await self._llm_call(system, prompt)
        
        try:
            action = json.loads(response)
            # Validate tool exists
            if action.get("tool_name") not in self.available_tools:
                logger.warning(f"Invalid tool selected: {action.get('tool_name')}")
                return None
            
            # Execute tool
            args = action.get("args", {})
            result = await self.run_tool(action.get("tool_name"), args)
            action["result"] = result
            action["success"] = result.get("status") == "success"
            
            return action
        except Exception as e:
            logger.error(f"Action phase failed: {e}")
            return None
    
    async def _observe_phase(self, action: Dict[str, Any], user_request: str) -> str:
        """
        Observe phase: Interpret action results.
        Determine if goal is achieved.
        
        Returns observation string.
        """
        result = action.get("result", {})
        
        prompt = f"""
Analyze the result of executing a tool.

## User Request
{user_request}

## Action Executed
Tool: {action.get('tool_name')}
Description: {action.get('description')}

## Result
{json.dumps(result, indent=2)[:1000]}

## Your Task
1. What did this result tell us?
2. Did it help toward the goal?
3. Do we have enough information to declare goal achieved?
4. What should we do next?

If goal is achieved, say "GOAL_ACHIEVED: [summary]"
Respond conversationally with observations.
"""
        
        system = "You are analyzing tool execution results. Be insightful."
        response = await self._llm_call(system, prompt)
        return response
    
    async def _synthesize_answer(self, user_request: str) -> str:
        """Synthesize final answer from all actions taken"""
        if not self.action_history:
            return "No actions were taken."
        
        prompt = f"""
Synthesize a final answer based on all actions taken.

## Original Request
{user_request}

## Actions Taken
{json.dumps([{"tool": a.get('tool_name'), "success": a.get('success')} for a in self.action_history], indent=2)}

## Reasoning History
{json.dumps(self.reasoning_history[-3:], indent=2)}

Provide a concise final answer to the user's request based on what we learned.
"""
        
        system = "You are synthesizing a final answer. Be concise and direct."
        response = await self._llm_call(system, prompt)
        return response
    
    def _build_skills_summary(self) -> str:
        """Build summary of available skills by phase"""
        summary = ""
        for phase in PentestPhase:
            skills = self.skills.get(phase, [])
            if skills:
                summary += f"\n**{phase.value.upper()}:**\n"
                for skill in skills[:3]:  # Limit to 3 per phase
                    name = getattr(skill, 'name', 'Unknown')
                    desc = getattr(skill, 'description', '')
                    summary += f"  - {name}: {desc[:80]}\n"
        return summary or "No skills loaded"
    
    def _build_available_actions(self) -> List[Dict[str, str]]:
        """Build list of available tools/skills for action selection"""
        actions = []
        for tool_name in self.available_tools.keys():
            args_info = self.tool_schema.get(tool_name, {}).get("args", [])
            actions.append({
                "tool_name": tool_name,
                "args": args_info
            })
        return actions[:20]  # Limit to 20 tools
    
    async def _llm_call(self, system: str, prompt: str) -> str:
        """Make async LLM call"""
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self.llm.generate_content,
            system,
            prompt
        )
    
    async def run_tool(self, tool_name: str, args: Dict) -> Dict:
        """Execute a tool via MCP"""
        try:
            tool = self.available_tools.get(tool_name)
            if not tool:
                return {"status": "error", "error": f"Unknown tool: {tool_name}"}
            
            callable_obj = getattr(tool, "fn", tool)
            
            # Filter args by signature
            import inspect
            try:
                sig = inspect.signature(callable_obj)
                filtered_args = {k: v for k, v in args.items() if k in sig.parameters}
            except Exception:
                filtered_args = args
            
            # Execute
            import asyncio
            if asyncio.iscoroutinefunction(callable_obj):
                result = await callable_obj(**filtered_args)
            else:
                result = callable_obj(**filtered_args)
            
            if not isinstance(result, dict):
                return {"status": "success", "data": result}
            
            if "status" not in result:
                return {"status": "success", "data": result}
            
            return result
        
        except Exception as e:
            logger.error(f"Tool execution failed: {e}")
            return {"status": "error", "error": str(e)}


# ============================================================================
# LEGACY COMPATIBILITY
# ============================================================================

class Agent(PenzerAgent):
    """Legacy wrapper for backward compatibility"""
    pass


if __name__ == "__main__":
    """Test agent"""
    
    async def test():
        agent = await PenzerAgent().async_init()
        result = await agent.execute_user_request("List available system tools")
        logger.info(f"Result: {result}")
    
    try:
        asyncio.run(test())
    except Exception as e:
        logger.error(f"Test failed: {e}")
