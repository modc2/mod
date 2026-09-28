"""trades — the front door, played back as a drill.

The page at /arena scores every proposed trade with one pure function:

    score = clamp(0, 100, 50 + 35·tanh(roi / 40) + 15·tanh(4·conviction))

This game deals two plausible trades a round and asks which one that
function ranks higher. Easy when one trade wins on both axes; the deals
that matter are the trade-offs — a hot trader nibbling against a mediocre
trader going all in — and reading those right *is* understanding the
score function. A seat's score is a count of right calls, so the board
reads as "can it rank trades" and nothing else.

Same DRILL view format as the rest of the pack, so one bot, one prompt
and one bridge play all of them. Deals are a function of the seed alone,
never of the answers — two seats compared here saw the same eight pairs.

    m arena/play game=trades players=lfm-1.2b,guess
"""

import math
import random

ROUNDS = 8


def score(roi, conviction):
    """The /arena score function, verbatim — trades.rs holds the twin."""
    conv = max(0.0, conviction)
    s = 50.0 + 35.0 * math.tanh(roi / 40.0) + 15.0 * math.tanh(4.0 * conv)
    return max(0.0, min(100.0, s))


class Trades:
    """Two proposed trades a round — pick the one the arena's own score
    function ranks higher."""

    name = "trades"
    players = [1, 4]
    max_turns = ROUNDS

    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.round = 0
        self.correct = {}
        self.seen = set()
        self.pair = self.deal()

    # ── the deals ────────────────────────────────────────────────────────
    # Deterministic in the seed. Redealing until the scores are clearly
    # apart keeps every question answerable — a coin-flip pair would grade
    # luck, not reading.

    def trade(self):
        side = self.rng.choice(["BUY", "SELL"])
        netuid = self.rng.randint(1, 128)
        roi = round(self.rng.uniform(-100.0, 200.0), 1)
        conviction = round(self.rng.random() ** 2 * 0.6, 3)
        book = self.rng.uniform(20.0, 5000.0)
        size = round(max(0.01, conviction * book), 2)
        return {"side": side, "netuid": netuid, "roi": roi,
                "conviction": conviction, "size": size}

    def deal(self):
        while True:
            a, b = self.trade(), self.trade()
            if abs(score(a["roi"], a["conviction"]) - score(b["roi"], b["conviction"])) >= 2.0:
                return (a, b)

    @staticmethod
    def line(letter, t):
        return (f"{letter}: {t['side']} {t['size']} tao on subnet {t['netuid']}"
                f" · trader roi {t['roi']:+.1f}%"
                f" · conviction {t['conviction'] * 100:.1f}% of the book")

    # ── the game ─────────────────────────────────────────────────────────

    def turn(self):
        """Everyone answers at once — the arena drops seats that aren't
        there, so the same list is right for one player or four."""
        return list(range(self.players[1]))

    def view(self, seat):
        if self.round >= ROUNDS:
            return self.over(seat)
        got = self.correct.get(seat, 0)
        a, b = self.pair
        return "\n".join([
            f"DRILL trades — round {self.round + 1} of {ROUNDS}",
            f"Seat {seat}. Correct so far: {got} of {self.round}.",
            "",
            "The arena scores a proposed trade as",
            "  score = clamp(0, 100, 50 + 35*tanh(roi / 40) + 15*tanh(4*conviction))",
            "roi = the trader's window return %, conviction = this trade's",
            "size as a share of that trader's whole book (as a fraction).",
            "",
            self.line("A", a),
            self.line("B", b),
            "",
            "Question: which trade scores higher?",
            "Answer with the letter alone.",
            "Legal moves: `A` or `B`",
        ])

    def over(self, seat):
        return "\n".join([
            "DRILL trades — over",
            f"Seat {seat}. Correct so far: {self.correct.get(seat, 0)} of {ROUNDS}.",
        ])

    def step(self, moves):
        a, b = self.pair
        sa, sb = score(a["roi"], a["conviction"]), score(b["roi"], b["conviction"])
        want = "A" if sa > sb else "B"
        legal, said = {}, []
        seats = sorted({int(k) for k in moves})
        self.seen.update(seats)
        for seat in seats:
            given = letter_in(str(moves.get(seat, "")))
            legal[seat] = given is not None
            if given == want:
                self.correct[seat] = self.correct.get(seat, 0) + 1
            said.append(f"seat {seat}: {given or '-'}")

        self.round += 1
        if self.round < ROUNDS:
            self.pair = self.deal()
        return {**legal,
                "note": f"A {sa:.1f} vs B {sb:.1f} — {want} · " + ", ".join(said)}

    def done(self):
        return self.round >= ROUNDS

    def result(self):
        played = max(self.seen) + 1 if self.seen else 1
        scores = [self.correct.get(s, 0) for s in range(played)]
        return {
            "scores": scores,
            "summary": f"right calls out of {ROUNDS}: "
                       + ", ".join(f"seat {s} {n}" for s, n in enumerate(scores)),
        }


def letter_in(text):
    """The last standalone A or B in a reply, or None.

    Strict about there being a letter, relaxed about what surrounds it: a
    model that says "trade B scores higher." has answered. A letter inside
    a longer word (the "a" in "trade") does not count.
    """
    found = None
    lower = text.lower()
    for i, c in enumerate(lower):
        if c not in "ab":
            continue
        before = lower[i - 1] if i else " "
        after = lower[i + 1] if i + 1 < len(lower) else " "
        if not before.isalnum() and not after.isalnum():
            found = c.upper()
    return found
