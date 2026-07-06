// Background copy-trade engine.
//
// Owns nothing on-chain — it polls the leaders' fills and emits copy
// "signals" (intent records). Actual order signing/submission happens
// via the /forward exchange endpoint, where the caller supplies the
// already-signed payload. This keeps the Rust binary keyless.

use crate::hl::{parse_fills, Client};
use crate::store::Store;
use parking_lot::RwLock;
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;
use std::sync::Arc;
use std::time::Duration;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Signal {
    pub id: String,
    pub follow_id: String,
    pub follower: String,
    pub leader: String,
    pub coin: String,
    pub side: String,            // "B" | "A"
    pub leader_px: f64,
    pub leader_sz: f64,
    pub copy_sz: f64,            // size scaled by follow.size_pct
    pub leader_tid: u64,
    pub ts_ms: i64,
    #[serde(default)]
    pub vault_address: Option<String>,
    #[serde(default)]
    pub status: String,          // "pending" | "executed" | "skipped"
}

pub struct Engine {
    hl: Arc<Client>,
    store: Arc<Store>,
    signals: RwLock<VecDeque<Signal>>,  // ring buffer
}

impl Engine {
    pub fn new(hl: Arc<Client>, store: Arc<Store>) -> Self {
        Self { hl, store, signals: RwLock::new(VecDeque::with_capacity(2000)) }
    }

    pub fn recent_signals(&self, follower: Option<&str>, limit: usize) -> Vec<Signal> {
        let g = self.signals.read();
        g.iter().rev()
            .filter(|s| follower.map_or(true, |f| s.follower.eq_ignore_ascii_case(f)))
            .take(limit)
            .cloned()
            .collect()
    }

    fn push_signal(&self, s: Signal) {
        let mut g = self.signals.write();
        if g.len() >= 2000 { g.pop_front(); }
        g.push_back(s);
    }

    pub fn mark_signal(&self, id: &str, status: &str) {
        let mut g = self.signals.write();
        if let Some(s) = g.iter_mut().find(|x| x.id == id) {
            s.status = status.to_string();
        }
    }

    pub async fn run(self: Arc<Self>) {
        let interval = std::env::var("COPY_POLL_MS").ok()
            .and_then(|s| s.parse::<u64>().ok()).unwrap_or(5000);
        let mut tick = tokio::time::interval(Duration::from_millis(interval));
        tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        loop {
            tick.tick().await;
            let follows = self.store.list_follows(None);
            for f in follows {
                if f.paused { continue; }
                if let Err(e) = self.poll_leader(&f).await {
                    tracing::warn!("poll {} -> {} failed: {e}", f.leader, f.follower);
                }
            }
        }
    }

    async fn poll_leader(&self, f: &crate::store::Follow) -> anyhow::Result<()> {
        // Fills since 5min ago — fast and small. We dedupe with last_seen_tid.
        let since = chrono::Utc::now().timestamp_millis() - 5 * 60_000;
        let v = self.hl.user_fills_by_time(&f.leader, since).await?;
        let fills = parse_fills(&v);
        if fills.is_empty() { return Ok(()); }

        let mut max_tid = f.last_seen_tid;
        for fill in fills.iter().rev() {              // chronological
            if fill.tid <= f.last_seen_tid { continue; }
            if !f.coins_allow.is_empty()
                && !f.coins_allow.iter().any(|c| c.eq_ignore_ascii_case(&fill.coin)) {
                continue;
            }
            if f.coins_deny.iter().any(|c| c.eq_ignore_ascii_case(&fill.coin)) {
                continue;
            }
            let leader_sz: f64 = fill.sz.parse().unwrap_or(0.0);
            let leader_px: f64 = fill.px.parse().unwrap_or(0.0);
            if leader_sz <= 0.0 || leader_px <= 0.0 { continue; }
            let mut copy_sz = leader_sz * (f.size_pct / 100.0);
            let notional = copy_sz * leader_px;
            if f.max_per_trade_usd > 0.0 && notional > f.max_per_trade_usd {
                copy_sz = f.max_per_trade_usd / leader_px;
            }
            let sig = Signal {
                id: uuid::Uuid::new_v4().to_string(),
                follow_id: f.id.clone(),
                follower: f.follower.clone(),
                leader: f.leader.clone(),
                coin: fill.coin.clone(),
                side: fill.side.clone(),
                leader_px, leader_sz, copy_sz,
                leader_tid: fill.tid,
                ts_ms: fill.time,
                vault_address: f.vault_address.clone(),
                status: "pending".into(),
            };
            self.push_signal(sig);
            if fill.tid > max_tid { max_tid = fill.tid; }
        }
        if max_tid > f.last_seen_tid {
            self.store.update_follow_cursor(&f.id, max_tid)?;
        }
        Ok(())
    }
}
