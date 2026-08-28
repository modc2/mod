"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import type { Backtest, IndexTrader, ServerStrat, StratVisibility } from "../lib/types";
import {
  shortSs58,
  createCopy,
  pauseCopy,
  resumeCopy,
  deleteCopy,
  syncCopy,
  walletBalance,
  backtestBasket,
  fetchStrats,
  createStrat,
  updateStrat,
  deleteStrat,
} from "../lib/api";
import { loadIndexes, deleteIndex, normalizedWeights } from "../lib/indexStore";
import BacktestPanel from "./BacktestPanel";
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
  const [indexes, setIndexes] = useState<ServerStrat[]>([]);
  const [fingerprint, setFingerprint] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [name, setName] = useState("My Index");
  const [traders, setTraders] = useState<IndexTrader[]>([]);
  const [hotkey, setHotkey] = useState("");
  // Sharing: a strat is private until you say otherwise. `whitelist` holds
  // other people's fingerprints (they read theirs off their own console).
  const [visibility, setVisibility] = useState<StratVisibility>("private");
  const [whitelist, setWhitelist] = useState<string[]>([]);
  const [whitelistDraft, setWhitelistDraft] = useState("");
  // Backtest — re-run on every edit to the basket (see the effect below).
  const [backtest, setBacktest] = useState<Backtest | null>(null);
  const [btLoading, setBtLoading] = useState(false);
  const [btError, setBtError] = useState("");
  const [btDays, setBtDays] = useState(7);
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

  // Load saved strats from the server, and carry over anything still stuck
  // in this browser's old localStorage list (one-shot, then it's deleted —
  // strats live server-side now so they survive a new laptop and can be
  // shared at all).
  const refreshStrats = useCallback(async () => {
    try {
      const res = await fetchStrats();
      setFingerprint(res.fingerprint);
      setIndexes(res.strats);
      return res.strats;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return [];
    }
  }, []);

  useEffect(() => {
    (async () => {
      const server = await refreshStrats();
      const legacy = loadIndexes();
      if (!legacy.length) return;
      const known = new Set(server.map((s) => s.name));
      for (const old of legacy) {
        try {
          if (!known.has(old.name)) {
            await createStrat({
              name: old.name,
              traders: old.traders,
              our_hotkey: old.our_hotkey ?? null,
              max_tao_per_tx: old.max_tao_per_tx ?? null,
              daily_limit_tao: old.daily_limit_tao ?? null,
              rebalance_threshold_pct: old.rebalance_threshold_pct ?? null,
              poll_interval_sec: old.poll_interval_sec ?? null,
              thesis: old.thesis ?? null,
              live_copy_ids: old.liveCopyIds || [],
            });
          }
          deleteIndex(old.id);
        } catch { /* leave it in localStorage and try again next load */ }
      }
      await refreshStrats();
    })();
  }, [refreshStrats]);

  // The hotkey is OUR side of a copy — the account the engine stakes from.
  // It's only needed to go live, and the module already knows it once a
  // wallet is set, so prefill it instead of asking for an ss58 by hand.
  useEffect(() => {
    if (hotkey) return;
    walletBalance()
      .then((w) => { if (w?.ss58) setHotkey(w.ss58); })
      .catch(() => { /* no wallet set — START explains what to do */ });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── backtest on every change ──
  // Any edit to the basket (membership, weight, on/off), the window or the
  // capital re-runs the replay. Debounced, and the response is dropped if
  // another edit landed while it was in flight, so dragging a weight can't
  // paint a stale curve.
  const basketKey = useMemo(
    () =>
      traders
        .filter((t) => t.enabled !== false && t.weight > 0)
        .map((t) => `${t.ss58}:${t.weight}`)
        .sort()
        .join("|"),
    [traders],
  );

  useEffect(() => {
    if (!basketKey) {
      setBacktest(null);
      setBtError("");
      return;
    }
    let live = true;
    setBtLoading(true);
    const timer = setTimeout(async () => {
      try {
        const res = await backtestBasket(traders, btDays, capitalTao);
        if (live) { setBacktest(res); setBtError(""); }
      } catch (e) {
        if (live) { setBacktest(null); setBtError(e instanceof Error ? e.message : String(e)); }
      } finally {
        if (live) setBtLoading(false);
      }
    }, 500);
    return () => { live = false; clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [basketKey, btDays, capitalTao]);

  // Seeded by a COPY button somewhere on the page, or by the strat agent
  // handing over a whole saved index. Keyed on the nonce so the same address
  // twice still lands.
  useEffect(() => {
    if (!stratSeed) return;
    if (stratSeed.indexId) {
      const idx = indexes.find((s) => s.id === stratSeed.indexId);
      if (idx) loadForEdit(idx);
      return;
    }
    if (stratSeed.ss58.length)
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
    setVisibility("private");
    setWhitelist([]);
    setCapitalTao(PROP_CAPITAL_TAO_DEFAULT);
    setThreshold(5);
    setMaxPerTxTao(10);
    setPollSec(300);
  }

  function loadForEdit(idx: ServerStrat) {
    setEditingId(idx.id);
    setName(idx.name);
    setTraders(idx.traders || []);
    if (idx.our_hotkey) setHotkey(idx.our_hotkey);
    setVisibility(idx.visibility);
    setWhitelist(idx.whitelist || []);
    // Reconstruct capital from per-copy max_tao
    setCapitalTao(idx.daily_limit_tao || PROP_CAPITAL_TAO_DEFAULT);
    setMaxPerTxTao(idx.max_tao_per_tx || 10);
    setThreshold(idx.rebalance_threshold_pct ?? 5);
    setPollSec(idx.poll_interval_sec ?? 300);
  }

  /** The current form as the API's write shape. */
  function formBody(liveCopyIds?: string[]) {
    return {
      name: name.trim() || "My Index",
      traders,
      visibility,
      whitelist,
      our_hotkey: hotkey || null,
      max_tao_per_tx: maxPerTxTao,
      daily_limit_tao: capitalTao,
      rebalance_threshold_pct: threshold,
      poll_interval_sec: pollSec,
      live_copy_ids:
        liveCopyIds ?? indexes.find((s) => s.id === editingId)?.live_copy_ids ?? [],
    };
  }

  /** Persist and return the saved row — the shared path for SAVE and START. */
  async function persist(liveCopyIds?: string[]): Promise<ServerStrat> {
    const body = formBody(liveCopyIds);
    const saved = editingId
      ? await updateStrat(editingId, body)
      : await createStrat(body);
    setEditingId(saved.id);
    await refreshStrats();
    return saved;
  }

  async function handleSave() {
    setError("");
    setInfo("");
    if (!name.trim()) return setError("name required");
    if (traders.length < 1) return setError("add at least one trader");
    setBusy(true);
    try {
      const saved = await persist();
      setInfo(`saved "${saved.name}" · ${saved.visibility}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  /** Flip one saved strat's visibility straight from the list. */
  async function setStratVisibility(idx: ServerStrat, vis: StratVisibility) {
    setBusy(true);
    setError("");
    try {
      await updateStrat(idx.id, {
        name: idx.name,
        traders: idx.traders || [],
        visibility: vis,
        whitelist: idx.whitelist || [],
        our_hotkey: idx.our_hotkey ?? null,
        max_tao_per_tx: idx.max_tao_per_tx ?? null,
        daily_limit_tao: idx.daily_limit_tao ?? null,
        rebalance_threshold_pct: idx.rebalance_threshold_pct ?? null,
        poll_interval_sec: idx.poll_interval_sec ?? null,
        thesis: idx.thesis ?? null,
        live_copy_ids: idx.live_copy_ids || [],
      });
      if (editingId === idx.id) setVisibility(vis);
      await refreshStrats();
      setInfo(vis === "public"
        ? `"${idx.name}" is on the hub — anyone can read and clone it`
        : `"${idx.name}" is ${vis}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleStart() {
    setError("");
    setInfo("");
    // Copying stakes from OUR account, so a live run needs our hotkey. It's
    // prefilled from the module's wallet — if it's still empty, the wallet
    // isn't set, and that's the thing to say.
    if (!hotkey)
      return setError(
        "no wallet set — a live copy stakes from your own hotkey, so set one " +
          "(POST /wallet/set, or the WALLET panel) and it fills in here. " +
          "Saving and backtesting need nothing.",
      );
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
    setBusy(true);
    const ids: string[] = [];
    try {
      // Persist first so we have an id to hang the live copies off.
      const saved = await persist();
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
      await persist(ids);
      setInfo(`started ${ids.length} copies`);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleStop(idx: ServerStrat) {
    if (!confirm(`Stop ${idx.name}? (deletes ${idx.live_copy_ids?.length || 0} copies)`)) return;
    setBusy(true);
    try {
      for (const id of idx.live_copy_ids || []) {
        try { await deleteCopy(id); } catch {}
      }
      await updateStrat(idx.id, {
        name: idx.name,
        traders: idx.traders || [],
        visibility: idx.visibility,
        whitelist: idx.whitelist || [],
        our_hotkey: idx.our_hotkey ?? null,
        max_tao_per_tx: idx.max_tao_per_tx ?? null,
        daily_limit_tao: idx.daily_limit_tao ?? null,
        rebalance_threshold_pct: idx.rebalance_threshold_pct ?? null,
        poll_interval_sec: idx.poll_interval_sec ?? null,
        thesis: idx.thesis ?? null,
        live_copy_ids: [],
      });
      await refreshStrats();
      setInfo(`stopped ${idx.name}`);
    } finally {
      setBusy(false);
    }
  }

  async function handleSyncAll(idx: ServerStrat) {
    setBusy(true);
    try {
      for (const id of idx.live_copy_ids || []) {
        try { await syncCopy(id); } catch {}
      }
      setInfo(`synced ${idx.live_copy_ids?.length || 0} copies`);
    } finally {
      setBusy(false);
    }
  }

  async function handlePauseAll(idx: ServerStrat) {
    setBusy(true);
    try {
      for (const id of idx.live_copy_ids || []) {
        try { await pauseCopy(id); } catch {}
      }
      setInfo(`paused ${idx.live_copy_ids?.length || 0} copies`);
    } finally {
      setBusy(false);
    }
  }

  async function handleResumeAll(idx: ServerStrat) {
    setBusy(true);
    try {
      for (const id of idx.live_copy_ids || []) {
        try { await resumeCopy(id); } catch {}
      }
      setInfo(`resumed ${idx.live_copy_ids?.length || 0} copies`);
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
          Tick any set of traders below. Every edit re-runs the backtest.
          Activating the index spawns one copy per trader with capital split
          by weight — like buying an ETF instead of a single stock.
        </p>
        {fingerprint && (
          <p className="mt-1 text-[10px] font-mono text-pixel-gray">
            your id <span className="text-pixel-white">{fingerprint}</span> — give
            it to someone who should see a whitelisted strat of yours
          </p>
        )}
      </header>

      {/* Saved indexes */}
      {indexes.length > 0 && (
        <section className="pixel-panel p-2 space-y-2">
          <div className="text-[10px] uppercase tracking-[2px] text-pixel-gray">
            Saved indexes
          </div>
          <ul className="space-y-1">
            {indexes.map((idx) => {
              const isLive = (idx.live_copy_ids?.length || 0) > 0;
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
                        idx.visibility === "public"
                          ? "border-green-400/40 text-green-400"
                          : idx.visibility === "whitelist"
                            ? "border-amber-400/40 text-amber-400"
                            : "text-pixel-gray"
                      }`}
                      title={
                        idx.visibility === "public"
                          ? "on the hub — anyone can read and clone it"
                          : idx.visibility === "whitelist"
                            ? `shared with ${(idx.whitelist || []).length} id(s)`
                            : "only you can see this"
                      }
                    >
                      {idx.visibility.toUpperCase()}
                    </span>
                    <span
                      className={`pixel-badge shrink-0 ${
                        isLive ? "border-green-400/40 text-green-400" : "text-pixel-gray"
                      }`}
                    >
                      {isLive ? `LIVE ${idx.live_copy_ids!.length}` : "draft"}
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
                    {idx.mine && (
                      <button
                        className={`pixel-btn text-[10px] px-2 py-0.5 ${
                          idx.visibility === "public" ? "border-green-400 text-green-400" : ""
                        }`}
                        onClick={() =>
                          setStratVisibility(
                            idx,
                            idx.visibility === "public" ? "private" : "public",
                          )
                        }
                        disabled={busy}
                        title={
                          idx.visibility === "public"
                            ? "Take it off the hub"
                            : "Publish to the hub — anyone can read and clone it"
                        }
                      >
                        {idx.visibility === "public" ? "UNPUBLISH" : "PUBLISH"}
                      </button>
                    )}
                    {idx.mine && (
                      <button
                        className="pixel-btn text-[10px] px-2 py-0.5 border-red-400/50 text-red-400"
                        onClick={async () => {
                          if (!confirm(`Delete index "${idx.name}"?`)) return;
                          setBusy(true);
                          try {
                            await deleteStrat(idx.id);
                            if (editingId === idx.id) resetForm();
                            await refreshStrats();
                          } catch (e) {
                            setError(e instanceof Error ? e.message : String(e));
                          } finally {
                            setBusy(false);
                          }
                        }}
                        disabled={busy}
                      >
                        DEL
                      </button>
                    )}
                    {!idx.mine && (
                      <span className="pixel-badge shrink-0 text-pixel-gray" title={`owner ${idx.owner_fingerprint}`}>
                        shared with you
                      </span>
                    )}
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
              Our hotkey <span className="text-pixel-gray/70">· live only</span>
            </div>
            <input
              value={hotkey}
              onChange={(e) => setHotkey(e.target.value)}
              className="pixel-input w-full font-mono text-sm"
              placeholder={"set a wallet and this fills itself"}
              title="The account copies stake FROM. Filled in from the module's wallet; saving and backtesting don't need it."
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

        {/* Who can see it. Private is the default and stays the default —
            publishing and whitelisting are things you do on purpose. */}
        <div className="border-t-2 border-pixel-border pt-2 space-y-2">
          <div className="text-[10px] uppercase tracking-[2px] text-pixel-gray">
            Sharing
          </div>
          <div className="flex flex-wrap gap-1">
            {(["private", "whitelist", "public"] as StratVisibility[]).map((v) => (
              <button
                key={v}
                type="button"
                className={`pixel-btn text-[10px] px-2 py-0.5 ${
                  visibility === v ? "border-green-400 text-green-400" : ""
                }`}
                onClick={() => setVisibility(v)}
                title={
                  v === "private"
                    ? "Only you"
                    : v === "whitelist"
                      ? "You plus the ids you list"
                      : "On the hub — anyone can read and clone it"
                }
              >
                {v.toUpperCase()}
              </button>
            ))}
          </div>
          {visibility === "whitelist" && (
            <div className="space-y-1">
              <div className="flex gap-1">
                <input
                  value={whitelistDraft}
                  onChange={(e) => setWhitelistDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key !== "Enter") return;
                    const id = whitelistDraft.trim().toLowerCase();
                    if (id && !whitelist.includes(id)) setWhitelist([...whitelist, id]);
                    setWhitelistDraft("");
                  }}
                  className="pixel-input flex-1 font-mono text-[12px]"
                  placeholder="paste someone's id, then Enter"
                />
              </div>
              {whitelist.length === 0 ? (
                <p className="arcade-prose arcade-prose-sm">
                  Nobody yet. Each console shows its own id at the top of this
                  panel — that's what you paste here.
                </p>
              ) : (
                <div className="flex flex-wrap gap-1">
                  {whitelist.map((id) => (
                    <button
                      key={id}
                      type="button"
                      className="pixel-badge text-pixel-gray-light"
                      onClick={() => setWhitelist(whitelist.filter((x) => x !== id))}
                      title="Remove"
                    >
                      {id} ×
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
          <p className="arcade-prose arcade-prose-sm">
            {visibility === "private"
              ? "Only this browser's key can read it."
              : visibility === "whitelist"
                ? "You, plus the ids above. Changes apply when you save."
                : "Anyone can read and clone it from the hub. Your ids and wallet stay yours."}
          </p>
        </div>

        <BacktestPanel
          backtest={backtest}
          loading={btLoading}
          error={btError}
          days={btDays}
          onDays={setBtDays}
          compact={compact}
        />

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
