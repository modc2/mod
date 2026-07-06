use crate::records::{DnsRecord, RecordType};
use sled::Db;
use std::collections::HashMap;
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use tracing::info;

/// Sled-backed DNS record store with CRDT (last-write-wins) merge semantics.
pub struct Store {
    db: Db,
    clock: AtomicU64,
    pub node_id: String,
}

impl Store {
    pub fn open(data_dir: &Path, node_id: String) -> Result<Arc<Self>, sled::Error> {
        let db = sled::open(data_dir.join("records"))?;

        // Recover clock from existing records
        let mut max_ts = 0u64;
        for zone_tree_name in db.tree_names() {
            let name = String::from_utf8_lossy(&zone_tree_name);
            if name == "__sled__default" {
                continue;
            }
            let tree = db.open_tree(&zone_tree_name)?;
            for entry in tree.iter() {
                if let Ok((_, val)) = entry {
                    if let Ok(rec) = serde_json::from_slice::<DnsRecord>(&val) {
                        max_ts = max_ts.max(rec.timestamp);
                    }
                }
            }
        }

        Ok(Arc::new(Self {
            db,
            clock: AtomicU64::new(max_ts),
            node_id,
        }))
    }

    fn next_timestamp(&self) -> u64 {
        self.clock.fetch_add(1, Ordering::SeqCst) + 1
    }

    /// Advance the clock if a remote timestamp is ahead of ours.
    fn advance_clock(&self, remote_ts: u64) {
        let _ = self
            .clock
            .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |current| {
                if remote_ts > current {
                    Some(remote_ts)
                } else {
                    None
                }
            });
    }

    fn zone_tree(&self, zone: &str) -> Result<sled::Tree, sled::Error> {
        self.db.open_tree(format!("zone:{}", zone.to_lowercase()))
    }

    /// Put a record (local write). Assigns a new timestamp.
    pub fn put(&self, zone: &str, mut record: DnsRecord) -> Result<DnsRecord, String> {
        record.validate()?;
        record.timestamp = self.next_timestamp();
        record.node_id = self.node_id.clone();

        let tree = self.zone_tree(zone).map_err(|e| e.to_string())?;
        let key = record.key();
        let val = serde_json::to_vec(&record).map_err(|e| e.to_string())?;
        tree.insert(key.as_bytes(), val)
            .map_err(|e| e.to_string())?;

        info!(zone, name = %record.name, rtype = %record.rtype, "record stored");
        Ok(record)
    }

    /// Merge a record from a remote peer. Only accepts if newer (LWW).
    /// Returns true if the record was accepted.
    pub fn merge(&self, zone: &str, record: &DnsRecord) -> Result<bool, String> {
        self.advance_clock(record.timestamp);
        let tree = self.zone_tree(zone).map_err(|e| e.to_string())?;
        let key = record.key();

        // Check existing
        if let Some(existing_bytes) = tree.get(key.as_bytes()).map_err(|e| e.to_string())? {
            let existing: DnsRecord =
                serde_json::from_slice(&existing_bytes).map_err(|e| e.to_string())?;
            if !record.is_newer_than(&existing) {
                return Ok(false); // ours is newer, ignore
            }
        }

        let val = serde_json::to_vec(record).map_err(|e| e.to_string())?;
        tree.insert(key.as_bytes(), val)
            .map_err(|e| e.to_string())?;
        info!(zone, name = %record.name, rtype = %record.rtype, ts = record.timestamp, "merged remote record");
        Ok(true)
    }

    /// Get a specific record.
    pub fn get(&self, zone: &str, name: &str, rtype: RecordType) -> Result<Option<DnsRecord>, String> {
        let tree = self.zone_tree(zone).map_err(|e| e.to_string())?;
        let key = format!("{}:{}", name.to_lowercase(), rtype);

        match tree.get(key.as_bytes()).map_err(|e| e.to_string())? {
            Some(bytes) => {
                let rec: DnsRecord =
                    serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
                if rec.deleted {
                    Ok(None)
                } else {
                    Ok(Some(rec))
                }
            }
            None => Ok(None),
        }
    }

    /// Get all records matching a name (any type).
    pub fn get_name(&self, zone: &str, name: &str) -> Result<Vec<DnsRecord>, String> {
        let tree = self.zone_tree(zone).map_err(|e| e.to_string())?;
        let prefix = format!("{}:", name.to_lowercase());
        let mut results = Vec::new();

        for entry in tree.scan_prefix(prefix.as_bytes()) {
            let (_, val) = entry.map_err(|e| e.to_string())?;
            let rec: DnsRecord = serde_json::from_slice(&val).map_err(|e| e.to_string())?;
            if !rec.deleted {
                results.push(rec);
            }
        }
        Ok(results)
    }

    /// Soft-delete a record.
    pub fn delete(&self, zone: &str, name: &str, rtype: RecordType) -> Result<Option<DnsRecord>, String> {
        let tree = self.zone_tree(zone).map_err(|e| e.to_string())?;
        let key = format!("{}:{}", name.to_lowercase(), rtype);

        match tree.get(key.as_bytes()).map_err(|e| e.to_string())? {
            Some(bytes) => {
                let mut rec: DnsRecord =
                    serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
                rec.deleted = true;
                rec.timestamp = self.next_timestamp();
                rec.node_id = self.node_id.clone();
                let val = serde_json::to_vec(&rec).map_err(|e| e.to_string())?;
                tree.insert(key.as_bytes(), val)
                    .map_err(|e| e.to_string())?;
                info!(zone, name, %rtype, "record deleted");
                Ok(Some(rec))
            }
            None => Ok(None),
        }
    }

    /// List all live records in a zone.
    pub fn list(&self, zone: &str) -> Result<Vec<DnsRecord>, String> {
        let tree = self.zone_tree(zone).map_err(|e| e.to_string())?;
        let mut records = Vec::new();

        for entry in tree.iter() {
            let (_, val) = entry.map_err(|e| e.to_string())?;
            let rec: DnsRecord = serde_json::from_slice(&val).map_err(|e| e.to_string())?;
            if !rec.deleted {
                records.push(rec);
            }
        }
        Ok(records)
    }

    /// List all zones.
    pub fn zones(&self) -> Vec<String> {
        self.db
            .tree_names()
            .into_iter()
            .filter_map(|name| {
                let s = String::from_utf8_lossy(&name).to_string();
                s.strip_prefix("zone:").map(|z| z.to_string())
            })
            .collect()
    }

    /// Export all records in a zone (including deleted, for full sync).
    pub fn export(&self, zone: &str) -> Result<Vec<DnsRecord>, String> {
        let tree = self.zone_tree(zone).map_err(|e| e.to_string())?;
        let mut records = Vec::new();
        for entry in tree.iter() {
            let (_, val) = entry.map_err(|e| e.to_string())?;
            let rec: DnsRecord = serde_json::from_slice(&val).map_err(|e| e.to_string())?;
            records.push(rec);
        }
        Ok(records)
    }

    /// Get stats about the store.
    pub fn stats(&self) -> HashMap<String, serde_json::Value> {
        let zones = self.zones();
        let mut total_records = 0usize;
        for z in &zones {
            if let Ok(recs) = self.list(z) {
                total_records += recs.len();
            }
        }
        let mut map = HashMap::new();
        map.insert(
            "zones".into(),
            serde_json::Value::Number(zones.len().into()),
        );
        map.insert(
            "records".into(),
            serde_json::Value::Number(total_records.into()),
        );
        map.insert(
            "clock".into(),
            serde_json::Value::Number(self.clock.load(Ordering::SeqCst).into()),
        );
        map.insert(
            "node_id".into(),
            serde_json::Value::String(self.node_id.clone()),
        );
        map
    }

    /// Check if a zone has any records (used to decide if we're authoritative).
    pub fn has_zone(&self, zone: &str) -> bool {
        if let Ok(tree) = self.zone_tree(zone) {
            tree.iter().next().is_some()
        } else {
            false
        }
    }
}
