"""
Penzer Autonomous Pentesting Agent
Simple ReAct loop (Reason → Act → Observe) using skills
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
        self.max_iterations = 10
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
        Execute user request through ReAct loop.
        
        Returns:
            {"status": "success|error", "response": "..."}
        """
        logger.info(f"Executing: {user_request}")
        self.iteration = 0
        self.action_history = []
        self.reasoning_history = []
        
        try:
            # Main ReAct loop
            while self.iteration < self.max_iterations:
                self.iteration += 1
                logger.info(f"\n{'='*60}")
                logger.info(f"ITERATION {self.iteration}/{self.max_iterations}")
                logger.info(f"{'='*60}")
                
                # ===== REASON =====
                logger.info("→ REASON")
                reasoning = await self._reason_phase(user_request)
                self.reasoning_history.append(reasoning)
                logger.info(f"  Reasoning: {reasoning[:100]}")
                
                # Check if done reasoning
                if "goal_achieved" in reasoning.lower() or "complete" in reasoning.lower():
                    logger.info("  Goal marked as achieved in reasoning")
                    return {"status": "success", "response": reasoning}
                
                # ===== ACT =====
                logger.info("→ ACT")
                action = await self._act_phase(user_request, reasoning)
                
                if not action:
                    logger.warning("  No action to take")
                    break
                
                self.action_history.append(action)
                logger.info(f"  Tool: {action.get('tool_name')}")
                
                # ===== OBSERVE =====
                logger.info("→ OBSERVE")
                observation = await self._observe_phase(action, user_request)
                logger.info(f"  Observation: {observation[:100]}")
                
                # Check if goal achieved
                if "goal_achieved" in observation.lower() or "complete" in observation.lower():
                    logger.info("  Goal achieved!")
                    final_answer = await self._synthesize_answer(user_request)
                    return {"status": "success", "response": final_answer}
            
            # Max iterations reached
            logger.warning(f"Max iterations ({self.max_iterations}) reached")
            final_answer = await self._synthesize_answer(user_request)
            return {"status": "success", "response": final_answer}
        
        except Exception as e:
            logger.error(f"Request execution failed: {e}")
            return {"status": "error", "response": str(e)}
    
    async def _reason_phase(self, user_request: str) -> str:
        """
        Reason phase: Analyze goal using relevant skills.
        Selects skills based on user request keywords and uses their agent_behavior.
        
        Returns reasoning string.
        """
        # Select relevant skills based on user request
        relevant_skills = self._select_relevant_skills(user_request)
        
        # Build skill guidance from agent_behavior
        skill_guidance = ""
        if relevant_skills:
            skill_guidance = "\n## Relevant Skills & Guidance\n"
            for i, skill in enumerate(relevant_skills, 1):
                skill_guidance += f"\n### Skill {i}: {skill.name} ({skill.phase.value})\n"
                skill_guidance += f"Description: {skill.description}\n"
                skill_guidance += f"Available Tools: {', '.join(skill.mcp_tools)}\n"
                skill_guidance += f"Guidance:\n{skill.agent_behavior}\n"
        
        tools_summary = json.dumps(list(self.tool_schema.keys())[:15], indent=2)
        
        prompt = f"""
You are an autonomous pentesting agent. Analyze the user request and reason about the approach.

## User Request
{user_request}

{skill_guidance}

## Available Tools
{tools_summary}

## Previous Actions
{json.dumps([a.get('tool_name') for a in self.action_history[-3:]], indent=2) if self.action_history else "None yet"}

## Task
Reason about:
1. What is the goal?
2. What constraints exist?
3. Which skill guidance should we follow?
4. What's the next step?
5. Have we achieved the goal?

Respond with clear reasoning. If goal is achieved, say "GOAL_ACHIEVED: [summary]"
"""
        
        system = "You are a pentesting agent reasoning engine guided by skill instructions. Be precise and tactical."
        response = await self._llm_call(system, prompt)
        return response
    
    async def _act_phase(self, user_request: str, reasoning: str) -> Optional[Dict[str, Any]]:
        """
        Act phase: Select and execute a tool or skill.
        Only uses tools from relevant skills.
        
        Returns action dict or None if no action.
        """
        # Select relevant skills and their tools
        relevant_skills = self._select_relevant_skills(user_request)
        available_actions = self._build_skill_guided_actions(relevant_skills)
        
        if not available_actions:
            logger.warning("No relevant skill tools available")
            available_actions = self._build_available_actions()
        
        # Build action examples based on available tools
        action_examples = []
        for action in available_actions[:3]:
            tool = action.get("tool_name")
            if tool == "execute_system_command":
                action_examples.append(f'{{"tool_name": "execute_system_command", "args": {{"command": "nmap -p- target.com"}}, "description": "Run network scan"}}')
            elif tool == "check_available_tools":
                action_examples.append(f'{{"tool_name": "check_available_tools", "args": {{"tool_category": "network"}}, "description": "List available tools"}}')
            else:
                action_examples.append(f'{{"tool_name": "{tool}", "args": {{}}, "description": "Execute {tool}"}}')
        
        examples_str = "\n".join(action_examples) if action_examples else ""
        
        prompt = f"""Based on the goal and reasoning, select ONE tool to execute next.

AVAILABLE TOOLS:
{json.dumps([a.get('tool_name') for a in available_actions], indent=2)}

EXAMPLES:
{examples_str}

RESPOND WITH ONLY THIS JSON FORMAT (no other text):
{{"tool_name": "TOOL_NAME", "args": {{"key": "value"}}, "description": "description"}}"""
        
        system = "You MUST respond with ONLY valid JSON. No explanations, no other text. Just the JSON object."
        response = await self._llm_call(system, prompt)
        
        try:
            # Try to parse JSON - handle various response formats
            response = response.strip()
            
            # If response contains JSON in code block, extract it
            if "```json" in response:
                response = response.split("```json")[1].split("```")[0].strip()
            elif "```" in response:
                response = response.split("```")[1].split("```")[0].strip()
            
            # Remove any leading/trailing whitespace and newlines
            response = response.strip()
            
            # Try to find JSON object in response (handle cases where LLM adds text around JSON)
            if response.startswith("{"):
                # Find the matching closing brace
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
            
            logger.debug(f"Parsing JSON response: {response[:100]}")
            action = json.loads(response)
            
            # Validate tool exists
            tool_name = action.get("tool_name")
            if tool_name not in self.available_tools:
                logger.warning(f"Invalid tool selected: {tool_name}, available: {list(self.available_tools.keys())}")
                # Fallback: pick first available tool if available
                if self.available_tools:
                    tool_name = list(self.available_tools.keys())[0]
                    action["tool_name"] = tool_name
                    logger.info(f"Fallback to tool: {tool_name}")
                else:
                    return None
            
            # Execute tool
            args = action.get("args", {})
            result = await self.run_tool(tool_name, args)
            action["result"] = result
            action["success"] = result.get("status") == "success"
            
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
        
        Returns observation string.
        """
        result = action.get("result", {})
        tool_name = action.get("tool_name", "")
        
        # **EXTRACT FINDINGS** - Build knowledge base from results
        self._extract_findings(tool_name, result)
        findings_summary = self._get_findings_summary()
        
        prompt = f"""
Analyze the result. Current findings: {findings_summary}

Tool: {tool_name}
Result: {str(result)[:300]}
Goal: {user_request}

Was this helpful? Have we found what we need? Keep response SHORT (1 sentence)."""
        
        system = "Analyze tool results briefly. Be concise."
        response = await self._llm_call(system, prompt)
        return response
    
    async def _synthesize_answer(self, user_request: str) -> str:
        """Synthesize final answer from all actions taken"""
        if not self.action_history:
            return "No actions were taken."
        
        prompt = f"""
Synthesize a final answer based on all actions taken.

## Original Request
{user_request}

## Actions Taken
{json.dumps([{"tool": a.get('tool_name'), "success": a.get('success')} for a in self.action_history], indent=2)}

## Reasoning History
{json.dumps(self.reasoning_history[-3:], indent=2)}

Provide a concise final answer to the user's request based on what we learned.
"""
        
        system = "You are synthesizing a final answer. Be concise and direct."
        response = await self._llm_call(system, prompt)
        return response
    
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
