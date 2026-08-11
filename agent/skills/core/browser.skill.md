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
    Empty or irrelevant results → rephrase the query and search again

  RULES
    - Use search first — only use open or scrape when you already have the right URL
    - Never dump raw HTML or full page content at the user
    - Extract only what directly answers the user's question
    - Use browser last — always try local tools first

priority: 0.95
core: true
version: "3.0"
---
# Browser Search
search → read → open if needed → summarize. Local tools first. Never dump raw HTML.