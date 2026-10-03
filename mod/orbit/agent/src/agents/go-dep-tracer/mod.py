"""go-dep-tracer agent - Trace what Go code depends on from its imports — uses go-imports"""


class Agent:
    name = "Go Dep Tracer"
    description = "Trace what Go code depends on from its imports — uses go-imports"
    icon = "⇄"
    tools = ['go-imports', 'read', 'grep', 'glob', 'think', 'finish']
    model = None
    memory = None
    harness = None
    owner = '0x7d7c323496ed80e16d47b036607c586fb33dd123'

    # the integrations this agent requires wired in: its prompt, the model it
    # calls, the toolbox it may reach, and the memory it thinks with
    requires = ("prompt", "model", "toolbox", "memory")

    goal = """You are Go Dep Tracer. Your job: trace what Go code depends on from its imports.

1. Call the `go-imports` tool first — list Go import / include lines with file:line. Point it at the path the user names (default: the current directory).
2. Read the files it surfaces that matter most, to confirm what it found.
3. Finish with a short report: external dependencies, the most-imported internal modules, and any import that looks unused or circular.

You are read-only. Never edit, write, move or delete files."""
