"use client";

// MY COPY TRADES — the board that answers "is the copying working".
//
// Two halves of one feed, never two screens: every trade the leaders I copy
// made, and every fill of mine, joined by lib/copyTrades.ts. The numbers that
// matter are the join's, not either half's on its own —
//
//   COVERAGE   how many of their trades I actually got. A desk that looks busy
//              and copies 3 of 60 is the failure mode this whole module keeps
//              re-discovering; it now has one number and it is at the top.
//   LAG        median seconds between their fill and mine.
//   SLIP       what the lag cost, in cents against their price.
//
// Filtering is the sentence box (components/SemanticFilterBar.tsx) over
// lib/semanticFilter.ts — "big buys on crypto under 30¢", "missed longshots".
// It runs in the browser on rows already fetched, so it is instant, and the
// same sentence can be ARMED as the gate a copy session runs under.
//
// `compact` is the sidebar rendering (one line a row, no leader table);
// everything else is shared, because a second implementation of this board is
// how the two would start disagreeing about what "copied" means.

import { useMemo, useState } from "react";
import Link from "next/link";

import { useCopyTrades } from "../lib/useCopyTrades";
import { applySemanticQuery, parseSemanticQuery, type SemanticQuery } from "../lib/semanticFilter";
import { useSentimentBook } from "../lib/useSentimentBook";
import type { CopyTradeRow } from "../lib/copyTrades";
import { shortAddress } from "../lib/identityStrat";
import SemanticFilterBar from "./SemanticFilterBar";

const WINDOWS = [1, 3, 7, 14, 30] as const;
export type CopyTradesView = "all" | "mine" | "missed";

function usd(n: number, digits = 2): string {
  if (!Number.isFinite(n)) return "—";
  return `$${Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

function clock(ts: number): string {
  const d = new Date(ts);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function ago(ts: number, now: number): string {
  const s = Math.max(0, Math.round((now - ts) / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86400) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}

function lagText(sec: number | null | undefined): string {
  if (sec == null) return "—";
  return sec < 60 ? `${sec}s` : sec < 3600 ? `${Math.round(sec / 60)}m` : `${Math.round(sec / 3600)}h`;
}

export default function CopyTradesPanel({
  compact = false,
  defaultDays = 7,
  /** Sidebar mounts this only while open — see CopyPanel. */
  enabled = true,
  /** Offered by the desk: arm the typed sentence on real allocations. */
  onArm,
  armLabel,
}: {
  compact?: boolean;
  defaultDays?: number;
  enabled?: boolean;
  onArm?: (gate: ReturnType<typeof import("../lib/semanticFilter").compileGate>) => void;
  armLabel?: string;
}) {
  const [days, setDays] = useState<number>(defaultDays);
  const [query, setQuery] = useState("");
  const [view, setView] = useState<CopyTradesView>("all");
  const [parsed, setParsed] = useState<SemanticQuery | null>(null);
  const { data, loading, error, refresh } = useCopyTrades({
    days, enabled, pollMs: compact ? 120_000 : 90_000,
  });

  const now = Date.now();
  const all = data?.rows ?? [];

  // The quick views are the two questions people open this for, as buttons
  // rather than a sentence they have to remember how to spell.
  const viewed = useMemo(() => {
    if (view === "mine") return all.filter((r) => r.kind === "mine");
    if (view === "missed") return all.filter((r) => r.kind === "leader" && !r.copied);
    return all;
  }, [all, view]);

  const q = useMemo(() => parsed ?? parseSemanticQuery(query), [parsed, query]);
  // A MARKET SENTIMENT clause ("against the crowd") needs the tape. Warmed
  // only when the sentence actually carries one; without it every row reads
  // `unknown` and passes, so a board with no price history shows the flow
  // unfiltered rather than showing nothing.
  const sentiment = useSentimentBook(viewed, q.sentiment);
  const { rows, dropped, reasons } = useMemo(
    () => applySemanticQuery(viewed, q, { now: Date.now(), rank: false, sentiment: sentiment.book.lookup }),
    [viewed, q, sentiment.book],
  );

  const s = data?.summary;
  const topReason = useMemo(() => {
    const entries = Object.entries(reasons).sort((a, b) => b[1] - a[1]);
    return entries[0] ?? null;
  }, [reasons]);

  return (
    <div className={compact ? "space-y-1.5" : "space-y-3"}>
      {/* ── The verdict line ── */}
      <div className={compact ? "px-0" : "pixel-panel p-3"}>
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[9.5px] font-mono tracking-[0.14em] text-pixel-gray">
            COVERAGE
          </span>
          <span
            className={`font-mono tabular-nums ${compact ? "text-[13px]" : "text-[19px]"} ${
              !s || s.leader === 0 ? "text-pixel-gray"
                : s.coverage >= 0.5 ? "text-green-400"
                : s.coverage > 0 ? "text-amber-400" : "text-red-400"
            }`}
            title="How many of the leaders' trades in this window have a fill of mine behind them. The one number a copy desk is judged on."
          >
            {s && s.leader > 0 ? `${Math.round(s.coverage * 100)}%` : "—"}
          </span>
          <span className="text-[10px] font-mono text-pixel-gray tabular-nums">
            {s ? `${s.copied}/${s.leader} trades · ${s.leaders} leader${s.leaders === 1 ? "" : "s"}` : "…"}
          </span>
          <span className="flex-1" />
          <div className="flex items-center gap-0.5">
            {WINDOWS.map((d) => (
              <button
                key={d}
                onClick={() => setDays(d)}
                className={`pixel-btn btn-xs ${d === days ? "border-pixel-green text-pixel-green" : ""}`}
                title={`The last ${d} day(s)`}
              >
                {d}D
              </button>
            ))}
            <button
              onClick={() => void refresh()}
              className="pixel-btn btn-xs"
              title="Re-read my fills and the leaders' feeds"
            >
              {loading ? "…" : "⟳"}
            </button>
          </div>
        </div>

        <div className="mt-1 flex items-center gap-2.5 flex-wrap text-[10px] font-mono text-pixel-gray tabular-nums">
          <span title="Median seconds between a leader's trade and my fill of it. Every cent of slippage starts here.">
            LAG {lagText(s?.medianLagSec)}
          </span>
          <span
            className={s?.avgSlipCents != null && s.avgSlipCents > 0 ? "text-amber-400" : ""}
            title="Mean signed difference between my price and the leader's, in cents. Positive = I paid up for being late."
          >
            SLIP {s?.avgSlipCents == null ? "—" : `${s.avgSlipCents > 0 ? "+" : ""}${s.avgSlipCents}¢`}
          </span>
          <span title="What I moved, against what they moved, in this window.">
            {usd(s?.myNotional ?? 0, 0)} <span className="text-pixel-gray/60">of</span> {usd(s?.leaderNotional ?? 0, 0)}
          </span>
          {!!s?.unattributed && (
            <span
              className="text-cyan-300/80"
              title="Fills of mine with no leader trade behind them: the engine's own stop-loss / take-profit exits, or anything traded by hand from the same wallet. Not an error — just not a copy."
            >
              {s.unattributed} UNATTRIBUTED
            </span>
          )}
        </div>

        {/* Honesty about the cache, kept apart from honesty about the flow. */}
        {!!data?.warming?.length && (
          <div className="mt-1 text-[9.5px] font-mono text-amber-400/90 leading-snug">
            {data.warming.length} leader{data.warming.length === 1 ? "" : "s"} not fetched yet — their
            trades are missing from this window, which is the cache, not them.
          </div>
        )}
        {data?.fillsError && (
          <div className="mt-1 text-[9.5px] font-mono text-red-300 leading-snug">
            couldn&apos;t read my fills: {data.fillsError}
          </div>
        )}
        {s && s.leader === 0 && s.mine > 0 && (
          <div className="mt-1 text-[9.5px] font-mono text-pixel-gray leading-snug">
            your fills are here, but none of the traders you copy traded in this window —
            coverage needs both halves, so it reads 0 rather than 100%
          </div>
        )}
        {data && !data.wallet && (
          <div className="mt-1 text-[9.5px] font-mono text-amber-400/90 leading-snug">
            no funded wallet resolved — the leaders&apos; half is real, my half is empty by construction
          </div>
        )}
        {error && (
          <div className="mt-1 text-[9.5px] font-mono text-red-300 leading-snug">{error}</div>
        )}
      </div>

      {/* ── The sentence box + the two quick views ── */}
      <div className={compact ? "" : "pixel-panel p-3 space-y-2"}>
        <div className="flex items-center gap-1 mb-1">
          {(["all", "mine", "missed"] as CopyTradesView[]).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={`pixel-btn btn-xs ${v === view ? "border-pixel-green text-pixel-green" : ""}`}
              title={
                v === "all" ? "Both halves: their trades and my fills"
                  : v === "mine" ? "Only my own on-chain fills"
                    : "Their trades with no fill of mine behind them"
              }
            >
              {v === "all" ? "ALL" : v === "mine" ? "MINE" : "MISSED"}
            </button>
          ))}
          <span className="flex-1" />
          {!compact && (
            <span className="text-[9.5px] font-mono text-pixel-gray">
              matched within {data?.matchMinutes ?? 30}m
            </span>
          )}
        </div>
        <SemanticFilterBar
          value={query}
          onChange={setQuery}
          onParsed={setParsed}
          kept={rows.length}
          total={viewed.length}
          compact={compact}
          onArm={onArm}
          armLabel={armLabel}
        />
        {dropped > 0 && topReason && (
          <div className="text-[9px] font-mono text-pixel-gray/70 truncate">
            {dropped} hidden — mostly: {topReason[0]} ({topReason[1]})
          </div>
        )}
      </div>

      {/* ── The feed ── */}
      <div
        className={compact ? "max-h-[34vh] overflow-y-auto space-y-0.5" : "pixel-panel divide-y divide-[var(--border)]"}
      >
        {rows.length === 0 ? (
          <div className="px-2 py-3 text-[10.5px] font-mono text-pixel-gray leading-snug">
            {loading
              ? "reading fills and leader feeds…"
              : all.length === 0
                ? "Nothing in this window yet. A copy session in TEST places no orders — the leaders' half still fills in."
                : "No row matches that sentence."}
          </div>
        ) : (
          rows.map((r) => <Row key={r.id} row={r} now={now} compact={compact} />)
        )}
      </div>

      {/* ── Per leader: who I keep up with, and who I don't ── */}
      {!compact && !!data?.leaders?.length && (
        <div className="pixel-panel p-3">
          <div className="text-[9.5px] font-mono tracking-[0.14em] text-pixel-gray mb-1.5">
            BY LEADER
          </div>
          <table className="pixel-table w-full">
            <thead>
              <tr>
                <th className="w-[36%]">LEADER</th>
                <th className="num w-[14%]">THEIR TRADES</th>
                <th className="num w-[14%]">I GOT</th>
                <th className="num w-[12%]">COVERAGE</th>
                <th className="num w-[12%]">LAG</th>
                <th className="num w-[12%]">MY $</th>
              </tr>
            </thead>
            <tbody>
              {data.leaders.map((l) => (
                <tr key={l.address}>
                  <td className="truncate">
                    <Link href={`/copy/${l.address}`} className="hover:text-green-400" title={l.address}>
                      {l.label}
                    </Link>
                  </td>
                  <td className="num tabular-nums">{l.trades}</td>
                  <td className="num tabular-nums">{l.copied}</td>
                  <td
                    className={`num tabular-nums ${
                      l.trades === 0 ? "" : l.coverage >= 0.5 ? "text-green-400" : l.coverage > 0 ? "text-amber-400" : "text-red-400"
                    }`}
                  >
                    {l.trades ? `${Math.round(l.coverage * 100)}%` : "—"}
                  </td>
                  <td className="num tabular-nums">{lagText(l.medianLagSec)}</td>
                  <td className="num tabular-nums">{usd(l.myNotional, 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/** One trade. Mine and theirs share a row shape so the eye can compare them
    down one column; the left rail is the only thing that differs. */
function Row({ row, now, compact }: { row: CopyTradeRow; now: number; compact: boolean }) {
  const mine = row.kind === "mine";
  const missed = !mine && !row.copied;
  return (
    <div
      className={`flex items-baseline gap-1.5 px-2 py-1 ${
        compact ? "" : "hover:bg-pixel-white/[0.04]"
      } ${mine ? "bg-green-400/[0.05]" : ""}`}
      title={`${new Date(row.timestamp).toLocaleString()}${row.count > 1 ? ` · ${row.count} fills merged` : ""}`}
    >
      <span
        className={`shrink-0 text-[9px] font-mono w-[26px] ${mine ? "text-green-400" : "text-pixel-gray"}`}
        title={mine ? "My on-chain fill" : "A trade the leader made"}
      >
        {mine ? "ME" : "THEM"}
      </span>
      <span
        className={`shrink-0 text-[9.5px] font-mono font-semibold w-[30px] ${
          row.side === "BUY" ? "text-green-400" : "text-red-400"
        }`}
      >
        {row.side}
      </span>
      <span className="min-w-0 flex-1 truncate text-[10.5px] font-mono" title={row.market}>
        {row.market || "—"}
      </span>
      <span className="shrink-0 text-[10px] font-mono tabular-nums text-pixel-gray w-[36px] text-right">
        {Math.round(row.price * 100)}¢
      </span>
      <span className="shrink-0 text-[10px] font-mono tabular-nums w-[52px] text-right">
        {usd(row.notional, 0)}
      </span>
      {mine ? (
        <span
          className={`shrink-0 text-[9.5px] font-mono tabular-nums w-[64px] text-right ${
            row.lagSec == null ? "text-cyan-300/70" : row.slipCents && row.slipCents > 2 ? "text-amber-400" : "text-pixel-gray"
          }`}
          title={
            row.lagSec == null
              ? row.side === "SELL"
                ? "No leader trade behind this exit — the engine's own stop-loss / take-profit, or a hand sell."
                : "No leader trade behind this buy: nobody on the desk bought this market within the match window. A hand trade, or a leader whose feed isn't cached."
              : `Filled ${row.lagSec}s after ${row.leaderLabel}, ${row.slipCents! >= 0 ? "worse" : "better"} by ${Math.abs(row.slipCents!)}¢`
          }
        >
          {row.lagSec == null
            ? row.side === "SELL" ? "own exit" : "no leader"
            : `+${lagText(row.lagSec)} ${row.slipCents! > 0 ? "+" : ""}${row.slipCents}¢`}
        </span>
      ) : (
        <span
          className={`shrink-0 text-[9.5px] font-mono w-[64px] text-right ${
            missed ? "text-red-400/80" : "text-green-400"
          }`}
          title={
            missed
              ? "No fill of mine within the match window: a gate, the budget, the CLOB floor, or simply not running."
              : `Mirrored ${row.copiedAt ? `${Math.round((row.copiedAt - row.timestamp) / 1000)}s later` : ""}`
          }
        >
          {missed ? "⊘ MISSED" : "✓ COPIED"}
        </span>
      )}
      {!compact && (
        <span className="shrink-0 w-[74px] text-right text-[9.5px] font-mono text-pixel-gray truncate" title={row.leader ?? undefined}>
          {row.leaderLabel || (row.leader ? shortAddress(row.leader) : "—")}
        </span>
      )}
      <span className="shrink-0 w-[38px] text-right text-[9px] font-mono text-pixel-gray/70 tabular-nums">
        {compact ? ago(row.timestamp, now) : clock(row.timestamp)}
      </span>
    </div>
  );
}
