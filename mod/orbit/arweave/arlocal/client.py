"""
ArweaveClient — store and retrieve data on Arweave.

Backends (auto-selected based on env / config):
  - Arweave gateway (https://arweave.net) for retrieval and tx info — always works.
  - `arweave-python-client` + wallet JWK for uploads.
  - Local SQLite index that tracks tx ids and metadata for easy listing.

Reads never need credentials. Uploads need a wallet JWK, read from
$ARWEAVE_WALLET or ~/.mod/arweave/wallet.json (off-tree, never committed).
Without one `put` falls back to the LOCAL backend: the bytes are kept at
~/.arweave-mod/blobs/<sha256-id> and indexed, so put/get/list/rm round-trip
on this box (the response carries a warning that nothing reached Arweave).
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import requests


DEFAULT_GATEWAY = "https://arweave.net"
GATEWAY_FALLBACKS = [
    "https://arweave.net",
    "https://arweave.dev",
    "https://g8way.io",
]

# Wallet JWK lives off-tree with the other per-module secrets, never in config.
WALLET_PATH = Path(os.path.expanduser("~/.mod/arweave/wallet.json"))

_rate_cache: Dict[str, Any] = {"ts": 0.0, "rate": None}
_RATE_TTL = 300.0


def ar_usd_rate() -> Optional[float]:
    """Return the current AR/USD rate from CoinGecko, cached for 5 minutes. Returns None on failure."""
    now = time.time()
    if _rate_cache["rate"] is not None and now - _rate_cache["ts"] < _RATE_TTL:
        return _rate_cache["rate"]
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price?ids=arweave&vs_currencies=usd",
            timeout=5,
        )
        r.raise_for_status()
        rate = float(r.json()["arweave"]["usd"])
        _rate_cache["ts"] = now
        _rate_cache["rate"] = rate
        return rate
    except Exception:
        return None


class ArweaveClient:
    """Pragmatic Arweave client. Works without auth for reads; uploads need a wallet."""

    def __init__(
        self,
        gateway: Optional[str] = None,
        wallet_path: Optional[str] = None,
        store_path: Optional[str] = None,
    ):
        self.gateway = (gateway or os.environ.get("ARWEAVE_GATEWAY", DEFAULT_GATEWAY)).rstrip("/")
        # Precedence: explicit arg > env > ~/.mod/arweave/wallet.json.
        self.wallet_path = wallet_path or os.environ.get("ARWEAVE_WALLET") or (
            str(WALLET_PATH) if WALLET_PATH.is_file() else None
        )

        self.store_dir = Path(store_path or os.path.expanduser("~/.arweave-mod"))
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.store_dir / "index.db"
        self.blob_dir = self.store_dir / "blobs"
        self.blob_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ─── local index ────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS objects (
                    txid TEXT PRIMARY KEY,
                    name TEXT,
                    size INTEGER,
                    backend TEXT,
                    created INTEGER NOT NULL,
                    metadata TEXT
                )"""
            )
            conn.commit()

    def _index(
        self,
        txid: str,
        name: Optional[str],
        size: Optional[int],
        backend: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO objects (txid, name, size, backend, created, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (txid, name, size, backend, int(time.time()), json.dumps(metadata or {})),
            )
            conn.commit()

    def list(self, limit: int = 100) -> List[Dict[str, Any]]:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT txid, name, size, backend, created, metadata FROM objects "
                "ORDER BY created DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        out = []
        for r in rows:
            row = dict(r)
            try:
                row["metadata"] = json.loads(row["metadata"] or "{}")
            except Exception:
                row["metadata"] = {}
            row["url"] = f"{self.gateway}/{row['txid']}" if row.get("backend") == "arweave" else None
            out.append(row)
        return out

    def rm(self, txid: str) -> Dict[str, Any]:
        """Drop a tx id from the local index (and its local blob, if any).
        Content already on Arweave is permanent and is NOT touched."""
        with sqlite3.connect(str(self.db_path)) as conn:
            cur = conn.execute("DELETE FROM objects WHERE txid = ?", (txid,))
            conn.commit()
        blob = self._blob_path(txid)
        if blob is not None and blob.is_file():
            blob.unlink()
        return {"txid": txid, "removed": cur.rowcount > 0}

    def push_local(self, txid: str) -> Dict[str, Any]:
        """Push a locally-stored item to Arweave once a wallet is configured.

        Reads the blob, recovers content_type and tags from the local index,
        uploads via _upload_bytes(), removes the old local entry on success,
        and returns the new arweave-backend result.
        """
        if not txid.startswith("sha256-"):
            raise ValueError(f"{txid!r} is not a local-backend id (must start with sha256-)")
        if not self.wallet_path or not Path(self.wallet_path).expanduser().is_file():
            raise ValueError("no Arweave wallet configured — configure one before pushing")
        blob = self._blob_path(txid)
        if blob is None or not blob.is_file():
            raise FileNotFoundError(f"no local blob found for {txid}")
        with sqlite3.connect(str(self.db_path)) as conn:
            row = conn.execute(
                "SELECT name, metadata FROM objects WHERE txid = ? AND backend = 'local'",
                (txid,),
            ).fetchone()
        if row is None:
            raise KeyError(f"{txid} not found in local index")
        name = row[0] or "upload.bin"
        try:
            meta = json.loads(row[1] or "{}") or {}
        except Exception:
            meta = {}
        content_type = meta.get("content_type") or "application/octet-stream"
        raw_tags = meta.get("tags") or []
        extra_tags = [tuple(t) for t in raw_tags] if raw_tags else None
        payload = blob.read_bytes()
        result = self._upload_bytes(payload, name, content_type, extra_tags=extra_tags)
        if result.get("backend") == "arweave":
            self.rm(txid)
        return result

    def _blob_path(self, txid: str) -> Optional[Path]:
        # Only local placeholder ids (sha256-<43 hex>) have blobs; the id is
        # validated so a crafted txid can never escape blob_dir.
        if not txid.startswith("sha256-"):
            return None
        hexpart = txid[len("sha256-"):]
        if len(hexpart) != 43 or any(c not in "0123456789abcdef" for c in hexpart):
            return None
        return self.blob_dir / hexpart

    # ─── put / store ────────────────────────────────────────────────────────

    def put(
        self,
        data: Union[bytes, str, dict, list],
        name: Optional[str] = None,
        content_type: Optional[str] = None,
        extra_tags: Optional[List[tuple]] = None,
    ) -> Dict[str, Any]:
        """Store arbitrary data on Arweave. Returns {txid, size, backend}."""
        if isinstance(data, (dict, list)):
            payload = json.dumps(data).encode()
            name = name or "data.json"
            content_type = content_type or "application/json"
        elif isinstance(data, str):
            payload = data.encode()
            name = name or "data.txt"
            content_type = content_type or "text/plain"
        elif isinstance(data, bytes):
            payload = data
            name = name or "data.bin"
            content_type = content_type or "application/octet-stream"
        else:
            raise TypeError(f"unsupported data type: {type(data).__name__}")

        return self._upload_bytes(payload, name, content_type, extra_tags=extra_tags)

    def put_file(self, path: str) -> Dict[str, Any]:
        """Store a file on Arweave. Returns {txid, size, backend}."""
        p = Path(path).expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(str(p))
        with open(p, "rb") as f:
            payload = f.read()
        return self._upload_bytes(payload, p.name, "application/octet-stream")

    def _upload_bytes(
        self, payload: bytes, name: str, content_type: str = "application/octet-stream",
        extra_tags: Optional[List[tuple]] = None,
    ) -> Dict[str, Any]:
        _extra = [list(t) for t in (extra_tags or [])]
        if not self.wallet_path or not Path(self.wallet_path).expanduser().is_file():
            digest = hashlib.sha256(payload).hexdigest()
            txid = f"sha256-{digest[:43]}"
            blob = self._blob_path(txid)
            if blob is not None:
                blob.write_bytes(payload)
            self._index(txid, name, len(payload), "local",
                        {"pinned": False, "content_type": content_type, "tags": _extra})
            return {
                "txid": txid,
                "size": len(payload),
                "backend": "local",
                "name": name,
                "pinned": False,
                "warning": "no Arweave wallet configured — content kept locally only "
                           "(set ARWEAVE_WALLET or ~/.mod/arweave/wallet.json to upload).",
            }

        tags = [("Content-Type", content_type)]
        if name:
            tags.append(("File-Name", name))
        if extra_tags:
            tags.extend(extra_tags)

        # Free path first: sign an ANS-104 data item and hand it to a bundler
        # (Turbo/Irys subsidize items under ~100 KiB — no AR balance needed).
        bundler_err: Optional[str] = None
        try:
            from . import bundle
            res = bundle.upload(payload, self.wallet_path, tags=tags)
            txid = res["txid"]
            self._index(txid, name, len(payload), "arweave",
                        {"pinned": True, "via": res["bundler"], "content_type": content_type,
                         "tags": _extra})
            return {"txid": txid, "size": len(payload), "backend": "arweave",
                    "via": res["bundler"], "name": name, "url": f"{self.gateway}/{txid}"}
        except Exception as e:
            bundler_err = str(e)

        # Funded fallback: a direct L1 transaction (needs AR in the wallet).
        try:
            import arweave  # type: ignore
            wallet = arweave.Wallet(str(Path(self.wallet_path).expanduser()))
            tx = arweave.Transaction(wallet, data=payload)
            tx.add_tag("Content-Type", content_type)
            if name:
                tx.add_tag("File-Name", name)
            for tag_name, tag_value in (extra_tags or []):
                tx.add_tag(tag_name, tag_value)
            tx.sign()
            tx.send()
        except Exception as e:
            # Nothing reached Arweave — keep the bytes locally so they survive.
            digest = hashlib.sha256(payload).hexdigest()
            txid = f"sha256-{digest[:43]}"
            blob = self._blob_path(txid)
            if blob is not None:
                blob.write_bytes(payload)
            self._index(txid, name, len(payload), "local",
                        {"pinned": False, "content_type": content_type, "tags": _extra})
            return {
                "txid": txid, "size": len(payload), "backend": "local",
                "name": name, "pinned": False,
                "warning": f"arweave upload failed, kept locally — bundler: {bundler_err}; direct tx: {e}",
            }

        txid = tx.id
        self._index(txid, name, len(payload), "arweave", {"pinned": True, "tags": _extra})
        return {"txid": txid, "size": len(payload), "backend": "arweave", "name": name,
                "url": f"{self.gateway}/{txid}"}

    def _get_metadata(self, txid: str) -> Dict[str, Any]:
        """Return the full metadata dict stored in the local index for txid, or {}."""
        with sqlite3.connect(str(self.db_path)) as conn:
            row = conn.execute(
                "SELECT metadata FROM objects WHERE txid = ?", (txid,)
            ).fetchone()
        if row is None:
            return {}
        try:
            return json.loads(row[0] or "{}") or {}
        except Exception:
            return {}

    def _get_stored_content_type(self, txid: str) -> Optional[str]:
        """Return the content_type stored in the local index for txid, or None."""
        with sqlite3.connect(str(self.db_path)) as conn:
            row = conn.execute(
                "SELECT metadata FROM objects WHERE txid = ?", (txid,)
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row[0] or "{}").get("content_type") or None
        except Exception:
            return None

    # ─── get / retrieve ─────────────────────────────────────────────────────

    def get(self, txid: str) -> Union[bytes, dict, list, str]:
        """Retrieve content by txid. Returns parsed JSON when possible, else bytes/str."""
        raw = self._fetch(txid)
        try:
            text = raw.decode()
        except UnicodeDecodeError:
            return raw
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def get_file(self, txid: str, dest: str) -> Dict[str, Any]:
        """Retrieve content by txid and write it to `dest`."""
        raw = self._fetch(txid)
        p = Path(dest).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "wb") as f:
            f.write(raw)
        return {"txid": txid, "path": str(p), "size": len(raw)}

    def _fetch_with_ct(self, txid: str) -> Tuple[bytes, Optional[str]]:
        """Fetch content and return (bytes, content_type_or_None)."""
        if txid.startswith("sha256-"):
            blob = self._blob_path(txid)
            if blob is not None and blob.is_file():
                return blob.read_bytes(), None
            raise ValueError(
                f"{txid} is a local-only id with no blob on this box; it was "
                "stored before local blobs existed or has been removed."
            )
        gateways = [self.gateway] + [g for g in GATEWAY_FALLBACKS if g != self.gateway]
        last_err: Optional[Exception] = None
        for gw in gateways:
            url = f"{gw.rstrip('/')}/{txid}"
            try:
                r = requests.get(url, timeout=60, allow_redirects=True)
                if r.ok and r.content:
                    ct = r.headers.get("content-type") or None
                    return r.content, ct
                last_err = RuntimeError(f"{url} → {r.status_code}")
            except requests.RequestException as e:
                last_err = e
        raise RuntimeError(f"all gateways failed for {txid}: {last_err}")

    def _fetch(self, txid: str) -> bytes:
        content, _ = self._fetch_with_ct(txid)
        return content

    # ─── tx / chain ─────────────────────────────────────────────────────────

    def tx(self, txid: str) -> Dict[str, Any]:
        """Return the Arweave tx record for a txid."""
        url = f"{self.gateway}/tx/{txid}"
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            return {"error": str(e), "txid": txid}

    def tx_confirm_status(self, txid: str) -> Dict[str, Any]:
        """Confirmation status for a txid.

        Returns one of:
          {status: "local"}                                        — sha256-… local id, never on-chain
          {status: "bundler_accepted", bundler: "turbo"}           — accepted by Turbo, not yet on chain
          {status: "pending"}                                      — broadcast, not yet mined (202)
          {status: "not_found"}                                    — unknown tx id (404)
          {status: "confirmed", confirmations: N, block_height: H} — mined and confirmed
        """
        if txid.startswith("sha256-"):
            return {"status": "local"}
        # For Turbo-uploaded items, check the bundler's own status before hitting
        # the chain gateway — the chain won't know the txid until the bundle is settled.
        meta = self._get_metadata(txid)
        if meta.get("via") == "turbo":
            try:
                turbo_url = f"https://upload.ardrive.io/v1/tx/{txid}/status"
                tr = requests.get(turbo_url, timeout=15)
                if tr.ok:
                    td = tr.json()
                    if isinstance(td, dict) and td.get("status") == "FINALIZED":
                        return {"status": "bundler_accepted", "bundler": "turbo"}
            except requests.RequestException:
                pass
        url = f"{self.gateway}/tx/{txid}/status"
        try:
            r = requests.get(url, timeout=15)
            if r.status_code == 202:
                return {"status": "pending"}
            if r.status_code == 404:
                return {"status": "not_found"}
            r.raise_for_status()
            data = r.json()
            return {
                "status": "confirmed",
                "confirmations": data.get("number_of_confirmations"),
                "block_height": data.get("block_height"),
            }
        except requests.RequestException as e:
            return {"status": "error", "error": str(e)}

    def status(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "gateway": self.gateway,
            "wallet_configured": bool(self.wallet_path and Path(self.wallet_path).expanduser().is_file()),
            "store_dir": str(self.store_dir),
        }
        if out["wallet_configured"]:
            try:
                from . import bundle
                out["wallet_address"] = bundle.address(self.wallet_path)
                out["uploader"] = f"bundler (turbo/irys, free <{bundle.FREE_LIMIT // 1024} KiB)"
            except Exception as e:
                out["wallet_error"] = str(e)
        try:
            r = requests.get(f"{self.gateway}/info", timeout=10)
            r.raise_for_status()
            info = r.json()
            out["arweave"] = {
                "ok": True,
                "network": info.get("network"),
                "version": info.get("version"),
                "height": info.get("height"),
                "blocks": info.get("blocks"),
            }
        except requests.RequestException as e:
            out["arweave"] = {"ok": False, "error": str(e)}
        with sqlite3.connect(str(self.db_path)) as conn:
            (count,) = conn.execute("SELECT COUNT(*) FROM objects").fetchone()
        out["indexed"] = count
        return out

    def info(self) -> Dict[str, Any]:
        """Network info from the gateway."""
        try:
            r = requests.get(f"{self.gateway}/info", timeout=10)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            return {"error": str(e)}

    def price(self, num_bytes: int) -> Dict[str, Any]:
        """Cost in winston to store `num_bytes` on Arweave."""
        try:
            r = requests.get(f"{self.gateway}/price/{int(num_bytes)}", timeout=10)
            r.raise_for_status()
            winston_str = r.text.strip()
            return {"bytes": int(num_bytes), "winston": winston_str, "ar": int(winston_str) / 1e12}
        except requests.RequestException as e:
            return {"error": str(e), "bytes": num_bytes}

    def balance(self, address: str) -> Dict[str, Any]:
        """AR balance for a wallet address. Returns {address, winston, ar} or {error}."""
        try:
            r = requests.get(f"{self.gateway}/wallet/{address}/balance", timeout=10)
            r.raise_for_status()
            winston = int(r.text.strip())
            return {"address": address, "winston": winston, "ar": winston / 1e12}
        except requests.RequestException as e:
            return {"error": str(e)}

    def bookmark(self, txid: str) -> Dict[str, Any]:
        """Fetch a permaweb tx's metadata and add it to the local index as a bookmark.

        Returns the indexed row; includes `already_indexed: True` if it was already present.
        Raises ValueError for local sha256- ids, RuntimeError on gateway failure.
        """
        if txid.startswith("sha256-"):
            raise ValueError(f"{txid!r} is a local-only id and cannot be bookmarked")
        # Return existing row if already indexed
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT txid, name, size, backend, created, metadata FROM objects WHERE txid = ?",
                (txid,),
            ).fetchone()
        if row is not None:
            result = dict(row)
            try:
                result["metadata"] = json.loads(result.get("metadata") or "{}")
            except Exception:
                result["metadata"] = {}
            result["url"] = f"{self.gateway}/{txid}" if result.get("backend") == "arweave" else None
            result["already_indexed"] = True
            return result
        # Fetch tx metadata from gateway
        url = f"{self.gateway}/tx/{txid}"
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            tx_data = r.json()
        except requests.RequestException as e:
            raise RuntimeError(f"gateway fetch failed for {txid}: {e}")
        if not isinstance(tx_data, dict):
            raise RuntimeError(f"gateway returned unexpected response for {txid}")
        raw_tags = tx_data.get("tags") or []
        name: Optional[str] = None
        ct = "application/octet-stream"
        for tag in raw_tags:
            n = tag.get("name", "")
            v = tag.get("value", "")
            if n == "File-Name" and not name:
                name = v
            if n == "Content-Type":
                ct = v
        size: Optional[int] = None
        try:
            size_raw = tx_data.get("data_size")
            if size_raw is not None:
                size = int(size_raw) or None
        except (ValueError, TypeError):
            pass
        if not name:
            name = txid[:12]
        tag_list = [[t.get("name", ""), t.get("value", "")] for t in raw_tags]
        self._index(txid, name, size, "arweave", {"tags": tag_list, "content_type": ct})
        return {
            "txid": txid, "name": name, "size": size, "backend": "arweave",
            "url": f"{self.gateway}/{txid}", "already_indexed": False,
        }

    def query(
        self,
        owner: Optional[str] = None,
        tags: Optional[List[Dict[str, Any]]] = None,
        first: int = 10,
        after: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search the permaweb via GraphQL. Returns transaction edges."""
        conditions = []
        if owner:
            conditions.append(f'owners: ["{owner}"]')
        if tags:
            tag_parts = []
            for t in tags:
                name = t.get("name", "")
                values = t.get("values", [])
                vals_str = ", ".join(f'"{v}"' for v in values)
                tag_parts.append(f'{{name: "{name}", values: [{vals_str}]}}')
            conditions.append(f'tags: [{", ".join(tag_parts)}]')
        conditions.append(f"first: {int(first)}")
        if after:
            conditions.append(f'after: "{after}"')
        gql = (
            "{ transactions("
            + ", ".join(conditions)
            + ") { edges { cursor node { id tags { name value } data { size } block { timestamp height } } } } }"
        )
        try:
            r = requests.post(
                f"{self.gateway}/graphql",
                json={"query": gql},
                headers={"Content-Type": "application/json"},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            return data.get("data", {}).get("transactions", {}).get("edges", [])
        except requests.RequestException as e:
            raise RuntimeError(f"GraphQL query failed: {e}")
