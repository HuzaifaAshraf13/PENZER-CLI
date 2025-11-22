# ToolsPrompts.py
# Unified, strict, tool‑specific instruction set for Penzer MCP tools.
# These prompts teach the LLM EXACTLY how each tool should be triggered.

# ------------------------------------------------------------
# NMAP — Network Scanning
# ------------------------------------------------------------
NMAP_SCAN_PROMPT = """
Tool: nmap_scan
Purpose:
  Perform host discovery, port scanning, or service enumeration on a user-specified target.
  Any user request involving "scan", "ping sweep", "discover devices", "enumerate ports",
  or "check services" should map directly to this tool.

Routing Rules:
  - Always call nmap_scan when user intent is scanning or discovery.
  - Use the exact target string the user provides, even if it's a subnet.
  - Accept optional 'args' only if the user explicitly mentions flags.
  - Never fabricate flags. If none given, leave args empty.
  - Do not apply allowlist, authorization, or validation logic yourself — MCP handles that.

Arguments:
  - target: string (required)
  - args: string (optional; default empty)

Output Expectation:
  - The agent only returns the tool call JSON; the tool returns actual scan output.
"""


# ------------------------------------------------------------
# METASPLOIT — Non-interactive Command Execution
# ------------------------------------------------------------
RUN_MSFCONSOLE_COMMAND_PROMPT = """
Tool: run_msfconsole
Purpose:
  Execute a sequence of Metasploit commands in non-interactive, scripted form.
  Use this tool for any user request asking to:
    - run Metasploit modules
    - automate auxiliary scanners
    - execute exploit modules in a scripted workflow
    - check vulnerabilities with Metasploit
    - fingerprint services using MSF

Routing Rules:
  - Only call when the user explicitly asks for Metasploit/MSF-related actions.
  - Convert the user request into a list of msfconsole commands EXACTLY as stated.
  - Do not guess module names or create missing parameters.
  - The caller provides required target(s) if needed; do not invent.

Arguments:
  - commands: list[string] (required)
  - authorization: string (optional; leave empty unless user provides)
  - target_list: list[string] (optional; only populate if explicitly given)
  - requester_id: string (optional; leave empty unless user gives)
  - reason: string (optional; user intent in simple words)

Output Expectation:
  - Tool will return structured per-command execution results.
"""


# ------------------------------------------------------------
# GITHUB SEARCH — Repository Code Search
# ------------------------------------------------------------
SEARCH_GITHUB_TOOL_PROMPT = """
Tool: search_github_repository
Purpose:
  Search for code, files, secrets, or patterns inside a PUBLIC GitHub repository.
  Use this tool when user requests:
    - "search this repo"
    - "find code for X in owner/repo"
    - "look up secrets/api keys/files in a GitHub repo"
    - "search GitHub for keyword inside a repo"

Routing Rules:
  - Trigger only when user SPECIFIES a repo owner + repo name.
  - Do NOT guess the repo name.
  - Extract 'query' EXACTLY as the user says.

Arguments:
  - owner: string (required)
  - repo: string (required)
  - query: string (required)

Output Expectation:
  - Tool will return list of matches with path + URL.
"""


# ------------------------------------------------------------
# EXPLOIT-DB SEARCH — Find Public Exploits
# ------------------------------------------------------------
SEARCH_EXPLOIT_DB_TOOL_PROMPT = """
Tool: search_exploit_db
Purpose:
  Search the Exploit‑DB index for exploits or CVEs that match a query.
  Use this tool when user asks:
    - "search CVE ..."
    - "find exploit for ..."
    - "exploit-db query ..."
    - "find exploit for version/service"

Routing Rules:
  - Map only when user intent clearly refers to vulnerabilities, CVEs, or exploits.
  - Extract query EXACTLY as given.
  - Platform is optional; include only if user explicitly states one.

Arguments:
  - query: string (required)
  - platform: string (optional)

Output Expectation:
  - Tool returns structured exploit entries (id, description, cve, platform).
"""
