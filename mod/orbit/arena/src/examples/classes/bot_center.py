"""A Connect Four bot, as a class with one method.

A player is any class defining `play(self, view, seat)`. It is handed the exact
text the game gave that seat and returns its move as text — the same contract a
model is held to, which is the point: a bot and an LLM sit in the same seat and
answer the same question, so the leaderboard compares like with like.

    m arena/upload path=bot_center.py
    m arena/enter name=center kind=class config='{"module":"bot_center"}'
"""

ORDER = (3, 2, 4, 1, 5, 0, 6)   # centre first — a column near the middle is in
                                # more possible fours than one at the edge


class CentreBot:
    """Wins or blocks if it can see one move ahead; otherwise plays centre."""

    name = "center"

    def play(self, view, seat):
        grid = self.read(view)
        legal = self.legal(view)
        me = "x" if seat == 0 else "o"
        them = "o" if seat == 0 else "x"

        for disc in (me, them):          # take the win first, then deny theirs
            for column in legal:
                if self.wins(grid, column, disc):
                    return str(column)
        for column in ORDER:
            if column in legal:
                return str(column)
        return str(legal[0]) if legal else "0"

    # ── reading the view ─────────────────────────────────────────────────
    # The view is text, the same text a model gets. Parsing it is the bot's
    # own problem, which is what keeps the game from having to know who is
    # sitting at it.

    def read(self, view):
        rows = []
        for line in view.splitlines():
            cells = line.split()
            # Seven cells of `x`, `o` or `.` is a board row; the `0 1 2 …`
            # header is seven tokens too, which is why the contents matter.
            if len(cells) == 7 and all(c in (".", "x", "o") for c in cells):
                rows.append([" " if c == "." else c for c in cells])
        return rows or [[" "] * 7 for _ in range(6)]

    def legal(self, view):
        for line in view.splitlines():
            if line.startswith("Legal moves:"):
                return [int(t) for t in line.split(":", 1)[1].replace(",", " ").split()
                        if t.isdigit()]
        return list(range(7))

    # ── one move of lookahead ────────────────────────────────────────────

    def wins(self, grid, column, disc):
        rows = len(grid)
        row = next((r for r in range(rows - 1, -1, -1) if grid[r][column] == " "), None)
        if row is None:
            return False
        grid[row][column] = disc
        won = self.four_from(grid, row, column)
        grid[row][column] = " "
        return won

    def four_from(self, grid, row, column):
        disc = grid[row][column]
        rows, columns = len(grid), len(grid[0])
        for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
            run = 1
            for step in (1, -1):
                r, c = row + dr * step, column + dc * step
                while 0 <= r < rows and 0 <= c < columns and grid[r][c] == disc:
                    run += 1
                    r, c = r + dr * step, c + dc * step
            if run >= 4:
                return True
        return False
