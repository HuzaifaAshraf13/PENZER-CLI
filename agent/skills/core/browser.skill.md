---
skill_id: core.browser
name: Browser Search
description: Search the web, open URLs, and scrape content for current information
keywords: [browser, search, web, google, url, scrape, find, lookup, online, internet, latest, news, current]
mcp_tools: [browser]
agent_behavior: |
  ACTION REFERENCE
    find info by topic         → browser · search · query
    open a known URL           → browser · open   · url
    extract content from page  → browser · scrape · url
  WHEN TO USE
    - User asks for current or latest information
    - User asks to search for something
    - User provides a URL to open or scrape
    - Local tools (terminal · file_editor · memory) can't answer the question
  SEARCH SEQUENCE
    search · query → read results → open best URL if more detail needed → summarize
    Empty results after retry → rephrase the query once, don't repeat the same query verbatim
    Still empty after rephrase → treat as "no results," don't keep looping
  ERROR HANDLING (search backend is a single-engine HTML scrape — treat failures accordingly)
    "unreachable"   → network/connectivity issue, not a bad query — don't retry immediately, report it
    "parse_error"   → search page structure changed or a block page was served —
                        don't retry the same query rapidly; if it recurs across different
                        queries in the same session, stop searching and tell the user
                        the search backend needs attention, don't keep silently failing
    "No results found" (after internal retry) → genuinely try a different phrasing, or
                        fall back to opening a known/likely URL directly if one is obvious
                        from context (e.g. official docs, a named site)
  RATE AWARENESS
    - This backend has no official API and can be soft-blocked under heavy request volume
    - Avoid firing many searches back-to-back for one task — batch reasoning between searches,
      don't search on every minor sub-question
    - Prefer one well-formed query over several narrow ones when possible
  RULES
    - Use search first — only use open or scrape when you already have the right URL
    - Never dump raw HTML or full page content at the user
    - Extract only what directly answers the user's question
    - Use browser last — always try local tools first
    - Search results are cached briefly — re-searching the exact same query right after won't hit the network again
    - Single-engine results — if something seems clearly wrong or outdated, cross-check
      by opening the source URL directly rather than trusting the snippet alone
priority: 0.95
core: true
version: "3.2"
---
# Browser Search
search → read → open if needed → summarize. Single-engine HTML backend — batch queries, don't hammer, cross-check via open when unsure. Local tools first. Never dump raw HTML.