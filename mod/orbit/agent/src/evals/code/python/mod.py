"""code/python eval - Python coding tasks for general-purpose agents."""


class Eval:
    name = "code/python"
    description = "Python coding tasks: implement, fix, refactor, review, audit."
    language = "python"
    owner = None  # None = inherit runtime owner key (m.key().address)
    # None = all subjects; list to restrict. Includes agent + claude runtimes.
    agents = [
        "default", "builder", "debugger", "refactorer",
        "reviewer", "safety", "architect",
        "agent", "claude",
    ]

    tasks = [
        {
            "prompt": (
                "Write a Python function `fizzbuzz(n: int) -> list[str]` that returns "
                "the FizzBuzz sequence from 1 to n. Use type hints and add a short docstring."
            ),
            "checks": ["def fizzbuzz", "Fizz", "Buzz"],
        },
        {
            "prompt": (
                "Implement an LRU cache decorator `lru(maxsize: int)` in pure Python "
                "(no functools.lru_cache). Use an OrderedDict and preserve call signatures."
            ),
            "checks": ["OrderedDict", "def lru", "maxsize"],
        },
        {
            "prompt": (
                "The function below has a bug — it raises `IndexError` on empty input. "
                "Identify the root cause and fix it.\n\n"
                "def first_or_default(seq, default=None):\n"
                "    return seq[0] if seq else seq[0]\n"
            ),
            "checks": ["return", "default"],
        },
        {
            "prompt": (
                "Refactor this Python code for readability without changing behavior. "
                "Add type hints and remove duplication.\n\n"
                "def p(x):\n"
                "    if x>0: return x*2\n"
                "    if x<0: return x*-2\n"
                "    return 0\n"
            ),
            "checks": ["def p", "int", "abs"],
        },
        {
            "prompt": (
                "Review this Python snippet for security issues and explain each finding "
                "with a severity (CRITICAL/HIGH/MEDIUM/LOW):\n\n"
                "import os\n"
                "def run(cmd): os.system('echo ' + cmd)\n"
            ),
            "checks": ["injection", "shell"],
        },
    ]
