"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import TopBar from "../components/TopBar";
import CopyIndex from "../components/CopyIndex";
import StratHub from "../components/StratHub";
import ConfirmDeleteStrat from "../components/ConfirmDeleteStrat";
import { useUrlSync } from "../context/FiltersContext";
import { useStratManager } from "../lib/stratManager";
import { useStratStats } from "../lib/stratStats";
import { useHubBacktests, HUB_BACKTEST_DAYS, HUB_WINDOWS } from "../lib/hubBacktest";
import { setActiveIndexId } from "../lib/indexStore";

/** Remembers the hub's backtest window across visits. */
const HUB_DAYS_KEY = "polymarket.hub.days";

/// /strats is two screens on one route:
///
///   /strats          → the STRAT HUB: every saved strat as a card, headlined
///                      by its 1-day backtest (all cards, same window, same
///                      engine — so they're comparable).
///   /strats?id=<id>  → that strat's workspace: the STRAT / BACKTEST / LIVE
///                      tabs, each with a subtab rail (STRAT → BUILD/SOURCE/
///                      MARKET, BACKTEST → RESULTS/TRADES, LIVE → PORTFOLIO/
///                      POSITIONS/STATS/TRADES/WALLET/HELP). Strat
///                      select/create lives in the TopBar picker, rename/delete
///                      in the STRAT tab, and the go-live checklist in the LIVE
///                      tab. Wallet/token/QR pairing, trading-wallet deposit/
///                      withdraw, bridge funds in, and legacy V1 migration all
///                      live under the LIVE → WALLET subtab (CopyIndex).
function StratsInner() {
  useUrlSync();
  const router = useRouter();
  const openId = useSearchParams().get("id");

  // CopyIndex reads the active strat out of indexStore on mount, so the URL's
  // id has to land there BEFORE it renders — hence the gate rather than a
  // plain effect (a child's effects run before its parent's).
  const [ready, setReady] = useState(false);
  useEffect(() => {
    if (openId) {
      setActiveIndexId(openId);
      window.dispatchEvent(new Event("strat-updated"));
    }
    setReady(true);
  }, [openId]);

  return (
    <div className="max-w-[1920px] mx-auto">
      <TopBar showSearch={false} />
      <div className="p-4">
        {openId
          ? ready && <CopyIndex searchFilter="" />
          : <Hub onOpen={(id) => router.push(`/strats?id=${id}`)} />}
      </div>
    </div>
  );
}

/// The hub's wiring: the shared strat manager for every mutation, the engine
/// ledger for real money, and one N-day replay per card — saved strats and
/// recommended templates alike — for the headline.
function Hub({ onOpen }: { onOpen: (id: string) => void }) {
  const {
    indexes, activeId, select, create, fork, forkDefault,
    requestDelete, pendingDelete, confirmDelete, cancelDelete,
  } = useStratManager();
  const { stats } = useStratStats();
  // The window survives navigation: someone comparing strats over 7 days
  // shouldn't be dropped back to 24h every time they open one.
  const [days, setDays] = useState(HUB_BACKTEST_DAYS);
  useEffect(() => {
    const stored = Number(localStorage.getItem(HUB_DAYS_KEY));
    if (HUB_WINDOWS.includes(stored)) setDays(stored);
  }, []);
  const changeDays = (d: number) => {
    setDays(d);
    try {
      localStorage.setItem(HUB_DAYS_KEY, String(d));
    } catch {
      // Shared-origin quota — the window just won't persist.
    }
  };
  const { results, pending, loading, worker, refresh } = useHubBacktests(indexes, days);

  const open = (id: string) => { select(id); onOpen(id); };

  return (
    <>
      <StratHub
        indexes={indexes}
        stats={stats}
        backtests={results}
        backtestPending={pending}
        backtestLoading={loading}
        worker={worker}
        days={days}
        onDaysChange={changeDays}
        activeId={activeId}
        onSelect={open}
        onDelete={requestDelete}
        onCreate={() => { const idx = create(); onOpen(idx.id); }}
        // A fork exists to be changed — land in its workspace.
        onForkSaved={(id) => { const copy = fork(id); if (copy) onOpen(copy.id); }}
        onFork={(t) => { const idx = forkDefault(t); onOpen(idx.id); }}
        onRefresh={refresh}
      />
      <ConfirmDeleteStrat
        name={pendingDelete === null ? null : indexes.find((i) => i.id === pendingDelete)?.name ?? pendingDelete}
        onConfirm={confirmDelete}
        onCancel={cancelDelete}
      />
    </>
  );
}

export default function StratsPage() {
  return (
    <Suspense>
      <StratsInner />
    </Suspense>
  );
}
