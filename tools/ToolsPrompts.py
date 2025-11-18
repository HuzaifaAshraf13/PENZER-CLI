# --- Existing Prompts with Example Outputs ---

NMAP_SCAN_PROMPT = """
Run a controlled, authorized nmap scan against an allowed target.

Purpose:
  - Perform discovery/inspection only for systems explicitly authorized for testing.
  - Return parsed/stdout results for diagnostic use (truncated if large).

Inputs:
  - target: string — IP address or hostname. MUST be present in the server allowlist.
  - args: string (optional) — additional nmap CLI args. Default: "-sV -Pn". Only allow a safe, whitelisted subset.
  - authorization: string — token/proof of permission.
  - requester_id: string — user or process identifier.
  - reason: string — justification for the scan.

Preconditions & validation:
  - Reject if authorization invalid, expired, or missing scope.
  - Validate target against allowlist.
  - Validate args against whitelist; deny dangerous flags.

Execution environment:
  - Run in sandboxed environment; no network access except to validated target.
  - Timeout: 5 minutes max.

Output & post-processing:
  - Structured JSON with stdout, stderr, summary of open ports and services.

Example Output:
{
  "status": "success",
  "reason": "",
  "target": "10.0.0.1",
  "args": "-sV -Pn",
  "started_at": "2025-11-18T12:00:00Z",
  "finished_at": "2025-11-18T12:01:00Z",
  "raw_output_truncated": false,
  "raw_output": "<nmap stdout here>",
  "summary": {
      "open_ports": [22, 80],
      "services": ["ssh", "http"]
  }
}
"""

RUN_MSFCONSOLE_COMMAND_PROMPT = """
Execute a scripted, non-interactive msfconsole session in a tightly controlled, audited environment.

Purpose:
  - Automate msfconsole tasks explicitly authorized by policy.

Inputs:
  - commands: list[string] — ordered commands to run.
  - authorization: string — token/proof of permission.
  - target_list: list[string] — explicit authorized targets.
  - requester_id: string — identity for audit logs.
  - reason: string — justification.

Output:
  - Structured JSON per command, overall status, start/stop timestamps.

Example Output:
{
  "status": "success",
  "reason": "",
  "commands_run": ["use auxiliary/scanner/ssh/ssh_version", "set RHOSTS 10.0.0.1", "run"],
  "per_command": [
      {"command": "use auxiliary/scanner/ssh/ssh_version", "status": "success", "output_truncated": false, "output_snippet": "<output>"},
      {"command": "set RHOSTS 10.0.0.1", "status": "success", "output_truncated": false, "output_snippet": "<output>"},
      {"command": "run", "status": "success", "output_truncated": false, "output_snippet": "<output>"}
  ],
  "started_at": "2025-11-18T12:00:00Z",
  "finished_at": "2025-11-18T12:05:00Z"
}
"""

SEARCH_GITHUB_TOOL_PROMPT = """
Tool Name: search_github_repository
Function: Searches for code within a specific GitHub repository.

Purpose:
  - Find files, code snippets, or configuration details within a given repository.
  - Useful for reconnaissance and understanding a target's codebase.

Inputs:
  - owner: string — The owner of the GitHub repository.
  - repo: string — The name of the GitHub repository.
  - query: string — The search term (e.g., "password", "api_key", "config.json").

Preconditions:
  - Requires a valid GITHUB_TOKEN to be set in the environment.
  - Use targeted queries to avoid noisy results.

Output:
  - A list of dictionaries, each containing the path, URL, and search score of a result.

Example Output:
[
  {
    "path": "config/database.yml",
    "url": "https://github.com/owner/repo/blob/main/config/database.yml",
    "score": 1.0
  },
  {
    "path": "src/main/java/com/example/App.java",
    "url": "https://github.com/owner/repo/blob/main/src/main/java/com/example/App.java",
    "score": 0.89
  }
]
"""

SEARCH_EXPLOIT_DB_TOOL_PROMPT = """
Tool Name: search_exploit_db
Function: Searches the Exploit Database for exploits matching a query and platform.

Purpose:
  - Find public CVEs and associated exploits.
  - Cross-reference Nmap service/version results.

Inputs:
  - query: string — primary search term or CVE ID.
  - platform: string (optional) — e.g., "windows", "linux", "webapps".

Preconditions:
  - Only call for known vulnerabilities or service version matching.
  - Never attempt to run exploits; info only.

Output:
  - List of dictionaries with 'id', 'description', 'cve', 'platform', 'author'.

Example Output:
[
  {
      "id": 50000,
      "description": "WordPress Plugin X - SQL Injection",
      "cve": "CVE-2023-1234",
      "platform": "webapps",
      "author": "Exploit-DB"
  },
  {
      "id": 49000,
      "description": "Windows LPE - Service Handle Abuse",
      "cve": "CVE-2022-9999",
      "platform": "windows",
      "author": "Exploit-DB"
  }
]
"""
