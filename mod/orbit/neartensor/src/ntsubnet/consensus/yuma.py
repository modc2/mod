"""Yuma-lite: stake-weighted, median-clipped (the Bittensor default, simplified)."""
from . import normalize

NAME = "yuma"
ABOUT = "Clip each validator's weight at the stake-weighted median, then stake-average. A lone validator can't pump a miner."


def run(weights, stakes):
    uids = sorted({u for w in weights.values() for u in w})
    raw = {}
    for uid in uids:
        pairs = [(stakes[hk], w.get(uid, 0.0)) for hk, w in weights.items()]
        med = weighted_median(pairs)
        total = sum(s for s, _ in pairs)
        raw[uid] = sum(s * min(v, med) for s, v in pairs) / total
    return normalize(raw)


def weighted_median(pairs):
    """pairs: [(stake, value)] → value at 50% of cumulative stake."""
    pairs = sorted(pairs, key=lambda p: p[1])
    half = sum(s for s, _ in pairs) / 2.0
    acc = 0.0
    for s, v in pairs:
        acc += s
        if acc >= half:
            return v
    return pairs[-1][1] if pairs else 0.0
