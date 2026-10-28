// MARKET TYPES — the presets behind "find me traders in bitcoin".
//
// Each preset is nothing but a label and a market-topic QUERY, because the
// query is the only thing that travels. The same string does three jobs and
// has to mean the same thing in all three:
//
//   1. `/active-traders?marketQuery=…` — narrows the leaderboard to traders
//      active in matching markets AND recomputes their P&L / trades / win
//      rate / Sharpe from ONLY those markets (routes.rs `apply_pagination`).
//   2. The allocation's `params.marketQuery` — the live engine's entry gate,
//      so a trader you found on their bitcoin flow is copied on their bitcoin
//      flow and nothing else (`copy.rs::engine_config`).
//   3. The row's backtest — `Strat.shouldMirror` applies the same gate, so
//      the card's number is a claim about the slice you actually copy.
//
// That is why these are queries and not the fixed `CATEGORIES` buckets from
// lib/polymarket.ts: a category is a server-side keyword list with no wire
// form an allocation can carry, so a category-filtered search could only ever
// be discovery theatre — find on bitcoin, then copy everything they touch.
//
// Matching is `marketMatchesQuery`: OR across comma groups, AND across the
// tokens in a group, substring against the market title.

/** A named market slice. `query` is the literal gate — what you see is what
    gets copied. */
export interface MarketType {
  label: string;
  query: string;
  /** Why this query and not a shorter one — shown as the chip's tooltip. */
  hint: string;
}

// Bare tickers are deliberately absent from these. Matching is SUBSTRING, so
// "eth" also matches "Whether the Fed cuts…" and "sol" matches "solar
// eclipse" — a gate that wide would have the engine copying trades the search
// never showed you. `btc` is safe (no English word contains it) and earns its
// place next to `bitcoin`.
export const MARKET_TYPES: MarketType[] = [
  {
    label: "BITCOIN",
    query: "bitcoin, btc",
    hint: "Both spellings — the dated price markets and the 5-minute Up/Down candles",
  },
  {
    label: "ETHEREUM",
    query: "ethereum",
    hint: 'Full name only — "eth" is a substring of "whether"',
  },
  { label: "SOLANA", query: "solana", hint: 'Full name only — "sol" is a substring of "solar"' },
  {
    label: "CRYPTO",
    query: "bitcoin, btc, ethereum, solana, crypto, xrp, dogecoin",
    hint: "The whole coin book, not one asset",
  },
  {
    label: "ELECTIONS",
    query: "election, president, senate, governor, primary",
    hint: "Electoral markets — excludes general political news markets",
  },
  {
    label: "MACRO",
    query: "fed, interest rate, inflation, cpi, recession, jobs report",
    hint: "Rates, prices and the data releases that move them",
  },
  {
    label: "SPORTS",
    query: "nba, nfl, mlb, nhl, soccer, ufc, tennis, premier league",
    hint: "League by league — the leagues are what titles actually say",
  },
  {
    label: "AI",
    query: "openai, gpt, claude, anthropic, gemini, llm",
    hint: 'Named models and labs — bare "ai" matches "said", "again", "trail"',
  },
  {
    label: "GEOPOLITICS",
    query: "ukraine, russia, israel, gaza, china, ceasefire",
    hint: "Conflict and diplomacy markets",
  },
];

/** The preset whose query this is, if any — so a query typed by hand that
    happens to equal a preset still lights the chip. */
export function matchPreset(query: string): MarketType | undefined {
  const q = query.trim().toLowerCase();
  return MARKET_TYPES.find((m) => m.query.toLowerCase() === q);
}

/** Short human name for a gate: the preset's label when it is one, the query
    itself otherwise. Used wherever a row has to say what it copies. */
export function describeMarketQuery(query: string | undefined | null): string {
  const q = (query ?? "").trim();
  if (!q) return "all markets";
  return matchPreset(q)?.label ?? q;
}
