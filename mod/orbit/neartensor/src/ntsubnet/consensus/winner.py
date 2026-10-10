"""Winner-take-all: the miner with the highest Yuma consensus gets everything."""
from . import yuma

NAME = "winner"
ABOUT = "Run Yuma, then give 100% to the top miner. Pure competition."


def run(weights, stakes):
    inc = yuma.run(weights, stakes)
    if not inc:
        return {}
    best = max(inc, key=lambda u: (inc[u], -int(u)))
    return {best: 1.0}
