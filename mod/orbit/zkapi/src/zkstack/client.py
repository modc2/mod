"""zkAPI v2 client flows over the upstream wire protocol.

Works against any deployment that speaks the upstream protocol: the module's
own devnet, or a live server (Sepolia/mainnet) when a funded sender is given.
The sequence mirrors the browser SDK / upstream acceptance harness:

  deposit:   indexer snapshot -> wallet.deposit_params -> vault.deposit
             -> NoteDeposited -> wallet.confirm_deposit
  request:   GET /v2/billing/quote -> tree path -> wallet.prepare_request (Groth16
             request proof, write-ahead journal) -> POST /v2/openrouter/leases
             -> inference with the issued runtime key -> POST .../leases/{id}
             (retire) -> GET /v2/requests/{id} -> wallet.complete_response
             (verifies the server's Schnorr signature over the next state)
  withdraw:  POST /v2/withdraw/clearance -> wallet.prepare_withdrawal (Groth16
             withdrawal proof) -> vault.mutualClose
  escape:    wallet.prepare_withdrawal(escape) -> vault.initiateEscapeWithdrawal
             -> (challenge window) -> vault.finalizeEscapeWithdrawal
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any

import eth_abi

from .chain import GWEI, Contract, Rpc, RpcError, Sender
from .wallet import WalletDriver, WalletError

WITHDRAWAL_TYPES = ("(uint16,uint64,address,uint256,uint256,uint256,uint256,uint256,"
                    "uint32,uint128,address,uint256,bool,uint256)")
WITHDRAWAL_FIELDS = ["protocol_version", "chain_id", "contract_address", "active_root",
                     "state_signing_key_x", "state_signing_key_y", "clearance_signing_key_x",
                     "clearance_signing_key_y", "note_id", "final_balance", "destination",
                     "withdrawal_nullifier", "has_clearance", "withdrawal_tag"]
NOTE_STATUS = ["uninitialized", "active", "pending_withdrawal", "closed"]
LEASE_MODE = "openrouter_ephemeral_lease"
MAX_NATIVE_UNITS = 9_007_199_254_740_991


class ZkApiError(RuntimeError):
    def __init__(self, message: str, code: str | None = None, status: int | None = None, detail: Any = None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.detail = detail


@dataclass
class Deployment:
    """Everything a client needs to talk to one zkAPI deployment."""
    name: str
    chain_id: int
    vault: str
    rpc_url: str
    server_url: str
    indexer_url: str
    request_charge_cap: int
    state_signing_key: dict
    clearance_signing_key: dict
    headers: dict = field(default_factory=dict)

    def wallet_config(self) -> dict:
        return {"protocol_version": 2, "chain_id": self.chain_id, "contract_address": self.vault.lower(),
                "request_charge_cap": self.request_charge_cap, "policy_charge_cap": 0, "policy_enabled": False,
                "state_signing_key": self.state_signing_key, "clearance_signing_key": self.clearance_signing_key}


def http_json(url: str, method: str = "GET", value: Any = None, headers: dict | None = None,
              timeout: float = 60.0) -> tuple[int, Any]:
    data = None if value is None else json.dumps(value).encode()
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"content-type": "application/json", "accept": "application/json",
                                          "user-agent": "zkapi-module/0.2", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    except (urllib.error.URLError, OSError) as e:
        raise ZkApiError(f"{url}: unreachable ({e})", code="unreachable") from None
    try:
        return status, json.loads(raw or b"null")
    except ValueError:
        return status, {"raw": raw[:500].decode(errors="replace")}


def decode_withdrawal_inputs(inputs_abi: str) -> list:
    return list(eth_abi.decode([WITHDRAWAL_TYPES], bytes.fromhex(inputs_abi[2:]))[0])


class ZkApiClient:
    def __init__(self, deployment: Deployment, wallets: WalletDriver, vault_abi: list[dict]):
        self.d = deployment
        self.rpc = Rpc(deployment.rpc_url)
        self.wallets = wallets
        self.vault = Contract(vault_abi, deployment.vault)

    # -- HTTP to the upstream services ------------------------------------------------------
    def server(self, path: str, method: str = "GET", value: Any = None, expect: tuple = (200,)) -> Any:
        status, body = http_json(self.d.server_url.rstrip("/") + path, method, value, self.d.headers)
        if status not in expect:
            code = body.get("error_code") if isinstance(body, dict) else None
            msg = body.get("error_message") if isinstance(body, dict) else None
            raise ZkApiError(f"server {method} {path}: {status} {code or ''} {msg or ''}".strip(),
                             code=code, status=status, detail=body)
        return body

    def indexer(self, path: str) -> Any:
        status, body = http_json(self.d.indexer_url.rstrip("/") + path, headers=self.d.headers)
        if status != 200:
            raise ZkApiError(f"indexer {path}: {status}", status=status, detail=body)
        return body

    # -- chain state -----------------------------------------------------------------------------
    def current_root(self) -> int:
        return int(self.vault.call(self.rpc, "currentRoot"))

    def note(self, note_id: int) -> dict:
        commitment, amount, expiry, status = self.vault.call(self.rpc, "notes", note_id)
        return {"note_id": note_id, "commitment": commitment, "deposit_amount": amount, "expiry_ts": expiry,
                "status": NOTE_STATUS[status] if status < len(NOTE_STATUS) else status}

    def pending_withdrawal(self, note_id: int) -> dict:
        exists, root, nullifier, balance, dest, deadline = self.vault.call(self.rpc, "pendingWithdrawals", note_id)
        return {"exists": exists, "active_root": hex(root), "withdrawal_nullifier": hex(nullifier),
                "final_balance": balance, "destination": dest, "challenge_deadline": deadline}

    def synced_snapshot(self, timeout: float = 30.0) -> dict:
        """Indexer snapshot whose root equals the vault's current root."""
        end = time.time() + timeout
        last = None
        while time.time() < end:
            root = self.current_root()
            try:
                snap = self.indexer("/v1/tree/snapshot")
                if int(snap["root"], 16) == root:
                    return snap
                last = f"indexer root {snap['root']} != chain {hex(root)}"
            except ZkApiError as e:
                last = str(e)
            time.sleep(0.2)
        raise ZkApiError(f"indexer did not catch up with the vault: {last}", code="indexer_lag")

    def tree_path(self, note_id: int, require_existing: bool = True, snapshot: dict | None = None) -> dict:
        snap = snapshot or self.synced_snapshot()
        return self.wallets("tree_path", args={"snapshot": snap, "note_id": note_id,
                                               "require_existing": require_existing})

    # -- wallets --------------------------------------------------------------------------------------
    def create_wallet(self, name: str, labels: dict | None = None) -> dict:
        return self.wallets("create", wallet=name, config=self.d.wallet_config(), labels=labels or {})

    def status(self, name: str) -> dict:
        st = self.wallets("status", wallet=name)
        note = (st.get("state") or {}).get("note")
        if note:
            try:
                st["onchain"] = self.note(note["note_id"])
                if st["onchain"]["status"] == "pending_withdrawal":
                    st["onchain"]["pending"] = self.pending_withdrawal(note["note_id"])
                closed = (st.get("closed") or {}).get("mode")
                # Reconcile the local view with the vault: a challenged escape restores
                # the note (the wallet's latest signed state is spendable again); a
                # finalized one closes it for good.
                if closed == "escape_pending" and st["onchain"]["status"] == "active":
                    st = {**self.wallets("reopen", wallet=name), "onchain": st["onchain"],
                          "reconciled": "escape was challenged; note restored"}
                elif closed == "escape_pending" and st["onchain"]["status"] == "closed":
                    st = {**self.wallets("mark_closed", wallet=name, mode="escape",
                                         transaction=st["closed"].get("transaction")),
                          "onchain": st["onchain"], "reconciled": "escape finalized"}
            except (RpcError, ZkApiError) as e:
                st["onchain_error"] = str(e)
        return st

    def _ensure_wallet_matches(self, name: str) -> dict:
        cfg = self.wallets("config", wallet=name)
        if int(cfg["chain_id"]) != self.d.chain_id or cfg["contract_address"].lower().lstrip("0x").lstrip("0") != \
                self.d.vault.lower()[2:].lstrip("0"):
            raise ZkApiError(f"wallet {name} belongs to a different deployment", code="wrong_deployment")
        return cfg

    # -- deposit -----------------------------------------------------------------------------------------
    def deposit(self, name: str, amount_gwei: int, sender: Sender, attempts: int = 4) -> dict:
        amount_gwei = int(amount_gwei)
        if not 0 < amount_gwei <= MAX_NATIVE_UNITS:
            raise ZkApiError("deposit must be between 1 gwei and 2^53-1 gwei", code="bad_amount")
        self._ensure_wallet_matches(name)
        params = self.wallets("deposit_params", wallet=name)
        commitment = "0x" + int(params["registration_commitment"], 16).to_bytes(32, "big").hex()
        last: Exception | None = None
        for _ in range(attempts):
            snap = self.synced_snapshot()
            path = self.tree_path(snap["next_note_id"], require_existing=False, snapshot=snap)
            data = self.vault.encode("deposit", commitment, amount_gwei, path["siblings"])
            try:
                receipt = sender.send(self.d.vault, data, value=amount_gwei * GWEI)
            except (RpcError, RuntimeError) as e:  # a concurrent deposit moved the root: retry on a fresh path
                last = e
                time.sleep(0.3)
                continue
            events = [e for e in self.vault.decode_logs(receipt) if e["event"] == "NoteDeposited"]
            if not events:
                raise ZkApiError("deposit mined without a NoteDeposited event", code="no_event")
            ev = events[0]["args"]
            note_id = int(ev["noteId"])
            state = self.wallets("confirm_deposit", wallet=name, args={
                "note_id": note_id, "amount": int(ev["amount"]), "expiry_ts": int(ev["expiryTs"])})
            self.synced_snapshot()
            return {"wallet": name, "note_id": note_id, "amount_gwei": amount_gwei,
                    "expiry_ts": int(ev["expiryTs"]), "transaction": receipt["transactionHash"],
                    "block": int(receipt["blockNumber"], 16), "from": sender.address, "status": state}
        raise ZkApiError(f"deposit failed: {self.vault.revert_reason(last) if last else 'unknown'}", code="deposit_failed")

    # -- request: lease -> inference -> settlement -------------------------------------------------------
    def quote(self) -> dict:
        return self.server("/v2/billing/quote")

    def _prepare(self, name: str, note_id: int) -> dict:
        quote = self.quote()
        path = self.tree_path(note_id)
        payload = json.dumps({"mode": LEASE_MODE, "version": 1, "billing_quote": quote}, separators=(",", ":"))
        cid = f"zkapi-{uuid.uuid4()}"
        prepared = self.wallets("prepare_request", wallet=name, args={
            "payload": payload, "active_root": path["active_root"], "merkle_siblings": path["siblings"],
            "client_request_id": cid, "request_time": int(time.time()), "created_at_ms": int(time.time() * 1000)})
        return prepared["request"]

    def open_lease(self, name: str) -> tuple[dict, dict]:
        """Prove solvency and obtain a bounded runtime key. Returns (request, lease)."""
        self._ensure_wallet_matches(name)
        if self.wallets("pending_request", wallet=name):
            self.recover(name)
        st = self.wallets("status", wallet=name)
        note = (st.get("state") or {}).get("note")
        if not note:
            raise ZkApiError(f"wallet {name} has no deposited note", code="no_note")
        if st.get("closed"):
            raise ZkApiError(f"wallet {name} is closed", code="closed")
        for attempt in range(3):
            request = self._prepare(name, note["note_id"])
            status, body = http_json(self.d.server_url.rstrip("/") + "/v2/openrouter/leases", "POST", request,
                                     self.d.headers, timeout=120)
            if status == 201:
                return request, body
            code = body.get("error_code") if isinstance(body, dict) else None
            # A quote that moved on before the server saw the request: prove the request was
            # never accepted (authoritative handshake), drop the journal and re-quote.
            if code in ("native_quote_superseded", "native_quote_expired", "stale_root"):
                if self._expire(name, request):
                    continue
            raise ZkApiError(f"lease rejected: {status} {code or ''} "
                             f"{(body or {}).get('error_message', '') if isinstance(body, dict) else ''}".strip(),
                             code=code, status=status, detail=body)
        raise ZkApiError("lease could not be issued after re-quoting", code="lease_failed")

    def _expire(self, name: str, request: dict) -> bool:
        cid = request["client_request_id"]
        status, body = http_json(f"{self.d.server_url.rstrip('/')}/v2/openrouter/leases/{cid}/expire", "POST",
                                 request, self.d.headers)
        if status == 200 and isinstance(body, dict) and body.get("status") in ("expired_unaccepted",
                                                                               "superseded_unaccepted"):
            self.wallets("discard_request", wallet=name, evidence=body)
            return True
        return False

    def settle(self, name: str, request: dict, timeout: float = 90.0) -> dict:
        """Retire the lease and install the server-signed next state."""
        cid = request["client_request_id"]
        status, body = http_json(f"{self.d.server_url.rstrip('/')}/v2/openrouter/leases/{cid}", "POST", request,
                                 self.d.headers, timeout=120)
        if status not in (200, 409) or (status == 409 and (body or {}).get("error_code") not in (
                "lease_settlement_pending", "lease_pending", None)):
            if status != 200:
                raise ZkApiError(f"lease retirement failed: {status} {(body or {}).get('error_code')}",
                                 code=(body or {}).get("error_code"), status=status, detail=body)
        end = time.time() + timeout
        while time.time() < end:
            found = self.server(f"/v2/requests/{cid}")
            response = found.get("request_response")
            if response:
                before = self.wallets("status", wallet=name)
                after = self.wallets("complete_response", wallet=name, response=response)
                return {"client_request_id": cid, "charge_gwei": int(response["charge_applied"]),
                        "response_payload": _maybe_json(response.get("response_payload")),
                        "balance_before_gwei": before["state"]["note"]["current_balance"],
                        "balance_gwei": after["state"]["note"]["current_balance"],
                        "next_anchor": response["next_anchor"], "status": after}
            time.sleep(0.5)
        raise ZkApiError(f"settlement for {cid} not available after {timeout}s; run recover later",
                         code="settlement_pending")

    def chat(self, name: str, body: dict) -> dict:
        """One anonymous, proof-paid OpenAI-style chat completion."""
        t0 = time.time()
        request, lease = self.open_lease(name)
        base = lease["openrouter_api_base"].rstrip("/")
        payload = dict(body)
        payload["stream"] = False
        try:
            status, result = http_json(base + "/chat/completions", "POST", payload,
                                       {"authorization": f"Bearer {lease['api_key']}"}, timeout=180)
        except ZkApiError as e:
            status, result = 502, {"error": {"message": str(e)}}
        settlement = self.settle(name, request)
        return {
            "status": status, "completion": result, "lease": {
                "client_request_id": request["client_request_id"], "key_source": lease.get("key_source"),
                "expires_at": lease.get("expires_at"), "api_base": base,
                "solvency_bound_gwei": int(request["public_inputs"]["solvency_bound"], 16)
                if str(request["public_inputs"]["solvency_bound"]).startswith("0x")
                else int(request["public_inputs"]["solvency_bound"]),
                "request_nullifier": request["public_inputs"]["request_nullifier"],
                "billing_quote": lease.get("billing_quote")},
            "settlement": {k: v for k, v in settlement.items() if k != "status"},
            "elapsed_s": round(time.time() - t0, 2),
        }

    def recover(self, name: str) -> dict:
        """Resolve a write-ahead journal left by an interrupted request."""
        pending = self.wallets("pending_request", wallet=name)
        if not pending:
            return {"recovered": False, "reason": "no pending request"}
        request = pending["request"]
        cid = request["client_request_id"]
        found = self.server(f"/v2/requests/{cid}", expect=(200, 404))
        if isinstance(found, dict) and found.get("request_response"):
            out = self.wallets("complete_response", wallet=name, response=found["request_response"])
            return {"recovered": True, "how": "signed settlement recovered", "status": out}
        if isinstance(found, dict) and found.get("nullifier_status") in ("reserved", "active", "settling", "pending"):
            try:
                return {"recovered": True, "how": "retired lease", **self.settle(name, request, timeout=30)}
            except ZkApiError as e:
                return {"recovered": False, "reason": str(e), "nullifier_status": found.get("nullifier_status")}
        if self._expire(name, request):
            return {"recovered": True, "how": "server confirmed the request was never accepted; journal discarded"}
        # Not expired and not known: the exact saved request can still be sent.
        status, body = http_json(self.d.server_url.rstrip("/") + "/v2/openrouter/leases", "POST", request,
                                 self.d.headers, timeout=120)
        if status == 201:
            return {"recovered": True, "how": "re-sent exact saved request", **self.settle(name, request)}
        return {"recovered": False, "reason": f"{status} {(body or {}).get('error_code')}"}

    # -- withdrawals --------------------------------------------------------------------------------------
    def _withdrawal_call(self, plan: dict) -> tuple[list, str, list]:
        return decode_withdrawal_inputs(plan["inputs_abi"]), plan["proof_hex"], [int(s, 16) for s in plan["siblings"]]

    def withdraw(self, name: str, destination: str, sender: Sender) -> dict:
        """Cooperative close: server clearance + withdrawal proof -> vault.mutualClose."""
        self._ensure_wallet_matches(name)
        st = self.wallets("status", wallet=name)
        if st.get("pending_request"):
            self.recover(name)
            st = self.wallets("status", wallet=name)
        note = st["state"]["note"]
        clearance = self.server("/v2/withdraw/clearance", "POST",
                                {"withdrawal_nullifier": st["withdrawal_nullifier"]})
        path = self.tree_path(note["note_id"])
        plan = self.wallets("prepare_withdrawal", wallet=name, args={
            "mode": "mutual", "destination": destination, "active_root": path["active_root"],
            "merkle_siblings": path["siblings"], "clearance": clearance})
        inputs, proof, siblings = self._withdrawal_call(plan)
        before = self.rpc.balance(destination)
        receipt = sender.send(self.d.vault, self.vault.encode("mutualClose", inputs, proof, siblings))
        self.wallets("mark_closed", wallet=name, mode="mutual", transaction=receipt["transactionHash"])
        return {"wallet": name, "mode": "mutual", "note_id": note["note_id"], "transaction": receipt["transactionHash"],
                "final_balance_gwei": int(inputs[9]), "destination": destination,
                "received_wei": self.rpc.balance(destination) - before + _gas_paid(receipt, sender, destination),
                "events": self.vault.decode_logs(receipt)}

    def escape(self, name: str, destination: str, sender: Sender, history_index: int | None = None) -> dict:
        """Unilateral exit with no server cooperation (starts the challenge window).

        ``history_index`` escapes with an *earlier, already-spent* signed state —
        i.e. attempts fraud; the challenger is expected to defeat it.
        """
        self._ensure_wallet_matches(name)
        st = self.wallets("status", wallet=name)
        note = st["state"]["note"]
        path = self.tree_path(note["note_id"])
        cmd: dict[str, Any] = {"args": {"mode": "escape", "destination": destination,
                                        "active_root": path["active_root"], "merkle_siblings": path["siblings"]}}
        if history_index is not None:
            cmd["history_index"] = int(history_index)
        plan = self.wallets("prepare_withdrawal", wallet=name, **cmd)
        inputs, proof, siblings = self._withdrawal_call(plan)
        receipt = sender.send(self.d.vault, self.vault.encode("initiateEscapeWithdrawal", inputs, proof, siblings))
        self.wallets("mark_closed", wallet=name, mode="escape_pending", transaction=receipt["transactionHash"])
        events = self.vault.decode_logs(receipt)
        deadline = next((e["args"]["challengeDeadline"] for e in events if e["event"] == "EscapeWithdrawalInitiated"), None)
        return {"wallet": name, "mode": "escape", "stale": history_index is not None, "note_id": note["note_id"],
                "transaction": receipt["transactionHash"], "final_balance_gwei": int(inputs[9]),
                "withdrawal_nullifier": hex(inputs[11]), "challenge_deadline": deadline, "events": events}

    def finalize_escape(self, note_id: int, sender: Sender) -> dict:
        receipt = sender.send(self.d.vault, self.vault.encode("finalizeEscapeWithdrawal", int(note_id)))
        return {"note_id": note_id, "transaction": receipt["transactionHash"], "events": self.vault.decode_logs(receipt)}

    def claim_expired(self, note_id: int, sender: Sender) -> dict:
        path = self.tree_path(int(note_id))
        receipt = sender.send(self.d.vault, self.vault.encode("claimExpired", int(note_id), path["siblings"]))
        return {"note_id": note_id, "transaction": receipt["transactionHash"], "events": self.vault.decode_logs(receipt)}


def _gas_paid(receipt: dict, sender: Sender, destination: str) -> int:
    """Add back gas when the destination also paid for the transaction."""
    if sender.address.lower() != destination.lower():
        return 0
    return int(receipt.get("gasUsed", "0x0"), 16) * int(receipt.get("effectiveGasPrice", "0x0"), 16)


def _maybe_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


__all__ = ["Deployment", "ZkApiClient", "ZkApiError", "WalletError", "http_json", "decode_withdrawal_inputs"]
