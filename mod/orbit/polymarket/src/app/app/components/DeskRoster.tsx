"use client";

// THE DESK ROSTER — who you've selected, kept in view while you're on the desk.
//
// The desk page scrolls: check five names in the finder and the tray, the
// rows and the book all drift off-screen. The docked column doesn't scroll
// away, so on /copy it carries the answer to "who have I actually picked?" —
// two lists, in commitment order:
//
//   COPY BOOK  the committed selection: every allocation, its $, its market
//              gate, and whether it is running right now (and in which mode).
//
// The un-committed half — the finder's checked shortlist — is the SELECTION
// tray (SelectionTray.tsx), mounted just above this in the same column.
//
// Reads only. The desk is where the controls live; duplicating START/STOP in
// a 340px strip is how two switches disagree. Each name links to its page.
// The HOW COPYING WORKS walkthrough that used to fill this column folds
// below — it earns the whole strip only while there is nothing to show.

import Link from "next/link";

import { useAuth } from "../context/AuthContext";
import { getOwnerAddress } from "../lib/access";
import { useCopyBook } from "../lib/useCopyBook";
import { usePicks } from "../lib/pickStore";
import { shortAddress } from "../lib/identityStrat";
import { describeMarketQuery } from "../lib/marketTypes";
import type { CopyBookRow } from "../lib/copyBook";
import { MODE } from "../lib/tradingMode";
import DeskGuide from "./DeskGuide";

function fmtUsd(v: number): string {
  return `$${v.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

export default function DeskRoster() {
  const { auth } = useAuth();
  // Same rule as the desk: the wallet that signed the gate IS the funded one.
  const eoa = getOwnerAddress() ?? auth.address ?? null;
  const { book, rows } = useCopyBook(eoa);
  // The interactive SELECTED tray sits ABOVE this block (SelectionTray in
  // UserSidebar) — here picks only decide whether the walkthrough folds.
  const { picks } = usePicks();

  const hasAny = picks.length > 0 || rows.length > 0;
  const t = book?.totals;

  return (
    <div className="flex flex-col">
      <div className="p-3 space-y-1.5 border-b border-pixel-gray/20">
        <div className="flex items-baseline gap-2">
          <span className="font-mono text-[9.5px] tracking-[0.14em] text-pixel-gray">
            COPY BOOK{rows.length > 0 ? ` · ${rows.length}` : ""}
          </span>
          {t && rows.length > 0 && (
            <span className="font-mono text-[9px] text-pixel-gray">
              {fmtUsd(t.allocatedUsd)} allocated
              {t.running > 0 ? (
                <>
                  {" · "}
                  {t.executing > 0 ? (
                    <span className="text-pixel-green">{t.executing} LIVE</span>
                  ) : (
                    <span className="text-amber-400">{t.running} on {MODE.TEST.label}</span>
                  )}
                </>
              ) : (
                " · none running"
              )}
            </span>
          )}
        </div>
        {book === null ? (
          <div className="font-mono text-[10px] text-pixel-gray">reading the book…</div>
        ) : rows.length === 0 ? (
          <div className="font-mono text-[10px] text-pixel-gray">
            nobody yet — the box on the desk is the way in
          </div>
        ) : (
          rows.map((row) => <RosterRow key={row.address} row={row} />)
        )}
      </div>

      {/* The walkthrough. Open when the column has nothing else to say,
          folded to one line once there is a selection to show. */}
      {hasAny ? (
        <details>
          <summary className="p-3 font-mono text-[9.5px] tracking-[0.14em] text-pixel-gray cursor-pointer select-none hover:text-pixel-gray-light">
            HOW COPYING WORKS ▾
          </summary>
          <DeskGuide compact />
        </details>
      ) : (
        <DeskGuide compact />
      )}
    </div>
  );
}

/** One committed name: who, gated to what, with how much, doing what. */
function RosterRow({ row }: { row: CopyBookRow }) {
  const gate = row.params?.marketQuery?.trim() ?? "";
  return (
    <div className="flex items-baseline gap-2 font-mono text-[10px]">
      <Link
        href={`/copy/${row.address}`}
        className="text-pixel-gray-light hover:text-pixel-green normal-case shrink-0"
        title={`Open ${row.name} — backtest, live session and wallet`}
      >
        {shortAddress(row.address)} ↗
      </Link>
      <span
        className="text-pixel-gray truncate min-w-0"
        title={gate ? `Only their “${gate}” trades are mirrored` : "Every market they trade"}
      >
        {gate ? `IN ${describeMarketQuery(gate)}` : "ALL MARKETS"}
      </span>
      <span className="flex-1" />
      <span className="text-pixel-white shrink-0">{fmtUsd(row.allocationUsd)}</span>
      <RowState row={row} />
    </div>
  );
}

/** The row's state in one word, colored the way the whole console colors it:
    LIVE green (real orders), TEST amber (following, sending nothing), OFF
    gray, PAUSED dim (kept out of START ALL). */
function RowState({ row }: { row: CopyBookRow }) {
  if (!row.enabled) {
    return <span className="shrink-0 text-[9px] tracking-[0.1em] text-pixel-gray/70" title="Paused — in the book, kept out of START ALL">PAUSED</span>;
  }
  if (row.live?.running) {
    return row.live.autoExecute ? (
      <span className="shrink-0 text-[9px] tracking-[0.1em] text-pixel-green" title={MODE.LIVE.active}>{MODE.LIVE.label}</span>
    ) : (
      <span className="shrink-0 text-[9px] tracking-[0.1em] text-amber-400" title={MODE.TEST.active}>{MODE.TEST.label}</span>
    );
  }
  return <span className="shrink-0 text-[9px] tracking-[0.1em] text-pixel-gray" title="Not started">OFF</span>;
}
