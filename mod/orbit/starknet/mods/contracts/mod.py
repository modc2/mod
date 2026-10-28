"""starknet.contracts — Contracts: Every contract this node knows: your Cairo contracts with their devnet address, then the well-known mainnet ones."""
import os
import sys
import time

_API = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'api')
if _API not in sys.path:
    sys.path.insert(0, _API)
import contracts as _contracts  # noqa: E402


class Mod:
    """starknet.contracts — yours + the well-known ones (api/contracts.py)."""

    def facts(self):
        c = _contracts.contracts()
        return [['yours', len(c['yours'])],
                ['on devnet', sum(1 for x in c['yours'] if x.get('devnet'))],
                ['well-known', len(c['known'])]]

    def contracts(self, network=None):
        return _contracts.contracts(network=network)
