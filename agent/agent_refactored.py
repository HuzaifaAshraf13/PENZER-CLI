"""
Penzer Autonomous Pentesting Agent - Refactored with Reason → Plan → Act Cycle
Modern agent architecture with clear separation of concerns
"""

import json
import asyncio
import logging
import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime

from agent.core import mcp, init_reme, cleanup_reme
from agent.llm import LLM
from agent.agent_state import AgentStateManager, AgentState, AgentPhase
from agent.reasoning_engine import ReasoningEngine
from agent.planning_engine import PlanningEngine
from agent.action_executor import ActionExecutor
from agent.observation_engine import ObservationEngine
from agent.memory_manager import MemoryManager
from agent.skill_selector import SkillSelector, create_skill_aware_system_prompt
from agent.skills import PentestPhase, load_all_skills
from config import MAX_ITERATIONS, REQUEST_TIMEOUT, DEFAULT_WORKSPACE

# Import session tools and resources
import session.session  # registers memory resources and tools

# Setup logging
logger = logging.getLogger(__name__)


class PenzerAgent:
    """
    Modern autonomous pentesting agent with Reason → Plan → Act cycle.
    
    Architecture:
    - ReasoningEngine: Analyzes context and formulates understanding
    - PlanningEngine: Creates step-by-step action plans
    - ActionExecutor: Executes tools and handles results
    - ObservationEngine: Interprets results and guides reflection
    - MemoryManager: Manages short and long-term learning
    """
    
    def __init__(self):
        """Initialize agent components"""
        self.llm = LLM()
        self.mcp_client = mcp
        self.workspace_id = DEFAULT_WORKSPACE
        
        # Initialize engines
        self.reasoning_engine: Optional[ReasoningEngine] = None
        self.planning_engine: Optional[PlanningEngine] = None
        self.action_executor: Optional[ActionExecutor] = None
        self.observation_engine: Optional[ObservationEngine] = None
        self.memory_manager: Optional[MemoryManager] = None
        
        # Tool schema
        self.tool_schema: Dict[str, Any] = {}
        self.available_tools: Dict[str, Any] = {}
        
        # Skills (for reference)
        self.all_skills = load_all_skills()
        self.skill_selector: Optional[SkillSelector] = None
        
        logger.info("PenzerAgent initialized")
    
    async def async_init(self) -> 'PenzerAgent':
        """
        Async initialization of agent components.
        Must be called before using agent.
        """
        logger.info("Starting async initialization")
        
        try:
            # Initialize ReMeApp for memory
            reme_success = await init_reme()
            if not reme_success:
                logger.warning("Long-term memory unavailable, using short-term only")
            
            # Load tools
            await self._load_tools()
            
            # Initialize engines with loaded tools
            self.reasoning_engine = ReasoningEngine(self.llm)
            self.planning_engine = PlanningEngine(self.llm, self.available_tools)
            self.action_executor = ActionExecutor(self.llm, self)
            self.observation_engine = ObservationEngine(self.llm)
            self.memory_manager = MemoryManager(self)
            
            # Load existing memory
            await self.memory_manager.load_memory(self.workspace_id)
            
            # Initialize skill selector
            self.skill_selector = SkillSelector(self.all_skills)
            
            tool_count = len(self.tool_schema)
            logger.info(f"Agent ready: {tool_count} tools loaded")
            
            return self
        
        except Exception as e:
            logger.error(f"Async initialization failed: {e}")
            raise
    
    async def _load_tools(self) -> None:
        """Load MCP tools into agent"""
        try:
            if hasattr(self.mcp_client, "get_tools"):
                tools = await self.mcp_client.get_tools()
            else:
                tools = getattr(self.mcp_client, "tools", {})
            
            self.tool_schema = self._serialize_tools_for_prompt(tools)
            self.available_tools = tools
            
            logger.info(f"Loaded {len(self.tool_schema)} tools")
        
        except Exception as e:
            logger.error(f"Failed to load tools: {e}")
            self.tool_schema = {}
            self.available_tools = {}
    
    def _serialize_tools_for_prompt(self, tools_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Serialize tools for LLM comprehension"""
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
        Execute user request through Reason → Plan → Act cycle.
        
        Returns:
            {"status": "success|error", "response": "..."}
        """
        try:
            # Create state manager
            session_id = str(uuid.uuid4())[:8]
            state_manager = AgentStateManager(session_id, user_request, MAX_ITERATIONS)
            
            logger.info(f"Executing request: {user_request[:100]}")
            
            # Run main loop with timeout
            try:
                await asyncio.wait_for(
                    self._reason_plan_act_loop(state_manager),
                    timeout=REQUEST_TIMEOUT
                )
            except asyncio.TimeoutError:
                state_manager.set_timeout()
                logger.warning(f"Request timed out after {REQUEST_TIMEOUT}s")
            
            # Return result
            if state_manager.state.final_answer:
                return {
                    "status": "success",
                    "response": state_manager.state.final_answer
                }
            else:
                error_msg = state_manager.state.error_message or "No result generated"
                return {
                    "status": "error",
                    "response": error_msg
                }
        
        except Exception as e:
            logger.error(f"Request execution failed: {e}")
            return {
                "status": "error",
                "response": f"Fatal error: {str(e)}"
            }
    
    async def _reason_plan_act_loop(self, state_manager: AgentStateManager) -> None:
        """
        Main Reason → Plan → Act cycle loop.
        Continues until goal achieved or max iterations reached.
        """
        
        while state_manager.state.iteration < state_manager.state.max_iterations:
            
            state_manager.increment_iteration()
            logger.info(f"\n{'='*60}")
            logger.info(f"ITERATION {state_manager.state.iteration}/{state_manager.state.max_iterations}")
            logger.info(f"{'='*60}")
            
            try:
                # ===== PHASE 1: REASONING =====
                logger.info("→ REASONING PHASE")
                reasoning_output = await self.reasoning_engine.reason(
                    state_manager.state.user_request,
                    state_manager.state,
                    self.memory_manager.get_short_term_context(),
                    self.memory_manager.get_long_term_context()
                )
                state_manager.state.add_reasoning(reasoning_output)
                logger.info(f"  Goal: {reasoning_output.goal_analysis[:80]}")
                
                # ===== PHASE 2: PLANNING =====
                logger.info("→ PLANNING PHASE")
                planning_output = await self.planning_engine.plan(
                    state_manager.state.user_request,
                    reasoning_output,
                    state_manager.state
                )
                state_manager.state.add_planning(planning_output)
                logger.info(f"  Strategy: {planning_output.overall_strategy[:80]}")
                logger.info(f"  Steps planned: {len(planning_output.step_by_step_plan)}")
                
                # ===== PHASE 3: ACTING =====
                logger.info("→ ACTION PHASE")
                action_output = await self.action_executor.execute(planning_output)
                state_manager.state.add_action(action_output)
                
                if action_output.success:
                    logger.info(f"  ✓ Tool executed: {action_output.tool_name}")
                    
                    # Auto-save to memory
                    await self.memory_manager.save_short_term(
                        self.workspace_id,
                        f"action_{state_manager.state.iteration}",
                        {
                            "tool": action_output.tool_name,
                            "result": action_output.result.get("data", {})
                        }
                    )
                else:
                    logger.warning(f"  ✗ Tool failed: {action_output.error_message}")
                
                # ===== PHASE 4: OBSERVATION =====
                logger.info("→ OBSERVATION PHASE")
                observation_output = await self.observation_engine.observe(
                    action_output,
                    planning_output.success_criteria,
                    state_manager.state,
                    state_manager.state.user_request
                )
                state_manager.state.add_observation(observation_output)
                
                # Check if goal achieved
                if observation_output.iteration_complete:
                    logger.info(f"  ✓ Goal achieved!")
                    state_manager.set_complete(
                        f"Goal achieved. Key findings: {', '.join(observation_output.key_findings)}"
                    )
                    break
                
                logger.info(f"  Findings: {', '.join(observation_output.key_findings[:2]) or 'None yet'}")
            
            except Exception as e:
                logger.error(f"Iteration failed: {e}")
                state_manager.set_failed(str(e))
                break
        
        # Check max iterations
        if state_manager.state.iteration >= state_manager.state.max_iterations:
            logger.warning(f"Reached max iterations ({MAX_ITERATIONS})")
            if not state_manager.state.final_answer:
                state_manager.set_complete(
                    "Max iterations reached. Review collected findings above."
                )
        
        # Consolidate learning
        try:
            await self.memory_manager.consolidate_learning(
                self.workspace_id,
                {
                    "actions_taken": len(state_manager.state.action_history),
                    "tools_used": [a.tool_name for a in state_manager.state.action_history],
                    "findings": self._extract_findings(state_manager.state)
                }
            )
        except Exception as e:
            logger.warning(f"Failed to consolidate learning: {e}")
    
    def _extract_findings(self, state: AgentState) -> List[str]:
        """Extract key findings from observations"""
        findings = []
        for obs in state.observation_history:
            findings.extend(obs.key_findings)
        return findings[:10]  # Limit to 10 findings
    
    async def run_tool(self, tool_name: str, args: Dict) -> Dict:
        """
        Execute a tool via MCP.
        
        Returns:
            Standardized ToolResult dict
        """
        if "workspace_id" not in args:
            args["workspace_id"] = self.workspace_id
        
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
            
            # Execute tool
            import asyncio
            if asyncio.iscoroutinefunction(callable_obj):
                result = await callable_obj(**filtered_args)
            else:
                result = callable_obj(**filtered_args)
            
            # Ensure dict response
            if not isinstance(result, dict):
                return {"status": "error", "error": f"Tool returned non-dict: {type(result)}"}
            
            if "status" not in result:
                return {"status": "success", "data": result}
            
            return result
        
        except Exception as e:
            logger.error(f"Tool execution failed: {e}")
            return {"status": "error", "error": str(e)}


# ============================================================================
# LEGACY COMPATIBILITY - Keep old Agent class as wrapper
# ============================================================================

class Agent(PenzerAgent):
    """Legacy Agent class - now extends PenzerAgent for backward compatibility"""
    pass


if __name__ == "__main__":
    """Test agent locally"""
    
    async def test():
        logger.info("Testing PenzerAgent...")
        agent = await PenzerAgent().async_init()
        
        test_request = "List available system tools"
        result = await agent.execute_user_request(test_request)
        
        logger.info(f"Result: {result}")
    
    try:
        asyncio.run(test())
    except KeyboardInterrupt:
        logger.info("Test interrupted")
    except Exception as e:
        logger.error(f"Test failed: {e}")
