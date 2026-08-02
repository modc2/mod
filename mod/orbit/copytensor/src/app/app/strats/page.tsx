"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import type { CopyConfig } from "../lib/types";
import {
  fetchCopies, pauseCopy, resumeCopy, deleteCopy, syncCopy, shortSs58,
} from "../lib/api";
import CopyForm from "../components/CopyForm";
import StratPicker from "../components/StratPicker";
import PageHeader from "../components/PageHeader";

type Mode = "index" | "single";

function StratsBody() {
  const params = useSearchParams();
  const target = params.get("target") || "";
  const [mode, setMode] = useState<Mode>("index");
  const [copies, setCopies] = useState<CopyConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    fetchCopies()
      .then(setCopies)
      .catch(() => {})
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  const runOp = async (id: string, op: () => Promise<unknown>) => {
    setBusyId(id);
    try { await op(); } finally { setBusyId(null); load(); }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="STRATS"
        right={
          <>
            <button
              className={`pixel-btn text-[11px] px-3 py-1 ${
                mode === "index" ? "border-green-400 text-green-400" : ""
              }`}
              onClick={() => setMode("index")}
            >
              INDEX OF TRADERS
            </button>
            <button
              className={`pixel-btn text-[11px] px-3 py-1 ${
                mode === "single" ? "border-green-400 text-green-400" : ""
              }`}
              onClick={() => setMode("single")}
            >
              SINGLE TARGET
            </button>
          </>
        }
      >
        Build an <span className="text-green-400">index of traders</span> to
        mirror a weighted basket (polymarket-style), or run a single-target
        copy. Set safety limits, pause/resume, or force-sync at any time.
      </PageHeader>

      {mode === "index" && <StratPicker seedTarget={target} />}

      <section>
        <h2 className="font-display text-lg font-bold mb-3">Active copies</h2>
        {loading ? (
          <p className="text-pixel-gray text-sm">loading…</p>
        ) : copies.length === 0 ? (
          <div className="pixel-panel p-6">
            <p className="arcade-prose">No copies yet. Start one below.</p>
          </div>
        ) : (
          <div className="pixel-panel overflow-hidden">
            <table className="pixel-table">
              <thead className="sticky">
                <tr>
                  <th>ID</th>
                  <th>Target</th>
                  <th>Status</th>
                  <th>Last sync</th>
                  <th className="num">Max/tx</th>
                  <th className="num">Daily</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {copies.map((c) => (
                  <tr key={c.id}>
                    <td className="font-mono text-[11px] text-pixel-gray">
                      {c.id.slice(0, 8)}…
                    </td>
                    <td>
                      <Link
                        href={`/traders/${c.target_ss58}`}
                        className="font-mono text-pixel-white hover:text-green-400 no-underline"
                      >
                        {c.label || shortSs58(c.target_ss58)}
                      </Link>
                    </td>
                    <td>
                      <span
                        className={`pixel-badge ${
                          c.status === "active"
                            ? "border-green-400/40 text-green-400"
                            : c.status === "paused"
                              ? "border-amber-400/40 text-amber-400"
                              : "border-red-400/40 text-red-400"
                        }`}
                      >
                        {c.status}
                      </span>
                    </td>
                    <td className="text-pixel-gray text-xs font-mono">
                      {c.last_sync_block ? `#${c.last_sync_block}` : "—"}
                    </td>
                    <td className="num font-mono">{c.config?.max_tao_per_tx}τ</td>
                    <td className="num font-mono">{c.config?.daily_limit_tao}τ</td>
                    <td>
                      <div className="flex flex-wrap gap-1">
                        {c.status === "active" ? (
                          <button
                            className="pixel-btn text-[10px] px-2 py-0.5"
                            onClick={() => runOp(c.id, () => pauseCopy(c.id))}
                            disabled={busyId === c.id}
                          >
                            PAUSE
                          </button>
                        ) : c.status === "paused" ? (
                          <button
                            className="pixel-btn text-[10px] px-2 py-0.5 border-green-400 text-green-400"
                            onClick={() => runOp(c.id, () => resumeCopy(c.id))}
                            disabled={busyId === c.id}
                          >
                            RESUME
                          </button>
                        ) : null}
                        <button
                          className="pixel-btn text-[10px] px-2 py-0.5"
                          onClick={() => runOp(c.id, () => syncCopy(c.id))}
                          disabled={busyId === c.id}
                        >
                          SYNC
                        </button>
                        <button
                          className="pixel-btn text-[10px] px-2 py-0.5 border-red-400/50 text-red-400"
                          onClick={() => {
                            if (confirm("Delete this copy?"))
                              runOp(c.id, () => deleteCopy(c.id));
                          }}
                          disabled={busyId === c.id}
                        >
                          DEL
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {mode === "single" && (
        <section>
          <h2 className="font-display text-lg font-bold mb-3">New single-target copy</h2>
          <CopyForm defaultTarget={target} />
        </section>
      )}
    </div>
  );
}

export default function StratsPage() {
  return (
    <Suspense fallback={<p className="text-pixel-gray">loading…</p>}>
      <StratsBody />
    </Suspense>
  );
}
