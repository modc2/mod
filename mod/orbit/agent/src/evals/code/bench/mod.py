"""code/bench eval - the task is its own benchmark.

Every other suite here grades a *proxy*: a regex over a file is a guess about
whether the program is right, and a program can match every pattern and still
be broken. These tasks carry the benchmark with them. The fixture is a
repository with a test suite in it, the agent's job is to make that suite
pass, and the `tests` scorer runs it over whatever was left on disk. The score
is the fraction of cases that pass, so a near-miss ranks above a blank file.

The tests an agent can read are not the tests it is graded on. Each task's
`hidden` map is written over a copy of the scratch dir at scoring time, and
the runner runs there:

  * the visible suite is a superset's shadow — the hidden one adds the edge
    cases the prompt never mentioned, so "make these four asserts pass" and
    "write the function" come apart on the board;
  * an agent that deletes or rewrites a test to make it green is graded by
    the copy it never saw, and scores what its implementation actually earns.

Budgets are larger than the other suites' because a real coding loop is read,
write, run, fix — four steps before anything is even wrong yet.
"""


# ── task 1: stubs + a suite that already says what they must do ──────
STATS_SRC = '''"""Summary statistics. Every function here is a stub."""


def mean(xs):
    raise NotImplementedError


def median(xs):
    raise NotImplementedError


def mode(xs):
    """The most common value. Ties break toward the smallest value."""
    raise NotImplementedError
'''

STATS_VISIBLE = '''import pytest
from stats import mean, median, mode


def test_mean():
    assert mean([1, 2, 3, 4]) == 2.5


def test_median_odd():
    assert median([5, 1, 3]) == 3


def test_median_even():
    assert median([4, 1, 3, 2]) == 2.5


def test_mode():
    assert mode([1, 2, 2, 3]) == 2
'''

# the graded suite: the visible cases, plus what the prompt never spelled out
STATS_HIDDEN = STATS_VISIBLE + '''

def test_mean_of_one():
    assert mean([7]) == 7


def test_median_is_not_the_middle_of_the_input_order():
    assert median([9, 1, 8, 2]) == 5.0


def test_mode_ties_break_low():
    assert mode([3, 3, 1, 1, 2]) == 1


def test_empty_raises():
    with pytest.raises(ValueError):
        mean([])
'''


# ── task 2: one failing test, one bug ────────────────────────────────
DUR_SRC = '''"""Parse a duration like "1h30m" or "45s" into seconds."""

UNITS = {"h": 3600, "m": 60, "s": 1}


def parse(text):
    total = 0
    number = ""
    for ch in text.strip():
        if ch.isdigit():
            number += ch
            continue
        if ch not in UNITS:
            raise ValueError(f"unknown unit: {ch}")
        if not number:
            raise ValueError("a unit needs a number in front of it")
        # the bug lives here
        total = int(number) * UNITS[ch]
        number = ""
    if number:
        raise ValueError("trailing number with no unit")
    return total
'''

DUR_VISIBLE = '''import pytest
from duration import parse


def test_single_unit():
    assert parse("45s") == 45


def test_two_units():
    assert parse("1h30m") == 5400
'''

DUR_HIDDEN = DUR_VISIBLE + '''

def test_three_units():
    assert parse("2h5m10s") == 7510


def test_order_does_not_matter_to_the_sum():
    assert parse("30m1h") == 5400


def test_unknown_unit_raises():
    with pytest.raises(ValueError):
        parse("10x")


def test_number_with_no_unit_raises():
    with pytest.raises(ValueError):
        parse("10m5")
'''


# ── task 3: a spec, and nothing to read ──────────────────────────────
ROMAN_HIDDEN = '''import pytest
from roman import to_roman


@pytest.mark.parametrize("n,want", [
    (1, "I"), (3, "III"), (4, "IV"), (9, "IX"), (14, "XIV"),
    (40, "XL"), (90, "XC"), (400, "CD"), (900, "CM"),
    (1987, "MCMLXXXVII"), (2024, "MMXXIV"), (3999, "MMMCMXCIX"),
])
def test_converts(n, want):
    assert to_roman(n) == want


def test_zero_raises():
    with pytest.raises(ValueError):
        to_roman(0)


def test_too_big_raises():
    with pytest.raises(ValueError):
        to_roman(4000)
'''


# ── task 4: add a feature, keep the old ones ─────────────────────────
CART_SRC = '''"""A shopping cart. The tests next to it all pass — keep it that way."""


class Cart:
    def __init__(self):
        self.items = []

    def add(self, name, price, qty=1):
        if qty < 1:
            raise ValueError("qty must be at least 1")
        self.items.append({"name": name, "price": price, "qty": qty})

    def subtotal(self):
        return sum(i["price"] * i["qty"] for i in self.items)

    def total(self):
        return round(self.subtotal(), 2)
'''

CART_VISIBLE = '''import pytest
from cart import Cart


def test_subtotal():
    c = Cart()
    c.add("pen", 1.5, 2)
    c.add("pad", 4.0)
    assert c.subtotal() == 7.0


def test_total_rounds():
    c = Cart()
    c.add("tea", 0.333, 3)
    assert c.total() == 1.0


def test_bad_qty_raises():
    c = Cart()
    with pytest.raises(ValueError):
        c.add("pen", 1.5, 0)
'''

CART_HIDDEN = CART_VISIBLE + '''

def test_percent_discount():
    c = Cart()
    c.add("pen", 10.0, 2)
    c.apply_discount("SAVE10")
    assert c.total() == 18.0


def test_discount_stacks_once():
    c = Cart()
    c.add("pen", 10.0)
    c.apply_discount("SAVE10")
    c.apply_discount("SAVE10")
    assert c.total() == 9.0


def test_unknown_code_raises():
    c = Cart()
    c.add("pen", 10.0)
    with pytest.raises(ValueError):
        c.apply_discount("NOPE")


def test_half_off():
    c = Cart()
    c.add("pen", 10.0, 3)
    c.apply_discount("HALF")
    assert c.total() == 15.0
'''


PYTEST = "python -m pytest -q"


class Eval:
    name = "code/bench"
    description = ("Self-benchmarking coding tasks: the fixture ships the suite, "
                   "the score is the fraction of cases that pass, and the graded "
                   "suite is the one the agent never saw.")
    language = "python"
    owner = None
    agents = None  # every subject

    tasks = [
        {
            "title": "make the red suite green",
            "prompt": (
                "Your working directory is {workdir}. It holds stats.py, whose three "
                "functions are stubs, and test_stats.py, which says what they must do. "
                "Implement them in stats.py so the suite passes. You can run it with "
                "`python3 -m pytest -q` in {workdir}. Do not change the tests — you are "
                "graded on a copy of them. Then finish."
            ),
            "steps": 14,
            "setup": {"files": {"stats.py": STATS_SRC, "test_stats.py": STATS_VISIBLE}},
            "scorers": [
                {"type": "tests", "cmd": PYTEST, "hidden": {"test_stats.py": STATS_HIDDEN}},
                # the stubs have to be gone, not merely passing by accident
                {"type": "file_not_contains", "path": "stats.py", "text": "NotImplementedError"},
            ],
        },
        {
            "title": "the failing test names the bug",
            "prompt": (
                "Your working directory is {workdir}. duration.py parses strings like "
                "\"1h30m\" into seconds and one of the two tests in test_duration.py "
                "fails. Run `python3 -m pytest -q` there, find the bug, fix duration.py "
                "and leave the tests alone. Then finish."
            ),
            "steps": 12,
            "setup": {"files": {"duration.py": DUR_SRC, "test_duration.py": DUR_VISIBLE}},
            "scorers": [
                {"type": "tests", "cmd": PYTEST, "hidden": {"test_duration.py": DUR_HIDDEN}},
                # the fix is one operator; deleting the guard clauses is not a fix
                {"type": "file_regex", "path": "duration.py", "pattern": r"unknown unit"},
            ],
        },
        {
            "title": "write it from the spec alone",
            "prompt": (
                "Your working directory is {workdir}, and it is empty. Write roman.py "
                "there with one function, to_roman(n), returning the Roman numeral for "
                "an integer n: to_roman(4) == 'IV', to_roman(1987) == 'MCMLXXXVII'. "
                "Raise ValueError for anything outside 1..3999. There are no tests to "
                "read here — a hidden suite will be run against your file. Then finish."
            ),
            "steps": 12,
            "scorers": [
                {"type": "tests", "cmd": PYTEST, "hidden": {"test_roman.py": ROMAN_HIDDEN}},
            ],
        },
        {
            "title": "add a feature without breaking the old ones",
            "prompt": (
                "Your working directory is {workdir}. cart.py has a Cart class and "
                "test_cart.py passes today. Add an apply_discount(code) method: "
                "'SAVE10' takes 10% off the total, 'HALF' takes 50% off, any other code "
                "raises ValueError, and applying the same code twice applies it twice. "
                "Every test that passes now must still pass. Run `python3 -m pytest -q` "
                "to check, then finish."
            ),
            "steps": 14,
            "setup": {"files": {"cart.py": CART_SRC, "test_cart.py": CART_VISIBLE}},
            "scorers": [
                {"type": "tests", "cmd": PYTEST, "hidden": {"test_cart.py": CART_HIDDEN}},
                {"type": "file_regex", "path": "cart.py", "pattern": r"def\s+apply_discount"},
            ],
        },
    ]
