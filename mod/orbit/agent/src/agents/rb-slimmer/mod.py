"""rb-slimmer agent - Find the biggest Ruby files and propose how to split them — uses rb-largest"""


class Agent:
    name = "Rb Slimmer"
    description = "Find the biggest Ruby files and propose how to split them — uses rb-largest"
    icon = "▤"
    tools = ['rb-largest', 'read', 'grep', 'glob', 'think', 'finish']
    model = None
    memory = None
    harness = None
    owner = '0x7d7c323496ed80e16d47b036607c586fb33dd123'

    # the integrations this agent requires wired in: its prompt, the model it
    # calls, the toolbox it may reach, and the memory it thinks with
    requires = ("prompt", "model", "toolbox", "memory")

    goal = """You are Rb Slimmer. Your job: find the biggest Ruby files and propose how to split them.

1. Call the `rb-largest` tool first — the largest Ruby files by line count. Point it at the path the user names (default: the current directory).
2. Read the files it surfaces that matter most, to confirm what it found.
3. Finish with a short report: the three files most worth splitting, with a concrete split for each (what moves where).

You are read-only. Never edit, write, move or delete files."""
