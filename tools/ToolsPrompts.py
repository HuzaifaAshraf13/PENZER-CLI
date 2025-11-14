# tools/ToolsPrompts.py

# --- Existing Prompts ---

NMAP_SCAN_PROMPT = """
Run a controlled, authorized nmap scan against an allowed target.

Purpose:
  - Perform discovery/inspection only for systems explicitly authorized for testing.
  - Return parsed/stdout results for diagnostic use (truncated if large).

Inputs (all required unless noted):
  - target: string — IP address or hostname. MUST be present in the server allowlist.
  - args: string (optional) — additional nmap CLI args. Default: "-sV -Pn". Only allow a safe, whitelisted subset (see 'Allowed args').
  - authorization: string — token/proof of permission. The server must validate token, issuer, scope, and expiry before any scan.
  - requester_id: string — user or process identifier for auditing.
  - reason: string — brief justification for the scan (stored in audit log).

Preconditions & validation:
  - Reject if authorization invalid, expired, or missing required scope.
  - Verify target against allowlist (exact match or approved CIDR). Reject otherwise.
  - Validate args against a whitelist (e.g., -sV, -Pn, -p <ports>, --top-ports=<n>, -T<0-4>) and deny dangerous flags (-A, --script, --osscan-guess, --traceroute, --packet-trace, -oN/-oX writing to arbitrary paths).
  - Enforce a maximum ports length and disallow raw shell metacharacters.
  - Enforce rate limits (scans per minute, concurrent scans per requester).

Execution environment:
  - Run in sandboxed environment with no network access except to validated target.
  - Use a dedicated, monitored scan host with immutable logging.
  - Set a maximum runtime/timeout (e.g., 5 minutes) and kill long-running scans.

Safety & ethics:
  - Confirm the authorization explicitly covers the target and the scan types requested.
  - If the token indicates limited scope (e.g., discovery-only), strip disallowed args before execution.
  - Always log requester_id, timestamp, target, args, authorization id, and outcome.

Output & post-processing:
  - Capture stdout and stderr, sanitize to remove any secrets or internal paths.
  - Truncate output over X KB and provide a note with truncation metadata.
  - Provide both raw stdout and a parsed summary (open ports, service/version, scan timestamp).
  - Return structured JSON:
      {
        "status": "success|failed|rejected",
        "reason": "if rejected or failed",
        "target": "...",
        "args": "...",
        "started_at": "...",
        "finished_at": "...",
        "raw_output_truncated": bool,
        "raw_output": "...",
        "summary": { "open_ports": [...], "services": [...] }
      }

Failure modes:
  - If validation fails, return status="rejected" with a clear reason and audit entry.
  - If execution error occurs, return status="failed" with sanitized error details and logs.

Examples:
  - Safe default scan: args="-sV -Pn"
  - Limited port scan: args="-p 22,80,443 -sV -Pn"

NOTE: Use only on systems you are authorized to test. The server must validate authorization before running.
"""

RUN_MSFCONSOLE_COMMAND_PROMPT = """
Execute a scripted, non-interactive msfconsole session in a tightly controlled, audited environment.

Purpose:
  - Allow automation of non-interactive msfconsole tasks that are explicitly authorized by policy.
  - Designed for legitimate red-team / pentest tasks where authorization and scope are proven.

Inputs (required):
  - commands: list[string] — ordered msfconsole commands to run (e.g., ["use auxiliary/scanner/ssh/ssh_version", "set RHOSTS 1.2.3.4", "run"]).
                 Commands must be validated and matched against an allowed-command policy (see below).
  - authorization: string — token/proof of permission. Must be validated for scope (e.g., "msf:run"), target, and expiration.
  - target_list: list[string] — explicit targets; each must be in the server allowlist or explicitly authorized by the token.
  - requester_id: string — identity for audit logs.
  - reason: string — justification stored in audit records.

Preconditions & validation:
  - Validate authorization token: issuer, audience, scopes, expiry, and target permissions.
  - Validate every command in `commands` against a command whitelist/regex. Disallow interactive or escalatory commands (e.g., anything that spawns shells or writes payloads to uncontrolled locations).
  - Validate that all RHOST or RHOSTS values are in `target_list` and appear on allowlist.
  - Enforce maximum command count and overall script runtime.
  - Disallow or sandbox destructive modules or exploit modules unless explicit high-privilege approval exists (approval must be recorded).

Allowed-command policy (example):
  - Allow: auxiliary scanners, safe enumeration, credential checks with explicit consent, sessionless modules.
  - Deny: commands that generate payloads to arbitrary IPs, upload arbitrary files, spawn interactive shells, pivoting, or privilege escalation modules without explicit recorded approval.

Execution environment:
  - Run in an isolated, monitored container with strict egress rules limited to authorized targets.
  - Enable detailed command tracing, immutable audit logs, and session recording.
  - Limit process privileges; never run as root unless absolutely required and approved.

Output & return format:
  - Capture stdout/stderr, sanitize sensitive data (credentials, tokens, internal hostnames).
  - Provide structured JSON including per-command results, start/stop timestamps, truncated output flags, and an overall status.
  - Example:
      {
        "status": "success|failed|rejected",
        "reason": "...",
        "commands_run": [...],
        "per_command": [
          {"command": "...", "status": "...", "output_truncated": bool, "output_snippet": "..."}
        ],
        "started_at": "...",
        "finished_at": "..."
      }

Logging & auditing:
  - Record requester_id, authorization id, full commands run (redact secrets), timestamps, container id, and target list.
  - Emit logs to a centralized SIEM and keep for retention policy (e.g., 1 year).

Failure & safety behavior:
  - If validation fails, return status="rejected" with clear reason.
  - If a command is disallowed, do not execute remaining commands; return partial results and an audit entry.
  - If runtime or resource limits breach, terminate the session and record the termination reason.

Human-in-the-loop & approvals:
  - For high-risk commands (exploit/privilege modules), require an out-of-band approvals token or an elevated authorization scope; store the approver identity.

NOTE: msfconsole execution is powerful. Ensure strict access controls, explicit authorization, and thorough logging. Use only on systems you are authorized to test.
"""

# --- New Data Source Prompts ---

# For the GitHub Resource (git://security-policy)
# This prompt instructs the LLM on how to interpret and use the content from the Resource.
GITHUB_SECURITY_POLICY_RESOURCE_PROMPT = """
Resource URI: resource://git://security-policy
Content Type: Plain Text (Markdown)

Purpose:
  - Provides the current, authorized SECURITY.md content for internal policy reference.
  - Gives the LLM contextual information about allowed security testing scope, reporting procedures, and responsible disclosure guidelines for the organization.

Usage:
  - **Reference this content directly** if the user asks about the organization's security policy, allowed vulnerability disclosure channels, or scope of authorized testing.
  - **Do NOT** assume this document grants permission to run tests (use authorization tokens for tools).
  - Summarize the relevant sections clearly in your response, quoting key text when necessary.

Data Validation & Safety:
  - The resource content is considered read-only and authoritative for policy.
  - Do not attempt to modify or write to this resource.
"""

# For the Exploit DB Tool (search_exploit_db)
# This prompt instructs the LLM on when and how to call the search tool.
SEARCH_EXPLOIT_DB_TOOL_PROMPT = """
Tool Name: search_exploit_db
Function: Searches the Exploit Database for exploits matching a query and platform.

Purpose:
  - Find publicly known vulnerabilities (CVEs) and associated exploits.
  - Use results to cross-reference Nmap findings (service versions) or target analysis.

Inputs (required):
  - query: string — The primary search term (e.g., "Apache Struts 2.3", "Wordpress", or a specific CVE ID like "CVE-2023-xxxx").
  - platform: string (optional) — Filter by platform (e.g., "windows", "linux", "webapps", "hardware").

Preconditions for Invocation:
  - **Only call this tool** when the user or internal logic requires information about a *known* vulnerability or exploit potential.
  - **DO NOT** call this tool if the intent is to run the exploit; only use it for *information gathering*.
  - You must have a clear **target service or software version** to provide an effective query.

Output & Post-processing:
  - The tool returns a list of dictionaries (JSON) containing 'id', 'description', and 'cve'.
  - Analyze the results, looking for exploits with a matching platform and high relevance to the current analysis.
  - If results are found, cite the relevant **CVE ID** and **Description** in your advice, but **never** reproduce the exploit code itself.

Example Call:
  - To cross-reference an Nmap result showing 'Microsoft IIS 7.5':
    <tool_name>search_exploit_db</tool_name> <arguments>{"query": "Microsoft IIS 7.5", "platform": "windows"}</arguments>
"""