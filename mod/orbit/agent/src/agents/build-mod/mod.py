"""build-mod agent - hands the run to the build module's job server"""


class Agent:
    name = "Build Console"
    description = ("The build module's job server — Claude Code with per-caller "
                   "sandboxing, snapshot CIDs and a public task ledger")
    icon = "✦"
    tools = None
    model = None
    # same shape as claude-mod, a different console: the run becomes a job in
    # orbit/build, so it lands in that module's ledger and stays steerable
    # there after this conversation moves on
    harness = "buildmod"
    owner = None

    goal = """You are running inside the orbit/agent console. Work in the directory you
were started in, read before you write, keep changes minimal, and end with a
short answer written to the user — that final message is all they see."""
