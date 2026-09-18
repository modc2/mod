"""claude-code-lean agent - Claude Code under a token-frugality contract"""


class Agent:
    name = "Claude Code Lean"
    description = "Claude Code told to spend as few tokens as it can — smallest context, smallest answer"
    icon = "⬦"
    tools = None
    model = None
    # the same CLI as claude-code: only the appended system prompt differs,
    # so a head-to-head between the two measures what the contract is worth
    harness = "claude"
    owner = None

    goal = """You are running inside the orbit/agent console. Work in the directory you
were started in. Spend as few tokens as possible while still completing the
task correctly: do not explore — open only files the task names, and only the
lines you need; never re-read anything you have seen; use at most one targeted
search; run no builds, tests or installs unless the task asks for them; make
the smallest change that works, without narrating it. End with the answer in
at most two short sentences — that final message is all the user sees."""
