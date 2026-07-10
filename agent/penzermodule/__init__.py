"""PENZER manager modules — split out of the former monolithic agent.py.

Each manager takes the owning PenzerAgent instance explicitly as `agent`
in its methods and operates on its state directly; state ownership
didn't move, only the behavior. agent.py keeps every original method
name as a one-line delegate, so external code calling the agent is
unaffected by this split.
"""
from agent.penzermodule.belief_manager import Phase, PHASE_TRANSITIONS, PHASE_TO_GOAL_PROGRESS, BeliefManager
from agent.penzermodule.memory_manager import MemoryManager
from agent.penzermodule.planner import Planner
from agent.penzermodule.execution_manager import ExecutionManager
from agent.penzermodule.reflection_manager import ReflectionManager
from agent.penzermodule.persistence_manager import PersistenceManager
from agent.penzermodule.resource_monitor import ResourceMonitor

__all__ = [
    "Phase", "PHASE_TRANSITIONS", "PHASE_TO_GOAL_PROGRESS",
    "BeliefManager", "MemoryManager", "Planner", "ExecutionManager",
    "ReflectionManager", "PersistenceManager", "ResourceMonitor",
]