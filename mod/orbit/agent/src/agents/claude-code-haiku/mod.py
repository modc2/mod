"""claude-code-haiku agent - Claude Code on the small model"""


class Agent:
    name = "Claude Code Haiku"
    description = "Claude Code on Haiku — the cheap end of the model lever, same CLI and tools"
    icon = "⬢"
    tools = None
    # the CLI's own model alias; the arena's boards say what the downgrade
    # costs in score and what it saves in tokens/USD
    model = "haiku"
    harness = "claude"
    owner = None

    goal = """You are running inside the orbit/agent console. Work in the directory you
were started in, read before you write, keep changes minimal, and end with a
short answer written to the user — that final message is all they see."""
