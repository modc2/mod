"""agi agent - Agi agent"""


class Agent:
    name = "Agi"
    description = "Agi agent"
    icon = ">_"
    tools = None
    model = 'sakana/fugu-ultra-v2'
    memory = None
    harness = None
    interface = None
    owner = None
    # console params saved with the agent — the runner, tool, model and
    # backend settings a console restores when this agent is picked. Free-
    # form JSON (dict); None = nothing saved beyond the fields above.
    params = None
    # set -> a feedback loop: {"builder": name, "reviewer": name} take turns
    # for `rounds` passes (params.rounds). Either may be this agent itself.
    loop = None
    # set -> a tool-agent: the agent IS this one tool ("x402.<slug>",
    # "mcp.<mod>.<tool>", "mod.<name>", a builtin…). JSON params call it
    # straight with no model in the loop; prose runs the loop on it alone.
    tool = None

    # the integrations this agent requires wired in: its prompt, the model it
    # calls, the toolbox it may reach, and the memory it thinks with
    requires = ("prompt", "model", "toolbox", "memory")

    goal = """do whatever the user wants"""
