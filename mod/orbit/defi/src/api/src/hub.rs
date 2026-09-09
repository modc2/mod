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
use std::collections::HashMap;
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
    /// Where the live numbers come from: unset = DefiLlama's yields index,
    /// "bittensor" = the bt module's subnet list. The join, not the vetting.
    #[serde(default)]
    pub source: Option<String>,
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
    /// Whether assembling this hub needs the bt module's subnet list at all —
    /// so the caller only knocks on Bittensor when an entry will use it.
    pub fn wants_subnets(&self) -> bool {
        self.entries.iter().any(|e| e.source.as_deref() == Some("bittensor"))
    }

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
    /// `subnets`/`tao_usd` feed the one non-llama entry (source "bittensor");
    /// pass them empty/None and that entry shows empty chains, like a drained
    /// protocol — never a stale number.
    pub fn assemble(
        &self,
        pools: &[Pool],
        registry: &Registry,
        fetched: u64,
        want_chain: Option<&str>,
        min_tvl: f64,
        subnets: &[Value],
        tao_usd: Option<f64>,
    ) -> Value {
        // The list view never carries trusted stake — that is a detail-card
        // read, priced accordingly (one slow validator fetch per listed subnet).
        let no_trust: HashMap<u64, Value> = HashMap::new();
        let mut rows: Vec<Value> = self
            .entries
            .iter()
            .map(|entry| self.protocol_row(entry, pools, registry, want_chain, min_tvl, false, subnets, tao_usd, &no_trust))
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
            "source": "curation: hub.json on this node · numbers: DefiLlama's yields index, and bt_subnets for Bittensor",
        })
    }

    /// One protocol in full — same row, plus every USD pool per chain instead
    /// of just the best one.
    #[allow(clippy::too_many_arguments)]
    pub fn protocol(
        &self,
        id: &str,
        pools: &[Pool],
        registry: &Registry,
        fetched: u64,
        min_tvl: f64,
        subnets: &[Value],
        tao_usd: Option<f64>,
        trust: &HashMap<u64, Value>,
    ) -> Result<Value, String> {
        let entry = self
            .entries
            .iter()
            .find(|e| e.id.eq_ignore_ascii_case(id))
            .ok_or_else(|| format!("no '{id}' in the hub — ids come from /hub"))?;
        let mut row = self.protocol_row(entry, pools, registry, None, min_tvl, true, subnets, tao_usd, trust);
        if let Some(obj) = row.as_object_mut() {
            obj.insert("as_of".into(), json!(fetched));
            obj.insert("age_seconds".into(), json!(crate::auth::now().saturating_sub(fetched)));
        }
        Ok(row)
    }

    #[allow(clippy::too_many_arguments)]
    fn protocol_row(
        &self,
        entry: &Entry,
        pools: &[Pool],
        registry: &Registry,
        want_chain: Option<&str>,
        min_tvl: f64,
        full: bool,
        subnets: &[Value],
        tao_usd: Option<f64>,
        trust: &HashMap<u64, Value>,
    ) -> Value {
        if entry.source.as_deref() == Some("bittensor") {
            return tao_row(entry, subnets, tao_usd, want_chain, full, trust);
        }
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

/// The one non-llama source: Bittensor, joined against the bt module's live
/// subnet list. Same row shape as every other protocol, but honest about the
/// two ways it differs — TAO goes in (not USD), and no APY is quoted because
/// none is promised. Its TVL rides on the row as `tvl_usd`/`tvl_tao` and is
/// kept OUT of `stable_tvl_usd`, so the hub's dollar total stays a dollar
/// total. No TAO/USD price in hand → dollar fields are null, never guessed.
fn tao_row(
    entry: &Entry,
    subnets: &[Value],
    tao_usd: Option<f64>,
    want_chain: Option<&str>,
    full: bool,
    trust: &HashMap<u64, Value>,
) -> Value {
    let tao_in = |s: &Value| s.get("tao_in").and_then(|v| v.as_f64()).unwrap_or(0.0);
    let mut list: Vec<&Value> = subnets.iter().collect();
    list.sort_by(|a, b| tao_in(b).partial_cmp(&tao_in(a)).unwrap_or(std::cmp::Ordering::Equal));
    let total_tao: f64 = list.iter().map(|s| tao_in(s)).sum();
    let tvl_usd = tao_usd.map(|p| round2(total_tao * p));

    let subnet_row = |s: &Value| -> Value {
        let netuid = s.get("netuid").and_then(|v| v.as_u64()).unwrap_or(0);
        let name = s
            .get("subnet_name")
            .or_else(|| s.get("name"))
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let depth = tao_in(s);
        let mut row = json!({
            "module_id": format!("tao:sn{netuid}"),
            "pool": format!("sn{netuid}"),
            "symbol": if netuid == 0 { "TAO root".to_string() } else { format!("SN{netuid} {name}").trim().to_string() },
            "apy": Value::Null,
            "apy_scored": 0.0,
            "note": "no promised rate — alpha emission on a floating price",
            "tvl_tao": round2(depth),
            "tvl_usd": tao_usd.map(|p| round2(depth * p)),
            "alpha_price_tao": s.get("price").and_then(|v| v.as_f64()),
            "enterable": true,
        });
        // Trusted stake, when it has been read for this subnet: the summary in
        // native stake units, plus the same figures said in TAO (stake × pool
        // price — a no-op on root, where the unit already is TAO) and dollars.
        if let Some(t) = trust.get(&netuid) {
            let mut t = t.clone();
            let price = s.get("price").and_then(|v| v.as_f64()).unwrap_or(0.0);
            let trusted_tao = t.get("trusted_stake").and_then(|v| v.as_f64()).map(|x| round2(x * price));
            let validator_tao = t.get("validator_stake").and_then(|v| v.as_f64()).map(|x| round2(x * price));
            if let Some(obj) = t.as_object_mut() {
                obj.insert("trusted_stake_tao".into(), json!(trusted_tao));
                obj.insert("validator_stake_tao".into(), json!(validator_tao));
                obj.insert(
                    "trusted_stake_usd".into(),
                    json!(trusted_tao.and_then(|x| tao_usd.map(|p| round2(x * p)))),
                );
            }
            row.as_object_mut().unwrap().insert("trusted_stake".into(), t);
        }
        row
    };

    let wanted = want_chain.map(|w| w == "tao" || w == "bittensor").unwrap_or(true);
    let chains: Vec<Value> = if wanted && !list.is_empty() {
        // "Best" is root: the deepest pool and the only one whose price does
        // not float against TAO — the honest default for money that intends
        // to sit, exactly as depth-adjustment picks it for USD protocols.
        let best = list
            .iter()
            .find(|s| s.get("netuid").and_then(|v| v.as_u64()) == Some(0))
            .or_else(|| list.first())
            .map(|s| subnet_row(s));
        let mut row = json!({
            "chain": "Bittensor",
            "desk": "tao",
            "module": "bt",
            "enterable": true,
            "pools": list.len(),
            "pool_word": "subnet",
            "tvl_usd": tvl_usd,
            "tvl_tao": round2(total_tao),
            "best": best,
        });
        if full {
            let listed: Vec<Value> = list.iter().take(12).map(|s| subnet_row(s)).collect();
            row.as_object_mut().unwrap().insert("usd_pools".into(), json!(listed));
        }
        vec![row]
    } else {
        Vec::new()
    };

    let best = chains.first().and_then(|c| c.get("best").filter(|b| !b.is_null())).map(|b| {
        let mut best = b.clone();
        if let Some(obj) = best.as_object_mut() {
            obj.insert("chain".into(), json!("Bittensor"));
            obj.insert("desk".into(), json!("tao"));
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
        "source": "bittensor",
        "enterable_from_desk": !chains.is_empty(),
        "stable_tvl_usd": 0.0,
        "tvl_usd": tvl_usd,
        "tvl_tao": round2(total_tao),
        "tao_usd": tao_usd.map(round2),
        "chain_count": chains.len(),
        "best": best,
        "chains": chains,
    })
}

/// The netuids the full Bittensor card lists — root first (it is "best", so
/// its trust line should land inside any fetch budget), then the deepest
/// pools, exactly the rows tao_row shows. This is what trusted stake gets
/// fetched for: one bt_validators read per subnet is far too slow to run for
/// all ~130, and the card never shows more than these anyway.
pub fn tao_listed(subnets: &[Value]) -> Vec<u64> {
    let tao_in = |s: &Value| s.get("tao_in").and_then(|v| v.as_f64()).unwrap_or(0.0);
    let mut list: Vec<&Value> = subnets.iter().collect();
    list.sort_by(|a, b| tao_in(b).partial_cmp(&tao_in(a)).unwrap_or(std::cmp::Ordering::Equal));
    let mut out: Vec<u64> = Vec::new();
    if subnets.iter().any(|s| s.get("netuid").and_then(|v| v.as_u64()) == Some(0)) {
        out.push(0);
    }
    for s in list.iter().take(12) {
        if let Some(n) = s.get("netuid").and_then(|v| v.as_u64()) {
            if !out.contains(&n) {
                out.push(n);
            }
        }
    }
    out
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
            source: None,
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
        let out = h.assemble(&pools, &Registry::default(), 0, None, 1_000_000.0, &[], None);
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
        let out = h.assemble(&pools, &Registry::default(), 0, None, 0.0, &[], None);
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
        let with = h.assemble(&pools, &registry, 0, None, 0.0, &[], None);
        assert_eq!(with["hub"][0]["enterable_from_desk"], json!(true));
        assert_eq!(with["hub"][0]["chains"][0]["best"]["enterable"], json!(true));
        let without = h.assemble(&pools, &Registry::default(), 0, None, 0.0, &[], None);
        assert_eq!(without["hub"][0]["enterable_from_desk"], json!(false));
    }

    #[test]
    fn a_drained_protocol_stays_visible_with_empty_chains() {
        let h = hub(vec![entry("ghost", "core", &["ghost"])]);
        let out = h.assemble(&[], &Registry::default(), 0, None, 0.0, &[], None);
        assert_eq!(out["hub"][0]["chain_count"], json!(0));
        assert_eq!(out["hub"][0]["best"], Value::Null);
    }

    fn tao_entry() -> Entry {
        let mut e = entry("bittensor", "frontier", &[]);
        e.source = Some("bittensor".into());
        e
    }

    fn subnet(netuid: u64, name: &str, tao_in: f64) -> Value {
        json!({ "netuid": netuid, "subnet_name": name, "tao_in": tao_in, "price": if netuid == 0 { 1.0 } else { 0.02 } })
    }

    #[test]
    fn bittensor_joins_the_bt_modules_subnets_not_llama() {
        let h = hub(vec![tao_entry()]);
        let subnets = vec![subnet(0, "root", 5_000_000.0), subnet(64, "chutes", 100_000.0)];
        let out = h.assemble(&[], &Registry::default(), 0, None, 1_000_000.0, &subnets, Some(260.0));
        let row = &out["hub"][0];
        assert_eq!(row["chain_count"], json!(1));
        assert_eq!(row["chains"][0]["chain"], json!("Bittensor"));
        assert_eq!(row["chains"][0]["desk"], json!("tao"));
        assert_eq!(row["chains"][0]["module"], json!("bt"));
        assert_eq!(row["chains"][0]["enterable"], json!(true));
        assert_eq!(row["enterable_from_desk"], json!(true));
        // Root is "best": deepest, and the only pool that does not float vs TAO.
        assert_eq!(row["best"]["module_id"], json!("tao:sn0"));
        assert_eq!(row["best"]["apy"], Value::Null);
        // Dollars come from the passed price, TAO figures ride alongside.
        assert_eq!(row["tvl_usd"], json!(5_100_000.0 * 260.0));
        assert_eq!(row["tvl_tao"], json!(5_100_000.0));
    }

    #[test]
    fn tao_tvl_never_pollutes_the_usd_stable_total() {
        let h = hub(vec![entry("aave-v3", "core", &["aave-v3"]), tao_entry()]);
        let pools = vec![pool("aave-v3", "Ethereum", "USDC", 4.0, 5e8, true)];
        let subnets = vec![subnet(0, "root", 5_000_000.0)];
        let out = h.assemble(&pools, &Registry::default(), 0, None, 0.0, &subnets, Some(260.0));
        // The hub's dollar total is stablecoin USD only — TAO is not a dollar.
        assert_eq!(out["stable_tvl_usd"], json!(5e8));
    }

    #[test]
    fn no_tao_price_means_null_dollars_not_a_guess() {
        let h = hub(vec![tao_entry()]);
        let subnets = vec![subnet(0, "root", 5_000_000.0)];
        let out = h.assemble(&[], &Registry::default(), 0, None, 0.0, &subnets, None);
        let row = &out["hub"][0];
        assert_eq!(row["tvl_usd"], Value::Null);
        assert_eq!(row["chains"][0]["tvl_usd"], Value::Null);
        assert_eq!(row["tvl_tao"], json!(5_000_000.0));
    }

    #[test]
    fn a_dark_bt_module_leaves_bittensor_visible_with_empty_chains() {
        let h = hub(vec![tao_entry()]);
        let out = h.assemble(&[], &Registry::default(), 0, None, 0.0, &[], Some(260.0));
        let row = &out["hub"][0];
        assert_eq!(row["chain_count"], json!(0));
        assert_eq!(row["best"], Value::Null);
        assert_eq!(row["enterable_from_desk"], json!(false));
    }

    #[test]
    fn a_chain_filter_hides_bittensor_like_anywhere_else() {
        let h = hub(vec![tao_entry()]);
        let subnets = vec![subnet(0, "root", 5_000_000.0)];
        let eth = h.assemble(&[], &Registry::default(), 0, Some("ethereum"), 0.0, &subnets, Some(260.0));
        assert_eq!(eth["hub"][0]["chain_count"], json!(0));
        let tao = h.assemble(&[], &Registry::default(), 0, Some("tao"), 0.0, &subnets, Some(260.0));
        assert_eq!(tao["hub"][0]["chain_count"], json!(1));
    }

    #[test]
    fn trusted_stake_rides_only_the_subnet_rows_it_was_read_for() {
        let h = hub(vec![tao_entry()]);
        let subnets = vec![subnet(0, "root", 5_000_000.0), subnet(64, "chutes", 100_000.0)];
        let mut trust = HashMap::new();
        trust.insert(
            0,
            crate::finance::trust_summary(
                0,
                &json!({ "top": [
                    { "hotkey": "a", "stake": 1000.0, "validator_trust": 0.8, "validator_permit": true },
                ]}),
            ),
        );
        let out = h
            .protocol("bittensor", &[], &Registry::default(), 0, 0.0, &subnets, Some(260.0), &trust)
            .unwrap();
        let t = &out["best"]["trusted_stake"];
        assert!((t["trusted_share"].as_f64().unwrap() - 0.8).abs() < 1e-9);
        // Root's unit already is TAO: stake × price 1, then dollars at the passed price.
        assert_eq!(t["trusted_stake_tao"], json!(800.0));
        assert_eq!(t["trusted_stake_usd"], json!(208_000.0));
        // A subnet nobody has read yet carries no trust line — absent, not zero.
        let pools = out["chains"][0]["usd_pools"].as_array().unwrap();
        let sn64 = pools.iter().find(|p| p["pool"] == json!("sn64")).unwrap();
        assert!(sn64.get("trusted_stake").is_none());
    }

    #[test]
    fn tao_listed_is_root_first_then_the_deepest_pools() {
        let subnets = vec![
            subnet(64, "chutes", 100_000.0),
            subnet(0, "root", 5_000_000.0),
            subnet(8, "ptn", 200_000.0),
        ];
        assert_eq!(tao_listed(&subnets), vec![0, 8, 64]);
    }

    #[test]
    fn best_is_depth_adjusted_not_the_headline() {
        let h = hub(vec![entry("aave-v3", "core", &["aave-v3"])]);
        let pools = vec![
            pool("aave-v3", "Ethereum", "USDC", 4.0, 5e8, true),
            // A hotter headline on a thin pool must not become "best".
            pool("aave-v3", "Base", "USDC", 10.0, 1_200_000.0, true),
        ];
        let out = h.assemble(&pools, &Registry::default(), 0, None, 1_000_000.0, &[], None);
        assert_eq!(out["hub"][0]["best"]["chain"], json!("Ethereum"));
    }
}
