"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  fetchCopies,
  fetchTraders,
  fetchWatches,
  unwatchAccount,
  watchAccount,
  shortSs58,
} from "../lib/api";
import type { AccountWatch, CopyConfig, TrackedTrader } from "../lib/types";
import { useCurrency, fmtValue } from "../context/CurrencyContext";
import ChangeChip from "./ChangeChip";
import Identicon from "./Identicon";
import Sparkline from "./Sparkline";

/**
 * The docked watchlist.
 *
 * It used to be a column of truncated SS58s — twelve identical grey strings
 * telling you nothing about the accounts you deliberately chose to follow.
 * Every number here comes from bt's index in the same single /traders call
 * the board uses, so a row costs nothing extra: value, 24h move, the shape
 * of the last day, and whether we're already mirroring it.
 */
type Sort = "value" | "change" | "added";

const REFRESH_MS = 60_000;
// Pool discovery watches every coldkey it finds, so this list is hundreds
// of rows deep. Paint a screenful and let the filter or the button reach
// the rest — a 500-row drawer is 40k pixels of scroll nobody reads.
const PAGE = 40;

const looksLikeSs58 = (s: string) => s.startsWith("5") && s.length >= 40;

export default function WatchlistDrawer({ onClose }: { onClose: () => void }) {
  const { currency, usdPerTao } = useCurrency();
  const [accounts, setAccounts] = useState<AccountWatch[]>([]);
  const [index, setIndex] = useState<Map<string, TrackedTrader>>(new Map());
  const [copying, setCopying] = useState<Set<string>>(new Set());
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<Sort>("value");
  const [limit, setLimit] = useState(PAGE);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const load = (quiet = false) => {
    if (!quiet) setLoading(true);
    setErr("");
    fetchWatches()
      .then((r) => setAccounts(r.accounts || []))
      .catch((e) => setErr(e.message))
      .finally(() => setLoading(false));
    // Prices and copies are decoration — a slow or missing bt must never
    // stop the list of addresses from rendering.
    fetchTraders()
      .then((b) => setIndex(new Map((b.rows || []).map((t) => [t.ss58, t]))))
      .catch(() => {});
    fetchCopies()
      .then((cs: CopyConfig[]) =>
        setCopying(
          new Set(
            cs.filter((c) => c.status !== "stopped").map((c) => c.target_ss58),
          ),
        ),
      )
      .catch(() => {});
  };

  useEffect(() => {
    load();
    const t = setInterval(() => load(true), REFRESH_MS);
    return () => clearInterval(t);
  }, []);

  // Any change to what's on screen starts the page count over.
  useEffect(() => { setLimit(PAGE); }, [q, sort]);

  const remove = async (ss58: string) => {
    // Optimistic: the row goes now, and comes back if the call fails.
    const before = accounts;
    setAccounts((a) => a.filter((x) => x.ss58 !== ss58));
    try {
      await unwatchAccount(ss58);
    } catch (e: unknown) {
      setAccounts(before);
      setErr(String(e));
    }
  };

  const add = async () => {
    const ss58 = q.trim();
    if (!looksLikeSs58(ss58)) return;
    setBusy(true);
    setErr("");
    try {
      await watchAccount(ss58);
      setQ("");
      load(true);
    } catch (e: unknown) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  // One input does both jobs: it filters what you have, and if what you
  // typed is an address it offers to add it.
  const addable = looksLikeSs58(q.trim()) &&
    !accounts.some((a) => a.ss58 === q.trim());

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const joined = accounts.map((a) => ({ ...a, t: index.get(a.ss58) }));
    const filtered = addable || !needle
      ? joined
      : joined.filter(
          (r) =>
            r.ss58.toLowerCase().includes(needle) ||
            (r.label || "").toLowerCase().includes(needle),
        );
    const val = (r: typeof joined[number]) => r.t?.total_tao ?? -1;
    return filtered.sort((a, b) => {
      if (sort === "value") return val(b) - val(a);
      if (sort === "change")
        return (b.t?.change_24h ?? -Infinity) - (a.t?.change_24h ?? -Infinity);
      return (b.added_at || "").localeCompare(a.added_at || "");
    });
  }, [accounts, index, q, sort, addable]);

  // Portfolio-level readout. The aggregate move is value-weighted — the
  // average of twelve percentages would let a 3 τ account outvote a 200k
  // one, which is exactly backwards.
  const totals = useMemo(() => {
    let value = 0, prior = 0, up = 0, down = 0, priced = 0;
    for (const r of rows) {
      const t = r.t;
      if (!t) continue;
      value += t.total_tao;
      priced++;
      if (t.change_24h != null) {
        prior += t.total_tao / (1 + t.change_24h / 100);
        if (t.change_24h > 0) up++;
        else if (t.change_24h < 0) down++;
      } else {
        prior += t.total_tao;
      }
    }
    return {
      value,
      // Against the rows on screen, not every account — under a filter the
      // other 500 aren't "waiting to be indexed", they're just not shown.
      warming: rows.length - priced,
      priced,
      up,
      down,
      pct: prior > 0 ? ((value - prior) / prior) * 100 : null,
    };
  }, [rows]);

  return (
    <div className="pixel-panel p-3 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-[12px] tracking-[3px] uppercase text-pixel-gray-light font-mono">
          watchlist
          <span className="ml-2 text-pixel-gray">{accounts.length}</span>
        </h3>
        <div className="flex items-center gap-1">
          <button
            onClick={() => load()}
            className="pixel-btn text-[10px] px-2 py-0.5"
            title="Reload"
          >
            ↻
          </button>
          <button
            onClick={onClose}
            className="pixel-btn text-[10px] px-2 py-0.5"
            title="Close drawer"
          >
            ✕
          </button>
        </div>
      </div>

      <div className="flex items-center gap-1">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && add()}
          placeholder="filter, or paste an SS58 to add…"
          className="pixel-input-sm w-full font-mono !text-[12px] min-w-0"
          aria-label="filter or add watched account"
        />
        {addable && (
          <button
            onClick={add}
            disabled={busy}
            className="pixel-btn text-[10px] px-2 py-1 text-green-400 border-green-400/40 shrink-0"
          >
            {busy ? "…" : "+ADD"}
          </button>
        )}
      </div>

      {accounts.length > 0 && (
        <div className="flex items-baseline justify-between gap-2 border-y-2 border-pixel-border py-1.5">
          <div className="min-w-0">
            <p className="text-[9px] uppercase tracking-[2px] text-pixel-gray">
              {/* Under a filter this is the value of what you're looking
                  at, so say which set it's adding up. */}
              {rows.length === accounts.length
                ? "tracked value"
                : `value · ${rows.length} of ${accounts.length}`}
            </p>
            <p className="font-mono text-[13px] text-pixel-white tabular-nums truncate">
              {totals.priced
                ? fmtValue(totals.value, currency, usdPerTao)
                : "—"}
            </p>
          </div>
          <div className="text-right shrink-0">
            <ChangeChip pct={totals.pct} size="xs" label="24h, value-weighted" />
            <p className="text-[9px] text-pixel-gray font-mono mt-0.5">
              <span className="text-green-400">{totals.up}▲</span>{" "}
              <span className="text-red-400">{totals.down}▼</span>
              {totals.warming > 0 && (
                <span title={`${totals.warming} not indexed yet`}>
                  {" "}
                  {totals.warming}·
                </span>
              )}
            </p>
          </div>
        </div>
      )}

      {accounts.length > 1 && (
        <div className="flex items-center gap-1">
          {(["value", "change", "added"] as Sort[]).map((s) => (
            <button
              key={s}
              onClick={() => setSort(s)}
              aria-pressed={sort === s}
              className={`pixel-btn text-[9px] px-1.5 py-0.5 uppercase ${
                sort === s ? "nav-active" : "text-pixel-gray"
              }`}
            >
              {s === "change" ? "24h" : s}
            </button>
          ))}
        </div>
      )}

      {err && <p className="text-red-400 text-xs break-all">{err}</p>}
      {loading && accounts.length === 0 && (
        <p className="text-pixel-gray text-xs">loading…</p>
      )}
      {!loading && accounts.length === 0 && (
        <p className="text-pixel-gray text-xs leading-relaxed">
          Nothing watched yet. Paste a coldkey above, or open a trader from
          the{" "}
          <Link href="/leaderboard" className="text-green-400">
            leaderboard
          </Link>{" "}
          and hit + WATCH.
        </p>
      )}
      {!loading && accounts.length > 0 && rows.length === 0 && (
        <p className="text-pixel-gray text-xs">No match for “{q.trim()}”.</p>
      )}

      <ul className="space-y-1">
        {rows.slice(0, limit).map((r) => {
          const t = r.t;
          // Freshly indexed accounts have a 24h window that hasn't closed
          // yet — fall back to the 7d move rather than printing an em-dash
          // next to a live value.
          const win = t?.change_24h != null ? "24h" : "7d";
          const chg = t ? t.change_24h ?? t.change_7d : null;
          // Under ~6 snapshots the trace is a flat line, and a filled flat
          // line is a progress bar, not a chart. Show the dashes instead.
          const spark = t?.spark && t.spark.length >= 6 ? t.spark : null;
          return (
            <li
              key={r.ss58}
              className="group px-2 py-1.5 rounded hover:bg-pixel-white/5"
            >
              <div className="flex items-center gap-2">
                <Identicon ss58={r.ss58} size={22} />
                <Link
                  href={`/traders/${r.ss58}`}
                  className="min-w-0 flex-1 no-underline"
                  title={r.ss58}
                >
                  <span className="block text-[12px] text-pixel-white group-hover:text-green-400 truncate">
                    {r.label || shortSs58(r.ss58)}
                  </span>
                  <span className="block text-[9px] text-pixel-gray font-mono truncate">
                    {shortSs58(r.ss58)}
                    {t ? ` · ${t.subnets} SN` : " · warming"}
                  </span>
                </Link>
                <span className="text-right shrink-0">
                  <span className="block font-mono text-[12px] text-pixel-white tabular-nums">
                    {t ? fmtValue(t.total_tao, currency, usdPerTao) : "—"}
                  </span>
                  <span title={t ? `${win} change` : "not indexed yet"}>
                    <ChangeChip pct={chg} size="xs" bare />
                  </span>
                </span>
              </div>

              <div className="flex items-center gap-2 mt-1 pl-[30px]">
                <Sparkline
                  values={spark}
                  width={160}
                  height={16}
                  strokeWidth={2}
                  className="w-full min-w-0 text-pixel-gray"
                />
                {/* Actions stay out of the way until you're on the row —
                    except COPYING, which is state, not an action. */}
                {copying.has(r.ss58) ? (
                  <span
                    className="text-[9px] font-mono text-green-400 shrink-0"
                    title="a copy config is mirroring this account"
                  >
                    ●COPYING
                  </span>
                ) : (
                  <Link
                    href={`/strats?target=${r.ss58}`}
                    className="text-[9px] font-mono text-pixel-gray hover:text-green-400 shrink-0 opacity-0 group-hover:opacity-100 focus:opacity-100 no-underline"
                  >
                    COPY
                  </Link>
                )}
                <button
                  onClick={() => remove(r.ss58)}
                  title="Remove from watchlist"
                  className="text-[10px] text-pixel-gray hover:text-red-400 font-mono shrink-0 opacity-0 group-hover:opacity-100 focus:opacity-100"
                >
                  ✕
                </button>
              </div>
            </li>
          );
        })}
      </ul>

      {rows.length > limit && (
        <button
          onClick={() => setLimit((n) => n + PAGE)}
          className="pixel-btn text-[10px] w-full py-1 text-pixel-gray-light"
        >
          + {Math.min(PAGE, rows.length - limit)} MORE
          <span className="text-pixel-gray"> · {rows.length - limit} left</span>
        </button>
      )}
    </div>
  );
}
