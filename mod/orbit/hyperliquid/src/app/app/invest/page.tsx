"use client";

/**
 * Your money — the page this whole feature exists for.
 *
 * Everything you've put to work in one list, whichever machine is doing the
 * trading: Hyperliquid's own vaults and the trader sleeves this console runs
 * inside your account. Same row, same numbers, same three buttons. The header
 * answers the only question that matters on arrival — *am I up or down* — and
 * the rows answer *on what*.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ago, fmtPct, fmtPnl, fmtUsd, shortAddr } from "../lib/api";
import {
  portfolio, explain, statusLabel, statusTone,
  type Portfolio, type Position,
} from "../lib/invest";
import { useSession } from "../lib/auth";
import { Identicon, Kpi, Meter, PageHead, SplitBar } from "../components/BoardBits";

export default function InvestPage() {
  const { me, state, label } = useSession();
  const [data, setData] = useState<Portfolio | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [showClosed, setShowClosed] = useState(false);

  const load = useCallback(async () => {
    if (!me) { setData(null); return; }
    setLoading(true); setErr(null);
    try { setData(await portfolio(me, showClosed)); }
    catch (e: any) { setErr(String(e?.message ?? e)); }
    finally { setLoading(false); }
  }, [me, showClosed]);

  useEffect(() => { load(); }, [load]);
  // Sleeves move on their own — keep the page honest without a refresh button.
  useEffect(() => {
    if (!me) return;
    const h = setInterval(load, 15_000);
    return () => clearInterval(h);
  }, [me, load]);

  const groups = useMemo(() => groupPositions(data?.positions ?? []), [data]);
  const winners = (data?.positions ?? []).filter((p) => p.value.pnl > 0).length;
  const losers = (data?.positions ?? []).filter((p) => p.value.pnl < 0).length;

  if (state === "loading") {
    return <div className="text-xs text-muted">{label}</div>;
  }

  if (!me) return <SignedOut />;

  const t = data?.totals;
  const cap = data?.capacity;

  return (
    <section className="space-y-5">
      <PageHead
        title="Invest"
        blurb="Every dollar you've put to work — vault deposits and traders you're backing — valued live."
        right={
          <div className="flex items-center gap-2">
            <Link href="/invest/new" className="btn-primary">invest</Link>
          </div>
        }
      />

      {data?.engine.dry_run && (
        <div className="rounded-lg border border-warn/30 bg-warn/[0.06] px-3 py-2 text-xs text-warn">
          This server is running in dry-run mode — every position is simulated, no orders are sent.
        </div>
      )}

      {/* ── the four numbers ── */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Kpi label="invested" value={fmtUsd(t?.invested ?? 0)}
          sub={`${t?.count ?? 0} position${(t?.count ?? 0) === 1 ? "" : "s"}`} />
        <Kpi label="worth now" value={fmtUsd(t?.equity ?? 0)}
          sub={t && t.invested > 0 ? `${fmtPct(t.roi_pct, 1)} return` : "—"}
          tone={(t?.pnl ?? 0) >= 0 ? "win" : "loss"} />
        <Kpi label="profit" value={fmtPnl(t?.pnl ?? 0)}
          sub={`${winners} up · ${losers} down`}
          tone={(t?.pnl ?? 0) >= 0 ? "win" : "loss"}>
          <SplitBar up={winners} down={losers} />
        </Kpi>
        <Kpi label="free to invest" value={fmtUsd(cap?.free ?? 0)}
          sub={cap ? `of ${fmtUsd(cap.account_value)} in your account` : "—"}>
          <Meter pct={cap && cap.account_value > 0 ? (cap.committed / cap.account_value) * 100 : 0} />
        </Kpi>
      </div>

      {err && <div className="text-xs text-loss break-words">{err}</div>}

      {/* ── the book ── */}
      {loading && !data ? (
        <div className="panel p-4 space-y-3">
          {[...Array(3)].map((_, i) => <div key={i} className="skeleton h-10 w-full" />)}
        </div>
      ) : groups.length === 0 ? (
        <Empty />
      ) : (
        <div className="space-y-4">
          {groups.map((g) => (
            <div key={g.key} className="panel overflow-hidden">
              {g.name && (
                <div className="px-4 py-2.5 border-b border-border flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="eyebrow !mb-0">basket</div>
                    <div className="text-sm font-semibold truncate">{g.name}</div>
                  </div>
                  <div className="flex items-center gap-5 text-right">
                    <Cell label="invested" value={fmtUsd(g.invested)} />
                    <Cell label="worth" value={fmtUsd(g.equity)} />
                    <Cell label="profit" value={fmtPnl(g.pnl)}
                      cls={g.pnl >= 0 ? "text-win" : "text-loss"} />
                  </div>
                </div>
              )}
              {g.positions.map((p) => <Row key={p.id} p={p} />)}
            </div>
          ))}
        </div>
      )}

      <div className="flex items-center justify-between text-[11px] text-muted">
        <button className="hover:text-ink" onClick={() => setShowClosed((v) => !v)}>
          {showClosed ? "hide closed positions" : "show closed positions"}
        </button>
        {data?.engine.stats.last_cycle_ms ? (
          <span>engine checked {ago(data.engine.stats.last_cycle_ms)}</span>
        ) : null}
      </div>
    </section>
  );
}

// ─── one position ───────────────────────────────────────────────────────

function Row({ p }: { p: Position }) {
  const tone = statusTone(p);
  const toneCls = tone === "win" ? "bg-win" : tone === "warn" ? "bg-warn"
    : tone === "loss" ? "bg-loss" : "bg-muted";
  const up = p.value.pnl >= 0;

  return (
    <Link href={`/invest/${p.id}`}
      className="group grid grid-cols-[1.8fr_repeat(3,1fr)_auto] gap-3 px-4 py-3 items-center table-row hover:bg-accent/[0.04]">
      <div className="flex items-center gap-2.5 min-w-0">
        <Identicon address={p.target} size={22} />
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-ink group-hover:text-accent transition-colors">
              {p.name || shortAddr(p.target)}
            </span>
            <span className={`pill !py-0 !px-1.5 text-[9px] ${
              p.mode === "paper" ? "text-warn border-warn/30"
              : p.kind === "vault" ? "text-accent2 border-accent2/30" : "text-muted"}`}>
              {p.mode === "paper" ? "paper" : p.kind}
            </span>
          </div>
          <div className="flex items-center gap-1.5 text-[11px] text-muted mt-0.5">
            <span className={`h-1.5 w-1.5 rounded-full ${toneCls}`} />
            <span className="truncate">
              {p.last_error ? p.last_error : statusLabel(p)}
              {p.kind === "trader" && p.legs.length > 0 && !p.last_error &&
                ` · ${p.legs.length} position${p.legs.length === 1 ? "" : "s"}`}
            </span>
          </div>
        </div>
      </div>

      <Cell label="invested" value={fmtUsd(p.net_contributed)} />
      <Cell label="worth" value={fmtUsd(p.value.equity)} />
      <Cell label="profit" value={fmtPnl(p.value.pnl)}
        sub={p.net_contributed > 0 ? fmtPct(p.value.roi_pct, 1) : undefined}
        cls={up ? "text-win" : "text-loss"} />

      <span className="btn-ghost opacity-0 group-hover:opacity-100 transition-opacity">manage</span>
    </Link>
  );
}

function Cell({ label, value, sub, cls = "text-ink" }: {
  label: string; value: string; sub?: string; cls?: string;
}) {
  return (
    <div className="text-right">
      <div className="text-[9px] uppercase tracking-wider text-muted">{label}</div>
      <div className={`num text-sm ${cls}`}>{value}</div>
      {sub && <div className="text-[10px] text-muted num">{sub}</div>}
    </div>
  );
}

// ─── empty / signed-out states ──────────────────────────────────────────

function Empty() {
  return (
    <div className="panel p-8 text-center space-y-5">
      <div>
        <div className="text-[17px] font-semibold">Nothing invested yet.</div>
        <p className="text-xs text-muted mt-1 max-w-md mx-auto">
          Pick someone who trades better than you do and put an amount behind them.
          Your money stays in your own account and you can pull it back at any time —
          or try it on paper first, with nothing at risk.
        </p>
      </div>
      <div className="grid sm:grid-cols-2 gap-3 max-w-xl mx-auto text-left">
        <Choice href="/invest/new?kind=trader" title="Back a trader"
          body="Your account holds what they hold, scaled to your money. No lockup." />
        <Choice href="/invest/new?kind=vault" title="Deposit into a vault"
          body="Hyperliquid's own vaults. The leader trades it; HL does the accounting." />
      </div>
    </div>
  );
}

function Choice({ href, title, body }: { href: string; title: string; body: string }) {
  return (
    <Link href={href}
      className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-4 hover:border-accent/40 hover:bg-accent/[0.04] transition-colors">
      <div className="text-sm font-semibold">{title}</div>
      <div className="text-[11px] text-muted mt-1">{body}</div>
    </Link>
  );
}

function SignedOut() {
  return (
    <section className="space-y-5">
      <PageHead title="Invest"
        blurb="Put money behind the traders on the board, or into a Hyperliquid vault — and watch it in one place." />
      <div className="panel p-8 text-center space-y-4">
        <p className="text-sm text-muted max-w-lg mx-auto">
          Connect your wallet (top right) to see your positions. Nothing here can move money
          without a signature from you, and you can preview exactly what any amount would buy
          before connecting anything.
        </p>
        <div className="flex items-center justify-center gap-2">
          <Link href="/" className="btn">browse traders</Link>
          <Link href="/vaults" className="btn">browse vaults</Link>
        </div>
      </div>
    </section>
  );
}

// ─── grouping ───────────────────────────────────────────────────────────

type Group = {
  key: string; name: string | null; positions: Position[];
  invested: number; equity: number; pnl: number;
};

/** Basket legs cluster under their basket; everything else stands alone. */
function groupPositions(positions: Position[]): Group[] {
  const groups = new Map<string, Group>();
  const singles: Position[] = [];
  for (const p of positions) {
    if (!p.group_id) { singles.push(p); continue; }
    const g = groups.get(p.group_id) ?? {
      key: p.group_id, name: p.group_name ?? "basket", positions: [],
      invested: 0, equity: 0, pnl: 0,
    };
    g.positions.push(p);
    g.invested += p.net_contributed;
    g.equity += p.value.equity;
    g.pnl += p.value.pnl;
    groups.set(p.group_id, g);
  }
  const out = [...groups.values()];
  if (singles.length) {
    out.unshift({
      key: "singles", name: null, positions: singles,
      invested: 0, equity: 0, pnl: 0,
    });
  }
  return out;
}
