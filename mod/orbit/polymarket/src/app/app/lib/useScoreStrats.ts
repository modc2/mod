"use client";

// Keeps one strat per score function in MY STRATS — ranked, materialized and
// graded out of sample (lib/scoreStrats.ts does the work; this is the loop).
//
// Lifecycle of a score strat:
//   RANK      the live board's top N under the listing's function becomes the
//             strat's roster (TRADER INDEX params). New listings get a strat,
//             existing ones get their roster refreshed — never their params.
//   GRADE     the same rule is replayed out of sample (picked on the board
//             from N days ago, traded over the N days since) and the result
//             lands on the strat as its backtest.
//   DISMISS   delete one from MY STRATS and it stays deleted: a listing whose
//             strat this browser made before and that is now missing is the
//             user's answer, not a gap to refill. RESTORE brings it back.
//
// Safety: a strat whose engine is RUNNING is never re-rostered underneath it
// — swapping who a live session copies is a decision, not a refresh. Every
// strat this makes starts liveEnabled:false; running one is the LIVE tab's job.

import { useCallback, useEffect, useRef, useState } from "react";
import type { TraderFeed } from "./hubReplay";
import { loadIndexes, updateIndex, upsertIndex } from "./indexStore";
import {
  SCORE_STRAT_DEFAULT_TEST_DAYS, SCORE_STRAT_DEFAULT_TOP_N, SCORE_STRAT_MAX_TOP_N,
  fetchBoardPool, oosBacktest, pastBoard, rankListing, sameRoster, scoreStratId, scoreStratIndex,
} from "./scoreStrats";
import { SCORE_FN_LIBRARY, fetchCommunityScoreFns, type ScoreFnListing } from "./scoreMarket";

const SETTINGS_KEY = "poly_score_strats_v1";
/** Auto re-rank cadence while the STRATS tab is open. The board's own sync is
    hourly; a roster that turns over faster than this is churn, not signal. */
const AUTO_RERANK_MS = 6 * 3600_000;

interface Settings {
  topN: Record<string, number>;
  testDays: number;
  /** Strat ids this browser has materialized — the dismiss detector. */
  made: string[];
  dismissed: string[];
  rankedAt: number;
}

function loadSettings(): Settings {
  const d: Settings = { topN: {}, testDays: SCORE_STRAT_DEFAULT_TEST_DAYS, made: [], dismissed: [], rankedAt: 0 };
  if (typeof window === "undefined") return d;
  try {
    return { ...d, ...(JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}") as Partial<Settings>) };
  } catch {
    return d;
  }
}

function saveSettings(s: Settings): void {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(s));
  } catch {
    // quota — settings are re-derivable
  }
}

export type RowState =
  | { kind: "idle" }
  | { kind: "busy"; what: string }
  | { kind: "ok"; count: number }
  | { kind: "skipped"; why: string }
  | { kind: "error"; error: string };

export interface ScoreStratsState {
  listings: ScoreFnListing[];
  settings: Settings;
  rows: Record<string, RowState>;
  /** Progress line for the whole pass, null when idle. */
  progress: string | null;
  busy: boolean;
  rerank: (only?: string[]) => Promise<void>;
  grade: (only?: string[]) => Promise<void>;
  setTopN: (listing: ScoreFnListing, n: number) => void;
  setTestDays: (d: number) => void;
  restore: (listing: ScoreFnListing) => void;
}

const changed = () => window.dispatchEvent(new Event("strat-updated"));

export function useScoreStrats(owner: string | null, running: Set<string>): ScoreStratsState {
  const [community, setCommunity] = useState<ScoreFnListing[]>([]);
  const [communityReady, setCommunityReady] = useState(false);
  const [settings, setSettingsState] = useState<Settings>(loadSettings);
  const [rows, setRows] = useState<Record<string, RowState>>({});
  const [progress, setProgress] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const feeds = useRef(new Map<string, Promise<TraderFeed>>());
  const runningRef = useRef(running);
  runningRef.current = running;

  useEffect(() => {
    let dead = false;
    void fetchCommunityScoreFns(owner).then((c) => {
      if (dead) return;
      setCommunity(c);
      setCommunityReady(true);
    });
    return () => { dead = true; };
  }, [owner]);

  const listings = [...SCORE_FN_LIBRARY, ...community];
  const listingsRef = useRef(listings);
  listingsRef.current = listings;

  const update = useCallback((patch: Partial<Settings>) => {
    const next = { ...loadSettings(), ...patch };
    saveSettings(next);
    setSettingsState(next);
    return next;
  }, []);
  const setRow = (id: string, s: RowState) => setRows((r) => ({ ...r, [id]: s }));

  const rerank = useCallback(async (only?: string[]) => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    try {
      let s = loadSettings();
      const targets = listingsRef.current.filter((l) => !only || only.includes(l.id));
      for (const l of targets) setRow(l.id, { kind: "busy", what: "reading board" });
      const pool = await fetchBoardPool({}, (d, t) => setProgress(`reading the board · page ${d}/${t}`));
      const stored = loadIndexes();
      for (const l of targets) {
        const id = scoreStratId(l);
        const existing = stored.find((i) => i.id === id);
        // Made here before and gone now = the user deleted it. Respect that.
        if (!existing && s.made.includes(id) && !s.dismissed.includes(id)) {
          s = update({ dismissed: [...s.dismissed, id] });
        }
        if (s.dismissed.includes(id)) {
          setRow(l.id, { kind: "skipped", why: "deleted — RESTORE to bring it back" });
          continue;
        }
        if (existing && runningRef.current.has(id)) {
          setRow(l.id, { kind: "skipped", why: "running live — roster frozen until you stop it" });
          continue;
        }
        setProgress(`ranking · ${l.name}`);
        try {
          const topN = s.topN[l.id] ?? existing?.scoreFn?.topN ?? SCORE_STRAT_DEFAULT_TOP_N;
          const ranked = await rankListing(l, pool, topN);
          const next = scoreStratIndex(l, ranked, topN, existing);
          // Unchanged roster: keep updatedAt so cached backtests stay keyed.
          if (existing && sameRoster(existing.traders, next.traders)) next.updatedAt = existing.updatedAt;
          upsertIndex(next);
          if (!s.made.includes(id)) s = update({ made: [...s.made, id] });
          setRow(l.id, { kind: "ok", count: ranked.length });
        } catch (e) {
          setRow(l.id, { kind: "error", error: e instanceof Error ? e.message : String(e) });
        }
      }
      if (!only) update({ rankedAt: Date.now() });
      changed();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      for (const l of listingsRef.current) {
        if (!only || only.includes(l.id)) setRow(l.id, { kind: "error", error: msg });
      }
    } finally {
      busyRef.current = false;
      setBusy(false);
      setProgress(null);
    }
  }, [update]);

  const grade = useCallback(async (only?: string[]) => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    try {
      const { testDays } = loadSettings();
      const past = await pastBoard(testDays, (d, t) => setProgress(`reading the board from ${testDays}d ago · page ${d}/${t}`));
      for (const l of listingsRef.current) {
        if (only && !only.includes(l.id)) continue;
        const strat = loadIndexes().find((i) => i.id === scoreStratId(l));
        if (!strat?.scoreFn) continue;
        setRow(l.id, { kind: "busy", what: `backtesting ${testDays}d` });
        setProgress(`backtesting · ${l.name}`);
        try {
          const r = await oosBacktest(l, strat, past, testDays, feeds.current);
          const bt = r.bt;
          const capital = bt?.capital ?? strat.capital ?? 1000;
          updateIndex(strat.id, {
            scoreFn: {
              ...strat.scoreFn,
              oos: {
                scanId: r.scanId, testDays, picks: r.picks.length,
                pnl: bt?.pnl ?? 0, roi: bt?.roi ?? 0, trades: bt?.trades ?? 0,
                capital, curve: bt?.curve ?? [], note: bt?.note,
                markedUsd: bt?.settlement?.markedUsd, at: r.at,
              },
            },
            // The card's BACKTEST column reads these — for a score strat
            // they ARE the out-of-sample grade of its rule.
            lastPnl: bt?.pnl ?? 0,
            lastRoi1k: capital > 0 ? ((bt?.pnl ?? 0) / capital) * 1000 : 0,
            lastTradeCount: bt?.trades ?? 0,
            lastBacktestAt: r.at,
          });
          setRow(l.id, { kind: "ok", count: strat.traders.length });
          changed();
        } catch (e) {
          setRow(l.id, { kind: "error", error: e instanceof Error ? e.message : String(e) });
        }
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setProgress(null);
      for (const l of listingsRef.current) {
        if (!only || only.includes(l.id)) setRow(l.id, { kind: "error", error: msg });
      }
    } finally {
      busyRef.current = false;
      setBusy(false);
      setProgress(null);
    }
  }, []);

  const setTopN = useCallback((l: ScoreFnListing, n: number) => {
    const topN = Math.max(1, Math.min(SCORE_STRAT_MAX_TOP_N, Math.round(n)));
    const s = loadSettings();
    update({ topN: { ...s.topN, [l.id]: topN } });
    // Rename only while the name is still the generated one.
    const strat = loadIndexes().find((i) => i.id === scoreStratId(l));
    if (strat && strat.name === `TOP ${strat.scoreFn?.topN} · ${l.name}`) {
      updateIndex(strat.id, { name: `TOP ${topN} · ${l.name}`, scoreFn: strat.scoreFn && { ...strat.scoreFn, topN } });
    } else if (strat?.scoreFn) {
      updateIndex(strat.id, { scoreFn: { ...strat.scoreFn, topN } });
    }
    void rerank([l.id]).then(() => grade([l.id]));
  }, [update, rerank, grade]);

  // A new window is a new question — grade it straight away.
  const setTestDays = useCallback((d: number) => {
    update({ testDays: d });
    void grade();
  }, [update, grade]);

  const restore = useCallback((l: ScoreFnListing) => {
    const id = scoreStratId(l);
    update({ dismissed: loadSettings().dismissed.filter((x) => x !== id), made: loadSettings().made.filter((x) => x !== id) });
    void rerank([l.id]).then(() => grade([l.id]));
  }, [update, rerank, grade]);

  // Auto: first visit materializes every score fn's strat and grades it; after
  // that, re-rank + re-grade when the last pass is older than AUTO_RERANK_MS.
  const autoRan = useRef(false);
  // Waits for the community shelf so its listings get strats on the same pass.
  useEffect(() => {
    if (autoRan.current || !communityReady) return;
    autoRan.current = true;
    if (Date.now() - loadSettings().rankedAt < AUTO_RERANK_MS) return;
    void rerank().then(() => grade());
  }, [communityReady, rerank, grade]);

  return { listings, settings, rows, progress, busy, rerank, grade, setTopN, setTestDays, restore };
}
