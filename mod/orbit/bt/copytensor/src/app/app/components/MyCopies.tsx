"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import type { CopyConfig } from "../lib/types";
import { deleteCopy, fetchCopies, pauseCopy, resumeCopy, shortSs58, syncCopy } from "../lib/api";
import { useCurrency, fmtValue } from "../context/CurrencyContext";
import Identicon from "./Identicon";

/**
 * Every trader you copy, as a card: who, how much, on or off. Three verbs
 * per card and nothing else — the full blended book with per-subnet drift
 * lives in AllocationBook on /strats for anyone who wants to read it.
 */
export default function MyCopies() {
  const { currency, usdPerTao } = useCurrency();
  const [copies, setCopies] = useState<CopyConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");

  const load = () =>
    fetchCopies().then(setCopies).catch((e) => setError(e.message)).finally(() => setLoading(false));
  useEffect(() => { load(); }, []);

  const run = async (id: string, op: () => Promise<unknown>) => {
    setBusy(id);
    setError("");
    try { await op(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); load(); }
  };

  if (loading) return <p className="arcade-prose-sm">loading…</p>;

  if (copies.length === 0) {
    return (
      <div className="pixel-panel p-6 space-y-3">
        <p className="arcade-prose">You aren&rsquo;t copying anyone yet.</p>
        <Link href="/" className="pixel-btn border-green-400 text-green-400 no-underline">
          PICK A TRADER
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {error && <p className="modal-error">{error}</p>}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {copies.map((c) => {
          const on = c.status === "active";
          const name = c.label || c.target_info?.label || shortSs58(c.target_ss58);
          const b = busy === c.id;
          return (
            <div key={c.id} className={`trader-card ${on ? "" : "opacity-60"}`}>
              <div className="flex items-center gap-2">
                <Identicon ss58={c.target_ss58} size={24} />
                <Link href={`/traders/${c.target_ss58}`} className="trader-card-name truncate no-underline">{name}</Link>
                <span className={`pixel-badge ml-auto ${on ? "border-green-400/40 text-green-400" : "border-amber-400/40 text-amber-400"}`}>
                  {on ? "ON" : "PAUSED"}
                </span>
              </div>
              <p className="trader-card-big text-cyan-400">{fmtValue(c.alloc_tao, currency, usdPerTao)}</p>
              <p className="trader-card-sub">
                following{c.target_info ? ` · ${c.target_info.num_subnets} subnets` : ""}
                {c.last_sync_block ? ` · synced #${c.last_sync_block.toLocaleString()}` : " · not synced yet"}
              </p>
              <div className="flex gap-2 mt-3">
                <button
                  disabled={b}
                  onClick={() => run(c.id, () => (on ? pauseCopy(c.id) : resumeCopy(c.id)))}
                  className="pixel-btn flex-1 py-2 text-[11px]"
                >
                  {on ? "PAUSE" : "RESUME"}
                </button>
                <button
                  disabled={b || !on}
                  onClick={() => run(c.id, () => syncCopy(c.id))}
                  className="pixel-btn px-3 py-2 text-[11px] text-pixel-gray-light"
                  title="Line your stake up with theirs right now"
                >
                  SYNC
                </button>
                <button
                  disabled={b}
                  onClick={() => { if (confirm(`Stop copying ${name}?`)) run(c.id, () => deleteCopy(c.id)); }}
                  className="pixel-btn px-3 py-2 text-[11px] text-red-400"
                >
                  STOP
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
