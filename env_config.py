"""Environment-aware configuration shared by Penzer runtime components."""

from __future__ import annotations

import os


def _boolean(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


ENVIRONMENT = os.getenv("PENZER_ENV", os.getenv("ENVIRONMENT", "dev"))
PROFILE = os.getenv("PENZER_PROFILE", os.getenv("PROFILE", "quality"))
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "auto")
LLM_MODEL = os.getenv("LLM_MODEL", "default")
MEMORY_BACKEND = os.getenv("MEMORY_BACKEND", "file")
REDIS_URL = os.getenv("REDIS_URL", "")
ENABLE_PARALLEL_TOOLS = _boolean("ENABLE_PARALLEL_TOOLS", True)
MAX_PARALLEL_TOOLS = int(os.getenv("MAX_PARALLEL_TOOLS", "4"))
SAFETY_LEVEL = os.getenv("SAFETY_LEVEL", "strict")
REQUIRE_APPROVAL = _boolean("REQUIRE_APPROVAL", True)
AUTO_APPROVE = _boolean("AUTO_APPROVE", False)

_BUDGETS = {
    "dev": (20, 21600, 2000000),
    "test": (10, 600, 200000),
    "production": (50, 21600, 2000000),
}
MAX_ITERATIONS, MAX_RUNTIME_SECONDS, MAX_TOKENS_PER_RUN = _BUDGETS.get(
    ENVIRONMENT, _BUDGETS["dev"]
)


def get_llm_config() -> dict[str, str]:
    """Return normalized LLM settings, reading environment at call time."""
    provider = os.getenv("LLM_PROVIDER", LLM_PROVIDER)
    api_key = os.getenv("LLM_API_KEY", "")
    api_url = os.getenv("LLM_API_URL", os.getenv("LOCAL_SERVER_URL", ""))
    model = os.getenv("LLM_MODEL", LLM_MODEL)
    return {
        "provider": provider,
        "api_key": api_key,
        "api_url": api_url,
        "base_url": api_url,
        "model": model,
    }
