"use client";

// The mode controls, shared by every screen that can trade.
//
// One switch, one chip, one banner, one legend — imported by the COPY DESK and
// by the strat workspace's LIVE panel so the two can't invent different
// controls for the same decision again. The vocabulary and colours all come
// from lib/tradingMode.ts; nothing here decides what TEST or LIVE mean.
//
// The shape both screens now use:
//
//     [ PAPER | REAL ]   [ START ] / [ STOP ]
//      ─── mode ───        ── run state ──
//
// Two controls because there are two independent facts. The desk used to fuse
// them (a DRY RUN button and a LIVE button that both *started* the engine, and
// no way to change your mind without stopping); the workspace used to split
// them but call the start button GO LIVE, which is the name of the other axis.

import {
  MODE, MODES, MODE_LEGEND, confirmGoLive, describeSession, liveBlockedReason,
  type RunState, type TradingMode,
} from "../lib/tradingMode";

// ── The switch ──

export function ModeSwitch({
  mode, onPick, running, canGoLive, subject, amountUsd, disabled, size = "md",
}: {
  mode: TradingMode;
  onPick: (mode: TradingMode) => void;
  /** True when a session is already up. Decides who owns the confirm: a
      running session goes hot the instant LIVE is picked, so it confirms
      here. A stopped one arms silently and the START button confirms. */
  running: boolean;
  /** False ⇒ LIVE is offered but locked, with the reason in its tooltip.
      Locked-and-explained beats hidden: "why can't I trade for real" is
      answerable, "where did the button go" isn't. */
  canGoLive: boolean;
  /** What is about to trade, for the confirm — a name, an address, "the desk". */
  subject: string;
  amountUsd?: number | null;
  disabled?: boolean;
  size?: "sm" | "md";
}) {
  const pad = size === "sm" ? "px-2 py-[2px] text-[10px]" : "px-2.5 py-1 text-[11px]";
  const blocked = liveBlockedReason(canGoLive);

  return (
    <div
      className="inline-flex items-center rounded-[4px] border border-pixel-border/70 p-[2px] gap-[2px]"
      role="group"
      aria-label="Trading mode"
    >
      {MODES.map((m) => {
        const active = m === mode;
        const locked = m === "LIVE" && !canGoLive;
        return (
          <button
            key={m}
            type="button"
            disabled={disabled || locked}
            aria-pressed={active}
            title={
              locked
                ? blocked!
                : active
                  ? MODE[m].active
                  : running
                    ? MODE[m].pick
                    : `Arm ${MODE[m].label} — this is the mode START will use. ${MODE[m].meaning}.`
            }
            onClick={() => {
              if (active || disabled || locked) return;
              // A live session flips the moment this is clicked, so the
              // confirm belongs here. A stopped one is only being armed —
              // START asks, once, right before anything can fill.
              if (m === "LIVE" && running && !confirmGoLive(subject, amountUsd)) return;
              onPick(m);
            }}
            className={`font-mono tracking-[0.12em] rounded-[3px] border transition-colors ${pad} ${
              active
                ? MODE[m].seg
                : "border-transparent text-pixel-gray hover:text-pixel-white hover:bg-pixel-white/[0.05]"
            } disabled:opacity-30 disabled:cursor-not-allowed`}
          >
            {MODE[m].label}
          </button>
        );
      })}
    </div>
  );
}

// ── The chip ──

/** Run state and mode in one badge — "STOPPED", "RUNNING" as `REAL ●`,
    "PAUSED · PAPER". Used on desk rows, the panel header and the nav tab, so
    all three agree about what a session is doing. */
export function SessionChip({
  run, mode, className = "",
}: {
  run: RunState;
  mode: TradingMode;
  className?: string;
}) {
  const s = describeSession(run, mode);
  return (
    <span
      title={s.title}
      className={`inline-flex items-center font-mono text-[9px] tracking-[0.12em] border rounded-[3px] px-1.5 py-[1px] ${s.chip} ${className}`}
    >
      {s.text}
    </span>
  );
}

// ── The legend ──

/** The two meanings, spelled out next to the switch. Cheap to render and it
    removes the entire class of "what does dry run mean" question. */
export function ModeLegend({ className = "" }: { className?: string }) {
  return (
    <span className={`font-mono text-[10px] text-pixel-gray ${className}`} title={MODE_LEGEND}>
      {MODE_LEGEND}
    </span>
  );
}

// ── The banner ──

/** "You are not trading."
 *
 *  Shown when a TEST session is throwing away mirrors that cleared every
 *  filter — the failure no gate tally can catch, because the mirrors weren't
 *  gated, they were simply never sent. Red because it is almost always
 *  someone who meant to be live; the fix button goes through the same confirm
 *  as every other route to real money. */
export function NotTradingBanner({
  count, subject, amountUsd, onGoLive,
}: {
  count: number;
  subject: string;
  amountUsd?: number | null;
  onGoLive: () => void;
}) {
  return (
    <div className="pixel-panel border-2 border-red-400/70 bg-red-400/10 p-3 flex items-start gap-3 flex-wrap">
      <span className="text-red-400 text-xl leading-none mt-0.5">⚠</span>
      <div className="flex-1 min-w-[240px]">
        <div className="text-sm font-bold text-red-400">
          {MODE.TEST.label} MODE — {count} mirror{count === 1 ? "" : "s"} passed every filter and{" "}
          {count === 1 ? "was" : "were"} NOT placed. You are not trading.
        </div>
        <div className="text-xs text-pixel-muted mt-1">
          Your filters are fine — the session is on {MODE.TEST.label}, so the engine logs what it would
          have done instead of sending it to the CLOB. Nothing is queued: these mirrors are
          gone, not deferred.
        </div>
        <div className="mt-2 flex items-center gap-2 flex-wrap">
          <button
            onClick={() => { if (confirmGoLive(subject, amountUsd)) onGoLive(); }}
            title={MODE.LIVE.pick}
            className="px-2.5 py-1 rounded border border-red-400/70 bg-red-400/15 text-red-300 hover:bg-red-400/25 text-[11px] font-mono tracking-[0.14em]"
          >
            SWITCH TO {MODE.LIVE.label} →
          </button>
          <span className="text-[10.5px] font-mono text-pixel-muted/70">
            (the {MODE.TEST.label}|{MODE.LIVE.label} switch in the header flips it back any time)
          </span>
        </div>
      </div>
    </div>
  );
}
