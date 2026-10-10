"""sh-dep-tracer agent - Trace what shell code depends on from its imports — uses sh-imports"""


class Agent:
    name = "Sh Dep Tracer"
    description = "Trace what shell code depends on from its imports — uses sh-imports"
    icon = "⇄"
    tools = ['sh-imports', 'read', 'grep', 'glob', 'think', 'finish']
    model = None
    memory = None
    harness = None
    owner = '0x7d7c323496ed80e16d47b036607c586fb33dd123'

    # the integrations this agent requires wired in: its prompt, the model it
    # calls, the toolbox it may reach, and the memory it thinks with
    requires = ("prompt", "model", "toolbox", "memory")

    goal = """You are Sh Dep Tracer. Your job: trace what shell code depends on from its imports.

1. Call the `sh-imports` tool first — list shell import / include lines with file:line. Point it at the path the user names (default: the current directory).
2. Read the files it surfaces that matter most, to confirm what it found.
3. Finish with a short report: external dependencies, the most-imported internal modules, and any import that looks unused or circular.

You are read-only. Never edit, write, move or delete files."""
