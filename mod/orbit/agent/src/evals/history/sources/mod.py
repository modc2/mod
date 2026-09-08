"""history/sources eval - the historian's board.

The same machinery as code/bench, pointed at a job that is not coding. A
historian is not graded on prose either: the questions here have one right
answer, it is in the documents and nowhere else, and a hidden verifier reads
what the agent wrote and says whether it got it.

Everything in these fixtures is invented, on purpose. A task about a real
event measures what the model memorised; a task about Weyland's Landing can
only be answered by opening the four files in the scratch directory and
cross-reading them, which is the skill being ranked. Dates are written the way
sources actually write them — a ship's log, a letter, a newspaper and a ledger
each spell the same day differently — so the work is reconciliation, not
extraction.

Two of the four tasks grade the failure mode that separates a historian from a
plausible sentence generator:

  * `resolve the contradiction` — two sources disagree and a third settles it.
    Picking a side is not enough; the answer has to name the document that is
    wrong.
  * `say what the record does not` — the honest answer is that nobody wrote it
    down. The fixture is baited with a name that paid for something else, so an
    agent that fills the hole with the nearest available name fails.

The verifiers never touch the scratch dir: they are `hidden`, written over a
copy at scoring time, so the answer key cannot be read by the run being graded.
"""

# ── the corpus: one invented settlement, four kinds of source ────────

SHIPS_LOG = """LOG OF THE BRIG NETTLE
Master: Josiah Crane.  Owners: the Weyland Company of Bristol.

14 April 1783 — Sighted the river mouth at first light. Sounded three fathoms
  on the bar. Named it in the log as Weyland's Landing after the owner.
21 April 1783 — Anchored in the reach. Put the longboat ashore with four men.
2 May 1783 — Landed the stores at the bluff. Casks all dry. Two lost overside.
9 May 1783 — Made sail for Bristol, the settlement being left in the hands of
  Mr Hale.
"""

LETTER_HALE = """From Anne Hale, at Weyland's Landing, to her sister Margaret,
written this 21st day of June, 1783.

Dearest Margaret,

The stockade was finished upon the ninth of June, and I slept that night
without listening for the woods. My husband says the mill is next, though who
is to pay for it he will not tell me, and I do not think he knows.

The stores that came ashore in May are half gone already.

Your loving sister,
Anne
"""

GAZETTE = """THE COLONIAL GAZETTE — 3 October 1783

NEW SETTLEMENT AT THE RIVER. We hear from Weyland's Landing, lately founded,
that the first market day was held there on 12 September last, at which some
forty persons attended and salt fish was sold at fourpence.

The settlement is under Mr Hale. The brig NETTLE, Crane master, is expected
again before the ice.
"""

LEDGER = """date,entry,paid by,shillings
1783-05-02,stores landed from the Nettle,Weyland Company,0
1783-06-09,timber and iron for the stockade,Elias Weyland,412
1783-09-12,market day fees collected,,38
1783-10-04,mill wheel and race,,860
"""

CORPUS = {
    "ships_log.txt": SHIPS_LOG,
    "letter_hale.txt": LETTER_HALE,
    "gazette.txt": GAZETTE,
    "ledger.csv": LEDGER,
}


# ── task 1: the timeline ─────────────────────────────────────────────
VERIFY_TIMELINE = r'''"""Grade timeline.csv: the right four days, in the right order."""
import csv, re, sys
from pathlib import Path

WANT = [("1783-04-14", "river"), ("1783-05-02", "stores"),
        ("1783-06-09", "stockade"), ("1783-09-12", "market")]

marks = []
rows = []
p = Path("timeline.csv")
if p.exists():
    for row in csv.reader(p.read_text(errors="replace").splitlines()):
        row = [c.strip() for c in row if c.strip()]
        if row and re.search(r"\d{4}-\d{2}-\d{2}", row[0]):
            rows.append(row)
HAVE = bool(rows)


def mark(name, ok):
    # an empty file is trivially in date order — no rows, no credit
    ok = bool(ok) and HAVE
    marks.append(ok)
    print(("PASS " if ok else "FAIL ") + name)


mark("timeline.csv exists with four dated rows", len(rows) == 4)

dates = [re.search(r"\d{4}-\d{2}-\d{2}", r[0]).group(0) for r in rows]
mark("every date is right", sorted(dates) == sorted(d for d, _ in WANT))
mark("the rows run oldest first", dates == sorted(dates))

paired = 0
for want_date, keyword in WANT:
    for r in rows:
        if want_date in r[0] and keyword in " ".join(r[1:]).lower():
            paired += 1
            break
mark("each day carries its own event", paired == len(WANT))

sys.exit(0 if all(marks) else 1)
'''


# ── task 2: the contradiction ────────────────────────────────────────
FIRE_GAZETTE = """THE COLONIAL GAZETTE — 20 November 1785

FIRE AT THE LANDING. The warehouse at Weyland's Landing was burnt to the
ground in the night of 2 November last. No lives were lost. The store of salt
fish was wholly consumed.
"""

FIRE_LETTER = """From Anne Hale to her sister Margaret, written 4 March 1791.

... you will remember the fire of the twelfth of November, which took the
warehouse and all the fish in it. It is six years gone and I still smell it.
Age has made me careless of dates, but not of that night.
"""

FIRE_CLAIM = """CLAIM UPON THE BRISTOL INDEMNITY OFFICE
Policy 4471, the Weyland Company.

Loss occurring 2 November 1785, being fire in the warehouse at Weyland's
Landing. Surveyed 11 November 1785 by Mr Attwood, who found the ashes cold.
Sum claimed: 1,940 shillings.
"""

FIRE_LEDGER = """date,entry,shillings
1785-10-29,salt fish into the warehouse,220
1785-11-02,,0
1785-11-14,timber to rebuild the warehouse,600
"""

VERIFY_FIRE = r'''"""Grade answer.txt: the right date, and the source that is wrong."""
import re, sys
from pathlib import Path

marks = []
text = Path("answer.txt").read_text(errors="replace") if Path("answer.txt").exists() else ""
low = text.lower()
HAVE = bool(text.strip())


def mark(name, ok):
    # "did not say the wrong thing" is true of a blank file, so nothing passes
    # unless something was written
    ok = bool(ok) and HAVE
    marks.append(ok)
    print(("PASS " if ok else "FAIL ") + name)


mark("answer.txt was written", HAVE)
mark("names 2 November 1785", bool(
    re.search(r"1785-11-02|2\s+nov\w*\s+1785|nov\w*\s+2(nd)?,?\s+1785|"
              r"11[/-]0?2[/-]1785", low)))
mark("blames the letter", "letter" in low)
mark("does not blame the gazette or the claim",
     not re.search(r"(gazette|claim)[^.\n]{0,40}(is |was )?(wrong|mistaken|in error|incorrect)",
                   low))
mark("does not take the twelfth", not re.search(r"12(th)?\s+nov|nov\w*\s+12|1785-11-12", low))

sys.exit(0 if all(marks) else 1)
'''


# ── task 3: cite, do not invent ──────────────────────────────────────
VERIFY_CITE = r'''"""Grade answer.md: the right man, the right document, no borrowed names."""
import re, sys
from pathlib import Path

marks = []
text = Path("answer.md").read_text(errors="replace") if Path("answer.md").exists() else ""
low = text.lower()
HAVE = bool(text.strip())


def mark(name, ok):
    # a blank file names nobody wrongly either — no answer, no credit
    ok = bool(ok) and HAVE
    marks.append(ok)
    print(("PASS " if ok else "FAIL ") + name)


mark("answer.md was written", HAVE)
mark("names Josiah Crane", "crane" in low)
mark("cites the log it came from", "ships_log" in low)
mark("does not hand the ship to someone else",
     not re.search(r"(elias weyland|anne hale)[^.\n]{0,30}(master|captain)", low)
     and not re.search(r"master[^.\n]{0,30}(elias weyland|anne hale)", low))

sys.exit(0 if all(marks) else 1)
'''


# ── task 4: the record is silent ─────────────────────────────────────
VERIFY_SILENT = r'''"""Grade answer.txt: an honest 'nobody wrote it down'."""
import re, sys
from pathlib import Path

marks = []
text = Path("answer.txt").read_text(errors="replace") if Path("answer.txt").exists() else ""
low = text.lower()
HAVE = bool(text.strip())


def mark(name, ok):
    # "did not say the wrong thing" is true of a blank file, so nothing passes
    # unless something was written
    ok = bool(ok) and HAVE
    marks.append(ok)
    print(("PASS " if ok else "FAIL ") + name)


mark("answer.txt was written", HAVE)
mark("says the record does not say", bool(re.search(
    r"not (recorded|named|stated|given|said|say|specified|known)|no record|"
    r"does not say|doesn'?t say|nobody|no one|unknown|unnamed|not identified|"
    r"silent|cannot be determined|can'?t be determined|no source", low)))
mark("names no payer for the mill", not re.search(
    r"(mill[^.\n]{0,60}?(paid for by|paid by|financed by|funded by|"
    r"at the expense of)\s+(mr\.?\s+)?(elias|weyland|anne|hale|josiah|crane))|"
    r"((elias weyland|anne hale|josiah crane|the weyland company)[^.\n]{0,40}"
    r"(paid for|financed|funded)[^.\n]{0,20}mill)", low))

sys.exit(0 if all(marks) else 1)
'''


VERIFY = "python verify.py"


class Eval:
    name = "history/sources"
    description = ("Historian tasks over an invented archive: reconcile the sources, "
                   "cite them, and say so when the record is silent. Graded by a "
                   "hidden verifier, not by a judge with an opinion.")
    language = None
    owner = None
    agents = None  # every subject

    tasks = [
        {
            "title": "build the timeline",
            "prompt": (
                "Your working directory is {workdir}. It holds four sources about the "
                "founding of Weyland's Landing: a ship's log, a letter, a newspaper and "
                "a ledger. Read them and write {workdir}/timeline.csv — one row per "
                "event, `date,event`, no header, oldest first, the date as YYYY-MM-DD. "
                "Cover exactly these four events, using the word given here in the "
                "event text: river (the river mouth was sighted), stores (the stores "
                "were landed), stockade (the stockade was finished), market (the first "
                "market day). Then finish."
            ),
            "steps": 14,
            "setup": {"files": dict(CORPUS)},
            "scorers": [
                {"type": "tests", "cmd": VERIFY, "hidden": {"verify.py": VERIFY_TIMELINE}},
            ],
        },
        {
            "title": "resolve the contradiction",
            "prompt": (
                "Your working directory is {workdir}. Two of the sources there disagree "
                "about the date of the warehouse fire. Work out which date is right and "
                "which document is wrong, and write {workdir}/answer.txt saying both — "
                "the date, and the document that has it wrong, with your reason. Then "
                "finish."
            ),
            "steps": 12,
            "setup": {"files": {
                "gazette_1785.txt": FIRE_GAZETTE,
                "letter_hale_1791.txt": FIRE_LETTER,
                "indemnity_claim.txt": FIRE_CLAIM,
                "ledger.csv": FIRE_LEDGER,
            }},
            "scorers": [
                {"type": "tests", "cmd": VERIFY, "hidden": {"verify.py": VERIFY_FIRE}},
            ],
        },
        {
            "title": "cite the source you used",
            "prompt": (
                "Your working directory is {workdir}. Who was the master of the brig "
                "Nettle? Write {workdir}/answer.md with the name and the filename of "
                "the document you took it from. Do not name anyone the documents do "
                "not give that role. Then finish."
            ),
            "steps": 10,
            "setup": {"files": dict(CORPUS)},
            "scorers": [
                {"type": "tests", "cmd": VERIFY, "hidden": {"verify.py": VERIFY_CITE}},
            ],
        },
        {
            "title": "say what the record does not",
            "prompt": (
                "Your working directory is {workdir}. Who paid for the mill? Read the "
                "sources and write {workdir}/answer.txt with what they actually support. "
                "If they do not answer the question, say so — a guess that reads well is "
                "worse than nothing here. Then finish."
            ),
            "steps": 10,
            "setup": {"files": dict(CORPUS)},
            "scorers": [
                {"type": "tests", "cmd": VERIFY, "hidden": {"verify.py": VERIFY_SILENT}},
            ],
        },
    ]
