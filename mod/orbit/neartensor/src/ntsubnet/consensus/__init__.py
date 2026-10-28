"""
Pluggable consensus. Every file in this folder is one consensus rule:

    NAME  = "mean"                          # what a subnet's `consensus` field says
    ABOUT = "one line for humans"
    def run(weights, stakes) -> {uid: incentive}

`weights` is {validator_hotkey: {uid: weight}}, `stakes` is
{validator_hotkey: stake}. Return incentive per miner uid summing to 1
(use `normalize`). To add a rule, drop a new file here — it is picked up
automatically and every subnet can switch to it by name.
"""
import importlib
import pkgutil


def normalize(raw):
    total = sum(v for v in raw.values() if v > 0)
    return {u: v / total for u, v in raw.items() if v > 0} if total > 0 else {}


RULES = {}
for _info in pkgutil.iter_modules(__path__):
    _m = importlib.import_module(f"{__name__}.{_info.name}")
    if callable(getattr(_m, "run", None)):
        RULES[getattr(_m, "NAME", _info.name)] = _m

DEFAULT = "yuma"


def get(name):
    if name not in RULES:
        raise ValueError(f"unknown consensus '{name}' — have: {', '.join(sorted(RULES))}")
    return RULES[name]


def available():
    return [{"name": n, "about": getattr(m, "ABOUT", "")} for n, m in sorted(RULES.items())]
