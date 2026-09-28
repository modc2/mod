"""hub mod.py — the catalog fns, against the fake tree (no service).

Run: pytest core/hub/test
"""
import socket

import pytest


def test_modules_walks_both_groups_and_skips_hidden(catalog):
    hub, _ = catalog
    mods = hub.modules("all")
    names = {(m["group"], m["name"]) for m in mods}
    assert names == {("orbit", "alpha"), ("orbit", "phantom"), ("orbit", "secret"),
                     ("core", "beta"), ("core", "broken"), ("core", "docs")}
    # .hidden / _priv never surface.
    assert not any(m["name"].startswith((".", "_")) for m in mods)


def test_modules_group_filter(catalog):
    hub, _ = catalog
    assert {m["group"] for m in hub.modules("core")} == {"core"}
    assert hub.names("orbit") == ["alpha", "phantom", "secret"]


def test_doc_flags_reflect_shipped_files(catalog):
    hub, _ = catalog
    rows = {m["name"]: m for m in hub.modules("all")}
    assert rows["alpha"]["readme"] and rows["alpha"]["skill"]
    assert rows["beta"]["readme"] and not rows["beta"]["skill"]
    assert not rows["phantom"]["readme"]


def test_desc_reads_both_config_locations(catalog):
    hub, _ = catalog
    assert hub.desc("alpha") == "First test module Alpha"
    # <mod>/<name>/config.json (package-style nesting).
    assert hub.desc("beta") == "Beta nested config"
    # Invalid json → empty description, no raise.
    assert hub.desc("broken") == ""
    assert hub.desc("phantom") == ""


def test_dir_resolves_and_raises(catalog):
    hub, home = catalog
    assert hub.dir("beta") == str(home / "mod" / "mod" / "core" / "beta")
    with pytest.raises(FileNotFoundError):
        hub.dir("no-such-module")


def test_doc_returns_readme_and_skill_text(catalog):
    hub, _ = catalog
    doc = hub.doc("alpha")
    assert doc["module"] == "alpha"
    assert "Alpha readme body." in doc["readme"]
    assert "alpha skill body" in doc["skill"]
    assert hub.doc("broken")["readme"] is None


def test_search_matches_name_and_description_case_insensitive(catalog):
    hub, _ = catalog
    assert hub.search("ALPHA") == ["alpha"]
    assert hub.search("first test") == ["alpha"]
    assert hub.search("nested CONFIG") == ["beta"]
    assert hub.search("zzz-no-hit") == []


def test_info_counts(catalog):
    hub, _ = catalog
    info = hub.info()
    assert info["modules"] == 6
    assert info["by_group"] == {"orbit": 3, "core": 3}
    assert info["with_readme"] == 3
    assert info["with_skill"] == 1


def test_probe_open_closed_and_garbage_ports(catalog):
    hub, _ = catalog
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    open_port = listener.getsockname()[1]
    # A port that was just freed is a reliable "closed" sample.
    tmp = socket.socket()
    tmp.bind(("127.0.0.1", 0))
    closed_port = tmp.getsockname()[1]
    tmp.close()
    try:
        out = hub.probe(f"{open_port}, {closed_port}, abc, ")
        assert out == {open_port: True, closed_port: False}
    finally:
        listener.close()
