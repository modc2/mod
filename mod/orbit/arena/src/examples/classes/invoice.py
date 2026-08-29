"""invoice — read a JSON invoice, then do the arithmetic on it. The capstone.

addup adds, times multiplies, jsonpath reads and jsonfix writes. This one needs
all four in a row, which is the thing that actually goes wrong in practice: the
document is read correctly, the multiplication is right, and the answer is
still wrong because the discount was applied after the tax.

Six rounds over one invoice, walking from "how many units of this sku" to the
grand total, so the transcript shows exactly where an answerer fell off — a
seat that gets the line totals and misses the grand total has a different
problem from one that misread the document.

Every amount is a whole number of credits and the tax rounds down, stated in
the view. That is deliberate: a drill about parsing and arithmetic should not
quietly also be a drill about someone's rounding convention.

    m arena/upload path=invoice.py
    m arena/play game=invoice players=calc,guess
"""

import json
import random

ROUNDS = 6

PART = ["kiln", "atlas", "beacon", "quarry", "mantle", "harbour", "vector"]


class Invoice:
    """One invoice, six questions, each leaning on the last."""

    name = "invoice"
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
        self.focus = self.rng.choice(self.doc["lines"])["sku"]

    # ── the invoice ──────────────────────────────────────────────────────

    def build(self):
        lines = [{
            "sku": f"{self.rng.choice(PART)}-{self.rng.randint(10, 99)}",
            "qty": self.rng.randint(2, 24),
            "unit": self.rng.randrange(120, 4000, 5),
            # a field that is not part of any answer: an invoice has columns
            # nobody asked about, and picking the right two is the skill
            "warehouse": self.rng.choice(["north", "south", "dock"]),
        } for _ in range(self.rng.randint(3, 5))]
        return {
            "invoice": f"INV-{self.rng.randint(1000, 9999)}",
            "currency": "credits",
            "lines": lines,
            "discount": self.rng.randrange(50, 900, 25),
            "tax_percent": self.rng.choice([5, 8, 12, 15]),
        }

    # ── the answers, computed the one right way ──────────────────────────

    def line(self, sku):
        return next(l for l in self.doc["lines"] if l["sku"] == sku)

    def line_total(self, sku):
        l = self.line(sku)
        return l["qty"] * l["unit"]

    def subtotal(self):
        return sum(l["qty"] * l["unit"] for l in self.doc["lines"])

    def discounted(self):
        return self.subtotal() - self.doc["discount"]

    def tax(self):
        return self.discounted() * self.doc["tax_percent"] // 100

    def total(self):
        return self.discounted() + self.tax()

    def asks(self):
        pct = self.doc["tax_percent"]
        return [
            (f"how many units of {self.focus} are on this invoice",
             self.line(self.focus)["qty"]),
            (f"line total for {self.focus} — its quantity times its unit price",
             self.line_total(self.focus)),
            ("subtotal — every line total added up",
             self.subtotal()),
            ("subtotal after the discount is taken off",
             self.discounted()),
            (f"tax on the discounted amount at {pct}%, rounded down to a whole credit",
             self.tax()),
            ("grand total — the discounted amount plus that tax",
             self.total()),
        ]

    # ── the game ─────────────────────────────────────────────────────────

    def turn(self):
        return list(range(self.players[1]))

    def view(self, seat):
        if self.round >= ROUNDS:
            return self.over(seat)
        question, _ = self.asks()[self.round]
        return "\n".join([
            f"DRILL invoice — round {self.round + 1} of {ROUNDS}",
            f"Seat {seat}. Correct so far: {self.correct.get(seat, 0)} of {self.round}.",
            "",
            "Invoice:",
            json.dumps(self.doc, indent=2),
            "",
            "Every amount is a whole number of credits. Tax is charged on the "
            "discounted amount, never on the subtotal, and rounds down.",
            "",
            f"Question: {question}",
            "Answer with the number alone.",
            "Legal moves: one integer",
        ])

    def over(self, seat):
        """Where this seat ended up.

        A view is still asked for after the last round — the runner shows every
        seat where the drill left it — so there has to be something to say that
        is not the seventh of six questions.
        """
        return "\n".join([
            f"DRILL invoice — over",
            f"Seat {seat}. Correct so far: {self.correct.get(seat, 0)} of {ROUNDS}.",
        ])

    def step(self, moves):
        question, want = self.asks()[self.round]
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
        return {**legal,
                "note": f"{question.split(' —')[0]} = {want} · " + ", ".join(said)}

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
    """The last integer in a reply, or None. Commas inside digits are dropped."""
    cleaned = text.replace(",", "")
    found, i = None, 0
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
