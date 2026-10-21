"""Yuma consensus for an anonymous subnet — the reference every chain matches.

Validators are anonymous, so there is no per-validator stake: a ring is one
tier and membership IS the stake (every validator weighs 1). kappa = 1/2.

    V      validators registered when the epoch's first tag was submitted
           (snapshotted on chain, so late registrations cannot move payouts)
    h_t[s] how many validators gave tag t the score s (0..100); the ones
           who did not score t count as 0. Vt = max(V, n_t)
    need   ceil(Vt / 2) = (Vt + 1) // 2
    C_t    consensus: the highest s with #{v : s_v >= s} >= need
           (0 when fewer than `need` validators scored t at all)
    R_t    clipped rank: sum_v min(s_v, C_t) — a score above what half the
           set agrees on counts only up to consensus
    share  emission * R_t // sum_t R_t   (0 if the sum is 0)

On chain: score() bumps hist[(epoch, tag, s)] and top[(epoch, tag)];
settle(epoch, tag) (anyone, epoch closed, once per tag) walks the histogram
from top down to C_t — at most 101 reads — stores (C_t, R_t) and adds R_t to
the epoch's rank total; claim() requires every tag of the epoch settled.
"""

MAX_SCORE = 100
KAPPA_NUM, KAPPA_DEN = 1, 2


def need(v):
    """Votes a score must have at or above it to be consensus (ceil(kappa*V))."""
    return (v * KAPPA_NUM + KAPPA_DEN - 1) // KAPPA_DEN


def hist(scores):
    h = [0] * (MAX_SCORE + 1)
    for s in scores:
        s = int(s)
        if not 0 <= s <= MAX_SCORE:
            raise ValueError(f'score {s} outside 0..{MAX_SCORE}')
        h[s] += 1
    return h


def settle(h, validators):
    """(consensus, rank) for one tag from its histogram — the exact walk the
    contracts do: from the top score down until `need` votes are covered."""
    n = sum(h)
    k = need(max(int(validators), n))
    total = sum(s * c for s, c in enumerate(h))
    if n < k or k == 0:
        return 0, 0
    above, mass = 0, 0  # votes / score mass at or above the cursor
    for s in range(MAX_SCORE, -1, -1):
        above += h[s]
        mass += h[s] * s
        if above >= k:
            # scores >= s clip to s; scores below s are kept as they are
            return s, total - mass + s * above
    return 0, 0  # unreachable: s = 0 covers all n >= k votes


def epoch(scores_by_tag, validators, emission):
    """Full Yuma for an epoch. scores_by_tag: {tag: [score, ...]} -> {tag:
    {consensus, rank, raw, share, clipped}} + totals. Pure; used by tests,
    simulate() and the console to check what the chain settled."""
    tags, total = {}, 0
    for tag, scores in scores_by_tag.items():
        c, r = settle(hist(scores), validators)
        tags[tag] = {'consensus': c, 'rank': r, 'raw': sum(map(int, scores)),
                     'scores': len(scores),
                     'clipped': sum(max(int(s) - c, 0) for s in scores)}
        total += r
    for t in tags.values():
        t['share'] = int(emission) * t['rank'] // total if total else 0
    return {'validators': int(validators), 'need': need(int(validators)),
            'rank_total': total, 'emission': int(emission), 'tags': tags}
