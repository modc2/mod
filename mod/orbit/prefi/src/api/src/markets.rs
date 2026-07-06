//! Market management and state logic

use crate::db::{self, Market, Position};
use rusqlite::{params, Connection};
use std::path::PathBuf;
use std::sync::Mutex;
use uuid::Uuid;

pub struct MarketManager {
    conn: Mutex<Connection>,
}

impl MarketManager {
    pub fn new(db_dir: PathBuf) -> Result<Self, String> {
        let db_path = db_dir.join("prefi.db");
        let conn = db::init_db(db_path).map_err(|e| e.to_string())?;
        Ok(Self {
            conn: Mutex::new(conn),
        })
    }

    /// Create a new prediction market
    pub fn create_market(
        &self,
        title: String,
        description: String,
        creator: String,
    ) -> Result<Market, String> {
        let id = Uuid::new_v4().to_string();
        let created_at = chrono::Utc::now().timestamp();

        let market = Market {
            id: id.clone(),
            title,
            description,
            creator,
            total_volume: 0.0,
            yes_price: 0.5,
            no_price: 0.5,
            status: "active".to_string(),
            resolution: None,
            created_at,
            resolved_at: None,
        };

        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT INTO markets (id, title, description, creator, total_volume, yes_price, no_price, status, created_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![
                &market.id,
                &market.title,
                &market.description,
                &market.creator,
                market.total_volume,
                market.yes_price,
                market.no_price,
                &market.status,
                market.created_at
            ],
        )
        .map_err(|e| e.to_string())?;

        Ok(market)
    }

    /// List all markets
    pub fn list_markets(&self, status: Option<String>) -> Result<Vec<Market>, String> {
        let conn = self.conn.lock().unwrap();

        let mut stmt = if let Some(s) = status {
            conn.prepare("SELECT id, title, description, creator, total_volume, yes_price, no_price, status, resolution, created_at, resolved_at FROM markets WHERE status = ?1 ORDER BY created_at DESC")
                .map_err(|e| e.to_string())?
        } else {
            conn.prepare("SELECT id, title, description, creator, total_volume, yes_price, no_price, status, resolution, created_at, resolved_at FROM markets ORDER BY created_at DESC")
                .map_err(|e| e.to_string())?
        };

        let markets_iter = if let Some(s) = status {
            stmt.query_map(params![s], |row| {
                Ok(Market {
                    id: row.get(0)?,
                    title: row.get(1)?,
                    description: row.get(2)?,
                    creator: row.get(3)?,
                    total_volume: row.get(4)?,
                    yes_price: row.get(5)?,
                    no_price: row.get(6)?,
                    status: row.get(7)?,
                    resolution: row.get(8)?,
                    created_at: row.get(9)?,
                    resolved_at: row.get(10)?,
                })
            })
        } else {
            stmt.query_map([], |row| {
                Ok(Market {
                    id: row.get(0)?,
                    title: row.get(1)?,
                    description: row.get(2)?,
                    creator: row.get(3)?,
                    total_volume: row.get(4)?,
                    yes_price: row.get(5)?,
                    no_price: row.get(6)?,
                    status: row.get(7)?,
                    resolution: row.get(8)?,
                    created_at: row.get(9)?,
                    resolved_at: row.get(10)?,
                })
            })
        }
        .map_err(|e| e.to_string())?;

        let mut markets = Vec::new();
        for market in markets_iter {
            markets.push(market.map_err(|e| e.to_string())?);
        }
        Ok(markets)
    }

    /// Get a single market by ID
    pub fn get_market(&self, market_id: &str) -> Result<Market, String> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn
            .prepare("SELECT id, title, description, creator, total_volume, yes_price, no_price, status, resolution, created_at, resolved_at FROM markets WHERE id = ?1")
            .map_err(|e| e.to_string())?;

        stmt.query_row(params![market_id], |row| {
            Ok(Market {
                id: row.get(0)?,
                title: row.get(1)?,
                description: row.get(2)?,
                creator: row.get(3)?,
                total_volume: row.get(4)?,
                yes_price: row.get(5)?,
                no_price: row.get(6)?,
                status: row.get(7)?,
                resolution: row.get(8)?,
                created_at: row.get(9)?,
                resolved_at: row.get(10)?,
            })
        })
        .map_err(|e| e.to_string())
    }

    /// Create a position in a market
    pub fn create_position(
        &self,
        market_id: String,
        user_address: String,
        position_type: String, // "yes" or "no"
        amount: f64,
    ) -> Result<Position, String> {
        let id = Uuid::new_v4().to_string();
        let created_at = chrono::Utc::now().timestamp();

        // Get current market price
        let market = self.get_market(&market_id)?;
        let entry_price = if position_type == "yes" {
            market.yes_price
        } else {
            market.no_price
        };

        let position = Position {
            id: id.clone(),
            market_id: market_id.clone(),
            user_address: user_address.clone(),
            position_type: position_type.clone(),
            amount,
            entry_price,
            created_at,
        };

        let conn = self.conn.lock().unwrap();

        // Insert position
        conn.execute(
            "INSERT INTO positions (id, market_id, user_address, position_type, amount, entry_price, created_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![
                &position.id,
                &position.market_id,
                &position.user_address,
                &position.position_type,
                position.amount,
                position.entry_price,
                position.created_at
            ],
        )
        .map_err(|e| e.to_string())?;

        // Update market volume and prices (simple constant product market maker)
        let new_volume = market.total_volume + amount;
        let (new_yes_price, new_no_price) = self.calculate_new_prices(
            market.yes_price,
            market.no_price,
            &position_type,
            amount,
            market.total_volume,
        );

        conn.execute(
            "UPDATE markets SET total_volume = ?1, yes_price = ?2, no_price = ?3 WHERE id = ?4",
            params![new_volume, new_yes_price, new_no_price, &market_id],
        )
        .map_err(|e| e.to_string())?;

        Ok(position)
    }

    /// Get positions for a user
    pub fn get_user_positions(&self, user_address: &str) -> Result<Vec<Position>, String> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn
            .prepare("SELECT id, market_id, user_address, position_type, amount, entry_price, created_at FROM positions WHERE user_address = ?1 ORDER BY created_at DESC")
            .map_err(|e| e.to_string())?;

        let positions_iter = stmt
            .query_map(params![user_address], |row| {
                Ok(Position {
                    id: row.get(0)?,
                    market_id: row.get(1)?,
                    user_address: row.get(2)?,
                    position_type: row.get(3)?,
                    amount: row.get(4)?,
                    entry_price: row.get(5)?,
                    created_at: row.get(6)?,
                })
            })
            .map_err(|e| e.to_string())?;

        let mut positions = Vec::new();
        for position in positions_iter {
            positions.push(position.map_err(|e| e.to_string())?);
        }
        Ok(positions)
    }

    /// Simple constant product market maker pricing
    fn calculate_new_prices(
        &self,
        yes_price: f64,
        no_price: f64,
        position_type: &str,
        amount: f64,
        total_volume: f64,
    ) -> (f64, f64) {
        let liquidity = total_volume.max(1000.0); // Minimum liquidity
        let impact = amount / liquidity * 0.1; // 10% price impact per liquidity ratio

        if position_type == "yes" {
            let new_yes = (yes_price + impact).min(0.99);
            let new_no = (1.0 - new_yes).max(0.01);
            (new_yes, new_no)
        } else {
            let new_no = (no_price + impact).min(0.99);
            let new_yes = (1.0 - new_no).max(0.01);
            (new_yes, new_no)
        }
    }

    /// Resolve a market
    pub fn resolve_market(&self, market_id: &str, resolution: bool) -> Result<Market, String> {
        let resolved_at = chrono::Utc::now().timestamp();

        let conn = self.conn.lock().unwrap();
        conn.execute(
            "UPDATE markets SET status = 'resolved', resolution = ?1, resolved_at = ?2 WHERE id = ?3",
            params![resolution, resolved_at, market_id],
        )
        .map_err(|e| e.to_string())?;

        drop(conn);
        self.get_market(market_id)
    }
}
