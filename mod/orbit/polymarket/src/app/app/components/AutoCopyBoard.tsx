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
import type { ForwardVerdict, HubBacktest } from "../lib/hubReplay";
import Sparkline from "./Sparkline";

const API = `${process.env.NEXT_PUBLIC_BASE_PATH ?? ""}/api/hub/autocopy`;
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
      ) : (
        <div className="space-y-0.5">
          {snap.cards.map((c) => {
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
