"""A Light Cycles rider: turn toward the most room.

For each safe direction it flood-fills the open cells it could reach from
there, and takes the biggest pocket — the one rule that beats most riders,
because the rider who runs out of room first is the one who loses. Ties go to
the direction that keeps it farthest from the other bike's head.

    m arena/upload path=bot_cycle.py
    m arena/enter name=cycle kind=class config='{"module":"bot_cycle"}'
"""

DIRS = {"N": (-1, 0), "S": (1, 0), "E": (0, 1), "W": (0, -1)}


class CycleBot:
    """Flood-fill: go where there is the most room left."""

    name = "cycle"

    def play(self, view, seat):
        grid = [l.split() for l in view.splitlines()
                if l.split() and all(len(t) == 1 and t in ".#aAbB" for t in l.split())
                and len(l.split()) > 4]
        legal = self.listed(view, "Legal moves:") or list("NESW")
        if not grid:
            return legal[0]
        me, them = ("A", "B") if seat == 0 else ("B", "A")
        head = self.find(grid, me)
        foe = self.find(grid, them)
        if head is None:
            return legal[0]

        best, pick = None, legal[0]
        for d in legal:
            cell = self.step(head, d)
            if not self.open(grid, cell):
                continue
            room = self.room(grid, cell)
            # a cell the other head could also reach this tick is a coin flip
            # on a head-on crash — worth it only if every other option is worse
            risky = foe is not None and any(self.step(foe, e) == cell for e in DIRS)
            far = abs(cell[0] - foe[0]) + abs(cell[1] - foe[1]) if foe else 0
            score = (room - (room // 2 if risky else 0), far)
            if best is None or score > best:
                best, pick = score, d
        return pick

    def listed(self, view, prefix):
        for line in view.splitlines():
            if line.startswith(prefix):
                return [t for t in line.split(":", 1)[1].replace(",", " ").split() if t in DIRS]
        return []

    def find(self, grid, ch):
        for r, row in enumerate(grid):
            if ch in row:
                return (r, row.index(ch))
        return None

    def step(self, at, d):
        return (at[0] + DIRS[d][0], at[1] + DIRS[d][1])

    def open(self, grid, cell):
        r, c = cell
        return 0 <= r < len(grid) and 0 <= c < len(grid[0]) and grid[r][c] == "."

    def room(self, grid, start):
        seen, todo = {start}, [start]
        while todo:
            at = todo.pop()
            for d in DIRS:
                nxt = self.step(at, d)
                if nxt not in seen and self.open(grid, nxt):
                    seen.add(nxt)
                    todo.append(nxt)
        return len(seen)
