# tools/ui_tool.py
"""
UI Tool: Desktop GUI interaction (click, type, screenshot, etc).
"""

import subprocess

from agent.core import mcp
from tools.standards import success, error, warning


@mcp.tool()
def ui(action: str, x: int = None, y: int = None, text: str = None, 
        app: str = None, screenshot_path: str = None) -> dict:
    """
    UI automation tool for desktop GUI interaction.
    
    Actions:
        click: Click at coordinates (x, y)
        type: Type text at current cursor position
        screenshot: Take desktop screenshot
        screenshot_save: Save screenshot to file
        get_active_window: Get title of active window
        find_window: Find window by name pattern
        
    Args:
        action: What to do (click, type, screenshot, etc)
        x, y: Coordinates for click action
        text: Text to type (for type action)
        app: Application name (for find_window action)
        screenshot_path: Where to save screenshot
    """
    try:
        if action == "screenshot":
            # Use xdotool/wmctrl or fallback to import-from-ImageMagick
            cmd = "which import && import -window root /tmp/penzer_screenshot.png && echo /tmp/penzer_screenshot.png"
            result = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=5)
            
            if result.returncode != 0:
                return warning(data={}, message="ImageMagick not available. Install: sudo apt-get install imagemagick")
            
            screenshot_file = result.stdout.strip()
            return success(data={
                "action": "screenshot",
                "path": screenshot_file,
                "message": "Screenshot saved"
            })
        
        elif action == "screenshot_save":
            if not screenshot_path:
                screenshot_path = "/tmp/penzer_screenshot.png"
            
            cmd = f"import -window root {screenshot_path} && echo 'Saved' || echo 'Failed'"
            result = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=5)
            
            if "Saved" in result.stdout:
                return success(data={"path": screenshot_path})
            else:
                return error("Could not take screenshot - ImageMagick required")
        
        elif action == "click":
            if x is None or y is None:
                return error("click action requires x, y coordinates")
            
            cmd = f"xdotool mousemove {x} {y} click 1"
            result = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=5)
            
            if result.returncode != 0:
                return warning(data={}, message="xdotool not available. Install: sudo apt-get install xdotool")
            
            return success(data={"action": "click", "x": x, "y": y})
        
        elif action == "type":
            if not text:
                return error("type action requires 'text' parameter")
            
            # Escape special characters
            safe_text = text.replace("'", "'\\''")
            cmd = f"xdotool type '{safe_text}'"
            result = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=5)
            
            if result.returncode != 0:
                return warning(data={}, message="xdotool not available")
            
            return success(data={"action": "type", "text": text})
        
        elif action == "get_active_window":
            cmd = "xdotool getactivewindow getwindowname"
            result = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=5)
            
            if result.returncode != 0:
                return warning(data={}, message="xdotool not available")
            
            return success(data={"active_window": result.stdout.strip()})
        
        else:
            return warning(data={}, message=f"Action '{action}' not yet implemented. Supported: click, type, screenshot, get_active_window")
    
    except Exception as e:
        return error(f"UI error: {str(e)}")
