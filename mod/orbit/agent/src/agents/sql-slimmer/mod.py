"""sql-slimmer agent - Find the biggest SQL files and propose how to split them — uses sql-largest"""


class Agent:
    name = "Sql Slimmer"
    description = "Find the biggest SQL files and propose how to split them — uses sql-largest"
    icon = "▤"
    tools = ['sql-largest', 'read', 'grep', 'glob', 'think', 'finish']
    model = None
    memory = None
    harness = None
    owner = '0x7d7c323496ed80e16d47b036607c586fb33dd123'

    # the integrations this agent requires wired in: its prompt, the model it
    # calls, the toolbox it may reach, and the memory it thinks with
    requires = ("prompt", "model", "toolbox", "memory")

    goal = """You are Sql Slimmer. Your job: find the biggest SQL files and propose how to split them.

1. Call the `sql-largest` tool first — the largest SQL files by line count. Point it at the path the user names (default: the current directory).
2. Read the files it surfaces that matter most, to confirm what it found.
3. Finish with a short report: the three files most worth splitting, with a concrete split for each (what moves where).

You are read-only. Never edit, write, move or delete files."""
