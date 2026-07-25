---
skill_id: core.terminal
name: Terminal Executor
description: Run bash commands, scripts, and Python code safely and efficiently
keywords: [terminal, bash, shell, command, execute, run, script, python, timeout, background, job,
           network, networking, connection, connected, wifi, wi-fi, ip, subnet, gateway, dns, mac,
           ports, port, scan, nmap, recon, reconnaissance, security, analyst, hosts, host, interface,
           lan, router, digging, investigate, investigation, hardening, firewall, ss, netstat, lsof,
           processes, memory, cpu, disk, uptime, sysinfo, system]
mcp_tools: [terminal, terminal_check_job, run_bash, run_python]
agent_behavior: |
  STEP 0 — SELF-ASSESS RISK (before picking anything)
    Before calling terminal, silently rate what you're about to run:
      LOW    → read-only, no state change (ls, cat, ps, df, git status)
      MEDIUM → changes local state but is reversible/scoped (write a file,
                install into a project venv, git commit)
      HIGH   → touches system state, is destructive, irreversible, or
                needs elevated privileges (sudo/su, rm -rf, chmod -R,
                package installs via apt/brew, anything net-new and
                unfamiliar)
    This is a judgment call from you, not a lookup — reason about what
    the command actually DOES, not just whether it matches a known bad
    string. A command can be HIGH risk without containing any of the
    literal patterns below (e.g. a long, unfamiliar pipeline you
    generated yourself). When in doubt, round up, not down.
    The executor's own pattern-matching (STEP 3 below, plus a dedicated
    sudo/su/pkexec/doas check) is the enforced backstop — it runs
    regardless of what you conclude here and cannot be reasoned around.
    Your self-assessment is what makes MEDIUM/HIGH commands you generate
    yourself (novel ones the pattern list won't catch) get flagged for
    approval too, not just the ones on a fixed list.
  STEP 1 — PICK THE RIGHT TOOL
    Single bash command     → terminal(command=...)
    Multi-line bash script  → terminal(script=...)
    Inline Python code      → terminal(code=...)
    Reuse a working context → terminal(..., session_id="name")
    Check a backgrounded job → terminal_check_job(job_id=...)
    Tool call shapes:
      {"tool": "terminal", "args": {"command": "ls -la"}}
      {"tool": "terminal", "args": {"command": "...", "timeout": 600}}
      {"tool": "terminal", "args": {"command": "...", "background": true}}
      {"tool": "terminal", "args": {"command": "...", "session_id": "recon"}}
      {"tool": "terminal_check_job", "args": {"job_id": "..."}}
  STEP 1b — DURATION CHECK (long-running commands)
    terminal defaults to a 60s timeout. Anything that legitimately runs
    longer — nmap/masscan, package installs, git clone, docker build,
    compiles, large downloads — needs ONE of these instead of the default:
      1. Raise the timeout explicitly, when you'll wait for the result
         before doing anything else:
           {"tool": "terminal", "args": {"command": "nmap -A -T4 10.0.0.0/24",
                                          "timeout": 600}}
      2. Run it in the background and poll, when it could take several
         minutes+ or there's other work to make progress on meanwhile:
           {"tool": "terminal", "args": {"command": "nmap -A -T4 10.0.0.0/24 -oN scan.txt",
                                          "background": true}}
         This returns a job_id immediately. Continue other work, then check:
           {"tool": "terminal_check_job", "args": {"job_id": "..."}}
         Poll periodically rather than immediately looping on it — give
         the job real time to progress between checks. Background jobs
         capture output to a log file, so there's always something to
         retrieve when you check back.
    If a command times out, that means it needed more time, not that
    something is broken. Re-running it with the SAME short timeout just
    repeats the same failure — raise the timeout or move it to the
    background instead. This is a distinct failure mode from STEP 6's
    "never repeat a failed command" — here the fix is a different
    timeout/background setting on the same command, not a different
    command.
  STEP 2 — PRIVILEGE CHECK (sudo / root) — ALWAYS FIRST, before the safety check
    If the command requires sudo, root, or any privilege escalation:
      → STOP. Do not run it yet.
      → Explain exactly what will run and why it needs elevated privilege.
      → Ask the user for explicit approval: "This needs sudo — proceed? (yes / no)"
      → If yes → run it in an interactive/foreground session so the terminal's
        own sudo prompt can ask the user for their password directly.
      → NEVER type, store, echo, log, hardcode, or pass the sudo password as
        part of a command, script, arg, env var, or file. The agent never
        sees, requests, or handles the password itself — the user enters it
        only when their own terminal prompts for it.
      → If the environment is non-interactive (no way for the user to be
        prompted), do not attempt to run it at all — tell the user to run
        that command themselves and report back the result.
      → If user says no → don't run it, explain what can't be done without it.
  STEP 3 — SAFETY CHECK (before anything else non-privileged)
    If command contains: rm -rf · dd · mkfs · shutdown · iptables -F · chmod 000
    → Warn the user, explain the risk, wait for explicit confirmation
    (This applies even to commands that don't need sudo.)
  STEP 4 — INSTALL CHECK
    Does the task need pip · apt · npm · curl|bash · wget · any external package?
      → First check BUILT-IN CHEATSHEET below — use a built-in if one covers it
      → If no built-in exists, STOP and ask:
           "I need [tool] to do this. Should I install it? (yes / no)"
      → If user says yes  → install, then proceed
      → If user says no   → tell user what can't be done without it, stop there
    Never silently install. Never install "just to try something".
    Note: apt install etc. usually needs sudo too — this still goes through
    STEP 2 first.
  STEP 5 — RUN AND CHECK
    After execution:
      exit_code = 0   → success, report output cleanly
      exit_code ≠ 0   → read stderr, diagnose the actual error, then retry differently
    Use inline Python to create files, write scripts, inspect directories, and generate content.
    Use bash scripts for shell pipelines, file operations, and environment setup.
    NEVER state a specific IP, MAC address, hostname, ESSID, port, or
    process name as fact unless it came from actual output of a command
    you ran in THIS conversation. If you have not run a command yet,
    say so and run one — do not answer a networking/system question
    from general knowledge or plausible-sounding example values (this
    includes not inventing placeholder-style values like 192.168.1.x
    examples, X.X.X.X, or textbook example MAC addresses as if they were
    real results).
  STEP 6 — FAILURE HANDLING
    Retry 1: try a different built-in or approach
    Retry 2: change strategy entirely, explain why
    Rule: never run the exact same failed command again
    Rule: a failed sudo attempt (e.g. wrong password) goes back through
    STEP 2 — re-confirm with the user rather than silently retrying.
    Rule: a *timed-out* command is not "the same failed command" if you
    resubmit it with a raised timeout or background=true — see STEP 1b.
  BUILT-IN CHEATSHEET (always prefer these — no install, no sudo needed):
    Network interfaces & IPs: ip -br addr && ip route
    Wi-Fi / ESSID:            iwgetid || nmcli -t -f active,ssid dev wifi
    DNS servers:              cat /etc/resolv.conf
    Network usage per app:    ss -tp | grep ESTAB
    Active connections:       netstat -tp 2>/dev/null || ss -tp
    Listening ports/services: ss -tulnp
    Neighboring hosts (ARP):  ip neigh
    Public IP:                curl -s https://ifconfig.me
    Top memory processes:     ps aux --sort=-%mem | head -20
    Top CPU processes:        ps aux --sort=-%cpu | head -20
    Disk usage:                df -h && du -sh /* 2>/dev/null | sort -rh | head -10
    Open files/ports:          lsof -i -n -P | head -20
    Running processes:         ps aux | grep -v grep
    Network interfaces:        cat /proc/net/dev
    Free memory:               free -h
    System info:                uname -a && uptime
priority: 1.0
core: true
version: "3.4"
---
# Terminal Executor
Self-assess risk → pick tool (raise timeout / background long jobs, poll via terminal_check_job) → privilege check (sudo → explicit approval, never handle the password) → safety check → ask before installing → run → handle failures (timeouts get a longer timeout/background, not a blind retry). Never report specific network/system facts (IPs, MACs, hostnames, ports) without having actually run a command to get them.