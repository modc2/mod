use anyhow::Result;
use rusqlite::{params, Connection};
use std::path::Path;
use std::sync::Mutex;

const SCHEMA: &str = r#"
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
"#;

pub struct Database {
    conn: Mutex<Connection>,
}

impl Database {
    pub fn open(path: &Path) -> Result<Self> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let conn = Connection::open(path)?;
        conn.execute_batch(SCHEMA)?;
        Ok(Self {
            conn: Mutex::new(conn),
        })
    }

    // ── snapshots ────────────────────────────────────────────────

    pub fn insert_snapshot(
        &self,
        ss58: &str,
        block: u64,
        timestamp: &str,
        total_value_tao: f64,
        allocations: &serde_json::Value,
    ) -> Result<i64> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT OR REPLACE INTO snapshots (ss58, block, timestamp, total_value_tao, allocations)
             VALUES (?1, ?2, ?3, ?4, ?5)",
            params![
                ss58,
                block as i64,
                timestamp,
                total_value_tao,
                serde_json::to_string(allocations)?
            ],
        )?;
        Ok(conn.last_insert_rowid())
    }

    pub fn get_nearest_snapshot(
        &self,
        ss58: &str,
        target_block: u64,
    ) -> Result<Option<SnapshotRow>> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT id, ss58, block, timestamp, total_value_tao, allocations
             FROM snapshots WHERE ss58 = ?1 AND block <= ?2
             ORDER BY block DESC LIMIT 1",
        )?;
        let row = stmt
            .query_row(params![ss58, target_block as i64], |row| {
                Ok(SnapshotRow {
                    id: row.get(0)?,
                    ss58: row.get(1)?,
                    block: row.get::<_, i64>(2)? as u64,
                    timestamp: row.get(3)?,
                    total_value_tao: row.get(4)?,
                    allocations_json: row.get(5)?,
                })
            })
            .ok();
        Ok(row)
    }

    pub fn get_snapshots(
        &self,
        ss58: &str,
        limit: usize,
    ) -> Result<Vec<SnapshotRow>> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT id, ss58, block, timestamp, total_value_tao, allocations
             FROM snapshots WHERE ss58 = ?1
             ORDER BY block DESC LIMIT ?2",
        )?;
        let rows = stmt
            .query_map(params![ss58, limit as i64], |row| {
                Ok(SnapshotRow {
                    id: row.get(0)?,
                    ss58: row.get(1)?,
                    block: row.get::<_, i64>(2)? as u64,
                    timestamp: row.get(3)?,
                    total_value_tao: row.get(4)?,
                    allocations_json: row.get(5)?,
                })
            })?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        Ok(rows)
    }

    // ── trades ───────────────────────────────────────────────────

    pub fn insert_trade(
        &self,
        copy_id: &str,
        block: Option<u64>,
        timestamp: &str,
        action: &str,
        netuid: u16,
        amount_tao: f64,
        status: &str,
    ) -> Result<String> {
        let id = uuid::Uuid::new_v4().to_string()[..8].to_string();
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT INTO trades (id, copy_id, block, timestamp, action, netuid, amount_tao, status)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![
                id,
                copy_id,
                block.map(|b| b as i64),
                timestamp,
                action,
                netuid as i64,
                amount_tao,
                status
            ],
        )?;
        Ok(id)
    }

    pub fn update_trade(
        &self,
        trade_id: &str,
        status: &str,
        tx_hash: Option<&str>,
        error: Option<&str>,
    ) -> Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "UPDATE trades SET status = ?1, tx_hash = ?2, error = ?3 WHERE id = ?4",
            params![status, tx_hash, error, trade_id],
        )?;
        Ok(())
    }

    pub fn get_trades(
        &self,
        copy_id: Option<&str>,
        limit: usize,
    ) -> Result<Vec<TradeRow>> {
        let conn = self.conn.lock().unwrap();
        if let Some(cid) = copy_id {
            let mut stmt = conn.prepare(
                "SELECT id, copy_id, block, timestamp, action, netuid, amount_tao,
                        tx_hash, status, error
                 FROM trades WHERE copy_id = ?1 ORDER BY created_at DESC LIMIT ?2",
            )?;
            let rows = stmt
                .query_map(params![cid, limit as i64], map_trade_row)?
                .collect::<std::result::Result<Vec<_>, _>>()?;
            return Ok(rows);
        }
        let mut stmt = conn.prepare(
            "SELECT id, copy_id, block, timestamp, action, netuid, amount_tao,
                    tx_hash, status, error
             FROM trades ORDER BY created_at DESC LIMIT ?1",
        )?;
        let rows = stmt
            .query_map(params![limit as i64], map_trade_row)?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        Ok(rows)
    }

    // ── copies ───────────────────────────────────────────────────

    pub fn insert_copy(
        &self,
        target_ss58: &str,
        config: &serde_json::Value,
        label: Option<&str>,
    ) -> Result<String> {
        let id = uuid::Uuid::new_v4().to_string()[..8].to_string();
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT INTO copies (id, target_ss58, label, config_json)
             VALUES (?1, ?2, ?3, ?4)",
            params![id, target_ss58, label, serde_json::to_string(config)?],
        )?;
        Ok(id)
    }

    pub fn get_copy(&self, copy_id: &str) -> Result<Option<CopyRow>> {
        let conn = self.conn.lock().unwrap();
        let row = conn
            .query_row(
                "SELECT id, target_ss58, label, status, config_json,
                        last_sync_block, created_at, updated_at
                 FROM copies WHERE id = ?1",
                params![copy_id],
                map_copy_row,
            )
            .ok();
        Ok(row)
    }

    pub fn list_copies(&self) -> Result<Vec<CopyRow>> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT id, target_ss58, label, status, config_json,
                    last_sync_block, created_at, updated_at
             FROM copies WHERE status != 'stopped' ORDER BY created_at DESC",
        )?;
        let rows = stmt
            .query_map([], map_copy_row)?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        Ok(rows)
    }

    pub fn update_copy_status(&self, copy_id: &str, status: &str) -> Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "UPDATE copies SET status = ?1, updated_at = CURRENT_TIMESTAMP WHERE id = ?2",
            params![status, copy_id],
        )?;
        Ok(())
    }

    pub fn update_copy_sync_block(&self, copy_id: &str, block: u64) -> Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "UPDATE copies SET last_sync_block = ?1, updated_at = CURRENT_TIMESTAMP WHERE id = ?2",
            params![block as i64, copy_id],
        )?;
        Ok(())
    }

    pub fn delete_copy(&self, copy_id: &str) -> Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "UPDATE copies SET status = 'stopped', updated_at = CURRENT_TIMESTAMP WHERE id = ?1",
            params![copy_id],
        )?;
        Ok(())
    }

    // ── accounts (watchlist) ─────────────────────────────────────

    pub fn add_account(&self, ss58: &str, label: Option<&str>) -> Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT OR REPLACE INTO accounts (ss58, label) VALUES (?1, ?2)",
            params![ss58, label],
        )?;
        Ok(())
    }

    pub fn remove_account(&self, ss58: &str) -> Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute("DELETE FROM accounts WHERE ss58 = ?1", params![ss58])?;
        Ok(())
    }

    pub fn list_accounts(&self) -> Result<Vec<AccountRow>> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT ss58, label, added_at FROM accounts ORDER BY added_at DESC",
        )?;
        let rows = stmt
            .query_map([], |row| {
                Ok(AccountRow {
                    ss58: row.get(0)?,
                    label: row.get(1)?,
                    added_at: row.get(2)?,
                })
            })?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        Ok(rows)
    }

    pub fn account_count(&self) -> Result<usize> {
        let conn = self.conn.lock().unwrap();
        let count: i64 =
            conn.query_row("SELECT COUNT(*) FROM accounts", [], |row| row.get(0))?;
        Ok(count as usize)
    }
}

// ── row types ───────────────────────────────────────────────────

#[derive(Debug)]
pub struct SnapshotRow {
    pub id: i64,
    pub ss58: String,
    pub block: u64,
    pub timestamp: String,
    pub total_value_tao: f64,
    pub allocations_json: String,
}

#[derive(Debug)]
pub struct TradeRow {
    pub id: String,
    pub copy_id: String,
    pub block: Option<u64>,
    pub timestamp: String,
    pub action: String,
    pub netuid: u16,
    pub amount_tao: f64,
    pub tx_hash: Option<String>,
    pub status: String,
    pub error: Option<String>,
}

#[derive(Debug)]
pub struct CopyRow {
    pub id: String,
    pub target_ss58: String,
    pub label: Option<String>,
    pub status: String,
    pub config_json: String,
    pub last_sync_block: Option<u64>,
    pub created_at: Option<String>,
    pub updated_at: Option<String>,
}

#[derive(Debug)]
pub struct AccountRow {
    pub ss58: String,
    pub label: Option<String>,
    pub added_at: Option<String>,
}

fn map_trade_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<TradeRow> {
    Ok(TradeRow {
        id: row.get(0)?,
        copy_id: row.get(1)?,
        block: row.get::<_, Option<i64>>(2)?.map(|b| b as u64),
        timestamp: row.get(3)?,
        action: row.get(4)?,
        netuid: row.get::<_, i64>(5)? as u16,
        amount_tao: row.get(6)?,
        tx_hash: row.get(7)?,
        status: row.get(8)?,
        error: row.get(9)?,
    })
}

fn map_copy_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<CopyRow> {
    Ok(CopyRow {
        id: row.get(0)?,
        target_ss58: row.get(1)?,
        label: row.get(2)?,
        status: row.get(3)?,
        config_json: row.get(4)?,
        last_sync_block: row.get::<_, Option<i64>>(5)?.map(|b| b as u64),
        created_at: row.get(6)?,
        updated_at: row.get(7)?,
    })
}
