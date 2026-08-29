"use client";

// /copy/<address> — ONE LEADER'S WORKSPACE.
//
// The desk at /copy answers "whose trades am I copying, with how much, and is
// it working?". This route is the drill-down for a single row of it: replay
// that leader over a window, watch the live session that mirrors them, and
// fund the wallet it trades through.
//
// The console used to reach this screen at /strats?id=<opaque id>, where the
// thing being edited was a SAVED STRAT — a multi-trader index living in
// localStorage, forked from a template gallery, publishable to a shelf. That
// whole layer is archived (see `src/_archive/README.md`). What is left is the
// only case that was ever actually traded: one leader, one dollar amount.
//
// So the workspace is bound to the SERVER's copy book, not to a browser store:
//
//   GET /copy/book  →  the row for this address  →  identityStrat(row)
//
// `identityStrat` is the same materialization the live engine and the backtest
// worker use (lib/identityStrat.ts, mirrored by api/src/copy.rs), which is what
// makes this screen's numbers a claim about the row the engine is running
// rather than about something that resembles it. The local `indexStore` entry
// is a CACHE of that — CopyIndex and the panels below it all read a
// `SavedIndex` — and it is overwritten from the server on every load.

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";

import TopBar from "../../components/TopBar";
import CopyIndex from "../../components/CopyIndex";
import { fetchCopyBook, upsertAllocation, type CopyBookRow } from "../../lib/copyBook";
import { identityStrat, shortAddress, strategyIdFor } from "../../lib/identityStrat";
import { loadIndexes, upsertIndex, setActiveIndexId } from "../../lib/indexStore";
import { getOwnerAddress } from "../../lib/access";
import { useAuth } from "../../context/AuthContext";

const ADDR_RE = /^0x[0-9a-fA-F]{40}$/;
/** Default size for a leader added from this screen's empty state. Same figure
    the desk's add bar starts at — one number, so the two entry points can't
    seed different-sized positions for the same act ("copy this trader"). */
const DEFAULT_ALLOCATION_USD = 25;

/** The book row, as the strat every panel below reads.
 *
 *  Server fields win: everything `identityStrat` sets is engine truth and is
 *  overwritten on each load, so a stale local copy can never show a size or a
 *  gate the engine isn't using. Fields it does NOT set (backtest-only sizing
 *  mode, trade filters, chart preferences) are local exploration and survive.  */
function materialize(row: CopyBookRow): void {
  const server = identityStrat(row);
  const local = loadIndexes().find((i) => i.id === server.id);
  upsertIndex({
    ...local,
    ...server,
    // SIM vs WALLET is which balance the BACKTEST sizes against — a preview
    // knob with no engine meaning, so the server's constant doesn't clobber it.
    fundsMode: local?.fundsMode ?? server.fundsMode,
  });
  setActiveIndexId(server.id);
  window.dispatchEvent(new Event("strat-updated"));
}

function WorkspaceInner() {
  const raw = String(useParams()?.address ?? "");
  const address = raw.trim().toLowerCase();
  const valid = ADDR_RE.test(address);

  const { auth } = useAuth();
  // Single-owner deployment: the wallet that signed into the gate IS the
  // funded one, and auth.address lags a wallet switch. Same rule as the desk.
  const eoa = getOwnerAddress() ?? auth.address ?? null;

  const [row, setRow] = useState<CopyBookRow | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "absent" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    if (!valid) return;
    try {
      const book = await fetchCopyBook(eoa);
      const found = book.allocations.find((a) => a.address.toLowerCase() === address) ?? null;
      if (!found) {
        setRow(null);
        setState("absent");
        return;
      }
      materialize(found);
      setRow(found);
      setState("ready");
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setState("error");
    }
  }, [address, eoa, valid]);

  // Re-read on the desk's own cadence so an allocation changed here, on the
  // desk, or by an MCP agent lands in the workspace without a reload.
  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), 15_000);
    return () => clearInterval(t);
  }, [load]);

  const addToDesk = useCallback(async () => {
    setAdding(true);
    setError(null);
    try {
      await upsertAllocation({ address, allocationUsd: DEFAULT_ALLOCATION_USD }, eoa);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setAdding(false);
    }
  }, [address, eoa, load]);

  const title = useMemo(
    () => (row?.label?.trim() ? row.label.trim() : `COPY ${shortAddress(address)}`),
    [row, address],
  );

  if (!valid) {
    return (
      <Shell>
        <Notice title="NOT A TRADER ADDRESS">
          <p>
            <code className="font-mono text-pixel-white">{raw || "(empty)"}</code> isn&apos;t a
            40-hex wallet address.
          </p>
          <DeskLink />
        </Notice>
      </Shell>
    );
  }

  if (state === "loading") {
    return (
      <Shell>
        <Notice title="READING THE COPY BOOK">
          <p className="text-pixel-gray">{shortAddress(address)}…</p>
        </Notice>
      </Shell>
    );
  }

  if (state === "error") {
    return (
      <Shell>
        <Notice title="COULDN'T READ THE COPY BOOK" tone="red">
          <p className="font-mono text-[12px] text-red-300 break-words">{error}</p>
          <button onClick={() => void load()} className="pixel-btn text-[12px] px-3 py-1 mt-3">
            RETRY
          </button>
          <DeskLink />
        </Notice>
      </Shell>
    );
  }

  if (state === "absent") {
    return (
      <Shell>
        <Notice title="NOT ON THE DESK">
          <p>
            You aren&apos;t copying{" "}
            <code className="font-mono text-pixel-white">{shortAddress(address)}</code> yet. A
            workspace is a view of a copy-book row, so there has to be a row first.
          </p>
          {error && (
            <p className="font-mono text-[12px] text-red-300 mt-2 break-words">{error}</p>
          )}
          <div className="flex flex-wrap items-center gap-3 mt-3">
            <button onClick={() => void addToDesk()} disabled={adding} className="pixel-btn text-[12px] px-3 py-1 disabled:opacity-40">
              {adding ? "ADDING…" : `COPY WITH $${DEFAULT_ALLOCATION_USD}`}
            </button>
            <Link href={`/traders/${address}`} className="text-pixel-green underline text-[12px]">
              look at their record first →
            </Link>
          </div>
          <DeskLink />
        </Notice>
      </Shell>
    );
  }

  return (
    <div className="max-w-[1920px] mx-auto">
      <TopBar showSearch={false} />
      <div className="p-4">
        {/* Breadcrumb — the desk is the parent, and this is one of its rows.
            Without it the workspace reads as a page you landed on rather than
            as a row you opened. */}
        <div className="flex flex-wrap items-center gap-2 mb-3 text-[12px] font-mono">
          <Link href="/copy" className="text-pixel-gray hover:text-pixel-white">
            ← COPY DESK
          </Link>
          <span className="text-pixel-gray-light">/</span>
          <span className="text-pixel-white tracking-[0.1em]">{title}</span>
          {/* Under this row's gate — see the same link on the desk row. */}
          <Link
            href={`/traders/${address}${
              row?.params?.marketQuery?.trim()
                ? `?mq=${encodeURIComponent(row.params.marketQuery.trim())}`
                : ""
            }`}
            className="text-pixel-gray hover:text-green-400 underline decoration-dotted"
            title={
              row?.params?.marketQuery?.trim()
                ? `Their own trading record in “${row.params.marketQuery.trim()}” — this row's gate`
                : "This leader's own trading record"
            }
          >
            {shortAddress(address)}
          </Link>
        </div>
        <CopyIndex key={strategyIdFor(address)} searchFilter="" />
      </div>
    </div>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="max-w-[1200px] mx-auto">
      <TopBar showSearch={false} />
      <div className="p-4">{children}</div>
    </div>
  );
}

function Notice({
  title,
  tone = "gray",
  children,
}: {
  title: string;
  tone?: "gray" | "red";
  children: React.ReactNode;
}) {
  return (
    <div
      className="pixel-panel p-5 text-[13px] leading-relaxed text-pixel-gray-light"
      style={{ borderColor: tone === "red" ? "rgb(248 113 113 / 0.4)" : undefined }}
    >
      <h1 className="text-[13px] font-semibold tracking-[0.14em] text-pixel-white mb-2">
        {title}
      </h1>
      {children}
    </div>
  );
}

function DeskLink() {
  return (
    <p className="mt-4">
      <Link href="/copy" className="text-pixel-green underline text-[12px]">
        ← back to the copy desk
      </Link>
    </p>
  );
}

export default function CopyWorkspacePage() {
  return (
    <Suspense>
      <WorkspaceInner />
    </Suspense>
  );
}
