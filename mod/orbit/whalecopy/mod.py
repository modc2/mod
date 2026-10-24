import json
import os
import sys

SELF = os.path.dirname(os.path.abspath(__file__))
STRAT = os.path.join(os.path.dirname(SELF), "strat")


def _protocol():
    sys.path.insert(0, STRAT) if STRAT not in sys.path else None
    import importlib.util
    spec = importlib.util.spec_from_file_location("strat_host", os.path.join(STRAT, "mod.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.Mod()


class Mod:
    description = "whalecopy — a strategy mod on the strat protocol"
    path = SELF

    def forward(self, **kwargs):
        return self.info()

    def info(self):
        with open(os.path.join(SELF, "config.json")) as f:
            return json.load(f)

    def code(self):
        with open(os.path.join(SELF, "strat.py")) as f:
            return f.read()

    def verify(self):
        return _protocol().verify("whalecopy")

    def backtest(self, days=7, capital=1000.0, traders=None):
        return _protocol().backtest("whalecopy", days=days, capital=capital,
                                    traders=traders)
