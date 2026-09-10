// THE MODE LAW — one definition of "test" vs "live", for every screen.
//
// Before this file the console had two consoles' worth of vocabulary for the
// same two states. The COPY DESK said DRY RUN / LIVE, the strat workspace said
// DRY RUN / EXECUTING ●, the nav said TEST / LIVE, the checklist said
// CAPITAL·PAPER, the backtest tab said SIMULATED. Worse, the word LIVE meant
// two different things one click apart: on the desk it meant "real money", in
// the nav it meant "the engine screen" — so a LIVE tab could quite happily be
// showing a session that had never placed an order.
//
// So: two axes, never conflated again.
//
//   RUN STATE   is the engine on?        STOPPED · RUNNING · PAUSED · ERROR
//   MODE        is the money real?       PAPER · REAL
//
// The words matter as much as the split. The mode used to be spelled TEST/LIVE
// — the same two words as the nav's BACKTEST and LIVE tabs — so the console
// said LIVE twice on one screen meaning two different things ("this is the
// engine page" and "this is real money"), and a header reading
// `LIVE … START · TEST` was three collisions in one row. PAPER and REAL cannot
// be confused with a page name: they answer only "is the money real?".
//
// A session has both, always. "Start" and "go live" are different verbs and
// different buttons. Everything user-facing about either axis — the word, the
// colour, the tooltip, the confirm — comes from here, so the two screens can't
// drift apart again.
//
// The wire keeps its own spelling: the engine config field is `autoExecute`,
// and `/copy/start` answers `mode: "LIVE" | "DRY RUN"` (the MCP tools and the
// README speak that dialect). `modeOf()` is the only translation point.

// The ids stay TEST|LIVE: they key the wire, the localStorage entries, the
// engine config and every `mode === "LIVE"` check in the app. Only the WORDS
// on screen changed — read `MODE[m].label`, never the id.
export type TradingMode = "TEST" | "LIVE";
export type RunState = "STOPPED" | "RUNNING" | "PAUSED" | "ERROR";

export interface ModeMeta {
  /** The word. The ONLY word — no synonyms anywhere in the UI. */
  label: string;
  /** Status glyph: hollow for simulated, filled for real. */
  dot: string;
  /** One line saying what the mode does. Same line on every screen. */
  meaning: string;
  /** Tooltip for the segment when this mode is already the active one. */
  active: string;
  /** Tooltip for the segment that switches INTO this mode. */
  pick: string;
  /** Status-chip classes (border + text + fill). */
  chip: string;
  /** Active-segment classes for the TEST|LIVE switch. */
  seg: string;
}

// Colour law, applied here and nowhere else:
//
//   amber  = TEST. Nothing real is happening. Also the BACKTEST accent — both
//            are "no money moved", and one colour for that is the point.
//   green  = LIVE. Real orders, real USDC, working as intended.
//   red    = STOP, errors, and the arming confirm. NOT the steady live state:
//            the old UI painted EXECUTING ● red, which put the same colour on
//            "you are trading" and "halt trading" and made red mean nothing.
//
// Nav accents are deliberately NOT from this palette (the subtab rails stay
// cyan/green as pure wayfinding) — colour here always answers "is the money
// real?", never "which screen am I on?".
export const MODE: Record<TradingMode, ModeMeta> = {
  TEST: {
    label: "PAPER",
    dot: "○",
    meaning: "live feed, pretend fills — no orders are sent, no money moves",
    active: "PAPER — the engine computes every mirror against the live feed and places none. No money can move.",
    pick: "Switch to PAPER — the engine keeps computing mirrors but stops sending them. No money can move.",
    chip: "border-amber-400/60 text-amber-400 bg-amber-400/[0.08]",
    seg: "border-amber-400/60 text-amber-400 bg-amber-400/[0.10]",
  },
  LIVE: {
    label: "REAL",
    dot: "●",
    meaning: "real orders on Polymarket, funded by your trading wallet",
    active: "REAL — mirrors are being sent to the CLOB as real orders against your wallet's USDC.",
    pick: "Switch to REAL — mirrors start being sent to the CLOB as REAL orders against your wallet's USDC.",
    chip: "border-green-400/70 text-green-400 bg-green-400/[0.08]",
    seg: "border-green-400/70 text-green-400 bg-green-400/[0.10]",
  },
};

/** Both modes in switch order, safest first. */
export const MODES: TradingMode[] = ["TEST", "LIVE"];

/** The one-line explainer that sits under every mode switch. */
export const MODE_LEGEND = `${MODE.TEST.label} ${MODE.TEST.dot} ${MODE.TEST.meaning}  ·  ${MODE.LIVE.label} ${MODE.LIVE.dot} ${MODE.LIVE.meaning}`;

/** The word for a mode. Use this anywhere a mode is named in the UI — never
    the id, which is wire spelling and reads as a page name. */
export function labelOf(mode: TradingMode): string {
  return MODE[mode].label;
}

/** Wire (`autoExecute`) → screen. The only place the translation happens. */
export function modeOf(autoExecute: boolean | null | undefined): TradingMode {
  return autoExecute ? "LIVE" : "TEST";
}

/** Screen → wire. */
export function autoExecuteFor(mode: TradingMode): boolean {
  return mode === "LIVE";
}

/** What a fresh session should be armed to.
 *
 *  Capital decides it, and the answer is SHOWN before anything starts rather
 *  than inferred at the moment of starting. Both halves of that matter:
 *
 *  • Unfunded ⇒ TEST, and LIVE is not even offered. An unfunded session that
 *    "goes live" places nothing and reports success, which is the most
 *    confusing state the engine has.
 *  • Funded ⇒ LIVE. Defaulting a funded wallet to TEST is how a session sat
 *    from 2026-08-01 to 08-08 logging hundreds of "would BUY" lines a day and
 *    placing nothing — the single worst bug this console has shipped. The fix
 *    is not to hide the default, it's to show it and confirm it.
 */
export function armedDefault(canGoLive: boolean): TradingMode {
  return canGoLive ? "LIVE" : "TEST";
}

/** Why LIVE is unavailable, or null when it's fine to arm. */
export function liveBlockedReason(canGoLive: boolean): string | null {
  return canGoLive
    ? null
    : "No capital behind this yet — fund the trading wallet (or give this trader an allocation) and REAL unlocks.";
}

function fmtUsd(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "";
  return `$${Math.abs(v).toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
}

/** The ONE confirm shown on the way to real money.
 *
 *  Every path to LIVE goes through here — the desk's per-row switch, the
 *  desk's START ALL, the strat workspace's START, the "you are not trading"
 *  banner. Before this, three of those four confirmed and the fourth (a funded
 *  wallet pressing GO LIVE) armed real orders silently. Same words, same
 *  friction, every time.
 *
 *  @param subject what is about to trade — "0xab…cd", "BTC MOMENTUM", "all 4
 *                 enabled traders".
 *  @param amountUsd the money at stake, when the caller knows it.
 */
export function confirmGoLive(subject: string, amountUsd?: number | null): boolean {
  const money = fmtUsd(amountUsd);
  if (typeof window === "undefined") return false;
  return window.confirm(
    `REAL MONEY.\n\n` +
      `${subject} will start placing REAL orders on Polymarket` +
      (money ? `, sized against ${money}` : "") +
      `.\n\nFills are real, losses are real, and an order that fills cannot be ` +
      `taken back. Switching back to PAPER stops new orders — it does not close ` +
      `positions already open.\n\nTrade for real?`,
  );
}

/** Status text for the two axes together, as one chip.
 *
 *  Run state is the noun, mode is the adjective: a stopped session has no
 *  mode worth reporting, a running one is always one or the other. This is
 *  what makes "RUNNING · TEST" sayable — the state the old UI could only
 *  express as a green LIVE tab next to an amber DRY RUN pill.
 */
export function describeSession(
  run: RunState,
  mode: TradingMode,
  opts?: { paused?: boolean },
): { text: string; chip: string; title: string } {
  if (run === "ERROR") {
    return {
      text: "ERROR",
      chip: "border-red-400/70 text-red-400 bg-red-400/[0.08]",
      title: "The engine stopped on an error — see the message above the tabs.",
    };
  }
  if (run === "STOPPED") {
    return {
      text: "STOPPED",
      chip: "border-pixel-gray/40 text-pixel-gray",
      title: `Not running. It will start in ${MODE[mode].label} — ${MODE[mode].meaning}.`,
    };
  }
  if (run === "PAUSED" || opts?.paused) {
    return {
      text: `PAUSED · ${MODE[mode].label}`,
      chip: "border-amber-400/60 text-amber-400 bg-amber-400/[0.08]",
      title: `Polling is suspended. On resume it continues in ${MODE[mode].label} — ${MODE[mode].meaning}.`,
    };
  }
  return {
    text: `${MODE[mode].label} ${MODE[mode].dot}`,
    chip: MODE[mode].chip,
    title: MODE[mode].active,
  };
}

/** Roll-up phrasing for a desk of N sessions — "3 REAL · 2 PAPER". Kept here
    so the desk's totals use the same two words as its rows. */
export function describeFleet(running: number, live: number): string {
  if (running === 0) return "none running";
  const test = Math.max(0, running - live);
  if (live === 0) return `${test} on ${MODE.TEST.label}`;
  if (test === 0) return `${live} ${MODE.LIVE.label}`;
  return `${live} ${MODE.LIVE.label} · ${test} ${MODE.TEST.label}`;
}
