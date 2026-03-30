"""
Agent State Manager for Penzer Autonomous Agent
Tracks reasoning, planning, actions, and memory for clear agent decision-making
"""

import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from dataclasses import dataclass, field, asdict
from enum import Enum

logger = logging.getLogger(__name__)


class AgentPhase(str, Enum):
    """Phases of the Reason-Plan-Act cycle"""
    REASONING = "reasoning"
    PLANNING = "planning"
    ACTING = "acting"
    OBSERVATION = "observation"
    REFLECTION = "reflection"


@dataclass
class ReasoningOutput:
    """Output from the reasoning phase"""
    phase: str = AgentPhase.REASONING.value
    context_summary: str = ""
    goal_analysis: str = ""
    constraints: List[str] = field(default_factory=list)
    relevant_memory: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PlanningOutput:
    """Output from the planning phase"""
    phase: str = AgentPhase.PLANNING.value
    overall_strategy: str = ""
    step_by_step_plan: List[Dict[str, str]] = field(default_factory=list)
    tool_selection_rationale: str = ""
    success_criteria: List[str] = field(default_factory=list)
    risk_assessment: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ActionOutput:
    """Output from the acting phase"""
    phase: str = AgentPhase.ACTING.value
    action_type: str = ""  # tool_execution, search, api_call, etc.
    action_description: str = ""
    tool_name: Optional[str] = None
    tool_args: Dict[str, Any] = field(default_factory=dict)
    result: Dict[str, Any] = field(default_factory=dict)
    success: bool = False
    error_message: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ObservationOutput:
    """Output from observing action results"""
    phase: str = AgentPhase.OBSERVATION.value
    action_result: Dict[str, Any] = field(default_factory=dict)
    interpretation: str = ""
    key_findings: List[str] = field(default_factory=list)
    next_steps_suggested: List[str] = field(default_factory=list)
    iteration_complete: bool = False
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AgentState:
    """
    Complete state of the agent during execution.
    Tracks all phases of the Reason-Plan-Act cycle.
    """
    
    # Session info
    session_id: str = ""
    user_request: str = ""
    start_time: str = field(default_factory=lambda: datetime.now().isoformat())
    
    # Cycle tracking
    iteration: int = 0
    max_iterations: int = 10
    
    # Phase outputs
    reasoning_history: List[ReasoningOutput] = field(default_factory=list)
    planning_history: List[PlanningOutput] = field(default_factory=list)
    action_history: List[ActionOutput] = field(default_factory=list)
    observation_history: List[ObservationOutput] = field(default_factory=list)
    
    # Memory references
    short_term_memory: Dict[str, Any] = field(default_factory=dict)
    long_term_memory: Dict[str, Any] = field(default_factory=dict)
    
    # Final output
    final_answer: Optional[str] = None
    status: str = "active"  # active, completed, failed, timeout
    error_message: Optional[str] = None
    
    def add_reasoning(self, output: ReasoningOutput) -> None:
        """Add reasoning phase output"""
        self.reasoning_history.append(output)
        logger.debug(f"Reasoning added (iteration {self.iteration}): {output.goal_analysis[:100]}")
    
    def add_planning(self, output: PlanningOutput) -> None:
        """Add planning phase output"""
        self.planning_history.append(output)
        logger.debug(f"Planning added (iteration {self.iteration}): {output.overall_strategy[:100]}")
    
    def add_action(self, output: ActionOutput) -> None:
        """Add action phase output"""
        self.action_history.append(output)
        logger.debug(f"Action added: {output.action_type} - {output.action_description[:100]}")
    
    def add_observation(self, output: ObservationOutput) -> None:
        """Add observation phase output"""
        self.observation_history.append(output)
        logger.debug(f"Observation added: {output.interpretation[:100]}")
    
    def get_last_reasoning(self) -> Optional[ReasoningOutput]:
        """Get most recent reasoning output"""
        return self.reasoning_history[-1] if self.reasoning_history else None
    
    def get_last_planning(self) -> Optional[PlanningOutput]:
        """Get most recent planning output"""
        return self.planning_history[-1] if self.planning_history else None
    
    def get_last_action(self) -> Optional[ActionOutput]:
        """Get most recent action output"""
        return self.action_history[-1] if self.action_history else None
    
    def get_last_observation(self) -> Optional[ObservationOutput]:
        """Get most recent observation output"""
        return self.observation_history[-1] if self.observation_history else None
    
    def get_cycle_summary(self) -> Dict[str, Any]:
        """Get summary of current cycle"""
        return {
            "iteration": self.iteration,
            "reasoning_steps": len(self.reasoning_history),
            "planning_steps": len(self.planning_history),
            "actions_taken": len(self.action_history),
            "observations": len(self.observation_history),
            "status": self.status
        }
    
    def get_action_chain(self) -> List[Dict[str, Any]]:
        """Get chain of actions and observations for context"""
        chain = []
        for action in self.action_history[-5:]:  # Last 5 actions
            chain.append({
                "action": action.action_description,
                "result": action.success,
                "key_finding": action.result.get("data", {}) if action.success else action.error_message
            })
        return chain
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert entire state to dictionary for serialization"""
        return {
            "session_id": self.session_id,
            "user_request": self.user_request,
            "start_time": self.start_time,
            "iteration": self.iteration,
            "max_iterations": self.max_iterations,
            "reasoning_history": [r.to_dict() for r in self.reasoning_history],
            "planning_history": [p.to_dict() for p in self.planning_history],
            "action_history": [a.to_dict() for a in self.action_history],
            "observation_history": [o.to_dict() for o in self.observation_history],
            "short_term_memory": self.short_term_memory,
            "long_term_memory": self.long_term_memory,
            "final_answer": self.final_answer,
            "status": self.status,
            "error_message": self.error_message
        }
    
    def get_context_for_llm(self) -> str:
        """
        Generate AI-friendly context string for LLM comprehension.
        Structured to help LLM understand state clearly.
        """
        lines = []
        lines.append("# AGENT STATE CONTEXT")
        lines.append(f"## Current Iteration: {self.iteration}/{self.max_iterations}")
        
        if self.reasoning_history:
            last_reason = self.reasoning_history[-1]
            lines.append(f"\n## Recent Reasoning:\n- Goal: {last_reason.goal_analysis}")
            lines.append(f"- Constraints: {', '.join(last_reason.constraints) or 'None'}")
        
        if self.planning_history:
            last_plan = self.planning_history[-1]
            lines.append(f"\n## Current Plan:\n{last_plan.overall_strategy}")
            if last_plan.step_by_step_plan:
                lines.append("### Steps:")
                for i, step in enumerate(last_plan.step_by_step_plan[:3], 1):
                    lines.append(f"  {i}. {step.get('description', '')}")
        
        if self.action_history:
            last_action = self.action_history[-1]
            lines.append(f"\n## Last Action:\n- Type: {last_action.action_type}")
            lines.append(f"- Status: {'✓ Success' if last_action.success else '✗ Failed'}")
            if last_action.error_message:
                lines.append(f"- Error: {last_action.error_message}")
        
        if self.short_term_memory:
            lines.append(f"\n## Short-term Memory:\n{json.dumps(self.short_term_memory, indent=2)[:500]}")
        
        lines.append("\n## Available Actions:")
        lines.append("- Execute a tool")
        lines.append("- Search for information")
        lines.append("- Provide final answer (when goal achieved)")
        
        return "\n".join(lines)


class AgentStateManager:
    """Manager for agent state lifecycle"""
    
    def __init__(self, session_id: str, user_request: str, max_iterations: int = 10):
        """Initialize state manager"""
        self.state = AgentState(
            session_id=session_id,
            user_request=user_request,
            max_iterations=max_iterations
        )
        logger.info(f"AgentStateManager initialized for session {session_id}")
    
    def increment_iteration(self) -> None:
        """Move to next iteration"""
        self.state.iteration += 1
        logger.info(f"Iteration incremented to {self.state.iteration}")
    
    def is_max_iterations_reached(self) -> bool:
        """Check if max iterations exceeded"""
        return self.state.iteration >= self.state.max_iterations
    
    def set_complete(self, final_answer: str) -> None:
        """Mark agent as complete with final answer"""
        self.state.final_answer = final_answer
        self.state.status = "completed"
        logger.info("Agent state marked as completed")
    
    def set_failed(self, error_message: str) -> None:
        """Mark agent as failed"""
        self.state.error_message = error_message
        self.state.status = "failed"
        logger.error(f"Agent state marked as failed: {error_message}")
    
    def set_timeout(self) -> None:
        """Mark agent as timed out"""
        self.state.status = "timeout"
        logger.warning("Agent state marked as timeout")
    
    def export_state(self, filepath: Optional[str] = None) -> Dict[str, Any]:
        """Export state for analysis or persistence"""
        state_dict = self.state.to_dict()
        
        if filepath:
            try:
                with open(filepath, 'w') as f:
                    json.dump(state_dict, f, indent=2)
                logger.info(f"State exported to {filepath}")
            except Exception as e:
                logger.error(f"Failed to export state: {e}")
        
        return state_dict
