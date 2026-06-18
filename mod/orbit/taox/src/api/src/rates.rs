use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use parking_lot::Mutex;
use serde_json::Value;

use crate::config::Config;
use crate::types::RatesView;

const CG_PRICE_URL: &str = "https://api.coingecko.com/api/v3/simple/price";
const TTL_SECS: i64 = 60;

/// On-disk + in-memory cache for CoinGecko's free public price endpoint.
/// No API key required; cached for 60s to stay polite.
pub struct RatesCache {
    config: Arc<Config>,
    path: PathBuf,
    inner: Mutex<Option<RatesView>>,
}

impl RatesCache {
    pub fn new(config: Arc<Config>, dir: &Path) -> Self {
        let path = dir.join("rates_cache.json");
        let inner = std::fs::read_to_string(&path)
            .ok()
            .and_then(|t| serde_json::from_str::<RatesView>(&t).ok());
        RatesCache { config, path, inner: Mutex::new(inner) }
    }

    pub fn snapshot(&self) -> Option<RatesView> {
        self.inner.lock().clone()
    }

    pub async fn get(&self, http: &reqwest::Client, refresh: bool) -> anyhow::Result<RatesView> {
        if !refresh {
            if let Some(v) = self.snapshot() {
                if now() - v.ts < TTL_SECS {
                    return Ok(v);
                }
            }
        }

        let mut ids: BTreeSet<String> = self
            .config
            .sources
            .values()
            .map(|s| s.coingecko_id.clone())
            .filter(|s| !s.is_empty())
            .collect();
        ids.insert(self.config.destination.coingecko_id.clone());

        let ids_param = ids.iter().cloned().collect::<Vec<_>>().join(",");
        let url = format!("{CG_PRICE_URL}?ids={ids_param}&vs_currencies=usd");

        let fetched = http.get(&url).send().await.and_then(|r| r.error_for_status());
        let data: Value = match fetched {
            Ok(resp) => match resp.json().await {
                Ok(v) => v,
                Err(e) => return self.fallback(format!("decode: {e}")),
            },
            Err(e) => return self.fallback(format!("fetch: {e}")),
        };

        let mut usd: BTreeMap<String, f64> = BTreeMap::new();
        if let Some(obj) = data.as_object() {
            for (k, v) in obj {
                if let Some(p) = v.get("usd").and_then(|x| x.as_f64()) {
                    usd.insert(k.clone(), p);
                }
            }
        }

        let tao_usd = usd
            .get(&self.config.destination.coingecko_id)
            .copied()
            .unwrap_or(0.0);

        let mut pairs: BTreeMap<String, f64> = BTreeMap::new();
        for (sym, cfg) in &self.config.sources {
            let src_usd = usd.get(&cfg.coingecko_id).copied().unwrap_or(0.0);
            let rate = if tao_usd > 0.0 { src_usd / tao_usd } else { 0.0 };
            pairs.insert(format!("{sym}_to_tao"), rate);
        }
        // TAO/USDT used as the reference for prediction settlement.
        let usdt_usd = usd.get("tether").copied().unwrap_or(0.0);
        if tao_usd > 0.0 && usdt_usd > 0.0 {
            pairs.insert("tao_to_usdt".into(), tao_usd / usdt_usd);
        }

        let view = RatesView { ts: now(), usd, pairs, stale: false };

        *self.inner.lock() = Some(view.clone());
        let _ = self.persist(&view);
        Ok(view)
    }

    fn persist(&self, view: &RatesView) -> anyhow::Result<()> {
        let body = serde_json::to_string_pretty(view)?;
        let tmp = self.path.with_extension("json.tmp");
        std::fs::write(&tmp, body)?;
        std::fs::rename(&tmp, &self.path)?;
        Ok(())
    }

    fn fallback(&self, why: String) -> anyhow::Result<RatesView> {
        tracing::warn!("rates fetch failed: {why}");
        if let Some(mut v) = self.snapshot() {
            v.stale = true;
            return Ok(v);
        }
        anyhow::bail!("rates unavailable: {why}")
    }
}

fn now() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}
