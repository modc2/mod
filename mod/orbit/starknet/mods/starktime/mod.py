"""starknet.starktime — StarkTime: The starktime mod (time-weighted STRK staking), as a Starknet app."""
import os
import sys
import time

_API = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'api')
if _API not in sys.path:
    sys.path.insert(0, _API)
import apps  # noqa: E402


class Mod:
    """starknet.starktime — points at orbit/starktime."""

    def facts(self):
        s = apps.get(f"http://127.0.0.1:{apps.port_of('starktime')}/stats")
        return [['mode', s.get('mode', '—')],
                ['staked', f"{apps.num(s.get('total_staked', 0))} STRK"],
                ['stakers', s.get('stakers', 0)],
                ['epoch', s.get('epoch', '—')]]
