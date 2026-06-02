// Thin async client over the Hyperliquid public Info / Exchange endpoints.

use dashmap::DashMap;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::time::{Duration, Instant};

pub struct Client {
    http: reqwest::Client,
    pub info_url: String,
    pub exchange_url: String,
    pub stats_url: String,
    pub testnet: bool,
    cache: DashMap<String, (Instant, Value)>,
}

impl Client {
    pub fn new(testnet: bool) -> Self {
        let base = if testnet {
            "https://api.hyperliquid-testnet.xyz"
        } else {
            "https://api.hyperliquid.xyz"
        };
        let stats_net = if testnet { "Testnet" } else { "Mainnet" };
        Self {
            http: reqwest::Client::builder()
                .timeout(std::time::Duration::from_secs(20))
                .build()
                .expect("http client"),
            info_url: format!("{base}/info"),
            exchange_url: format!("{base}/exchange"),
            stats_url: format!("https://stats-data.hyperliquid.xyz/{stats_net}/leaderboard"),
            testnet,
            cache: DashMap::new(),
        }
    }

    fn cache_get(&self, key: &str, ttl: Duration) -> Option<Value> {
        let e = self.cache.get(key)?;
        if e.0.elapsed() < ttl { Some(e.1.clone()) } else { None }
    }
    fn cache_put(&self, key: String, v: Value) {
        self.cache.insert(key, (Instant::now(), v));
    }

    pub async fn info(&self, body: Value) -> anyhow::Result<Value> {
        // Hyperliquid's /info bursts to 429 under load; back off and retry.
        let mut delay_ms = 250u64;
        for attempt in 0..5 {
            let r = self.http.post(&self.info_url).json(&body).send().await?;
            let status = r.status();
            let txt = r.text().await?;
            if status.is_success() {
                return Ok(serde_json::from_str(&txt).unwrap_or(Value::Null));
            }
            if status.as_u16() == 429 && attempt < 4 {
                tokio::time::sleep(Duration::from_millis(delay_ms)).await;
                delay_ms = (delay_ms * 2).min(2_000);
                continue;
            }
            anyhow::bail!("info {} {}", status, txt);
        }
        unreachable!()
    }

    pub async fn all_mids(&self) -> anyhow::Result<Value> {
        self.info(json!({"type": "allMids"})).await
    }

    pub async fn meta_and_ctxs(&self) -> anyhow::Result<Value> {
        self.info(json!({"type": "metaAndAssetCtxs"})).await
    }

    pub async fn l2_book(&self, coin: &str) -> anyhow::Result<Value> {
        self.info(json!({"type": "l2Book", "coin": coin})).await
    }

    pub async fn candles(&self, coin: &str, interval: &str, start: i64, end: i64) -> anyhow::Result<Value> {
        self.info(json!({
            "type": "candleSnapshot",
            "req": {"coin": coin, "interval": interval, "startTime": start, "endTime": end}
        })).await
    }

    pub async fn user_state(&self, addr: &str) -> anyhow::Result<Value> {
        self.info(json!({"type": "clearinghouseState", "user": addr})).await
    }

    pub async fn user_fills(&self, addr: &str) -> anyhow::Result<Value> {
        self.info(json!({"type": "userFills", "user": addr})).await
    }

    pub async fn user_fills_by_time(&self, addr: &str, start_ms: i64) -> anyhow::Result<Value> {
        // Cache the last ~31 days of fills per address so window-toggles in
        // the UI hit cache instead of triggering a fresh scan + 429 storm.
        // score_fills filters by cutoff in memory, so an over-long fetch is
        // fine — we just want enough history to cover the longest UI window.
        let key = format!("fills:{addr}");
        if let Some(v) = self.cache_get(&key, Duration::from_secs(300)) {
            return Ok(v);
        }
        let now = chrono::Utc::now().timestamp_millis();
        let fetch_start = start_ms.min(now - 31 * 86_400_000);
        let v = self.info(json!({
            "type": "userFillsByTime",
            "user": addr,
            "startTime": fetch_start
        })).await?;
        self.cache_put(key, v.clone());
        Ok(v)
    }

    pub async fn user_pnl(&self, addr: &str) -> anyhow::Result<Value> {
        self.info(json!({"type": "userHistoricalPnl", "user": addr})).await
    }

    pub async fn user_funding(&self, addr: &str) -> anyhow::Result<Value> {
        self.info(json!({"type": "userFunding", "user": addr})).await
    }

    pub async fn open_orders(&self, addr: &str) -> anyhow::Result<Value> {
        self.info(json!({"type": "openOrders", "user": addr})).await
    }

    pub async fn leaderboard(&self) -> anyhow::Result<Value> {
        // The /info "leaderboard" type was retired; the public web UI
        // pulls from a stats CDN that returns {"leaderboardRows": [...]}.
        if let Some(v) = self.cache_get("leaderboard", Duration::from_secs(60)) {
            return Ok(v);
        }
        let r = self.http.get(&self.stats_url).send().await?;
        let status = r.status();
        let txt = r.text().await?;
        if !status.is_success() {
            anyhow::bail!("leaderboard {} {}", status, txt);
        }
        let v: Value = serde_json::from_str(&txt).unwrap_or(Value::Null);
        self.cache_put("leaderboard".into(), v.clone());
        Ok(v)
    }

    pub async fn vaults(&self) -> anyhow::Result<Value> {
        self.info(json!({"type": "vaults"})).await
    }

    pub async fn vault_details(&self, addr: &str) -> anyhow::Result<Value> {
        self.info(json!({"type": "vaultDetails", "vaultAddress": addr})).await
    }

    pub async fn vault_pnl(&self, addr: &str) -> anyhow::Result<Value> {
        self.info(json!({"type": "vaultHistoricalPnl", "vaultAddress": addr})).await
    }
}

// ── Shared trade/fill type ──────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Fill {
    pub coin: String,
    #[serde(default)]
    pub side: String,        // "B" | "A"
    #[serde(default)]
    pub px: String,
    #[serde(default)]
    pub sz: String,
    #[serde(default)]
    pub time: i64,
    #[serde(default, rename = "closedPnl")]
    pub closed_pnl: String,
    #[serde(default)]
    pub fee: String,
    #[serde(default, rename = "tid")]
    pub tid: u64,
    #[serde(default, rename = "oid")]
    pub oid: u64,
}

pub fn parse_fills(v: &Value) -> Vec<Fill> {
    serde_json::from_value::<Vec<Fill>>(v.clone()).unwrap_or_default()
}
