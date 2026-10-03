"""hub api.py — pure helpers + the catalog scan, no server, no network.

The service is loopback-only and serves the RAW catalog by design (privacy
is the app's job) — the scan tests assert that contract explicitly.

Run: pytest core/hub/test
"""
import asyncio
import json
import os
import socket


# ── small helpers ────────────────────────────────────────────────────────

def test_valid_name(hub_api):
    assert hub_api.valid_name("alpha")
    assert hub_api.valid_name("a-b_C1")
    assert not hub_api.valid_name("")
    assert not hub_api.valid_name("a b")
    assert not hub_api.valid_name("a/b")
    assert not hub_api.valid_name("x" * 65)


def test_str_list_keeps_only_strings(hub_api):
    assert hub_api.str_list({"fns": ["a", 1, None, "b"]}, "fns") == ["a", "b"]
    assert hub_api.str_list({"fns": "not-a-list"}, "fns") == []
    assert hub_api.str_list(None, "fns") == []


def test_display_shortens_home(api):
    mod, home = api
    assert mod.display(str(home) + "/x/y") == "~/x/y"
    assert mod.display("/etc/hosts") == "/etc/hosts"


def test_safe_anchor_stays_inside_the_tree(api):
    mod, home = api
    root = str((home / "mod").resolve())
    inside = str(home / "mod" / "mod" / "orbit")
    assert mod.safe_anchor(None) == root
    assert mod.safe_anchor(inside) == str((home / "mod" / "mod" / "orbit").resolve())
    # Outside hints — absolute or via .. traversal — clamp to the root.
    assert mod.safe_anchor("/etc") == root
    assert mod.safe_anchor(str(home / "mod" / ".." / "elsewhere")) == root


def test_newest_mtime_skips_build_dirs_and_caps_depth(hub_api, tmp_path):
    src = tmp_path / "src.py"
    src.write_text("x")
    os.utime(src, (1000, 1000))
    junk = tmp_path / "node_modules" / "pkg.js"
    junk.parent.mkdir()
    junk.write_text("x")
    os.utime(junk, (2000, 2000))
    deep = tmp_path / "a" / "b" / "deep.py"
    deep.parent.mkdir(parents=True)
    deep.write_text("x")
    os.utime(deep, (3000, 3000))
    assert hub_api.newest_mtime(str(tmp_path), 1, 0) == 1000
    assert hub_api.newest_mtime(str(tmp_path), 6, 0) == 3000


# ── registry (the sorted-owner CID gotcha) ───────────────────────────────

def test_registry_cid_iterates_owners_in_sorted_order(hub_api):
    # build's serde_json maps are BTreeMaps: owner_a must win regardless of
    # python dict insertion order.
    reg = {"owner_b": {"alpha": "cid_from_b"}, "owner_a": {"alpha": "cid_from_a"}}
    assert hub_api.registry_cid(reg, "alpha") == "cid_from_a"


def test_registry_cid_lookup_rules(hub_api):
    assert hub_api.registry_cid({"o": {"myapp": "C"}}, "MyApp") == "C"
    assert hub_api.registry_cid({"o": "not-a-map", "p": {"x": "C"}}, "x") == "C"
    assert hub_api.registry_cid({"o": {"x": 5}}, "x") is None
    assert hub_api.registry_cid(None, "x") is None
    assert hub_api.registry_cid({}, "x") is None


def test_registry_map_accepts_root_and_data_wrapped(api):
    mod, home = api
    reg_file = home / ".mod" / "api" / "registry.json"
    assert mod.registry_map() == {"owner_b": {"alpha": "cid_from_b"},
                                  "owner_a": {"alpha": "cid_from_a"}}
    reg_file.write_text(json.dumps({"data": {"o": {"x": "C"}}}))
    assert mod.registry_map() == {"o": {"x": "C"}}
    reg_file.unlink()
    assert mod.registry_map() is None


# ── privacy records ──────────────────────────────────────────────────────

def test_is_private_reads_build_records_off_disk(api):
    mod, home = api
    assert mod.is_private("secret") is True
    assert mod.is_private("alpha") is False       # record exists, enabled: false
    assert mod.is_private("junk") is False        # unreadable record = not private
    assert mod.is_private("never-heard-of") is False
    # Nested-mod names map slash → double underscore.
    (home / ".mod" / "build" / "private" / "a__b.json").write_text('{"enabled": true}')
    assert mod.is_private("a/b") is True


# ── nested mods ──────────────────────────────────────────────────────────

def test_find_nested_mods_elides_src_and_skips_build_dirs(hub_api, tmp_path):
    (tmp_path / "tool").mkdir()
    (tmp_path / "tool" / "config.json").write_text(json.dumps({"description": "a tool"}))
    (tmp_path / "src" / "inner").mkdir(parents=True)
    (tmp_path / "src" / "inner" / "mod.py").write_text("")
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "config.json").write_text("{}")
    out = []
    hub_api.find_nested_mods(str(tmp_path), str(tmp_path), 4, out)
    assert {m["rel"] for m in out} == {"tool", "inner"}
    tool = next(m for m in out if m["rel"] == "tool")
    assert tool["has_config"] and tool["description"] == "a tool"


# ── the catalog scan ─────────────────────────────────────────────────────

def test_scan_catalog_lists_real_modules_only(api):
    mod, _ = api
    rows = {m["name"]: m for m in mod.scan_catalog()["modules"]}
    # phantom has no config.json / mod.py anywhere — a marker-less dir is
    # not a module; hidden and underscore dirs never appear.
    assert "phantom" not in rows
    assert not any(n.startswith((".", "_")) for n in rows)
    assert {"mod", "alpha", "beta", "broken", "secret"} <= set(rows)
    # RAW catalog by contract: the private module IS present here; the app
    # (server.js) is what hides it. If this assert breaks, privacy moved.
    assert "secret" in rows


def test_scan_catalog_row_fields(api):
    mod, home = api
    rows = {m["name"]: m for m in mod.scan_catalog()["modules"]}
    alpha = rows["alpha"]
    assert alpha["category"] == "orbit"
    assert alpha["description"] == "First test module Alpha"
    assert alpha["fns"] == ["hello", "world"]          # non-strings dropped
    assert alpha["deps"] == ["beta"]
    assert alpha["app_url"] == "http://localhost:60001/alpha"
    assert alpha["cid"] == "cid_from_a"                # sorted-owner winner
    assert rows["beta"]["description"] == "Beta nested config"
    assert rows["broken"]["has_config"] is False       # invalid json
    assert rows["broken"]["has_mod_py"] is True
    # Root row carries the tree version from {anchor}/config.json.
    assert rows["mod"]["category"] == "root"
    assert rows["mod"]["version"] == "9.9.9"


def test_scan_catalog_query_filters_by_name(api):
    mod, _ = api
    out = mod.scan_catalog(q="ALPh")
    assert [m["name"] for m in out["modules"]] == ["alpha"]
    assert out["count"] == 1


# ── probe ────────────────────────────────────────────────────────────────

def test_probe_rejects_bad_ports(hub_api):
    for bad in ("abc", "0", "70000", "1,abc"):
        resp = asyncio.run(hub_api.probe(ports=bad))
        assert resp.status_code == 400


def test_probe_reports_open_and_closed_and_dedupes(hub_api):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    open_port = listener.getsockname()[1]
    tmp = socket.socket()
    tmp.bind(("127.0.0.1", 0))
    closed_port = tmp.getsockname()[1]
    tmp.close()
    try:
        out = asyncio.run(hub_api.probe(ports=f"{open_port},{open_port},{closed_port}"))
        assert out == {"ok": True, "ports": {open_port: True, closed_port: False}}
    finally:
        listener.close()
