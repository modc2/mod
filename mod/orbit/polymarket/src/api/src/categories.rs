// Semantic category keywords — mirror of CATEGORY_KEYWORDS in
// app/lib/polymarket.ts. Kept in sync by hand. When the frontend changes,
// update here too.

pub fn keywords_for(category: &str) -> &'static [&'static str] {
    match category {
        "politics" => &[
            "election", "president", "congress", "senate", "party", "trump",
            "biden", "vote", "governor", "republican", "democrat", "midterm",
            "political",
        ],
        "sports" => &[
            "nba", "nfl", "mlb", "nhl", "soccer", "football", "basketball",
            "baseball", "tennis", "ufc", "championship", "playoffs",
            "super bowl", "world cup", "winner:", "score", "game handicap",
            "match", "beat the", "grand prix", "f1",
        ],
        "crypto" => &[
            "bitcoin", "btc", "eth", "ethereum", "solana", "sol", "crypto",
            "token", "altcoin", "defi", "nft", "bnb", "dogecoin", "xrp",
            "memecoin",
        ],
        "pop-culture" => &[
            "movie", "album", "oscar", "grammy", "emmy", "celebrity",
            "kardashian", "taylor swift", "drake", "rihanna", "box office",
            "tv show", "streaming",
        ],
        "business" => &[
            "stock", "market cap", "revenue", "ipo", "company", "ceo",
            "acquisition", "earnings", "nasdaq", "s&p", "dow",
        ],
        "science" => &[
            "nasa", "space", "climate", "temperature", "earthquake",
            "hurricane", "sea ice", "starship", "asteroid", "disease",
        ],
        "tech" => &[
            "apple", "google", "meta", "microsoft", "openai", "ai model",
            "launch", "release date", "tesla",
        ],
        "ai" => &[
            "ai", "gpt", "claude", "openai", "llm", "artificial intelligence",
            "chatgpt", "gemini", "machine learning",
        ],
        _ => &[],
    }
}

/// True if any of the trader's market titles match the category's keywords.
/// Unknown / empty category matches everything (filter is a no-op).
pub fn trader_in_category(market_titles: &[String], category: &str) -> bool {
    if category.is_empty() { return true; }
    let kws = keywords_for(category);
    if kws.is_empty() { return true; }
    market_titles.iter().any(|m| {
        let lower = m.to_lowercase();
        kws.iter().any(|kw| lower.contains(kw))
    })
}

/// Per-title variant. True if a single market title matches the category.
pub fn title_in_category(market_title: &str, category: &str) -> bool {
    if category.is_empty() { return true; }
    let kws = keywords_for(category);
    if kws.is_empty() { return true; }
    let lower = market_title.to_lowercase();
    kws.iter().any(|kw| lower.contains(kw))
}

// ─── Free-text market-topic query matching ───────────────────────────────
// Mirror of `marketMatchesQuery` in app/lib/marketQuery.ts — keep in sync.
// Used by both the trader leaderboard filter (find the best traders for a
// topic) and the live engine's strat market filter (only act on matching
// markets). Semantics: OR across comma/pipe-separated GROUPS, AND across the
// tokens WITHIN a group (stopwords dropped). A title matches if ANY group's
// tokens are all present — so "bitcoin, btc" matches either a "Bitcoin …" or a
// "BTC …" title. Empty/all-stopword query is a no-op (matches everything).
// Mirror of `marketMatchesQuery` in src/app/app/lib/marketQuery.ts.

const STOPWORDS: &[&str] = &[
    "of", "the", "a", "an", "to", "in", "on", "for", "and", "or", "at", "by",
    "is", "be", "will", "this", "that", "with", "vs",
];

/// Split a query into meaningful lowercased tokens (stopwords removed).
fn query_tokens(query: &str) -> Vec<String> {
    query
        .to_lowercase()
        .split(|c: char| !c.is_ascii_alphanumeric())
        .filter(|t| !t.is_empty() && !STOPWORDS.contains(t))
        .map(|t| t.to_string())
        .collect()
}

/// True if `title` matches `query`. OR across comma/pipe groups, AND within a
/// group. Empty query ⇒ matches everything. A group with no meaningful tokens
/// falls back to a raw substring test.
pub fn market_matches_query(title: &str, query: &str) -> bool {
    let trimmed = query.trim();
    if trimmed.is_empty() {
        return true;
    }
    let hay = title.to_lowercase();
    trimmed
        .split(|c: char| c == ',' || c == '|')
        .map(|g| g.trim())
        .filter(|g| !g.is_empty())
        .any(|group| {
            let tokens = query_tokens(group);
            if tokens.is_empty() {
                hay.contains(&group.to_lowercase())
            } else {
                tokens.iter().all(|t| hay.contains(t.as_str()))
            }
        })
}

/// True if ANY of a trader's market titles match the query.
pub fn trader_matches_query(market_titles: &[String], query: &str) -> bool {
    if query.trim().is_empty() {
        return true;
    }
    market_titles.iter().any(|m| market_matches_query(m, query))
}

/// How many of a trader's titles match — sort tiebreaker so traders heaviest
/// in the queried topic surface first.
pub fn query_match_count(market_titles: &[String], query: &str) -> usize {
    if query.trim().is_empty() {
        return 0;
    }
    market_titles
        .iter()
        .filter(|m| market_matches_query(m, query))
        .count()
}

/// Count how many of a trader's market titles fall in the category.
/// Used as a sort tiebreaker so traders heavier in the category rank first.
pub fn title_match_count(market_titles: &[String], category: &str) -> usize {
    if category.is_empty() { return 0; }
    let kws = keywords_for(category);
    if kws.is_empty() { return 0; }
    market_titles
        .iter()
        .filter(|m| {
            let lower = m.to_lowercase();
            kws.iter().any(|kw| lower.contains(kw))
        })
        .count()
}

#[cfg(test)]
mod query_tests {
    use super::*;

    #[test]
    fn empty_query_matches_everything() {
        assert!(market_matches_query("Bitcoin price above $100k", ""));
        assert!(market_matches_query("anything", "   "));
    }

    #[test]
    fn all_tokens_must_be_present() {
        // "price of bitcoin" → tokens [price, bitcoin] (of dropped).
        assert!(market_matches_query("Bitcoin price above $100k", "price of bitcoin"));
        assert!(market_matches_query("Will the price of Bitcoin hit $150k?", "bitcoin"));
        // Missing a token → no match.
        assert!(!market_matches_query("Will Bitcoin hit $150k?", "price bitcoin"));
        // Unrelated market → no match.
        assert!(!market_matches_query("Trump wins 2024 election", "bitcoin"));
    }

    #[test]
    fn comma_groups_are_or_matched() {
        // "bitcoin, btc" → group [bitcoin] OR group [btc]: a BTC-titled market
        // matches even though it never says "bitcoin", and vice versa.
        assert!(market_matches_query("BTC above $100k", "bitcoin, btc"));
        assert!(market_matches_query("Will Bitcoin hit $150k?", "bitcoin, btc"));
        // AND still holds WITHIN a group across the OR.
        assert!(market_matches_query("Bitcoin price above $100k", "price bitcoin, eth"));
        assert!(market_matches_query("ETH above $5k", "price bitcoin, eth"));
        // Matches none of the groups → no match. Pipe works as a separator too.
        assert!(!market_matches_query("Trump wins 2024", "bitcoin | btc | eth"));
    }

    #[test]
    fn all_stopword_query_falls_back_to_substring() {
        // No meaningful tokens — raw substring test against the trimmed query.
        assert!(market_matches_query("the winner is", "the"));
        assert!(!market_matches_query("winner", "the"));
    }

    #[test]
    fn trader_and_count_helpers() {
        let titles = vec![
            "Bitcoin price above $100k".to_string(),
            "ETH above $5k".to_string(),
            "Trump wins".to_string(),
        ];
        assert!(trader_matches_query(&titles, "bitcoin"));
        assert!(!trader_matches_query(&titles, "solana"));
        assert_eq!(query_match_count(&titles, "above"), 2);
        assert_eq!(query_match_count(&titles, ""), 0);
    }
}
