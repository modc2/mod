"""jsonpath — six questions about one JSON document. Can it actually parse?

Reading JSON and searching JSON look the same until the document has the same
key at three different depths, which is why this one is built the way it is:
every generated document repeats `name` and `email` on the org, on each team's
lead and on each member, so an answerer that grabs the first `"email"` it sees
is reliably wrong, and one that walks `teams.1.members.2.email` is reliably
right. The drill is about the walk, not the grep.

Same view format as the rest of the pack (see addup.py). The document is in the
view, so nothing has to be fetched and the question is answerable from the text
alone — which is the arena's rule for every seat: a move is a function of the
view it was given.

    m arena/upload path=jsonpath.py
    m arena/play game=jsonpath players=calc,guess
"""

import json
import random

ROUNDS = 6

FIRST = ["ada", "brik", "cyn", "dov", "esa", "fen", "gil", "hux", "ivo", "jun",
         "kai", "lum", "mira", "nox", "opal", "pyre", "quin", "rey"]
TEAM = ["signals", "ledger", "atlas", "harbour", "kiln", "vector", "orchard"]
DOMAIN = ["mod.example", "orbit.example", "arena.example"]


class JsonPath:
    """One document, six paths into it. The answer is the value at the path."""

    name = "jsonpath"
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
                # a lead is shaped exactly like a member, one level up — this
                # is the collision the drill is built around
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

    def questions(self):
        """Six paths, fixed in shape and varying in target, so every match asks
        the same *kinds* of question of a different document."""
        t = self.rng.randrange(3)
        deep = self.doc["teams"][t]["members"]
        m = self.rng.randrange(len(deep))
        return [
            (f"teams.{t}.members.{m}.email", "text"),
            (f"teams.{t}.budget", "number"),
            (f"teams.{(t + 1) % 3}.lead.name", "text"),
            (f"teams.{t}.members.{m}.active", "bool"),
            (f"teams.{(t + 2) % 3}.members.0.hours", "number"),
            ("meta.version", "number"),
        ]

    def at(self, path):
        """Walk a dotted path. `teams.1.members.0.email` — a number is an index
        into a list, anything else is a key."""
        node = self.doc
        for part in path.split("."):
            node = node[int(part)] if part.isdigit() else node[part]
        return node

    # ── the game ─────────────────────────────────────────────────────────

    def turn(self):
        return list(range(self.players[1]))

    def view(self, seat):
        if self.round >= ROUNDS:
            return self.over(seat)
        path, kind = self.asks[self.round]
        shape = {"text": "the value alone, unquoted",
                 "number": "the number alone",
                 "bool": "`true` or `false`"}[kind]
        return "\n".join([
            f"DRILL jsonpath — round {self.round + 1} of {ROUNDS}",
            f"Seat {seat}. Correct so far: {self.correct.get(seat, 0)} of {self.round}.",
            "",
            "Document:",
            json.dumps(self.doc, indent=2, sort_keys=True),
            "",
            f"Question: value at {path}",
            "A path is dotted; a number in it indexes a list, counting from 0.",
            f"Answer with {shape}.",
            f"Legal moves: {shape}",
        ])

    def over(self, seat):
        """Where this seat ended up.

        A view is still asked for after the last round — the runner shows every
        seat where the drill left it — so there has to be something to say that
        is not the seventh of six questions.
        """
        return "\n".join([
            f"DRILL jsonpath — over",
            f"Seat {seat}. Correct so far: {self.correct.get(seat, 0)} of {ROUNDS}.",
        ])

    def step(self, moves):
        path, kind = self.asks[self.round]
        want = self.at(path)
        legal, said = {}, []
        seats = sorted({int(k) for k in moves})
        self.seen.update(seats)
        for seat in seats:
            reply = str(moves.get(seat, ""))
            ok, given = matches(reply, want, kind)
            legal[seat] = given is not None
            if ok:
                self.correct[seat] = self.correct.get(seat, 0) + 1
            said.append(f"seat {seat}: {'—' if given is None else given}")

        self.round += 1
        return {**legal, "note": f"{path} = {json.dumps(want)} · " + ", ".join(said)}

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
# The rule across this pack: strict about producing an answer of the right
# kind, relaxed about what surrounds it. "The email is bo12@mod.example." has
# answered the question, and marking it down measures manners, not parsing.

def matches(reply, want, kind):
    """(was it right, what we read) — `None` read back means nothing legible."""
    text = reply.strip().strip("`").strip()
    if not text:
        return False, None
    if kind == "number":
        given = last_number(text)
        return (given is not None and float(given) == float(want)), given
    if kind == "bool":
        given = last_word(text, ("true", "false", "yes", "no"))
        if given is None:
            return False, None
        return (given in ("true", "yes")) == bool(want), given
    given = text.strip('"').strip()
    if given == str(want):
        return True, given
    tail = text.split()[-1].strip('".,;:()[]`')
    return tail == str(want), tail


def last_number(text):
    cleaned = text.replace(",", "")
    found, i = None, 0
    while i < len(cleaned):
        if cleaned[i].isdigit():
            j = i
            while j < len(cleaned) and (cleaned[j].isdigit() or
                                        (cleaned[j] == "." and j + 1 < len(cleaned)
                                         and cleaned[j + 1].isdigit())):
                j += 1
            sign = -1 if i and cleaned[i - 1] == "-" else 1
            found = sign * float(cleaned[i:j]) if "." in cleaned[i:j] else sign * int(cleaned[i:j])
            i = j
        else:
            i += 1
    return found


def last_word(text, among):
    words = [w.strip('".,;:()[]`').lower() for w in text.split()]
    for w in reversed(words):
        if w in among:
            return w
    return None
