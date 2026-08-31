"use client";

// AUTO-SYNC chip — the server-side sync schedule, in the leaderboard header.
//
// Distinct from the ↻ SYNC button next to it: that one streams a fresh
// aggregation into THIS tab, while this chip shows (and edits) the cadence the
// API re-warms the trader leaderboards on in the background — every 5 min by
// default, running whether or not the console is open. Owner-only, like every
// other route (access.rs gates the whole API).

import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchSyncSchedule,
  updateSyncSchedule,
  runSyncNow,
  formatInterval,
  formatCountdown,
  type SyncSchedule,
} from "../lib/syncSchedule";

const POLL_CLOSED_MS = 30_000;
const POLL_OPEN_MS = 5_000;

function formatClock(unixSecs: number | null): string {
  if (!unixSecs) return "—";
  return new Date(unixSecs * 1000).toLocaleTimeString();
}

export default function SyncScheduleChip() {
  const [sched, setSched] = useState<SyncSchedule | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The minutes box IS the control — seeded with the live server value so the
  // panel shows a number you can edit, not an empty box beside eight chips.
  const [minInput, setMinInput] = useState("");
  // Local seconds ticker so the countdown moves between polls.
  const [, setTick] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  // When the current payload was received — the countdown runs off the
  // server's `now` plus this offset, so a skewed browser clock can't make
  // "next sync" read hours out.
  const fetchedAtRef = useRef(Date.now());

  const receive = useCallback((s: SyncSchedule) => {
    fetchedAtRef.current = Date.now();
    setSched(s);
  }, []);

  const load = useCallback(async () => {
    try {
      receive(await fetchSyncSchedule());
    } catch {
      // Old binary without /sync/* or a dropped session — the chip just
      // stays hidden rather than shouting at the user.
      setSched(null);
    }
  }, [receive]);

  useEffect(() => {
    void load();
    const t = setInterval(load, open ? POLL_OPEN_MS : POLL_CLOSED_MS);
    return () => clearInterval(t);
  }, [load, open]);

  useEffect(() => {
    const t = setInterval(() => setTick((v) => v + 1), 1000);
    return () => clearInterval(t);
  }, []);

  // Only fires when the cadence actually changed (our own save, or another
  // tab's), so it never yanks the field out from under someone typing.
  useEffect(() => {
    if (sched) setMinInput(String(Math.round(sched.intervalSecs / 60)));
  }, [sched?.intervalSecs]); // eslint-disable-line react-hooks/exhaustive-deps

  // Close on outside click / Escape — same interaction as NavMenu's dropdown.
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

  const apply = useCallback(
    async (patch: { enabled?: boolean; intervalSecs?: number }) => {
      setBusy(true);
      setError(null);
      try {
        receive(await updateSyncSchedule(patch));
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [receive],
  );

  const runNow = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      receive(await runSyncNow());
      // The scheduler picks the request up within a tick; re-read so the
      // panel flips to RUNNING instead of looking like nothing happened.
      setTimeout(() => void load(), 1500);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [load, receive]);

  if (!sched) return null;

  // Countdown off the SERVER clock: `now` was captured when the payload was
  // built, so add the seconds elapsed locally since then rather than trusting
  // the browser's absolute clock.
  const serverNow = sched.now + Math.floor((Date.now() - fetchedAtRef.current) / 1000);
  const secsToNext = sched.nextRunAt != null ? sched.nextRunAt - serverNow : null;

  const chipColor = !sched.enabled
    ? "border-pixel-border text-pixel-gray"
    : sched.running
      ? "border-green-400 text-green-400"
      : sched.lastError
        ? "border-red-400/60 text-red-400"
        : "border-green-400/40 text-green-400/90";

  // The box is the whole control, so validate it here: SAVE lights up only
  // when the typed number differs from what the server is running and lands
  // inside the accepted window.
  const minMinutes = Math.round(sched.minIntervalSecs / 60);
  const maxMinutes = Math.round(sched.maxIntervalSecs / 60);
  const typedMin = minInput === "" ? null : Number(minInput);
  const inRange = typedMin != null && typedMin >= minMinutes && typedMin <= maxMinutes;
  const dirty = typedMin != null && typedMin !== Math.round(sched.intervalSecs / 60);
  // An empty box is mid-edit, not a mistake — only a typed number can be wrong.
  const badNumber = typedMin != null && !inRange;
  const save = () => {
    if (inRange && dirty) void apply({ intervalSecs: typedMin! * 60 });
  };

  return (
    <div ref={rootRef} className="relative shrink-0">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className={`pixel-btn text-[11px] px-2 py-0.5 font-mono tracking-wider flex items-center gap-1 hover:bg-green-400/10 ${chipColor}`}
        title={
          sched.enabled
            ? `Background sync every ${formatInterval(sched.intervalSecs)} on the server — runs whether or not this console is open. Click to change.`
            : "Background sync is PAUSED — click to re-enable"
        }
      >
        {sched.running && <span className="w-1.5 h-1.5 bg-green-400 animate-pulse" />}
        AUTO {sched.enabled ? formatInterval(sched.intervalSecs) : "OFF"}
        {sched.enabled && secsToNext != null && !sched.running && (
          <span className="text-pixel-gray">· {formatCountdown(secsToNext)}</span>
        )}
        {sched.running && <span className="text-pixel-gray">· syncing</span>}
      </button>

      {open && (
        <div
          className="absolute right-0 top-full mt-1.5 z-50 w-[300px] rounded-[var(--radius-sm)] backdrop-blur-md p-3 space-y-3"
          style={{
            background:
              "linear-gradient(180deg, rgb(var(--pixel-black-rgb)/0.96), rgb(var(--pixel-bg-rgb)/0.94))",
            border: "1px solid var(--border)",
            boxShadow: "0 12px 32px rgba(0,0,0,0.45)",
          }}
        >
          <div className="flex items-center justify-between">
            <span className="text-[12px] text-pixel-white tracking-wider">AUTO-SYNC</span>
            <button
              onClick={() => void apply({ enabled: !sched.enabled })}
              disabled={busy}
              className={`pixel-btn text-[11px] px-2 py-0.5 disabled:opacity-40 ${
                sched.enabled
                  ? "border-green-400 text-green-400 bg-green-400/10"
                  : "border-pixel-border text-pixel-gray hover:text-pixel-white"
              }`}
            >
              {sched.enabled ? "ON" : "PAUSED"}
            </button>
          </div>

          <p className="text-[11px] text-pixel-gray leading-snug">
            The server re-pulls the 1/7/14/30-day trader leaderboards on this
            cadence, in the background — no browser needed.
          </p>

          {/* One control: the number of minutes. */}
          <div className="flex items-center gap-1.5">
            <span className="text-[11px] text-pixel-gray tracking-wider">EVERY</span>
            <input
              type="text"
              inputMode="numeric"
              value={minInput}
              onChange={(e) => setMinInput(e.target.value.replace(/[^0-9]/g, ""))}
              onKeyDown={(e) => {
                if (e.key === "Enter") save();
              }}
              className={`pixel-input-sm w-20 text-center font-mono text-[13px] ${
                badNumber ? "border-red-400 text-red-400" : ""
              }`}
            />
            <span className="text-[11px] text-pixel-gray tracking-wider">MINUTES</span>
            <button
              onClick={save}
              disabled={busy || !dirty || !inRange}
              className="pixel-btn ml-auto text-[11px] px-2.5 py-0.5 border-green-400/60 text-green-400 hover:bg-green-400/10 disabled:opacity-30 disabled:border-pixel-border disabled:text-pixel-gray"
            >
              {dirty ? "SAVE" : "SAVED"}
            </button>
          </div>
          <p className={`text-[10px] font-mono ${badNumber ? "text-red-400" : "text-pixel-gray"}`}>
            {minMinutes}–{maxMinutes} minutes ({Math.round(sched.maxIntervalSecs / 86400)} days max)
          </p>

          {/* Status */}
          <div className="border-t border-pixel-border pt-2 space-y-1 text-[11px] font-mono">
            <Row
              label="LAST"
              value={
                sched.running
                  ? "running…"
                  : sched.lastRunAt
                    ? `${formatClock(sched.lastRunAt)}${
                        sched.lastDurationSecs != null ? ` · ${sched.lastDurationSecs}s` : ""
                      }`
                    : "not since restart"
              }
            />
            <Row
              label="NEXT"
              value={
                !sched.enabled
                  ? "paused"
                  : secsToNext != null
                    ? `${formatClock(sched.nextRunAt)} · ${formatCountdown(secsToNext)}`
                    : "—"
              }
            />
            <Row label="RUNS" value={`${sched.runs}${sched.lastTrigger ? ` · ${sched.lastTrigger}` : ""}`} />
            {sched.lastError && <Row label="ERROR" value={sched.lastError} danger />}
            {error && <Row label="FAILED" value={error} danger />}
          </div>

          <button
            onClick={() => void runNow()}
            disabled={busy || sched.running}
            className="pixel-btn w-full text-[11px] px-2 py-1 border-green-400/60 text-green-400 hover:bg-green-400/10 disabled:opacity-40 disabled:cursor-not-allowed"
            title="Run a full background cycle on the server right now — keeps going even if you close this tab"
          >
            {sched.running ? "SYNC RUNNING…" : "RUN SYNC NOW"}
          </button>
        </div>
      )}
    </div>
  );
}

function Row({ label, value, danger }: { label: string; value: string; danger?: boolean }) {
  return (
    <div className="flex items-start gap-2">
      <span className="text-pixel-gray shrink-0 w-[42px]">{label}</span>
      <span className={`${danger ? "text-red-400" : "text-pixel-gray-light"} break-words`}>
        {value}
      </span>
    </div>
  );
}
