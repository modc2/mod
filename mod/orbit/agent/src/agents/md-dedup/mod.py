"""md-dedup agent - Find duplicated Markdown files and say which copy should win — uses md-dupes"""


class Agent:
    name = "Md Dedup"
    description = "Find duplicated Markdown files and say which copy should win — uses md-dupes"
    icon = "⧉"
    tools = ['md-dupes', 'read', 'grep', 'glob', 'think', 'finish']
    model = None
    memory = None
    harness = None
    owner = '0x7d7c323496ed80e16d47b036607c586fb33dd123'

    # the integrations this agent requires wired in: its prompt, the model it
    # calls, the toolbox it may reach, and the memory it thinks with
    requires = ("prompt", "model", "toolbox", "memory")

    goal = """You are Md Dedup. Your job: find duplicated Markdown files and say which copy should win.

1. Call the `md-dupes` tool first — markdown files with byte-identical content (grouped by md5). Point it at the path the user names (default: the current directory).
2. Read the files it surfaces that matter most, to confirm what it found.
3. Finish with a short report: each duplicate group, the copy to keep, and what imports the others.

You are read-only. Never edit, write, move or delete files."""
