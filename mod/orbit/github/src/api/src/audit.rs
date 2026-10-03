//! The decision log.
//!
//! Every gated request — allowed or refused — appends one JSON line to
//! `~/.mod/github/audit.jsonl`: when, who, from where, what they asked for,
//! what the module decided and why. A gate with no record of its refusals is
//! a gate nobody can debug and nobody can trust, so the refusals are logged
//! with the same care as the successes.
//!
//! What is deliberately NOT written: request bodies, GitHub tokens, and the
//! bearer token itself. The caller's *address* is an identity, not a secret;
//! their credential never touches the disk.

use std::io::Write;

use serde::{Deserialize, Serialize};

/// Rotate once the log passes this, keeping one previous generation. Bounded
/// so an unattended box cannot fill its disk with a public endpoint's traffic.
const MAX_BYTES: u64 = 4 * 1024 * 1024;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Entry {
    pub t: f64,
    /// The key when signed, `ip:…` when not.
    pub who: String,
    pub ip: String,
    pub role: String,
    pub method: String,
    pub route: String,
    /// "ok" | "banned" | "unauthorized" | "rate_limited" | "invalid" | "error"
    pub decision: String,
    pub status: u16,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub detail: Option<String>,
    pub ms: u64,
}

pub fn append(e: &Entry) {
    let path = crate::store::path("audit.jsonl");
    if std::fs::metadata(&path).map(|m| m.len()).unwrap_or(0) > MAX_BYTES {
        let _ = std::fs::rename(&path, path.with_extension("jsonl.1"));
    }
    let Ok(line) = serde_json::to_string(e) else { return };
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&path) {
        let _ = writeln!(f, "{line}");
    }
}

/// The most recent `n` entries, newest first, optionally filtered.
pub fn tail(n: usize, subject: Option<&str>, denied_only: bool) -> Vec<Entry> {
    let path = crate::store::path("audit.jsonl");
    let Ok(text) = std::fs::read_to_string(&path) else { return vec![] };
    let mut out: Vec<Entry> = text
        .lines()
        .rev()
        .filter_map(|l| serde_json::from_str::<Entry>(l).ok())
        .filter(|e| !denied_only || e.decision != "ok")
        .filter(|e| match subject {
            Some(s) if !s.is_empty() => e.who.eq_ignore_ascii_case(s) || e.ip == s,
            _ => true,
        })
        .take(n)
        .collect();
    out.shrink_to_fit();
    out
}

/// Refusals per subject over the last `window` seconds — the number that says
/// whether someone is probing rather than using.
pub fn denials(window: f64, now: f64) -> std::collections::BTreeMap<String, u32> {
    let mut counts = std::collections::BTreeMap::new();
    for e in tail(4000, None, true) {
        if now - e.t <= window {
            *counts.entry(e.who.clone()).or_insert(0) += 1;
        }
    }
    counts
}
