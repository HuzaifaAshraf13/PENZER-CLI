"""
Penzer Autonomous Pentesting Agent
ReAct loop (Reason → Act → Observe) with Skill-Guided Decision Making

The agent uses skills as tactical guidance for autonomous pentesting.
Skills define WHAT the agent should do (scan, enumerate, exploit, etc.)
System prompts separate from agent logic for modularity.
"""

import json
import asyncio
import logging
from typing import Dict, Any, List, Optional
from enum import Enum

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


class LoopPhase(Enum):
    """Current phase in ReAct loop"""
    REASON = "reason"
    ACT = "act"
    OBSERVE = "observe"


class PenzerAgent:
    """
    Autonomous pentesting agent with Reason → Act → Observe loop.
    Uses skills to guide decision-making and builds persistent findings.
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
        self.max_iterations = 5  # Reduced from 10 for faster execution
        self.action_history: List[Dict[str, Any]] = []
        self.reasoning_history: List[str] = []
        
        # Phase tracking for workflow progression
        self.current_phase = PentestPhase.SCAN
        self.phase_completed = False
        
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
    
    async def execute_user_request(self, user_request: str) -> Dict[str, str]:
        """
        Execute user request through smart ReAct loop.
        Agent decides when to stop or ask user for continuation.
        
        Returns:
            {"status": "success|error", "response": "..."}
        """
        logger.info(f"Executing: {user_request}")
        self.iteration = 0
        self.action_history = []
        self.reasoning_history = []
        
        try:
            # Smart ReAct loop - agent decides when to continue
            while True:
                self.iteration += 1
                
                # ===== REASON =====
                logger.info("\n→ REASON")
                reasoning = await self._reason_phase(user_request)
                self.reasoning_history.append(reasoning)
                
                # Check if agent thinks it should stop
                should_stop, reason = self._check_should_stop(reasoning)
                if should_stop:
                    logger.info(f"Agent: {reason}")
                    final_answer = await self._synthesize_answer(user_request)
                    return {"status": "success", "response": final_answer}
                
                # ===== ACT =====
                logger.info("→ ACT")
                action = await self._act_phase(user_request, reasoning)
                
                if not action:
                    logger.warning("No action to take, stopping")
                    final_answer = await self._synthesize_answer(user_request)
                    return {"status": "success", "response": final_answer}
                
                self.action_history.append(action)
                logger.info(f"  Executed: {action.get('tool_name')}")
                
                # ===== OBSERVE =====
                logger.info("→ OBSERVE")
                observation = await self._observe_phase(action, user_request)
                
                # Check if satisfied
                if self._is_goal_satisfied(observation):
                    logger.info("Goal satisfied!")
                    final_answer = await self._synthesize_answer(user_request)
                    return {"status": "success", "response": final_answer}
                
                # Check if too many iterations - ask user
                if self.iteration >= self.max_iterations:
                    logger.warning(f"Reached {self.max_iterations} iterations")
                    summary = self._get_current_summary()
                    print(f"\n{'='*70}")
                    print(f"[AGENT STATUS]")
                    print(f"Iterations: {self.iteration}")
                    print(f"Actions: {len(self.action_history)}")
                    print(f"Summary: {summary[:200]}")
                    print(f"{'='*70}")
                    print(f"\nContinue? (y/n): ", end="", flush=True)
                    try:
                        user_input = input().lower().strip()
                        if user_input != 'y':
                            logger.info("User stopped agent")
                            final_answer = await self._synthesize_answer(user_request)
                            return {"status": "success", "response": final_answer}
                        else:
                            self.iteration = 0  # Reset counter
                            logger.info("Continuing...")
                    except:
                        # If no input available, stop
                        final_answer = await self._synthesize_answer(user_request)
                        return {"status": "success", "response": final_answer}
        
        except Exception as e:
            logger.error(f"Request execution failed: {e}")
            return {"status": "error", "response": str(e)}
    
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
            command = action_data.get("command", "echo 'No command generated'")
            description = action_data.get("description", "Execute command")
            
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
            return None
        except Exception as e:
            logger.error(f"Action phase failed: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return None
    
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
