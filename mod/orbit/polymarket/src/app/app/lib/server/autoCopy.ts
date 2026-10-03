// AUTO COPY — the background copy-trade of a VETTED leaderboard.
//
// Every worker pass replays "what if I copy-traded each of the current top
// traders, one identity strat per trader" — nobody has to fork anything
// or publish a manifest for it. The roster is vetted BEFORE anyone enters
// the test: ranked by STEADY (consistent returns — per-period Sharpe of the
// PnL curve) rather than raw PnL by default, and floored on account age
// (firstTradeTs), trades in the window, and curve consistency — so a wallet
// that is six days old, or made all its money in one spike, never gets a
// card, let alone a copy. Each vetted trader gets TWO replays over two
// windows that CANNOT overlap, because one ends where the other begins:
//
//   TRAIN  [now − (train+test)d, now − test d]   why they were picked
//   TEST   [now − test d,        now         ]   did it keep paying
//
// Defaults: 20d train + 10d test = 30 days back, the last 10 the backtest.
// Both are user-set through /_api/hub/autocopy; validation clamps them so
// train ≥ 1, test ≥ 1 and train+test ≤ 30 (MAX_LOOKBACK_DAYS — the feed's
// own ceiling; asking past it silently returns less data, which would make
// the train window a fiction).
//
// EFFICIENCY: this rides the existing worker pass end to end. The roster's
// feeds are kept warm by the fetch loop (runRefresh unions these addresses
// into its roster), both replays read the pass's shared feed map (one disk
// load per trader per pass), resolutions come out of the pass's shared
// budget, and identity strats carry no momentum so no price tape is ever
// fetched. Steady state: zero upstream requests, CPU only.
//
// The TEST replay also carries the engine's own holdout (stats frozen at the
// test start over exactly the train window) — that is the deployable number.
// The card's verdict compares train vs test with the same rule the
// walk-forward uses (`forwardVerdict`), so "held" means the same thing here
// as everywhere else on the console.

import { mkdirSync, readFileSync } from "fs";
import { join } from "path";

import { identityStrat } from "../identityStrat";
import {
  backtestOne, forwardVerdict,
  type FeedLoader, type ForwardVerdict, type HubBacktest, type LegResolver, type TraderFeed,
} from "../hubReplay";
import { fetchTopTraderAddresses, MAX_LOOKBACK_DAYS } from "../polymarket";
import { writeAtomic } from "./feedStore";
import { stateDir } from "./ownerToken";

export interface AutoCopySettings {
  /** Off = the pass skips it entirely and the fetch loop stops warming it. */
  enabled: boolean;
  /** How many of the board's top traders to copy. */
  count: number;
  /** Train window length in days — ends where the test window starts. */
  trainDays: number;
  /** Test (backtest) window length in days — ends now. */
  testDays: number;
  /** What ranks the roster. "steady" = the per-period Sharpe of the PnL
      curve (consistent returns), "pnl" = the raw top-PnL board this used to
      be. An API binary too old to know sort=steady falls back to pnl on its
      own — the vetting floors below still apply either way. */
  rankBy: "steady" | "pnl";
  /** Track-record floor — a trader's FIRST-EVER trade must be at least this
      many days old. UNKNOWN age is KEPT (board convention: a 429'd lookup
      must not empty the roster). 0 = off. */
  minHistoryDays: number;
  /** Trade-history floor — minimum trades inside the lookback window.
      0 = off. */
  minTrades: number;
  /** Consistency floor 0–1 — minimum share of the PnL curve's moved segments
      that climbed. UNKNOWN curve is CUT while on (the curve's shape IS the
      subject). 0 = off. */
  minConsistency: number;
}

/** 20 + 10: look 30 days back, the last 10 are the backtest. The vetting
    floors default ON — the board exists to avoid copying spike-lucky or
    week-old wallets, so out of the box it ranks by STEADY and requires a
    30-day record, 20 trades in the window, and a curve that climbed in at
    least 60% of its moved segments. */
export const AUTO_COPY_DEFAULTS: AutoCopySettings = {
  enabled: true,
  count: 10,
  trainDays: 20,
  testDays: 10,
  rankBy: "steady",
  minHistoryDays: 30,
  minTrades: 20,
  minConsistency: 0.6,
};

/** Paper capital each identity replay runs on — same as the shelf's cards. */
const AUTO_COPY_CAPITAL = 1000;
/** Leaderboard re-query cadence. The board barely moves inside a few hours,
    and every re-query risks churning the roster mid-comparison. */
const ROSTER_TTL_MS = 3 * 3600_000;
const MAX_COUNT = 20;

export interface AutoCopyCard {
  address: string;
  /** Leaderboard rank at pick time (1-based). */
  rank: number;
  /** [now − (train+test), now − test] — the record they were picked on. */
  train: HubBacktest;
  /** [now − test, now] — with `holdout` inside it: the same window replayed
      with stats frozen at its start over exactly the train window. */
  test: HubBacktest;
  /** train vs test under the walk-forward rule — "held" is the pass. */
  verdict: ForwardVerdict;
  at: number;
}

export interface AutoCopyState {
  status: {
    at: number;
    running: boolean;
    /** The roster the last pass replayed, best rank first. */
    roster: string[];
    /** The settings that produced `results` — the UI labels windows off
        THESE, not off whatever was just typed into the form. */
    settings: AutoCopySettings;
    error?: string;
  };
  /** address → card. */
  results: Record<string, AutoCopyCard>;
}

// Same dir the worker's own files live in — created here too, because the
// settings route can run before the worker's first cycle does.
function hubDir(): string {
  const dir = join(stateDir(), "hub");
  mkdirSync(dir, { recursive: true });
  return dir;
}
const settingsPath = () => join(hubDir(), "autocopy_settings.json");
const statePath = () => join(hubDir(), "autocopy_state.json");
const rosterPath = () => join(hubDir(), "autocopy_roster.json");

function readJson<T>(path: string, fallback: T): T {
  try {
    return JSON.parse(readFileSync(path, "utf8")) as T;
  } catch {
    return fallback;
  }
}

/** Clamp anything into a valid, NON-OVERLAPPING split. The windows are
    back-to-back by construction (train ends where test begins), so "don't
    overlap" reduces to both being ≥ 1 day and fitting inside the 30-day feed:
    test keeps what it asked for (capped to leave train at least a day), train
    gets clamped to the room the feed leaves behind the test window. */
export function normalizeAutoCopy(raw: Partial<AutoCopySettings>): AutoCopySettings {
  const num = (v: unknown, fallback: number) =>
    Number.isFinite(Number(v)) && Number(v) > 0 ? Math.round(Number(v)) : fallback;
  // Vetting floors: 0 is a VALID value ("off"), unlike the windows where 0
  // makes no sense — so only a non-number falls back to the default.
  const floor = (v: unknown, fallback: number) =>
    Number.isFinite(Number(v)) && Number(v) >= 0 ? Math.round(Number(v)) : fallback;
  const testDays = Math.min(num(raw.testDays, AUTO_COPY_DEFAULTS.testDays), MAX_LOOKBACK_DAYS - 1);
  const trainDays = Math.min(num(raw.trainDays, AUTO_COPY_DEFAULTS.trainDays), MAX_LOOKBACK_DAYS - testDays);
  const minConsistency = Number(raw.minConsistency);
  return {
    enabled: typeof raw.enabled === "boolean" ? raw.enabled : AUTO_COPY_DEFAULTS.enabled,
    count: Math.min(num(raw.count, AUTO_COPY_DEFAULTS.count), MAX_COUNT),
    trainDays: Math.max(1, trainDays),
    testDays: Math.max(1, testDays),
    rankBy: raw.rankBy === "pnl" ? "pnl" : AUTO_COPY_DEFAULTS.rankBy,
    minHistoryDays: floor(raw.minHistoryDays, AUTO_COPY_DEFAULTS.minHistoryDays),
    minTrades: floor(raw.minTrades, AUTO_COPY_DEFAULTS.minTrades),
    minConsistency: Number.isFinite(minConsistency)
      ? Math.min(1, Math.max(0, minConsistency))
      : AUTO_COPY_DEFAULTS.minConsistency,
  };
}

export function readAutoCopySettings(): AutoCopySettings {
  return normalizeAutoCopy(readJson<Partial<AutoCopySettings>>(settingsPath(), {}));
}

/** Persist new settings. When the split or roster size changed, the existing
    cards describe windows that no longer exist — wipe them rather than let a
    "TRAIN 20D" header sit over numbers computed on 15. */
export function writeAutoCopySettings(raw: Partial<AutoCopySettings>): AutoCopySettings {
  const next = normalizeAutoCopy(raw);
  const prev = readAutoCopySettings();
  writeAtomic(settingsPath(), JSON.stringify(next));
  if (
    next.trainDays !== prev.trainDays || next.testDays !== prev.testDays ||
    next.count !== prev.count || next.rankBy !== prev.rankBy ||
    next.minHistoryDays !== prev.minHistoryDays ||
    next.minTrades !== prev.minTrades ||
    next.minConsistency !== prev.minConsistency
  ) {
    const state = readAutoCopyState();
    state.results = {};
    state.status = { ...state.status, at: 0, settings: next };
    writeAtomic(statePath(), JSON.stringify(state));
  }
  return next;
}

export function readAutoCopyState(): AutoCopyState {
  return readJson<AutoCopyState>(statePath(), {
    status: { at: 0, running: false, roster: [], settings: readAutoCopySettings() },
    results: {},
  });
}

// ── Roster ──────────────────────────────────────────────────────

interface RosterCache {
  addrs: string[];
  /** The query that produced it — a settings change is a different roster. */
  key: string;
  at: number;
}

function rosterKey(s: AutoCopySettings): string {
  return `${s.trainDays + s.testDays}d:${s.count}:${s.rankBy}:` +
    `${s.minHistoryDays}h:${s.minTrades}t:${s.minConsistency}c`;
}

/** The VETTED roster, leaderboard-ranked over the FULL lookback
    (train + test days). Ranked by the settings' rankBy (STEADY by default —
    consistent returns, not one lucky spike) and pre-filtered by the vetting
    floors: minimum account age (firstTradeTs), minimum trades in the window,
    minimum PnL-curve consistency. Traders that fail vetting never enter the
    test at all — that's the point. Disk-cached so replay passes read it for
    free; only an aged-out or resettled query touches the network, and a
    failed query keeps the previous roster rather than emptying the board. */
export async function resolveAutoCopyRoster(s: AutoCopySettings): Promise<string[]> {
  const key = rosterKey(s);
  const hit = readJson<RosterCache | null>(rosterPath(), null);
  if (hit && hit.key === key && Date.now() - hit.at < ROSTER_TTL_MS && hit.addrs.length > 0) {
    return hit.addrs;
  }
  let addrs: string[] = [];
  try {
    addrs = await fetchTopTraderAddresses({
      days: s.trainDays + s.testDays,
      sort: s.rankBy === "steady" ? "steady" : "pnl",
      minHistoryDays: s.minHistoryDays,
      minTrades: s.minTrades,
      minConsistency: s.minConsistency,
    }, s.count);
  } catch {
    addrs = [];
  }
  if (addrs.length > 0) {
    const lower = addrs.map((a) => a.toLowerCase());
    writeAtomic(rosterPath(), JSON.stringify({ addrs: lower, key, at: Date.now() } satisfies RosterCache));
    return lower;
  }
  return hit && hit.key === key ? hit.addrs : [];
}

/** What the fetch loop should keep warm for this board — empty when off. */
export async function autoCopyWarmAddresses(): Promise<string[]> {
  const s = readAutoCopySettings();
  if (!s.enabled) return [];
  return resolveAutoCopyRoster(s);
}

// ── The pass ────────────────────────────────────────────────────

/** One trader as the strat the copy desk would run for them — the SAME
    identity template the live engine uses, so this board's numbers describe
    the copy trade you'd actually get. */
function autoIdentity(address: string) {
  const now = Date.now();
  return identityStrat({
    address,
    allocationUsd: AUTO_COPY_CAPITAL,
    enabled: false,
    addedAt: now,
    updatedAt: now,
  });
}

/** Replay the whole board once, inside a worker pass. Shares the pass's feed
    map, loader and resolution budget — see the file header. Cards are written
    as they land so a long pass still leaves the board something to read. */
export async function runAutoCopyPass(
  feeds: Map<string, Promise<TraderFeed>>,
  loader: FeedLoader,
  resolve: LegResolver,
): Promise<void> {
  const settings = readAutoCopySettings();
  const state = readAutoCopyState();
  if (!settings.enabled) {
    if (state.status.running) {
      state.status.running = false;
      writeAtomic(statePath(), JSON.stringify(state));
    }
    return;
  }

  const roster = await resolveAutoCopyRoster(settings);
  state.status = { at: state.status.at, running: true, roster, settings };
  // Forget traders who fell off the board — a stale card outranked by nobody
  // would sit there quoting a roster that no longer exists.
  const keep = new Set(roster);
  for (const addr of Object.keys(state.results)) {
    if (!keep.has(addr)) delete state.results[addr];
  }
  writeAtomic(statePath(), JSON.stringify(state));

  const testStart = Date.now() - settings.testDays * 86400_000;
  let error: string | undefined;
  try {
    for (let i = 0; i < roster.length; i++) {
      const addr = roster[i];
      const idx = autoIdentity(addr);
      // TEST first — it's the number the board leads with. Holdout lookback =
      // train + test, so the frozen stats window is exactly the train window.
      const test = await backtestOne(idx, settings.testDays, feeds, loader, resolve, {
        forward: false,
        holdout: true,
        holdoutLookbackDays: settings.trainDays + settings.testDays,
      });
      // TRAIN: same strat, same feed, window ending where TEST began.
      const train = await backtestOne(idx, settings.trainDays, feeds, loader, resolve, {
        forward: false,
        holdout: false,
        asOf: testStart,
      });
      if (!test || !train) continue;
      state.results[addr] = {
        address: addr,
        rank: i + 1,
        train,
        test,
        verdict: forwardVerdict(
          { pnl: train.pnl, trades: train.trades },
          { pnl: test.pnl, trades: test.trades },
        ),
        at: Date.now(),
      };
      writeAtomic(statePath(), JSON.stringify(state));
    }
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  state.status = {
    at: Date.now(), running: false, roster, settings,
    ...(error ? { error } : {}),
  };
  writeAtomic(statePath(), JSON.stringify(state));
}
