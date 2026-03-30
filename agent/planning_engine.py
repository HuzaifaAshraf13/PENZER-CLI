"""
Planning Engine for Penzer Agent
Generates step-by-step plans based on reasoning output
"""

import logging
import json
from typing import Dict, Any, List, Optional

from agent.agent_state import PlanningOutput, ReasoningOutput, AgentState
from agent.llm import LLM

logger = logging.getLogger(__name__)


class PlanningEngine:
    """
    Creates action plans based on reasoning.
    Selects appropriate tools and strategies.
    """
    
    def __init__(self, llm: LLM, available_tools: Dict[str, Any]):
        """Initialize planning engine"""
        self.llm = llm
        self.available_tools = available_tools
        logger.info(f"PlanningEngine initialized with {len(available_tools)} tools")
    
    def build_planning_prompt(
        self,
        user_request: str,
        reasoning_output: ReasoningOutput,
        available_tools: List[str],
        previous_attempts: List[str] = None
    ) -> str:
        """Build AI-friendly planning prompt"""
        
        if previous_attempts is None:
            previous_attempts = []
        
        prompt = f"""
# PLANNING PHASE

## User Request
{user_request}

## Reasoning Analysis
- Context: {reasoning_output.context_summary}
- Goal: {reasoning_output.goal_analysis}
- Constraints: {', '.join(reasoning_output.constraints) or 'None'}

## Available Tools
These tools can be executed:
{json.dumps(available_tools[:15], indent=2)}

## Previous Attempts (what didn't work)
{json.dumps(previous_attempts) if previous_attempts else "None yet"}

## Your Task
Create a clear action plan. Respond with JSON containing:
1. "overall_strategy": High-level approach to solve the goal
2. "step_by_step_plan": Array of steps with description and tool
3. "tool_selection_rationale": Why these tools are chosen
4. "success_criteria": How to know when goal is achieved
5. "risk_assessment": Any risks or fallback plans

Format: Return ONLY valid JSON, no other text.

Example:
{{
  "overall_strategy": "Use reconnaissance tools to gather information...",
  "step_by_step_plan": [
    {{"step": 1, "description": "Scan network", "tool": "check_available_tools"}},
    {{"step": 2, "description": "Identify targets", "tool": "execute_system_command"}}
  ],
  "tool_selection_rationale": "These tools are chosen because...",
  "success_criteria": ["Information gathered", "Vulnerabilities identified"],
  "risk_assessment": "No major risks identified"
}}
"""
        return prompt
    
    async def plan(
        self,
        user_request: str,
        reasoning_output: ReasoningOutput,
        current_state: AgentState
    ) -> PlanningOutput:
        """
        Execute planning phase.
        Generate action plan based on reasoning.
        """
        
        logger.info(f"Starting planning phase (iteration {current_state.iteration})")
        
        try:
            # Get list of available tool names
            available_tools = list(self.available_tools.keys())
            
            # Get previous failed attempts
            previous_attempts = [
                action.tool_name for action in current_state.action_history
                if not action.success
            ]
            
            # Build prompt
            prompt = self.build_planning_prompt(
                user_request,
                reasoning_output,
                available_tools,
                previous_attempts
            )
            
            # Get LLM response
            llm_response = await self._get_llm_plan(prompt)
            
            # Parse response
            plan_data = self._parse_planning_response(llm_response)
            
            # Create planning output
            output = PlanningOutput(
                overall_strategy=plan_data.get("overall_strategy", ""),
                step_by_step_plan=plan_data.get("step_by_step_plan", []),
                tool_selection_rationale=plan_data.get("tool_selection_rationale", ""),
                success_criteria=plan_data.get("success_criteria", []),
                risk_assessment=plan_data.get("risk_assessment", "")
            )
            
            logger.info(f"Planning complete: {len(output.step_by_step_plan)} steps planned")
            return output
        
        except Exception as e:
            logger.error(f"Planning phase failed: {e}")
            # Return minimal plan on error
            return PlanningOutput(
                overall_strategy="Execute available tools to progress toward goal",
                step_by_step_plan=[],
                tool_selection_rationale="Error in planning, using fallback strategy",
                success_criteria=["Task completed"],
                risk_assessment="Reduced planning information"
            )
    
    async def _get_llm_plan(self, prompt: str) -> str:
        """Get plan from LLM"""
        system_prompt = """You are a planning engine for an autonomous pentesting agent.
Create clear, executable action plans that achieve the stated goals.
Consider tool availability and efficiency.
Be specific about which tools to use and in what order."""
        
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
    
    def _parse_planning_response(self, response: str) -> Dict[str, Any]:
        """Parse LLM response into structured plan"""
        try:
            data = json.loads(response)
            
            # Ensure required fields exist
            required = ["overall_strategy", "step_by_step_plan"]
            for field in required:
                if field not in data:
                    data[field] = self._get_fallback_value(field)
            
            return data
        
        except json.JSONDecodeError:
            logger.warning("Failed to parse planning as JSON, using fallback")
            return {
                "overall_strategy": response[:500],
                "step_by_step_plan": [],
                "tool_selection_rationale": "Parsed from text response",
                "success_criteria": ["Goal achieved"],
                "risk_assessment": "Unable to assess"
            }
    
    def _get_fallback_value(self, field: str) -> Any:
        """Get fallback value for missing fields"""
        fallbacks = {
            "overall_strategy": "Execute available tools to achieve goal",
            "step_by_step_plan": [],
            "tool_selection_rationale": "Tools selected based on availability",
            "success_criteria": ["Goal achieved"],
            "risk_assessment": "Standard risk profile"
        }
        return fallbacks.get(field, "N/A")
    
    def extract_first_action(self, plan: PlanningOutput) -> Optional[Dict[str, Any]]:
        """Extract first action from plan"""
        if plan.step_by_step_plan and len(plan.step_by_step_plan) > 0:
            return plan.step_by_step_plan[0]
        return None
