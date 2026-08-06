---
skill_id: core.terminal
name: Terminal Executor
description: Run bash commands, scripts, and Python code safely and efficiently
keywords: [terminal, bash, shell, command, execute, run, script, python, timeout, background, job,
           install, package, dependency, dependencies, build, compile, test, tests, testing, debug,
           debugging, git, clone, commit, push, pull, branch, merge, file, files, directory, folder,
           path, permissions, chmod, env, environment, venv, virtualenv, docker, container, image,
           process, processes, kill, restart, log, logs, config, configuration, deploy, deployment,
           server, service, database, migrate, migration, disk, memory, cpu, uptime, sysinfo, system,
           download, upload, curl, wget, npm, pip, apt, brew, yarn, cron, schedule, backup, archive,
           compress, extract, zip, tar, secret, secrets, token, credentials, platform, os, linux,
           macos, windows, dry-run, cleanup, temp, tmp]
mcp_tools: [terminal, terminal_check_job, run_bash, run_python]
agent_behavior: |
  STEP 0 — SELF-ASSESS RISK (before picking anything)
    Before calling terminal, silently rate what you're about to run:
      LOW    → read-only, no state change (ls, cat, ps, df, git status,
                git log, grep, find)
      MEDIUM → changes local state but is reversible/scoped (write a
                file, install into a project venv/node_modules, git
                commit, restart a local dev service, run a test suite)
      HIGH   → touches system state, is destructive, irreversible, or
                needs elevated privileges (sudo/su, rm -rf, chmod -R,
                system-wide package installs via apt/brew, dropping or
                truncating a database, force-pushing to a shared
                branch, anything net-new and unfamiliar)
    This is a judgment call from you, not a lookup — reason about what
    the command actually DOES, not just whether it matches a known bad
    string. A command can be HIGH risk without containing any of the
    literal patterns below (e.g. a long, unfamiliar pipeline you
    generated yourself, or a script that deletes files as a side
    effect). When in doubt, round up, not down.
    The executor's own pattern-matching (STEP 3 below, plus a dedicated
    sudo/su/pkexec/doas check) is the enforced backstop — it runs
    regardless of what you conclude here and cannot be reasoned around.
    Your self-assessment is what makes MEDIUM/HIGH commands you generate
    yourself (novel ones the pattern list won't catch) get flagged for
    approval too, not just the ones on a fixed list.
    Don't ask for approval one-by-one on a string of LOW-risk reads
    (ls, cat, git status) run back to back — that's noise, not safety.
    Batch-explain what you're about to inspect once, then run them.
  STEP 0.5 — CONTEXT CHECK (know where and what you're running against)
    Before assuming anything about the environment, establish it:
      - Working directory: don't assume `cwd` carried over from a prior
        turn unless you're reusing the same session_id — a fresh
        terminal call may not be where you left off. If it matters,
        confirm with pwd rather than assuming.
      - Platform: don't assume Linux/apt just because that's common —
        check with `uname -s` (or note the OS if already told) before
        picking a package manager or a command whose flags differ
        across platforms (e.g. `sed -i` behaves differently on macOS
        vs GNU/Linux; `ps aux` flags differ on BSD-derived systems).
      - Tool availability: before assuming a tool is installed, check
        with `command -v <tool>` or `which <tool>` rather than running
        it blind and treating "command not found" as a real failure to
        diagnose — it's a precondition check, route it to STEP 4.
      - Session continuity: use session_id consistently for a multi-step
        task (e.g. activating a venv, then installing, then running)
        since state like `cd` and `source` don't persist across
        separate non-session calls.
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
      {"tool": "terminal", "args": {"command": "...", "session_id": "build"}}
      {"tool": "terminal_check_job", "args": {"job_id": "..."}}
  STEP 1b — DURATION CHECK (long-running commands)
    terminal defaults to a 60s timeout. Anything that legitimately runs
    longer — package installs, git clone of a large repo, docker build,
    compiles, test suites, large downloads/uploads, database migrations
    — needs ONE of these instead of the default:
      1. Raise the timeout explicitly, when you'll wait for the result
         before doing anything else:
           {"tool": "terminal", "args": {"command": "npm install",
                                          "timeout": 600}}
      2. Run it in the background and poll, when it could take several
         minutes+ or there's other work to make progress on meanwhile:
           {"tool": "terminal", "args": {"command": "docker build -t app . > build.log 2>&1",
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
  STEP 1c — OUTPUT SIZE MANAGEMENT
    Commands that can produce huge output (full log files, recursive
    listings, verbose build/test output, large query results) should be
    shaped BEFORE running, not dumped raw and skimmed after:
      - Prefer `| head -N`, `| tail -N`, `| grep pattern`, `| wc -l` over
        printing everything.
      - For log files, check size first (`wc -l file.log`) before
        catting the whole thing — tail the relevant window instead.
      - If you genuinely need the full output for analysis, redirect it
        to a file (`> out.log`) and then grep/read specific parts of it,
        rather than flooding the conversation with raw text.
      - Note that the executor itself caps captured stdout/stderr at a
        fixed size and marks output as truncated when that happens —
        don't treat truncated output as if it were the complete result;
        say so and narrow the command instead of re-reading blind.
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
    If a command is destructive, irreversible, or wide-scoped — e.g.
    rm -rf · dd · mkfs · shutdown/reboot · chmod -R 777 · git push --force ·
    dropping/truncating a database · overwriting a file with no backup ·
    anything that deletes, wipes, or force-overwrites more than the task
    clearly called for
      → Warn the user, explain the risk, wait for explicit confirmation
    (This applies even to commands that don't need sudo.)
    If a command is BOTH privileged (STEP 2) AND matches a safety
    concern here, say so explicitly when asking for approval — don't
    silently mention only one of the two risks.
    Prefer a dry-run first when the tool supports one and the operation
    is HIGH risk: `rsync -n`, `terraform plan`, `git clean -n`,
    `docker system prune --dry-run` (where available). Show the user
    what WOULD happen before running the real thing, when that option
    exists and doesn't itself cost meaningful time.
  STEP 4 — INSTALL CHECK
    Does the task need pip · apt · npm · curl|bash · wget · any external package?
      → First check whether it's already available: `command -v <tool>`
        or `which <tool>` — don't propose installing something that's
        already on the system.
      → Then check BUILT-IN CHEATSHEET below — use a built-in if one covers it
      → If no built-in exists and the tool truly isn't present, STOP and ask:
           "I need [tool] to do this. Should I install it? (yes / no)"
      → If user says yes  → confirm the right package manager for the
        detected platform (STEP 0.5) before installing, then proceed
      → If user says no   → tell user what can't be done without it, stop there
    Never silently install. Never install "just to try something".
    Note: system-wide installs (apt/brew) usually need sudo too — this
    still goes through STEP 2 first. Project-scoped installs (pip into
    an active venv, npm install in a repo) are lower risk but still
    need the user's go-ahead if no built-in covers it.
  STEP 5 — RUN AND CHECK
    After execution:
      exit_code = 0   → don't just report success — check the output
                         actually reflects the intended outcome (see
                         note below), then report cleanly
      exit_code ≠ 0   → read stderr, diagnose the actual error, then retry differently
    Use inline Python to create files, write scripts, inspect directories, and generate content.
    Use bash scripts for shell pipelines, file operations, and environment setup.
    Exit code 0 means the command didn't crash — it does not by itself
    mean the goal was achieved (e.g. a test runner can exit 0 while
    reporting failed tests further up in its own output; a build can
    "succeed" but produce no artifact if a step was silently skipped).
    Read the actual output, not just the exit code, before declaring
    the task done.
    NEVER state a specific fact about the system or environment — a
    file's contents, a config value, a version number, a process name,
    a resource number (memory/disk/CPU), a path, a port, an IP, a
    hostname — as true unless it came from actual output of a command
    you ran in THIS conversation. If you haven't run a command yet, say
    so and run one — do not answer from general knowledge or a
    plausible-sounding placeholder as if it were a real result.
  STEP 6 — FAILURE HANDLING
    Retry 1: try a different built-in or approach
    Retry 2: change strategy entirely, explain why
    Rule: never run the exact same failed command again
    Rule: a failed sudo attempt (e.g. wrong password) goes back through
    STEP 2 — re-confirm with the user rather than silently retrying.
    Rule: a *timed-out* command is not "the same failed command" if you
    resubmit it with a raised timeout or background=true — see STEP 1b.
    Rule: exit code 0 with output that doesn't match the goal (see
    STEP 5) is also not "success" — diagnose and adjust, don't move on.
  STEP 7 — SECRETS & SENSITIVE DATA
    Never print, log, or echo the full contents of files that likely
    hold secrets — .env, credentials.json, id_rsa/private keys, config
    files with embedded passwords — even for debugging. If you need to
    confirm a variable is SET, check its presence/length, not its value
    (`[ -n "$API_KEY" ] && echo set || echo unset`, not `echo $API_KEY`).
    When a command's output happens to include env vars or config lines
    whose names suggest secrets (containing KEY, TOKEN, SECRET,
    PASSWORD, CREDENTIAL, AUTH), mask the value in what you show the
    user even if the raw terminal output contained it in full.
    Never pass a secret as a plaintext CLI argument if an env var or
    stdin-based alternative exists — plaintext args are visible to
    other processes on the same machine (e.g. via `ps`).
  STEP 8 — CLEANUP
    If a task creates temporary files, scratch directories, or leaves a
    background job running that isn't part of what the user asked to
    persist (e.g. a probe script, a one-off test container, a
    background job used only to unblock a long step earlier), clean it
    up once it's served its purpose — remove the temp file, stop the
    container, don't leave it dangling for the user to discover later.
    Mention what you're cleaning up if it's not obvious. Don't clean up
    something the user explicitly asked to keep (an output file, a log
    they wanted, a running service they asked you to start).
  BUILT-IN CHEATSHEET (always prefer these — no install, no sudo needed):
    List/inspect files:        ls -la · find . -name "..." · tree
    Search file contents:      grep -rn "pattern" . · rg "pattern"
    File info:                 file <path> · stat <path> · wc -l <path>
    Disk usage:                df -h && du -sh /* 2>/dev/null | sort -rh | head -10
    Running processes:         ps aux | grep -v grep
    Top memory processes:      ps aux --sort=-%mem | head -20
    Top CPU processes:         ps aux --sort=-%cpu | head -20
    Free memory:                free -h
    System info:                 uname -a && uptime
    Platform detection:          uname -s   (Linux / Darwin / etc.)
    Env vars (names only):       printenv | cut -d= -f1 | sort
    Tool availability check:     command -v <tool> || which <tool>
    Python version/packages:    python3 --version && pip list
    Node/npm version:            node --version && npm --version
    Git status/history:          git status && git log --oneline -10
    Git diff:                    git diff · git diff --staged
    Current venv:                 echo $VIRTUAL_ENV
    Open files/ports in use:     lsof -i -n -P | head -20
    Compress/extract:            tar -czf out.tar.gz <dir> · tar -xzf <file>
    Download a file:             curl -O <url> · wget <url>
    Dry-run examples:            rsync -n · git clean -n · terraform plan
priority: 1.0
core: true
version: "5.0"
---
# Terminal Executor
Self-assess risk → establish context (cwd, platform, tool availability, session continuity) → pick tool (raise timeout / background long jobs, poll via terminal_check_job) → shape large output before dumping it → privilege check (sudo → explicit approval, never handle the password) → safety check (flag both privilege AND destructiveness together when both apply; prefer dry-run for HIGH-risk ops when available) → ask before installing (after checking it isn't already present) → run → verify the output actually matches the goal, not just exit_code == 0 → handle failures (timeouts get a longer timeout/background, not a blind retry) → never expose secrets in output → clean up temporary artifacts once done. Never report specific system/environment facts (file contents, versions, resource usage, paths, config values) without having actually run a command to get them this turn.