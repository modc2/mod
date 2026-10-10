"""jsondiff — two near-identical documents; name the path where they differ.

The third leg of the JSON pack. jsonpath asks whether a document can be read,
jsonfix whether one can be written; this asks whether two can be *compared* —
which is what an agent does every time it checks a config against a baseline
or a response against a fixture. Eyeballing two forty-line documents finds the
loud difference and misses the quiet one, so every round here changes exactly
one leaf, somewhere a grep for the key would find three other copies of it.

The answer is the dotted path to the changed value — the same path language
jsonpath drills, because reading a document and naming a place in it are one
skill. Grading is relaxed about prose around the path and strict about the
path itself.

    m arena/upload path=jsondiff.py
    m arena/play game=jsondiff players=calc,guess
"""

import json
import random

ROUNDS = 6

FIRST = ["ada", "brik", "cyn", "dov", "esa", "fen", "gil", "hux", "ivo", "jun",
         "kai", "lum", "mira", "nox", "opal", "pyre", "quin", "rey"]
TEAM = ["signals", "ledger", "atlas", "harbour", "kiln", "vector", "orchard"]
DOMAIN = ["mod.example", "orbit.example", "arena.example"]


class JsonDiff:
    """One document twice, one leaf changed. The answer is where."""

    name = "jsondiff"
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
        self.rounds = [self.make() for _ in range(ROUNDS)]

    # ── the documents ────────────────────────────────────────────────────

    def build(self):
        """Same shape as jsonpath's document, and for the same reason: `name`,
        `email` and `hours` each appear at several depths, so the changed one
        has to be *located*, not just recognised."""
        pick = self.rng.choice
        teams = []
        for i in range(3):
            members = [{
                "name": pick(FIRST),
                "email": f"{pick(FIRST)}{self.rng.randint(10, 99)}@{pick(DOMAIN)}",
                "hours": self.rng.randint(4, 40),
                "active": bool(self.rng.getrandbits(1)),
            } for _ in range(self.rng.randint(2, 4))]
            teams.append({
                "name": TEAM[i],
                "budget": self.rng.randrange(1000, 90000, 50),
                "lead": {"name": pick(FIRST),
                         "email": f"lead@{pick(DOMAIN)}",
                         "hours": self.rng.randint(20, 50)},
                "members": members,
            })
        return {
            "org": {"name": "modco", "email": f"hello@{pick(DOMAIN)}"},
            "teams": teams,
            "meta": {"version": self.rng.randint(2, 40),
                     "email": f"ops@{pick(DOMAIN)}"},
        }

    def leaves(self, node, path=""):
        """Every dotted path to a scalar, so the mutation can land anywhere."""
        out = []
        if isinstance(node, dict):
            for k, v in node.items():
                out.extend(self.leaves(v, f"{path}.{k}" if path else k))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                out.extend(self.leaves(v, f"{path}.{i}"))
        else:
            out.append((path, node))
        return out

    def mutate(self, value):
        """A changed value of the same type — a diff that changes a string to
        a number is visible from orbit and drills nothing."""
        if isinstance(value, bool):
            return not value
        if isinstance(value, int):
            return value + self.rng.randint(1, 9)
        return f"{self.rng.choice(FIRST)}{self.rng.randint(10, 99)}@{self.rng.choice(DOMAIN)}" \
            if "@" in str(value) else self.rng.choice([w for w in FIRST + TEAM if w != value])

    def make(self):
        doc = self.build()
        path, old = self.rng.choice(self.leaves(doc))
        changed = json.loads(json.dumps(doc))
        node = changed
        parts = path.split(".")
        for part in parts[:-1]:
            node = node[int(part)] if part.isdigit() else node[part]
        last = parts[-1]
        new = self.mutate(old)
        if last.isdigit():
            node[int(last)] = new
        else:
            node[last] = new
        return doc, changed, path

    # ── the game ─────────────────────────────────────────────────────────

    def turn(self):
        return list(range(self.players[1]))

    def view(self, seat):
        if self.round >= ROUNDS:
            return self.over(seat)
        before, after, _ = self.rounds[self.round]
        return "\n".join([
            f"DRILL jsondiff — round {self.round + 1} of {ROUNDS}",
            f"Seat {seat}. Correct so far: {self.correct.get(seat, 0)} of {self.round}.",
            "",
            "Document A:",
            json.dumps(before, indent=2, sort_keys=True),
            "",
            "Document B:",
            json.dumps(after, indent=2, sort_keys=True),
            "",
            "Question: exactly one value differs between A and B. Answer with",
            "the dotted path to it — a number in the path indexes a list,",
            "counting from 0, e.g. teams.1.members.2.email",
            "Legal moves: a dotted path",
        ])

    def over(self, seat):
        """A view is still asked for after the last round — the runner shows
        every seat where the drill left it."""
        return "\n".join([
            "DRILL jsondiff — over",
            f"Seat {seat}. Correct so far: {self.correct.get(seat, 0)} of {ROUNDS}.",
        ])

    def step(self, moves):
        _, _, want = self.rounds[self.round]
        legal, said = {}, []
        seats = sorted({int(k) for k in moves})
        self.seen.update(seats)
        for seat in seats:
            given = read_path(str(moves.get(seat, "")))
            legal[seat] = given is not None
            if given == want:
                self.correct[seat] = self.correct.get(seat, 0) + 1
            said.append(f"seat {seat}: {'—' if given is None else given}")

        self.round += 1
        return {**legal, "note": f"changed at {want} · " + ", ".join(said)}

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
# The pack's rule: strict about the path, relaxed about what surrounds it.
# "The difference is at teams.1.budget." has answered the question.

def read_path(reply):
    """The last thing in the reply that looks like a dotted path, or None."""
    best = None
    for word in reply.split():
        w = word.strip('`"\'.,;:()[]')
        if w and all(p and (p.isdigit() or p.replace("_", "").isalnum())
                     for p in w.split(".")):
            if "." in w or best is None:
                best = w
    return best
