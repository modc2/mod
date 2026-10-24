//! The ASK desk — "I have $5,000 for six months, keep it safe" — answered
//! from the hub's own numbers.
//!
//! Two layers, and every response says which it is:
//!
//!   1. PLAN — deterministic. Read the question for an amount, a horizon, a
//!      risk appetite and a chain; rank the vetted hub rows for it; project
//!      what the amount becomes over the horizon at the record's FLOOR (the
//!      observed band's lower bound), at a TYPICAL rate (today's, capped by
//!      the 30-day mean so a one-day spike cannot headline) and at the
//!      record's CEILING. Pure arithmetic over numbers the hub already
//!      serves — instant, free, unit-tested, and the same answer for everyone.
//!   2. EXPLAIN — optional, signed callers only. The finished plan goes to the
//!      claudecode agent contract (orbit/claudecode, loopback) for a short
//!      plain-English read. The agent phrases; it never picks — a model
//!      cannot add a protocol the plan did not rank.
//!
//! Nothing here is advice: the rates are the market's, the floor is a
//! historical bound and not a promise, and every pick carries its risks.

use serde::Deserialize;
use serde_json::{json, Value};

/// Depth at which a pool counts as fully deep — same constant the hub and
/// the finance registry use, so "depth-adjusted" means one thing.
const DEPTH_FULL: f64 = 10_000_000.0;

#[derive(Debug, Clone, Deserialize, Default)]
pub struct AskBody {
    /// Free text — "I have $5k for a year on Base, keep it boring". Every
    /// structured field below overrides what the text says.
    pub question: Option<String>,
    pub amount: Option<f64>,
    pub months: Option<f64>,
    /// low | medium | high
    pub risk: Option<String>,
    /// a desk id (ethereum, base, solana, tao) or any chain name the hub lists
    pub chain: Option<String>,
    /// only protocols this desk can put money into
    pub from_desk: Option<bool>,
    pub limit: Option<usize>,
    /// ask the agent for prose on top (REST: signed caller; MCP: bearer)
    pub explain: Option<bool>,
    pub model: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Risk {
    Low,
    Medium,
    High,
}

impl Risk {
    fn word(self) -> &'static str {
        match self {
            Risk::Low => "low",
            Risk::Medium => "medium",
            Risk::High => "high",
        }
    }
    fn parse(s: &str) -> Option<Risk> {
        match s.trim().to_lowercase().as_str() {
            "low" | "safe" | "conservative" => Some(Risk::Low),
            "medium" | "mid" | "balanced" | "normal" => Some(Risk::Medium),
            "high" | "aggressive" | "max" => Some(Risk::High),
            _ => None,
        }
    }
}

/// What the question boiled down to. `parsed` lists which fields came from
/// the text, so the console can show "I read: $5,000 · 6 months · low risk".
#[derive(Debug, Clone, PartialEq)]
pub struct Brief {
    pub amount: f64,
    pub months: f64,
    pub risk: Risk,
    pub chain: Option<String>,
    pub from_desk: bool,
    pub parsed: Vec<String>,
}

const DEFAULT_AMOUNT: f64 = 1_000.0;
const DEFAULT_MONTHS: f64 = 6.0;

const SAFE_WORDS: &[&str] = &[
    "safe", "safest", "safely", "low", "lowest", "conservative", "careful", "boring", "sleep",
    "cautious", "preserve", "preservation", "stable", "steady", "secure", "minimal",
];
const MID_WORDS: &[&str] = &["balanced", "medium", "moderate", "middle", "normal", "reasonable"];
const BOLD_WORDS: &[&str] = &[
    "aggressive", "aggressively", "high", "highest", "max", "maximum", "maximise", "maximize",
    "degen", "yolo", "risky", "most", "best", "juicy", "moon", "bold", "brave", "greedy", "risk-on",
];

/// How deep the headline pool must be before a low or medium appetite will
/// look at it: a 120% rate on a $2m pool is a curiosity, not a place for
/// someone's savings. High appetite sees everything.
const MIN_POOL_TVL_LOW: f64 = DEPTH_FULL;
const MIN_POOL_TVL_MEDIUM: f64 = DEPTH_FULL / 2.0;

const NUMBER_WORDS: &[(&str, f64)] = &[
    ("a", 1.0), ("an", 1.0), ("one", 1.0), ("two", 2.0), ("three", 3.0), ("four", 4.0),
    ("five", 5.0), ("six", 6.0), ("seven", 7.0), ("eight", 8.0), ("nine", 9.0), ("ten", 10.0),
    ("eleven", 11.0), ("twelve", 12.0), ("half", 0.5), ("couple", 2.0), ("few", 3.0),
];

/// Lower-case word tokens, punctuation stripped except what a number needs.
fn tokens(text: &str) -> Vec<String> {
    text.to_lowercase()
        .split_whitespace()
        .map(|w| {
            // Edges lose their punctuation ("solana," → "solana"); a number
            // keeps its inner separators ("5,000", "2.5k") and a leading $.
            w.trim_start_matches(|c: char| !(c.is_alphanumeric() || c == '$'))
                .trim_end_matches(|c: char| !(c.is_alphanumeric() || c == '%'))
                .to_string()
        })
        .filter(|w| !w.is_empty())
        .collect()
}

/// "5,000" · "5k" · "$5000" · "2.5m" → a number, with the suffix applied.
fn parse_number(raw: &str) -> Option<(f64, Option<char>)> {
    let s = raw.trim_start_matches('$').replace(',', "");
    if s.is_empty() {
        return None;
    }
    let (digits, suffix) = match s.chars().last() {
        Some(c @ ('k' | 'm')) => (&s[..s.len() - 1], Some(c)),
        _ => (s.as_str(), None),
    };
    if digits.is_empty() || !digits.chars().all(|c| c.is_ascii_digit() || c == '.') {
        return None;
    }
    let n: f64 = digits.parse().ok()?;
    Some((
        match suffix {
            Some('k') => n * 1_000.0,
            Some('m') => n * 1_000_000.0,
            _ => n,
        },
        suffix,
    ))
}

fn horizon_unit(word: &str) -> Option<f64> {
    let w = word.trim_matches(|c: char| c == ',' || c == '.').trim_end_matches('s');
    match w {
        "month" | "mo" | "mth" => Some(1.0),
        "year" | "yr" => Some(12.0),
        "week" | "wk" => Some(12.0 / 52.0),
        "day" => Some(12.0 / 365.0),
        "quarter" => Some(3.0),
        _ => None,
    }
}

const MONEY_WORDS: &[&str] = &["usd", "usdc", "usdt", "dai", "dollar", "dollars", "bucks", "stables", "stablecoins", "cash"];

/// Read a free-text question. Every structured field in `body` wins over the
/// text; the text fills what the caller left blank.
pub fn brief(body: &AskBody) -> Brief {
    let text = body.question.clone().unwrap_or_default();
    let toks = tokens(&text);
    let mut parsed = Vec::new();

    // ── amount ────────────────────────────────────────────────────────
    let mut amount: Option<f64> = None;
    let mut amount_at: Option<usize> = None;
    for (i, t) in toks.iter().enumerate() {
        let dollar = t.starts_with('$');
        if let Some((n, suffix)) = parse_number(t) {
            let next = toks.get(i + 1).map(|s| s.as_str()).unwrap_or("");
            let is_money = dollar || suffix.is_some() || MONEY_WORDS.contains(&next);
            let is_horizon = horizon_unit(next).is_some() || t.ends_with('%');
            if is_money && !is_horizon {
                amount = Some(n);
                amount_at = Some(i);
                break;
            }
        }
    }
    if amount.is_none() {
        // A bare number that is not a horizon and is big enough to be money.
        for (i, t) in toks.iter().enumerate() {
            if let Some((n, _)) = parse_number(t) {
                let next = toks.get(i + 1).map(|s| s.as_str()).unwrap_or("");
                if horizon_unit(next).is_none() && !t.ends_with('%') && n >= 50.0 {
                    amount = Some(n);
                    amount_at = Some(i);
                    break;
                }
            }
        }
    }
    if body.amount.is_none() && amount.is_some() {
        parsed.push("amount".into());
    }
    let amount = body.amount.filter(|a| a.is_finite() && *a > 0.0).or(amount).unwrap_or(DEFAULT_AMOUNT);

    // ── horizon ───────────────────────────────────────────────────────
    let mut months: Option<f64> = None;
    for (i, t) in toks.iter().enumerate() {
        if let Some(unit) = horizon_unit(t) {
            if Some(i) == amount_at {
                continue;
            }
            let prev = if i > 0 { toks[i - 1].as_str() } else { "" };
            let n = parse_number(prev)
                .filter(|_| Some(i - 1) != amount_at)
                .map(|(n, _)| n)
                .or_else(|| NUMBER_WORDS.iter().find(|(w, _)| *w == prev).map(|(_, n)| *n))
                .unwrap_or(1.0);
            months = Some(n * unit);
            break;
        }
    }
    if body.months.is_none() && months.is_some() {
        parsed.push("horizon".into());
    }
    let months = body
        .months
        .filter(|m| m.is_finite() && *m > 0.0)
        .or(months)
        .unwrap_or(DEFAULT_MONTHS)
        .clamp(0.25, 120.0);

    // ── risk appetite ─────────────────────────────────────────────────
    let risk_text = if toks.iter().any(|t| SAFE_WORDS.contains(&t.as_str())) {
        Some(Risk::Low)
    } else if toks.iter().any(|t| MID_WORDS.contains(&t.as_str())) {
        Some(Risk::Medium)
    } else if toks.iter().any(|t| BOLD_WORDS.contains(&t.as_str())) {
        Some(Risk::High)
    } else {
        None
    };
    let risk_body = body.risk.as_deref().and_then(Risk::parse);
    if risk_body.is_none() && risk_text.is_some() {
        parsed.push("risk".into());
    }
    let risk = risk_body.or(risk_text).unwrap_or(Risk::Medium);

    // ── chain ─────────────────────────────────────────────────────────
    let chain_text = toks.iter().find_map(|t| chain_word(t));
    if body.chain.as_deref().map(str::trim).filter(|c| !c.is_empty()).is_none() && chain_text.is_some() {
        parsed.push("chain".into());
    }
    let chain = body
        .chain
        .as_deref()
        .map(|c| c.trim().to_lowercase())
        .filter(|c| !c.is_empty())
        .or(chain_text);

    // ── from this desk ────────────────────────────────────────────────
    let lower = text.to_lowercase();
    let desk_text = lower.contains("from here") || lower.contains("this desk") || lower.contains("from the desk");
    if body.from_desk.is_none() && desk_text {
        parsed.push("from_desk".into());
    }
    let from_desk = body.from_desk.unwrap_or(desk_text);

    Brief { amount, months, risk, chain, from_desk, parsed }
}

/// A chain mentioned by name → the id the hub's chain rows answer to.
fn chain_word(t: &str) -> Option<String> {
    Some(
        match t {
            "ethereum" | "eth" | "mainnet" => "ethereum",
            "base" => "base",
            "solana" | "sol" => "solana",
            "bittensor" | "tao" => "tao",
            "arbitrum" | "arb" => "arbitrum",
            "optimism" | "op" => "op mainnet",
            "polygon" | "matic" => "polygon",
            "avalanche" | "avax" => "avalanche",
            "bsc" | "bnb" | "binance" => "bsc",
            "tron" => "tron",
            "monad" => "monad",
            "hyperliquid" | "hl" => "hyperliquid",
            _ => return None,
        }
        .to_string(),
    )
}

/// How much weight a tier carries in the ranking. The tier says how much
/// history stands behind a name, not how safe it is — so it nudges, it does
/// not decide.
fn tier_weight(tier: &str) -> f64 {
    match tier {
        "core" => 1.0,
        "established" => 0.85,
        _ => 0.65,
    }
}

fn depth_factor(tvl_usd: f64) -> f64 {
    (tvl_usd / DEPTH_FULL).clamp(0.0, 1.0).sqrt()
}

/// What `amount` earns over `months` at `apr` percent, compounding yearly
/// quoted APY the way the index quotes it.
pub fn earn(amount: f64, apr_pct: f64, months: f64) -> f64 {
    if !apr_pct.is_finite() {
        return 0.0;
    }
    let growth = (1.0 + apr_pct / 100.0).powf(months / 12.0) - 1.0;
    (amount * growth * 100.0).round() / 100.0
}

fn round2(v: f64) -> f64 {
    (v * 100.0).round() / 100.0
}

fn f(v: &Value, key: &str) -> Option<f64> {
    v.get(key).and_then(|x| x.as_f64())
}

fn s<'a>(v: &'a Value, key: &str) -> &'a str {
    v.get(key).and_then(|x| x.as_str()).unwrap_or("")
}

/// The chain row of a hub entry that answers to `want` — by desk id
/// (ethereum, base, solana, tao) or by the index's chain name, either case.
fn chain_row<'a>(row: &'a Value, want: &str) -> Option<&'a Value> {
    row.get("chains")?.as_array()?.iter().find(|c| {
        s(c, "desk").eq_ignore_ascii_case(want) || s(c, "chain").eq_ignore_ascii_case(want)
    })
}

/// One ranked candidate, before it is written out.
struct Candidate {
    thin: bool,
    row: Value,
    chain: Value,
    best: Value,
    band: Value,
    enterable: bool,
    floor: f64,
    typical: f64,
    ceiling: f64,
    key: f64,
}

/// Rank the hub for a brief and project every pick. `hub` is the payload of
/// GET /hub (unfiltered — the chain is picked per row here, so each pick's
/// numbers are that chain's own).
pub fn plan(hub: &Value, body: &AskBody) -> Value {
    let brief = brief(body);
    let limit = body.limit.unwrap_or(3).clamp(1, 10);
    let rows = hub.get("hub").and_then(|v| v.as_array()).cloned().unwrap_or_default();

    let mut excluded = json!({ "no_rate": 0, "gated": 0, "tier": 0, "thin_record": 0, "thin_pool": 0, "emissions": 0, "off_chain": 0, "not_enterable": 0 });
    let mut bump = |k: &str| {
        if let Some(n) = excluded.get_mut(k).and_then(|v| v.as_u64()) {
            excluded[k] = json!(n + 1);
        }
    };

    let mut candidates: Vec<Candidate> = Vec::new();
    for row in rows.iter() {
        if row.get("gated").and_then(|v| v.as_bool()).unwrap_or(false) {
            bump("gated");
            continue;
        }
        let tier = s(row, "tier").to_string();
        let tier_ok = match brief.risk {
            Risk::Low => tier == "core",
            Risk::Medium => tier == "core" || tier == "established",
            Risk::High => true,
        };
        if !tier_ok {
            bump("tier");
            continue;
        }

        // The numbers come from the chosen chain's row when a chain was
        // asked for, from the protocol's headline otherwise.
        let (chain, best, band, enterable) = match brief.chain.as_deref() {
            Some(want) => match chain_row(row, want) {
                Some(c) => (
                    c.clone(),
                    c.get("best").cloned().unwrap_or(Value::Null),
                    c.get("apr_band").cloned().unwrap_or(Value::Null),
                    c.get("enterable").and_then(|v| v.as_bool()).unwrap_or(false),
                ),
                None => {
                    bump("off_chain");
                    continue;
                }
            },
            None => {
                let best = row.get("best").cloned().unwrap_or(Value::Null);
                let chain = best
                    .get("chain")
                    .and_then(|v| v.as_str())
                    .and_then(|name| chain_row(row, name))
                    .cloned()
                    .unwrap_or(Value::Null);
                let enterable = row.get("enterable_from_desk").and_then(|v| v.as_bool()).unwrap_or(false)
                    && (chain.is_null() || chain.get("enterable").and_then(|v| v.as_bool()).unwrap_or(false));
                (chain, best, row.get("apr_band").cloned().unwrap_or(Value::Null), enterable)
            }
        };
        let Some(apy_now) = f(&best, "apy") else {
            bump("no_rate");
            continue;
        };
        if brief.from_desk && !enterable {
            bump("not_enterable");
            continue;
        }

        let tvl = f(&best, "tvl_usd").unwrap_or(0.0);
        let deep_enough = match brief.risk {
            Risk::Low => tvl >= MIN_POOL_TVL_LOW,
            Risk::Medium => tvl >= MIN_POOL_TVL_MEDIUM,
            Risk::High => true,
        };
        if !deep_enough {
            bump("thin_pool");
            continue;
        }
        let lower = f(&band, "apr_lower");
        let upper = f(&band, "apr_upper");
        let confidence = s(&band, "confidence");
        let mean30 = f(&best, "apy_mean_30d").filter(|m| m.is_finite() && *m > 0.0);
        // A band that dips below zero, rests on a thin history, or belongs to
        // a pool too young to have a 30-day mean is a different animal from a
        // lending rate: low and medium appetite step around it, high is told.
        let thin = lower.is_none() || confidence == "low" || lower.unwrap_or(0.0) < 0.0 || mean30.is_none();
        if thin && brief.risk != Risk::High {
            bump("thin_record");
            continue;
        }
        // Low appetite wants a rate made of fees, not of a token being
        // printed: more than half emissions is a farm, however deep.
        let emissions = f(&best, "emissions_share").unwrap_or(0.0);
        if brief.risk == Risk::Low && emissions > 50.0 {
            bump("emissions");
            continue;
        }

        let floor = lower.unwrap_or(0.0).max(0.0);
        let ceiling = upper.unwrap_or(apy_now).max(apy_now.min(upper.unwrap_or(apy_now)));
        // Typical: today's rate, but a one-day spike cannot headline — the
        // 30-day mean caps it when the index has one.
        let typical = match mean30 {
            Some(m) if m > 0.0 => apy_now.min(m).max(floor),
            _ => apy_now.max(floor),
        }
        .min(ceiling.max(floor));
        let bonus = if enterable { 1.15 } else { 1.0 };
        let key = match brief.risk {
            Risk::Low => floor * depth_factor(tvl) * tier_weight(&tier) * bonus,
            Risk::Medium => typical * depth_factor(tvl) * tier_weight(&tier) * bonus,
            Risk::High => typical * tier_weight(&tier) * bonus,
        };
        candidates.push(Candidate { thin, row: row.clone(), chain, best, band, enterable, floor, typical, ceiling, key });
    }

    // Ties on the key (two floors of zero, say) fall back to the typical rate.
    candidates.sort_by(|a, b| {
        b.key
            .partial_cmp(&a.key)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| b.typical.partial_cmp(&a.typical).unwrap_or(std::cmp::Ordering::Equal))
    });
    let considered = candidates.len();

    let picks: Vec<Value> = candidates
        .iter()
        .take(limit)
        .enumerate()
        .map(|(i, c)| {
            let row = &c.row;
            let module = s(&c.chain, "module");
            let chain_name = if c.chain.is_null() { s(&c.best, "chain").to_string() } else { s(&c.chain, "chain").to_string() };
            let desk = if c.chain.is_null() { s(&c.best, "desk").to_string() } else { s(&c.chain, "desk").to_string() };
            let way_in = if c.enterable {
                format!("put USD in from this desk — the {module} module signs")
            } else if !desk.is_empty() {
                "listed — enter through the protocol's own app".to_string()
            } else {
                "read-only from this desk — bridge first".to_string()
            };
            let risk_line = row.get("risk").cloned().unwrap_or(Value::Null);
            let earn_floor = earn(brief.amount, c.floor, brief.months);
            let earn_typical = earn(brief.amount, c.typical, brief.months);
            let earn_ceiling = earn(brief.amount, c.ceiling, brief.months);
            json!({
                "rank": i + 1,
                "id": s(row, "id"),
                "name": s(row, "name"),
                "tier": s(row, "tier"),
                "category": s(row, "category"),
                "since": row.get("since").cloned().unwrap_or(Value::Null),
                "chain": chain_name,
                "desk": if desk.is_empty() { Value::Null } else { json!(desk) },
                "module": if module.is_empty() { Value::Null } else { json!(module) },
                "enterable": c.enterable,
                "module_id": c.best.get("module_id").cloned().unwrap_or(Value::Null),
                "symbol": s(&c.best, "symbol"),
                "pool_tvl_usd": f(&c.best, "tvl_usd").map(round2),
                "apy_now": round2(f(&c.best, "apy").unwrap_or(0.0)),
                "apy_typical": round2(c.typical),
                "band": {
                    "lower": round2(c.floor),
                    "upper": round2(c.ceiling),
                    "confidence": s(&c.band, "confidence"),
                    "basis": s(&c.band, "basis"),
                },
                // A thin record (negative or low-confidence band, no 30-day
                // mean) only reaches a high appetite — and says so, because
                // "typical" means nothing there: the number is the headline.
                "thin_record": c.thin,
                "earn": { "floor": earn_floor, "typical": earn_typical, "ceiling": earn_ceiling },
                "ends_with": {
                    "floor": round2(brief.amount + earn_floor),
                    "typical": round2(brief.amount + earn_typical),
                    "ceiling": round2(brief.amount + earn_ceiling),
                },
                "why": row.get("legit").and_then(|v| v.as_array()).map(|a| a.iter().take(2).cloned().collect::<Vec<_>>()).unwrap_or_default(),
                "risks": row.get("risks").and_then(|v| v.as_array()).map(|a| a.iter().take(3).cloned().collect::<Vec<_>>()).unwrap_or_default(),
                "risk_score": risk_line.get("score").cloned().unwrap_or(Value::Null),
                "risk_level": risk_line.get("level").cloned().unwrap_or(Value::Null),
                "way_in": way_in,
                "blurb": s(row, "blurb"),
                "website": s(row, "website"),
            })
        })
        .collect();

    let rule = match brief.risk {
        Risk::Low => "low appetite: core-tier names only, fee-driven pools at least $10m deep with a solid record, ranked by the FLOOR of their observed APR band × depth — what the record says they have paid even in a bad stretch",
        Risk::Medium => "medium appetite: core and established names, pools at least $5m deep with a 30-day record, ranked by a typical rate (today's, capped by the 30-day mean) × depth",
        Risk::High => "high appetite: every vetted name with a quoted rate, ranked by its typical rate — depth and thin histories are shown, not filtered",
    };

    json!({
        "brief": {
            "amount": brief.amount,
            "months": brief.months,
            "risk": brief.risk.word(),
            "chain": brief.chain,
            "from_desk": brief.from_desk,
            "parsed_from_question": brief.parsed,
            "question": body.question.clone().unwrap_or_default(),
        },
        "picks": picks,
        "considered": considered,
        "curated": rows.len(),
        "excluded": excluded,
        "rule": rule,
        "math": "earn = amount × ((1 + apr/100)^(months/12) − 1); floor/ceiling are the observed APR band's bounds, typical is today's rate capped by the 30-day mean",
        "note": "nothing here is advice — the rates are the market's, the floor is a historical bound and not a promise, and every pick carries what can go wrong",
        "as_of": hub.get("as_of").cloned().unwrap_or(Value::Null),
        "age_seconds": hub.get("age_seconds").cloned().unwrap_or(Value::Null),
        "layer": "plan",
    })
}

/// The prose layer: the plan handed to the claudecode agent contract. The
/// agent phrases what the plan already decided; the prompt says so.
pub async fn explain(base: &str, plan: &Value, model: Option<&str>) -> Result<Value, String> {
    let brief = plan.get("brief").cloned().unwrap_or(Value::Null);
    let picks: Vec<Value> = plan
        .get("picks")
        .and_then(|v| v.as_array())
        .map(|a| {
            a.iter()
                .map(|p| {
                    json!({
                        "name": p.get("name"), "tier": p.get("tier"), "chain": p.get("chain"),
                        "apy_now": p.get("apy_now"), "band": p.get("band"), "earn": p.get("earn"),
                        "enterable_from_desk": p.get("enterable"), "way_in": p.get("way_in"),
                        "risks": p.get("risks"), "why": p.get("why"), "blurb": p.get("blurb"),
                    })
                })
                .collect()
        })
        .unwrap_or_default();
    let query = format!(
        "You are the ASK desk of a DeFi console, speaking to someone who does not know what a lending pool is.\n\
         Below is a PLAN computed from the console's own vetted numbers. Write the plain-English read of it.\n\
         Rules: at most 170 words; no markdown headers or tables; no emoji. Restate the brief in one line \
         (amount, horizon, appetite). Walk the picks in the plan's order, one short paragraph each: what it is \
         in one clause, what the amount would earn over the horizon at the floor, typically, and at the ceiling \
         (dollars, rounded), one concrete risk, and whether it can be entered from this desk. Never add a \
         protocol that is not in the plan, never change the numbers, never promise. End with one sentence: \
         the rates are the market's and the floor is history, not a guarantee.\n\n\
         BRIEF: {}\nPICKS: {}\nRULE: {}",
        brief,
        Value::Array(picks),
        plan.get("rule").and_then(|v| v.as_str()).unwrap_or("")
    );
    let http = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(90))
        .build()
        .map_err(|e| e.to_string())?;
    let mut body = json!({ "query": query });
    if let Some(m) = model.filter(|m| !m.is_empty()) {
        body["model"] = json!(m);
    } else {
        body["model"] = json!("haiku");
    }
    let resp = http
        .post(format!("{}/run", base.trim_end_matches('/')))
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("the agent contract is unreachable at {base}: {e}"))?;
    let status = resp.status();
    let out: Value = resp.json().await.map_err(|e| format!("agent answered without JSON: {e}"))?;
    if !status.is_success() {
        return Err(format!("agent returned {status}: {}", out.to_string().chars().take(300).collect::<String>()));
    }
    if let Some(err) = out.get("error").and_then(|v| v.as_str()) {
        return Err(format!("agent run failed: {err}"));
    }
    let finish = out
        .get("result")
        .and_then(|v| v.as_array())
        .and_then(|a| a.iter().rev().find(|s| s.get("tool").and_then(|t| t.as_str()) == Some("finish")))
        .cloned()
        .unwrap_or(Value::Null);
    let answer = finish
        .pointer("/params/summary")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|t| !t.is_empty())
        .ok_or_else(|| "the agent finished without an answer".to_string())?;
    let usage = finish.pointer("/params/usage").cloned().unwrap_or(Value::Null);
    Ok(json!({
        "answer": answer,
        "model": usage.get("model").cloned().unwrap_or(Value::Null),
        "cost_usd": usage.get("cost").cloned().unwrap_or(Value::Null),
        "duration_ms": usage.get("duration_ms").cloned().unwrap_or(Value::Null),
        "task_id": out.get("task_id").cloned().unwrap_or(Value::Null),
        "layer": "explain",
        "by": "orbit/claudecode agent contract — phrases the plan, never picks",
    }))
}

/// Is the agent contract there? Public, so the console knows whether to
/// offer the EXPLAIN button at all.
pub async fn status(base: &str) -> Value {
    let http = reqwest::Client::builder().timeout(std::time::Duration::from_secs(3)).build();
    let reachable = match http {
        Ok(h) => h.get(format!("{}/agents", base.trim_end_matches('/'))).send().await.map(|r| r.status().is_success()).unwrap_or(false),
        Err(_) => false,
    };
    json!({ "plan": "always — deterministic, free", "explain": { "reachable": reachable, "url": base, "needs": "a signed-in caller; spends the host's model credits" } })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn q(text: &str) -> Brief {
        brief(&AskBody { question: Some(text.into()), ..Default::default() })
    }

    #[test]
    fn reads_amount_horizon_and_appetite() {
        let b = q("I have $5,000 for 6 months, keep it safe");
        assert_eq!(b.amount, 5000.0);
        assert_eq!(b.months, 6.0);
        assert_eq!(b.risk, Risk::Low);
        assert!(b.parsed.contains(&"amount".to_string()));
        assert!(b.parsed.contains(&"horizon".to_string()));
    }

    #[test]
    fn reads_suffixes_words_and_chains() {
        let b = q("put 2.5k usdc on base for a year, aggressive");
        assert_eq!(b.amount, 2500.0);
        assert_eq!(b.months, 12.0);
        assert_eq!(b.risk, Risk::High);
        assert_eq!(b.chain.as_deref(), Some("base"));
        let b = q("10k on solana, for two weeks");
        assert_eq!(b.amount, 10_000.0);
        assert!((b.months - 24.0 / 52.0).abs() < 1e-9);
        assert_eq!(b.chain.as_deref(), Some("solana"));
    }

    #[test]
    fn bare_numbers_are_not_horizons() {
        let b = q("3 months with 1000");
        assert_eq!(b.months, 3.0);
        assert_eq!(b.amount, 1000.0);
        let b = q("what pays most over 2 years");
        assert_eq!(b.amount, DEFAULT_AMOUNT);
        assert_eq!(b.months, 24.0);
        assert_eq!(b.risk, Risk::High);
    }

    #[test]
    fn mid_words_read_as_medium_and_farms_fail_low() {
        assert_eq!(q("balanced, 1000 for a year").risk, Risk::Medium);
        assert_eq!(q("2k, bold, 3 months").risk, Risk::High);
        assert!(q("balanced, 1000 for a year").parsed.contains(&"risk".to_string()));
        let mut hub = hub_fixture();
        // make the farm core-tier: low appetite must still refuse it for its emissions
        hub["hub"][3]["tier"] = json!("core");
        hub["hub"][3]["best"]["apy_mean_30d"] = json!(100.0);
        hub["hub"][3]["best"]["tvl_usd"] = json!(50_000_000.0);
        let out = plan(&hub, &AskBody { question: Some("1000 safe".into()), ..Default::default() });
        let ids: Vec<&str> = out["picks"].as_array().unwrap().iter().map(|p| p["id"].as_str().unwrap()).collect();
        assert_eq!(ids, vec!["aave-v3"]);
        assert_eq!(out["excluded"]["emissions"], 1);
    }

    #[test]
    fn body_fields_win_over_text() {
        let b = brief(&AskBody {
            question: Some("$5k for 6 months, safe".into()),
            amount: Some(250.0),
            months: Some(1.0),
            risk: Some("high".into()),
            chain: Some("Ethereum".into()),
            ..Default::default()
        });
        assert_eq!(b.amount, 250.0);
        assert_eq!(b.months, 1.0);
        assert_eq!(b.risk, Risk::High);
        assert_eq!(b.chain.as_deref(), Some("ethereum"));
        assert!(b.parsed.is_empty());
    }

    #[test]
    fn earn_compounds_the_quoted_apy() {
        assert_eq!(earn(1000.0, 10.0, 12.0), 100.0);
        assert!((earn(1000.0, 10.0, 6.0) - 48.81).abs() < 0.01);
        assert_eq!(earn(1000.0, 0.0, 6.0), 0.0);
    }

    fn hub_fixture() -> Value {
        json!({
            "as_of": 1, "age_seconds": 10,
            "hub": [
                { "id": "aave-v3", "name": "Aave V3", "tier": "core", "category": "Lending", "since": 2020,
                  "legit": ["five years live"], "risks": ["smart-contract risk"], "enterable_from_desk": true,
                  "apr_band": { "apr_lower": 2.0, "apr_upper": 14.0, "confidence": "high", "basis": "t" },
                  "best": { "module_id": "llama:a", "symbol": "USDC", "chain": "Ethereum", "desk": "ethereum", "apy": 11.66, "apy_mean_30d": 4.34, "tvl_usd": 19_000_000.0 },
                  "chains": [
                    { "chain": "Ethereum", "desk": "ethereum", "module": "eth", "enterable": true, "apr_band": { "apr_lower": 2.0, "apr_upper": 14.0, "confidence": "high" },
                      "best": { "module_id": "llama:a", "symbol": "USDC", "chain": "Ethereum", "desk": "ethereum", "apy": 11.66, "apy_mean_30d": 4.34, "tvl_usd": 19_000_000.0 } },
                    { "chain": "Base", "desk": "base", "module": "eth", "enterable": true, "apr_band": { "apr_lower": 3.0, "apr_upper": 9.0, "confidence": "high" },
                      "best": { "module_id": "llama:b", "symbol": "USDC", "chain": "Base", "desk": "base", "apy": 6.0, "apy_mean_30d": 5.5, "tvl_usd": 50_000_000.0 } }
                  ] },
                { "id": "hyperliquid-vaults", "name": "Hyperliquid vaults", "tier": "established", "category": "Perps vaults",
                  "legit": [], "risks": ["the leader can lose it all"], "enterable_from_desk": true,
                  "apr_band": { "apr_lower": -594.0, "apr_upper": 1326.0, "confidence": "low", "basis": "t" },
                  "best": { "module_id": "hl:vault:x", "symbol": "USDC", "chain": "Hyperliquid", "desk": "hyperliquid", "apy": 540.0, "tvl_usd": 6_000_000.0 },
                  "chains": [ { "chain": "Hyperliquid", "desk": "hyperliquid", "module": "hyperliquid", "enterable": true,
                      "best": { "module_id": "hl:vault:x", "symbol": "USDC", "chain": "Hyperliquid", "apy": 540.0, "tvl_usd": 6_000_000.0 },
                      "apr_band": { "apr_lower": -594.0, "apr_upper": 1326.0, "confidence": "low" } } ] },
                { "id": "pendle", "name": "Pendle", "tier": "established", "category": "Fixed yield", "legit": [], "risks": [],
                  "enterable_from_desk": false,
                  "apr_band": { "apr_lower": 2.5, "apr_upper": 37.0, "confidence": "high", "basis": "t" },
                  "best": { "module_id": "llama:p", "symbol": "PT", "chain": "Ethereum", "desk": "ethereum", "apy": 16.0, "apy_mean_30d": 15.0, "tvl_usd": 100_000_000.0 },
                  "chains": [ { "chain": "Ethereum", "desk": "ethereum", "module": "eth", "enterable": false,
                      "best": { "module_id": "llama:p", "symbol": "PT", "chain": "Ethereum", "apy": 16.0, "apy_mean_30d": 15.0, "tvl_usd": 100_000_000.0 },
                      "apr_band": { "apr_lower": 2.5, "apr_upper": 37.0, "confidence": "high" } } ] },
                { "id": "curve", "name": "Curve", "tier": "established", "category": "Stable LP", "legit": [], "risks": [],
                  "enterable_from_desk": false,
                  "apr_band": { "apr_lower": 0.0, "apr_upper": 146.0, "confidence": "high", "basis": "t" },
                  "best": { "module_id": "llama:c", "symbol": "IDAI-IUSDC", "chain": "Ethereum", "desk": "ethereum", "apy": 123.0, "emissions_share": 90.0, "tvl_usd": 2_000_000.0 },
                  "chains": [ { "chain": "Ethereum", "desk": "ethereum", "module": "eth", "enterable": false,
                      "best": { "module_id": "llama:c", "symbol": "IDAI-IUSDC", "chain": "Ethereum", "apy": 123.0, "emissions_share": 90.0, "tvl_usd": 2_000_000.0 },
                      "apr_band": { "apr_lower": 0.0, "apr_upper": 146.0, "confidence": "high" } } ] },
                { "id": "bittensor", "name": "Bittensor", "tier": "frontier", "legit": [], "risks": [], "enterable_from_desk": true,
                  "apr_band": null, "best": { "module_id": "tao:sn0", "symbol": "TAO root", "chain": "Bittensor", "desk": "tao", "apy": null, "tvl_usd": 1.0e9 },
                  "chains": [] },
                { "id": "ondo", "name": "Ondo", "tier": "established", "gated": true, "legit": [], "risks": [], "best": null, "chains": [] }
            ]
        })
    }

    #[test]
    fn low_appetite_keeps_core_only_and_projects_the_floor() {
        let out = plan(&hub_fixture(), &AskBody { question: Some("$10,000 for a year, safe".into()), ..Default::default() });
        let picks = out["picks"].as_array().unwrap();
        assert_eq!(picks.len(), 1);
        assert_eq!(picks[0]["id"], "aave-v3");
        assert_eq!(picks[0]["earn"]["floor"], 200.0);
        // typical = today's 11.66 capped by the 30d mean 4.34
        assert_eq!(picks[0]["apy_typical"], 4.34);
        assert_eq!(out["excluded"]["tier"], 4);
        assert_eq!(out["excluded"]["gated"], 1);
        assert_eq!(out["brief"]["risk"], "low");
    }

    #[test]
    fn medium_appetite_steps_around_thin_negative_bands() {
        let out = plan(&hub_fixture(), &AskBody { question: Some("1000 for 6 months".into()), ..Default::default() });
        let ids: Vec<&str> = out["picks"].as_array().unwrap().iter().map(|p| p["id"].as_str().unwrap()).collect();
        assert!(ids.contains(&"aave-v3"));
        assert!(ids.contains(&"pendle"));
        assert!(!ids.contains(&"hyperliquid-vaults"));
        // the vault's band is thin and negative; the farm has no 30-day mean
        assert!(!ids.contains(&"curve"));
        assert_eq!(out["excluded"]["thin_record"], 1);
        assert_eq!(out["excluded"]["thin_pool"], 1);
        // bittensor quotes no rate but is frontier — the tier gate says no first
        assert_eq!(out["excluded"]["tier"], 1);
        assert_eq!(out["excluded"]["gated"], 1);
    }

    #[test]
    fn high_appetite_shows_everything_with_a_rate() {
        let out = plan(&hub_fixture(), &AskBody { question: Some("max yield, 1000 for a month".into()), ..Default::default() });
        let picks = out["picks"].as_array().unwrap();
        assert_eq!(picks[0]["id"], "hyperliquid-vaults");
        assert_eq!(picks[0]["thin_record"], true); // the vault: negative, low-confidence band
        assert_eq!(picks[1]["thin_record"], true); // the farm: no 30-day mean
        assert_eq!(picks[2]["id"], "pendle");
        assert_eq!(picks[2]["thin_record"], false);
        assert_eq!(picks.len(), 3); // pendle, curve ride along — nothing hidden at high appetite
        // the floor of a negative band is reported as zero, never negative
        assert_eq!(picks[0]["band"]["lower"], 0.0);
        assert_eq!(picks[0]["earn"]["floor"], 0.0);
    }

    #[test]
    fn a_chain_picks_that_chains_numbers() {
        let out = plan(&hub_fixture(), &AskBody { question: Some("$1000 on base for a year".into()), ..Default::default() });
        let picks = out["picks"].as_array().unwrap();
        assert_eq!(picks.len(), 1);
        assert_eq!(picks[0]["chain"], "Base");
        assert_eq!(picks[0]["module_id"], "llama:b");
        assert_eq!(picks[0]["earn"]["floor"], 30.0);
        assert_eq!(out["excluded"]["off_chain"], 3);
    }

    #[test]
    fn from_desk_drops_read_only_rows() {
        let out = plan(&hub_fixture(), &AskBody { question: Some("1000 from this desk".into()), ..Default::default() });
        let ids: Vec<&str> = out["picks"].as_array().unwrap().iter().map(|p| p["id"].as_str().unwrap()).collect();
        assert_eq!(ids, vec!["aave-v3"]);
        // pendle and curve are both read-only — the desk gate speaks before depth does
        assert_eq!(out["excluded"]["not_enterable"], 2);
        assert_eq!(out["excluded"]["thin_pool"], 0);
        assert_eq!(out["brief"]["from_desk"], true);
    }
}
