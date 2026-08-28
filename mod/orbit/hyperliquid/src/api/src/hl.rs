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
                // HL's stats CDN routinely takes 20-30s under load — the
                // earlier 20s ceiling caused prewarm to consistently return
                // 0 traders. 60s is long enough to absorb the worst-case
                // latency without holding handlers open indefinitely.
                .timeout(std::time::Duration::from_secs(60))
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
    pub fn cache_evict_prefix(&self, prefix: &str) {
        self.cache.retain(|k, _| !k.starts_with(prefix));
    }

    pub async fn info(&self, body: Value) -> anyhow::Result<Value> {
        // Hyperliquid's /info bursts to 429 under load; back off and retry.
        // Bigger retry budget so a single 429 doesn't drop a candidate
        // from the cohort. 8 attempts × backoff up to 4s = ~30s worst case.
        let mut delay_ms = 250u64;
        for attempt in 0..8 {
            let r = self.http.post(&self.info_url).json(&body).send().await?;
            let status = r.status();
            let txt = r.text().await?;
            if status.is_success() {
                return Ok(serde_json::from_str(&txt).unwrap_or(Value::Null));
            }
            if status.as_u16() == 429 && attempt < 7 {
                tokio::time::sleep(Duration::from_millis(delay_ms)).await;
                delay_ms = (delay_ms * 2).min(4_000);
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
        // TTL must outlast one full prewarm cycle (3 windows × ~2min each),
        // otherwise entries expire before the next window reuses them and
        // every cycle re-triggers the 429 storm it exists to prevent.
        let key = format!("fills:{addr}");
        if let Some(v) = self.cache_get(&key, Duration::from_secs(1500)) {
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
        // "userHistoricalPnl" is not a real HL info type (the node rejects it);
        // "portfolio" is the supported source of equity/PnL curves. It returns
        // [[period, { accountValueHistory, pnlHistory, vlm }], ...] for
        // day/week/month/allTime (+ perp-only variants).
        let key = format!("portfolio:{addr}");
        if let Some(v) = self.cache_get(&key, Duration::from_secs(300)) {
            return Ok(v);
        }
        let v = self.info(json!({"type": "portfolio", "user": addr})).await?;
        self.cache_put(key, v.clone());
        Ok(v)
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
        // The /info "vaults" type was retired (422s now); the public web UI
        // pulls the full vault universe from the same stats CDN as the
        // leaderboard. Each entry is { apr, pnls, summary:{ name, vaultAddress,
        // leader, tvl, isClosed, relationship, createTimeMillis } }.
        if let Some(v) = self.cache_get("vaults", Duration::from_secs(60)) {
            return Ok(v);
        }
        let url = self.stats_url.replace("leaderboard", "vaults");
        let r = self.http.get(&url).send().await?;
        let status = r.status();
        let txt = r.text().await?;
        if !status.is_success() {
            anyhow::bail!("vaults {} {}", status, txt);
        }
        let v: Value = serde_json::from_str(&txt).unwrap_or(Value::Null);
        self.cache_put("vaults".into(), v.clone());
        Ok(v)
    }

    pub async fn vault_details(&self, addr: &str, user: Option<&str>) -> anyhow::Result<Value> {
        // Passing `user` populates followerState / maxWithdrawable for that
        // depositor, which the invest page needs to show "your position".
        // Cached briefly (key includes the user, since followerState differs);
        // vault_transfer evicts by vault prefix so a fresh deposit shows up.
        let key = format!("vaultDetails:{}:{}", addr.to_lowercase(), user.unwrap_or("-").to_lowercase());
        if let Some(v) = self.cache_get(&key, Duration::from_secs(45)) {
            return Ok(v);
        }
        let mut body = json!({"type": "vaultDetails", "vaultAddress": addr});
        if let Some(u) = user {
            body.as_object_mut().unwrap().insert("user".into(), Value::String(u.to_string()));
        }
        let v = self.info(body).await?;
        self.cache_put(key, v.clone());
        Ok(v)
    }

    pub async fn vault_pnl(&self, addr: &str) -> anyhow::Result<Value> {
        self.info(json!({"type": "vaultHistoricalPnl", "vaultAddress": addr})).await
    }

    pub async fn extra_agents(&self, addr: &str) -> anyhow::Result<Value> {
        // Agent wallets the user has approved via `approveAgent` —
        // [{address, name?, validUntil}]. Lets the UI show whether our
        // backend agent is already authorized before offering to sign.
        self.info(json!({"type": "extraAgents", "user": addr})).await
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
