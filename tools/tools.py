# tools/tools.py
from typing import List, Optional
import subprocess
import shlex
import tempfile
import os
from agent.server import mcp
from tools.ToolsPrompts import NMAP_SCAN_PROMPT, RUN_MSFCONSOLE_COMMAND_PROMPT

@mcp.tool()
def nmap_scan(target: str, args: Optional[str] = "-sV -Pn", authorization: Optional[str] = None) -> str:
    _ = NMAP_SCAN_PROMPT  # reference prompt for LLM reasoning
    try:
        cmd = ["nmap"] + shlex.split(args) + [target]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return proc.stdout or proc.stderr or ""
    except Exception as e:
        return f"ERROR: {e}"

@mcp.tool()
def run_msfconsole_command(commands: List[str], authorization: Optional[str] = None) -> str:
    _ = RUN_MSFCONSOLE_COMMAND_PROMPT
    cmdfile = None
    try:
        with tempfile.NamedTemporaryFile("w", delete=False) as tf:
            for c in commands:
                tf.write(c.rstrip("\r\n") + "\n")
            cmdfile = tf.name
        proc = subprocess.run(["msfconsole", "-q", "-r", cmdfile], capture_output=True, text=True, timeout=300)
        return proc.stdout or proc.stderr or ""
    except Exception as e:
        return f"ERROR: {e}"
    finally:
        if cmdfile and os.path.exists(cmdfile):
            try: os.remove(cmdfile)
            except: pass
