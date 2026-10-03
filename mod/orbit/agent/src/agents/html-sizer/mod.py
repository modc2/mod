"""html-sizer agent - Measure how much HTML code a project has and where it lives — uses html-loc"""


class Agent:
    name = "Html Sizer"
    description = "Measure how much HTML code a project has and where it lives — uses html-loc"
    icon = "#"
    tools = ['html-loc', 'read', 'grep', 'glob', 'think', 'finish']
    model = None
    memory = None
    harness = None
    owner = '0x7d7c323496ed80e16d47b036607c586fb33dd123'

    # the integrations this agent requires wired in: its prompt, the model it
    # calls, the toolbox it may reach, and the memory it thinks with
    requires = ("prompt", "model", "toolbox", "memory")

    goal = """You are Html Sizer. Your job: measure how much HTML code a project has and where it lives.

1. Call the `html-loc` tool first — count HTML files and their total lines. Point it at the path the user names (default: the current directory).
2. Read the files it surfaces that matter most, to confirm what it found.
3. Finish with a short report: file and line totals, then the three directories holding most of it.

You are read-only. Never edit, write, move or delete files."""
