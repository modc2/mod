"use client";

// THE COPY STUDIO — the console's front door, built around one loop:
//
//   find a trader → highlight them → see what copying them would have done →
//   add them with $X → see what the whole allocation would have done → COPY ALL
//
// Highlighting (hover or click a row) replays that trader at the amount you'd
// give them; every change to an amount replays the allocation. Nothing is
// committed until COPY ALL, which writes the same /copy/allocations rows the
// desk and the pm_copy_* MCP tools use — and those start in DRY RUN.
//
// The math lives in lib/copyStudio.ts (pure, tested); the history in
// lib/useTraderFeeds.ts. This file is layout and wiring only.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";

import {
  DEFAULT_LEG_USD, STUDIO_STORAGE_KEY, STUDIO_WINDOWS, addLeg, fitToBankroll, fmtPct, fmtUsd,
  parseStudio, previewKey, removeLeg, setLegUsd, simulateAllocation, simulateTrader, splitByResult,
  splitEqual, totalUsd, verdict, weights, type StudioLeg, type StudioState, type Verdict,
} from "../lib/copyStudio";
import type { Sleeve } from "../lib/basketSim";
import { useTraderFeeds } from "../lib/useTraderFeeds";
import {
  DEFAULT_SCORE_WEIGHTS, SCORE_PARTS, SCORE_PART_HINT, SCORE_PART_LABEL, SCORE_STORAGE_KEY,
  cleanWeights, explainScore, parseWeights, rankByScore, steadyScore, type ScoreWeights,
} from "../lib/copyScore";
import { fetchTradersPage, formatVolume, timeAgo, type TopTrader } from "../lib/polymarket";
import { COPY_BOOK_CHANGED_EVENT, upsertAllocation } from "../lib/copyBook";
import { shortAddress } from "../lib/identityStrat";
import { getOwnerAddress } from "../lib/access";
import { useAuth } from "../context/AuthContext";
import BundlerPanel from "./BundlerPanel";
import EquityChart from "./EquityChart";
import Sparkline from "./Sparkline";

/** `pool` is the server sort the board is pulled under. STEADY and PROFIT %
    are computed here from each row's copy replay, so they re-rank the
    top-PROFIT pool; `usd` and the rest are the server's own order. */
const SORTS = [
  { key: "steady", label: "STEADY", pool: "pnl", local: true },
  { key: "pct", label: "PROFIT %", pool: "pnl", local: true },
  { key: "usd", label: "PROFIT $", pool: "pnl", local: false },
  { key: "roi", label: "ROI", pool: "roi", local: false },
  { key: "volume", label: "VOLUME", pool: "volume", local: false },
] as const;

/** Hover has to settle before a replay fires — sweeping the mouse down the
    board must not queue twenty 30-day history walks. */
const HOVER_SETTLE_MS = 220;
const PAGE_SIZE = 40;

/** Frequency floor — average trades per day over the backtest window. Low-
    frequency traders give a copy book too few fills to learn anything from.
    Applied server-side (`minTrades` = per-day × window) so the top-40 pool is
    drawn from frequent traders only, not thinned after the fact. */
const MIN_PER_DAY_KEY = "poly:copyStudio:minPerDay:v1";

/** The front door opens SIMPLE: search, six columns, one green button. MORE
    opens every dial (sorts, filters, score weights, agent bundles, splits) —
    the full studio, unchanged. Remembered per device. */
const SIMPLE_KEY = "poly:copyStudio:simple:v1";

const TONE: Record<Verdict, string> = {
  up: "text-green-400",
  down: "text-red-400",
  flat: "text-pixel-gray-light",
  idle: "text-amber-400",
  loading: "text-pixel-gray",
};

function fmtPerDay(v: number): string {
  return v >= 10 ? String(Math.round(v)) : v >= 1 ? v.toFixed(1) : v > 0 ? v.toFixed(2) : "0";
}

function toneOf(v: number): string {
  return v > 0.005 ? "text-green-400" : v < -0.005 ? "text-red-400" : "text-pixel-gray-light";
}

function useStudioState(): [StudioState, (fn: (s: StudioState) => StudioState) => void] {
  const [state, setState] = useState<StudioState>(() => parseStudio(null));
  const hydrated = useRef(false);
  useEffect(() => {
    try { setState(parseStudio(localStorage.getItem(STUDIO_STORAGE_KEY))); } catch {}
    hydrated.current = true;
  }, []);
  const update = useCallback((fn: (s: StudioState) => StudioState) => {
    setState((prev) => {
      const next = fn(prev);
      if (hydrated.current) {
        try { localStorage.setItem(STUDIO_STORAGE_KEY, JSON.stringify(next)); } catch {}
      }
      return next;
    });
  }, []);
  return [state, update];
}

export default function CopyStudio() {
  const [studio, update] = useStudioState();
  const { legs, days, bankroll } = studio;
  const { auth } = useAuth();
  const eoa = getOwnerAddress() ?? auth.address ?? null;

  // ── Simple vs. full studio ──
  const [simple, setSimple] = useState(true);
  useEffect(() => {
    try { if (localStorage.getItem(SIMPLE_KEY) === "0") setSimple(false); } catch {}
  }, []);
  const toggleSimple = () => setSimple((v) => {
    try { localStorage.setItem(SIMPLE_KEY, v ? "0" : "1"); } catch {}
    return !v;
  });

  // ── Board ──
  const [sort, setSort] = useState<string>("steady");
  // Click the active sort again to flip it (worst first).
  const [asc, setAsc] = useState(false);
  const pickSort = (k: string) => {
    if (k === sort) setAsc((v) => !v);
    else { setSort(k); setAsc(false); }
  };
  const isLocal = SORTS.find((x) => x.key === sort)?.local ?? false;
  const pool = SORTS.find((x) => x.key === sort)?.pool ?? "pnl";
  const [search, setSearch] = useState("");
  const [topic, setTopic] = useState("");
  const [activeOnly, setActiveOnly] = useState(true);
  const [minPerDay, setMinPerDayRaw] = useState(0);
  useEffect(() => {
    try { const v = Number(localStorage.getItem(MIN_PER_DAY_KEY)); if (v > 0) setMinPerDayRaw(v); } catch {}
  }, []);
  const setMinPerDay = (v: number) => {
    const n = Number.isFinite(v) && v > 0 ? Math.min(v, 1000) : 0;
    setMinPerDayRaw(n);
    try { if (n > 0) localStorage.setItem(MIN_PER_DAY_KEY, String(n)); else localStorage.removeItem(MIN_PER_DAY_KEY); } catch {}
  };
  const [traders, setTraders] = useState<TopTrader[]>([]);
  const [boardTotal, setBoardTotal] = useState(0);
  const [boardState, setBoardState] = useState<"loading" | "ready" | "cold" | "error">("loading");
  const [page, setPage] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);

  useEffect(() => {
    let live = true;
    setBoardState("loading");
    setPage(0);
    const t = setTimeout(() => {
      fetchTradersPage({
        days, sort: pool, order: !isLocal && asc ? "asc" : "desc", page: 0, pageSize: PAGE_SIZE,
        search: search.trim() || undefined,
        marketQuery: topic.trim() || undefined,
        maxLastTradeHrs: activeOnly ? 24 : 0,
        minTrades: minPerDay > 0 ? Math.ceil(minPerDay * days) : undefined,
      })
        .then((r) => {
          if (!live) return;
          setTraders(r.traders ?? []);
          setBoardTotal(r.total ?? 0);
          setBoardState(r.cold ? "cold" : "ready");
        })
        .catch(() => live && setBoardState("error"));
    }, 300);
    return () => { live = false; clearTimeout(t); };
  }, [days, pool, search, topic, activeOnly, minPerDay, isLocal, asc]);

  const loadMore = useCallback(() => {
    if (loadingMore || traders.length >= boardTotal) return;
    const nextPage = page + 1;
    setLoadingMore(true);
    fetchTradersPage({
      days, sort: pool, order: !isLocal && asc ? "asc" : "desc", page: nextPage, pageSize: PAGE_SIZE,
      search: search.trim() || undefined,
      marketQuery: topic.trim() || undefined,
      maxLastTradeHrs: activeOnly ? 24 : 0,
      minTrades: minPerDay > 0 ? Math.ceil(minPerDay * days) : undefined,
    })
      .then((r) => {
        setTraders((prev) => [...prev, ...(r.traders ?? [])]);
        setBoardTotal(r.total ?? 0);
        setPage(nextPage);
        setLoadingMore(false);
      })
      .catch(() => setLoadingMore(false));
  }, [loadingMore, traders.length, boardTotal, page, days, pool, isLocal, asc, search, topic, activeOnly, minPerDay]);

  // ── Highlight ──
  const [hovered, setHovered] = useState<string | null>(null);
  const [pinned, setPinned] = useState<string | null>(null);
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onHover = (addr: string | null) => {
    if (hoverTimer.current) clearTimeout(hoverTimer.current);
    hoverTimer.current = setTimeout(() => setHovered(addr), addr ? HOVER_SETTLE_MS : 0);
  };
  useEffect(() => () => { if (hoverTimer.current) clearTimeout(hoverTimer.current); }, []);

  const focus = hovered ?? pinned ?? legs[0]?.address ?? null;
  const focusLeg = legs.find((l) => l.address === focus) ?? null;
  const [previewUsd, setPreviewUsd] = useState(DEFAULT_LEG_USD);
  const focusAsLeg: StudioLeg | null = focus
    ? focusLeg ?? { address: focus, usd: previewUsd, marketQuery: topic.trim() }
    : null;

  // ── History + replays ──
  // Every board row is replayed in the background (that's what fills IF
  // COPIED and the STEADY score); the focused row and the allocation jump
  // the queue so pointing at someone still answers right away.
  const urgentAddrs = useMemo(
    () => [...(focus ? [focus] : []), ...legs.map((l) => l.address)],
    [legs, focus],
  );
  const boardAddrs = useMemo(() => traders.map((t) => t.address), [traders]);
  const { feeds, loaded } = useTraderFeeds(boardAddrs, urgentAddrs);

  const previewCache = useRef(new Map<string, Sleeve>());
  const preview = useMemo<Sleeve | null>(() => {
    if (!focusAsLeg || !loaded.has(focusAsLeg.address)) return null;
    const key = previewKey(focusAsLeg, days);
    const hit = previewCache.current.get(key);
    if (hit) return hit;
    const s = simulateTrader(focusAsLeg, feeds, days);
    previewCache.current.set(key, s);
    return s;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusAsLeg?.address, focusAsLeg?.usd, focusAsLeg?.marketQuery, days, loaded, feeds]);

  const legsReady = legs.filter((l) => loaded.has(l.address));
  const run = useMemo(
    () => (legsReady.length > 0 ? simulateAllocation(legsReady, feeds, days) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [legsReady.map((l) => previewKey(l, days)).join(","), feeds, days],
  );
  const sleeveBy = useMemo(() => new Map((run?.sleeves ?? []).map((s) => [s.address, s])), [run]);

  // Each row at the size you'd give it (its allocation $, else the preview $).
  const rowLeg = useCallback(
    (a: string): StudioLeg => legs.find((l) => l.address === a) ?? { address: a, usd: previewUsd, marketQuery: topic.trim() },
    [legs, previewUsd, topic],
  );
  const rowSims = useMemo(() => {
    const out = new Map<string, Sleeve>();
    for (const t of traders) {
      const a = t.address.toLowerCase();
      if (!loaded.has(a)) continue;
      const leg = rowLeg(a);
      const key = previewKey(leg, days);
      let sim = previewCache.current.get(key);
      if (!sim) {
        sim = simulateTrader(leg, feeds, days);
        previewCache.current.set(key, sim);
      }
      out.set(a, sim);
    }
    return out;
  }, [traders, loaded, feeds, days, rowLeg]);

  // ── STEADY score: disclosed, and the weights are yours ──
  const [scoreW, setScoreW] = useState<ScoreWeights>(DEFAULT_SCORE_WEIGHTS);
  const [showScore, setShowScore] = useState(false);
  useEffect(() => {
    try { setScoreW(parseWeights(localStorage.getItem(SCORE_STORAGE_KEY))); } catch {}
  }, []);
  const setWeight = (k: keyof ScoreWeights, v: number) => setScoreW((w) => {
    const next = cleanWeights({ ...w, [k]: v });
    try { localStorage.setItem(SCORE_STORAGE_KEY, JSON.stringify(next)); } catch {}
    return next;
  });
  const resetWeights = () => {
    setScoreW(DEFAULT_SCORE_WEIGHTS);
    try { localStorage.removeItem(SCORE_STORAGE_KEY); } catch {}
  };
  const scores = useMemo(() => {
    const out = new Map<string, ReturnType<typeof steadyScore>>();
    rowSims.forEach((sim, a) => out.set(a, steadyScore(sleeveBy.get(a) ?? sim, scoreW)));
    return out;
  }, [rowSims, sleeveBy, scoreW]);

  // While the pointer is on the board the order holds still — rows sliding
  // under the cursor as scores land would change who you're looking at.
  const [boardHot, setBoardHot] = useState(false);
  const heldOrder = useRef<string[]>([]);
  const rows = useMemo(() => {
    if (!isLocal) return traders;
    if (boardHot && heldOrder.current.length > 0) {
      const at = new Map(heldOrder.current.map((a, i) => [a, i]));
      return [...traders].sort((x, y) =>
        (at.get(x.address.toLowerCase()) ?? 1e9) - (at.get(y.address.toLowerCase()) ?? 1e9));
    }
    const keyOf = (t: TopTrader): number | null => {
      const a = t.address.toLowerCase();
      if (sort === "steady") return scores.get(a)?.score ?? null;
      const sim = sleeveBy.get(a) ?? rowSims.get(a);
      return sim && sim.trades > 0 ? sim.pct : null;
    };
    // Unreplayed rows stay at the bottom either way.
    return rankByScore(traders, (t) => { const v = keyOf(t); return v == null ? null : asc ? -v : v; });
  }, [traders, scores, rowSims, sleeveBy, sort, asc, isLocal, boardHot]);
  useEffect(() => { heldOrder.current = rows.map((t) => t.address.toLowerCase()); }, [rows]);
  const replayed = traders.filter((t) => loaded.has(t.address.toLowerCase())).length;
  const shares = weights(legs);
  const allocated = totalUsd(legs);

  // ── Edits ──
  const setDays = (d: number) => update((s) => ({ ...s, days: d }));
  const add = (addr: string) => {
    const usd = addr === focus ? previewUsd : DEFAULT_LEG_USD;
    update((s) => ({ ...s, legs: addLeg(s.legs, { address: addr, usd, marketQuery: topic.trim() }) }));
  };
  const remove = (addr: string) => update((s) => ({ ...s, legs: removeLeg(s.legs, addr) }));
  const resize = (addr: string, usd: number) => update((s) => ({ ...s, legs: setLegUsd(s.legs, addr, usd) }));
  const inLegs = (addr: string) => legs.some((l) => l.address === addr.toLowerCase());

  // ── Commit ──
  const [committing, setCommitting] = useState(false);
  const [commitMsg, setCommitMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const copyAll = async () => {
    const funded = legs.filter((l) => l.usd > 0);
    if (funded.length === 0 || committing) return;
    setCommitting(true);
    setCommitMsg(null);
    try {
      for (const l of funded) {
        await upsertAllocation(
          { address: l.address, allocationUsd: l.usd, params: { marketQuery: l.marketQuery ?? "" } },
          eoa,
        );
      }
      window.dispatchEvent(new Event(COPY_BOOK_CHANGED_EVENT));
      setCommitMsg({ ok: true, text: `${funded.length} trader${funded.length === 1 ? "" : "s"} added to your copy desk (${fmtUsd(totalUsd(funded))}). They start in DRY RUN.` });
    } catch (e) {
      setCommitMsg({ ok: false, text: e instanceof Error ? e.message : "could not save — are you signed in?" });
    } finally {
      setCommitting(false);
    }
  };

  const pv = verdict(preview, days);

  return (
    <div className="space-y-3">
      {/* ── Header: the one sentence + window ── */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex-1 min-w-[16rem]">
          <h1 className="font-mono text-[15px] tracking-[0.16em] text-pixel-white">COPY TRADERS</h1>
          <p className="font-mono text-[10.5px] text-pixel-gray mt-0.5">
            Every trader below is backtested for you and ranked by how steady copying them would have been. Point at one for the full replay, add the ones you like, then copy them all.
          </p>
        </div>
        <div className="ml-auto flex shrink-0 items-center gap-2">
          <span className="font-mono text-[9.5px] tracking-[0.14em] text-pixel-gray">BACKTEST</span>
          {STUDIO_WINDOWS.map((d) => (
            <button
              key={d}
              type="button"
              onClick={() => setDays(d)}
              className={`pixel-btn btn-xs ${d === days ? "border-pixel-green text-pixel-green" : ""}`}
            >
              {d}D
            </button>
          ))}
          <Link href="/strats" className="pixel-btn btn-xs ml-2" title="Build your own strategy instead of copying people">
            MY STRATS →
          </Link>
        </div>
      </div>

      {/* ── The BUNDLER agent's proposals; LOAD replaces the allocation ── */}
      {!simple && <BundlerPanel
        onLoad={(ls) => update((s) => ({
          ...s,
          legs: ls.reduce<StudioLeg[]>((acc, l) => addLeg(acc, { address: l.address, usd: l.usd, label: l.label ?? null }), []),
        }))}
      />}

      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_460px]">
        {/* ── Left: the board ── */}
        <section className="border border-pixel-border rounded-[var(--radius-sm)] min-w-0">
          <div className="flex flex-wrap items-center gap-2 p-2 border-b border-pixel-border">
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="find a trader (name or 0x…)"
              className="pixel-input flex-1 min-w-[140px] text-[11px]"
            />
            <button
              type="button"
              onClick={toggleSimple}
              className={`pixel-btn btn-xs ${simple ? "" : "border-pixel-green text-pixel-green"}`}
              title="Sorting, filters, market topics, score weights, agent bundles — the full studio"
            >
              {simple ? "MORE ▾" : "LESS ▴"}
            </button>
            {!simple && <>
            <input
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="only these markets (e.g. bitcoin, nba)"
              title="Ranks traders on these markets only — and anyone you add from here is copied only in them"
              className="pixel-input flex-1 min-w-[140px] text-[11px]"
            />
            {SORTS.map((s) => (
              <button
                key={s.key}
                type="button"
                onClick={() => pickSort(s.key)}
                className={`pixel-btn btn-xs ${sort === s.key ? "border-pixel-green text-pixel-green" : ""}`}
                title={s.key === "steady" ? "Most consistent to copy first — scored from each trader's copy backtest. Press HOW IT'S SCORED to see and change the mix."
                  : s.key === "pct" ? "Biggest % return if you had copied them (the IF COPIED column) — fair across big and small traders"
                  : "Press again to flip the order"}
              >
                {s.label}{sort === s.key ? (asc ? " ↑" : " ↓") : ""}
              </button>
            ))}
            <button
              type="button"
              onClick={() => setActiveOnly((v) => !v)}
              className={`pixel-btn btn-xs ${activeOnly ? "border-pixel-green text-pixel-green" : ""}`}
              title="Only traders who traded in the last 24 hours — a quiet trader gives you nothing to copy"
            >
              ACTIVE 24H
            </button>
            <label
              className={`flex items-center gap-1 font-mono text-[9.5px] tracking-[0.12em] ${minPerDay > 0 ? "text-pixel-green" : "text-pixel-gray"}`}
              title={`Only traders who averaged at least this many trades a day over the ${days}D window — drops the low-frequency ones. 0 = off.`}
            >
              MIN
              <input
                type="number" min={0} step={1}
                value={minPerDay || ""}
                placeholder="N"
                onChange={(e) => setMinPerDay(Number(e.target.value))}
                className="pixel-input w-12 text-[11px] py-0"
                aria-label="minimum trades per day"
              />
              TRADES/DAY
            </label>
            <button
              type="button"
              onClick={() => setShowScore((v) => !v)}
              className={`pixel-btn btn-xs ${showScore ? "border-pixel-green text-pixel-green" : ""}`}
              title="What the STEADY score is made of — and change how much each part counts"
            >
              HOW IT&apos;S SCORED
            </button>
            </>}
          </div>

          {!simple && showScore && (
            <div className="p-2 border-b border-pixel-border space-y-1.5 font-mono">
              <p className="text-[10px] text-pixel-gray leading-[1.5]">
                STEADY (0–100) ranks traders by how consistent copying them would have been over the last {days}D at
                your size — not by how much they made. Each part is read off the same backtest as IF COPIED, scored 0–100,
                then blended by the weights below. Defaults favour a smooth, shallow equity line because that&apos;s what tends
                to carry forward; past return is the least repeatable part.
              </p>
              <div className="grid gap-x-4 gap-y-1 sm:grid-cols-2">
                {SCORE_PARTS.map((k) => (
                  <label key={k} className="flex items-center gap-2 text-[10px]" title={SCORE_PART_HINT[k]}>
                    <span className="w-16 tracking-[0.1em] text-pixel-white">{SCORE_PART_LABEL[k]}</span>
                    <input
                      type="range" min={0} max={100} step={5}
                      value={scoreW[k]}
                      onChange={(e) => setWeight(k, Number(e.target.value))}
                      className="flex-1 accent-green-400"
                    />
                    <span className="w-8 text-right text-pixel-gray-light">{scoreW[k]}</span>
                  </label>
                ))}
              </div>
              <ul className="text-[9.5px] text-pixel-gray leading-[1.5] space-y-0.5">
                {SCORE_PARTS.map((k) => (
                  <li key={k}><span className="text-pixel-gray-light">{SCORE_PART_LABEL[k]}</span> — {SCORE_PART_HINT[k]}</li>
                ))}
              </ul>
              <div className="flex items-center gap-2 text-[9.5px] text-pixel-gray">
                <span>Hover a score to see its parts. Saved on this device.</span>
                <button type="button" onClick={resetWeights} className="pixel-btn btn-xs ml-auto">RESET</button>
              </div>
            </div>
          )}

          <div className="overflow-x-auto">
            <table className="w-full font-mono text-[11px]">
              <thead>
                <tr className="text-[9px] tracking-[0.14em] text-pixel-gray text-left">
                  <th className="px-2 py-1.5 w-8" />
                  <th className="px-2 py-1.5">TRADER</th>
                  <SortTh k="usd" sort={sort} asc={asc} onPick={pickSort}>PROFIT {days}D</SortTh>
                  {!simple && <>
                  <SortTh k="volume" sort={sort} asc={asc} onPick={pickSort}>VOLUME</SortTh>
                  <th className="px-2 py-1.5 text-right" title={`Average trades per day over the ${days}D window`}>TRADES/D</th>
                  <th className="px-2 py-1.5 text-right">WIN</th>
                  <th className="px-2 py-1.5 text-right">LAST</th>
                  </>}
                  <th className="px-2 py-1.5">CURVE</th>
                  <SortTh k="pct" sort={sort} asc={asc} onPick={pickSort} title="% return if you had copied them — click to sort">IF COPIED</SortTh>
                  <SortTh k="steady" sort={sort} asc={asc} onPick={pickSort} title="0–100: how consistent copying them would have been. HOW IT'S SCORED shows the mix. Click to sort.">STEADY</SortTh>
                </tr>
              </thead>
              <tbody
                onMouseEnter={() => setBoardHot(true)}
                onMouseLeave={() => { setBoardHot(false); onHover(null); }}
              >
                {rows.map((t) => {
                  const a = t.address.toLowerCase();
                  const picked = inLegs(a);
                  const isFocus = a === focus;
                  const sim = sleeveBy.get(a) ?? rowSims.get(a);
                  const sc = scores.get(a);
                  return (
                    <tr
                      key={a}
                      onMouseEnter={() => onHover(a)}
                      onClick={() => setPinned((p) => (p === a ? null : a))}
                      className={`cursor-pointer border-t border-pixel-border/60 ${
                        isFocus ? "bg-green-400/10" : "hover:bg-pixel-white/[0.04]"
                      }`}
                    >
                      <td className="px-2 py-1">
                        <button
                          type="button"
                          onClick={(e) => { e.stopPropagation(); if (picked) { remove(a); } else { add(a); } }}
                          title={picked ? "Remove from your allocation" : "Add to your allocation"}
                          className={`pixel-btn btn-xs w-6 ${picked ? "border-pixel-green text-pixel-green" : ""}`}
                        >
                          {picked ? "✓" : "+"}
                        </button>
                      </td>
                      <td className="px-2 py-1 text-pixel-white">
                        {shortAddress(a)}
                        {pinned === a && <span className="ml-1 text-[9px] text-pixel-green">PINNED</span>}
                      </td>
                      <td className={`px-2 py-1 text-right ${toneOf(t.pnl)}`}>{fmtUsd(t.pnl, true)}</td>
                      {!simple && <>
                      <td className="px-2 py-1 text-right text-pixel-gray-light">{formatVolume(t.volume)}</td>
                      <td className="px-2 py-1 text-right text-pixel-gray-light" title={`${t.recentTrades} trades in ${days}D · ${t.trades24h ?? 0} in the last 24h`}>
                        {fmtPerDay(t.recentTrades / Math.max(1, days))}
                      </td>
                      <td className="px-2 py-1 text-right text-pixel-gray-light">
                        {t.winRate >= 0 ? `${Math.round(t.winRate)}%` : "—"}
                      </td>
                      <td className="px-2 py-1 text-right text-pixel-gray">
                        {t.lastTradeTs ? timeAgo(t.lastTradeTs * 1000) : "—"}
                      </td>
                      </>}
                      <td className="px-2 py-1">
                        {t.pnlCurve && t.pnlCurve.length > 1 ? <Sparkline data={t.pnlCurve} width={70} height={18} /> : null}
                      </td>
                      <td className={`px-2 py-1 text-right ${sim ? (sim.trades > 0 ? toneOf(sim.net) : "text-amber-400") : "text-pixel-gray"}`}>
                        {sim ? (sim.trades > 0 ? fmtPct(sim.pct) : "idle") : "·"}
                      </td>
                      <td
                        className={`px-2 py-1 text-right ${sc?.score == null ? "text-pixel-gray" : sc.score >= 60 ? "text-green-400" : sc.score >= 40 ? "text-pixel-gray-light" : "text-red-400"}`}
                        title={sc ? explainScore(sc, scoreW) : "replaying…"}
                      >
                        {sc?.score != null ? Math.round(sc.score) : sim ? "—" : "·"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {traders.length === 0 && (
              <p className="p-4 font-mono text-[11px] text-pixel-gray">
                {boardState === "loading" ? "loading traders…"
                  : boardState === "cold" ? "the leaderboard is still warming up — try again in a minute"
                  : boardState === "error" ? "could not reach the leaderboard"
                  : minPerDay > 0 ? `no traders match — nobody here averages ${minPerDay}+ trades a day; lower MIN TRADES/DAY`
                  : "no traders match — clear the search, or turn off ACTIVE 24H"}
              </p>
            )}
            {traders.length > 0 && (
              <p className="px-2 py-1.5 font-mono text-[9.5px] text-pixel-gray border-t border-pixel-border/60">
                top {traders.length} of {boardTotal.toLocaleString()} · each row backtested at {fmtUsd(previewUsd)}
                {replayed < traders.length ? ` · replaying ${replayed}/${traders.length}…` : ""}
                {minPerDay > 0 ? ` · ${minPerDay}+ trades/day` : ""}
                {sort === "steady" ? " · ranked by STEADY" : sort === "pct" ? " · ranked by % return if copied" : ""}
              </p>
            )}
            {boardState === "ready" && traders.length < boardTotal && (
              <div className="flex justify-center py-2 border-t border-pixel-border/60">
                <button
                  type="button"
                  onClick={loadMore}
                  disabled={loadingMore}
                  className="pixel-btn btn-xs"
                >
                  {loadingMore ? "LOADING…" : `LOAD MORE (${traders.length} / ${boardTotal.toLocaleString()})`}
                </button>
              </div>
            )}
          </div>
        </section>

        {/* ── Right: the highlighted trader, then the allocation ── */}
        <aside className="space-y-3 min-w-0 lg:sticky lg:top-2 lg:self-start lg:max-h-[calc(100vh-1rem)] lg:overflow-y-auto">
          <section className="border border-pixel-border rounded-[var(--radius-sm)] p-3 space-y-2">
            {!focus ? (
              <p className="font-mono text-[11px] text-pixel-gray">
                Point at a trader on the left to backtest copying them.
              </p>
            ) : (
              <>
                <div className="flex items-center justify-between gap-2">
                  <div className="font-mono text-[9.5px] tracking-[0.14em] text-pixel-gray">
                    IF YOU COPIED <span className="text-pixel-white">{shortAddress(focus)}</span> FOR {days}D
                  </div>
                  <Link href={`/traders/${focus}`} className="font-mono text-[9.5px] text-pixel-gray hover:text-pixel-white">
                    PROFILE →
                  </Link>
                </div>
                <div className="flex items-center gap-2 font-mono text-[11px]">
                  <span className="text-pixel-gray">WITH $</span>
                  <input
                    type="number"
                    min={0}
                    value={focusLeg ? focusLeg.usd : previewUsd}
                    onChange={(e) => {
                      const v = Number(e.target.value);
                      if (focusLeg) resize(focusLeg.address, v);
                      else setPreviewUsd(Number.isFinite(v) && v > 0 ? v : 0);
                    }}
                    className="pixel-input w-24 text-[11px]"
                  />
                  {inLegs(focus) ? (
                    <button type="button" onClick={() => remove(focus)} className="pixel-btn btn-xs ml-auto">
                      REMOVE
                    </button>
                  ) : (
                    <button type="button" onClick={() => add(focus)} className="pixel-btn btn-xs border-pixel-green text-pixel-green ml-auto">
                      + ADD TO ALLOCATION
                    </button>
                  )}
                </div>
                <p className={`font-mono text-[11.5px] ${TONE[pv.tone]}`}>{pv.text}</p>
                {preview && preview.trades > 0 && (
                  <>
                    <div className="grid grid-cols-4 gap-2 font-mono text-[10px]">
                      <Stat label="MAX DROP" value={`${preview.drawdown.toFixed(1)}%`} />
                      <Stat label="COPIED" value={`${preview.executed}/${preview.observed}`} hint="their buys you would have copied / their buys" />
                      <Stat label="FEES" value={fmtUsd(preview.fees)} />
                      <Stat label="SETTLED" value={`${Math.round(preview.confidence * 100)}%`} hint="share of the result from markets that actually resolved, vs. marked at last price" />
                    </div>
                    <div>
                      <EquityChart history={preview.equity} markers={preview.markers} timeControls={false} />
                    </div>
                  </>
                )}
              </>
            )}
          </section>

          <section className="border border-pixel-border rounded-[var(--radius-sm)] p-3 space-y-2">
            <div className="flex items-center justify-between">
              <div className="font-mono text-[9.5px] tracking-[0.14em] text-pixel-gray">
                YOUR ALLOCATION · {legs.length} TRADER{legs.length === 1 ? "" : "S"} · {fmtUsd(allocated)}
              </div>
              {legs.length > 0 && (
                <button type="button" onClick={() => update((s) => ({ ...s, legs: [] }))} className="font-mono text-[9px] text-pixel-gray hover:text-pixel-white">
                  CLEAR
                </button>
              )}
            </div>

            {legs.length === 0 ? (
              <p className="font-mono text-[11px] text-pixel-gray">Press + on a trader to start an allocation.</p>
            ) : (
              <>
                <div className="space-y-1">
                  {legs.map((l) => {
                    const s = sleeveBy.get(l.address);
                    const v = loaded.has(l.address) ? verdict(s ?? null, days) : verdict(null, days);
                    return (
                      <div
                        key={l.address}
                        onMouseEnter={() => onHover(l.address)}
                        onMouseLeave={() => onHover(null)}
                        className={`flex items-center gap-2 font-mono text-[11px] px-1 py-0.5 rounded ${l.address === focus ? "bg-green-400/10" : ""}`}
                      >
                        <span className="w-[92px] text-pixel-white truncate" title={l.address}>
                          {shortAddress(l.address)}
                        </span>
                        <span className="text-pixel-gray">$</span>
                        <input
                          type="number"
                          min={0}
                          value={l.usd}
                          onChange={(e) => resize(l.address, Number(e.target.value))}
                          className="pixel-input w-20 text-[11px]"
                        />
                        <span className="w-10 text-right text-pixel-gray">{Math.round((shares.get(l.address) ?? 0) * 100)}%</span>
                        <span
                          className={`flex-1 text-right ${s && s.trades > 0 ? toneOf(s.net) : TONE[v.tone]}`}
                          title={v.text}
                        >
                          {s ? (s.trades > 0 ? `${fmtUsd(s.net, true)} ${fmtPct(s.pct)}` : "idle") : l.usd > 0 ? "…" : "$0"}
                        </span>
                        <button type="button" onClick={() => remove(l.address)} className="text-pixel-gray hover:text-red-400 px-1" title="Remove">
                          ×
                        </button>
                      </div>
                    );
                  })}
                </div>

                <div className="flex flex-wrap items-center gap-1.5 pt-1 border-t border-pixel-border/60">
                  <span className="font-mono text-[9.5px] text-pixel-gray">SPLIT $</span>
                  <input
                    type="number"
                    min={0}
                    value={bankroll}
                    onChange={(e) => update((s) => ({ ...s, bankroll: Math.max(0, Number(e.target.value) || 0) }))}
                    className="pixel-input w-24 text-[11px]"
                  />
                  <button type="button" className="pixel-btn btn-xs" onClick={() => update((s) => ({ ...s, legs: splitEqual(s.legs, s.bankroll) }))} title="Same amount on everyone">
                    EQUAL
                  </button>
                  <button type="button" className="pixel-btn btn-xs" onClick={() => update((s) => ({ ...s, legs: fitToBankroll(s.legs, s.bankroll) }))} title="Keep your proportions, scale to this total">
                    KEEP RATIOS
                  </button>
                  <button
                    type="button"
                    className="pixel-btn btn-xs"
                    disabled={!run}
                    onClick={() => update((s) => ({ ...s, legs: splitByResult(s.legs, s.bankroll, sleeveBy) }))}
                    title="More money to whoever did best in this backtest; $0 to losers and idle traders. Past results — use with care."
                  >
                    BY RESULT
                  </button>
                </div>

                {run && (
                  <div className="space-y-1.5 pt-1 border-t border-pixel-border/60">
                    <div className="flex flex-wrap items-baseline gap-x-3 font-mono text-[11px]">
                      <span className="text-pixel-gray text-[9.5px] tracking-[0.14em]">ALL TOGETHER, {days}D</span>
                      <span className={toneOf(run.portfolio.net)}>
                        {fmtUsd(run.portfolio.net, true)} ({fmtPct(run.portfolio.pct)})
                      </span>
                      <span className="text-pixel-gray">max drop {run.portfolio.drawdown.toFixed(1)}%</span>
                      <span className="text-pixel-gray">{run.portfolio.trades} trades</span>
                    </div>
                    {run.portfolio.idleUsd > 0 && (
                      <p className="font-mono text-[10px] text-amber-400">
                        {fmtUsd(run.portfolio.idleUsd)} sat idle — {run.portfolio.legs - run.portfolio.legsTrading} trader(s) placed nothing at their size. Try more $ on them, or swap them out.
                      </p>
                    )}
                    {legsReady.length < legs.length && (
                      <p className="font-mono text-[10px] text-pixel-gray">loading {legs.length - legsReady.length} trader(s)…</p>
                    )}
                    <div>
                      <EquityChart history={run.portfolio.equity} markers={run.portfolio.markers} timeControls={false} />
                    </div>
                  </div>
                )}

                <div className="flex items-center gap-2 pt-1">
                  <button
                    type="button"
                    onClick={copyAll}
                    disabled={committing || allocated <= 0}
                    className="pixel-btn border-pixel-green text-pixel-green flex-1"
                  >
                    {committing ? "SAVING…" : `COPY ALL · ${fmtUsd(allocated)}`}
                  </button>
                  <Link href="/copy" className="pixel-btn btn-xs" title="Start, stop and watch the traders you copy">
                    DESK →
                  </Link>
                </div>
                {commitMsg && (
                  <p className={`font-mono text-[10px] ${commitMsg.ok ? "text-green-400" : "text-red-400"}`}>
                    {commitMsg.text}
                    {commitMsg.ok && <> <Link href="/copy" className="underline">Open the desk</Link> to start them.</>}
                  </p>
                )}
                <p className="font-mono text-[9px] text-pixel-gray/80 leading-[1.5]">
                  Backtests replay each trader&apos;s real fills with the same sizing, fees and order floor the live engine uses. Each trader trades only their own amount. Past results don&apos;t guarantee anything.
                </p>
              </>
            )}
          </section>
        </aside>
      </div>
    </div>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div title={hint}>
      <div className="text-[8.5px] tracking-[0.12em] text-pixel-gray">{label}</div>
      <div className="text-pixel-white">{value}</div>
    </div>
  );
}

/** A header you can click to sort by; the active one shows its direction. */
function SortTh({ k, sort, asc, onPick, title, children }: {
  k: string; sort: string; asc: boolean; onPick: (k: string) => void; title?: string; children: React.ReactNode;
}) {
  const on = sort === k;
  return (
    <th
      onClick={() => onPick(k)}
      title={title ?? "Click to sort"}
      className={`px-2 py-1.5 text-right cursor-pointer select-none hover:text-pixel-white ${on ? "text-pixel-green" : ""}`}
    >
      {children}{on ? (asc ? " ↑" : " ↓") : ""}
    </th>
  );
}
