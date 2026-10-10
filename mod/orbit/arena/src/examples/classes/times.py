"""times — six products, more digits each round. Can it multiply?

The addition drill's twin, and deliberately the same shape (see addup.py for
the view format every drill in this pack shares). It is worth having both:
adding a long list and multiplying two four-digit numbers fail differently.
Addition goes wrong by dropping a carry somewhere in the middle; multiplication
goes wrong all at once, and a model that is fine at 12 x 9 can be hopeless at
4817 x 2934 while sounding exactly as confident.

Two of the six rounds ask for a product of three small numbers instead of two
big ones — same skill, different shape, and it catches an answerer that has
memorised times tables rather than learnt the operation.

    m arena/upload path=times.py
    m arena/play game=times players=calc,guess
"""

import random

ROUNDS = 6

# digits per factor, round by round — the ramp is the whole difficulty curve
WIDTHS = [(2, 1), (2, 2), (3, 2), (1, 1, 1), (4, 3), (2, 2, 2)]


class Times:
    """Multiply. Two factors most rounds, three on the short ones."""

    name = "times"
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
        self.factors = self.deal(0)

    def deal(self, r):
        """Factors with the widths this round asks for, never a leading zero
        and never a 1 — multiplying by one measures nothing."""
        out = []
        for digits in WIDTHS[r % len(WIDTHS)]:
            low = 2 if digits == 1 else 10 ** (digits - 1)
            out.append(self.rng.randint(low, 10 ** digits - 1))
        return out

    def question(self):
        return " * ".join(str(f) for f in self.factors)

    def answer(self):
        total = 1
        for f in self.factors:
            total *= f
        return total

    def turn(self):
        return list(range(self.players[1]))

    def view(self, seat):
        if self.round >= ROUNDS:
            return self.over(seat)
        got = self.correct.get(seat, 0)
        return "\n".join([
            f"DRILL times — round {self.round + 1} of {ROUNDS}",
            f"Seat {seat}. Correct so far: {got} of {self.round}.",
            "",
            f"Question: {self.question()}",
            "Answer with the product alone. Work it out exactly — an answer "
            "that is close is an answer that is wrong.",
            "Legal moves: one integer, e.g. `14127078`",
        ])

    def over(self, seat):
        """Where this seat ended up.

        A view is still asked for after the last round — the runner shows every
        seat where the drill left it — so there has to be something to say that
        is not the seventh of six questions.
        """
        return "\n".join([
            f"DRILL times — over",
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
            self.factors = self.deal(self.round)
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
    """The last integer in a reply, or None. Commas inside digits are dropped —
    a product written 14,127,078 is the same number."""
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
