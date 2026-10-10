"""addup — six sums, each longer than the last. Can it add?

A *drill* is a game whose questions are generated rather than played for: every
seat gets the same question at the same moment, answers it, and is graded on
the spot. Nobody can take a piece off anybody, so a seat's score is a count of
right answers instead of a win — which is exactly the shape to sit a model or
an agent in, because what comes out the other end is accuracy on a skill and
not a story about a board.

The five drills in this pack share one view format, so one bot, one prompt and
one bridge can play all of them:

    DRILL <name> — round R of N
    Seat S. Correct so far: c of n.

    <whatever the question needs to be answerable — a document, a table>

    Question: <one line>
    Answer with <what a good answer looks like>.
    Legal moves: <the same thing again, for a player that reads only this line>

An answer that cannot be read as the right *kind* of thing is illegal and lands
on the seat's illegal rate. An answer that is well-formed and wrong is legal
and simply scores nothing. Those are two different failures — "did not follow
the format" and "cannot add" — and the board keeps them apart.

One more property, which everything built on these drills leans on: the
questions are a function of the seed alone and never of the answers. Round five
asks the same thing whether round four was right, wrong or left blank. So a
table can be replayed forward to any round without having to be played well,
and two seats compared on a drill were compared on the same six questions.

    m arena/upload path=addup.py
    m arena/play game=addup players=calc,guess
"""

import random

ROUNDS = 6


class AddUp:
    """Sum a list of integers. Longer lists and bigger numbers each round."""

    name = "addup"
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
        self.numbers = self.deal(0)

    # ── the questions ────────────────────────────────────────────────────
    # Deterministic in the seed, so the same match replays identically and two
    # players compared on this drill were compared on the same six sums.

    def deal(self, r):
        """Round r: r+3 numbers, wider as r grows, signed from round 3 on."""
        top = 10 ** (2 + r // 2)
        low = -top if r >= 3 else 1
        return [self.rng.randint(low, top) for _ in range(r + 3)]

    def question(self):
        text = str(self.numbers[0])
        for n in self.numbers[1:]:
            text += f" - {abs(n)}" if n < 0 else f" + {n}"
        return text

    def answer(self):
        return sum(self.numbers)

    # ── the game ─────────────────────────────────────────────────────────

    def turn(self):
        """Everyone answers at once — the arena drops seats that aren't there,
        so the same list is right whether one player showed up or four."""
        return list(range(self.players[1]))

    def view(self, seat):
        if self.round >= ROUNDS:
            return self.over(seat)
        got = self.correct.get(seat, 0)
        return "\n".join([
            f"DRILL addup — round {self.round + 1} of {ROUNDS}",
            f"Seat {seat}. Correct so far: {got} of {self.round}.",
            "",
            f"Question: {self.question()}",
            "Answer with the total alone.",
            "Legal moves: one integer, e.g. `-4213`",
        ])

    def over(self, seat):
        """Where this seat ended up.

        A view is still asked for after the last round — the runner shows every
        seat where the drill left it — so there has to be something to say that
        is not the seventh of six questions.
        """
        return "\n".join([
            f"DRILL addup — over",
            f"Seat {seat}. Correct so far: {self.correct.get(seat, 0)} of {ROUNDS}.",
        ])

    def step(self, moves):
        want = self.answer()
        legal, said = {}, []
        seats = sorted({int(k) for k in moves})
        self.seen.update(seats)
        for seat in seats:
            given = number_in(str(moves.get(seat, "")))
            legal[seat] = given is not None
            if given == want:
                self.correct[seat] = self.correct.get(seat, 0) + 1
            said.append(f"seat {seat}: {'—' if given is None else given}")

        self.round += 1
        if self.round < ROUNDS:
            self.numbers = self.deal(self.round)
        return {**legal, "note": f"= {want} · " + ", ".join(said)}

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


def number_in(text):
    """The last integer in a reply, or None if there isn't one.

    Strict about there being a number, relaxed about what surrounds it: a model
    that says "the total is 1,204." has answered, and pretending otherwise
    measures its manners rather than its arithmetic. Commas inside digits go,
    because that is how people write thousands.
    """
    cleaned = text.replace(",", "")
    found = None
    i = 0
    while i < len(cleaned):
        if cleaned[i].isdigit():
            j = i
            while j < len(cleaned) and cleaned[j].isdigit():
                j += 1
            sign = -1 if i and cleaned[i - 1] == "-" else 1
            found = sign * int(cleaned[i:j])
            i = j
        else:
            i += 1
    return found
