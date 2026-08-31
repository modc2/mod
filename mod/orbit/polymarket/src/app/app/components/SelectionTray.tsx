"use client";

// THE SELECTION TRAY — the finder's checked traders, as a sidebar block.
//
// Selecting used to be committing: to see what copying a name would have done
// you had to COPY it into the book or BASKET it and walk to /copy/basket.
// The tray closes that gap — every checked row becomes an IDENTITY strat on
// the spot (lib/identityStrat.ts, the same object the desk row, the live
// engine and the worker all run) and is replayed through the same client-side
// engine the hub cards use (backtestOne), over the SAME window the
// leaderboard ranked them on and gated to the SAME query that found them.
// Rank on bitcoin 7D, the replay is bitcoin 7D.
//
// It lives in the user sidebar, not on the desk: the desk page scrolls the
// finder off-screen, and a 340px docked column is the one place a shortlist
// stays in view while you keep browsing. Reads come from lib/pickStore (the
// finder's checkboxes write there); the replay runs ITSELF — check a name
// and it starts, change the $ and it re-runs, debounced and sequential so
// five picks don't fire five concurrent 30-day feed walks.
//
// COPY ALL is the commit: the same /copy/allocations upserts a single COPY
// uses, then COPY_BOOK_CHANGED_EVENT tells every mounted useCopyBook to
// re-read — the desk shows the new rows without waiting out its poll.

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";

import { useAuth } from "../context/AuthContext";
import { getOwnerAddress } from "../lib/access";
import { identityStrat, shortAddress } from "../lib/identityStrat";
import { backtestOne, type HubBacktest, type TraderFeed } from "../lib/hubReplay";
import { COPY_BOOK_CHANGED_EVENT, upsertAllocation } from "../lib/copyBook";
import { addToDraft } from "../lib/basketDraft";
import {
  clearPicks, removePick, setPickUsd, usePicks, type TraderPick,
} from "../lib/pickStore";

function pickKey(p: TraderPick, days: number): string {
  return `${p.address}|${p.usd}|${p.marketQuery}|${days}`;
}

/** Amount edits re-run the replay, so they wait for you to stop typing. */
const DEBOUNCE_MS = 450;

function fmtSigned(v: number): string {
  if (!Number.isFinite(v)) return "—";
  const sign = v > 0 ? "+" : v < 0 ? "-" : "";
  return `${sign}$${Math.abs(v).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

/** Replay every pick, automatically, one at a time. Results are keyed by
    everything that changes them (address, $, gate, window) so a re-check or
    a window flip re-runs exactly what changed and nothing else. */
function useSelectionBacktests(picks: TraderPick[], days: number) {
  const [results, setResults] = useState<Record<string, HubBacktest>>({});
  const [running, setRunning] = useState<string | null>(null);
  // Per-address feeds, shared across picks and re-picks for the session —
  // the 30-day pull is the expensive part, not the replay.
  const feedRef = useRef(new Map<string, Promise<TraderFeed>>());

  const want = picks.map((p) => pickKey(p, days)).join("|");

  useEffect(() => {
    let cancelled = false;
    const queue = picks.filter((p) => !(pickKey(p, days) in results));
    if (queue.length === 0) return;

    const timer = setTimeout(async () => {
      for (const p of queue) {
        if (cancelled) return;
        const key = pickKey(p, days);
        setRunning(key);
        const strat = identityStrat({
          address: p.address,
          allocationUsd: p.usd,
          enabled: true,
          addedAt: 0,
          updatedAt: 0,
          ...(p.marketQuery ? { params: { marketQuery: p.marketQuery } } : {}),
        });
        try {
          const bt = await backtestOne(strat, days, feedRef.current);
          if (cancelled) return;
          if (bt) setResults((prev) => ({ ...prev, [key]: bt }));
        } catch {
          // A failed replay just stays "replaying…" until the next change.
        }
      }
      if (!cancelled) setRunning(null);
    }, DEBOUNCE_MS);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [want, days]);

  return { results, running };
}

export default function SelectionTray() {
  const { picks, days } = usePicks();
  const { auth } = useAuth();
  // Same rule as the desk: the wallet that signed the gate IS the funded one.
  const eoa = getOwnerAddress() ?? auth.address ?? null;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { results, running } = useSelectionBacktests(picks, days);

  const done = picks
    .map((p) => results[pickKey(p, days)])
    .filter((bt): bt is HubBacktest => !!bt);
  const allDone = done.length === picks.length;
  const totalUsd = picks.reduce((s, p) => s + p.usd, 0);
  const totalPnl = done.reduce((s, bt) => s + bt.pnl, 0);
  const totalTrades = done.reduce((s, bt) => s + bt.trades, 0);
  const positive = done.filter((bt) => bt.pnl > 0).length;

  const copyAll = async () => {
    if (picks.length === 0 || busy) return;
    setBusy(true);
    setError(null);
    try {
      // Sequential upserts — each is the same route a single COPY uses, and
      // an already-copied name is updated in place, not duplicated.
      for (const p of picks) {
        await upsertAllocation(
          { address: p.address, allocationUsd: p.usd, params: { marketQuery: p.marketQuery } },
          eoa,
        );
      }
      clearPicks();
      window.dispatchEvent(new Event(COPY_BOOK_CHANGED_EVENT));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const basketAll = () => {
    picks.forEach((p) =>
      addToDraft({
        address: p.address,
        allocationUsd: p.usd,
        enabled: true,
        ...(p.marketQuery ? { params: { marketQuery: p.marketQuery } } : {}),
      }),
    ); // addToDraft → writeDraft fires BASKET_EVENT itself — badges update.
  };

  if (picks.length === 0) return null;

  return (
    <div className="p-3 space-y-1.5 border-b border-pixel-green/40">
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-[9.5px] tracking-[0.14em] text-pixel-green">
          SELECTED · {picks.length} · ${totalUsd.toLocaleString()}
        </span>
        <span className="flex-1" />
        <button
          className="font-mono text-[9px] tracking-[0.1em] text-pixel-gray hover:text-pixel-white"
          disabled={busy}
          onClick={clearPicks}
          title="Uncheck everything"
        >
          CLEAR
        </button>
      </div>

      <div className="space-y-1">
        {picks.map((p) => (
          <PickRow
            key={p.address}
            pick={p}
            days={days}
            bt={results[pickKey(p, days)]}
            replaying={running === pickKey(p, days)}
            busy={busy}
          />
        ))}
      </div>

      {/* The line the tray exists for: the whole selection, as one number. */}
      <div className="flex flex-wrap items-baseline gap-x-2 font-mono text-[9.5px] pt-0.5">
        <span className="text-[9px] tracking-[0.14em] text-pixel-gray">TOGETHER · {days}D</span>
        {done.length === 0 ? (
          <span className="text-pixel-gray">replaying…</span>
        ) : (
          <>
            <span className={totalPnl > 0 ? "text-pixel-green" : totalPnl < 0 ? "text-red-400" : "text-pixel-gray-light"}>
              {fmtSigned(totalPnl)}
            </span>
            <span className="text-pixel-gray-light">{totalTrades} trades</span>
            <span className="text-pixel-gray-light">{positive}/{done.length} positive</span>
            {!allDone && <span className="text-pixel-gray">({picks.length - done.length} replaying)</span>}
          </>
        )}
      </div>

      {error && (
        <div className="font-mono text-[9.5px] leading-4 text-red-400">{error}</div>
      )}

      <div className="flex items-center gap-1.5 pt-0.5">
        <button
          className="pixel-btn btn-xs border-pixel-green text-pixel-green"
          disabled={busy}
          onClick={() => void copyAll()}
          title={`Add all ${picks.length} to the copy desk, each with its own $ and market gate — names already there are updated in place`}
        >
          {busy ? "COPYING…" : `COPY ALL ${picks.length} → DESK`}
        </button>
        <button
          className="pixel-btn btn-xs"
          disabled={busy}
          onClick={basketAll}
          title="Shortlist all of them into the basket draft — size the split on /copy/basket"
        >
          + BASKET
        </button>
      </div>

      <p className="font-mono text-[8.5px] leading-[1.5] text-pixel-gray/80">
        each replayed over the last {days}D as you check and size — their real
        trades mirrored with your $, not a promise about the next {days}
      </p>
    </div>
  );
}

/** One pick, one line: who · your $ · what it would have made · verdict · ✕.
    The market gate and trade count live in the titles — a 340px column earns
    its keep by staying scannable. */
function PickRow({
  pick, days, bt, replaying, busy,
}: {
  pick: TraderPick;
  days: number;
  bt?: HubBacktest;
  replaying: boolean;
  busy: boolean;
}) {
  const [draft, setDraft] = useState(String(pick.usd));
  useEffect(() => setDraft(String(pick.usd)), [pick.usd]);
  const commit = () => {
    const v = Number(draft);
    if (Number.isFinite(v) && v > 0 && v !== pick.usd) setPickUsd(pick.address, v);
    else setDraft(String(pick.usd));
  };

  const verdict = bt?.forward?.verdict;
  const verdictTone =
    verdict === "held"
      ? "text-pixel-green"
      : verdict === "faded" || verdict === "no-edge"
        ? "text-red-400"
        : "text-pixel-gray";

  return (
    <div className="flex items-center gap-1.5 font-mono text-[10px]">
      <Link
        href={`/traders/${pick.address}${pick.marketQuery ? `?mq=${encodeURIComponent(pick.marketQuery)}&days=${days}` : `?days=${days}`}`}
        className="text-pixel-gray-light hover:text-pixel-green normal-case shrink-0"
        title={`${pick.address}\n${pick.marketQuery ? `Only their “${pick.marketQuery}” trades are replayed — and copied` : "Every market they trade"}`}
      >
        {shortAddress(pick.address)}
      </Link>
      <input
        className="pixel-input-sm input-xs w-14 font-mono text-[10px]"
        value={draft}
        inputMode="decimal"
        disabled={busy}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
        title="Dollars behind this trader — edits re-run their replay"
      />
      <span className="flex-1" />
      {!bt ? (
        <span className="text-[9px] text-pixel-gray">{replaying ? "replaying…" : "queued…"}</span>
      ) : (
        <>
          <span
            className={bt.pnl > 0 ? "text-pixel-green" : bt.pnl < 0 ? "text-red-400" : "text-pixel-gray-light"}
            title={`${bt.trades} trades over the last ${days}D${bt.trades === 0 && bt.note ? ` — ${bt.note}` : ""}`}
          >
            {fmtSigned(bt.pnl)}
          </span>
          <span
            className={`text-[8.5px] tracking-[0.08em] ${verdict ? verdictTone : "text-pixel-gray/70"}`}
            title={
              verdict
                ? "Walk-forward: also replayed over the window BEFORE this one. HELD is the only pass."
                : "No walk-forward check on this replay"
            }
          >
            {verdict ? verdict.toUpperCase() : "—"}
          </span>
        </>
      )}
      <button
        className="text-pixel-gray hover:text-red-400 text-[11px] leading-none px-0.5 shrink-0"
        disabled={busy}
        onClick={() => removePick(pick.address)}
        title="Uncheck"
      >
        ✕
      </button>
    </div>
  );
}
