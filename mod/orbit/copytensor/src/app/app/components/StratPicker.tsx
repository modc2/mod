"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import type { IndexTrader, LeaderboardEntry, SavedIndex } from "../lib/types";
import {
  fetchLeaderboard,
  fmtTao,
  shortSs58,
  createCopy,
  pauseCopy,
  resumeCopy,
  deleteCopy,
  syncCopy,
  fetchCopies,
} from "../lib/api";
import {
  loadIndexes,
  saveIndex,
  updateIndex,
  deleteIndex,
  normalizedWeights,
} from "../lib/indexStore";

const PROP_CAPITAL_TAO_DEFAULT = 100;

type Props = {
  /** Optional ss58 to seed as the first trader (from leaderboard "COPY" button) */
  seedTarget?: string;
};

/**
 * StratPicker — basket builder for an "index of traders" (polymarket-style).
 *
 * The index is stored client-side (localStorage). Activating an index creates
 * one server-side /copy per trader, with `max_tao_per_tx` and `daily_limit_tao`
 * scaled by each trader's weight share of the configured total capital.
 */
export default function StratPicker({ seedTarget }: Props) {
  const [indexes, setIndexes] = useState<SavedIndex[]>([]);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [name, setName] = useState("My Index");
  const [traders, setTraders] = useState<IndexTrader[]>([]);
  const [hotkey, setHotkey] = useState("");
  const [capitalTao, setCapitalTao] = useState(PROP_CAPITAL_TAO_DEFAULT);
  const [threshold, setThreshold] = useState(5);
  const [maxPerTxTao, setMaxPerTxTao] = useState(10);
  const [pollSec, setPollSec] = useState(300);
  const [leaderboard, setLeaderboard] = useState<LeaderboardEntry[]>([]);
  const [picking, setPicking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");

  // Load saved indexes
  useEffect(() => {
    setIndexes(loadIndexes());
    const onChange = () => setIndexes(loadIndexes());
    window.addEventListener("copytensor:indexes-changed", onChange);
    return () => window.removeEventListener("copytensor:indexes-changed", onChange);
  }, []);

  // Seed from leaderboard COPY button
  useEffect(() => {
    if (seedTarget && !traders.some((t) => t.ss58 === seedTarget)) {
      setTraders((cur) => [
        ...cur,
        { ss58: seedTarget, weight: 1, enabled: true },
      ]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seedTarget]);

  // Leaderboard for the picker
  useEffect(() => {
    fetchLeaderboard(7, 100).then(setLeaderboard).catch(() => {});
  }, []);

  const weights = useMemo(() => normalizedWeights(traders), [traders]);

  const totalRawWeight = useMemo(
    () =>
      traders
        .filter((t) => t.enabled !== false)
        .reduce((s, t) => s + t.weight, 0),
    [traders],
  );

  function addTrader(entry: LeaderboardEntry) {
    setTraders((cur) =>
      cur.some((t) => t.ss58 === entry.ss58)
        ? cur
        : [
            ...cur,
            {
              ss58: entry.ss58,
              label: entry.label || null,
              weight: 1,
              enabled: true,
            },
          ],
    );
  }

  function setWeight(ss58: string, w: number) {
    setTraders((cur) =>
      cur.map((t) => (t.ss58 === ss58 ? { ...t, weight: Math.max(0, w) } : t)),
    );
  }

  function toggleEnabled(ss58: string) {
    setTraders((cur) =>
      cur.map((t) =>
        t.ss58 === ss58 ? { ...t, enabled: !(t.enabled !== false) } : t,
      ),
    );
  }

  function removeTrader(ss58: string) {
    setTraders((cur) => cur.filter((t) => t.ss58 !== ss58));
  }

  function resetForm() {
    setEditingId(null);
    setName("My Index");
    setTraders([]);
    setHotkey("");
    setCapitalTao(PROP_CAPITAL_TAO_DEFAULT);
    setThreshold(5);
    setMaxPerTxTao(10);
    setPollSec(300);
  }

  function loadForEdit(idx: SavedIndex) {
    setEditingId(idx.id);
    setName(idx.name);
    setTraders(idx.traders);
    setHotkey(idx.our_hotkey || "");
    // Reconstruct capital from per-copy max_tao
    setCapitalTao(idx.daily_limit_tao || PROP_CAPITAL_TAO_DEFAULT);
    setMaxPerTxTao(idx.max_tao_per_tx || 10);
    setThreshold(idx.rebalance_threshold_pct ?? 5);
    setPollSec(idx.poll_interval_sec ?? 300);
  }

  function handleSave() {
    setError("");
    setInfo("");
    if (!name.trim()) return setError("name required");
    if (traders.length < 1) return setError("add at least one trader");
    const saved = saveIndex({
      id: editingId || undefined,
      name: name.trim(),
      traders,
      our_hotkey: hotkey || undefined,
      max_tao_per_tx: maxPerTxTao,
      daily_limit_tao: capitalTao,
      rebalance_threshold_pct: threshold,
      poll_interval_sec: pollSec,
    });
    setEditingId(saved.id);
    setInfo(`saved "${saved.name}"`);
  }

  async function handleStart() {
    setError("");
    setInfo("");
    if (!hotkey) return setError("our hotkey ss58 required");
    if (traders.length < 1) return setError("add traders first");
    const enabled = traders.filter((t) => t.enabled !== false && t.weight > 0);
    if (enabled.length < 1) return setError("no enabled traders with weight");
    // Persist first so we have an id
    const saved = saveIndex({
      id: editingId || undefined,
      name: name.trim() || "My Index",
      traders,
      our_hotkey: hotkey,
      max_tao_per_tx: maxPerTxTao,
      daily_limit_tao: capitalTao,
      rebalance_threshold_pct: threshold,
      poll_interval_sec: pollSec,
    });
    setEditingId(saved.id);

    setBusy(true);
    const ids: string[] = [];
    try {
      const total = enabled.reduce((s, t) => s + t.weight, 0);
      for (const t of enabled) {
        const share = t.weight / total;
        const dailyShare = Math.max(1, capitalTao * share);
        const maxTxShare = Math.max(0.1, Math.min(maxPerTxTao, dailyShare));
        const label = `${saved.name}:${shortSs58(t.ss58)}`;
        const res = await createCopy({
          target_ss58: t.ss58,
          our_hotkey: hotkey,
          label,
          max_tao_per_tx: maxTxShare,
          daily_limit_tao: dailyShare,
          rebalance_threshold_pct: threshold,
        } as any);
        ids.push((res as any).id);
      }
      updateIndex(saved.id, { liveCopyIds: ids });
      setInfo(`started ${ids.length} copies`);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleStop(idx: SavedIndex) {
    if (!confirm(`Stop ${idx.name}? (deletes ${idx.liveCopyIds?.length || 0} copies)`)) return;
    setBusy(true);
    try {
      for (const id of idx.liveCopyIds || []) {
        try { await deleteCopy(id); } catch {}
      }
      updateIndex(idx.id, { liveCopyIds: [] });
      setInfo(`stopped ${idx.name}`);
    } finally {
      setBusy(false);
    }
  }

  async function handleSyncAll(idx: SavedIndex) {
    setBusy(true);
    try {
      for (const id of idx.liveCopyIds || []) {
        try { await syncCopy(id); } catch {}
      }
      setInfo(`synced ${idx.liveCopyIds?.length || 0} copies`);
    } finally {
      setBusy(false);
    }
  }

  async function handlePauseAll(idx: SavedIndex) {
    setBusy(true);
    try {
      for (const id of idx.liveCopyIds || []) {
        try { await pauseCopy(id); } catch {}
      }
      setInfo(`paused ${idx.liveCopyIds?.length || 0} copies`);
    } finally {
      setBusy(false);
    }
  }

  async function handleResumeAll(idx: SavedIndex) {
    setBusy(true);
    try {
      for (const id of idx.liveCopyIds || []) {
        try { await resumeCopy(id); } catch {}
      }
      setInfo(`resumed ${idx.liveCopyIds?.length || 0} copies`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <header>
        <h2 className="font-display text-lg font-bold mb-1">Index of traders</h2>
        <p className="text-pixel-gray-light text-xs">
          Build a weighted basket of validators. Activating the index spawns one
          copy per trader with capital split by weight — like buying an ETF
          instead of a single stock.
        </p>
      </header>

      {/* Saved indexes */}
      {indexes.length > 0 && (
        <section className="pixel-panel p-3 space-y-2">
          <div className="text-[10px] uppercase tracking-[2px] text-pixel-gray">
            Saved indexes
          </div>
          <table className="pixel-table">
            <thead>
              <tr>
                <th>Name</th>
                <th className="num">Traders</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {indexes.map((idx) => {
                const isLive = (idx.liveCopyIds?.length || 0) > 0;
                return (
                  <tr key={idx.id}>
                    <td className="font-mono">{idx.name}</td>
                    <td className="num font-mono">{idx.traders.length}</td>
                    <td>
                      <span
                        className={`pixel-badge ${
                          isLive
                            ? "border-green-400/40 text-green-400"
                            : "text-pixel-gray"
                        }`}
                      >
                        {isLive ? `LIVE (${idx.liveCopyIds!.length})` : "draft"}
                      </span>
                    </td>
                    <td>
                      <div className="flex flex-wrap gap-1">
                        <button
                          className="pixel-btn text-[10px] px-2 py-0.5"
                          onClick={() => loadForEdit(idx)}
                          disabled={busy}
                        >
                          EDIT
                        </button>
                        {isLive && (
                          <>
                            <button
                              className="pixel-btn text-[10px] px-2 py-0.5"
                              onClick={() => handleSyncAll(idx)}
                              disabled={busy}
                            >
                              SYNC
                            </button>
                            <button
                              className="pixel-btn text-[10px] px-2 py-0.5"
                              onClick={() => handlePauseAll(idx)}
                              disabled={busy}
                            >
                              PAUSE
                            </button>
                            <button
                              className="pixel-btn text-[10px] px-2 py-0.5 border-green-400 text-green-400"
                              onClick={() => handleResumeAll(idx)}
                              disabled={busy}
                            >
                              RESUME
                            </button>
                            <button
                              className="pixel-btn text-[10px] px-2 py-0.5 border-red-400/50 text-red-400"
                              onClick={() => handleStop(idx)}
                              disabled={busy}
                            >
                              STOP
                            </button>
                          </>
                        )}
                        <button
                          className="pixel-btn text-[10px] px-2 py-0.5 border-red-400/50 text-red-400"
                          onClick={() => {
                            if (confirm(`Delete index "${idx.name}"?`)) {
                              deleteIndex(idx.id);
                              if (editingId === idx.id) resetForm();
                            }
                          }}
                          disabled={busy}
                        >
                          DEL
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      )}

      {/* Builder */}
      <section className="pixel-panel p-4 space-y-4">
        <div className="flex items-baseline justify-between">
          <h3 className="font-display text-base font-bold">
            {editingId ? "Edit index" : "New index"}
          </h3>
          {editingId && (
            <button
              className="pixel-btn text-[10px] px-2 py-0.5"
              onClick={resetForm}
            >
              NEW
            </button>
          )}
        </div>

        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <div className="text-[10px] uppercase tracking-[2px] text-pixel-gray mb-1">
              Name
            </div>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="pixel-input w-full font-mono text-sm"
              placeholder="My Index"
            />
          </label>
          <label className="block">
            <div className="text-[10px] uppercase tracking-[2px] text-pixel-gray mb-1">
              Our hotkey ss58
            </div>
            <input
              value={hotkey}
              onChange={(e) => setHotkey(e.target.value)}
              className="pixel-input w-full font-mono text-sm"
              placeholder="5..."
            />
          </label>
        </div>

        <div className="grid grid-cols-4 gap-3">
          <label className="block">
            <div className="text-[10px] uppercase tracking-[2px] text-pixel-gray mb-1">
              Total capital (τ)
            </div>
            <input
              type="number" min="1" step="1"
              value={capitalTao}
              onChange={(e) => setCapitalTao(Number(e.target.value) || 0)}
              className="pixel-input w-full font-mono text-sm"
            />
          </label>
          <label className="block">
            <div className="text-[10px] uppercase tracking-[2px] text-pixel-gray mb-1">
              Max τ / tx
            </div>
            <input
              type="number" min="0.1" step="0.1"
              value={maxPerTxTao}
              onChange={(e) => setMaxPerTxTao(Number(e.target.value) || 0)}
              className="pixel-input w-full font-mono text-sm"
            />
          </label>
          <label className="block">
            <div className="text-[10px] uppercase tracking-[2px] text-pixel-gray mb-1">
              Rebal threshold %
            </div>
            <input
              type="number" min="0.5" step="0.5"
              value={threshold}
              onChange={(e) => setThreshold(Number(e.target.value) || 0)}
              className="pixel-input w-full font-mono text-sm"
            />
          </label>
          <label className="block">
            <div className="text-[10px] uppercase tracking-[2px] text-pixel-gray mb-1">
              Poll interval (s)
            </div>
            <input
              type="number" min="30" step="30"
              value={pollSec}
              onChange={(e) => setPollSec(Number(e.target.value) || 0)}
              className="pixel-input w-full font-mono text-sm"
            />
          </label>
        </div>

        {/* Traders table */}
        <div>
          <div className="flex items-center justify-between mb-2">
            <div className="text-[10px] uppercase tracking-[2px] text-pixel-gray">
              Traders ({traders.length})
            </div>
            <button
              type="button"
              className="pixel-btn text-[10px] px-2 py-0.5 border-green-400 text-green-400"
              onClick={() => setPicking((p) => !p)}
            >
              {picking ? "DONE" : "+ ADD FROM LEADERBOARD"}
            </button>
          </div>

          {traders.length === 0 ? (
            <div className="text-pixel-gray text-xs italic">
              No traders yet. Open the picker above or paste an ss58 below.
            </div>
          ) : (
            <table className="pixel-table">
              <thead>
                <tr>
                  <th style={{ width: 32 }}></th>
                  <th>Trader</th>
                  <th className="num">Weight</th>
                  <th className="num">Share</th>
                  <th className="num">Daily τ</th>
                  <th style={{ width: 40 }}></th>
                </tr>
              </thead>
              <tbody>
                {traders.map((t) => {
                  const share = weights.get(t.ss58) || 0;
                  const dailyShare = capitalTao * share;
                  return (
                    <tr
                      key={t.ss58}
                      className={t.enabled === false ? "opacity-40" : ""}
                    >
                      <td>
                        <input
                          type="checkbox"
                          checked={t.enabled !== false}
                          onChange={() => toggleEnabled(t.ss58)}
                        />
                      </td>
                      <td>
                        <Link
                          href={`/traders/${t.ss58}`}
                          className="font-mono text-pixel-white hover:text-green-400 no-underline"
                        >
                          {t.label || shortSs58(t.ss58)}
                        </Link>
                      </td>
                      <td className="num">
                        <input
                          type="number" min="0" step="0.1"
                          value={t.weight}
                          onChange={(e) =>
                            setWeight(t.ss58, Number(e.target.value) || 0)
                          }
                          className="pixel-input-sm w-16 text-right font-mono"
                        />
                      </td>
                      <td className="num font-mono text-pixel-gray-light">
                        {(share * 100).toFixed(1)}%
                      </td>
                      <td className="num font-mono">{fmtTao(dailyShare)}</td>
                      <td>
                        <button
                          className="pixel-btn text-[10px] px-1.5 py-0.5 border-red-400/50 text-red-400"
                          onClick={() => removeTrader(t.ss58)}
                        >
                          ×
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}

          {/* Paste an ss58 directly */}
          <PasteTrader
            onAdd={(ss58) =>
              setTraders((cur) =>
                cur.some((t) => t.ss58 === ss58)
                  ? cur
                  : [...cur, { ss58, weight: 1, enabled: true }],
              )
            }
          />

          {picking && (
            <div className="mt-3 pixel-panel p-2 max-h-[300px] overflow-y-auto">
              <div className="text-[10px] uppercase tracking-[2px] text-pixel-gray mb-1">
                Pick from leaderboard
              </div>
              <table className="pixel-table">
                <thead>
                  <tr>
                    <th>Trader</th>
                    <th className="num">Stake</th>
                    <th className="num">7d PnL</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {leaderboard.map((e) => {
                    const added = traders.some((t) => t.ss58 === e.ss58);
                    return (
                      <tr key={e.ss58}>
                        <td className="font-mono">
                          {e.label || shortSs58(e.ss58)}
                        </td>
                        <td className="num font-mono">
                          {fmtTao(e.total_stake_tao)}
                        </td>
                        <td
                          className={`num font-mono ${
                            e.pnl_tao >= 0 ? "text-green-400" : "text-red-400"
                          }`}
                        >
                          {e.pnl_tao >= 0 ? "+" : ""}
                          {e.pnl_pct.toFixed(2)}%
                        </td>
                        <td>
                          <button
                            className="pixel-btn text-[10px] px-2 py-0.5"
                            disabled={added}
                            onClick={() => addTrader(e)}
                          >
                            {added ? "ADDED" : "+ ADD"}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {error && (
          <div className="pixel-panel-red px-3 py-2 text-[12px] text-red-400 font-mono">
            {error}
          </div>
        )}
        {info && !error && (
          <div className="pixel-panel px-3 py-2 text-[12px] text-green-400 font-mono">
            {info}
          </div>
        )}

        <div className="flex gap-2">
          <button
            className="pixel-btn"
            onClick={handleSave}
            disabled={busy}
            type="button"
          >
            SAVE DRAFT
          </button>
          <button
            className="pixel-btn border-green-400 text-green-400"
            onClick={handleStart}
            disabled={busy || totalRawWeight <= 0}
            type="button"
          >
            {busy ? "STARTING…" : "START INDEX LIVE"}
          </button>
        </div>
      </section>
    </div>
  );
}

function PasteTrader({ onAdd }: { onAdd: (ss58: string) => void }) {
  const [v, setV] = useState("");
  return (
    <form
      className="mt-3 flex gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        const s = v.trim();
        if (s.startsWith("5") && s.length >= 40) {
          onAdd(s);
          setV("");
        }
      }}
    >
      <input
        value={v}
        onChange={(e) => setV(e.target.value)}
        placeholder="paste an ss58 to add manually…"
        className="pixel-input-sm flex-1 font-mono text-xs"
      />
      <button className="pixel-btn text-[10px] px-2 py-1" type="submit">
        + ADD
      </button>
    </form>
  );
}
