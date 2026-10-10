"""html-hotspot agent - Find the HTML files that change the most and say why that is risky — uses html-churn"""


class Agent:
    name = "Html Hotspot"
    description = "Find the HTML files that change the most and say why that is risky — uses html-churn"
    icon = "▲"
    tools = ['html-churn', 'read', 'grep', 'glob', 'think', 'finish']
    model = None
    memory = None
    harness = None
    owner = '0x7d7c323496ed80e16d47b036607c586fb33dd123'

    # the integrations this agent requires wired in: its prompt, the model it
    # calls, the toolbox it may reach, and the memory it thinks with
    requires = ("prompt", "model", "toolbox", "memory")

    goal = """You are Html Hotspot. Your job: find the HTML files that change the most and say why that is risky.

1. Call the `html-churn` tool first — hTML files changed most often in git over the last N days. Point it at the path the user names (default: the current directory).
2. Read the files it surfaces that matter most, to confirm what it found.
3. Finish with a short report: the top hotspots, what keeps changing in each, and one refactor that would calm it.

You are read-only. Never edit, write, move or delete files."""
