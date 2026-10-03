"""CDP target discovery, attachment, and per-session event collection."""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

from cdpify import Client

from .session import CDPSession


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CDPEventParams:
    args: List[Dict[str, Any]] = field(default_factory=list)
    type: str = ""
    stackTrace: Dict[str, Any] = field(default_factory=dict)
    request: Dict[str, Any] = field(default_factory=dict)
    response: Dict[str, Any] = field(default_factory=dict)
    errorText: str = ""

    def as_params(self) -> Dict[str, Any]:
        return {
            "args": self.args,
            "type": self.type,
            "stackTrace": self.stackTrace,
            "request": self.request,
            "response": self.response,
            "errorText": self.errorText,
        }


async def refresh_tabs(session: CDPSession) -> List[Dict[str, str]]:
    if session.root_client is None:
        return []
    result = await session.root_client.execute("Target.getTargets")
    targets = [target for target in result.get("targetInfos", []) if target.get("type") == "page"]
    live_ids = {target["targetId"] for target in targets}
    for target_id in list(session.target_clients):
        if target_id not in live_ids:
            session.target_clients.pop(target_id, None)
            session.target_sessions.pop(target_id, None)
    for target in targets:
        target_id = target["targetId"]
        if target_id not in session.target_clients:
            attached = await session.root_client.execute(
                "Target.attachToTarget", {"targetId": target_id, "flatten": True}
            )
            session.target_sessions[target_id] = attached["sessionId"]
            session.target_clients[target_id] = session.root_client.session(attached["sessionId"])
            await session.target_clients[target_id].execute("Page.enable")
            await session.target_clients[target_id].execute("Runtime.enable")
            await session.target_clients[target_id].execute("Network.enable")
    if session.target_id not in live_ids and targets:
        session.target_id = targets[0]["targetId"]
        session.client = session.target_clients[session.target_id]
        session.element_refs.clear()
    return [{
        "id": target["targetId"],
        "url": target.get("url", ""),
        "title": target.get("title", ""),
        "active": target["targetId"] == session.target_id,
    } for target in targets]


async def capture_events(root_client: Client, session: CDPSession, event_name: str) -> None:
    try:
        async for received in root_client.listen_all(event_name, CDPEventParams):
            event = received.value
            params = event.as_params()
            cdp_session_id = received.session_id
            target_id = next((
                target for target, value in session.target_sessions.items()
                if value == cdp_session_id
            ), "")
            if event_name == "Runtime.consoleAPICalled":
                args = params.get("args", [])
                message = " ".join(str(arg.get("value", arg.get("description", ""))) for arg in args)
                session.console_messages.append({
                    "type": str(params.get("type", "log")),
                    "text": message[:500],
                    "url": str((params.get("stackTrace", {}).get("callFrames") or [{}])[0].get("url", "")),
                })
                del session.console_messages[:-50]
            else:
                session.network_events.append({
                    "event": event_name.rsplit(".", 1)[-1],
                    "target": target_id,
                    "url": str(params.get("request", {}).get("url") or params.get("response", {}).get("url") or "")[:500],
                    "method": str(params.get("request", {}).get("method", "")),
                    "status": params.get("response", {}).get("status"),
                    "error": str(params.get("errorText", "")),
                })
                del session.network_events[:-100]
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.debug("CDP event stream ended: %s", event_name, exc_info=True)