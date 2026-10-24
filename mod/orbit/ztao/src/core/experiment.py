"""How big can a subnet get on ONE chain, and what does an epoch cost there?

A sweep over epoch length x miners x validators, for one chain at a time.
Two things decide the answer and they move differently:

  dollars    one epoch is the same set of transactions whatever its length
             (M submits, V x M scores, M settles, M claims for today's
             contract), so $ / epoch does NOT depend on the epoch length;
             $ / day does (24 / hours epochs a day).
  blockspace an epoch must fit in the blocks it lasts. A longer epoch has more
             blocks, so the largest subnet that fits grows with it. `share` is
             the fraction of the chain the subnet may take (the rest is other
             people's traffic).

`max` = the largest set that satisfies the blockspace share (and, if given, a
$ / day budget) with every member acting. The cost of one epoch is then
simulated `trials` times: gas x token price drawn lognormal(0, vol), each
miner and validator shows up with probability drawn uniform(p_min, 1); p5 /
p50 / p95 of $ / epoch are reported per cell.

Pure arithmetic on what economics() measured (forecast.units), so it runs
offline and is tested on fixed numbers. Strategies other than baseline are
the MODELED contract changes from core/forecast.
"""
import math
import random

from . import forecast as fc

HOURS = (1, 2, 4, 6, 12, 24)
MINERS = (4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096)
VALIDATORS = (4, 8, 16, 32, 64, 128, 256)
BITTENSOR = {'miners': 256, 'validators': 64}   # one Bittensor subnet's UIDs
CEIL = 1 << 22                                   # search ceiling for max()


def load_units(row, cap_proof=None):
    """forecast.units() again, but in the chain's own capacity unit (gas, CU,
    Tgas, ms) instead of dollars, so epoch_cost() can price blockspace. None
    when the adapter reports no per-action load."""
    acts = row.get('actions') or {}
    score = (acts.get('score') or {}).get('load')
    meas = row.get('ring')
    pts = [{'ring': p['ring'], 'usd': p['load']} for p in row.get('by_ring') or []
           if p.get('load') and not p.get('est')]
    ab = fc.fit(pts)
    if not ab and score and meas:
        ab = (0.0, score / meas)
    if not ab:
        return None
    a, b = ab
    at = a + b * (meas or 4)

    def rel(k):
        v = (acts.get(k) or {}).get('load')
        return v / at if v and at else 1.0
    settle = (acts.get('settle') or {}).get('load') or at * 0.1
    return {'a': a, 'b': b, 'ring': meas, 'submit_k': rel('submit'),
            'claim_k': rel('claim'), 'settle': settle,
            'register': (acts.get('register') or {}).get('load') or settle,
            'entry': settle * fc.ENTRY_OF_SETTLE,
            'proof': cap_proof if cap_proof is not None else 30 * at,
            'score_at': at, 'max_ring': row.get('max_ring'),
            'scores_per_hour': None}


def epoch_load(lu, M, V, ring, strategy, K, C=fc.COMMITTEE):
    e = fc.epoch_cost(lu, M, V, ring, strategy, K, C)
    return e['total'] - e['prover']        # off-chain proving uses no blocks


def per_hour(row):
    """Capacity units the chain produces per hour, or None."""
    cap, bs = row.get('block_capacity'), row.get('block_seconds')
    return cap * 3600 / bs if cap and bs else None


def _largest(ok):
    """Largest n in [1, CEIL] with ok(n), ok monotone decreasing; 0 if none."""
    if not ok(1):
        return 0
    lo, hi = 1, 2
    while hi <= CEIL and ok(hi):
        lo, hi = hi, hi * 2
    if hi > CEIL:
        return CEIL
    while hi - lo > 1:
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if ok(mid) else (lo, mid)
    return lo


def limits(u, lu, cap_h, hours, ring, strategy, K, share, budget_day):
    """The predicate 'an M x V subnet fits' for one epoch length."""
    room = cap_h * hours * share if cap_h and lu else None
    per_day = 24 / hours

    def ok(M, V):
        if room is not None and epoch_load(lu, M, V, ring, strategy, K) > room:
            return False
        if budget_day and fc.epoch_cost(u, M, V, ring, strategy, K)['total'] \
                * per_day > budget_day:
            return False
        return True
    return ok, room


def simulate(u, M, V, ring, strategy, K, trials, vol, p_min, rng):
    """$ of one epoch under random gas and turnout: (p5, p50, p95)."""
    out = []
    for _ in range(trials):
        g = math.exp(rng.gauss(0, vol) - vol * vol / 2)   # mean-1 lognormal
        m = sum(rng.random() < rng.uniform(p_min, 1) for _ in range(M)) \
            if M <= 64 else max(1, round(M * rng.uniform(p_min, 1)))
        v = sum(rng.random() < rng.uniform(p_min, 1) for _ in range(V)) \
            if V <= 64 else max(1, round(V * rng.uniform(p_min, 1)))
        out.append(fc.epoch_cost(u, max(m, 1), max(v, 1), ring, strategy, K)
                   ['total'] * g)
    out.sort()
    q = lambda p: out[min(len(out) - 1, int(p * len(out)))]  # noqa: E731
    return q(0.05), q(0.5), q(0.95)


def run(row, u, lu, hours=HOURS, miners=MINERS, validators=VALIDATORS,
        ring=16, strategy='baseline', share=0.1, budget_day=None, trials=200,
        vol=0.5, p_min=0.8, seed=7):
    """The whole experiment for one chain (see module doc)."""
    if strategy not in fc.STRATEGIES:
        raise ValueError(f'strategy one of {fc.STRATEGIES}')
    if u.get('max_ring'):
        ring = min(int(ring), int(u['max_ring']))
    share = min(max(float(share), 1e-4), 1.0)
    cap_h = per_hour(row)
    rng = random.Random(seed)
    sims = {}                                   # (M, V, K) -> (p5, p50, p95)
    out_h = []
    for H in hours:
        K = max(1, round(24 / H))               # lazy: claim once a day
        ok, room = limits(u, lu, cap_h, H, ring, strategy, K, share, budget_day)
        sq = _largest(lambda n: ok(n, n))
        maxM = {V: _largest(lambda m: ok(m, V)) for V in validators}
        maxV = {M: _largest(lambda v: ok(M, v)) for M in miners}
        cells = []
        for V in validators:
            for M in miners:
                key = (M, V, K if strategy in ('lazy', 'proof') else 0)
                if key not in sims:
                    sims[key] = simulate(u, M, V, ring, strategy, K, trials,
                                         vol, p_min, rng)
                e = fc.epoch_cost(u, M, V, ring, strategy, K)
                ld = epoch_load(lu, M, V, ring, strategy, K) if lu else None
                p5, p50, p95 = sims[key]
                cells.append({
                    'miners': M, 'validators': V, 'usd': e['total'],
                    'p5': p5, 'p50': p50, 'p95': p95,
                    'usd_day': e['total'] * 24 / H,
                    'share': ld / (cap_h * H) if ld and cap_h else None,
                    'fits': ok(M, V),
                    'parts': {k: v for k, v in e.items() if k != 'total'}})
        bt = fc.epoch_cost(u, BITTENSOR['miners'], BITTENSOR['validators'],
                           ring, strategy, K)['total']
        out_h.append({
            'hours': H, 'K': K, 'blocks': (H * 3600 / row['block_seconds'])
            if row.get('block_seconds') else None,
            'room': room, 'max_square': sq,
            'max_miners': [{'validators': V, 'miners': maxM[V]} for V in validators],
            'max_validators': [{'miners': M, 'validators': maxV[M]} for M in miners],
            'bittensor': {**BITTENSOR, 'usd': bt, 'usd_day': bt * 24 / H,
                          'fits': ok(BITTENSOR['miners'], BITTENSOR['validators'])},
            'cells': cells})
    return {'hours': list(hours), 'miners': list(miners),
            'validators': list(validators), 'ring': ring,
            'strategy': strategy, 'label': fc.LABELS[strategy],
            'privacy': {'kept': fc.PRIVACY[strategy][0],
                        'note': fc.PRIVACY[strategy][1]},
            'share': share, 'budget_day': budget_day, 'trials': trials,
            'vol': vol, 'p_min': p_min, 'cap_per_hour': cap_h,
            'capacity_unit': row.get('capacity_unit'),
            'capacity_estimated': bool(row.get('capacity_estimated')),
            'tx_cap': row.get('tx_cap'), 'block_seconds': row.get('block_seconds'),
            'blockspace_known': bool(cap_h and lu), 'epochs': out_h}
