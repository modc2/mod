//! MARKET SENTIMENT — the live engine's half of `app/lib/marketSentiment.ts`.
//!
//! The copy gate has two dimensions that live on the trade itself (side, price,
//! size, category) and one that does not: what the MARKET was doing when the
//! leader took it. That third one is this file.
//!
//! ```text
//! drift = p(now) − p(now − windowHours)      on the leader's OWN outcome token
//!
//!   bullish   drift ≥ +flatBand   the crowd was moving toward what they bought
//!   bearish   drift ≤ −flatBand   the crowd was moving away — a contrarian entry
//!   flat      |drift| < flatBand  the market barely moved
//!   unknown   no usable history
//! ```
//!
//! Two invariants, both mirrored from the TypeScript:
//!
//! 1. The reading is taken on the token the leader traded, so a positive drift
//!    always means the same thing whichever leg that is.
//! 2. **UNKNOWN PASSES.** A market whose price history did not load has no
//!    mood, and a filter that silently rejected it would recreate this
//!    module's oldest bug — a gate nobody chose, refusing most of the flow.
//!    `unknown: "block"` is available and is always an explicit choice.
//!
//! The gate itself is pure and synchronous. The DATA is not: one CLOB
//! prices-history request per outcome token, TTL-cached in-process, batched
//! once per cycle by `fetch_sentiment`. That is why the engine applies this
//! dimension in a second pass over the cycle's mirror candidates rather than
//! inside the per-trade loop — see `live_engine.rs`.

use std::collections::{HashMap, HashSet};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use serde_json::Value;

const CLOB_API: &str = "https://clob.polymarket.com";

pub const DEFAULT_WINDOW_HOURS: f64 = 6.0;
pub const DEFAULT_FLAT_BAND: f64 = 0.02;

/// Outcome tokens one cycle will spend price-history requests on. A cycle that
/// blows past this reads the rest as `unknown`, which passes — the gate
/// degrades toward copying, never toward silence.
const TOKEN_BUDGET: usize = 120;

/// How long a token's series is reused. Sentiment is a multi-hour drift; a
/// 3-minute-old series moves the reading by well under the flat band, and
/// re-pulling 120 histories every 2-minute cycle would be the single largest
/// upstream cost in the engine.
const CACHE_TTL: Duration = Duration::from_secs(180);

/// Mirror of `SentimentFilter` in app/lib/marketSentiment.ts. Unset dimensions
/// are OMITTED on the wire, not sent as null — identity.fixture.json compares
/// this key for key against the browser's object.
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
pub struct SentimentFilter {
    /// Moods to copy in: any of "bullish" | "bearish" | "flat".
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub lean: Option<Vec<String>>,
    /// Signed drift band, in probability points.
    #[serde(rename = "minDrift", default, skip_serializing_if = "Option::is_none")]
    pub min_drift: Option<f64>,
    #[serde(rename = "maxDrift", default, skip_serializing_if = "Option::is_none")]
    pub max_drift: Option<f64>,
    /// How far back the drift is measured. Default 6h.
    #[serde(rename = "windowHours", default, skip_serializing_if = "Option::is_none")]
    pub window_hours: Option<f64>,
    /// Movement under this counts as FLAT. Default 0.02.
    #[serde(rename = "flatBand", default, skip_serializing_if = "Option::is_none")]
    pub flat_band: Option<f64>,
    /// "pass" (default) | "block" — what to do with an unreadable market.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub unknown: Option<String>,
}

/// One market's reading as of one instant.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MarketSentiment {
    #[serde(rename = "tokenId")]
    pub token_id: String,
    pub price: f64,
    pub from: f64,
    pub drift: f64,
    pub strength: f64,
    /// "bullish" | "bearish" | "flat" | "unknown".
    pub lean: String,
    #[serde(rename = "windowHours")]
    pub window_hours: f64,
    pub points: usize,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub note: Option<String>,
}

/// True when the filter constrains something. An inactive filter costs zero
/// requests — this is the switch the engine checks before fetching anything.
pub fn filter_active(f: &Option<SentimentFilter>) -> bool {
    let Some(f) = f else { return false };
    f.lean.as_ref().is_some_and(|l| !l.is_empty() && l.len() < 4)
        || f.min_drift.is_some()
        || f.max_drift.is_some()
        || f.unknown.as_deref() == Some("block")
}

pub fn window_hours(f: &Option<SentimentFilter>) -> f64 {
    match f.as_ref().and_then(|f| f.window_hours) {
        Some(h) if h.is_finite() && h > 0.0 => h,
        _ => DEFAULT_WINDOW_HOURS,
    }
}

pub fn flat_band(f: &Option<SentimentFilter>) -> f64 {
    match f.as_ref().and_then(|f| f.flat_band) {
        Some(b) if b.is_finite() && b >= 0.0 => b,
        _ => DEFAULT_FLAT_BAND,
    }
}

pub fn unknown_reading(token_id: &str, window_hours: f64, note: &str) -> MarketSentiment {
    MarketSentiment {
        token_id: token_id.to_string(),
        price: 0.0,
        from: 0.0,
        drift: 0.0,
        strength: 0.0,
        lean: "unknown".to_string(),
        window_hours,
        points: 0,
        note: Some(note.to_string()),
    }
}

/// Read the drift out of an ascending `(ms, price)` series as of `at_ms`.
/// Port of `readSentiment` — same anchor rules, same unknown cases.
pub fn read_sentiment(
    series: &[(i64, f64)],
    at_ms: i64,
    token_id: &str,
    window_hours: f64,
    flat_band: f64,
) -> MarketSentiment {
    if series.is_empty() {
        return unknown_reading(token_id, window_hours, "no price history");
    }
    let window_ms = (window_hours * 3_600_000.0) as i64;

    let Some(now_idx) = series.iter().rposition(|(t, _)| *t <= at_ms) else {
        return unknown_reading(token_id, window_hours, "history starts after this trade");
    };
    let (now_t, now_p) = series[now_idx];

    // Anchor: last point at or before `at_ms − window`. Falling back to the
    // series head is only honest when the head is at least a third of the
    // window old — otherwise a "6h drift" would be four minutes of tape.
    let target = at_ms - window_ms;
    let from_idx = match series[..=now_idx].iter().rposition(|(t, _)| *t <= target) {
        Some(i) => i,
        None => {
            let covered = now_t - series[0].0;
            if covered < window_ms / 3 {
                return unknown_reading(
                    token_id,
                    window_hours,
                    &format!(
                        "only {:.1}h of history for a {}h window",
                        covered as f64 / 3_600_000.0,
                        window_hours
                    ),
                );
            }
            0
        }
    };
    let (_, from_p) = series[from_idx];

    let drift = now_p - from_p;
    let strength = drift.abs();
    let lean = if strength < flat_band {
        "flat"
    } else if drift > 0.0 {
        "bullish"
    } else {
        "bearish"
    };
    MarketSentiment {
        token_id: token_id.to_string(),
        price: now_p,
        from: from_p,
        drift,
        strength,
        lean: lean.to_string(),
        window_hours,
        points: now_idx - from_idx + 1,
        note: None,
    }
}

/// Why the gate rejected, or None when it passes. Named dimensions so the
/// heartbeat's gate tally can credit sentiment the way it credits price.
/// Port of `sentimentReject`.
pub fn sentiment_reject(
    reading: Option<&MarketSentiment>,
    filter: &Option<SentimentFilter>,
) -> Option<&'static str> {
    if !filter_active(filter) {
        return None;
    }
    let f = filter.as_ref().unwrap();
    let lean = reading.map(|r| r.lean.as_str()).unwrap_or("unknown");

    if lean == "unknown" {
        // Never a silent rejection — only when the strat asked for it.
        return if f.unknown.as_deref() == Some("block") {
            Some("sentiment-unknown")
        } else {
            None
        };
    }
    if let Some(wanted) = &f.lean {
        if !wanted.is_empty() && !wanted.iter().any(|w| w == lean) {
            return Some("sentiment");
        }
    }
    let drift = reading.map(|r| r.drift).unwrap_or(0.0);
    if let Some(min) = f.min_drift {
        if drift < min {
            return Some("sentiment-drift");
        }
    }
    if let Some(max) = f.max_drift {
        if drift > max {
            return Some("sentiment-drift");
        }
    }
    None
}

/// One line for the heartbeat: what the gate is set to.
pub fn describe(filter: &Option<SentimentFilter>) -> String {
    if !filter_active(filter) {
        return String::new();
    }
    let f = filter.as_ref().unwrap();
    let mut parts: Vec<String> = Vec::new();
    if let Some(l) = &f.lean {
        if !l.is_empty() && l.len() < 4 {
            parts.push(l.join("/"));
        }
    }
    if f.min_drift.is_some() || f.max_drift.is_some() {
        parts.push(format!(
            "drift {}…{}",
            f.min_drift.map(|d| format!("{:+.0}c", d * 100.0)).unwrap_or_else(|| "any".into()),
            f.max_drift.map(|d| format!("{:+.0}c", d * 100.0)).unwrap_or_else(|| "any".into()),
        ));
    }
    parts.push(format!("{}h", window_hours(filter)));
    if f.unknown.as_deref() == Some("block") {
        parts.push("skip unreadable".into());
    }
    parts.join(" · ")
}

// ── Getting the prices ─────────────────────────────────────────────────────

struct CachedSeries {
    at: Instant,
    points: Vec<(i64, f64)>,
}

/// `HashMap::new` is not const, so the map is built on first use. One process,
/// one cache — the engine runs every session in the same process.
static SERIES_CACHE: Mutex<Option<HashMap<String, CachedSeries>>> = Mutex::new(None);

/// Pull the price history every one of `token_ids` needs and read each one as
/// of `at_ms`. Never fails as a whole: a token whose fetch errors simply has
/// no entry, reads `unknown`, and is governed by the filter's own policy.
///
/// Returns (readings, tokens dropped for budget).
pub async fn fetch_sentiment(
    http: &reqwest::Client,
    token_ids: &[String],
    at_ms: i64,
    filter: &Option<SentimentFilter>,
) -> (HashMap<String, MarketSentiment>, usize) {
    let hours = window_hours(filter);
    let band = flat_band(filter);

    let mut seen: HashSet<&str> = HashSet::new();
    let mut wanted: Vec<&str> = Vec::new();
    for id in token_ids {
        if !id.is_empty() && seen.insert(id.as_str()) {
            wanted.push(id.as_str());
        }
    }
    let over_budget = wanted.len().saturating_sub(TOKEN_BUDGET);
    wanted.truncate(TOKEN_BUDGET);

    // A window's worth of runway before the oldest point we will read from, or
    // every early anchor lands inside the window and reads as "not enough
    // history".
    let start_sec = (at_ms - (hours * 3_600_000.0 * 1.2) as i64) / 1000;
    let end_sec = at_ms / 1000 + 60;

    let mut out: HashMap<String, MarketSentiment> = HashMap::new();
    let mut to_fetch: Vec<&str> = Vec::new();
    {
        let mut guard = SERIES_CACHE.lock().unwrap();
        let cache = guard.get_or_insert_with(HashMap::new);
        for id in &wanted {
            match cache.get(*id) {
                Some(c) if c.at.elapsed() < CACHE_TTL => {
                    out.insert(
                        (*id).to_string(),
                        read_sentiment(&c.points, at_ms, id, hours, band),
                    );
                }
                _ => to_fetch.push(id),
            }
        }
    }

    // Bounded fan-out. Shares the upstream budget with a cycle whose fills are
    // time-critical, so 4 at a time, same as the browser.
    for chunk in to_fetch.chunks(4) {
        let fetched = futures::future::join_all(chunk.iter().map(|id| {
            let url = format!(
                "{}/prices-history?market={}&startTs={}&endTs={}&fidelity=5",
                CLOB_API, id, start_sec, end_sec
            );
            let http = http.clone();
            async move {
                let points = match http.get(&url).send().await {
                    Ok(resp) => match resp.text().await {
                        Ok(text) => parse_history(&text),
                        Err(_) => Vec::new(),
                    },
                    Err(_) => Vec::new(),
                };
                ((*id).to_string(), points)
            }
        }))
        .await;

        let mut guard = SERIES_CACHE.lock().unwrap();
        let cache = guard.get_or_insert_with(HashMap::new);
        for (id, points) in fetched {
            if points.is_empty() {
                continue;
            }
            out.insert(id.clone(), read_sentiment(&points, at_ms, &id, hours, band));
            cache.insert(id, CachedSeries { at: Instant::now(), points });
        }
    }

    (out, over_budget)
}

/// prices-history stamps unix SECONDS; every series in this codebase is ms.
fn parse_history(text: &str) -> Vec<(i64, f64)> {
    let Ok(parsed) = serde_json::from_str::<Value>(text) else {
        return Vec::new();
    };
    let Some(history) = parsed.get("history").and_then(|h| h.as_array()) else {
        return Vec::new();
    };
    let mut points: Vec<(i64, f64)> = history
        .iter()
        .filter_map(|pt| {
            let t = pt.get("t").and_then(|t| t.as_i64())?;
            let p = pt.get("p").and_then(|p| p.as_f64())?;
            Some((if t > 1_000_000_000_000 { t } else { t * 1000 }, p))
        })
        .collect();
    points.sort_by_key(|(t, _)| *t);
    points
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tape(delta: f64, start: f64, points: usize, end_ms: i64) -> Vec<(i64, f64)> {
        (0..points)
            .map(|i| {
                (
                    end_ms - ((points - 1 - i) as i64) * 5 * 60_000,
                    start + delta * (i as f64) / ((points - 1) as f64),
                )
            })
            .collect()
    }

    #[test]
    fn direction_is_measured_on_the_leaders_own_token() {
        let now = 1_700_000_000_000;
        let up = read_sentiment(&tape(0.14, 0.4, 80, now), now, "t", 6.0, 0.02);
        let down = read_sentiment(&tape(-0.14, 0.6, 80, now), now, "t", 6.0, 0.02);
        let still = read_sentiment(&tape(0.005, 0.4, 80, now), now, "t", 6.0, 0.02);
        assert_eq!(up.lean, "bullish");
        assert_eq!(down.lean, "bearish");
        assert_eq!(still.lean, "flat");
        assert!(up.drift > 0.0 && down.drift < 0.0);
    }

    #[test]
    fn thin_history_is_unknown_not_flat() {
        let now = 1_700_000_000_000;
        assert_eq!(read_sentiment(&[], now, "t", 6.0, 0.02).lean, "unknown");
        assert_eq!(
            read_sentiment(&tape(0.1, 0.4, 4, now), now, "t", 6.0, 0.02).lean,
            "unknown"
        );
        // A trade older than the whole series is never marked at a future price.
        assert_eq!(
            read_sentiment(&tape(0.1, 0.4, 80, now), now - 90 * 86_400_000, "t", 6.0, 0.02).lean,
            "unknown"
        );
    }

    #[test]
    fn unknown_passes_unless_blocking_was_asked_for() {
        let f = Some(SentimentFilter { lean: Some(vec!["bullish".into()]), ..Default::default() });
        assert_eq!(sentiment_reject(None, &f), None);
        let blocking = Some(SentimentFilter {
            lean: Some(vec!["bullish".into()]),
            unknown: Some("block".into()),
            ..Default::default()
        });
        assert_eq!(sentiment_reject(None, &blocking), Some("sentiment-unknown"));
    }

    #[test]
    fn the_gate_names_its_dimension() {
        let now = 1_700_000_000_000;
        let bull = read_sentiment(&tape(0.14, 0.4, 80, now), now, "t", 6.0, 0.02);
        let bear_only = Some(SentimentFilter { lean: Some(vec!["bearish".into()]), ..Default::default() });
        assert_eq!(sentiment_reject(Some(&bull), &bear_only), Some("sentiment"));
        let bull_only = Some(SentimentFilter { lean: Some(vec!["bullish".into()]), ..Default::default() });
        assert_eq!(sentiment_reject(Some(&bull), &bull_only), None);
        let strong = Some(SentimentFilter {
            lean: Some(vec!["bullish".into()]),
            min_drift: Some(0.5),
            ..Default::default()
        });
        assert_eq!(sentiment_reject(Some(&bull), &strong), Some("sentiment-drift"));
    }

    #[test]
    fn an_empty_filter_costs_nothing() {
        assert!(!filter_active(&None));
        assert!(!filter_active(&Some(SentimentFilter::default())));
        // A window with no direction is a dial, not a gate.
        assert!(!filter_active(&Some(SentimentFilter {
            window_hours: Some(12.0),
            ..Default::default()
        })));
        assert!(filter_active(&Some(SentimentFilter {
            lean: Some(vec!["bearish".into()]),
            ..Default::default()
        })));
    }

    #[test]
    fn unset_dimensions_are_omitted_on_the_wire() {
        let f = SentimentFilter { lean: Some(vec!["bearish".into()]), ..Default::default() };
        let json = serde_json::to_string(&f).unwrap();
        assert_eq!(json, r#"{"lean":["bearish"]}"#);
    }
}
