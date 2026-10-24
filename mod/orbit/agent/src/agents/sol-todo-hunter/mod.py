"""sol-todo-hunter agent - Find the open TODO and FIXME markers in Solidity code and rank which to fix first — uses sol-todos"""


class Agent:
    name = "Sol Todo Hunter"
    description = "Find the open TODO and FIXME markers in Solidity code and rank which to fix first — uses sol-todos"
    icon = "☐"
    tools = ['sol-todos', 'read', 'grep', 'glob', 'think', 'finish']
    model = None
    memory = None
    harness = None
    owner = '0x7d7c323496ed80e16d47b036607c586fb33dd123'

    # the integrations this agent requires wired in: its prompt, the model it
    # calls, the toolbox it may reach, and the memory it thinks with
    requires = ("prompt", "model", "toolbox", "memory")

    goal = """You are Sol Todo Hunter. Your job: find the open TODO and FIXME markers in Solidity code and rank which to fix first.

1. Call the `sol-todos` tool first — list TODO / FIXME / XXX / HACK markers in Solidity files, with file:line. Point it at the path the user names (default: the current directory).
2. Read the files it surfaces that matter most, to confirm what it found.
3. Finish with a short report: the five markers that matter most, each with file:line and the change it asks for.

You are read-only. Never edit, write, move or delete files."""
