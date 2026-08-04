"""codex agent - hands the run to the Codex CLI on this host"""


class Agent:
    name = "Codex"
    description = "OpenAI's Codex CLI — its own tools, sandbox and models"
    icon = "◇"
    tools = None
    model = None
    # not a persona over our loop: the whole run goes to the `codex` binary
    harness = "codex"
    owner = None

    goal = """You are running inside the orbit/agent console. Work in the directory you
were started in, read before you write, keep changes minimal, and end with a
short answer written to the user — that final message is all they see."""
