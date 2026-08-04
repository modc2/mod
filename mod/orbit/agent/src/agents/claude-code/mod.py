"""claude-code agent - hands the run to the Claude Code CLI on this host"""


class Agent:
    name = "Claude Code"
    description = "Anthropic's Claude Code CLI — its own tools, sandbox and models"
    icon = "⬡"
    tools = None
    model = None
    # not a persona over our loop: the whole run goes to the `claude` binary
    harness = "claude"
    owner = None

    goal = """You are running inside the orbit/agent console. Work in the directory you
were started in, read before you write, keep changes minimal, and end with a
short answer written to the user — that final message is all they see."""
