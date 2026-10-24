"""starknet/api/fund.py — fund every account and contract the Starknet mods own.

The Starknet mods that touch a chain each run a local starknet-devnet:

    cairo   :51292  sandbox for every contract in cairo/src/mods/ — accounts
                    alice/bob/carol/dave + one deployment per contract
    zuma    :51332  the anon subnet — predeployed accounts + the Zuma contract

starktime (paper ledger), zquorum (cairo-run, no chain) and zkwasm (scaffold)
hold nothing on chain, so they are reported and skipped.

Devnet ETH (WEI) and STRK (FRI) are minted for free with POST /mint, so
"funding" here means: top every address up to `target` whole tokens of each.
It only mints the shortfall, so a second run does nothing. Mainnet is never
touched — no key is held, and real STRK cannot be minted.

    balances()               every address, both tokens, no writes
    fund(target=1000)        top every address up to target ETH + STRK
"""

import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ORBIT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UNITS = {'ETH': 'WEI', 'STRK': 'FRI'}
ONE = 10 ** 18
OFFCHAIN = {'starktime': 'paper ledger, no chain',
            'zquorum': 'proofs run in cairo-run, no chain',
            'zkwasm': 'scaffold, no chain'}


def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _http(url, body=None, timeout=5):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data,
                                 headers={'content-type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b'null')


def _alive(port):
    # /is_alive answers plain text, so no _http here
    try:
        urllib.request.urlopen(f'http://127.0.0.1:{port}/is_alive', timeout=1.5)
        return True
    except Exception:  # noqa: BLE001
        return False


# ── targets: [{mod, port, name, kind, address}] ──

def _cairo():
    sb = os.path.join(ORBIT, 'cairo', 'src', '.sandbox')
    out = []
    for section in _load(os.path.join(sb, 'accounts.json'), {}).values():
        for name, a in section.items():
            out.append({'name': name, 'kind': 'account', 'address': a['address']})
    for name, d in _load(os.path.join(sb, 'deployments.json'), {}).items():
        out.append({'name': f"cairo.{name}", 'kind': 'contract',
                    'address': d['address']})
    return 51292, out


def _zuma():
    port = 51332
    out = []
    try:
        rows = _http(f'http://127.0.0.1:{port}/predeployed_accounts')
        out += [{'name': f'account {i}', 'kind': 'account', 'address': r['address']}
                for i, r in enumerate(rows)]
    except Exception:  # noqa: BLE001 — down: reported by status
        pass
    dep = _load(os.path.join(ORBIT, 'ztensor', 'data', 'deployments.json'), {})
    if (dep.get('devnet') or {}).get('address'):
        out.append({'name': 'zuma', 'kind': 'contract',
                    'address': dep['devnet']['address']})
    return port, out


SOURCES = {'cairo': _cairo, 'zuma': _zuma}


def _targets():
    chains = []
    for mod, fn in SOURCES.items():
        port, rows = fn()
        chains.append({'mod': mod, 'port': port, 'up': _alive(port), 'targets': rows})
    return chains


def _balance(port, address, unit):
    r = _http(f'http://127.0.0.1:{port}/account_balance?address={address}&unit={unit}')
    return int(r['amount'])


def _mint(port, address, amount, unit):
    _http(f'http://127.0.0.1:{port}/mint',
          {'address': address, 'amount': amount, 'unit': unit})


def _one(port, t, target_wei):
    row = dict(t)
    for sym, unit in UNITS.items():
        try:
            have = _balance(port, t['address'], unit)
            minted = 0
            if target_wei is not None and have < target_wei:
                minted = target_wei - have
                _mint(port, t['address'], minted, unit)
                have = target_wei
            row[sym] = have / ONE
            if minted:
                row.setdefault('minted', {})[sym] = minted / ONE
        except Exception as e:  # noqa: BLE001
            row.setdefault('errors', []).append(f'{sym}: {e}')
    return row


def _run(target):
    target_wei = None if target is None else int(float(target) * ONE)
    chains = _targets()
    jobs = []
    with ThreadPoolExecutor(8) as ex:
        for c in chains:
            if c['up']:
                jobs.append((c, [ex.submit(_one, c['port'], t, target_wei)
                                 for t in c['targets']]))
            else:
                c['note'] = f"devnet :{c['port']} is down — start the {c['mod']} mod"
        for c, futs in jobs:
            c['targets'] = [f.result() for f in futs]
    minted = sum(1 for c in chains for t in c['targets'] if t.get('minted'))
    return {'network': 'devnet', 'target': target, 'chains': chains,
            'funded': minted,
            'addresses': sum(len(c['targets']) for c in chains),
            'offchain': OFFCHAIN,
            'mainnet': 'not touched: no key is held here and real STRK cannot be minted'}


def balances():
    """Every devnet account and contract of the Starknet mods, ETH + STRK."""
    return _run(None)


def fund(target=1000):
    """Top every devnet account and contract up to `target` ETH and STRK."""
    target = float(target)
    if not 0 < target <= 1e9:
        raise ValueError('target must be between 0 and 1e9 tokens')
    return _run(target)
