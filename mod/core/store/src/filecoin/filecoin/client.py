"""
FilecoinClient — store and retrieve data on Filecoin.

Backends (auto-selected based on env / config):
  - Lotus JSON-RPC node (chain head, deal lookups, raw RPC pass-through)
  - Lighthouse Storage (free uploads that make Filecoin deals; requires API key)
  - IPFS / Filecoin public gateway (always-available retrieval)
  - Local SQLite index (tracks CIDs and metadata for easy listing)
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


DEFAULT_RPC = "https://api.node.glif.io/rpc/v1"
DEFAULT_GATEWAY = "https://gateway.lighthouse.storage"
LIGHTHOUSE_UPLOAD = "https://node.lighthouse.storage/api/v0/add"
LIGHTHOUSE_DEALS = "https://api.lighthouse.storage/api/lighthouse/deal_status"


class FilecoinClient:
    """Pragmatic Filecoin client. Works without auth for reads; uploads need a Lighthouse API key."""

    def __init__(
        self,
        rpc_url: Optional[str] = None,
        rpc_token: Optional[str] = None,
        lighthouse_key: Optional[str] = None,
        gateway: Optional[str] = None,
        store_path: Optional[str] = None,
    ):
        self.rpc_url = rpc_url or os.environ.get("FILECOIN_RPC", DEFAULT_RPC)
        self.rpc_token = rpc_token or os.environ.get("FILECOIN_TOKEN")
        self.lighthouse_key = lighthouse_key or os.environ.get("LIGHTHOUSE_API_KEY")
        self.gateway = (gateway or os.environ.get("FILECOIN_GATEWAY", DEFAULT_GATEWAY)).rstrip("/")

        self.store_dir = Path(store_path or os.path.expanduser("~/.filecoin-mod"))
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.store_dir / "index.db"
        self._init_db()

    # ─── local index ────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS objects (
                    cid TEXT PRIMARY KEY,
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
        cid: str,
        name: Optional[str],
        size: Optional[int],
        backend: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO objects (cid, name, size, backend, created, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (cid, name, size, backend, int(time.time()), json.dumps(metadata or {})),
            )
            conn.commit()

    def list(self, limit: int = 100) -> List[Dict[str, Any]]:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT cid, name, size, backend, created, metadata FROM objects "
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

    def rm(self, cid: str) -> Dict[str, Any]:
        with sqlite3.connect(str(self.db_path)) as conn:
            cur = conn.execute("DELETE FROM objects WHERE cid = ?", (cid,))
            conn.commit()
            return {"cid": cid, "removed": cur.rowcount > 0}

    # ─── put / store ────────────────────────────────────────────────────────

    def put(
        self,
        data: Union[bytes, str, dict, list],
        name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Store arbitrary data on Filecoin (via Lighthouse). Returns {cid, size, backend}."""
        if isinstance(data, (dict, list)):
            payload = json.dumps(data).encode()
            name = name or "data.json"
        elif isinstance(data, str):
            payload = data.encode()
            name = name or "data.txt"
        elif isinstance(data, bytes):
            payload = data
            name = name or "data.bin"
        else:
            raise TypeError(f"unsupported data type: {type(data).__name__}")

        return self._upload_bytes(payload, name)

    def put_file(self, path: str) -> Dict[str, Any]:
        """Store a file on Filecoin. Returns {cid, size, backend}."""
        p = Path(path).expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(str(p))
        with open(p, "rb") as f:
            payload = f.read()
        return self._upload_bytes(payload, p.name)

    def _upload_bytes(self, payload: bytes, name: str) -> Dict[str, Any]:
        if not self.lighthouse_key:
            # No write backend configured — record a deterministic content hash
            # so callers see consistent behavior, but flag it as unpinned.
            digest = hashlib.sha256(payload).hexdigest()
            cid = f"sha256-{digest[:46]}"
            self._index(cid, name, len(payload), "local", {"pinned": False})
            return {
                "cid": cid,
                "size": len(payload),
                "backend": "local",
                "pinned": False,
                "warning": "LIGHTHOUSE_API_KEY not set — content not uploaded to Filecoin.",
            }

        headers = {"Authorization": f"Bearer {self.lighthouse_key}"}
        files = {"file": (name, payload)}
        try:
            r = requests.post(LIGHTHOUSE_UPLOAD, headers=headers, files=files, timeout=120)
            r.raise_for_status()
        except requests.RequestException as e:
            return {"error": f"lighthouse upload failed: {e}", "backend": "lighthouse"}

        body = r.json()
        cid = body.get("Hash") or body.get("hash") or body.get("cid")
        size = int(body.get("Size") or body.get("size") or len(payload))
        if not cid:
            return {"error": "no CID in response", "raw": body}

        self._index(cid, name, size, "lighthouse", {"pinned": True})
        return {"cid": cid, "size": size, "backend": "lighthouse", "name": name}

    # ─── get / retrieve ─────────────────────────────────────────────────────

    def get(self, cid: str) -> Union[bytes, dict, list, str]:
        """Retrieve content by CID. Returns parsed JSON when possible, else bytes."""
        raw = self._fetch(cid)
        try:
            text = raw.decode()
        except UnicodeDecodeError:
            return raw
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def get_file(self, cid: str, dest: str) -> Dict[str, Any]:
        """Retrieve content by CID and write it to `dest`."""
        raw = self._fetch(cid)
        p = Path(dest).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "wb") as f:
            f.write(raw)
        return {"cid": cid, "path": str(p), "size": len(raw)}

    def _fetch(self, cid: str) -> bytes:
        if cid.startswith("sha256-"):
            raise ValueError(
                f"{cid} is a local-only placeholder; configure LIGHTHOUSE_API_KEY "
                "to upload content to Filecoin before retrieval."
            )
        # Try the configured gateway first, then fall back to public IPFS gateways.
        gateways = [
            self.gateway,
            "https://w3s.link",
            "https://ipfs.io",
            "https://dweb.link",
        ]
        last_err: Optional[Exception] = None
        for gw in gateways:
            url = f"{gw.rstrip('/')}/ipfs/{cid}"
            try:
                r = requests.get(url, timeout=60, allow_redirects=True)
                if r.ok and r.content:
                    return r.content
                last_err = RuntimeError(f"{url} → {r.status_code}")
            except requests.RequestException as e:
                last_err = e
        raise RuntimeError(f"all gateways failed for {cid}: {last_err}")

    # ─── deals / chain ──────────────────────────────────────────────────────

    def deals(self, cid: str) -> Any:
        """Return Filecoin storage deals for a CID (via Lighthouse public API)."""
        try:
            r = requests.get(LIGHTHOUSE_DEALS, params={"cid": cid}, timeout=30)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            return {"error": str(e), "cid": cid}

    def chain_head(self) -> Dict[str, Any]:
        """Filecoin chain head via the configured Lotus RPC endpoint."""
        return self.rpc("Filecoin.ChainHead", [])

    def rpc(self, method: str, params: Optional[list] = None) -> Dict[str, Any]:
        """Pass-through Lotus JSON-RPC."""
        body = {"jsonrpc": "2.0", "method": method, "params": params or [], "id": 1}
        headers = {"Content-Type": "application/json"}
        if self.rpc_token:
            headers["Authorization"] = f"Bearer {self.rpc_token}"
        try:
            r = requests.post(self.rpc_url, json=body, headers=headers, timeout=30)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            return {"error": str(e), "method": method}

    # ─── status ─────────────────────────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "rpc_url": self.rpc_url,
            "gateway": self.gateway,
            "lighthouse_configured": bool(self.lighthouse_key),
            "store_dir": str(self.store_dir),
        }
        head = self.chain_head()
        if "error" in head:
            out["lotus"] = {"ok": False, "error": head["error"]}
        else:
            result = head.get("result", {})
            out["lotus"] = {
                "ok": True,
                "height": result.get("Height"),
                "blocks": len(result.get("Blocks", [])),
            }
        with sqlite3.connect(str(self.db_path)) as conn:
            (count,) = conn.execute("SELECT COUNT(*) FROM objects").fetchone()
        out["indexed"] = count
        return out
