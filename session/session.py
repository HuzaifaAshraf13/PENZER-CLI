from __future__ import annotations

import json
import logging
from typing import Any

from agent.core import mcp, reme_app
from tools.standards import success, error

logger = logging.getLogger(__name__)

_mem: dict[str, dict[str, Any]] = {}


def _reme_ok() -> bool:
    return reme_app is not None and getattr(reme_app, "_initialized", False)


def _session(ws: str) -> dict[str, Any]:
    return _mem.setdefault(ws, {})


async def _reme(task: str, ws: str, **kw):
    """Call ReMe or return None on failure."""
    if not _reme_ok():
        return None
    try:
        return await reme_app.async_execute(task, workspace_id=ws, **kw)
    except Exception:
        logger.exception("ReMe %s failed", task)
        return None


@mcp.tool()
async def memory(
    action: str,
    workspace_id: str,
    data: dict[str, Any] | None = None,
    query: str | None = None,
) -> dict:
    """
    Manage workspace memory.

    action      : store | retrieve | search | forget
    workspace_id: scope key (e.g. "pentest_1")
    data        : key/value pairs to store  (store only)
    query       : search term               (search / retrieve)

    Examples:
        memory(action="store",    workspace_id="ws1", data={"ports": [22, 80]})
        memory(action="retrieve", workspace_id="ws1")
        memory(action="search",   workspace_id="ws1", query="SQL injection")
        memory(action="forget",   workspace_id="ws1")
    """
    data   = data or {}
    action = action.lower().strip()

    try:
        if action == "store":
            _session(workspace_id).update(data)
            lt = await _reme("summary_task_memory", workspace_id,
                             trajectories=[{"role": "assistant", "content": json.dumps(data)}])
            return success(
                data={"workspace_id": workspace_id, "stored_keys": list(data.keys())},
                metadata={"long_term_saved": lt is not None},
            )

        if action == "retrieve":
            res = {"session": dict(_session(workspace_id))}
            lt  = await _reme("retrieve_task_memory", workspace_id,
                              query=query or "Return all memory for this workspace.")
            if lt:
                res["long_term"] = lt.get("answer", {})
            return success(data=res)

        if action == "search":
            if not query:
                return error("query is required for search")
            needle  = query.lower()
            matches = {k: v for k, v in _session(workspace_id).items()
                       if needle in k.lower() or needle in str(v).lower()}
            res = {"session": matches}
            lt  = await _reme("retrieve_task_memory", workspace_id, query=query)
            if lt:
                res["long_term"] = lt.get("answer", {})
            return success(data=res)

        if action == "forget":
            deleted = len(_mem.pop(workspace_id, {}))
            return success(data={"workspace_id": workspace_id, "deleted_entries": deleted})

        return error("Unknown action. Use: store | retrieve | search | forget")

    except Exception as exc:
        logger.exception("Memory tool failed")
        return error(f"Memory operation failed: {exc}")