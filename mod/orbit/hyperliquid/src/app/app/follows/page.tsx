"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  listFollows, deleteFollow, pauseFollow, resumeFollow, updateFollow,
  shortAddr, ago, Follow, fmtPct,
} from "../lib/api";
import { useWallet } from "../lib/wallet";

export default function FollowsPage() {
  const { address } = useWallet();
  const [follows, setFollows] = useState<Follow[]>([]);
  const [loading, setLoading] = useState(true);
  const [filterMine, setFilterMine] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const filt = filterMine && address ? address : undefined;
      const r = await listFollows(filt);
      setFollows(r.follows);
    } finally { setLoading(false); }
  }, [address, filterMine]);

  useEffect(() => { load(); }, [load]);

  const onDelete = async (id: string) => {
    if (!confirm("delete this follow?")) return;
    await deleteFollow(id); load();
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
          <h1 className="text-xl text-ink">my follows</h1>
          <p className="text-xs text-muted mt-1">copy-trade configurations + status</p>
        </div>
        <div className="flex gap-2 items-center">
          <label className="text-[11px] text-muted flex items-center gap-1">
            <input type="checkbox" checked={filterMine} onChange={(e) => setFilterMine(e.target.checked)} />
            mine only
          </label>
          <button className="btn" onClick={load} disabled={loading}>refresh</button>
        </div>
      </div>

      {follows.length === 0 ? (
        <div className="panel p-6 text-xs text-muted">
          no follows yet. <Link href="/" className="text-accent2">find a trader →</Link>
        </div>
      ) : (
        <div className="panel">
          <div className="grid grid-cols-[1.5fr_1.5fr_1fr_1fr_1fr_1.6fr] gap-2 px-4 py-2 border-b border-border text-[10px] uppercase tracking-wider text-muted">
            <div>leader</div>
            <div>follower</div>
            <div className="text-right">size %</div>
            <div className="text-right">max / trade</div>
            <div className="text-right">created</div>
            <div className="text-right">actions</div>
          </div>
          {follows.map((f) => (
            <div key={f.id}
              className={`grid grid-cols-[1.5fr_1.5fr_1fr_1fr_1fr_1.6fr] gap-2 px-4 py-2.5 items-center table-row
                ${f.paused ? "opacity-60" : ""}`}>
              <Link href={`/trader/${f.leader}`} className="text-accent2 hover:underline">
                {shortAddr(f.leader)}
              </Link>
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
              <div className="flex justify-end gap-1">
                {f.paused
                  ? <button className="btn" onClick={() => onPause(f)}>resume</button>
                  : <button className="btn" onClick={() => onPause(f)}>pause</button>}
                <button className="btn-danger" onClick={() => onDelete(f.id)}>delete</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
