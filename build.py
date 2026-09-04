"""Build-time validation helpers for Penzer's 12-factor runtime."""

from __future__ import annotations


def validate_config() -> dict:
    import env_config

    required = ("LLM_PROVIDER", "LLM_MODEL", "ENVIRONMENT", "PROFILE")
    missing = [name for name in required if not getattr(env_config, name, None)]
    return {"status": "error" if missing else "success", "missing": missing}


def validate_skills() -> dict:
    from agent.skills import load_all_skills

    data = load_all_skills()
    count = len(data.get("core", [])) + len(data.get("generated", []))
    return {"status": "success", "skill_count": count}


def validate_tools() -> dict:
    try:
        from tools.plugins import load_plugin_tools
        count = len(load_plugin_tools())
    except Exception:
        count = 0
    return {"status": "success", "tool_count": count}
