"""claude-mod agent - hands the run to the claude module's job server"""


class Agent:
    name = "Claude Console"
    description = ("The claude module's job server — Claude Code with per-caller "
                   "sandboxing, snapshot CIDs and a public task ledger")
    icon = "✦"
    tools = None
    model = None
    # not a persona over our loop, and not a local CLI either: the run becomes
    # a job in orbit/claude, so it keeps running (and stays steerable) there
    # after this conversation moves on
    harness = "claudemod"
    owner = None

    goal = """You are running inside the orbit/agent console. Work in the directory you
were started in, read before you write, keep changes minimal, and end with a
short answer written to the user — that final message is all they see."""
