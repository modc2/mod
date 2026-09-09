"""Stateless agent — the A/B baseline.

Reads `Legal moves:` from the view and picks the first option every time.
Makes no MCP calls, holds no state between turns, and plays identically when
it sees the same view twice.  This is the reference point against which
memory-augmented agents are compared: if a memory agent does not beat this,
its memory system is not helping.

    m arena/upload path=agent_stateless.py
    m arena/enter name=stateless kind=class config='{"module":"agent_stateless"}'
    m arena/play game=nim players=stateless,lucky
"""


class StatelessAgent:
    """Picks the first announced legal move.  No memory.  No MCP calls."""

    name = "stateless"

    def play(self, view, seat):
        for line in view.splitlines():
            head, _, rest = line.partition(":")
            if head.strip().lower() in ("legal moves", "moves", "options"):
                opts = [t.strip() for t in rest.replace(",", " ").split()
                        if t.strip()]
                return opts[0] if opts else ""
        return ""
