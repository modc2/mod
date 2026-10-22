"""What a subnet costs to run as it grows, and what each way of compressing
the gas saves.

Inputs are the dollar prices economics() already measured (live gas x token
price applied to the costs measured on the local chain); this file does only
arithmetic, so it runs offline and is tested on fixed numbers.

Per epoch, today's contract costs

    M x submit(r)  +  V x M x score(r)  +  M x settle  +  M x claim(r)

with M miners, V validators and r the ring (anonymity set) each signature
hides in. The V x M score term is the whole problem: it grows with the
PRODUCT of the two sets and each one is a full ring verification.

The ladder of fixes, each one including the ones before it:

  baseline   the contract as deployed (measured).
  batch      one ring signature per validator per epoch over the WHOLE score
             vector (Bittensor's set_weights), nullifier per (epoch) instead
             of per (epoch, tag): V verifications instead of V x M. One
             settle_all per epoch instead of M settle txs.
  lazy       + a miner claims K epochs in one signature (points accrue per
             epoch, the claim proves the K tags): claim cost / K.
  proof      + every signature is checked OFF chain and the epoch lands as
             one validity proof (all LSAGs valid, nullifiers distinct, the
             Zuma consensus result is X): one verify + M result writes, plus
             the prover's compute.

batch/lazy/proof are the cheapest, but each one LINKS actions that today's
contract keeps apart (see PRIVACY): batch publishes one validator's whole
score vector under one nullifier, lazy ties K of a miner's epoch tags
together. The privacy-preserving ladder keeps every signature exactly as it
is today (per-tag nullifiers, per-epoch tags) and only changes who checks it:

  committee  sortition: validator v scores tag t only when
             H(nullifier_v(epoch, t)) < C / V. The nullifier is already
             public and only v can compute it, so the committee is secret
             until it acts, verifiable by anyone and cannot be ground (the
             key is fixed at registration, the scope carries an epoch
             beacon). ~C scores per tag instead of V: V x M -> C x M.
  private    + every submit, score and claim (one per epoch, to a fresh
             payout address) is verified OFF chain and the epoch lands as
             one validity proof. The per-tag nullifiers are epoch-scoped, so
             their uniqueness is checked inside the proof and never written;
             on chain: one verify, M tags, M consensus results, M claim
             records.
             Individual scores need not be published at all.

The cost of committee is security, not privacy: Zuma's majority clip now
holds per committee (quorum ceil(max(C, n_t) / 2)), so a coalition of a
fraction f of the validators captures a tag with probability
capture(V, C, f) instead of 0 for f < 1/2, and by abstaining zeroes it with
probability starve(V, C, f).

Everything but baseline is MODELED, not measured: they need contract
changes. The per-entry write and proof-verify costs below are estimates and
are labelled as such wherever they are shown.
"""
import math

STRATEGIES = ('baseline', 'batch', 'lazy', 'proof', 'committee', 'private')
LABELS = {
    'baseline': 'today: one ring sig per score',
    'batch': 'one sig per validator per epoch',
    'lazy': '+ claim K epochs at once',
    'proof': '+ one validity proof per epoch',
    'committee': 'secret committee of ~C scores each tag',
    'private': '+ every sig checked in one validity proof',
}
# two ladders, each rung includes the ones before it: the linking one trades
# unlinkability for gas, the private one keeps exactly today's
LADDERS = {'linking': ('baseline', 'batch', 'lazy', 'proof'),
           'private': ('baseline', 'committee', 'private')}
# what an observer learns beyond today's contract
PRIVACY = {
    'baseline': (True, 'nothing beyond the ring: every score has its own '
                 'nullifier, every epoch its own tag'),
    'batch': (False, "one validator's scores for every tag in an epoch are "
              'linked into one vector, a fingerprint that can follow it '
              'across epochs'),
    'lazy': (False, "a miner's K epoch tags are linked by the one claim "
             '(the same as claiming K times to one address)'),
    'proof': (False, 'inherits batch and lazy linkage'),
    'committee': (True, 'nothing: selection is a function of the public '
                  'nullifier; costs security (capture), not privacy'),
    'private': (True, 'less than today: per-tag scores can stay inside the '
                'proof, only consensus results are published'),
}
COMMITTEE = 32            # expected scores per tag under sortition
# a batched score entry updates ~4 slots (sum, n, hist, top); a settle writes
# 5 and reads <=101, so an entry is priced at a quarter of a measured settle
ENTRY_OF_SETTLE = 0.25
# off-chain proving: LSAG ~4k Cairo steps per ring member (ring 4 claim
# measured ~16k); Stwo/Stone CPU proving is ~1e6 steps/s on a ~$0.05/h core,
# i.e. ~$0.00001 per M steps — priced 10x higher to cover recursion/overhead
STEPS_PER_MEMBER = 4_000
PROVER_USD_PER_MSTEP = 0.0001
# one on-chain validity-proof check, in each chain's own cost units (priced
# by its adapter's fee()): a Groth16 wrap of the STARK — ~350k gas on the EVM
# (pairing precompile), ~300k CU with Solana's alt_bn128 syscalls, ~30 Tgas
# with NEAR's alt_bn128 host fns, ~50M L2 gas for a Garaga verifier on
# Starknet. Estimates; a chain missing here is priced at 30 ring scores.
PROOF_COST = {
    'eth': {'gas': 350_000},
    'solana': {'txs': 1, 'cu': 300_000},
    'near': {'tgas': 30},
    'starknet': {'steps': 1, 'l2_gas': 50_000_000},
}


def fit(by_ring):
    """score $ = a + b x ring, least squares over the by_ring table."""
    pts = [(p['ring'], p['usd']) for p in by_ring or []
           if p.get('ring') and p.get('usd')]
    if not pts:
        return None
    if len(pts) == 1:
        r, u = pts[0]
        return 0.0, u / r
    n = len(pts)
    sx = sum(r for r, _ in pts)
    sy = sum(u for _, u in pts)
    sxx = sum(r * r for r, _ in pts)
    sxy = sum(r * u for r, u in pts)
    b = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    a = (sy - b * sx) / n
    if b <= 0:                         # degenerate table: fall back to linear
        return 0.0, sy / sx
    return max(a, 0.0), b


def units(row, proof_usd=None):
    """Per-action dollar prices for one chain from its economics() row, or
    None when the chain has no priced run yet."""
    acts = row.get('actions') or {}
    ab = fit(row.get('by_ring'))
    score = (acts.get('score') or {}).get('usd')
    meas = row.get('ring')
    if not ab and score and meas:
        ab = (0.0, score / meas)
    if not ab:
        return None
    a, b = ab
    at = a + b * (meas or 4)

    def rel(k):  # submit/claim are ring-signed too: same curve, own ratio
        u = (acts.get(k) or {}).get('usd')
        return u / at if u and at else 1.0
    settle = (acts.get('settle') or {}).get('usd') or at * 0.1
    aph = row.get('actions_per_hour')
    return {'a': a, 'b': b, 'ring': meas, 'submit_k': rel('submit'),
            'claim_k': rel('claim'), 'settle': settle,
            'register': (acts.get('register') or {}).get('usd') or settle,
            'entry': settle * ENTRY_OF_SETTLE, 'proof': proof_usd,
            'score_at': at, 'max_ring': row.get('max_ring'),
            # blockspace: how many measured-ring scores fit in an hour
            'scores_per_hour': aph}


def sig(u, r):
    return u['a'] + u['b'] * r


def _committee(V, C, f):
    """Selected coalition / honest counts under sortition: two independent
    binomials with p = C / V (pmfs), and the quorum rule. Zuma's committee
    quorum mirrors V_t = max(V, n_t): k = ceil(max(C, n_t) / 2)."""
    k = int(round(V * f))
    h = V - k
    p = min(C / V, 1.0)

    def pmf(n):
        return [math.comb(n, i) * p ** i * (1 - p) ** (n - i)
                for i in range(n + 1)]
    return pmf(k), pmf(h)


def capture(V, C, f):
    """P(a coalition holding a fraction f of the V validators reaches the
    quorum of one tag's sortition committee while every honest member it
    drew votes), i.e. it sets that tag's consensus. C >= V means everyone
    scores: the deterministic Zuma bound, 0 for f < 1/2."""
    if C >= V:
        k = int(round(V * f))
        return 1.0 if k and 2 * k >= V else 0.0
    pa, ph = _committee(V, C, f)
    return sum(x * y for a, x in enumerate(pa) if a for hh, y in enumerate(ph)
               if 2 * a >= max(C, a + hh))


def starve(V, C, f):
    """P(the honest validators drawn for a tag are fewer than the quorum
    ceil(C / 2)), so a coalition of fraction f that ABSTAINS zeroes the tag.
    The liveness price of committees; 0 when everyone scores and f < 1/2."""
    if C >= V:
        return 0.0 if 2 * int(round(V * f)) < V else 1.0
    _, ph = _committee(V, C, f)
    return sum(y for hh, y in enumerate(ph) if 2 * hh < C)


def epoch_cost(u, M, V, ring, strategy='baseline', K=24, C=COMMITTEE):
    """Dollar breakdown of ONE epoch. ring is capped by each set's size;
    C is the expected committee size of the privacy-preserving ladder."""
    rm, rv = max(1, min(ring, M)), max(1, min(ring, V))
    c = min(max(C, 1), V)
    s = sig(u, rv)
    sub = M * u['submit_k'] * sig(u, rm)
    clm = M * u['claim_k'] * sig(u, rm)
    out = {'submit': sub, 'score': 0.0, 'settle': 0.0, 'claim': 0.0,
           'proof': 0.0, 'prover': 0.0}
    if strategy == 'baseline':
        out.update(score=V * M * s, settle=M * u['settle'], claim=clm)
    elif strategy in ('batch', 'lazy'):
        out.update(score=V * (s + M * u['entry']),
                   settle=u['settle'] + max(M - 1, 0) * u['entry'],
                   claim=clm / (K if strategy == 'lazy' else 1))
    elif strategy == 'proof':
        # submits and scores are verified inside the proof; their nullifiers
        # and the M results still land on chain as plain writes
        steps = (M * rm + V * rv + M * rm / K) * STEPS_PER_MEMBER
        out.update(submit=M * u['entry'], score=V * u['entry'],
                   settle=M * u['entry'], claim=clm / K,
                   proof=u['proof'] if u['proof'] is not None
                   else 30 * u['score_at'],
                   prover=steps / 1e6 * PROVER_USD_PER_MSTEP)
    elif strategy == 'committee':
        out.update(score=c * M * s, settle=M * u['settle'], claim=clm)
    elif strategy == 'private':
        # same signatures as today (claims every epoch, K = 1), all verified
        # in the proof; per-tag nullifiers are epoch-scoped and never stored
        steps = (M * rm + c * M * rv + M * rm) * STEPS_PER_MEMBER
        out.update(submit=M * u['entry'], score=0.0, settle=M * u['entry'],
                   claim=M * u['entry'],
                   proof=u['proof'] if u['proof'] is not None
                   else 30 * u['score_at'],
                   prover=steps / 1e6 * PROVER_USD_PER_MSTEP)
    else:
        raise ValueError(f'strategy one of {STRATEGIES}')
    out['total'] = sum(out.values())
    return out


def curve(start, end, months, shape='exp'):
    """Set size per month 0..months, from start to end."""
    start, end, months = max(1, int(start)), max(1, int(end)), max(1, int(months))
    out = []
    for t in range(months + 1):
        f = t / months
        if shape == 'linear':
            v = start + (end - start) * f
        elif shape == 'logistic':      # slow start, fast middle, saturates
            k = 1 / (1 + math.exp(-10 * (f - 0.5)))
            k0, k1 = 1 / (1 + math.exp(5)), 1 / (1 + math.exp(-5))
            v = start + (end - start) * (k - k0) / (k1 - k0)
        else:
            v = start * (end / start) ** f
        out.append(max(1, round(v)))
    return out


def project(u, miners=(16, 256), validators=(4, 64), months=12, shape='exp',
            ring=16, epoch_hours=1.0, K=None, C=COMMITTEE, f=0.25):
    """Month-by-month cost of every strategy as the sets grow.

    Each row: month, M, V, ring, epochs that month, per strategy the $ of one
    epoch, of the month and cumulative, $ per miner and per validator per
    month, and the share of the chain's blockspace one epoch needs."""
    epoch_hours = max(float(epoch_hours), 1.0)
    K = int(K or max(1, round(24 / epoch_hours)))   # default: claim daily
    if u.get('max_ring'):
        ring = min(int(ring), int(u['max_ring']))
    Ms = curve(miners[0], miners[1], months, shape)
    Vs = curve(validators[0], validators[1], months, shape)
    epochs = 730 / epoch_hours                         # per month (30.4 d)
    cum = dict.fromkeys(STRATEGIES, 0.0)
    rows, prevM, prevV = [], 0, 0
    for t, (M, V) in enumerate(zip(Ms, Vs)):
        joined = max(M - prevM, 0) + max(V - prevV, 0)
        prevM, prevV = M, V
        row = {'month': t, 'miners': M, 'validators': V,
               'ring': min(ring, M, V), 'epochs': epochs, 'joined': joined}
        for st in STRATEGIES:
            e = epoch_cost(u, M, V, ring, st, K, C)
            month = e['total'] * epochs + joined * u['register']
            cum[st] += month
            miner = (e['submit'] + e['claim']) * epochs / M
            val = e['score'] * epochs / V
            # blockspace: epoch $ in units of measured-ring scores vs what
            # the chain carries in one epoch (approx: cost ~ load)
            cap = (u['scores_per_hour'] or 0) * epoch_hours
            share = (e['total'] / u['score_at'] / cap) if cap and u['score_at'] \
                else None
            row[st] = {'epoch': e['total'], 'month': month, 'cum': cum[st],
                       'per_miner': miner, 'per_validator': val,
                       'blockspace': share,
                       'parts': {k: v for k, v in e.items() if k != 'total'}}
        row['capture'] = capture(V, C, f)
        row['starve'] = starve(V, C, f)
        rows.append(row)
    last = rows[-1]
    base = last['baseline']['month']
    return {'rows': rows, 'K': K, 'epoch_hours': epoch_hours, 'ring': ring,
            'strategies': list(STRATEGIES), 'labels': LABELS,
            'ladders': {k: list(v) for k, v in LADDERS.items()},
            'privacy': {k: {'kept': v[0], 'note': v[1]}
                        for k, v in PRIVACY.items()},
            'committee': C, 'coalition': f,
            'capture': capture(last['validators'], C, f),
            'starve': starve(last['validators'], C, f),
            'savings': {st: (base / last[st]['month']) if last[st]['month']
                        else None for st in STRATEGIES},
            'units': u}
