---
skill_id: ui_tool_skill
name: GUI & Desktop Interaction Skill
phase: exploitation
description: Desktop GUI automation, screenshot capture, user input simulation, window management
keywords:
  - gui
  - click
  - type
  - screenshot
  - desktop
  - window
  - input
  - interact
  - automation
mcp_tools:
  - ui
agent_behavior: |
  When user needs GUI interaction or desktop automation:
  1. Capture current desktop state with screenshot
  2. Analyze interface elements and locate targets
  3. Perform clicks at specific coordinates
  4. Type text input into fields
  5. Verify actions via subsequent screenshots
  6. Report back on success/failure with visual confirmation
---

## WHEN TO USE

Use this skill when you need to:
- **Automate user interactions** on vulnerable applications or services
- **Click buttons or links** in web browsers or desktop applications
- **Type credentials or payloads** into input fields during exploitation
- **Capture screenshots** for evidence or state verification
- **Navigate menus** or dialog boxes programmatically
- **Interact with GUI tools** that lack command-line interfaces
- **Simulate human-like** interaction patterns for evasion

## ALGORITHM/PROCEDURE

### Screenshot Capture Pattern
1. Call `ui(action="screenshot")`
2. Receive file path to captured desktop image
3. Analyze image for target elements, buttons, input fields
4. Return screenshot path or convert to text description

### Click Interaction Pattern
1. Identify target element position (x, y coordinates)
2. Call `ui(action="click", x=X, y=Y)`
3. Wait briefly for response/action
4. Capture screenshot to verify click result
5. Report on success

### Text Input Pattern
1. Ensure cursor focus on target input field
2. Call `ui(action="type", text="payload_or_credentials")`
3. Verify text appeared in field via screenshot
4. Proceed with form submission if needed

### Window Management Pattern
1. Call `ui(action="get_active_window")`
2. Receive name of currently focused window
3. Use to verify target application is active
4. Switch windows if needed before automation

## INTEGRATION

**With Other Skills:**
- **exploitation.skill.md**: Automate click/type for exploit delivery to GUI apps
- **post_exploitation.skill.md**: GUI automation for privilege escalation or lateral movement
- **reporting.skill.md**: Embed screenshots as evidence in security reports

**MCP Tool Interaction:**
- Calls `ui` tool with action, coordinates, or text parameters
- Receives JSON responses with success/error status and action confirmation
- Screenshot data includes file path for image analysis

## LLM OPTIMIZATION

**Prompt Injection:**
```
GUI automation requires precision:
1. Always take screenshot first to identify correct coordinates
2. Remember coordinate system: (0,0) is top-left, increases right and down
3. Wait between rapid clicks to allow application response time
4. Verify each action with follow-up screenshot before proceeding
```

**Context Window:**
- Screenshots: Store paths only, not raw image data (saves tokens)
- Large findings: Reference screenshot paths in memory for later analysis
- Click sequences: Break into atomic steps for clarity and debugging

**Example Reasoning:**
```
User: "Log into the vulnerable app and click the admin button"
Semantic activation: Yes (keywords: click, type, gui, login)
Skill reasoning: Screenshot → locate login fields → type credentials → take screenshot → locate admin button → click
Tool chain: ui(screenshot) → analyze → ui(type user) → ui(type pass) → ui(screenshot) → ui(click) → report
```
