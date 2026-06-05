skill_id: ui_tool_skill
name: GUI & Desktop Automation Skill
description: Desktop GUI interaction, screenshot capture, input simulation, window management for any application
keywords:
  - gui
  - click
  - type
  - screenshot
  - desktop
  - window
  - automation
  - interact
  - application
mcp_tools:
  - ui
agent_behavior: |
  When user needs GUI interaction or desktop automation:
  1. Capture current desktop state with screenshot
  2. Analyze interface elements and locate targets
  3. Perform clicks at specific coordinates
  4. Type text input into fields
  5. Navigate windows and manage focus
  6. Verify actions via subsequent screenshots
  7. Report back on success/failure with visual confirmation
---
## WHEN TO USE
Use this skill when you need to:
- **Automate interactions** with any desktop application
- **Click buttons, links, or UI elements** in any application
- **Type text** into input fields, forms, or text areas
- **Capture screenshots** for verification or documentation
- **Navigate menus, dialogs, or windows** programmatically
- **Switch between applications** and manage window focus
- **Simulate user interactions** for testing or automation
- **Interact with GUI tools** that lack command-line interfaces

## ALGORITHM/PROCEDURE

### Screenshot Capture
1. Call `ui(action="screenshot")`
2. Receive file path to captured desktop image
3. Analyze image for target elements
4. Use path for verification or documentation

### Click Interaction
1. Identify target element position (x, y coordinates)
2. Call `ui(action="click", x=X, y=Y)`
3. Wait briefly for application response
4. Capture screenshot to verify click result
5. Report success or failure

### Text Input
1. Ensure cursor focus on target input field
2. Call `ui(action="type", text="input_text")`
3. Verify text appeared via screenshot
4. Proceed with next action if needed

### Window Management
1. Call `ui(action="get_active_window")` to get current focused window
2. Call `ui(action="move_mouse", x=X, y=Y)` to move cursor
3. Call `ui(action="scroll", text="down")` or `text="up"` to scroll
4. Call `ui(action="key_press", text="Return")` for keyboard shortcuts

## INTEGRATION
**MCP Tool Interaction:**
- Calls `ui` tool with appropriate action parameters
- Receives JSON responses with success status and confirmation
- Screenshot paths are returned for image analysis
- All actions are validated before proceeding

## LLM OPTIMIZATION
**Best Practices:**
1. Always take screenshot first to identify correct coordinates
2. Remember coordinate system: (0,0) is top-left, increases right and down
3. Wait between rapid actions to allow application response time
4. Verify each action with follow-up screenshot before proceeding
5. Use keyboard shortcuts (key_press) when available for efficiency

**Context Window:**
- Store screenshot paths only, not raw image data
- Reference paths in memory for later analysis
- Break complex interactions into atomic steps