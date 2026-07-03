from agent.agent import PenzerAgent


def test_resume_snapshot_restores_execution_state():
    agent = PenzerAgent.__new__(PenzerAgent)
    agent._goal = ""
    agent.history = []
    agent._trace = []
    agent._resume_state = {}
    agent._milestones = []
    agent._execution_queue = []
    agent._execution_index = 0
    agent._active_execution_item = None
    agent._belief = {"goal_progress": "not_started", "verified_facts": [], "assumptions": [], "unknowns": [], "last_action": "", "last_outcome": ""}
    agent._complexity_score = 0.0
    agent._is_complex_task = False
    agent._max_iter = 10
    agent._matched_skills = []
    agent._last_matched_skills = []
    agent._system_prompt = ""
    agent._subtasks = []
    agent._subtask_idx = 0
    agent._milestone_idx = 0
    agent._total_subtasks = 0
    agent._current_subtask = ""
    agent._execution_complete = False

    snapshot = {
        "goal": "inspect memory",
        "history": [{"role": "user", "content": "hi"}],
        "trace": [{"tool": "memory"}],
        "resume_state": {"current_step": "check memory", "blocked_steps": ["none"]},
        "milestones": [{"milestone": "inspect", "steps": ["look up facts"]}],
        "execution_queue": [{"kind": "step", "title": "check memory"}],
        "execution_index": 1,
        "active_execution_item": {"title": "check memory"},
        "belief": {"goal_progress": "blocked", "verified_facts": ["fact"], "assumptions": [], "unknowns": [], "last_action": "memory", "last_outcome": "done"},
        "complexity_score": 0.8,
        "is_complex_task": True,
        "max_iter": 20,
        "matched_skills": ["memory"],
        "last_matched_skills": ["memory"],
        "system_prompt": "prompt",
        "subtasks": ["look up facts"],
        "subtask_idx": 1,
        "milestone_idx": 0,
        "total_subtasks": 1,
        "current_subtask": "look up facts",
        "execution_complete": True,
    }

    agent._restore_snapshot(snapshot)

    assert agent._goal == "inspect memory"
    assert agent._resume_state["current_step"] == "check memory"
    assert agent._milestones[0]["milestone"] == "inspect"
    assert agent._execution_queue[0]["title"] == "check memory"
    assert agent._execution_index == 1
    assert agent._active_execution_item["title"] == "check memory"
    assert agent._belief["goal_progress"] == "blocked"
    assert agent._complexity_score == 0.8
    assert agent._subtasks == ["look up facts"]
    assert agent._subtask_idx == 1
    assert agent._milestone_idx == 0
    assert agent._total_subtasks == 1
    assert agent._current_subtask == "look up facts"
    assert agent._execution_complete is True
