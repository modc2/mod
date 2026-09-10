//! The treasury — what you picked, locked, and paid out on BlocTime's clock.
//!
//! The yields table answers "what is paying what". This answers the next
//! question: *so where does the money go?* You choose a pool, say how much and
//! for how many weeks, and the choice becomes an allocation. An allocation is a
//! plan until it is locked, and once it is locked the principal is not yours to
//! recall — that is the whole point.
//!
//! Payout runs on BlocTime's week, not a week of our own. `WEEK` and `OFFSET`
//! here are BlocTime.sol's `DISTRIBUTION_PERIOD` and `DISTRIBUTION_OFFSET`, so
//! every window this module talks about is the same instant BlocTime sweeps its
//! own pot: Friday 12:00 EST — 17:00 UTC, pinned year round. The split is by
//! BLOC balance, read live from the bloctime module, which is the module that
//! owns those balances. Nothing here mints, holds or moves BLOC.
//!
//! Three layers, and the difference between them is stated on every response
//! rather than blurred:
//!   * the LEDGER — allocations on this node, `~/.mod/defi/treasury/`. Local
//!     bookkeeping. Honest about being bookkeeping.
//!   * the SCHEDULE and the SPLIT — arithmetic over that ledger and the live
//!     BLOC weights. A projection, labelled as one.
//!   * the CONTRACT — a deployed ModBlocTimeTreasury, read and written through
//!     the `eth` module, which holds the key. Once an allocation is bound to
//!     one of those, its numbers come from the chain and the ledger is only a
//!     label on top.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};

/// BlocTime.sol, verbatim. Unix time 0 was a Thursday 00:00 UTC, so every
/// 7-day window starts on a Thursday; Friday 12:00 EST is 1 day 17 hours in.
pub const WEEK: u64 = 7 * 86_400;
pub const OFFSET: u64 = 86_400 + 17 * 3_600;

/// Start of the weekly window containing `ts`.
pub fn window_start(ts: u64) -> u64 {
    let b = (ts / WEEK) * WEEK + OFFSET;
    if b <= ts {
        b
    } else {
        b.saturating_sub(WEEK)
    }
}

/// The next Friday 12:00 EST at or after `ts`.
pub fn next_window(ts: u64) -> u64 {
    window_start(ts) + WEEK
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Allocation {
    pub id: String,
    pub owner: String,
    pub created: u64,
    pub updated: u64,

    // ── what you chose, and what it was paying when you chose it ──────────
    /// The DefiLlama pool id this came from, so the choice can be re-checked
    /// against the same row later.
    #[serde(default)]
    pub pool: String,
    #[serde(default)]
    pub project: String,
    #[serde(default)]
    pub chain: String,
    #[serde(default)]
    pub symbol: String,
    /// The APY at the moment of choosing. Kept because it is the number the
    /// decision was made on — never refreshed in place, or the record would
    /// quietly rewrite its own history.
    #[serde(default)]
    pub apy_at_choice: f64,
    #[serde(default)]
    pub apy_base_at_choice: f64,
    #[serde(default)]
    pub tvl_at_choice: f64,

    // ── what you are committing ───────────────────────────────────────────
    /// Human units of the asset, as typed. Never a float.
    pub amount: String,
    #[serde(default)]
    pub asset: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub asset_address: Option<String>,
    #[serde(default = "one")]
    pub term_weeks: u32,
    /// false — the principal itself is the payout, released a slice a week.
    /// true  — the principal is escrowed for the term and only the yield is
    ///         distributed.
    #[serde(default)]
    pub return_principal: bool,

    // ── where it went ─────────────────────────────────────────────────────
    /// "planned" (ledger only) · "locked" (on-chain) · "closed".
    #[serde(default = "planned")]
    pub status: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub network: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub treasury: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub lock_id: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tx: Option<String>,
    #[serde(default)]
    pub note: String,
}

fn one() -> u32 {
    1
}
fn planned() -> String {
    "planned".into()
}

impl Allocation {
    fn amount_f64(&self) -> f64 {
        self.amount.trim().parse().unwrap_or(0.0)
    }

    /// Windows that have opened since this was locked, capped at the term.
    fn weeks_elapsed(&self, now: u64) -> u32 {
        if self.status != "locked" {
            return 0;
        }
        let start = window_start(self.updated);
        let now_start = window_start(now);
        let passed = now_start.saturating_sub(start) / WEEK;
        (passed as u32).min(self.term_weeks)
    }

    /// What this allocation puts into one weekly payout.
    ///
    /// A streaming lock releases a slice of the principal. An escrowed one
    /// releases only the yield the chosen position earns, which is a
    /// projection from the APY at choosing — so it is reported separately from
    /// the principal, never added into it as if it were cash in hand.
    fn weekly(&self) -> (f64, f64) {
        let amount = self.amount_f64();
        let principal = if self.return_principal || self.term_weeks == 0 {
            0.0
        } else {
            amount / self.term_weeks as f64
        };
        // Simple weekly rate off the quoted APY. Not compounded: an APY that
        // already compounds would be double-counted, and the difference over a
        // week is noise next to the fact that the rate itself floats.
        let yield_ = amount * (self.apy_at_choice / 100.0) / 52.0;
        (principal, yield_)
    }

    pub fn view(&self, now: u64) -> Value {
        let (principal, yield_) = self.weekly();
        let elapsed = self.weeks_elapsed(now);
        let remaining = self.term_weeks.saturating_sub(elapsed);
        json!({
            "id": self.id,
            "owner": self.owner,
            "created": self.created,
            "updated": self.updated,
            "pool": self.pool,
            "project": self.project,
            "chain": self.chain,
            "symbol": self.symbol,
            "apy_at_choice": self.apy_at_choice,
            "apy_base_at_choice": self.apy_base_at_choice,
            "tvl_at_choice": self.tvl_at_choice,
            "amount": self.amount,
            "asset": self.asset,
            "asset_address": self.asset_address,
            "term_weeks": self.term_weeks,
            "return_principal": self.return_principal,
            "status": self.status,
            "network": self.network,
            "treasury": self.treasury,
            "lock_id": self.lock_id,
            "tx": self.tx,
            "note": self.note,
            "weeks_elapsed": elapsed,
            "weeks_remaining": remaining,
            "weekly_principal": round4(principal),
            "weekly_yield_projected": round4(yield_),
            "weekly_total_projected": round4(principal + yield_),
            "unlocks_at": if self.return_principal && self.status == "locked" {
                json!(window_start(self.updated) + (self.term_weeks as u64 + 1) * WEEK)
            } else {
                Value::Null
            },
            "recallable": self.status == "planned",
        })
    }
}

/// Where a deployed treasury lives, once there is one.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Binding {
    #[serde(default)]
    pub address: String,
    #[serde(default)]
    pub network: String,
    #[serde(default)]
    pub asset: String,
    #[serde(default)]
    pub weight: String,
    #[serde(default)]
    pub decimals: u32,
    #[serde(default)]
    pub bound_by: String,
    #[serde(default)]
    pub bound_at: u64,
}

pub struct Treasury {
    root: PathBuf,
    http: reqwest::Client,
    /// The bloctime module — the only place BLOC weights come from.
    pub bloctime: String,
    pub activator: String,
}

impl Treasury {
    pub fn new(data_dir: &Path) -> Self {
        let root = data_dir.join("treasury");
        let _ = std::fs::create_dir_all(root.join("allocations"));
        Self {
            root,
            http: reqwest::Client::builder()
                .timeout(std::time::Duration::from_secs(60))
                .build()
                .expect("http client"),
            bloctime: std::env::var("DEFI_BLOCTIME_URL")
                .unwrap_or_else(|_| "http://localhost:8851".into()),
            activator: std::env::var("DEFI_ACTIVATOR_URL")
                .unwrap_or_else(|_| "http://localhost:9000".into()),
        }
    }

    // ── the ledger ────────────────────────────────────────────────────────

    fn alloc_path(&self, id: &str) -> PathBuf {
        self.root.join("allocations").join(format!("{}.json", sanitize(id)))
    }

    pub fn list(&self) -> Vec<Allocation> {
        let mut out = Vec::new();
        if let Ok(entries) = std::fs::read_dir(self.root.join("allocations")) {
            for entry in entries.flatten() {
                if let Ok(body) = std::fs::read_to_string(entry.path()) {
                    if let Ok(a) = serde_json::from_str::<Allocation>(&body) {
                        out.push(a);
                    }
                }
            }
        }
        out.sort_by(|a, b| b.created.cmp(&a.created));
        out
    }

    pub fn get(&self, id: &str) -> Option<Allocation> {
        serde_json::from_str(&std::fs::read_to_string(self.alloc_path(id)).ok()?).ok()
    }

    pub fn save(&self, a: &Allocation) -> Result<(), String> {
        std::fs::write(
            self.alloc_path(&a.id),
            serde_json::to_string_pretty(a).map_err(|e| e.to_string())?,
        )
        .map_err(|e| e.to_string())
    }

    pub fn delete(&self, id: &str) -> Result<(), String> {
        std::fs::remove_file(self.alloc_path(id)).map_err(|e| e.to_string())
    }

    /// Record a choice off the yields table. The APY, base APY and TVL are
    /// copied in as they were at this moment and never refreshed — the point of
    /// the row is to say what the decision was made on, so that a rate that
    /// later collapses is visible as a change rather than quietly erased.
    pub fn choose(&self, body: &Value, owner: &str, now: u64) -> Result<Allocation, String> {
        let s = |k: &str| {
            body.get(k)
                .and_then(|v| v.as_str())
                .map(|v| v.trim().to_string())
                .unwrap_or_default()
        };
        let f = |k: &str| {
            body.get(k)
                .and_then(|v| v.as_f64().or_else(|| v.as_str().and_then(|s| s.parse().ok())))
                .unwrap_or(0.0)
        };

        let amount = s("amount");
        let parsed: f64 = amount
            .parse()
            .map_err(|_| format!("'{amount}' is not an amount — plain decimal digits, please"))?;
        if parsed <= 0.0 {
            return Err("an allocation of nothing is not a choice".into());
        }
        let term_weeks = body
            .get("term_weeks")
            .and_then(|v| v.as_u64().or_else(|| v.as_str().and_then(|s| s.parse().ok())))
            .unwrap_or(4) as u32;
        if term_weeks == 0 || term_weeks > 520 {
            return Err("term_weeks must be between 1 and 520".into());
        }

        let id = body
            .get("id")
            .and_then(|v| v.as_str())
            .map(|v| v.to_string())
            .unwrap_or_else(|| format!("a-{now}-{}", &sanitize(&s("pool"))[..sanitize(&s("pool")).len().min(8)]));

        // Editing an existing row is fine while it is a plan; once it is locked
        // the terms are on chain and this ledger does not get to disagree.
        if let Some(prev) = self.get(&id) {
            if prev.owner != owner {
                return Err("that allocation belongs to someone else".into());
            }
            if prev.status == "locked" {
                return Err("that allocation is locked on chain — its terms cannot be edited".into());
            }
        }

        let asset_address = body
            .get("asset_address")
            .and_then(|v| v.as_str())
            .filter(|v| !v.trim().is_empty())
            .map(normalize_address)
            .transpose()?;

        let allocation = Allocation {
            id,
            owner: owner.to_string(),
            created: self.get(&s("id")).map(|p| p.created).unwrap_or(now),
            updated: now,
            pool: s("pool"),
            project: s("project"),
            chain: s("chain"),
            symbol: s("symbol"),
            apy_at_choice: f("apy"),
            apy_base_at_choice: f("apy_base"),
            tvl_at_choice: f("tvl_usd"),
            amount,
            asset: if s("asset").is_empty() { s("symbol") } else { s("asset") },
            asset_address,
            term_weeks,
            return_principal: body
                .get("return_principal")
                .and_then(|v| v.as_bool())
                .unwrap_or(false),
            status: planned(),
            network: None,
            treasury: None,
            lock_id: None,
            tx: None,
            note: s("note"),
        };
        self.save(&allocation)?;
        Ok(allocation)
    }

    /// Point this node at a deployed ModBlocTimeTreasury.
    pub fn bind(&self, body: &Value, who: &str, now: u64) -> Result<Binding, String> {
        let address = body
            .get("address")
            .and_then(|v| v.as_str())
            .ok_or("'address' is required — the deployed treasury")?;
        let network = body
            .get("network")
            .and_then(|v| v.as_str())
            .unwrap_or("base-sepolia")
            .trim()
            .to_string();
        let binding = Binding {
            address: normalize_address(address)?,
            network,
            asset: body
                .get("asset")
                .and_then(|v| v.as_str())
                .map(normalize_address)
                .transpose()?
                .unwrap_or_default(),
            weight: body
                .get("weight")
                .and_then(|v| v.as_str())
                .map(normalize_address)
                .transpose()?
                .unwrap_or_default(),
            decimals: body
                .get("decimals")
                .and_then(|v| v.as_u64().or_else(|| v.as_str().and_then(|s| s.parse().ok())))
                .unwrap_or(18) as u32,
            bound_by: who.to_string(),
            bound_at: now,
        };
        self.set_binding(&binding)?;
        Ok(binding)
    }

    // ── the binding, and who is eligible ──────────────────────────────────

    pub fn binding(&self) -> Binding {
        std::fs::read_to_string(self.root.join("binding.json"))
            .ok()
            .and_then(|b| serde_json::from_str(&b).ok())
            .unwrap_or_default()
    }

    pub fn set_binding(&self, b: &Binding) -> Result<(), String> {
        std::fs::write(
            self.root.join("binding.json"),
            serde_json::to_string_pretty(b).map_err(|e| e.to_string())?,
        )
        .map_err(|e| e.to_string())
    }

    /// The addresses whose BLOC this node watches. The contract keeps its own
    /// registered set on chain; this is the local mirror, so a preview works
    /// before anything is deployed.
    pub fn participants(&self) -> Vec<String> {
        std::fs::read_to_string(self.root.join("participants.json"))
            .ok()
            .and_then(|b| serde_json::from_str::<Vec<String>>(&b).ok())
            .unwrap_or_default()
    }

    pub fn set_participants(&self, list: &[String]) -> Result<(), String> {
        std::fs::write(
            self.root.join("participants.json"),
            serde_json::to_string_pretty(list).map_err(|e| e.to_string())?,
        )
        .map_err(|e| e.to_string())
    }

    pub fn add_participant(&self, address: &str) -> Result<Vec<String>, String> {
        let address = normalize_address(address)?;
        let mut list = self.participants();
        if !list.iter().any(|a| a.eq_ignore_ascii_case(&address)) {
            list.push(address);
        }
        self.set_participants(&list)?;
        Ok(list)
    }

    pub fn remove_participant(&self, address: &str) -> Result<Vec<String>, String> {
        let address = normalize_address(address)?;
        let list: Vec<String> = self
            .participants()
            .into_iter()
            .filter(|a| !a.eq_ignore_ascii_case(&address))
            .collect();
        self.set_participants(&list)?;
        Ok(list)
    }

    // ── BlocTime, through the module that owns it ─────────────────────────

    async fn bloctime_get(&self, route: &str) -> Result<Value, String> {
        let url = format!("{}{route}", self.bloctime);
        let send = |url: String| async move {
            self.http
                .get(&url)
                .send()
                .await
                .map_err(|e| e.to_string())?
                .json::<Value>()
                .await
                .map_err(|e| e.to_string())
        };
        match send(url.clone()).await {
            Ok(v) => Ok(v),
            Err(first) => {
                self.wake().await;
                send(url).await.map_err(|second| {
                    format!(
                        "bloctime is not answering at {} ({first}; after waking it: {second}) \
                         — start it with `m bloctime/serve`",
                        self.bloctime
                    )
                })
            }
        }
    }

    async fn wake(&self) {
        if self.activator.is_empty() {
            return;
        }
        let _ = self
            .http
            .get(format!("{}/api/bloctime/health", self.activator))
            .timeout(std::time::Duration::from_secs(20))
            .send()
            .await;
    }

    async fn bloc_balance(&self, address: &str) -> Result<f64, String> {
        let body = self
            .http
            .post(format!("{}/overview", self.bloctime))
            .json(&json!({ "address": address }))
            .send()
            .await
            .map_err(|e| e.to_string())?
            .json::<Value>()
            .await
            .map_err(|e| e.to_string())?;
        let raw = body
            .pointer("/result/blocBalance")
            .and_then(|v| v.as_str())
            .unwrap_or("0");
        Ok(wei_to_f64(raw))
    }

    /// Who is eligible, how much BLOC each holds, and therefore each one's
    /// share of a week. The shares are of the WATCHED SET, not of total supply
    /// — same rule the contract uses, and the difference is reported so nobody
    /// reads a 40% share as 40% of BlocTime.
    pub async fn holders(&self) -> Result<Value, String> {
        let stats = self.bloctime_get("/stats").await?;
        let supply = wei_to_f64(
            stats
                .pointer("/result/totalSupply")
                .and_then(|v| v.as_str())
                .unwrap_or("0"),
        );
        let watched = self.participants();
        let mut rows = Vec::new();
        let mut total = 0.0;
        for address in &watched {
            let bloc = self.bloc_balance(address).await.unwrap_or(0.0);
            total += bloc;
            rows.push(json!({ "address": address, "bloc": round4(bloc) }));
        }
        for row in rows.iter_mut() {
            let bloc = row.get("bloc").and_then(|v| v.as_f64()).unwrap_or(0.0);
            row["share_of_pot_pct"] = json!(if total > 0.0 { round4(bloc / total * 100.0) } else { 0.0 });
            row["share_of_supply_pct"] =
                json!(if supply > 0.0 { round4(bloc / supply * 100.0) } else { 0.0 });
        }
        rows.sort_by(|a, b| {
            b.get("bloc")
                .and_then(|v| v.as_f64())
                .partial_cmp(&a.get("bloc").and_then(|v| v.as_f64()))
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        Ok(json!({
            "holders": rows,
            "registered_weight": round4(total),
            "bloc_total_supply": round4(supply),
            "uncovered_supply": round4((supply - total).max(0.0)),
            "bloctime": {
                "module": self.bloctime,
                "contract": stats.pointer("/result/address").cloned().unwrap_or(Value::Null),
                "network": stats.pointer("/result/network").cloned().unwrap_or(Value::Null),
                "explorer": stats.pointer("/result/explorer").cloned().unwrap_or(Value::Null),
            },
            "note": "shares are of the watched set — BLOC held outside it earns nothing here, \
                     exactly as the contract's registered set behaves",
        }))
    }

    // ── the schedule ──────────────────────────────────────────────────────

    /// The next `weeks` payout windows, and what each one would release from
    /// the ledger. Everything downstream of an APY is marked projected.
    pub fn schedule(&self, weeks: usize, now: u64) -> Value {
        let allocations = self.list();
        let mut windows = Vec::new();
        let mut at = next_window(now);
        for index in 0..weeks.clamp(1, 52) {
            let mut principal = 0.0;
            let mut projected_yield = 0.0;
            let mut contributors = Vec::new();
            for a in &allocations {
                if a.status != "locked" {
                    continue;
                }
                let elapsed = a.weeks_elapsed(now) as usize + index;
                if elapsed >= a.term_weeks as usize {
                    continue;
                }
                let (p, y) = a.weekly();
                principal += p;
                projected_yield += y;
                contributors.push(json!({
                    "id": a.id, "project": a.project, "symbol": a.symbol,
                    "principal": round4(p), "yield_projected": round4(y),
                    "week": elapsed + 1, "of": a.term_weeks,
                }));
            }
            windows.push(json!({
                "week": index + 1,
                "at": at,
                "at_iso": iso(at),
                "opens": "Friday 12:00 EST (17:00 UTC)",
                "principal_released": round4(principal),
                "yield_projected": round4(projected_yield),
                "total_projected": round4(principal + projected_yield),
                "from": contributors,
            }));
            at += WEEK;
        }
        json!({
            "windows": windows,
            "period_seconds": WEEK,
            "offset_seconds": OFFSET,
            "next_at": next_window(now),
            "next_iso": iso(next_window(now)),
            "seconds_until_next": next_window(now).saturating_sub(now),
            "clock": "BlocTime's — DISTRIBUTION_PERIOD 7 days, DISTRIBUTION_OFFSET 1 day 17 hours, \
                      pinned to EST year round",
        })
    }

    /// Next Friday, in full: what goes in, who it splits across, and what each
    /// address would get.
    pub async fn preview(&self, now: u64) -> Result<Value, String> {
        let schedule = self.schedule(1, now);
        let week = schedule
            .pointer("/windows/0")
            .cloned()
            .unwrap_or_else(|| json!({}));
        let principal = week.get("principal_released").and_then(|v| v.as_f64()).unwrap_or(0.0);
        let projected = week.get("yield_projected").and_then(|v| v.as_f64()).unwrap_or(0.0);
        let pot = principal + projected;

        let holders = self.holders().await?;
        let weight = holders.get("registered_weight").and_then(|v| v.as_f64()).unwrap_or(0.0);
        let splits: Vec<Value> = holders
            .get("holders")
            .and_then(|h| h.as_array())
            .map(|rows| {
                rows.iter()
                    .map(|r| {
                        let bloc = r.get("bloc").and_then(|v| v.as_f64()).unwrap_or(0.0);
                        let share = if weight > 0.0 { bloc / weight } else { 0.0 };
                        json!({
                            "address": r.get("address"),
                            "bloc": bloc,
                            "share_pct": round4(share * 100.0),
                            "principal": round4(principal * share),
                            "yield_projected": round4(projected * share),
                            "total_projected": round4(pot * share),
                        })
                    })
                    .collect()
            })
            .unwrap_or_default();

        Ok(json!({
            "at": next_window(now),
            "at_iso": iso(next_window(now)),
            "seconds_until": next_window(now).saturating_sub(now),
            "pot_principal": round4(principal),
            "pot_yield_projected": round4(projected),
            "pot_total_projected": round4(pot),
            "splits": splits,
            "registered_weight": weight,
            "from": week.get("from").cloned().unwrap_or(json!([])),
            "basis": if splits.is_empty() {
                "nobody is watched yet — add an address with POST /treasury/participants, \
                 or register() on the deployed treasury"
            } else {
                "pro-rata by BLOC across the watched set, at the balances held right now"
            },
            "projected": true,
            "why_projected": "principal released is arithmetic on the ledger and is exact; \
                              the yield line extrapolates the APY at the time of choosing and \
                              will not be what actually lands",
        }))
    }

    // ── the deployed contract, through the eth module ─────────────────────

    /// Read `summary()` off a bound treasury. Everything here is on-chain fact,
    /// so it is returned under its own key and never merged into the ledger's
    /// projections.
    pub async fn onchain(&self, dex: &crate::dex::Dex, token: Option<&str>) -> Result<Value, String> {
        let binding = self.binding();
        if binding.address.is_empty() {
            return Err(
                "no treasury is bound on this node yet — deploy the BlocTime Treasury block, \
                 then POST /treasury/bind {address, network}"
                    .into(),
            );
        }
        let decimals = if binding.decimals == 0 { 18 } else { binding.decimals };
        let read = dex
            .peer(
                "eth",
                "eth_read",
                json!({
                    "address": binding.address,
                    "function": "summary",
                    "network": binding.network,
                    "abi": abi(),
                    "args": [],
                }),
                token,
            )
            .await?;

        // The eth module stringifies anything over 2^53, so every number here
        // is parsed out of a string rather than an f64.
        let s = read.get("result").cloned().unwrap_or(Value::Null);
        let at = |key: &str, index: usize| -> String {
            s.get(key)
                .or_else(|| s.get(index))
                .map(stringify_uint)
                .unwrap_or_else(|| "0".into())
        };
        let due = s
            .get("due")
            .or_else(|| s.get(7))
            .and_then(|v| v.as_bool())
            .unwrap_or(false);
        let next_at: u64 = at("nextAt", 6).parse().unwrap_or(0);

        Ok(json!({
            "treasury": binding.address,
            "network": binding.network,
            "asset": binding.asset,
            "weight_token": binding.weight,
            "balance": units(&at("balance", 0), decimals),
            "locked": units(&at("locked", 1), decimals),
            "owed_unclaimed": units(&at("owed", 2), decimals),
            "payout_this_week": units(&at("payoutNow", 3), decimals),
            "registered_weight_bloc": units(&at("weightRegistered", 4), 18),
            "registered_holders": at("holders", 5),
            "next_distribution_at": next_at,
            "next_distribution_iso": iso(next_at),
            "distribution_due": due,
            "distributed_total": units(&at("paidTotal", 8), decimals),
            "weeks_paid": at("weeksPaid", 9),
            "read_by": format!("eth_read summary() on {}", binding.address),
        }))
    }

    /// Lock an allocation into the bound treasury for real: approve, then
    /// `lock(amount, termWeeks, returnPrincipal)`.
    ///
    /// The guard stacks rather than being reinvented — this refuses a non-test
    /// network without `confirm`, and the eth module refuses again underneath
    /// with its own rule and its own locked keystore.
    pub async fn lock_onchain(
        &self,
        dex: &crate::dex::Dex,
        allocation: &Allocation,
        account: &str,
        confirm: bool,
        password: Option<&Value>,
        token: Option<&str>,
    ) -> Result<Value, String> {
        let binding = self.binding();
        if binding.address.is_empty() {
            return Err("no treasury is bound — POST /treasury/bind {address, network} first".into());
        }
        let asset = allocation
            .asset_address
            .clone()
            .filter(|a| a.starts_with("0x"))
            .or_else(|| Some(binding.asset.clone()).filter(|a| a.starts_with("0x")))
            .ok_or("this allocation has no asset address, and none is bound to the treasury")?;
        let decimals = if binding.decimals == 0 { 18 } else { binding.decimals };
        let units = crate::dex::to_base_units(&allocation.amount, decimals)?;

        let testnet = crate::dex::chain(&binding.network)
            .map(|c| c.testnet)
            .unwrap_or(binding.network.contains("sepolia") || binding.network.contains("test"));
        if !testnet && !confirm {
            return Err(format!(
                "{} is not a testnet and this would move {} {} you cannot recall — \
                 pass confirm=true to mean it",
                binding.network, allocation.amount, allocation.asset
            ));
        }

        let mut approve = json!({
            "account": account, "token": asset, "spender": binding.address,
            "amount": format!("{units}wei"), "network": binding.network, "confirm": confirm,
        });
        if let Some(p) = password {
            approve["password"] = p.clone();
        }
        let approval = dex
            .peer("eth", "eth_approve", approve, token)
            .await
            .map_err(|e| format!("the approval failed, so nothing was locked: {e}"))?;

        let mut call = json!({
            "account": account,
            "address": binding.address,
            "function": "lock",
            "args": [units.to_string(), allocation.term_weeks, allocation.return_principal],
            "network": binding.network,
            "abi": abi(),
            "confirm": confirm,
        });
        if let Some(p) = password {
            call["password"] = p.clone();
        }
        let result = dex.peer("eth", "eth_write", call, token).await?;
        Ok(json!({
            "locked": true,
            "treasury": binding.address,
            "network": binding.network,
            "amount": allocation.amount,
            "term_weeks": allocation.term_weeks,
            "return_principal": allocation.return_principal,
            "approval": approval,
            "result": result,
            "executed_by": format!("eth_write lock() on {}", binding.address),
        }))
    }

    /// Call `distribute()`. Permissionless on chain, and the contract itself
    /// enforces the window — this only refuses early so a wallet is not asked
    /// to pay gas for a revert it can see coming.
    pub async fn distribute_onchain(
        &self,
        dex: &crate::dex::Dex,
        account: &str,
        confirm: bool,
        password: Option<&Value>,
        token: Option<&str>,
        now: u64,
    ) -> Result<Value, String> {
        let binding = self.binding();
        if binding.address.is_empty() {
            return Err("no treasury is bound — POST /treasury/bind {address, network} first".into());
        }
        let state = self.onchain(dex, token).await?;
        if !state.get("distribution_due").and_then(|v| v.as_bool()).unwrap_or(false) {
            let at = state.get("next_distribution_at").and_then(|v| v.as_u64()).unwrap_or(next_window(now));
            return Err(format!(
                "the window is shut — the next one opens {} ({}s from now). \
                 distribute() would revert with NOT_DISTRIBUTION_TIME.",
                iso(at),
                at.saturating_sub(now)
            ));
        }
        let mut call = json!({
            "account": account, "address": binding.address, "function": "distribute",
            "args": [], "network": binding.network, "abi": abi(), "confirm": confirm,
        });
        if let Some(p) = password {
            call["password"] = p.clone();
        }
        let result = dex.peer("eth", "eth_write", call, token).await?;
        Ok(json!({
            "distributed": true,
            "treasury": binding.address,
            "network": binding.network,
            "window": window_start(now),
            "window_iso": iso(window_start(now)),
            "before": state,
            "result": result,
        }))
    }

    /// Pull whatever the weekly splits have already credited to this account.
    pub async fn claim_onchain(
        &self,
        dex: &crate::dex::Dex,
        account: &str,
        confirm: bool,
        password: Option<&Value>,
        token: Option<&str>,
    ) -> Result<Value, String> {
        let binding = self.binding();
        if binding.address.is_empty() {
            return Err("no treasury is bound — POST /treasury/bind {address, network} first".into());
        }
        let mut call = json!({
            "account": account, "address": binding.address, "function": "claim",
            "args": [], "network": binding.network, "abi": abi(), "confirm": confirm,
        });
        if let Some(p) = password {
            call["password"] = p.clone();
        }
        Ok(json!({
            "treasury": binding.address,
            "network": binding.network,
            "result": dex.peer("eth", "eth_write", call, token).await?,
        }))
    }

    /// Put an address into the contract's own registered set, so it is eligible
    /// for the on-chain split rather than only the local preview.
    pub async fn register_onchain(
        &self,
        dex: &crate::dex::Dex,
        account: &str,
        who: Option<&str>,
        confirm: bool,
        password: Option<&Value>,
        token: Option<&str>,
    ) -> Result<Value, String> {
        let binding = self.binding();
        if binding.address.is_empty() {
            return Err("no treasury is bound — POST /treasury/bind {address, network} first".into());
        }
        let (function, args) = match who {
            Some(address) => ("registerFor", json!([normalize_address(address)?])),
            None => ("register", json!([])),
        };
        let mut call = json!({
            "account": account, "address": binding.address, "function": function,
            "args": args, "network": binding.network, "abi": abi(), "confirm": confirm,
        });
        if let Some(p) = password {
            call["password"] = p.clone();
        }
        Ok(json!({
            "treasury": binding.address,
            "registered": who.unwrap_or(account),
            "result": dex.peer("eth", "eth_write", call, token).await?,
        }))
    }

    /// The whole desk in one read: the ledger, the clock, and — when there is
    /// one — the contract.
    pub async fn desk(&self, dex: &crate::dex::Dex, now: u64, token: Option<&str>) -> Value {
        let allocations = self.list();
        let locked: f64 = allocations
            .iter()
            .filter(|a| a.status == "locked")
            .map(|a| a.amount_f64())
            .sum();
        let planned: f64 = allocations
            .iter()
            .filter(|a| a.status == "planned")
            .map(|a| a.amount_f64())
            .sum();
        let binding = self.binding();
        let onchain = if binding.address.is_empty() {
            Value::Null
        } else {
            match self.onchain(dex, token).await {
                Ok(v) => v,
                Err(e) => json!({ "error": e, "treasury": binding.address }),
            }
        };
        json!({
            "allocations": allocations.iter().map(|a| a.view(now)).collect::<Vec<_>>(),
            "locked_total": round4(locked),
            "planned_total": round4(planned),
            "count": allocations.len(),
            "schedule": self.schedule(4, now),
            "binding": binding,
            "onchain": onchain,
            "watched": self.participants(),
            "bloctime": self.bloctime,
            "contract_block": "treasury",
            "note": "an allocation is a plan until status is 'locked'; locking is a real \
                     transaction the eth module signs against a deployed BlocTime Treasury",
        })
    }
}

// ── helpers ────────────────────────────────────────────────────────────────

fn sanitize(id: &str) -> String {
    id.chars()
        .filter(|c| c.is_ascii_alphanumeric() || *c == '-' || *c == '_')
        .take(96)
        .collect()
}

pub fn normalize_address(address: &str) -> Result<String, String> {
    let a = address.trim();
    if a.len() == 42 && a.starts_with("0x") && a[2..].chars().all(|c| c.is_ascii_hexdigit()) {
        Ok(a.to_lowercase())
    } else {
        Err(format!("'{address}' is not an 0x address"))
    }
}

/// 18-decimal wei string → whole tokens. Only ever for display, and only after
/// the exact string has been kept somewhere else.
fn wei_to_f64(raw: &str) -> f64 {
    raw.trim().parse::<f64>().unwrap_or(0.0) / 1e18
}

/// A uint the eth module may have handed back as a string, a number, or a
/// hex string. All three land as a decimal string, digits intact.
fn stringify_uint(value: &Value) -> String {
    match value {
        Value::String(s) => {
            if let Some(hex) = s.strip_prefix("0x") {
                u128::from_str_radix(hex, 16).map(|v| v.to_string()).unwrap_or_else(|_| s.clone())
            } else {
                s.clone()
            }
        }
        Value::Number(n) => n.to_string(),
        _ => "0".into(),
    }
}

fn units(base: &str, decimals: u32) -> Value {
    match base.parse::<u128>() {
        Ok(v) => json!(crate::dex::from_base_units(v, decimals)),
        Err(_) => json!(base),
    }
}

fn round4(value: f64) -> f64 {
    if !value.is_finite() {
        return 0.0;
    }
    (value * 10_000.0).round() / 10_000.0
}

/// Unix seconds → an ISO-8601 UTC timestamp, without pulling in a date crate
/// for the one thing this module needs a calendar for.
pub fn iso(ts: u64) -> String {
    if ts == 0 {
        return String::new();
    }
    let days = ts / 86_400;
    let rem = ts % 86_400;
    let (h, mi, s) = (rem / 3600, (rem % 3600) / 60, rem % 60);
    // Civil-from-days (Howard Hinnant's algorithm), shifted to a March-based year.
    let z = days as i64 + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    format!("{y:04}-{m:02}-{d:02}T{h:02}:{mi:02}:{s:02}Z")
}

/// The parts of ModBlocTimeTreasury this module calls. Deliberately not the
/// whole ABI — a console reads `summary()`, and a wallet locks, distributes,
/// claims and registers. Anything else belongs to the deployment plan.
pub fn abi() -> Value {
    json!([
        {
            "type": "function", "name": "summary", "stateMutability": "view", "inputs": [],
            "outputs": [{
                "type": "tuple", "name": "", "components": [
                    { "type": "uint256", "name": "balance" },
                    { "type": "uint256", "name": "locked" },
                    { "type": "uint256", "name": "owed" },
                    { "type": "uint256", "name": "payoutNow" },
                    { "type": "uint256", "name": "weightRegistered" },
                    { "type": "uint256", "name": "holders" },
                    { "type": "uint256", "name": "nextAt" },
                    { "type": "bool", "name": "due" },
                    { "type": "uint256", "name": "paidTotal" },
                    { "type": "uint256", "name": "weeksPaid" }
                ]
            }]
        },
        {
            "type": "function", "name": "lock", "stateMutability": "nonpayable",
            "inputs": [
                { "type": "uint256", "name": "amount" },
                { "type": "uint32", "name": "termWeeks" },
                { "type": "bool", "name": "returnPrincipal" }
            ],
            "outputs": [{ "type": "uint256", "name": "id" }]
        },
        {
            "type": "function", "name": "distribute", "stateMutability": "nonpayable",
            "inputs": [], "outputs": [{ "type": "uint256", "name": "amount" }]
        },
        {
            "type": "function", "name": "claim", "stateMutability": "nonpayable",
            "inputs": [], "outputs": [{ "type": "uint256", "name": "amount" }]
        },
        {
            "type": "function", "name": "register", "stateMutability": "nonpayable",
            "inputs": [], "outputs": []
        },
        {
            "type": "function", "name": "registerFor", "stateMutability": "nonpayable",
            "inputs": [{ "type": "address", "name": "who" }], "outputs": []
        },
        {
            "type": "function", "name": "claimable", "stateMutability": "view",
            "inputs": [{ "type": "address", "name": "" }],
            "outputs": [{ "type": "uint256", "name": "" }]
        },
        {
            "type": "function", "name": "nextDistributionTime", "stateMutability": "view",
            "inputs": [], "outputs": [{ "type": "uint256", "name": "" }]
        }
    ])
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 2026-08-28 is a Friday. 17:00 UTC that day is 1787936400.
    const FRIDAY_1700: u64 = 1_787_936_400;

    #[test]
    fn the_window_is_bloctimes_window() {
        // Exactly on the boundary, the window has opened.
        assert_eq!(window_start(FRIDAY_1700), FRIDAY_1700);
        // A second earlier belongs to the previous week.
        assert_eq!(window_start(FRIDAY_1700 - 1), FRIDAY_1700 - WEEK);
        // And the next one is seven days out, not "seven days from now".
        assert_eq!(next_window(FRIDAY_1700 + 3600), FRIDAY_1700 + WEEK);
    }

    #[test]
    fn every_window_lands_on_friday_1700_utc() {
        let mut at = next_window(FRIDAY_1700);
        for _ in 0..60 {
            let stamp = iso(at);
            assert!(stamp.ends_with("T17:00:00Z"), "{stamp} is not 17:00 UTC");
            // Unix day 0 was a Thursday, so Friday is day % 7 == 1.
            assert_eq!((at / 86_400) % 7, 1, "{stamp} is not a Friday");
            at += WEEK;
        }
    }

    fn allocation(amount: &str, weeks: u32, escrow: bool, apy: f64) -> Allocation {
        Allocation {
            id: "a1".into(), owner: "0x1".into(), created: 0, updated: FRIDAY_1700,
            pool: "p".into(), project: "aave".into(), chain: "Base".into(), symbol: "USDC".into(),
            apy_at_choice: apy, apy_base_at_choice: apy, tvl_at_choice: 1e8,
            amount: amount.into(), asset: "USDC".into(), asset_address: None,
            term_weeks: weeks, return_principal: escrow, status: "locked".into(),
            network: None, treasury: None, lock_id: None, tx: None, note: String::new(),
        }
    }

    #[test]
    fn a_streaming_lock_pays_its_principal_out_in_equal_slices() {
        let a = allocation("5200", 52, false, 0.0);
        let (principal, _) = a.weekly();
        assert_eq!(principal, 100.0);
    }

    #[test]
    fn an_escrowed_lock_pays_only_yield() {
        let a = allocation("5200", 52, true, 10.0);
        let (principal, yield_) = a.weekly();
        assert_eq!(principal, 0.0);
        assert!((yield_ - 10.0).abs() < 1e-9, "10% of 5200 over 52 weeks is 10/wk, got {yield_}");
    }

    #[test]
    fn the_term_runs_out_rather_than_paying_forever() {
        let a = allocation("400", 4, false, 0.0);
        assert_eq!(a.weeks_elapsed(FRIDAY_1700), 0);
        assert_eq!(a.weeks_elapsed(FRIDAY_1700 + 2 * WEEK), 2);
        assert_eq!(a.weeks_elapsed(FRIDAY_1700 + 9 * WEEK), 4);
    }

    #[test]
    fn a_uint_survives_whichever_shape_the_eth_module_sends_it_in() {
        assert_eq!(stringify_uint(&json!("123456789012345678901234567890")), "123456789012345678901234567890");
        assert_eq!(stringify_uint(&json!(42)), "42");
        assert_eq!(stringify_uint(&json!("0xff")), "255");
    }
}
