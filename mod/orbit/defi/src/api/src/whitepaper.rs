//! Whitepapers, generated and content-addressed.
//!
//! The module's own whitepaper is a hand-written document compiled in. Each
//! *finance* module gets a generated one: its four faces (returns, liquidity,
//! conditions, execution) written down as a dated document, stored under the
//! protocol's object store at its CID. The CID cannot appear inside the
//! document it addresses, so it rides alongside in the response.

use serde_json::Value;

/// The hand-written whitepaper of the defi module itself.
pub const SELF: &str = include_str!("../../../WHITEPAPER.md");

fn s(v: &Value, path: &str) -> String {
    v.pointer(path).and_then(|x| x.as_str()).unwrap_or("—").to_string()
}

fn num(v: &Value, path: &str) -> String {
    match v.pointer(path) {
        Some(Value::Number(n)) => format!("{:.2}", n.as_f64().unwrap_or(0.0)),
        _ => "n/q".into(),
    }
}

fn money(v: &Value, path: &str) -> String {
    match v.pointer(path).and_then(|x| x.as_f64()) {
        Some(t) if t >= 1e9 => format!("${:.2}b", t / 1e9),
        Some(t) if t >= 1e6 => format!("${:.1}m", t / 1e6),
        Some(t) if t >= 1e3 => format!("${:.0}k", t / 1e3),
        Some(t) => format!("${t:.0}"),
        None => "—".into(),
    }
}

/// One finance module, written down. Everything in it comes from the live
/// module card — this is a snapshot with a date on it, not a promise.
pub fn module_paper(module: &Value, now: u64) -> String {
    let date = chrono_date(now);
    let id = s(module, "/id");
    let project = s(module, "/project");
    let name = s(module, "/name");
    let chain = s(module, "/chain_label");
    let kind = s(module, "/kind");
    let mut out = String::new();

    out.push_str(&format!("# {project} — {name}\n\n"));
    out.push_str(&format!(
        "*Finance module `{id}` of the DeFi ✦ Modular Finance protocol · {chain} · {kind} · generated {date}. \
         Every number is as-of this date, joined from live sources; nothing here is a promise.*\n\n"
    ));

    out.push_str("## 1. What it is\n\n");
    out.push_str(&format!(
        "A place money can go that gives a return: **{project}**'s {kind} on {chain}, symbol `{}`. \
         This document is its standing description under the protocol — returns, liquidity, conditions, \
         and the exact execution path in and out.\n\n",
        s(module, "/symbol")
    ));

    out.push_str("## 2. Returns\n\n");
    out.push_str(&format!("| | |\n|---|---|\n| APY | {}% |\n| — of which fees (`apy_base`) | {}% |\n| — of which emissions (`apy_reward`) | {}% |\n| 30-day mean | {}% |\n| 7-day change | {}% |\n\n",
        num(module, "/returns/apy"), num(module, "/returns/apy_base"), num(module, "/returns/apy_reward"),
        num(module, "/returns/apy_mean_30d"), num(module, "/returns/apy_change_7d")));
    out.push_str(&format!("Basis: {}. Fees and emissions are never merged — a rate that is mostly emissions is a different promise than one that is mostly fees.\n\n", s(module, "/returns/basis")));

    out.push_str("## 3. Liquidity\n\n");
    out.push_str(&format!(
        "Depth {} ({}). Entry: {}. Exit: {}{}{}.\n\n",
        money(module, "/liquidity/tvl_usd"),
        s(module, "/liquidity/depth"),
        s(module, "/liquidity/entry"),
        s(module, "/liquidity/exit"),
        module.pointer("/liquidity/exit_delay_days").and_then(|v| v.as_f64()).filter(|d| *d > 0.0).map(|d| format!(", up to {d:.0} days")).unwrap_or_default(),
        module.pointer("/liquidity/lock_days").and_then(|v| v.as_f64()).filter(|d| *d > 0.0).map(|d| format!(", locked {d:.0} days")).unwrap_or_default(),
    ));
    if let Some(note) = module.pointer("/liquidity/exit_note").and_then(|v| v.as_str()) {
        out.push_str(&format!("{note}\n\n"));
    }

    out.push_str("## 4. Conditions — what it is subject to\n\n");
    match module.get("conditions").and_then(|c| c.as_array()).filter(|c| !c.is_empty()) {
        Some(conds) => {
            for c in conds {
                out.push_str(&format!(
                    "- **{}** — {}\n",
                    c.get("level").and_then(|v| v.as_str()).unwrap_or("note"),
                    c.get("text").and_then(|v| v.as_str()).unwrap_or("")
                ));
            }
            out.push('\n');
        }
        None => out.push_str("No conditions recorded — which means unexamined, not safe.\n\n"),
    }

    out.push_str("## 5. Execution — the way in and out\n\n");
    match module.get("adapter").filter(|a| !a.is_null()) {
        Some(adapter) => {
            out.push_str(&format!(
                "Adapter `{}`{}.\n\n- **In:** {}\n- **Out:** {}\n- **Executed by:** {}\n\n",
                s(adapter, "/kind"),
                adapter.get("address").and_then(|v| v.as_str()).map(|a| format!(" at `{a}`")).unwrap_or_default(),
                s(adapter, "/enter"),
                s(adapter, "/exit"),
                s(adapter, "/executed_by"),
            ));
            let chain_id = s(module, "/chain");
            out.push_str(match chain_id.as_str() {
                "ethereum" | "base" | "sepolia" | "base-sepolia" | "evm" =>
                    "**Signers, by operation:** your own browser wallet can sign both legs — every quote \
                     carries a machine-readable `wallet` plan (approval, then the deposit/supply/swap) — or the \
                     `eth` module's keystore signs server-side with its own confirm gate. No key lives in this desk.\n\n",
                "solana" =>
                    "**Signers, by operation:** an injected Solana wallet (Phantom) can sign the Jupiter route, \
                     or the `solana` module's keystore signs server-side under its spend ceiling. No key lives in this desk.\n\n",
                "tao" =>
                    "**Signers, by operation:** the `bt` module's coldkey only — no browser wallet speaks Bittensor. \
                     Buying is a stake, selling is an unstake, and the bt module's own guard applies.\n\n",
                _ => "**Signers:** see the adapter above.\n\n",
            });
        }
        None => out.push_str(
            "**Read-only.** No adapter here: the module is listed for its terms, and entering it means the \
             protocol's own app. Adding an adapter is a row in `adapters.json`.\n\n",
        ),
    }

    out.push_str("## 6. Provenance and storage\n\n");
    out.push_str(&format!(
        "Source: `{}`. This document is generated from the live module card, content-addressed (CIDv1, raw, \
         sha2-256) and stored under the protocol's object store — fetch it back at `/objects/{{cid}}` with the \
         CID returned beside this text, or regenerate it any day for that day's numbers.\n\n",
        s(module, "/source"),
    ));

    out.push_str("## 7. Disclaimer\n\n");
    out.push_str(
        "Curated is not certified. The rates are the market's, not ours; the contracts are the protocol's, \
         not ours; an agent audit reduces the unknowns and certifies nothing. A quote measures the round trip \
         in and out *today* — tomorrow is not in this document.\n",
    );
    out
}

/// yyyy-mm-dd from a unix timestamp, without pulling in chrono.
fn chrono_date(ts: u64) -> String {
    let days = ts / 86_400;
    let mut year = 1970u64;
    let mut remaining = days;
    loop {
        let leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
        let len = if leap { 366 } else { 365 };
        if remaining < len {
            break;
        }
        remaining -= len;
        year += 1;
    }
    let leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
    let months = [31, if leap { 29 } else { 28 }, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    let mut month = 0usize;
    while remaining >= months[month] {
        remaining -= months[month];
        month += 1;
    }
    format!("{year}-{:02}-{:02}", month + 1, remaining + 1)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn a_paper_carries_the_four_faces_and_the_signer_rule() {
        let module = json!({
            "id": "llama:abc", "project": "Sky", "name": "sUSDS", "symbol": "SUSDS",
            "chain": "ethereum", "chain_label": "Ethereum", "kind": "savings",
            "returns": { "apy": 5.6, "apy_base": 5.6, "apy_reward": 0.0, "basis": "index" },
            "liquidity": { "tvl_usd": 173_000_000.0, "depth": "deep", "entry": "instant", "exit": "instant" },
            "conditions": [ { "level": "note", "text": "governance sets the rate" } ],
            "adapter": { "kind": "erc4626", "address": "0xa3931d71877c0e7a3148cb7eb4463524fec27fbd",
                         "enter": "deposit", "exit": "withdraw", "executed_by": "eth" },
            "source": "defillama",
        });
        let paper = module_paper(&module, 1_757_500_000);
        assert!(paper.contains("Sky — sUSDS"));
        assert!(paper.contains("browser wallet"));
        assert!(paper.contains("Returns"));
        assert!(paper.contains("Curated is not certified"));
    }

    #[test]
    fn dates_come_out_right() {
        assert_eq!(chrono_date(0), "1970-01-01");
        assert_eq!(chrono_date(1_757_500_000), "2025-09-10");
    }
}
