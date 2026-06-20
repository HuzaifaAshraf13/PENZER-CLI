---
skill_id: core.browser
name: Browser Search
description: Search the web, open URLs, scrape content, find current information
keywords: [browser, search, web, google, url, scrape, find, lookup, online, internet, latest, news, current]
mcp_tools: [browser]
agent_behavior: |
  WHEN TO USE:
  - User asks for current/latest information
  - User asks to search for something
  - User provides a URL to open or scrape
  - Terminal/local tools can't answer the question

  SEARCH STEPS:
  1. Use browser with action "search" and a clear query
  2. Read the results
  3. If more detail needed: use action "open" on the best URL
  4. Summarize findings clearly — no raw HTML dumps

  EXAMPLES:
  Search: {"tool": "browser", "args": {"action": "search", "query": "latest Python version"}}
  Open URL: {"tool": "browser", "args": {"action": "open", "url": "https://python.org"}}
  Scrape: {"tool": "browser", "args": {"action": "scrape", "url": "https://example.com"}}

  AFTER RESULTS:
  - Extract only what's relevant to the user's question
  - Never dump raw HTML or full page content
  - If result is empty or irrelevant: try a different search query
priority: 0.95
core: true
version: "2.0"
---
# Browser Search
Search the web and scrape URLs. Always summarize results cleanly.