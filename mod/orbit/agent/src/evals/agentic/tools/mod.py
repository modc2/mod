"""agentic/tools eval - does the agent reach for a tool, or guess.

These tasks cannot be answered from the prompt alone: the answer is in the
scratch dir, so the agent has to look. They also grade restraint — one task
passes only if nothing was written.
"""


class Eval:
    name = "agentic/tools"
    description = "Tool-use tasks: find the answer on disk, and stop when done."
    language = None
    owner = None
    agents = None  # every subject

    tasks = [
        {
            "title": "count files by extension",
            "prompt": (
                "Your working directory is {workdir}. How many files there end in .py? "
                "Look — do not guess. Finish with the count as a plain number in your "
                "summary."
            ),
            "steps": 8,
            "setup": {"files": {
                "main.py": "print('hi')\n",
                "util.py": "def noop():\n    pass\n",
                "server.py": "PORT = 9000\n",
                "readme.md": "# demo\n",
                "data.csv": "a,b\n1,2\n",
            }},
            "scorers": [
                {"type": "regex", "pattern": r"\b3\b"},
                {"type": "max_steps", "n": 8},
            ],
        },
        {
            "title": "find the marker",
            "prompt": (
                "Your working directory is {workdir}. Exactly one file there contains "
                "the word FIXME. Search for it and finish by naming that file."
            ),
            "steps": 8,
            "setup": {"files": {
                "alpha.txt": "nothing to see\n",
                "beta.txt": "still nothing\n",
                "gamma.txt": "FIXME: the retry loop never backs off\n",
                "delta.txt": "clean\n",
            }},
            "scorers": [
                {"type": "regex", "pattern": r"gamma\.txt"},
            ],
        },
        {
            "title": "read-only question",
            "prompt": (
                "Your working directory is {workdir}. Read config.ini there and finish "
                "by stating which host it points at. Answer only — write nothing, "
                "change nothing."
            ),
            "steps": 6,
            "setup": {"files": {"config.ini": "[net]\nhost = relay.internal\n"
                                              "port = 7000\n"}},
            "scorers": [
                {"type": "regex", "pattern": r"relay\.internal"},
                {"type": "tool_not_used", "tool": "write"},
                {"type": "tool_not_used", "tool": "edit"},
            ],
        },
    ]
