# tools/file_editor_tool.py
"""
File Editor Tool: Read, write, edit, delete, and manage files.
"""

from pathlib import Path

from agent.core import mcp
from tools.standards import success, error, warning


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
        
        if action == "read":
            if not filepath:
                return error("read action requires 'filepath'")
            
            if not filepath.exists():
                return error(f"File not found: {filepath}")
            
            try:
                text = filepath.read_text()
                
                # Handle line ranges
                if line_start or line_end:
                    lines = text.split('\n')
                    start = (line_start or 1) - 1
                    end = (line_end or len(lines))
                    text = '\n'.join(lines[start:end])
                
                return success(data={
                    "action": "read",
                    "filepath": str(filepath),
                    "size_bytes": len(text),
                    "lines": len(text.split('\n')),
                    "content": text[:10000]  # First 10KB
                })
            except Exception as e:
                return error(f"Could not read file: {str(e)}")
        
        elif action == "write":
            if not filepath or content is None:
                return error("write action requires 'filepath' and 'content'")
            
            try:
                filepath.parent.mkdir(parents=True, exist_ok=True)
                filepath.write_text(content)
                return success(data={
                    "action": "write",
                    "filepath": str(filepath),
                    "bytes_written": len(content),
                    "status": "File written successfully"
                })
            except Exception as e:
                return error(f"Could not write file: {str(e)}")
        
        elif action == "append":
            if not filepath or content is None:
                return error("append action requires 'filepath' and 'content'")
            
            try:
                if filepath.exists():
                    current = filepath.read_text()
                    filepath.write_text(current + '\n' + content)
                else:
                    filepath.write_text(content)
                
                return success(data={
                    "action": "append",
                    "filepath": str(filepath),
                    "bytes_added": len(content)
                })
            except Exception as e:
                return error(f"Could not append to file: {str(e)}")
        
        elif action == "replace":
            if not filepath or not find or replace is None:
                return error("replace action requires 'filepath', 'find', and 'replace'")
            
            try:
                text = filepath.read_text()
                new_text = text.replace(find, replace)
                filepath.write_text(new_text)
                
                changes = text.count(find)
                return success(data={
                    "action": "replace",
                    "filepath": str(filepath),
                    "replacements": changes,
                    "status": f"Replaced {changes} occurrence(s)"
                })
            except Exception as e:
                return error(f"Could not replace in file: {str(e)}")
        
        elif action == "delete":
            if not filepath:
                return error("delete action requires 'filepath'")
            
            try:
                if filepath.exists():
                    filepath.unlink()
                    return success(data={
                        "action": "delete",
                        "filepath": str(filepath),
                        "status": "File deleted"
                    })
                else:
                    return warning(data={}, message=f"File not found: {filepath}")
            except Exception as e:
                return error(f"Could not delete file: {str(e)}")
        
        elif action == "create":
            if not filepath:
                return error("create action requires 'filepath'")
            
            try:
                filepath.parent.mkdir(parents=True, exist_ok=True)
                filepath.touch()
                return success(data={
                    "action": "create",
                    "filepath": str(filepath),
                    "status": "File created"
                })
            except Exception as e:
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
