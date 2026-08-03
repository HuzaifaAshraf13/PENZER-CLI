# tools/file_editor_tool.py
"""
File Editor Tool: Read, write, edit, delete, and manage files.
"""

import difflib
import os
from pathlib import Path

from agent.core import mcp
from tools.standards import success, error, warning
from agent.activity_timeline import emit_activity_event, update_activity_event

_WORKDIR = Path.cwd()


def _is_disallowed_write(target: Path) -> bool:
    try:
        resolved = target.resolve(strict=False)
        workdir = _WORKDIR.resolve(strict=False)
        return not str(resolved).startswith(str(workdir))
    except Exception:
        return True


def _requires_approval(action: str, filepath: Path | None) -> tuple[bool, str]:
    if action in {"delete", "write", "append", "replace"} and filepath is not None:
        if _is_disallowed_write(filepath):
            return True, "Approval required before destructive or out-of-workdir file mutation."
    return False, ""


@mcp.tool()
def file_editor(action: str, filepath: str = None, content: str = None, 
                find: str = None, replace: str = None, line_start: int = None, 
                line_end: int = None) -> dict:
    """
    File editor tool for reading, writing, and editing files.
    
    Actions:
        read: Read entire file or specific lines
        write: Write content to file (creates if not exists)
        append: Append content to file
        replace: Find and replace text (or specific line ranges)
        delete: Delete file
        create: Create new empty file
        list: List directory contents
        diff: Show diff between two files
        
    Args:
        action: What to do (read, write, append, replace, delete, create, list, diff)
        filepath: Path to file
        content: Content to write/append
        find: Text to find (for replace action)
        replace: Text to replace with (for replace action)
        line_start: Start line for range operations (1-indexed)
        line_end: End line for range operations
    """
    try:
        filepath = Path(filepath) if filepath else None

        if filepath is not None:
            requires_approval, approval_message = _requires_approval(action, filepath)
            if requires_approval:
                return warning(data={}, message=approval_message)

        if action == "read":
            if not filepath:
                return error("read action requires 'filepath'")
            
            if not filepath.exists():
                return error(f"File not found: {filepath}")
            
            activity_id = emit_activity_event(
                event_type="file_operation",
                title="Reading file",
                message=str(filepath),
                status="running",
                details={"operation": "read", "path": str(filepath)},
            )
            try:
                text = filepath.read_text()
                
                # Handle line ranges
                if line_start or line_end:
                    lines = text.split('\n')
                    start = (line_start or 1) - 1
                    end = (line_end or len(lines))
                    text = '\n'.join(lines[start:end])
                
                if activity_id:
                    update_activity_event(activity_id, status="success", details={"operation": "read", "path": str(filepath), "size_bytes": len(text), "lines": len(text.split('\n'))})
                return success(data={
                    "action": "read",
                    "filepath": str(filepath),
                    "size_bytes": len(text),
                    "lines": len(text.split('\n')),
                    "content": text[:10000]  # First 10KB
                })
            except Exception as e:
                if activity_id:
                    update_activity_event(activity_id, status="failed", message=str(e))
                return error(f"Could not read file: {str(e)}")
        
        elif action == "write":
            if not filepath or content is None:
                return error("write action requires 'filepath' and 'content'")
            
            activity_id = emit_activity_event(
                event_type="file_operation",
                title="Writing file",
                message=str(filepath),
                status="running",
                details={"operation": "write", "path": str(filepath)},
            )
            try:
                old_text = filepath.read_text() if filepath.exists() else ""
                filepath.parent.mkdir(parents=True, exist_ok=True)
                filepath.write_text(content)
                new_text = filepath.read_text()
                old_lines = old_text.splitlines()
                new_lines = new_text.splitlines()
                diff_lines = list(difflib.unified_diff(old_lines, new_lines, fromfile=str(filepath) + " (before)", tofile=str(filepath) + " (after)", lineterm=""))
                added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
                removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
                diff_preview = "\n".join(diff_lines[:20])
                if activity_id:
                    update_activity_event(
                        activity_id,
                        status="success",
                        details={
                            "operation": "write",
                            "path": str(filepath),
                            "bytes_written": len(content),
                            "lines_added": added,
                            "lines_removed": removed,
                            "diff_preview": diff_preview,
                        },
                    )
                return success(data={
                    "action": "write",
                    "filepath": str(filepath),
                    "bytes_written": len(content),
                    "lines_added": max(0, len(new_lines) - len(old_lines)),
                    "lines_removed": max(0, len(old_lines) - len(new_lines)),
                    "status": "File written successfully"
                })
            except Exception as e:
                if activity_id:
                    update_activity_event(activity_id, status="failed", message=str(e))
                return error(f"Could not write file: {str(e)}")
        
        elif action == "append":
            if not filepath or content is None:
                return error("append action requires 'filepath' and 'content'")
            
            activity_id = emit_activity_event(
                event_type="file_operation",
                title="Appending file",
                message=str(filepath),
                status="running",
                details={"operation": "append", "path": str(filepath)},
            )
            try:
                old_text = filepath.read_text() if filepath.exists() else ""
                if filepath.exists():
                    filepath.write_text(old_text + '\n' + content)
                else:
                    filepath.write_text(content)
                new_text = filepath.read_text()
                old_lines = old_text.splitlines()
                new_lines = new_text.splitlines()
                diff_lines = list(difflib.unified_diff(old_lines, new_lines, fromfile=str(filepath) + " (before)", tofile=str(filepath) + " (after)", lineterm=""))
                added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
                removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
                diff_preview = "\n".join(diff_lines[:20])
                if activity_id:
                    update_activity_event(
                        activity_id,
                        status="success",
                        details={
                            "operation": "append",
                            "path": str(filepath),
                            "bytes_added": len(content),
                            "lines_added": added,
                            "lines_removed": removed,
                            "diff_preview": diff_preview,
                        },
                    )
                return success(data={
                    "action": "append",
                    "filepath": str(filepath),
                    "bytes_added": len(content),
                    "lines_added": added,
                })
            except Exception as e:
                if activity_id:
                    update_activity_event(activity_id, status="failed", message=str(e))
                return error(f"Could not append to file: {str(e)}")
        
        elif action == "replace":
            if not filepath or not find or replace is None:
                return error("replace action requires 'filepath', 'find', and 'replace'")
            
            activity_id = emit_activity_event(
                event_type="file_operation",
                title="Editing file",
                message=str(filepath),
                status="running",
                details={"operation": "replace", "path": str(filepath)},
            )
            try:
                text = filepath.read_text()
                new_text = text.replace(find, replace)
                filepath.write_text(new_text)
                
                changes = text.count(find)
                old_lines = text.splitlines()
                new_lines = new_text.splitlines()
                diff_lines = list(difflib.unified_diff(old_lines, new_lines, fromfile=str(filepath) + " (before)", tofile=str(filepath) + " (after)", lineterm=""))
                added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
                removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
                diff_preview = "\n".join(diff_lines[:20])
                if activity_id:
                    update_activity_event(
                        activity_id,
                        status="success",
                        details={
                            "operation": "replace",
                            "path": str(filepath),
                            "replacements": changes,
                            "lines_added": added,
                            "lines_removed": removed,
                            "diff_preview": diff_preview,
                        },
                    )
                return success(data={
                    "action": "replace",
                    "filepath": str(filepath),
                    "replacements": changes,
                    "lines_added": max(0, len(new_lines) - len(old_lines)),
                    "lines_removed": max(0, len(old_lines) - len(new_lines)),
                    "status": f"Replaced {changes} occurrence(s)"
                })
            except Exception as e:
                if activity_id:
                    update_activity_event(activity_id, status="failed", message=str(e))
                return error(f"Could not replace in file: {str(e)}")
        
        elif action == "delete":
            if not filepath:
                return error("delete action requires 'filepath'")
            
            activity_id = emit_activity_event(
                event_type="file_operation",
                title="Deleting file",
                message=str(filepath),
                status="running",
                details={"operation": "delete", "path": str(filepath)},
            )
            try:
                if filepath.exists():
                    filepath.unlink()
                    if activity_id:
                        update_activity_event(activity_id, status="success", details={"operation": "delete", "path": str(filepath)})
                    return success(data={
                        "action": "delete",
                        "filepath": str(filepath),
                        "status": "File deleted"
                    })
                else:
                    return warning(data={}, message=f"File not found: {filepath}")
            except Exception as e:
                if activity_id:
                    update_activity_event(activity_id, status="failed", message=str(e))
                return error(f"Could not delete file: {str(e)}")
        
        elif action == "create":
            if not filepath:
                return error("create action requires 'filepath'")
            
            activity_id = emit_activity_event(
                event_type="file_operation",
                title="Creating file",
                message=str(filepath),
                status="running",
                details={"operation": "create", "path": str(filepath)},
            )
            try:
                filepath.parent.mkdir(parents=True, exist_ok=True)
                filepath.touch()
                if activity_id:
                    update_activity_event(activity_id, status="success", details={"operation": "create", "path": str(filepath)})
                return success(data={
                    "action": "create",
                    "filepath": str(filepath),
                    "status": "File created"
                })
            except Exception as e:
                if activity_id:
                    update_activity_event(activity_id, status="failed", message=str(e))
                return error(f"Could not create file: {str(e)}")
        
        elif action == "list":
            if not filepath:
                filepath = Path(".")
            
            try:
                if filepath.is_dir():
                    entries = sorted([str(p.relative_to(filepath)) for p in filepath.iterdir()])[:50]
                    return success(data={
                        "action": "list",
                        "directory": str(filepath),
                        "entries": entries,
                        "count": len(entries)
                    })
                else:
                    return error(f"Not a directory: {filepath}")
            except Exception as e:
                return error(f"Could not list directory: {str(e)}")
        
        else:
            return warning(data={}, message=f"Action '{action}' not yet implemented")
    
    except Exception as e:
        return error(f"File editor error: {str(e)}")
