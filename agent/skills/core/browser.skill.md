---
skill_id: core.browser
name: Browser Search
description: Search the web, open URLs, and scrape content for current information
keywords: [browser, search, web, duckduckgo, url, scrape, find, lookup, online, internet, latest, news, current, wikipedia]
mcp_tools: [browser]
agent_behavior: |

  ACTION REFERENCE
    find info by topic         → browser · search    · query
    open a known URL           → browser · open      · url
    extract raw HTML from page → browser · scrape    · url
    get a short summary of a page → browser · summary · url
    get a Wikipedia summary    → browser · wikipedia · query (article title)

  WHEN TO USE
    - User asks for current or latest information
    - User asks to search for something
    - User provides a URL to open, summarise, or scrape
    - Local tools (terminal · file_editor · memory) can't answer the question
    - The user specifically asks for a Wikipedia article

  SEARCH SEQUENCE
    1. search · query → returns top results with snippets and a summary of the best result
    2. If more detail is needed, open the most relevant result:
       - open · url → returns clean, readable text (main content)
       - summary · url → returns a short extract (ideal for quick answers)
       - scrape · url → returns raw HTML (only if you need to parse something specific)
    3. If search results are empty or irrelevant, rephrase the query and try again.

  OPTIMISATION
    - For known Wikipedia articles, use `wikipedia` directly to get a summary without searching.
    - For JavaScript-heavy pages, the tool can optionally use a headless browser; this is automatic when needed.

  RULES
    - Use search first — only use open, summary, or scrape when you already have the right URL.
    - Never dump raw HTML or full page content at the user. Always summarise or extract the relevant parts.
    - Extract only what directly answers the user's question.
    - Use browser last — always try local tools first (terminal, file_editor, memory, etc.).
    - When summarising, keep it concise; prefer the `summary` action over `open` for quick answers.
    - If you use `scrape`, parse the HTML yourself and present the findings, not the raw code.

priority: 0.95
core: true
version: "3.1"
---
# Browser Search
search → read → open/summary if needed → answer concisely. Local tools first. Never dump raw HTML.