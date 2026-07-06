use sha2::{Digest, Sha256};
use std::sync::Arc;

use crate::{config::Config, ratelimit::RateLimiter, store::Store};

pub type AppState = Arc<SharedState>;

pub struct SharedState {
    pub config: Config,
    pub store: Store,
    pub rate_limiter: RateLimiter,
    pub snapshot_audit: SnapshotAudit,
}

#[derive(Debug, Clone)]
pub struct SnapshotAudit {
    /// SHA-256 hex of the snapshot file contents — content-addressed identity
    /// for auditability. Anyone can re-hash the on-disk file and verify.
    pub cid: String,
    /// Unix seconds — file mtime (when the snapshot was last written).
    pub updated_at: i64,
    /// Byte size of the snapshot file as written.
    pub bytes: u64,
}

impl SnapshotAudit {
    fn from_path(path: &std::path::Path) -> Self {
        let (cid, bytes) = std::fs::read(path)
            .map(|raw| {
                let n = raw.len() as u64;
                let mut h = Sha256::new();
                h.update(&raw);
                (hex::encode(h.finalize()), n)
            })
            .unwrap_or_default();
        let updated_at = std::fs::metadata(path)
            .and_then(|m| m.modified())
            .ok()
            .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
            .map(|d| d.as_secs() as i64)
            .unwrap_or(0);
        Self { cid, updated_at, bytes }
    }
}

impl SharedState {
    pub fn new(config: Config) -> anyhow::Result<Self> {
        let store = Store::open(
            &config.snapshot_path(),
            config.claims_path(),
            config.commitments_path(),
            config.used_sigs_path(),
        )?;
        let rate_limiter = RateLimiter::new(60, 600);
        let snapshot_audit = SnapshotAudit::from_path(&config.snapshot_path());
        Ok(Self {
            config,
            store,
            rate_limiter,
            snapshot_audit,
        })
    }
}
