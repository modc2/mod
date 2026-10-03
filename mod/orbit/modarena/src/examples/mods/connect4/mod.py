"""Connect Four — drop a disc, four in a row wins.

A game is a class. Define `view`, `step`, `done` and `result` and the arena can
sit two players down at it; everything else here is ordinary Python. The state
is `self`, because that is where a Python programmer keeps state.

Upload it and it is playable:

    m modarena/upload path=connect4.py
    m modarena/play game=connect4 players=opus,greedy
"""

COLUMNS = 7
ROWS = 6
DISCS = ("x", "o")


class ConnectFour:
    """Drop a disc down a column; four in a row, any direction, wins."""

    name = "connect4"
    players = 2
    max_turns = 42

    def __init__(self, seed):
        # `seed` is the match seed. Nothing here is random, so it only decides
        # which side moves first — which is enough to stop two deterministic
        # bots from replaying one identical match forever.
        self.grid = [[" "] * COLUMNS for _ in range(ROWS)]
        self.first = seed % 2
        self.played = 0
        self.winner = None
        self.fault = None

    # ── what a seat can see ──────────────────────────────────────────────

    def view(self, seat):
        # `.` for an empty cell, not a space: the view is the only thing a
        # player gets, and a board drawn out of spaces cannot be read back.
        board = "\n".join(" ".join(c if c != " " else "." for c in row) for row in self.grid)
        return (
            f"Connect Four. You are seat {seat}, playing `{DISCS[seat]}`.\n"
            f"Columns are numbered 0-6, left to right. Discs fall to the bottom.\n\n"
            f"{'0 1 2 3 4 5 6'}\n{board}\n\n"
            f"Four in a row — across, down or diagonal — wins.\n"
            f"Legal moves: {', '.join(str(c) for c in self.open_columns())}\n"
            f"Reply with the column number and nothing else."
        )

    # ── one round of moves ───────────────────────────────────────────────

    def step(self, moves):
        """`moves` is {seat: "3"}. Return {seat: was_it_legal}.

        Whatever this marks illegal is counted against that player for good —
        the illegal-move rate on the leaderboard is built out of exactly this.
        """
        seat = self.whose_turn()
        raw = str(moves.get(seat, "")).strip()
        column = self.column_in(raw)

        if column is None:
            # A player who cannot name a legal column forfeits the turn. Three
            # in a row and it forfeits the match, which is how a game says
            # "answer the question" without hanging.
            self.fault = (self.fault or 0) + 1
            if self.fault >= 3:
                self.winner = 1 - seat
            return {seat: False, "note": f"seat {seat} said {raw!r}, which is not an open column"}

        self.fault = 0
        row = self.drop(column, DISCS[seat])
        self.played += 1
        if self.four_from(row, column):
            self.winner = seat
        return {seat: True}

    def done(self):
        return self.winner is not None or not self.open_columns()

    def result(self):
        if self.winner is None:
            return {"scores": [0.5, 0.5], "summary": f"drawn after {self.played} discs"}
        scores = [0, 0]
        scores[self.winner] = 1
        return {
            "scores": scores,
            "summary": f"seat {self.winner} ({DISCS[self.winner]}) connected four on disc {self.played}",
        }

    # ── the game itself ──────────────────────────────────────────────────

    def whose_turn(self):
        return (self.first + self.played) % 2

    def turn(self):
        """Optional. Who moves now — leave it out and seats alternate anyway."""
        return self.whose_turn()

    def open_columns(self):
        return [c for c in range(COLUMNS) if self.grid[0][c] == " "]

    def column_in(self, text):
        """The column a move names, or None. Generous about spelling: a model
        that answers `column 4` has still said 4."""
        digits = "".join(c if c.isdigit() else " " for c in text).split()
        if not digits:
            return None
        column = int(digits[0])
        return column if column in self.open_columns() else None

    def drop(self, column, disc):
        for row in range(ROWS - 1, -1, -1):
            if self.grid[row][column] == " ":
                self.grid[row][column] = disc
                return row
        return -1

    def four_from(self, row, column):
        disc = self.grid[row][column]
        for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
            run = 1
            for step in (1, -1):
                r, c = row + dr * step, column + dc * step
                while 0 <= r < ROWS and 0 <= c < COLUMNS and self.grid[r][c] == disc:
                    run += 1
                    r, c = r + dr * step, c + dc * step
            if run >= 4:
                return True
        return False
