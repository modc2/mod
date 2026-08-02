"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useThemeColors } from "../../context/ThemeContext";
import {
  fetchSubnetDetail,
  fetchSubnetHistory,
  fmtCompact,
  shortSs58,
} from "../../lib/api";
import type { PricePoint, SubnetDetail } from "../../lib/types";
import { useCurrency, fmtAlphaPrice } from "../../context/CurrencyContext";
import ChangeChip from "../../components/ChangeChip";
import SubnetLogo from "../../components/SubnetLogo";

const RANGES: { label: string; hours: number }[] = [
  { label: "24H", hours: 24 },
  { label: "7D", hours: 168 },
  { label: "30D", hours: 720 },
  { label: "ALL", hours: 8760 },
];

export default function SubnetDetailPage() {
  const skin = useThemeColors();
  const { netuid } = useParams<{ netuid: string }>();
  const { currency, usdPerTao } = useCurrency();
  const [d, setD] = useState<SubnetDetail | null>(null);
  const [series, setSeries] = useState<PricePoint[]>([]);
  const [hours, setHours] = useState(168);
  const [err, setErr] = useState("");

  useEffect(() => {
    let alive = true;
    const load = () =>
      fetchSubnetDetail(netuid)
        .then((x) => alive && setD(x))
        .catch((e) => alive && setErr(e.message));
    load();
    const id = setInterval(() => {
      if (document.visibilityState === "visible") load();
    }, 20_000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [netuid]);

  useEffect(() => {
    let alive = true;
    fetchSubnetHistory(netuid, hours)
      .then((s) => alive && setSeries(s))
      .catch(() => alive && setSeries([]));
    return () => {
      alive = false;
    };
  }, [netuid, hours]);

  const chart = useMemo(
    () =>
      series.map((p) => ({
        t: p.t * 1000,
        price: currency === "USD" && usdPerTao ? p.price * usdPerTao : p.price,
        mcap: p.mcap ?? 0,
      })),
    [series, currency, usdPerTao],
  );

  const windowPct = useMemo(() => {
    if (chart.length < 2) return null;
    const a = chart[0].price;
    const b = chart[chart.length - 1].price;
    return a > 0 ? ((b - a) / a) * 100 : null;
  }, [chart]);

  if (err) return <p className="text-red-400 font-mono">{err}</p>;
  if (!d) return <p className="text-pixel-gray font-mono">loading subnet…</p>;

  const s = d.subnet;
  const up = (windowPct ?? s.change_24h ?? 0) >= 0;
  const line = up ? skin.lime : skin.red;
  const money = (tao: number | null | undefined) => {
    if (tao == null) return "—";
    return currency === "USD" && usdPerTao
      ? `$${fmtCompact(tao * usdPerTao)}`
      : `${fmtCompact(tao)} τ`;
  };

  // Share is relative to the validators actually listed, not the whole
  // metagraph — the table only ever shows the top slice.
  const shownStake = d.validators.reduce((a, v) => a + v.stake, 0) || 1;

  const links = [
    s.url && { label: "website", href: s.url.startsWith("http") ? s.url : `https://${s.url}` },
    s.github && { label: "github", href: s.github },
    s.discord?.startsWith("http") && { label: "discord", href: s.discord },
    d.contact?.includes("@") && { label: d.contact, href: `mailto:${d.contact}` },
  ].filter(Boolean) as { label: string; href: string }[];

  return (
    <div className="space-y-5">
      <Link href="/subnets" className="text-pixel-gray hover:text-pixel-white text-sm no-underline">
        ← all subnets
      </Link>

      {/* hero */}
      <header className="pixel-panel p-5">
        <div className="flex flex-wrap items-start gap-4">
          <SubnetLogo netuid={s.netuid} name={s.name} symbol={s.symbol} logo={s.logo} size={56} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="font-display text-2xl font-bold text-pixel-white">{s.name}</h1>
              <span className="pixel-badge border-pixel-white/15 text-pixel-gray-light font-mono">
                SN{s.netuid}
              </span>
              {s.symbol && (
                <span className="pixel-badge border-pixel-white/15 text-pixel-gray-light font-mono">
                  {s.symbol}
                </span>
              )}
              <span className="pixel-badge border-green-400/30 text-green-400">live</span>
            </div>
            {s.description && (
              <p className="arcade-prose mt-1.5">{s.description}</p>
            )}
            {links.length > 0 && (
              <div className="flex flex-wrap gap-3 mt-2">
                {links.map((l) => (
                  <a
                    key={l.href}
                    href={l.href}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="text-[11px] font-mono text-green-400 hover:underline"
                  >
                    {l.label} ↗
                  </a>
                ))}
              </div>
            )}
          </div>

          <div className="text-right">
            <p className="font-mono text-3xl font-bold text-pixel-white tabular-nums leading-none">
              {fmtAlphaPrice(s.alpha_price_tao, currency, usdPerTao)}
            </p>
            <div className="flex items-center justify-end gap-2 mt-2">
              <span className="text-[10px] uppercase tracking-[1.5px] text-pixel-gray">1h</span>
              <ChangeChip pct={s.change_1h} size="xs" />
              <span className="text-[10px] uppercase tracking-[1.5px] text-pixel-gray">24h</span>
              <ChangeChip pct={s.change_24h} size="xs" />
            </div>
          </div>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4 mt-5 pt-4 border-t border-pixel-white/5">
          <Stat label="market cap" value={money(s.market_cap_tao)} />
          <Stat label="24h volume" value={money(s.vol_24h_tao)} />
          <Stat label="tao in pool" value={money(s.total_stake_tao)} />
          <Stat label="alpha in / out" value={`${fmtCompact(s.alpha_in)} / ${fmtCompact(s.alpha_out)}`} />
          <Stat label="neurons" value={d.neurons ? String(d.neurons) : "—"} />
          <Stat
            label="tempo"
            value={`${s.tempo}${d.blocks_since_last_step != null ? ` · +${d.blocks_since_last_step}` : ""}`}
            hint="blocks per epoch · blocks since last step"
          />
        </div>
      </header>

      {/* chart */}
      <section className="pixel-panel p-4">
        <div className="flex flex-wrap items-center gap-3 mb-3">
          <h2 className="font-display text-sm font-bold">Alpha price</h2>
          {windowPct != null && <ChangeChip pct={windowPct} size="sm" label="over selected range" />}
          <div className="flex gap-1 ml-auto">
            {RANGES.map((r) => (
              <button
                key={r.label}
                onClick={() => setHours(r.hours)}
                className={`pixel-btn text-[10px] px-2 py-1 ${
                  hours === r.hours ? "border-green-400 text-green-400" : "text-pixel-gray-light"
                }`}
              >
                {r.label}
              </button>
            ))}
          </div>
        </div>

        {chart.length < 2 ? (
          <p className="text-pixel-gray font-mono text-sm py-12 text-center">
            no indexed history for this window yet — the indexer fills in as it snapshots.
          </p>
        ) : (
          <div style={{ height: 280 }}>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chart} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
                <defs>
                  <linearGradient id="snfill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={line} stopOpacity={0.3} />
                    <stop offset="100%" stopColor={line} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis
                  dataKey="t"
                  type="number"
                  domain={["dataMin", "dataMax"]}
                  scale="time"
                  tickFormatter={(t) => {
                    const d = new Date(t);
                    // Inside a week, bare dates repeat across every tick —
                    // the hour is the part that actually distinguishes them.
                    if (hours <= 24)
                      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
                    if (hours <= 168)
                      return `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${String(d.getHours()).padStart(2, "0")}h`;
                    return d.toLocaleDateString([], { month: "short", day: "numeric" });
                  }}
                  minTickGap={72}
                  tickLine={false}
                  axisLine={false}
                />
                <YAxis
                  domain={["auto", "auto"]}
                  width={86}
                  tickFormatter={(v) => (currency === "USD" ? `$${v.toPrecision(3)}` : v.toPrecision(4))}
                  tickLine={false}
                  axisLine={false}
                />
                <Tooltip
                  contentStyle={{
                    background: skin.panel,
                    border: `2px solid ${skin.border}`,
                    borderRadius: 0,
                    fontSize: 12,
                  }}
                  labelFormatter={(t) => new Date(Number(t)).toLocaleString()}
                  formatter={(v) => {
                    const n = Number(v);
                    return [
                      currency === "USD" ? `$${n.toPrecision(5)}` : `${n.toPrecision(6)} τ`,
                      "α price",
                    ];
                  }}
                />
                <Area
                  // stepAfter everywhere: see Sparkline.tsx
                  type="stepAfter"
                  dataKey="price"
                  stroke={line}
                  strokeWidth={1.8}
                  fill="url(#snfill)"
                  dot={false}
                  isAnimationActive={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
      </section>

      {/* validators */}
      <section className="pixel-panel overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-pixel-white/5">
          <h2 className="font-display text-sm font-bold">Top validators</h2>
          <span className="text-[11px] text-pixel-gray font-mono">
            {d.validators.length ? `${d.validators.length} of ${d.neurons}` : ""}
          </span>
          {d.owner_coldkey && (
            <span className="text-[10px] text-pixel-gray font-mono ml-auto">
              owner {shortSs58(d.owner_coldkey)}
            </span>
          )}
        </div>
        {d.validators.length === 0 ? (
          <p className="text-pixel-gray font-mono text-sm p-4">
            validator rankings unavailable — the bt index isn’t reachable right now.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="pixel-table" style={{ minWidth: 720 }}>
              <thead className="sticky">
                <tr>
                  <th style={{ width: 74 }}>UID</th>
                  <th>Coldkey</th>
                  <th className="num">Stake (α)</th>
                  <th className="num" title="share of the stake held by the validators listed here">
                    Share
                  </th>
                  <th className="num">Trust</th>
                  <th className="num">Dividends</th>
                  <th style={{ width: 80 }}></th>
                </tr>
              </thead>
              <tbody>
                {d.validators.map((v) => {
                  const share = (v.stake / shownStake) * 100;
                  return (
                    <tr key={v.uid}>
                      <td className="num text-pixel-gray">{v.uid}</td>
                      <td>
                        <Link
                          href={`/traders/${v.coldkey}`}
                          className="font-mono text-pixel-white hover:text-green-400 no-underline"
                          title={v.coldkey}
                        >
                          {shortSs58(v.coldkey)}
                        </Link>
                      </td>
                      <td className="num font-mono">{fmtCompact(v.stake)}</td>
                      <td className="num">
                        <div className="flex items-center gap-2 justify-end">
                          <span className="font-mono text-[11px] text-pixel-gray-light">
                            {share.toFixed(1)}%
                          </span>
                          <div className="pixel-bar w-14 !h-1.5">
                            <div
                              className="pixel-bar-fill bg-green-400/60"
                              style={{ width: `${Math.min(100, share)}%` }}
                            />
                          </div>
                        </div>
                      </td>
                      <td className="num font-mono text-pixel-gray-light">
                        {(v.validator_trust * 100).toFixed(1)}%
                      </td>
                      <td className="num font-mono text-pixel-gray-light">
                        {(v.dividends * 100).toFixed(2)}%
                      </td>
                      <td>
                        <Link
                          href={`/strats?target=${v.coldkey}`}
                          className="pixel-btn text-[10px] px-2 py-0.5 text-green-400 border-green-400/40 no-underline"
                        >
                          COPY
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div title={hint}>
      <p className="text-[10px] tracking-[2px] uppercase text-pixel-gray">{label}</p>
      <p className="font-mono text-[15px] text-pixel-white tabular-nums">{value}</p>
    </div>
  );
}
