---
skill_id: core.browser
name: Browser Search & Web Intelligence
description: Search the web, extract content, summarize information, and gather intelligence from online sources
keywords: [browser, search, web, duckduckgo, url, scrape, find, lookup, online, internet, latest, news, current, wikipedia, research, fetch, extract, summarize, intelligence]
mcp_tools: [browser]
agent_behavior: |

  ─── ACTION REFERENCE ──────────────────────────────────────────────────────────

  find info by topic              → browser · search    · query
  open a known URL                → browser · open      · url
  extract raw HTML                → browser · scrape    · url
  get a short page summary        → browser · summary   · url
  get Wikipedia summary           → browser · wikipedia · query (article title)
  research a topic in depth       → browser · search    · query (then open/summary)

  ─── WHEN TO USE ──────────────────────────────────────────────────────────────

  ✓ User asks for current or latest information
  ✓ User asks to search for something
  ✓ User provides a URL to open, summarize, or scrape
  ✓ User asks for research on a topic
  ✓ Local tools (terminal · file_editor · memory) can't answer the question
  ✓ User specifically asks for a Wikipedia article
  ✓ User asks for news, updates, or real-time information

  ✗ Don't use for static/local knowledge (use memory or file_editor)
  ✗ Don't use for code generation (use code tools)

  ─── SEARCH SEQUENCE ───────────────────────────────────────────────────────────

  1. SEARCH → browser · search · query
     → Returns top results with snippets and summary of the best result

  2. EVALUATE → Check if results directly answer the user's question
     → If yes: SUMMARIZE and respond
     → If no: REPHRASE query and search again

  3. DEEP DIVE → If more detail needed:
     → summary · url → Get quick summary (preferred for fast answers)
     → open · url → Get detailed readable content
     → scrape · url → Only if you need to parse something specific

  4. RESEARCH → For complex topics:
     → Search multiple angles
     → Open multiple relevant sources
     → Synthesize information from different perspectives

  ─── RULES ──────────────────────────────────────────────────────────────────────

  ✅ DO:
    - Extract only what directly answers the user's question
    - Summarize and respond directly in the conversation
    - Synthesize information from multiple sources when needed
    - Credit sources when citing specific information
    - Ask clarifying questions if search results are ambiguous
    - Use `summary` over `open` for quick answers (saves time and tokens)
    - Verify information from multiple sources for important facts

  ❌ DON'T:
    - Create .txt files or save content to disk
    - Dump raw HTML or full page content
    - Copy-paste entire articles
    - Ignore irrelevant results - rephrase and search again
    - Use browser when local tools can answer
    - Trust a single source for critical information

  ─── OPTIMIZATION TIPS ─────────────────────────────────────────────────────────

  Search Queries:
    • Be specific: "Python 3.12 async features" > "Python"
    • Include context: "climate change effects on agriculture 2026"
    • Use quotes for exact phrases: "machine learning applications"

  Content Extraction:
    • For articles: use `summary` to get the main point fast
    • For documentation: use `open` to get detailed explanations
    • For data/statistics: use `scrape` to parse specific elements

  Time Management:
    • Set timeout=10 for quick checks
    • Set timeout=20 for important/detailed research
    • Use render_js=True only when pages don't load properly

  ─── RESPONSE FORMAT ──────────────────────────────────────────────────────────

  When responding from browser results:

  1. Start with a clear answer to the user's question
  2. Include key facts/summary (2-3 sentences)
  3. Add supporting details if relevant
  4. Cite sources (e.g., "according to [source]")
  5. Ask if they want more details

  Example:
  "Based on the search results, [direct answer]. [Source] reports that [key fact].
  Would you like more details about [specific aspect]?"

  ─── ADVANCED USAGE ──────────────────────────────────────────────────────────

  Multi-Source Research:
  1. Search topic → Get top 3 results
  2. Open/summary each → Extract different perspectives
  3. Synthesize → Combine into a coherent answer
  4. Cite differences → Note any conflicting information

  Fact-Checking:
  1. Search claim → Get results
  2. Compare multiple sources → Verify consistency
  3. Check Wikipedia → For established facts
  4. Flag uncertainty → If sources disagree, mention this

  News/Current Events:
  1. Search with date → "latest AI news 2026-08-11"
  2. Open most recent sources → Check timestamps
  3. Summarize key developments → Focus on what changed
  4. Highlight implications → Why it matters

priority: 0.95
core: true
version: "4.0"
---
# Browser Search & Web Intelligence
Search → Evaluate → Summarize → Respond. Research multiple sources when needed. Never save files. Always cite sources.