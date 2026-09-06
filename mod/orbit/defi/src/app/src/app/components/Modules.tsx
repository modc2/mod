"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as api from "../lib/api";
import type { Prefill } from "./Hub";

type Props = {
  say: (text: string, bad?: boolean) => void;
  address: string | null;
  /// The HUB's EXPLORE hands over a prefilled filter, and optionally the exact
  /// module to open once the rows land.
  prefill?: Prefill | null;
  onOpenTreasury: () => void;
  onOpenBook: () => void;
};

export const money = (value: number | null | undefined) => {
  if (value === null || value === undefined || !isFinite(value)) return "—";
  if (value >= 1e9) return `$${(value / 1e9).toFixed(2)}b`;
  if (value >= 1e6) return `$${(value / 1e6).toFixed(1)}m`;
  if (value >= 1e3) return `$${(value / 1e3).toFixed(0)}k`;
  return `$${value.toFixed(0)}`;
};

export const pct = (value: number | null | undefined, digits = 2) =>
  value === null || value === undefined || !isFinite(Number(value)) ? "—" : `${Number(value).toFixed(digits)}%`;

const CHAINS = [
  { id: "", label: "ALL" },
  { id: "ethereum", label: "ETHEREUM" },
  { id: "base", label: "BASE" },
  { id: "solana", label: "SOLANA" },
  { id: "tao", label: "BITTENSOR", preview: true },
];

const EXIT_WORD: Record<string, string> = {
  instant: "instant",
  market: "at market",
  queue: "queue",
  queue_or_market: "queue · or market",
  cooldown: "cooldown",
  cooldown_or_market: "cooldown · or market",
  request: "on request",
  redemption: "redemption",
  epoch: "end of epoch",
  locked: "locked",
  locked_until_maturity: "until maturity",
};

function exitLabel(liq: any): string {
  if (!liq) return "—";
  const base = EXIT_WORD[liq.exit] ?? liq.exit ?? "—";
  if (liq.lock_days > 0) return `locked ${liq.lock_days}d`;
  if (liq.exit_delay_days > 0 && liq.exit !== "instant") return `${base} · ≤${liq.exit_delay_days}d`;
  return base;
}

/// A single-series sparkline — the module's rate over the last year. One
/// series, so the title names it and there is no legend; hover reads a point.
function Spark({ points }: { points: { t: any; apy: number | null }[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const clean = points.filter((p) => p.apy !== null && isFinite(Number(p.apy)));
  if (clean.length < 2) return null;
  const w = 360;
  const h = 54;
  const values = clean.map((p) => Number(p.apy));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const x = (i: number) => (i / (clean.length - 1)) * (w - 2) + 1;
  const y = (v: number) => h - 4 - ((v - min) / span) * (h - 10);
  const d = values.map((v, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const at = hover ?? clean.length - 1;
  const when = clean[at]?.t ? new Date(Number(clean[at].t) * (String(clean[at].t).length > 12 ? 1 : 1000)) : null;
  return (
    <div style={{ marginTop: 8 }}>
      <div className="label" style={{ display: "flex", justifyContent: "space-between" }}>
        <span>APY, last {clean.length} days</span>
        <span style={{ color: "var(--muted)", textTransform: "none", letterSpacing: 0 }}>
          {pct(values[at])}
          {when && !isNaN(when.getTime()) ? ` · ${when.toISOString().slice(0, 10)}` : ""}
        </span>
      </div>
      <svg
        viewBox={`0 0 ${w} ${h}`}
        width="100%"
        height={h}
        style={{ display: "block", marginTop: 4 }}
        onMouseMove={(e) => {
          const box = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const rel = (e.clientX - box.left) / box.width;
          setHover(Math.max(0, Math.min(clean.length - 1, Math.round(rel * (clean.length - 1)))));
        }}
        onMouseLeave={() => setHover(null)}
      >
        <line x1="0" x2={w} y1={y(values[values.length - 1])} y2={y(values[values.length - 1])} stroke="var(--line)" strokeDasharray="3 4" />
        <path d={d} fill="none" stroke="var(--accent)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
        {hover !== null && (
          <>
            <line x1={x(at)} x2={x(at)} y1="0" y2={h} stroke="var(--line)" />
            <circle cx={x(at)} cy={y(values[at])} r="4" fill="var(--accent)" stroke="var(--panel)" strokeWidth="2" />
          </>
        )}
      </svg>
    </div>
  );
}

/// The home of the console: every place money can go, as a module with its
/// own return, its own liquidity and its own conditions — and a way in.
export default function Modules({ say, address, prefill, onOpenTreasury, onOpenBook }: Props) {
  const [rows, setRows] = useState<any[]>([]);
  const [meta, setMeta] = useState<any>(null);
  const [facets, setFacets] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [chain, setChain] = useState(prefill?.chain ?? "");
  const [kind, setKind] = useState("");
  const [q, setQ] = useState(prefill?.q ?? "");
  const [minTvl, setMinTvl] = useState("1000000");
  const [sort, setSort] = useState("score");
  const [addable, setAddable] = useState(false);
  const [instant, setInstant] = useState(false);
  const [stable, setStable] = useState(prefill?.stable ?? false);
  const [organic, setOrganic] = useState(false);

  const [picked, setPicked] = useState<any>(null);
  const [detail, setDetail] = useState<any>(null);

  const [amount, setAmount] = useState("100");
  const [account, setAccount] = useState("");
  const [auth, setAuth] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [quote, setQuote] = useState<any>(null);
  const [outcome, setOutcome] = useState<any>(null);
  const [busy, setBusy] = useState<"" | "quote" | "enter">("");

  const params = useMemo(
    () => ({
      chain: chain || undefined,
      kind: kind || undefined,
      q: q.trim() || undefined,
      min_tvl: chain === "tao" ? "0" : minTvl,
      sort,
      addable: addable || undefined,
      instant: instant || undefined,
      stable: stable || undefined,
      organic: organic || undefined,
      limit: 120,
    }),
    [chain, kind, q, minTvl, sort, addable, instant, stable, organic]
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const body = await api.getModules(params);
      setRows(body.modules ?? []);
      setMeta(body);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [params]);

  useEffect(() => {
    const timer = setTimeout(load, 200);
    return () => clearTimeout(timer);
  }, [load]);

  useEffect(() => {
    api.getModuleFacets().then(setFacets).catch(() => {});
  }, []);

  // The HUB said which module to open — pick it once, as soon as it lands.
  const pickedFromHub = useRef(false);
  useEffect(() => {
    if (!prefill?.pick || pickedFromHub.current) return;
    const hit = rows.find((m) => m.id === prefill.pick);
    if (hit) {
      pickedFromHub.current = true;
      setPicked(hit);
    }
  }, [rows, prefill]);

  // Open a module: full row plus its history.
  useEffect(() => {
    if (!picked) return setDetail(null);
    setDetail(picked);
    setQuote(null);
    setOutcome(null);
    setConfirm(false);
    api.getModule(picked.id, true).then(setDetail).catch(() => {});
  }, [picked]);

  const chainModule = detail?.adapter?.module ?? "eth";
  const accountHint =
    chainModule === "solana" ? "solana keystore wallet" : chainModule === "bt" ? "bittensor coldkey" : "eth account name";

  const runQuote = async () => {
    if (!detail) return;
    setBusy("quote");
    setOutcome(null);
    try {
      const body: any = { amount: amount.trim() };
      if (auth.trim()) body.auth = auth.trim();
      setQuote(await api.quoteModule(detail.id, body));
    } catch (e: any) {
      say(e.message, true);
    } finally {
      setBusy("");
    }
  };

  const enter = async () => {
    if (!detail) return;
    if (detail.adapter?.kind === "treasury_lock") return onOpenTreasury();
    setBusy("enter");
    try {
      const body: any = { module: detail.id, amount: amount.trim(), confirm };
      if (account.trim()) body.account = account.trim();
      if (auth.trim()) body.auth = auth.trim();
      const out = await api.enterModule(body);
      setOutcome(out);
      if (out?.quote) setQuote(out.quote);
      say(
        out?.entered
          ? `in — ${amount} ${detail.adapter?.asset?.symbol ?? ""} → ${detail.project}`
          : out?.needs_confirm
            ? "priced — tick confirm to send it"
            : out?.swap?.reason ?? out?.reason ?? "not sent",
        !out?.entered && !out?.needs_confirm
      );
    } catch (e: any) {
      setOutcome({ error: e.message });
      say(e.message, true);
    } finally {
      setBusy("");
    }
  };

  const sources = meta?.sources ?? {};
  const age = sources?.defillama?.age_seconds;

  return (
    <div className="fin">
      {/* ── filter band ─────────────────────────────────────────────── */}
      <div className="band">
        <div className="chips">
          {CHAINS.map((c) => (
            <button
              key={c.id}
              className={`chip ${chain === c.id ? "active" : ""}`}
              onClick={() => setChain(c.id)}
              title={c.preview ? "Bittensor subnets — no APY quoted, more coming" : ""}
            >
              {c.label}
              {facets?.chains && c.id && (
                <span className="chip-n">{facets.chains.find((f: any) => f.id === c.id)?.modules ?? ""}</span>
              )}
              {c.preview && <span className="chip-tag">preview</span>}
            </button>
          ))}
        </div>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="search — aave, jito, USDC, lending, wstETH…"
          style={{ width: 260 }}
        />
        <select value={kind} onChange={(e) => setKind(e.target.value)} style={{ width: 170 }}>
          <option value="">every kind</option>
          {(facets?.kinds ?? []).map((k: any) => (
            <option key={k.kind} value={k.kind}>
              {k.kind} ({k.modules})
            </option>
          ))}
        </select>
        <select value={minTvl} onChange={(e) => setMinTvl(e.target.value)} style={{ width: 124 }} disabled={chain === "tao"}>
          <option value="100000">TVL ≥ $100k</option>
          <option value="1000000">TVL ≥ $1m</option>
          <option value="10000000">TVL ≥ $10m</option>
          <option value="100000000">TVL ≥ $100m</option>
        </select>
        <select value={sort} onChange={(e) => setSort(e.target.value)} style={{ width: 150 }}>
          <option value="score">depth-adjusted</option>
          <option value="apy">highest APY</option>
          <option value="tvl">deepest</option>
          <option value="base">most organic</option>
          <option value="mean30d">best 30d mean</option>
        </select>
        <label className="tick" title="only modules this desk can put money into">
          <input type="checkbox" checked={addable} onChange={(e) => setAddable(e.target.checked)} /> addable
        </label>
        <label className="tick" title="only modules you can leave instantly — natively or at market">
          <input type="checkbox" checked={instant} onChange={(e) => setInstant(e.target.checked)} /> instant exit
        </label>
        <label className="tick">
          <input type="checkbox" checked={stable} onChange={(e) => setStable(e.target.checked)} /> stables
        </label>
        <label className="tick" title="most of the rate is fees, not token emissions">
          <input type="checkbox" checked={organic} onChange={(e) => setOrganic(e.target.checked)} /> organic
        </label>
      </div>

      {/* ── stats ───────────────────────────────────────────────────── */}
      <div className="stats">
        <div className="stat">
          <span className="stat-n">{meta ? meta.matched : "…"}</span>
          <span className="stat-l">modules</span>
        </div>
        <div className="stat">
          <span className="stat-n" style={{ color: "var(--accent)" }}>{meta ? meta.addable : "…"}</span>
          <span className="stat-l">addable from here</span>
        </div>
        <div className="stat">
          <span className="stat-n">{sources?.defillama?.pools_in_scope ?? "…"}</span>
          <span className="stat-l">eth · base · sol pools</span>
        </div>
        <div className="stat">
          <span className="stat-n">{sources?.bittensor?.subnets ?? (sources?.bittensor?.error ? "off" : "…")}</span>
          <span className="stat-l">tao subnets</span>
        </div>
        <div className="stat">
          <span className="stat-n">{sources?.composer?.vaults ?? 0}</span>
          <span className="stat-l">your vaults</span>
        </div>
        <div style={{ flex: 1 }} />
        <span className="pill" title="age of the cached index">
          {age === undefined ? "…" : age < 90 ? "live" : `${Math.round(age / 60)}m old`}
        </span>
        <button className="ghost" onClick={onOpenBook}>
          the book →
        </button>
      </div>

      <div className="fin-body">
        {/* ── the list ──────────────────────────────────────────────── */}
        <div className="scroll fin-list">
          {error && (
            <div className="issue" style={{ margin: 12 }}>
              <span>!</span>
              <span>{error}</span>
            </div>
          )}
          {loading && rows.length === 0 && <div className="empty">reading the registry…</div>}
          <div className="mod-head">
            <span>module</span>
            <span>kind</span>
            <span style={{ textAlign: "right" }}>APY</span>
            <span>of which fees</span>
            <span style={{ textAlign: "right" }}>depth</span>
            <span>exit</span>
            <span style={{ textAlign: "right" }}>way in</span>
          </div>
          {rows.map((m) => {
            const active = picked?.id === m.id;
            const r = m.returns ?? {};
            const l = m.liquidity ?? {};
            const fees = r.apy ? Math.max(0, Math.min(100, 100 - (r.emissions_share ?? 0))) : 0;
            return (
              <div key={m.id} className={`mod-row ${active ? "active" : ""}`} onClick={() => setPicked(m)}>
                <div className="mod-name">
                  <span className={`chain-dot ${m.chain}`} title={m.chain_label} />
                  <div style={{ minWidth: 0 }}>
                    <div className="mod-project">{m.project}</div>
                    <div className="mod-sub">{m.name}</div>
                  </div>
                </div>
                <span className="mod-kind">{m.kind}</span>
                <span className="mod-apy">{r.apy === null || r.apy === undefined ? <span className="dim">n/q</span> : pct(r.apy)}</span>
                <span className="mod-fees" title={`${(r.emissions_share ?? 0).toFixed(0)}% of the rate is emissions`}>
                  {r.apy ? (
                    <>
                      <span className="bar">
                        <span className="bar-fill" style={{ width: `${fees}%` }} />
                      </span>
                      <span className="dim">{pct(r.apy_base, 1)}</span>
                    </>
                  ) : (
                    <span className="dim">{r.emission_tao_per_block !== undefined ? "emission" : "—"}</span>
                  )}
                </span>
                <span className="mod-tvl">
                  {l.tvl_usd !== null && l.tvl_usd !== undefined ? money(l.tvl_usd) : l.tvl_tao ? `${Math.round(l.tvl_tao).toLocaleString()} τ` : "—"}
                  <span className="dim"> {l.depth}</span>
                </span>
                <span className={`mod-exit ${l.instant_exit ? "" : "slow"}`}>{exitLabel(l)}</span>
                <span style={{ textAlign: "right" }}>
                  {m.addable ? (
                    <span className="tag ok">{m.adapter?.kind === "swap_receipt" ? "buy receipt" : m.adapter?.kind === "tao_subnet" ? "stake" : m.adapter?.kind === "treasury_lock" ? "lock" : "deposit"}</span>
                  ) : m.gated ? (
                    <span className="tag bad">gated</span>
                  ) : (
                    <span className="tag">read-only</span>
                  )}
                </span>
              </div>
            );
          })}
          {!loading && rows.length === 0 && !error && <div className="empty">nothing passes those filters — lower the TVL floor or drop a toggle</div>}
          {meta?.rule && <div className="foot">{meta.rule}</div>}
        </div>

        {/* ── the rail ──────────────────────────────────────────────── */}
        <aside className="rail scroll">
          {!detail ? (
            <div className="rail-empty">
              <div style={{ fontSize: 22, color: "var(--accent)" }}>✦</div>
              <div style={{ marginTop: 10, lineHeight: 1.7 }}>
                Every row is a <b>module</b>: somewhere money can go that gives a return. Pick one to see
                what it pays, how you get in, how fast you get out, and what it is subject to — then put
                money in from here, through the module that owns the chain.
              </div>
              <div className="mono-small" style={{ marginTop: 12, lineHeight: 1.6 }}>
                Ethereum and Base through <b>eth</b> · Solana through <b>solana</b> · Bittensor through{" "}
                <b>bt</b>. No keys live here.
              </div>
            </div>
          ) : (
            <>
              <div className="rail-head">
                <span className={`chain-dot ${detail.chain}`} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 14, fontWeight: 700 }}>{detail.project}</div>
                  <div className="mod-sub">
                    {detail.name} · {detail.chain_label} · {detail.kind}
                  </div>
                </div>
                <span className="apy-big">
                  {detail.returns?.apy === null || detail.returns?.apy === undefined ? "n/q" : pct(detail.returns.apy)}
                </span>
              </div>

              <div className="kv-grid">
                <div className="kv"><span>fees</span><b>{pct(detail.returns?.apy_base)}</b></div>
                <div className="kv"><span>emissions</span><b>{pct(detail.returns?.apy_reward)}</b></div>
                <div className="kv"><span>30d mean</span><b>{pct(detail.returns?.apy_mean_30d)}</b></div>
                <div className="kv"><span>7d change</span><b>{detail.returns?.apy_change_7d == null ? "—" : `${Number(detail.returns.apy_change_7d) > 0 ? "+" : ""}${Number(detail.returns.apy_change_7d).toFixed(1)}%`}</b></div>
              </div>
              <div className="mono-small" style={{ marginTop: 6, lineHeight: 1.5 }}>{detail.returns?.basis}</div>
              {detail.chart?.points && <Spark points={detail.chart.points} />}

              <div className="label" style={{ marginTop: 16 }}>Liquidity</div>
              <div className="kv-grid" style={{ marginTop: 6 }}>
                <div className="kv"><span>depth</span><b>{detail.liquidity?.tvl_usd != null ? money(detail.liquidity.tvl_usd) : detail.liquidity?.tvl_tao ? `${Math.round(detail.liquidity.tvl_tao).toLocaleString()} τ` : "—"} <i className="dim">{detail.liquidity?.depth}</i></b></div>
                <div className="kv"><span>entry</span><b>{detail.liquidity?.entry}</b></div>
                <div className="kv"><span>exit</span><b className={detail.liquidity?.instant_exit ? "" : "warn"}>{exitLabel(detail.liquidity)}</b></div>
                <div className="kv"><span>lock</span><b>{detail.liquidity?.lock_days ? `${detail.liquidity.lock_days} days` : "none"}</b></div>
              </div>
              <div className="mono-small" style={{ marginTop: 6, lineHeight: 1.5 }}>{detail.liquidity?.exit_note}</div>

              <div className="label" style={{ marginTop: 16 }}>Conditions</div>
              <div className="conds">
                {(detail.conditions ?? []).map((c: any, i: number) => (
                  <div key={i} className={`cond ${c.level}`}>
                    <span className="cond-dot" />
                    <span>{c.text}</span>
                  </div>
                ))}
              </div>

              <div className="label" style={{ marginTop: 16 }}>Way in · way out</div>
              {detail.adapter ? (
                <div className="card" style={{ marginTop: 6, lineHeight: 1.6, fontSize: 11 }}>
                  <div><span className="dim">in</span> {detail.adapter.enter}</div>
                  <div style={{ marginTop: 4 }}><span className="dim">out</span> {detail.adapter.exit}</div>
                  <div className="mono-small" style={{ marginTop: 6 }}>{detail.adapter.executed_by}{detail.adapter.address ? ` · ${detail.adapter.address}` : ""}</div>
                </div>
              ) : (
                <div className="mono-small" style={{ marginTop: 6, lineHeight: 1.6 }}>
                  No adapter here yet. Listed for its terms; enter it through the protocol's own app. Adding
                  one is a row in <code>adapters.json</code>.
                </div>
              )}

              {detail.addable && (
                <>
                  <div className="label" style={{ marginTop: 18 }}>Add money</div>
                  <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
                    <input value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="amount" />
                    <span className="unit">{detail.adapter?.asset?.symbol ?? ""}</span>
                  </div>
                  {detail.adapter?.kind !== "treasury_lock" && (
                    <>
                      <input value={account} onChange={(e) => setAccount(e.target.value)} placeholder={accountHint} style={{ marginTop: 6 }} />
                      <input value={auth} onChange={(e) => setAuth(e.target.value)} type="password" placeholder={`bearer token for the ${chainModule} module (optional)`} style={{ marginTop: 6 }} />
                    </>
                  )}
                  <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
                    <button onClick={runQuote} disabled={busy !== "" || !amount.trim()} style={{ flex: 1 }}>
                      {busy === "quote" ? "pricing…" : "quote in & out"}
                    </button>
                    {detail.adapter?.kind === "treasury_lock" ? (
                      <button className="primary" onClick={onOpenTreasury} style={{ flex: 1 }}>open the treasury</button>
                    ) : (
                      <button className="primary" onClick={enter} disabled={busy !== "" || !amount.trim()} style={{ flex: 1 }}>
                        {busy === "enter" ? "sending…" : confirm ? "ADD MONEY" : "add (dry until confirmed)"}
                      </button>
                    )}
                  </div>
                  {detail.adapter?.kind !== "treasury_lock" && (
                    <label className="tick" style={{ marginTop: 8 }}>
                      <input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} /> yes, real money on {detail.chain_label}
                    </label>
                  )}

                  {quote && (
                    <div className="card" style={{ marginTop: 10 }}>
                      <div style={{ display: "flex", gap: 7, alignItems: "baseline" }}>
                        <span style={{ fontSize: 12, fontWeight: 600, flex: 1 }}>
                          {quote.entry?.expected ?? "—"} {quote.entry?.receipt ?? ""}
                        </span>
                        {quote.round_trip_cost_pct != null && (
                          <span className={`pill ${Number(quote.round_trip_cost_pct) > 1 ? "warn" : "ok"}`} title="what a round trip in and straight back out would cost today">
                            round trip {pct(quote.round_trip_cost_pct)}
                          </span>
                        )}
                      </div>
                      <div className="mono-small" style={{ marginTop: 6, lineHeight: 1.7 }}>
                        {(quote.plan ?? []).map((s: any) => (
                          <div key={s.step}>{s.step}. {s.what}</div>
                        ))}
                        {quote.entry?.impact_pct != null && <div>entry impact {pct(quote.entry.impact_pct, 3)} · {quote.entry.quoted_by ?? ""}</div>}
                        {quote.exit_today?.get_back && <div>out today: {quote.exit_today.get_back} {detail.adapter?.asset?.symbol} back{quote.exit_today.impact_pct != null ? ` · impact ${pct(quote.exit_today.impact_pct, 3)}` : ""}</div>}
                        {quote.exit_today?.how && <div>out: {quote.exit_today.how}{quote.exit_today.vault_total_assets ? ` · vault holds ${quote.exit_today.vault_total_assets}` : ""}</div>}
                        {quote.exit_today?.error && <div style={{ color: "var(--warn)" }}>exit quote: {quote.exit_today.error}</div>}
                      </div>
                    </div>
                  )}

                  {outcome && (
                    <div className="card" style={{ marginTop: 10, borderColor: outcome.entered ? "var(--accent-dim)" : outcome.error ? "#3a2126" : "var(--line)" }}>
                      <div style={{ fontSize: 12, fontWeight: 600 }}>
                        {outcome.entered ? "in — recorded in the book" : outcome.needs_confirm ? "not sent — needs confirm" : outcome.planned ? "planned — lock it in the treasury" : outcome.error ? "failed" : "not sent"}
                      </div>
                      <div className="mono-small" style={{ marginTop: 6, lineHeight: 1.6 }}>
                        {outcome.error ?? outcome.reason ?? outcome.swap?.reason ?? outcome.next ?? ""}
                        {(outcome.position?.txs ?? []).map((t: string) => (
                          <div key={t}>{t}</div>
                        ))}
                      </div>
                      {outcome.entered && (
                        <button className="ghost" onClick={onOpenBook} style={{ marginTop: 7, padding: "3px 9px", fontSize: 11 }}>see it in the book →</button>
                      )}
                    </div>
                  )}
                  <div className="mono-small" style={{ marginTop: 10, lineHeight: 1.6 }}>
                    Executed by the {chainModule} module with its own guards; this desk holds no key.
                    {!address && " Sign in to have positions attributed to your wallet."}
                  </div>
                </>
              )}
            </>
          )}
        </aside>
      </div>
    </div>
  );
}
