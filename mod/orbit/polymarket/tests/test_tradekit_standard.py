"""This module conforms to tradekit/1 — the shared fn surface of the
copy-trading venue modules (hyperliquid, polymarket, dydx). Offline."""

import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STANDARD = os.path.join(os.path.dirname(ROOT), "tradekit", "src", "standard.py")


def test_conforms_to_tradekit():
    spec = importlib.util.spec_from_file_location("tradekit_standard", STANDARD)
    std = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(std)
    report = std.check(ROOT)
    assert report["ok"], report["problems"]
