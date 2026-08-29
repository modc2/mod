"""Colonel Blotto — split 20 soldiers across 3 fields; take the most fields.

Two things this shows that a turn-based game does not:

  * `turn` returns **both** seats, so the arena asks them at the same time and
    neither sees the other's move before committing to its own
  * `view` shows each seat only its own history — hidden information is just
    a game that declines to mention things

Six rounds. Winning a field means putting more soldiers on it than the other
side did; a tied field counts for neither.
"""

FIELDS = 3
SOLDIERS = 20
ROUNDS = 6


class Blotto:
    """Simultaneous allocation. Ties on a field go to nobody."""

    name = "blotto"
    players = 2
    max_turns = ROUNDS

    def __init__(self, seed):
        self.round = 0
        self.fields_won = [0, 0]
        self.history = [[], []]      # per seat: (mine, theirs, fields I took)
        self.last_note = ""

    def turn(self):
        """Both seats move at once. That is the whole of a simultaneous game."""
        return [0, 1]

    def view(self, seat):
        lines = [
            f"Colonel Blotto, round {self.round + 1} of {ROUNDS}. You are seat {seat}.",
            f"Split exactly {SOLDIERS} soldiers across {FIELDS} fields.",
            "Whoever puts more soldiers on a field takes it; equal takes neither.",
            f"Fields taken so far — you {self.fields_won[seat]}, "
            f"them {self.fields_won[1 - seat]}.",
        ]
        if self.history[seat]:
            lines.append("Rounds so far:")
            for i, (mine, theirs, took) in enumerate(self.history[seat], 1):
                lines.append(f"  {i}: you {mine} vs them {theirs} — you took {took}")
        lines += [
            "",
            f"Legal moves: any {FIELDS} numbers adding to {SOLDIERS}, e.g. `10 5 5`.",
            "Reply with the numbers and nothing else.",
        ]
        return "\n".join(lines)

    def step(self, moves):
        armies, legal = {}, {}
        for seat in (0, 1):
            army = self.army_in(str(moves.get(seat, "")))
            legal[seat] = army is not None
            # An unreadable answer is nothing on every field: illegal, and it
            # loses the round rather than stalling it.
            armies[seat] = army or [0] * FIELDS

        took = [0, 0]
        for field in range(FIELDS):
            a, b = armies[0][field], armies[1][field]
            if a > b:
                took[0] += 1
            elif b > a:
                took[1] += 1

        for seat in (0, 1):
            self.fields_won[seat] += took[seat]
            self.history[seat].append((armies[seat], armies[1 - seat], took[seat]))

        self.round += 1
        self.last_note = f"round {self.round}: {armies[0]} vs {armies[1]} — fields {took}"
        return {**legal, "note": self.last_note}

    def done(self):
        return self.round >= ROUNDS

    def result(self):
        a, b = self.fields_won
        if a == b:
            return {"scores": [0.5, 0.5], "summary": f"drawn {a}-{b} on fields"}
        return {
            "scores": [1, 0] if a > b else [0, 1],
            "summary": f"seat {0 if a > b else 1} took the most fields, {max(a, b)}-{min(a, b)}",
        }

    def army_in(self, text):
        """Read an allocation out of free text, or None if it is not one."""
        numbers = [int(t) for t in "".join(c if c.isdigit() else " " for c in text).split()]
        if len(numbers) != FIELDS or sum(numbers) != SOLDIERS:
            return None
        return numbers
