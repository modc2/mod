"""jsontypes — six questions about the shape of one JSON document.

The structural half of the JSON pack. jsonpath asks for a value, jsondiff for
a location; this asks about the document itself — what type sits at a path,
how long an array is, how many keys an object holds. It is the question an
agent answers implicitly every time it writes `for item in response.teams`:
get the shape wrong and the value never mattered.

The document nests arrays in objects in arrays, and the drill deliberately
asks about places where the type is a near miss — a `"42"` that is a string,
a one-element array, an empty object — because those are the shapes that
break real code.

    m arena/upload path=jsontypes.py
    m arena/play game=jsontypes players=calc,guess
"""

import json
import random

ROUNDS = 6

WORDS = ["harbour", "kiln", "atlas", "signal", "ledger", "vector", "orchard",
         "beacon", "quarry", "mantle"]
TYPES = ("object", "array", "string", "number", "boolean", "null")


class JsonTypes:
    """One document, six questions about its shape rather than its values."""

    name = "jsontypes"
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
        self.doc = self.build()
        self.asks = self.questions()

    # ── the document ─────────────────────────────────────────────────────

    def build(self):
        """Every JSON type somewhere, and the traps on purpose: a number in
        quotes, a bare number, an empty object, arrays of unequal length."""
        pick = self.rng.choice
        rows = [{
            "id": pick(WORDS),
            "count": self.rng.randint(0, 90),
            # the trap: shaped like a number, typed like a string
            "code": str(self.rng.randint(100, 999)),
            "flags": [pick(WORDS) for _ in range(self.rng.randint(0, 3))],
            "extra": pick([None, {}, {"note": pick(WORDS)}, pick(WORDS)]),
        } for _ in range(self.rng.randint(3, 5))]
        return {
            "batch": pick(WORDS),
            "ok": bool(self.rng.getrandbits(1)),
            "rows": rows,
            "totals": {"count": sum(r["count"] for r in rows),
                       "checked": self.rng.randint(0, len(rows))},
        }

    def at(self, path):
        node = self.doc
        for part in path.split("."):
            node = node[int(part)] if part.isdigit() else node[part]
        return node

    @staticmethod
    def type_of(value):
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, (int, float)):
            return "number"
        if isinstance(value, str):
            return "string"
        if isinstance(value, list):
            return "array"
        return "object"

    def questions(self):
        """Six (question, answer, kind) rounds: four types, a length, a key
        count. The type questions aim at the traps by name."""
        rows = self.doc["rows"]
        r = self.rng.randrange(len(rows))
        extra_row = max(range(len(rows)),
                        key=lambda i: 0 if rows[i]["extra"] is None else 1) \
            if any(row["extra"] is not None for row in rows) else r
        qs = [
            (f"the type of the value at rows.{r}.code",
             self.type_of(rows[r]["code"]), "type"),
            (f"the type of the value at rows.{r}.count",
             self.type_of(rows[r]["count"]), "type"),
            (f"the type of the value at rows.{extra_row}.extra",
             self.type_of(rows[extra_row]["extra"]), "type"),
            ("the type of the value at totals",
             self.type_of(self.doc["totals"]), "type"),
            (f"how many elements the array at rows.{r}.flags holds",
             str(len(rows[r]["flags"])), "number"),
            ("how many keys the object at rows.0 holds",
             str(len(rows[0])), "number"),
        ]
        self.rng.shuffle(qs)
        return qs

    # ── the game ─────────────────────────────────────────────────────────

    def turn(self):
        return list(range(self.players[1]))

    def view(self, seat):
        if self.round >= ROUNDS:
            return self.over(seat)
        question, _, kind = self.asks[self.round]
        shape = {"type": "one of: object, array, string, number, boolean, null",
                 "number": "the number alone"}[kind]
        return "\n".join([
            f"DRILL jsontypes — round {self.round + 1} of {ROUNDS}",
            f"Seat {seat}. Correct so far: {self.correct.get(seat, 0)} of {self.round}.",
            "",
            "Document:",
            json.dumps(self.doc, indent=2, sort_keys=True),
            "",
            f"Question: {question}",
            "A path is dotted; a number in it indexes a list, counting from 0.",
            f"Answer with {shape}.",
            f"Legal moves: {shape}",
        ])

    def over(self, seat):
        """A view is still asked for after the last round — the runner shows
        every seat where the drill left it."""
        return "\n".join([
            "DRILL jsontypes — over",
            f"Seat {seat}. Correct so far: {self.correct.get(seat, 0)} of {ROUNDS}.",
        ])

    def step(self, moves):
        question, want, kind = self.asks[self.round]
        legal, said = {}, []
        seats = sorted({int(k) for k in moves})
        self.seen.update(seats)
        for seat in seats:
            reply = str(moves.get(seat, ""))
            given = (last_word(reply, TYPES) if kind == "type"
                     else last_number(reply))
            legal[seat] = given is not None
            if given is not None and str(given) == want:
                self.correct[seat] = self.correct.get(seat, 0) + 1
            said.append(f"seat {seat}: {'—' if given is None else given}")

        self.round += 1
        return {**legal, "note": f"{question} = {want} · " + ", ".join(said)}

    def done(self):
        return self.round >= ROUNDS

    def result(self):
        played = max(self.seen) + 1 if self.seen else 1
        scores = [self.correct.get(s, 0) for s in range(played)]
        return {
            "scores": scores,
            "summary": f"correct out of {ROUNDS}: "
                       + ", ".join(f"seat {s} {n}" for s, n in enumerate(scores)),
        }


# ── reading an answer ────────────────────────────────────────────────────
# The pack's rule: strict about producing an answer of the right kind,
# relaxed about what surrounds it.

def last_word(text, among):
    words = [w.strip('`"\'.,;:()[]').lower() for w in text.split()]
    for w in reversed(words):
        if w in among:
            return w
    return None


def last_number(text):
    found = None
    for word in text.replace(",", " ").split():
        w = word.strip('`"\'.,;:()[]')
        if w.lstrip("-").isdigit():
            found = str(int(w))
    return found
