"use client";

/**
 * One position, and everything true about it.
 *
 * The centre of this page is the side-by-side: **what you hold** against
 * **what they hold**. That table is the answer to the question every
 * copy-trading product dodges — "am I actually in the same trades?" — and the
 * gap between the two columns is exactly what the engine is about to trade.
 *
 * Money-moving buttons are one click with a typed amount and no modal, because
 * hiding "take my money back" behind a dialog is how a product loses trust.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ago, fmtPct, fmtPnl, fmtUsd, shortAddr } from "../../lib/api";
import {
  addToPosition, closePosition, explain, forgetPosition, pausePosition,
  position as fetchPosition, resumePosition, statusLabel, withdrawFromPosition,
  type PositionDetail,
} from "../../lib/invest";
import { Identicon, Kpi, Meter } from "../../components/BoardBits";
import AuthGate from "../../components/AuthGate";

export default function PositionPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [p, setP] = useState<PositionDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [amount, setAmount] = useState("");
  const [action, setAction] = useState<"add" | "withdraw" | null>(null);

  const load = useCallback(async () => {
    try { setP(await fetchPosition(String(id))); setErr(null); }
    catch (e: any) { setErr(String(e?.message ?? e)); }
  }, [id]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const h = setInterval(load, 10_000);
    return () => clearInterval(h);
  }, [load]);

  const run = async (name: string, fn: () => Promise<unknown>) => {
    setBusy(name); setErr(null);
    try { await fn(); await load(); setAmount(""); setAction(null); }
    catch (e: any) { setErr(String(e?.message ?? e)); }
    finally { setBusy(null); }
  };

  if (err && !p) return <div className="text-xs text-loss break-words">{err}</div>;
  if (!p) return <div className="text-xs text-muted">loading position…</div>;

  const isTrader = p.kind === "trader";
  const live = p.status === "active" || p.status === "closing";
  const amt = Number(amount) || 0;
  const rows = mergeLegs(p);

  return (
    <div className="max-w-4xl space-y-5">
      <Link href="/invest" className="text-[11px] text-muted hover:text-ink">← your positions</Link>

      {/* ── who ── */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="flex items-start gap-3 min-w-0">
          <Identicon address={p.target} size={38} />
          <div className="min-w-0">
            <h1 className="text-gradient text-[24px] font-bold tracking-tight leading-tight truncate">
              {p.name || shortAddr(p.target)}
            </h1>
            <div className="flex items-center gap-2 mt-1 flex-wrap">
              <span className={`pill !py-0 !px-2 text-[10px] ${
                p.mode === "paper" ? "text-warn border-warn/30"
                : p.kind === "vault" ? "text-accent2 border-accent2/30" : "text-muted"}`}>
                {p.mode === "paper" ? "paper money" : p.kind}
              </span>
              <span className="text-[11px] text-muted">{statusLabel(p)}</span>
              {p.group_name && (
                <span className="text-[11px] text-muted">
                  · part of <span className="text-ink">{p.group_name}</span>
                  {p.group_weight > 0 && ` (${Math.round(p.group_weight * 100)}%)`}
                </span>
              )}
              <Link href={isTrader ? `/trader/${p.target}` : `/vaults/${p.target}`}
                className="text-[11px] text-accent2 hover:text-accent">
                {isTrader ? "their record →" : "the vault →"}
              </Link>
            </div>
          </div>
        </div>
      </div>

      <p className="text-xs text-muted max-w-2xl">{explain(p)}</p>

      {p.last_error && (
        <div className="rounded-lg border border-loss/30 bg-loss/[0.06] px-3 py-2 text-xs text-loss">
          {p.last_error}
          {p.next_attempt_ms > Date.now() && (
            <span className="text-muted"> · retrying {ago(p.next_attempt_ms)}</span>
          )}
        </div>
      )}

      {/* ── the numbers ── */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Kpi label="worth now" value={fmtUsd(p.value.equity)}
          sub={p.value.authoritative ? "reported by Hyperliquid" : `last checked ${p.last_sync_ms ? ago(p.last_sync_ms) : "—"}`}
          tone={p.value.pnl >= 0 ? "win" : "loss"} />
        <Kpi label="profit" value={fmtPnl(p.value.pnl)}
          sub={p.net_contributed > 0 ? `${fmtPct(p.value.roi_pct, 1)} on what you put in` : "—"}
          tone={p.value.pnl >= 0 ? "win" : "loss"} />
        <Kpi label="you put in" value={fmtUsd(p.net_contributed)}
          sub={p.withdrawn_usd > 0 ? `${fmtUsd(p.contributed_usd)} in · ${fmtUsd(p.withdrawn_usd)} out` : "never withdrawn"} />
        <Kpi label="exposure" value={fmtUsd(p.value.exposure)}
          sub={isTrader ? `${p.value.leverage.toFixed(2)}× your money · limit ${p.risk.max_leverage}×` : "held inside the vault"}>
          {isTrader && <Meter pct={(p.value.leverage / Math.max(p.risk.max_leverage, 0.01)) * 100} />}
        </Kpi>
      </div>

      {isTrader && (
        <div className="text-[10px] text-muted">
          Profit is measured from the fills this position caused, marked at live prices — {p.value.basis_note}.
          {p.value.realized !== 0 && <> Realized so far: <span className="num">{fmtPnl(p.value.realized)}</span>.</>}
        </div>
      )}

      {/* ── money in / out ── */}
      {p.status !== "closed" && (
        <div className="panel p-4 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <AuthGate action="manage this position">
              <>
                <button className={`btn ${action === "add" ? "border-accent text-accent" : ""}`}
                  onClick={() => setAction(action === "add" ? null : "add")}>add money</button>
                <button className={`btn ${action === "withdraw" ? "border-accent text-accent" : ""}`}
                  onClick={() => setAction(action === "withdraw" ? null : "withdraw")}>take money out</button>
                {live ? (
                  <button className="btn" disabled={!!busy}
                    onClick={() => run("pause", () => pausePosition(p.id))}>
                    {busy === "pause" ? "pausing…" : "pause"}
                  </button>
                ) : p.status === "paused" ? (
                  <button className="btn" disabled={!!busy}
                    onClick={() => run("resume", () => resumePosition(p.id))}>
                    {busy === "resume" ? "resuming…" : "resume"}
                  </button>
                ) : null}
                <button className="btn-danger ml-auto" disabled={!!busy}
                  onClick={() => run("close", () => closePosition(p.id))}>
                  {busy === "close" ? "closing…" : "close position"}
                </button>
              </>
            </AuthGate>
          </div>

          {action && (
            <div className="flex flex-wrap items-center gap-2 pt-1">
              <div className="relative">
                <span className="absolute left-3 top-1/2 -translate-y-1/2 text-muted text-sm">$</span>
                <input className="input num w-36 !pl-7" type="number" min={0} step={10}
                  placeholder="0" value={amount} onChange={(e) => setAmount(e.target.value)} autoFocus />
              </div>
              {action === "withdraw" && (
                <button className="btn" onClick={() => setAmount(String(Math.floor(p.basis)))}>
                  all ({fmtUsd(p.basis)})
                </button>
              )}
              <button className="btn-primary" disabled={!!busy || !(amt > 0)}
                onClick={() => run(action, () => action === "add"
                  ? addToPosition(p.id, amt)
                  : withdrawFromPosition(p.id, amt))}>
                {busy ? "working…" : action === "add" ? `add ${fmtUsd(amt)}` : `take out ${fmtUsd(amt)}`}
              </button>
              <span className="text-[11px] text-muted">
                {action === "add"
                  ? isTrader
                    ? "Positions scale up on the next pass, within a few seconds."
                    : "Deposits come from your Hyperliquid balance."
                  : isTrader
                    ? "Your positions shrink to match — the money is free again immediately."
                    : "Withdrawals go back to your Hyperliquid balance, subject to the vault's lockup."}
              </span>
            </div>
          )}
        </div>
      )}

      {p.status === "closed" && (
        <div className="panel p-4 flex items-center justify-between gap-3">
          <span className="text-xs text-muted">
            Closed {ago(p.updated_ms)}. Final result: <span className={p.value.pnl >= 0 ? "text-win" : "text-loss"}>
              {fmtPnl(p.value.pnl)}</span>.
          </span>
          <button className="btn" disabled={!!busy}
            onClick={() => run("forget", async () => { await forgetPosition(p.id); router.push("/invest"); })}>
            remove from my list
          </button>
        </div>
      )}

      {/* ── you vs them ── */}
      {isTrader && (
        <div className="panel">
          <div className="px-4 py-2.5 border-b border-border flex items-center justify-between">
            <span className="eyebrow !mb-0">what you hold · what they hold</span>
            {p.leader && (
              <span className="text-[11px] text-muted num">
                you're {(p.leader.scale * 100).toFixed(3)}% of their {fmtUsd(p.leader.equity)} account
              </span>
            )}
          </div>
          {rows.length === 0 ? (
            <div className="px-4 py-8 text-center text-xs text-muted">
              {p.status === "paused"
                ? "Paused with nothing open."
                : "Nothing open yet — this trader is in cash, or the first pass hasn't run."}
            </div>
          ) : (
            <>
              <div className="grid grid-cols-[1fr_repeat(4,1fr)] gap-2 px-4 py-2 text-[10px] uppercase tracking-wider text-muted border-b border-border">
                <div>coin</div>
                <div className="text-right">your size</div>
                <div className="text-right">your value</div>
                <div className="text-right">target</div>
                <div className="text-right">profit</div>
              </div>
              {rows.map((r) => (
                <div key={r.coin} className="grid grid-cols-[1fr_repeat(4,1fr)] gap-2 px-4 py-2.5 items-center table-row">
                  <div className="flex items-center gap-2">
                    <span className={`pill !py-0 !px-1.5 text-[9px] ${
                      r.side === "long" ? "text-win border-win/30"
                      : r.side === "short" ? "text-loss border-loss/30" : "text-muted"}`}>
                      {r.side}
                    </span>
                    <span className="text-ink text-xs">{r.coin}</span>
                  </div>
                  <div className="num text-right text-xs">{r.size ? r.size.toPrecision(3) : "—"}</div>
                  <div className="num text-right text-xs">{r.notional ? fmtUsd(r.notional) : "—"}</div>
                  <div className="num text-right text-xs text-muted">
                    {r.target ? fmtUsd(r.target) : <span className="text-warn">exit</span>}
                    {r.drift && <span className="text-warn"> · {r.drift}</span>}
                  </div>
                  <div className={`num text-right text-xs ${r.unrealized >= 0 ? "text-win" : "text-loss"}`}>
                    {r.notional ? fmtPnl(r.unrealized) : "—"}
                  </div>
                </div>
              ))}
            </>
          )}
        </div>
      )}

      {/* ── trades ── */}
      {p.fills.length > 0 && (
        <div className="panel">
          <div className="px-4 py-2.5 border-b border-border eyebrow !mb-0">
            trades this position made
          </div>
          <div className="max-h-80 overflow-y-auto">
            {p.fills.slice(0, 60).map((f, i) => (
              <div key={i} className="grid grid-cols-[auto_1fr_repeat(3,1fr)] gap-2 px-4 py-2 items-center table-row text-xs">
                <span className="text-[10px] text-muted w-16">{ago(f.ts_ms)}</span>
                <span className="flex items-center gap-2">
                  <span className={f.side === "buy" ? "text-win" : "text-loss"}>{f.side}</span>
                  <span className="text-ink">{f.coin}</span>
                  {!f.live && <span className="pill !py-0 !px-1 text-[9px] text-warn border-warn/30">paper</span>}
                </span>
                <span className="num text-right text-muted">{f.size.toPrecision(3)}</span>
                <span className="num text-right text-muted">@ {f.price.toPrecision(6)}</span>
                <span className={`num text-right ${f.realized === 0 ? "text-muted" : f.realized > 0 ? "text-win" : "text-loss"}`}>
                  {f.realized === 0 ? fmtUsd(f.notional) : fmtPnl(f.realized)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── history ── */}
      {(p.events.length > 0 || p.flows.length > 0) && (
        <div className="panel">
          <div className="px-4 py-2.5 border-b border-border eyebrow !mb-0">history</div>
          <div className="max-h-64 overflow-y-auto">
            {timeline(p).slice(0, 40).map((e, i) => (
              <div key={i} className="px-4 py-2 flex items-baseline gap-3 table-row text-xs">
                <span className="text-[10px] text-muted w-16 shrink-0">{ago(e.ts)}</span>
                <span className={e.tone === "loss" ? "text-loss" : e.tone === "win" ? "text-win" : "text-muted"}>
                  {e.text}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {err && <div className="text-xs text-loss break-words">{err}</div>}
    </div>
  );
}

// ─── you vs them ────────────────────────────────────────────────────────

type MergedLeg = {
  coin: string; side: "long" | "short" | "flat";
  size: number; notional: number; unrealized: number;
  target: number; drift: string | null;
};

/** Union of what the sleeve holds and what it is aiming at, so a coin the
 *  trader has entered but you haven't yet is visible rather than absent. */
function mergeLegs(p: PositionDetail): MergedLeg[] {
  const targets = new Map((p.leader?.targets ?? []).map((t) => [t.coin, t]));
  const coins = new Set<string>([...p.legs.map((l) => l.coin), ...targets.keys()]);
  const out: MergedLeg[] = [];
  for (const coin of coins) {
    const leg = p.legs.find((l) => l.coin === coin);
    const t = targets.get(coin);
    const size = leg?.size ?? 0;
    const notional = leg?.notional ?? 0;
    const target = t ? Math.abs(t.notional) : 0;
    const gap = target - notional;
    out.push({
      coin,
      side: size > 0 ? "long" : size < 0 ? "short" : t ? (t.size > 0 ? "long" : "short") : "flat",
      size,
      notional,
      unrealized: leg?.unrealized ?? 0,
      target,
      drift: Math.abs(gap) >= Math.max(p.risk.min_order_usd, 1)
        ? `${gap > 0 ? "buying" : "selling"} ${fmtUsd(Math.abs(gap))}`
        : null,
    });
  }
  return out.sort((a, b) => Math.max(b.notional, b.target) - Math.max(a.notional, a.target));
}

function timeline(p: PositionDetail) {
  const flows = p.flows.map((f) => ({
    ts: f.ts_ms,
    tone: f.dir === "in" ? "win" : "muted",
    text: `${f.dir === "in" ? "added" : "took out"} ${fmtUsd(f.amount_usd)}${f.note ? ` — ${f.note}` : ""}`,
  }));
  const events = p.events.map((e) => ({
    ts: e.ts_ms,
    tone: e.kind === "error" || e.kind === "order-failed" ? "loss" : e.kind === "stop" ? "loss" : "muted",
    text: e.text,
  }));
  return [...flows, ...events].sort((a, b) => b.ts - a.ts);
}
