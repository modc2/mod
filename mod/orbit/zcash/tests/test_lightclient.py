"""
Tests for the shielded proving backend.

This is the layer that turned "this module cannot create a shielded spend"
into a send button, so what it needs pinned is not the cryptography -- that
lives in a Rust binary the Electric Coin Company maintains -- but the seams
around it, which are exactly where a wallet loses money or lies to its owner:

  * the *sync* readout, because "synced" is what gates a spend, and one wrong
    comparison against a ScanPriority makes an unscanned wallet look ready;
  * the *ladder*, because a send attempted before the prover exists or before
    the scan finishes must say which rung is missing rather than fail deep
    inside a subprocess;
  * the *dry run*, because a shielded_send without broadcast=True must not
    reach the prover at all;
  * the *identity*, because the mnemonic's second copy is only as safe as the
    age key it is sealed to.

None of it needs the binary or the network. The one test that does is marked
`live` and skips when the prover is not installed.
"""

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

os.environ.setdefault("ZCASH_WALLET_DIR", tempfile.mkdtemp())
os.environ.setdefault("ZCASH_LIGHTCLIENT_DIR", tempfile.mkdtemp())

from zcash import lightclient as lc  # noqa: E402
from zcash import wallet  # noqa: E402


@pytest.fixture
def wallets(tmp_path, monkeypatch):
    """A private wallet dir and light-client dir for one test."""
    monkeypatch.setenv("ZCASH_WALLET_DIR", str(tmp_path / "wallets"))
    monkeypatch.setenv("ZCASH_LIGHTCLIENT_DIR", str(tmp_path / "light"))
    return tmp_path


def make_db(path: Path, birthday, tip, ranges, max_scanned=None):
    """A minimal stand-in for the light client's data.sqlite."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE blocks (height INTEGER)")
    conn.execute("CREATE TABLE accounts (birthday_height INTEGER)")
    conn.execute("CREATE TABLE scan_queue (block_range_start INTEGER, "
                 "block_range_end INTEGER, priority INTEGER)")
    conn.execute("INSERT INTO accounts VALUES (?)", (birthday,))
    if max_scanned is not None:
        conn.execute("INSERT INTO blocks VALUES (?)", (max_scanned,))
    for start, end, priority in ranges:
        conn.execute("INSERT INTO scan_queue VALUES (?,?,?)",
                     (start, end, priority))
    conn.commit()
    conn.close()
    return path


# ── The sync readout ────────────────────────────────────────────────────────

def test_a_scanned_tip_range_does_not_count_as_work_remaining(wallets):
    """The trap that made a fresh wallet look permanently behind.

    `scan_queue.priority` is a label, not a to-do flag: a range that has been
    scanned keeps priority 10 (Scanned), and the tip range sits there forever.
    Reading "remaining" as `priority > 0` therefore reports a fully synced
    wallet as unsynced and blocks every send it will ever try.
    """
    d = lc.wallet_dir("w")
    make_db(d / lc.DATA_DB, birthday=3_400_000, tip=3_467_334,
            ranges=[(419_200, 3_467_333, 0),      # Ignored
                    (3_467_333, 3_467_334, 10)],  # Scanned -- and done
            max_scanned=3_467_333)
    p = lc._db_progress("w")
    assert p["blocks_remaining"] == 0
    assert p["synced"] is True
    assert p["percent"] == 100.0


def test_unscanned_ranges_are_counted_and_leave_the_wallet_unsynced(wallets):
    d = lc.wallet_dir("w")
    make_db(d / lc.DATA_DB, birthday=3_000_000, tip=3_100_001,
            ranges=[(3_000_000, 3_050_000, 20),   # Historic -- still to do
                    (3_050_000, 3_100_001, 10)],
            max_scanned=3_100_000)
    p = lc._db_progress("w")
    assert p["blocks_remaining"] == 50_000
    assert p["synced"] is False
    assert 0 < p["percent"] < 100


def test_a_wallet_behind_the_tip_is_not_synced_even_with_an_empty_queue(wallets):
    """Nothing queued is not the same as caught up."""
    d = lc.wallet_dir("w")
    make_db(d / lc.DATA_DB, birthday=3_000_000, tip=3_100_001,
            ranges=[(3_000_000, 3_100_001, 10)],
            max_scanned=3_050_000)
    assert lc._db_progress("w")["synced"] is False


def test_status_never_calls_a_running_scan_synced(wallets, monkeypatch):
    """A scan in flight has a half-built commitment tree behind it.

    The database's last committed range can say "done" while the scanner is
    mid-batch; trusting it would let a spend be anchored to a tree that is
    about to change.
    """
    d = lc.wallet_dir("w")
    d.mkdir(parents=True, exist_ok=True)
    (d / lc.BUILD_MARKER).write_text("")
    make_db(d / lc.DATA_DB, birthday=3_400_000, tip=3_467_334,
            ranges=[(3_467_333, 3_467_334, 10)], max_scanned=3_467_333)
    monkeypatch.setattr(lc, "_read_job", lambda n: {"pid": os.getpid()})
    st = lc.status("w")
    assert st["syncing"] is True
    assert st["synced"] is False
    assert st["spendable"] is False


def test_progress_survives_a_wallet_with_no_database_yet(wallets):
    assert lc._db_progress("never-synced") == {}


# ── The identity that seals the second copy of the seed ─────────────────────

def test_the_age_identity_round_trips_under_the_wallet_password(wallets):
    d = lc.wallet_dir("w")
    d.mkdir(parents=True, exist_ok=True)
    lc._seal_identity("w", "AGE-SECRET-KEY-1EXAMPLE", "correct horse")

    on_disk = (d / "identity.enc").read_text()
    assert "AGE-SECRET-KEY" not in on_disk, "the key is sitting there in clear"

    with lc._Identity("w", "correct horse") as path:
        assert Path(path).read_text() == "AGE-SECRET-KEY-1EXAMPLE"
        assert oct(Path(path).stat().st_mode)[-3:] == "600"
        materialised = path
    assert not os.path.exists(materialised), "the clear identity outlived the send"


def test_a_wrong_password_does_not_open_the_identity(wallets):
    lc.wallet_dir("w").mkdir(parents=True, exist_ok=True)
    lc._seal_identity("w", "AGE-SECRET-KEY-1EXAMPLE", "correct horse")
    with pytest.raises(wallet.WalletError):
        with lc._Identity("w", "battery staple"):
            pass


# ── Reading the prover's own failures ───────────────────────────────────────

class _CP:
    returncode = 1

    def __init__(self, text):
        self.stderr, self.stdout = text, ""


def test_an_anchor_too_shallow_is_explained_rather_than_echoed():
    """"Must scan blocks first" is what the prover says; it is not what is wrong."""
    with pytest.raises(lc.LightClientError) as e:
        lc._translate_send_error(_CP("Error: Must scan blocks first"))
    assert "anchor" in str(e.value)
    assert "birthday" in str(e.value)


def test_insufficient_funds_mentions_confirmations():
    with pytest.raises(lc.LightClientError) as e:
        lc._translate_send_error(_CP("Error: Insufficient balance (have 0)"))
    assert "confirmations" in str(e.value)


def test_an_unrecognised_failure_is_passed_through_not_swallowed():
    lc._translate_send_error(_CP("Error: something new"))   # returns, no raise
    with pytest.raises(lc.LightClientError) as e:
        lc._fail(_CP("Error: something new"), "the shielded send failed")
    assert "something new" in str(e.value)


def test_the_txid_is_read_out_of_the_tool_output():
    txid = "a" * 64
    assert lc._extract_txid(f"Sent transaction with txid {txid}\n") == txid
    assert lc._extract_txid("nothing here") is None


# ── The birthday margin ─────────────────────────────────────────────────────

def test_the_light_client_starts_below_the_wallet_birthday(wallets, monkeypatch):
    """A birthday at the chain tip leaves no depth to anchor a spend against.

    The prover refuses with "Must scan blocks first", which reads like a bug.
    Backing the scan up a hundred blocks costs seconds and removes it.
    """
    seen = {}

    def fake_run(args, stdin=None, timeout=300, cwd=None):
        seen["args"] = args
        Path(args[args.index("-i") + 1]).write_text("AGE-SECRET-KEY-1X")
        (lc.wallet_dir("w") / lc.BUILD_MARKER).write_text("")

        class OK:
            returncode = 0
            stdout = stderr = ""
        return OK()

    monkeypatch.setattr(lc, "_run", fake_run)
    monkeypatch.setattr(lc, "binary", lambda: "/fake/zcash-devtool")
    out = lc.init("w", "abandon " * 11 + "about", "pw", birthday=3_467_333)

    i = seen["args"].index("--birthday")
    assert int(seen["args"][i + 1]) == 3_467_333 - lc.BIRTHDAY_MARGIN
    assert out["birthday"] == 3_467_333 - lc.BIRTHDAY_MARGIN


def test_the_margin_never_reaches_below_sapling_activation(wallets, monkeypatch):
    seen = {}

    def fake_run(args, stdin=None, timeout=300, cwd=None):
        seen["args"] = args
        Path(args[args.index("-i") + 1]).write_text("k")
        (lc.wallet_dir("w") / lc.BUILD_MARKER).write_text("")

        class OK:
            returncode = 0
            stdout = stderr = ""
        return OK()

    monkeypatch.setattr(lc, "_run", fake_run)
    monkeypatch.setattr(lc, "binary", lambda: "/fake/zcash-devtool")
    lc.init("w", "seed words", "pw", birthday=lc.SAPLING_ACTIVATION + 5)
    i = seen["args"].index("--birthday")
    assert int(seen["args"][i + 1]) == lc.SAPLING_ACTIVATION


# ── Where the backend is, and what it says when it is not there ─────────────

def test_no_binary_reports_not_installed_with_a_way_out(monkeypatch):
    monkeypatch.setenv("ZCASH_DEVTOOL_BIN", "/nonexistent/zcash-devtool")
    a = lc.available()
    assert a["installed"] is False
    assert "shielded_backend_install" in a["how_to_install"]


def test_operations_refuse_clearly_with_no_binary(wallets, monkeypatch):
    monkeypatch.setenv("ZCASH_DEVTOOL_BIN", "/nonexistent/zcash-devtool")
    with pytest.raises(lc.LightClientError) as e:
        lc._run(["wallet", "balance"])
    assert "shielded_backend_install" in str(e.value)


# ── The module's ladder ─────────────────────────────────────────────────────

def _mod():
    """A Mod whose `_lightclient` is the same object these tests patch.

    Loading `zcash/mod.py` as a loose file gives it a *second* copy of
    lightclient, so a monkeypatch here would never be seen there. Importing it
    as part of the package keeps one copy, which is the whole point.
    """
    from zcash import mod as _m
    return _m.Mod()


UA = ("u1p095h2qzp42q3dln7l20nft47emferl7q9rg66w3a9062q30gky56n5g24t6wfdek556"
      "cw8k49t0qdrw0t9wa85f9jqadj56xcnnr53mdgxukze2z02ngq3l9j26rfmpm9gu0tsu70"
      "xzvvw4y8drly0nd0jgw4y9l64rlguufxewl09q8w0rtylqpmn63tsf00kjnyl98254xj35"
      "zmp")


def test_a_send_with_no_prover_names_the_missing_rung(wallets, monkeypatch):
    monkeypatch.setenv("ZCASH_DEVTOOL_BIN", "/nonexistent/zcash-devtool")
    m = _mod()
    assert not m.chain.has_node, "a configured node would take precedence"
    out = m.shielded_send(name="nope", password="pw", to=UA, amount=0.01)
    assert "no proving backend" in out["error"]
    assert any("shielded_backend_install" in s for s in out["how_to_send"])


def test_a_send_with_a_prover_but_no_light_client_says_so(wallets, monkeypatch):
    m = _mod()
    monkeypatch.setattr(lc, "binary", lambda: "/fake/zcash-devtool")
    monkeypatch.setattr(lc, "available", lambda: {"installed": True})
    monkeypatch.setattr(lc, "initialized", lambda n: False)
    out = m.shielded_send(name="nope", password="pw", to=UA, amount=0.01)
    assert "has not been set up" in out["error"]
    assert any("shielded_sync_start" in s for s in out["how_to_send"])


def test_a_send_while_the_scan_is_running_is_refused(wallets, monkeypatch):
    m = _mod()
    monkeypatch.setattr(lc, "available", lambda: {"installed": True})
    monkeypatch.setattr(lc, "initialized", lambda n: True)
    monkeypatch.setattr(lc, "status", lambda n: {"syncing": True, "percent": 12})
    out = m.shielded_send(name="w", password="pw", to=UA, amount=0.01)
    assert "still scanning" in out["error"]
    assert out["sync"]["percent"] == 12


def test_a_dry_run_never_reaches_the_prover(wallets, monkeypatch):
    """The whole point of the gate: no proof is built, nothing is broadcast."""
    m = _mod()
    called = []
    monkeypatch.setattr(lc, "available", lambda: {"installed": True})
    monkeypatch.setattr(lc, "initialized", lambda n: True)
    monkeypatch.setattr(lc, "status", lambda n: {"syncing": False, "synced": True,
                                                 "chain_tip_height": 3_467_333})
    monkeypatch.setattr(lc, "balance", lambda n: {
        "sapling_spendable_zat": 500_000_000, "orchard_spendable_zat": 0})
    def fake_send(*a, **k):
        called.append(a)
        return {"sent": True, "txid": "b" * 64}
    monkeypatch.setattr(lc, "send", fake_send)

    out = m.shielded_send(name="w", password="pw", to=UA, amount=0.01)
    assert out["mode"] == "DRY RUN"
    assert out["sent"] is False
    assert called == [], "a dry run built a proof"

    out = m.shielded_send(name="w", password="pw", to=UA, amount=0.01,
                          broadcast=True)
    assert called, "broadcast=True did not reach the prover"


def test_a_send_over_the_spendable_balance_is_refused_before_proving(
        wallets, monkeypatch):
    m = _mod()
    called = []
    monkeypatch.setattr(lc, "available", lambda: {"installed": True})
    monkeypatch.setattr(lc, "initialized", lambda n: True)
    monkeypatch.setattr(lc, "status", lambda n: {"syncing": False, "synced": True})
    monkeypatch.setattr(lc, "balance", lambda n: {
        "sapling_spendable_zat": 1000, "orchard_spendable_zat": 0})
    monkeypatch.setattr(lc, "send", lambda *a, **k: called.append(a))
    out = m.shielded_send(name="w", password="pw", to=UA, amount=1.0,
                          broadcast=True)
    assert "not enough spendable" in out["error"]
    assert called == []


def test_a_transparent_destination_is_sent_to_the_transparent_path(wallets):
    m = _mod()
    out = m.shielded_send(name="w", password="pw",
                          to="t1KsFtFDLDyzMGGTUJ4L5dxTdz2N9NwyLKa", amount=0.1)
    assert "error" in out


# ── The surfaces agree about the new functions ──────────────────────────────

NEW_FNS = ["shielded_backend", "shielded_backend_install", "shielded_sync_start",
           "shielded_sync_status", "shielded_sync_stop", "shielded_spendable",
           "shielded_shield"]


def test_every_new_function_is_exposed_and_gated():
    """`fns` exposure comes from config.json, and the gate from api.py.

    A function added to the class but not to config.json is invisible to the
    fleet; one added to config.json but not to the guard list is reachable
    without a token. Both have happened here before.
    """
    config = json.loads((Path(_ROOT) / "config.json").read_text())
    sys.path.insert(0, _ROOT)
    import api

    for fn in NEW_FNS:
        assert fn in config["fns"], f"{fn} is not exposed in config.json"
        assert hasattr(_mod(), fn), f"{fn} is not on the Mod class"

    for fn in NEW_FNS:
        if fn == "shielded_backend":
            assert fn in api.OPEN_FNS, "the app must see whether a prover exists"
        else:
            assert fn in api.GUARDED_FNS, f"{fn} spends or reveals and is open"


def test_the_stdio_server_enforces_the_same_gate_as_the_rest_api():
    sys.path.insert(0, _ROOT)
    import api
    import mcp
    assert set(api.GUARDED_FNS) == set(mcp._FALLBACK_GUARDED)


def test_capabilities_stops_claiming_shielded_sending_is_impossible(monkeypatch):
    monkeypatch.setattr(lc, "available", lambda: {"installed": True})
    caps = _mod().capabilities()
    assert caps["shielded_sapling"]["send"] is True
    assert caps["shielded_orchard"]["send"] is True
    assert caps["shielded_sapling"]["cannot"] is None


def test_capabilities_is_honest_when_there_is_no_prover(monkeypatch):
    m = _mod()
    monkeypatch.setattr(lc, "available", lambda: {"installed": False})
    caps = m.capabilities()
    assert caps["shielded_sapling"]["send"] is False
    assert "shielded_backend_install" in caps["shielded_sapling"]["cannot"]


# ── With the real binary, if this host has one ──────────────────────────────

@pytest.mark.live
def test_the_installed_prover_runs():
    if not lc.binary():
        pytest.skip("no prover installed on this host")
    assert lc.available()["runnable"] is True
