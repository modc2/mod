"""Modular consensus — every rule is a pure function of a tag's score
HISTOGRAM, so any of them can settle an epoch from the same data.

That one design choice is what makes the rules swappable AND cheap: a
histogram is a SUM of one-hot vectors (one per validator), and a sum is
exactly what simple secure aggregation (core/secagg.py) computes without
revealing any single validator's scores. Pick any rule here and the
aggregate the chain (or the validity proof) needs is the same masked sum.

A rule maps (hist, validators) -> (consensus, rank):
    hist        hist[s] = how many validators gave the tag score s (0..100);
                validators who did not score the tag count as 0 via
                Vt = max(validators, votes) — the Zuma convention
    consensus   the agreed score 0..100 (what the console shows)
    rank        the mass the tag carries into the emission split:
                share = emission * rank / sum(rank over tags)

Shipped rules (RULES, in display order):
    yuma     Zuma/Yuma: consensus = highest s half of Vt rates at-or-above,
             every score CLIPPED to it (core/yuma.py is the reference — this
             registry delegates to it, it never re-implements it).
             A minority cannot raise a tag above what half the set agrees on.
    median   the same consensus cut, but rank = consensus x Vt: individual
             scores only pick the median, they carry no extra weight at all.
             Most manipulation-resistant, least expressive.
    mean     consensus = round(sum / Vt), rank = raw sum. Cheapest to
             aggregate (sum + count per tag, no histogram needed) but one
             liar moves a tag by delta / Vt — use only with trusted sets.
    trimmed  drop the top and bottom ceil(q * Vt) votes (q = 0.25), mean of
             the rest: outliers gone, honest spread kept.

Every rule keeps epoch() a drop-in for yuma.epoch(): same input, same output
shape, and rule='yuma' is bit-for-bit yuma.epoch() (tests pin it).
"""
import math

from . import yuma

MAX_SCORE = yuma.MAX_SCORE
TRIM = 1, 4                       # trimmed: drop ceil(Vt / 4) from each end

RULES = {}


def rule(name, note, aggregate):
    def deco(fn):
        RULES[name] = {'fn': fn, 'note': note, 'aggregate': aggregate}
        return fn
    return deco


def _vt(h, validators):
    """Zuma's Vt = max(registered validators, votes cast); non-voters are
    zeros, so the histogram a rule sees always sums to Vt."""
    n = sum(h)
    vt = max(int(validators), n)
    full = list(h)
    full[0] += vt - n
    return full, vt


@rule('yuma', 'highest score half the set rates at-or-above; every score '
      'clipped to it (the Zuma reference, core/yuma.py)', 'histogram')
def _yuma(h, validators):
    return yuma.settle(h, validators)


@rule('median', 'the same consensus cut as yuma, but rank = consensus x Vt: '
      'scores only elect the median, they add no weight', 'histogram')
def _median(h, validators):
    c, _ = yuma.settle(h, validators)
    _, vt = _vt(h, validators)
    return c, c * vt


@rule('mean', 'plain average over Vt; cheapest aggregate (sum + count, no '
      'histogram) but one liar moves it by delta / Vt', 'sum')
def _mean(h, validators):
    full, vt = _vt(h, validators)
    total = sum(s * c for s, c in enumerate(full))
    return (round(total / vt), total) if vt else (0, 0)


@rule('trimmed', 'drop the top and bottom ceil(Vt/4) votes, mean of the '
      'rest: outliers gone, honest spread kept', 'histogram')
def _trimmed(h, validators):
    full, vt = _vt(h, validators)
    cut = math.ceil(vt * TRIM[0] / TRIM[1])
    keep = vt - 2 * cut
    if keep <= 0:
        return _median(h, validators)
    votes = [s for s, c in enumerate(full) for _ in range(c)]
    mid = votes[cut:vt - cut]
    total = sum(mid)
    return round(total / keep), round(total * vt / keep)


def rules():
    """What each rule does and what aggregate it needs — the public listing."""
    return [{'rule': k, 'note': v['note'], 'aggregate': v['aggregate']}
            for k, v in RULES.items()]


def settle(h, validators, kind='yuma'):
    """(consensus, rank) for one tag under `kind`."""
    if kind not in RULES:
        raise ValueError(f'rule one of {sorted(RULES)}, got {kind!r}')
    return RULES[kind]['fn'](list(h), int(validators))


def epoch(scores_by_tag, validators, emission, kind='yuma'):
    """Full epoch under `kind` — yuma.epoch() generalized; kind='yuma' is
    exactly yuma.epoch() plus the rule name."""
    tags, total = {}, 0
    for tag, scores in scores_by_tag.items():
        h = yuma.hist(scores)
        c, r = settle(h, validators, kind)
        tags[tag] = {'consensus': c, 'rank': r, 'raw': sum(map(int, scores)),
                     'scores': len(scores),
                     'clipped': sum(max(int(s) - c, 0) for s in scores)}
        total += r
    for t in tags.values():
        t['share'] = int(emission) * t['rank'] // total if total else 0
    return {'rule': kind, 'validators': int(validators),
            'need': yuma.need(int(validators)), 'rank_total': total,
            'emission': int(emission), 'tags': tags}
