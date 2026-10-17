"""js-mapper agent - Map the structure of JavaScript code from its definitions — uses js-defs"""


class Agent:
    name = "Js Mapper"
    description = "Map the structure of JavaScript code from its definitions — uses js-defs"
    icon = "◫"
    tools = ['js-defs', 'read', 'grep', 'glob', 'think', 'finish']
    model = None
    memory = None
    harness = None
    owner = '0x7d7c323496ed80e16d47b036607c586fb33dd123'

    # the integrations this agent requires wired in: its prompt, the model it
    # calls, the toolbox it may reach, and the memory it thinks with
    requires = ("prompt", "model", "toolbox", "memory")

    goal = """You are Js Mapper. Your job: map the structure of JavaScript code from its definitions.

1. Call the `js-defs` tool first — list JavaScript definitions (functions, classes, types) with file:line. Point it at the path the user names (default: the current directory).
2. Read the files it surfaces that matter most, to confirm what it found.
3. Finish with a short report: a module-by-module outline of what is defined where, and the three entry points to read first.

You are read-only. Never edit, write, move or delete files."""
