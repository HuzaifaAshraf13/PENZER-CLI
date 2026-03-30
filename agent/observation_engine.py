"""
Observation Engine for Penzer Agent
Interprets action results and decides next steps
"""

import logging
import json
from typing import Dict, Any, Optional

from agent.agent_state import ObservationOutput, ActionOutput, AgentState
from agent.llm import LLM

logger = logging.getLogger(__name__)


class ObservationEngine:
    """
    Observes and interprets action results.
    Provides insights for next iteration planning.
    """
    
    def __init__(self, llm: LLM):
        """Initialize observation engine"""
        self.llm = llm
        logger.info("ObservationEngine initialized")
    
    async def observe(
        self,
        action_output: ActionOutput,
        success_criteria: list,
        current_state: AgentState,
        user_request: str
    ) -> ObservationOutput:
        """
        Observe and interpret action results.
        Determine key findings and next steps.
        """
        
        logger.info(f"Observing action result: {action_output.action_type}")
        
        try:
            # Build observation prompt
            prompt = self._build_observation_prompt(
                action_output,
                success_criteria,
                user_request,
                current_state
            )
            
            # Get LLM interpretation
            llm_response = await self._get_llm_observation(prompt)
            
            # Parse response
            observation_data = self._parse_observation_response(llm_response)
            
            # Determine if goal is achieved
            iteration_complete = self._check_goal_achievement(
                observation_data.get("goal_achieved", False),
                success_criteria
            )
            
            # Create observation output
            output = ObservationOutput(
                action_result=action_output.result,
                interpretation=observation_data.get("interpretation", ""),
                key_findings=observation_data.get("key_findings", []),
                next_steps_suggested=observation_data.get("next_steps", []),
                iteration_complete=iteration_complete
            )
            
            logger.info(f"Observation complete: Goal achieved={iteration_complete}")
            return output
        
        except Exception as e:
            logger.error(f"Observation phase failed: {e}")
            return ObservationOutput(
                action_result=action_output.result,
                interpretation="Error during observation",
                key_findings=[],
                next_steps_suggested=["Continue with next planned action"],
                iteration_complete=False
            )
    
    def _build_observation_prompt(
        self,
        action_output: ActionOutput,
        success_criteria: list,
        user_request: str,
        current_state: AgentState
    ) -> str:
        """Build AI-friendly observation prompt"""
        
        prompt = f"""
# OBSERVATION PHASE

## Original User Request
{user_request}

## Success Criteria (Goal Definition)
{json.dumps(success_criteria, indent=2)}

## Action Executed
- Tool: {action_output.tool_name}
- Status: {'✓ Success' if action_output.success else '✗ Failed'}
- Description: {action_output.action_description}

## Action Result
{json.dumps(action_output.result, indent=2)[:1500]}

## Progress Summary
- Iteration: {current_state.iteration}/{current_state.max_iterations}
- Previous Actions: {len(current_state.action_history)}

## Your Task
Analyze the result and respond with JSON containing:
1. "interpretation": What does this result mean?
2. "key_findings": Important discoveries from this action
3. "goal_achieved": Has the user's goal been achieved?
4. "confidence": Confidence level (0-100)
5. "next_steps": Suggested next actions if goal not achieved

Format: Return ONLY valid JSON, no other text.

Example:
{{
  "interpretation": "The action revealed important information...",
  "key_findings": ["Finding 1", "Finding 2"],
  "goal_achieved": false,
  "confidence": 75,
  "next_steps": ["Next action 1", "Next action 2"]
}}
"""
        return prompt
    
    async def _get_llm_observation(self, prompt: str) -> str:
        """Get observation from LLM"""
        system_prompt = """You are an observation engine analyzing action results.
Interpret results clearly and determine if progress is being made toward the goal.
Be specific about findings and next steps."""
        
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
    
    def _parse_observation_response(self, response: str) -> Dict[str, Any]:
        """Parse LLM response into structured observation"""
        try:
            data = json.loads(response)
            
            # Ensure required fields
            return {
                "interpretation": data.get("interpretation", "Action executed"),
                "key_findings": data.get("key_findings", []),
                "goal_achieved": data.get("goal_achieved", False),
                "confidence": data.get("confidence", 50),
                "next_steps": data.get("next_steps", [])
            }
        
        except json.JSONDecodeError:
            logger.warning("Failed to parse observation as JSON, using fallback")
            return {
                "interpretation": response[:500],
                "key_findings": [],
                "goal_achieved": False,
                "confidence": 0,
                "next_steps": ["Continue with next planned action"]
            }
    
    def _check_goal_achievement(
        self,
        goal_achieved: bool,
        success_criteria: list
    ) -> bool:
        """
        Check if goal has been achieved based on criteria.
        Returns true if iteration should complete (goal reached or decision made).
        """
        return goal_achieved
