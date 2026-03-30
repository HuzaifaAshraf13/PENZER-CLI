"""
Reasoning Engine for Penzer Agent
Analyzes context, goals, and constraints to inform planning
"""

import logging
import json
from typing import Dict, Any, List, Optional
from datetime import datetime

from agent.agent_state import ReasoningOutput, AgentState
from agent.llm import LLM

logger = logging.getLogger(__name__)


class ReasoningEngine:
    """
    Analyzes current context and generates reasoning output.
    Focuses on understanding what needs to be done and why.
    """
    
    def __init__(self, llm: LLM):
        """Initialize reasoning engine with LLM"""
        self.llm = llm
        logger.info("ReasoningEngine initialized")
    
    def build_reasoning_prompt(
        self,
        user_request: str,
        current_state: AgentState,
        short_term_memory: Dict[str, Any],
        long_term_memory: Dict[str, Any]
    ) -> str:
        """
        Build AI-friendly prompt for reasoning phase.
        Clear, structured format for LLM comprehension.
        """
        
        # Build recent context
        recent_actions = []
        if current_state.action_history:
            for action in current_state.action_history[-3:]:
                recent_actions.append({
                    "tool": action.tool_name or "N/A",
                    "description": action.action_description,
                    "success": action.success
                })
        
        prompt = f"""
# REASONING PHASE

## Task
User Request: {user_request}

## Current Context
- Iteration: {current_state.iteration}/{current_state.max_iterations}
- Total Actions Taken: {len(current_state.action_history)}

## Recent Actions
{json.dumps(recent_actions[-3:], indent=2) if recent_actions else "No actions yet"}

## Short-term Memory (Current Session)
{json.dumps(short_term_memory, indent=2)[:1000] if short_term_memory else "Empty"}

## Long-term Memory (Past Knowledge)
{json.dumps(long_term_memory, indent=2)[:1000] if long_term_memory else "Empty"}

## Your Task
Analyze the situation and respond with JSON containing:
1. "context_summary": Brief summary of current situation
2. "goal_analysis": What specifically needs to be accomplished?
3. "constraints": List of constraints or limitations
4. "relevant_memory": Key facts from memory that are relevant

Format: Return ONLY valid JSON, no other text.

Example format:
{{
  "context_summary": "...",
  "goal_analysis": "...",
  "constraints": ["constraint1", "constraint2"],
  "relevant_memory": {{"key": "value"}}
}}
"""
        return prompt
    
    async def reason(
        self,
        user_request: str,
        current_state: AgentState,
        short_term_memory: Dict[str, Any],
        long_term_memory: Dict[str, Any]
    ) -> ReasoningOutput:
        """
        Execute reasoning phase.
        Analyze context and return structured reasoning output.
        """
        
        logger.info(f"Starting reasoning phase (iteration {current_state.iteration})")
        
        try:
            # Build prompt
            prompt = self.build_reasoning_prompt(
                user_request,
                current_state,
                short_term_memory,
                long_term_memory
            )
            
            # Get LLM response
            llm_response = await self._get_llm_reasoning(prompt)
            
            # Parse response
            reasoning_data = self._parse_reasoning_response(llm_response)
            
            # Create reasoning output
            output = ReasoningOutput(
                context_summary=reasoning_data.get("context_summary", ""),
                goal_analysis=reasoning_data.get("goal_analysis", ""),
                constraints=reasoning_data.get("constraints", []),
                relevant_memory=reasoning_data.get("relevant_memory", {})
            )
            
            logger.info(f"Reasoning complete: {output.goal_analysis[:100]}")
            return output
        
        except Exception as e:
            logger.error(f"Reasoning phase failed: {e}")
            # Return empty reasoning on error
            return ReasoningOutput(
                context_summary="Error during reasoning",
                goal_analysis=user_request,
                constraints=[],
                relevant_memory={}
            )
    
    async def _get_llm_reasoning(self, prompt: str) -> str:
        """Get reasoning from LLM"""
        system_prompt = """You are a reasoning engine for an autonomous pentesting agent.
Analyze the current state and generate clear, structured reasoning about what needs to be done.
Be precise and focus on goal achievement."""
        
        response = await self._llm_call_async(system_prompt, prompt)
        return response
    
    async def _llm_call_async(self, system_prompt: str, prompt: str) -> str:
        """Make async LLM call"""
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self.llm.generate_content,
            system_prompt,
            prompt
        )
    
    def _parse_reasoning_response(self, response: str) -> Dict[str, Any]:
        """Parse LLM response into structured reasoning"""
        try:
            # Try direct JSON parse
            data = json.loads(response)
            
            # Validate required fields
            required = ["context_summary", "goal_analysis"]
            if all(field in data for field in required):
                return data
            
            # Fallback if fields missing
            return {
                "context_summary": data.get("context_summary", "Analysis complete"),
                "goal_analysis": data.get("goal_analysis", "Goal assessed"),
                "constraints": data.get("constraints", []),
                "relevant_memory": data.get("relevant_memory", {})
            }
        
        except json.JSONDecodeError:
            logger.warning("Failed to parse reasoning as JSON, using fallback")
            return {
                "context_summary": response[:200],
                "goal_analysis": response[:500],
                "constraints": [],
                "relevant_memory": {}
            }
