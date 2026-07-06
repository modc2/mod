"""
SQLite storage for copytensor — snapshots, trades, copy configs, watched accounts.
"""

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DATA_DIR, "copytensor.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ss58        TEXT    NOT NULL,
    block       INTEGER NOT NULL,
    timestamp   TEXT    NOT NULL,
    total_value_tao REAL NOT NULL,
    allocations TEXT    NOT NULL,
    created_at  TEXT    DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(ss58, block)
);
CREATE INDEX IF NOT EXISTS idx_snap_ss58_block ON snapshots(ss58, block);

CREATE TABLE IF NOT EXISTS trades (
    id          TEXT PRIMARY KEY,
    copy_id     TEXT    NOT NULL,
    block       INTEGER,
    timestamp   TEXT    NOT NULL,
    action      TEXT    NOT NULL,
    netuid      INTEGER NOT NULL,
    amount_tao  REAL    NOT NULL,
    tx_hash     TEXT,
    status      TEXT    NOT NULL DEFAULT 'pending',
    error       TEXT,
    created_at  TEXT    DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_trades_copy ON trades(copy_id);

CREATE TABLE IF NOT EXISTS copies (
    id              TEXT PRIMARY KEY,
    target_ss58     TEXT    NOT NULL,
    label           TEXT,
    status          TEXT    NOT NULL DEFAULT 'active',
    config_json     TEXT    NOT NULL,
    last_sync_block INTEGER,
    created_at      TEXT    DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT    DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS accounts (
    ss58        TEXT PRIMARY KEY,
    label       TEXT,
    added_at    TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


class Database:
    def __init__(self, db_path: str = DB_PATH):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._init_schema()

    def _init_schema(self):
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ── snapshots ────────────────────────────────────────────────────

    def insert_snapshot(self, ss58: str, block: int, timestamp: str,
                        total_value_tao: float, allocations: List[Dict]) -> int:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO snapshots (ss58, block, timestamp, total_value_tao, allocations) "
                "VALUES (?, ?, ?, ?, ?)",
                (ss58, block, timestamp, total_value_tao, json.dumps(allocations))
            )
            return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def get_nearest_snapshot(self, ss58: str, target_block: int) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM snapshots WHERE ss58 = ? AND block <= ? "
                "ORDER BY block DESC LIMIT 1",
                (ss58, target_block)
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            d["allocations"] = json.loads(d["allocations"])
            return d

    def get_snapshots(self, ss58: str, from_block: int = 0,
                      to_block: int = 2**63, limit: int = 100) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM snapshots WHERE ss58 = ? AND block >= ? AND block <= ? "
                "ORDER BY block DESC LIMIT ?",
                (ss58, from_block, to_block, limit)
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["allocations"] = json.loads(d["allocations"])
                out.append(d)
            return out

    # ── trades ───────────────────────────────────────────────────────

    def insert_trade(self, copy_id: str, block: Optional[int], timestamp: str,
                     action: str, netuid: int, amount_tao: float,
                     tx_hash: Optional[str] = None, status: str = "pending",
                     error: Optional[str] = None) -> str:
        trade_id = str(uuid.uuid4())[:8]
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO trades (id, copy_id, block, timestamp, action, netuid, "
                "amount_tao, tx_hash, status, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (trade_id, copy_id, block, timestamp, action, netuid,
                 amount_tao, tx_hash, status, error)
            )
        return trade_id

    def update_trade(self, trade_id: str, **kwargs):
        allowed = {"status", "tx_hash", "error", "block"}
        sets = {k: v for k, v in kwargs.items() if k in allowed}
        if not sets:
            return
        clause = ", ".join(f"{k} = ?" for k in sets)
        with self._conn() as conn:
            conn.execute(
                f"UPDATE trades SET {clause} WHERE id = ?",
                list(sets.values()) + [trade_id]
            )

    def get_trades(self, copy_id: Optional[str] = None, limit: int = 50) -> List[Dict]:
        with self._conn() as conn:
            if copy_id:
                rows = conn.execute(
                    "SELECT * FROM trades WHERE copy_id = ? ORDER BY created_at DESC LIMIT ?",
                    (copy_id, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            return [dict(r) for r in rows]

    # ── copies ───────────────────────────────────────────────────────

    def insert_copy(self, target_ss58: str, config: Dict,
                    label: Optional[str] = None) -> str:
        copy_id = str(uuid.uuid4())[:8]
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO copies (id, target_ss58, label, config_json) VALUES (?, ?, ?, ?)",
                (copy_id, target_ss58, label, json.dumps(config))
            )
        return copy_id

    def get_copy(self, copy_id: str) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM copies WHERE id = ?", (copy_id,)).fetchone()
            if not row:
                return None
            d = dict(row)
            d["config"] = json.loads(d.pop("config_json"))
            return d

    def list_copies(self, status: Optional[str] = None) -> List[Dict]:
        with self._conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM copies WHERE status = ? ORDER BY created_at DESC",
                    (status,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM copies WHERE status != 'stopped' ORDER BY created_at DESC"
                ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["config"] = json.loads(d.pop("config_json"))
                out.append(d)
            return out

    def update_copy(self, copy_id: str, **kwargs):
        allowed = {"status", "label", "last_sync_block"}
        sets = {k: v for k, v in kwargs.items() if k in allowed}
        if not sets:
            return
        sets["updated_at"] = "CURRENT_TIMESTAMP"
        clause = ", ".join(
            f"{k} = CURRENT_TIMESTAMP" if v == "CURRENT_TIMESTAMP" else f"{k} = ?"
            for k, v in sets.items()
        )
        vals = [v for v in sets.values() if v != "CURRENT_TIMESTAMP"]
        with self._conn() as conn:
            conn.execute(f"UPDATE copies SET {clause} WHERE id = ?", vals + [copy_id])

    def update_copy_config(self, copy_id: str, config: Dict):
        with self._conn() as conn:
            conn.execute(
                "UPDATE copies SET config_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (json.dumps(config), copy_id)
            )

    def delete_copy(self, copy_id: str):
        with self._conn() as conn:
            conn.execute("UPDATE copies SET status = 'stopped', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (copy_id,))

    # ── accounts (watchlist) ─────────────────────────────────────────

    def add_account(self, ss58: str, label: Optional[str] = None):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO accounts (ss58, label) VALUES (?, ?)",
                (ss58, label)
            )

    def remove_account(self, ss58: str):
        with self._conn() as conn:
            conn.execute("DELETE FROM accounts WHERE ss58 = ?", (ss58,))

    def list_accounts(self) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM accounts ORDER BY added_at DESC").fetchall()
            return [dict(r) for r in rows]

    def has_account(self, ss58: str) -> bool:
        with self._conn() as conn:
            row = conn.execute("SELECT 1 FROM accounts WHERE ss58 = ?", (ss58,)).fetchone()
            return row is not None
