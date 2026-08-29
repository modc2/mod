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
// Mirrors MIN_SLEEVE_TAO in src/engine/allocator.py — a sleeve below this is
// reported and skipped rather than staked as dust.
const MIN_SLEEVE_TAO = 0.05;
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
 * Two ways to size a basket, and they are the same basket underneath:
 *   SPLIT  — a total pot divided by relative weights (good for "equal-weight
 *            my top 10")
 *   PER-TRADER τ — type the TAO behind each trader directly (good for "40 on
 *            this one, 10 on that one")
 * Switching between them carries the numbers over, so neither is a dead end.
 *
 * Either way, going live resolves to one `alloc_tao` per trader. That is the
 * unit the server allocator works in: every active copy contributes its
 * trader's SHAPE at its own SIZE, and they blend into one desired book.
 * Before this, weights only became a daily spend cap that the engine ignored,
 * and each copy independently drove the whole portfolio to its own target.
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
  // "split" = one pot cut by weight; "tao" = a TAO figure typed per trader.
  const [sizing, setSizing] = useState<"split" | "tao">("split");
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
  // Any edit to the basket (membership, weight or τ, on/off), the window or
  // the capital re-runs the replay. Debounced, and the response is dropped
  // if another edit landed while it was in flight, so dragging a weight
  // can't paint a stale curve.
  //
  // Deliberately built from raw fields rather than the resolved sizes: this
  // runs during render, and `allocOf` below is a const the render hasn't
  // reached yet.
  const basketKey = useMemo(
    () =>
      sizing +
      "/" +
      traders
        .filter((t) => t.enabled !== false)
        .map((t) => `${t.ss58}:${t.weight}:${t.alloc_tao ?? ""}`)
        .sort()
        .join("|"),
    [traders, sizing],
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
        // Send the RESOLVED sizes: a τ basket must be replayed at the money
        // actually behind each trader, not at a stale relative weight.
        const rows = traders.map((t) => ({ ...t, alloc_tao: allocOf(t) || null }));
        const res = await backtestBasket(rows, btDays, totalAlloc);
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

  // ── sizing ──
  // One function answers "how much TAO is behind this trader", whichever
  // mode you're in, and everything downstream (the table, the total, the
  // backtest, going live) reads it rather than re-deriving the split.
  const allocOf = useCallback(
    (t: IndexTrader): number => {
      if (t.enabled === false) return 0;
      if (sizing === "tao") return Math.max(0, t.alloc_tao ?? 0);
      const share = t.weight > 0 ? weights.get(t.ss58) || 0 : 0;
      return capitalTao * share;
    },
    [sizing, weights, capitalTao],
  );

  /** The pot. Typed in SPLIT mode, the sum of the sleeves in τ mode. */
  const totalAlloc = useMemo(
    () =>
      sizing === "tao"
        ? traders.reduce((s, t) => s + allocOf(t), 0)
        : capitalTao,
    [sizing, traders, allocOf, capitalTao],
  );

  const enabledCount = useMemo(
    () => traders.filter((t) => t.enabled !== false && allocOf(t) > 0).length,
    [traders, allocOf],
  );

  // A sleeve under the server's floor is skipped, not staked — so a basket
  // that's too wide for its capital silently drops its tail. Say which.
  const dustLegs = useMemo(
    () =>
      traders.filter(
        (t) => t.enabled !== false && allocOf(t) > 0 && allocOf(t) < MIN_SLEEVE_TAO,
      ).length,
    [traders, allocOf],
  );

  /** Swap sizing mode, seeding the new mode from what's on screen now. */
  function switchSizing(next: "split" | "tao") {
    if (next === sizing) return;
    if (next === "tao") {
      // Freeze the current split into per-trader TAO so nothing jumps.
      setTraders((cur) =>
        cur.map((t) => ({ ...t, alloc_tao: Number(allocOf(t).toFixed(4)) })),
      );
    } else {
      // Weights ARE the allocations, just un-normalized; the pot is their sum.
      const total = traders.reduce((s, t) => s + allocOf(t), 0);
      setTraders((cur) =>
        cur.map((t) => ({ ...t, weight: allocOf(t) || (t.enabled === false ? t.weight : 0) })),
      );
      if (total > 0) setCapitalTao(Number(total.toFixed(4)));
    }
    setSizing(next);
  }

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
        fresh.push({
          ss58: r.ss58,
          label: r.label ?? null,
          weight: 1,
          // In τ mode a fresh row would otherwise be a 0τ sleeve that does
          // nothing; seed it with an even cut of the pot so it's live and
          // visible the moment it lands.
          alloc_tao:
            sizing === "tao"
              ? Number((capitalTao / Math.max(1, cur.length + rows.length)).toFixed(4))
              : null,
          enabled: true,
        });
      }
      return fresh.length ? [...cur, ...fresh] : cur;
    });
  }

  function setWeight(ss58: string, w: number) {
    setTraders((cur) =>
      cur.map((t) => (t.ss58 === ss58 ? { ...t, weight: Math.max(0, w) } : t)),
    );
  }

  /** The TAO behind one trader, typed directly. */
  function setAlloc(ss58: string, tao: number) {
    setTraders((cur) =>
      cur.map((t) =>
        t.ss58 === ss58 ? { ...t, alloc_tao: Math.max(0, tao) } : t,
      ),
    );
  }

  /** Split the pot evenly across everything enabled. */
  function equalize() {
    const live = traders.filter((t) => t.enabled !== false);
    if (sizing === "split") {
      setTraders((cur) => cur.map((t) => ({ ...t, weight: 1 })));
      return;
    }
    if (!live.length) return;
    const each = Number((totalAlloc / live.length).toFixed(4));
    setTraders((cur) =>
      cur.map((t) => (t.enabled === false ? t : { ...t, alloc_tao: each })),
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
    // A basket saved with per-trader τ reopens in τ mode; one saved before
    // that existed (or sized by weight) reopens as a split.
    const sized =
      idx.sizing ||
      ((idx.traders || []).some((t) => (t.alloc_tao ?? 0) > 0) ? "tao" : "split");
    setSizing(sized);
    setCapitalTao(
      sized === "tao"
        ? (idx.traders || []).reduce(
            (s, t) => s + (t.enabled === false ? 0 : t.alloc_tao || 0),
            0,
          ) || PROP_CAPITAL_TAO_DEFAULT
        : idx.daily_limit_tao || PROP_CAPITAL_TAO_DEFAULT,
    );
    setMaxPerTxTao(idx.max_tao_per_tx || 10);
    setThreshold(idx.rebalance_threshold_pct ?? 5);
    setPollSec(idx.poll_interval_sec ?? 300);
  }

  /** The current form as the API's write shape. */
  function formBody(liveCopyIds?: string[]) {
    return {
      name: name.trim() || "My Index",
      // Freeze the resolved τ onto every row. A basket sized by weight still
      // saves what that weight came out to, so reopening it — or handing it
      // to someone else — shows the same money it was built on.
      traders: traders.map((t) => ({
        ...t,
        alloc_tao: Number(allocOf(t).toFixed(6)) || null,
      })),
      visibility,
      whitelist,
      our_hotkey: hotkey || null,
      sizing,
      max_tao_per_tx: maxPerTxTao,
      daily_limit_tao: totalAlloc,
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
    const enabled = traders.filter((t) => t.enabled !== false && allocOf(t) > 0);
    if (enabled.length < 1)
      return setError(
        sizing === "tao"
          ? "no trader has any τ allocated"
          : "no enabled traders with weight",
      );
    const tooSmall = enabled.filter((t) => allocOf(t) < MIN_SLEEVE_TAO);
    if (tooSmall.length)
      return setError(
        `${tooSmall.length} trader(s) are under the ${MIN_SLEEVE_TAO}τ ` +
          `minimum sleeve and would be skipped — raise the capital, trim the ` +
          `basket, or give them more τ.`,
      );
    // ALL on a 1000-trader board is one click away, and each trader is a
    // server-side copy config that polls the chain. Say the number out loud
    // before spawning it.
    if (
      enabled.length > BULK_CONFIRM_AT &&
      !confirm(
        `Start ${enabled.length} live copies, ` +
          `${totalAlloc.toFixed(2)}τ allocated in total?`,
      )
    )
      return;
    setBusy(true);
    const ids: string[] = [];
    try {
      // Persist first so we have an id to hang the live copies off.
      const saved = await persist();
      for (const t of enabled) {
        const alloc = allocOf(t);
        const label = `${saved.name}:${shortSs58(t.ss58)}`;
        const res = await createCopy({
          target_ss58: t.ss58,
          our_hotkey: hotkey,
          label,
          // The money behind this trader. Every live copy contributes its
          // trader's shape at this size, and the server blends them into
          // one book — so these add up instead of overwriting each other.
          alloc_tao: Number(alloc.toFixed(6)),
          max_tao_per_tx: Math.max(0.1, Math.min(maxPerTxTao, alloc)),
          rebalance_threshold_pct: threshold,
          poll_interval_sec: pollSec,
        });
        ids.push(res.id);
      }
      await persist(ids);
      setInfo(
        `started ${ids.length} copies · ${totalAlloc.toFixed(2)}τ allocated`,
      );
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
          Tick any set of traders below, then decide the money behind each
          one — a pot split by weight, or a τ figure typed per trader. Every
          edit re-runs the backtest. Going live blends all of them into one
          book, so the sleeves add up instead of fighting over your stake.
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
              {sizing === "tao" && (
                <span className="text-pixel-gray/70"> · sum</span>
              )}
            </div>
            {sizing === "tao" ? (
              // In τ mode the pot isn't an input — it's whatever the sleeves
              // add up to. An editable box here would just be a number that
              // silently disagrees with the rows under it.
              <div
                className="pixel-input w-full font-mono text-sm text-pixel-gray-light"
                title="The sum of the per-trader allocations below"
              >
                {totalAlloc.toFixed(2)}
              </div>
            ) : (
              <input
                type="number" min="1" step="1"
                value={capitalTao}
                onChange={(e) => setCapitalTao(Number(e.target.value) || 0)}
                className="pixel-input w-full font-mono text-sm"
              />
            )}
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
              {enabledCount > 0 && (
                <span className="text-pixel-gray-light normal-case tracking-normal font-mono">
                  {" "}· {totalAlloc.toFixed(2)}τ over {enabledCount}
                </span>
              )}
            </div>
            <div className="flex flex-wrap gap-1 justify-end">
              {/* How the money is decided. Both modes carry their numbers
                  across, so this is a view on one basket, not two. */}
              <div className="flex" role="group" aria-label="Sizing mode">
                {(["split", "tao"] as const).map((m) => (
                  <button
                    key={m}
                    type="button"
                    onClick={() => switchSizing(m)}
                    className={`pixel-btn text-[9px] px-1.5 py-0.5 ${
                      sizing === m
                        ? "border-green-400 text-green-400"
                        : "text-pixel-gray"
                    }`}
                    title={
                      m === "split"
                        ? "One pot, cut by relative weight"
                        : "Type the TAO behind each trader"
                    }
                  >
                    {m === "split" ? "SPLIT" : "PER-TRADER τ"}
                  </button>
                ))}
              </div>
              {traders.length > 1 && (
                <button
                  type="button"
                  className="pixel-btn text-[9px] px-1.5 py-0.5"
                  onClick={equalize}
                  title={
                    sizing === "tao"
                      ? "Split the current total evenly across every trader"
                      : "Give every trader the same share"
                  }
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
                  {sizing === "split" && <th className="num">Weight</th>}
                  <th className="num" title="The TAO behind this trader">
                    τ
                  </th>
                  {(!compact || sizing === "tao") && (
                    <th className="num">Share</th>
                  )}
                  <th className="!px-1" style={{ width: 40 }}></th>
                </tr>
              </thead>
              <tbody>
                {traders.slice(0, shown).map((t) => {
                  const alloc = allocOf(t);
                  const share = totalAlloc > 0 ? alloc / totalAlloc : 0;
                  const dust = alloc > 0 && alloc < MIN_SLEEVE_TAO;
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
                      {sizing === "split" && (
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
                      )}
                      <td className="num">
                        {sizing === "tao" ? (
                          <input
                            type="number" min="0" step="0.5"
                            value={t.alloc_tao ?? 0}
                            onChange={(e) =>
                              setAlloc(t.ss58, Number(e.target.value) || 0)
                            }
                            className={`pixel-input-sm w-20 text-right font-mono ${
                              dust ? "border-amber-400 text-amber-400" : ""
                            }`}
                            title={
                              dust
                                ? `Under the ${MIN_SLEEVE_TAO}τ minimum sleeve — this one would be skipped`
                                : "TAO behind this trader"
                            }
                          />
                        ) : (
                          <span
                            className={`font-mono ${
                              dust ? "text-amber-400" : ""
                            }`}
                          >
                            {fmtValue(alloc, currency, usdPerTao)}
                          </span>
                        )}
                      </td>
                      {(!compact || sizing === "tao") && (
                        <td className="num font-mono text-pixel-gray-light">
                          {(share * 100).toFixed(1)}%
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

          {dustLegs > 0 && (
            <p className="mt-2 text-[11px] font-mono text-amber-400">
              {dustLegs} of {enabledCount} sleeves are under {MIN_SLEEVE_TAO}τ
              and would be skipped as dust — {totalAlloc.toFixed(2)}τ doesn't
              stretch this far. Raise the capital or trim the basket.
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
