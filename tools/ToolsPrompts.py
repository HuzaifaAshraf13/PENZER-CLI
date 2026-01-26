# ToolsPrompts.py
# Unified, strict, tool‑specific instruction set for Penzer MCP tools.
# These prompts teach the LLM EXACTLY how each tool should be triggered.

# ------------------------------------------------------------
# NMAP — Network Scanning
# ------------------------------------------------------------
NMAP_SCAN_PROMPT = """
Tool: nmap_scan

Intent:
  Use this tool for any network scanning or discovery task.
  This includes port scanning, host discovery, service enumeration,
  OS detection, or ping sweeps.

When to Call:
  - Call nmap_scan whenever the user intent involves:
    "scan", "nmap", "discover hosts", "ping sweep",
    "enumerate ports", "check open ports", "identify services".
  - Do NOT perform scanning logic yourself.

Arguments:
  - target (required):
      Use the exact target string provided by the user.
      This may be a single host, IP, domain, or subnet.
  - args (optional):
      Only include flags explicitly requested by the user
      (e.g. "-sV", "-p-").
      If the user does not mention flags, pass an empty string.
      Never invent or assume flags.

Rules:
  - The agent decides *what* to scan.
  - The tool executes and parses the scan.
  - Do not add validation, authorization, or safety checks.
  - Do not modify the target or arguments.

Output:
  - Return only the tool call with arguments.
  - Do not summarize, explain, or interpret scan results.
"""

# tools/ToolsPrompts.py

# ---------------- TOOL PROMPTS TEMPLATE ----------------

MEM_LOG_FINDING_PROMPT = """
You have access to a tool called `mem_log_finding`:

- Purpose: Log a discovery or finding from the pentest session.
- Arguments:
    - workspace_id (str): The workspace ID of the current session.
    - finding (str): The text describing the discovery.
    - severity (str, optional): One of 'info', 'low', 'medium', 'high', 'critical'. Default is 'info'.
- Returns: Confirmation that the finding was logged.
- Notes: Use this tool to keep a permanent record of findings in long-term memory.
"""


# ------------------------------------------------------------
# METASPLOIT — Non-interactive Command Execution
# ------------------------------------------------------------
RUN_MSFCONSOLE_COMMAND_PROMPT = """
Tool: run_msfconsole

Intent:
  Use this tool to execute Metasploit (msfconsole) commands in a scripted,
  non-interactive manner.

When to Call:
  - Call this tool only when the user explicitly requests Metasploit / MSF actions.
  - Examples include:
      - running auxiliary scanners
      - executing exploit or post modules
      - checking vulnerabilities via Metasploit
      - fingerprinting services using MSF modules

Command Construction Rules:
  - Convert the user request into a list of msfconsole commands.
  - Use only commands, modules, and parameters explicitly mentioned by the user.
  - Do NOT guess module paths, options, payloads, or targets.
  - Do NOT invent missing values or infer defaults.
  - Preserve the user’s intent exactly.

Arguments:
  - commands (required):
      Ordered list of msfconsole commands to execute.
  - All other fields must be omitted unless explicitly provided by the user.

Rules:
  - The agent translates intent → commands.
  - The tool executes commands as-is.
  - Do not perform validation, safety checks, or optimization.
  - Do not explain Metasploit behavior or results.

Output:
  - Return only the tool call with arguments.
  - Do not summarize or interpret the results.
"""



# ------------------------------------------------------------
# GITHUB SEARCH — Repository Code Search
# ------------------------------------------------------------
SEARCH_GITHUB_TOOL_PROMPT = """
Tool: search_github_repository

Intent:
  Use this tool to search code within a specific public GitHub repository.

When to Call:
  - Call this tool only when the user explicitly specifies:
      - a repository owner AND
      - a repository name
  - Examples:
      - "search owner/repo for X"
      - "find X in owner/repo"
      - "look for secrets in owner/repo"

Query Rules:
  - Extract the search query exactly as stated by the user.
  - Do NOT expand, rewrite, or infer additional keywords.
  - Do NOT search outside the specified repository.

Arguments:
  - owner (required):
      Repository owner provided by the user.
  - repo (required):
      Repository name provided by the user.
  - query (required):
      Exact keyword or pattern provided by the user.

Rules:
  - The agent performs intent → argument extraction only.
  - The tool executes the search and normalizes results.
  - Do not add filters, ranking logic, or assumptions.

Output:
  - Return only the tool call with arguments.
  - Do not summarize or interpret search results.
"""


# ------------------------------------------------------------
# EXPLOIT-DB SEARCH — Find Public Exploits
# ------------------------------------------------------------
SEARCH_EXPLOIT_DB_TOOL_PROMPT = """
Tool: search_exploit_db

Intent:
  Use this tool to search Exploit‑DB for publicly known exploits or CVE entries.

When to Call:
  - Call this tool only when the user intent explicitly involves:
      - exploits
      - vulnerabilities
      - CVEs
      - Exploit‑DB
  - Do NOT call for general security questions or mitigations.

Query Rules:
  - Extract the search query exactly as stated by the user.
  - Do NOT expand, rewrite, normalize, or infer keywords.
  - Do NOT add version numbers or service names unless explicitly provided.

Arguments:
  - query (required):
      Exact query string provided by the user.
  - platform (optional):
      Include only if the user explicitly mentions a platform or OS.

Rules:
  - The agent performs intent → argument extraction only.
  - The tool performs the search and parses results.
  - Do not rank, filter, or assess exploit severity.

Output:
  - Return only the tool call with arguments.
  - Do not summarize or interpret exploit results.
"""

