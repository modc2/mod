"use client";

// SCAN HISTORY chip — browse previous scans, see what data each one holds.
//
// The server archives every background warmup cycle as a timestamped scan
// (hourly, aligned to the hour by default). This chip sits in the leaderboard
// header next to the AUTO chip: ‹ › step the board through past scans, the
// label shows WHICH scan is on screen (or LIVE), and the panel is the
// coverage grid — one row per scan, one cell per warmed window, so "which
// data do we have" is answerable at a glance:
//
//   ●  archived that cycle (click the row to view it)
//   ○  skipped — fresh enough already, the data lives in an earlier scan
//   ✕  that window's sweep failed
//
// Viewing a scan is read-only time travel: the board re-reads the archive
// through the same server-side filter/sort path as live, and LIVE returns.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchScans, type ScanMeta } from "../lib/polymarket";
import type { WarmWindow } from "../lib/syncSchedule";

const POLL_MS = 60_000;

/** "09-09 14:00" — date + time, minute precision. Scans are hour-grained, so
    seconds are noise; the date matters once history spans days. */
export function formatScanStamp(unixSecs: number): string {
  const d = new Date(unixSecs * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

interface Props {
  /** The board's current server cache key — which column of the grid applies
      to what's on screen, and which window ‹ › steps through. */
  currentView: WarmWindow;
  /** The scan on screen, null = live. Owned by the board so it can re-fetch. */
  scanId: number | null;
  onSelect: (id: number | null) => void;
}

export default function ScanChip({ currentView, scanId, onSelect }: Props) {
  const [scans, setScans] = useState<ScanMeta[] | null>(null);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetchScans();
      setScans(r.scans);
    } catch {
      // Old binary without /scans — the chip hides rather than erroring.
      setScans(null);
    }
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  // Close on outside click / Escape — same interaction as the AUTO chip.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const curKey = `${currentView.days}:${currentView.minPerDay}:${currentView.pool}`;

  // Scans the CURRENT board can actually step to — ones that archived this
  // window. A skipped/errored cell has nothing to read back, and stepping to
  // a 404 would just bounce the user to live.
  const viewable = useMemo(
    () =>
      (scans ?? []).filter((s) =>
        s.windows.some((w) => w.key === curKey && !w.skipped && !w.error && w.count > 0),
      ),
    [scans, curKey],
  );

  // The grid's columns: every window any listed scan touched, widest last —
  // the union, so a window added to the warm list mid-history still lines up.
  const columns = useMemo(() => {
    const seen = new Map<string, { key: string; days: number; minPerDay: number }>();
    for (const s of scans ?? []) {
      for (const w of s.windows) {
        if (!seen.has(w.key)) seen.set(w.key, { key: w.key, days: w.days, minPerDay: w.minPerDay });
      }
    }
    return [...seen.values()].sort((a, b) => a.days - b.days);
  }, [scans]);

  if (!scans || scans.length === 0) return null;

  const idx = scanId != null ? viewable.findIndex((s) => s.id === scanId) : -1;
  // ‹ older: from live, the newest archived scan; from a scan, the next older.
  const older = idx < 0 ? viewable[0] : viewable[idx + 1];
  // › newer: from a scan, the next newer archived one — or back to live.
  const newerId = idx > 0 ? viewable[idx - 1].id : null;
  const current = scanId != null ? scans.find((s) => s.id === scanId) : undefined;

  const stepBtn =
    "pixel-btn text-[11px] px-1.5 py-0.5 font-mono border-pixel-border text-pixel-gray hover:text-pixel-white hover:border-pixel-white disabled:opacity-30 disabled:hover:text-pixel-gray disabled:hover:border-pixel-border";

  return (
    <div ref={rootRef} className="relative shrink-0 flex items-center gap-1">
      <button
        onClick={() => older && onSelect(older.id)}
        disabled={!older}
        className={stepBtn}
        title={
          older
            ? `View the previous scan — ${formatScanStamp(older.startedAt)}`
            : "No older scan holds this board's window"
        }
      >
        ‹
      </button>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className={`pixel-btn text-[11px] px-2 py-0.5 font-mono tracking-wider ${
          scanId != null
            ? "border-amber-400 text-amber-400 bg-amber-400/10"
            : "border-pixel-border text-pixel-gray hover:text-pixel-white hover:border-pixel-white"
        }`}
        title={
          scanId != null
            ? `Viewing the scan from ${current ? formatScanStamp(current.startedAt) : scanId} — click for the full scan history`
            : `${scans.length} archived scans — click to see which data each one holds`
        }
      >
        {scanId != null && current ? `SCAN ${formatScanStamp(current.startedAt)}` : "SCANS"}
      </button>
      <button
        onClick={() => onSelect(newerId)}
        disabled={scanId == null}
        className={stepBtn}
        title={scanId == null ? "Already live" : newerId ? "View the next newer scan" : "Back to LIVE"}
      >
        ›
      </button>

      {open && (
        <div
          className="absolute right-0 top-full mt-1.5 z-50 w-[340px] rounded-[var(--radius-sm)] backdrop-blur-md p-3 space-y-2"
          style={{
            background:
              "linear-gradient(180deg, rgb(var(--pixel-black-rgb)/0.96), rgb(var(--pixel-bg-rgb)/0.94))",
            border: "1px solid var(--border)",
            boxShadow: "0 12px 32px rgba(0,0,0,0.45)",
          }}
        >
          <div className="flex items-baseline justify-between">
            <span className="text-[12px] text-pixel-white tracking-wider">SCAN HISTORY</span>
            <span className="text-[10px] font-mono text-pixel-gray">{scans.length} scans</span>
          </div>
          <p className="text-[10px] text-pixel-gray leading-snug">
            Every background sync is archived with its timestamp. Click a row to
            see the board as it stood then. ● archived · ○ not re-pulled (an
            earlier scan holds it) · ✕ failed.
          </p>

          {/* Column headers — the warmed windows. */}
          <div className="flex items-center gap-1 font-mono text-[10px] text-pixel-gray pr-1">
            <span className="w-[104px] shrink-0">WHEN</span>
            <span className="flex-1" />
            {columns.map((c) => (
              <span key={c.key} className="w-7 text-center shrink-0" title={`${c.days}-day window${c.minPerDay ? ` · ≥${c.minPerDay}/day` : ""}`}>
                {c.days}D{c.minPerDay ? "+" : ""}
              </span>
            ))}
          </div>

          <div className="max-h-[300px] overflow-y-auto space-y-0.5 pr-1">
            {/* LIVE row — the way back. */}
            <button
              onClick={() => {
                onSelect(null);
                setOpen(false);
              }}
              className={`w-full flex items-center gap-1 font-mono text-[11px] px-1.5 py-1 border text-left ${
                scanId == null
                  ? "border-green-400/60 text-green-400 bg-green-400/10"
                  : "border-pixel-border text-pixel-gray hover:text-pixel-white hover:border-pixel-white"
              }`}
              title="The live board — always the latest data"
            >
              <span className="w-[104px] shrink-0">● LIVE</span>
              <span className="flex-1" />
            </button>

            {scans.map((s) => {
              const cur = s.windows.find((w) => w.key === curKey);
              const openable = !!cur && !cur.skipped && !cur.error && cur.count > 0;
              const selected = s.id === scanId;
              return (
                <button
                  key={s.id}
                  onClick={() => {
                    if (!openable) return;
                    onSelect(s.id);
                    setOpen(false);
                  }}
                  disabled={!openable}
                  className={`w-full flex items-center gap-1 font-mono text-[11px] px-1.5 py-1 border text-left ${
                    selected
                      ? "border-amber-400 text-amber-400 bg-amber-400/10"
                      : openable
                        ? "border-pixel-border text-pixel-gray-light hover:text-pixel-white hover:border-pixel-white"
                        : "border-pixel-border/60 text-pixel-gray opacity-70 cursor-default"
                  }`}
                  title={
                    openable
                      ? `View the board as of ${formatScanStamp(s.startedAt)} (${s.trigger})`
                      : cur?.skipped
                        ? "This cycle didn't re-pull the board you're viewing — step to an older scan that did"
                        : cur?.error
                          ? `Sweep failed: ${cur.error}`
                          : "This scan never covered the board you're viewing"
                  }
                >
                  <span className="w-[104px] shrink-0">
                    {formatScanStamp(s.startedAt)}
                    {s.trigger === "manual" && (
                      <span className="text-pixel-gray"> ·M</span>
                    )}
                    {!s.finishedAt && <span className="text-green-400 animate-pulse"> ●</span>}
                  </span>
                  <span className="flex-1" />
                  {columns.map((c) => {
                    const w = s.windows.find((x) => x.key === c.key);
                    const glyph = !w ? "·" : w.error ? "✕" : w.skipped ? "○" : w.count > 0 ? "●" : "✕";
                    const color = !w
                      ? "text-pixel-gray/40"
                      : w.error || (!w.skipped && w.count === 0)
                        ? "text-red-400"
                        : w.skipped
                          ? "text-pixel-gray"
                          : c.key === curKey
                            ? selected
                              ? "text-amber-400"
                              : "text-green-400"
                            : "text-green-400/60";
                    return (
                      <span
                        key={c.key}
                        className={`w-7 text-center shrink-0 ${color}`}
                        title={
                          !w
                            ? `${c.days}D wasn't on the warm list this cycle`
                            : w.error
                              ? `${c.days}D failed: ${w.error}`
                              : w.skipped
                                ? `${c.days}D not re-pulled — data from ${formatScanStamp(w.syncedAt)} was still current`
                                : `${c.days}D · ${w.count} traders · synced ${formatScanStamp(w.syncedAt)}`
                        }
                      >
                        {glyph}
                      </span>
                    );
                  })}
                </button>
              );
            })}
          </div>

          <p className="text-[10px] font-mono text-pixel-gray leading-snug">
            hourly scans land at :00 — cadence &amp; windows are set in the AUTO
            chip · history is pruned oldest-first (~a week kept)
          </p>
        </div>
      )}
    </div>
  );
}
