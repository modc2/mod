"""ts-changelog agent - Summarize what changed recently in the TypeScript files — uses ts-recent"""


class Agent:
    name = "Ts Changelog"
    description = "Summarize what changed recently in the TypeScript files — uses ts-recent"
    icon = "◷"
    tools = ['ts-recent', 'read', 'grep', 'glob', 'think', 'finish']
    model = None
    memory = None
    harness = None
    owner = '0x7d7c323496ed80e16d47b036607c586fb33dd123'

    # the integrations this agent requires wired in: its prompt, the model it
    # calls, the toolbox it may reach, and the memory it thinks with
    requires = ("prompt", "model", "toolbox", "memory")

    goal = """You are Ts Changelog. Your job: summarize what changed recently in the TypeScript files.

1. Call the `ts-recent` tool first — typeScript files modified in the last N days, newest first. Point it at the path the user names (default: the current directory).
2. Read the files it surfaces that matter most, to confirm what it found.
3. Finish with a short report: a short changelog grouped by area, newest first, naming the files behind each line.

You are read-only. Never edit, write, move or delete files."""
