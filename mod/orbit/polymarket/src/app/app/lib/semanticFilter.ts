// SEMANTIC TRADE FILTER — one sentence, typed by a person, turned into a gate.
//
// The console already had two filters and neither one is what a person asks
// for out loud:
//
//   lib/marketQuery.ts   matches a market TITLE against literal tokens. "crypto"
//                        finds a market only if the word "crypto" is in its
//                        name, which almost none of them are — the flow is
//                        "Bitcoin above $110,000", "ETH up or down", "Will SOL…".
//   lib/tradeFilters.ts  gates one trade on its own attributes (side, price
//                        band, notional band, category) — but only as numbers
//                        somebody typed into four separate boxes.
//
// What a person actually says is "big buys on crypto under 30 cents, last 3
// days, not the 5-minute candles". This file parses exactly that: one line of
// free text becomes a structured query with a topic (expanded through a concept
// lexicon, so "crypto" reaches "btc" and "ethereum" and "solana"), a side, a
// price band, a notional band, a time window, an outcome leg, a leader, and a
// copied/missed status — plus a CHIP for each clause so the screen can show
// what it understood, and `unknown` for the words it didn't.
//
// Two properties are load-bearing:
//
//   • PURE. No React, no network, no clock beyond the `now` you pass in. The
//     panel, the trades board and lib/__test__.ts all call the same functions.
//   • It COMPILES BACK to the gate the live engine already enforces.
//     `compileGate()` emits a `marketQuery` string in the exact OR-group /
//     AND-token dialect of lib/marketQuery.ts (and its Rust mirror
//     `market_matches_query`), plus a `TradeFilters` for the attribute half.
//     So a sentence you filtered your history with can be armed as the rule a
//     copy session runs under, without inventing a second matcher the engine
//     doesn't know. Whatever CANNOT be expressed that way (a time window, an
//     exclusion, "only the ones I missed") comes back in `viewOnly` and the UI
//     says so out loud rather than pretending the gate covers it.

import type { TradeFilters } from "./types";

/** The minimum a row needs to be filtered. Both halves of the copy trades
    board (my fills and the leaders' trades) satisfy it — see lib/copyTrades.ts. */
export interface SemanticTrade {
  /** Market title — what the topic half matches against. */
  market: string;
  side: "BUY" | "SELL";
  /** Leader fill price, 0–1. */
  price: number;
  size: number;
  /** USD moved. Falls back to price × size when absent. */
  notional?: number;
  /** ms epoch. */
  timestamp: number;
  outcome?: string | null;
  /** Leader address — set on a leader's trade, and on one of mine once it has
      been attributed back to the trade it mirrored. */
  leader?: string | null;
  leaderLabel?: string | null;
  /** Leader rows: did we mirror it. Undefined ⇒ unknown, never "no". */
  copied?: boolean;
  /** True on my own fills. */
  mine?: boolean;
}

/** One expanded word of the topic. `word` is what the user typed; `terms` is
    what a title may contain to satisfy it. */
export interface TopicUnit {
  word: string;
  terms: string[];
  /** Set when the word was a known concept rather than a literal. */
  concept?: string;
}

/** A clause the parser understood, for the "here's what I read" row. */
export interface Clause {
  kind:
    | "topic" | "exclude" | "side" | "price" | "notional"
    | "time" | "outcome" | "leader" | "status";
  /** Short label for the chip. */
  label: string;
  /** Longer text for its tooltip. */
  detail: string;
  /** False ⇒ this clause filters the SCREEN but cannot be armed as a live
      copy gate (the engine has no such knob). */
  enforceable: boolean;
}

export interface SemanticQuery {
  raw: string;
  /** OR across groups, AND across the units inside a group — same shape as a
      comma-separated `marketQuery`, because that is what it compiles to. */
  groups: TopicUnit[][];
  /** Titles hitting any of these are dropped ("not sports", "-candles"). */
  exclude: TopicUnit[];
  sides?: "buy" | "sell";
  minPrice?: number;
  maxPrice?: number;
  minNotional?: number;
  maxNotional?: number;
  /** Rolling window in ms, measured back from `now`. */
  windowMs?: number;
  outcome?: "yes" | "no";
  /** Address fragment or label fragment the row's leader must contain. */
  who?: string;
  status?: "copied" | "missed" | "mine";
  chips: Clause[];
  /** Words that were used as literal topic terms because nothing else claimed
      them. Shown dimmed — a typo shouldn't look like a filter that works. */
  literals: string[];
  /** Nothing was asked for ⇒ everything passes. */
  empty: boolean;
}

// ── The concept lexicon ────────────────────────────────────────────────────
//
// The whole reason "crypto" works. Keys are what a person types; values are
// the terms that actually appear in Polymarket titles. Superset of the
// CATEGORY_KEYWORDS buckets in lib/polymarket.ts (which back the leaderboard's
// category pills) — that list is tuned for bucketing a trader's whole history,
// this one for matching ONE title, so it carries the tickers and the market
// families the desk trades most.

const CONCEPTS: Record<string, string[]> = {
  crypto: [
    "bitcoin", "btc", "ethereum", "eth", "solana", "sol", "xrp", "ripple",
    "dogecoin", "doge", "crypto", "coin", "token", "altcoin", "memecoin",
    "defi", "nft", "bnb", "cardano", "ada", "chainlink", "link", "avax",
    "stablecoin", "usdt", "binance", "coinbase",
  ],
  bitcoin: ["bitcoin", "btc"],
  ethereum: ["ethereum", "eth", "ether"],
  solana: ["solana", "sol"],
  memecoin: ["doge", "dogecoin", "shib", "pepe", "bonk", "wif", "memecoin"],
  // The module's densest family: recurring Up/Down candles. Worth its own
  // concept because "no candles" is the single most useful exclusion here
  // (see the late-fill measurements in the README).
  candles: ["up or down", "updown", "up/down", "5m", "15m", "hourly", "1h candle"],
  politics: [
    "election", "president", "presidential", "senate", "congress", "governor",
    "primary", "vote", "voter", "ballot", "democrat", "republican", "gop",
    "trump", "biden", "harris", "mamdani", "cabinet", "impeach", "nominee",
    "parliament", "prime minister", "chancellor", "referendum", "midterm",
  ],
  election: ["election", "primary", "ballot", "vote", "poll", "nominee", "seat"],
  geopolitics: [
    "ukraine", "russia", "putin", "zelensky", "israel", "gaza", "hamas",
    "iran", "china", "taiwan", "north korea", "war", "ceasefire", "invasion",
    "nato", "sanctions", "peace deal", "hostage",
  ],
  war: ["war", "invasion", "ceasefire", "strike", "military", "troops"],
  sports: [
    "nba", "nfl", "mlb", "nhl", "ncaa", "soccer", "football", "basketball",
    "baseball", "hockey", "tennis", "golf", "ufc", "boxing", "f1", "formula 1",
    "grand prix", "premier league", "la liga", "champions league", "world cup",
    "super bowl", "playoffs", "championship", "match", "vs", "vs.", "beat the",
    "olympics", "medal",
  ],
  nba: ["nba", "lakers", "celtics", "warriors", "knicks", "playoffs"],
  nfl: ["nfl", "super bowl", "quarterback", "touchdown"],
  soccer: ["soccer", "premier league", "la liga", "champions league", "fifa", "world cup", "uefa"],
  macro: [
    "fed", "fomc", "interest rate", "rate cut", "rate hike", "cpi", "inflation",
    "recession", "gdp", "unemployment", "jobs report", "powell", "treasury",
    "yield", "tariff", "debt ceiling",
  ],
  fed: ["fed", "fomc", "powell", "rate cut", "rate hike", "interest rate"],
  inflation: ["inflation", "cpi", "pce", "price index"],
  stocks: [
    "stock", "s&p", "nasdaq", "dow", "ipo", "earnings", "revenue", "market cap",
    "share price", "tesla", "nvidia", "apple", "shares",
  ],
  business: ["company", "ceo", "acquisition", "merger", "bankruptcy", "layoffs", "ipo", "earnings"],
  ai: [
    "ai", "gpt", "openai", "anthropic", "claude", "gemini", "llm", "model",
    "artificial intelligence", "chatgpt", "grok", "deepseek", "agi",
  ],
  tech: ["apple", "google", "meta", "microsoft", "openai", "tesla", "spacex", "launch", "release date", "iphone"],
  space: ["nasa", "spacex", "starship", "rocket", "launch", "moon", "mars", "asteroid", "satellite"],
  science: ["nasa", "climate", "temperature", "earthquake", "hurricane", "vaccine", "disease", "study"],
  weather: ["hurricane", "temperature", "snow", "rain", "storm", "heat", "climate", "el niño"],
  culture: [
    "movie", "box office", "album", "oscar", "grammy", "emmy", "golden globe",
    "celebrity", "taylor swift", "drake", "netflix", "rotten tomatoes", "tv show",
  ],
  awards: ["oscar", "grammy", "emmy", "golden globe", "nobel", "person of the year"],
  health: ["fda", "vaccine", "outbreak", "covid", "flu", "measles", "cdc", "drug approval"],
};

/** Concepts that CONTAIN other concepts. "sports" has to reach the team names
    living under "nba", or a title like "Lakers vs Celtics" — which never says
    the word sport — misses the filter that was obviously about it. Expanded
    one level deep, which is all the lexicon is. */
const INCLUDES: Record<string, string[]> = {
  crypto: ["bitcoin", "ethereum", "solana", "memecoin", "candles"],
  sports: ["nba", "nfl", "soccer"],
  politics: ["election"],
  macro: ["fed", "inflation"],
  tech: ["ai", "space"],
  geopolitics: ["war"],
  culture: ["awards"],
};

/** Concept aliases — a word that means an existing concept. */
const ALIASES: Record<string, string> = {
  cryptocurrency: "crypto", coins: "crypto", tokens: "crypto",
  btc: "bitcoin", eth: "ethereum", sol: "solana",
  memecoins: "memecoin", memes: "memecoin",
  candle: "candles", updown: "candles", scalps: "candles", scalping: "candles",
  political: "politics", elections: "election", voting: "election",
  geopolitical: "geopolitics", wars: "war", conflict: "war",
  sport: "sports", games: "sports", game: "sports",
  economy: "macro", macroeconomics: "macro", rates: "fed", "interest": "fed",
  equities: "stocks", stock: "stocks", markets: "stocks",
  artificial: "ai", llms: "ai", models: "ai",
  technology: "tech", startups: "tech",
  movies: "culture", entertainment: "culture", music: "culture", film: "culture",
  award: "awards", oscars: "awards", grammys: "awards",
  medicine: "health", pandemic: "health",
  climate: "weather", storms: "weather",
};

/** Words that carry no topic meaning on their own. Superset of
    marketQuery.ts's list — this parser also eats the connective tissue of a
    spoken sentence ("show me the trades where…"). */
const STOPWORDS = new Set([
  "of", "the", "a", "an", "to", "in", "on", "for", "and", "or", "at", "by",
  "is", "be", "will", "this", "that", "with", "vs", "show", "me", "my",
  "all", "any", "trades", "trade", "only", "just", "where", "which", "was",
  "were", "are", "i", "it", "some", "them", "their", "when", "what", "who",
  "did", "do", "made", "make", "over", "under", "from", "last", "past",
  "days", "day", "hours", "hour", "minutes", "cents", "cent", "than",
]);

/** Expand one typed word into the terms a title may contain. */
export function expandConcept(word: string): TopicUnit {
  const w = word.toLowerCase().trim();
  const key = CONCEPTS[w] ? w : ALIASES[w] && CONCEPTS[ALIASES[w]] ? ALIASES[w] : null;
  if (key) {
    const nested = (INCLUDES[key] ?? []).flatMap((child) => CONCEPTS[child] ?? []);
    return { word: w, terms: dedupe([w, ...CONCEPTS[key], ...nested]), concept: key };
  }
  // Not a concept: the word itself, plus its singular, so "elections" still
  // reaches "election" without a lexicon entry for every plural.
  const terms = [w];
  if (w.length > 4 && w.endsWith("s") && !w.endsWith("ss")) terms.push(w.slice(0, -1));
  return { word: w, terms };
}

function dedupe(xs: string[]): string[] {
  return Array.from(new Set(xs.map((x) => x.toLowerCase())));
}

// ── Number helpers ─────────────────────────────────────────────────────────

/** "1.5k" → 1500, "2m" → 2000000, "1,200" → 1200. */
function money(raw: string, suffix?: string): number {
  const n = Number(raw.replace(/,/g, ""));
  if (!Number.isFinite(n)) return NaN;
  const s = (suffix ?? "").toLowerCase();
  return s === "k" ? n * 1000 : s === "m" ? n * 1_000_000 : n;
}

/** A price the user typed, normalized to 0–1. "30c" / "30 cents" / "30%" → 0.3;
    "0.3" → 0.3. Anything above 1 without a unit is treated as cents, because
    nobody means a probability of 30. */
function price(raw: string): number {
  const n = Number(raw);
  if (!Number.isFinite(n)) return NaN;
  return n > 1 ? n / 100 : n;
}

const CENT_UNIT = String.raw`(?:¢|c\b|cents?\b|%)`;
const DAY_MS = 86_400_000;

// ── The parser ─────────────────────────────────────────────────────────────

/** Parse one line of free text. Clauses are consumed left to right; whatever
    survives is the topic. Never throws — an unparseable query degrades to a
    literal title search, which is the behaviour of the plain box it replaces. */
export function parseSemanticQuery(raw: string): SemanticQuery {
  const chips: Clause[] = [];
  const q: SemanticQuery = {
    raw, groups: [], exclude: [], chips, literals: [], empty: true,
  };
  let text = ` ${(raw || "").toLowerCase().replace(/\s+/g, " ")} `;
  if (!raw || !raw.trim()) return q;

  /** Match, record a chip, and remove the matched span from the topic text. */
  const eat = (re: RegExp, fn: (m: RegExpExecArray) => Clause | null) => {
    text = text.replace(re, (...args) => {
      const m = args.slice(0, -2) as unknown as RegExpExecArray;
      const clause = fn(m);
      if (clause) chips.push(clause);
      return " ";
    });
  };

  // ── Outcome leg. FIRST, so "no side" isn't read as the exclusion "not". ──
  eat(/\b(yes|no)[- ](?:side|leg|outcome|shares?|tokens?)\b/g, (m) => {
    q.outcome = m[1] as "yes" | "no";
    return { kind: "outcome", label: `${m[1].toUpperCase()} leg`, enforceable: false,
      detail: `Only fills on the ${m[1].toUpperCase()} outcome token. View-only — the engine mirrors whichever leg the leader took.` };
  });

  // ── Status: what the row IS, not what the market is about. ──
  eat(/\b(copied|mirrored)\b/g, () => {
    q.status = "copied";
    return { kind: "status", label: "COPIED", enforceable: false,
      detail: "Leader trades this desk actually mirrored, and my fills that mirror one." };
  });
  eat(/\b(missed|skipped|not copied|uncopied|filtered out|rejected)\b/g, () => {
    q.status = "missed";
    return { kind: "status", label: "MISSED", enforceable: false,
      detail: "Leader trades with no fill of mine behind them — what the gates, the budget or the latency refused." };
  });
  eat(/\b(?:my own|mine|my fills)\b/g, () => {
    q.status = "mine";
    return { kind: "status", label: "MINE", enforceable: false,
      detail: "Only my own on-chain fills." };
  });

  // ── Who. ──
  eat(/\b(?:from|by|leader|following)\s+(0x[0-9a-f]{4,}|[a-z0-9][a-z0-9_.-]{2,})/g, (m) => {
    q.who = m[1];
    return { kind: "leader", label: `FROM ${m[1].slice(0, 12)}`, enforceable: false,
      detail: `Only rows whose leader's address or label contains "${m[1]}". Per-leader gating is what the copy book's rows already are.` };
  });

  // ── Side. ──
  eat(/\b(buys?|bought|buying|entries|entry|enter(?:ed|s)?|longs?|adds?)\b/g, () => {
    q.sides = "buy";
    return { kind: "side", label: "BUY", enforceable: true, detail: "Entries only — no exits." };
  });
  eat(/\b(sells?|sold|selling|exits?|exiting|closed?|closes)\b/g, () => {
    q.sides = "sell";
    return { kind: "side", label: "SELL", enforceable: true, detail: "Exits only — no entries." };
  });

  // ── Price band. Cent-denominated forms first (they carry a unit). ──
  eat(new RegExp(String.raw`\bbetween\s+(\d+(?:\.\d+)?)\s*(?:${CENT_UNIT})?\s+and\s+(\d+(?:\.\d+)?)\s*${CENT_UNIT}`, "g"), (m) => {
    const [lo, hi] = [price(m[1]), price(m[2])].sort((a, b) => a - b);
    q.minPrice = lo; q.maxPrice = hi;
    return { kind: "price", label: `${cents(lo)}–${cents(hi)}`, enforceable: true,
      detail: `Leader's fill price between ${cents(lo)} and ${cents(hi)}.` };
  });
  eat(new RegExp(String.raw`\b(?:under|below|less than|cheaper than|<)\s*\$?(\d+(?:\.\d+)?)\s*${CENT_UNIT}`, "g"), (m) => {
    q.maxPrice = price(m[1]);
    return { kind: "price", label: `≤ ${cents(q.maxPrice!)}`, enforceable: true,
      detail: `Leader paid no more than ${cents(q.maxPrice!)}.` };
  });
  eat(new RegExp(String.raw`\b(?:over|above|more than|at least|richer than|>)\s*\$?(\d+(?:\.\d+)?)\s*${CENT_UNIT}`, "g"), (m) => {
    q.minPrice = price(m[1]);
    return { kind: "price", label: `≥ ${cents(q.minPrice!)}`, enforceable: true,
      detail: `Leader paid at least ${cents(q.minPrice!)}.` };
  });

  // ── Notional band. Dollar amounts with no cent unit. ──
  eat(/\bbetween\s+\$(\d[\d,]*(?:\.\d+)?)\s*([km])?\s+and\s+\$?(\d[\d,]*(?:\.\d+)?)\s*([km])?/g, (m) => {
    const [lo, hi] = [money(m[1], m[2]), money(m[3], m[4])].sort((a, b) => a - b);
    q.minNotional = lo; q.maxNotional = hi;
    return { kind: "notional", label: `$${short(lo)}–$${short(hi)}`, enforceable: true,
      detail: `Trades worth between $${short(lo)} and $${short(hi)}.` };
  });
  eat(/\b(?:over|above|more than|at least|bigger than|larger than|>)\s*\$(\d[\d,]*(?:\.\d+)?)\s*([km])?/g, (m) => {
    q.minNotional = money(m[1], m[2]);
    return { kind: "notional", label: `≥ $${short(q.minNotional!)}`, enforceable: true,
      detail: `The leader put at least $${short(q.minNotional!)} behind it.` };
  });
  eat(/\b(?:under|below|less than|smaller than|<)\s*\$(\d[\d,]*(?:\.\d+)?)\s*([km])?/g, (m) => {
    q.maxNotional = money(m[1], m[2]);
    return { kind: "notional", label: `≤ $${short(q.maxNotional!)}`, enforceable: true,
      detail: `No more than $${short(q.maxNotional!)} behind it.` };
  });

  // ── Named bands. The vocabulary people actually use. ──
  eat(/\b(longshots?|lottery tickets?)\b/g, () => {
    q.maxPrice = Math.min(q.maxPrice ?? 1, 0.15);
    return { kind: "price", label: "LONGSHOTS", enforceable: true, detail: "Fills at 15¢ or less." };
  });
  eat(/\b(favou?rites?|near ?certain(?:ties)?)\b/g, () => {
    q.minPrice = Math.max(q.minPrice ?? 0, 0.8);
    return { kind: "price", label: "FAVORITES", enforceable: true, detail: "Fills at 80¢ or more." };
  });
  eat(/\b(coin ?flips?|toss[- ]?ups?|50\/50|even money)\b/g, () => {
    q.minPrice = Math.max(q.minPrice ?? 0, 0.4);
    q.maxPrice = Math.min(q.maxPrice ?? 1, 0.6);
    return { kind: "price", label: "40–60¢", enforceable: true,
      detail: "The coin-flip band. Measured on this desk as the one price band where copying loses money on its own merits, not on friction." };
  });
  eat(/\b(cheap|low ?priced)\b/g, () => {
    q.maxPrice = Math.min(q.maxPrice ?? 1, 0.25);
    return { kind: "price", label: "≤ 25¢", enforceable: true, detail: "Cheap: 25¢ or less." };
  });
  eat(/\b(whales?|size|chunky|big|large|heavy)\b/g, () => {
    q.minNotional = Math.max(q.minNotional ?? 0, 500);
    return { kind: "notional", label: "≥ $500", enforceable: true, detail: "Size: $500 or more behind the trade." };
  });
  eat(/\b(dust|tiny|small|scraps?)\b/g, () => {
    q.maxNotional = Math.min(q.maxNotional ?? Infinity, 25);
    return { kind: "notional", label: "≤ $25", enforceable: true, detail: "Dust: $25 or less." };
  });

  // ── Time window. View-only: the engine copies what is happening NOW. ──
  eat(/\b(?:last|past|previous)\s+(\d+)\s*(m|min|mins|minutes?|h|hr|hrs|hours?|d|days?|w|weeks?)\b/g, (m) => {
    const n = Number(m[1]);
    const unit = m[2][0];
    const ms = unit === "m" ? n * 60_000
      : unit === "h" ? n * 3_600_000
      : unit === "w" ? n * 7 * DAY_MS
      : n * DAY_MS;
    q.windowMs = ms;
    return { kind: "time", label: `LAST ${n}${unit.toUpperCase()}`, enforceable: false,
      detail: "A window over the history on screen. Not part of a live gate — the engine only ever sees new trades." };
  });
  eat(/\b(today|last 24 ?h(?:ours)?|24h)\b/g, () => {
    q.windowMs = DAY_MS;
    return { kind: "time", label: "24H", enforceable: false, detail: "The last 24 hours." };
  });
  eat(/\b(this week|last week|7 ?d)\b/g, () => {
    q.windowMs = 7 * DAY_MS;
    return { kind: "time", label: "7D", enforceable: false, detail: "The last 7 days." };
  });
  eat(/\b(last hour|past hour|1 ?h)\b/g, () => {
    q.windowMs = 3_600_000;
    return { kind: "time", label: "1H", enforceable: false, detail: "The last hour." };
  });
  eat(/\b(just now|right now|recent(?:ly)?|fresh)\b/g, () => {
    q.windowMs = 6 * 3_600_000;
    return { kind: "time", label: "6H", enforceable: false, detail: "The last six hours." };
  });

  // ── Exclusions. "not sports", "no candles", "-politics", "excluding x". ──
  eat(/(?:\bnot\b|\bno\b|\bexcluding\b|\bexcept\b|\bwithout\b|\s-)\s*([a-z][a-z0-9]{2,})/g, (m) => {
    if (STOPWORDS.has(m[1])) return null;
    const unit = expandConcept(m[1]);
    q.exclude.push(unit);
    return { kind: "exclude", label: `NOT ${m[1].toUpperCase()}`, enforceable: false,
      detail: `Drops titles matching ${unit.terms.slice(0, 6).join(", ")}${unit.terms.length > 6 ? "…" : ""}. A live gate can only say what to copy, never what to skip — so this one filters the screen.` };
  });

  // ── Whatever is left is the topic. Commas/pipes are OR, spaces are AND —
  //    the same dialect as lib/marketQuery.ts, so the compile below is exact.
  for (const rawGroup of text.split(/[,|]/)) {
    const words = rawGroup
      .split(/[^a-z0-9$]+/)
      .map((w) => w.trim())
      .filter((w) => w.length > 1 && !STOPWORDS.has(w) && !/^\d+$/.test(w));
    if (words.length === 0) continue;
    const units = words.map(expandConcept);
    q.groups.push(units);
    for (const u of units) if (!u.concept) q.literals.push(u.word);
  }
  for (const group of q.groups) {
    const label = group.map((u) => (u.concept ?? u.word).toUpperCase()).join(" + ");
    chips.push({
      kind: "topic", label, enforceable: true,
      detail: group
        .map((u) => `${u.word} → ${u.terms.slice(0, 8).join(", ")}${u.terms.length > 8 ? `, +${u.terms.length - 8} more` : ""}`)
        .join("  ·  "),
    });
  }

  q.empty =
    q.groups.length === 0 && q.exclude.length === 0 && !q.sides && !q.status &&
    q.minPrice === undefined && q.maxPrice === undefined &&
    q.minNotional === undefined && q.maxNotional === undefined &&
    q.windowMs === undefined && !q.outcome && !q.who;
  return q;
}

function cents(p: number): string {
  return `${Math.round(p * 100)}¢`;
}

function short(n: number): string {
  if (!Number.isFinite(n)) return "∞";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n % 1_000_000 ? 1 : 0)}m`;
  if (n >= 1000) return `${(n / 1000).toFixed(n % 1000 ? 1 : 0)}k`;
  return String(Math.round(n));
}

// ── Matching ───────────────────────────────────────────────────────────────

/** Does `title` contain `term`? Word-boundary aware so "sol" doesn't match
    "solution" — but multi-word terms ("up or down") match as a phrase. */
function titleHas(title: string, term: string): boolean {
  const i = title.indexOf(term);
  if (i < 0) return false;
  const before = i === 0 ? " " : title[i - 1];
  const after = i + term.length >= title.length ? " " : title[i + term.length];
  return !/[a-z0-9]/.test(before) && !/[a-z0-9]/.test(after);
}

/** Which of a unit's terms the title hit ([] ⇒ no match). */
function unitHits(title: string, unit: TopicUnit): string[] {
  return unit.terms.filter((t) => titleHas(title, t));
}

export interface MatchResult {
  pass: boolean;
  /** Why it failed — one short phrase, for the "0 of 412" explainer. */
  reason?: string;
  /** Terms the title hit, for the row's tooltip. */
  hits: string[];
  /** Relevance, for "best match first" ordering. 0 when there is no topic. */
  score: number;
}

/** Apply a parsed query to one trade. `now` defaults to wall clock and is a
    parameter so tests (and a server render) are deterministic. */
export function semanticMatch(
  trade: SemanticTrade,
  q: SemanticQuery,
  now: number = Date.now(),
): MatchResult {
  if (q.empty) return { pass: true, hits: [], score: 0 };
  const title = (trade.market || "").toLowerCase();
  const notional = trade.notional ?? trade.price * trade.size;

  if (q.sides && trade.side !== (q.sides === "buy" ? "BUY" : "SELL")) {
    return { pass: false, reason: `not a ${q.sides}`, hits: [], score: 0 };
  }
  if (q.minPrice !== undefined && trade.price < q.minPrice) {
    return { pass: false, reason: `below ${cents(q.minPrice)}`, hits: [], score: 0 };
  }
  if (q.maxPrice !== undefined && trade.price > q.maxPrice) {
    return { pass: false, reason: `above ${cents(q.maxPrice)}`, hits: [], score: 0 };
  }
  if (q.minNotional !== undefined && notional < q.minNotional) {
    return { pass: false, reason: `under $${short(q.minNotional)}`, hits: [], score: 0 };
  }
  if (q.maxNotional !== undefined && notional > q.maxNotional) {
    return { pass: false, reason: `over $${short(q.maxNotional)}`, hits: [], score: 0 };
  }
  if (q.windowMs !== undefined && trade.timestamp < now - q.windowMs) {
    return { pass: false, reason: "outside the window", hits: [], score: 0 };
  }
  if (q.outcome && (trade.outcome ?? "").toLowerCase() !== q.outcome) {
    return { pass: false, reason: `not the ${q.outcome} leg`, hits: [], score: 0 };
  }
  if (q.who) {
    const who = `${trade.leader ?? ""} ${trade.leaderLabel ?? ""}`.toLowerCase();
    if (!who.includes(q.who)) return { pass: false, reason: "another leader", hits: [], score: 0 };
  }
  if (q.status === "mine" && !trade.mine) {
    return { pass: false, reason: "not one of mine", hits: [], score: 0 };
  }
  if (q.status === "copied" && !(trade.copied || (trade.mine && trade.leader))) {
    return { pass: false, reason: "never copied", hits: [], score: 0 };
  }
  if (q.status === "missed" && (trade.copied || trade.mine)) {
    return { pass: false, reason: "this one was copied", hits: [], score: 0 };
  }

  for (const unit of q.exclude) {
    const hit = unitHits(title, unit);
    if (hit.length) return { pass: false, reason: `excluded (${hit[0]})`, hits: [], score: 0 };
  }

  if (q.groups.length === 0) return { pass: true, hits: [], score: 0 };

  // OR across groups, AND within one. Score = how many terms the winning
  // group hit, so an exact-word title outranks an incidental mention.
  let best: { hits: string[]; score: number } | null = null;
  for (const group of q.groups) {
    const hits: string[] = [];
    let ok = true;
    for (const unit of group) {
      const hit = unitHits(title, unit);
      if (hit.length === 0) { ok = false; break; }
      hits.push(...hit);
      // A literal the user typed is worth more than a synonym reached for.
      // (`unit.word` first in `terms` — see expandConcept.)
    }
    if (!ok) continue;
    const score = hits.reduce((s, h) => s + (group.some((u) => u.word === h) ? 1.5 : 1), 0);
    if (!best || score > best.score) best = { hits: dedupe(hits), score };
  }
  if (!best) {
    const asked = q.groups.map((g) => g.map((u) => u.concept ?? u.word).join("+")).join(" or ");
    return { pass: false, reason: `not about ${asked}`, hits: [], score: 0 };
  }
  return { pass: true, hits: best.hits, score: best.score };
}

/** Filter + rank in one pass. Rows keep their time order unless a topic was
    asked for, in which case the strongest matches come first — a search says
    "these are the ones", a feed says "this is what happened". */
export function applySemanticQuery<T extends SemanticTrade>(
  rows: T[],
  q: SemanticQuery,
  opts: { now?: number; rank?: boolean } = {},
): { rows: (T & { _hits?: string[]; _score?: number })[]; dropped: number; reasons: Record<string, number> } {
  const now = opts.now ?? Date.now();
  const reasons: Record<string, number> = {};
  const kept: (T & { _hits?: string[]; _score?: number })[] = [];
  for (const r of rows) {
    const m = semanticMatch(r, q, now);
    if (m.pass) kept.push(m.hits.length ? { ...r, _hits: m.hits, _score: m.score } : r);
    else if (m.reason) reasons[m.reason] = (reasons[m.reason] ?? 0) + 1;
  }
  if (opts.rank && q.groups.length > 0) {
    kept.sort((a, b) => (b._score ?? 0) - (a._score ?? 0) || b.timestamp - a.timestamp);
  }
  return { rows: kept, dropped: rows.length - kept.length, reasons };
}

// ── Compiling back to a live gate ──────────────────────────────────────────

export interface CompiledGate {
  /** The `marketQuery` dialect lib/marketQuery.ts and its Rust mirror parse:
      comma-separated OR groups, space-separated AND tokens. */
  marketQuery: string;
  tradeFilters: TradeFilters;
  /** Clauses that filter the screen but cannot be armed. Shown, never hidden. */
  viewOnly: string[];
  /** True ⇒ there is something worth arming. */
  any: boolean;
}

/** How many OR groups a compiled query may carry. One concept expands to
    dozens of terms and two concepts AND-ed together are their product, so
    there has to be a ceiling; the engine matches each group with a substring
    test, so the ceiling is about the readability of the string, not its cost.
    Big enough that a single concept ("crypto", 39 terms) compiles WHOLE — the
    expansion is the entire point, and a `marketQuery` of the literal word
    "crypto" would match almost no title. */
const MAX_GROUPS = 64;

/** Turn a parsed query into the gate the copy engine already enforces.
    Everything it can't express comes back in `viewOnly`. */
export function compileGate(q: SemanticQuery): CompiledGate {
  const viewOnly = q.chips.filter((c) => !c.enforceable).map((c) => c.label);

  // Cross-product the expanded terms of each AND unit inside each OR group.
  // Two AND-ed concepts are a product, so the terms per unit are TRIMMED to
  // fit rather than the expansion being abandoned — a narrower gate still
  // reaches the markets the sentence was about, where the bare literal word
  // ("crypto") reaches none of them. The user's own word is always first in
  // `terms` (see expandConcept), so trimming keeps what they actually typed.
  const budget = Math.max(
    1,
    Math.floor(MAX_GROUPS / Math.max(1, q.groups.length)),
  );
  const groups: string[] = [];
  for (const group of q.groups) {
    const perUnit = Math.max(1, Math.floor(Math.pow(budget, 1 / group.length)));
    let combos: string[] = [""];
    for (const unit of group) {
      const terms = unit.terms.slice(0, group.length === 1 ? budget : perUnit);
      const next: string[] = [];
      for (const c of combos) for (const t of terms) next.push(c ? `${c} ${t}` : t);
      combos = next;
    }
    groups.push(...combos);
  }

  const tradeFilters: TradeFilters = {};
  if (q.sides) tradeFilters.sides = q.sides;
  if (q.minPrice !== undefined) tradeFilters.minPrice = q.minPrice;
  if (q.maxPrice !== undefined) tradeFilters.maxPrice = q.maxPrice;
  if (q.minNotional !== undefined) tradeFilters.minNotional = q.minNotional;
  if (q.maxNotional !== undefined && Number.isFinite(q.maxNotional)) {
    tradeFilters.maxNotional = q.maxNotional;
  }

  const marketQuery = dedupe(groups).join(", ");
  return {
    marketQuery,
    tradeFilters,
    viewOnly,
    any: marketQuery.length > 0 || Object.keys(tradeFilters).length > 0,
  };
}

/** One line describing what a compiled gate will do, for the confirm. */
export function describeGate(gate: CompiledGate): string {
  const parts: string[] = [];
  if (gate.marketQuery) {
    const n = gate.marketQuery.split(",").length;
    parts.push(`markets matching ${n} pattern${n === 1 ? "" : "s"}`);
  }
  const f = gate.tradeFilters;
  if (f.sides) parts.push(`${f.sides.toUpperCase()} only`);
  if (f.minPrice !== undefined || f.maxPrice !== undefined) {
    parts.push(`${cents(f.minPrice ?? 0)}–${cents(f.maxPrice ?? 1)}`);
  }
  if (f.minNotional !== undefined || f.maxNotional !== undefined) {
    parts.push(`$${short(f.minNotional ?? 0)}–$${short(f.maxNotional ?? Infinity)}`);
  }
  return parts.length ? parts.join(" · ") : "no gate";
}

/** Suggestions for the empty box. Each is a real, parseable query. */
export const SEMANTIC_EXAMPLES = [
  "big buys on crypto under 30¢",
  "politics, not candles",
  "sells above 80¢ last 3 days",
  "missed longshots",
  "sports coin flips over $200",
  "ai and elections this week",
] as const;
