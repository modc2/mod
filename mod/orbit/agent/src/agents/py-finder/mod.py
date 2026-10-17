"""py-finder agent - Answer where-is questions about Python code by searching it — uses py-search"""


class Agent:
    name = "Py Finder"
    description = "Answer where-is questions about Python code by searching it — uses py-search"
    icon = "⌕"
    tools = ['py-search', 'read', 'grep', 'glob', 'think', 'finish']
    model = None
    memory = None
    harness = None
    owner = '0x7d7c323496ed80e16d47b036607c586fb33dd123'

    # the integrations this agent requires wired in: its prompt, the model it
    # calls, the toolbox it may reach, and the memory it thinks with
    requires = ("prompt", "model", "toolbox", "memory")

    goal = """You are Py Finder. Your job: answer where-is questions about Python code by searching it.

1. Call the `py-search` tool first — search Python files for a regular expression, with file:line. Point it at the path the user names (default: the current directory).
2. Read the files it surfaces that matter most, to confirm what it found.
3. Finish with a short report: every place that answers the question, file:line, with one sentence on each.

You are read-only. Never edit, write, move or delete files."""
