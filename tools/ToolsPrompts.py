# tools/ToolsPrompts.py
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
