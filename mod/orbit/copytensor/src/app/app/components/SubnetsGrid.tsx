"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { fetchSubnets, fmtCompact } from "../lib/api";
import type { SubnetInfo } from "../lib/types";
import { useFilters } from "../context/FiltersContext";
import { useCurrency, fmtAlphaPrice } from "../context/CurrencyContext";
import ChangeChip from "./ChangeChip";
import Sparkline from "./Sparkline";
import SubnetLogo from "./SubnetLogo";

const POLL_MS = 20_000;
const VIEW_KEY = "copytensor:subnets:view";
const SORT_KEY = "copytensor:subnets:sort";

type SortId = "market_cap" | "price" | "change_24h" | "change_1h" | "vol_24h" | "stake" | "netuid";

const SORTS: { id: SortId; label: string; pick: (s: SubnetInfo) => number | null | undefined }[] = [
  { id: "market_cap", label: "Market cap", pick: (s) => s.market_cap_tao },
  { id: "vol_24h", label: "24h volume", pick: (s) => s.vol_24h_tao },
  { id: "change_24h", label: "24h change", pick: (s) => s.change_24h },
  { id: "change_1h", label: "1h change", pick: (s) => s.change_1h },
  { id: "price", label: "Alpha price", pick: (s) => s.alpha_price_tao },
  { id: "stake", label: "TAO in pool", pick: (s) => s.total_stake_tao },
  { id: "netuid", label: "Netuid", pick: (s) => s.netuid },
];

export default function SubnetsGrid() {
  const { search } = useFilters();
  const { currency, usdPerTao } = useCurrency();
  const [subnets, setSubnets] = useState<SubnetInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [view, setView] = useState<"cards" | "table">("cards");
  const [sortId, setSortId] = useState<SortId>("market_cap");
  const [asc, setAsc] = useState(false);

  // Restore the last chosen layout/sort — this page is a browsing surface
  // people come back to.
  useEffect(() => {
    try {
      const v = localStorage.getItem(VIEW_KEY);
      if (v === "cards" || v === "table") setView(v);
      const s = localStorage.getItem(SORT_KEY);
      if (s && SORTS.some((x) => x.id === s)) setSortId(s as SortId);
    } catch {}
  }, []);

  useEffect(() => {
    let alive = true;
    const load = () =>
      fetchSubnets()
        .then((d) => {
          if (!alive) return;
          setSubnets(d);
          setErr("");
        })
        .catch((e) => alive && setErr(e.message))
        .finally(() => alive && setLoading(false));
    load();
    const id = setInterval(() => {
      if (document.visibilityState === "visible") load();
    }, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const pick = SORTS.find((s) => s.id === sortId)!.pick;

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const r = q
      ? subnets.filter(
          (s) =>
            s.name.toLowerCase().includes(q) ||
            String(s.netuid) === q ||
            (s.description || "").toLowerCase().includes(q),
        )
      : subnets;
    const dir = asc ? 1 : -1;
    return [...r].sort((a, b) => {
      const av = pick(a);
      const bv = pick(b);
      // Subnets the index has no value for sink to the bottom either way.
      if (av == null && bv == null) return a.netuid - b.netuid;
      if (av == null) return 1;
      if (bv == null) return -1;
      return (av - bv) * dir;
    });
  }, [subnets, search, sortId, asc, pick]);

  const choose = (id: SortId) => {
    setSortId(id);
    try { localStorage.setItem(SORT_KEY, id); } catch {}
  };
  const chooseView = (v: "cards" | "table") => {
    setView(v);
    try { localStorage.setItem(VIEW_KEY, v); } catch {}
  };

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] tracking-[2px] uppercase text-pixel-gray mr-1">
          {filtered.length} of {subnets.length}
        </span>

        <div className="flex flex-wrap gap-1">
          {SORTS.map((s) => (
            <button
              key={s.id}
              onClick={() => (sortId === s.id ? setAsc((v) => !v) : choose(s.id))}
              className={`pixel-btn text-[10px] px-2 py-1 ${
                sortId === s.id ? "border-green-400 text-green-400" : "text-pixel-gray-light"
              }`}
              title={sortId === s.id ? "Click again to flip direction" : `Sort by ${s.label}`}
            >
              {s.label}
              {sortId === s.id && <span className="ml-1">{asc ? "▲" : "▼"}</span>}
            </button>
          ))}
        </div>

        <div className="flex gap-1 ml-auto">
          {(["cards", "table"] as const).map((v) => (
            <button
              key={v}
              onClick={() => chooseView(v)}
              className={`pixel-btn text-[10px] px-2 py-1 ${
                view === v ? "border-green-400 text-green-400" : "text-pixel-gray-light"
              }`}
              aria-pressed={view === v}
            >
              {v === "cards" ? "▦ CARDS" : "☰ TABLE"}
            </button>
          ))}
        </div>
      </div>

      {err && (
        <div className="pixel-panel-red px-3 py-2 text-[12px] text-red-400 font-mono">{err}</div>
      )}

      {loading && subnets.length === 0 ? (
        <SkeletonGrid />
      ) : view === "cards" ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4 gap-3">
          {filtered.map((s) => (
            <SubnetCard key={s.netuid} s={s} currency={currency} usdPerTao={usdPerTao} />
          ))}
        </div>
      ) : (
        <SubnetTable rows={filtered} currency={currency} usdPerTao={usdPerTao} />
      )}

      {!loading && filtered.length === 0 && (
        <p className="text-pixel-gray text-sm font-mono">no subnet matches “{search}”.</p>
      )}
    </section>
  );
}

// ── card view ─────────────────────────────────────────────────────

function SubnetCard({
  s,
  currency,
  usdPerTao,
}: {
  s: SubnetInfo;
  currency: "TAO" | "USD";
  usdPerTao: number | null;
}) {
  const up = (s.change_24h ?? 0) >= 0;
  const money = (tao: number | null | undefined) => {
    if (tao == null) return "—";
    return currency === "USD" && usdPerTao
      ? `$${fmtCompact(tao * usdPerTao)}`
      : `${fmtCompact(tao)} τ`;
  };

  return (
    <Link href={`/subnets/${s.netuid}`} className="market-card group no-underline block !p-4">
      <div className="flex items-center gap-2.5">
        <SubnetLogo netuid={s.netuid} name={s.name} symbol={s.symbol} logo={s.logo} size={34} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <h3 className="font-display text-[15px] font-bold text-pixel-white truncate">
              {s.name}
            </h3>
            {s.symbol && (
              <span className="text-[11px] text-pixel-gray font-mono">{s.symbol}</span>
            )}
          </div>
          <span className="text-[10px] tracking-[1.5px] uppercase text-pixel-gray font-mono">
            SN{s.netuid}
          </span>
        </div>
        <ChangeChip pct={s.change_24h} size="sm" label="24h" />
      </div>

      <div className="flex items-end justify-between gap-3 mt-3">
        <div className="min-w-0">
          <p className="font-mono text-[19px] font-bold text-pixel-white tabular-nums leading-none">
            {fmtAlphaPrice(s.alpha_price_tao, currency, usdPerTao)}
          </p>
          <p className="text-[10px] text-pixel-gray mt-1 font-mono">
            per α · 1h <ChangeChip pct={s.change_1h} size="xs" bare arrow={false} />
          </p>
        </div>
        <Sparkline values={s.spark} width={112} height={36} color={up ? undefined : "#f87171"} />
      </div>

      <div className="grid grid-cols-3 gap-2 mt-3 pt-3 border-t border-pixel-white/5">
        <Cell label="mcap" value={money(s.market_cap_tao)} />
        <Cell label="24h vol" value={money(s.vol_24h_tao)} />
        <Cell label="in pool" value={money(s.total_stake_tao)} />
      </div>

      {s.description && (
        <p className="text-[11px] text-pixel-gray mt-2.5 line-clamp-2 leading-snug">
          {s.description}
        </p>
      )}
    </Link>
  );
}

function Cell({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <p className="text-[9px] tracking-[1.5px] uppercase text-pixel-gray">{label}</p>
      <p className="font-mono text-[12px] text-pixel-white tabular-nums truncate">{value}</p>
    </div>
  );
}

// ── table view ────────────────────────────────────────────────────

function SubnetTable({
  rows,
  currency,
  usdPerTao,
}: {
  rows: SubnetInfo[];
  currency: "TAO" | "USD";
  usdPerTao: number | null;
}) {
  const money = (tao: number | null | undefined) => {
    if (tao == null) return "—";
    return currency === "USD" && usdPerTao
      ? `$${fmtCompact(tao * usdPerTao)}`
      : `${fmtCompact(tao)}`;
  };

  return (
    <div className="pixel-panel overflow-x-auto">
      <table className="pixel-table" style={{ minWidth: 860 }}>
        <thead className="sticky">
          <tr>
            <th style={{ width: 40 }}>#</th>
            <th>Subnet</th>
            <th className="num">Price</th>
            <th className="num">1h</th>
            <th className="num">24h</th>
            <th className="num">Market cap</th>
            <th className="num">24h vol</th>
            <th className="num">In pool</th>
            <th style={{ width: 110 }}>7d trend</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s, i) => (
            <tr key={s.netuid}>
              <td className="num text-pixel-gray">{i + 1}</td>
              <td>
                <Link
                  href={`/subnets/${s.netuid}`}
                  className="flex items-center gap-2 no-underline text-pixel-white hover:text-green-400"
                >
                  <SubnetLogo
                    netuid={s.netuid}
                    name={s.name}
                    symbol={s.symbol}
                    logo={s.logo}
                    size={20}
                  />
                  <span className="truncate">{s.name}</span>
                  <span className="text-[10px] text-pixel-gray font-mono">SN{s.netuid}</span>
                </Link>
              </td>
              <td className="num font-mono">
                {fmtAlphaPrice(s.alpha_price_tao, currency, usdPerTao)}
              </td>
              <td className="num">
                <ChangeChip pct={s.change_1h} size="xs" bare arrow={false} />
              </td>
              <td className="num">
                <ChangeChip pct={s.change_24h} size="xs" bare />
              </td>
              <td className="num font-mono">{money(s.market_cap_tao)}</td>
              <td className="num font-mono">{money(s.vol_24h_tao)}</td>
              <td className="num font-mono">{money(s.total_stake_tao)}</td>
              <td>
                <Sparkline values={s.spark} width={96} height={22} fill={false} strokeWidth={1.2} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SkeletonGrid() {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4 gap-3">
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} className="pixel-panel h-[168px] skeleton-pulse" />
      ))}
    </div>
  );
}
