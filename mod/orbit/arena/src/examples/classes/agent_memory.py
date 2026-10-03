"""Memory agent — recalls past wins via the arena MCP door.

On the first turn of each match this agent queries the arena for its own
match history (`list_matches` → `get_match`) and builds a map from position
fingerprint to the move it played from that position in a match it won.  On
subsequent turns that map is consulted before the fallback (first legal move),
so the agent improves across matches when the arena door is open.

The door is granted per-match (`mcp=["arena"]` in `run_match`).  Without it,
`self.mcp` raises and the agent falls back to the stateless strategy —
illegal rate stays zero either way.

A/B usage:

    m arena/upload path=agent_stateless.py
    m arena/upload path=agent_memory.py
    m arena/enter name=stateless kind=class config='{"module":"agent_stateless"}'
    m arena/enter name=memory    kind=class config='{"module":"agent_memory"}'
    # play three matches each against the same opponent, then compare records
    m arena/play game=nim players=stateless,lucky
    m arena/play game=nim players=memory,lucky    # with MCP door open in the server
    m arena/leaderboard game=nim
"""

import random


class MemoryAgent:
    """Recalls winning moves from past matches via self.mcp.

    Memory is loaded once per match (lazily, on the first turn).  Any MCP
    failure — door closed, arena not answering — is caught silently and the
    agent continues with the stateless fallback.
    """

    name = "memory-bot"

    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.history = []        # (view_key, move) recorded this match
        self.known_wins = None   # None = not loaded yet

    # ── helpers ───────────────────────────────────────────────────────────

    def _view_key(self, view):
        """Stable fingerprint for a position: the Legal-moves line."""
        for line in view.splitlines():
            head, _, rest = line.partition(":")
            if head.strip().lower() in ("legal moves", "moves", "options"):
                return "legal:" + rest.strip()
        return view[:80]

    def _load_history(self):
        """Return {view_key: move} for positions seen in past wins.

        Calls list_matches then get_match for each win.  Any exception
        returns an empty dict so the fallback kicks in.
        """
        try:
            result = self.mcp("arena", "list_matches", {
                "player": self.name, "limit": 30,
            })
            matches = (result or {}).get("matches", [])
        except Exception as exc:
            print(f"memory: list_matches failed ({exc}) — playing cold")
            return {}

        wins = {}
        for m in matches:
            seats = m.get("seats", [])
            my_info = next(
                (s for s in seats if s.get("player_name") == self.name), None
            )
            if not my_info or my_info.get("score", 0) <= 0.5:
                continue
            mid = m.get("id", "")
            if not mid:
                continue
            try:
                full = self.mcp("arena", "get_match", {"id": mid})
                my_idx = next(
                    (i for i, s in enumerate(full.get("seats", []))
                     if s.get("player_name") == self.name),
                    None,
                )
                if my_idx is None:
                    continue
                for turn in (full.get("turns") or []):
                    if (turn.get("seat") == my_idx
                            and turn.get("legal")
                            and turn.get("mv")):
                        key = self._view_key(turn.get("view", ""))
                        wins[key] = turn["mv"]
            except Exception:
                continue

        return wins

    # ── play ─────────────────────────────────────────────────────────────

    def play(self, view, seat):
        # Load memory on the first turn.
        if self.known_wins is None:
            self.known_wins = self._load_history()
            print(f"memory: {len(self.known_wins)} winning positions loaded")

        key = self._view_key(view)
        if key in self.known_wins:
            recalled = self.known_wins[key]
            print(f"memory: replaying known move {recalled!r}")
            return recalled

        # Fallback: first legal move.
        for line in view.splitlines():
            head, _, rest = line.partition(":")
            if head.strip().lower() in ("legal moves", "moves", "options"):
                opts = [t.strip() for t in rest.replace(",", " ").split()
                        if t.strip()]
                move = opts[0] if opts else ""
                self.history.append((key, move))
                return move
        return ""
