import os
import logging
from typing import List, Dict, Any, Optional
from reme_ai import ReMeApp
# 🚀 IMPORT the central instance, don't create a new one
from agent.core import mcp 

# Setup structured logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Penzer-Memory")

# 2. Configure Persistent ReMe Storage
db_path = os.path.join(os.getcwd(), "memory_store")
os.makedirs(db_path, exist_ok=True)

reme_app = ReMeApp(
    "llm.default.model_name=qwen3-8b",
    "embedding_model.default.model_name=text-embedding-v4",
    "vector_store.default.backend=chroma", 
    f"vector_store.chroma.path={db_path}"
)

# --------------------------------------------------
# 1. RESOURCES
# --------------------------------------------------

@mcp.resource("pentest://{workspace_id}/scope")
async def get_scope_resource(workspace_id: str) -> str:
    """Provides the current Rules of Engagement and Scope constraints."""
    async with reme_app as app:
        result = await app.async_execute(
            name="retrieve_personal_memory",
            workspace_id=workspace_id,
            query="What is the authorized scope and rules of engagement?"
        )
        return str(result.get("answer", "No scope defined."))

@mcp.resource("pentest://{workspace_id}/session_summary")
async def get_session_summary(workspace_id: str) -> str:
    """High-level summary of all findings (ports, vulnerabilities) in this session."""
    async with reme_app as app:
        result = await app.async_execute(
            name="retrieve_task_memory",
            workspace_id=workspace_id,
            query="Summarize all current findings, open ports, and vulnerabilities discovered."
        )
        return str(result.get("answer", "No findings logged yet."))

# --------------------------------------------------
# 2. TOOLS
# --------------------------------------------------

@mcp.tool(name="mem_log_finding")
async def log_finding(workspace_id: str, finding: str, severity: str = "info"):
    """Log a discovery. Severity: info, low, medium, high, or critical."""
    async with reme_app as app:
        return await app.async_execute(
            name="summary_task_memory",
            workspace_id=workspace_id,
            trajectories=[{"role": "assistant", "content": f"[{severity.upper()}] Finding: {finding}"}]
        )

@mcp.tool(name="mem_archive_exploit_playbook")
async def archive_exploit_playbook(workspace_id: str, service_name: str, steps: List[str], success: bool):
    """Archives a sequence of commands for a specific service."""
    status = "SUCCESS" if success else "FAILED"
    formatted_steps = "\n".join([f"- {step}" for step in steps])
    async with reme_app as app:
        return await app.async_execute(
            name="summary_task_memory",
            workspace_id=workspace_id,
            trajectories=[
                {"role": "user", "content": f"Target Service: {service_name}"},
                {"role": "assistant", "content": f"Steps taken:\n{formatted_steps}\nResult: {status}"}
            ]
        )

@mcp.tool(name="mem_query_past_experiences")
async def query_past_experiences(workspace_id: str, technology_stack: str):
    """Search memory for past successful exploits."""
    async with reme_app as app:
        result = await app.async_execute(
            name="retrieve_task_memory",
            workspace_id=workspace_id,
            query=f"Past exploit paths for: {technology_stack}"
        )
        return result.get("answer", "No prior experience found.")

@mcp.tool(name="mem_set_operator_preference")
async def set_operator_preference(workspace_id: str, preference: str):
    """Store specific operator instructions."""
    async with reme_app as app:
        return await app.async_execute(
            name="summary_personal_memory",
            workspace_id=workspace_id,
            trajectories=[{"role": "user", "content": preference}]
        )