"""starknet/api/contracts.py — every contract this app knows, in one list.

Two kinds:

    yours    the Cairo contracts in the cairo workbench (each one is a mod at
             cairo/src/mods/<name>), with build state and — if it has been
             deployed to the local devnet — its address there, plus zuma's
             own devnet deployment
    known    well-known mainnet contracts: ETH, STRK, the stablecoins, the
             STRK20 privacy pool and the Pragma oracle

    contracts(network)   {yours: [...], known: [...]}

Read-only. The workbench being down is reported, not fatal.
"""

import json
import os

import chain
import stables
import strk20
import studio

ORBIT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SANDBOX = os.path.join(ORBIT, 'cairo', 'src', '.sandbox', 'deployments.json')
ZUMA = os.path.join(ORBIT, 'ztensor', 'data', 'deployments.json')


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001 — absent file = nothing deployed
        return {}


def _yours():
    err = None
    try:
        rows = studio.call('contracts', {}).get('contracts') or []
    except Exception as e:  # noqa: BLE001
        rows, err = [], str(e)
    dep = _load(SANDBOX)
    out = []
    for c in rows:
        d = dep.get(c['name']) or {}
        out.append({'name': c['name'], 'description': c.get('description'),
                    'template': c.get('template'), 'state': c.get('state'),
                    'mod': f"cairo.{c['name']}", 'app': f"/cairo/{c['name']}/",
                    'devnet': {'address': d['address'], 'class_hash': d.get('class_hash'),
                               'tx': d.get('tx'), 'by': d.get('by'), 'at': d.get('at'),
                               'port': 51292} if d.get('address') else None})
    z = (_load(ZUMA).get('devnet') or {})
    if z.get('address'):
        out.append({'name': 'zuma (mod)', 'description': 'The zuma mod\'s own LSAG subnet contract.',
                    'template': None, 'state': None, 'mod': 'ztensor', 'app': None,
                    'devnet': {'address': z['address'], 'class_hash': z.get('class_hash'),
                               'port': 51332}})
    return out, err


def _known(net):
    out = [{'name': k.upper(), 'kind': 'token', 'address': chain.TOKENS[k]['address'],
            'note': 'Native gas token' if k in ('eth', 'strk') else None}
           for k in ('eth', 'strk')]
    for t in stables.registry(network=net)['tokens']:
        out.append({'name': t['symbol'], 'kind': 'stablecoin', 'address': t['address'],
                    'note': t.get('name')})
    try:
        out.append({'name': 'STRK20 pool', 'kind': 'privacy', 'address': strk20.pool(net),
                    'note': 'Shielded notes; public side only'})
    except ValueError:
        pass
    if stables.PRAGMA.get(net):
        out.append({'name': 'Pragma oracle', 'kind': 'oracle', 'address': stables.PRAGMA[net],
                    'note': 'Price feeds used for stablecoin pegs'})
    return out


def contracts(network=None):
    net = network or chain.DEFAULT_NETWORK
    yours, err = _yours()
    return {'network': net, 'yours': yours, 'known': _known(net),
            'workbench': {'ok': err is None, 'error': err}}
