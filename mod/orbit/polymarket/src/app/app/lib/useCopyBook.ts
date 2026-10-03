"use client";

// THE COPY BOOK, as a hook.
//
// This used to live inside `components/CopyDesk.tsx`, on a screen of its own at
// /copy. The desk and the strat hub were two consoles for the same job — "which
// strategies am I running, with how much, and are they working?" — answered in
// two vocabularies, on two routes, with two sets of switches. They are one wall
// now (`components/StratHub.tsx`), so the state that used to be the desk's is a
// hook the hub mounts.
//
// The contract is unchanged and it is the whole point of the design: nothing
// here is stored in the browser. Every read is `GET /copy/book`, every write is
// a `POST /copy/*`, and those are the same routes the `pm_copy_*` MCP tools
// call. Ask an agent to "put $50 on 0xab…" and the wall shows it on the next
// poll; move a number on the wall and the agent sees it. A localStorage mirror
// is how the two drift.

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  COPY_BOOK_CHANGED_EVENT,
  fetchCopyBook, upsertAllocation, removeAllocation, rebalanceBook, setBankroll,
  startCopying, stopCopying, setCopyExecution,
  type CopyBook, type CopyBookRow,
} from "./copyBook";
import { identityStrat, type AllocationParams } from "./identityStrat";
import { armedDefault, autoExecuteFor, modeOf, type TradingMode } from "./tradingMode";
import type { SavedIndex } from "./types";

/** The book is re-read on this cadence. It is also how an allocation an agent
    changed over MCP arrives on screen without a reload. */
const POLL_MS = 15_000;

export interface CopyBookState {
  book: CopyBook | null;
  rows: CopyBookRow[];
  /** Every row as the IDENTITY STRAT it materializes into — the same object
      the live engine runs and the backtest replays, so a copy card on the wall
      is measured exactly like a saved strat's card. */
  strats: SavedIndex[];
  /** The wallet the book is read against; null ⇒ reads work, nothing can run. */
  eoa: string | null;
  error: string | null;
  /** Mutation key currently in flight ("alloc:0xab…", "start-all", …), so a
      row can disable just its own controls. */
  busy: string | null;
  /** The mode a row is in (running) or would start in (stopped). */
  modeFor: (row: CopyBookRow) => TradingMode;
  /** Desk-wide armed mode, behind START ALL. */
  deskMode: TradingMode;
  deskCanGoLive: boolean;
  arm: (key: string, mode: TradingMode) => void;
  reload: () => Promise<void>;

  // ── Writes. Each resolves after the book has been re-read. ──
  /** Add or resize a leader. `params` is a PATCH of the identity template —
      omitted knobs keep their value — which is how the sentence box arms a
      market + trade gate on several traders at once without restating their
      sizing. */
  allocate: (
    address: string,
    allocationUsd: number,
    label?: string,
    params?: AllocationParams,
  ) => Promise<void>;
  setEnabled: (row: CopyBookRow, enabled: boolean) => Promise<void>;
  remove: (address: string) => Promise<void>;
  setBankrollUsd: (bankroll: number) => Promise<void>;
  rebalance: (mode: "equal" | "weighted") => Promise<void>;
  start: (address?: string, mode?: TradingMode) => Promise<void>;
  stop: (address?: string) => Promise<void>;
  /** Flip a RUNNING session between TEST and LIVE in place. A stopped row just
      remembers the arm — there is no session to flip. */
  setMode: (row: CopyBookRow, mode: TradingMode) => Promise<void>;
}

export function useCopyBook(eoa: string | null): CopyBookState {
  const [book, setBook] = useState<CopyBook | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  // Mode a STOPPED row will start in. A running row reads its mode off the
  // server (`live.autoExecute`) and this map is ignored — the wall never
  // renders a mode the engine isn't actually in. Keyed by address; "" is the
  // desk-wide arm behind START ALL.
  const [armed, setArmed] = useState<Record<string, TradingMode>>({});

  const reload = useCallback(async () => {
    try {
      setBook(await fetchCopyBook(eoa));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [eoa]);

  useEffect(() => {
    void reload();
    const t = setInterval(() => void reload(), POLL_MS);
    // A write made outside this hook (the sidebar tray's COPY ALL) announces
    // itself — re-read now instead of on the next poll.
    const onChanged = () => void reload();
    window.addEventListener(COPY_BOOK_CHANGED_EVENT, onChanged);
    return () => {
      clearInterval(t);
      window.removeEventListener(COPY_BOOK_CHANGED_EVENT, onChanged);
    };
  }, [reload]);

  /** Run a mutation, surface its error where it can be read, and take the book
      from the response so the screen never guesses at server state. Routes that
      don't answer with a book (`/live/execution`) fall through to a fresh GET,
      so the mode chip still comes from the server. */
  const mutate = useCallback(
    async (key: string, fn: () => Promise<unknown>) => {
      setBusy(key);
      setError(null);
      try {
        const res = await fn();
        const next = (res as { book?: CopyBook }).book ?? (res as CopyBook);
        if (next && Array.isArray((next as CopyBook).allocations)) setBook(next as CopyBook);
        else await reload();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        await reload();
      } finally {
        setBusy(null);
      }
    },
    [reload],
  );

  const rows = useMemo(() => book?.allocations ?? [], [book]);
  const strats = useMemo(() => rows.map((r) => identityStrat(r)), [rows]);

  /** Order matters: a live session's real mode always wins, then whatever the
      user armed, then the capital-derived default. So the switch never shows
      LIVE over a session quietly sitting in TEST — the exact lie that let a
      funded wallet place nothing for a week. */
  const modeFor = useCallback(
    (row: CopyBookRow): TradingMode =>
      row.live?.running
        ? modeOf(row.live.autoExecute)
        : armed[row.address] ?? armedDefault(row.allocationUsd > 0),
    [armed],
  );

  const deskCanGoLive = useMemo(
    () => rows.some((r) => r.enabled && r.allocationUsd > 0),
    [rows],
  );

  return {
    book,
    rows,
    strats,
    eoa,
    error,
    busy,
    modeFor,
    deskMode: armed[""] ?? armedDefault(deskCanGoLive),
    deskCanGoLive,
    arm: (key, mode) => setArmed((a) => ({ ...a, [key]: mode })),
    reload,

    allocate: (address, allocationUsd, label, params) =>
      mutate(`alloc:${address}`, () =>
        upsertAllocation(
          { address, allocationUsd, ...(label ? { label } : {}), ...(params ? { params } : {}) },
          eoa,
        ),
      ),
    setEnabled: (row, enabled) =>
      mutate(`toggle:${row.address}`, () =>
        upsertAllocation({ address: row.address, allocationUsd: row.allocationUsd, enabled }, eoa),
      ),
    remove: (address) => mutate(`rm:${address}`, () => removeAllocation(address, eoa)),
    setBankrollUsd: (bankroll) => mutate("bankroll", () => setBankroll(bankroll, eoa)),
    rebalance: (mode) =>
      mutate("rebalance", () => rebalanceBook(book?.bankroll ?? 0, mode, eoa)),
    start: (address, mode) =>
      mutate(address ? `start:${address}` : "start-all", () =>
        startCopying(eoa!, {
          ...(address ? { address } : {}),
          autoExecute: autoExecuteFor(mode ?? armed[address ?? ""] ?? "TEST"),
        }),
      ),
    stop: (address) =>
      mutate(address ? `stop:${address}` : "stop-all", () => stopCopying(eoa!, address)),
    setMode: async (row, mode) => {
      if (!row.live?.running) {
        setArmed((a) => ({ ...a, [row.address]: mode }));
        return;
      }
      await mutate(`mode:${row.address}`, () =>
        setCopyExecution(eoa!, row.strategyId, autoExecuteFor(mode)),
      );
    },
  };
}
