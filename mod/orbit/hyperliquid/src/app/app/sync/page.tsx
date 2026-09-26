"use client";

// Data integrity — the console's answer to "is what I'm looking at current
// and complete?". Everything a board or profile renders comes out of the
// API's background sync passes (board refresher, index deepener, curve
// prewarm); this page shows each cache's age, each window's completeness,
// and the recent pass history, all read from the public /sync ledger.

import { useCallback, useEffect, useMemo, useState } from "react";
import { ago, fetchSyncStatus, SyncStatus, SyncBoardRow, SyncEvent } from "../lib/api";
import { Freshness, Kpi, Meter, PageHead } from "../components/BoardBits";

const WINDOW = (days: number) => (days === 1 ? "1D" : `${days}D`);

const fmtDur = (ms: number) =>
  ms < 1000 ? `${ms}ms` : ms < 60_000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms / 60_000)}m`;

const fmtClock = (ms: number) =>
  new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

// Board state is judged against the SERVER's thresholds (it reports them), so
// this page can't drift from what the refresher actually promises: inside two
// refresh cycles = current, past the stale line = the loop is wedged.
type BoardState = "fresh" | "lagging" | "stale";
function boardState(s: SyncStatus, b: SyncBoardRow): BoardState {
  const age = s.now_ms - b.updated_at;
  if (age <= s.refresh_interval_ms * 2) return "fresh";
  if (age <= s.board_stale_after_ms) return "lagging";
  return "stale";
}

/** Status pill: dot + word, never color alone. */
function StatePill({ state }: { state: BoardState }) {
  const cls = state === "fresh" ? "text-win" : state === "lagging" ? "text-warn" : "text-loss";
  const dot = state === "fresh" ? "bg-win" : state === "lagging" ? "bg-warn" : "bg-loss";
  return (
    <span className={`inline-flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider ${cls}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${dot}`} />
      {state}
    </span>
  );
}

const KIND_LABEL: Record<string, string> = {
  board: "board refresh",
  deepen: "index deepen",
  curves: "curve prewarm",
};

function EventRow({ ev }: { ev: SyncEvent }) {
  return (
    <tr className="border-t border-white/[0.04] hover:bg-white/[0.02] transition-colors">
      <td className="px-3 py-1.5 font-mono text-[11px] text-muted whitespace-nowrap" title={new Date(ev.ts_ms).toISOString()}>
        {fmtClock(ev.ts_ms)}
      </td>
      <td className="px-3 py-1.5 text-[11px] text-ink whitespace-nowrap">{KIND_LABEL[ev.kind] ?? ev.kind}</td>
      <td className="px-3 py-1.5 font-mono text-[11px] text-muted whitespace-nowrap">{ev.key}</td>
      <td className="px-3 py-1.5 font-mono text-[11px] text-ink text-right">{ev.rows.toLocaleString()}</td>
      <td className="px-3 py-1.5 font-mono text-[11px] text-muted text-right">{fmtDur(ev.duration_ms)}</td>
      <td className="px-3 py-1.5">
        {ev.ok ? (
          <span className="inline-flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-win">
            <span className="h-1.5 w-1.5 rounded-full bg-win" />ok
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-loss" title={ev.note}>
            <span className="h-1.5 w-1.5 rounded-full bg-loss" />failed
          </span>
        )}
      </td>
      <td className="px-3 py-1.5 text-[11px] text-muted max-w-[36ch] truncate" title={ev.note}>{ev.note || "—"}</td>
    </tr>
  );
}

export default function SyncPage() {
  const [data, setData] = useState<SyncStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      setData(await fetchSyncStatus(200));
      setError(null);
    } catch (e: any) {
      setError(String(e?.message ?? e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 15_000);
    return () => clearInterval(t);
  }, [load]);

  const derived = useMemo(() => {
    if (!data) return null;
    const boards = data.boards.entries;
    const freshBoards = boards.filter((b) => boardState(data, b) !== "stale").length;
    const lastPass = data.history[0]?.ts_ms ?? 0;
    const failures = data.history.filter((e) => !e.ok).length;
    // One completeness number: fresh over target, summed across the windows
    // the deepener keeps warm.
    const target = data.coverage.reduce((a, c) => a + c.target, 0);
    const fresh = data.coverage.reduce((a, c) => a + c.fresh, 0);
    const coveragePct = target > 0 ? Math.round((fresh / target) * 100) : 0;
    return { boards, freshBoards, lastPass, failures, target, fresh, coveragePct };
  }, [data]);

  return (
    <div className="space-y-5">
      <PageHead
        title="data integrity"
        blurb={
          <>
            What backs every number on the boards: when each cached leaderboard was last recomputed,
            how complete the per-wallet fill index is, and the recent history of background sync
            passes — so you can see the data is current and full before trusting it.
          </>
        }
        right={
          <Freshness
            loading={loading}
            label={data ? `checked ${ago(data.now_ms)} · auto-refreshes` : "loading"}
          />
        }
      />

      {error && (
        <div className="rounded-lg border border-loss/30 bg-loss/10 px-3 py-2 text-[11px] text-loss">
          Couldn&apos;t reach the sync ledger: {error}
        </div>
      )}

      {data && derived && (
        <>
          {/* Headline: are we current, are we complete, did anything fail. */}
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Kpi
              label="boards current"
              value={`${derived.freshBoards}/${derived.boards.length}`}
              tone={data.boards.stale > 0 ? "loss" : "win"}
              sub={`refreshed every ${Math.round(data.refresh_interval_ms / 60_000)}m · stale after ${Math.round(data.board_stale_after_ms / 60_000)}m`}
            >
              <Meter pct={derived.boards.length ? (derived.freshBoards / derived.boards.length) * 100 : 0} />
            </Kpi>
            <Kpi
              label="index coverage"
              value={`${derived.coveragePct}%`}
              tone={derived.coveragePct >= 90 ? "win" : undefined}
              sub={`${derived.fresh.toLocaleString()} of ${derived.target.toLocaleString()} tracked wallets have fresh fill stats (TTL ${Math.round(data.index_ttl_ms / 60_000)}m)`}
            >
              <Meter pct={derived.coveragePct} />
            </Kpi>
            <Kpi
              label="indexed wallets"
              value={data.index.entries.toLocaleString()}
              sub="window-scoped fill scans held on disk — what coin filters and win rates are answered from"
            />
            <Kpi
              label="last sync pass"
              value={derived.lastPass ? ago(derived.lastPass) : "—"}
              tone={derived.failures > 0 ? "loss" : undefined}
              sub={
                derived.failures > 0
                  ? `${derived.failures} failed pass${derived.failures === 1 ? "" : "es"} in recent history`
                  : `${data.history.length} recent passes, all succeeded`
              }
            />
          </div>

          {/* Every cached board: what window/rank it answers, and its age. */}
          <section className="panel overflow-hidden">
            <div className="px-4 pt-3.5 pb-2 flex items-baseline justify-between gap-3">
              <h2 className="eyebrow">board freshness</h2>
              <span className="text-[10px] text-muted">
                what /traders serves — each row is one cached leaderboard compute
              </span>
            </div>
            <table className="w-full text-left">
              <thead>
                <tr className="table-head">
                  <th className="px-3 py-1.5">window</th>
                  <th className="px-3 py-1.5">ranked by</th>
                  <th className="px-3 py-1.5">activity gate</th>
                  <th className="px-3 py-1.5 text-right">traders held</th>
                  <th className="px-3 py-1.5">last computed</th>
                  <th className="px-3 py-1.5">status</th>
                </tr>
              </thead>
              <tbody>
                {derived.boards.map((b) => (
                  <tr key={b.key} className="border-t border-white/[0.04] hover:bg-white/[0.02] transition-colors">
                    <td className="px-3 py-2 font-mono text-[11px] text-ink">{WINDOW(b.days)}</td>
                    <td className="px-3 py-2 text-[11px] text-ink uppercase">{b.rank}</td>
                    <td className="px-3 py-2 text-[11px] text-muted">{b.active === "24h" ? "traded in last 24h" : "traded in window"}</td>
                    <td className="px-3 py-2 font-mono text-[11px] text-ink text-right">
                      {b.rows.toLocaleString()}
                      {b.all && <span className="ml-1.5 text-[9px] uppercase tracking-wider text-muted">full board</span>}
                    </td>
                    <td className="px-3 py-2 font-mono text-[11px] text-muted" title={new Date(b.updated_at).toISOString()}>
                      {ago(b.updated_at)}
                    </td>
                    <td className="px-3 py-2"><StatePill state={boardState(data, b)} /></td>
                  </tr>
                ))}
                {derived.boards.length === 0 && (
                  <tr><td colSpan={6} className="px-3 py-6 text-center text-[11px] text-muted">
                    No boards cached yet — the refresher fills this within a couple of minutes of boot.
                  </td></tr>
                )}
              </tbody>
            </table>
          </section>

          {/* Per-window completeness: what the index holds vs what the
              deepener aims to keep warm. */}
          <section className="panel overflow-hidden">
            <div className="px-4 pt-3.5 pb-2 flex items-baseline justify-between gap-3">
              <h2 className="eyebrow">fill-index completeness</h2>
              <span className="text-[10px] text-muted">
                win rates, sharpe and coin filters are answered from these per-wallet scans
              </span>
            </div>
            <table className="w-full text-left">
              <thead>
                <tr className="table-head">
                  <th className="px-3 py-1.5">window</th>
                  <th className="px-3 py-1.5 text-right">wallets held</th>
                  <th className="px-3 py-1.5 text-right">fresh now</th>
                  <th className="px-3 py-1.5 w-[26%]">top-rank coverage</th>
                  <th className="px-3 py-1.5">newest scan</th>
                  <th className="px-3 py-1.5">oldest scan</th>
                </tr>
              </thead>
              <tbody>
                {data.index.windows.map((w) => {
                  const cov = data.coverage.find((c) => c.days === w.days);
                  const pct = cov && cov.target > 0 ? Math.round((cov.fresh / cov.target) * 100) : null;
                  return (
                    <tr key={w.days} className="border-t border-white/[0.04] hover:bg-white/[0.02] transition-colors">
                      <td className="px-3 py-2 font-mono text-[11px] text-ink">{WINDOW(w.days)}</td>
                      <td className="px-3 py-2 font-mono text-[11px] text-ink text-right">{w.total.toLocaleString()}</td>
                      <td className="px-3 py-2 font-mono text-[11px] text-ink text-right">{w.fresh.toLocaleString()}</td>
                      <td className="px-3 py-2">
                        {cov && pct != null ? (
                          <div className="flex items-center gap-2" title={`deepener keeps the top ${cov.target} ranked wallets warm; ${cov.fresh} are inside the TTL (checked ${ago(cov.checked_ms)})`}>
                            <div className="flex-1"><Meter pct={pct} /></div>
                            <span className="font-mono text-[11px] text-muted shrink-0">{cov.fresh}/{cov.target}</span>
                          </div>
                        ) : (
                          <span className="text-[11px] text-muted">no reading yet</span>
                        )}
                      </td>
                      <td className="px-3 py-2 font-mono text-[11px] text-muted">{ago(w.newest_ms)}</td>
                      <td className="px-3 py-2 font-mono text-[11px] text-muted">{ago(w.oldest_ms)}</td>
                    </tr>
                  );
                })}
                {data.index.windows.length === 0 && (
                  <tr><td colSpan={6} className="px-3 py-6 text-center text-[11px] text-muted">
                    Index is empty — the deepener starts filling it a few wallets per cycle.
                  </td></tr>
                )}
              </tbody>
            </table>
          </section>

          {/* The previous syncs themselves, newest first. */}
          <section className="panel overflow-hidden">
            <div className="px-4 pt-3.5 pb-2 flex items-baseline justify-between gap-3">
              <h2 className="eyebrow">sync history</h2>
              <span className="text-[10px] text-muted">
                every background pass, newest first · kept across restarts
              </span>
            </div>
            <div className="max-h-[28rem] overflow-y-auto">
              <table className="w-full text-left">
                <thead className="sticky top-0 bg-panel">
                  <tr className="table-head">
                    <th className="px-3 py-1.5">time</th>
                    <th className="px-3 py-1.5">pass</th>
                    <th className="px-3 py-1.5">target</th>
                    <th className="px-3 py-1.5 text-right">rows</th>
                    <th className="px-3 py-1.5 text-right">took</th>
                    <th className="px-3 py-1.5">result</th>
                    <th className="px-3 py-1.5">detail</th>
                  </tr>
                </thead>
                <tbody>
                  {data.history.map((ev, i) => <EventRow key={`${ev.ts_ms}-${i}`} ev={ev} />)}
                  {data.history.length === 0 && (
                    <tr><td colSpan={7} className="px-3 py-6 text-center text-[11px] text-muted">
                      No passes recorded yet — history starts accumulating from the next refresh cycle.
                    </td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
