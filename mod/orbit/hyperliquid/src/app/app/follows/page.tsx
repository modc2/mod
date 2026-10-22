"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  listFollows, deleteFollow, pauseFollow, resumeFollow, updateFollow,
  listSignals, shortAddr, ago, Follow, Signal, fmtPct, fmtPnl,
} from "../lib/api";
import { useWallet } from "../lib/wallet";
import { LegacyNote, Spark } from "../components/BoardBits";
import { useCurves } from "../lib/curves";

export default function FollowsPage() {
  const { address } = useWallet();
  const [follows, setFollows] = useState<Follow[]>([]);
  const [loading, setLoading] = useState(true);
  const [deletePending, setDeletePending] = useState<string | null>(null);
  const [signals, setSignals] = useState<Signal[] | null>(null);
  const curveMap = useCurves(follows.map(f => f.leader), 7, follows.length > 0);

  // Follows are private per wallet — the API only serves your own rows,
  // scoped to the signed-in address on the auth token.
  const load = useCallback(async () => {
    if (!address) { setFollows([]); setLoading(false); return; }
    setLoading(true);
    try {
      const r = await listFollows(address);
      setFollows(r.follows);
    } finally { setLoading(false); }
  }, [address]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!address) { setSignals([]); return; }
    let alive = true;
    const poll = async () => {
      try {
        const r = await listSignals(address, 50);
        if (alive) setSignals(r.signals);
      } catch { /* silent poll */ }
    };
    poll();
    const iv = setInterval(poll, 5000);
    return () => { alive = false; clearInterval(iv); };
  }, [address]);

  const onDelete = async (id: string) => {
    await deleteFollow(id);
    setDeletePending(null);
    load();
  };
  const onPause = async (f: Follow) => {
    if (f.paused) await resumeFollow(f.id); else await pauseFollow(f.id);
    load();
  };
  const onSize = async (f: Follow, v: number) => {
    await updateFollow(f.id, { size_pct: v });
    load();
  };

  return (
    <div className="space-y-4">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-gradient text-[24px] font-bold tracking-tight leading-tight">my follows</h1>
          <p className="text-xs text-muted mt-1">copy-trade configurations + status</p>
        </div>
        <div className="flex gap-2 items-center">
          <button className="btn" onClick={load} disabled={loading}>refresh</button>
        </div>
      </div>

      <LegacyNote>
        The older copy path — each leader fill scaled by a percentage. To have a
        sleeve tracked and rebalanced for you automatically, use
      </LegacyNote>

      {/* ── recent signals ── */}
      <div className="space-y-1">
        <h2 className="text-[13px] font-semibold tracking-tight">recent signals</h2>
        <div className="panel">
          <div className="grid grid-cols-[1fr_1.2fr_0.8fr_0.7fr_0.9fr_0.9fr_0.9fr] gap-2 px-4 py-2 border-b border-border text-[10px] uppercase tracking-wider text-muted">
            <div>time</div>
            <div>leader</div>
            <div>coin</div>
            <div>side</div>
            <div className="text-right">leader sz</div>
            <div className="text-right">copy sz</div>
            <div className="text-right">status</div>
          </div>
          {signals === null ? (
            <div className="px-4 py-3 text-xs text-muted animate-pulse">loading…</div>
          ) : signals.length === 0 ? (
            <div className="px-4 py-4 text-xs text-muted">no signals yet — signals appear here once a follow fires</div>
          ) : signals.map((s) => (
            <div key={s.id} className="grid grid-cols-[1fr_1.2fr_0.8fr_0.7fr_0.9fr_0.9fr_0.9fr] gap-2 px-4 py-2 items-center table-row">
              <div className="text-[11px] text-muted">{ago(s.ts_ms)}</div>
              <Link href={`/trader/${s.leader}`} className="text-accent2 hover:underline text-xs">
                {shortAddr(s.leader)}
              </Link>
              <div className="text-xs">{s.coin}</div>
              <div className={`text-xs font-medium ${s.side === "BUY" ? "text-win" : "text-danger"}`}>
                {s.side}
              </div>
              <div className="text-right text-xs num">{s.leader_sz}</div>
              <div className="text-right text-xs num">{s.copy_sz}</div>
              <div className={`text-right text-xs ${
                s.status === "filled" || s.status === "ok" ? "text-accent2"
                : s.status === "skipped" || s.status === "error" ? "text-danger"
                : "text-muted"
              }`}>{s.status}</div>
            </div>
          ))}
        </div>
      </div>

      {follows.length === 0 ? (
        <div className="panel p-6 text-xs text-muted">
          no follows yet. <Link href="/" className="text-accent2">find a trader →</Link>
        </div>
      ) : (
        <div className="panel">
          <div className="grid grid-cols-[1.5fr_44px_0.8fr_1.2fr_0.9fr_0.9fr_0.8fr_1.6fr] gap-2 px-4 py-2 border-b border-border text-[10px] uppercase tracking-wider text-muted">
            <div>leader</div>
            <div>7d</div>
            <div className="text-right">pnl</div>
            <div>follower</div>
            <div className="text-right">size %</div>
            <div className="text-right">max / trade</div>
            <div className="text-right">created</div>
            <div className="text-right">actions</div>
          </div>
          {follows.map((f) => {
            const c = curveMap[f.leader.toLowerCase()];
            return (
            <div key={f.id}
              className={`grid grid-cols-[1.5fr_44px_0.8fr_1.2fr_0.9fr_0.9fr_0.8fr_1.6fr] gap-2 px-4 py-2.5 items-center table-row
                ${f.paused ? "opacity-60" : ""}`}>
              <Link href={`/trader/${f.leader}`} className="text-accent2 hover:underline">
                {shortAddr(f.leader)}
              </Link>
              <div className="min-w-0">
                {c === undefined ? (
                  <div className="h-10 rounded-md bg-white/[0.04] animate-pulse" />
                ) : c.available && c.points.length >= 2 ? (
                  <span className={c.pnl >= 0 ? "text-win" : "text-loss"}>
                    <Spark points={c.points} height={40} />
                  </span>
                ) : (
                  <div className="h-10 rounded-md border border-dashed border-white/[0.08]" />
                )}
              </div>
              <div className={`text-right text-xs num ${c?.available ? (c.pnl >= 0 ? "text-win" : "text-loss") : "text-muted"}`}>
                {c?.available ? fmtPnl(c.pnl) : "—"}
              </div>
              <div className="text-muted text-xs num">{shortAddr(f.follower)}</div>
              <div className="text-right">
                <input className="input w-20 num text-right" type="number" min={0} max={100} step={0.5}
                  defaultValue={f.size_pct}
                  onBlur={(e) => onSize(f, Number(e.target.value))} />
              </div>
              <div className="num text-right text-xs">
                {f.max_per_trade_usd > 0 ? `$${f.max_per_trade_usd}` : "∞"}
              </div>
              <div className="text-right text-[11px] text-muted">{ago(f.created_ms)}</div>
              <div className="flex justify-end gap-1 items-center">
                {f.paused
                  ? <button className="btn" onClick={() => onPause(f)}>resume</button>
                  : <button className="btn" onClick={() => onPause(f)}>pause</button>}
                {deletePending === f.id ? (
                  <>
                    <button className="btn-danger" onClick={() => onDelete(f.id)}>yes, delete</button>
                    <button className="btn" onClick={() => setDeletePending(null)}>cancel</button>
                  </>
                ) : (
                  <button className="btn-danger" onClick={() => setDeletePending(f.id)}>delete</button>
                )}
              </div>
            </div>
          );
          })}
        </div>
      )}
    </div>
  );
}
