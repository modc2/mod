"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import * as api from "../lib/api";

type Props = { onClose: () => void; say: (text: string, bad?: boolean) => void; address: string | null };

const money = (value: number | null | undefined) => {
  if (value === null || value === undefined || !isFinite(value)) return "—";
  if (value >= 1e9) return `$${(value / 1e9).toFixed(2)}b`;
  if (value >= 1e6) return `$${(value / 1e6).toFixed(1)}m`;
  if (value >= 1e3) return `$${(value / 1e3).toFixed(0)}k`;
  return `$${value.toFixed(0)}`;
};

const pct = (value: number | null | undefined, digits = 2) =>
  value === null || value === undefined || !isFinite(value) ? "—" : `${value.toFixed(digits)}%`;

function countdown(seconds: number): string {
  if (seconds <= 0) return "open now";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return d > 0 ? `${d}d ${h}h` : h > 0 ? `${h}h ${m}m` : `${m}m`;
}

/// The yields table and the treasury, side by side, because they are one
/// decision: what is paying, and where the money goes when you pick it.
///
/// Nothing here computes an APR. Every rate is DefiLlama's, forwarded by this
/// module's /yields routes with `apy_base` and `apy_reward` kept apart, and the
/// panel keeps them apart too — a row whose rate is mostly emissions says so on
/// its face rather than in a footnote nobody reads.
export default function YieldDesk({ onClose, say, address }: Props) {
  const [view, setView] = useState<"protocols" | "pools">("protocols");
  const [rows, setRows] = useState<any[]>([]);
  const [meta, setMeta] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [chain, setChain] = useState("");
  const [q, setQ] = useState("");
  const [minTvl, setMinTvl] = useState("1000000");
  const [stable, setStable] = useState(false);
  const [organic, setOrganic] = useState(false);
  const [sort, setSort] = useState("score");
  const [facets, setFacets] = useState<any>(null);

  const [picked, setPicked] = useState<any>(null);
  const [amount, setAmount] = useState("1000");
  const [weeks, setWeeks] = useState("12");
  const [escrow, setEscrow] = useState(true);
  const [assetAddress, setAssetAddress] = useState("");

  const [desk, setDesk] = useState<any>(null);
  const [preview, setPreview] = useState<any>(null);
  const [watch, setWatch] = useState("");
  const [busy, setBusy] = useState("");

  const params = useMemo(
    () => ({
      chain: chain || undefined,
      q: q.trim() || undefined,
      min_tvl: minTvl || undefined,
      stable: stable || undefined,
      organic: organic || undefined,
      sort,
      limit: 80,
    }),
    [chain, q, minTvl, stable, organic, sort]
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const body =
        view === "protocols"
          ? await api.getYieldProtocols({ ...params, sort: sort === "score" ? "tvl" : sort })
          : await api.getYields(params);
      setRows(view === "protocols" ? body.protocols : body.pools);
      setMeta(body);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [view, params, sort]);

  const loadDesk = useCallback(async () => {
    try {
      const [d, p] = await Promise.all([
        api.getTreasury(),
        api.getPreview().catch(() => null),
      ]);
      setDesk(d);
      setPreview(p);
    } catch (e: any) {
      say(e.message, true);
    }
  }, [say]);

  // The index is a 12 MB upstream fetch cached server-side, so re-filtering is
  // cheap — debounce only enough to not fire on every keystroke.
  useEffect(() => {
    const timer = setTimeout(load, 220);
    return () => clearTimeout(timer);
  }, [load]);

  useEffect(() => {
    api.getYieldFacets().then(setFacets).catch(() => {});
    loadDesk();
  }, [loadDesk]);

  const choose = async () => {
    if (!address) return say("sign in to put a choice in the treasury", true);
    if (!picked) return say("pick a row first", true);
    setBusy("choose");
    try {
      const body = {
        pool: picked.pool ?? picked.best_pool?.pool ?? "",
        project: picked.project,
        chain: picked.chain ?? picked.best_pool?.chain ?? "",
        symbol: picked.symbol ?? picked.best_pool?.symbol ?? "",
        apy: picked.apy,
        apy_base: picked.apy_base,
        tvl_usd: picked.tvl_usd,
        amount: amount.trim(),
        asset: picked.symbol ?? picked.best_pool?.symbol ?? "",
        asset_address: assetAddress.trim() || undefined,
        term_weeks: Number(weeks) || 12,
        return_principal: escrow,
      };
      const out = await api.chooseAllocation(body);
      say(`${out.allocation.amount} ${out.allocation.asset} → treasury (plan)`);
      await loadDesk();
    } catch (e: any) {
      say(e.message, true);
    } finally {
      setBusy("");
    }
  };

  const drop = async (id: string) => {
    try {
      await api.dropAllocation(id);
      say("plan dropped");
      await loadDesk();
    } catch (e: any) {
      say(e.message, true);
    }
  };

  const addWatch = async () => {
    if (!address) return say("sign in first", true);
    try {
      await api.watchAddress(watch.trim());
      setWatch("");
      await loadDesk();
      say("watching");
    } catch (e: any) {
      say(e.message, true);
    }
  };

  const until: number = preview?.seconds_until ?? desk?.schedule?.seconds_until_next ?? 0;
  const onchain = desk?.onchain;

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "var(--bg)",
        zIndex: 40,
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 9,
          padding: "10px 12px",
          borderBottom: "1px solid var(--line)",
          background: "var(--panel)",
        }}
      >
        <span style={{ fontSize: 12, fontWeight: 700, letterSpacing: "0.06em" }}>YIELD</span>
        <span className="pill">
          {meta ? `${meta.matched} of ${meta.universe ?? "—"}` : "…"}
        </span>
        {meta?.age_seconds !== undefined && (
          <span className="pill" title="how old the cached index is">
            {meta.age_seconds < 90 ? "live" : `${Math.round(meta.age_seconds / 60)}m old`}
          </span>
        )}
        <div style={{ flex: 1 }} />
        <span className="pill ok" title={preview?.at_iso}>
          <span className="dot" />
          next payout {countdown(until)}
        </span>
        <button className="ghost" onClick={onClose} style={{ padding: "3px 10px" }}>
          ×
        </button>
      </div>

      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
        {/* ── the table ─────────────────────────────────────────────────── */}
        <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
          <div
            style={{
              display: "flex",
              gap: 6,
              padding: "9px 12px",
              borderBottom: "1px solid var(--line)",
              flexWrap: "wrap",
              alignItems: "center",
            }}
          >
            <button
              className={view === "protocols" ? "primary" : ""}
              onClick={() => setView("protocols")}
            >
              by protocol
            </button>
            <button className={view === "pools" ? "primary" : ""} onClick={() => setView("pools")}>
              by pool
            </button>
            <select value={chain} onChange={(e) => setChain(e.target.value)} style={{ width: 140 }}>
              <option value="">every chain</option>
              {(facets?.chains ?? []).map((c: any) => (
                <option key={c.name} value={c.name}>
                  {c.name}
                  {c.chain_id ? " ✦" : ""}
                </option>
              ))}
            </select>
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="search protocol, symbol…"
              style={{ width: 200 }}
            />
            <select value={minTvl} onChange={(e) => setMinTvl(e.target.value)} style={{ width: 130 }}>
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
            <label style={{ display: "flex", gap: 5, alignItems: "center", fontSize: 11, color: "var(--muted)" }}>
              <input type="checkbox" checked={stable} onChange={(e) => setStable(e.target.checked)} style={{ width: 13 }} />
              stables
            </label>
            <label
              style={{ display: "flex", gap: 5, alignItems: "center", fontSize: 11, color: "var(--muted)" }}
              title="only pools where most of the rate is fees rather than token emissions"
            >
              <input type="checkbox" checked={organic} onChange={(e) => setOrganic(e.target.checked)} style={{ width: 13 }} />
              organic
            </label>
          </div>

          <div className="scroll" style={{ flex: 1 }}>
            {error && (
              <div className="issue" style={{ margin: 12 }}>
                <span>!</span>
                <span>{error}</span>
              </div>
            )}
            {loading && rows.length === 0 && (
              <div style={{ padding: 20, color: "var(--dim)", fontSize: 11 }}>reading the index…</div>
            )}
            <table style={{ width: "100%", minWidth: 760, borderCollapse: "collapse", fontSize: 11 }}>
              <thead>
                <tr style={{ position: "sticky", top: 0, background: "var(--panel)", zIndex: 1 }}>
                  {(view === "protocols"
                    ? ["protocol", "pools", "APR", "of which fees", "best pool", "TVL", "chains"]
                    : ["protocol", "pool", "chain", "APR", "fees", "30d mean", "TVL", ""]
                  ).map((h, i) => (
                    <th
                      key={i}
                      className="label"
                      style={{
                        textAlign: i === 0 || i === 1 || i === 4 ? "left" : "right",
                        padding: "7px 10px",
                        borderBottom: "1px solid var(--line)",
                        fontWeight: 400,
                      }}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r: any, i: number) => {
                  const key = view === "protocols" ? r.project : r.pool;
                  const isPicked =
                    picked && (view === "protocols" ? picked.project === r.project : picked.pool === r.pool);
                  const emissions = r.emissions_share ?? (r.apy > 0 ? ((r.apy_reward ?? 0) / r.apy) * 100 : 0);
                  return (
                    <tr
                      key={key ?? i}
                      onClick={() => {
                        setPicked(r);
                        setAssetAddress("");
                      }}
                      style={{
                        borderBottom: "1px solid var(--line-soft)",
                        cursor: "pointer",
                        background: isPicked ? "rgba(52,211,153,0.07)" : undefined,
                      }}
                    >
                      <td style={{ padding: "7px 10px", color: isPicked ? "var(--accent)" : "var(--text)" }}>
                        {r.project}
                      </td>
                      {view === "protocols" ? (
                        <>
                          <td style={{ padding: "7px 10px", textAlign: "right", color: "var(--dim)" }}>{r.pools}</td>
                          <td style={{ padding: "7px 10px", textAlign: "right", fontWeight: 600 }}>{pct(r.apy)}</td>
                          <td style={{ padding: "7px 10px", textAlign: "right", color: "var(--muted)" }}>
                            {pct(r.apy_base)}
                          </td>
                          <td style={{ padding: "7px 10px", color: "var(--muted)" }}>
                            {r.best_pool ? `${r.best_pool.symbol} · ${pct(r.best_pool.apy)}` : "—"}
                          </td>
                          <td style={{ padding: "7px 10px", textAlign: "right", color: "var(--muted)" }}>
                            {money(r.tvl_usd)}
                          </td>
                          <td
                            style={{ padding: "7px 10px", textAlign: "right", whiteSpace: "nowrap" }}
                            title={(r.chains ?? []).join(", ")}
                          >
                            <span className="mono-small">
                              {(r.chains ?? []).slice(0, 2).join(" · ")}
                              {(r.chains ?? []).length > 2 ? ` +${r.chains.length - 2}` : ""}
                            </span>
                          </td>
                        </>
                      ) : (
                        <>
                          <td style={{ padding: "7px 10px", color: "var(--muted)" }}>
                            {r.symbol}
                            {r.meta ? <span className="mono-small"> {r.meta}</span> : null}
                          </td>
                          <td style={{ padding: "7px 10px", textAlign: "right", color: "var(--muted)" }}>{r.chain}</td>
                          <td style={{ padding: "7px 10px", textAlign: "right", fontWeight: 600 }}>{pct(r.apy)}</td>
                          <td
                            style={{ padding: "7px 10px", textAlign: "right", color: emissions > 50 ? "var(--warn)" : "var(--muted)" }}
                            title={`${emissions.toFixed(0)}% of this rate is token emissions`}
                          >
                            {pct(r.apy_base)}
                          </td>
                          <td style={{ padding: "7px 10px", textAlign: "right", color: "var(--muted)" }}>
                            {pct(r.apy_mean_30d)}
                          </td>
                          <td style={{ padding: "7px 10px", textAlign: "right", color: "var(--muted)" }}>
                            {money(r.tvl_usd)}
                          </td>
                          <td style={{ padding: "7px 10px", textAlign: "right" }}>
                            {r.tradable_on && <span className="pill ok">{r.tradable_on}</span>}
                          </td>
                        </>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {!loading && rows.length === 0 && !error && (
              <div style={{ padding: 20, color: "var(--dim)", fontSize: 11 }}>
                nothing passes those filters — try a lower TVL floor
              </div>
            )}
            {meta?.note && (
              <div style={{ padding: "12px 12px 22px", color: "var(--dim)", fontSize: 10, lineHeight: 1.6 }}>
                {meta.note}. Source: {meta.source}.
              </div>
            )}
          </div>
        </div>

        {/* ── the treasury ──────────────────────────────────────────────── */}
        <aside
          style={{
            width: 390,
            borderLeft: "1px solid var(--line)",
            background: "var(--panel)",
            display: "flex",
            flexDirection: "column",
            flexShrink: 0,
          }}
        >
          <div className="scroll" style={{ flex: 1, padding: 12 }}>
            <div className="label">Lock into the treasury</div>
            {!picked ? (
              <div style={{ fontSize: 11, color: "var(--dim)", lineHeight: 1.6, marginTop: 8 }}>
                Pick a row on the left. What you choose is written into a treasury that pays out
                every Friday 12:00 EST — BlocTime&apos;s window, not one of ours — split across BLOC
                holders in proportion to what they hold.
              </div>
            ) : (
              <>
                <div className="card" style={{ marginTop: 8 }}>
                  <div style={{ display: "flex", gap: 7, alignItems: "baseline" }}>
                    <span style={{ fontSize: 12, fontWeight: 600, flex: 1 }}>{picked.project}</span>
                    <span style={{ fontSize: 15, fontWeight: 700, color: "var(--accent)" }}>
                      {pct(picked.apy)}
                    </span>
                  </div>
                  <div className="mono-small" style={{ marginTop: 5, lineHeight: 1.6 }}>
                    {picked.symbol ?? picked.best_pool?.symbol} · {picked.chain ?? picked.best_pool?.chain} ·{" "}
                    {money(picked.tvl_usd)} TVL
                    <br />
                    {pct(picked.apy_base)} of it is fees
                    {picked.apy_mean_30d !== undefined && picked.apy_mean_30d !== null
                      ? ` · 30d mean ${pct(picked.apy_mean_30d)}`
                      : ""}
                  </div>
                </div>

                <div style={{ display: "flex", gap: 6, marginTop: 9 }}>
                  <div style={{ flex: 1 }}>
                    <div className="label">Amount</div>
                    <input value={amount} onChange={(e) => setAmount(e.target.value)} />
                  </div>
                  <div style={{ width: 96 }}>
                    <div className="label">Weeks</div>
                    <input value={weeks} onChange={(e) => setWeeks(e.target.value)} />
                  </div>
                </div>

                <div style={{ marginTop: 9 }}>
                  <div className="label">What gets paid out</div>
                  <select
                    value={escrow ? "yield" : "stream"}
                    onChange={(e) => setEscrow(e.target.value === "yield")}
                    style={{ marginTop: 4 }}
                  >
                    <option value="yield">the yield — principal comes back after the term</option>
                    <option value="stream">the principal — streamed out a slice a week</option>
                  </select>
                  <div className="mono-small" style={{ marginTop: 6, lineHeight: 1.6 }}>
                    {escrow
                      ? `≈ ${((Number(amount) || 0) * (picked.apy / 100) / 52).toFixed(2)} ${
                          picked.symbol ?? ""
                        } a week, projected off ${pct(picked.apy)}. Principal locked ${weeks} weeks.`
                      : `${((Number(amount) || 0) / (Number(weeks) || 1)).toFixed(2)} ${
                          picked.symbol ?? ""
                        } a week for ${weeks} weeks. Nothing comes back — the principal IS the payout.`}
                  </div>
                </div>

                <div style={{ marginTop: 9 }}>
                  <div className="label">Asset address (needed to lock on chain)</div>
                  <input
                    value={assetAddress}
                    onChange={(e) => setAssetAddress(e.target.value)}
                    placeholder="0x… the ERC20 you are locking"
                    style={{ marginTop: 4 }}
                  />
                </div>

                <button
                  className="primary"
                  onClick={choose}
                  disabled={!address || busy === "choose"}
                  style={{ width: "100%", marginTop: 10 }}
                >
                  {busy === "choose" ? "writing…" : address ? "put it in the treasury" : "sign in first"}
                </button>
                <div className="mono-small" style={{ marginTop: 6, lineHeight: 1.5 }}>
                  This writes a plan. Nothing moves until you lock it against a deployed treasury —
                  and after that it cannot be recalled early.
                </div>
              </>
            )}

            {/* the weekly window */}
            <div className="label" style={{ marginTop: 18 }}>
              Next distribution
            </div>
            <div className="card" style={{ marginTop: 6 }}>
              <div style={{ display: "flex", alignItems: "baseline", gap: 7 }}>
                <span style={{ fontSize: 15, fontWeight: 700, color: "var(--accent)" }}>
                  {countdown(until)}
                </span>
                <span className="mono-small" style={{ flex: 1, textAlign: "right" }}>
                  {preview?.at_iso ?? ""}
                </span>
              </div>
              <div className="mono-small" style={{ marginTop: 6, lineHeight: 1.6 }}>
                Friday 12:00 EST · BlocTime&apos;s DISTRIBUTION_OFFSET, pinned year round
                <br />
                pot: {(preview?.pot_principal ?? 0).toFixed(2)} principal +{" "}
                {(preview?.pot_yield_projected ?? 0).toFixed(2)} projected yield
              </div>
              {onchain && !onchain.error && (
                <div className="mono-small" style={{ marginTop: 6, color: "var(--accent)" }}>
                  on chain: {onchain.payout_this_week} to pay · {onchain.registered_holders} registered ·{" "}
                  {onchain.weeks_paid} weeks paid
                </div>
              )}
            </div>

            {/* who it splits across */}
            <div className="label" style={{ marginTop: 16 }}>
              Split across BLOC holders
            </div>
            <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
              <input
                value={watch}
                onChange={(e) => setWatch(e.target.value)}
                placeholder="0x… holder to watch"
              />
              <button onClick={addWatch} disabled={!watch.trim()}>
                add
              </button>
            </div>
            <div style={{ marginTop: 7, display: "flex", flexDirection: "column", gap: 4 }}>
              {(preview?.splits ?? []).map((s: any) => (
                <div
                  key={s.address}
                  style={{ display: "flex", gap: 7, fontSize: 11, alignItems: "baseline" }}
                >
                  <span className="mono-small" style={{ flex: 1 }}>
                    {String(s.address).slice(0, 10)}…{String(s.address).slice(-4)}
                  </span>
                  <span style={{ color: "var(--muted)" }}>{s.bloc.toFixed(0)} BLOC</span>
                  <span style={{ color: "var(--accent)", width: 54, textAlign: "right" }}>
                    {pct(s.share_pct, 1)}
                  </span>
                </div>
              ))}
              {(preview?.splits ?? []).length === 0 && (
                <div className="mono-small">{preview?.basis ?? "nobody is watched yet"}</div>
              )}
            </div>

            {/* the ledger */}
            <div className="label" style={{ marginTop: 18 }}>
              In the treasury ({desk?.count ?? 0})
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 6 }}>
              {(desk?.allocations ?? []).map((a: any) => (
                <div key={a.id} className="card">
                  <div style={{ display: "flex", gap: 7, alignItems: "baseline" }}>
                    <span style={{ fontSize: 12, fontWeight: 600, flex: 1 }}>
                      {a.amount} {a.asset}
                    </span>
                    <span className={`pill ${a.status === "locked" ? "ok" : ""}`}>{a.status}</span>
                  </div>
                  <div className="mono-small" style={{ marginTop: 5, lineHeight: 1.6 }}>
                    {a.project} · {pct(a.apy_at_choice)} when chosen · week {a.weeks_elapsed}/
                    {a.term_weeks}
                    <br />
                    {a.return_principal
                      ? `${a.weekly_yield_projected}/wk projected yield, principal returns at term`
                      : `${a.weekly_principal}/wk principal out`}
                  </div>
                  {a.recallable && (
                    <button
                      className="ghost danger"
                      onClick={() => drop(a.id)}
                      style={{ marginTop: 7, padding: "3px 9px", fontSize: 11 }}
                    >
                      drop this plan
                    </button>
                  )}
                </div>
              ))}
              {(desk?.allocations ?? []).length === 0 && (
                <div className="mono-small">nothing chosen yet</div>
              )}
            </div>

            {/* the contract */}
            <div className="label" style={{ marginTop: 18 }}>
              The contract
            </div>
            <div className="mono-small" style={{ marginTop: 6, lineHeight: 1.6 }}>
              {desk?.binding?.address ? (
                <>
                  bound to {desk.binding.address} on {desk.binding.network}
                  {onchain?.error ? <span style={{ color: "var(--danger)" }}> — {onchain.error}</span> : null}
                </>
              ) : (
                <>
                  No treasury deployed yet. Drop the <b>BlocTime Treasury</b> block on the canvas,
                  wire the asset into it, deploy it with your wallet, then bind the address here —
                  after that these numbers come off the chain instead of this ledger.
                </>
              )}
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}
