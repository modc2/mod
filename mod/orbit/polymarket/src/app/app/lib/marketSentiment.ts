// MARKET SENTIMENT — the third gate.
//
// `marketQuery` picks WHICH MARKETS a strat copies in. `tradeFilters` picks
// WHICH TRADES inside them, from the trade's own attributes. Neither one can
// see the thing a person actually means by "don't copy that": the state of the
// MARKET at the moment the leader took the trade. A $2,000 buy at 34¢ on a
// crypto market is the same row whether the crowd has been walking that
// outcome UP all morning or dumping it since midnight — and those are not the
// same trade.
//
// This file is that missing dimension, and it is deliberately the narrowest
// honest definition of "sentiment" available from data Polymarket actually
// publishes:
//
//     sentiment = which way the crowd has moved the price of the exact
//                 outcome token the leader traded, over the last N hours.
//
//     drift = p(at the trade) − p(N hours before the trade)      [prob points]
//
// Measuring it on the LEADER'S OWN TOKEN is what makes the reading directional
// without a second convention: a positive drift always means the crowd has
// been moving TOWARD what they bought, whatever "Yes"/"No"/team the token is.
// So:
//
//   BULLISH   the odds on their outcome were rising into their entry — they
//             are buying strength, with the crowd, paying up.
//   BEARISH   the odds were falling — they are buying the dip, fading the
//             crowd, catching the knife. Contrarian.
//   FLAT      the market has not moved more than `flatBand` either way.
//
// What this is NOT, said out loud so nobody reads more into a chip than it
// carries: it is not news, not social sentiment, not an order-book imbalance,
// and not a forecast. It is price drift on one token over one window. That is
// a real, checkable number, and every other thing on that list would be a
// number this deployment cannot compute.
//
// UNKNOWN IS NOT BEARISH. A market with no usable history in the window reads
// `unknown`, and the default `unknown: "pass"` lets those trades through. The
// #1 failure mode in this module is a gate nobody chose silently rejecting the
// whole flow (see the missing-price-floor note in lib/tradeFilters.ts); a
// sentiment filter that blocks every market whose history didn't load would be
// exactly that bug with a new name. `unknown: "block"` is available and is
// always an explicit choice.
//
// Pure except for `warmSentiment`, which is the only function here that
// touches the network. Mirror of `sentiment.rs` in the Rust live engine —
// pinned by parity.fixture.json `sentimentCases`.

import { fetchPriceWindow, type PricePoint } from "./polymarket";

/** Which way the crowd has been moving the leader's own outcome token. */
export type SentimentLean = "bullish" | "bearish" | "flat" | "unknown";

/** The gate. Every set dimension is AND-ed with the rest of `TradeFilters`.
    An all-empty filter is a no-op and costs nothing — no history is fetched
    for a strat that isn't gating on sentiment. */
export interface SentimentFilter {
  /** Which market moods to copy in. Empty/undefined ⇒ every mood.
      `["bullish"]` = only trades taken into rising odds (with the crowd);
      `["bearish"]` = only contrarian entries; `["flat"]` = quiet markets. */
  lean?: SentimentLean[];
  /** Signed drift band in PROBABILITY POINTS (0.05 = 5¢ of movement).
      `{minDrift: 0.05}` = the outcome must have gained at least 5¢.
      `{maxDrift: -0.05}` = it must have LOST at least 5¢. Undefined ends mean
      no bound on that end. */
  minDrift?: number;
  maxDrift?: number;
  /** How far back the drift is measured. Default 6h — long enough that a
      single fill doesn't set the mood, short enough to still be "now". */
  windowHours?: number;
  /** Movement under this (probability points) counts as FLAT rather than as a
      weak direction. Default 0.02 (2¢). */
  flatBand?: number;
  /** What to do with a market whose history didn't resolve. Default "pass". */
  unknown?: "pass" | "block";
}

/** One market's reading, as of one instant. */
export interface MarketSentiment {
  /** CLOB outcome-token id the reading is for. */
  tokenId: string;
  /** Mark at the instant asked about (last point at or before it). */
  price: number;
  /** Mark one window earlier — what the drift is measured from. */
  from: number;
  /** `price − from`, in probability points. Signed. */
  drift: number;
  /** `|drift|`. */
  strength: number;
  lean: SentimentLean;
  /** Window the drift covers, in hours. */
  windowHours: number;
  /** Price points that backed the reading. 0 ⇒ nothing was known. */
  points: number;
  /** Why the reading is `unknown`, when it is. */
  note?: string;
}

/** The least a row needs to be given a mood: which outcome token, and when.
 *
 *  Two spellings of the same field, because the codebase has two: the data-api
 *  activity row (and so `PolymarketTrade`) calls the CTF outcome token `asset`,
 *  while the engine's `ObservedTrade` / `OpenPosition` call it `tokenId`. Every
 *  reader here takes whichever is present rather than making 40 call sites
 *  rename a field on the way in. */
export interface SentimentSubject {
  tokenId?: string;
  asset?: string;
  timestamp?: number;
}

/** The outcome token a row is about, whichever name it arrived under. */
export function subjectToken(t: SentimentSubject | undefined | null): string {
  return (t?.tokenId || t?.asset || "").trim();
}

/** Look one up for a trade. Returns undefined when the book has no reading —
    which the gate treats as `unknown`, not as a rejection. */
export type SentimentLookup = (trade: SentimentSubject) => MarketSentiment | undefined;

export const DEFAULT_SENTIMENT_WINDOW_HOURS = 6;
export const DEFAULT_SENTIMENT_FLAT_BAND = 0.02;

/** Price-history requests one browser pass will spend on sentiment. Each token
    is one CLOB call, and this shares an upstream budget with a live engine
    whose fills are time-critical — same restraint momentumTape.ts shows. Trades
    beyond the budget read `unknown` and the coverage number says so. */
export const SENTIMENT_TOKEN_BUDGET = 120;

/** Concurrent price-history requests. */
const CONCURRENCY = 4;

/** How far back ONE price-history request may reach.
 *
 *  Measured against the live CLOB, not guessed: a `startTs`/`endTs` span of 14
 *  days answers, and 30 days comes back
 *  `invalid filters: 'startTs' and 'endTs' interval is too long` — at ANY
 *  fidelity, so coarsening the bars does not buy more range. A sample that
 *  reaches further back than this is fetched from the cap forward, and the
 *  trades that fall off the front read `unknown` (and so, by default, are
 *  copied) rather than the whole request failing and EVERY market reading
 *  unknown. That was the first version of this function and it is why the
 *  card printed "COVERAGE 0%" over a perfectly good bench. */
export const MAX_HISTORY_SPAN_MS = 14 * 86_400_000;

/** True when the filter actually constrains something. An unset filter must
    cost nothing — no fetch, no gate, no chip. */
export function sentimentFilterActive(f: SentimentFilter | undefined | null): boolean {
  if (!f) return false;
  return (
    (Array.isArray(f.lean) && f.lean.length > 0 && f.lean.length < 4) ||
    f.minDrift != null ||
    f.maxDrift != null ||
    f.unknown === "block"
  );
}

/** Normalize the dials, so the gate, the fetcher and the Rust mirror all agree
    on what an omitted knob means. */
export function sentimentWindowHours(f: SentimentFilter | undefined | null): number {
  const h = f?.windowHours;
  return h != null && Number.isFinite(h) && h > 0 ? h : DEFAULT_SENTIMENT_WINDOW_HOURS;
}
export function sentimentFlatBand(f: SentimentFilter | undefined | null): number {
  const b = f?.flatBand;
  return b != null && Number.isFinite(b) && b >= 0 ? b : DEFAULT_SENTIMENT_FLAT_BAND;
}

/** A reading that knows nothing. Carries the reason so a UI can say WHY a
    market has no mood instead of printing a neutral-looking 0¢ drift. */
export function unknownSentiment(tokenId: string, windowHours: number, note: string): MarketSentiment {
  return {
    tokenId,
    price: 0,
    from: 0,
    drift: 0,
    strength: 0,
    lean: "unknown",
    windowHours,
    points: 0,
    note,
  };
}

/** Read the drift out of a price series AS OF `atMs`.
 *
 *  Pure, and the same call serves both halves: live passes `Date.now()`, a
 *  replay passes the leader trade's own timestamp. That is the whole reason
 *  backtest and live can agree about a sentiment gate — the backtest is not
 *  approximating the live reading, it is computing it at a different instant.
 *
 *  `series` must be sorted ascending by `t` (fetchPriceWindow guarantees it). */
export function readSentiment(
  series: PricePoint[] | undefined | null,
  atMs: number,
  tokenId: string,
  windowHours = DEFAULT_SENTIMENT_WINDOW_HOURS,
  flatBand = DEFAULT_SENTIMENT_FLAT_BAND,
): MarketSentiment {
  const pts = Array.isArray(series) ? series : [];
  if (pts.length === 0) return unknownSentiment(tokenId, windowHours, "no price history");

  const windowMs = windowHours * 3_600_000;
  // Latest point at or before the instant. A replay asking about a trade older
  // than the series start gets nothing rather than a future price.
  let nowIdx = -1;
  for (let i = pts.length - 1; i >= 0; i--) {
    if (pts[i].t <= atMs) { nowIdx = i; break; }
  }
  if (nowIdx < 0) {
    return unknownSentiment(tokenId, windowHours, "history starts after this trade");
  }
  const now = pts[nowIdx];

  // The anchor is the last point at or before `atMs − window`. When the series
  // doesn't reach back that far we anchor on its FIRST point instead, but only
  // if that point is at least a third of the window old — anchoring a "6h
  // drift" on a mark from four minutes ago would be a number that reads like
  // six hours of crowd conviction and isn't.
  const target = atMs - windowMs;
  let fromIdx = -1;
  for (let i = nowIdx; i >= 0; i--) {
    if (pts[i].t <= target) { fromIdx = i; break; }
  }
  if (fromIdx < 0) {
    const first = pts[0];
    const covered = now.t - first.t;
    if (covered < windowMs / 3) {
      return unknownSentiment(
        tokenId,
        windowHours,
        `only ${(covered / 3_600_000).toFixed(1)}h of history for a ${windowHours}h window`,
      );
    }
    fromIdx = 0;
  }
  const from = pts[fromIdx];

  const drift = now.p - from.p;
  const strength = Math.abs(drift);
  const lean: SentimentLean = strength < flatBand ? "flat" : drift > 0 ? "bullish" : "bearish";
  return {
    tokenId,
    price: now.p,
    from: from.p,
    drift,
    strength,
    lean,
    windowHours,
    points: nowIdx - fromIdx + 1,
  };
}

/** Why the gate rejected this reading, or null when it passes. Named the same
    way `trade_filter_reject` names its dimensions, so the LIVE panel's funnel
    can credit "sentiment" the way it credits "price" or "size". */
export function sentimentReject(
  reading: MarketSentiment | undefined,
  filter: SentimentFilter | undefined | null,
): string | null {
  if (!sentimentFilterActive(filter)) return null;
  const f = filter!;
  const lean = reading?.lean ?? "unknown";

  if (lean === "unknown") {
    // Unknown is never silently a rejection. It is one only when the strat
    // asked for that in so many words.
    return f.unknown === "block" ? "sentiment-unknown" : null;
  }
  if (Array.isArray(f.lean) && f.lean.length > 0 && !f.lean.includes(lean)) {
    return "sentiment";
  }
  const drift = reading!.drift;
  if (f.minDrift != null && drift < f.minDrift) return "sentiment-drift";
  if (f.maxDrift != null && drift > f.maxDrift) return "sentiment-drift";
  return null;
}

/** Boolean form. */
export function sentimentPasses(
  reading: MarketSentiment | undefined,
  filter: SentimentFilter | undefined | null,
): boolean {
  return sentimentReject(reading, filter) === null;
}

// ── Saying it in words ─────────────────────────────────────────────────────

const LEAN_WORDS: Record<SentimentLean, string> = {
  bullish: "with the crowd",
  bearish: "against the crowd",
  flat: "quiet markets",
  unknown: "unreadable",
};

/** The mood the leader's outcome is in, as a person would say it. */
export function leanLabel(lean: SentimentLean): string {
  return LEAN_WORDS[lean];
}

/** One line describing what the filter will do. "" when nothing is on. */
export function describeSentiment(f: SentimentFilter | undefined | null): string {
  if (!sentimentFilterActive(f)) return "";
  const parts: string[] = [];
  const h = sentimentWindowHours(f);
  if (Array.isArray(f!.lean) && f!.lean.length > 0 && f!.lean.length < 4) {
    parts.push(f!.lean.map((l) => LEAN_WORDS[l]).join(" or "));
  }
  if (f!.minDrift != null || f!.maxDrift != null) {
    const lo = f!.minDrift != null ? `${signCents(f!.minDrift)}` : "any";
    const hi = f!.maxDrift != null ? `${signCents(f!.maxDrift)}` : "any";
    parts.push(`drift ${lo}…${hi}`);
  }
  parts.push(`${h}h`);
  if (f!.unknown === "block") parts.push("skip unreadable");
  return parts.join(" · ");
}

/** A reading as a chip: "+7¢ / 6h". */
export function describeReading(s: MarketSentiment | undefined): string {
  if (!s || s.lean === "unknown") return "no read";
  return `${signCents(s.drift)} / ${s.windowHours}h`;
}

function signCents(p: number): string {
  const c = Math.round(p * 100);
  return `${c > 0 ? "+" : c < 0 ? "−" : ""}${Math.abs(c)}¢`;
}

// ── Getting the prices ─────────────────────────────────────────────────────

/** What a warm pass covered, so a screen can be honest about the rest. */
export interface SentimentBook {
  /** tokenId → the series the readings come from. */
  series: Map<string, PricePoint[]>;
  /** Distinct tokens asked about. */
  asked: number;
  /** Tokens a series actually came back for. */
  covered: number;
  /** Tokens dropped for budget — these read `unknown`. */
  overBudget: number;
  /** Oldest instant the fetched history reaches, ms epoch. Trades before it
      read `unknown`; a screen that asked about older flow must say so. */
  coversFromMs: number;
  /** True when the ask was cut short by `MAX_HISTORY_SPAN_MS`. */
  spanCapped: boolean;
  /** The lookup to hand the gate. */
  lookup: SentimentLookup;
}

export function emptySentimentBook(): SentimentBook {
  return {
    series: new Map(), asked: 0, covered: 0, overBudget: 0,
    coversFromMs: 0, spanCapped: false, lookup: () => undefined,
  };
}

export interface WarmOpts {
  filter?: SentimentFilter | null;
  /** Instant a live gate reads at. Omit ⇒ each trade is read at its OWN
      timestamp, which is what a replay wants. */
  atMs?: number;
  budget?: number;
  /** Oldest trade the book must cover, ms epoch. Defaults to the oldest trade
      passed in. The fetch reaches one window further back than this. */
  fromMs?: number;
}

/** Fetch the price history every one of `trades` needs and return the book.
 *
 *  Never throws and never rejects a trade on its own: a token whose history
 *  fails simply has no series, reads `unknown`, and is governed by the
 *  filter's own `unknown` policy. */
export async function warmSentiment(
  trades: SentimentSubject[],
  opts: WarmOpts = {},
): Promise<SentimentBook> {
  const filter = opts.filter;
  if (!sentimentFilterActive(filter)) return emptySentimentBook();

  const windowHours = sentimentWindowHours(filter);
  const flatBand = sentimentFlatBand(filter);
  const budget = opts.budget ?? SENTIMENT_TOKEN_BUDGET;

  // Newest trade first, so a budget cut drops the OLDEST readings — the ones a
  // live session cares least about.
  const ordered = [...trades]
    .filter((t) => subjectToken(t).length > 0)
    .sort((a, b) => (b.timestamp ?? 0) - (a.timestamp ?? 0));

  const wanted: string[] = [];
  const seen = new Set<string>();
  let oldest = Number.POSITIVE_INFINITY;
  let newest = 0;
  for (const t of ordered) {
    const id = subjectToken(t);
    if (!seen.has(id)) { seen.add(id); wanted.push(id); }
    const ts = t.timestamp ?? 0;
    if (ts > 0) {
      if (ts < oldest) oldest = ts;
      if (ts > newest) newest = ts;
    }
  }
  const asked = wanted.length;
  const take = wanted.slice(0, budget);

  const toMs = Math.max(newest, opts.atMs ?? Date.now());
  const askedFrom = opts.fromMs ?? (Number.isFinite(oldest) ? oldest : toMs);
  // One window of runway before the first trade, or the anchor point for that
  // trade would be inside the trade window and every early reading would be
  // "only 0.4h of history".
  const wantFrom = askedFrom - windowHours * 3_600_000 * 1.2;
  // …clamped to what one request may span. Trades before this read `unknown`.
  const fromMs = Math.max(wantFrom, toMs - MAX_HISTORY_SPAN_MS);
  const startSec = Math.floor(fromMs / 1000);
  const endSec = Math.ceil(toMs / 1000);
  // 5-minute bars: fine enough that a 6h drift is a real curve, coarse enough
  // that a week of them is a few hundred points.
  const fidelity = 5;

  const series = new Map<string, PricePoint[]>();
  let cursor = 0;
  await Promise.all(
    Array.from({ length: Math.min(CONCURRENCY, take.length) }, async () => {
      for (;;) {
        const i = cursor++;
        if (i >= take.length) return;
        const id = take[i];
        try {
          const pts = await fetchPriceWindow(id, startSec, endSec, fidelity);
          if (pts.length > 0) series.set(id, pts);
        } catch {
          // A missing series is `unknown`, not a rejection. Nothing to log.
        }
      }
    }),
  );

  const cache = new Map<string, MarketSentiment>();
  const lookup: SentimentLookup = (trade) => {
    const id = subjectToken(trade);
    if (!id) return undefined;
    const at = opts.atMs ?? trade.timestamp ?? Date.now();
    const key = `${id}@${Math.round(at / 60_000)}`;
    const hit = cache.get(key);
    if (hit) return hit;
    const reading = readSentiment(series.get(id), at, id, windowHours, flatBand);
    cache.set(key, reading);
    return reading;
  };

  return {
    series,
    asked,
    covered: series.size,
    overBudget: Math.max(0, asked - take.length),
    coversFromMs: fromMs,
    spanCapped: fromMs > wantFrom + 60_000,
    lookup,
  };
}

/** How much of a set of trades the book can actually speak for, 0–1. A screen
    that gates on sentiment must show this: a 12% coverage gate is a gate on
    12% of the flow, whatever the chip says. */
export function sentimentCoverage(
  trades: SentimentSubject[],
  book: SentimentBook,
): { readable: number; total: number; fraction: number } {
  let readable = 0;
  for (const t of trades) {
    const r = book.lookup(t);
    if (r && r.lean !== "unknown") readable++;
  }
  const total = trades.length;
  return { readable, total, fraction: total > 0 ? readable / total : 0 };
}

/** Tally a set of trades by the mood they were taken in — what the SENTIMENT
    card renders, and what makes "would this gate copy anything?" answerable
    before it is armed. */
export function sentimentBreakdown(
  trades: SentimentSubject[],
  book: SentimentBook,
): Record<SentimentLean, number> {
  const out: Record<SentimentLean, number> = { bullish: 0, bearish: 0, flat: 0, unknown: 0 };
  for (const t of trades) out[book.lookup(t)?.lean ?? "unknown"]++;
  return out;
}
