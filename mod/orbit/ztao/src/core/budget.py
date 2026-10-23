"""Is it affordable? — the feasibility test and the scale curve.

core/forecast.py prices one epoch. This file asks the two questions that
actually decide whether a subnet can exist on a chain:

  feasible()  can an M x V subnet run for under $X a week, and with which
              (strategy, epoch length)? One PASS/FAIL per cell, the cheapest
              passing cell, and the largest subnet that cell still affords.
  scale()     how the weekly bill grows with the set sizes, past 10,000 of
              each. The headline is the GROWTH ORDER, measured as the
              log-log slope of the last two points, because that is what
              decides whether a subnet can ever be big:

                  baseline    O(V x M)  every validator signs every tag
                  batch       O(V x M)  batching the signatures still leaves
                                        a score ENTRY per (validator, tag)
                  committee   O(C x M)  a fixed-size committee signs
                  secagg      O(M)      the scores leave the chain entirely
                  aggregate   O(1)      one transaction per epoch, whatever
                                        the size; only withdrawals scale,
                                        and those amortize over K epochs

ORDER states the band each slope must land in and the tests hold the
arithmetic to it — which is how `batch` got caught claiming O(V + M).

TARGET is the bar this module holds itself to: one dollar a week for a
Bittensor-sized subnet (256 miners, 64 validators). Running cost and payout
cost are reported apart, because they are paid by different people — the
subnet pays to reach consensus every epoch whether or not anyone withdraws,
a miner pays to withdraw when it chooses to.

Everything here is arithmetic over forecast.units(), so it runs offline.
"""
import math

from . import forecast as fc

WEEK_H = 168
TARGET = 1.0                     # $ per week...
TARGET_SET = (256, 64)           # ...for this many miners, validators
HOURS = (1, 2, 4, 6, 12, 24)
WITHDRAW_DAYS = 30               # how often a miner takes its emission out
SIZES = (64, 256, 1024, 4096, 10_000, 16_384, 65_536)
RATIO = 4                        # miners per validator (256 / 64)
CEILING = 1 << 20                # where the 'largest set that fits' search stops


def weekly(u, M, V, ring=16, strategy='baseline', epoch_hours=1.0, K=None,
           C=fc.COMMITTEE, withdraw_days=WITHDRAW_DAYS):
    """One week of an M x V subnet, split by WHO PAYS:

      running  reaching consensus — submits, scores, settle, the proof. The
               subnet pays it every epoch whether or not anyone withdraws.
      payout   miners taking their emission out, once every `withdraw_days`.
               A miner picks its own cadence, so this term is a policy
               choice, not a property of the chain; it is reported apart so
               it cannot hide inside a headline number.
    """
    epoch_hours = max(float(epoch_hours), 1.0)
    K = int(K or max(1, round(float(withdraw_days) * 24 / epoch_hours)))
    e = fc.epoch_cost(u, int(M), int(V), int(ring), strategy, K, C)
    n = WEEK_H / epoch_hours
    run = (e['submit'] + e['score'] + e['settle'] + e['proof'] + e['prover']) * n
    pay = e['claim'] * n
    return {'miners': int(M), 'validators': int(V), 'strategy': strategy,
            'epoch_hours': epoch_hours, 'K': K, 'epochs': n,
            'withdraw_days': float(withdraw_days),
            'epoch_usd': e['total'], 'running': run, 'payout': pay,
            'total': run + pay, 'per_miner': (e['submit'] + e['claim']) * n / M,
            'per_validator': e['score'] * n / V,
            'parts': {k: v * n for k, v in e.items() if k != 'total'}}


# What each rung's cost does as the sets grow, with the log-log slope the
# arithmetic must actually show over SIZES (tests pin every measured slope
# inside its band, so a claim here cannot drift away from the model).
# Note batch/lazy/proof: batching the SIGNATURES leaves V x M score ENTRIES
# behind, so they stay quadratic in writes — only moving the scores off the
# chain altogether (secagg) breaks that.
ORDER = {
    'baseline':  {'label': 'O(V x M) sigs', 'slope': (1.8, 2.2)},
    'batch':     {'label': 'O(V) sigs + O(V x M) writes', 'slope': (1.5, 2.2)},
    'lazy':      {'label': 'O(V) sigs + O(V x M) writes', 'slope': (1.5, 2.2)},
    'proof':     {'label': 'O(V + M) writes + 1 proof', 'slope': (0.6, 1.3)},
    'committee': {'label': 'O(C x M) sigs', 'slope': (0.8, 1.3)},
    'secagg':    {'label': 'O(M) sigs', 'slope': (0.8, 1.3)},
    'private':   {'label': 'O(M) writes + 1 proof', 'slope': (0.6, 1.3)},
    'aggregate': {'label': 'O(1) + O(M / K)', 'slope': (0.0, 0.6)},
    'proved':    {'label': 'O(1) + O(M / K) + 1 proof', 'slope': (0.6, 1.3)},
}


def order(strategy):
    return (ORDER.get(strategy) or {}).get('label')


def _largest(ok, ceiling=CEILING):
    """Largest n with ok(n) (monotone), 0 if even 1 fails."""
    if not ok(1):
        return 0
    lo, hi = 1, 2
    while hi <= ceiling and ok(hi):
        lo, hi = hi, hi * 2
    if hi > ceiling:
        return ceiling
    while hi - lo > 1:
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if ok(mid) else (lo, mid)
    return lo


def feasible(u, target=TARGET, miners=TARGET_SET[0], validators=TARGET_SET[1],
             ring=16, hours=HOURS, strategies=fc.STRATEGIES, K=None,
             C=fc.COMMITTEE, caps=None, ratio=RATIO,
             withdraw_days=WITHDRAW_DAYS):
    """The feasibility grid for one chain: strategy x epoch length, each cell
    PASS/FAIL against `target` $/week for an M x V subnet.

    caps (optional, from the chain's economics row): {'tx_cap', 'unit',
    'proof_load', 'sig_load'} — a rung whose single biggest transaction does
    not fit the chain's per-transaction limit is marked `over_tx_cap` and
    cannot pass however cheap it looks."""
    M, V = int(miners), int(validators)
    target = float(target)
    caps = caps or {}
    cap, proof_load = caps.get('tx_cap'), caps.get('proof_load')
    over = bool(cap and proof_load and proof_load > cap)
    rows, best, best_key = [], None, None
    for st in strategies:
        needs_proof = st in ('proof', 'private', 'proved')
        blocked = over and needs_proof
        cells = []
        for H in hours:
            w = weekly(u, M, V, ring, st, H, K, C, withdraw_days)
            ok = w['total'] <= target and not blocked
            cells.append({'hours': H, 'K': w['K'], 'usd_week': w['total'],
                          'running': w['running'], 'payout': w['payout'],
                          'epoch_usd': w['epoch_usd'], 'pass': ok,
                          'headroom': (target / w['total']) if w['total'] else None,
                          'parts': w['parts']})
            # the shortest epoch that fits, then trustless over challengeable,
            # then cheapest: the config to actually deploy
            key = (H, st in fc.OPTIMISTIC, w['total'])
            if ok and (best_key is None or key < best_key):
                best, best_key = {'strategy': st, 'label': fc.LABELS[st],
                                  **cells[-1]}, key
        cheapest = min(cells, key=lambda c: c['usd_week'])
        rows.append({
            'strategy': st, 'label': fc.LABELS[st],
            'order': order(st), 'optimistic': st in fc.OPTIMISTIC,
            'privacy': {'kept': fc.PRIVACY[st][0], 'note': fc.PRIVACY[st][1]},
            'soundness': fc.SOUNDNESS[st], 'trust': fc.TRUST[fc.SOUNDNESS[st]],
            'needs_proof': needs_proof, 'over_tx_cap': blocked,
            'cheapest': cheapest['usd_week'], 'cheapest_hours': cheapest['hours'],
            'passes': [c['hours'] for c in cells if c['pass']], 'cells': cells})
    out = {'target': target, 'miners': M, 'validators': V, 'ring': ring,
           'hours': list(hours), 'committee': C, 'ratio': ratio,
           'strategies': list(strategies),
           'orders': {k: v['label'] for k, v in ORDER.items()},
           'feasible': best is not None, 'best': best,
           'tx_cap': cap, 'capacity_unit': caps.get('unit'),
           'proof_load': proof_load, 'proof_over_tx_cap': over, 'rows': rows}
    if best:
        st, H = best['strategy'], best['hours']
        out['max_miners'] = _largest(
            lambda m: weekly(u, m, max(1, m // ratio), ring, st, H, K, C,
                             withdraw_days)['total'] <= target)
        out['max_validators'] = _largest(
            lambda v: weekly(u, M, v, ring, st, H, K, C, withdraw_days)
            ['total'] <= target)
        # a rung whose cost does not depend on a set size has no largest
        # size: say so instead of reporting the search ceiling as an answer
        out['unbounded'] = [k for k in ('max_miners', 'max_validators')
                            if out[k] >= CEILING]
        out['search_ceiling'] = CEILING
    return out


def order_of(u, strategy, ring=16, epoch_hours=1.0, K=None, C=fc.COMMITTEE,
             ratio=RATIO, a=4096, b=16384, withdraw_days=WITHDRAW_DAYS):
    """Measured growth order: the log-log slope of $/week between two large
    sizes (M = n, V = n / ratio). ~2 = O(V x M), ~1 = linear, ~0 = flat."""
    ya, yb = (weekly(u, n, max(1, n // ratio), ring, strategy, epoch_hours, K,
                     C, withdraw_days)['total'] for n in (a, b))
    if not ya or not yb:
        return None
    return math.log(yb / ya) / math.log(b / a)


def scale(u, sizes=SIZES, ratio=RATIO, ring=16, strategy=None,
          strategies=fc.STRATEGIES, epoch_hours=1.0, K=None, C=fc.COMMITTEE,
          target=TARGET, withdraw_days=WITHDRAW_DAYS):
    """$ per week against set size, past 10,000 of each.

    Each row is one size: M = n miners, V = n / ratio validators (so the
    256 x 64 target set is the n = 256 row), and per strategy the weekly
    bill, the bill per miner, and whether it is still under `target`."""
    sizes = sorted({int(n) for n in sizes if int(n) > 0})
    sts = [strategy] if strategy else list(strategies)
    rows = []
    for n in sizes:
        V = max(1, n // ratio)
        row = {'n': n, 'miners': n, 'validators': V}
        for st in sts:
            w = weekly(u, n, V, ring, st, epoch_hours, K, C, withdraw_days)
            row[st] = {'usd_week': w['total'], 'running': w['running'],
                       'payout': w['payout'], 'epoch_usd': w['epoch_usd'],
                       'per_miner': w['per_miner'], 'under_target':
                       w['total'] <= float(target)}
        rows.append(row)
    big = [n for n in sizes if n >= 1024] or sizes
    a, b = (big[0], big[-1]) if len(big) > 1 else (sizes[0], sizes[-1])
    per_st = {}
    for st in sts:
        knee = _largest(lambda n: weekly(  # noqa: B023
            u, n, max(1, n // ratio), ring, st, epoch_hours, K, C,
            withdraw_days)['total'] <= float(target))
        per_st[st] = {
            'label': fc.LABELS[st], 'claimed_order': order(st),
            'slope_band': list(ORDER[st]['slope']) if st in ORDER else None,
            'slope': order_of(u, st, ring, epoch_hours, K, C, ratio, a, b,
                              withdraw_days),
            'fit_between': [a, b], 'max_n_under_target': knee,
            'optimistic': st in fc.OPTIMISTIC,
            'soundness': fc.SOUNDNESS[st]}
    return {'sizes': sizes, 'ratio': ratio, 'ring': ring, 'target': target,
            'epoch_hours': max(float(epoch_hours), 1.0), 'committee': C,
            'strategies': sts, 'growth': per_st, 'rows': rows}
