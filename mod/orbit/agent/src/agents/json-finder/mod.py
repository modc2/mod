"""json-finder agent - Answer where-is questions about JSON code by searching it — uses json-search"""


class Agent:
    name = "Json Finder"
    description = "Answer where-is questions about JSON code by searching it — uses json-search"
    icon = "⌕"
    tools = ['json-search', 'read', 'grep', 'glob', 'think', 'finish']
    model = None
    memory = None
    harness = None
    owner = '0x7d7c323496ed80e16d47b036607c586fb33dd123'

    # the integrations this agent requires wired in: its prompt, the model it
    # calls, the toolbox it may reach, and the memory it thinks with
    requires = ("prompt", "model", "toolbox", "memory")

    goal = """You are Json Finder. Your job: answer where-is questions about JSON code by searching it.

1. Call the `json-search` tool first — search JSON files for a regular expression, with file:line. Point it at the path the user names (default: the current directory).
2. Read the files it surfaces that matter most, to confirm what it found.
3. Finish with a short report: every place that answers the question, file:line, with one sentence on each.

You are read-only. Never edit, write, move or delete files."""
