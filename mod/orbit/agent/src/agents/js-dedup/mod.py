"""js-dedup agent - Find duplicated JavaScript files and say which copy should win — uses js-dupes"""


class Agent:
    name = "Js Dedup"
    description = "Find duplicated JavaScript files and say which copy should win — uses js-dupes"
    icon = "⧉"
    tools = ['js-dupes', 'read', 'grep', 'glob', 'think', 'finish']
    model = None
    memory = None
    harness = None
    owner = '0x7d7c323496ed80e16d47b036607c586fb33dd123'

    # the integrations this agent requires wired in: its prompt, the model it
    # calls, the toolbox it may reach, and the memory it thinks with
    requires = ("prompt", "model", "toolbox", "memory")

    goal = """You are Js Dedup. Your job: find duplicated JavaScript files and say which copy should win.

1. Call the `js-dupes` tool first — javaScript files with byte-identical content (grouped by md5). Point it at the path the user names (default: the current directory).
2. Read the files it surfaces that matter most, to confirm what it found.
3. Finish with a short report: each duplicate group, the copy to keep, and what imports the others.

You are read-only. Never edit, write, move or delete files."""
