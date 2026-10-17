"""starknet.privacy — Privacy: The public side of the STRK20 privacy pool: deposits, withdrawals, notes, nullifiers and helpers. Never asks for a viewing key."""
import os
import sys
import time

_API = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'api')
if _API not in sys.path:
    sys.path.insert(0, _API)
import strk20  # noqa: E402

_hit = {'at': 0, 'v': None}


class Mod:
    """starknet.privacy — the STRK20 pool's public side (api/strk20.py)."""

    def facts(self):
        if not _hit['v'] or time.time() - _hit['at'] > 120:   # ~10 RPC reads: keep a while
            _hit.update(at=time.time(), v=strk20.state())
        p = _hit['v']
        h = p.get('public_holdings') or {}
        n = lambda k: h[k] if isinstance(h.get(k), (int, float)) else 0  # noqa: E731
        return [['pool', 'paused' if p.get('paused') else 'live'],
                ['STRK', f"{n('strk') / 1e6:,.2f}M"],
                ['USDC', f"{n('usdc') / 1e3:,.0f}k"],
                ['version', p.get('version', '—')]]

    def pool(self, network=None):
        return strk20.state(network=network)

    def activity(self, event=None, limit=20, network=None):
        return strk20.activity(name=event, limit=int(limit), network=network)

    def user(self, address, network=None):
        return strk20.user(address, network=network)

    def helpers(self, network=None):
        return strk20.helpers(network=network)

    def docs(self, page=None, q=None):
        return strk20.docs(page, q)
