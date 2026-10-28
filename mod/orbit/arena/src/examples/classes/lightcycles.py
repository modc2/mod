"""Light Cycles — two bikes, one arena, every cell you cross becomes a wall.

Both riders steer at the same time (`turn` returns both seats), so nobody gets
to see the other's move before committing to their own. Hit a wall, a trail or
the edge and you are out; outlast the other rider and you win. Two riders who
crash on the same tick — or drive into the same cell — draw.

The seed scatters a few blocks, always point-symmetric about the centre, so the
board differs from match to match but never favours a seat.

    m arena/upload path=lightcycles.py
    m arena/upload path=bot_cycle.py
"""

SIZE = 13
DIRS = {"N": (-1, 0), "S": (1, 0), "E": (0, 1), "W": (0, -1)}
BACK = {"N": "S", "S": "N", "E": "W", "W": "E"}
HEADS = ("A", "B")
TRAILS = ("a", "b")


class LightCycles:
    """Simultaneous steering on a 13x13 grid. Last rider moving wins."""

    name = "lightcycles"
    players = 2
    max_turns = SIZE * SIZE

    def __init__(self, seed):
        self.grid = [["."] * SIZE for _ in range(SIZE)]
        mid = SIZE // 2
        self.pos = [(mid, 2), (mid, SIZE - 3)]
        self.heading = ["E", "W"]
        self.alive = [True, True]
        self.ticks = 0
        self.note = ""
        self.blocks(seed)
        for seat in (0, 1):
            r, c = self.pos[seat]
            self.grid[r][c] = HEADS[seat]

    def blocks(self, seed):
        # A tiny LCG, not `random`: the board is a pure function of the seed.
        x = (seed * 2654435761 + 1) & 0xFFFFFFFF
        mid = SIZE // 2
        for _ in range(3 + seed % 4):
            x = (x * 1103515245 + 12345) & 0x7FFFFFFF
            r, c = (x >> 8) % SIZE, (x >> 16) % SIZE
            # keep the starting lanes clear, then mirror through the centre
            if r == mid or (r, c) == (mid, mid):
                continue
            self.grid[r][c] = "#"
            self.grid[SIZE - 1 - r][SIZE - 1 - c] = "#"

    def turn(self):
        return [0, 1]

    # ── what a seat can see ──────────────────────────────────────────────

    def view(self, seat):
        r, c = self.pos[seat]
        tr, tc = self.pos[1 - seat]
        board = "\n".join(" ".join(row) for row in self.grid)
        safe = [d for d in self.options(seat) if self.free(self.ahead(seat, d))]
        lines = [
            f"Light Cycles, tick {self.ticks + 1}. You are seat {seat}, riding `{HEADS[seat]}` "
            f"(your trail is `{TRAILS[seat]}`). The other rider is `{HEADS[1 - seat]}`.",
            f"You are at row {r}, column {c}, heading {self.heading[seat]}. "
            f"They are at row {tr}, column {tc}, heading {self.heading[1 - seat]}.",
            "Row 0 is the top, column 0 the left. N is up, S down, E right, W left.",
            "`.` is open; `#`, any trail and the edge are walls. Both riders move at once.",
            "",
            board,
            "",
            f"Safe right now: {', '.join(safe) if safe else 'nothing — brace yourself'}",
            f"Legal moves: {', '.join(self.options(seat))}",
            "Reply with one letter and nothing else.",
        ]
        if self.note:
            lines.insert(2, f"Last tick: {self.note}")
        return "\n".join(lines)

    # ── one tick ─────────────────────────────────────────────────────────

    def step(self, moves):
        legal = {}
        for seat in (0, 1):
            d = self.direction_in(str(moves.get(seat, "")), seat)
            legal[seat] = d is not None
            # An unreadable answer keeps the bike going straight. That is the
            # penalty: you lose the turn, not necessarily the match.
            if d is not None:
                self.heading[seat] = d

        targets = [self.ahead(seat, self.heading[seat]) for seat in (0, 1)]
        for seat in (0, 1):
            r, c = self.pos[seat]
            self.grid[r][c] = TRAILS[seat]
        crashed = [not self.free(t) for t in targets]
        if targets[0] == targets[1]:
            crashed = [True, True]

        for seat in (0, 1):
            if crashed[seat]:
                self.alive[seat] = False
            else:
                self.pos[seat] = targets[seat]
                r, c = targets[seat]
                self.grid[r][c] = HEADS[seat]

        self.ticks += 1
        if all(crashed):
            self.note = "both riders crashed"
        elif any(crashed):
            self.note = f"`{HEADS[crashed.index(True)]}` hit a wall"
        else:
            self.note = f"A went {self.heading[0]}, B went {self.heading[1]}"
        return {**legal, "note": f"tick {self.ticks}: {self.note}"}

    def done(self):
        return not all(self.alive) or self.ticks >= self.max_turns

    def result(self):
        a, b = self.alive
        if a == b:
            return {"scores": [0.5, 0.5], "summary": f"drawn after {self.ticks} ticks — {self.note}"}
        winner = 0 if a else 1
        scores = [0, 0]
        scores[winner] = 1
        return {"scores": scores,
                "summary": f"seat {winner} (`{HEADS[winner]}`) outlasted the other rider, {self.ticks} ticks"}

    # ── the arena itself ─────────────────────────────────────────────────

    def options(self, seat):
        """Every way but straight back — reversing into your own trail is not a move."""
        return [d for d in "NESW" if d != BACK[self.heading[seat]]]

    def ahead(self, seat, d):
        r, c = self.pos[seat]
        dr, dc = DIRS[d]
        return (r + dr, c + dc)

    def free(self, cell):
        r, c = cell
        return 0 <= r < SIZE and 0 <= c < SIZE and self.grid[r][c] == "."

    def direction_in(self, text, seat):
        """The direction a move names, or None. `north`, `n`, `go E` all count."""
        words = {"NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
                 "UP": "N", "DOWN": "S", "RIGHT": "E", "LEFT": "W"}
        for token in "".join(ch if ch.isalpha() else " " for ch in text.upper()).split():
            d = words.get(token, token if token in DIRS else None)
            if d:
                return d if d in self.options(seat) else None
        return None
