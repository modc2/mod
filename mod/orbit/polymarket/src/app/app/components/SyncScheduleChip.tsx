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
  describeWindow,
  windowKey,
  hasWindow,
  type SyncSchedule,
  type WarmWindow,
} from "../lib/syncSchedule";

const POLL_CLOSED_MS = 30_000;
const POLL_OPEN_MS = 5_000;

function formatClock(unixSecs: number | null): string {
  if (!unixSecs) return "—";
  return new Date(unixSecs * 1000).toLocaleTimeString();
}

interface Props {
  /** The board the console is looking at RIGHT NOW — (days, minPerDay, pool)
      is the server's cache key, so this is what "cache this view" adds to the
      warm list. Omit and the panel only edits the list it already has. */
  currentView?: WarmWindow;
}

export default function SyncScheduleChip({ currentView }: Props = {}) {
  const [sched, setSched] = useState<SyncSchedule | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The minutes box IS the control — seeded with the live server value so the
  // panel shows a number you can edit, not an empty box beside eight chips.
  const [minInput, setMinInput] = useState("");
  // The ADD row — a draft window, kept as strings so a half-typed number
  // isn't a validation error yet.
  const [draft, setDraft] = useState({ days: "", minPerDay: "0", pool: "2000" });
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
    async (patch: { enabled?: boolean; intervalSecs?: number; windows?: WarmWindow[] }) => {
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

  // ── The warm list ──
  // A board answers instantly only if its (days, minPerDay, pool) triple was
  // aggregated by a sweep. So this list is the real answer to "why is this
  // filter still spinning": it isn't on it. Editing it is a whole-list
  // replace, matching the server's PATCH semantics.
  const windows = sched.windows;
  const full = windows.length >= sched.maxWindows;
  const setWindows = (next: WarmWindow[]) => void apply({ windows: next });
  const removeWindow = (key: string) => {
    const next = windows.filter((w) => windowKey(w) !== key);
    // The server rejects an empty list (a sweep that warms nothing means
    // every board goes cold) — say so here instead of round-tripping for it.
    if (next.length === 0) {
      setError("keep at least one window warm — an empty list means every board loads cold");
      return;
    }
    setWindows(next);
  };

  const draftDays = Number(draft.days);
  const draftPerDay = draft.minPerDay === "" ? 0 : Number(draft.minPerDay);
  const draftPool = Number(draft.pool);
  const draftWindow: WarmWindow | null =
    Number.isFinite(draftDays) && draftDays >= 1 && draftDays <= sched.maxDays &&
    Number.isFinite(draftPerDay) && draftPerDay >= 0 &&
    Number.isFinite(draftPool) && draftPool >= sched.minPool && draftPool <= sched.maxPool
      ? { days: draftDays, minPerDay: draftPerDay, pool: draftPool }
      : null;
  const draftDuplicate = !!draftWindow && hasWindow(windows, draftWindow);
  const addDraft = () => {
    if (!draftWindow || draftDuplicate || full) return;
    setWindows([...windows, draftWindow]);
    setDraft({ days: "", minPerDay: "0", pool: String(sched.maxPool) });
  };

  // The board on screen, when it isn't already warmed — the one-click version
  // of the ADD row, and the reason most people will open this panel.
  const currentWarm = currentView ? hasWindow(windows, currentView) : true;

  return (
    <div ref={rootRef} className="relative shrink-0">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className={`pixel-btn text-[11px] px-2 py-0.5 font-mono tracking-wider flex items-center gap-1 hover:bg-green-400/10 ${chipColor}`}
        title={
          sched.enabled
            ? `Background sync every ${formatInterval(sched.intervalSecs)} on the server — runs whether or not this console is open.${
                sched.running
                  ? " Syncing now."
                  : secsToNext != null
                    ? ` Next in ${formatCountdown(secsToNext)}.`
                    : ""
              } Click to change.`
            : "Background sync is PAUSED — click to re-enable"
        }
      >
        {/* Countdown / "syncing" live in the tooltip + panel, not the label —
            the collapsed chip stays a fixed narrow width so the header row
            never wraps. The pulse dot is the running signal. */}
        {sched.running && <span className="w-1.5 h-1.5 bg-green-400 animate-pulse" />}
        AUTO {sched.enabled ? formatInterval(sched.intervalSecs) : "OFF"}
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
            The server re-pulls the leaderboards below on this cadence, in the
            background — no browser needed.
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

          {/* ── CACHED VIEWS ──
              Which filters answer from cache. A board whose window isn't here
              was never aggregated, so opening it starts a ~10-minute rebuild
              the fleet activator kills — it spins forever instead of loading.
              Pinning the view you browse is what makes it instant. */}
          <div className="border-t border-pixel-border pt-2 space-y-1.5">
            <div className="flex items-baseline justify-between">
              <span className="text-[12px] text-pixel-white tracking-wider">CACHED VIEWS</span>
              <span className="text-[10px] font-mono text-pixel-gray">
                {windows.length}/{sched.maxWindows}
              </span>
            </div>
            <p className="text-[10px] text-pixel-gray leading-snug">
              Only these DAYS · MIN-PER-DAY · POOL combinations are pre-built.
              Any other filter loads cold — and usually doesn&apos;t finish.
            </p>

            <div className="space-y-1">
              {windows.map((w) => {
                const key = windowKey(w);
                const isCurrent = currentView && windowKey(currentView) === key;
                return (
                  <div
                    key={key}
                    className={`flex items-center gap-1.5 font-mono text-[11px] px-1.5 py-0.5 border ${
                      isCurrent
                        ? "border-green-400/60 text-green-400 bg-green-400/10"
                        : "border-pixel-border text-pixel-gray-light"
                    }`}
                    title={
                      isCurrent
                        ? "The board you're looking at — it answers from cache"
                        : `Cache key ${key}`
                    }
                  >
                    <span className="truncate">{describeWindow(w)}</span>
                    <span className="flex-1" />
                    {isCurrent && <span className="text-[10px] shrink-0">ON SCREEN</span>}
                    <button
                      onClick={() => removeWindow(key)}
                      disabled={busy || windows.length <= 1}
                      title={
                        windows.length <= 1
                          ? "The last window can't be removed — the sweep would warm nothing"
                          : "Stop pre-building this board"
                      }
                      className="shrink-0 px-1 text-pixel-gray hover:text-red-400 disabled:opacity-30 disabled:hover:text-pixel-gray"
                    >
                      ✕
                    </button>
                  </div>
                );
              })}
            </div>

            {/* One click for the common case: cache what I'm looking at. */}
            {currentView && !currentWarm && (
              <button
                onClick={() => {
                  if (full) {
                    setError(`already warming ${sched.maxWindows} views — remove one first`);
                    return;
                  }
                  setWindows([...windows, currentView]);
                }}
                disabled={busy}
                className="pixel-btn w-full text-[11px] px-2 py-1 border-green-400/60 text-green-400 hover:bg-green-400/10 disabled:opacity-40"
                title="Pre-build this exact board every cycle so it loads from cache instead of rebuilding"
              >
                + CACHE THIS VIEW ({describeWindow(currentView)})
              </button>
            )}

            {/* Any other combination, typed. */}
            <div className="flex items-center gap-1 font-mono text-[11px]">
              <input
                type="text"
                inputMode="numeric"
                value={draft.days}
                onChange={(e) => setDraft((d) => ({ ...d, days: e.target.value.replace(/[^0-9]/g, "") }))}
                onKeyDown={(e) => { if (e.key === "Enter") addDraft(); }}
                placeholder="D"
                title={`Window in days (1–${sched.maxDays})`}
                className="pixel-input-sm w-10 text-center text-[11px]"
              />
              <span className="text-pixel-gray">·</span>
              <input
                type="text"
                inputMode="decimal"
                value={draft.minPerDay}
                onChange={(e) => setDraft((d) => ({ ...d, minPerDay: e.target.value.replace(/[^0-9.]/g, "") }))}
                onKeyDown={(e) => { if (e.key === "Enter") addDraft(); }}
                title="Minimum trades per day a trader must average to enter the board"
                className="pixel-input-sm w-12 text-center text-[11px]"
              />
              <span className="text-pixel-gray text-[10px]">/DAY</span>
              <span className="text-pixel-gray">·</span>
              <input
                type="text"
                inputMode="numeric"
                value={draft.pool}
                onChange={(e) => setDraft((d) => ({ ...d, pool: e.target.value.replace(/[^0-9]/g, "") }))}
                onKeyDown={(e) => { if (e.key === "Enter") addDraft(); }}
                title={`Candidate pool size (${sched.minPool}–${sched.maxPool})`}
                className="pixel-input-sm w-14 text-center text-[11px]"
              />
              <button
                onClick={addDraft}
                disabled={busy || !draftWindow || draftDuplicate || full}
                title={
                  full
                    ? `Already warming ${sched.maxWindows} views — remove one first`
                    : draftDuplicate
                      ? "Already cached"
                      : "Add this board to the warm list"
                }
                className="pixel-btn ml-auto text-[11px] px-2 py-0.5 border-pixel-border text-pixel-gray hover:text-pixel-white hover:border-pixel-white disabled:opacity-30"
              >
                ADD
              </button>
            </div>
            <p className="text-[10px] font-mono text-pixel-gray">
              each view is a full sweep — more views, longer cycle
            </p>
          </div>

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
