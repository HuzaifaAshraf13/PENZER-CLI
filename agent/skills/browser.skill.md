---
skill_id: browser_tool_skill
name: Browser & Web Interaction Skill
phase: reconnaissance
description: Web search, URL navigation, content scraping, and form interaction for information gathering
keywords:
  - search
  - web
  - scrape
  - navigate
  - google
  - http
  - content
  - information
mcp_tools:
  - browser
agent_behavior: |
  When user requests web searches, URL navigation, or content extraction:
  1. Use browser tool to search Google for relevant information
  2. Analyze search results and select best matches
  3. Open URLs and scrape page content for intel
  4. Extract actionable information from page structure
  5. Pass findings back for analysis or further action
---

## WHEN TO USE

Use this skill when you need to:
- **Search the web** for information, vulnerability databases, security advisories
- **Navigate URLs** to fetch page content without local tools
- **Scrape content** from websites for data gathering or reconnaissance
- **Gather public intelligence** on targets, CVEs, exploits
- **Find documentation** or configuration examples online
- **Locate security resources** or tool repositories

## ALGORITHM/PROCEDURE

### Web Search Pattern
1. Parse user query for keywords
2. Call `browser(action="search", query=keywords)`
3. Receive up to 5 URLs from Google results
4. Return results or proceed to open URLs

### Content Fetching Pattern
1. Take target URL from search results or user input
2. Call `browser(action="open", url=target_url)`
3. Extract page title and content preview
4. Parse for relevant information
5. Return findings or save to memory

### Scraping Pattern
1. Identify target URL for content extraction
2. Call `browser(action="scrape", url=url)`
3. Remove HTML tags and extract pure text
4. Return cleaned content (up to 5KB)
5. Store important findings in memory

## INTEGRATION

**With Other Skills:**
- **enumeration.skill.md**: Browser searches for target information gathering
- **reporting.skill.md**: Embed web-sourced intelligence in reports
- **memory**: Store search results, URLs, and extracted content for future reference

**MCP Tool Interaction:**
- Calls `browser` tool with action, query, or URL parameters
- Receives JSON responses with success/error status and data
- Data includes search results, page titles, or text content

## LLM OPTIMIZATION

**Prompt Injection:**
```
When searching the web, be specific with queries to get relevant results.
For example: "CVE 2024 Apache vulnerability" is better than "vulnerability".
Prioritize security databases, official documentation, and established resources.
```

**Context Window:**
- Search results: Limited to 5 URLs per query (token efficient)
- Page content: Limited to 5000 chars (fits within context)
- Store large findings in memory tool for later reference

**Example Reasoning:**
```
User: "Find information about CVE-2024-1234"
Semantic activation: Yes (keywords: search, cve, vulnerability)
Skill reasoning: Use browser search to locate CVE details from NVD or security databases
Tool chain: browser(search) → browser(open NVD link) → memory(store) → respond
```
