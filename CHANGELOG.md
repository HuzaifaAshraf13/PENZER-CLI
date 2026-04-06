# Changelog

All notable changes to this project will be documented in this file.

## [0.2.0] - April 6, 2026

### Added
- **User-Driven ReAct Loop**: Agent asks "Continue analyzing?" after each iteration instead of forcing iterations
- **Async-Safe User Prompts**: User input runs in executor thread, doesn't block async event loop
- **Fallback Action Generation**: Agent generates safe fallback commands when LLM fails
- **Enhanced Error Handling**: API errors detected and handled gracefully with recovery
- **Adaptive Token Configuration**: Device auto-detection with 4-tier token scaling (512-2048)
- **Improved System Prompts**: Added error handling guidance and explicit requirements
- **Skill Guidance in Prompts**: ACT phase now receives detailed skill guidance from matched skills
- **LLM Error Detection**: Detects API errors (429, timeouts, etc) and continues with fallbacks

### Fixed
- Event loop blocking from synchronous `input()` calls
- LLM API rate limiting causing agent failures (429 errors)
- Dummy "No command generated" placeholder commands causing infinite loops
- Missing error recovery when LLM service fails
- ACT phase not using skill guidance properly
- OBSERVE phase stopping on LLM errors

### Changed
- `_ask_user_continue()` now runs in async executor via `_ask_user_continue_async()`
- ACT phase returns fallback action instead of None on errors
- REASON phase continues on LLM errors with recovery reasoning
- System prompts explicitly tell LLM to always output something (never fail silently)
- Prompt templates include more detailed examples and error handling instructions

### Improved
- Device capability detection for optimal token settings
- Error messages and logging for debugging
- Fallback command mapping based on user request keywords
- Skill matching and guidance building
- Overall agent resilience and fault tolerance

## [0.1.0] - Initial Release

### Features
- ReAct loop (Reason → Act → Observe)
- Skill-guided decision making (10 skills across 5 phases)
- GGUF model support with llama.cpp
- API model support (Google Generative AI, etc)
- Tool orchestration via MCP
- Session management
- Basic CLI interface

---

## Version 0.2.0 Key Improvements

### User Experience
- **No forced iterations**: User controls depth of analysis
- **Interactive control**: "Continue?" prompt after each cycle
- **Progress visibility**: Shows hosts, services, vulns, credentials found
- **Graceful degradation**: Works even when API is rate-limited

### Robustness
- **Error recovery**: Continues with fallback commands on LLM failure
- **Async safety**: Non-blocking user prompts
- **Device optimization**: Tokens scale from 512 to 2048 based on RAM
- **Skill-guided intelligence**: 10 pentesting skills guide reasoning and actions

### Architecture
- **Separated prompts**: System prompts separate from logic for clarity
- **Better guidance**: Skills send detailed tactical instructions to LLM
- **Fallback strategy**: Safe fallback commands keep agent operating
- **Error handling**: All phases catch and recover from errors

---

## Future Plans

### Version 0.3.0
- [ ] Retry logic with exponential backoff for API calls
- [ ] Multi-turn reasoning for complex tasks
- [ ] Persistent findings storage across sessions
- [ ] Custom skill creation interface
- [ ] Integration with more pentest tools

### Version 0.4.0
- [ ] Web UI dashboard
- [ ] Real-time progress visualization
- [ ] Collaborative multi-agent tasks
- [ ] Advanced reporting with graphs/charts

---

## Migration Guide (0.1.0 → 0.2.0)

### Breaking Changes
None - fully backward compatible

### New Behavior
- Agent now prompts for user confirmation after each cycle
- When running scripts, send "no" at prompts to auto-complete

### New Environment Variable
- No new environment variables required

### Database Changes
- No database changes needed

---

## Contributors

- Eric Penzer (Huzaifa Ashraf)
