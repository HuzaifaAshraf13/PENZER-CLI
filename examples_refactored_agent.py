"""
Example usage and testing of refactored Penzer Agent
Demonstrates the Reason → Plan → Act cycle in action
"""

import asyncio
import logging
from agent.agent import PenzerAgent
from logger import get_logger

logger = get_logger(__name__)


async def example_1_basic_usage():
    """Example 1: Basic agent usage"""
    print("\n" + "="*70)
    print("EXAMPLE 1: Basic Agent Usage")
    print("="*70)
    
    # Initialize agent
    agent = await PenzerAgent().async_init()
    
    # Execute request
    request = "Check what security tools are available on the system"
    result = await agent.execute_user_request(request)
    
    print(f"\nStatus: {result['status']}")
    print(f"Response: {result['response'][:200]}")


async def example_2_understand_state():
    """Example 2: Understanding agent state"""
    print("\n" + "="*70)
    print("EXAMPLE 2: Understanding Agent State")
    print("="*70)
    
    from agent.agent_state import AgentStateManager, ReasoningOutput, PlanningOutput
    
    # Create state
    state_mgr = AgentStateManager("session_123", "Scan the network", max_iterations=5)
    
    # Simulate reasoning
    reasoning = ReasoningOutput(
        context_summary="Network scanning requested",
        goal_analysis="Discover open ports on local network",
        constraints=["Limited to local subnet", "No root access"],
        relevant_memory={"previous_network": "192.168.1.0/24"}
    )
    state_mgr.state.add_reasoning(reasoning)
    
    # Simulate planning
    planning = PlanningOutput(
        overall_strategy="Use nmap for network discovery",
        step_by_step_plan=[
            {"step": 1, "description": "Check nmap availability", "tool": "check_available_tools"},
            {"step": 2, "description": "Scan network", "tool": "execute_system_command"}
        ],
        tool_selection_rationale="nmap is fastest and most reliable",
        success_criteria=["Ports discovered", "Services identified"],
        risk_assessment="Low risk operation"
    )
    state_mgr.state.add_planning(planning)
    
    # Display state
    print("\nAgent State Summary:")
    print(f"  Session: {state_mgr.state.session_id}")
    print(f"  Request: {state_mgr.state.user_request}")
    print(f"  Status: {state_mgr.state.status}")
    print(f"  Iterations: {state_mgr.state.iteration}/{state_mgr.state.max_iterations}")
    
    print("\nCycle Components:")
    print(f"  Reasoning steps: {len(state_mgr.state.reasoning_history)}")
    print(f"  Planning steps: {len(state_mgr.state.planning_history)}")
    print(f"  Actions taken: {len(state_mgr.state.action_history)}")
    print(f"  Observations: {len(state_mgr.state.observation_history)}")
    
    # Show AI context
    print("\nAI-Friendly Context:")
    print(state_mgr.state.get_context_for_llm()[:400])


async def example_3_engines_individually():
    """Example 3: Using engines individually"""
    print("\n" + "="*70)
    print("EXAMPLE 3: Individual Engine Usage")
    print("="*70)
    
    from agent.agent import PenzerAgent
    from agent.agent_state import AgentState
    
    agent = await PenzerAgent().async_init()
    state = AgentState(session_id="test", user_request="List system tools")
    
    # Reasoning Engine
    print("\n1. REASONING ENGINE")
    reasoning = await agent.reasoning_engine.reason(
        "List available system tools",
        state,
        agent.memory_manager.get_short_term_context(),
        agent.memory_manager.get_long_term_context()
    )
    print(f"   Goal: {reasoning.goal_analysis}")
    print(f"   Context: {reasoning.context_summary}")
    print(f"   Constraints: {reasoning.constraints}")
    
    # Planning Engine
    print("\n2. PLANNING ENGINE")
    plan = await agent.planning_engine.plan(
        "List available system tools",
        reasoning,
        state
    )
    print(f"   Strategy: {plan.overall_strategy}")
    print(f"   Steps: {len(plan.step_by_step_plan)}")
    if plan.step_by_step_plan:
        for step in plan.step_by_step_plan[:2]:
            print(f"     - {step.get('description')}")
    
    # Action Executor
    print("\n3. ACTION EXECUTOR")
    action = await agent.action_executor.execute(plan)
    print(f"   Tool: {action.tool_name}")
    print(f"   Success: {action.success}")
    if action.error_message:
        print(f"   Error: {action.error_message}")
    
    # Observation Engine
    print("\n4. OBSERVATION ENGINE")
    if action.success:
        observation = await agent.observation_engine.observe(
            action,
            plan.success_criteria,
            state,
            "List available system tools"
        )
        print(f"   Interpretation: {observation.interpretation}")
        print(f"   Findings: {observation.key_findings}")
        print(f"   Goal Achieved: {observation.iteration_complete}")


async def example_4_memory_usage():
    """Example 4: Memory management"""
    print("\n" + "="*70)
    print("EXAMPLE 4: Memory Management")
    print("="*70)
    
    from agent.agent import PenzerAgent
    
    agent = await PenzerAgent().async_init()
    
    # Save to short-term
    print("\n1. Short-term Memory")
    await agent.memory_manager.save_short_term(
        "pentest_1",
        "open_ports",
        ["22", "80", "443"]
    )
    print(f"   Saved: open_ports")
    print(f"   Short-term entries: {agent.memory_manager.summary()['short_term_entries']}")
    
    # Save to long-term
    print("\n2. Long-term Memory")
    await agent.memory_manager.save_long_term(
        "pentest_1",
        "vulnerability_pattern_sql_injection",
        {
            "pattern": "SQL injection in login form",
            "location": "/admin/login.php",
            "severity": "high"
        }
    )
    print(f"   Saved: vulnerability_pattern_sql_injection")
    print(f"   Long-term entries: {agent.memory_manager.summary()['long_term_entries']}")
    
    # Search memory
    print("\n3. Memory Search")
    results = await agent.memory_manager.search_memory("pentest_1", "port")
    print(f"   Search results for 'port':")
    for key, value in results['short_term'].items():
        print(f"     - {key}: {value}")
    
    # Get context
    print("\n4. Memory Context for LLM")
    short_context = agent.memory_manager.get_short_term_context(max_items=5)
    print(f"   Short-term context entries: {len(short_context)}")
    print(f"   Keys: {list(short_context.keys())}")


async def example_5_full_cycle():
    """Example 5: Full Reason → Plan → Act cycle"""
    print("\n" + "="*70)
    print("EXAMPLE 5: Full Cycle Execution")
    print("="*70 + "\n")
    
    agent = await PenzerAgent().async_init()
    
    request = "Check what penetration testing tools are available"
    print(f"Request: {request}\n")
    
    result = await agent.execute_user_request(request)
    
    print(f"\n{'='*70}")
    print("FINAL RESULT")
    print(f"{'='*70}")
    print(f"Status: {result['status']}")
    print(f"Response: {result['response'][:500]}")


async def main():
    """Run all examples"""
    print("\n" + "█"*70)
    print("PENZER AGENT REFACTORING - EXAMPLES")
    print("█"*70)
    
    try:
        # Run examples
        await example_1_basic_usage()
        await example_2_understand_state()
        await example_3_engines_individually()
        await example_4_memory_usage()
        await example_5_full_cycle()
        
        print("\n" + "█"*70)
        print("ALL EXAMPLES COMPLETED SUCCESSFULLY")
        print("█"*70 + "\n")
    
    except KeyboardInterrupt:
        print("\nInterrupted by user")
    except Exception as e:
        logger.error(f"Example execution failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
