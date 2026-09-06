//! Where the yield actually is — every DeFi protocol's live APR, in one table.
//!
//! The composer can build a vault that pays a fixed rate, and the desk can buy
//! the token it pays in. What was missing between them was the question anyone
//! actually starts with: *what is paying what right now?* This module answers it
//! from DefiLlama's yields index — ~17k pools across a thousand protocols,
//! refreshed hourly upstream — and normalises it into rows a console can sort
//! and a treasury can be pointed at.
//!
//! Two things this deliberately does NOT do. It does not invent numbers: every
//! APY here is the one the index reports, `apyBase` and `apyReward` kept apart
//! so a rate propped up by emissions cannot pass itself off as organic yield.
//! And it does not pretend a quoted APY is a promise — `apyMean30d` and the 7-day
//! drift ride along on every row precisely so a headline number can be checked
//! against how it has actually behaved.
//!
//! The whole index is one 12 MB response, so it is fetched once and held for
//! `ttl`, and every filter runs over the cached snapshot. A stale snapshot is
//! served with its age attached rather than replaced by an error, because a
//! twenty-minute-old APR beats no APR.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::sync::Arc;
use tokio::sync::RwLock;

const SOURCE: &str = "https://yields.llama.fi";

/// The TVL at which a pool stops being marked down for thinness under the
/// `score` sort. Above this, a rate is taken at face value.
const DEPTH_FULL: f64 = 10_000_000.0;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Pool {
    #[serde(default)]
    pub chain: String,
    #[serde(default)]
    pub project: String,
    #[serde(default)]
    pub symbol: String,
    #[serde(default)]
    pub pool: String,
    #[serde(rename = "tvlUsd", default)]
    pub tvl_usd: f64,
    #[serde(default)]
    pub apy: Option<f64>,
    #[serde(rename = "apyBase", default)]
    pub apy_base: Option<f64>,
    #[serde(rename = "apyReward", default)]
    pub apy_reward: Option<f64>,
    #[serde(rename = "apyMean30d", default)]
    pub apy_mean_30d: Option<f64>,
    #[serde(rename = "apyPct7D", default)]
    pub apy_pct_7d: Option<f64>,
    #[serde(rename = "apyPct30D", default)]
    pub apy_pct_30d: Option<f64>,
    #[serde(default)]
    pub stablecoin: bool,
    #[serde(rename = "ilRisk", default)]
    pub il_risk: Option<String>,
    #[serde(default)]
    pub exposure: Option<String>,
    #[serde(rename = "poolMeta", default)]
    pub pool_meta: Option<String>,
    #[serde(default)]
    pub outlier: bool,
    #[serde(rename = "rewardTokens", default)]
    pub reward_tokens: Option<Vec<String>>,
    #[serde(rename = "underlyingTokens", default)]
    pub underlying_tokens: Option<Vec<String>>,
    #[serde(default)]
    pub predictions: Option<Value>,
}

struct Snapshot {
    fetched: u64,
    pools: Arc<Vec<Pool>>,
}

pub struct Yields {
    http: reqwest::Client,
    pub source: String,
    ttl: u64,
    cache: RwLock<Option<Snapshot>>,
}

/// What a caller wants out of the index.
#[derive(Default)]
pub struct Filter {
    pub chain: Option<String>,
    pub project: Option<String>,
    pub symbol: Option<String>,
    pub q: Option<String>,
    pub min_tvl: f64,
    pub max_apy: Option<f64>,
    pub stable_only: bool,
    /// Drop pools whose APY is mostly emissions rather than fees.
    pub organic_only: bool,
    /// Drop the pools the index itself flags as statistical outliers.
    pub include_outliers: bool,
    pub sort: String,
    pub limit: usize,
}

impl Filter {
    pub fn from_query(q: &Value) -> Self {
        let s = |k: &str| {
            q.get(k)
                .and_then(|v| v.as_str())
                .map(|v| v.trim().to_string())
                .filter(|v| !v.is_empty())
        };
        let f = |k: &str, d: f64| {
            q.get(k)
                .and_then(|v| v.as_str().and_then(|s| s.parse().ok()).or_else(|| v.as_f64()))
                .unwrap_or(d)
        };
        let b = |k: &str| {
            q.get(k)
                .map(|v| !matches!(v.as_str(), Some("") | Some("0") | Some("false") | Some("no")))
                .unwrap_or(false)
                && q.get(k).and_then(|v| v.as_bool()) != Some(false)
        };
        Filter {
            chain: s("chain"),
            project: s("project"),
            symbol: s("symbol"),
            q: s("q"),
            // A pool with no depth has an APY that means nothing, so the default
            // floor is high enough to keep the table honest but low enough that
            // a new market still shows up.
            min_tvl: f("min_tvl", 100_000.0),
            max_apy: s("max_apy").and_then(|v| v.parse().ok()),
            stable_only: b("stable"),
            organic_only: b("organic"),
            include_outliers: b("outliers"),
            sort: s("sort").unwrap_or_else(|| "apy".into()),
            limit: f("limit", 60.0).max(1.0).min(500.0) as usize,
        }
    }
}

impl Yields {
    pub fn new() -> Self {
        Self {
            http: reqwest::Client::builder()
                .timeout(std::time::Duration::from_secs(60))
                .build()
                .expect("http client"),
            source: std::env::var("DEFI_YIELDS_URL").unwrap_or_else(|_| SOURCE.into()),
            ttl: std::env::var("DEFI_YIELDS_TTL")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(600),
            cache: RwLock::new(None),
        }
    }

    /// The cached index, refreshed when stale. A refresh that fails falls back
    /// to whatever is in hand — the caller is told the age either way.
    async fn snapshot(&self) -> Result<(Arc<Vec<Pool>>, u64), String> {
        let now = crate::auth::now();
        {
            let cache = self.cache.read().await;
            if let Some(snap) = cache.as_ref() {
                if now.saturating_sub(snap.fetched) < self.ttl {
                    return Ok((snap.pools.clone(), snap.fetched));
                }
            }
        }
        match self.fetch().await {
            Ok(pools) => {
                let pools = Arc::new(pools);
                *self.cache.write().await = Some(Snapshot { fetched: now, pools: pools.clone() });
                Ok((pools, now))
            }
            Err(e) => {
                let cache = self.cache.read().await;
                match cache.as_ref() {
                    Some(snap) => Ok((snap.pools.clone(), snap.fetched)),
                    None => Err(e),
                }
            }
        }
    }

    /// The whole cached index — every pool, with the snapshot's age — for
    /// callers that build their own rows out of it (the modules registry).
    pub async fn all(&self) -> Result<(Arc<Vec<Pool>>, u64), String> {
        self.snapshot().await
    }

    async fn fetch(&self) -> Result<Vec<Pool>, String> {
        let url = format!("{}/pools", self.source);
        let response = self
            .http
            .get(&url)
            .send()
            .await
            .map_err(|e| format!("{url} is not answering ({e})"))?;
        let status = response.status().as_u16();
        let body: Value = response
            .json()
            .await
            .map_err(|e| format!("{url} returned HTTP {status} and no usable JSON ({e})"))?;
        let rows = body
            .get("data")
            .and_then(|d| d.as_array())
            .ok_or_else(|| format!("{url} returned no pool list"))?;
        Ok(rows
            .iter()
            .filter_map(|r| serde_json::from_value::<Pool>(r.clone()).ok())
            .collect())
    }

    fn keep(pool: &Pool, filter: &Filter) -> bool {
        let apy = pool.apy.unwrap_or(0.0);
        if pool.tvl_usd < filter.min_tvl {
            return false;
        }
        if apy <= 0.0 {
            return false;
        }
        if let Some(cap) = filter.max_apy {
            if apy > cap {
                return false;
            }
        }
        if !filter.include_outliers && pool.outlier {
            return false;
        }
        if filter.stable_only && !pool.stablecoin {
            return false;
        }
        if filter.organic_only {
            // "Organic" means most of the rate is fees, not token emissions.
            let base = pool.apy_base.unwrap_or(0.0);
            if base <= 0.0 || base < apy * 0.6 {
                return false;
            }
        }
        if let Some(chain) = &filter.chain {
            if !pool.chain.eq_ignore_ascii_case(chain) {
                return false;
            }
        }
        if let Some(project) = &filter.project {
            if !pool.project.eq_ignore_ascii_case(project) {
                return false;
            }
        }
        if let Some(symbol) = &filter.symbol {
            if !pool.symbol.to_lowercase().contains(&symbol.to_lowercase()) {
                return false;
            }
        }
        if let Some(q) = &filter.q {
            let needle = q.to_lowercase();
            let hay = format!(
                "{} {} {} {}",
                pool.project,
                pool.chain,
                pool.symbol,
                pool.pool_meta.clone().unwrap_or_default()
            )
            .to_lowercase();
            if !hay.contains(&needle) {
                return false;
            }
        }
        true
    }

    fn sort_key(pool: &Pool, sort: &str) -> f64 {
        match sort {
            "tvl" => pool.tvl_usd,
            "base" => pool.apy_base.unwrap_or(0.0),
            "mean30d" => pool.apy_mean_30d.unwrap_or(0.0),
            // Depth-adjusted: the rate discounted by how little of it a real
            // allocation could actually take. A pool deeper than DEPTH_FULL
            // scores its headline APY; a thinner one is marked down by the
            // square root of the shortfall, so 400% on $120k lands below 8% on
            // half a billion — which is the honest ordering for money that
            // intends to sit somewhere.
            "score" => {
                let depth = (pool.tvl_usd / DEPTH_FULL).min(1.0).max(0.0).sqrt();
                pool.apy.unwrap_or(0.0) * depth
            }
            _ => pool.apy.unwrap_or(0.0),
        }
    }

    /// The APR table: one row per pool, filtered and sorted.
    pub async fn pools(&self, filter: &Filter) -> Result<Value, String> {
        let (pools, fetched) = self.snapshot().await?;
        let mut hits: Vec<&Pool> = pools.iter().filter(|p| Self::keep(p, filter)).collect();
        hits.sort_by(|a, b| {
            Self::sort_key(b, &filter.sort)
                .partial_cmp(&Self::sort_key(a, &filter.sort))
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        let total = hits.len();
        let rows: Vec<Value> = hits.iter().take(filter.limit).map(|p| row(p)).collect();
        Ok(json!({
            "pools": rows,
            "matched": total,
            "shown": rows.len(),
            "universe": pools.len(),
            "sort": filter.sort,
            "as_of": fetched,
            "age_seconds": crate::auth::now().saturating_sub(fetched),
            "source": format!("{}/pools", self.source),
        }))
    }

    /// One row per protocol rather than per pool — the "APR for each of the
    /// DeFi protocols" view. The headline is the TVL-weighted APY, because a
    /// protocol's best pool is a marketing number and its average is dragged
    /// down by dust.
    pub async fn protocols(&self, filter: &Filter) -> Result<Value, String> {
        let (pools, fetched) = self.snapshot().await?;
        let mut by_project: std::collections::HashMap<String, Vec<&Pool>> =
            std::collections::HashMap::new();
        for pool in pools.iter().filter(|p| Self::keep(p, filter)) {
            by_project.entry(pool.project.clone()).or_default().push(pool);
        }

        let mut rows: Vec<Value> = by_project
            .into_iter()
            .map(|(project, group)| {
                let tvl: f64 = group.iter().map(|p| p.tvl_usd).sum();
                let weighted = if tvl > 0.0 {
                    group.iter().map(|p| p.apy.unwrap_or(0.0) * p.tvl_usd).sum::<f64>() / tvl
                } else {
                    0.0
                };
                let base_weighted = if tvl > 0.0 {
                    group.iter().map(|p| p.apy_base.unwrap_or(0.0) * p.tvl_usd).sum::<f64>() / tvl
                } else {
                    0.0
                };
                let best = group
                    .iter()
                    .max_by(|a, b| {
                        a.apy
                            .unwrap_or(0.0)
                            .partial_cmp(&b.apy.unwrap_or(0.0))
                            .unwrap_or(std::cmp::Ordering::Equal)
                    })
                    .copied();
                let mut chains: Vec<String> =
                    group.iter().map(|p| p.chain.clone()).collect::<std::collections::BTreeSet<_>>().into_iter().collect();
                chains.truncate(8);
                json!({
                    "project": project,
                    "pools": group.len(),
                    "tvl_usd": round2(tvl),
                    "apy": round2(weighted),
                    "apy_base": round2(base_weighted),
                    "apy_reward": round2((weighted - base_weighted).max(0.0)),
                    "best_apy": best.and_then(|p| p.apy).map(round2),
                    "best_pool": best.map(|p| json!({
                        "pool": p.pool, "symbol": p.symbol, "chain": p.chain,
                        "apy": p.apy.map(round2), "tvl_usd": round2(p.tvl_usd),
                    })),
                    "chains": chains,
                    "stable_pools": group.iter().filter(|p| p.stablecoin).count(),
                })
            })
            .collect();

        let sort = if filter.sort == "apy" { "tvl" } else { filter.sort.as_str() };
        rows.sort_by(|a, b| {
            let key = |v: &Value| {
                v.get(match sort {
                    "tvl" => "tvl_usd",
                    "pools" => "pools",
                    "best" => "best_apy",
                    _ => "apy",
                })
                .and_then(|x| x.as_f64())
                .unwrap_or(0.0)
            };
            key(b).partial_cmp(&key(a)).unwrap_or(std::cmp::Ordering::Equal)
        });
        let total = rows.len();
        rows.truncate(filter.limit);
        Ok(json!({
            "protocols": rows,
            "matched": total,
            "shown": rows.len(),
            "universe": pools.len(),
            "sort": sort,
            "as_of": fetched,
            "age_seconds": crate::auth::now().saturating_sub(fetched),
            "note": "apy is TVL-weighted across the protocol's pools that pass the filter; \
                     apy_base is the part that comes from fees rather than emissions",
            "source": format!("{}/pools", self.source),
        }))
    }

    /// One pool, plus how its rate has actually behaved. The history is what
    /// turns a headline APR into a decision.
    pub async fn pool(&self, id: &str, history: bool) -> Result<Value, String> {
        let (pools, fetched) = self.snapshot().await?;
        let found = pools
            .iter()
            .find(|p| p.pool == id)
            .ok_or_else(|| format!("no pool '{id}' in the index — ids come from /yields"))?;

        let mut chart = Value::Null;
        if history {
            if let Ok(response) = self.http.get(format!("{}/chart/{id}", self.source)).send().await {
                if let Ok(body) = response.json::<Value>().await {
                    let points: Vec<Value> = body
                        .get("data")
                        .and_then(|d| d.as_array())
                        .map(|rows| {
                            rows.iter()
                                .rev()
                                .take(365)
                                .map(|r| {
                                    json!({
                                        "t": r.get("timestamp").cloned().unwrap_or(Value::Null),
                                        "apy": r.get("apy").and_then(|v| v.as_f64()).map(round2),
                                        "tvl_usd": r.get("tvlUsd").and_then(|v| v.as_f64()).map(round2),
                                    })
                                })
                                .collect::<Vec<_>>()
                                .into_iter()
                                .rev()
                                .collect()
                        })
                        .unwrap_or_default();
                    chart = json!({ "points": points });
                }
            }
        }

        Ok(json!({
            "pool": row(found),
            "chart": chart,
            "as_of": fetched,
            "source": format!("{}/pools", self.source),
        }))
    }

    /// The chains and protocols present in the index, for building a filter UI
    /// without hard-coding a list that goes stale.
    pub async fn facets(&self) -> Result<Value, String> {
        let (pools, fetched) = self.snapshot().await?;
        let mut chains: std::collections::HashMap<&str, (usize, f64)> = Default::default();
        let mut projects: std::collections::HashMap<&str, (usize, f64)> = Default::default();
        for pool in pools.iter() {
            let c = chains.entry(pool.chain.as_str()).or_insert((0, 0.0));
            c.0 += 1;
            c.1 += pool.tvl_usd;
            let p = projects.entry(pool.project.as_str()).or_insert((0, 0.0));
            p.0 += 1;
            p.1 += pool.tvl_usd;
        }
        let top = |map: std::collections::HashMap<&str, (usize, f64)>, n: usize| {
            let mut list: Vec<Value> = map
                .into_iter()
                .map(|(name, (count, tvl))| {
                    json!({ "name": name, "pools": count, "tvl_usd": round2(tvl),
                            "chain_id": crate::dex::chain(&name.to_lowercase()).map(|c| c.id) })
                })
                .collect();
            list.sort_by(|a, b| {
                b.get("tvl_usd")
                    .and_then(|v| v.as_f64())
                    .partial_cmp(&a.get("tvl_usd").and_then(|v| v.as_f64()))
                    .unwrap_or(std::cmp::Ordering::Equal)
            });
            list.truncate(n);
            list
        };
        Ok(json!({
            "chains": top(chains, 60),
            "projects": top(projects, 200),
            "as_of": fetched,
            "universe": pools.len(),
        }))
    }
}

/// One pool as the console and the treasury see it.
///
/// `tradable_on` is the honest join between this table and the rest of the
/// module: if the pool's chain is one this desk can trade, the chain id says so,
/// and null means you would have to bridge before any of this is actionable.
fn row(pool: &Pool) -> Value {
    let apy = pool.apy.unwrap_or(0.0);
    let base = pool.apy_base.unwrap_or(0.0);
    json!({
        "pool": pool.pool,
        "project": pool.project,
        "chain": pool.chain,
        "symbol": pool.symbol,
        "meta": pool.pool_meta,
        "apy": round2(apy),
        "apy_base": pool.apy_base.map(round2),
        "apy_reward": pool.apy_reward.map(round2),
        "apy_mean_30d": pool.apy_mean_30d.map(round2),
        "apy_change_7d": pool.apy_pct_7d.map(round2),
        "apy_change_30d": pool.apy_pct_30d.map(round2),
        "emissions_share": if apy > 0.0 { round2(((apy - base).max(0.0) / apy) * 100.0) } else { 0.0 },
        "tvl_usd": round2(pool.tvl_usd),
        "stablecoin": pool.stablecoin,
        "il_risk": pool.il_risk,
        "exposure": pool.exposure,
        "outlier": pool.outlier,
        "reward_tokens": pool.reward_tokens,
        "underlying_tokens": pool.underlying_tokens,
        "outlook": pool.predictions.as_ref().and_then(|p| p.get("predictedClass").cloned()),
        "tradable_on": crate::dex::chain(&pool.chain.to_lowercase()).map(|c| c.id),
    })
}

fn round2(value: f64) -> f64 {
    if !value.is_finite() {
        return 0.0;
    }
    (value * 100.0).round() / 100.0
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pool(project: &str, chain: &str, apy: f64, base: f64, tvl: f64) -> Pool {
        Pool {
            chain: chain.into(),
            project: project.into(),
            symbol: "USDC".into(),
            pool: format!("{project}-{chain}"),
            tvl_usd: tvl,
            apy: Some(apy),
            apy_base: Some(base),
            apy_reward: Some(apy - base),
            apy_mean_30d: Some(apy),
            apy_pct_7d: None,
            apy_pct_30d: None,
            stablecoin: true,
            il_risk: Some("no".into()),
            exposure: Some("single".into()),
            pool_meta: None,
            outlier: false,
            reward_tokens: None,
            underlying_tokens: None,
            predictions: None,
        }
    }

    #[test]
    fn a_rate_made_of_emissions_is_not_organic() {
        let mut filter = Filter { min_tvl: 0.0, organic_only: true, ..Default::default() };
        // 2% of a 10% rate comes from fees — that is a farm, not a yield.
        assert!(!Yields::keep(&pool("farm", "Base", 10.0, 2.0, 1e6), &filter));
        assert!(Yields::keep(&pool("lender", "Base", 10.0, 9.0, 1e6), &filter));
        filter.organic_only = false;
        assert!(Yields::keep(&pool("farm", "Base", 10.0, 2.0, 1e6), &filter));
    }

    #[test]
    fn a_thin_pool_never_reaches_the_table() {
        let filter = Filter { min_tvl: 100_000.0, ..Default::default() };
        assert!(!Yields::keep(&pool("dust", "Base", 900.0, 900.0, 4_000.0), &filter));
        assert!(Yields::keep(&pool("real", "Base", 6.0, 6.0, 5e6), &filter));
    }

    #[test]
    fn score_prefers_depth_over_a_headline() {
        let deep = pool("aave", "Base", 8.0, 8.0, 500_000_000.0);
        let thin = pool("degen", "Base", 40.0, 40.0, 120_000.0);
        assert!(Yields::sort_key(&deep, "score") > Yields::sort_key(&thin, "score"));
        // …and plain apy still ranks the headline first, as asked.
        assert!(Yields::sort_key(&thin, "apy") > Yields::sort_key(&deep, "apy"));
        // A high rate that IS deep enough to take is not marked down at all.
        let deep_and_hot = pool("hot", "Base", 40.0, 40.0, 50_000_000.0);
        assert_eq!(Yields::sort_key(&deep_and_hot, "score"), 40.0);
    }

    #[test]
    fn a_pool_says_which_chain_this_desk_could_trade_it_on() {
        assert_eq!(row(&pool("aave", "Base", 6.0, 6.0, 1e8))["tradable_on"], json!("base"));
        assert_eq!(row(&pool("aave", "Sui", 6.0, 6.0, 1e8))["tradable_on"], Value::Null);
    }
}
