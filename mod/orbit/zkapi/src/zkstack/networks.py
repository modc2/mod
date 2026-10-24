"""Live zkAPI deployments (Ethereum mainnet, Sepolia): pins, probes, verification.

The trusted pins are the ones the upstream SDK ships in
``vendor/zkapi/sdk/assets/config/<network>.json``. ``verify()`` cross-checks
four independent sources against them:

  1. the operator's live manifest (``/config.json``)
  2. the chain itself (vault immutables, runtime code keccak, Chainlink feed)
  3. the protocol server (``/health``, ``/v1/attestation``, ``/v2/billing/quote``)
  4. the indexer (root / next note id agree with the vault)

and checks that this module's local Groth16 proving keys hash to the pinned
values — i.e. that proofs made here are accepted by the live verifier.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

from eth_utils import keccak

from . import paths
from .chain import Contract, Rpc, RpcError
from .client import Deployment, ZkApiError, http_json

NETWORKS = ("mainnet", "sepolia")
VAULT_READS = ["currentRoot", "nextNoteId", "requestChargeCap", "challengePeriod", "noteTtl", "paused",
               "treasury", "proofAdapter", "stateSigningKeyX", "stateSigningKeyY", "clearanceSigningKeyX",
               "clearanceSigningKeyY", "nativeAssetWeiPerUnit", "PROTOCOL_VERSION", "owner"]


def pins(network: str) -> dict:
    if network not in NETWORKS:
        raise ZkApiError(f"unknown network {network}; choose one of {NETWORKS}", code="unknown_network")
    path = paths.UPSTREAM_DIR / "sdk" / "assets" / "config" / f"{network}.json"
    data = json.loads(path.read_text())
    return {**data["trusted_deployment"], "deployment_manifest_url": data.get("deployment_manifest_url"),
            "require_oa_key_source": data.get("require_oa_key_source")}


def headers(network: str) -> dict:
    # Sepolia's operator gates /v2/* behind a shared password (docs/testnet-auth.md).
    pw = os.environ.get("ZKAPI_TESTNET_PASSWORD") if network == "sepolia" else None
    return {"X-ZKAPI-Testnet-Password": pw} if pw else {}


def deployment(network: str) -> Deployment:
    p = pins(network)
    return Deployment(name=network, chain_id=p["chain_id"], vault=p["contract_address"],
                      rpc_url=os.environ.get(f"ZKAPI_{network.upper()}_RPC", p["rpc_url"]),
                      server_url=p["protocol_server_url"], indexer_url=p["indexer_url"],
                      request_charge_cap=p["request_charge_cap"], state_signing_key=p["state_signing_key"],
                      clearance_signing_key=p["clearance_signing_key"], headers=headers(network))


def _sha256(path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _hex_eq(a: Any, b: Any) -> bool:
    try:
        return int(str(a), 16) == int(str(b), 16)
    except (TypeError, ValueError):
        return str(a).lower() == str(b).lower()


def probe(network: str) -> dict:
    """Read-only snapshot of one live deployment."""
    p = pins(network)
    d = deployment(network)
    out: dict[str, Any] = {"network": network, "pins": p, "at": int(time.time())}

    def get(url: str, hdrs: dict | None = None) -> Any:
        try:
            status, body = http_json(url, headers=hdrs, timeout=15)
            return body if status == 200 else {"_status": status, "_body": body}
        except ZkApiError as e:
            return {"_error": str(e)}

    out["manifest"] = get(p["deployment_manifest_url"]) if p.get("deployment_manifest_url") else None
    base = p["protocol_server_url"].rstrip("/")
    out["server"] = {"health": get(base + "/health"), "attestation": get(base + "/v1/attestation"),
                     "quote": get(base + "/v2/billing/quote", d.headers)}
    ibase = p["indexer_url"].rstrip("/")
    out["indexer"] = {"root": get(ibase + "/v1/tree/root"), "next_note_id": get(ibase + "/v1/tree/next-note-id")}
    rpc = Rpc(d.rpc_url, timeout=20)
    vault_abi = json.loads(paths.artifact("ZkApiVault").read_text())["abi"]
    vault = Contract(vault_abi, p["contract_address"])
    chain: dict[str, Any] = {"rpc_url": d.rpc_url}
    try:
        chain["chain_id"] = rpc.chain_id()
        chain["block"] = rpc.block_number()
        for name in VAULT_READS:
            try:
                v = vault.call(rpc, name)
                chain[name] = hex(v) if isinstance(v, int) and v > 2**64 else v
            except RpcError as e:
                chain[name] = {"_error": str(e)}
        chain["vault_balance_eth"] = rpc.balance(p["contract_address"]) / 1e18
        code = rpc.code(p["contract_address"])
        chain["vault_runtime_keccak256"] = "0x" + keccak(hexstr=code).hex() if code not in ("0x", "") else None
        adapter = chain.get("proofAdapter")
        if isinstance(adapter, str):
            acode = rpc.code(adapter)
            chain["adapter_runtime_keccak256"] = "0x" + keccak(hexstr=acode).hex() if acode not in ("0x", "") else None
        feed = p["native_price_feed_address"]
        raw = rpc.eth_call(feed, "0xfeaf968c", "finalized")
        words = [int(raw[2 + 64 * i: 2 + 64 * (i + 1)], 16) for i in range(5)]
        chain["price_feed_finalized"] = {"round_id": str(words[0]), "answer": str(words[1]),
                                         "usd": words[1] / 10 ** p["native_price_feed_decimals"],
                                         "updated_at": words[3]}
    except RpcError as e:
        chain["_error"] = str(e)
    out["chain"] = chain
    out["local_setup"] = {f: _sha256(paths.SETUP_DIR / f) for f in
                          ("request.pk", "request.vk", "withdrawal.pk", "withdrawal.vk")}
    return out


def verify(network: str, snapshot: dict | None = None) -> dict:
    """Cross-check pins, manifest, chain, server, indexer and local proving keys."""
    s = snapshot or probe(network)
    p, m, c = s["pins"], s.get("manifest") or {}, s["chain"]
    health, att, quote = s["server"]["health"], s["server"]["attestation"], s["server"]["quote"]
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: Any = None) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    sk, ck = p["state_signing_key"], p["clearance_signing_key"]
    check("rpc chain id matches pin", c.get("chain_id") == p["chain_id"], c.get("chain_id"))
    check("vault code deployed", bool(c.get("vault_runtime_keccak256")), p["contract_address"])
    ev = (m.get("deployment_evidence") or {}) if isinstance(m, dict) else {}
    if ev.get("vault_runtime_keccak256"):
        check("vault runtime keccak == operator evidence",
              _hex_eq(c.get("vault_runtime_keccak256"), ev["vault_runtime_keccak256"]), c.get("vault_runtime_keccak256"))
    if ev.get("verifier_runtime_keccak256"):
        check("proof adapter runtime keccak == operator evidence",
              _hex_eq(c.get("adapter_runtime_keccak256"), ev["verifier_runtime_keccak256"]),
              c.get("adapter_runtime_keccak256"))
    check("vault state signing key == pin",
          _hex_eq(c.get("stateSigningKeyX"), sk["x"]) and _hex_eq(c.get("stateSigningKeyY"), sk["y"]))
    check("vault clearance signing key == pin",
          _hex_eq(c.get("clearanceSigningKeyX"), ck["x"]) and _hex_eq(c.get("clearanceSigningKeyY"), ck["y"]))
    check("vault request charge cap == pin", c.get("requestChargeCap") == p["request_charge_cap"],
          c.get("requestChargeCap"))
    check("vault is native ETH (1 gwei units)", c.get("nativeAssetWeiPerUnit") == 10**9, c.get("nativeAssetWeiPerUnit"))
    check("vault protocol version 2", c.get("PROTOCOL_VERSION") == 2, c.get("PROTOCOL_VERSION"))
    check("vault not paused", c.get("paused") is False, c.get("paused"))
    if m and "_error" not in m and "_status" not in m:
        check("manifest deployment id == pin", m.get("deployment_id") == p["deployment_id"], m.get("deployment_id"))
        check("manifest vault == pin", str(m.get("contract_address", "")).lower() == p["contract_address"].lower())
        check("manifest signing keys == pin", m.get("state_signing_key") == sk and m.get("clearance_signing_key") == ck)
        if m.get("proof_adapter_address"):
            check("manifest proof adapter == vault.proofAdapter",
                  str(m["proof_adapter_address"]).lower() == str(c.get("proofAdapter", "")).lower())
        if m.get("treasury_address"):
            check("manifest treasury == vault.treasury",
                  str(m["treasury_address"]).lower() == str(c.get("treasury", "")).lower())
    else:
        check("manifest reachable", False, m)
    check("server healthy", isinstance(health, dict) and health.get("status") == "ok", health.get("status")
          if isinstance(health, dict) else health)
    if isinstance(att, dict) and att.get("status") == "ok":
        check("server attestation keys == pin", att.get("state_signing_key") == sk and att.get("clearance_signing_key") == ck)
        check("server attests the pinned vault", str(att.get("contract_address", "")).lower() == p["contract_address"].lower())
        check("server root == vault root", _hex_eq(att.get("current_root"), c.get("currentRoot")),
              {"server": att.get("current_root"), "vault": c.get("currentRoot")})
    else:
        check("server attestation reachable", False, att)
    if isinstance(quote, dict) and quote.get("asset") == "native_eth":
        check("server quote uses the pinned feed", quote.get("feed_address", "").lower() ==
              p["native_price_feed_address"].lower())
        fin = c.get("price_feed_finalized") or {}
        check("server quote round <= finalized chain round",
              int(quote.get("round_id", "0")) <= int(fin.get("round_id", "0") or 0),
              {"quote": quote.get("round_id"), "finalized": fin.get("round_id")})
        check("quote is fresh", int(quote.get("expires_at", 0)) > time.time(), quote.get("expires_at"))
    else:
        detail = quote.get("_status") if isinstance(quote, dict) else quote
        if detail == 401 and network == "sepolia":
            check("billing quote password-gated (expected on Sepolia; set ZKAPI_TESTNET_PASSWORD)", True, 401)
        else:
            check("billing quote reachable", False, detail)
    root, nid = s["indexer"]["root"], s["indexer"]["next_note_id"]
    if isinstance(root, dict) and "root" in root:
        check("indexer root == vault root", _hex_eq(root["root"], c.get("currentRoot")),
              {"indexer": root["root"], "vault": c.get("currentRoot")})
    if isinstance(nid, dict) and "next_note_id" in nid:
        check("indexer next note id == vault", nid["next_note_id"] == c.get("nextNoteId"), nid["next_note_id"])
    local = s["local_setup"]
    check("local request.pk == pinned (proofs from this module verify on-chain)",
          local.get("request.pk") == p["request_proving_key_sha256"], local.get("request.pk"))
    check("local withdrawal.pk == pinned", local.get("withdrawal.pk") == p["withdrawal_proving_key_sha256"],
          local.get("withdrawal.pk"))
    setup = (m.get("proof_setup") or {}) if isinstance(m, dict) else {}
    if setup.get("request_verifying_key_sha256"):
        check("local request.vk == manifest", local.get("request.vk") == setup["request_verifying_key_sha256"])
        check("local withdrawal.vk == manifest", local.get("withdrawal.vk") == setup["withdrawal_verifying_key_sha256"])
    check("circuit id", p.get("circuit_id") == paths.CIRCUIT_ID, p.get("circuit_id"))
    passed = sum(1 for x in checks if x["ok"])
    return {"network": network, "ok": passed == len(checks), "passed": passed, "total": len(checks),
            "checks": checks,
            "summary": {"vault": p["contract_address"], "notes": c.get("nextNoteId"),
                        "vault_balance_eth": c.get("vault_balance_eth"), "block": c.get("block"),
                        "eth_usd": (c.get("price_feed_finalized") or {}).get("usd"),
                        "server": p["protocol_server_url"], "key_source": "oa_org" if p.get("require_oa_key_source")
                        else "openrouter"}}
