"""Stake-weighted mean: no clipping, every validator's view counts by stake."""
from . import normalize

NAME = "mean"
ABOUT = "Plain stake-weighted average of validator weights. Simplest; trusts validators fully."


def run(weights, stakes):
    total = sum(stakes.values())
    raw = {}
    for hk, w in weights.items():
        for uid, v in w.items():
            raw[uid] = raw.get(uid, 0.0) + stakes[hk] * v / total
    return normalize(raw)
