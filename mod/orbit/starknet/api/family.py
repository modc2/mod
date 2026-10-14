"""starknet/api/family.py — the other Starknet mods on this node, read live.

The starknet mod is the front door; the rest of the Starknet work lives in
sibling mods, each with its own port and its own app:

    cairo      contract workbench: scaffold, vibecode, build, test, deploy to
               a local devnet — every contract is its own app at /cairo/<c>/
    starktime  lock STRK for time; weight = amount x lock multiplier
    zquorum    a group reaches a quorum without revealing who voted
    zuma       Bittensor-style subnet where every action is a ring signature
               verified by a Starknet contract
    zkwasm     zero-knowledge WASM workbench (scaffold)

mods() probes each one's API in parallel (short timeout, 15s cache) and
returns what it is, whether it is up, a few live numbers, and where its app
lives. Nothing here writes; a mod that is down is reported, not started.

    mods()      the whole family, live
"""

import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ORBIT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PRIVATE = os.path.expanduser('~/.mod/build/private')
OVERRIDES = os.path.expanduser('~/.mod/caddy/overrides.json')
TTL = 15
_cache = {'at': 0, 'data': None}


def _get(url, body=None, timeout=2.5):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={'content-type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.loads(r.read() or b'null')
    return out.get('result', out) if isinstance(out, dict) and body is not None else out


def _num(v, d=2):
    if not isinstance(v, (int, float)):
        return str(v)
    return f'{v:,.0f}' if v == int(v) else f'{v:,.{d}f}'


def _short(a):
    return f'{a[:6]}…{a[-4:]}' if isinstance(a, str) and len(a) > 18 else a


# ── per-mod probes: each returns {facts: [[label, value]], items?: [...]} ──

def _cairo(port):
    base = f'http://127.0.0.1:{port}/cairo/api'
    cs = _get(f'{base}/contracts').get('contracts', [])
    tc = _get(f'{base}/toolchain')
    scarb = ((tc.get('toolchain') or tc).get('scarb') or {}).get('version', '')
    return {
        'facts': [['contracts', len(cs)],
                  ['built', sum(1 for c in cs if c.get('built'))],
                  ['scarb', scarb.split(' ')[1] if scarb.startswith('scarb ') else (scarb or '—')]],
        'items': [{'name': c['name'], 'desc': c.get('description') or '',
                   'state': c.get('state') or ('fresh' if c.get('built') else 'unbuilt'),
                   'href': f"/cairo/{c['name']}/"} for c in cs],
    }


def _starktime(port):
    s = _get(f'http://127.0.0.1:{port}/stats')
    return {'facts': [['mode', s.get('mode', '—')],
                      ['staked', f"{_num(s.get('total_staked', 0))} STRK"],
                      ['stakers', s.get('stakers', 0)],
                      ['epoch', s.get('epoch', '—')]]}


def _zquorum(port):
    base = f'http://127.0.0.1:{port}/zquorum/api'
    h = _get(f'{base}/health')
    qs = _get(f'{base}/list').get('quorums', [])
    sn = h.get('starknet') or {}
    dev = (sn.get('deployments') or {}).get('devnet')
    return {'facts': [['quorums', len(qs)],
                      ['on starknet', sn.get('anchored', 0)],
                      ['contract', _short(dev) if dev else 'not deployed'],
                      ['devnet', 'up' if sn.get('devnet_up') else 'down']],
            'items': [{'name': q.get('name') or q.get('quorum'),
                       'desc': ' · '.join(x for x in (
                           q.get('desc') or '',
                           f"on {q['chain']['network']}" if q.get('chain') else '') if x),
                       'state': 'reached' if q.get('reached') else 'open'} for q in qs[:8]]}


def _zuma(port):
    s = _get(f'http://127.0.0.1:{port}/status', body={})
    dev = (s.get('deployments') or {}).get('devnet') or {}
    ep = s.get('epoch') or {}
    h = s.get('health') or {}
    return {'facts': [['devnet', 'up' if h.get('devnet_up') else 'down'],
                      ['contract', _short(dev.get('address')) or 'not deployed'],
                      ['epoch', ep.get('epoch', '—')],
                      ['next in', f"{ep.get('blocks_left', '—')} blk"],
                      ['keys', len(s.get('keys') or [])]]}


def _zkwasm(port):
    i = _get(f'http://127.0.0.1:{port}/info')
    return {'facts': [['stage', i.get('stage', '—')], ['version', i.get('version', '—')]]}


FAMILY = [
    {'name': 'cairo', 'title': 'Cairo workbench', 'tag': 'build',
     'pitch': 'Write a Cairo contract, let an agent edit it, build and test it, '
              'then poke it on a local devnet. Every contract gets its own app.',
     'probe': _cairo},
    {'name': 'starktime', 'title': 'StarkTime', 'tag': 'stake',
     'pitch': 'Lock STRK for longer, earn more. Weight = amount x lock multiplier '
              '(1x flexible up to 6x at four years); rewards stream every second.',
     'probe': _starktime},
    {'name': 'zquorum', 'title': 'zQuorum', 'tag': 'privacy',
     'pitch': 'A group reaches a decision without anyone learning who voted. '
              'Commitments in, nullifiers out, all hashing done in Cairo.',
     'probe': _zquorum},
    {'name': 'zuma', 'title': 'Zuma', 'tag': 'privacy',
     'pitch': 'A Bittensor-style subnet where miners and validators stay anonymous: '
              'every action is a ring signature checked by a Starknet contract.',
     'probe': _zuma},
    {'name': 'zkwasm', 'title': 'zkWASM', 'tag': 'research',
     'pitch': 'Run WebAssembly programs and prove what they did. '
              'Early scaffold: the prover is not wired in yet.',
     'probe': _zkwasm, 'app': False},
]


def _config(name):
    try:
        return json.load(open(os.path.join(ORBIT, name, 'config.json')))
    except Exception:
        return {}


def _private(name):
    try:
        return bool(json.load(open(os.path.join(PRIVATE, f'{name}.json'))).get('enabled'))
    except Exception:
        return False


def _routed():
    try:
        return set(json.load(open(OVERRIDES)))
    except Exception:
        return set()


def _one(spec, routed):
    name = spec['name']
    cfg = _config(name)
    port = cfg.get('port')
    base = None if spec.get('app') is False else \
        cfg.get('base_path') or (f'/{name}' if cfg.get('app_port') else None)
    public = name in routed or (cfg.get('route') and not _private(name))
    out = {'name': name, 'title': spec['title'], 'tag': spec['tag'], 'pitch': spec['pitch'],
           'icon': cfg.get('icon') or name[0].upper(), 'version': cfg.get('version'),
           'description': cfg.get('description', ''), 'port': port,
           'app': f'{base}/' if base else None, 'public': bool(public),
           'up': False, 'facts': [], 'items': [], 'ms': None}
    if not port:
        out['error'] = 'no port in config.json'
        return out
    t = time.time()
    try:
        out.update(spec['probe'](port))
        out['up'] = True
    except Exception as e:
        out['error'] = str(e)[:160]
    out['ms'] = int((time.time() - t) * 1000)
    return out


def mods(fresh=False):
    if not fresh and _cache['data'] and time.time() - _cache['at'] < TTL:
        return _cache['data']
    routed = _routed()
    with ThreadPoolExecutor(len(FAMILY)) as ex:
        rows = list(ex.map(lambda s: _one(s, routed), FAMILY))
    data = {'mods': rows, 'up': sum(r['up'] for r in rows), 'count': len(rows),
            'at': int(time.time())}
    _cache.update(at=time.time(), data=data)
    return data


if __name__ == '__main__':
    print(json.dumps(mods(), indent=2))
