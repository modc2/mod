// The ƒ SCORE MARKET — a searchable shelf of score functions.
//
// Two kinds of listing, one shape: BUILTIN functions this file ships (curated,
// heavy on "consistent ROI" hunters — the question the market exists to
// answer), and COMMUNITY functions published to the API's score-fn store
// (score_fns.rs), portable across deploys by CID exactly like shared strats.
// A listing is just a named source string — USE drops it into the same score
// editor the user could have typed it into, so it compiles through the normal
// path and a broken listing can never rank a board silently.

import { API_BASE } from "./polymarket";

export interface ScoreFnListing {
  id: string;
  name: string;
  description: string;
  tags: string[];
  source: string;
  /** True for the curated shelf this file ships; false = community. */
  builtin: boolean;
  /** Publisher EOA (community listings only). */
  author?: string;
  /** Set when the caller owns this community listing (server-computed). */
  mine?: boolean;
  createdAt?: number;
}

/** The curated shelf. Every entry is honest about its gates: `-1` sentinels
    (consistency, winRate, exitEntry) are checked explicitly, never multiplied
    through, and winRate is used as the 0–100 percent it actually is. */
export const SCORE_FN_LIBRARY: ScoreFnListing[] = [
  {
    id: "steady-roi",
    name: "STEADY ROI",
    description:
      "ROI, but only when it was earned steadily. Hides anyone under $500 traded, anyone whose PnL curve can't be judged, and anyone whose curve is boom-bust (less than 60% of active stretches green). What's left ranks by plain ROI.",
    tags: ["consistent", "roi", "steady", "filter"],
    builtin: true,
    source: `// STEADY ROI — return-on-turnover, gated on a steady curve.
if (volume < 500) return null;      // too small to copy
if (consistency < 0) return null;   // curve too flat/short to judge
if (consistency < 0.6) return null; // boom-bust
return 100 * pnl / volume;`,
  },
  {
    id: "consistency-x-roi",
    name: "CONSISTENCY × ROI",
    description:
      "ROI weighted by how steadily it was made — a 20% ROI earned in green stretch after green stretch outranks the same 20% from one lucky spike. Unknown consistency hides the row rather than zeroing it.",
    tags: ["consistent", "roi", "weighted"],
    builtin: true,
    source: `// ROI × steadiness — one number, no hard cutoff.
if (volume < 100) return null;
if (consistency < 0) return null; // -1 = unknown, don't multiply by it
return (100 * pnl / volume) * consistency;`,
  },
  {
    id: "proven-winner",
    name: "PROVEN WINNER",
    description:
      "Win rate you can actually trust: at least 10 settled positions (a percentage of four is not a track record), at least 55% of them winners. Score is the win rate, discounted while the sample is still thin.",
    tags: ["consistent", "winrate", "track-record", "settled"],
    builtin: true,
    source: `// Win rate with a real sample behind it. winRate is 0–100.
if (decided < 10) return null;  // not enough settled positions to judge
if (winRate < 55) return null;
return winRate * Math.min(1, decided / 30); // thin samples rank lower`,
  },
  {
    id: "dollar-rider",
    name: "$1 RIDER",
    description:
      "Win rate, the strict version: of the trader's settled buys, the share that rode ALL the way to a full $1 resolution. No credit for scalps sold early — this is the hit rate you'd get by just copying their buys and holding to redemption (pair it with a strat whose COPY SELLS is off). Needs 10+ settled positions; thin samples rank lower.",
    tags: ["winrate", "resolution", "hold", "buys", "settled"],
    builtin: true,
    source: `// Chance a BUY finishes at $1. resolveRate is 0–100; -1 = unknown.
if (resolveRate < 0) return null;  // nothing settled to judge
if (decided < 10) return null;     // a percentage of four is not a track record
return resolveRate * Math.min(1, decided / 30); // thin samples rank lower`,
  },
  {
    id: "smooth-curve",
    name: "SMOOTH CURVE (PY)",
    description:
      "Python. Fits a straight line to the cumulative PnL curve and scores ROI × R² — a machine printing money in a straight line beats an equally profitable rollercoaster. Smooth losers are still hidden.",
    tags: ["consistent", "roi", "curve", "python", "r2"],
    builtin: true,
    source: `def score(curve, pnl, volume):
    # ROI x how straight the PnL line is (R^2 of curve vs time).
    if volume < 500 or len(curve) < 6:
        return None
    n = len(curve)
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(curve) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, curve))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in curve)
    if vx == 0 or vy == 0:
        return None  # flat curve — nothing to judge
    if curve[-1] <= 0:
        return None  # a smooth loser is still a loser
    r2 = (cov * cov) / (vx * vy)
    return (100.0 * pnl / volume) * r2`,
  },
  {
    id: "no-tourists",
    name: "NO TOURISTS",
    description:
      "Hides the one-shot accounts — under 10 positions, under 3 markets, or under $1k traded — and ranks whoever is left on Sharpe, the risk-adjusted consistency number the server already computes per trade.",
    tags: ["filter", "sharpe", "activity", "breadth"],
    builtin: true,
    source: `// Real operators only, ranked on risk-adjusted return.
if (positions < 10 || markets < 3 || volume < 1000) return null;
return sharpe;`,
  },
  {
    id: "grinder",
    name: "THE GRINDER",
    description:
      "The full gauntlet: real volume, a settled track record of 15+, a majority win rate, and a steady curve — then ranks by ROI × consistency. The strictest consistent-ROI hunter on the shelf; expect it to hide most of the board.",
    tags: ["consistent", "roi", "winrate", "strict", "filter"],
    builtin: true,
    source: `// Every consistency gate at once. Survivors are the grinders.
if (volume < 1000) return null;
if (decided < 15) return null;       // settled sample large enough to mean something
if (winRate < 50) return null;       // winRate is 0–100
if (consistency < 0.55) return null; // steady curve (-1 unknown fails too)
return (100 * pnl / volume) * consistency;`,
  },
];

/** Token-AND search over name, description, tags, and source — "consistent
    roi" matches a listing mentioning both words anywhere. Empty query =
    everything. */
export function searchScoreFns(query: string, listings: ScoreFnListing[]): ScoreFnListing[] {
  const tokens = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (tokens.length === 0) return listings;
  return listings.filter((l) => {
    const hay = `${l.name} ${l.description} ${l.tags.join(" ")} ${l.source}`.toLowerCase();
    return tokens.every((t) => hay.includes(t));
  });
}

// ── Community listings — the API's score-fn store ──

const MARKET_API = `${API_BASE}/score-fns`;

interface ApiScoreFn {
  id: string;
  name: string;
  description: string;
  tags: string[];
  source: string;
  author: string;
  mine: boolean;
  createdAt: number;
}

/** Every community listing the caller can see (public + their own).
    Failure degrades to an empty shelf — the builtin library still works. */
export async function fetchCommunityScoreFns(owner?: string | null): Promise<ScoreFnListing[]> {
  try {
    const url = owner ? `${MARKET_API}?owner=${encodeURIComponent(owner)}` : MARKET_API;
    const res = await fetch(url);
    if (!res.ok) return [];
    const data = (await res.json()) as { fns?: ApiScoreFn[] };
    return (data.fns ?? []).map((f) => ({
      id: f.id,
      name: f.name,
      description: f.description,
      tags: f.tags ?? [],
      source: f.source,
      builtin: false,
      author: f.author,
      mine: f.mine,
      createdAt: f.createdAt,
    }));
  } catch {
    return [];
  }
}

/** Publish the current formula to the community shelf. Returns the saved
    listing, or throws with a human-readable message. */
export async function publishScoreFn(input: {
  owner: string;
  name: string;
  description: string;
  source: string;
  tags?: string[];
}): Promise<void> {
  const res = await fetch(MARKET_API, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) {
    const data = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(data.error || `publish failed (${res.status})`);
  }
}

export async function deleteScoreFn(id: string, owner: string): Promise<void> {
  const res = await fetch(`${MARKET_API}/${encodeURIComponent(id)}?owner=${encodeURIComponent(owner)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const data = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(data.error || `delete failed (${res.status})`);
  }
}

/** Share a listing cross-deploy: the API bundles it and returns a CID
    (same content-addressable store strats share through). */
export async function shareScoreFn(id: string): Promise<string> {
  const res = await fetch(`${MARKET_API}/${encodeURIComponent(id)}/share`, { method: "POST" });
  const data = (await res.json().catch(() => ({}))) as { cid?: string; error?: string };
  if (!res.ok || !data.cid) throw new Error(data.error || `share failed (${res.status})`);
  return data.cid;
}

/** Import a score fn shared from another deploy by CID — lands as the
    caller's own (private) listing. */
export async function importScoreFn(cid: string, owner: string): Promise<void> {
  const res = await fetch(`${MARKET_API}/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cid, owner }),
  });
  if (!res.ok) {
    const data = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(data.error || `import failed (${res.status})`);
  }
}
