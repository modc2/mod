"""
bt — Bittensor mod: subnets, wallets, balances, and alpha trading.

The original bt.py source was lost; its compiled form survives as
_bt_engine.pyc (python 3.12) and is loaded here sourcelessly. The
classes below subclass the originals so the mod framework can anchor
on this file. Regenerate real source here before changing behavior.
"""
import importlib.util
import os

_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    'bt_engine', os.path.join(_here, '_bt_engine.pyc'))
_engine = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_engine)

build_engine = _engine.build_engine
_get_engine = _engine._get_engine


class Bt(_engine.Bt):
    """Bittensor chain surface: subnets, neurons, wallets, balance, transfer."""


class BtTrader(_engine.BtTrader):
    """Alpha trading over subnets: price, scan, portfolio, buy/sell/swap."""
