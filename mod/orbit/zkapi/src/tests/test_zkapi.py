"""zkapi module tests.

Unit tests always run. ZKAPI_TEST_E2E=1 (default via ``m zkapi/test``) runs the
full lifecycle against a scratch devnet of the real upstream stack on ports
51481-51485; ZKAPI_TEST_LIVE=1 also verifies the live mainnet deployment.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from zkstack import paths  # noqa: E402
from zkstack.chain import Contract, link_bytecode  # noqa: E402
from zkstack.client import WITHDRAWAL_TYPES, decode_withdrawal_inputs  # noqa: E402
from zkstack.provider import Provider  # noqa: E402
from zkstack.wallet import WalletDriver, WalletError  # noqa: E402

E2E = os.environ.get("ZKAPI_TEST_E2E", "1") == "1"
LIVE = os.environ.get("ZKAPI_TEST_LIVE", "0") == "1"
BUILT = paths.bin_path("zkapi-wallet").exists() and paths.artifact("ZkApiVault").exists()
GWEI = 10**9


# -- unit ----------------------------------------------------------------------------------------------
def test_upstream_pinned_and_built():
    assert (paths.UPSTREAM_DIR / ".git").exists(), "run m zkapi/setup"
    import subprocess
    head = subprocess.run(["git", "-C", str(paths.UPSTREAM_DIR), "rev-parse", "HEAD"], capture_output=True,
                          text=True).stdout.strip()
    assert head == paths.UPSTREAM_COMMIT
    for name in ("zkapi", "zkapi-indexerd", "zkapi-challenged", "zkapi-wallet"):
        assert paths.bin_path(name).exists(), name


def test_vault_abi_encoding_matches_upstream_signatures():
    vault = Contract.from_artifact(paths.artifact("ZkApiVault"), "0x" + "11" * 20)
    data = vault.encode("deposit", "0x" + "ab" * 32, 1000, [0] * 32)
    from eth_utils import keccak
    assert data.startswith("0x" + keccak(text="deposit(bytes32,uint128,uint256[32])")[:4].hex())
    inputs = [2, 31337, "0x" + "22" * 20, 5, 6, 7, 8, 9, 3, 1000, "0x" + "33" * 20, 11, True, 12]
    sel = keccak(text=f"mutualClose({WITHDRAWAL_TYPES},bytes,uint256[32])")[:4].hex()
    assert vault.encode("mutualClose", inputs, "0x" + "00" * 256, [0] * 32).startswith("0x" + sel)


def test_withdrawal_inputs_roundtrip():
    import eth_abi
    values = (2, 1, "0x" + "22" * 20, 5, 6, 7, 8, 9, 3, 1000, "0x" + "33" * 20, 11, True, 12)
    blob = "0x" + eth_abi.encode([WITHDRAWAL_TYPES], [values]).hex()
    out = decode_withdrawal_inputs(blob)
    assert out[9] == 1000 and out[12] is True and out[8] == 3


def test_link_bytecode_splices_poseidon():
    art = json.loads(paths.artifact("ZkApiVault").read_text())
    linked = link_bytecode(art, {"src/libraries/Bn254Poseidon.sol:Bn254Poseidon": "0x" + "ab" * 20})
    assert "__$" not in linked and "ab" * 20 in linked
    with pytest.raises(KeyError):
        link_bytecode(art, {})


def test_provider_matches_openrouter_management_semantics(tmp_path):
    p = Provider(tmp_path / "p.json")
    exp = int(time.time()) + 300
    iso = datetime.fromtimestamp(exp, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    status, created = p.create_key({"name": "lease-1", "limit": 0.02, "expires_at": iso,
                                    "include_byok_in_limit": True})
    assert status == 200 and created["data"]["expires_at"] == iso and created["data"]["limit"] == 0.02
    assert created["data"]["include_byok_in_limit"] is True
    h = created["data"]["hash"]
    status, res = p.chat(f"Bearer {created['key']}", {"model": "zkapi/echo",
                                                      "messages": [{"role": "user", "content": "hi"}]})
    assert status == 200 and res["usage"]["cost"] > 0
    status, usage = p.handle_management("GET", f"/v1/keys/{h}", {}, None)
    assert usage["data"]["usage"] == pytest.approx(res["usage"]["cost"]) and usage["data"]["byok_usage"] == 0
    status, dis = p.handle_management("PATCH", f"/v1/keys/{h}", {}, {"disabled": True})
    assert dis["data"]["disabled"] is True
    assert p.chat(f"Bearer {created['key']}", {"messages": [{"role": "user", "content": "x"}]})[0] == 401
    assert p.handle_management("DELETE", f"/v1/keys/{h}", {}, None) == (200, {"deleted": True})
    assert p.handle_management("GET", f"/v1/keys/{h}", {}, None)[0] == 404
    # the key ledger survives a restart (the server settles across restarts)
    assert Provider(tmp_path / "p.json").management_key == p.management_key


def test_provider_caps_spend_at_limit(tmp_path):
    p = Provider(tmp_path / "p.json")
    iso = datetime.fromtimestamp(int(time.time()) + 300, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _, k = p.create_key({"name": "x", "limit": 0.000001, "expires_at": iso, "include_byok_in_limit": True})
    assert p.chat(f"Bearer {k['key']}", {"messages": [{"role": "user", "content": "long " * 200}]})[0] == 200
    assert p.keys[k["data"]["hash"]]["usage"] <= 0.000001
    assert p.chat(f"Bearer {k['key']}", {"messages": [{"role": "user", "content": "again"}]})[0] == 402


@pytest.mark.skipif(not BUILT, reason="run m zkapi/setup")
def test_wallet_driver_persistence_and_guards(tmp_path):
    drv = WalletDriver(tmp_path)
    keys = drv("public_keys", state_seed="0x1", clear_seed="0x2")
    assert keys["circuit_id"] == paths.CIRCUIT_ID
    cfg = {"protocol_version": 2, "chain_id": 1, "contract_address": "0x" + "44" * 20, "request_charge_cap": 50000,
           "policy_charge_cap": 0, "policy_enabled": False, "state_signing_key": keys["state_signing_key"],
           "clearance_signing_key": keys["clearance_signing_key"]}
    drv("create", wallet="a", config=cfg)
    with pytest.raises(WalletError):
        drv("create", wallet="a", config=cfg)
    with pytest.raises(WalletError):
        drv("create", wallet="../evil", config=cfg)
    first = drv("deposit_params", wallet="a")
    assert drv("deposit_params", wallet="a") == first, "deposit secret must be durable/idempotent"
    drv.close()
    drv2 = WalletDriver(tmp_path)
    assert drv2("deposit_params", wallet="a") == first, "secret survives a driver restart"
    assert oct((tmp_path / "a.json").stat().st_mode)[-3:] == "600"
    with pytest.raises(WalletError, match="pending deposit|withdraw first"):
        drv2("delete", wallet="a")
    with pytest.raises(WalletError, match="no pending request"):
        drv2("discard_request", wallet="a", evidence={"status": "expired_unaccepted"})
    drv2.close()


# -- end to end on the real stack ------------------------------------------------------------------------
@pytest.fixture(scope="module")
def devnet():
    if not (E2E and BUILT):
        pytest.skip("e2e disabled or toolchain not built")
    from zkstack.devnet import Devnet
    root = Path(tempfile.mkdtemp(prefix="zkapi-e2e-"))
    d = Devnet(root=root, ports={"anvil": 51481, "indexer": 51482, "server": 51483, "provider": 51484})
    d.up()
    yield d
    d.down()


def test_e2e_deposit_request_settle_withdraw(devnet):
    d = devnet
    c = d.client()
    c.create_wallet("alice")
    dep = c.deposit("alice", 1_000_000, d.sender(3))
    assert dep["status"]["state"]["note"]["is_genesis"] is True
    assert c.note(dep["note_id"])["status"] == "active"
    r1 = c.chat("alice", {"model": "zkapi/echo", "messages": [{"role": "user", "content": "hello"}]})
    assert r1["status"] == 200 and "hello" in r1["completion"]["choices"][0]["message"]["content"]
    s1 = r1["settlement"]
    assert s1["charge_gwei"] > 0 and s1["balance_gwei"] == 1_000_000 - s1["charge_gwei"]
    assert r1["lease"]["key_source"] == "openrouter"
    # every lease key was disabled, read and deleted by the real server before it signed
    kinds = [e["type"] for e in d.provider.events if e.get("hash")]
    assert kinds[-5:] == ["issued", "inference", "disabled", "usage_read", "deleted"]
    r2 = c.chat("alice", {"messages": [{"role": "user", "content": "again"}]})
    assert r2["lease"]["request_nullifier"] != r1["lease"]["request_nullifier"], "requests are unlinkable"
    total = s1["charge_gwei"] + r2["settlement"]["charge_gwei"]
    dest = d.account(6)
    treasury_before = d.rpc.balance(d.deployment["treasury"])
    w = c.withdraw("alice", dest, d.sender(7))
    assert w["final_balance_gwei"] == 1_000_000 - total
    assert w["received_wei"] == (1_000_000 - total) * GWEI
    assert d.rpc.balance(d.deployment["treasury"]) - treasury_before == total * GWEI
    assert c.note(dep["note_id"])["status"] == "closed"


def test_e2e_stale_escape_is_challenged(devnet):
    d = devnet
    c = d.client()
    c.create_wallet("mallory")
    c.deposit("mallory", 500_000, d.sender(4))
    c.chat("mallory", {"messages": [{"role": "user", "content": "one"}]})
    c.chat("mallory", {"messages": [{"role": "user", "content": "two"}]})
    note_id = c.status("mallory")["state"]["note"]["note_id"]
    esc = c.escape("mallory", d.account(8), d.sender(8), history_index=1)  # already-spent state
    assert esc["stale"] and c.note(note_id)["status"] == "pending_withdrawal"
    end = time.time() + 60
    while time.time() < end and c.note(note_id)["status"] != "active":
        d.mine(1)
        time.sleep(0.5)
    assert c.note(note_id)["status"] == "active", "zkapi-challenged must restore the note"
    assert c.vault.call(c.rpc, "usedNullifiers", int(esc["withdrawal_nullifier"], 16)) is True
    st = c.status("mallory")
    assert st.get("reconciled") and st["closed"] is None
    # the honest latest state is still spendable after the failed fraud
    assert c.chat("mallory", {"messages": [{"role": "user", "content": "three"}]})["status"] == 200


def test_e2e_honest_escape_finalizes_after_window(devnet):
    d = devnet
    c = d.client()
    c.create_wallet("carol")
    c.deposit("carol", 300_000, d.sender(5))
    c.chat("carol", {"messages": [{"role": "user", "content": "hi"}]})
    bal = c.status("carol")["state"]["note"]["current_balance"]
    esc = c.escape("carol", d.account(9), d.sender(5))
    time.sleep(2)
    d.mine(2)
    time.sleep(1)
    assert c.note(esc["note_id"])["status"] == "pending_withdrawal", "honest escape must not be challenged"
    with pytest.raises(Exception):
        c.finalize_escape(esc["note_id"], d.sender(5))  # window still open
    d.fast_forward(86_401)
    before = d.rpc.balance(d.account(9))
    c.finalize_escape(esc["note_id"], d.sender(5))
    assert d.rpc.balance(d.account(9)) - before == bal * GWEI
    assert c.status("carol")["closed"]["mode"] == "escape"


def test_e2e_superseded_quote_requotes(devnet):
    d = devnet
    c = d.client()
    c.create_wallet("dave")
    c.deposit("dave", 200_000, d.sender(3))
    real_quote = c.quote
    calls = []

    def stale_quote():
        q = real_quote()
        if not calls:
            d.push_price()  # a new finalized round lands before the first lease request
        calls.append(q["round_id"])
        return q
    c.quote = stale_quote
    try:
        req, lease = c.open_lease("dave")
    finally:
        c.quote = real_quote
    assert lease["api_key"] and len(calls) == 2 and calls[0] != calls[1], calls
    assert c.settle("dave", req)["charge_gwei"] == 0  # unused lease settles at zero


def test_e2e_restart_keeps_everything(devnet):
    d = devnet
    before = {w["wallet"]: w["state"] for w in d.wallets("list")}
    root = d.client().current_root()
    d.down()
    d.up()
    c = d.client()
    assert c.current_root() == root
    assert {w["wallet"]: w["state"] for w in d.wallets("list")} == before
    assert c.chat("dave", {"messages": [{"role": "user", "content": "after restart"}]})["status"] == 200


def test_e2e_http_access_model(devnet):
    from http.server import ThreadingHTTPServer
    from zkstack.server import App, make_handler
    app = App(devnet)
    srv = ThreadingHTTPServer(("127.0.0.1", 51485), make_handler(app))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:51485/zkapi/api/"

    def call(path, body=None, headers=None):
        req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"content-type": "application/json", "accept": "application/json",
                                              **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    try:
        remote = {"x-forwarded-for": "203.0.113.9"}
        assert call("devnet/reset", {}, remote)[0] == 403
        status, w = call("wallets", {"name": "web", "deposit_gwei": 100_000}, remote)
        assert status == 200 and w["wallet_token"]
        assert call("wallets/web/chat", {"prompt": "x"}, remote)[0] == 403
        tok = {**remote, "x-zkapi-wallet-token": w["wallet_token"]}
        status, r = call("wallets/web/chat", {"prompt": "via http"}, tok)
        assert status == 200 and r["settlement"]["charge_gwei"] > 0
        status, comp = call("v1/chat/completions", {"model": "zkapi/echo",
                                                    "messages": [{"role": "user", "content": "openai"}]})
        assert status == 200 and comp["zkapi"]["settlement"]["charge_gwei"] > 0
        status, man = call("devnet/manifest")
        assert man["trusted_deployment"]["circuit_id"] == paths.CIRCUIT_ID
        status, att = call("devnet/server/v1/attestation")
        assert att["state_signing_key"] == devnet.deployment["state_signing_key"]
        status, root = call("devnet/indexer/v1/tree/root")
        assert int(root["root"], 16) == devnet.client().current_root()
        # agents: on the contract, editable by default on the echo devnet, claude brain owner-only
        status, ag = call("agents", None, remote)
        assert status == 200 and ag["agents"][0]["id"] == "zeke" and ag["can_edit"] and not ag["is_owner"]
        status, z = call("agents/zeke", {"prompt": "be terse", "temperature": 0.5}, remote)
        assert status == 200 and z["params"]["prompt"] == "be terse"
        assert call("agents/zeke", {"brain": "claude"}, remote)[0] == 403
        assert call("agents/publish", {}, remote)[0] == 403
        status, a = call("agents", {"id": "scout"}, remote)
        assert status == 200 and a["brain"] == "zkapi"
        status, done = call("agents/scout/run", {"query": "hi"}, remote)
        assert status == 200 and done["usage"]["charge_gwei"] > 0 and "hi" in done["answer"]
        assert [s["tool"] for s in done["result"]][-3:] == ["zkapi:lease", "zkapi:settle", "finish"]
        assert call("agents/zeke/delete", {}, remote)[0] == 400
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.skipif(not LIVE, reason="ZKAPI_TEST_LIVE=1 to verify the live mainnet deployment")
def test_live_mainnet_verifies():
    from zkstack import networks
    v = networks.verify("mainnet")
    assert v["ok"], [c for c in v["checks"] if not c["ok"]]
