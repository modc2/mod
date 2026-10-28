"""starknet.zuma — Zuma: The zuma mod (anonymous subnet), as a Starknet app."""
import os
import sys
import time

_API = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'api')
if _API not in sys.path:
    sys.path.insert(0, _API)
import apps  # noqa: E402


class Mod:
    """starknet.zuma — points at orbit/ztensor (its API is POST-only)."""

    def facts(self):
        s = apps.get(f"http://127.0.0.1:{apps.port_of('ztensor')}/status", body={})
        dev = (s.get('deployments') or {}).get('devnet') or {}
        ep = s.get('epoch') or {}
        h = s.get('health') or {}
        return [['devnet', 'up' if h.get('devnet_up') else 'down'],
                ['contract', apps.short(dev.get('address')) or 'not deployed'],
                ['epoch', ep.get('epoch', '—')],
                ['next in', f"{ep.get('blocks_left', '—')} blk"],
                ['keys', len(s.get('keys') or [])]]
