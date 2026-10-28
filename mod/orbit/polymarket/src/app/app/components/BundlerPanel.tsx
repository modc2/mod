"use client";

// ◆ BUNDLES — the studio's window onto the BUNDLER agent (/_api/bundler).
//
// Folded, it is one line: the best bundle the agent has, its grade and how it
// did on days it never saw. Open, it is a card per bundle with LOAD, which
// replaces the studio's allocation with that bundle's legs — from there the
// studio's own replay + COPY ALL (DRY RUN) take over, so the agent never
// touches the copy desk. FORGE NOW starts a run (a few minutes).

import { useCallback, useEffect, useState } from "react";

import { getAccessToken } from "../lib/access";
import { shortAddress } from "../lib/identityStrat";
import type { Bundle, Grade } from "../lib/bundleForge";

const API = "/polymarket/_api/bundler";
const POLL_ACTIVE_MS = 5000;
const POLL_IDLE_MS = 60_000;
const OPEN_KEY = "poly:bundler:open";

interface Run {
  id: string;
  at: number;
  status: "running" | "done" | "failed";
  summary?: string;
  pool?: number;
  survivors?: number;
  bundles?: Bundle[];
  reviewed?: boolean;
  reviewError?: string;
  forge?: { legUsd: number; blockDays: number; blocks: number; testBlocks: number };
  skill?: Record<"top" | "rest" | "rejected", { n: number; pUp: number; mean: number }>;
}

interface State {
  settings: { enabled: boolean; intervalSecs: number };
  active: { id: string; stage?: string } | null;
  runs: { id: string; status: string; error?: string }[];
  latest: Run | null;
}

export interface LoadLeg {
  address: string;
  usd: number;
  label?: string | null;
}

const GRADE_TONE: Record<Grade, string> = {
  A: "border-green-400 text-green-400",
  B: "border-sky-400 text-sky-400",
  C: "border-amber-400 text-amber-400",
  F: "border-red-400 text-red-400",
};

function headers(): Record<string, string> {
  const t = getAccessToken();
  return { "Content-Type": "application/json", ...(t ? { Authorization: `Bearer ${t}` } : {}) };
}

const signed = (v: number, d = 1) => `${v >= 0 ? "+" : ""}${v.toFixed(d)}%`;
const frac = (v: number, d = 1) => signed(v * 100, d);
const ago = (t: number) => {
  const m = Math.round((Date.now() - t) / 60_000);
  return m < 60 ? `${m}m ago` : m < 2880 ? `${Math.round(m / 60)}h ago` : `${Math.round(m / 1440)}d ago`;
};

export default function BundlerPanel({ onLoad }: { onLoad: (legs: LoadLeg[]) => void }) {
  const [st, setSt] = useState<State | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState<string | null>(null);

  useEffect(() => {
    try { setOpen(localStorage.getItem(OPEN_KEY) === "1"); } catch {}
  }, []);
  const toggleOpen = () => setOpen((o) => {
    try { localStorage.setItem(OPEN_KEY, o ? "0" : "1"); } catch {}
    return !o;
  });

  const refresh = useCallback(async () => {
    try {
      const r = await fetch(API, { headers: headers(), cache: "no-store" });
      if (r.status === 401) { setErr("sign in to see the agent's bundles"); return; }
      if (!r.ok) { setErr(`bundler unavailable (HTTP ${r.status})`); return; }
      setSt((await r.json()) as State);
      setErr(null);
    } catch {
      setErr("bundler unavailable");
    }
  }, []);

  useEffect(() => {
    void refresh();
    const t = setInterval(refresh, st?.active ? POLL_ACTIVE_MS : POLL_IDLE_MS);
    return () => clearInterval(t);
  }, [refresh, st?.active]);

  const forge = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const r = await fetch(`${API}?run=1`, { method: "POST", headers: headers(), body: "{}" });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) setErr(j.error === "agent off" ? "the BUNDLER agent is switched off (STRATS ▸ AGENTS)" : j.error || `HTTP ${r.status}`);
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  const setLoop = async (enabled: boolean) => {
    await fetch(API, { method: "POST", headers: headers(), body: JSON.stringify({ enabled }) });
    await refresh();
  };

  const latest = st?.latest ?? null;
  const bundles = latest?.bundles ?? [];
  const best = bundles[0];
  const lastFailed = st?.runs[0]?.status === "failed" ? st.runs[0] : null;

  const load = (b: Bundle) => {
    onLoad(b.legs.map((l) => ({ address: l.address, usd: l.usd, label: null })));
    setLoaded(b.id);
  };

  return (
    <section className="border border-pixel-border rounded-[var(--radius-sm)]">
      {/* ── the one line ── */}
      <div className="flex flex-wrap items-center gap-2 px-3 py-2">
        <button type="button" onClick={toggleOpen} className="font-mono text-[11px] tracking-[0.14em] text-pixel-white">
          {open ? "▾" : "▸"} ◆ AGENT BUNDLES
        </button>
        <span className="font-mono text-[10.5px] text-pixel-gray truncate min-w-0 flex-1">
          {err ? err
            : st?.active ? `forging… ${st.active.stage ?? ""}`
            : best ? (
              <>
                best <span className={GRADE_TONE[best.grade].split(" ")[1]}>{best.grade}</span>
                {" · "}{best.review?.name || `${best.legs.length} traders`}
                {" · "}<span className={best.holdout.pct >= 0 ? "text-green-400" : "text-red-400"}>{signed(best.holdout.pct)}</span>
                {` on ${best.holdout.days} days it never saw · ${latest ? ago(latest.at) : ""}`}
              </>
            )
            : latest ? latest.summary
            : "no bundles yet — FORGE NOW asks the agent to build some (takes a few minutes)"}
        </span>
        <button type="button" className="pixel-btn btn-xs" disabled={busy || !!st?.active} onClick={forge}
          title="Replay every cached trader in 3-day blocks, build bundles on older days, prove them on the last 9">
          {st?.active ? "FORGING…" : "FORGE NOW"}
        </button>
      </div>

      {open && (
        <div className="border-t border-pixel-border px-3 py-3 space-y-3">
          <p className="font-mono text-[10px] text-pixel-gray leading-relaxed">
            The agent replays copying every trader it has history for in {latest?.forge?.blockDays ?? 3}-day blocks,
            picks traders that are green block after block using only the older days, then tests each bundle on
            the most recent {(latest?.forge?.testBlocks ?? 3) * (latest?.forge?.blockDays ?? 3)} days it never looked at.
            {" "}<b className="text-green-400">A</b> = held up and steady · <b className="text-sky-400">B</b> promising ·
            {" "}<b className="text-amber-400">C</b> unproven · <b className="text-red-400">F</b> lost money on the unseen days.
            {" "}LOAD puts a bundle into your allocation below; nothing is copied until COPY ALL (which starts in DRY RUN).
          </p>
          {lastFailed && (
            <p className="font-mono text-[10px] text-red-400">last run failed: {lastFailed.error}</p>
          )}
          {latest?.reviewError && (
            <p className="font-mono text-[10px] text-amber-400">reviewer unavailable ({latest.reviewError}) — numbers below are unreviewed</p>
          )}
          {latest?.skill && latest.skill.top.n > 0 && (
            <p className="font-mono text-[10px] text-pixel-gray"
              title="Reporting only — the holdout never feeds a pick. If top picks stop beating rejects, the method isn't predicting and the bundles deserve no trust.">
              SCREEN CHECK · up on the unseen days: top picks{" "}
              <b className={latest.skill.top.pUp > latest.skill.rejected.pUp + 0.1 ? "text-green-400" : "text-amber-400"}>
                {Math.round(latest.skill.top.pUp * 100)}%
              </b>
              {" "}· other kept {Math.round(latest.skill.rest.pUp * 100)}% · rejected {Math.round(latest.skill.rejected.pUp * 100)}%
              {latest.skill.top.pUp <= latest.skill.rejected.pUp + 0.1 ? " — the screen isn't predicting this run; treat bundles as unproven" : ""}
            </p>
          )}
          {latest && latest.summary && bundles.length > 0 && (
            <p className="font-mono text-[10.5px] text-pixel-gray-light">{latest.summary}</p>
          )}

          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {bundles.map((b) => (
              <div key={b.id} className="border border-pixel-border rounded-[var(--radius-sm)] p-3 space-y-2">
                <div className="flex items-start gap-2">
                  <span className={`font-mono text-[13px] border px-1.5 leading-tight ${GRADE_TONE[b.grade]}`} title={b.gradeText}>
                    {b.grade}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="font-mono text-[11.5px] text-pixel-white truncate">
                      {b.review?.name || `Bundle ${b.id.slice(1)}`}
                    </div>
                    <div className="font-mono text-[9.5px] text-pixel-gray">
                      {b.legs.length} traders · ${b.totalUsd.toLocaleString()} total
                      {b.review?.verdict ? ` · agent says ${b.review.verdict.toUpperCase()}` : ""}
                    </div>
                  </div>
                </div>

                <div className="grid grid-cols-3 gap-2 font-mono text-[10px]">
                  <Stat label="UNSEEN DAYS" value={signed(b.holdout.pct)} tone={b.holdout.pct >= 0 ? "text-green-400" : "text-red-400"}
                    hint={`$${b.holdout.net.toFixed(2)} over the last ${b.holdout.days} days, drawdown ${b.holdout.drawdown.toFixed(1)}%`} />
                  <Stat label="GREEN BLOCKS" value={`${b.train.green + b.test.green}/${b.train.n + b.test.n}`}
                    hint={`train ${b.train.green}/${b.train.n} · unseen ${b.test.green}/${b.test.n}`} />
                  <Stat label="UP AFTER A WEEK" value={`${Math.round(b.pWeekUp * 100)}%`}
                    hint="Share of resampled ~7-day stretches that ended up — from this bundle's own blocks" />
                </div>

                {b.review?.thesis && <p className="font-mono text-[10px] text-pixel-gray-light leading-snug">{b.review.thesis}</p>}
                {b.review?.risks && b.review.risks.length > 0 && (
                  <ul className="font-mono text-[9.5px] text-amber-400/90 leading-snug list-none">
                    {b.review.risks.map((r, i) => <li key={i}>! {r}</li>)}
                  </ul>
                )}

                <table className="w-full font-mono text-[9.5px]" style={{ tableLayout: "auto" }}>
                  <tbody>
                    {b.legs.map((l) => (
                      <tr key={l.address} title={l.topMarkets.join("\n")}>
                        <td className="text-pixel-gray-light py-0.5" style={{ overflow: "visible" }}>{shortAddress(l.address)}</td>
                        <td className="text-pixel-gray text-right" style={{ overflow: "visible" }}>{l.trainGreen} green</td>
                        <td className={`text-right ${l.holdoutPct >= 0 ? "text-green-400" : "text-red-400"}`} style={{ overflow: "visible" }}>
                          {l.holdoutTrades > 0 ? signed(l.holdoutPct) : "idle"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>

                <button type="button" onClick={() => load(b)}
                  className={`pixel-btn btn-xs w-full ${loaded === b.id ? "border-pixel-green text-pixel-green" : ""}`}>
                  {loaded === b.id ? "LOADED INTO YOUR ALLOCATION ↓" : `LOAD ${b.legs.length} TRADERS × $${b.legs[0]?.usd ?? 0}`}
                </button>
              </div>
            ))}
          </div>

          <div className="flex items-center gap-2 font-mono text-[10px] text-pixel-gray">
            <span>AUTO-FORGE every {Math.round((st?.settings.intervalSecs ?? 21600) / 3600)}h</span>
            <button type="button" className={`pixel-btn btn-xs ${st?.settings.enabled ? "border-pixel-green text-pixel-green" : ""}`}
              onClick={() => setLoop(!st?.settings.enabled)}>
              {st?.settings.enabled ? "ON" : "OFF"}
            </button>
            {latest && <span>· last run {ago(latest.at)} · {latest.survivors}/{latest.pool} traders passed the screen</span>}
          </div>
        </div>
      )}
    </section>
  );
}

function Stat({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: string }) {
  return (
    <div title={hint}>
      <div className="text-[8.5px] tracking-[0.12em] text-pixel-gray">{label}</div>
      <div className={`text-[12px] ${tone ?? "text-pixel-white"}`}>{value}</div>
    </div>
  );
}
