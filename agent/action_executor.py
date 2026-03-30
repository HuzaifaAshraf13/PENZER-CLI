"""
Action Executor for Penzer Agent
Executes planned actions and tools, handling errors and validation
"""

import logging
import json
from typing import Dict, Any, Optional

from agent.agent_state import ActionOutput, PlanningOutput
from agent.llm import LLM

logger = logging.getLogger(__name__)


class ActionExecutor:
    """
    Executes actions from the plan.
    Handles tool execution, error recovery, and result validation.
    """
    
    def __init__(self, llm: LLM, tool_executor):
        """Initialize action executor"""
        self.llm = llm
        self.tool_executor = tool_executor
        logger.info("ActionExecutor initialized")
    
    async def execute(
        self,
        plan: PlanningOutput,
        previous_failures: int = 0
    ) -> ActionOutput:
        """
        Execute the next action from the plan.
        Returns structured action output with result.
        """
        
        logger.info(f"Executing action (previous failures: {previous_failures})")
        
        try:
            # Extract next action from plan
            action_step = self._get_next_action(plan)
            
            if not action_step:
                logger.warning("No valid action to execute")
                return ActionOutput(
                    action_type="no_action",
                    action_description="No action available in plan",
                    success=False,
                    error_message="Plan contains no executable steps"
                )
            
            # Prepare action
            tool_name = action_step.get("tool", "")
            tool_args = action_step.get("args", {})
            action_description = action_step.get("description", "")
            
            logger.info(f"Executing tool: {tool_name}")
            
            # Execute tool
            result = await self.tool_executor.run_tool(tool_name, tool_args)
            
            # Validate result
            success = result.get("status") == "success"
            error_msg = result.get("error") if not success else None
            
            # Create action output
            output = ActionOutput(
                action_type="tool_execution",
                action_description=action_description,
                tool_name=tool_name,
                tool_args=tool_args,
                result=result,
                success=success,
                error_message=error_msg
            )
            
            if success:
                logger.info(f"Tool execution successful: {tool_name}")
            else:
                logger.warning(f"Tool execution failed: {error_msg}")
            
            return output
        
        except Exception as e:
            logger.error(f"Action execution failed: {e}")
            return ActionOutput(
                action_type="tool_execution",
                action_description="Failed to execute action",
                success=False,
                error_message=str(e)
            )
    
    def _get_next_action(self, plan: PlanningOutput) -> Optional[Dict[str, Any]]:
        """Extract next action from plan"""
        if not plan.step_by_step_plan:
            return None
        
        # Get first step (assumes sequential execution)
        first_step = plan.step_by_step_plan[0]
        
        # Validate step has required fields
        if "tool" in first_step:
            return first_step
        
        return None
    
    async def validate_result(
        self,
        action_output: ActionOutput,
        success_criteria: list
    ) -> bool:
        """
        Validate action result against success criteria.
        Uses LLM to intelligently evaluate results.
        """
        
        if not action_output.success:
            return False
        
        try:
            # Build validation prompt
            prompt = f"""
Evaluate if this action result meets the success criteria.

Success Criteria:
{json.dumps(success_criteria, indent=2)}

Action Result:
{json.dumps(action_output.result, indent=2)[:1000]}

Respond with JSON:
{{
  "criteria_met": true/false,
  "reasoning": "explanation"
}}
"""
            
            system_prompt = "You are evaluating if an action result meets success criteria. Be precise."
            
            response = await self._llm_call_async(system_prompt, prompt)
            data = json.loads(response)
            
            criteria_met = data.get("criteria_met", False)
            logger.info(f"Validation result: {criteria_met}")
            return criteria_met
        
        except Exception as e:
            logger.warning(f"Validation failed, using default: {e}")
            return action_output.success
    
    async def should_retry(
        self,
        action_output: ActionOutput,
        error_history: list,
        max_retries: int = 3
    ) -> bool:
        """
        Determine if action should be retried.
        Uses LLM for intelligent retry decision.
        """
        
        # Check retry count
        similar_errors = sum(
            1 for error in error_history
            if error == action_output.tool_name
        )
        
        if similar_errors >= max_retries:
            logger.info(f"Max retries reached for {action_output.tool_name}")
            return False
        
        try:
            # Ask LLM if retry is worthwhile
            prompt = f"""
Should we retry this action?

Tool: {action_output.tool_name}
Error: {action_output.error_message}
Previous Similar Failures: {similar_errors}

Respond with JSON:
{{
  "should_retry": true/false,
  "reason": "explanation"
}}
"""
            
            system_prompt = "You are deciding whether to retry a failed tool execution. Consider if retry is likely to succeed."
            
            response = await self._llm_call_async(system_prompt, prompt)
            data = json.loads(response)
            
            should_retry = data.get("should_retry", False)
            logger.info(f"Retry decision: {should_retry}")
            return should_retry
        
        except Exception as e:
            logger.warning(f"Retry decision failed: {e}")
            return similar_errors < max_retries
    
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
