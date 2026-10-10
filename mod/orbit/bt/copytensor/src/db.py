"""
SQLite storage for copytensor — snapshots, trades, copy configs, watched accounts.
"""

import json
import os
import sqlite3
import time
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
    -- Which copies asked for this move, and for how much. A portfolio pass
    -- blends every sleeve into one trade per subnet, so `copy_id` alone
    -- (the largest contributor) can't say who the money belonged to.
    contributors_json TEXT,
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

-- Saved strats. They used to live in one browser's localStorage, which made
-- "my strats" mean "this laptop's strats" and left nothing to share. Owner
-- is the SHA-256 of the caller's owner key (X-Owner-Key) — the key itself
-- never touches the disk. visibility is 'private' (default), 'public' (the
-- hub lists it) or 'whitelist' (owner + the fingerprints in whitelist_json).
CREATE TABLE IF NOT EXISTS strats (
    id            TEXT PRIMARY KEY,
    owner_hash    TEXT    NOT NULL,
    name          TEXT    NOT NULL,
    visibility    TEXT    NOT NULL DEFAULT 'private',
    whitelist_json TEXT   NOT NULL DEFAULT '[]',
    payload_json  TEXT    NOT NULL,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_strats_owner ON strats(owner_hash);
CREATE INDEX IF NOT EXISTS idx_strats_vis ON strats(visibility);
"""


class Database:
    def __init__(self, db_path: str = DB_PATH):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._init_schema()

    def _init_schema(self):
        with self._conn() as conn:
            # WAL: the snapshot workers and the leaderboard walkers hit this
            # file from many threads at once, and the default rollback
            # journal turns every concurrent writer into "database is
            # locked" (a dropped snapshot, silently). Set once — it is a
            # persistent property of the file.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(SCHEMA)
            self._migrate(conn)

    @staticmethod
    def _migrate(conn):
        """Columns added after the first release. CREATE TABLE IF NOT EXISTS
        never touches a table that already exists, so an old file keeps the
        old shape until it is ALTERed here."""
        have = {r[1] for r in conn.execute("PRAGMA table_info(trades)")}
        if "contributors_json" not in have:
            conn.execute("ALTER TABLE trades ADD COLUMN contributors_json TEXT")

    # Long enough to outlast a burst of concurrent snapshot writes.
    BUSY_TIMEOUT_SEC = 30

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=self.BUSY_TIMEOUT_SEC)
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

    def get_first_snapshot(self, ss58: str) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM snapshots WHERE ss58 = ? ORDER BY block ASC LIMIT 1",
                (ss58,)
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            d["allocations"] = json.loads(d["allocations"])
            return d

    def get_snapshots(self, ss58: str, from_block: int = 0,
                      to_block: int = 2**63 - 1, limit: int = 100) -> List[Dict]:
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
                     error: Optional[str] = None,
                     contributors: Optional[Dict[str, float]] = None) -> str:
        trade_id = str(uuid.uuid4())[:8]
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO trades (id, copy_id, block, timestamp, action, netuid, "
                "amount_tao, tx_hash, status, error, contributors_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (trade_id, copy_id, block, timestamp, action, netuid,
                 amount_tao, tx_hash, status, error,
                 json.dumps(contributors) if contributors else None)
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
                # A portfolio pass files one trade per subnet under its
                # largest contributor, so filtering by copy has to look
                # inside the split too or a sleeve's own tape goes missing.
                rows = conn.execute(
                    "SELECT * FROM trades WHERE copy_id = ? "
                    "   OR contributors_json LIKE ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (copy_id, f'%"{copy_id}"%', limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                raw = d.pop("contributors_json", None)
                try:
                    d["contributors"] = json.loads(raw) if raw else None
                except (TypeError, ValueError):
                    d["contributors"] = None
                out.append(d)
            return out

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

    # ── strats ───────────────────────────────────────────────────────
    # A strat row is (who owns it, who may see it, the basket itself). The
    # payload is the whole client-side shape, stored as JSON so the picker
    # can keep evolving without a migration for every new knob.

    def _strat_row(self, r: sqlite3.Row) -> Dict:
        d = dict(r)
        d["whitelist"] = json.loads(d.pop("whitelist_json") or "[]")
        d.update(json.loads(d.pop("payload_json") or "{}"))
        return d

    def create_strat(self, owner_hash: str, name: str, payload: Dict,
                     visibility: str = "private",
                     whitelist: Optional[List[str]] = None,
                     strat_id: Optional[str] = None) -> Dict:
        now = int(time.time())
        sid = strat_id or f"st_{uuid.uuid4().hex[:12]}"
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO strats (id, owner_hash, name, visibility, whitelist_json,"
                " payload_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (sid, owner_hash, name, visibility,
                 json.dumps(whitelist or []), json.dumps(payload), now, now),
            )
        return self.get_strat(sid)

    def get_strat(self, strat_id: str) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM strats WHERE id = ?", (strat_id,)).fetchone()
            return self._strat_row(row) if row else None

    def update_strat(self, strat_id: str, *, name: Optional[str] = None,
                     payload: Optional[Dict] = None,
                     visibility: Optional[str] = None,
                     whitelist: Optional[List[str]] = None) -> Optional[Dict]:
        cur = self.get_strat(strat_id)
        if not cur:
            return None
        sets, vals = ["updated_at = ?"], [int(time.time())]
        if name is not None:
            sets.append("name = ?"); vals.append(name)
        if visibility is not None:
            sets.append("visibility = ?"); vals.append(visibility)
        if whitelist is not None:
            sets.append("whitelist_json = ?"); vals.append(json.dumps(whitelist))
        if payload is not None:
            sets.append("payload_json = ?"); vals.append(json.dumps(payload))
        vals.append(strat_id)
        with self._conn() as conn:
            conn.execute(f"UPDATE strats SET {', '.join(sets)} WHERE id = ?", vals)
        return self.get_strat(strat_id)

    def delete_strat(self, strat_id: str):
        with self._conn() as conn:
            conn.execute("DELETE FROM strats WHERE id = ?", (strat_id,))

    def list_strats(self, owner_hash: Optional[str] = None,
                    fingerprint: Optional[str] = None,
                    include_public: bool = True) -> List[Dict]:
        """Everything this caller may see: their own, the public shelf, and
        anything whose whitelist names their FINGERPRINT (what they hand out;
        the full hash never leaves this process). An anonymous caller sees
        the public shelf only."""
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM strats ORDER BY updated_at DESC").fetchall()
        out = []
        for r in rows:
            s = self._strat_row(r)
            mine = bool(owner_hash) and s["owner_hash"] == owner_hash
            listed = bool(fingerprint) and fingerprint in (s.get("whitelist") or [])
            if mine or listed or (include_public and s["visibility"] == "public"):
                out.append(s)
        return out

    def list_public_strats(self) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM strats WHERE visibility = 'public' ORDER BY updated_at DESC"
            ).fetchall()
        return [self._strat_row(r) for r in rows]
