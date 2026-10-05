"""Working-directory state shared by terminal calls."""

import os


_cwd = os.getcwd()
_SESSION_CWDS: dict[str, str] = {}


def resolve_workdir(session_id: str | None, workdir: str | None) -> str:
    if workdir:
        target = os.path.abspath(os.path.expanduser(workdir))
        if session_id:
            _SESSION_CWDS[session_id] = target
        return target

    if session_id and session_id in _SESSION_CWDS:
        return _SESSION_CWDS[session_id]

    return _cwd


def set_workdir(session_id: str | None, target: str) -> None:
    if session_id:
        _SESSION_CWDS[session_id] = target
    else:
        global _cwd
        _cwd = target