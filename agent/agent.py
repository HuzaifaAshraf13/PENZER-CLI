# agent/agent_refactored.py
"""
Modular, async-safe pentesting agent with robust error handling and optimized memory management.
Implements ReAct workflow with skill-driven planning, async-compatible tool execution, 
and persistent memory consolidation.
"""

import json
import asyncio
import inspect
import logging
import time
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum

from agent.core import mcp, init_reme, cleanup_reme
from agent.llm import LLM
from agent.skill_selector import (
    SkillSelector,
    create_skill_aware_system_prompt,
    PentestPhaseDetector
)
from agent.skills import PentestPhase, load_all_skills

# Configure logging
logger = logging.getLogger("penzer.agent")
logger.setLevel(logging.DEBUG)

# Optional imports with fallback
try:
    import session.sessionprompts
except ImportError:
    logger.debug("session.sessionprompts not available")

try:
    import tools.ToolsPrompts
except ImportError:
    logger.debug("tools.ToolsPrompts not available")

try:
    import session.session
except ImportError:
    logger.debug("session.session not available")


# ================ DATA STRUCTURES ================

class ToolExecutionStatus(Enum):
    """Status of tool execution."""
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    TIMEOUT = "timeout"
    RETRYABLE = "retryable"


@dataclass
class ToolResult:
    """Standardized result from tool execution."""
    status: ToolExecutionStatus
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    execution_time_ms: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "status": self.status.value,
            "data": self.data,
            "error": self.error,
            "metadata": self.metadata,
            "execution_time_ms": self.execution_time_ms
        }
    
    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "ToolResult":
        """Create from dictionary."""
        return ToolResult(
            status=ToolExecutionStatus(data.get("status", "error")),
            data=data.get("data", {}),
            error=data.get("error"),
            metadata=data.get("metadata", {}),
            execution_time_ms=data.get("execution_time_ms", 0.0)
        )


@dataclass
class LLMDecision:
    """Parsed decision from LLM."""
    thought: str
    tool: Optional[str] = None
    args: Dict[str, Any] = field(default_factory=dict)
    final_answer: Optional[str] = None
    confidence: float = 0.8
    raw_response: str = ""
    
    def is_complete(self) -> bool:
        """Check if decision has final answer (workflow complete)."""
        return self.final_answer is not None
    
    def has_action(self) -> bool:
        """Check if decision specifies a tool to call."""
        return self.tool is not None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "thought": self.thought,
            "tool": self.tool,
            "args": self.args,
            "final_answer": self.final_answer,
            "confidence": self.confidence
        }


@dataclass
class ExecutionMetrics:
    """Metrics for workflow execution."""
    total_iterations: int = 0
    successful_tools: int = 0
    failed_tools: int = 0
    total_execution_time_ms: float = 0.0
    tool_executions: Dict[str, int] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)


# ================ AGENT CLASS ================

class Agent:
    """
    Autonomous pentesting agent with ReAct workflow, skill-driven planning,
    and async-safe tool execution.
    """
    
    # Constants
    DEFAULT_MEMORY_FETCH_TIMEOUT_SEC = 2.0
    DEFAULT_MEMORY_DISPLAY_LIMIT = 5
    DEFAULT_LONG_TERM_MEMORY_DISPLAY_LIMIT = 3
    
    def __init__(
        self,
        max_iterations: int = 10,
        llm_timeout_sec: float = 30.0,
        tool_timeout_sec: float = 60.0,
        tool_retries: int = 2,
        workspace_id: str = "default"
    ):
        """
        Initialize Agent with configuration.
        
        Args:
            max_iterations: Maximum workflow iterations
            llm_timeout_sec: Timeout for LLM calls
            tool_timeout_sec: Timeout for tool execution
            tool_retries: Number of retry attempts for tools
            workspace_id: Session workspace identifier
        """
        self.max_iterations = max_iterations
        self.llm_timeout_sec = llm_timeout_sec
        self.tool_timeout_sec = tool_timeout_sec
        self.tool_retries = tool_retries
        self.workspace_id = workspace_id
        
        # State
        self.llm = LLM()
        self.mcp_client = mcp
        self.tool_schema: Dict[str, Any] = {}
        self.resource_uris: List[str] = []
        self.formatted_system_prompt: str = ""
        self.all_skills = load_all_skills()
        self.skill_selector: Optional[SkillSelector] = None
        self.current_phase: Optional[PentestPhase] = None
        self.current_skill: Optional[Dict[str, Any]] = None
        self.message_history: List[Dict[str, str]] = []
        self.metrics = ExecutionMetrics()
    
    async def async_init(self) -> "Agent":
        """Async initialization of agent."""
        logger.info("Initializing Penzer Agent...")
        
        # CRITICAL: Import tool modules to register them with MCP
        # Must happen BEFORE _load_tool_schema() so @mcp.tool() decorators are registered
        try:
            import session.session
            logger.debug("✓ Registered session.session (memory tools)")
        except Exception as e:
            logger.debug(f"Failed to import session.session: {e}")
        
        try:
            import tools.tools
            logger.debug("✓ Registered tools.tools (security tools)")
        except Exception as e:
            logger.debug(f"Failed to import tools.tools: {e}")
        
        # Initialize memory
        reme_success = await init_reme()
        if not reme_success:
            logger.warning("Long-term memory unavailable, using short-term only")
        
        # Load tools and resources
        self.tool_schema = await self._load_tool_schema()
        self.resource_uris = self._load_resource_uris()
        
        # Initialize skill selector
        self.skill_selector = SkillSelector(self.all_skills)
        
        logger.info(f"✓ Agent initialized: {len(self.tool_schema)} tools, {len(self.resource_uris)} resources")
        return self
    
    async def _load_tool_schema(self) -> Dict[str, Any]:
        """Load tool schema from MCP with robust fallbacks."""
        try:
            # Try 1: Async get_tools() method
            if hasattr(self.mcp_client, "get_tools") and callable(getattr(self.mcp_client, "get_tools")):
                try:
                    tools_dict = await self.mcp_client.get_tools()
                    logger.debug(f"✓ Loaded {len(tools_dict)} tools via async get_tools()")
                    return tools_dict
                except Exception as e:
                    logger.debug(f"get_tools() failed: {e}")
            
            # Try 2: Direct .tools attribute
            if hasattr(self.mcp_client, "tools"):
                tools_dict = getattr(self.mcp_client, "tools", {})
                if tools_dict:
                    logger.debug(f"✓ Loaded {len(tools_dict)} tools via .tools attribute")
                    return tools_dict
            
            # Try 3: Internal ._tools attribute
            if hasattr(self.mcp_client, "_tools"):
                tools_dict = getattr(self.mcp_client, "_tools", {})
                if tools_dict:
                    logger.debug(f"✓ Loaded {len(tools_dict)} tools via ._tools attribute")
                    return tools_dict
        
        except Exception as e:
            logger.error(f"Error loading tool schema: {e}")
        
        logger.warning("No tools loaded from MCP")
        return {}
    
    def _load_resource_uris(self) -> List[str]:
        """Load resource URIs from MCP."""
        try:
            resources = getattr(self.mcp_client, "resources", {})
            uris = list(resources.keys())
            logger.debug(f"✓ Loaded {len(uris)} resources")
            return uris
        except Exception as e:
            logger.warning(f"Error loading resources: {e}")
            return []
    
    async def run_tool(
        self,
        tool_name: str,
        args: Dict[str, Any],
        retry_count: int = 0
    ) -> ToolResult:
        """
        Execute an MCP tool with retry logic and timeout handling.
        
        Args:
            tool_name: Name of the tool to execute
            args: Arguments to pass to the tool
            retry_count: Current retry attempt
            
        Returns:
            ToolResult with status, data, and metadata
        """
        start_time = time.time()
        
        try:
            # Get tools dict
            tools_dict = self.tool_schema or {}
            if not tools_dict:
                tools_dict = await self._load_tool_schema()
            
            # Lookup tool
            if tool_name not in tools_dict:
                available = ", ".join(sorted(tools_dict.keys())[:10])
                error_msg = f"Tool '{tool_name}' not found. Available: {available}"
                logger.error(error_msg)
                self.metrics.failed_tools += 1
                self.metrics.errors.append(error_msg)
                return ToolResult(
                    status=ToolExecutionStatus.ERROR,
                    error=error_msg,
                    execution_time_ms=(time.time() - start_time) * 1000
                )
            
            tool = tools_dict[tool_name]
            
            # Extract callable object
            callable_obj = tool
            if hasattr(tool, "fn"):
                callable_obj = tool.fn
            elif hasattr(tool, "__wrapped__"):
                callable_obj = tool.__wrapped__
            elif hasattr(tool, "func"):
                callable_obj = tool.func
            
            # Filter args to match function signature
            try:
                sig = inspect.signature(callable_obj)
                params = set(sig.parameters.keys())
                filtered_args = {k: v for k, v in args.items() if k in params}
            except (ValueError, TypeError):
                filtered_args = args
            
            # Execute tool
            logger.debug(f"Executing tool: {tool_name} with args: {filtered_args}")
            
            if asyncio.iscoroutinefunction(callable_obj):
                # Async tool
                result = await asyncio.wait_for(
                    callable_obj(**filtered_args),
                    timeout=self.tool_timeout_sec
                )
            else:
                # Sync tool - run in thread pool
                result = await asyncio.wait_for(
                    asyncio.to_thread(callable_obj, **filtered_args),
                    timeout=self.tool_timeout_sec
                )
            
            execution_time_ms = (time.time() - start_time) * 1000
            self.metrics.successful_tools += 1
            self.metrics.tool_executions[tool_name] = self.metrics.tool_executions.get(tool_name, 0) + 1
            
            logger.info(f"✓ Tool '{tool_name}' completed in {execution_time_ms:.0f}ms")
            
            return ToolResult(
                status=ToolExecutionStatus.SUCCESS,
                data=result if isinstance(result, dict) else {"result": result},
                execution_time_ms=execution_time_ms
            )
        
        except asyncio.TimeoutError:
            execution_time_ms = (time.time() - start_time) * 1000
            logger.warning(f"⏱ Tool '{tool_name}' timed out after {execution_time_ms:.0f}ms")
            
            # Retry on timeout
            if retry_count < self.tool_retries:
                await asyncio.sleep(0.5 * (retry_count + 1))
                logger.info(f"Retrying '{tool_name}' (attempt {retry_count + 1}/{self.tool_retries})")
                return await self.run_tool(tool_name, args, retry_count + 1)
            
            self.metrics.failed_tools += 1
            self.metrics.errors.append(f"Timeout: {tool_name}")
            return ToolResult(
                status=ToolExecutionStatus.TIMEOUT,
                error=f"Tool execution timed out after {self.tool_timeout_sec}s",
                execution_time_ms=execution_time_ms
            )
        
        except (ConnectionError, OSError) as e:
            execution_time_ms = (time.time() - start_time) * 1000
            logger.warning(f"⚠ Retryable error in '{tool_name}': {type(e).__name__}")
            
            # Retry on connection errors
            if retry_count < self.tool_retries:
                await asyncio.sleep(0.5 * (retry_count + 1))
                logger.info(f"Retrying '{tool_name}' (attempt {retry_count + 1}/{self.tool_retries})")
                return await self.run_tool(tool_name, args, retry_count + 1)
            
            self.metrics.failed_tools += 1
            self.metrics.errors.append(f"{type(e).__name__}: {tool_name}")
            return ToolResult(
                status=ToolExecutionStatus.RETRYABLE,
                error=str(e),
                execution_time_ms=execution_time_ms
            )
        
        except Exception as e:
            execution_time_ms = (time.time() - start_time) * 1000
            logger.error(f"✗ Tool '{tool_name}' failed: {e}", exc_info=True)
            self.metrics.failed_tools += 1
            self.metrics.errors.append(f"{type(e).__name__}: {str(e)[:100]}")
            return ToolResult(
                status=ToolExecutionStatus.ERROR,
                error=str(e),
                execution_time_ms=execution_time_ms
            )
    
    def _parse_llm_decision(self, raw: str) -> LLMDecision:
        """
        Parse LLM response with robust JSON handling.
        
        Implements 5-tier fallback strategy:
        1. Direct JSON parse
        2. Markdown code block removal
        3. Nested JSON extraction
        4. Partial JSON extraction
        5. Wrap as thought field
        """
        raw = raw.strip()
        
        # Try 1: Direct JSON parse
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return LLMDecision(
                    thought=data.get("thought", ""),
                    tool=data.get("tool"),
                    args=data.get("args", {}),
                    final_answer=data.get("final_answer"),
                    raw_response=raw
                )
        except json.JSONDecodeError:
            pass
        
        # Try 2: Remove markdown code blocks
        try:
            cleaned = raw.replace("```json", "").replace("```", "").strip()
            data = json.loads(cleaned)
            if isinstance(data, dict):
                return LLMDecision(
                    thought=data.get("thought", ""),
                    tool=data.get("tool"),
                    args=data.get("args", {}),
                    final_answer=data.get("final_answer"),
                    raw_response=raw
                )
        except json.JSONDecodeError:
            pass
        
        # Try 3: Extract nested JSON from thought
        try:
            start_idx = raw.find('{')
            end_idx = raw.rfind('}') + 1
            if start_idx != -1 and end_idx > start_idx:
                json_str = raw[start_idx:end_idx]
                data = json.loads(json_str)
                if isinstance(data, dict):
                    return LLMDecision(
                        thought=data.get("thought", raw),
                        tool=data.get("tool"),
                        args=data.get("args", {}),
                        final_answer=data.get("final_answer"),
                        raw_response=raw
                    )
        except json.JSONDecodeError:
            pass
        
        # Try 4: Extract partial JSON (first { to last })
        try:
            start_idx = raw.find('{')
            end_idx = raw.rfind('}')
            if start_idx != -1 and end_idx > start_idx:
                json_str = raw[start_idx:end_idx + 1]
                data = json.loads(json_str)
                if isinstance(data, dict):
                    return LLMDecision(
                        thought=data.get("thought", raw),
                        tool=data.get("tool"),
                        args=data.get("args", {}),
                        final_answer=data.get("final_answer"),
                        raw_response=raw
                    )
        except json.JSONDecodeError:
            pass
        
        # Try 5: Wrap entire response as thought
        logger.warning("Failed to parse LLM response, wrapping as thought")
        return LLMDecision(
            thought=raw,
            tool=None,
            args={},
            final_answer=None,
            raw_response=raw
        )
    
    async def process_input(
        self,
        user_request: str,
        workspace_id: Optional[str] = None
    ) -> str:
        """
        Process user input through ReAct workflow.
        
        Args:
            user_request: User's request or query
            workspace_id: Optional workspace identifier
            
        Returns:
            Final answer from the workflow
        """
        workspace_id = workspace_id or self.workspace_id
        self.message_history = []
        self.metrics = ExecutionMetrics()
        
        logger.info(f"Starting workflow for: {user_request[:100]}")
        
        # 1. Detect phase and select skill
        if not self.skill_selector:
            self.skill_selector = SkillSelector(self.all_skills)
        
        skill, phase, confidence = self.skill_selector.select_skill(user_request)
        self.current_phase = phase
        self.current_skill = self.skill_selector.skill_to_dict(skill) if skill else None
        
        logger.info(f"Selected phase: {phase.value} (confidence: {confidence:.2f})")
        logger.info(f"Selected skill: {self.current_skill.get('name', 'Unknown') if self.current_skill else 'None'}")
        
        # 2. Fetch memory
        short_term_memory = {}
        try:
            mem_result = await self.run_tool("mem_get_short", {"workspace_id": workspace_id})
            if mem_result.status == ToolExecutionStatus.SUCCESS:
                short_term_memory = mem_result.data
        except Exception as e:
            logger.warning(f"Failed to fetch short-term memory: {e}")
        
        # 3. Build system prompt
        base_context = f"Workspace: {workspace_id}\nPhase: {phase.value}"
        if short_term_memory:
            base_context += f"\nCurrent Memory: {json.dumps(short_term_memory, indent=2)[:500]}"
        
        system_prompt = create_skill_aware_system_prompt(self.current_skill, base_context)
        
        # 4. Main ReAct loop
        for iteration in range(self.max_iterations):
            logger.debug(f"Iteration {iteration + 1}/{self.max_iterations}")
            self.metrics.total_iterations = iteration + 1
            
            # Get LLM decision
            try:
                llm_input = f"{system_prompt}\n\nUser: {user_request}"
                decision_raw = await asyncio.wait_for(
                    asyncio.to_thread(self.llm.generate_content, llm_input),
                    timeout=self.llm_timeout_sec
                )
                decision = self._parse_llm_decision(decision_raw)
                logger.debug(f"Decision: thought='{decision.thought[:50]}...', tool={decision.tool}, final_answer={decision.final_answer is not None}")
            
            except asyncio.TimeoutError:
                logger.error("LLM call timed out")
                return "Error: LLM call timed out"
            except Exception as e:
                logger.error(f"LLM error: {e}")
                return f"Error: {str(e)}"
            
            # Check if complete
            if decision.is_complete():
                logger.info(f"Workflow complete: {decision.final_answer[:100]}")
                return decision.final_answer
            
            # Execute tool if specified
            if decision.has_action():
                tool_result = await self.run_tool(decision.tool, decision.args)
                logger.info(f"Tool result: {tool_result.status.value}")
                
                # Auto-save to memory
                await self._auto_save_to_memory(workspace_id, decision.tool, tool_result)
                
                # Add to message history
                self.message_history.append({
                    "role": "assistant",
                    "content": decision.raw_response
                })
                self.message_history.append({
                    "role": "tool",
                    "tool": decision.tool,
                    "content": json.dumps(tool_result.to_dict())
                })
            else:
                logger.warning("No action specified, using final answer as thought")
                return decision.thought
        
        logger.warning(f"Reached max iterations ({self.max_iterations})")
        return "Max iterations reached without completion"
    
    async def _auto_save_to_memory(
        self,
        workspace_id: str,
        tool_name: str,
        result: ToolResult
    ) -> None:
        """Auto-save tool results to memory."""
        try:
            # Short-term save
            await self.run_tool("mem_set_short", {
                "workspace_id": workspace_id,
                f"{tool_name}_result": {
                    "status": result.status.value,
                    "timestamp": datetime.now().isoformat(),
                    "data_summary": str(result.data)[:200]
                }
            })
        except Exception as e:
            logger.warning(f"Failed to save to short-term memory: {e}")
        
        try:
            # Long-term save with timeout
            await asyncio.wait_for(
                self.run_tool("mem_set_long", {
                    "workspace_id": workspace_id,
                    f"{tool_name}_{self.current_phase.value if self.current_phase else 'unknown'}": {
                        "status": result.status.value,
                        "timestamp": datetime.now().isoformat(),
                        "tool": tool_name,
                        "data": result.data
                    }
                }),
                timeout=self.DEFAULT_MEMORY_FETCH_TIMEOUT_SEC
            )
        except asyncio.TimeoutError:
            logger.warning("Long-term memory save timed out (non-blocking)")
        except Exception as e:
            logger.warning(f"Failed to save to long-term memory: {e}")
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get execution metrics."""
        return self.metrics.to_dict()
    
    async def cleanup(self) -> None:
        """Cleanup agent resources."""
        try:
            await cleanup_reme()
            logger.info("✓ Agent cleanup complete")
        except Exception as e:
            logger.warning(f"Cleanup error: {e}")


# ================ USAGE EXAMPLES ================

async def example_basic_usage():
    """Example 1: Basic initialization and usage."""
    agent = await Agent().async_init()
    result = await agent.process_input("Scan 192.168.1.0/24")
    print(f"Result: {result}")
    await agent.cleanup()


async def example_custom_config():
    """Example 2: Custom configuration."""
    agent = await Agent(
        max_iterations=20,
        llm_timeout_sec=45,
        tool_timeout_sec=90,
        tool_retries=3
    ).async_init()
    result = await agent.process_input("Enumerate services on 192.168.1.1")
    print(f"Result: {result}")
    await agent.cleanup()


async def example_metrics():
    """Example 3: Access execution metrics."""
    agent = await Agent().async_init()
    await agent.process_input("Check available tools")
    metrics = agent.get_metrics()
    print(f"Iterations: {metrics['total_iterations']}")
    print(f"Successful tools: {metrics['successful_tools']}")
    print(f"Tool usage: {metrics['tool_executions']}")
    await agent.cleanup()


async def example_direct_tool():
    """Example 4: Direct tool execution."""
    agent = await Agent().async_init()
    result = await agent.run_tool(
        "execute_system_command",
        {"command": "whoami"}
    )
    print(f"Status: {result.status.value}")
    print(f"Output: {result.data}")
    await agent.cleanup()


if __name__ == "__main__":
    import asyncio
    asyncio.run(example_basic_usage())
