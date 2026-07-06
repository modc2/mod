// Free-text market-topic matcher — shared by the trader leaderboard filter
// (find the best traders for a topic) and the strat market filter (only act on
// markets matching the query, so a strat isn't "too general").
//
// Semantics: a query is OR-ed across comma/pipe-separated GROUPS, and AND-ed
// across the tokens WITHIN a group. Each group is lowercased, split on
// non-alphanumerics, with a small set of stopwords dropped. A title matches if
// ANY group matches; a group matches when the title contains EVERY token in it.
// So "price of bitcoin" → one group [price, bitcoin] → matches "Bitcoin price
// above $100k", and "bitcoin, btc" → groups [bitcoin] OR [btc] → matches either
// a "Bitcoin …" or a "BTC …" title (so a crypto strat catches both tickers).
// An empty / all-stopword query is a no-op → matches everything.
//
// Mirror of `market_matches_query` in src/api/src/categories.rs — keep in sync.

const STOPWORDS = new Set([
  "of", "the", "a", "an", "to", "in", "on", "for", "and", "or", "at", "by",
  "is", "be", "will", "this", "that", "with", "vs",
]);

/** Lowercase + split a query into meaningful tokens (stopwords removed). */
export function marketQueryTokens(query: string): string[] {
  return query
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter((t) => t.length > 0 && !STOPWORDS.has(t));
}

/** True if `title` matches `query`. OR across comma/pipe groups, AND within a
    group. Empty/whitespace query ⇒ matches all. A group with no meaningful
    tokens falls back to a raw trimmed-substring test. */
export function marketMatchesQuery(title: string, query: string | undefined | null): boolean {
  if (!query || !query.trim()) return true;
  const hay = title.toLowerCase();
  const groups = query.split(/[,|]/).map((g) => g.trim()).filter((g) => g.length > 0);
  if (groups.length === 0) return true;
  return groups.some((group) => {
    const tokens = marketQueryTokens(group);
    if (tokens.length === 0) return hay.includes(group.toLowerCase());
    return tokens.every((t) => hay.includes(t));
  });
}

/** True if ANY of a trader's market titles match the query. */
export function traderMatchesMarketQuery(titles: string[], query: string | undefined | null): boolean {
  if (!query || !query.trim()) return true;
  return titles.some((t) => marketMatchesQuery(t, query));
}

/** How many of a trader's titles match — used as a leaderboard sort tiebreaker
    so traders heavier in the topic surface first. */
export function marketQueryMatchCount(titles: string[], query: string | undefined | null): number {
  if (!query || !query.trim()) return 0;
  return titles.reduce((n, t) => (marketMatchesQuery(t, query) ? n + 1 : n), 0);
}
