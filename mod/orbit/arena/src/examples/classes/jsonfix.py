"""jsonfix — six broken JSON literals to repair. Can it emit valid JSON?

The other half of parsing. jsonpath asks whether a document can be read;
this asks whether one can be *written* — which is the half that actually breaks
things, because an agent whose tool call is a JSON object emits JSON on every
turn and a single quote in the wrong place costs the whole step.

Each round shows a literal broken exactly one way — single quotes, a trailing
comma, unquoted keys, Python's `True`/`None`, a missing brace, a `//` comment —
and asks for JSON that parses to what was obviously meant. Grading is not a
diff: the answer is parsed and compared to the intended object, so any valid
spelling of the right value passes and pretty-printing is free.

The breakages are the six that show up in real output, in a fixed order, so a
match is a fair comparison and a failure names the habit that caused it.

    m arena/upload path=jsonfix.py
    m arena/play game=jsonfix players=calc,guess
"""

import json
import random

ROUNDS = 6

WORDS = ["harbour", "kiln", "atlas", "signal", "ledger", "vector", "orchard",
         "beacon", "quarry", "mantle"]

# the fault in each round, in order — named in the transcript when a seat misses
FAULTS = ["single quotes", "trailing comma", "unquoted keys",
          "python literals", "missing brace", "// comment"]


class JsonFix:
    """Broken JSON in, valid JSON out. Judged on what it parses to."""

    name = "jsonfix"
    # A literal, because the registry reads this file with a scanner rather
    # than an interpreter: a name here is one it cannot resolve, and the
    # seat count would quietly become somebody's default.
    players = [1, 4]
    max_turns = ROUNDS

    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.round = 0
        self.correct = {}
        self.seen = set()
        self.target = self.build()

    # ── the object, and the six ways to spoil it ─────────────────────────

    def build(self):
        pick = self.rng.choice
        return {
            "id": self.rng.randint(100, 999),
            "name": f"{pick(WORDS)}-{pick(WORDS)}",
            "tags": [pick(WORDS) for _ in range(self.rng.randint(2, 3))],
            "active": bool(self.rng.getrandbits(1)),
            # quarter steps only: every value here is exact in binary, so a
            # correct answer can never lose to floating-point dust
            "score": self.rng.randrange(1, 40) / 4,
            "owner": {"name": pick(WORDS), "seats": self.rng.randint(1, 6)},
        }

    def broken(self):
        text = json.dumps(self.target, indent=2, sort_keys=True)
        fault = FAULTS[self.round % len(FAULTS)]
        if fault == "single quotes":
            return text.replace('"', "'")
        if fault == "trailing comma":
            # one comma before the close of every block — the classic
            return text.replace("\n  ]", ",\n  ]").replace("\n}", ",\n}")
        if fault == "unquoted keys":
            out = []
            for line in text.splitlines():
                head, sep, tail = line.partition('": ')
                if sep and head.strip().startswith('"'):
                    out.append(head.replace('"', "", 1) + ": " + tail)
                else:
                    out.append(line)
            return "\n".join(out)
        if fault == "python literals":
            return (text.replace("true", "True").replace("false", "False")
                        .replace("null", "None"))
        if fault == "missing brace":
            return text.rstrip().rstrip("}").rstrip()
        return "// the config, as pasted from the notes\n" + text

    # ── the game ─────────────────────────────────────────────────────────

    def turn(self):
        return list(range(self.players[1]))

    def view(self, seat):
        if self.round >= ROUNDS:
            return self.over(seat)
        return "\n".join([
            f"DRILL jsonfix — round {self.round + 1} of {ROUNDS}",
            f"Seat {seat}. Correct so far: {self.correct.get(seat, 0)} of {self.round}.",
            "",
            "This is meant to be JSON and is not:",
            self.broken(),
            "",
            "Question: repair it — same data, valid JSON",
            "Change nothing but the syntax. Your answer is parsed and compared "
            "to the object that was meant, so spacing and key order are free.",
            "Answer with the JSON alone.",
            "Legal moves: one JSON object",
        ])

    def over(self, seat):
        """Where this seat ended up.

        A view is still asked for after the last round — the runner shows every
        seat where the drill left it — so there has to be something to say that
        is not the seventh of six questions.
        """
        return "\n".join([
            f"DRILL jsonfix — over",
            f"Seat {seat}. Correct so far: {self.correct.get(seat, 0)} of {ROUNDS}.",
        ])

    def step(self, moves):
        legal, said = {}, []
        seats = sorted({int(k) for k in moves})
        self.seen.update(seats)
        for seat in seats:
            given = json_in(str(moves.get(seat, "")))
            legal[seat] = given is not None
            if given == self.target:
                self.correct[seat] = self.correct.get(seat, 0) + 1
                said.append(f"seat {seat}: ok")
            elif given is None:
                said.append(f"seat {seat}: no JSON")
            else:
                said.append(f"seat {seat}: parsed, wrong data")

        fault = FAULTS[self.round % len(FAULTS)]
        self.round += 1
        if self.round < ROUNDS:
            self.target = self.build()
        return {**legal, "note": f"fault was {fault} · " + ", ".join(said)}

    def done(self):
        return self.round >= ROUNDS

    def result(self):
        played = max(self.seen) + 1 if self.seen else 1
        scores = [self.correct.get(s, 0) for s in range(played)]
        return {
            "scores": scores,
            "summary": f"repaired {ROUNDS} literals: "
                       + ", ".join(f"seat {s} {n}" for s, n in enumerate(scores)),
        }


def json_in(reply):
    """The JSON object in a reply, or None.

    The first brace-balanced span in the text, parsed. Prose and code fences
    around the object are forgiven — an answer that says "here you go:" and
    then hands over correct JSON has repaired the JSON — but the span itself
    has to parse: a repair that is still broken is not a repair.
    """
    text = reply.strip()
    start = text.find("{")
    if start < 0:
        return None
    depth, in_string, escaped = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except ValueError:
                    return None
    return None
