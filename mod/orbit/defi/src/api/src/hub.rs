//! The HUB — the curated front door for USD.
//!
//! The modules registry answers "everywhere money can go"; the hub answers the
//! question people actually arrive with: *which protocols are legitimate enough
//! to put dollars into, and on which chains?* The answer is a hand-vetted list
//! (hub.json — track record, named team, public audits, plain-USD entry) joined
//! live against DefiLlama's index, so the names are curated but every number
//! beside them is the market's, fetched this hour.
//!
//! Two honesty rules. Curated is not certified: every entry carries its risks
//! next to its credentials, and the file's own note says what the vetting did
//! and did not check. And the join is live both ways — a protocol whose stable
//! pools drain out of the index shows up here with empty chains rather than
//! being quietly propped up by a stale description.

use crate::finance::Registry;
use crate::yields::Pool;
use serde::Deserialize;
use serde_json::{json, Value};
use std::path::Path;

/// One vetted protocol, as written down in hub.json.
#[derive(Debug, Clone, Deserialize)]
pub struct Entry {
    pub id: String,
    pub name: String,
    pub category: String,
    /// core | established | frontier — how much history stands behind it.
    pub tier: String,
    pub since: u32,
    pub website: String,
    #[serde(default)]
    pub llama_projects: Vec<String>,
    #[serde(default)]
    pub usd_in: Vec<String>,
    pub blurb: String,
    #[serde(default)]
    pub legit: Vec<String>,
    #[serde(default)]
    pub risks: Vec<String>,
    /// Curve-style: you hold the pool's mix, not a single-sided deposit.
    #[serde(default)]
    pub paired: bool,
}

#[derive(Debug, Clone, Deserialize, Default)]
struct File {
    #[serde(default)]
    note: String,
    #[serde(default)]
    protocols: Vec<Entry>,
}

pub struct Hub {
    note: String,
    entries: Vec<Entry>,
    pub load_error: Option<String>,
}

fn tier_rank(tier: &str) -> u8 {
    match tier {
        "core" => 0,
        "established" => 1,
        _ => 2,
    }
}

impl Hub {
    pub fn load(path: &Path) -> Self {
        let parsed: Result<File, String> = std::fs::read_to_string(path)
            .map_err(|e| format!("{}: {e}", path.display()))
            .and_then(|text| {
                serde_json::from_str(&text).map_err(|e| format!("{} is not a hub registry: {e}", path.display()))
            });
        match parsed {
            Ok(file) => Self { note: file.note, entries: file.protocols, load_error: None },
            Err(e) => {
                eprintln!("[defi] hub registry: {e}");
                Self { note: String::new(), entries: Vec::new(), load_error: Some(e) }
            }
        }
    }

    /// Does this pool belong to the entry, and is it a USD pool worth quoting?
    fn keep(entry: &Entry, pool: &Pool, min_tvl: f64) -> bool {
        entry.llama_projects.iter().any(|p| p.eq_ignore_ascii_case(&pool.project))
            && pool.stablecoin
            && !pool.outlier
            && pool.apy.unwrap_or(0.0) > 0.0
            && pool.tvl_usd >= min_tvl
    }

    /// Depth-adjusted rate, same shape as the yields table's `score` sort: a
    /// headline on a thin pool is marked down so "best" means "best for money
    /// that intends to sit there".
    fn score(pool: &Pool) -> f64 {
        let depth = (pool.tvl_usd / 10_000_000.0).min(1.0).sqrt();
        pool.apy.unwrap_or(0.0) * depth
    }

    /// The whole hub: every vetted protocol with its live per-chain USD pools.
    pub fn assemble(
        &self,
        pools: &[Pool],
        registry: &Registry,
        fetched: u64,
        want_chain: Option<&str>,
        min_tvl: f64,
    ) -> Value {
        let mut rows: Vec<Value> = self
            .entries
            .iter()
            .map(|entry| self.protocol_row(entry, pools, registry, want_chain, min_tvl, false))
            .collect();
        rows.sort_by(|a, b| {
            let rank = |v: &Value| tier_rank(v.get("tier").and_then(|t| t.as_str()).unwrap_or(""));
            let tvl = |v: &Value| v.get("stable_tvl_usd").and_then(|t| t.as_f64()).unwrap_or(0.0);
            rank(a).cmp(&rank(b)).then(tvl(b).partial_cmp(&tvl(a)).unwrap_or(std::cmp::Ordering::Equal))
        });

        let mut chains: std::collections::BTreeMap<String, (usize, f64)> = Default::default();
        for row in &rows {
            for c in row.get("chains").and_then(|c| c.as_array()).into_iter().flatten() {
                let name = c.get("chain").and_then(|v| v.as_str()).unwrap_or("?");
                let e = chains.entry(name.to_string()).or_default();
                e.0 += 1;
                e.1 += c.get("tvl_usd").and_then(|v| v.as_f64()).unwrap_or(0.0);
            }
        }
        let mut chain_rows: Vec<Value> = chains
            .into_iter()
            .map(|(name, (protocols, tvl))| {
                let desk = crate::dex::chain(&name.to_lowercase()).filter(|c| !c.testnet);
                json!({
                    "chain": name,
                    "protocols": protocols,
                    "tvl_usd": round2(tvl),
                    "desk": desk.map(|c| c.id),
                    "module": desk.map(|c| c.module),
                })
            })
            .collect();
        chain_rows.sort_by(|a, b| {
            b.get("tvl_usd")
                .and_then(|v| v.as_f64())
                .partial_cmp(&a.get("tvl_usd").and_then(|v| v.as_f64()))
                .unwrap_or(std::cmp::Ordering::Equal)
        });

        let total: f64 = rows.iter().filter_map(|r| r.get("stable_tvl_usd").and_then(|v| v.as_f64())).sum();
        json!({
            "hub": rows,
            "protocols": self.entries.len(),
            "chains": chain_rows,
            "stable_tvl_usd": round2(total),
            "note": self.note,
            "min_tvl": min_tvl,
            "as_of": fetched,
            "age_seconds": crate::auth::now().saturating_sub(fetched),
            "source": "curation: hub.json on this node · numbers: DefiLlama's yields index",
        })
    }

    /// One protocol in full — same row, plus every USD pool per chain instead
    /// of just the best one.
    pub fn protocol(
        &self,
        id: &str,
        pools: &[Pool],
        registry: &Registry,
        fetched: u64,
        min_tvl: f64,
    ) -> Result<Value, String> {
        let entry = self
            .entries
            .iter()
            .find(|e| e.id.eq_ignore_ascii_case(id))
            .ok_or_else(|| format!("no '{id}' in the hub — ids come from /hub"))?;
        let mut row = self.protocol_row(entry, pools, registry, None, min_tvl, true);
        if let Some(obj) = row.as_object_mut() {
            obj.insert("as_of".into(), json!(fetched));
            obj.insert("age_seconds".into(), json!(crate::auth::now().saturating_sub(fetched)));
        }
        Ok(row)
    }

    fn protocol_row(
        &self,
        entry: &Entry,
        pools: &[Pool],
        registry: &Registry,
        want_chain: Option<&str>,
        min_tvl: f64,
        full: bool,
    ) -> Value {
        let mut by_chain: std::collections::BTreeMap<&str, Vec<&Pool>> = Default::default();
        for pool in pools.iter().filter(|p| Self::keep(entry, p, min_tvl)) {
            if let Some(want) = want_chain {
                let desk = crate::dex::chain(&pool.chain.to_lowercase()).map(|c| c.id);
                if !pool.chain.eq_ignore_ascii_case(want) && desk != Some(want) {
                    continue;
                }
            }
            by_chain.entry(pool.chain.as_str()).or_default().push(pool);
        }

        let mut chains: Vec<Value> = by_chain
            .into_iter()
            .map(|(chain, mut group)| {
                group.sort_by(|a, b| Self::score(b).partial_cmp(&Self::score(a)).unwrap_or(std::cmp::Ordering::Equal));
                let tvl: f64 = group.iter().map(|p| p.tvl_usd).sum();
                let desk = crate::dex::chain(&chain.to_lowercase()).filter(|c| !c.testnet);
                let enterable = group.iter().any(|p| registry.adapter_for(p).is_some());
                let best = group.first().map(|p| pool_row(p, registry));
                let mut row = json!({
                    "chain": chain,
                    "desk": desk.map(|c| c.id),
                    "module": desk.map(|c| c.module),
                    "enterable": enterable,
                    "pools": group.len(),
                    "tvl_usd": round2(tvl),
                    "best": best,
                });
                if full {
                    let listed: Vec<Value> = group.iter().take(12).map(|p| pool_row(p, registry)).collect();
                    row.as_object_mut().unwrap().insert("usd_pools".into(), json!(listed));
                }
                row
            })
            .collect();
        chains.sort_by(|a, b| {
            b.get("tvl_usd")
                .and_then(|v| v.as_f64())
                .partial_cmp(&a.get("tvl_usd").and_then(|v| v.as_f64()))
                .unwrap_or(std::cmp::Ordering::Equal)
        });

        let stable_tvl: f64 = chains.iter().filter_map(|c| c.get("tvl_usd").and_then(|v| v.as_f64())).sum();
        // The headline "best" prefers a chain this desk can reach: a hotter
        // depth-adjusted rate on a chain you would have to bridge to first is
        // an answer to a different question.
        let best = chains
            .iter()
            .filter_map(|c| c.get("best").filter(|b| !b.is_null()).map(|b| (c, b)))
            .max_by(|a, b| {
                let reachable = |c: &Value| c.get("desk").map(|d| !d.is_null()).unwrap_or(false);
                let s = |v: &Value| v.get("apy_scored").and_then(|x| x.as_f64()).unwrap_or(0.0);
                reachable(a.0)
                    .cmp(&reachable(b.0))
                    .then(s(a.1).partial_cmp(&s(b.1)).unwrap_or(std::cmp::Ordering::Equal))
            })
            .map(|(c, b)| {
                let mut best = b.clone();
                if let Some(obj) = best.as_object_mut() {
                    obj.insert("chain".into(), c.get("chain").cloned().unwrap_or(Value::Null));
                    obj.insert("desk".into(), c.get("desk").cloned().unwrap_or(Value::Null));
                }
                best
            });

        json!({
            "id": entry.id,
            "name": entry.name,
            "category": entry.category,
            "tier": entry.tier,
            "since": entry.since,
            "website": entry.website,
            "blurb": entry.blurb,
            "usd_in": entry.usd_in,
            "legit": entry.legit,
            "risks": entry.risks,
            "paired": entry.paired,
            "llama_projects": entry.llama_projects,
            "enterable_from_desk": chains.iter().any(|c| c.get("enterable") == Some(&json!(true))),
            "stable_tvl_usd": round2(stable_tvl),
            "chain_count": chains.len(),
            "best": best,
            "chains": chains,
        })
    }
}

/// One USD pool as the hub quotes it: the yields row's essentials plus the two
/// facts the hub exists for — the module id that opens it on the desk, and
/// whether this desk can actually put money in.
fn pool_row(pool: &Pool, registry: &Registry) -> Value {
    let apy = pool.apy.unwrap_or(0.0);
    let base = pool.apy_base.unwrap_or(0.0);
    json!({
        "module_id": format!("llama:{}", pool.pool),
        "pool": pool.pool,
        "symbol": pool.symbol,
        "meta": pool.pool_meta,
        "apy": round2(apy),
        "apy_base": pool.apy_base.map(round2),
        "apy_reward": pool.apy_reward.map(round2),
        "apy_mean_30d": pool.apy_mean_30d.map(round2),
        "apy_scored": round2(Hub::score(pool)),
        "emissions_share": if apy > 0.0 { round2(((apy - base).max(0.0) / apy) * 100.0) } else { 0.0 },
        "tvl_usd": round2(pool.tvl_usd),
        "enterable": registry.adapter_for(pool).is_some(),
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

    fn entry(id: &str, tier: &str, projects: &[&str]) -> Entry {
        Entry {
            id: id.into(),
            name: id.into(),
            category: "Lending".into(),
            tier: tier.into(),
            since: 2020,
            website: String::new(),
            llama_projects: projects.iter().map(|p| p.to_string()).collect(),
            usd_in: vec![],
            blurb: String::new(),
            legit: vec![],
            risks: vec![],
            paired: false,
        }
    }

    fn pool(project: &str, chain: &str, symbol: &str, apy: f64, tvl: f64, stable: bool) -> Pool {
        Pool {
            chain: chain.into(),
            project: project.into(),
            symbol: symbol.into(),
            pool: format!("{project}-{chain}-{symbol}"),
            tvl_usd: tvl,
            apy: Some(apy),
            apy_base: Some(apy),
            apy_reward: None,
            apy_mean_30d: Some(apy),
            apy_pct_7d: None,
            apy_pct_30d: None,
            stablecoin: stable,
            il_risk: Some("no".into()),
            exposure: Some("single".into()),
            pool_meta: None,
            outlier: false,
            reward_tokens: None,
            underlying_tokens: None,
            predictions: None,
        }
    }

    fn hub(entries: Vec<Entry>) -> Hub {
        Hub { note: String::new(), entries, load_error: None }
    }

    #[test]
    fn a_protocol_groups_its_usd_pools_by_chain() {
        let h = hub(vec![entry("aave-v3", "core", &["aave-v3"])]);
        let pools = vec![
            pool("aave-v3", "Ethereum", "USDC", 4.0, 5e8, true),
            pool("aave-v3", "Base", "USDC", 6.0, 2e8, true),
            pool("aave-v3", "Arbitrum", "USDT", 5.0, 1e8, true),
            // Not USD — must never reach the hub.
            pool("aave-v3", "Ethereum", "WETH", 2.0, 1e9, false),
            // Someone else's pool.
            pool("degen-farm", "Base", "USDC", 900.0, 2e6, true),
        ];
        let out = h.assemble(&pools, &Registry::default(), 0, None, 1_000_000.0);
        let row = &out["hub"][0];
        assert_eq!(row["chain_count"], json!(3));
        assert_eq!(row["stable_tvl_usd"], json!(8e8));
        // Chains come deepest first, and each knows whether the desk reaches it.
        assert_eq!(row["chains"][0]["chain"], json!("Ethereum"));
        assert_eq!(row["chains"][0]["desk"], json!("ethereum"));
        assert_eq!(row["chains"][2]["chain"], json!("Arbitrum"));
        assert_eq!(row["chains"][2]["desk"], Value::Null);
    }

    #[test]
    fn core_outranks_frontier_regardless_of_size() {
        let h = hub(vec![
            entry("hot-new", "frontier", &["hot-new"]),
            entry("old-bank", "core", &["old-bank"]),
        ]);
        let pools = vec![
            pool("hot-new", "Ethereum", "USDC", 30.0, 9e9, true),
            pool("old-bank", "Ethereum", "USDC", 4.0, 1e8, true),
        ];
        let out = h.assemble(&pools, &Registry::default(), 0, None, 0.0);
        assert_eq!(out["hub"][0]["id"], json!("old-bank"));
        assert_eq!(out["hub"][1]["id"], json!("hot-new"));
    }

    #[test]
    fn enterable_follows_the_adapter_registry() {
        let h = hub(vec![entry("aave-v3", "core", &["aave-v3"])]);
        let pools = vec![pool("aave-v3", "Ethereum", "USDC", 4.0, 5e8, true)];
        let registry: Registry = serde_json::from_value(json!({
            "adapters": [{
                "match": { "chain": "Ethereum", "project": "aave-v3", "symbol": "USDC" },
                "kind": "aave_v3",
                "asset": { "symbol": "USDC", "address": "0x0", "decimals": 6 }
            }]
        }))
        .unwrap();
        let with = h.assemble(&pools, &registry, 0, None, 0.0);
        assert_eq!(with["hub"][0]["enterable_from_desk"], json!(true));
        assert_eq!(with["hub"][0]["chains"][0]["best"]["enterable"], json!(true));
        let without = h.assemble(&pools, &Registry::default(), 0, None, 0.0);
        assert_eq!(without["hub"][0]["enterable_from_desk"], json!(false));
    }

    #[test]
    fn a_drained_protocol_stays_visible_with_empty_chains() {
        let h = hub(vec![entry("ghost", "core", &["ghost"])]);
        let out = h.assemble(&[], &Registry::default(), 0, None, 0.0);
        assert_eq!(out["hub"][0]["chain_count"], json!(0));
        assert_eq!(out["hub"][0]["best"], Value::Null);
    }

    #[test]
    fn best_is_depth_adjusted_not_the_headline() {
        let h = hub(vec![entry("aave-v3", "core", &["aave-v3"])]);
        let pools = vec![
            pool("aave-v3", "Ethereum", "USDC", 4.0, 5e8, true),
            // A hotter headline on a thin pool must not become "best".
            pool("aave-v3", "Base", "USDC", 10.0, 1_200_000.0, true),
        ];
        let out = h.assemble(&pools, &Registry::default(), 0, None, 1_000_000.0);
        assert_eq!(out["hub"][0]["best"]["chain"], json!("Ethereum"));
    }
}
