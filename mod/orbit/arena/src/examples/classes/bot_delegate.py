"""The Python half of the same idea: a player that asks another player.

`bot_oracle.rs` does this in Rust, through a synchronous host call, because
wasm cannot wait. A Python class does not have that problem — the sandbox is a
process talking a line protocol to something that is perfectly happy to sit
there — so the whole mechanism is one method call.

It still cannot open a socket. `self.mcp` writes a line; the host makes the
call, against a server the arena was configured with, and hands back what it
said. What the class names is `arena`, never a URL.
"""


class Delegate:
    """Asks the arena for the best-rated player, then plays what it plays."""

    name = "bot-delegate"

    def __init__(self, seed):
        self.seed = seed
        self.oracle = None
        self.asked = 0

    def _pick_oracle(self):
        """The strongest player entered, other than this one. Asked once."""
        board = self.mcp("arena", "leaderboard", {"limit": 10})
        for row in (board.get("players") or board.get("leaderboard") or []):
            name = row.get("name") or ""
            module = row.get("module") or ""
            if name and name != self.name and module:
                return module
        return ""

    def play(self, view, seat):
        if self.oracle is None:
            self.oracle = self._pick_oracle()
            print(f"asking {self.oracle or 'nobody — nothing is entered'}")

        if self.oracle:
            self.asked += 1
            reply = self.mcp("arena", "module_tool", {
                "module": self.oracle,
                "tool": "play",
                "arguments": {"view": view, "seat": seat},
            })
            move = (reply or {}).get("move") or ""
            if move.strip():
                return move
            print(f"{self.oracle} said nothing: {reply.get('error', 'no move')}")

        # The fallback matters more than the delegation: this is what it plays
        # when the arena is unreachable, the oracle is gone, or nothing is
        # entered yet — all of which happen.
        for line in view.splitlines():
            if line.startswith("Legal moves:"):
                options = [m.strip() for m in line.split(":", 1)[1].split(",") if m.strip()]
                return random.choice(options) if options else ""
        return ""
