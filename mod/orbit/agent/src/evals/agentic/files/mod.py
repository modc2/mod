"""agentic/files eval - can the agent change the world, not just describe it.

Every task is graded on what is on disk when the run ends, not on what the
agent said it would do. `setup.files` is seeded into the match's scratch dir
first and `{workdir}` is replaced with its absolute path, so the same fixture
faces every agent.
"""


class Eval:
    name = "agentic/files"
    description = "File-effect tasks: read the fixture, produce the artifact."
    language = "python"
    owner = None
    agents = None  # every subject

    tasks = [
        {
            "title": "count the lines of a file",
            "prompt": (
                "Your working directory is {workdir}. It contains notes.txt. "
                "Count the lines in it and write a file {workdir}/count.txt whose "
                "entire contents are that number and nothing else. Then finish."
            ),
            "steps": 6,
            "setup": {"files": {"notes.txt": "alpha\nbravo\ncharlie\ndelta\n"
                                             "echo\nfoxtrot\ngolf\n"}},
            "scorers": [
                {"type": "file_exists", "path": "count.txt"},
                {"type": "file_regex", "path": "count.txt", "pattern": r"^\s*7\s*$"},
            ],
        },
        {
            "title": "pull a value out of json",
            "prompt": (
                "Your working directory is {workdir}. Read service.json there and write "
                "{workdir}/port.txt containing only the value of its \"port\" field. "
                "Then finish."
            ),
            "steps": 6,
            "setup": {"files": {"service.json": '{\n  "name": "relay",\n  "port": 8412,\n'
                                                '  "replicas": 3\n}\n'}},
            "scorers": [
                {"type": "file_regex", "path": "port.txt", "pattern": r"^\s*8412\s*$"},
            ],
        },
        {
            "title": "fix the failing function",
            "prompt": (
                "Your working directory is {workdir}. calc.py there has a bug: add(2, 3) "
                "returns -1 instead of 5. Fix calc.py in place, leaving the rest of the "
                "file alone, then finish."
            ),
            "steps": 8,
            "setup": {"files": {"calc.py": "def add(a, b):\n    return a - b\n\n\n"
                                           "def mul(a, b):\n    return a * b\n"}},
            "scorers": [
                {"type": "file_regex", "path": "calc.py", "pattern": r"a\s*\+\s*b"},
                # the untouched half of the file has to survive the edit
                {"type": "file_regex", "path": "calc.py", "pattern": r"a\s*\*\s*b"},
            ],
        },
        {
            "title": "leave the fixture alone",
            "prompt": (
                "Your working directory is {workdir}. Read readme.md there and write a "
                "one-line summary of what the project does into {workdir}/summary.txt. "
                "Do not modify readme.md. Then finish."
            ),
            "steps": 6,
            "setup": {"files": {"readme.md": "# ledgerd\n\nA tiny append-only ledger "
                                             "daemon for offline payment terminals.\n"}},
            "scorers": [
                {"type": "file_exists", "path": "summary.txt"},
                {"type": "file_regex", "path": "summary.txt", "pattern": r"(?i)ledger"},
                # the fixture must come back out exactly as it went in
                {"type": "file_regex", "path": "readme.md",
                 "pattern": r"^# ledgerd\n\nA tiny append-only ledger daemon"},
            ],
        },
    ]
