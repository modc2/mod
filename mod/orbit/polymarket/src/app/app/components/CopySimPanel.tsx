"use client";

// SIMULATE THE COPY — "if I put $N behind this trader, what happens?"
//
// This is the question the console existed to answer and could not answer
// until you had already committed: a backtest only appeared at /copy/<addr>,
// which is a view of a copy-book ROW, which means you had to add the leader —
// spend the decision — before you could see what copying them would have done.
//
// So the replay runs here, on the profile, before any money is named. And it
// runs through the SAME pipeline the desk and the live engine use, not a
// lookalike:
//
//   allocation → identityStrat() → stratFromIndex() → runBacktest()
//
// `identityStrat` is the materialization `api/src/copy.rs` mirrors (pinned by
// identity.fixture.json), `runBacktest` is the function the hub cards and the
// BACKTEST tab call. What you size here is what the engine would size, under
// the gate the filter rail above is showing, with the knobs this panel writes
// straight onto the allocation when you press COPY.
//
// Two honesty rules this panel will not bend:
//
//   • The LADDER is the headline, not the single number. A copy is not linear
//     in N: the order floor (`clobMinNotional`) eats small sizes, `maxTrade`
//     and `maxOpenPositions` cap big ones, and proportional sizing divides by
//     the leader's own bankroll or flow. $50 and $500 behind the same trader
//     are different strategies, and the table says so.
//   • Every result reports how much of it is FACT. Copy replays value leftover
//     inventory at the last price a leader printed unless the market's
//     resolution is known — and leaders ride winners out and let losers expire,
//     so a mostly-MARKED number is biased upward. `settlement` rides along.

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";

import { runBacktest, stratFromIndex, stratBacktestParams, type BacktestResult } from "../lib/backtest";
import {
  identityStrat, type Allocation, type AllocationParams,
  IDENTITY_MIN_TRADE, IDENTITY_MAX_TRADE, IDENTITY_MAX_OPEN_POSITIONS,
  IDENTITY_STOP_LOSS, IDENTITY_TAKE_PROFIT, IDENTITY_SIZING, IDENTITY_TURNOVER,
  IDENTITY_MIN_MINUTES_TO_CLOSE, IDENTITY_MAX_TRADE_AGE_SEC,
} from "../lib/identityStrat";
import { fetchTraderBankrolls } from "../lib/liveSessions";
import { fetchResolvedLegs } from "../lib/hubCache";
import { describeMarketQuery } from "../lib/marketTypes";
import { addToDraft, inDraft } from "../lib/basketDraft";
import { marketMatchesQuery } from "../lib/marketQuery";
import { settlementConfidence } from "../lib/backtest";
import type { PolymarketPosition, PolymarketTrade, SavedIndex, TradeFilters, SizingModel } from "../lib/types";
import EquityChart from "./EquityChart";

/** The sizes the ladder always reports, plus whatever you typed. Chosen to
    straddle the order floor: below ~$25 a proportional mirror of most leaders
    lands under `clobMinNotional` and simply doesn't place. */
const LADDER = [10, 25, 50, 100, 250, 500, 1000, 2500];

const AMOUNT_PRESETS = [25, 100, 500, 1000];

interface Props {
  address: string;
  /** The trader's synced fills — the same array the profile renders. */
  trades: PolymarketTrade[];
  positions: PolymarketPosition[];
  /** The profile's lookback. The replay window IS the window you're reading. */
  days: number;
  /** The filter rail's topic gate — the copy runs under it too. */
  marketQuery: string;
  /** Side / price / size / category dimensions from the rail, when set. */
  tradeFilters?: TradeFilters | null;
  /** Rail keywords, which are a TAPE filter and not an engine gate — the panel
      says so rather than simulating something the engine can't enforce. */
  keywords?: string[];
  onUseKeywordsAsTopic?: () => void;
  loading?: boolean;
  /** Their record only reaches back as far as the feed served — every number
      here inherits that ceiling. */
  feedDepthCapped?: boolean;
  deskAllocationUsd?: number | null;
  /** Commit the simulated configuration: amount + the knobs + the gate. */
  onCopyToDesk?: (allocationUsd: number, params: AllocationParams) => void | Promise<void>;
}

function fmtUsd(v: number): string {
  const s = Math.abs(v) >= 1000 ? Math.round(Math.abs(v)).toLocaleString("en-US") : Math.abs(v).toFixed(2);
  return `${v < 0 ? "−" : ""}$${s}`;
}

function fmtPct(v: number): string {
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(1)}%`;
}

/** The copy ratio, readably. Below 1 it's a percentage of the leader's trade;
    above 1 your capital dwarfs what they deployed and "7575%" reads as a bug,
    so say ×75.8 — and the sentence around it explains the clamp. */
function fmtRatio(r: number): string {
  return r >= 1 ? `×${r.toFixed(1)}` : `${(r * 100).toFixed(2)}%`;
}

function toneClass(v: number): string {
  return v > 0 ? "text-green-400" : v < 0 ? "text-red-400" : "text-pixel-white";
}

/** Peak-to-trough of the simulated equity curve, as a % of the peak. */
function maxDrawdown(history: { liq: number; pos: number }[]): number {
  let peak = 0;
  let worst = 0;
  for (const p of history) {
    const eq = p.liq + p.pos;
    if (eq > peak) peak = eq;
    if (peak > 0) worst = Math.min(worst, (eq - peak) / peak);
  }
  return worst * 100;
}

export default function CopySimPanel({
  address,
  trades,
  positions,
  days,
  marketQuery,
  tradeFilters = null,
  keywords = [],
  onUseKeywordsAsTopic,
  loading = false,
  feedDepthCapped = false,
  deskAllocationUsd = null,
  onCopyToDesk,
}: Props) {
  const [amountStr, setAmountStr] = useState("100");
  // Debounced so typing "1000" doesn't run four replays.
  const [amount, setAmount] = useState(100);
  useEffect(() => {
    const n = Number(amountStr);
    const t = setTimeout(() => setAmount(Number.isFinite(n) && n > 0 ? n : 0), 250);
    return () => clearTimeout(t);
  }, [amountStr]);

  const [showKnobs, setShowKnobs] = useState(false);
  const [showLadder, setShowLadder] = useState(true);
  const [busy, setBusy] = useState(false);
  const [added, setAdded] = useState(false);
  // Shortlisted, not committed. One profile answers "what would $N behind THIS
  // trader have done"; the basket answers "and how does that compare with the
  // other five I like, if I split $2,000 across them" — so the amount and the
  // gate you just simulated ride over there instead of being retyped.
  const [basketed, setBasketed] = useState(false);
  useEffect(() => { setBasketed(inDraft(address)); }, [address]);

  // The knobs the copy would run under. Defaults ARE the identity template —
  // edit them and both the replay and the allocation you commit change
  // together, so you can never save a configuration you didn't simulate.
  const [minTrade, setMinTrade] = useState(IDENTITY_MIN_TRADE);
  const [maxTrade, setMaxTrade] = useState(IDENTITY_MAX_TRADE);
  const [maxOpen, setMaxOpen] = useState(IDENTITY_MAX_OPEN_POSITIONS);
  const [sizing, setSizing] = useState<SizingModel>(IDENTITY_SIZING);
  const [turnover, setTurnover] = useState(IDENTITY_TURNOVER);
  const [stopLoss, setStopLoss] = useState(IDENTITY_STOP_LOSS);
  const [takeProfit, setTakeProfit] = useState(IDENTITY_TAKE_PROFIT);
  const [minToClose, setMinToClose] = useState(IDENTITY_MIN_MINUTES_TO_CLOSE);
  const [maxAgeSec, setMaxAgeSec] = useState(IDENTITY_MAX_TRADE_AGE_SEC);

  const params = useMemo((): AllocationParams => ({
    minTrade, maxTrade, maxOpenPositions: maxOpen, sizing, turnover,
    stopLoss, takeProfit, minMinutesToClose: minToClose, maxTradeAgeSec: maxAgeSec,
    marketQuery: marketQuery.trim(),
  }), [minTrade, maxTrade, maxOpen, sizing, turnover, stopLoss, takeProfit,
       minToClose, maxAgeSec, marketQuery]);

  // ── The two things the replay needs from the server ──
  // The leader's bankroll is the denominator proportional sizing divides by
  // (`copyRatioFor`); without it the sim falls back to a volume model and
  // would size differently than the live session. Missing is survivable and
  // said out loud below.
  const [bankrolls, setBankrolls] = useState<Map<string, number>>(new Map());
  useEffect(() => {
    let live = true;
    void fetchTraderBankrolls([address]).then((b) => { if (live) setBankrolls(b); });
    return () => { live = false; };
  }, [address]);

  // How the window's markets actually paid out. See the file header — without
  // this the replay marks dead losers at their entry price.
  const windowConditionIds = useMemo(() => {
    const cutoff = Date.now() - days * 86400_000;
    const ids = new Set<string>();
    for (const t of trades) {
      if (t.timestamp >= cutoff && t.conditionId) ids.add(t.conditionId);
    }
    return [...ids].sort();
  }, [trades, days]);
  const [resolved, setResolved] = useState<Map<string, number>>(new Map());
  useEffect(() => {
    if (windowConditionIds.length === 0) { setResolved(new Map()); return; }
    let live = true;
    void fetchResolvedLegs(windowConditionIds).then((r) => { if (live) setResolved(r); });
    return () => { live = false; };
  }, [windowConditionIds.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── One replay, parameterized by capital ──
  const replay = useCallback(
    (capital: number): BacktestResult | null => {
      if (!(capital > 0) || loading) return null;
      const alloc: Allocation = {
        address, allocationUsd: capital, enabled: true, params,
        addedAt: Date.now(), updatedAt: Date.now(),
      };
      // The allocation as the engine would materialize it, plus the rail's
      // per-trade dimensions (which live on the strat, not the allocation).
      const idx: SavedIndex = {
        ...identityStrat(alloc),
        ...(tradeFilters ? { tradeFilters } : {}),
      };
      const p = stratBacktestParams(idx);
      return runBacktest({
        watchlist: [address],
        traderTrades: new Map([[address, trades]]),
        traderPositions: new Map([[address, positions]]),
        traderWeights: { [address]: 1 },
        traderBankrolls: bankrolls,
        strat: stratFromIndex(idx),
        days,
        capital,
        minTrade: p.minTrade,
        maxTrade: p.maxTrade,
        maxOpenPositions: p.maxOpenPositions,
        stopLossPct: p.stopLossPct,
        takeProfitFrac: p.takeProfitFrac,
        marketQuery: p.marketQuery,
        pollMinutes: p.pollMinutes,
        sizing: idx.sizing,
        turnover: idx.turnover,
        resolved,
        loading,
      });
    },
    [address, trades, positions, bankrolls, days, params, tradeFilters, resolved, loading],
  );

  const result = useMemo(() => replay(amount), [replay, amount]);
  const sim = result?.sim;

  const ladder = useMemo(() => {
    if (!showLadder || loading || trades.length === 0) return [];
    const sizes = [...new Set([...LADDER, ...(amount > 0 ? [amount] : [])])].sort((a, b) => a - b);
    return sizes.map((c) => {
      const r = replay(c);
      return {
        capital: c,
        net: r?.sim.netPnl ?? 0,
        pct: r && c > 0 ? (r.sim.netPnl / c) * 100 : 0,
        trades: r?.sim.rows.length ?? 0,
        executed: r?.sim.funnel.executed ?? 0,
        ratio: r?.copyRatio.get(address) ?? 0,
        skipped: r?.sim.skipped ?? 0,
      };
    });
  }, [showLadder, replay, amount, address, loading, trades.length]);

  // ── The denominator proportional sizing actually divides by ──
  // Recomputed here rather than read off the ratio, because the ratio alone
  // can't tell you WHY it is what it is. In flow mode it's the capital this
  // leader deployed in the window UNDER THE GATE (the same max(buy,sell) that
  // `computeCopyRatios` uses); in bankroll mode it's their net worth. When the
  // gate leaves them no flow at all the denominator floors at $1 and the ratio
  // goes to nonsense — which is a fact about the gate, not a size, and the
  // panel has to say that instead of "sized at 10000% of every trade".
  const leaderFlow = useMemo(() => {
    const cutoff = Date.now() - days * 86400_000;
    let buy = 0, sell = 0;
    for (const t of trades) {
      if (t.timestamp < cutoff) continue;
      if (!marketMatchesQuery(t.market, marketQuery)) continue;
      const v = t.price * t.size;
      if (t.side === "BUY") buy += v; else sell += v;
    }
    return Math.max(buy, sell);
  }, [trades, days, marketQuery]);

  const bankroll = bankrolls.get(address.toLowerCase());

  const stats = useMemo(() => {
    if (!sim) return null;
    const exits = sim.rows.filter((r) => r.side === "SELL");
    const wins = exits.filter((r) => r.realized > 0).length;
    const losses = exits.filter((r) => r.realized < 0).length;
    const conf = settlementConfidence(sim.settlement);
    return {
      wins, losses,
      winRate: wins + losses > 0 ? (wins / (wins + losses)) * 100 : -1,
      drawdown: maxDrawdown(sim.equityHistory),
      confidence: conf,
      endEquity: sim.cash + sim.posValue,
      pct: amount > 0 ? (sim.netPnl / amount) * 100 : 0,
      ratio: result?.copyRatio.get(address) ?? 0,
    };
  }, [sim, amount, result, address]);

  const gate = describeMarketQuery(marketQuery);
  const keywordsUnmodeled = keywords.length > 0;

  const commit = async () => {
    if (!onCopyToDesk || !(amount > 0)) return;
    setBusy(true);
    try {
      await onCopyToDesk(amount, params);
      setAdded(true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="pixel-panel">
      {/* ── Header: the amount, and the act ── */}
      <div className="flex items-center gap-2 flex-wrap px-3 py-2.5 border-b-2 border-pixel-border">
        <span className="text-[13px] text-pixel-white tracking-[0.14em]">SIMULATE THE COPY</span>
        <span className="text-[12px] text-pixel-gray">
          what this trader would have done with your money, over the last {days}D
          {marketQuery.trim() ? ` in ${gate}` : ""}
        </span>

        <span className="flex-1" />

        <span className="text-[12px] text-pixel-gray tracking-wider">COPY WITH $</span>
        <input
          className="pixel-input-sm w-24 font-mono text-[13px]"
          value={amountStr}
          inputMode="decimal"
          onChange={(e) => setAmountStr(e.target.value)}
          title="The dollar amount behind this leader — the same figure the copy book would hold"
        />
        {AMOUNT_PRESETS.map((p) => (
          <button
            key={p}
            onClick={() => setAmountStr(String(p))}
            className={`pixel-btn text-[12px] px-2 py-0.5 ${
              amount === p ? "border-green-400 text-green-400" : "border-pixel-border text-pixel-gray hover:text-pixel-white"
            }`}
          >
            ${p}
          </button>
        ))}
        <button
          onClick={() => setShowKnobs((s) => !s)}
          className={`pixel-btn text-[12px] px-2 py-0.5 ${
            showKnobs ? "border-green-400 text-green-400" : "border-pixel-border text-pixel-gray hover:text-pixel-white"
          }`}
          title="The engine knobs this replay ran under — editing them re-runs it, and they ride along when you copy"
        >
          ⚙ KNOBS
        </button>
      </div>

      {/* ── The knobs. Same fields `AllocationParams` carries, so what you
             simulate is literally what gets written to the copy book. ── */}
      {showKnobs && (
        <div className="px-3 py-2.5 border-b-2 border-pixel-border grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-x-4 gap-y-2 font-mono">
          {([
            { label: "MIN TRADE $", value: minTrade, set: setMinTrade, step: 1, hint: "Skip a mirror smaller than this" },
            { label: "MAX TRADE $", value: maxTrade, set: setMaxTrade, step: 5, hint: "Cap on any single mirrored order" },
            { label: "MAX OPEN", value: maxOpen, set: setMaxOpen, step: 1, hint: "Concurrent open positions" },
            { label: "STOP LOSS", value: stopLoss, set: setStopLoss, step: 0.05, hint: "Sell when price decays to this fraction of entry. 0 = off" },
            { label: "TAKE PROFIT", value: takeProfit, set: setTakeProfit, step: 0.01, hint: "Liquidate at this absolute price. 0 = off" },
            { label: "MIN→CLOSE", value: minToClose, set: setMinToClose, step: 5, hint: "Refuse markets resolving sooner than this many minutes" },
            { label: "MAX AGE s", value: maxAgeSec, set: setMaxAgeSec, step: 30, hint: "Refuse leader fills older than this. 0 = off" },
            { label: "TURNOVER", value: turnover, set: setTurnover, step: 0.5, hint: "flow sizing: how many times the allocation may be deployed across the window" },
          ] as const).map((k) => (
            <label key={k.label} className="flex items-center gap-2" title={k.hint}>
              <span className="text-[12px] text-pixel-gray tracking-wider flex-1">{k.label}</span>
              <input
                type="number"
                step={k.step}
                min={0}
                value={k.value}
                onChange={(e) => {
                  const n = Number(e.target.value);
                  if (Number.isFinite(n) && n >= 0) k.set(n);
                }}
                className="pixel-input-sm w-20 text-[12px]"
              />
            </label>
          ))}
          <label className="flex items-center gap-2" title="What mirrors are sized proportionally TO: the leader's net worth (bankroll) or the capital they deployed this window (flow)">
            <span className="text-[12px] text-pixel-gray tracking-wider flex-1">SIZING</span>
            {(["flow", "bankroll"] as const).map((s) => (
              <button
                key={s}
                onClick={() => setSizing(s)}
                className={`pixel-btn text-[11px] px-1.5 py-0.5 ${
                  sizing === s ? "border-green-400 text-green-400" : "border-pixel-border text-pixel-gray"
                }`}
              >
                {s.toUpperCase()}
              </button>
            ))}
          </label>
        </div>
      )}

      {/* ── The result ── */}
      {loading ? (
        <div className="p-6 text-center text-[13px] text-pixel-gray">
          WAITING FOR THIS TRADER&apos;S HISTORY…
        </div>
      ) : !sim || !stats ? (
        <div className="p-6 text-center text-[13px] text-pixel-gray">
          ENTER AN AMOUNT ABOVE $0 TO REPLAY THE COPY
        </div>
      ) : sim.funnel.observed === 0 ? (
        <div className="p-6 text-center space-y-1">
          <div className="text-[14px] text-pixel-gray-light tracking-wider">
            NOTHING TO COPY IN THIS WINDOW
          </div>
          <div className="text-[12px] text-pixel-gray">
            {marketQuery.trim()
              ? `this trader placed no BUY matching “${marketQuery.trim()}” in the last ${days}D — widen the topic filter above, or lengthen the lookback`
              : `this trader placed no BUY in the last ${days}D`}
          </div>
        </div>
      ) : (
        <>
          {/* Headline — the number, and immediately what it rests on. */}
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-px bg-pixel-border">
            {([
              { label: `${days}D NET`, value: fmtUsd(sim.netPnl), tone: sim.netPnl, hint: "Final simulated equity minus the capital you put in" },
              { label: "RETURN", value: fmtPct(stats.pct), tone: sim.netPnl, hint: `On $${amount.toLocaleString("en-US")} of capital` },
              { label: "ENDS WITH", value: fmtUsd(stats.endEquity), tone: stats.endEquity - amount, hint: "Cash + the value of whatever the copy still holds" },
              { label: "TRADES", value: `${sim.funnel.executed}`, tone: 0, hint: `${sim.funnel.executed} entries filled out of ${sim.funnel.observed} the leader placed` },
              { label: "WIN RATE", value: stats.winRate < 0 ? "—" : `${Math.round(stats.winRate)}%`, tone: stats.winRate < 0 ? 0 : stats.winRate - 50, hint: `${stats.wins} winning exits, ${stats.losses} losing` },
              { label: "MAX DRAWDOWN", value: `${stats.drawdown.toFixed(1)}%`, tone: stats.drawdown, hint: "Deepest peak-to-trough on the simulated curve" },
            ] as const).map((s) => (
              <div key={s.label} className="bg-pixel-black px-3 py-2 text-center" title={s.hint}>
                <div className="text-[12px] text-pixel-gray tracking-wider mb-0.5">{s.label}</div>
                <div className={`text-[15px] font-mono ${toneClass(s.tone)}`}>{s.value}</div>
              </div>
            ))}
          </div>

          {/* The curve. Same component the LIVE panel draws a real wallet on.
              Suppressed when nothing was copied — a flat line at your starting
              capital is not a result, and drawing one makes "the gate refused
              everything" look like "the strategy went nowhere". The funnel
              below is the answer in that case. */}
          {sim.funnel.executed === 0 ? (
            <div className="px-3 py-3 border-t-2 border-pixel-border text-[12px] font-mono text-amber-400">
              NOTHING WAS COPIED — no curve to draw. Where the leader&apos;s flow went is
              below; widen the topic filter, the lookback or the knobs to change it.
            </div>
          ) : sim.equityHistory.length > 1 && (
            <div className="px-3 py-2 border-t-2 border-pixel-border">
              <EquityChart
                history={sim.equityHistory}
                markers={sim.markers}
                emptyHint="no simulated fills in this window"
              />
            </div>
          )}

          {/* ── Where the leader's flow went ──
                A copy that made 3 trades out of 180 is not a broken sim; it is
                a gate doing its job, and the reason belongs on screen next to
                the number rather than in a log nobody opens. */}
          <div className="px-3 py-2.5 border-t-2 border-pixel-border space-y-2">
            <div className="flex items-center gap-2 flex-wrap font-mono text-[12px]">
              <span className="text-pixel-gray tracking-wider w-14 shrink-0">FLOW</span>
              {([
                { label: "OBSERVED", n: sim.funnel.observed, cls: "text-pixel-white" },
                { label: "GATED", n: sim.funnel.gated, cls: "text-pixel-gray-light" },
                { label: "OUTRANKED", n: sim.funnel.outranked, cls: "text-pixel-gray-light" },
                { label: "TOO SMALL / NO CASH", n: sim.funnel.skipped, cls: "text-amber-400" },
                { label: "COPIED", n: sim.funnel.executed, cls: "text-green-400" },
              ] as const).map((b, i) => (
                <span key={b.label} className="flex items-center gap-2">
                  {i > 0 && <span className="text-pixel-gray">→</span>}
                  <span className={b.cls}>
                    {b.n.toLocaleString()} <span className="text-pixel-gray tracking-wider">{b.label}</span>
                  </span>
                </span>
              ))}
            </div>
            {Object.keys(sim.funnel.reasons).length > 0 && (
              <div className="flex items-center gap-1.5 flex-wrap font-mono text-[11px] pl-14">
                {Object.entries(sim.funnel.reasons)
                  .sort((a, b) => b[1] - a[1])
                  .slice(0, 8)
                  .map(([reason, n]) => (
                    <span key={reason} className="pixel-badge border-pixel-border text-pixel-gray">
                      {reason} · {n.toLocaleString()}
                    </span>
                  ))}
              </div>
            )}
            <div className="font-mono text-[11px] text-pixel-gray pl-14">
              {sizing === "flow" ? (
                leaderFlow < 1 ? (
                  <span className="text-amber-400">
                    {`they deployed nothing under this gate in the last ${days}D, so there was no flow to size against — every mirror above is the sizing floor, not a proportion`}
                  </span>
                ) : (
                  <>
                    {`sized at ${fmtRatio(stats.ratio)} of every trade they place — $${amount.toLocaleString("en-US")} × ${turnover} turnover spread across the $${Math.round(leaderFlow).toLocaleString("en-US")} they deployed in this window, the same denominator the live engine divides by`}
                    {stats.ratio > 1 && (
                      <span className="text-amber-400">
                        {` — your capital is larger than their whole window, so mirrors ride the $${maxTrade} MAX TRADE cap rather than any proportion of them`}
                      </span>
                    )}
                  </>
                )
              ) : bankroll != null && bankroll >= 1 ? (
                `sized at ${fmtRatio(stats.ratio)} of every trade they place — the fraction of their $${Math.round(bankroll).toLocaleString("en-US")} bankroll you are putting behind each one, the same denominator the live engine divides by`
              ) : (
                `sized at ${fmtRatio(stats.ratio)} of every trade they place — their bankroll wasn't readable, so this fell back to the volume model (a live session that CAN read it will size differently)`
              )}
            </div>
          </div>

          {/* ── The ladder — the real answer to "with N dollars" ── */}
          <div className="border-t-2 border-pixel-border">
            <button
              onClick={() => setShowLadder((s) => !s)}
              className="w-full flex items-center gap-2 px-3 py-2 text-left"
            >
              <span className="text-[12px] text-pixel-gray">{showLadder ? "▾" : "▸"}</span>
              <span className="text-[13px] text-pixel-white tracking-wider">WITH HOW MUCH?</span>
              <span className="text-[12px] text-pixel-gray">
                the same {days}D replayed at every size — copying is not linear in N
              </span>
            </button>
            {/* Every size copying nothing is one sentence, not eight rows of
                $0.00 — the gate is the finding, and a table of zeros buries
                it. */}
            {showLadder && ladder.length > 0 && ladder.every((r) => r.executed === 0) ? (
              <div className="px-3 pb-3 font-mono text-[11px] text-pixel-gray">
                {`no size copies anything here — from $${ladder[0].capital} to $${ladder[ladder.length - 1].capital.toLocaleString("en-US")}, the same gate refuses the same flow. Capital isn't the constraint.`}
              </div>
            ) : showLadder && ladder.length > 0 && (
              <div className="overflow-x-auto">
                <table className="pixel-table w-full" style={{ minWidth: "620px" }}>
                  <thead>
                    <tr>
                      <th>CAPITAL</th>
                      <th className="text-right">COPIED</th>
                      <th className="text-right">SKIPPED</th>
                      <th className="text-right">SIZE VS LEADER</th>
                      <th className="text-right">NET</th>
                      <th className="text-right">RETURN</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {ladder.map((r) => {
                      const here = r.capital === amount;
                      return (
                        <tr key={r.capital} className={here ? "bg-green-400/5" : ""}>
                          <td className={`font-mono text-[12px] ${here ? "text-green-400" : "text-pixel-white"}`}>
                            ${r.capital.toLocaleString("en-US")}
                          </td>
                          <td className="num text-right font-mono text-[12px] text-pixel-gray-light">
                            {r.executed.toLocaleString()}
                          </td>
                          <td
                            className={`num text-right font-mono text-[12px] ${r.skipped > 0 ? "text-amber-400" : "text-pixel-gray"}`}
                            title="Reached the wallet but couldn't be placed — under the order floor, or out of cash"
                          >
                            {r.skipped.toLocaleString()}
                          </td>
                          <td className="num text-right font-mono text-[12px] text-pixel-gray">
                            {fmtRatio(r.ratio)}
                          </td>
                          <td className={`num text-right font-mono text-[12px] ${toneClass(r.net)}`}>
                            {fmtUsd(r.net)}
                          </td>
                          <td className={`num text-right font-mono text-[12px] ${toneClass(r.net)}`}>
                            {fmtPct(r.pct)}
                          </td>
                          <td className="text-right">
                            {!here && (
                              <button
                                onClick={() => setAmountStr(String(r.capital))}
                                className="pixel-btn text-[11px] px-2 py-0.5 border-pixel-border text-pixel-gray hover:text-pixel-white hover:border-pixel-white"
                              >
                                USE
                              </button>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* ── What this number does and does not know ── */}
          <div className="px-3 py-2.5 border-t-2 border-pixel-border space-y-1 font-mono text-[11px] text-pixel-gray">
            {/* A settlement line with nothing in it ("100% SETTLED — 0 legs")
                is a confidence claim about an empty result. Only say it when
                the replay actually held something. */}
            {sim.funnel.executed > 0 && (
            <div>
              <span className={stats.confidence >= 0.8 ? "text-green-400" : stats.confidence >= 0.4 ? "text-amber-400" : "text-red-400"}>
                {`${Math.round(stats.confidence * 100)}% SETTLED`}
              </span>
              {` — ${sim.settlement.resolved} legs closed at a looked-up resolution (${fmtUsd(sim.settlement.resolvedUsd)}), `}
              {`${sim.settlement.marked} at the last price a leader printed (${fmtUsd(sim.settlement.markedUsd)}). `}
              {sim.settlement.marked > 0 && "Marked legs flatter the result: leaders sell winners and let losers expire, so a loser's last print is its entry price."}
            </div>
            )}
            <div>
              THE SPREAD IS UNMODELED ON BOTH SIDES — the replay fills at the price the leader
              got, and so does the live engine&apos;s own backtest. Real slippage is a cost
              neither number carries.
            </div>
            {feedDepthCapped && (
              <div className="text-amber-400">
                THIS TRADER&apos;S FEED WAS DEPTH-CAPPED — the window is only partly covered, so
                every figure above is a floor, not a total.
              </div>
            )}
            {keywordsUnmodeled && (
              <div className="text-amber-400 flex items-center gap-2 flex-wrap">
                <span>
                  {`KEYWORD CHIPS (${keywords.join(", ")}) NARROW THE TAPE ABOVE BUT ARE NOT A COPY GATE — this replay ignored them.`}
                </span>
                {onUseKeywordsAsTopic && (
                  <button
                    onClick={onUseKeywordsAsTopic}
                    className="pixel-btn text-[11px] px-2 py-0.5 border-amber-500/60 text-amber-400"
                    title="Move them into the topic filter, which the engine does enforce"
                  >
                    USE AS TOPIC
                  </button>
                )}
              </div>
            )}
          </div>
        </>
      )}

      {/* ── Commit: the simulated configuration, written to the copy book ── */}
      {onCopyToDesk && (
        <div className="flex items-center gap-2 flex-wrap px-3 py-2.5 border-t-2 border-pixel-border">
          <button
            onClick={() => void commit()}
            disabled={busy || !(amount > 0) || added}
            className="pixel-btn border-pixel-green/70 text-pixel-green hover:text-pixel-white hover:border-pixel-white text-[13px] py-1 tracking-wider disabled:opacity-40"
            title={
              deskAllocationUsd !== null
                ? `Already copied with $${deskAllocationUsd} — this resizes them to $${amount} and re-points their gate at ${gate}`
                : `Copy them with $${amount}, gated to ${gate}, under exactly the knobs above`
            }
          >
            {busy
              ? "…"
              : added
                ? "✓ ON THE DESK"
                : deskAllocationUsd !== null
                  ? `UPDATE TO $${amount.toLocaleString("en-US")}`
                  : `COPY WITH $${amount.toLocaleString("en-US")}`}
          </button>
          <button
            onClick={() => {
              addToDraft({ address, allocationUsd: amount, enabled: true, params });
              setBasketed(true);
            }}
            disabled={!(amount > 0)}
            className={`pixel-btn text-[12px] py-1 tracking-wider disabled:opacity-40 ${
              basketed ? "border-pixel-green text-pixel-green" : ""
            }`}
            title="Shortlist them at this amount, with these knobs — the basket sizes several traders against each other and replays the whole split before anything is committed"
          >
            {basketed ? "✓ IN BASKET" : "+ BASKET"}
          </button>
          <Link href="/copy/basket" className="font-mono text-[11px] text-pixel-gray hover:text-pixel-green">
            open the basket ↗
          </Link>
          <span className="font-mono text-[11px] text-pixel-gray">
            {`COPY writes the amount, the ${gate} gate and these knobs to the copy book — nothing is placed until you start the session. BASKET commits nothing at all.`}
          </span>
        </div>
      )}
    </div>
  );
}
