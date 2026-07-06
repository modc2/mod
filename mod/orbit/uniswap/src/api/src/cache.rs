use crate::models::trader::TraderResult;

/// SQLite-backed disk cache for trader results
#[derive(Clone)]
pub struct DiskCache {
    path: String,
}

impl DiskCache {
    pub fn new(path: &str) -> Self {
        // Ensure directory exists
        if let Some(parent) = std::path::Path::new(path).parent() {
            let _ = std::fs::create_dir_all(parent);
        }

        // Initialize DB
        if let Ok(conn) = rusqlite::Connection::open(path) {
            let _ = conn.execute_batch(
                "CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );",
            );
        }

        Self {
            path: path.to_string(),
        }
    }

    pub fn get(&self, key: &str, ttl_secs: i64) -> Option<Vec<TraderResult>> {
        let conn = rusqlite::Connection::open(&self.path).ok()?;
        let now = chrono::Utc::now().timestamp();

        let mut stmt = conn
            .prepare("SELECT data, created_at FROM cache WHERE key = ?1")
            .ok()?;

        let result: Option<(String, i64)> = stmt
            .query_row(rusqlite::params![key], |row| {
                Ok((row.get(0)?, row.get(1)?))
            })
            .ok();

        match result {
            Some((data, created_at)) if now - created_at < ttl_secs => {
                serde_json::from_str(&data).ok()
            }
            Some(_) => {
                // Expired — clean up
                let _ = conn.execute("DELETE FROM cache WHERE key = ?1", rusqlite::params![key]);
                None
            }
            None => None,
        }
    }

    pub fn set(&self, key: &str, data: &[TraderResult]) {
        let conn = match rusqlite::Connection::open(&self.path) {
            Ok(c) => c,
            Err(_) => return,
        };

        let json = match serde_json::to_string(data) {
            Ok(j) => j,
            Err(_) => return,
        };

        let now = chrono::Utc::now().timestamp();

        let _ = conn.execute(
            "INSERT OR REPLACE INTO cache (key, data, created_at) VALUES (?1, ?2, ?3)",
            rusqlite::params![key, json, now],
        );
    }
}
