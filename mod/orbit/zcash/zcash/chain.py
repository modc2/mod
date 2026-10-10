"""
Chain access for Zcash: UTXOs, tip height, consensus branch id, broadcast.

Two backends:

  * Blockchair (default, no key needed) -- read UTXOs and push raw transactions.
  * A local/remote zcashd or zebrad JSON-RPC node, if ZCASH_RPC_URL is set.
    A node is authoritative, avoids rate limits, and is the only way to reach
    the shielded pools.

The consensus branch id is *discovered from the chain* rather than hardcoded:
a network upgrade changes it, and a stale constant would make every signature
invalid. We read it out of the header of a recent v5 transaction and fall back
to the last known value only if discovery fails.
"""

import json
import os
import time

import requests

BLOCKCHAIR = "https://api.blockchair.com/zcash"

# Last observed value; only used if live discovery fails.
FALLBACK_BRANCH_ID = 0x37A5165B

_cache = {}
_CACHE_TTL = 60


def _cached(key, ttl, produce):
    hit = _cache.get(key)
    now = time.time()
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = produce()
    _cache[key] = (now, value)
    return value


class ChainError(Exception):
    pass


class Chain:
    def __init__(self, rpc_url: str = None, rpc_user: str = None, rpc_password: str = None,
                 api_key: str = None, timeout: int = 25):
        self.rpc_url = rpc_url or os.environ.get("ZCASH_RPC_URL") or None
        self.rpc_user = rpc_user or os.environ.get("ZCASH_RPC_USER")
        self.rpc_password = rpc_password or os.environ.get("ZCASH_RPC_PASSWORD")
        self.api_key = api_key or os.environ.get("BLOCKCHAIR_API_KEY")
        self.timeout = timeout

    # ── Blockchair ─────────────────────────────────────────────────────────

    def _bc(self, path: str, params: dict = None) -> dict:
        params = dict(params or {})
        if self.api_key:
            params["key"] = self.api_key
        try:
            r = requests.get(f"{BLOCKCHAIR}{path}", params=params, timeout=self.timeout)
        except requests.RequestException as e:
            raise ChainError(f"blockchair unreachable: {e}")
        if r.status_code == 402 or r.status_code == 429:
            raise ChainError("blockchair rate limit reached; set BLOCKCHAIR_API_KEY "
                             "or configure ZCASH_RPC_URL for a private node")
        if not r.ok:
            raise ChainError(f"blockchair {path} returned HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError:
            raise ChainError(f"blockchair {path} returned non-JSON")

    # ── Node RPC ───────────────────────────────────────────────────────────

    @property
    def has_node(self) -> bool:
        return bool(self.rpc_url)

    def rpc(self, method: str, *params):
        if not self.rpc_url:
            raise ChainError("no Zcash node configured (set ZCASH_RPC_URL)")
        auth = (self.rpc_user, self.rpc_password) if self.rpc_user else None
        try:
            r = requests.post(
                self.rpc_url, auth=auth, timeout=self.timeout,
                json={"jsonrpc": "1.0", "id": "zcash-mod", "method": method, "params": list(params)})
        except requests.RequestException as e:
            raise ChainError(f"node unreachable: {e}")
        try:
            body = r.json()
        except ValueError:
            raise ChainError(f"node returned non-JSON (HTTP {r.status_code})")
        if body.get("error"):
            raise ChainError(f"node error: {body['error']}")
        return body.get("result")

    def node_info(self) -> dict:
        """Probe the configured node; never raises."""
        if not self.rpc_url:
            return {"configured": False}
        try:
            info = self.rpc("getblockchaininfo")
            return {
                "configured": True, "reachable": True,
                "chain": info.get("chain"),
                "blocks": info.get("blocks"),
                "upgrade": _active_upgrade(info),
            }
        except ChainError as e:
            return {"configured": True, "reachable": False, "error": str(e)}

    # ── Chain state ────────────────────────────────────────────────────────

    def tip_height(self) -> int:
        if self.has_node:
            try:
                return int(self.rpc("getblockcount"))
            except ChainError:
                pass
        data = _cached("tip", _CACHE_TTL, lambda: self._bc("/stats"))
        height = data.get("data", {}).get("best_block_height")
        if height is None:
            raise ChainError("could not determine chain tip")
        return int(height)

    def consensus_branch_id(self) -> int:
        """Current consensus branch id, discovered live."""
        return _cached("branch", 900, self._discover_branch_id)

    def _discover_branch_id(self) -> int:
        # A node states it directly.
        if self.has_node:
            try:
                info = self.rpc("getblockchaininfo")
                upgrade = _active_upgrade(info)
                if upgrade and upgrade.get("branch_id"):
                    return int(upgrade["branch_id"], 16)
                if info.get("consensus", {}).get("nextblock"):
                    return int(info["consensus"]["nextblock"], 16)
            except (ChainError, ValueError, KeyError):
                pass
        # Otherwise read it out of a recent v5 transaction header.
        try:
            recent = self._bc("/transactions", {"limit": 30})
            for row in recent.get("data", []):
                h = row.get("hash")
                if not h:
                    continue
                raw = self.raw_transaction(h)
                if raw[:8] == "05000080":
                    return int.from_bytes(bytes.fromhex(raw[16:24]), "little")
        except (ChainError, ValueError):
            pass
        return FALLBACK_BRANCH_ID

    def raw_transaction(self, txid: str) -> str:
        if self.has_node:
            try:
                return self.rpc("getrawtransaction", txid)
            except ChainError:
                pass
        data = self._bc(f"/raw/transaction/{txid}")
        entry = (data.get("data") or {}).get(txid)
        if not entry:
            raise ChainError(f"transaction {txid} not found")
        return entry["raw_transaction"]

    # ── Shielded ───────────────────────────────────────────────────────────

    def shielded_transactions(self, from_height: int, to_height: int,
                              max_requests: int = 40) -> list:
        """Transactions in a height range that carry Sapling bundles.

        Blockchair serializes each Sapling spend and output (including the
        encrypted ciphertexts) into the transaction row, so a shielded scan
        costs one request per 100 transactions rather than one per
        transaction. Rows without a Sapling bundle are dropped here.
        """
        return [row for row in self.transactions_in_range(
                    from_height, to_height, max_requests)
                if row.get("shielded_output_raw") or row.get("shielded_input_raw")]

    def transactions_in_range(self, from_height: int, to_height: int,
                              max_requests: int = 40) -> list:
        """Every transaction row in a height range, in chain order.

        The Orchard scanner needs this rather than `shielded_transactions`:
        the explorer flags a row as shielded only when it carries a *Sapling*
        bundle, so an Orchard-only payment looks like an ordinary transaction
        here and would be filtered away before anyone tried to read it.
        """
        out, offset = [], 0
        for _ in range(max_requests):
            data = self._bc("/transactions", {
                "q": f"block_id({from_height}..{to_height})",
                "limit": 100, "offset": offset, "s": "id(asc)"})
            rows = data.get("data") or []
            out.extend(rows)
            total = (data.get("context") or {}).get("total_rows") or 0
            offset += len(rows)
            if not rows or offset >= total:
                return out
        raise ChainError(
            f"height range {from_height}..{to_height} holds more transactions "
            f"than one scan should fetch from the public explorer; narrow it "
            f"or configure ZCASH_RPC_URL")

    def transaction_row(self, txid: str) -> dict:
        """One transaction as the explorer sees it, Sapling bundle included."""
        data = self._bc(f"/dashboards/transaction/{txid}")
        entry = (data.get("data") or {}).get(txid)
        if not entry:
            raise ChainError(f"transaction {txid} not found")
        return entry.get("transaction") or {}

    def block_raw_transactions(self, height: int) -> list:
        """[(txid, raw hex)] for a block. Node only -- see shielded.py."""
        block = self.rpc("getblock", str(height), 1)
        return [(txid, self.rpc("getrawtransaction", txid))
                for txid in (block.get("tx") or [])]

    # ── UTXOs ──────────────────────────────────────────────────────────────

    def utxos(self, address: str) -> list:
        """Confirmed spendable outputs for a transparent address."""
        if self.has_node:
            try:
                rows = self.rpc("getaddressutxos", {"addresses": [address]})
                return [{"txid": u["txid"], "vout": int(u["outputIndex"]),
                         "value": int(u["satoshis"]), "script_pubkey": u["script"],
                         "height": u.get("height")} for u in rows]
            except ChainError:
                pass
        # Blockchair's `limit` is a "transactions,utxos" pair -- a bare 0 would
        # silently return an empty utxo array and look like a zero balance.
        data = self._bc(f"/dashboards/address/{address}", {"limit": "0,1000"})
        entry = (data.get("data") or {}).get(address)
        if entry is None:
            raise ChainError(f"address {address} not found")
        script_hex = (entry.get("address") or {}).get("script_hex")
        if not script_hex:
            # No script means the address has never been seen on chain.
            try:
                from . import keys as _k
            except ImportError:
                import keys as _k
            script_hex = _k.address_to_script(address).hex()
        return [{"txid": u["transaction_hash"], "vout": int(u["index"]),
                 "value": int(u["value"]), "script_pubkey": script_hex,
                 "height": u.get("block_id")} for u in entry.get("utxo", [])]

    def balance(self, address: str) -> dict:
        data = self._bc(f"/dashboards/address/{address}", {"limit": "0,0"})
        entry = (data.get("data") or {}).get(address)
        if entry is None:
            raise ChainError(f"address {address} not found")
        a = entry.get("address") or {}
        return {
            "address": address,
            "balance_zatoshi": int(a.get("balance") or 0),
            "balance_zec": (a.get("balance") or 0) / 1e8,
            "balance_usd": a.get("balance_usd"),
            "received_zatoshi": int(a.get("received") or 0),
            "spent_zatoshi": int(a.get("spent") or 0),
            "transaction_count": a.get("transaction_count") or 0,
            "utxo_count": a.get("unspent_output_count") or 0,
            "first_seen": a.get("first_seen_receiving"),
            "last_seen": a.get("last_seen_receiving"),
        }

    # ── Broadcast ──────────────────────────────────────────────────────────

    def broadcast(self, raw_hex: str) -> dict:
        """Submit a signed transaction. Tries the node first, then Blockchair."""
        errors = []
        if self.has_node:
            try:
                txid = self.rpc("sendrawtransaction", raw_hex)
                return {"txid": txid, "via": "node"}
            except ChainError as e:
                errors.append(f"node: {e}")
        try:
            r = requests.post(f"{BLOCKCHAIR}/push/transaction",
                              data={"data": raw_hex}, timeout=self.timeout)
            body = r.json()
        except (requests.RequestException, ValueError) as e:
            errors.append(f"blockchair: {e}")
            raise ChainError("broadcast failed: " + "; ".join(errors))
        ctx = body.get("context") or {}
        if ctx.get("error"):
            errors.append(f"blockchair: {ctx['error']}")
            raise ChainError("broadcast rejected: " + "; ".join(errors))
        data = body.get("data") or {}
        txid = data.get("transaction_hash") or data.get("hash")
        if not txid:
            errors.append(f"blockchair: unexpected response {json.dumps(body)[:200]}")
            raise ChainError("broadcast failed: " + "; ".join(errors))
        return {"txid": txid, "via": "blockchair"}


def _active_upgrade(info: dict) -> dict:
    """Pick the activated network upgrade with the greatest activation height."""
    best = None
    for branch_id, u in (info.get("upgrades") or {}).items():
        if u.get("status") != "active":
            continue
        if best is None or u.get("activationheight", 0) > best.get("activationheight", 0):
            best = dict(u, branch_id=branch_id)
    return best or {}
