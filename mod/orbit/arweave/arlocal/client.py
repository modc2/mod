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
from typing import Any, Dict, List, Optional, Union

import requests


DEFAULT_GATEWAY = "https://arweave.net"
GATEWAY_FALLBACKS = [
    "https://arweave.net",
    "https://arweave.dev",
    "https://g8way.io",
]

# Wallet JWK lives off-tree with the other per-module secrets, never in config.
WALLET_PATH = Path(os.path.expanduser("~/.mod/arweave/wallet.json"))


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

        return self._upload_bytes(payload, name, content_type)

    def put_file(self, path: str) -> Dict[str, Any]:
        """Store a file on Arweave. Returns {txid, size, backend}."""
        p = Path(path).expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(str(p))
        with open(p, "rb") as f:
            payload = f.read()
        return self._upload_bytes(payload, p.name, "application/octet-stream")

    def _upload_bytes(
        self, payload: bytes, name: str, content_type: str = "application/octet-stream"
    ) -> Dict[str, Any]:
        if not self.wallet_path or not Path(self.wallet_path).expanduser().is_file():
            digest = hashlib.sha256(payload).hexdigest()
            txid = f"sha256-{digest[:43]}"
            blob = self._blob_path(txid)
            if blob is not None:
                blob.write_bytes(payload)
            self._index(txid, name, len(payload), "local",
                        {"pinned": False, "content_type": content_type})
            return {
                "txid": txid,
                "size": len(payload),
                "backend": "local",
                "name": name,
                "pinned": False,
                "warning": "no Arweave wallet configured — content kept locally only "
                           "(set ARWEAVE_WALLET or ~/.mod/arweave/wallet.json to upload).",
            }

        try:
            import arweave  # type: ignore
        except ImportError:
            return {
                "error": "arweave-python-client not installed; pip install arweave-python-client",
                "backend": "arweave",
            }

        try:
            wallet = arweave.Wallet(str(Path(self.wallet_path).expanduser()))
            tx = arweave.Transaction(wallet, data=payload)
            tx.add_tag("Content-Type", content_type)
            if name:
                tx.add_tag("File-Name", name)
            tx.sign()
            tx.send()
        except Exception as e:
            return {"error": f"arweave upload failed: {e}", "backend": "arweave"}

        txid = tx.id
        self._index(txid, name, len(payload), "arweave", {"pinned": True})
        return {"txid": txid, "size": len(payload), "backend": "arweave", "name": name}

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

    def _fetch(self, txid: str) -> bytes:
        if txid.startswith("sha256-"):
            blob = self._blob_path(txid)
            if blob is not None and blob.is_file():
                return blob.read_bytes()
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
                    return r.content
                last_err = RuntimeError(f"{url} → {r.status_code}")
            except requests.RequestException as e:
                last_err = e
        raise RuntimeError(f"all gateways failed for {txid}: {last_err}")

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

    def status(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "gateway": self.gateway,
            "wallet_configured": bool(self.wallet_path and Path(self.wallet_path).expanduser().is_file()),
            "store_dir": str(self.store_dir),
        }
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
            return {"bytes": int(num_bytes), "winston": r.text.strip()}
        except requests.RequestException as e:
            return {"error": str(e), "bytes": num_bytes}

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
            + ") { edges { cursor node { id tags { name value } data { size } } } } }"
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
