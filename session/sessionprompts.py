# session/sessionprompts.py (template)

# ---------------- AGENT PROMPTS TEMPLATE ----------------

# Scope query prompt (agent uses this to ask what’s allowed)
SCOPE_PROMPT = """
You are an authorized pentester. Only return the allowed scope and rules of engagement for this workspace.
"""

# Session summary prompt (agent uses this to summarize findings)
SESSION_SUMMARY_PROMPT = """
Summarize all current findings in this pentest session:
- Open ports
- Vulnerabilities
- Exploits attempted

Be concise and structured.
"""

# Operator preference prompt (agent uses this to remember operator style)
OPERATOR_PREF_PROMPT = """
Remember operator preferences for responses: short, concise, and technical.
Do not include unnecessary explanations.
"""

# Optional: template for additional memory queries
MEMORY_QUERY_TEMPLATE = """
Retrieve persistent memory or past experiences for: {topic}
"""
