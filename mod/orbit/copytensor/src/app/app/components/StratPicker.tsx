"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import type { IndexTrader, SavedIndex } from "../lib/types";
import {
  shortSs58,
  createCopy,
  pauseCopy,
  resumeCopy,
  deleteCopy,
  syncCopy,
} from "../lib/api";
import {
  loadIndexes,
  saveIndex,
  updateIndex,
  deleteIndex,
  normalizedWeights,
} from "../lib/indexStore";
import { useCurrency, fmtValue } from "../context/CurrencyContext";
import { useSidebar } from "../context/SidebarContext";
import TraderSelect, { type Candidate } from "./TraderSelect";

const PROP_CAPITAL_TAO_DEFAULT = 100;
// Above this many traders, going live asks first.
const BULK_CONFIRM_AT = 25;
// Basket rows painted at once. Weights apply to all of them either way.
const BASKET_PAGE = 50;

type Props = {
  /** Narrow layout — the drawer at its default width, not expanded. */
  compact?: boolean;
};

/**
 * StratPicker — basket builder for an "index of traders" (polymarket-style).
 *
 * The index is stored client-side (localStorage). Activating an index creates
 * one server-side /copy per trader, with `max_tao_per_tx` and `daily_limit_tao`
 * scaled by each trader's weight share of the configured total capital.
 *
 * It lives in the right-hand drawer so you can build a basket while reading
 * the board it comes from — COPY on any row drops that trader straight in.
 */
export default function StratPicker({ compact }: Props) {
  const { currency, usdPerTao } = useCurrency();
  const { stratSeed } = useSidebar();
  const [indexes, setIndexes] = useState<SavedIndex[]>([]);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [name, setName] = useState("My Index");
  const [traders, setTraders] = useState<IndexTrader[]>([]);
  const [hotkey, setHotkey] = useState("");
  const [capitalTao, setCapitalTao] = useState(PROP_CAPITAL_TAO_DEFAULT);
  const [threshold, setThreshold] = useState(5);
  const [maxPerTxTao, setMaxPerTxTao] = useState(10);
  const [pollSec, setPollSec] = useState(300);
  const [picking, setPicking] = useState(true);
  // ALL on a 1000-trader board fills the basket in one click; painting a
  // thousand weight inputs makes every keystroke crawl.
  const [shown, setShown] = useState(BASKET_PAGE);
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

  // Seeded by a COPY button somewhere on the page. Keyed on the nonce so the
  // same address twice still lands.
  useEffect(() => {
    if (!stratSeed?.ss58.length) return;
    addTraders(stratSeed.ss58.map((ss58) => ({ ss58 })));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stratSeed?.nonce]);

  const weights = useMemo(() => normalizedWeights(traders), [traders]);

  const chosen = useMemo(() => new Set(traders.map((t) => t.ss58)), [traders]);

  // Each copy is floored at 1τ/day server-side, so a wide basket on thin
  // capital quietly spends more than you typed. Say so before it happens.
  const enabledCount = useMemo(
    () => traders.filter((t) => t.enabled !== false && t.weight > 0).length,
    [traders],
  );
  const flooredSpend = enabledCount > capitalTao ? enabledCount : 0;

  const totalRawWeight = useMemo(
    () =>
      traders
        .filter((t) => t.enabled !== false)
        .reduce((s, t) => s + t.weight, 0),
    [traders],
  );

  /** Append any number of traders, ignoring ones already in the basket. */
  function addTraders(rows: { ss58: string; label?: string | null }[]) {
    setTraders((cur) => {
      // Dedupe against the basket *and* within the batch — a subnet's top
      // validators are hotkeys, and one coldkey can own several of them.
      const have = new Set(cur.map((t) => t.ss58));
      const fresh: IndexTrader[] = [];
      for (const r of rows) {
        if (have.has(r.ss58)) continue;
        have.add(r.ss58);
        fresh.push({ ss58: r.ss58, label: r.label ?? null, weight: 1, enabled: true });
      }
      return fresh.length ? [...cur, ...fresh] : cur;
    });
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
    // ALL on a 1000-trader board is one click away, and each trader is a
    // server-side copy config that polls the chain. Say the number out loud
    // before spawning it.
    if (
      enabled.length > BULK_CONFIRM_AT &&
      !confirm(
        `Start ${enabled.length} live copies — one per trader, ` +
          `~${Math.max(1, capitalTao / enabled.length).toFixed(2)}τ each per day?`,
      )
    )
      return;
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
    // pb clears the fixed build badge in the bottom-right corner, which
    // otherwise sits on top of the picker's paste row.
    <div className="space-y-4 pb-10">
      <header>
        <h2 className="font-display text-base font-bold mb-1">Index of traders</h2>
        <p className="arcade-prose arcade-prose-sm mt-1">
          Tick any set of traders below. Activating the index spawns one copy
          per trader with capital split by weight — like buying an ETF instead
          of a single stock.
        </p>
      </header>

      {/* Saved indexes */}
      {indexes.length > 0 && (
        <section className="pixel-panel p-2 space-y-2">
          <div className="text-[10px] uppercase tracking-[2px] text-pixel-gray">
            Saved indexes
          </div>
          <ul className="space-y-1">
            {indexes.map((idx) => {
              const isLive = (idx.liveCopyIds?.length || 0) > 0;
              return (
                <li
                  key={idx.id}
                  className="border-t-2 border-pixel-border pt-1 first:border-t-0 first:pt-0"
                >
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-[12px] text-pixel-white truncate flex-1 min-w-0">
                      {idx.name}
                      <span className="text-pixel-gray"> · {idx.traders.length}</span>
                    </span>
                    <span
                      className={`pixel-badge shrink-0 ${
                        isLive ? "border-green-400/40 text-green-400" : "text-pixel-gray"
                      }`}
                    >
                      {isLive ? `LIVE ${idx.liveCopyIds!.length}` : "draft"}
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-1 mt-1">
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
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {/* Builder */}
      <section className="pixel-panel p-3 space-y-3">
        <div className="flex items-baseline justify-between">
          <h3 className="font-display text-sm font-bold">
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

        <div className={`grid gap-2 ${compact ? "grid-cols-1" : "grid-cols-2"}`}>
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

        <div className={`grid gap-2 ${compact ? "grid-cols-2" : "grid-cols-4"}`}>
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

        {/* Basket */}
        <div>
          <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
            <div className="text-[10px] uppercase tracking-[2px] text-pixel-gray shrink-0">
              Basket ({traders.length})
            </div>
            <div className="flex flex-wrap gap-1 justify-end">
              {traders.length > 1 && (
                <button
                  type="button"
                  className="pixel-btn text-[9px] px-1.5 py-0.5"
                  onClick={() =>
                    setTraders((cur) => cur.map((t) => ({ ...t, weight: 1 })))
                  }
                  title="Give every trader the same share"
                >
                  EQUAL
                </button>
              )}
              {traders.length > 0 && (
                <button
                  type="button"
                  className="pixel-btn text-[9px] px-1.5 py-0.5 border-red-400/50 text-red-400"
                  onClick={() => setTraders([])}
                >
                  CLEAR
                </button>
              )}
              <button
                type="button"
                className="pixel-btn text-[10px] px-2 py-0.5 border-green-400 text-green-400"
                onClick={() => setPicking((p) => !p)}
              >
                {picking ? "HIDE PICKER" : "+ PICK TRADERS"}
              </button>
            </div>
          </div>

          {traders.length === 0 ? (
            <div className="arcade-prose arcade-prose-sm">
              Empty basket. Tick traders in the picker, or hit COPY on any row
              of the board or your watchlist.
            </div>
          ) : (
            <table className="pixel-table">
              <thead>
                <tr>
                  {/* Fixed layout + 12px cell padding clips a button in a
                      32px column — the caps get their own widths. */}
                  <th className="!px-1.5" style={{ width: 34 }}></th>
                  <th>Trader</th>
                  <th className="num">Weight</th>
                  <th className="num">Share</th>
                  {!compact && <th className="num">Daily τ</th>}
                  <th className="!px-1" style={{ width: 40 }}></th>
                </tr>
              </thead>
              <tbody>
                {traders.slice(0, shown).map((t) => {
                  const share = weights.get(t.ss58) || 0;
                  const dailyShare = capitalTao * share;
                  return (
                    <tr
                      key={t.ss58}
                      className={t.enabled === false ? "opacity-40" : ""}
                    >
                      <td className="!px-1.5">
                        <input
                          type="checkbox"
                          checked={t.enabled !== false}
                          onChange={() => toggleEnabled(t.ss58)}
                          title="Include in the index"
                        />
                      </td>
                      <td>
                        <Link
                          href={`/traders/${t.ss58}`}
                          className="font-mono text-pixel-white hover:text-green-400 no-underline"
                          title={t.ss58}
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
                          className="pixel-input-sm w-14 text-right font-mono"
                        />
                      </td>
                      <td className="num font-mono text-pixel-gray-light">
                        {(share * 100).toFixed(1)}%
                      </td>
                      {!compact && (
                        <td className="num font-mono">
                          {fmtValue(dailyShare, currency, usdPerTao)}
                        </td>
                      )}
                      <td className="!px-1">
                        <button
                          className="pixel-btn text-[10px] px-1.5 py-0.5 border-red-400/50 text-red-400"
                          onClick={() => removeTrader(t.ss58)}
                          title="Remove from basket"
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

          {traders.length > shown && (
            <button
              type="button"
              onClick={() => setShown((n) => n + BASKET_PAGE)}
              className="pixel-btn text-[10px] w-full py-1 mt-1 text-pixel-gray-light"
            >
              + {Math.min(BASKET_PAGE, traders.length - shown)} MORE
              <span className="text-pixel-gray">
                {" "}· {traders.length - shown} not shown
              </span>
            </button>
          )}

          {flooredSpend > 0 && (
            <p className="mt-2 text-[11px] font-mono text-amber-400">
              {enabledCount} traders on {capitalTao}τ — each copy floors at
              1τ/day, so this would run at {flooredSpend}τ/day. Raise capital
              or trim the basket.
            </p>
          )}

          {picking && (
            <div className="mt-2">
              <TraderSelect
                chosen={chosen}
                compact={compact}
                onAdd={(rows: Candidate[]) =>
                  addTraders(rows.map((r) => ({ ss58: r.ss58, label: r.label })))
                }
              />
            </div>
          )}
        </div>

        {error && (
          <div className="pixel-panel-red px-3 py-2 text-[12px] text-red-400 font-mono break-all">
            {error}
          </div>
        )}
        {info && !error && (
          <div className="pixel-panel px-3 py-2 text-[12px] text-green-400 font-mono">
            {info}
          </div>
        )}

        <div className="flex flex-wrap gap-2">
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
            {busy ? "STARTING…" : `START ${traders.length || ""} LIVE`}
          </button>
        </div>
      </section>
    </div>
  );
}
