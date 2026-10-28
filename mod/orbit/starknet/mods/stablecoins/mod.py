"""starknet.stablecoins — Stablecoins: Every stablecoin on Starknet with its live supply, how it is backed, and its peg from the Pragma oracle."""
import os
import sys
import time

_API = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'api')
if _API not in sys.path:
    sys.path.insert(0, _API)
import stables  # noqa: E402


class Mod:
    """starknet.stablecoins — supply + peg of every stablecoin (api/stables.py)."""

    def facts(self):
        s = stables.stables()
        ts = [t for t in s.get('tokens', []) if isinstance(t.get('deviation_bps'), (int, float))]
        worst = max(ts, key=lambda t: abs(t['deviation_bps']), default=None)
        return [['supply', f"${s.get('total_supply_usd', 0) / 1e6:,.1f}M"],
                ['coins', len(s.get('tokens', []))],
                ['widest peg', f"{worst['symbol']} {worst['deviation_bps']:+.0f}bp" if worst else '—']]

    def stablecoins(self, network=None):
        return stables.stables(network=network)

    def registry(self, network=None):
        return stables.registry(network=network)
