"""Shared fixtures for hub tests.

Everything runs against a throwaway module tree built under a temp HOME —
no live service, no network, nothing read from the real repo. The tree
mirrors the real layout the code walks:

    {home}/mod/config.json          root config (version)
    {home}/mod/mod/orbit/<mods>     the fleet
    {home}/mod/mod/core/<mods>
    {home}/.mod/api/registry.json   {owner: {mod: cid}}
    {home}/.mod/build/private/*.json  build's privacy records
    {home}/.mod/build/visibility.json build's hub listing policy (default private)
"""
import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE = os.path.dirname(HERE)


def _write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def build_fake_home(home: Path) -> Path:
    """A small but representative fleet: a normal module, a nested-config
    module, a mod.py-only module with broken json, a marker-less phantom
    dir, a private module, and hidden dirs that must never surface."""
    repo = home / "mod" / "mod"

    _write(home / "mod" / "config.json", json.dumps({"name": "mod", "version": "9.9.9"}))

    alpha = repo / "orbit" / "alpha"
    _write(alpha / "config.json", json.dumps({
        "name": "alpha",
        "description": "First test module Alpha",
        "version": "1.2.3",
        "fns": ["hello", 42, "world"],
        "deps": ["beta"],
        "urls": {"app": "http://localhost:60001/alpha", "api": "http://localhost:60000"},
    }))
    _write(alpha / "mod.py", "class Mod:\n    pass\n")
    _write(alpha / "README.md", "# alpha\n\nAlpha readme body.\n")
    _write(alpha / "skill.md", "alpha skill body\n")

    # Config nested at <mod>/<name>/config.json (package-style module).
    beta = repo / "core" / "beta"
    _write(beta / "beta" / "config.json", json.dumps({"description": "Beta nested config"}))
    _write(beta / "README.md", "# beta\n")

    # mod.py present but config.json is invalid json → module, empty desc.
    broken = repo / "core" / "broken"
    _write(broken / "config.json", "{not json")
    _write(broken / "mod.py", "class Mod:\n    pass\n")

    # No config.json, no mod.py, nothing nested — not a module to the api scan.
    _write(repo / "orbit" / "phantom" / "notes.txt", "just a directory\n")

    # Private module: enabled record under build's private dir.
    secret = repo / "orbit" / "secret"
    _write(secret / "config.json", json.dumps({"description": "you should not see me"}))
    _write(secret / "README.md", "# secret\n")

    # Unlisted module: no encryption record, just never made public in the
    # host's listing policy (the default for every module).
    draft = repo / "orbit" / "draft"
    _write(draft / "config.json", json.dumps({"description": "not published yet"}))
    _write(draft / "README.md", "# draft\n")

    # Hidden / underscore dirs are always skipped.
    _write(repo / "orbit" / ".hidden" / "config.json", "{}")
    _write(repo / "orbit" / "_priv" / "config.json", "{}")

    priv = home / ".mod" / "build" / "private"
    _write(priv / "secret.json", json.dumps({"enabled": True}))
    _write(priv / "alpha.json", json.dumps({"enabled": False}))
    _write(priv / "junk.json", "not json at all")

    # build's hub listing policy: private by default, these made public.
    # "secret" is listed but its encryption record still hides it.
    _write(home / ".mod" / "build" / "visibility.json", json.dumps({
        "default": "private",
        "modules": {m: "public" for m in ("alpha", "beta", "broken", "secret", "docs")},
    }))

    # Two owners hold a CID for alpha — sorted key order picks owner_a.
    _write(home / ".mod" / "api" / "registry.json", json.dumps({
        "owner_b": {"alpha": "cid_from_b"},
        "owner_a": {"alpha": "cid_from_a"},
    }))

    # Whitepaper files the hub app reads live from core/docs.
    _write(repo / "core" / "docs" / "docs" / "whitepaper.md", "# WP ENGINEER\n")
    _write(repo / "core" / "docs" / "docs" / "simple" / "whitepaper.md", "# WP HUMAN\n")

    return home


@pytest.fixture()
def fake_home(tmp_path):
    return build_fake_home(tmp_path)


def _load(name, filename, stub_mod=False):
    prev = sys.modules.get("mod")
    if stub_mod:
        stub = types.ModuleType("mod")
        stub.get_text = lambda p: Path(p).read_text()
        sys.modules["mod"] = stub
    try:
        spec = importlib.util.spec_from_file_location(name, os.path.join(MODULE, filename))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        if stub_mod:
            if prev is None:
                sys.modules.pop("mod", None)
            else:
                sys.modules["mod"] = prev


@pytest.fixture(scope="session")
def hub_mod():
    """core/hub/mod.py with `import mod` stubbed (get_text = read file)."""
    return _load("hub_mod_under_test", "mod.py", stub_mod=True)


@pytest.fixture(scope="session")
def hub_api():
    pytest.importorskip("fastapi")
    return _load("hub_api_under_test", "api.py")


@pytest.fixture()
def catalog(hub_mod, fake_home, monkeypatch):
    """A hub Mod instance pointed at the fake tree."""
    monkeypatch.setattr(hub_mod, "REPO", str(fake_home / "mod" / "mod"))
    return hub_mod.Mod(), fake_home


@pytest.fixture()
def api(hub_api, fake_home, monkeypatch):
    """hub api module with HOME pointed at the fake tree."""
    monkeypatch.setattr(hub_api, "HOME", str(fake_home))
    return hub_api, fake_home
