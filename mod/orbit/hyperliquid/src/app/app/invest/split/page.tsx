"use client";

/**
 * Several traders, a dollar amount on each.
 *
 * A basket splits ONE amount by weight; this is the other way round — you
 * say "$300 on this one, $1,000 on that one" and every row is checked against
 * that exact number before anything happens: what those dollars would have
 * done over the window, and every one of the trader's trades replayed at your
 * size (the same server-side replay as the trader page, data checks
 * included). Pressing invest opens one trader position per row, each its own
 * sleeve you can add to, pause or close later.
 *
 * The plan lives in this browser (localStorage) so it survives a reload while
 * you shop around; `?add=0x…` appends a trader from anywhere in the console.
 */

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  agentStatus, backtestTrader, fetchTopTraders, fmtPct, fmtPnl, fmtUsd, shortAddr, walletConfig,
  type TopTrader, type TraderBacktest, type WalletNetConfig,
} from "../../lib/api";
import { invest, portfolio, type InvestMode } from "../../lib/invest";
import { approveAgentFlow } from "../../lib/hlActions";
import { useSession } from "../../lib/auth";
import { useWallet } from "../../lib/wallet";
import AuthGate from "../../components/AuthGate";
import CopyTrades from "../../components/CopyTrades";
import { Identicon, PageHead } from "../../components/BoardBits";

type Row = { address: string; usd: number };
type Result = { ok: boolean; text: string; id?: string };

const KEY = "hl.invest.split";
const WINDOWS = [7, 30, 90];
const isAddr = (s: string) => /^0x[a-fA-F0-9]{40}$/.test(s.trim());

function loadRows(): Row[] {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || "[]");
    return Array.isArray(v) ? v.filter((r) => r && isAddr(r.address)).map((r) => ({ address: r.address.toLowerCase(), usd: Number(r.usd) || 0 })) : [];
  } catch { return []; }
}

export default function SplitPage() {
  return (
    <Suspense fallback={<div className="text-xs text-muted">loading…</div>}>
      <Split />
    </Suspense>
  );
}

function Split() {
  const params = useSearchParams();
  const wallet = useWallet();
  const { me } = useSession();

  const [rows, setRows] = useState<Row[]>([]);
  const [ready, setReady] = useState(false);
  const [days, setDays] = useState(30);
  const [mode, setMode] = useState<InvestMode>("paper");
  const [tests, setTests] = useState<Record<string, TraderBacktest | "loading" | { error: string }>>({});
  const [open, setOpen] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, Result>>({});
  const [busy, setBusy] = useState(false);
  const [free, setFree] = useState<number | null>(null);
  const [approved, setApproved] = useState<boolean | null>(null);
  const [cfg, setCfg] = useState<WalletNetConfig | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // Restore the plan, then fold in ?add=0x… (once).
  useEffect(() => {
    let r = loadRows();
    const add = params.get("add");
    if (add && isAddr(add) && !r.some((x) => x.address === add.toLowerCase())) {
      r = [...r, { address: add.toLowerCase(), usd: Number(params.get("usd")) || 100 }];
    }
    setRows(r);
    setReady(true);
  }, [params]);
  useEffect(() => { if (ready) localStorage.setItem(KEY, JSON.stringify(rows)); }, [rows, ready]);

  useEffect(() => {
    if (!me) { setFree(null); setApproved(null); return; }
    portfolio(me).then((p) => setFree(p.capacity.free)).catch(() => setFree(null));
    agentStatus(me).then((r) => setApproved(r.approved)).catch(() => setApproved(null));
  }, [me]);
  useEffect(() => { walletConfig().then(setCfg).catch(() => {}); }, []);

  // Backtest every row at ITS dollar amount (debounced per row).
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const testKey = (r: Row) => `${r.address}:${r.usd}:${days}`;
  useEffect(() => {
    for (const r of rows) {
      const k = testKey(r);
      if (!(r.usd > 0) || tests[k]) continue;
      clearTimeout(timers.current[r.address]);
      timers.current[r.address] = setTimeout(() => {
        setTests((t) => ({ ...t, [k]: "loading" }));
        backtestTrader(r.address, r.usd, days)
          .then((b) => setTests((t) => ({ ...t, [k]: b })))
          .catch((e) => setTests((t) => ({ ...t, [k]: { error: String(e?.message ?? e) } })));
      }, 450);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, days]);

  const testOf = (r: Row) => tests[testKey(r)];
  const total = rows.reduce((a, r) => a + (r.usd > 0 ? r.usd : 0), 0);
  const summary = useMemo(() => {
    let pnl = 0, value = 0, counted = 0, failed = 0, trades = 0;
    for (const r of rows) {
      const t = tests[testKey(r)];
      if (!t || t === "loading" || "error" in t || !t.available) continue;
      counted++; pnl += t.pnl; value += t.final_value; trades += t.trades_total ?? t.trades?.length ?? 0;
      if (!t.ok) failed++;
    }
    return { pnl, value, counted, failed, trades };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, tests, days]);

  const add = (address: string, usd = 100) => {
    const a = address.trim().toLowerCase();
    if (!isAddr(a)) return;
    setRows((rs) => rs.some((r) => r.address === a) ? rs : [...rs, { address: a, usd }]);
  };
  const setUsd = (address: string, usd: number) =>
    setRows((rs) => rs.map((r) => (r.address === address ? { ...r, usd } : r)));
  const remove = (address: string) => setRows((rs) => rs.filter((r) => r.address !== address));

  const needsAgent = mode === "live" && approved === false;
  const over = mode === "live" && free != null && total > free + 1e-6;

  const enableTrading = async () => {
    if (!me || !cfg) return;
    setErr(null);
    try { await approveAgentFlow(wallet, cfg, me); setApproved(true); }
    catch (e: any) { setErr(e?.code === 4001 ? "You dismissed the signature request." : String(e?.message ?? e)); }
  };

  const investAll = useCallback(async () => {
    if (!me) return;
    setBusy(true); setErr(null);
    const out: Record<string, Result> = {};
    for (const r of rows) {
      if (!(r.usd > 0) || results[r.address]?.ok) continue;
      try {
        const res = await invest({ investor: me, kind: "trader", target: r.address, amount_usd: r.usd, mode });
        out[r.address] = { ok: true, text: `${fmtUsd(r.usd)} ${mode === "paper" ? "on paper" : "invested"}`, id: res.position?.id };
      } catch (e: any) {
        out[r.address] = { ok: false, text: String(e?.message ?? e).replace(/^\/invest \d+ /, "") };
      }
      setResults((x) => ({ ...x, ...out }));
    }
    setBusy(false);
    portfolio(me).then((p) => setFree(p.capacity.free)).catch(() => {});
  }, [me, rows, mode, results]);

  const done = rows.length > 0 && rows.every((r) => results[r.address]?.ok);

  return (
    <section className="space-y-5 max-w-4xl">
      <PageHead
        title="Several traders, $ on each"
        blurb="Put a dollar amount on every trader you want to back. Each row shows what exactly that amount would have done — every one of their trades at your size — before you commit."
        right={<Link href="/invest" className="hover:text-ink">your positions →</Link>}
      />

      <div className="flex items-center gap-3 flex-wrap text-[11px]">
        <span className="text-muted uppercase tracking-wider">check over</span>
        {WINDOWS.map((d) => (
          <button key={d} onClick={() => setDays(d)}
            className={`pill !py-0.5 ${days === d ? "border-accent/50 text-accent" : "text-muted"}`}>{d}d</button>
        ))}
        <span className="flex-1" />
        {(["paper", "live"] as const).map((m) => (
          <button key={m} onClick={() => setMode(m)}
            className={`btn ${mode === m ? "border-accent text-accent" : ""}`}>
            {m === "live" ? "real money" : "paper"}
          </button>
        ))}
      </div>

      <AddRow onAdd={add} have={rows.map((r) => r.address)} />

      {rows.length === 0 ? (
        <div className="panel p-6 text-center text-xs text-muted">
          No traders yet — paste an address above or pick from the top list.
        </div>
      ) : (
        <div className="panel divide-y divide-white/[0.05]">
          {rows.map((r) => {
            const t = testOf(r);
            const bt = t && t !== "loading" && !("error" in t) ? t : null;
            const res = results[r.address];
            const isOpen = open === r.address;
            return (
              <div key={r.address}>
                <div className="px-4 py-3 grid grid-cols-[1.4fr_7.5rem_1fr_1fr_1fr_7.5rem] gap-3 items-center">
                  <Link href={`/trader/${r.address}`} className="flex items-center gap-2 min-w-0 hover:text-accent">
                    <Identicon address={r.address} size={22} />
                    <span className="font-mono text-xs truncate">{shortAddr(r.address)}</span>
                  </Link>
                  <div className="relative">
                    <span className="absolute left-2 top-1/2 -translate-y-1/2 text-muted text-xs">$</span>
                    <input className="input num w-full !pl-5 text-sm" type="number" min={0} step={10}
                      value={r.usd || ""} placeholder="0" disabled={busy || res?.ok}
                      onChange={(e) => setUsd(r.address, Math.max(0, Number(e.target.value) || 0))} />
                  </div>
                  <Cell label={`would be now (${days}d)`}
                    value={t === "loading" ? "…" : bt?.available ? fmtUsd(bt.final_value) : "—"}
                    tone={bt?.available ? (bt.pnl >= 0 ? "win" : "loss") : undefined} />
                  <Cell label="net pnl"
                    value={t === "loading" ? "…" : bt?.available ? `${fmtPnl(bt.pnl)}` : "—"}
                    sub={bt?.available ? fmtPct(bt.roi_pct, 1) : undefined}
                    tone={bt?.available ? (bt.pnl >= 0 ? "win" : "loss") : undefined} />
                  <Cell label="their trades"
                    value={bt?.available ? String(bt.trades_total ?? bt.trades?.length ?? 0) : "—"}
                    sub={bt?.available ? (bt.ok ? "checks pass" : "checks FAILED") : t && t !== "loading" && "error" in t ? "error" : bt ? "no history" : undefined}
                    tone={bt?.available && !bt.ok ? "warn" : undefined} />
                  <div className="flex items-center gap-1">
                    <button className="btn-ghost" disabled={!bt?.available}
                      onClick={() => setOpen(isOpen ? null : r.address)}>{isOpen ? "hide" : "trades"}</button>
                    <button className="btn-ghost hover:text-loss" disabled={busy}
                      aria-label="remove" onClick={() => remove(r.address)}>×</button>
                  </div>
                </div>
                {res && (
                  <div className={`px-4 pb-2 text-[11px] ${res.ok ? "text-win" : "text-loss"}`}>
                    {res.text}
                    {res.id && <> · <Link href={`/invest/${res.id}`} className="text-accent2 hover:text-accent">open position →</Link></>}
                  </div>
                )}
                {bt && !bt.available && bt.note && (
                  <div className="px-4 pb-2 text-[11px] text-muted">can&apos;t replay this wallet — {bt.note}</div>
                )}
                {isOpen && bt?.available && (
                  <div className="border-t border-border bg-white/[0.01]">
                    {!bt.ok && (
                      <div className="px-4 pt-2 text-[11px] text-warn">
                        {bt.checks.filter((c) => c.status === "fail").map((c) => c.detail).join(" · ")}
                      </div>
                    )}
                    <CopyTrades trades={bt.trades ?? []} total={bt.trades_total} capital={r.usd} />
                  </div>
                )}
              </div>
            );
          })}

          {/* totals */}
          <div className="px-4 py-3 grid grid-cols-[1.4fr_7.5rem_1fr_1fr_1fr_7.5rem] gap-3 items-center bg-white/[0.02]">
            <span className="text-[11px] uppercase tracking-wider text-muted">{rows.length} trader{rows.length === 1 ? "" : "s"}</span>
            <span className="num text-sm text-ink">{fmtUsd(total)}</span>
            <Cell label="all of it now" value={summary.counted ? fmtUsd(summary.value) : "—"}
              tone={summary.pnl >= 0 ? "win" : "loss"} />
            <Cell label="net pnl" value={summary.counted ? fmtPnl(summary.pnl) : "—"}
              sub={summary.counted && total > 0 ? fmtPct((summary.pnl / total) * 100, 1) : undefined}
              tone={summary.pnl >= 0 ? "win" : "loss"} />
            <Cell label="trades" value={String(summary.trades)}
              sub={summary.failed ? `${summary.failed} failed checks` : undefined}
              tone={summary.failed ? "warn" : undefined} />
            <span />
          </div>
        </div>
      )}

      {rows.length > 0 && (
        <div className="panel p-4 space-y-3">
          <div className="text-[11px] text-muted">
            {mode === "paper"
              ? "Paper: each trader gets its own position, tracked and priced live — no orders, no money."
              : <>Real money: each trader becomes its own sleeve in your Hyperliquid account, sized to its dollar amount.
                {free != null && <> Free to invest: <span className="num text-ink">{fmtUsd(free)}</span>.</>}</>}
          </div>
          {over && <div className="text-[11px] text-warn">That&apos;s {fmtUsd(total - (free ?? 0))} more than your free balance — lower some amounts or deposit first.</div>}
          <AuthGate action="invest">
            {needsAgent ? (
              <button className="btn-primary" onClick={enableTrading} disabled={!cfg}>enable trading (one signature)</button>
            ) : (
              <button className="btn-primary" onClick={investAll}
                disabled={busy || done || total <= 0 || over}>
                {busy ? "investing…" : done ? "all invested"
                  : `invest ${fmtUsd(total)} across ${rows.filter((r) => r.usd > 0).length} traders${mode === "paper" ? " on paper" : ""}`}
              </button>
            )}
          </AuthGate>
          {err && <div className="text-[11px] text-loss">{err}</div>}
          <p className="text-[10px] text-muted">
            The check replays each trader&apos;s last {days} days at your amount: their equity curve
            scaled to your dollars (net of a copy&apos;s taker fees), and every fill at your size.
            A replay of the past is not a forecast.
          </p>
        </div>
      )}
    </section>
  );
}

function Cell({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: "win" | "loss" | "warn" }) {
  const cls = tone === "win" ? "text-win" : tone === "loss" ? "text-loss" : tone === "warn" ? "text-warn" : "text-ink";
  return (
    <span className="text-right min-w-0">
      <span className="block text-[9px] uppercase tracking-wider text-muted truncate">{label}</span>
      <span className={`block num text-xs ${cls}`}>{value}</span>
      {sub && <span className={`block text-[10px] num ${tone === "warn" ? "text-warn" : "text-muted"}`}>{sub}</span>}
    </span>
  );
}

function AddRow({ onAdd, have }: { onAdd: (a: string, usd?: number) => void; have: string[] }) {
  const [v, setV] = useState("");
  const [usd, setUsd] = useState("100");
  const [top, setTop] = useState<TopTrader[] | null>(null);
  const [showTop, setShowTop] = useState(false);
  useEffect(() => {
    if (showTop && !top) fetchTopTraders(7, 1, 25).then((r) => setTop(r.traders)).catch(() => setTop([]));
  }, [showTop, top]);
  const ok = isAddr(v);
  return (
    <div className="panel">
      <div className="px-4 py-3 flex items-center gap-2 flex-wrap">
        <input className="input font-mono text-xs flex-1 min-w-[240px]" placeholder="paste a trader's 0x… address"
          value={v} onChange={(e) => setV(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && ok) { onAdd(v, Number(usd) || 100); setV(""); } }} />
        <div className="relative">
          <span className="absolute left-2 top-1/2 -translate-y-1/2 text-muted text-xs">$</span>
          <input className="input num w-24 !pl-5 text-xs" type="number" min={0} value={usd} onChange={(e) => setUsd(e.target.value)} />
        </div>
        <button className="btn" disabled={!ok} onClick={() => { onAdd(v, Number(usd) || 100); setV(""); }}>add</button>
        <button className="btn-ghost" onClick={() => setShowTop((s) => !s)}>{showTop ? "hide top traders" : "pick from top traders"}</button>
      </div>
      {showTop && (
        <div className="border-t border-border max-h-72 overflow-y-auto">
          {!top && <div className="px-4 py-3"><div className="skeleton h-4 w-full" /></div>}
          {top?.slice(0, 15).map((t) => {
            const added = have.includes(t.address.toLowerCase());
            return (
              <button key={t.address} disabled={added} onClick={() => onAdd(t.address, Number(usd) || 100)}
                className="w-full px-4 py-2 grid grid-cols-[1.4fr_repeat(3,1fr)_auto] gap-3 items-center text-left hover:bg-accent/[0.04] disabled:opacity-50">
                <span className="flex items-center gap-2 min-w-0">
                  <Identicon address={t.address} size={18} />
                  <span className="font-mono text-xs truncate">{shortAddr(t.address)}</span>
                </span>
                <Cell label="7d return" value={fmtPct(t.roi ?? 0, 0)} tone={(t.roi ?? 0) >= 0 ? "win" : "loss"} />
                <Cell label="7d profit" value={fmtPnl(t.pnl ?? 0)} tone={(t.pnl ?? 0) >= 0 ? "win" : "loss"} />
                <Cell label="account" value={fmtUsd(t.account_value ?? 0)} />
                <span className="text-[11px] text-accent2">{added ? "added" : `+ $${Number(usd) || 100}`}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
