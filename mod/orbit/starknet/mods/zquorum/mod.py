"""starknet.zquorum — zQuorum: The zquorum mod (anonymous quorums), as a Starknet app."""
import os
import sys
import time

_API = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'api')
if _API not in sys.path:
    sys.path.insert(0, _API)
import apps  # noqa: E402


class Mod:
    """starknet.zquorum — points at orbit/zquorum."""

    def facts(self):
        base = f"http://127.0.0.1:{apps.port_of('zquorum')}/zquorum/api"
        h = apps.get(f'{base}/health')
        qs = apps.get(f'{base}/list').get('quorums', [])
        sn = h.get('starknet') or {}
        dev = (sn.get('deployments') or {}).get('devnet')
        return {'facts': [['quorums', len(qs)],
                          ['on starknet', sn.get('anchored', 0)],
                          ['contract', apps.short(dev) if dev else 'not deployed'],
                          ['devnet', 'up' if sn.get('devnet_up') else 'down']],
                'items': [{'name': q.get('name') or q.get('quorum'),
                           'desc': ' · '.join(x for x in (
                               q.get('desc') or '',
                               f"on {q['chain']['network']}" if q.get('chain') else '') if x),
                           'state': 'reached' if q.get('reached') else 'open'} for q in qs[:8]]}
