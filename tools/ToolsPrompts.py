# ToolsPrompts.py

from agent.core import mcp
# ------------------------------------------------------------
# NMAP — Network Scanning
# ------------------------------------------------------------
@mcp.prompt(
    name="nmap_scan_rules",
    description="Rules for using the nmap_scan tool"
)
def nmap_scan_prompt():
    return [
        {
            "role": "system",
            "content": (
                "Use the nmap_scan tool for any network scanning or discovery task.\n"
                "This includes port scanning, host discovery, service enumeration,\n"
                "OS detection, and ping sweeps.\n\n"

                "WHEN TO CALL:\n"
                "- scan, nmap, discover hosts, ping sweep\n"
                "- enumerate ports, check open ports, identify services\n\n"

                "ARGUMENT RULES:\n"
                "- target: use the exact user-provided target\n"
                "- args: include ONLY flags explicitly mentioned by the user\n"
                "- if no flags are mentioned, pass an empty string\n"
                "- NEVER invent or assume flags\n\n"

                "RULES:\n"
                "- Do NOT perform scanning logic yourself\n"
                "- Do NOT modify the target or arguments\n"
                "- Do NOT add validation, authorization, or safety checks\n\n"

                "OUTPUT:\n"
                "- Return ONLY the tool call with arguments\n"
                "- Do NOT summarize, explain, or interpret results"
            )
        }
    ]


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
from agent.core import mcp

# ------------------------------------------------------------
# METASPLOIT — Non-interactive Command Execution
# ------------------------------------------------------------
@mcp.prompt(
    name="run_msfconsole_rules",
    description="Rules for executing Metasploit commands using run_msfconsole"
)
def run_msfconsole_prompt():
    return [
        {
            "role": "system",
            "content": (
                "Use the run_msfconsole tool to execute Metasploit (msfconsole)\n"
                "commands in a scripted, non-interactive manner.\n\n"

                "WHEN TO CALL:\n"
                "- ONLY when the user explicitly requests Metasploit or MSF actions\n"
                "- Examples: auxiliary scanners, exploit modules, post modules,\n"
                "  vulnerability checks, or service fingerprinting via MSF\n\n"

                "COMMAND CONSTRUCTION RULES:\n"
                "- Convert the user request into an ordered list of msfconsole commands\n"
                "- Use ONLY commands, modules, and parameters explicitly mentioned\n"
                "- Do NOT guess module paths, payloads, options, or targets\n"
                "- Do NOT invent values or infer defaults\n"
                "- Preserve the user’s intent exactly\n\n"

                "ARGUMENTS:\n"
                "- commands: required, ordered list of msfconsole commands\n"
                "- Omit all other fields unless explicitly provided\n\n"

                "RULES:\n"
                "- Do NOT perform validation, safety checks, or optimization\n"
                "- Do NOT explain Metasploit behavior or results\n\n"

                "OUTPUT:\n"
                "- Return ONLY the tool call with arguments\n"
                "- Do NOT summarize or interpret results"
            )
        }
    ]



# ------------------------------------------------------------
# GITHUB SEARCH — Repository Code Search
# ------------------------------------------------------------
from agent.core import mcp

# ------------------------------------------------------------
# GITHUB — Repository Code Search
# ------------------------------------------------------------
@mcp.prompt(
    name="search_github_repository_rules",
    description="Rules for searching code in a specific GitHub repository"
)
def search_github_repository_prompt():
    return [
        {
            "role": "system",
            "content": (
                "Use the search_github_repository tool to search code within\n"
                "a specific public GitHub repository.\n\n"

                "WHEN TO CALL:\n"
                "- ONLY when the user explicitly specifies BOTH:\n"
                "  - a repository owner\n"
                "  - a repository name\n"
                "- Examples: search owner/repo for X, find X in owner/repo,\n"
                "  look for secrets in owner/repo\n\n"

                "QUERY RULES:\n"
                "- Extract the search query EXACTLY as stated by the user\n"
                "- Do NOT expand, rewrite, or infer additional keywords\n"
                "- Do NOT search outside the specified repository\n\n"

                "ARGUMENTS:\n"
                "- owner: repository owner provided by the user\n"
                "- repo: repository name provided by the user\n"
                "- query: exact keyword or pattern provided by the user\n\n"

                "RULES:\n"
                "- Perform intent → argument extraction ONLY\n"
                "- Do NOT add filters, ranking logic, or assumptions\n\n"

                "OUTPUT:\n"
                "- Return ONLY the tool call with arguments\n"
                "- Do NOT summarize or interpret results"
            )
        }
    ]

# ------------------------------------------------------------
# EXPLOIT-DB SEARCH — Find Public Exploits
# ------------------------------------------------------------
from agent.core import mcp

# ------------------------------------------------------------
# EXPLOIT‑DB — Public Exploit Search
# ------------------------------------------------------------
@mcp.prompt(
    name="search_exploit_db_rules",
    description="Rules for searching Exploit‑DB for public exploits or CVEs"
)
def search_exploit_db_prompt():
    return [
        {
            "role": "system",
            "content": (
                "Use the search_exploit_db tool to search Exploit‑DB for\n"
                "publicly known exploits or CVE entries.\n\n"

                "WHEN TO CALL:\n"
                "- ONLY when the user intent explicitly involves:\n"
                "  exploits, vulnerabilities, CVEs, or Exploit‑DB\n"
                "- Do NOT call for general security questions or mitigations\n\n"

                "QUERY RULES:\n"
                "- Extract the search query EXACTLY as stated by the user\n"
                "- Do NOT expand, rewrite, normalize, or infer keywords\n"
                "- Do NOT add version numbers or service names unless explicitly provided\n\n"

                "ARGUMENTS:\n"
                "- query: required, exact query string from the user\n"
                "- platform: optional, ONLY if the user explicitly mentions an OS or platform\n\n"

                "RULES:\n"
                "- Perform intent → argument extraction ONLY\n"
                "- Do NOT rank, filter, or assess exploit severity\n\n"

                "OUTPUT:\n"
                "- Return ONLY the tool call with arguments\n"
                "- Do NOT summarize or interpret results"
            )
        }
    ]

