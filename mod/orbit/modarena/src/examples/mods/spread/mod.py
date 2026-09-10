"""A Blotto bot that reads the rules out of the view.

The game states its terms in the text it shows each seat — how many fields,
how many soldiers. This reads those two numbers back out and answers with a
valid allocation, which is all a game ever asks of a player.

It plays the standard counter to an even split: overload two fields and
concede the third, jittered so a repeat match is not a repeat.
"""

import random


class SpreadBot:
    """Overloads two fields, concedes one, and never fields an illegal army."""

    name = "spread"

    def play(self, view, seat):
        fields, soldiers = self.terms(view)
        if fields < 1:
            return ""

        # Concede one field, split the rest slightly unevenly. Against an even
        # split this takes two fields to one; against a bot doing the same, it
        # comes down to which field each of them gave up.
        give_up = random.randrange(fields)
        army = [0] * fields
        contested = [f for f in range(fields) if f != give_up]
        left = soldiers
        for i, field in enumerate(contested):
            if i == len(contested) - 1:
                army[field] = left
            else:
                share = left // (len(contested) - i)
                army[field] = max(0, share + random.randint(-2, 2))
                left -= army[field]
        # Rounding and jitter both have to come out somewhere.
        army[contested[-1]] = soldiers - sum(army[f] for f in contested[:-1])
        if army[contested[-1]] < 0:
            army = self.even(fields, soldiers)
        return " ".join(str(n) for n in army)

    def terms(self, view):
        """`Split exactly 20 soldiers across 3 fields` — the numbers, or (0, 0)."""
        fields = soldiers = 0
        for line in view.splitlines():
            words = line.replace(",", " ").split()
            for i, word in enumerate(words):
                if not word.isdigit():
                    continue
                after = words[i + 1].strip(".").lower() if i + 1 < len(words) else ""
                if after.startswith("soldier"):
                    soldiers = int(word)
                elif after.startswith("field"):
                    fields = int(word)
        return fields, soldiers

    def even(self, fields, soldiers):
        army = [soldiers // fields] * fields
        army[0] += soldiers - sum(army)
        return army
