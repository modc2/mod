"""feedback agent - the builder does the task, the reviewer critiques it, repeat

A feedback loop between two agents rather than a persona over one loop:
round 1 the builder does the task; the reviewer reads the result and writes
concrete feedback; round 2 a fresh builder run gets the same task PLUS that
feedback and improves the work already on disk; … for `rounds` builder runs
(default 3), with a review between each. The run's answer is the last
builder's report.

Knobs (run kwargs, or the console's ✦ params):
    rounds    builder passes, 1-10 (default 3)
    builder   agent that does the work      (default: the `loop` below)
    reviewer  agent that reviews it         (default: the `loop` below)

Mod._run sees `loop` on the agent and hands the run to Mod._run_feedback.
"""


class Agent:
    name = "Feedback"
    description = "Builder ⇄ reviewer feedback loop — the task is redone each round with the reviewer's notes"
    icon = "↻"
    tools = None
    model = None
    harness = None
    # the two agents in the loop — any registry names work
    loop = {"builder": "builder", "reviewer": "reviewer"}
    # the console params a pick restores: build's composer switches to the
    # ✦ agent runner (the loop lives on this module) with rounds = 3
    params = {"console": "build", "runner": "agent", "agent": {"rounds": 3}, "rounds": 3}
    # a loop of other agents, not a coder of its own — keep it off the board
    arena = False

    goal = """You are a feedback loop: a builder agent does the task, a reviewer agent critiques the result, and the builder redoes the task with that feedback — for a set number of rounds. Each round must leave the work strictly better than the last."""
