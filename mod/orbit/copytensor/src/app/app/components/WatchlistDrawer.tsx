"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { fetchWatches, unwatchAccount, shortSs58 } from "../lib/api";
import type { AccountWatch } from "../lib/types";

export default function WatchlistDrawer({ onClose }: { onClose: () => void }) {
  const [accounts, setAccounts] = useState<AccountWatch[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");

  const load = () => {
    setLoading(true);
    fetchWatches()
      .then((r) => setAccounts(r.accounts || []))
      .catch((e) => setErr(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const remove = async (ss58: string) => {
    try {
      await unwatchAccount(ss58);
      setAccounts((a) => a.filter((x) => x.ss58 !== ss58));
    } catch (e: unknown) {
      setErr(String(e));
    }
  };

  return (
    <div className="pixel-panel p-3 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-[12px] tracking-[3px] uppercase text-pixel-gray-light font-mono">
          watchlist
        </h3>
        <button
          onClick={onClose}
          className="pixel-btn text-[10px] px-2 py-0.5"
          title="Close drawer"
        >
          ✕
        </button>
      </div>

      {err && <p className="text-red-400 text-xs">{err}</p>}
      {loading && <p className="text-pixel-gray text-xs">loading…</p>}
      {!loading && accounts.length === 0 && (
        <p className="text-pixel-gray text-xs">
          No watched accounts. Lookup an SS58 to add one.
        </p>
      )}

      <ul className="space-y-1">
        {accounts.map((a) => (
          <li
            key={a.ss58}
            className="flex items-center justify-between gap-2 hover:bg-pixel-white/5 px-2 py-1.5 rounded"
          >
            <Link
              href={`/traders/${a.ss58}`}
              className="font-mono text-[12px] text-pixel-white truncate flex-1"
              title={a.ss58}
            >
              {a.label || shortSs58(a.ss58)}
            </Link>
            <button
              onClick={() => remove(a.ss58)}
              title="Remove from watchlist"
              className="text-[10px] text-pixel-gray hover:text-red-400 font-mono"
            >
              ✕
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
