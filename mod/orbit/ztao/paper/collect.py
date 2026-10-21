"""Freeze everything the paper cites into paper/data/snapshot.json.

Costs are re-read from the module's own measurement files (data/*_gas.json,
data/last_sim_*.json, data/tests.json, adapter default tables) and mainnet
prices are fetched ONCE (mod economics(fresh=True)); the paper's tables and
figures are generated from this snapshot only, so a rebuild reproduces the
same numbers until collect.py is run again.

    python3 paper/collect.py            # refresh the snapshot (network)
    python3 paper/figures.py            # tables + figures from it (offline)
"""
import importlib.util
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
OUT = os.path.join(ROOT, 'paper', 'data', 'snapshot.json')


def load_mod():
    spec = importlib.util.spec_from_file_location(
        'ztao_src', os.path.join(SRC, '__init__.py'),
        submodule_search_locations=[SRC])
    pkg = importlib.util.module_from_spec(spec)
    sys.modules['ztao_src'] = pkg
    spec.loader.exec_module(pkg)
    spec = importlib.util.spec_from_file_location('ztao_mod', os.path.join(SRC, 'mod.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def per_action(m, NETS, chain):
    """Mean cost per action kind from the chain's last full epoch."""
    sim = m.last_sim(chain) or {}
    out = {}
    for e in sim.get('events') or []:
        if e['kind'] in ('register', 'submit', 'score', 'settle', 'claim'):
            out.setdefault(e['kind'], []).append(e)
    a = NETS.chain(chain)
    rows = {}
    for k, evs in out.items():
        cs = [a.cost(e) for e in evs]
        cs = [c for c in cs if c]
        if not cs:
            continue
        rows[k] = {f: round(sum(c.get(f) or 0 for c in cs) / len(cs), 3)
                   for f in a.cost_fields}
        rows[k]['n'] = len(cs)
        ring = next((e.get('ring') for e in evs if e.get('ring')), None)
        if ring:
            rows[k]['ring'] = ring
    res = sim.get('result') or {}
    return {'at': sim.get('at'), 'seconds': res.get('seconds'),
            'yuma_ok': res.get('yuma_ok'), 'double_claim': res.get('double_claim'),
            'actions': rows}


def main():
    mod = load_mod()
    m, nets = mod.Mod(), mod.NETS
    eco = m.economics(fresh=True)
    snap = {'at': int(time.time()), 'date': time.strftime('%Y-%m-%d', time.gmtime()),
            'tests': m.tests(), 'costs': m.costs(), 'economics': eco,
            'chains': {}}
    for a in nets:
        snap['chains'][a.chain] = {
            'title': a.title, 'max_ring': a.max_ring,
            'cost_fields': list(a.cost_fields), 'symbol': a.symbol,
            'ui': a.ui, 'sim': per_action(m, nets, a.chain)}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(snap, open(OUT, 'w'), indent=1, default=str)
    print(OUT, {c: bool(v['sim']['actions']) for c, v in snap['chains'].items()})


if __name__ == '__main__':
    main()
