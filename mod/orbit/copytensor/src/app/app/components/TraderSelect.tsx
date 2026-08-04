"use client";

import { useEffect, useMemo, useState } from "react";
import { fetchLeaderboard, fetchTraders, fetchWatches, shortSs58 } from "../lib/api";
import { useCurrency, fmtValue } from "../context/CurrencyContext";
import ChangeChip from "./ChangeChip";
import Identicon from "./Identicon";

/** One row of the union of everything we know how to copy. */
export type Candidate = {
  ss58: string;
  label: string | null;
  /** Portfolio value in τ — leaderboard stake, else the index's total. */
  value: number | null;
  /** Windowed move in %, whichever window we have for this row. */
  pnlPct: number | null;
  onBoard: boolean;
  watched: boolean;
  indexed: boolean;
};

type Source = "all" | "board" | "watched" | "indexed";
type Sort = "value" | "pnl";

const PAGE = 60;
const SOURCES: { id: Source; label: string }[] = [
  { id: "all", label: "ALL" },
  { id: "board", label: "BOARD" },
  { id: "watched", label: "WATCHED" },
  { id: "indexed", label: "INDEXED" },
];

const looksLikeSs58 = (s: string) => s.startsWith("5") && s.length >= 40;

/** Pull every ss58 out of a pasted blob — commas, spaces or newlines. */
const parseSs58s = (blob: string) =>
  blob.split(/[\s,;]+/).map((s) => s.trim()).filter(looksLikeSs58);

type Props = {
  /** ss58s already in the basket — shown checked and counted, never re-added. */
  chosen: Set<string>;
  onAdd: (rows: Candidate[]) => void;
  /** Drop the value/PnL columns when the drawer is narrow. */
  compact?: boolean;
};

/**
 * The trader selector.
 *
 * The basket used to be fillable only from the top-100 leaderboard, one row
 * at a time — so "copy these fifteen" meant fifteen clicks and anyone off the
 * board was unreachable. This unions the board, the watchlist and every
 * coldkey bt indexes into one searchable list, ticks any number of them at
 * once, and takes pasted addresses in bulk for the rest of the chain.
 */
export default function TraderSelect({ chosen, onAdd, compact }: Props) {
  const { currency, usdPerTao } = useCurrency();
  const [rows, setRows] = useState<Candidate[]>([]);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [source, setSource] = useState<Source>("all");
  const [sort, setSort] = useState<Sort>("value");
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const [limit, setLimit] = useState(PAGE);
  const [paste, setPaste] = useState("");

  useEffect(() => {
    let alive = true;
    // Three independent reads — a cold watchlist or a warming index must not
    // hide the leaderboard, so each one merges in on its own.
    const merged = new Map<string, Candidate>();
    const blank = (ss58: string): Candidate => ({
      ss58, label: null, value: null, pnlPct: null,
      onBoard: false, watched: false, indexed: false,
    });
    const flush = () => { if (alive) setRows(Array.from(merged.values())); };

    // Each source paints the moment it lands — the leaderboard walks a
    // 1000-coldkey pool and a picker that stays blank until the slowest of
    // three calls returns reads as broken.
    Promise.allSettled([
      fetchLeaderboard(7, 200).then((board) => {
        for (const e of board) {
          const c = merged.get(e.ss58) || blank(e.ss58);
          c.label = c.label || e.label;
          c.value = e.total_stake_tao;
          c.pnlPct = e.pnl_pct;
          c.onBoard = true;
          merged.set(e.ss58, c);
        }
        flush();
      }),
      fetchTraders().then((b) => {
        for (const t of b.rows || []) {
          const c = merged.get(t.ss58) || blank(t.ss58);
          c.label = c.label || t.label;
          if (c.value == null) c.value = t.total_tao;
          if (c.pnlPct == null) c.pnlPct = t.change_7d ?? t.change_24h;
          c.indexed = true;
          merged.set(t.ss58, c);
        }
        flush();
      }),
      fetchWatches().then((w) => {
        for (const a of w.accounts || []) {
          const c = merged.get(a.ss58) || blank(a.ss58);
          c.label = c.label || a.label;
          c.watched = true;
          merged.set(a.ss58, c);
        }
        flush();
      }),
    ]).then(() => {
      flush();
      if (alive) setLoading(false);
    });

    return () => { alive = false; };
  }, []);

  // Any change to what's on screen starts the page count over.
  useEffect(() => { setLimit(PAGE); }, [q, source, sort]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const out = rows.filter((r) => {
      if (source === "board" && !r.onBoard) return false;
      if (source === "watched" && !r.watched) return false;
      if (source === "indexed" && !r.indexed) return false;
      if (!needle) return true;
      return (
        r.ss58.toLowerCase().includes(needle) ||
        (r.label || "").toLowerCase().includes(needle)
      );
    });
    return out.sort((a, b) =>
      sort === "pnl"
        ? (b.pnlPct ?? -Infinity) - (a.pnlPct ?? -Infinity)
        : (b.value ?? -1) - (a.value ?? -1),
    );
  }, [rows, q, source, sort]);

  // Only rows you could actually add — ticking something already in the
  // basket would make "ADD 12" add nine.
  const addable = useMemo(
    () => filtered.filter((r) => !chosen.has(r.ss58)),
    [filtered, chosen],
  );
  const tickedRows = useMemo(
    () => addable.filter((r) => ticked.has(r.ss58)),
    [addable, ticked],
  );

  const toggle = (ss58: string) =>
    setTicked((cur) => {
      const next = new Set(cur);
      next.has(ss58) ? next.delete(ss58) : next.add(ss58);
      return next;
    });

  const tickMany = (rs: Candidate[]) =>
    setTicked((cur) => {
      const next = new Set(cur);
      for (const r of rs) next.add(r.ss58);
      return next;
    });

  const commit = (rs: Candidate[]) => {
    if (!rs.length) return;
    onAdd(rs);
    setTicked((cur) => {
      const next = new Set(cur);
      for (const r of rs) next.delete(r.ss58);
      return next;
    });
  };

  const pasted = parseSs58s(paste).filter((s) => !chosen.has(s));

  return (
    <div className="pixel-panel p-2 space-y-2">
      <div className="flex items-center gap-1">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="search label or ss58…"
          className="pixel-input-sm w-full font-mono !text-[12px] min-w-0"
          aria-label="search traders"
        />
        <span className="text-[9px] font-mono text-pixel-gray shrink-0 tabular-nums">
          {loading ? "…" : `${filtered.length}`}
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-1">
        {SOURCES.map((s) => (
          <button
            key={s.id}
            type="button"
            onClick={() => setSource(s.id)}
            aria-pressed={source === s.id}
            className={`pixel-btn text-[9px] px-1.5 py-0.5 ${
              source === s.id ? "nav-active" : "text-pixel-gray"
            }`}
          >
            {s.label}
          </button>
        ))}
        <span className="mx-1 text-pixel-border">|</span>
        {(["value", "pnl"] as Sort[]).map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => setSort(s)}
            aria-pressed={sort === s}
            className={`pixel-btn text-[9px] px-1.5 py-0.5 uppercase ${
              sort === s ? "nav-active" : "text-pixel-gray"
            }`}
          >
            {s === "pnl" ? "7d" : s}
          </button>
        ))}
      </div>

      {/* Bulk picks. "ALL" means everything the filter is showing, which is
          how you copy a whole slice of the board in one go. */}
      <div className="flex flex-wrap items-center gap-1">
        <button
          type="button"
          className="pixel-btn text-[9px] px-1.5 py-0.5"
          onClick={() => tickMany(addable)}
          disabled={!addable.length}
        >
          ALL {addable.length ? `(${addable.length})` : ""}
        </button>
        <button
          type="button"
          className="pixel-btn text-[9px] px-1.5 py-0.5"
          onClick={() => tickMany(addable.slice(0, 10))}
          disabled={!addable.length}
        >
          TOP 10
        </button>
        <button
          type="button"
          className="pixel-btn text-[9px] px-1.5 py-0.5 text-pixel-gray"
          onClick={() => setTicked(new Set())}
          disabled={!ticked.size}
        >
          NONE
        </button>
        <button
          type="button"
          className="pixel-btn text-[10px] px-2 py-0.5 ml-auto border-green-400 text-green-400 disabled:border-pixel-border disabled:text-pixel-gray"
          onClick={() => commit(tickedRows)}
          disabled={!tickedRows.length}
        >
          + ADD {tickedRows.length || ""}
        </button>
      </div>

      {loading && rows.length === 0 && (
        <p className="text-pixel-gray text-xs">loading traders…</p>
      )}
      {!loading && filtered.length === 0 && (
        <p className="text-pixel-gray text-xs">
          {q.trim() ? `No match for “${q.trim()}”.` : "Nothing to pick yet."}
        </p>
      )}

      <ul className="space-y-0.5 max-h-[42vh] overflow-y-auto">
        {filtered.slice(0, limit).map((r) => {
          const already = chosen.has(r.ss58);
          const on = already || ticked.has(r.ss58);
          return (
            <li key={r.ss58}>
              <label
                className={`flex items-center gap-2 px-1.5 py-1 rounded cursor-pointer hover:bg-pixel-white/5 ${
                  already ? "opacity-50" : ""
                }`}
                title={r.ss58}
              >
                <input
                  type="checkbox"
                  checked={on}
                  disabled={already}
                  onChange={() => toggle(r.ss58)}
                  className="shrink-0"
                />
                <Identicon ss58={r.ss58} size={18} />
                <span className="min-w-0 flex-1">
                  <span className="block text-[11px] text-pixel-white truncate">
                    {r.label || shortSs58(r.ss58)}
                  </span>
                  <span className="block text-[9px] text-pixel-gray font-mono truncate">
                    {already ? "in basket" : shortSs58(r.ss58)}
                  </span>
                </span>
                {!compact && (
                  <span className="text-right shrink-0">
                    <span className="block font-mono text-[11px] text-pixel-white tabular-nums">
                      {r.value != null ? fmtValue(r.value, currency, usdPerTao) : "—"}
                    </span>
                    <ChangeChip pct={r.pnlPct} size="xs" bare />
                  </span>
                )}
              </label>
            </li>
          );
        })}
      </ul>

      {filtered.length > limit && (
        <button
          type="button"
          onClick={() => setLimit((n) => n + PAGE)}
          className="pixel-btn text-[10px] w-full py-1 text-pixel-gray-light"
        >
          + {Math.min(PAGE, filtered.length - limit)} MORE
          <span className="text-pixel-gray"> · {filtered.length - limit} left</span>
        </button>
      )}

      {/* Anything off the board or the index still has an address. */}
      <form
        className="flex gap-1 border-t-2 border-pixel-border pt-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (!pasted.length) return;
          commit(pasted.map((ss58) => {
            const known = rows.find((r) => r.ss58 === ss58);
            return known || {
              ss58, label: null, value: null, pnlPct: null,
              onBoard: false, watched: false, indexed: false,
            };
          }));
          setPaste("");
        }}
      >
        <input
          value={paste}
          onChange={(e) => setPaste(e.target.value)}
          placeholder="paste ss58s (comma or space separated)…"
          className="pixel-input-sm flex-1 font-mono !text-[11px] min-w-0"
          aria-label="paste addresses to add"
        />
        <button
          type="submit"
          className="pixel-btn text-[10px] px-2 py-1 shrink-0"
          disabled={!pasted.length}
        >
          + ADD {pasted.length || ""}
        </button>
      </form>
    </div>
  );
}
