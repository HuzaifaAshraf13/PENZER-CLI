# agent/agent.py
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

# Configure logging FIRST
logger = logging.getLogger("penzer.agent")
logger.setLevel(logging.DEBUG)

# Import and register ALL tools and prompts FIRST (before anything uses mcp)
# Note: tools.tools is imported in async_init() to avoid circular imports

# 1. Session memory tools (no circular dependency)
try:
    import session.session  # registers memory tools (mem_get_short, mem_set_short, etc.)
    logger.debug("✓ Loaded session.session (memory tools)")
except Exception as e:
    logger.debug(f"session.session error: {e}")

# 2. Prompts
try:
    import session.sessionprompts  # registers session prompts
except ImportError:
    logger.debug("session.sessionprompts not available")

try:
    import tools.ToolsPrompts      # registers tool prompts
except ImportError:
    logger.debug("tools.ToolsPrompts not available")

if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(name)s] %(levelname)s: %(message)s"))
    logger.addHandler(handler)


# -----------------------------------------------------------
# ENUMS AND DATA STRUCTURES
# -----------------------------------------------------------

class ToolExecutionStatus(Enum):
    """Status enum for tool execution results."""
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    TIMEOUT = "timeout"
    RETRYABLE = "retryable"


@dataclass
class ToolResult:
    """Standardized result structure for tool execution."""
    status: ToolExecutionStatus
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    execution_time_ms: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "status": self.status.value,
            "data": self.data,
            "error": self.error,
            "metadata": self.metadata,
            "execution_time_ms": self.execution_time_ms
        }
    
    @staticmethod
    def from_dict(d: Dict[str, Any]) -> 'ToolResult':
        """Create from dictionary."""
        return ToolResult(
            status=ToolExecutionStatus(d.get("status", "error")),
            data=d.get("data", {}),
            error=d.get("error"),
            metadata=d.get("metadata", {}),
            execution_time_ms=d.get("execution_time_ms", 0.0)
        )


@dataclass
class LLMDecision:
    """Parsed LLM output in ReAct format."""
    thought: str
    tool: Optional[str] = None
    args: Dict[str, Any] = field(default_factory=dict)
    final_answer: Optional[str] = None
    confidence: float = 0.8
    raw_response: str = ""
    
    def is_complete(self) -> bool:
        """Check if task is complete (has final_answer)."""
        return self.final_answer is not None and self.final_answer.strip() != ""
    
    def has_action(self) -> bool:
        """Check if there's a tool action to execute."""
        return self.tool is not None and self.tool.strip() != ""


@dataclass
class ExecutionMetrics:
    """Track execution metrics across workflow iterations."""
    total_iterations: int = 0
    successful_tools: int = 0
    failed_tools: int = 0
    total_execution_time_ms: float = 0.0
    tool_executions: Dict[str, int] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    
    def add_tool_execution(self, tool_name: str, success: bool, time_ms: float):
        """Record tool execution."""
        if tool_name not in self.tool_executions:
            self.tool_executions[tool_name] = 0
        self.tool_executions[tool_name] += 1
        
        if success:
            self.successful_tools += 1
        else:
            self.failed_tools += 1
        
        self.total_execution_time_ms += time_ms
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)


class Agent:
    """
    Modular pentesting agent with skill-driven workflow, async safety, and robust error handling.
    
    Features:
    - Skill-based decision making with dynamic prioritization
    - Optimized memory handling (short-term & long-term with summarization)
    - Retry logic, timeouts, and error recovery for tool execution
    - Robust JSON parsing with nested/partial JSON support
    - Metrics tracking and auto memory persistence
    """
    
    # Default configuration
    DEFAULT_MAX_ITERATIONS = 10
    DEFAULT_LLM_TIMEOUT_SEC = 30
    DEFAULT_TOOL_TIMEOUT_SEC = 60
    DEFAULT_TOOL_RETRIES = 2
    DEFAULT_MEMORY_FETCH_TIMEOUT_SEC = 2.0
    
    def __init__(self, max_iterations: int = None, llm_timeout_sec: float = None,
                 tool_timeout_sec: float = None, tool_retries: int = None):
        """
        Initialize Agent with configurable parameters.
        
        Args:
            max_iterations: Max iterations per workflow (default: 10)
            llm_timeout_sec: Timeout for LLM calls (default: 30)
            tool_timeout_sec: Timeout for tool execution (default: 60)
            tool_retries: Number of retries for failed tools (default: 2)
        """
        self.llm = LLM()
        self.mcp_client = mcp
        
        # Configuration
        self.max_iterations = max_iterations or self.DEFAULT_MAX_ITERATIONS
        self.llm_timeout_sec = llm_timeout_sec or self.DEFAULT_LLM_TIMEOUT_SEC
        self.tool_timeout_sec = tool_timeout_sec or self.DEFAULT_TOOL_TIMEOUT_SEC
        self.tool_retries = tool_retries or self.DEFAULT_TOOL_RETRIES
        
        # State (filled during async_init)
        self.tool_schema: Dict[str, Any] = {}
        self.resource_uris: List[str] = []
        self.formatted_system_prompt: str = ""
        
        # Skill management
        self.all_skills = load_all_skills()
        self.skill_selector: Optional[SkillSelector] = None
        self.current_phase: Optional[PentestPhase] = None
        self.current_skill: Optional[Dict[str, Any]] = None
        
        # Metrics
        self.metrics = ExecutionMetrics()
        
        logger.debug(f"Agent initialized with max_iterations={self.max_iterations}")

    async def async_init(self):
        """
        Async initialization: loads tools, resources, and initializes skill selector.
        Must be called before using the agent.
        """
        print("DEBUG: async_init started")  # Direct print for debugging
        logger.info("Starting async initialization...")
        
        # Ensure all tools are registered with MCP before loading schema
        print("DEBUG: About to import session.session")  # Direct print
        logger.debug("Importing session.session...")
        try:
            import session.session
            print(f"DEBUG: session.session imported successfully")  # Direct print
            logger.info("✓ Loaded session.session (memory tools)")
        except Exception as e:
            print(f"DEBUG: session.session import failed: {e}")  # Direct print
            logger.error(f"Failed to load session.session: {e}")
        
        print("DEBUG: About to import tools.tools")  # Direct print
        logger.debug("Importing tools.tools...")
        try:
            import tools.tools
            print(f"DEBUG: tools.tools imported successfully")  # Direct print
            logger.info("✓ Loaded tools.tools (security tools)")
        except Exception as e:
            print(f"DEBUG: tools.tools import failed: {e}")  # Direct print
            logger.error(f"Failed to load tools.tools: {e}")
        
        # Initialize ReMeApp for long-term memory
        reme_success = await init_reme()
        if not reme_success:
            logger.warning("Long-term memory unavailable, continuing with short-term only")
        
        # Load MCP tools and resources
        try:
            self.tool_schema = await self._load_tool_schema()
            logger.debug(f"Loaded {len(self.tool_schema)} MCP tools")
        except Exception as e:
            logger.error(f"Failed to load tool schema: {e}")
            self.tool_schema = {}
        
        try:
            self.resource_uris = self._load_resource_uris()
            logger.debug(f"Loaded {len(self.resource_uris)} resource URIs")
        except Exception as e:
            logger.error(f"Failed to load resource URIs: {e}")
            self.resource_uris = []
        
        # Build system prompt
        self.formatted_system_prompt = self._build_system_prompt()
        
        # Initialize skill selector
        try:
            self.skill_selector = SkillSelector(self.all_skills)
            total_skills = sum(len(s) for s in self.all_skills.values())
            logger.info(f"Loaded {total_skills} skills across {len(self.all_skills)} phases")
        except Exception as e:
            logger.error(f"Failed to initialize skill selector: {e}")
            raise
        
        logger.info("Async initialization complete ✓")
        return self

    
    # -----------------------------------------------------------
    # LOADERS: Tool Schema & Resources
    # -----------------------------------------------------------
    
    async def _load_tool_schema(self) -> Dict[str, Any]:
        """
        Async-safe tool schema loading from FastMCP with error recovery.
        Handles both get_tools() method and .tools attribute.
        
        Returns:
            Dictionary mapping tool names to tool objects
        """
        tools_dict = {}
        
        # Try 1: Use async get_tools() method
        try:
            if hasattr(self.mcp_client, "get_tools") and callable(getattr(self.mcp_client, "get_tools")):
                tools_dict = await self.mcp_client.get_tools()
                logger.info(f"Loaded {len(tools_dict)} tools via get_tools()")
                return tools_dict
        except Exception as e:
            logger.debug(f"get_tools() failed: {e}")
        
        # Try 2: Access .tools attribute directly
        try:
            tools_dict = getattr(self.mcp_client, "tools", {})
            if tools_dict:
                logger.info(f"Loaded {len(tools_dict)} tools via .tools attribute")
                return tools_dict
        except Exception as e:
            logger.debug(f".tools attribute access failed: {e}")
        
        # Try 3: Attempt to list tool names if available
        try:
            if hasattr(self.mcp_client, "_tools"):
                tools_dict = getattr(self.mcp_client, "_tools", {})
                if tools_dict:
                    logger.info(f"Loaded {len(tools_dict)} tools via ._tools")
                    return tools_dict
        except Exception as e:
            logger.debug(f"._tools access failed: {e}")
        
        logger.warning("Could not load any tools from MCP client")
        return {}

    def _load_resource_uris(self) -> List[str]:
        """Load resource URIs from MCP client with error handling."""
        try:
            resources = getattr(self.mcp_client, "resources", {})
            resource_list = list(resources.keys())
            logger.info(f"Loaded {len(resource_list)} resources from MCP")
            return resource_list
        except Exception as e:
            logger.warning(f"Failed to load resources: {e}")
            return []

    def _serialize_tools_for_prompt(self, tools_dict: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert FastMCP tool objects into JSON-serializable dict with only names and parameters.
        
        Args:
            tools_dict: Dictionary of tool objects from MCP
            
        Returns:
            Serializable dict mapping tool names to their parameters
        """
        serial = {}
        for name, tool_obj in tools_dict.items():
            try:
                # Extract callable from wrapper
                fn = getattr(tool_obj, "fn", tool_obj)
                if hasattr(fn, "__wrapped__"):
                    fn = fn.__wrapped__
                
                # Extract parameters from signature
                params = []
                try:
                    sig = inspect.signature(fn)
                    params = [p for p in sig.parameters.keys()]
                except Exception:
                    # Fallback: use annotations
                    ann = getattr(fn, "__annotations__", {}) or {}
                    params = list(ann.keys())
                
                serial[name] = {"args": params}
                logger.debug(f"Serialized tool: {name} with params {params}")
            except Exception as e:
                logger.debug(f"Failed to serialize tool {name}: {e}")
                serial[name] = {"args": []}
        
        return serial

    
    def _build_memory_context(self, short_term: Dict[str, Any], long_term: Dict[str, Any]) -> str:
        """
        Build optimized memory context from short-term and long-term memory.
        Summarizes and limits to minimize token usage.
        
        Args:
            short_term: Current session discoveries
            long_term: Learned knowledge from long-term memory
            
        Returns:
            Formatted memory context string
        """
        memory_lines = ["# === MEMORY ==="]
        
        # SHORT-TERM (current discoveries - max 5 entries)
        if short_term:
            memory_lines.append("## Short-term (Current Session)")
            for i, (key, value) in enumerate(short_term.items()):
                if i >= 5:
                    memory_lines.append(f"... and {len(short_term) - 5} more entries")
                    break
                try:
                    if isinstance(value, dict):
                        summary = json.dumps(value)[:80]
                    else:
                        summary = str(value)[:80]
                    memory_lines.append(f"- {key}: {summary}")
                except Exception as e:
                    logger.debug(f"Error serializing memory key {key}: {e}")
        
        # LONG-TERM (learned knowledge - max 3 entries)
        if long_term:
            memory_lines.append("## Long-term (Learned Knowledge)")
            for i, (key, value) in enumerate(long_term.items()):
                if i >= 3:
                    memory_lines.append(f"... and {len(long_term) - 3} more entries")
                    break
                if key not in ["timestamp", "metadata"]:
                    try:
                        if isinstance(value, dict):
                            summary = json.dumps(value)[:80]
                        else:
                            summary = str(value)[:80]
                        memory_lines.append(f"- {key}: {summary}")
                    except Exception as e:
                        logger.debug(f"Error serializing long-term key {key}: {e}")
        
        return "\n".join(memory_lines)

    def _build_system_prompt(self) -> str:
        """Build minimal base system prompt."""
        return "You are Penzer, an autonomous pentesting agent. Return ONLY valid JSON with 'thought' field."

    
    # -----------------------------------------------------------
    # TOOL EXECUTION: Retry Logic, Timeouts, Error Handling
    # -----------------------------------------------------------
    
    async def run_tool(self, tool_name: str, args: Dict[str, Any], 
                      retry_count: int = 0) -> ToolResult:
        """
        Execute a tool via MCP with robust error handling, retries, and timeouts.
        Supports both async and sync MCP tools.
        
        Args:
            tool_name: Name of the tool to execute
            args: Tool arguments
            retry_count: Current retry attempt (for internal use)
            
        Returns:
            ToolResult with standardized status, data, error, and metadata
        """
        start_time = time.time()
        workspace_id = "pentest_1"
        
        if "workspace_id" not in args:
            args["workspace_id"] = workspace_id
        
        try:
            # 1. Fetch available tools with multiple fallback strategies
            tools_dict = None
            tool = None
            
            try:
                # Try async get_tools()
                if hasattr(self.mcp_client, "get_tools") and callable(getattr(self.mcp_client, "get_tools")):
                    tools_dict = await self.mcp_client.get_tools()
                else:
                    # Try direct attribute access
                    tools_dict = getattr(self.mcp_client, "tools", {})
            except Exception as e:
                logger.debug(f"Failed to fetch tools dict: {e}")
                tools_dict = {}
            
            if not tools_dict:
                error_msg = f"Cannot fetch MCP tools (empty tools dict)"
                logger.error(error_msg)
                return ToolResult(
                    status=ToolExecutionStatus.ERROR,
                    error=error_msg,
                    metadata={"tool": tool_name, "retry": retry_count}
                )
            
            # 2. Lookup tool with multiple strategies
            tool = tools_dict.get(tool_name)
            
            if not tool:
                error_msg = f"Unknown tool: {tool_name}"
                logger.warning(error_msg)
                logger.debug(f"Available tools: {list(tools_dict.keys())}")
                return ToolResult(
                    status=ToolExecutionStatus.ERROR,
                    error=error_msg,
                    metadata={"available_tools": list(tools_dict.keys())}
                )
            
            # 3. Extract callable - handle both direct functions and wrapped tools
            callable_obj = tool
            if hasattr(tool, "fn"):
                callable_obj = tool.fn
            elif hasattr(tool, "__wrapped__"):
                callable_obj = tool.__wrapped__
            elif hasattr(tool, "func"):
                callable_obj = tool.func
            
            logger.debug(f"Executing tool: {tool_name}")
            
            # 4. Filter arguments based on function signature
            filtered_args = {}
            try:
                sig = inspect.signature(callable_obj)
                params = set(sig.parameters.keys())
                
                logger.debug(f"[{tool_name}] Function params: {params}, Provided args: {set(args.keys())}")
                
                # Filter args to only those accepted by the function
                filtered_args = {k: v for k, v in args.items() if k in params}
                
                logger.debug(f"[{tool_name}] Filtered args: {list(filtered_args.keys())}")
                
                # Ensure workspace_id is included if needed
                if "workspace_id" in params and "workspace_id" not in filtered_args:
                    filtered_args["workspace_id"] = workspace_id
                
                logger.debug(f"[{tool_name}] Final args: {list(filtered_args.keys())}")
            except Exception as e:
                error_msg = f"Failed to inspect tool signature: {e}"
                logger.error(error_msg)
                # Continue anyway with all args
                filtered_args = args.copy()
            
            # 5. Execute with timeout - handle both async and sync
            execution_time = 0
            result = None
            
            try:
                is_async = asyncio.iscoroutinefunction(callable_obj)
                
                if is_async:
                    logger.debug(f"[{tool_name}] Executing as async function")
                    result = await asyncio.wait_for(
                        callable_obj(**filtered_args),
                        timeout=self.tool_timeout_sec
                    )
                else:
                    logger.debug(f"[{tool_name}] Executing as sync function in thread pool")
                    result = await asyncio.wait_for(
                        asyncio.to_thread(callable_obj, **filtered_args),
                        timeout=self.tool_timeout_sec
                    )
                
                execution_time = (time.time() - start_time) * 1000
                logger.info(f"[{tool_name}] Executed successfully in {execution_time:.0f}ms")
                
            except asyncio.TimeoutError:
                error_msg = f"Tool execution timeout after {self.tool_timeout_sec}s"
                logger.error(f"[{tool_name}] {error_msg}")
                execution_time = (time.time() - start_time) * 1000
                
                # Retry on timeout if we haven't exceeded limit
                if retry_count < self.tool_retries:
                    logger.info(f"[{tool_name}] Retrying ({retry_count + 1}/{self.tool_retries})...")
                    await asyncio.sleep(0.5 * (retry_count + 1))  # Exponential backoff
                    return await self.run_tool(tool_name, args, retry_count + 1)
                
                return ToolResult(
                    status=ToolExecutionStatus.TIMEOUT,
                    error=error_msg,
                    execution_time_ms=execution_time,
                    metadata={"tool": tool_name, "timeout_sec": self.tool_timeout_sec}
                )
            except Exception as e:
                error_msg = f"Tool execution error: {type(e).__name__}: {str(e)[:200]}"
                logger.error(f"[{tool_name}] {error_msg}")
                execution_time = (time.time() - start_time) * 1000
                
                # Retry on retryable errors
                if retry_count < self.tool_retries and self._is_retryable_error(e):
                    logger.info(f"[{tool_name}] Retrying ({retry_count + 1}/{self.tool_retries})...")
                    await asyncio.sleep(0.5 * (retry_count + 1))
                    return await self.run_tool(tool_name, args, retry_count + 1)
                
                return ToolResult(
                    status=ToolExecutionStatus.ERROR,
                    error=error_msg,
                    execution_time_ms=execution_time,
                    metadata={"tool": tool_name, "retry": retry_count}
                )
            
            # 6. Normalize result to ToolResult format
            if not isinstance(result, dict):
                logger.warning(f"[{tool_name}] Tool returned non-dict: {type(result)}")
                return ToolResult(
                    status=ToolExecutionStatus.SUCCESS,
                    data={"result": result},
                    execution_time_ms=execution_time,
                    metadata={"tool": tool_name, "raw_type": str(type(result))}
                )
            
            # Wrap result if missing status field (for tools that use standards.success/error/warning)
            if "status" not in result:
                logger.debug(f"[{tool_name}] Wrapping result with success status")
                return ToolResult(
                    status=ToolExecutionStatus.SUCCESS,
                    data=result,
                    execution_time_ms=execution_time,
                    metadata={"tool": tool_name}
                )
            
            # Convert status string to enum
            status_str = result.get("status", "success")
            try:
                status = ToolExecutionStatus(status_str)
            except ValueError:
                logger.warning(f"[{tool_name}] Unknown status: {status_str}, defaulting to error")
                status = ToolExecutionStatus.ERROR
            
            return ToolResult(
                status=status,
                data=result.get("data", {}),
                error=result.get("error"),
                metadata={"tool": tool_name, **result.get("metadata", {})},
                execution_time_ms=execution_time
            )
        
        except Exception as e:
            logger.error(f"Unexpected error in run_tool: {e}", exc_info=True)
            execution_time = (time.time() - start_time) * 1000
            return ToolResult(
                status=ToolExecutionStatus.ERROR,
                error=f"Unexpected error: {str(e)[:200]}",
                execution_time_ms=execution_time,
                metadata={"tool": tool_name}
            )
    
    def _is_retryable_error(self, error: Exception) -> bool:
        """Determine if an error is worth retrying."""
        retryable_types = (
            ConnectionError, TimeoutError, OSError,
            asyncio.TimeoutError, asyncio.CancelledError
        )
        return isinstance(error, retryable_types)
    
    
    # -----------------------------------------------------------
    # LLM DECISION PARSING: Robust JSON Handling
    # -----------------------------------------------------------
    
    def _parse_llm_decision(self, raw: str) -> Optional[LLMDecision]:
        """
        Parse LLM raw output into structured LLMDecision.
        
        Robust handling of:
        - Valid JSON with 'thought' field
        - JSON wrapped in markdown backticks
        - Nested JSON (JSON strings inside JSON)
        - Partial/malformed JSON (extracts first valid object)
        - Non-JSON text (wraps as thought)
        
        Args:
            raw: Raw LLM output string
            
        Returns:
            LLMDecision with required 'thought' field, or None if parsing completely fails
        """
        if not raw or not isinstance(raw, str):
            logger.warning("LLM returned empty or non-string response")
            return None
        
        txt = raw.strip()
        raw_response = txt
        
        logger.debug(f"Parsing LLM decision from: {txt[:100]}...")
        
        # Try 1: Remove markdown-style code blocks
        if txt.startswith("```") and txt.endswith("```"):
            lines = txt.split("\n")
            txt = "\n".join(lines[1:-1]).strip()
            logger.debug("Removed markdown code block wrapper")
        
        # Try 2: Direct JSON parse
        try:
            parsed = json.loads(txt)
            if isinstance(parsed, dict):
                decision = self._extract_llm_decision(parsed)
                if decision and decision.thought:
                    logger.debug(f"Successfully parsed JSON decision: {decision.tool or 'no-action'}")
                    decision.raw_response = raw_response
                    return decision
            else:
                # Non-dict JSON (array, string, number, etc)
                logger.debug(f"JSON parsed but not dict type: {type(parsed)}")
                return LLMDecision(
                    thought=json.dumps(parsed),
                    raw_response=raw_response
                )
        except json.JSONDecodeError as e:
            logger.debug(f"Direct JSON parse failed: {e}")
        
        # Try 3: Extract JSON from partial/malformed input
        start_idx = txt.find("{")
        end_idx = txt.rfind("}")
        
        if start_idx != -1 and end_idx > start_idx:
            try:
                extracted = txt[start_idx:end_idx + 1]
                parsed = json.loads(extracted)
                if isinstance(parsed, dict):
                    decision = self._extract_llm_decision(parsed)
                    if decision and decision.thought:
                        logger.debug("Extracted valid JSON from partial input")
                        decision.raw_response = raw_response
                        return decision
            except json.JSONDecodeError:
                logger.debug("Partial JSON extraction failed")
        
        # Try 4: Wrap as thought (last resort)
        if any(kw in txt.lower() for kw in ["thought", "tool", "final_answer", "action", "error"]):
            logger.info("Wrapping LLM response as thought (fallback)")
            return LLMDecision(
                thought=txt[:500],
                confidence=0.5,
                raw_response=raw_response
            )
        
        # Complete failure
        logger.error(f"Failed to parse LLM decision from: {txt[:200]}")
        return None
    
    def _extract_llm_decision(self, parsed_json: Dict[str, Any]) -> Optional[LLMDecision]:
        """
        Extract LLMDecision from parsed JSON dict.
        Handles nested JSON and ensures 'thought' field is always present.
        
        Args:
            parsed_json: Parsed JSON object
            
        Returns:
            LLMDecision with all fields populated
        """
        try:
            # Check for nested JSON in 'thought' field
            thought = parsed_json.get("thought", "")
            
            if isinstance(thought, str) and thought.strip().startswith("{"):
                try:
                    nested = json.loads(thought)
                    if isinstance(nested, dict):
                        # Merge nested into outer, outer takes precedence
                        for key in ["tool", "args", "final_answer"]:
                            if key not in parsed_json and key in nested:
                                parsed_json[key] = nested[key]
                        # Use nested thought if available
                        if "thought" in nested and nested["thought"] != thought:
                            thought = nested["thought"]
                        logger.debug("Extracted nested JSON from thought field")
                except json.JSONDecodeError:
                    pass  # Not nested JSON, use as-is
            
            # Ensure 'thought' is always present
            if not thought or not isinstance(thought, str):
                # Build thought from remaining fields
                remaining = {k: v for k, v in parsed_json.items() 
                           if k not in ["tool", "args", "final_answer"]}
                thought = json.dumps(remaining) if remaining else "Processing..."
                logger.debug("Generated thought from remaining fields")
            
            return LLMDecision(
                thought=thought,
                tool=parsed_json.get("tool"),
                args=parsed_json.get("args", {}),
                final_answer=parsed_json.get("final_answer"),
                confidence=parsed_json.get("confidence", 0.8),
                raw_response=""
            )
        except Exception as e:
            logger.error(f"Error extracting decision from JSON: {e}")
            return None
            
    
    # -----------------------------------------------------------
    # WORKFLOW: Skill-Driven ReAct Loop
    # -----------------------------------------------------------
    
    async def process_input(self, user_input: str) -> Optional[str]:
        """
        Skill-driven autonomous workflow with ReAct loop.
        
        Workflow:
        1. Select appropriate skill based on user intent & phase
        2. Load skill's agent_behavior for LLM instructions
        3. Fetch short-term & long-term memory (async, with timeout)
        4. Build system prompt with skill context & available tools
        5. Iterate: LLM decision → tool execution → observation → repeat
        6. Continue until LLM returns final_answer or max iterations
        7. Auto-consolidate findings to long-term memory
        
        Args:
            user_input: The user's request/task
            
        Returns:
            Final answer from agent, or None if workflow failed
        """
        workspace_id = "pentest_1"
        iteration = 0
        messages: List[Dict[str, Any]] = []
        
        logger.info(f"Processing input: {user_input[:80]}...")
        
        # === PHASE 1: Skill Selection ===
        if not self.skill_selector:
            logger.error("Skill selector not initialized. Call async_init() first.")
            return None
        
        try:
            selected_skill, phase, skill_confidence = self.skill_selector.select_skill(user_request=user_input)
            self.current_phase = phase
            
            if selected_skill:
                self.current_skill = self.skill_selector.skill_to_dict(selected_skill)
                skill_name = self.current_skill.get('name', 'Unknown')
                logger.info(f"Selected skill: {skill_name} (phase: {phase.value}, confidence: {skill_confidence:.2f})")
            else:
                logger.warning("No matching skill found")
                return None
        except Exception as e:
            logger.error(f"Skill selection failed: {e}")
            return None
        
        # === PHASE 2: Fetch Memory (Short & Long-term, Async) ===
        short_term_context = {}
        long_term_context = {}
        
        # Fetch short-term memory (synchronous, fast)
        try:
            short_mem_result = await self.run_tool("mem_get_short", {"workspace_id": workspace_id})
            if short_mem_result.status == ToolExecutionStatus.SUCCESS:
                short_term_context = short_mem_result.data
                logger.debug(f"Loaded {len(short_term_context)} short-term memory entries")
            elif short_mem_result.status != ToolExecutionStatus.TIMEOUT:
                logger.warning(f"Short-term memory fetch warning: {short_mem_result.error}")
        except Exception as e:
            logger.debug(f"Short-term memory fetch error: {e}")
        
        # Fetch long-term memory (async, with timeout to avoid blocking)
        try:
            long_mem_task = asyncio.create_task(
                self.run_tool("mem_get_long", {"workspace_id": workspace_id})
            )
            try:
                long_mem_result = await asyncio.wait_for(
                    long_mem_task, 
                    timeout=self.DEFAULT_MEMORY_FETCH_TIMEOUT_SEC
                )
                if long_mem_result.status == ToolExecutionStatus.SUCCESS:
                    long_term_context = long_mem_result.data
                    logger.debug(f"Loaded {len(long_term_context)} long-term memory entries")
                elif long_mem_result.status != ToolExecutionStatus.TIMEOUT:
                    logger.warning(f"Long-term memory warning: {long_mem_result.error}")
            except asyncio.TimeoutError:
                logger.warning(f"Long-term memory fetch timeout (>{self.DEFAULT_MEMORY_FETCH_TIMEOUT_SEC}s), proceeding")
                long_mem_task.cancel()
        except Exception as e:
            logger.debug(f"Long-term memory error: {e}")
        
        # === PHASE 3: Build System Prompt with Skills & Memory ===
        system_prompt_with_skills = create_skill_aware_system_prompt(
            self.current_skill,
            base_context=""
        )
        
        memory_section = self._build_memory_context(short_term_context, long_term_context)
        
        system_prompt_with_context = f"""{system_prompt_with_skills}

{memory_section}
"""
        
        # Fetch and add available tools info
        try:
            available_tools_result = await self.run_tool("check_available_tools", {"tool_category": "all"})
            if available_tools_result.status == ToolExecutionStatus.SUCCESS:
                available_tools_data = available_tools_result.data
                available_list = available_tools_data.get("available_tools", {})
                tool_names = list(available_list.keys())
                logger.info(f"Available tools: {', '.join(tool_names[:5])}{'...' if len(tool_names) > 5 else ''}")
                
                available_tools_info = f"\n[AVAILABLE TOOLS] These security tools are available:\n"
                for tool_name in sorted(tool_names):
                    available_tools_info += f"  - {tool_name}\n"
                available_tools_info += """Use these tools when appropriate. IMPORTANT: You MUST take action - never ask for parameters.
If information is missing, use sensible defaults (e.g., localhost, common ports, etc).
Execute tools and proceed autonomously.\n"""
                
                system_prompt_with_context = f"""{system_prompt_with_context}

{available_tools_info}

MANDATORY INSTRUCTION: Do not ask the user for clarification. Take autonomous action using available tools.
Use defaults if needed. Execute commands immediately and report findings.
"""
        except Exception as e:
            logger.debug(f"Failed to fetch available tools: {e}")
        
        # Add initial user message
        messages.append({"role": "user", "content": user_input})
        
        # === PHASE 4: Main ReAct Loop ===
        self.metrics.total_iterations = 0
        final_answer = None
        
        while iteration < self.max_iterations:
            iteration += 1
            self.metrics.total_iterations += 1
            logger.info(f"\n[ITERATION {iteration}/{self.max_iterations}]")
            
            # Build conversation prompt
            conversation_text = self._build_conversation_prompt(messages)
            
            # === LLM Call with Timeout ===
            try:
                logger.debug("Calling LLM...")
                decision_raw = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.llm.generate_content,
                        system_instruction=system_prompt_with_context,
                        prompt=conversation_text
                    ),
                    timeout=self.llm_timeout_sec
                )
                logger.debug(f"LLM response received ({len(decision_raw)} chars)")
            except asyncio.TimeoutError:
                error_msg = f"LLM timeout after {self.llm_timeout_sec}s"
                logger.error(error_msg)
                self.metrics.errors.append(error_msg)
                break
            except Exception as e:
                error_msg = f"LLM call failed: {e}"
                logger.error(error_msg)
                self.metrics.errors.append(error_msg)
                break
            
            # === Parse LLM Decision ===
            decision = self._parse_llm_decision(decision_raw)
            
            if not decision:
                logger.error("Failed to parse LLM response")
                logger.debug(f"Raw response was: {decision_raw[:200]}")
                self.metrics.errors.append("LLM parsing failed")
                break
            
            # Log decision
            action_str = f"tool={decision.tool}" if decision.has_action() else "no-action"
            logger.info(f"Decision: {action_str}, confidence={decision.confidence:.2f}")
            
            # Append thought to message history
            if decision.thought:
                messages.append({
                    "role": "assistant",
                    "content": f"[THOUGHT] {decision.thought[:200]}"
                })
            
            # === Check if Complete ===
            if decision.is_complete():
                final_answer = decision.final_answer
                logger.info(f"\n✓ Task complete: {final_answer[:100]}")
                messages.append({
                    "role": "assistant",
                    "content": f"[FINAL ANSWER] {final_answer}"
                })
                
                # Auto-consolidate findings
                asyncio.create_task(
                    self._consolidate_phase_findings(workspace_id, final_answer)
                )
                break
            
            # === Execute Tool if Specified ===
            if decision.has_action():
                logger.info(f"Executing tool: {decision.tool}")
                tool_result = await self.run_tool(decision.tool, decision.args)
                
                # Record metrics
                self.metrics.add_tool_execution(
                    decision.tool,
                    tool_result.status == ToolExecutionStatus.SUCCESS,
                    tool_result.execution_time_ms
                )
                
                # Handle result
                if tool_result.status == ToolExecutionStatus.SUCCESS:
                    logger.info(f"Tool succeeded in {tool_result.execution_time_ms:.0f}ms")
                    observation = f"[OBSERVATION] Tool '{decision.tool}' succeeded:\n{json.dumps(tool_result.data, indent=2)[:500]}"
                    messages.append({"role": "user", "content": observation})
                    
                    # Auto-save to memory
                    asyncio.create_task(
                        self._auto_save_to_memory(workspace_id, decision.tool, tool_result)
                    )
                
                elif tool_result.status == ToolExecutionStatus.WARNING:
                    warning_msg = tool_result.error or "Unknown warning"
                    logger.warning(f"Tool warning: {warning_msg}")
                    observation = f"[OBSERVATION - WARNING] Tool completed with warning: {warning_msg}"
                    messages.append({"role": "user", "content": observation})
                    
                    asyncio.create_task(
                        self._auto_save_to_memory(workspace_id, decision.tool, tool_result)
                    )
                
                elif tool_result.status == ToolExecutionStatus.TIMEOUT:
                    logger.warning(f"Tool timeout: {tool_result.error}")
                    observation = f"[OBSERVATION - ERROR] Tool timed out: {tool_result.error}. Try a different approach."
                    messages.append({"role": "user", "content": observation})
                
                else:  # ERROR
                    logger.error(f"Tool error: {tool_result.error}")
                    observation = f"[OBSERVATION - ERROR] Tool '{decision.tool}' failed: {tool_result.error}. Try an alternative approach."
                    messages.append({"role": "user", "content": observation})
            
            else:
                # No action specified but also not complete - provide guidance
                logger.warning("No action or final_answer from LLM, requesting decision")
                messages.append({
                    "role": "user",
                    "content": "[GUIDANCE] No action taken. Please either specify a tool to execute or provide a final_answer."
                })
        
        # === Workflow Complete ===
        if iteration >= self.max_iterations:
            logger.warning(f"Reached max iterations ({self.max_iterations})")
            final_answer = "Reached iteration limit"
        
        logger.info(f"\nWorkflow complete. Metrics: {self.metrics.to_dict()}")
        return final_answer
    
    # -----------------------------------------------------------
    # HELPER METHODS: Conversation Building & Memory Consolidation
    # -----------------------------------------------------------
    
    def _build_conversation_prompt(self, messages: List[Dict[str, Any]]) -> str:
        """
        Build conversation prompt from message history.
        Formats as role: content pairs with explicit JSON response instruction.
        
        Args:
            messages: List of message dicts with role and content
            
        Returns:
            Formatted prompt string
        """
        prompt_lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            prompt_lines.append(f"{role}: {content}")
        
        # Explicit instruction for next decision
        prompt_lines.append("\n" + "="*60)
        prompt_lines.append("NEXT DECISION (respond ONLY with JSON):")
        prompt_lines.append("="*60)
        prompt_lines.append('{"thought": "...", "tool": "...", "args": {...}, "final_answer": "..."}')
        
        return "\n".join(prompt_lines)
    
    async def _consolidate_phase_findings(self, workspace_id: str, phase_summary: str) -> None:
        """
        Consolidate findings at phase completion to long-term memory.
        Ensures discoveries are persisted for future sessions.
        
        Args:
            workspace_id: Workspace identifier
            phase_summary: Summary of findings
        """
        try:
            consolidation_data = {
                "phase": self.current_phase.value if self.current_phase else "unknown",
                "skill": self.current_skill.get("name") if self.current_skill else "unknown",
                "summary": phase_summary,
                "timestamp": datetime.now().isoformat(),
                "status": "completed"
            }
            
            result = await self.run_tool("mem_set_long", {
                "workspace_id": workspace_id,
                f"phase_{self.current_phase.value}_findings": consolidation_data
            })
            
            if result.status == ToolExecutionStatus.SUCCESS:
                logger.info(f"✓ Phase findings consolidated to long-term memory")
            else:
                logger.debug(f"Phase consolidation warning: {result.error}")
        
        except Exception as e:
            logger.debug(f"Phase consolidation failed: {e}")
    
    async def _auto_save_to_memory(self, workspace_id: str, tool_name: str, 
                                   result: ToolResult) -> None:
        """
        Automatically save tool execution result to both short-term and long-term memory.
        
        Short-term: Quick access during current session
        Long-term: Persistent knowledge for future sessions via ReMeApp
        
        Args:
            workspace_id: Workspace identifier
            tool_name: Name of executed tool
            result: ToolResult from execution
        """
        try:
            memory_entry = {
                "tool": tool_name,
                "timestamp": datetime.now().isoformat(),
                "phase": self.current_phase.value if self.current_phase else "unknown",
                "skill": self.current_skill.get("name") if self.current_skill else "unknown",
                "status": result.status.value,
                "result_summary": str(result.data)[:200] if result.data else None
            }
            
            # 1️⃣ SHORT-TERM MEMORY (fast, in-memory)
            try:
                short_result = await self.run_tool("mem_set_short", {
                    "workspace_id": workspace_id,
                    "data": {f"{tool_name}_execution": memory_entry}
                })
                if short_result.status != ToolExecutionStatus.SUCCESS:
                    logger.debug(f"Short-term memory save warning: {short_result.error}")
            except Exception as e:
                logger.debug(f"Short-term memory save error: {e}")
            
            # 2️⃣ LONG-TERM MEMORY (ReMeApp, persistent)
            try:
                long_result = await self.run_tool("mem_set_long", {
                    "workspace_id": workspace_id,
                    "data": {f"{tool_name}_{self.current_phase.value}": memory_entry}
                })
                if long_result.status != ToolExecutionStatus.SUCCESS:
                    logger.debug(f"Long-term memory save warning: {long_result.error}")
            except Exception as e:
                logger.debug(f"Long-term memory save error: {e}")
        
        except Exception as e:
            logger.debug(f"Memory auto-save failed: {e}")
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get current execution metrics."""
        return self.metrics.to_dict()


# -----------------------------------------------------------
# ENTRY POINT: Example Usage
# -----------------------------------------------------------

if __name__ == "__main__":
    logger.info("Starting Penzer Security Agent...")
    
    async def main():
        """Main async entry point with error handling."""
        try:
            # Initialize agent with custom configuration (optional)
            agent = await Agent(
                max_iterations=10,
                llm_timeout_sec=30,
                tool_timeout_sec=60,
                tool_retries=2
            ).async_init()
            logger.info(f"✓ Agent ready with {len(agent.tool_schema)} tools")
            
            # Interactive loop
            while True:
                try:
                    user_input = input("\n👤 You: ").strip()
                    if not user_input:
                        continue
                    if user_input.lower() in ("quit", "exit", "bye"):
                        logger.info("Shutting down. Goodbye!")
                        break
                    
                    # Process user input
                    final_answer = await agent.process_input(user_input)
                    if final_answer:
                        print(f"\n🤖 Agent: {final_answer}\n")
                        # Print metrics
                        metrics = agent.get_metrics()
                        logger.debug(f"Metrics: {metrics}")
                    
                except KeyboardInterrupt:
                    logger.info("Interrupted by user")
                    break
                except Exception as e:
                    logger.error(f"Error during conversation: {e}")
                    continue
        
        except Exception as e:
            logger.error(f"Fatal initialization error: {e}")
            return 1
        
        return 0
    
    try:
        exit_code = asyncio.run(main())
        exit(exit_code)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        exit(1)


# -----------------------------------------------------------
# USAGE EXAMPLES (for documentation)
# -----------------------------------------------------------

"""
USAGE EXAMPLES FOR THE REFACTORED AGENT

Example 1: Basic Initialization and Usage
===========================================

    import asyncio
    from agent.agent import Agent
    
    async def example_basic():
        # Initialize agent
        agent = await Agent().async_init()
        
        # Process a simple pentest task
        result = await agent.process_input("Scan the target network 192.168.1.0/24")
        print(f"Result: {result}")
    
    asyncio.run(example_basic())


Example 2: Custom Configuration
================================

    import asyncio
    from agent.agent import Agent
    
    async def example_custom_config():
        # Create agent with custom timeout and retry settings
        agent = await Agent(
            max_iterations=20,           # Allow more iterations
            llm_timeout_sec=45,          # Longer LLM timeout
            tool_timeout_sec=90,         # Longer tool timeout
            tool_retries=3               # More retries
        ).async_init()
        
        # Use the agent
        await agent.process_input("Find vulnerabilities in the web service")
    
    asyncio.run(example_custom_config())


Example 3: Working with Metrics
===============================

    import asyncio
    from agent.agent import Agent
    
    async def example_metrics():
        agent = await Agent().async_init()
        
        # Process multiple tasks
        await agent.process_input("Scan 10.0.0.0/24")
        await agent.process_input("Enumerate services on discovered hosts")
        
        # Get execution metrics
        metrics = agent.get_metrics()
        print(f"Total iterations: {metrics['total_iterations']}")
        print(f"Successful tools: {metrics['successful_tools']}")
        print(f"Failed tools: {metrics['failed_tools']}")
        print(f"Total time: {metrics['total_execution_time_ms']:.0f}ms")
        print(f"Tool usage: {metrics['tool_executions']}")
    
    asyncio.run(example_metrics())


Example 4: Error Handling & Recovery
====================================

    import asyncio
    from agent.agent import Agent, ToolExecutionStatus
    
    async def example_error_handling():
        agent = await Agent().async_init()
        
        # Execute a tool manually with error handling
        result = await agent.run_tool(
            "execute_system_command",
            {"command": "nmap -sV target.com"}
        )
        
        if result.status == ToolExecutionStatus.SUCCESS:
            print(f"Success: {result.data}")
        elif result.status == ToolExecutionStatus.TIMEOUT:
            print(f"Tool timed out: {result.error}")
        elif result.status == ToolExecutionStatus.ERROR:
            print(f"Tool error: {result.error}")
            print(f"Metadata: {result.metadata}")
    
    asyncio.run(example_error_handling())


Example 5: Direct Memory Management
===================================

    import asyncio
    from agent.agent import Agent
    
    async def example_memory():
        agent = await Agent().async_init()
        
        workspace_id = "pentest_1"
        
        # Get short-term memory
        short_result = await agent.run_tool("mem_get_short", {"workspace_id": workspace_id})
        if short_result.status.value == "success":
            print(f"Short-term memory: {short_result.data}")
        
        # Get long-term memory
        long_result = await agent.run_tool("mem_get_long", {"workspace_id": workspace_id})
        if long_result.status.value == "success":
            print(f"Long-term memory: {long_result.data}")
        
        # Save to memory
        save_result = await agent.run_tool("mem_set_short", {
            "workspace_id": workspace_id,
            "data": {
                "discovered_hosts": ["192.168.1.1", "192.168.1.2"]
            }
        })
    
    asyncio.run(example_memory())


Example 6: LLM Decision Parsing
==============================

    from agent.agent import Agent, LLMDecision
    
    def example_parsing():
        agent = Agent()
        
        # Test various LLM response formats
        test_responses = [
            # Valid JSON
            '{"thought": "Analyzing target", "tool": "nmap", "args": {"target": "10.0.0.1"}}',
            
            # JSON in markdown
            '```json\\n{"thought": "Scanning", "tool": "ping", "args": {"target": "10.0.0.1"}}\\n```',
            
            # Nested JSON
            '{"thought": "{\\"tool\\": \\"nmap\\", \\"target\\": \\"10.0.0.1\\"}", "final_answer": "Done"}',
            
            # Partial/malformed with final answer
            '{"thought": "Analysis", "final_answer": "Discovered 5 hosts"}',
            
            # Non-JSON fallback
            'I need to analyze the target first',
        ]
        
        for i, response in enumerate(test_responses, 1):
            decision = agent._parse_llm_decision(response)
            if decision:
                print(f"Test {i}: ✓ thought={decision.thought[:30]}..., "
                      f"tool={decision.tool}, complete={decision.is_complete()}")
            else:
                print(f"Test {i}: ✗ Failed to parse")


Example 7: Skill Selection & Prioritization
===========================================

    from agent.agent import Agent
    from agent.skill_selector import SkillSelector, PentestPhaseDetector
    from agent.skills import load_all_skills, PentestPhase
    
    def example_skill_selection():
        # Load skills
        all_skills = load_all_skills()
        selector = SkillSelector(all_skills)
        
        # Test different requests
        test_requests = [
            "Scan the network 192.168.1.0/24",
            "Enumerate services running on discovered hosts",
            "Find CVE exploits for Apache 2.4.41",
            "Escalate privileges from www-data",
            "Generate comprehensive pentest report",
        ]
        
        for request in test_requests:
            skill, phase, confidence = selector.select_skill(request)
            if skill:
                skill_dict = selector.skill_to_dict(skill)
                print(f"\\nRequest: {request}")
                print(f"  → Skill: {skill_dict['name']}")
                print(f"  → Phase: {phase.value}")
                print(f"  → Confidence: {confidence:.2%}")
                print(f"  → Priority: {skill_dict.get('priority', 0.5):.1f}")


Example 8: Async-Safe Workflow
==============================

    import asyncio
    from agent.agent import Agent
    
    async def example_async_workflow():
        # Multiple agents working in parallel
        agent1 = await Agent().async_init()
        agent2 = await Agent().async_init()
        
        # Run tasks concurrently
        results = await asyncio.gather(
            agent1.process_input("Scan network 10.0.0.0/24"),
            agent2.process_input("Scan network 172.16.0.0/24"),
            return_exceptions=True
        )
        
        for i, result in enumerate(results, 1):
            if isinstance(result, Exception):
                print(f"Agent {i} error: {result}")
            else:
                print(f"Agent {i} result: {result}")
    
    asyncio.run(example_async_workflow())


Example 9: Full Pentest Workflow Simulation
==========================================

    import asyncio
    from agent.agent import Agent
    
    async def example_full_workflow():
        agent = await Agent().async_init()
        
        # Simulate a full pentest workflow
        tasks = [
            "Scan the target network to find active hosts",
            "Enumerate services and versions on discovered hosts",
            "Research potential exploits for identified vulnerabilities",
            "Escalate privileges if initial access gained",
            "Generate final penetration test report with findings",
        ]
        
        for i, task in enumerate(tasks, 1):
            print(f"\\n[TASK {i}/{len(tasks)}] {task}")
            result = await agent.process_input(task)
            print(f"Result: {result}")
            
            # Print metrics after each task
            metrics = agent.get_metrics()
            print(f"Total tools used: {len(metrics['tool_executions'])}")
    
    asyncio.run(example_full_workflow())


Example 10: Tool Execution with Retry Logic
===========================================

    import asyncio
    from agent.agent import Agent, ToolExecutionStatus
    
    async def example_tool_retries():
        agent = await Agent(tool_retries=3).async_init()
        
        # This tool might fail/timeout and will be retried
        result = await agent.run_tool(
            "execute_system_command",
            {"command": "nmap -A -p- target.com"}
        )
        
        print(f"Status: {result.status.value}")
        print(f"Execution time: {result.execution_time_ms:.0f}ms")
        print(f"Metadata: {result.metadata}")
        
        if result.status == ToolExecutionStatus.SUCCESS:
            print(f"Data: {result.data}")
        else:
            print(f"Error: {result.error}")
    
    asyncio.run(example_tool_retries())

KEY IMPROVEMENTS IN THE REFACTORED AGENT
=========================================

1. MODULARITY & ARCHITECTURE:
   - Clear separation of concerns with dedicated classes (Agent, ToolResult, LLMDecision, ExecutionMetrics)
   - Dataclass-based structures for type safety
   - Comprehensive logging throughout

2. ASYNC SAFETY:
   - Proper async/await usage throughout
   - Timeout handling for LLM calls and tool execution
   - Background tasks for non-blocking memory persistence
   - Concurrent memory fetches with proper cancellation

3. ERROR HANDLING:
   - Retry logic with exponential backoff for transient failures
   - Timeout-specific error handling
   - Type-safe argument filtering before tool execution
   - Comprehensive error reporting in ToolResult

4. MEMORY MANAGEMENT:
   - Short-term memory for current session (fast)
   - Long-term memory via ReMeApp (persistent)
   - Auto-save after each tool execution
   - Phase consolidation at completion

5. LLM DECISION PARSING:
   - Robust JSON parsing with multiple fallbacks
   - Support for nested JSON in 'thought' field
   - Extraction from partial/malformed JSON
   - Markdown code block handling
   - Fallback to plain text wrapping

6. SKILL SELECTION:
   - Dynamic prioritization based on relevance + priority
   - Confidence scoring (0.0-1.0)
   - Phase detection with confidence
   - Keyword matching with multi-word phrase support
   - Logging for debugging skill selection

7. WORKFLOW MANAGEMENT:
   - Clear iteration limits with safe defaults
   - Metrics tracking (iterations, tools, timing)
   - Structured message history
   - Automatic memory consolidation
   - Better decision validation

8. TOOL EXECUTION:
   - Standardized ToolResult format
   - Retry logic with exponential backoff
   - Timeout handling per tool
   - Thread pool execution for sync functions
   - Detailed metadata and error reporting
"""
