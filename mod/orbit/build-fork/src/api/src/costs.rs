//! Per-task cost metering — what each job actually cost, and what that adds
//! up to per user.
//!
//! Two jobs here, deliberately kept apart:
//!
//! 1. **Pricing one task.** The Claude CLI already reports `total_cost_usd` on
//!    its `result` event, so for the default agent we take the number the
//!    thing that spent the money says it spent. Codex reports raw token
//!    counts instead, so those are priced against the table below. The table
//!    is also the fallback when a `result` event arrives with usage but no
//!    cost (older CLIs, subscription auth).
//!
//! 2. **Aggregating.** `/costs` answers "what has everyone spent, and what is
//!    the average per user this month" straight off the jobs DB — one row per
//!    task, so the ledger is the audit trail. That average is the settlement
//!    oracle for the `costmarket` module's prediction market, which is why it
//!    is public and why the epoch boundaries are calendar months in UTC:
//!    two parties have to be able to compute the same number.
//!
//! Everything is USD micro-units (`usd6`, 1e-6 of a dollar), matching
//! credits.rs so the two can be added without a conversion step.
//!
//! Policy lives off-tree at ~/.mod/{module}/costs.json — see `Policy`.

use serde::{Deserialize, Serialize};

// ── Pricing ──────────────────────────────────────────────────────────

/// Per-million-token prices in micro-dollars. Cache writes bill at 1.25× the
/// input rate (5-minute TTL — the CLI's default) and cache reads at 0.1×,
/// which is why only input/output are stored per model.
struct ModelPrice {
    input_per_mtok: u64,
    output_per_mtok: u64,
}

/// Longest-prefix match, so dated snapshots and `[1m]` suffixes resolve to
/// their family without a table entry each.
fn price_for(model: &str) -> ModelPrice {
    let m = model.trim().to_lowercase();
    let m = m.strip_suffix("[1m]").unwrap_or(&m).trim().to_string();
    // (prefix, input $/MTok, output $/MTok) — most specific first.
    const TABLE: &[(&str, u64, u64)] = &[
        ("claude-fable-5", 10_000_000, 50_000_000),
        ("claude-mythos-5", 10_000_000, 50_000_000),
        ("claude-mythos-preview", 10_000_000, 50_000_000),
        ("claude-opus-5", 5_000_000, 25_000_000),
        ("claude-opus-4", 5_000_000, 25_000_000),
        ("claude-opus", 5_000_000, 25_000_000),
        ("claude-sonnet-5", 3_000_000, 15_000_000),
        ("claude-sonnet-4", 3_000_000, 15_000_000),
        ("claude-sonnet", 3_000_000, 15_000_000),
        ("claude-haiku-4-5", 1_000_000, 5_000_000),
        ("claude-haiku", 1_000_000, 5_000_000),
        ("fable", 10_000_000, 50_000_000),
        ("opus", 5_000_000, 25_000_000),
        ("sonnet", 3_000_000, 15_000_000),
        ("haiku", 1_000_000, 5_000_000),
    ];
    for (prefix, input, output) in TABLE {
        if m.starts_with(prefix) {
            return ModelPrice {
                input_per_mtok: *input,
                output_per_mtok: *output,
            };
        }
    }
    // Unknown model: price it as the default agent model rather than free, so
    // an unrecognised name can't silently become an unmetered escape hatch.
    ModelPrice {
        input_per_mtok: 5_000_000,
        output_per_mtok: 25_000_000,
    }
}

/// Token counts for one turn, as reported by the agent.
#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize)]
pub struct Usage {
    pub input: u64,
    /// Tokens served from the prompt cache (billed at 0.1× input).
    pub cache_read: u64,
    /// Tokens written to the prompt cache (billed at 1.25× input).
    pub cache_write: u64,
    pub output: u64,
}

impl Usage {
    /// Every token that entered the model, cached or not — what the console
    /// shows as "in".
    pub fn total_input(&self) -> u64 {
        self.input + self.cache_read + self.cache_write
    }
}

/// Price a turn from token counts. Rounds down at micro-dollar resolution;
/// a sub-microdollar turn costs zero, which is the honest answer at this
/// precision.
pub fn price_usd6(model: &str, u: &Usage) -> u128 {
    let p = price_for(model);
    let per = |tokens: u64, rate_per_mtok: u64, numerator: u128, denominator: u128| -> u128 {
        (tokens as u128) * (rate_per_mtok as u128) * numerator / (1_000_000u128 * denominator)
    };
    per(u.input, p.input_per_mtok, 1, 1)
        + per(u.cache_write, p.input_per_mtok, 5, 4) // 1.25×
        + per(u.cache_read, p.input_per_mtok, 1, 10) // 0.10×
        + per(u.output, p.output_per_mtok, 1, 1)
}

// ── One task's cost ──────────────────────────────────────────────────

/// What a finished task cost, and how we know.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct TaskCost {
    pub usd6: u128,
    pub usage: Usage,
    pub duration_ms: u64,
    /// "reported" — the agent told us the dollar figure; "priced" — we
    /// computed it from token counts. Kept per row so a later pricing change
    /// can be identified as ours rather than the agent's.
    pub source: String,
}

fn n(v: &serde_json::Value, key: &str) -> u64 {
    v.get(key).and_then(|x| x.as_u64()).unwrap_or(0)
}

/// Read a Claude CLI `result` event. The CLI reports `total_cost_usd` even on
/// subscription auth, so that is preferred; token counts are still recorded
/// because they are what the average-cost market is ultimately about.
pub fn from_claude_result(v: &serde_json::Value, model: &str) -> Option<TaskCost> {
    if v.get("type").and_then(|t| t.as_str()) != Some("result") {
        return None;
    }
    let usage = v.get("usage").map(|u| Usage {
        input: n(u, "input_tokens"),
        cache_read: n(u, "cache_read_input_tokens"),
        cache_write: n(u, "cache_creation_input_tokens"),
        output: n(u, "output_tokens"),
    })
    .unwrap_or_default();

    let reported = v
        .get("total_cost_usd")
        .and_then(|c| c.as_f64())
        .filter(|c| *c > 0.0)
        .map(|c| (c * 1_000_000.0).round() as u128);

    let (usd6, source) = match reported {
        Some(c) => (c, "reported"),
        None => (price_usd6(model, &usage), "priced"),
    };

    Some(TaskCost {
        usd6,
        usage,
        duration_ms: n(v, "duration_ms"),
        source: source.to_string(),
    })
}

/// Read a codex `turn.completed` event — token counts only, so always priced.
pub fn from_codex_turn(v: &serde_json::Value, model: &str) -> Option<TaskCost> {
    if v.get("type").and_then(|t| t.as_str()) != Some("turn.completed") {
        return None;
    }
    let u = v.get("usage")?;
    let usage = Usage {
        input: n(u, "input_tokens"),
        cache_read: n(u, "cached_input_tokens"),
        cache_write: 0,
        output: n(u, "output_tokens"),
    };
    Some(TaskCost {
        usd6: price_usd6(model, &usage),
        usage,
        duration_ms: 0,
        source: "priced".to_string(),
    })
}

/// Codex reports usage once per turn and a job may run several; costs add.
impl TaskCost {
    pub fn merge(&mut self, other: &TaskCost) {
        self.usd6 += other.usd6;
        self.usage.input += other.usage.input;
        self.usage.cache_read += other.usage.cache_read;
        self.usage.cache_write += other.usage.cache_write;
        self.usage.output += other.usage.output;
        self.duration_ms += other.duration_ms;
        if self.source.is_empty() {
            self.source = other.source.clone();
        }
    }
}

// ── Policy ───────────────────────────────────────────────────────────

fn module_name() -> String {
    crate::credits::module_name()
}

fn state_dir() -> Option<std::path::PathBuf> {
    Some(dirs::home_dir()?.join(".mod").join(module_name()))
}

fn policy_path() -> Option<std::path::PathBuf> {
    Some(state_dir()?.join("costs.json"))
}

/// Spend policy. Off-tree so a fork can be strict without a code change, and
/// so nothing about who may spend what lands in a committed config.json.
#[derive(Serialize, Deserialize, Clone)]
pub struct Policy {
    /// Record cost per task and debit the caller's credit account.
    #[serde(default = "yes")]
    pub metering: bool,
    /// Refuse new tasks from a caller whose balance is at or below this.
    /// The owner is never gated — when the money runs out the console still
    /// belongs to whoever owns it.
    #[serde(default = "yes")]
    pub gate_when_empty: bool,
    /// Balance a non-owner must hold to submit, in micro-dollars. Zero means
    /// "any positive balance"; raise it to hold back a reserve for a task
    /// that is about to cost more than the caller has left.
    #[serde(default)]
    pub min_balance_usd6: u64,
    /// Free allowance per identity, in micro-dollars, before the gate bites.
    /// Metered either way — this only decides who is turned away.
    #[serde(default)]
    pub free_tier_usd6: u64,
    /// Markup applied to the metered figure when debiting, in basis points
    /// (500 = 5%). The recorded task cost stays the raw number.
    #[serde(default)]
    pub margin_bps: u32,
}

fn yes() -> bool {
    true
}

impl Default for Policy {
    fn default() -> Self {
        Self {
            metering: true,
            gate_when_empty: true,
            min_balance_usd6: 0,
            free_tier_usd6: 0,
            margin_bps: 0,
        }
    }
}

pub fn policy() -> Policy {
    policy_path()
        .and_then(|p| std::fs::read_to_string(p).ok())
        .and_then(|c| serde_json::from_str(&c).ok())
        .unwrap_or_default()
}

pub fn save_policy(p: &Policy) -> Result<(), String> {
    let path = policy_path().ok_or("no home dir")?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("mkdir: {}", e))?;
    }
    let json = serde_json::to_string_pretty(p).map_err(|e| format!("encode: {}", e))?;
    std::fs::write(&path, json).map_err(|e| format!("write: {}", e))
}

/// The metered figure plus the configured margin — what actually gets debited.
pub fn billable_usd6(raw_usd6: u128) -> u128 {
    let bps = policy().margin_bps as u128;
    raw_usd6 + raw_usd6 * bps / 10_000
}

// ── Aggregation ──────────────────────────────────────────────────────

fn db_path() -> std::path::PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    std::path::PathBuf::from(home)
        .join(".mod")
        .join(module_name())
        .join("claude_jobs.db")
}

/// One identity's spend over a window.
#[derive(Serialize, Clone)]
pub struct UserSpend {
    pub identity: String,
    pub tasks: u64,
    pub usd: String,
    pub usd6: String,
    pub tokens_in: u64,
    pub tokens_out: u64,
}

/// The public cost picture for a window — and, for a calendar month, the
/// number the prediction market settles on.
#[derive(Serialize)]
pub struct CostSummary {
    /// "2026-08" for a month window, otherwise "window".
    pub epoch: String,
    pub from_ts: i64,
    pub to_ts: i64,
    pub users: u64,
    pub tasks: u64,
    pub total_usd: String,
    pub total_usd6: String,
    /// total ÷ users — the settlement figure. Zero users ⇒ "0.00".
    pub avg_usd_per_user: String,
    pub avg_usd6_per_user: String,
    pub avg_usd_per_task: String,
    /// Whether this window is closed (entirely in the past). An open window's
    /// average still moves, so a market must not settle on it.
    pub final_: bool,
    pub rows: Vec<UserSpend>,
}

/// UTC month bounds for "YYYY-MM". Returns (start, end-exclusive).
pub fn month_bounds(month: &str) -> Option<(i64, i64)> {
    use chrono::{Datelike, NaiveDate, TimeZone, Utc};
    let mut parts = month.split('-');
    let y: i32 = parts.next()?.parse().ok()?;
    let m: u32 = parts.next()?.parse().ok()?;
    if !(1..=12).contains(&m) || parts.next().is_some() {
        return None;
    }
    let start = NaiveDate::from_ymd_opt(y, m, 1)?;
    let (ny, nm) = if m == 12 { (y + 1, 1) } else { (y, m + 1) };
    let end = NaiveDate::from_ymd_opt(ny, nm, 1)?;
    let to_ts = |d: NaiveDate| {
        Utc.from_utc_datetime(&d.and_hms_opt(0, 0, 0).unwrap())
            .timestamp()
    };
    let _ = start.day();
    Some((to_ts(start), to_ts(end)))
}

/// The current UTC month, "YYYY-MM".
pub fn current_month() -> String {
    chrono::Utc::now().format("%Y-%m").to_string()
}

fn fmt(usd6: u128) -> String {
    crate::credits::fmt_usd(usd6)
}

/// Per-identity spend between two timestamps, newest-spend first. Anonymous
/// (local-mode) tasks are grouped under "local" rather than dropped — they
/// cost real money and belong in the total.
pub fn spend_rows(from_ts: i64, to_ts: i64) -> Vec<UserSpend> {
    let conn = match rusqlite::Connection::open(db_path()) {
        Ok(c) => c,
        Err(_) => return Vec::new(),
    };
    let mut stmt = match conn.prepare(
        "SELECT CASE WHEN user_address = '' THEN 'local' ELSE lower(user_address) END AS ident,
                COUNT(*), COALESCE(SUM(cost_usd6), 0),
                COALESCE(SUM(tokens_in), 0), COALESCE(SUM(tokens_out), 0)
         FROM claude_jobs
         WHERE created_at >= ?1 AND created_at < ?2
         GROUP BY ident
         ORDER BY 3 DESC",
    ) {
        Ok(s) => s,
        Err(_) => return Vec::new(),
    };
    stmt.query_map(rusqlite::params![from_ts, to_ts], |row| {
        let usd6: i64 = row.get(2)?;
        let usd6 = usd6.max(0) as u128;
        Ok(UserSpend {
            identity: row.get(0)?,
            tasks: row.get::<_, i64>(1)? as u64,
            usd: fmt(usd6),
            usd6: usd6.to_string(),
            tokens_in: row.get::<_, i64>(3)?.max(0) as u64,
            tokens_out: row.get::<_, i64>(4)?.max(0) as u64,
        })
    })
    .map(|rows| rows.filter_map(|r| r.ok()).collect())
    .unwrap_or_default()
}

/// Summarise a window. `epoch` is a label only — pass the month string when
/// the bounds came from `month_bounds`.
pub fn summarize(epoch: &str, from_ts: i64, to_ts: i64) -> CostSummary {
    let rows = spend_rows(from_ts, to_ts);
    let total: u128 = rows.iter().filter_map(|r| r.usd6.parse::<u128>().ok()).sum();
    let tasks: u64 = rows.iter().map(|r| r.tasks).sum();
    let users = rows.len() as u128;
    let avg_user = if users == 0 { 0 } else { total / users };
    let avg_task = if tasks == 0 { 0 } else { total / tasks as u128 };
    CostSummary {
        epoch: epoch.to_string(),
        from_ts,
        to_ts,
        users: users as u64,
        tasks,
        total_usd: fmt(total),
        total_usd6: total.to_string(),
        avg_usd_per_user: fmt(avg_user),
        avg_usd6_per_user: avg_user.to_string(),
        avg_usd_per_task: fmt(avg_task),
        final_: to_ts <= chrono::Utc::now().timestamp(),
        rows,
    }
}

/// Summarise a calendar month, e.g. "2026-08".
pub fn summarize_month(month: &str) -> Option<CostSummary> {
    let (from, to) = month_bounds(month)?;
    Some(summarize(month, from, to))
}

/// One identity's own tasks, newest first — the caller's spend history.
#[derive(Serialize)]
pub struct TaskCostRow {
    pub job_id: String,
    pub created_at: i64,
    pub model: String,
    pub agent: String,
    pub usd: String,
    pub tokens_in: u64,
    pub tokens_out: u64,
    pub prompt: String,
}

pub fn recent_for(identity: &str, limit: usize) -> Vec<TaskCostRow> {
    let conn = match rusqlite::Connection::open(db_path()) {
        Ok(c) => c,
        Err(_) => return Vec::new(),
    };
    let ident = if identity.trim().is_empty() {
        "local".to_string()
    } else {
        identity.trim().to_lowercase()
    };
    let mut stmt = match conn.prepare(
        "SELECT id, created_at, model, agent, COALESCE(cost_usd6, 0),
                COALESCE(tokens_in, 0), COALESCE(tokens_out, 0), prompt
         FROM claude_jobs
         WHERE (CASE WHEN user_address = '' THEN 'local' ELSE lower(user_address) END) = ?1
         ORDER BY created_at DESC LIMIT ?2",
    ) {
        Ok(s) => s,
        Err(_) => return Vec::new(),
    };
    stmt.query_map(rusqlite::params![ident, limit as i64], |row| {
        let usd6: i64 = row.get(4)?;
        let id: String = row.get(0)?;
        // These are the caller's own rows, but the prompt may be vault-sealed:
        // open it only if their vault is unlocked, else show the mask.
        let prompt: String = row.get(7)?;
        let prompt = if crate::vault::is_sealed(&prompt) {
            match crate::vault::session_key(&ident) {
                Some(key) => crate::vault::open_text(&key, &prompt),
                None => crate::vault::mask(&prompt),
            }
        } else {
            prompt
        };
        Ok(TaskCostRow {
            job_id: id,
            created_at: row.get(1)?,
            model: row.get(2)?,
            agent: row.get::<_, String>(3).unwrap_or_else(|_| "claude".into()),
            usd: fmt(usd6.max(0) as u128),
            tokens_in: row.get::<_, i64>(5)?.max(0) as u64,
            tokens_out: row.get::<_, i64>(6)?.max(0) as u64,
            prompt: prompt.chars().take(160).collect(),
        })
    })
    .map(|rows| rows.filter_map(|r| r.ok()).collect())
    .unwrap_or_default()
}

// ── Tests ────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn opus_prices_at_five_and_twentyfive() {
        let u = Usage { input: 1_000_000, cache_read: 0, cache_write: 0, output: 1_000_000 };
        // $5 in + $25 out = $30.00
        assert_eq!(price_usd6("claude-opus-5", &u), 30_000_000);
    }

    #[test]
    fn cache_tiers_apply() {
        let u = Usage { input: 0, cache_read: 1_000_000, cache_write: 1_000_000, output: 0 };
        // fable: $10/MTok in → read 0.1× = $1, write 1.25× = $12.50
        assert_eq!(price_usd6("claude-fable-5", &u), 13_500_000);
    }

    #[test]
    fn model_family_prefixes_resolve() {
        let u = Usage { input: 1_000_000, ..Default::default() };
        assert_eq!(price_usd6("claude-haiku-4-5-20251001", &u), 1_000_000);
        assert_eq!(price_usd6("claude-sonnet-5", &u), 3_000_000);
        assert_eq!(price_usd6("claude-opus-5[1m]", &u), 5_000_000);
        // Unknown names bill at the Opus rate, never free.
        assert_eq!(price_usd6("something-new", &u), 5_000_000);
    }

    #[test]
    fn reported_cost_wins_over_the_table() {
        let v = serde_json::json!({
            "type": "result",
            "total_cost_usd": 0.1234,
            "duration_ms": 4200,
            "usage": {"input_tokens": 10, "output_tokens": 20}
        });
        let c = from_claude_result(&v, "claude-opus-5").unwrap();
        assert_eq!(c.usd6, 123_400);
        assert_eq!(c.source, "reported");
        assert_eq!(c.duration_ms, 4200);
    }

    #[test]
    fn missing_cost_falls_back_to_pricing() {
        let v = serde_json::json!({
            "type": "result",
            "usage": {"input_tokens": 1_000_000, "output_tokens": 0}
        });
        let c = from_claude_result(&v, "claude-sonnet-5").unwrap();
        assert_eq!(c.usd6, 3_000_000);
        assert_eq!(c.source, "priced");
    }

    #[test]
    fn month_bounds_are_utc_calendar_months() {
        let (a, b) = month_bounds("2026-08").unwrap();
        assert_eq!(b - a, 31 * 86400);
        assert!(month_bounds("2026-13").is_none());
        assert!(month_bounds("2026-08-01").is_none());
        // February in a leap year.
        let (a, b) = month_bounds("2028-02").unwrap();
        assert_eq!(b - a, 29 * 86400);
    }

    #[test]
    fn margin_is_basis_points() {
        // Pure arithmetic — exercised here without touching the policy file.
        let raw: u128 = 1_000_000;
        assert_eq!(raw + raw * 500 / 10_000, 1_050_000);
    }
}
