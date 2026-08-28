"""agentic/quick eval - the cheapest tasks that still tell agents apart.

Every match is an LLM loop, and every step in it re-sends the whole history
plus the tool schema. So the bill for a round is driven by step budgets, not
by how hard the task reads. These tasks are sized for that: a one-line
fixture, one thing to do, and a budget of two or three steps — a pass is
"looked once, acted once, stopped".

The point is not easy tasks, it is *short* ones. Each still has a way to
fail: guess instead of look, write the wrong thing, damage the fixture,
answer a read-only question by writing, or keep poking after the answer is
already in hand. A task nobody can fail is a constant you are paying tokens
to re-measure.

Use this suite when you want the board current without spending on it:

    m agent/arena/set_config suites='["agentic/quick"]'
"""


class Eval:
    name = "agentic/quick"
    description = "Micro tasks: one look, one action, stop. Cheapest useful signal."
    language = None
    owner = None
    agents = None  # every subject

    tasks = [
        {
            "title": "read one value",
            "prompt": (
                "Your working directory is {workdir}. It contains token.txt. "
                "Finish by stating the exact token in it. Do not guess."
            ),
            "steps": 3,
            "setup": {"files": {"token.txt": "heron-4417\n"}},
            "scorers": [
                {"type": "regex", "pattern": r"heron-4417"},
            ],
        },
        {
            "title": "write one value",
            "prompt": (
                "Your working directory is {workdir}. Write {workdir}/ok.txt whose "
                "entire contents are the word ready, then finish."
            ),
            "steps": 3,
            "scorers": [
                {"type": "file_regex", "path": "ok.txt", "pattern": r"^\s*ready\s*$"},
            ],
        },
        {
            "title": "flip one line",
            "prompt": (
                "Your working directory is {workdir}. In settings.ini there, change "
                "mode from off to on. Leave every other line alone, then finish."
            ),
            "steps": 3,
            "setup": {"files": {"settings.ini": "mode = off\nretries = 2\n"}},
            "scorers": [
                {"type": "file_regex", "path": "settings.ini", "pattern": r"mode\s*=\s*on"},
                # the line it was told not to touch has to come back out intact
                {"type": "file_regex", "path": "settings.ini", "pattern": r"retries\s*=\s*2"},
            ],
        },
        {
            "title": "answer without writing",
            "prompt": (
                "Your working directory is {workdir}. host.txt names one host. "
                "Finish by stating it. Write nothing, change nothing."
            ),
            "steps": 3,
            "setup": {"files": {"host.txt": "relay.internal\n"}},
            "scorers": [
                {"type": "regex", "pattern": r"relay\.internal"},
                {"type": "tool_not_used", "tool": "write"},
            ],
        },
        {
            "title": "stop immediately",
            "prompt": (
                "Nothing needs doing. Finish now with the word ACK in your summary. "
                "Do not read or write anything."
            ),
            # the whole task is one step; the budget only exists so a run that
            # ignores the instruction still terminates
            "steps": 2,
            "scorers": [
                {"type": "regex", "pattern": r"ACK"},
                # this is the suite grading the behaviour it exists to reward:
                # the answer was already in the prompt, so a tool call is waste
                {"type": "max_steps", "n": 1},
            ],
        },
    ]
