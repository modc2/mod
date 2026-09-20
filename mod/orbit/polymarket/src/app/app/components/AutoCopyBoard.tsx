"use client";

// AUTO COPY — the board the background worker keeps for you: one copy-trade
// replay per current top-PnL trader, no forking, no manifest, nothing to
// publish. Renders under the BACKTEST workspace.
//
// The two dials are the whole interface: TRAIN and TEST window lengths, in
// days. The windows are back-to-back BY CONSTRUCTION — train ends at the
// exact instant test begins — so they cannot overlap whatever is typed; the
// bar under the dials draws the split so that's visible rather than asserted.
// The server clamps whatever is saved into train ≥ 1, test ≥ 1 and
// train + test ≤ 30 (the trade feed's own ceiling) and answers with what it
// actually stored, which is what the form then shows.
//
// Per trader, the card answers one question, the same one the walk-forward
// asks everywhere else: the TRAIN column is why they were picked, the TEST
// column is whether that kept paying AFTER the training data ran out, and
// HOLDOUT is the test window replayed with the trader's stats frozen at its
// start (the deployable number). Defaults: 20d train + 10d test = 30 days
// back with the last 10 as the backtest.

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import { getAccessToken } from "../lib/access";
import { shortAddress } from "../lib/identityStrat";
import {
  DEFAULT_STEADY_FLOOR,
  steadyEnough,
  type ForwardVerdict,
  type HubBacktest,
  type WinRecord,
} from "../lib/hubReplay";
import Sparkline from "./Sparkline";

const API = `${process.env.NEXT_PUBLIC_BASE_PATH ?? ""}/_api/hub/autocopy`;
const POLL_MS = 60_000;

interface Settings {
  enabled: boolean;
  count: number;
  trainDays: number;
  testDays: number;
}

interface Card {
  address: string;
  rank: number;
  train: HubBacktest;
  test: HubBacktest;
  verdict: ForwardVerdict;
  at: number;
}

interface Snapshot {
  settings: Settings;
  maxLookbackDays: number;
  status: { at: number; running: boolean; roster: string[]; settings: Settings; error?: string };
  cards: Card[];
}

function authHeaders(): Record<string, string> {
  const t = getAccessToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

const fmtUsd = (n: number) => `${n < 0 ? "−" : ""}$${Math.abs(n).toFixed(2)}`;
const fmtDay = (ms: number) =>
  new Date(ms).toLocaleDateString("en-US", { month: "short", day: "numeric" }).toUpperCase();

const VERDICT_STYLE: Record<ForwardVerdict, { label: string; cls: string; hint: string }> = {
  held: { label: "HELD", cls: "text-green-400 border-green-400/50", hint: "Profitable in the train window AND the test window — confirmed out-of-sample." },
  faded: { label: "FADED", cls: "text-red-400 border-red-400/50", hint: "Made money in the train window, lost it in the test window — the look of a one-good-run trader." },
  recovered: { label: "RECOVERED", cls: "text-cyan-400 border-cyan-400/50", hint: "Lost in the train window, made money in the test window." },
  "no-edge": { label: "NO EDGE", cls: "text-red-400/80 border-red-400/40", hint: "Lost in both windows." },
  untested: { label: "UNTESTED", cls: "text-pixel-gray border-pixel-border", hint: "No trades in the train window — nothing to confirm." },
  stalled: { label: "STALLED", cls: "text-amber-300 border-amber-300/50", hint: "Profitable in the train window, then stopped trading in the test window." },
  idle: { label: "IDLE", cls: "text-pixel-gray border-pixel-border", hint: "No trades in either window." },
};

/** "WIN 63% · STEADY 4/5" — how the TEST P&L was made, not just how much of
    it there was. WIN is the hit rate over the legs the window decided; STEADY
    is the shape — of the stretches this trader traded in, the share that came
    out AHEAD in dollars. Read together they say which KIND of book it is: 40%
    / 6-of-6 is a longshot buyer being paid for its tail, 100% / 1-of-5 is a
    scalper whose expiries eat it alive. UNRATED (too few decided legs, or too
    few stretches to see a shape) is a third state, never a zero. */
function SteadyCell({ bt, floor }: { bt: HubBacktest; floor: number }) {
  const w: WinRecord | undefined = bt.wins;
  const rated = !!w && w.consistency >= 0;
  const pct = w && w.winRate >= 0 ? Math.round(w.winRate * 100) : null;
  const tone = !rated
    ? "text-pixel-gray"
    : w!.consistency >= 0.75
      ? "text-green-400"
      : w!.consistency >= 0.5
        ? "text-amber-400"
        : "text-red-400";
  return (
    <span
      className="flex flex-col items-end shrink-0 min-w-[76px]"
      title={
        w
          ? `WIN RECORD over the ${bt.days}d test window — ${w.wins} of ${w.decided} DECIDED ` +
            "legs came back for more than they cost. A leg is decided when the copy was SOLD " +
            "(realized P&L net of the closing fee) or when its market RESOLVED under us ($1 or " +
            "$0). Resolutions count because leaders sell their winners and let their losers " +
            "expire — score only the sales and an expiring book reads as a perfect record. " +
            "Still-open positions, and legs valued at a last observed price, are not " +
            "counted.\n\n" +
            (rated
              ? `STEADY ${w.winningBuckets}/${w.activeBuckets}: the window is cut into six equal ` +
                `stretches, ${w.activeBuckets} of them decided a leg, and copying this trader came ` +
                `out AHEAD in ${w.winningBuckets} of those (${w.consistency.toFixed(2)}).\n\n` +
                "Dollars, not leg count — and the two disagree constantly here. A book that buys " +
                "longshots is right well under half the time and still makes money; a book that " +
                "scalps pennies is right almost always and still bleeds. STEADY asks the question " +
                "a copier is actually asking: did it come out ahead, stretch after stretch." +
                (floor > 0 ? ` The STEADY ≥ ${floor.toFixed(2)} filter is on.` : "")
              : `UNRATED: ${w.decided} decided leg(s) across ${w.activeBuckets} stretch(es) — ` +
                "under 5 legs, or inside fewer than 3 stretches, there is no shape to judge. " +
                "The STEADY filter hides these rather than guessing.")
          : "No win record on this card — it was replayed before win records existed, or it " +
            "closed nothing. The STEADY filter hides it rather than guessing."
      }
    >
      <span className="text-[9px] tracking-[0.14em] text-pixel-gray">WIN</span>
      <span className={`text-[11px] font-semibold tabular-nums ${pct === null ? "text-pixel-gray" : pct >= 50 ? "text-green-400" : "text-red-400"}`}>
        {pct === null ? "—" : `${pct}%`}
      </span>
      <span className={`text-[9.5px] tabular-nums ${tone}`}>
        {rated ? `STEADY ${w!.winningBuckets}/${w!.activeBuckets}` : "UNRATED"}
      </span>
    </span>
  );
}

function PnlCell({ label, bt, title }: { label: string; bt: HubBacktest; title: string }) {
  const pos = bt.pnl > 0;
  const neg = bt.pnl < 0;
  return (
    <span className="flex flex-col items-end shrink-0 min-w-[86px]" title={title}>
      <span className="text-[9px] tracking-[0.14em] text-pixel-gray">{label}</span>
      <span className={`text-[12px] font-semibold tabular-nums ${pos ? "text-green-400" : neg ? "text-red-400" : "text-pixel-gray-light"}`}>
        {bt.pnl >= 0 ? "+" : ""}{fmtUsd(bt.pnl)}
      </span>
      <span className="text-[9.5px] tabular-nums text-pixel-gray">
        {bt.roi >= 0 ? "+" : ""}{bt.roi.toFixed(1)}% · {bt.trades}tr
      </span>
    </span>
  );
}

export default function AutoCopyBoard() {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  // The form's draft — decoupled from the saved settings so typing doesn't
  // fight the poll. null field = "follow the server".
  const [draftTrain, setDraftTrain] = useState<string | null>(null);
  const [draftTest, setDraftTest] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [rerunning, setRerunning] = useState(false);
  // STEADY floor — 0 = off. When on, a card must have won the majority of its
  // legs in at least this share of the stretches it traded in, measured on the
  // TEST window (the out-of-sample one). Local to the board, like the trader
  // index's MIN CONSISTENCY: a view filter, not a saved setting the worker
  // reads — the cards themselves are always kept so turning it off shows the
  // whole board again.
  const [steadyFloor, setSteadyFloor] = useState(0);

  const load = useCallback(async () => {
    try {
      const res = await fetch(API, { headers: authHeaders(), cache: "no-store" });
      if (!res.ok) return;
      setSnap((await res.json()) as Snapshot);
    } catch {
      // Board keeps whatever it had; next poll retries.
    }
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  if (!snap) return null;

  const { settings, maxLookbackDays, status } = snap;
  const trainDays = draftTrain === null ? settings.trainDays : Number(draftTrain);
  const testDays = draftTest === null ? settings.testDays : Number(draftTest);
  const validNums =
    Number.isFinite(trainDays) && Number.isFinite(testDays) && trainDays >= 1 && testDays >= 1;
  const overCap = validNums && trainDays + testDays > maxLookbackDays;
  const dirty = trainDays !== settings.trainDays || testDays !== settings.testDays;

  // The STEADY cut, applied to the TEST window — the out-of-sample half. A
  // steady TRAIN record is what put a trader on the board in the first place;
  // it is the test window that says whether the hit rate survived the data it
  // was picked on, so that is the one worth sampling from.
  const shown = steadyFloor > 0
    ? snap.cards.filter((c) => steadyEnough(c.test, steadyFloor))
    : snap.cards;
  const hidden = snap.cards.length - shown.length;

  const now = Date.now();
  const testStart = now - testDays * 86400_000;
  const trainStart = testStart - trainDays * 86400_000;
  // Split bar proportions — the picture of "they don't overlap".
  const trainPct = validNums ? (trainDays / (trainDays + testDays)) * 100 : 50;

  const post = async (body: Partial<Settings>) => {
    setSaving(true);
    try {
      const res = await fetch(API, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        setSnap((await res.json()) as Snapshot);
        setDraftTrain(null);
        setDraftTest(null);
      }
    } catch {
      // Leave the draft in place — the user can retry SAVE.
    } finally {
      setSaving(false);
    }
  };

  const rerun = async () => {
    setRerunning(true);
    try {
      const res = await fetch(`${API}?run=1`, { method: "POST", headers: authHeaders() });
      if (res.ok) setSnap((await res.json()) as Snapshot);
    } catch {
    } finally {
      setRerunning(false);
    }
  };

  const dayInput = (value: number, set: (v: string) => void, label: string, hint: string) => (
    <label className="flex items-center gap-1.5" title={hint}>
      <span className="text-[9.5px] tracking-[0.14em] text-pixel-gray">{label}</span>
      <input
        type="number"
        min={1}
        max={maxLookbackDays - 1}
        value={Number.isFinite(value) ? value : ""}
        onChange={(e) => set(e.target.value)}
        className="w-[52px] bg-transparent border border-pixel-border rounded-[var(--radius-sm)] px-1.5 py-0.5 text-[12px] font-mono text-pixel-white tabular-nums outline-none focus:border-green-400/60"
      />
      <span className="text-[9.5px] text-pixel-gray">D</span>
    </label>
  );

  return (
    <div className="pixel-panel p-3 space-y-2.5">
      {/* ── Header ── */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[11px] font-mono font-bold tracking-[0.18em] text-pixel-white">
          AUTO COPY · TOP PNL
        </span>
        <span
          className="text-[10px] font-mono text-pixel-gray"
          title="The background worker replays this board on every pass — one identity copy-strat per top-PnL trader, over the cached feeds. No forking, nothing to publish."
        >
          {status.roster.length > 0 ? `${status.roster.length} traders` : "warming up"}
          {status.at > 0 && ` · pass ${fmtDay(status.at)} ${new Date(status.at).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })}`}
          {status.running && " · running…"}
          {status.error && <span className="text-red-400"> · {status.error}</span>}
        </span>
        <span className="flex-1" />
        <button
          onClick={() => void post({ enabled: !settings.enabled })}
          disabled={saving}
          className={`px-2 py-0.5 rounded-[var(--radius-sm)] border text-[9.5px] font-mono font-semibold tracking-[0.1em] transition-colors ${
            settings.enabled
              ? "text-green-400 border-green-400/50"
              : "text-pixel-gray border-pixel-border hover:text-pixel-white"
          }`}
          title={settings.enabled ? "On — the worker replays this board every pass. Click to stop." : "Off — click to have the worker replay the top-PnL board in the background."}
        >
          {settings.enabled ? "● AUTO ON" : "○ AUTO OFF"}
        </button>
        {/* The filter the board exists for: keep only the traders whose copy
            record is STEADY — winning in most of the stretches they traded in,
            not in one lucky burst — so what gets sampled from here is a
            repeatable hit rate rather than a single good week. UNRATED and
            unrecorded cards are CUT while it is on: the shape of the record IS
            the subject of this filter, so "we can't tell" cannot read as
            "it's fine" (same exception the board's MIN CONSISTENCY makes). */}
        <label
          className={`flex items-center gap-1 px-2 py-0.5 rounded-[var(--radius-sm)] border text-[9.5px] font-mono font-semibold tracking-[0.1em] transition-colors ${
            steadyFloor > 0
              ? "border-green-400/60 text-green-400 bg-green-400/[0.10]"
              : "border-pixel-border text-pixel-gray hover:text-green-400 hover:border-green-400/60"
          }`}
          title={
            steadyFloor > 0
              ? `Showing only traders whose test-window copy record won the majority of its legs in ≥ ${steadyFloor.toFixed(2)} of the stretches it traded in. Cards with too few closed legs to judge (UNRATED) are hidden too. Click ◈ STEADY to turn off.`
              : "Filter the board down to traders with a CONSISTENT win rate — won most of their legs in most stretches of the test window, not all at once. Click to turn on (default 0.75 = three stretches in four)."
          }
        >
          <button
            type="button"
            onClick={() => setSteadyFloor((v) => (v > 0 ? 0 : DEFAULT_STEADY_FLOOR))}
            className="tracking-[0.1em]"
          >
            {steadyFloor > 0 ? "◈ STEADY" : "◇ STEADY"}
          </button>
          {steadyFloor > 0 && (
            <input
              type="number"
              min={0}
              max={1}
              step={0.05}
              value={steadyFloor}
              onChange={(e) => {
                const n = Number(e.target.value);
                setSteadyFloor(Number.isFinite(n) ? Math.min(1, Math.max(0, n)) : 0);
              }}
              className="w-[46px] bg-transparent border border-green-400/40 rounded-[var(--radius-sm)] px-1 py-0 text-[10px] font-mono text-green-400 tabular-nums outline-none focus:border-green-400"
              title="The floor, 0–1. 1.00 = won in every stretch it traded in; 0.75 = three stretches in four."
            />
          )}
        </label>
        <button
          onClick={() => void rerun()}
          disabled={rerunning || status.running}
          className="px-2 py-0.5 rounded-[var(--radius-sm)] border border-pixel-border text-[9.5px] font-mono font-semibold tracking-[0.1em] text-pixel-gray hover:text-green-400 hover:border-green-400/60 transition-colors disabled:opacity-40"
          title="Replay the board now, out of the cached feeds — no upstream requests"
        >
          ↻ RERUN
        </button>
      </div>

      {/* ── The two dials + the split bar ── */}
      <div className="space-y-1.5">
        <div className="flex items-center gap-3 flex-wrap">
          {dayInput(trainDays, setDraftTrain, "TRAIN", "How many days the pick/scoring window covers. It ends exactly where the test window starts, so the two can never share a day.")}
          <span className="text-[10px] text-pixel-gray">→</span>
          {dayInput(testDays, setDraftTest, "TEST", "The backtest window — the most recent N days, replayed with each trader's stats frozen at its start (the HOLDOUT column).")}
          <span
            className={`text-[9.5px] font-mono ${overCap ? "text-red-400" : "text-pixel-gray"}`}
            title={`The trade feed holds ${maxLookbackDays} days — a train window past that would be measured on data that doesn't exist.`}
          >
            = {validNums ? trainDays + testDays : "?"} days back{overCap && ` · max ${maxLookbackDays}, will be clamped`}
          </span>
          {dirty && (
            <button
              onClick={() => void post({ trainDays, testDays })}
              disabled={saving || !validNums}
              className="px-2 py-0.5 rounded-[var(--radius-sm)] border border-green-400/50 text-[9.5px] font-mono font-semibold tracking-[0.1em] text-green-400 hover:bg-green-400/10 transition-colors disabled:opacity-40"
              title="Save the split — old cards are cleared and the worker re-replays the board on the new windows"
            >
              {saving ? "SAVING…" : "APPLY"}
            </button>
          )}
        </div>
        {/* Non-overlap, drawn: train ends at the tick where test begins. */}
        {validNums && (
          <div className="flex items-center gap-2">
            <span className="text-[9px] font-mono text-pixel-gray tabular-nums shrink-0">{fmtDay(trainStart)}</span>
            <div className="flex-1 flex h-[14px] rounded-[3px] overflow-hidden border border-pixel-border/70 text-[8.5px] font-mono tracking-[0.1em]">
              <div
                className="flex items-center justify-center bg-cyan-400/15 text-cyan-300/90"
                style={{ width: `${trainPct}%` }}
                title={`TRAIN — ${fmtDay(trainStart)} → ${fmtDay(testStart)} (${trainDays}d). What the pick is based on.`}
              >
                TRAIN {trainDays}D
              </div>
              <div
                className="flex items-center justify-center bg-green-400/15 text-green-300/90 border-l border-pixel-border"
                style={{ width: `${100 - trainPct}%` }}
                title={`TEST — ${fmtDay(testStart)} → now (${testDays}d). Replayed blind to everything after ${fmtDay(testStart)}.`}
              >
                TEST {testDays}D
              </div>
            </div>
            <span className="text-[9px] font-mono text-pixel-gray shrink-0">NOW</span>
          </div>
        )}
      </div>

      {/* ── The cards ── */}
      {snap.cards.length === 0 ? (
        <div className="text-[10.5px] font-mono text-pixel-gray leading-relaxed">
          {settings.enabled
            ? "No cards yet — the worker builds them on its next pass (it fetches each trader's 30-day feed first, so a cold start takes a cycle or two)."
            : "Auto copy is off. Turn it on and the worker will replay the top-PnL board in the background."}
        </div>
      ) : shown.length === 0 ? (
        /* An empty STEADY shelf is an answer, and a common one — say it in
           words so it can't be read as a broken filter. */
        <div className="px-3 py-2 rounded-[var(--radius-sm)] border border-amber-400/40 text-[10.5px] font-mono text-amber-400/90 leading-relaxed">
          None of the {snap.cards.length} traders on this board won the majority of their copied
          legs in {steadyFloor.toFixed(2)} of the stretches of the last {testDays}d — each one
          either won in bursts, or closed too few trades to judge. That is a result, not a broken
          filter: turn ◈ STEADY off, or lower the floor, to see what each card actually did.
        </div>
      ) : (
        <div className="space-y-0.5">
          {hidden > 0 && (
            <div className="px-2.5 pb-1 text-[9.5px] font-mono text-pixel-gray">
              ◈ STEADY ≥ {steadyFloor.toFixed(2)} · {hidden} of {snap.cards.length} hidden (won in
              too few stretches, or too few closed legs to rate)
            </div>
          )}
          {shown.map((c) => {
            const v = VERDICT_STYLE[c.verdict] ?? VERDICT_STYLE.idle;
            const h = c.test.holdout;
            return (
              <div
                key={c.address}
                className="flex items-center gap-3 rounded-[var(--radius-sm)] px-2.5 py-1.5 hover:bg-pixel-white/[0.04] transition-colors"
              >
                <span className="text-[10px] font-mono text-pixel-gray tabular-nums w-[20px] shrink-0">#{c.rank}</span>
                <Link
                  href={`/copy/${c.address}`}
                  className="text-[11.5px] font-mono font-semibold text-pixel-white hover:text-green-400 shrink-0 w-[110px] truncate"
                  title={`${c.address} — open this trader's copy workspace`}
                >
                  {shortAddress(c.address)}
                </Link>
                <span className="flex-1 min-w-[60px]" title={`TEST window equity curve (${c.test.days}d)`}>
                  <Sparkline data={c.test.curve} height={22} stretch />
                </span>
                <PnlCell
                  label={`TRAIN ${c.train.days}D`}
                  bt={c.train}
                  title={`Copy-trading this trader over ${fmtDay(now - (c.train.days + c.test.days) * 86400_000)} → ${fmtDay(now - c.test.days * 86400_000)} — the record they were picked on.`}
                />
                <PnlCell
                  label={`TEST ${c.test.days}D`}
                  bt={c.test}
                  title={`The last ${c.test.days} days, replayed after the train window ended. ${c.test.note ?? ""}`}
                />
                <SteadyCell bt={c.test} floor={steadyFloor} />
                <span
                  className="flex flex-col items-end shrink-0 min-w-[70px]"
                  title={h
                    ? `HOLDOUT — the test window replayed with this trader's stats frozen at its start, scored on the train window only. The deployable number.`
                    : "No holdout on this card yet."}
                >
                  <span className="text-[9px] tracking-[0.14em] text-pixel-gray">HOLDOUT</span>
                  {h ? (
                    <span className={`text-[11px] font-semibold tabular-nums ${h.pnl > 0 ? "text-green-400" : h.pnl < 0 ? "text-red-400" : "text-pixel-gray-light"}`}>
                      {h.roi >= 0 ? "+" : ""}{h.roi.toFixed(1)}%
                    </span>
                  ) : (
                    <span className="text-[11px] text-pixel-gray">—</span>
                  )}
                </span>
                <span
                  className={`shrink-0 px-1.5 py-0.5 rounded-[3px] border text-[8.5px] font-mono font-semibold tracking-[0.12em] ${v.cls}`}
                  title={v.hint}
                >
                  {v.label}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
