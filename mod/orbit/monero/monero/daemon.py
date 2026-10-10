"""
Chain access for Monero: tip, blocks, transactions, fees, broadcast.

Three backends, tried in that order:

  * a node you configure with MONERO_DAEMON_URL -- authoritative, no rate
    limits, and the only one that never leaks what you are looking up;
  * a public remote node from PUBLIC_NODES, when none is configured. Public
    nodes are restricted (no mining or admin RPC) but serve blocks, fees and
    broadcast, which is all this module needs;
  * xmrchain.net's explorer API, for the read-only stats a node cannot give
    cheaply (price context, mempool summaries) and as a last resort when every
    node is unreachable.

Scanning (scan.py) needs raw transactions, so it insists on a node: the
explorer would have to be handed a view key to do the same work, and this
module will not do that.
"""

import os
import time

import requests

EXPLORER = "https://xmrchain.net/api"
COINGECKO = "https://api.coingecko.com/api/v3"

# Public nodes that answer plain HTTPS on the standard restricted port. First
# one to respond wins and is remembered for the process lifetime.
PUBLIC_NODES = [
    "https://xmr-node.cakewallet.com:18081",
    "https://node.monerodevs.org:18089",
    "https://nodes.hashvault.pro:18081",
    "https://xmr.stormycloud.org:18089",
]

ATOMIC = 10 ** 12          # piconero per XMR
COINBASE_UNLOCK = 60       # blocks a miner reward stays locked

_cache = {}
_CACHE_TTL = 30


def _cached(key, ttl, produce):
    hit = _cache.get(key)
    now = time.time()
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = produce()
    _cache[key] = (now, value)
    return value


class DaemonError(Exception):
    pass


def xmr(piconero) -> float:
    return (piconero or 0) / ATOMIC


def piconero(amount) -> int:
    from decimal import Decimal, ROUND_DOWN
    return int((Decimal(str(amount)) * ATOMIC).quantize(Decimal(1), rounding=ROUND_DOWN))


class Daemon:
    def __init__(self, url: str = None, timeout: int = 25):
        self.configured = url or os.environ.get("MONERO_DAEMON_URL") or None
        self.timeout = timeout
        self._live = self.configured
        self._user = os.environ.get("MONERO_DAEMON_USER")
        self._password = os.environ.get("MONERO_DAEMON_PASSWORD")

    # ── Node selection ─────────────────────────────────────────────────────

    @property
    def url(self) -> str:
        if self._live:
            return self._live
        for candidate in PUBLIC_NODES:
            try:
                r = requests.post(f"{candidate}/json_rpc", timeout=8,
                                  json={"jsonrpc": "2.0", "id": "0", "method": "get_block_count"})
                if r.ok and "result" in r.json():
                    self._live = candidate
                    return candidate
            except (requests.RequestException, ValueError):
                continue
        raise DaemonError(
            "no Monero node reachable. Set MONERO_DAEMON_URL to your own "
            "(monerod --rpc-bind-port 18081 --restricted-rpc), or check the "
            "network -- every public node in PUBLIC_NODES failed.")

    @property
    def is_own_node(self) -> bool:
        return bool(self.configured)

    def _auth(self):
        if self._user:
            return requests.auth.HTTPDigestAuth(self._user, self._password or "")
        return None

    # ── RPC ────────────────────────────────────────────────────────────────

    def rpc(self, method: str, params: dict = None):
        """A json_rpc call (get_info, get_block, get_fee_estimate, ...)."""
        body = {"jsonrpc": "2.0", "id": "monero-mod", "method": method,
                "params": params or {}}
        try:
            r = requests.post(f"{self.url}/json_rpc", json=body,
                              auth=self._auth(), timeout=self.timeout)
        except requests.RequestException as e:
            raise DaemonError(f"node {self.url} unreachable: {e}")
        if not r.ok:
            raise DaemonError(f"node returned HTTP {r.status_code} for {method}")
        try:
            payload = r.json()
        except ValueError:
            raise DaemonError(f"node returned non-JSON for {method}")
        if "error" in payload:
            err = payload["error"]
            raise DaemonError(f"{method}: {err.get('message', err)}")
        return payload.get("result", {})

    def raw(self, path: str, body: dict = None):
        """A legacy (non-json_rpc) endpoint: /get_transactions, /sendrawtransaction."""
        try:
            r = requests.post(f"{self.url}/{path.lstrip('/')}", json=body or {},
                              auth=self._auth(), timeout=self.timeout)
        except requests.RequestException as e:
            raise DaemonError(f"node {self.url} unreachable: {e}")
        if not r.ok:
            raise DaemonError(f"node returned HTTP {r.status_code} for {path}")
        try:
            return r.json()
        except ValueError:
            raise DaemonError(f"node returned non-JSON for {path}")

    # ── Explorer ───────────────────────────────────────────────────────────

    def explorer(self, path: str, params: dict = None) -> dict:
        try:
            r = requests.get(f"{EXPLORER}/{path.lstrip('/')}", params=params,
                             timeout=self.timeout)
        except requests.RequestException as e:
            raise DaemonError(f"explorer unreachable: {e}")
        if not r.ok:
            raise DaemonError(f"explorer returned HTTP {r.status_code} for {path}")
        try:
            payload = r.json()
        except ValueError:
            raise DaemonError(f"explorer returned non-JSON for {path}")
        if payload.get("status") != "success":
            detail = (payload.get("data") or {}).get("title") or payload.get("status")
            raise DaemonError(f"explorer: {detail}")
        return payload.get("data") or {}

    # ── Chain reads ────────────────────────────────────────────────────────

    def info(self) -> dict:
        """Node view of the chain. Falls back to the explorer if no node answers."""
        try:
            i = self.rpc("get_info")
            hf = i.get("hard_fork_version")
            if hf is None:
                # Restricted nodes blank this in get_info but still answer
                # hard_fork_info, which is where the real consensus version is.
                try:
                    hf = self.rpc("hard_fork_info").get("version")
                except DaemonError:
                    hf = None
            return {
                "height": i.get("height"), "target_height": i.get("target_height"),
                "difficulty": i.get("difficulty"), "hashrate": i.get("difficulty", 0) // 120,
                "top_block_hash": i.get("top_block_hash"),
                "tx_count": i.get("tx_count"), "tx_pool_size": i.get("tx_pool_size"),
                "block_size_limit": i.get("block_size_limit"),
                "network": "mainnet" if i.get("mainnet") else
                           "testnet" if i.get("testnet") else
                           "stagenet" if i.get("stagenet") else "unknown",
                "hard_fork_version": hf,
                "database_size": i.get("database_size"),
                "synchronized": i.get("synchronized"),
                "source": "node", "node": self.url,
            }
        except DaemonError:
            d = self.explorer("networkinfo")
            return {
                "height": d.get("height"), "difficulty": d.get("difficulty"),
                "hashrate": d.get("hash_rate"), "top_block_hash": d.get("top_block_hash"),
                "tx_count": d.get("tx_count"), "tx_pool_size": d.get("tx_pool_size"),
                "block_size_limit": d.get("block_size_limit"),
                "network": "mainnet" if d.get("nettype") == 0 else "other",
                "hard_fork_version": d.get("current_hf_version"),
                "fee_per_kb": d.get("fee_per_kb"),
                "source": "explorer", "node": None,
            }

    def tip_height(self) -> int:
        return _cached("tip", 15, lambda: int(self.info()["height"]))

    def block(self, height: int = None, hash: str = None) -> dict:
        params = {}
        if hash:
            params["hash"] = hash
        elif height is not None:
            params["height"] = int(height)
        else:
            params["height"] = self.tip_height() - 1
        try:
            b = self.rpc("get_block", params)
            header = b.get("block_header", {})
            import json as _json
            body = _json.loads(b["json"]) if b.get("json") else {}
            return {
                "height": header.get("height"), "hash": header.get("hash"),
                "timestamp": header.get("timestamp"),
                "size": header.get("block_size"), "weight": header.get("block_weight"),
                "difficulty": header.get("difficulty"),
                "reward": header.get("reward"), "reward_xmr": xmr(header.get("reward")),
                "miner_tx_hash": header.get("miner_tx_hash"),
                "num_txes": header.get("num_txes"),
                "tx_hashes": body.get("tx_hashes", []),
                "major_version": header.get("major_version"),
                "prev_hash": header.get("prev_hash"),
                "source": "node",
            }
        except DaemonError:
            key = hash or (height if height is not None else self.tip_height() - 1)
            d = self.explorer(f"block/{key}")
            return {
                "height": d.get("block_height"), "hash": d.get("hash"),
                "timestamp": d.get("timestamp"), "size": d.get("size"),
                "difficulty": d.get("difficulty"),
                "reward": d.get("block_reward"), "reward_xmr": xmr(d.get("block_reward")),
                "num_txes": len(d.get("txs") or []) - 1,
                "tx_hashes": [t.get("tx_hash") for t in (d.get("txs") or [])],
                "source": "explorer",
            }

    def transactions(self, hashes: list, decode: bool = True) -> list:
        """Raw transactions by hash. Node-only: this is what scanning reads."""
        if not hashes:
            return []
        payload = self.raw("get_transactions",
                           {"txs_hashes": list(hashes), "decode_as_json": bool(decode)})
        if payload.get("status") not in ("OK", None):
            raise DaemonError(f"get_transactions: {payload.get('status')}")
        return payload.get("txs") or []

    def transaction(self, txid: str) -> dict:
        """One transaction, summarised for humans."""
        try:
            txs = self.transactions([txid])
            if txs:
                import json as _json
                t = txs[0]
                body = _json.loads(t["as_json"]) if t.get("as_json") else {}
                return {
                    "hash": t.get("tx_hash"), "block_height": t.get("block_height"),
                    "timestamp": t.get("block_timestamp"),
                    "in_pool": t.get("in_pool"), "confirmations": t.get("confirmations"),
                    "version": body.get("version"),
                    "unlock_time": body.get("unlock_time"),
                    "inputs": len(body.get("vin") or []),
                    "outputs": len(body.get("vout") or []),
                    "ring_size": _ring_size(body),
                    "fee": (body.get("rct_signatures") or {}).get("txnFee"),
                    "fee_xmr": xmr((body.get("rct_signatures") or {}).get("txnFee")),
                    "rct_type": (body.get("rct_signatures") or {}).get("type"),
                    "extra_bytes": len(body.get("extra") or []),
                    "source": "node",
                }
        except DaemonError:
            pass
        d = self.explorer(f"transaction/{txid}")
        return {
            "hash": d.get("tx_hash"), "block_height": d.get("block_height"),
            "timestamp": d.get("timestamp"), "in_pool": d.get("mempool"),
            "confirmations": d.get("confirmations"),
            "inputs": len(d.get("inputs") or []), "outputs": len(d.get("outputs") or []),
            "ring_size": d.get("mixin"), "fee": d.get("tx_fee"),
            "fee_xmr": xmr(d.get("tx_fee")), "size": d.get("tx_size"),
            "payment_id": d.get("payment_id") or None,
            "source": "explorer",
        }

    def mempool(self, limit: int = 25) -> dict:
        d = self.explorer("mempool")
        txs = (d.get("txs") or [])[:limit]
        return {
            "count": len(d.get("txs") or []),
            "transactions": [{"hash": t.get("tx_hash"), "fee": t.get("tx_fee"),
                              "fee_xmr": xmr(t.get("tx_fee")), "size": t.get("tx_size"),
                              "ring_size": t.get("mixin")} for t in txs],
        }

    def fee_estimate(self, priority: int = 1) -> dict:
        """Per-byte fee tiers as the network currently sees them."""
        f = self.rpc("get_fee_estimate")
        tiers = f.get("fees") or [f.get("fee")]
        idx = max(0, min(int(priority), len(tiers) - 1))
        return {"fee_per_byte": tiers[idx], "tiers": tiers,
                "priority": idx, "quantization_mask": f.get("quantization_mask"),
                "note": "Monero fees are per byte; a typical 2-in/2-out transaction "
                        "is about 1.5 kB."}

    def broadcast(self, tx_hex: str) -> dict:
        payload = self.raw("sendrawtransaction", {"tx_as_hex": tx_hex, "do_not_relay": False})
        if payload.get("status") != "OK":
            reasons = [k for k in ("double_spend", "invalid_input", "invalid_output",
                                   "low_mixin", "overspend", "fee_too_low", "too_big",
                                   "not_relayed", "sanity_check_failed")
                       if payload.get(k)]
            raise DaemonError(
                f"node rejected the transaction: {payload.get('reason') or payload.get('status')}"
                + (f" ({', '.join(reasons)})" if reasons else ""))
        return {"relayed": True, "node": self.url, "status": payload.get("status")}

    def outputs(self, indexes: list) -> list:
        """Global-index outputs, used to show what a ring is made of."""
        payload = self.raw("get_outs", {
            "outputs": [{"amount": 0, "index": int(i)} for i in indexes],
            "get_txid": True})
        return payload.get("outs") or []

    # ── Market ─────────────────────────────────────────────────────────────

    def price(self) -> dict:
        def fetch():
            try:
                r = requests.get(f"{COINGECKO}/simple/price", timeout=self.timeout,
                                 params={"ids": "monero", "vs_currencies": "usd",
                                         "include_market_cap": "true",
                                         "include_24hr_change": "true",
                                         "include_24hr_vol": "true"})
                r.raise_for_status()
                d = r.json().get("monero") or {}
            except (requests.RequestException, ValueError) as e:
                raise DaemonError(f"price unavailable: {e}")
            return {"price_usd": d.get("usd"), "market_cap_usd": d.get("usd_market_cap"),
                    "volume_24h_usd": d.get("usd_24h_vol"),
                    "change_24h_pct": d.get("usd_24h_change"), "source": "coingecko"}
        return _cached("price", 60, fetch)

    def supply(self) -> dict:
        """Circulating supply.

        Not read from the chain: summing emission needs get_coinbase_tx_sum,
        which every restricted node disables, and the explorer's /emission
        counts only the blocks it has indexed. Rather than publish a number
        that is quietly wrong by 18 million XMR, this says where it came from.
        """
        def fetch():
            try:
                r = requests.get(f"{COINGECKO}/coins/monero", timeout=self.timeout,
                                 params={"localization": "false", "tickers": "false",
                                         "community_data": "false",
                                         "developer_data": "false"})
                r.raise_for_status()
                md = r.json().get("market_data") or {}
            except (requests.RequestException, ValueError) as e:
                raise DaemonError(f"supply unavailable: {e}")
            return {"circulating_xmr": md.get("circulating_supply"),
                    "total_xmr": md.get("total_supply"),
                    "tail_emission_xmr_per_block": 0.6,
                    "source": "coingecko",
                    "note": "Monero has no supply cap: tail emission adds 0.6 XMR "
                            "per block (~157,680 XMR/year) forever."}
        return _cached("supply", 300, fetch)

    def node_info(self) -> dict:
        try:
            url = self.url
        except DaemonError as e:
            return {"configured": self.is_own_node, "reachable": False, "error": str(e)}
        return {"configured": self.is_own_node, "reachable": True, "url": url,
                "own_node": self.is_own_node,
                "note": None if self.is_own_node else
                        "Using a public node. It can see which blocks you ask for; "
                        "set MONERO_DAEMON_URL to your own monerod for privacy."}


def _ring_size(body: dict) -> int:
    for vin in body.get("vin") or []:
        key = vin.get("key") or {}
        offsets = key.get("key_offsets") or []
        if offsets:
            return len(offsets)
    return 0
