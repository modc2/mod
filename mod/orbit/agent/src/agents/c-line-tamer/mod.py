"""c-line-tamer agent - Find the over-long lines in C files and say which ones hurt readability — uses c-longlines"""


class Agent:
    name = "C Line Tamer"
    description = "Find the over-long lines in C files and say which ones hurt readability — uses c-longlines"
    icon = "↔"
    tools = ['c-longlines', 'read', 'grep', 'glob', 'think', 'finish']
    model = None
    memory = None
    harness = None
    owner = '0x7d7c323496ed80e16d47b036607c586fb33dd123'

    # the integrations this agent requires wired in: its prompt, the model it
    # calls, the toolbox it may reach, and the memory it thinks with
    requires = ("prompt", "model", "toolbox", "memory")

    goal = """You are C Line Tamer. Your job: find the over-long lines in C files and say which ones hurt readability.

1. Call the `c-longlines` tool first — c lines longer than a width, with file:line and length. Point it at the path the user names (default: the current directory).
2. Read the files it surfaces that matter most, to confirm what it found.
3. Finish with a short report: the worst offenders with file:line and a rewrapped version of each.

You are read-only. Never edit, write, move or delete files."""
