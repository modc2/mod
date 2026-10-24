"""tradekit/1 — the venue modules agree with each other, and scaffold
produces a conformant module. Offline: static checks + a temp ORBIT."""

import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load():
    spec = importlib.util.spec_from_file_location("tradekit_mod_test", os.path.join(ROOT, "src", "mod.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


tk = _load()


@pytest.mark.parametrize("name", ["hyperliquid", "polymarket", "dydx"])
def test_venue_conforms(name):
    r = tk.Tradekit().check(name)
    assert r["ok"], r["problems"]


def test_board_lists_all_venues():
    names = set(tk.Tradekit().venues())
    assert {"hyperliquid", "polymarket", "dydx"} <= names


def test_required_is_hyperliquid_surface():
    # hyperliquid is the reference: every required fn is native there, no alias
    r = tk.Tradekit().check("hyperliquid")
    assert r["aliases"] == {}
    assert all(r["fns"][f]["native"] == f for f in tk.standard.required_fns())


def test_scaffold_is_conformant(tmp_path, monkeypatch):
    monkeypatch.setattr(tk, "ORBIT", str(tmp_path))
    out = tk.Tradekit().scaffold(name="testvenue", venue="TestVenue", api_port=59990)
    assert out["ok"], out["check"]
    d = tmp_path / "testvenue"
    cfg = json.loads((d / "config.json").read_text())
    assert cfg["ports"] == {"api": 59990, "app": 59991}
    assert cfg["standard"]["venue"] == "TestVenue"
    for f in d.rglob("*"):
        if f.is_file():
            assert "__NAME__" not in f.read_text() and "__API_PORT__" not in f.read_text(), f
    assert "testvenue" in (d / "src/strats/base.py").read_text()


def test_scaffold_refuses_foreign_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(tk, "ORBIT", str(tmp_path))
    (tmp_path / "taken").mkdir()
    (tmp_path / "taken" / "config.json").write_text('{"name":"taken","description":"hand made"}')
    with pytest.raises(ValueError):
        tk.Tradekit().scaffold(name="taken")
    with pytest.raises(ValueError):
        tk.Tradekit().scaffold(name="taken", force=True)
    assert (tmp_path / "taken" / "config.json").exists()


def test_template_live_start_defaults_dry_run():
    src = open(os.path.join(ROOT, "src", "template", "src", "mod.py")).read()
    assert "auto_execute: bool = False" in src
    rs = open(os.path.join(ROOT, "src", "template", "src", "api", "src", "main.rs")).read()
    assert "real execution is not implemented" in rs
