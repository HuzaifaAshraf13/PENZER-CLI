"""Bubblewrap command construction for terminal execution."""

from __future__ import annotations

import os
import shutil


class SandboxUnavailable(RuntimeError):
    """Raised when a requested sandbox backend is unavailable."""


def build_sandbox_command(command: list[str], workdir: str, mode: str | None = None) -> list[str]:
    selected = (mode or os.getenv("PENZER_SANDBOX_MODE", "off")).strip().lower()
    if selected == "off":
        return command
    if selected not in {"read-only", "workspace-write"}:
        raise ValueError(f"Unknown sandbox mode: {selected}")

    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise SandboxUnavailable(
            f"PENZER_SANDBOX_MODE={selected} requires bubblewrap (bwrap), but it is not installed."
        )

    workspace = os.path.abspath(os.path.expanduser(workdir or os.getcwd()))
    if not os.path.isdir(workspace):
        raise ValueError(f"Sandbox workdir does not exist: {workspace}")

    writable = selected == "workspace-write"
    workspace_mount = "--bind" if writable else "--ro-bind"
    return [
        bwrap,
        "--die-with-parent",
        "--unshare-net",
        "--new-session",
        "--ro-bind", "/", "/",
        workspace_mount, workspace, workspace,
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        "--chdir", workspace,
        "--",
        *command,
    ]
