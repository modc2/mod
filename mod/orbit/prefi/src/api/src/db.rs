//! Database schema and operations for PreFi markets

use rusqlite::{Connection, Result as SqlResult};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Market {
    pub id: String,
    pub title: String,
    pub description: String,
    pub creator: String,
    pub total_volume: f64,
    pub yes_price: f64,
    pub no_price: f64,
    pub status: String, // "active", "resolved", "cancelled"
    pub resolution: Option<bool>,
    pub created_at: i64,
    pub resolved_at: Option<i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Position {
    pub id: String,
    pub market_id: String,
    pub user_address: String,
    pub position_type: String, // "yes" or "no"
    pub amount: f64,
    pub entry_price: f64,
    pub created_at: i64,
}

pub fn init_db(db_path: PathBuf) -> SqlResult<Connection> {
    std::fs::create_dir_all(db_path.parent().unwrap())?;
    let conn = Connection::open(db_path)?;

    conn.execute(
        "CREATE TABLE IF NOT EXISTS markets (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            creator TEXT NOT NULL,
            total_volume REAL DEFAULT 0.0,
            yes_price REAL DEFAULT 0.5,
            no_price REAL DEFAULT 0.5,
            status TEXT DEFAULT 'active',
            resolution INTEGER,
            created_at INTEGER NOT NULL,
            resolved_at INTEGER
        )",
        [],
    )?;

    conn.execute(
        "CREATE TABLE IF NOT EXISTS positions (
            id TEXT PRIMARY KEY,
            market_id TEXT NOT NULL,
            user_address TEXT NOT NULL,
            position_type TEXT NOT NULL,
            amount REAL NOT NULL,
            entry_price REAL NOT NULL,
            created_at INTEGER NOT NULL,
            FOREIGN KEY (market_id) REFERENCES markets(id)
        )",
        [],
    )?;

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_market_user
         ON positions(market_id, user_address)",
        [],
    )?;

    Ok(conn)
}
