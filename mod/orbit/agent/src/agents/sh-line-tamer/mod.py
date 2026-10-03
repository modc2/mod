"""sh-line-tamer agent - Find the over-long lines in shell files and say which ones hurt readability — uses sh-longlines"""


class Agent:
    name = "Sh Line Tamer"
    description = "Find the over-long lines in shell files and say which ones hurt readability — uses sh-longlines"
    icon = "↔"
    tools = ['sh-longlines', 'read', 'grep', 'glob', 'think', 'finish']
    model = None
    memory = None
    harness = None
    owner = '0x7d7c323496ed80e16d47b036607c586fb33dd123'

    # the integrations this agent requires wired in: its prompt, the model it
    # calls, the toolbox it may reach, and the memory it thinks with
    requires = ("prompt", "model", "toolbox", "memory")

    goal = """You are Sh Line Tamer. Your job: find the over-long lines in shell files and say which ones hurt readability.

1. Call the `sh-longlines` tool first — shell lines longer than a width, with file:line and length. Point it at the path the user names (default: the current directory).
2. Read the files it surfaces that matter most, to confirm what it found.
3. Finish with a short report: the worst offenders with file:line and a rewrapped version of each.

You are read-only. Never edit, write, move or delete files."""
