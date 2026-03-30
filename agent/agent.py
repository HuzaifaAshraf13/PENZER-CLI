# agent/agent.py
"""
Clean, modular pentesting agent with MCP integration.
Handles: skill selection, tool execution via MCP, memory management, ReAct loop.
"""

import asyncio
import logging
import json
import time
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from enum import Enum
from datetime import datetime

from agent.core import mcp, init_reme, cleanup_reme
from agent.llm import LLM
from agent.skill_selector import PentestPhaseDetector, SkillSelector
from agent.skills import PentestPhase, load_all_skills

logger = logging.getLogger("penzer.agent")


class ToolExecutionStatus(Enum):
    """Status of tool execution."""
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    PARTIAL = "partial"


@dataclass
class ToolResult:
    """Result from a tool execution."""
    status: ToolExecutionStatus
    data: Dict[str, Any] = None
    error: Optional[str] = None
    execution_time_ms: float = 0.0
    
    def __post_init__(self):
        if self.data is None:
            self.data = {}


class Agent:
    """
    Pentesting agent that:
    1. Connects to MCP server for tools
    2. Selects skills based on user intent
    3. Executes tools via MCP
    4. Manages memory (short-term and long-term)
    5. Implements ReAct loop for autonomous decision-making
    """
    
    def __init__(self, max_iterations: int = 10, tool_timeout_sec: float = 30):
        """
        Initialize agent.
        
        Args:
            max_iterations: Max ReAct loop iterations
            tool_timeout_sec: Timeout per tool execution
        """
        self.max_iterations = max_iterations
        self.tool_timeout_sec = tool_timeout_sec
        self.workspace_id = "pentest_1"
        
        # These will be initialized in async_init()
        self.mcp_client = None
        self.tools_dict = {}
        self.llm = None
        self.all_skills = {}
        self.phase_detector = None
        self.skill_selector = None
        
        # Current state
        self.current_phase = None
        self.current_skill = None
        self.iteration_count = 0
        
        logger.info("✓ Agent initialized (awaiting async_init())")
    
    async def async_init(self):
        """
        Async initialization: connect to MCP, load skills, setup tools.
        Must be called before using the agent.
        """
        try:
            logger.info("Starting async initialization...")
            
            # 0. Import tool modules to register them with MCP
            logger.debug("Registering tool modules with MCP...")
            try:
                import tools.tools
                logger.debug("  ✓ tools.tools registered")
            except Exception as e:
                logger.warning(f"Failed to import tools.tools: {e}")
            
            try:
                import session.session
                logger.debug("  ✓ session.session registered")
            except Exception as e:
                logger.warning(f"Failed to import session.session: {e}")
            
            # 1. Initialize MCP connection
            logger.debug("Connecting to MCP server...")
            self.mcp_client = mcp
            
            # 2. Load tools from MCP
            logger.debug("Loading tools from MCP...")
            self.tools_dict = {}
            
            # Try multiple strategies to get tools
            if hasattr(self.mcp_client, '_tool_manager') and hasattr(self.mcp_client._tool_manager, '_tools'):
                self.tools_dict = self.mcp_client._tool_manager._tools
                logger.debug(f"Loaded tools via _tool_manager._tools")
            elif hasattr(self.mcp_client, 'tools'):
                self.tools_dict = self.mcp_client.tools
                logger.debug(f"Loaded tools via .tools attribute")
            else:
                logger.warning("Could not load tools from MCP")
            
            logger.info(f"✓ Loaded {len(self.tools_dict)} MCP tools")
            if self.tools_dict:
                logger.debug(f"  Tools: {list(self.tools_dict.keys())}")
            
            # 3. Initialize LLM
            logger.debug("Initializing LLM...")
            self.llm = LLM()
            logger.info("✓ LLM initialized")
            
            # 4. Load skills
            logger.debug("Loading skills...")
            self.all_skills = load_all_skills()
            phase_count = len(self.all_skills)
            skill_count = sum(len(skills) for skills in self.all_skills.values())
            logger.info(f"✓ Loaded {skill_count} skills across {phase_count} phases")
            
            # 5. Initialize skill selector
            logger.debug("Initializing skill selector...")
            self.phase_detector = PentestPhaseDetector()
            self.skill_selector = SkillSelector(self.all_skills)
            logger.info("✓ Skill selector ready")
            
            # 6. Initialize long-term memory
            logger.debug("Initializing ReMeApp for long-term memory...")
            await init_reme()
            logger.info("✓ Long-term memory initialized")
            
            logger.info("Async initialization complete ✓\n")
            
        except Exception as e:
            logger.error(f"Failed to initialize agent: {e}")
            raise
    
    async def execute_user_request(self, user_input: str) -> Dict[str, Any]:
        """
        Execute a user request through the ReAct loop.
        
        Args:
            user_input: User's request (e.g., "scan localhost")
            
        Returns:
            Dict with status, response, and metadata
        """
        logger.info(f"\n{'='*60}")
        logger.info(f"USER REQUEST: {user_input}")
        logger.info(f"{'='*60}\n")
        
        try:
            # 1. Detect phase from user input
            detected_phase, phase_confidence = self.phase_detector.detect_phase(user_input)
            logger.info(f"📍 Detected phase: {detected_phase.value} (confidence: {phase_confidence:.2f})")
            self.current_phase = detected_phase
            
            # 2. Select best skill for this request
            selected_skill, skill_confidence = self.skill_selector.select_skill(
                user_input, 
                current_phase=detected_phase
            )
            logger.info(f"🎯 Selected skill: {selected_skill.get('name')} (confidence: {skill_confidence:.2f})")
            self.current_skill = selected_skill
            
            # 3. Run ReAct loop
            logger.info(f"\n🔄 Starting ReAct loop (max {self.max_iterations} iterations)...")
            react_result = await self._react_loop(user_input, selected_skill)
            
            # 4. Return results
            return {
                "status": "success",
                "response": react_result,
                "phase": detected_phase.value,
                "skill": selected_skill.get("name"),
                "iterations": self.iteration_count
            }
            
        except Exception as e:
            logger.error(f"Error executing request: {e}")
            return {
                "status": "error",
                "response": str(e),
                "phase": self.current_phase.value if self.current_phase else None,
                "skill": self.current_skill.get("name") if self.current_skill else None
            }
        finally:
            # Cleanup
            self.iteration_count = 0
            await cleanup_reme()
    
    async def _react_loop(self, user_request: str, skill: Dict[str, Any]) -> str:
        """
        ReAct loop: Reasoning → Action → Observation → Repeat
        
        Args:
            user_request: Original user request
            skill: Selected skill
            
        Returns:
            Final response after loop completes
        """
        system_prompt = self._build_system_prompt(skill)
        context_history = []
        
        for iteration in range(1, self.max_iterations + 1):
            self.iteration_count = iteration
            logger.info(f"\n--- Iteration {iteration}/{self.max_iterations} ---")
            
            try:
                # 1. THINK: Get LLM reasoning
                logger.debug("LLM thinking...")
                llm_response = await self.llm.generate(
                    system_prompt=system_prompt,
                    user_message=user_request,
                    context=context_history,
                    temperature=0.3
                )
                
                logger.debug(f"LLM response:\n{llm_response}")
                
                # 2. Parse LLM response
                action = self._parse_llm_response(llm_response)
                
                if action.get("type") == "final_answer":
                    logger.info(f"\n✅ Agent complete: {action.get('content')}")
                    return action.get("content", "Task completed")
                
                # 3. ACTION: Execute tool
                if action.get("type") == "tool":
                    tool_name = action.get("tool")
                    tool_args = action.get("args", {})
                    
                    logger.info(f"🔧 Executing tool: {tool_name}")
                    tool_result = await self.run_tool(tool_name, tool_args)
                    
                    # 4. OBSERVATION: Store result and update context
                    observation = {
                        "iteration": iteration,
                        "tool": tool_name,
                        "status": tool_result.status.value,
                        "result": tool_result.data if tool_result.status == ToolExecutionStatus.SUCCESS else tool_result.error,
                        "time_ms": tool_result.execution_time_ms
                    }
                    context_history.append(observation)
                    
                    logger.info(f"✓ Tool completed: {tool_result.status.value} ({tool_result.execution_time_ms:.0f}ms)")
                    
                    # Auto-save to memory
                    await self._save_to_memory(tool_name, tool_result)
                    
                    # Add observation to next LLM prompt
                    user_request = self._format_observation(observation)
                else:
                    logger.warning(f"Unknown action type: {action.get('type')}")
                    break
                    
            except Exception as e:
                logger.error(f"Iteration {iteration} failed: {e}")
                if iteration >= self.max_iterations:
                    return f"Failed after {iteration} iterations: {e}"
                continue
        
        return f"Completed {self.max_iterations} iterations"
    
    def _build_system_prompt(self, skill: Dict[str, Any]) -> str:
        """Build system prompt for LLM based on selected skill."""
        return f"""You are a penetration testing agent executing a {self.current_phase.value} phase task.

SKILL: {skill.get('name')}
DESCRIPTION: {skill.get('description')}

INSTRUCTIONS:
{skill.get('agent_behavior', '')}

RESPONSE FORMAT:
You MUST respond with ONLY valid JSON in ONE of these formats:

1. To execute a tool:
{{"type": "tool", "tool": "tool_name", "args": {{"param": "value"}}, "reasoning": "why you chose this action"}}

2. To complete the task:
{{"type": "final_answer", "content": "summary of findings"}}

IMPORTANT:
- Always provide JSON only, no other text
- Use available tools to gather information
- Execute commands autonomously - do not ask for clarification
- Save important findings using memory tools
"""
    
    def _parse_llm_response(self, response: str) -> Dict[str, Any]:
        """Parse LLM response as JSON."""
        try:
            # Try to extract JSON from response
            json_start = response.find('{')
            json_end = response.rfind('}') + 1
            if json_start >= 0 and json_end > json_start:
                json_str = response[json_start:json_end]
                return json.loads(json_str)
        except json.JSONDecodeError:
            pass
        
        # Default: return final answer
        return {
            "type": "final_answer",
            "content": response[:500]
        }
    
    def _format_observation(self, observation: Dict[str, Any]) -> str:
        """Format tool execution result as observation for LLM."""
        if observation["status"] == "success":
            return f"Tool '{observation['tool']}' executed successfully. Result: {json.dumps(observation['result'])[:500]}"
        else:
            return f"Tool '{observation['tool']}' failed: {observation['result']}"
    
    async def run_tool(self, tool_name: str, args: Dict[str, Any]) -> ToolResult:
        """
        Execute a tool through the MCP server.
        
        Args:
            tool_name: Name of tool to execute
            args: Tool arguments
            
        Returns:
            ToolResult with status and data
        """
        import inspect
        
        start_time = time.time()
        
        try:
            # 1. Validate tool exists
            if tool_name not in self.tools_dict:
                logger.error(f"Tool not found: {tool_name}")
                logger.debug(f"Available tools: {list(self.tools_dict.keys())}")
                return ToolResult(
                    status=ToolExecutionStatus.ERROR,
                    error=f"Unknown tool: {tool_name}"
                )
            
            tool = self.tools_dict[tool_name]
            
            # 2. Get the callable function
            callable_obj = tool
            if hasattr(tool, "fn"):
                callable_obj = tool.fn
            elif hasattr(tool, "__wrapped__"):
                callable_obj = tool.__wrapped__
            
            if not callable(callable_obj):
                logger.error(f"Tool {tool_name} is not callable")
                return ToolResult(
                    status=ToolExecutionStatus.ERROR,
                    error=f"Tool {tool_name} is not callable"
                )
            
            # 3. Filter arguments based on function signature
            filtered_args = args.copy()
            try:
                sig = inspect.signature(callable_obj)
                params = set(sig.parameters.keys())
                
                # Only keep args that the function accepts
                filtered_args = {k: v for k, v in args.items() if k in params}
                
                logger.debug(f"  {tool_name} params: {params}, filtered args: {list(filtered_args.keys())}")
            except Exception as e:
                logger.debug(f"  Could not inspect signature: {e}, using all args")
            
            logger.debug(f"  Calling {tool_name}({list(filtered_args.keys())})")
            
            # 4. Execute tool with timeout
            is_async = asyncio.iscoroutinefunction(callable_obj)
            
            if is_async:
                result = await asyncio.wait_for(
                    callable_obj(**filtered_args),
                    timeout=self.tool_timeout_sec
                )
            else:
                result = await asyncio.wait_for(
                    asyncio.to_thread(callable_obj, **filtered_args),
                    timeout=self.tool_timeout_sec
                )
            
            execution_time_ms = (time.time() - start_time) * 1000
            
            logger.debug(f"  ✓ Tool returned: {type(result)} in {execution_time_ms:.0f}ms")
            
            return ToolResult(
                status=ToolExecutionStatus.SUCCESS,
                data=result if isinstance(result, dict) else {"output": str(result)},
                execution_time_ms=execution_time_ms
            )
            
        except asyncio.TimeoutError:
            execution_time_ms = (time.time() - start_time) * 1000
            logger.error(f"Tool execution timeout after {execution_time_ms:.0f}ms")
            return ToolResult(
                status=ToolExecutionStatus.TIMEOUT,
                error=f"Tool execution timeout ({self.tool_timeout_sec}s exceeded)",
                execution_time_ms=execution_time_ms
            )
        except Exception as e:
            execution_time_ms = (time.time() - start_time) * 1000
            logger.error(f"Tool execution failed: {e}")
            return ToolResult(
                status=ToolExecutionStatus.ERROR,
                error=str(e),
                execution_time_ms=execution_time_ms
            )
    
    async def _save_to_memory(self, tool_name: str, result: ToolResult) -> None:
        """
        Auto-save tool execution result to memory.
        
        Args:
            tool_name: Name of executed tool
            result: Tool execution result
        """
        try:
            memory_entry = {
                "tool": tool_name,
                "timestamp": datetime.now().isoformat(),
                "phase": self.current_phase.value if self.current_phase else "unknown",
                "status": result.status.value,
                "data": str(result.data)[:200] if result.data else None
            }
            
            # Save to short-term memory
            try:
                short_result = await self.run_tool("mem_set_short", {
                    "workspace_id": self.workspace_id,
                    "data": {f"{tool_name}_{int(time.time())}": memory_entry}
                })
                if short_result.status == ToolExecutionStatus.SUCCESS:
                    logger.debug(f"✓ Saved to short-term memory")
            except Exception as e:
                logger.debug(f"Short-term memory save failed: {e}")
            
            # Save to long-term memory
            try:
                long_result = await self.run_tool("mem_set_long", {
                    "workspace_id": self.workspace_id,
                    "data": {f"{tool_name}_{self.current_phase.value}": memory_entry}
                })
                if long_result.status == ToolExecutionStatus.SUCCESS:
                    logger.debug(f"✓ Saved to long-term memory")
            except Exception as e:
                logger.debug(f"Long-term memory save failed: {e}")
                
        except Exception as e:
            logger.debug(f"Memory save failed: {e}")
