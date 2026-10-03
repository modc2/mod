//! Polymarket's taker fee — the Rust mirror of `app/lib/fees.ts`.
//!
//! The matcher charges the taker, and only the taker:
//!
//! ```text
//! fee = rate x p x (1 - p) x shares
//! ```
//!
//! `rate` is per category (crypto 7%, sports/economics/culture/weather 5%,
//! politics/finance/tech/mentions 4%, geopolitics free). The dollar fee peaks
//! at 50c and is symmetric — a fill at 30c and one at 70c pay the same — so a
//! coin-flip market is the most expensive place to trade and a resolved leg
//! (p = 0 or 1) is free, which is why a redeem costs nothing.
//!
//! This engine used to book zero for all of it, which meant its per-strat
//! ledger reported a realized P&L the wallet never actually held. It also
//! meant the capital it freed to fund a buy was short by exactly the fee the
//! matcher was about to take.
//!
//! Two things deliberately DON'T live here:
//!   * the `fee_rate_bps` on a signed order. That stays 0 — Polymarket's docs
//!     are explicit that fees are applied at match time and the order does not
//!     carry them, and any other value gets the POST rejected as an invalid
//!     payload.
//!   * gas. CLOB fills are matched on-chain by Polymarket's operator and
//!     redeems/withdrawals go through its relayer, so a trading engine pays no
//!     gas at all. What a deployment pays for (proxy deploy, approvals,
//!     funding) is priced in the browser, once, in `app/lib/fees.ts`.
//!
//! Rates read from docs.polymarket.com/polymarket-learn/trading/fees,
//! 2026-09-01. Keep in sync with `TAKER_FEE_RATE` in app/lib/fees.ts by hand,
//! the same way `categories.rs` mirrors the frontend's keyword lists.

/// The published taker rate for a market category.
pub fn rate_for_category(category: &str) -> f64 {
    match category {
        "crypto" => 0.07,
        "sports" | "economics" | "culture" | "weather" => 0.05,
        "politics" | "finance" | "tech" | "mentions" => 0.04,
        "geopolitics" => 0.0,
        _ => 0.05, // published "Other / General"
    }
}

/// The default when a market's name says nothing — the published general rate.
pub const DEFAULT_TAKER_RATE: f64 = 0.05;

/// Ordered category keywords. FIRST match wins, which is why geopolitics is
/// checked before politics ("Will the U.S. invade Iran" is a fee-free world
/// event, not a 4% politics market) and crypto before everything (a
/// `btc-updown-5m` candle is the most expensive market on the platform).
/// Mirror of `CATEGORY_KEYWORDS` in app/lib/fees.ts.
const CATEGORY_KEYWORDS: &[(&str, &[&str])] = &[
    ("crypto", &[
        "bitcoin", "btc", "ethereum", "eth-", "ether", "solana", "sol-", "xrp",
        "dogecoin", "doge", "crypto", "altcoin", "memecoin", "stablecoin", "defi",
        "nft", "binance", "coinbase listing", "updown", "up or down", "hyperliquid",
    ]),
    ("geopolitics", &[
        "ceasefire", "invade", "invasion", "war ", "at war", "nato", "sanction",
        "hostage", "missile", "airstrike", "troops", "peace deal", "peace plan",
        "nuclear test", "annex", "coup", "regime change", "military strike",
    ]),
    ("economics", &[
        "fed ", "federal reserve", "fomc", "interest rate", "rate cut", "rate hike",
        "cpi", "inflation", "unemployment", "jobs report", "gdp", "recession",
        "jerome powell", "basis points", " bps ",
    ]),
    ("finance", &[
        "stock", "s&p", "nasdaq", "dow jones", "earnings", "ipo", "market cap",
        "share price", "bankrupt", "acquisition", "merger",
    ]),
    ("sports", &[
        "nba", "nfl", "mlb", "nhl", "ncaa", "ufc", "atp", "wta", "epl", "laliga",
        "soccer", "football", "basketball", "baseball", "hockey", "tennis", "golf",
        "boxing", "olympic", "world cup", "champions league", "super bowl",
        "playoff", "grand prix", "formula 1", "f1-", " vs. ", " vs ", "o/u",
        "total-", "moneyline", "spread-", "lol-", "dota", "csgo", "cs2", "valorant",
        "esports", "-win-on-",
        // Daily team markets ("Will Bristol City FC win on 2026-09-01?")
        // carry no sport in the title at all — only the shape does.
        " win on ", " fc ", " cf ", "united win", "-vs-",
    ]),
    ("politics", &[
        "election", "president", "senate", "congress", "governor", "parliament",
        "prime minister", "nomination", "impeach", "cabinet", "republican",
        "democrat", "primary", "poll", "approval rating", "vote",
    ]),
    ("tech", &[
        "openai", "gpt", "claude", "anthropic", "gemini", "llm", "ai model",
        "apple", "google", "microsoft", "tesla", "spacex", "starship", "chip",
    ]),
    ("mentions", &["mention", "say the word", "tweet", "posts about", "how many times"]),
    ("culture", &[
        "oscar", "grammy", "emmy", "box office", "album", "movie", "rotten tomatoes",
        "billboard", "netflix", "time person of the year", "nobel",
    ]),
    ("weather", &["hurricane", "temperature", "rainfall", "snowfall", "tornado", "wildfire", "sea ice"]),
];

/// Best-effort category for a market, from its title (and slug, when the
/// caller has one — recurring series carry their whole identity there).
pub fn category_for_market(title: &str) -> &'static str {
    let hay = title.to_lowercase();
    if hay.trim().is_empty() {
        return "other";
    }
    for (cat, kws) in CATEGORY_KEYWORDS {
        for kw in *kws {
            if hay.contains(kw) {
                return cat;
            }
        }
    }
    "other"
}

/// The taker rate a market charges, inferred from its name.
pub fn rate_for_market(title: &str) -> f64 {
    rate_for_category(category_for_market(title))
}

/// `fee = rate x p x (1 - p) x shares`, in USDC.
pub fn taker_fee(shares: f64, price: f64, rate: f64) -> f64 {
    if !(shares > 0.0) || !(rate > 0.0) {
        return 0.0;
    }
    let p = price.clamp(0.0, 1.0);
    rate * p * (1.0 - p) * shares
}

/// The fee on a fill in `title`'s market, priced at that market's own rate.
pub fn fee_for_fill(title: &str, shares: f64, price: f64) -> f64 {
    taker_fee(shares, price, rate_for_market(title))
}

/// The same fee expressed against the dollars going in — `shares = notional /
/// price`, so this is `rate x (1 - p) x notional`.
pub fn taker_fee_on_notional(notional: f64, price: f64, rate: f64) -> f64 {
    if !(notional > 0.0) || !(price > 0.0) {
        return 0.0;
    }
    taker_fee(notional / price, price, rate)
}

/// Headroom to leave on top of a buy's notional so the matcher's fee doesn't
/// bounce it for insufficient balance, when the fill price isn't known yet.
///
/// Note the asymmetry that makes this NOT `taker_fee(.., 0.5, ..)`: against a
/// fixed number of SHARES the fee peaks at 50c, but against a fixed number of
/// DOLLARS it is `rate x (1 - p) x notional`, which grows as the price falls —
/// $100 buys twenty times more shares at 5c than at 100c. The bound as p -> 0
/// is the whole rate, so that is what we reserve. Being short here is a failed
/// order; being long is a few idle cents.
pub fn fee_headroom(notional: f64) -> f64 {
    if !(notional > 0.0) { return 0.0; }
    DEFAULT_TAKER_RATE * notional
}

/// Headroom when the fill price IS known — the exact fee, no padding.
pub fn fee_headroom_at(notional: f64, price: f64, rate: f64) -> f64 {
    taker_fee_on_notional(notional, price, rate)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn matches_the_published_fee_table() {
        // docs.polymarket.com, Crypto (7%), 100 shares.
        assert!((taker_fee(100.0, 0.50, 0.07) - 1.75).abs() < 0.005);
        assert!((taker_fee(100.0, 0.10, 0.07) - 0.63).abs() < 0.005);
        // Symmetric around a coin flip: 30c and 70c pay the same.
        assert!((taker_fee(100.0, 0.30, 0.05) - taker_fee(100.0, 0.70, 0.05)).abs() < 1e-9);
        // Sports 5%, politics 4%.
        assert!((taker_fee(100.0, 0.50, 0.05) - 1.25).abs() < 0.005);
        assert!((taker_fee(100.0, 0.50, 0.04) - 1.00).abs() < 0.005);
        // A resolved leg is free — which is why redeems cost nothing.
        assert_eq!(taker_fee(100.0, 1.0, 0.07), 0.0);
        assert_eq!(taker_fee(100.0, 0.0, 0.07), 0.0);
    }

    #[test]
    fn categories_resolve_the_way_the_browser_resolves_them() {
        assert_eq!(category_for_market("Bitcoin Up or Down"), "crypto");
        assert_eq!(category_for_market("Seattle Mariners vs. Boston Red Sox: O/U 7.5"), "sports");
        // Geopolitics is checked before politics, and it is free.
        assert_eq!(category_for_market("Will the U.S. invade Iran before 2027?"), "geopolitics");
        assert_eq!(rate_for_market("Will the U.S. invade Iran before 2027?"), 0.0);
        assert_eq!(category_for_market("Will the Fed decrease interest rates?"), "economics");
        assert_eq!(category_for_market(""), "other");
        assert_eq!(rate_for_market("something nobody has a keyword for"), DEFAULT_TAKER_RATE);
    }

    #[test]
    fn headroom_covers_every_price() {
        // Against a fixed DOLLAR notional the fee is rate x (1 - p), so it is
        // worst at the cheap end — not at 50c, where the per-SHARE fee peaks.
        for price in [0.01_f64, 0.05, 0.25, 0.5, 0.75, 0.99] {
            let actual = taker_fee_on_notional(100.0, price, DEFAULT_TAKER_RATE);
            assert!(
                fee_headroom(100.0) >= actual - 1e-9,
                "headroom {} short of the fee at {}c: {}",
                fee_headroom(100.0), price * 100.0, actual,
            );
        }
        // A 1c fill is the near-worst case and it very nearly costs the rate.
        assert!((taker_fee_on_notional(100.0, 0.01, 0.05) - 4.95).abs() < 1e-9);
        // Knowing the price prices it exactly instead.
        assert!((fee_headroom_at(100.0, 0.5, 0.05) - 2.5).abs() < 1e-9);
    }
}
