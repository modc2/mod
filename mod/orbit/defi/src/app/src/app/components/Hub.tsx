"use client";

import { useEffect, useState } from "react";
import * as api from "../lib/api";
import { money, pct } from "./Modules";

/// What EXPLORE hands to the MODULES room: a prefilled filter, and optionally
/// the exact module to open.
export type Prefill = { q?: string; stable?: boolean; chain?: string; pick?: string };

type Props = {
  say: (text: string, bad?: boolean) => void;
  onExplore: (prefill: Prefill) => void;
};

const CHAIN_FILTERS = [
  { id: "", label: "EVERYWHERE" },
  { id: "ethereum", label: "ETHEREUM" },
  { id: "base", label: "BASE" },
  { id: "solana", label: "SOLANA" },
  { id: "tao", label: "BITTENSOR" },
];

const TIER_WORD: Record<string, string> = {
  core: "core",
  established: "established",
  frontier: "frontier",
};

/// What one row of a protocol IS — the hub calls them "USD pools" everywhere
/// except the three entries that are not dollar pools, which name their own
/// unit (a Bittensor subnet, a Hyperliquid vault, a Polymarket trader).
function poolWord(chain: any, n: number) {
  const word = chain?.pool_word ?? "USD pool";
  return `${word}${n === 1 ? "" : "s"}`;
}

function TierTag({ tier }: { tier: string }) {
  const cls = tier === "core" ? "ok" : tier === "frontier" ? "bad" : "";
  return <span className={`tag ${cls}`}>{TIER_WORD[tier] ?? tier}</span>;
}

/// The HUB — the console's front door. A short, hand-vetted list of protocols
/// legitimate enough to point dollars at, each card showing where it runs
/// (multichain), what it pays right now, and one action.
export default function Hub({ say, onExplore }: Props) {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [chain, setChain] = useState("");
  const [picked, setPicked] = useState<any>(null);
  const [detail, setDetail] = useState<any>(null);
  // The full chain roster survives filtering: the API's `chains` collapses to
  // the selected chain once a filter is on, so keep the unfiltered snapshot.
  const [allChains, setAllChains] = useState<any[]>([]);
  const [showChains, setShowChains] = useState(false);
  // A chain filter can be a slow read (Bittensor knocks the bt module), and a
  // stale grid under a freshly-clicked chip reads as the chip doing nothing.
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let dead = false;
    setError(null);
    setLoading(true);
    // The open card may not survive the new filter — a rail describing a
    // protocol the grid no longer lists is the confusing part.
    setPicked(null);
    api
      .getHub(chain ? { chain } : {})
      .then((d) => {
        if (dead) return;
        setData(d);
        if (!chain) setAllChains(d.chains ?? []);
      })
      .catch((e) => !dead && setError(e.message))
      .finally(() => !dead && setLoading(false));
    return () => {
      dead = true;
    };
  }, [chain]);

  // Open a protocol: the card plus every USD pool it runs, per chain.
  useEffect(() => {
    if (!picked) return setDetail(null);
    setDetail(picked);
    api.getHubProtocol(picked.id).then(setDetail).catch((e: any) => say(e.message, true));
  }, [picked, say]);

  const rows: any[] = data?.hub ?? [];
  const reachable = rows.filter((r) => r.enterable_from_desk).length;
  // Bittensor (and the vaults) keep their TVL out of stable_tvl_usd because it
  // is not dollars — filtered down to them, the dollar stat would read $0.
  const usdOnly = !data || (data.stable_tvl_usd ?? 0) > 0;
  // How many vetted names the chain filter dropped, so the shortlist getting
  // shorter reads as the filter working rather than as the hub losing rows.
  const offChain: number = data?.off_chain ?? 0;
  const chainLabel = CHAIN_FILTERS.find((c) => c.id === chain)?.label ?? chain.toUpperCase();

  const explore = (p: any, pool?: any) => {
    // Bittensor's rows aren't stablecoins — a stable filter would hide them.
    if (p.source === "bittensor") {
      onExplore({ chain: "tao", pick: pool?.module_id ?? p.best?.module_id });
      return;
    }
    onExplore({
      q: p.llama_projects?.[0] ?? p.name,
      stable: true,
      pick: pool?.module_id ?? p.best?.module_id,
    });
  };

  return (
    <div className="fin">
      {/* ── filter band ─────────────────────────────────────────────── */}
      <div className="band">
        <div className="chips">
          {CHAIN_FILTERS.map((c) => (
            <button key={c.id} className={`chip ${chain === c.id ? "active" : ""}`} onClick={() => setChain(c.id)}>
              {c.label}
            </button>
          ))}
        </div>
        <span style={{ fontSize: 11, color: "var(--muted)" }}>
          Legit places for dollars — hand-picked names, live numbers, every chain they run on.
        </span>
        <div style={{ flex: 1 }} />
        <span className="pill" title={loading ? "reading this chain now" : "age of the cached DefiLlama index"}>
          {loading ? "reading…" : data ? (data.age_seconds < 90 ? "live" : `${Math.round(data.age_seconds / 60)}m old`) : "…"}
        </span>
      </div>

      {/* ── stats ───────────────────────────────────────────────────── */}
      <div className="stats">
        <div className="stat">
          <span className="stat-n">{data ? rows.length : "…"}</span>
          <span className="stat-l">vetted protocols</span>
        </div>
        <div
          className="stat"
          style={{ cursor: "pointer" }}
          title="show every chain the vetted protocols run on"
          onClick={() => setShowChains((s) => !s)}
        >
          <span className="stat-n">
            {allChains.length || (data ? data.chains?.length ?? 0 : "…")}{" "}
            <span style={{ fontSize: 10, color: "var(--accent)" }}>{showChains ? "▴" : "▾"}</span>
          </span>
          <span className="stat-l">chains</span>
        </div>
        <div className="stat" title={usdOnly ? "dollars sitting in the vetted USD pools" : "everything sitting in the shown protocols — Bittensor and the vaults are not dollars"}>
          <span className="stat-n">{data ? money(usdOnly ? data.stable_tvl_usd : data.tvl_usd ?? data.stable_tvl_usd) : "…"}</span>
          <span className="stat-l">{usdOnly ? "USD sitting in them" : "value sitting in them"}</span>
        </div>
        <div className="stat">
          <span className="stat-n" style={{ color: "var(--accent)" }}>{data ? reachable : "…"}</span>
          <span className="stat-l">enterable from this desk</span>
        </div>
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 10, color: "var(--dim)", maxWidth: 420, lineHeight: 1.5, textAlign: "right" }}>
          Curated is not certified — every card carries what can go wrong, and the rates are the
          market&apos;s, not ours.
        </span>
      </div>

      {/* ── every chain the vetted protocols run on, each one a filter ── */}
      {showChains && (
        <div className="band" style={{ flexWrap: "wrap", rowGap: 5 }}>
          {allChains.map((c: any) => {
            const active = chain.toLowerCase() === String(c.chain).toLowerCase() || (c.desk && chain === c.desk);
            return (
              <button
                key={c.chain}
                className={`hub-chain ${c.desk ? "in-reach" : ""}`}
                style={active ? { borderColor: "var(--accent)", background: "rgba(52, 211, 153, 0.15)" } : {}}
                title={
                  c.desk
                    ? `this desk has a route to ${c.chain} through the ${c.module} module`
                    : `${c.chain} is read-only from this desk — bridge first`
                }
                onClick={() => setChain(active ? "" : String(c.chain).toLowerCase())}
              >
                <span className={`chain-dot ${c.desk ?? ""}`} />
                {c.chain}
                <b>{c.protocols}</b>
                <span className="dim">{money(c.tvl_usd)}</span>
              </button>
            );
          })}
          {allChains.length === 0 && <span className="dim">reading the chain roster…</span>}
        </div>
      )}

      <div className="fin-body">
        {/* ── the cards ─────────────────────────────────────────────── */}
        <div className="scroll fin-list">
          {error && (
            <div className="issue" style={{ margin: 12 }}>
              <span>!</span>
              <span>{error}</span>
            </div>
          )}
          {!data && !error && <div className="empty">reading the hub…</div>}
          {data && !loading && rows.length === 0 && !error && (
            <div className="empty">
              nothing on the vetted list runs on {chainLabel} — try EVERYWHERE
            </div>
          )}
          <div className="hub-grid" style={loading && data ? { opacity: 0.45 } : {}}>
            {rows.map((p) => {
              const best = p.best;
              const chains: any[] = p.chains ?? [];
              const active = picked?.id === p.id;
              return (
                <div key={p.id} className={`hub-card ${active ? "active" : ""}`} onClick={() => setPicked(p)}>
                  <div className="hub-head">
                    <span className="hub-name">{p.name}</span>
                    <TierTag tier={p.tier} />
                    <span className="dim" style={{ marginLeft: "auto" }}>
                      {p.category} · since {p.since}
                    </span>
                  </div>
                  <div className="hub-blurb">{p.blurb}</div>
                  <div className="hub-chains">
                    {chains.slice(0, 5).map((c) => (
                      <span
                        key={c.chain}
                        className={`hub-chain ${c.enterable ? "in-reach" : ""}`}
                        title={
                          c.enterable
                            ? `enterable from this desk through the ${c.module} module`
                            : c.desk
                              ? "listed — enter through the protocol's own app"
                              : "this desk has no route there — bridge first"
                        }
                      >
                        <span className={`chain-dot ${c.desk ?? ""}`} />
                        {c.chain}
                        {/* No APY is promised on a TAO subnet or a vault — say
                            what is there instead of printing a dash. */}
                        <b>{c.best?.apy == null ? `${c.pools} ${poolWord(c, c.pools)}` : pct(c.best?.apy, 1)}</b>
                      </span>
                    ))}
                    {chains.length > 5 && <span className="hub-chain dim-chain">+{chains.length - 5} more</span>}
                    {chains.length === 0 && <span className="dim">no USD pools pass the floor right now</span>}
                  </div>
                  <div className="hub-foot">
                    {best ? (
                      <div className="hub-best">
                        <span className="apy-big">{best.apy == null ? money(best.tvl_usd) : pct(best.apy)}</span>
                        <span className="hub-best-sub">
                          {best.symbol} on {best.chain} ·{" "}
                          {best.apy == null ? "staked — no promised rate" : `${money(best.tvl_usd)} deep`}
                        </span>
                      </div>
                    ) : (
                      <span className="dim">—</span>
                    )}
                    <button
                      className={p.enterable_from_desk ? "primary" : ""}
                      onClick={(e) => {
                        e.stopPropagation();
                        explore(p);
                      }}
                    >
                      {p.enterable_from_desk ? (p.source === "bittensor" ? "stake TAO →" : "put USD in →") : "explore →"}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
          {offChain > 0 && rows.length > 0 && !loading && (
            <div className="foot">
              {offChain} more vetted {offChain === 1 ? "protocol does" : "protocols do"} not run on{" "}
              {chainLabel} — hidden by this filter.
            </div>
          )}
          {data?.note && <div className="foot">{data.note}</div>}
        </div>

        {/* ── the rail ──────────────────────────────────────────────── */}
        <aside className="rail scroll">
          {!detail ? (
            <div className="rail-empty">
              <div style={{ fontSize: 22, color: "var(--accent)" }}>✦</div>
              <div style={{ marginTop: 10, lineHeight: 1.7 }}>
                A short list made the cut: a real track record, a named team, public audits, and a
                way in for plain USD — plus three entries that say so on their cards: Bittensor
                (TAO in), Hyperliquid vaults (USDC behind a perps leader, trailing APR quoted not
                promised) and Polymarket copy-trading (one trader mirrored one-to-one). Pick a card
                to see <b>why it&apos;s here</b>, <b>what can go wrong</b>, and every chain it runs
                on — then put money in through the module that owns that chain.
              </div>
              <div className="mono-small" style={{ marginTop: 12, lineHeight: 1.6 }}>
                Green chains are enterable from this desk — Ethereum and Base through <b>eth</b>,
                Solana through <b>solana</b>, Bittensor through <b>bt</b>, vaults through{" "}
                <b>hyperliquid</b>, copy sessions through <b>polymarket</b> (owner-gated). The rest
                are shown honestly as read-only.
              </div>
            </div>
          ) : (
            <>
              <div className="rail-head">
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 15, fontWeight: 700, display: "flex", gap: 8, alignItems: "center" }}>
                    {detail.name} <TierTag tier={detail.tier} />
                  </div>
                  <div className="mod-sub">
                    {detail.category} · since {detail.since} ·{" "}
                    {detail.source === "bittensor"
                      ? `${money(detail.tvl_usd)} of TAO staked`
                      : `${money(detail.stable_tvl_usd)} in USD pools`}
                  </div>
                </div>
                <span className="apy-big">{detail.best ? pct(detail.best.apy) : "—"}</span>
              </div>

              <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.65, marginTop: 10 }}>{detail.blurb}</div>

              <div style={{ display: "flex", gap: 6, marginTop: 10, flexWrap: "wrap", alignItems: "center" }}>
                <span className="dim">takes</span>
                {(detail.usd_in?.length ? detail.usd_in : detail.source === "bittensor" ? ["TAO"] : ["—"]).map(
                  (s: string) => (
                    <span key={s} className="pill">{s}</span>
                  ),
                )}
                <a
                  href={detail.website}
                  target="_blank"
                  rel="noreferrer"
                  className="mono-small"
                  style={{ marginLeft: "auto", color: "var(--accent)" }}
                >
                  {String(detail.website ?? "").replace(/^https?:\/\//, "")} ↗
                </a>
              </div>

              <div className="label" style={{ marginTop: 16 }}>Why it&apos;s here</div>
              <div className="conds">
                {(detail.legit ?? []).map((t: string, i: number) => (
                  <div key={i} className="cond">
                    <span className="cond-dot" style={{ background: "var(--accent)" }} />
                    <span>{t}</span>
                  </div>
                ))}
              </div>

              <div className="label" style={{ marginTop: 14 }}>What can go wrong</div>
              <div className="conds">
                {(detail.risks ?? []).map((t: string, i: number) => (
                  <div key={i} className="cond risk">
                    <span className="cond-dot" />
                    <span>{t}</span>
                  </div>
                ))}
              </div>

              <div className="label" style={{ marginTop: 16 }}>Where it runs</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 6 }}>
                {(detail.chains ?? []).map((c: any) => (
                  <div key={c.chain} className="card" style={{ padding: "8px 10px" }}>
                    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                      <span className={`chain-dot ${c.desk ?? ""}`} />
                      <b style={{ fontSize: 12 }}>{c.chain}</b>
                      <span className="dim">
                        {c.pools} {poolWord(c, c.pools)} · {money(c.tvl_usd)}
                      </span>
                      <span style={{ marginLeft: "auto" }}>
                        {c.enterable ? (
                          <span className="tag ok">in reach</span>
                        ) : c.desk ? (
                          <span className="tag">their app</span>
                        ) : (
                          <span className="tag">bridge first</span>
                        )}
                      </span>
                    </div>
                    {c.best && (
                      <div
                        className="mono-small"
                        style={{ marginTop: 5, display: "flex", gap: 8, alignItems: "center" }}
                      >
                        <span>
                          best: {c.best.symbol}{" "}
                          {c.best.apy == null
                            ? "· no promised rate ·"
                            : `${pct(c.best.apy)} (${pct(c.best.apy_base, 1)} fees) ·`}{" "}
                          {money(c.best.tvl_usd)}
                        </span>
                        <button
                          className="ghost"
                          style={{ marginLeft: "auto", padding: "1px 8px", fontSize: 10 }}
                          onClick={() => explore(detail, c.best)}
                        >
                          open →
                        </button>
                      </div>
                    )}
                  </div>
                ))}
              </div>

              <button
                className={detail.enterable_from_desk ? "primary" : ""}
                style={{ width: "100%", marginTop: 14 }}
                onClick={() => explore(detail)}
              >
                {detail.enterable_from_desk
                  ? detail.source === "bittensor"
                    ? "stake TAO from this desk →"
                    : "put USD in from this desk →"
                  : `see its ${poolWord(detail.chains?.[0], 2)} in MODULES →`}
              </button>
              <div className="mono-small" style={{ marginTop: 8, lineHeight: 1.6 }}>
                Opens the MODULES room filtered to this protocol&apos;s{" "}
                {poolWord(detail.chains?.[0], 2)} — quote the round trip before anything moves.
                Executed by the chain&apos;s own module; no keys live here.
              </div>
            </>
          )}
        </aside>
      </div>
    </div>
  );
}
