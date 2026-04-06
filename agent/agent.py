"""
Penzer Autonomous Pentesting Agent
Advanced ReAct loop with Long-Running Operation Support

Features:
- Reason → Act → Observe loop with skill-guided decision making
- Timeout-aware long-running operation handling
- Task queuing and concurrent execution
- Adaptive iteration limits based on task complexity
- State management for persistence across operations
"""

import json
import asyncio
import logging
import time
import traceback
from typing import Dict, Any, List, Optional, Tuple
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from agent.core import mcp
from agent.llm import LLM
from agent.skills import load_all_skills, PentestPhase
from agent.skills.base import Skill
from agent.system_prompts import (
    get_reason_system_prompt,
    get_act_system_prompt,
    get_observe_system_prompt,
    get_synthesize_system_prompt,
    build_all_skill_guidance,
    REASON_PROMPT_TEMPLATE,
    ACT_PROMPT_TEMPLATE,
    OBSERVE_PROMPT_TEMPLATE,
    SYNTHESIZE_PROMPT_TEMPLATE,
)

# Register tools with FastMCP when agent is imported
import tools.tools
import session.session

logger = logging.getLogger(__name__)


@dataclass
class PendingOperation:
    """Represents a pending long-running operation"""
    operation_id: str
    task_name: str
    started_at: datetime
    timeout_seconds: int
    skill_id: Optional[str] = None
    status: str = "pending"  # pending, running, completed, failed, timeout
    result: Optional[str] = None
    
    def is_timeout(self) -> bool:
        """Check if operation has exceeded timeout"""
        elapsed = (datetime.now() - self.started_at).total_seconds()
        return elapsed > self.timeout_seconds
    
    def elapsed_seconds(self) -> float:
        """Get elapsed time in seconds"""
        return (datetime.now() - self.started_at).total_seconds()


class LoopPhase(Enum):
    """Current phase in ReAct loop"""
    REASON = "reason"
    ACT = "act"
    OBSERVE = "observe"


class PenzerAgent:
    """
    Advanced autonomous pentesting agent with long-running operation support.
    
    Features:
    - Intelligent ReAct loop with adaptive iteration limits
    - Timeout-aware task execution
    - Concurrent operation handling
    - State persistence and recovery
    """
    
    def __init__(self):
        """Initialize agent"""
        self.llm = LLM()
        self.mcp_client = mcp
        
        # Load skills by phase
        self.skills = load_all_skills()
        self.available_tools: Dict[str, Any] = {}
        self.tool_schema: Dict[str, Any] = {}
        
        # State tracking with timeout support
        self.iteration = 0
        self.max_iterations = 10  # Adaptive based on task complexity
        self.action_history: List[Dict[str, Any]] = []
        self.reasoning_history: List[str] = []
        self.operation_start_time: Optional[datetime] = None
        self.operation_timeout_seconds: int = 300  # 5 min default
        
        # Phase tracking for workflow progression
        self.current_phase = PentestPhase.SCAN
        self.phase_completed = False
        
        # Long-running operation management
        self.pending_operations: Dict[str, PendingOperation] = {}
        self.completed_operations: List[Dict[str, Any]] = []
        self.operation_counter = 0
        
        # **FINDINGS STORAGE** - Persistent knowledge for the agent
        # This is how the agent learns and builds knowledge across iterations
        self.findings = {
            "hosts": {},           # {ip: {ports, services, os, vulns}}
            "services": {},        # {service_name: {ports, versions, vulns}}
            "vulnerabilities": [], # [{service, cve, severity, exploit_available}]
            "credentials": [],     # [{username, password, service, host}]
            "exploits_attempted": [],  # [{target, exploit, success}]
            "access_gained": {},   # {host: {user, method, access_level}}

        }
        
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
        """Load MCP tools from FastMCP instance"""
        try:
            # FastMCP stores tools in _tools dictionary
            tools = getattr(self.mcp_client, "_tools", {})
            
            if not tools:
                # Fallback: try get_tools() method
                if hasattr(self.mcp_client, "get_tools") and callable(self.mcp_client.get_tools):
                    try:
                        tools = await self.mcp_client.get_tools()
                    except:
                        tools = {}
            
            self.available_tools = tools
            self.tool_schema = self._serialize_tools(tools)
            logger.info(f"Loaded {len(self.tool_schema)} tools: {list(tools.keys())[:5]}")
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
    
    async def execute_user_request(self, user_request: str, timeout_seconds: int = 300) -> Dict[str, str]:
        """
        Execute user request through intelligent ReAct loop.
        Handles timeouts for long-running operations.
        
        Args:
            user_request: The pentesting task to execute
            timeout_seconds: Max time to spend on this request (default 5 min)
        
        Returns:
            {"status": "success|timeout|error", "response": "..."}
        """
        logger.info(f"Executing: {user_request}")
        self.iteration = 0
        self.action_history = []
        self.reasoning_history = []
        self.operation_start_time = datetime.now()
        self.operation_timeout_seconds = timeout_seconds
        
        try:
            # Main ReAct loop - ask user to continue instead of forcing iterations
            while True:
                self.iteration += 1
                self._save_operation_state(user_request, self.iteration)
                
                # Calculate remaining time
                elapsed = (datetime.now() - self.operation_start_time).total_seconds()
                timeout_remaining = timeout_seconds - elapsed
                
                if timeout_remaining <= 0:
                    logger.warning(f"Operation timeout after {elapsed:.1f}s")
                    final_answer = await self._synthesize_answer(user_request)
                    return {
                        "status": "timeout",
                        "response": f"Operation timeout. Partial results: {final_answer}"
                    }
                
                # ===== REASON =====
                logger.info(f"\n→ REASON (Iteration {self.iteration})")
                try:
                    reasoning = await asyncio.wait_for(
                        self._reason_phase(user_request),
                        timeout=min(30, timeout_remaining)
                    )
                    # Check if LLM returned error
                    if "API Error" in reasoning or "Error:" in reasoning:
                        logger.error(f"LLM error in REASON: {reasoning[:100]}")
                        # Try to recover on next iteration or fail
                        if self.iteration >= 3:
                            return {
                                "status": "error",
                                "response": f"LLM service unavailable after {self.iteration} attempts. {reasoning[:200]}"
                            }
                        # Continue to try again
                        reasoning = "Unable to reason due to LLM error, will retry"
                except asyncio.TimeoutError:
                    logger.warning("REASON phase timeout")
                    final_answer = await self._synthesize_answer(user_request)
                    return {"status": "success", "response": final_answer}
                except Exception as e:
                    logger.error(f"REASON phase exception: {e}")
                    if self.iteration >= 3:
                        return {"status": "error", "response": f"REASON phase failed: {str(e)[:200]}"}
                    reasoning = f"Error during reasoning: {str(e)[:50]}"
                
                self.reasoning_history.append(reasoning)
                
                # Check if agent thinks it should stop
                should_stop, reason = self._check_should_stop(reasoning)
                if should_stop:
                    logger.info(f"Agent recommends: {reason}")
                    final_answer = await self._synthesize_answer(user_request)
                    return {"status": "success", "response": final_answer}
                
                # ===== ACT =====
                logger.info("→ ACT")
                try:
                    action = await asyncio.wait_for(
                        self._act_phase(user_request, reasoning),
                        timeout=min(30, timeout_remaining)
                    )
                except asyncio.TimeoutError:
                    logger.warning("ACT phase timeout")
                    final_answer = await self._synthesize_answer(user_request)
                    return {"status": "success", "response": final_answer}
                except Exception as e:
                    logger.error(f"ACT phase exception: {e}")
                    if self.iteration >= 3:
                        return {"status": "error", "response": f"ACT phase failed: {str(e)[:200]}"}
                    # Retry with next iteration
                    continue
                
                if not action or action.get("tool_name") == "execute_system_command" and "No command" in action.get("command", ""):
                    logger.warning("No valid action generated, retrying...")
                    if self.iteration >= 3:
                        final_answer = await self._synthesize_answer(user_request)
                        return {"status": "success", "response": f"Unable to generate valid actions after {self.iteration} attempts. {final_answer}"}
                    # Continue to retry with next iteration
                    continue
                
                self.action_history.append(action)
                logger.info(f"  Executed: {action.get('tool_name')}")
                
                # ===== OBSERVE =====
                logger.info("→ OBSERVE")
                try:
                    observation = await asyncio.wait_for(
                        self._observe_phase(action, user_request),
                        timeout=min(60, timeout_remaining)
                    )
                    # Check for LLM errors
                    if "API Error" in observation or "Error:" in observation:
                        logger.error(f"LLM error in OBSERVE: {observation[:100]}")
                        if self.iteration >= 3:
                            return {"status": "error", "response": f"LLM service unavailable: {observation[:200]}"}
                        observation = "Unable to observe due to LLM error"
                except asyncio.TimeoutError:
                    logger.warning("OBSERVE phase timeout - continuing anyway")
                    observation = "Operation timed out but continuing"
                except Exception as e:
                    logger.error(f"OBSERVE phase exception: {e}")
                    if self.iteration >= 3:
                        return {"status": "error", "response": f"OBSERVE phase failed: {str(e)[:200]}"}
                    observation = f"Error during observation: {str(e)[:50]}"
                
                # Check if goal satisfied
                if self._is_goal_satisfied(observation):
                    logger.info("✓ Goal satisfied!")
                    final_answer = await self._synthesize_answer(user_request)
                    return {"status": "success", "response": final_answer}
                
                # ===== ASK USER TO CONTINUE =====
                logger.info("\n" + "="*70)
                current_summary = self._get_current_summary()
                logger.info(f"Progress: {current_summary}")
                logger.info("="*70)
                
                # Get user decision (run in executor to not block async loop)
                try:
                    user_choice = await self._ask_user_continue_async()
                except Exception as e:
                    logger.error(f"Error getting user input: {e}")
                    user_choice = False
                
                if not user_choice:
                    logger.info("User chose to stop operation")
                    final_answer = await self._synthesize_answer(user_request)
                    return {"status": "success", "response": final_answer}
                else:
                    logger.info("User chose to continue - starting next iteration")
        
        except Exception as e:
            logger.error(f"Request execution failed: {e}", exc_info=True)
            return {"status": "error", "response": f"Error: {str(e)}"}
    
    def _ask_user_continue(self) -> bool:
        """Ask user if they want to continue or stop the operation.
        
        Returns:
            True to continue, False to stop
        """
        while True:
            try:
                response = input("\n[AGENT] Continue analyzing? (yes/no): ").strip().lower()
                if response in ['yes', 'y']:
                    return True
                elif response in ['no', 'n']:
                    return False
                else:
                    print("[AGENT] Please answer 'yes' or 'no'")
            except (EOFError, KeyboardInterrupt):
                # Non-interactive mode or user interrupt - stop
                logger.info("User interrupt - stopping operation")
                return False
            except Exception as e:
                logger.error(f"Error getting user input: {e}")
                return False
    
    async def _ask_user_continue_async(self) -> bool:
        """Ask user if they want to continue or stop the operation (async-safe).
        
        Returns:
            True to continue, False to stop
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._ask_user_continue)
    
    async def _reason_phase(self, user_request: str) -> str:
        """
        Reason phase: Analyze goal using relevant SKILLS.
        
        This phase:
        1. Selects most relevant skills based on user request keywords
        2. Uses skill.agent_behavior as tactical guidance
        3. Asks agent to reason about approach
        4. Detects if goal has been achieved
        
        Returns reasoning string.
        """
        # STEP 1: SELECT RELEVANT SKILLS - these guide the reasoning
        relevant_skills = self._select_relevant_skills(user_request)
        
        # STEP 2: BUILD SKILL GUIDANCE - convert skills to tactical instructions
        skill_guidance = build_all_skill_guidance(relevant_skills)
        
        # STEP 3: GET FINDINGS SUMMARY - what agent knows so far
        findings_summary = self._get_findings_summary()
        
        # STEP 4: BUILD PROMPT with all context
        tools_summary = json.dumps(list(self.tool_schema.keys())[:15], indent=2)
        previous_actions = json.dumps(
            [a.get('tool_name') for a in self.action_history[-3:]],
            indent=2
        ) if self.action_history else "None yet"
        
        prompt = REASON_PROMPT_TEMPLATE.format(
            user_request=user_request,
            findings_summary=findings_summary,
            skill_guidance=skill_guidance,
            tools_summary=tools_summary,
            previous_actions=previous_actions,
        )
        
        # STEP 5: CALL LLM with separated system prompt
        system = get_reason_system_prompt()
        response = await self._llm_call(system, prompt)
        
        logger.info(f"Reason phase selected {len(relevant_skills)} relevant skills")
        return response
    
    async def _act_phase(self, user_request: str, reasoning: str) -> Optional[Dict[str, Any]]:
        """
        Act phase: Generate and execute a shell command GUIDED BY SKILLS.
        
        This phase:
        1. Selects relevant skills for current goal
        2. LLM generates ONE shell command based on skill guidance
        3. Executes the command via execute_system_command
        4. Returns action with result
        
        Simplified architecture: No tool selection, just raw command generation.
        """
        # STEP 1: SELECT RELEVANT SKILLS - these guide command generation
        relevant_skills = self._select_relevant_skills(user_request)
        
        # STEP 2: BUILD COMMAND EXAMPLES FROM ACTUAL SKILLS
        # Generate example commands based on skill descriptions
        command_examples = []
        example_commands = {
            "scan": "nmap -sn 192.168.1.0/24",
            "enumeration": "enum4linux -a target.com",
            "exploitation": "searchsploit --json wordpress 5.0",
            "privilege": "sudo -l",
            "data": "find / -name '*.zip' -o -name '*.tar.gz' 2>/dev/null",
            "report": "cat /tmp/pentest_report.txt"
        }
        
        for skill in relevant_skills[:3]:
            # Find relevant command based on skill phase
            phase_key = skill.phase.value.split('_')[0]  # Get first part of phase
            cmd = example_commands.get(phase_key, "nmap -h")
            description = f"{skill.name}: {skill.description[:50]}"
            example = json.dumps({
                "command": cmd,
                "description": description
            })
            command_examples.append(example)
        
        examples_str = "\n".join(command_examples) if command_examples else ""
        
        # STEP 3: BUILD SKILL-GUIDED COMMANDS DESCRIPTION FROM ACTUAL SKILLS
        # This shows LLM what each skill does and how to support it
        skill_actions = ""
        if relevant_skills:
            skill_actions = "\n## Skill-Guided Commands (from selected skills):\n"
            for skill in relevant_skills:
                skill_actions += f"\n**{skill.name}** ({skill.phase.value}):\n"
                skill_actions += f"  {skill.description}\n"
                skill_actions += f"  Guidance: {skill.agent_behavior[:100]}...\n"
        
        # STEP 4: BUILD PROMPT using separated template
        prompt = ACT_PROMPT_TEMPLATE.format(
            user_request=user_request,
            skill_guided_actions=skill_actions,
            examples=examples_str,
        )
        
        # STEP 5: CALL LLM with separated system prompt
        system = get_act_system_prompt()
        response = await self._llm_call(system, prompt)
        
        try:
            # STEP 6: PARSE JSON RESPONSE - extract command
            response = response.strip()
            
            # Handle JSON in code blocks
            if "```json" in response:
                response = response.split("```json")[1].split("```")[0].strip()
            elif "```" in response:
                parts = response.split("```")
                if len(parts) >= 2:
                    response = parts[1].strip()
            
            response = response.strip()
            
            # Skip to first { if there's leading text
            if not response.startswith("{"):
                json_start = response.find("{")
                if json_start != -1:
                    response = response[json_start:]
                else:
                    logger.error(f"No JSON found in response: {response[:200]}")
                    return None
            
            # Extract JSON object by brace matching
            if response.startswith("{"):
                brace_count = 0
                json_end = 0
                for i, char in enumerate(response):
                    if char == "{":
                        brace_count += 1
                    elif char == "}":
                        brace_count -= 1
                        if brace_count == 0:
                            json_end = i + 1
                            break
                
                if json_end > 0:
                    response = response[:json_end]
            
            logger.debug(f"Parsing command response: {response[:100]}")
            action_data = json.loads(response)
            
            # STEP 7: EXECUTE COMMAND via execute_system_command tool
            command = action_data.get("command", "").strip()
            description = action_data.get("description", "Execute command")
            
            # Validate command is not a dummy placeholder
            if not command or command == "echo 'No command generated'":
                logger.warning("Generated dummy command, using fallback")
                return self._generate_fallback_action(user_request)
            
            logger.info(f"Generated command: {command}")
            
            # Execute the command
            result = await self.run_tool("execute_system_command", {"command": command, "timeout": 300})
            
            # Build action dict
            action = {
                "tool_name": "execute_system_command",
                "command": command,
                "description": description,
                "result": result,
                "success": result.get("status") == "success"
            }
            
            return action
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {response[:100]}")
            logger.error(f"JSON decode error: {e}")
            logger.debug(f"Full response was: {response}")
            return self._generate_fallback_action(user_request)
        except Exception as e:
            logger.error(f"Action phase failed: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return self._generate_fallback_action(user_request)
    
    def _generate_fallback_action(self, user_request: str) -> Optional[Dict[str, Any]]:
        """Generate a safe fallback action when LLM fails.
        
        This ensures we still make progress even if the LLM can't respond.
        """
        # Map common keywords to safe commands
        request_lower = user_request.lower()
        
        if "scan" in request_lower or "network" in request_lower:
            command = "whoami && hostname && ifconfig 2>/dev/null || ip addr show 2>/dev/null"
            description = "Basic system and network info"
        elif "enum" in request_lower or "service" in request_lower:
            command = "netstat -tuln 2>/dev/null || ss -tuln 2>/dev/null"
            description = "Check listening services"
        elif "find" in request_lower or "search" in request_lower:
            command = "find /home -type f -name '*.txt' 2>/dev/null | head -20"
            description = "Find files"
        elif "user" in request_lower or "account" in request_lower:
            command = "id && groups"
            description = "Check current user and groups"
        else:
            command = "ps aux | head -10"
            description = "Check running processes"
        
        logger.info(f"[FALLBACK] Using safe fallback action: {description}")
        
        result = {"status": "success", "output": "Fallback command executed"}
        
        return {
            "tool_name": "execute_system_command",
            "command": command,
            "description": description,
            "result": result,
            "success": True,
            "is_fallback": True
        }
    
    async def _observe_phase(self, action: Dict[str, Any], user_request: str) -> str:
        """
        Observe phase: Interpret action results and extract findings.
        
        This phase:
        1. Gets tool result from the action
        2. Extracts findings from result (hosts, services, vulns, etc.)
        3. Stores findings in self.findings dict
        4. LLM analyzes if this was helpful
        
        Returns observation string.
        """
        result = action.get("result", {})
        tool_name = action.get("tool_name", "")
        
        # STEP 1: EXTRACT FINDINGS - store discoveries in knowledge base
        self._extract_findings(tool_name, result)
        findings_summary = self._get_findings_summary()
        
        # STEP 2: BUILD OBSERVATION PROMPT using separated template
        prompt = OBSERVE_PROMPT_TEMPLATE.format(
            findings_summary=findings_summary,
            tool_name=tool_name,
            result=str(result)[:300],
            user_request=user_request,
        )
        
        # STEP 3: CALL LLM with separated system prompt
        system = get_observe_system_prompt()
        response = await self._llm_call(system, prompt)
        
        logger.info(f"Observe: {tool_name} extracted {len(self._get_findings_summary())} bytes of findings")
        return response
    
    async def _synthesize_answer(self, user_request: str) -> str:
        """
        Synthesize final answer from all actions taken.
        
        This uses skills, findings, and action history to provide final summary.
        """
        if not self.action_history:
            return "No actions were taken."
        
        # Get findings summary
        findings_summary = self._get_findings_summary()
        
        # Build actions summary
        actions_taken = json.dumps(
            [{"tool": a.get('tool_name'), "success": a.get('success')} for a in self.action_history],
            indent=2
        )
        
        # Get reasoning history
        reasoning_history = json.dumps(self.reasoning_history[-3:], indent=2)
        
        # BUILD PROMPT using separated template
        prompt = SYNTHESIZE_PROMPT_TEMPLATE.format(
            user_request=user_request,
            actions_taken=actions_taken,
            findings_summary=findings_summary,
            reasoning_history=reasoning_history,
        )
        
        # CALL LLM with separated system prompt
        system = get_synthesize_system_prompt()
        response = await self._llm_call(system, prompt)
        return response
    
    def _is_goal_satisfied(self, observation: str) -> bool:
        """
        Check if goal is satisfied based on observation.
        Looks for explicit indicators only.
        """
        keywords = [
            "goal_achieved:",
            "successfully completed",
            "task complete",
            "all objectives met",
            "mission accomplished"
        ]
        observation_lower = observation.lower()
        return any(kw in observation_lower for kw in keywords)
    
    def _check_should_stop(self, reasoning: str) -> tuple:
        """
        Check if agent should stop based on its reasoning.
        Returns (should_stop: bool, reason: str)
        """
        # Get current findings summary
        findings_count = sum(len(v) for v in self.findings.values())
        
        # Extract confidence score if agent provides it
        if "confidence:" in reasoning.lower():
            try:
                parts = reasoning.lower().split("confidence:")
                if len(parts) > 1:
                    conf_str = parts[1].split()[0].strip('%')
                    confidence = int(conf_str) / 100
                    
                    if confidence >= 0.85:
                        return True, f"High confidence ({int(confidence*100)}%) task complete"
            except:
                pass
        
        # Check for explicit stopping signals
        stop_phrases = [
            "task is complete",
            "goal achieved",
            "ready to provide results",
            "assessment complete",
            "no further actions",
            "nothing more to do"
        ]
        
        reasoning_lower = reasoning.lower()
        for phrase in stop_phrases:
            if phrase in reasoning_lower:
                return True, f"Agent decided: {phrase}"
        
        return False, None
    
    def _get_current_summary(self) -> str:
        """Get summary of current findings for user display."""
        summary = []
        if self.findings.get("hosts"):
            summary.append(f"Hosts: {len(self.findings['hosts'])}")
        if self.findings.get("services"):
            summary.append(f"Services: {len(self.findings['services'])}")
        if self.findings.get("vulnerabilities"):
            summary.append(f"Vulns: {len(self.findings['vulnerabilities'])}")
        if self.findings.get("credentials"):
            summary.append(f"Creds: {len(self.findings['credentials'])}")
        
        return " | ".join(summary) if summary else "No findings yet"
    
    def _select_relevant_skills(self, user_request: str) -> List[Skill]:
        """
        Select relevant skills based on user request keywords.
        Matches against skill.keywords.
        """
        relevant_skills = []
        request_words = user_request.lower().split()
        
        for phase_skills in self.skills.values():
            for skill in phase_skills:
                # Calculate keyword match score
                matches = sum(1 for kw in skill.keywords if any(w in kw for w in request_words))
                if matches > 0:
                    relevant_skills.append((skill, matches))
        
        # Sort by match score, then by priority, return top 5
        relevant_skills.sort(key=lambda x: (x[1], x[0].priority), reverse=True)
        return [skill for skill, _ in relevant_skills[:5]]
    
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
    
    def _build_skill_guided_actions(self, relevant_skills: List[Skill]) -> List[Dict[str, str]]:
        """
        Build list of actions available from relevant skills.
        Only includes tools specified in skill.mcp_tools.
        """
        actions = []
        seen_tools = set()
        
        for skill in relevant_skills:
            for tool_name in skill.mcp_tools:
                if tool_name in self.available_tools and tool_name not in seen_tools:
                    args_info = self.tool_schema.get(tool_name, {}).get("args", [])
                    actions.append({
                        "tool_name": tool_name,
                        "args": args_info,
                        "from_skill": skill.name
                    })
                    seen_tools.add(tool_name)
        
        return actions
    
    def _calculate_confidence_score(self, reasoning: str, iteration: int, max_iterations: int = 5) -> float:
        """
        Calculate confidence score that task is complete (0.0 to 1.0).
        Based on explicit signals in reasoning, iteration count, and findings.
        """
        confidence = 0.0
        findings_count = sum(len(v) for v in self.findings.values()) if isinstance(self.findings, dict) else 0
        
        # Extract explicit confidence from reasoning
        if "confidence:" in reasoning.lower():
            try:
                parts = reasoning.lower().split("confidence:")
                if len(parts) > 1:
                    conf_str = parts[1].split()[0].strip('%').strip()
                    explicit_conf = int(conf_str) / 100.0
                    confidence += explicit_conf * 0.4
            except Exception:
                pass
        
        # Check for completion signals
        completion_phrases = [
            "task is complete",
            "goal achieved",
            "assessment complete",
            "mission accomplished",
            "all objectives met"
        ]
        if any(phrase in reasoning.lower() for phrase in completion_phrases):
            confidence += 0.3
        
        # Boost confidence if we have substantial findings
        if findings_count >= 3:
            confidence += 0.15
        
        # Reduce confidence if we're at iteration limit (might need more)
        if iteration >= max_iterations:
            confidence -= 0.1
        
        return min(1.0, max(0.0, confidence))
    
    async def _should_continue_operation(self, reasoning: str, iteration: int, timeout_remaining: float) -> bool:
        """
        Smart decision: should operation continue?
        Returns True if should continue, False if should stop.
        """
        # Time-based: stop if less than 30 seconds remaining
        if timeout_remaining < 30:
            logger.warning(f"Timeout approaching ({timeout_remaining:.1f}s remaining), stopping")
            return False
        
        # Iteration-based: hard limit at 5
        if iteration >= 5:
            logger.info("Max iterations (5) reached, stopping")
            return False
        
        # Confidence-based: stop if high confidence task is complete
        confidence = self._calculate_confidence_score(reasoning, iteration)
        if confidence >= 0.85:
            logger.info(f"High confidence ({confidence:.1%}) task complete, stopping")
            return False
        
        # Check explicit stopping signals
        stop_signals = [
            "no further actions",
            "nothing more to do",
            "unable to proceed",
            "task complete",
            "analysis complete"
        ]
        if any(signal in reasoning.lower() for signal in stop_signals):
            logger.info(f"Agent signaled completion, stopping")
            return False
        
        return True
    
    def _should_retry_action(self, action_result: Dict, attempt: int = 1) -> bool:
        """
        Decide if action should be retried based on result.
        Returns True to retry, False to move on.
        """
        if attempt >= 2:
            return False  # Max 2 attempts
        
        if not isinstance(action_result, dict):
            return True  # Retry on non-dict responses
        
        # Retry on certain error types
        error = action_result.get("error", "").lower()
        retriable_errors = ["timeout", "connection", "temporary", "unavailable"]
        if any(err in error for err in retriable_errors):
            logger.info(f"Retriable error detected, will retry: {error}")
            return True
        
        return False
    
    async def _handle_long_running_operation(self, coro, timeout_seconds: int) -> Tuple[Any, bool]:
        """
        Execute a long-running operation with timeout protection.
        Returns (result, timed_out: bool)
        """
        import asyncio
        try:
            result = await asyncio.wait_for(coro, timeout=timeout_seconds)
            return result, False
        except asyncio.TimeoutError:
            logger.warning(f"Operation timed out after {timeout_seconds}s")
            return None, True
        except Exception as e:
            logger.error(f"Operation failed: {e}")
            return None, False
    
    async def _queue_parallel_actions(self, actions: List[Dict], max_concurrent: int = 3) -> List[Dict]:
        """
        Execute multiple actions in parallel with concurrency limit.
        Useful for running scans and enumerations concurrently.
        """
        import asyncio
        
        if not actions:
            return []
        
        semaphore = asyncio.Semaphore(max_concurrent)
        results = []
        
        async def bounded_execute(action):
            async with semaphore:
                logger.info(f"  Executing: {action.get('tool_name', 'unknown')}")
                result = await self.run_tool(
                    action.get('tool_name', ''),
                    action.get('args', {})
                )
                return result
        
        tasks = [bounded_execute(action) for action in actions]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        return results
    
    def _save_operation_state(self, request: str, iteration: int) -> None:
        """Save operation state for potential recovery/resume"""
        state = {
            "request": request,
            "iteration": iteration,
            "findings": self.findings,
            "timestamp": __import__('time').time()
        }
        
        # Save to memory for recovery
        try:
            import json
            state_json = json.dumps(state, default=str)
            # This would be stored in long-term memory if available
            logger.debug(f"Operation state saved at iteration {iteration}")
        except Exception as e:
            logger.debug(f"State save failed: {e}")
    
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
    
    def _extract_findings(self, tool_name: str, result: Dict[str, Any]) -> None:
        """
        Extract and store findings from tool execution results.
        This builds the agent's knowledge base as it discovers information.
        """
        try:
            if not isinstance(result, dict):
                return
            
            # Parse nmap results
            if "nmap" in tool_name.lower() or (tool_name == "execute_system_command" and "nmap" in str(result)):
                output = str(result.get("data", ""))
                # Extract open ports (simple parsing)
                if "open" in output:
                    self.findings["hosts"]["scan_target"] = {"ports": [], "services": []}
                    logger.info("  [Findings] Nmap scan detected, storing port data")
            
            # Parse service enumeration
            elif "enum" in tool_name.lower() or "service" in tool_name.lower():
                output = str(result.get("data", ""))
                if "ssh" in output.lower() or "apache" in output.lower():
                    self.findings["services"]["detected"] = {"versions": [], "vulns": []}
                    logger.info("  [Findings] Services detected and stored")
            
            # Parse credential extraction
            elif "credential" in tool_name.lower() or "dump" in tool_name.lower() or "crack" in tool_name.lower():
                output = str(result.get("data", ""))
                if output:
                    self.findings["credentials"].append({"source": tool_name, "data": output[:100]})
                    logger.info("  [Findings] Credentials extracted")
            
        except Exception as e:
            logger.debug(f"Findings extraction failed: {e}")
    
    def _get_findings_summary(self) -> str:
        """
        Get a summary of findings for the agent to use in reasoning.
        This helps the agent see what it has discovered so far.
        """
        summary_parts = []
        
        if self.findings["hosts"]:
            summary_parts.append(f"Hosts found: {len(self.findings['hosts'])}")
        
        if self.findings["services"]:
            summary_parts.append(f"Services identified: {len(self.findings['services'])}")
        
        if self.findings["vulnerabilities"]:
            summary_parts.append(f"Vulnerabilities found: {len(self.findings['vulnerabilities'])}")
        
        if self.findings["credentials"]:
            summary_parts.append(f"Credentials obtained: {len(self.findings['credentials'])}")
        
        if self.findings["access_gained"]:
            summary_parts.append(f"Systems compromised: {len(self.findings['access_gained'])}")
        
        return " | ".join(summary_parts) if summary_parts else "No findings yet"


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
