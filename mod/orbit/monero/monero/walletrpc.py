"""
Client for monero-wallet-rpc -- the module's spending backend.

This module does not build Monero transactions itself, and says so plainly
rather than pretending. A Monero spend needs a CLSAG ring signature over a
16-member ring and a Bulletproofs+ range proof; in pure Python that is both
impractically slow and impossible to verify against anything, and a proof that
is subtly wrong is a transaction the network silently rejects -- or worse, one
that leaks which ring member is real.

So spending is delegated to the reference implementation, which is what every
serious Monero tool does:

    monero-wallet-rpc --wallet-file ~/wallets/mine \\
        --rpc-bind-port 18083 --disable-rpc-login \\
        --daemon-address node.example:18081

Everything else in this module -- addresses, keys, seed phrases, scanning,
explorer, bridging -- works with no wallet RPC at all.

The send flow deliberately mirrors the rest of the fleet: `transfer` is called
with do_not_relay first, which builds and signs a real transaction and returns
its exact fee, size and hash without publishing it. Only relay_tx publishes.
"""

import os

import requests

try:
    from .daemon import xmr
except ImportError:  # loaded as a loose module by the mod runtime
    from daemon import xmr

DEFAULT_URL = "http://127.0.0.1:18083"


class WalletRPCError(Exception):
    pass


class WalletRPC:
    def __init__(self, url: str = None, user: str = None, password: str = None,
                 timeout: int = 60):
        self.url = (url or os.environ.get("MONERO_WALLET_RPC_URL") or DEFAULT_URL).rstrip("/")
        self.user = user or os.environ.get("MONERO_WALLET_RPC_USER")
        self.password = password or os.environ.get("MONERO_WALLET_RPC_PASSWORD")
        self.timeout = timeout

    # ── Transport ──────────────────────────────────────────────────────────

    def call(self, method: str, params: dict = None):
        auth = (requests.auth.HTTPDigestAuth(self.user, self.password or "")
                if self.user else None)
        try:
            r = requests.post(f"{self.url}/json_rpc", auth=auth, timeout=self.timeout,
                              json={"jsonrpc": "2.0", "id": "monero-mod",
                                    "method": method, "params": params or {}})
        except requests.RequestException as e:
            raise WalletRPCError(
                f"monero-wallet-rpc is not reachable at {self.url} ({e}). "
                "Start one with:  monero-wallet-rpc --wallet-file <file> "
                "--rpc-bind-port 18083 --disable-rpc-login --daemon-address <node>  "
                "or set MONERO_WALLET_RPC_URL.")
        if r.status_code == 401:
            raise WalletRPCError(
                "monero-wallet-rpc rejected the credentials; set "
                "MONERO_WALLET_RPC_USER and MONERO_WALLET_RPC_PASSWORD to match "
                "its --rpc-login")
        if not r.ok:
            raise WalletRPCError(f"wallet rpc returned HTTP {r.status_code} for {method}")
        try:
            payload = r.json()
        except ValueError:
            raise WalletRPCError(f"wallet rpc returned non-JSON for {method}")
        if "error" in payload:
            err = payload["error"]
            raise WalletRPCError(f"{method}: {err.get('message', err)} "
                                 f"(code {err.get('code')})")
        return payload.get("result", {})

    @property
    def available(self) -> bool:
        try:
            self.call("get_version")
            return True
        except WalletRPCError:
            return False

    def status(self) -> dict:
        try:
            version = self.call("get_version").get("version")
        except WalletRPCError as e:
            # `reason`, not `error`: "no wallet RPC running" is this function's
            # answer, not a failure to answer. Callers that treat a truthy
            # `error` as a fault would otherwise turn a working status check
            # into an opaque one.
            return {"available": False, "url": self.url, "reason": str(e)}
        out = {"available": True, "url": self.url, "version": version}
        try:
            out["height"] = self.call("get_height").get("height")
            out["address"] = self.call("get_address", {"account_index": 0}).get("address")
        except WalletRPCError:
            pass
        return out

    # ── Reads ──────────────────────────────────────────────────────────────

    def balance(self, account: int = 0) -> dict:
        b = self.call("get_balance", {"account_index": int(account),
                                      "all_accounts": False})
        return {
            "balance": b.get("balance"), "balance_xmr": xmr(b.get("balance")),
            "unlocked": b.get("unlocked_balance"),
            "unlocked_xmr": xmr(b.get("unlocked_balance")),
            "blocks_to_unlock": b.get("blocks_to_unlock"),
            "multisig_import_needed": b.get("multisig_import_needed"),
            "per_subaddress": [
                {"address": s.get("address"), "label": s.get("label"),
                 "balance_xmr": xmr(s.get("balance")),
                 "unlocked_xmr": xmr(s.get("unlocked_balance")),
                 "outputs": s.get("num_unspent_outputs")}
                for s in (b.get("per_subaddress") or [])],
            "note": "unlocked is what you can spend now; the rest is waiting "
                    "out its 10-block lock (60 blocks for mined coins).",
        }

    def addresses(self, account: int = 0) -> dict:
        return self.call("get_address", {"account_index": int(account)})

    def accounts(self) -> dict:
        return self.call("get_accounts")

    def height(self) -> int:
        return int(self.call("get_height").get("height", 0))

    def refresh(self, start_height: int = None) -> dict:
        params = {} if start_height is None else {"start_height": int(start_height)}
        return self.call("refresh", params)

    def transfers(self, incoming: bool = True, outgoing: bool = True,
                  pending: bool = True, failed: bool = False,
                  pool: bool = True, account: int = 0) -> dict:
        result = self.call("get_transfers", {
            "in": bool(incoming), "out": bool(outgoing), "pending": bool(pending),
            "failed": bool(failed), "pool": bool(pool),
            "account_index": int(account)})
        rows = []
        for kind in ("in", "out", "pending", "pool", "failed"):
            for t in result.get(kind) or []:
                rows.append({
                    "direction": kind, "txid": t.get("txid"),
                    "amount": t.get("amount"), "amount_xmr": xmr(t.get("amount")),
                    "fee_xmr": xmr(t.get("fee")), "height": t.get("height"),
                    "timestamp": t.get("timestamp"),
                    "confirmations": t.get("confirmations"),
                    "address": t.get("address"), "payment_id": t.get("payment_id"),
                    "note": t.get("note"), "double_spend_seen": t.get("double_spend_seen"),
                })
        rows.sort(key=lambda r: (r.get("height") or 10 ** 12), reverse=True)
        return {"count": len(rows), "transfers": rows}

    def validate(self, address: str) -> dict:
        return self.call("validate_address", {"address": address,
                                              "any_net_type": True})

    # ── Spending ───────────────────────────────────────────────────────────

    def transfer(self, destinations: list, priority: int = 1, account: int = 0,
                 relay: bool = False, payment_id: str = None,
                 ring_size: int = 16, subtract_fee: bool = False) -> dict:
        """Build (and optionally relay) a transaction.

        With relay=False this still constructs and signs the real thing --
        the fee, weight and hash below are the ones the network would see --
        but returns tx_metadata instead of publishing. Pass that metadata to
        relay() to send it.
        """
        params = {
            "destinations": destinations,
            "account_index": int(account),
            "priority": int(priority),
            "ring_size": int(ring_size),
            "get_tx_key": True,
            "get_tx_hex": True,
            "get_tx_metadata": True,
            "do_not_relay": not relay,
        }
        if subtract_fee:
            params["subtract_fee_from_outputs"] = list(range(len(destinations)))
        if payment_id:
            params["payment_id"] = payment_id
        return self.call("transfer", params)

    def relay(self, tx_metadata: str) -> dict:
        return self.call("relay_tx", {"hex": tx_metadata})

    def sweep_all(self, address: str, priority: int = 1, account: int = 0,
                  relay: bool = False) -> dict:
        return self.call("sweep_all", {
            "address": address, "account_index": int(account),
            "priority": int(priority), "ring_size": 16,
            "get_tx_keys": True, "get_tx_hex": True, "get_tx_metadata": True,
            "do_not_relay": not relay})

    # ── Wallet files ───────────────────────────────────────────────────────

    def open_wallet(self, filename: str, password: str = "") -> dict:
        self.call("open_wallet", {"filename": filename, "password": password})
        return {"opened": filename}

    def close_wallet(self) -> dict:
        self.call("close_wallet")
        return {"closed": True}

    def create_wallet(self, filename: str, password: str = "",
                      language: str = "English") -> dict:
        self.call("create_wallet", {"filename": filename, "password": password,
                                    "language": language})
        return {"created": filename}

    def restore_wallet(self, filename: str, seed_phrase: str, password: str = "",
                       restore_height: int = 0) -> dict:
        """Hand a seed phrase from this module's wallet store to wallet-rpc."""
        self.call("restore_deterministic_wallet", {
            "filename": filename, "password": password, "seed": seed_phrase,
            "restore_height": int(restore_height), "language": "English"})
        return {"restored": filename, "restore_height": restore_height}

    def export_key_images(self, all_images: bool = True) -> dict:
        """Key images for the wallet's outputs.

        This is how a view-only wallet learns what has been spent: the hot
        wallet exports, the watcher imports. It is also the piece pure-Python
        scanning cannot produce on its own.
        """
        return self.call("export_key_images", {"all": bool(all_images)})

    def import_key_images(self, signed_key_images: list) -> dict:
        return self.call("import_key_images", {"signed_key_images": signed_key_images})
