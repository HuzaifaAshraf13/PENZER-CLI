# session/sessionprompts.py (template)

# ---------------- AGENT PROMPTS TEMPLATE ----------------

# Scope query prompt (agent uses this to ask what’s allowed)
SCOPE_PROMPT = """
You are an authorized penetration testing agent operating within a defined workspace.

Your task:
- Identify the allowed scope (targets, IP ranges, domains).
- Identify rules of engagement (what actions are permitted or forbidden).

Rules:
- Return ONLY factual scope information.
- Do NOT infer, guess, or expand scope.
- If scope is not explicitly defined, return exactly:
  "SCOPE NOT DEFINED"

Output format:
- Targets:
- Allowed actions:
- Forbidden actions:
"""

# Session summary prompt (agent uses this to summarize findings)
SESSION_SUMMARY_PROMPT = """
Summarize the current penetration testing session for long-term memory storage.

Include ONLY confirmed information.

Required sections:
- Discovered open ports and services
- Identified vulnerabilities
- Exploits attempted and results
- Credentials or access gained (if any)

Rules:
- Be concise.
- Use bullet points.
- Do NOT speculate.
- Output must be suitable for persistent memory storage.
"""

# Operator preference prompt (agent uses this to remember operator style)
OPERATOR_PREF_PROMPT = """
Extract operator communication preferences from the interaction.

Focus on:
- Response length (short / medium / verbose)
- Tone (technical, casual, explanatory)
- Level of detail (high-level vs command-level)

Output rules:
- Convert preferences into concise rules.
- Do NOT include explanations.
- Example format:
  - Response length: short
  - Tone: technical
  - Detail level: command-focused
"""

# Optional: template for additional memory queries
MEMORY_QUERY_TEMPLATE = """
Retrieve relevant long-term memory related to the following topic:

Topic: {topic}

Rules:
- Focus only on this workspace.
- Ignore unrelated or outdated information.
- Return actionable findings, not raw text.
"""
SHORT_TERM_MEMORY_PROMPT = """
You have access to the following short-term session memory.
Use this memory to inform your reasoning and avoid repeated actions.

Rules for usage:
- Memory is workspace-specific and temporary.
- Only include confirmed findings, not guesses.
- Organize memory by categories:
  1. Targets discovered (IP, domain)
  2. Open ports and services
  3. Vulnerabilities detected
  4. Exploits attempted and results
  5. Temporary credentials or session tokens
  6. Notes about in-progress tasks
- Include timestamps or order indicators if available.
- Never repeat actions already recorded here.
- Always refer to this memory before planning next steps.

Memory:
{short_memory}
"""

