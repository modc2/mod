"""starknet.cairo — Cairo workbench: The cairo mod's contract workbench, as a Starknet app."""
import os
import sys
import time

_API = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'api')
if _API not in sys.path:
    sys.path.insert(0, _API)
import apps  # noqa: E402


class Mod:
    """starknet.cairo — points at orbit/cairo; facts() reads its API live."""

    def facts(self):
        base = f"http://127.0.0.1:{apps.port_of('cairo')}/cairo/api"
        cs = apps.get(f'{base}/contracts').get('contracts', [])
        tc = apps.get(f'{base}/toolchain')
        scarb = ((tc.get('toolchain') or tc).get('scarb') or {}).get('version', '')
        return {'facts': [['contracts', len(cs)],
                          ['built', sum(1 for c in cs if c.get('built'))],
                          ['scarb', scarb.split(' ')[1] if scarb.startswith('scarb ') else (scarb or '—')]],
                'items': [{'name': c['name'], 'desc': c.get('description') or '',
                           'state': c.get('state') or ('fresh' if c.get('built') else 'unbuilt'),
                           'href': f"/cairo/{c['name']}/"} for c in cs]}
