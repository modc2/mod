"use client";

/**
 * The money surface. One component, every kind of investment.
 *
 * Whether you're backing a trader, depositing into a vault, or funding a whole
 * basket, the sequence is identical and deliberately short: **type an amount →
 * see exactly what it buys → press invest.** The preview is the point. Copy
 * trading has always been sold as a percentage ("mirror 10% of their size"),
 * which tells an investor nothing about their own money; here the number you
 * type is the number that gets deployed, and the panel shows the positions it
 * will open before you commit to any of them.
 *
 * Everything that isn't the decision — leverage ceiling, slippage, minimum
 * order, stop-loss — is real, and lives behind MORE. Defaults are chosen so
 * that someone who never opens it is still safe: exposure capped at 1× the
 * money invested, no matter how much leverage the trader they're copying runs.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  agentStatus, fmtUsd, fmtPct, shortAddr, vaultDetails, walletConfig,
  type WalletNetConfig,
} from "../lib/api";
import {
  invest, portfolio, previewInvest,
  type InvestMode, type Preview, type Risk,
} from "../lib/invest";
import { approveAgentFlow } from "../lib/hlActions";
import { useSession } from "../lib/auth";
import { useWallet } from "../lib/wallet";
import AuthGate from "./AuthGate";

type Kind = "trader" | "vault" | "strat";

const QUICK = [50, 100, 250, 1000];

export default function InvestPanel({
  kind,
  target,
  name,
  legs,
  onDone,
  compact = false,
}: {
  kind: Kind;
  /** Trader wallet, vault address, or strat id. */
  target: string;
  name?: string;
  /** For a strat: its legs, so the panel can preview the largest one. */
  legs?: { address: string; weight: number }[];
  onDone?: () => void;
  compact?: boolean;
}) {
  const router = useRouter();
  const wallet = useWallet();
  const { me, canWrite } = useSession();

  const [amount, setAmount] = useState("");
  const [mode, setMode] = useState<InvestMode>("live");
  const [risk, setRisk] = useState<Partial<Risk>>({ max_leverage: 1 });
  const [showMore, setShowMore] = useState(false);

  const [free, setFree] = useState<number | null>(null);
  const [vault, setVault] = useState<any>(null);
  const [cfg, setCfg] = useState<WalletNetConfig | null>(null);
  const [approved, setApproved] = useState<boolean | null>(null);
  const [approving, setApproving] = useState(false);

  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const amt = Number(amount) || 0;
  const label = name || shortAddr(target);

  // What this account can still put to work, and whether it has authorized
  // the agent that signs on its behalf.
  useEffect(() => {
    if (!me) { setFree(null); setApproved(null); return; }
    portfolio(me).then((p) => setFree(p.capacity.free)).catch(() => setFree(null));
    agentStatus(me).then((r) => setApproved(r.approved)).catch(() => setApproved(null));
  }, [me]);
  useEffect(() => { walletConfig().then(setCfg).catch(() => {}); }, []);
  useEffect(() => {
    if (kind !== "vault") return;
    vaultDetails(target, me ?? undefined).then(setVault).catch(() => {});
  }, [kind, target, me]);

  // Live preview of the sizing, debounced. For a basket we preview its
  // biggest leg at that leg's share — the honest representative slice.
  const previewTarget = useMemo(() => {
    if (kind === "trader") return { address: target, share: 1 };
    if (kind === "strat" && legs?.length) {
      const top = [...legs].sort((a, b) => b.weight - a.weight)[0];
      return { address: top.address, share: top.weight };
    }
    return null;
  }, [kind, target, legs]);

  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (!previewTarget || !(amt > 0)) { setPreview(null); return; }
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      setPreviewing(true);
      previewInvest(previewTarget.address, amt * previewTarget.share, {
        max_leverage: risk.max_leverage,
      })
        .then(setPreview)
        .catch(() => setPreview(null))
        .finally(() => setPreviewing(false));
    }, 400);
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, [previewTarget, amt, risk.max_leverage]);

  const enableTrading = async () => {
    if (!me || !cfg) return;
    setApproving(true); setErr(null);
    try {
      await approveAgentFlow(wallet, cfg, me);
      setApproved(true);
      setMsg("Trading enabled — one signature, done.");
    } catch (e: any) {
      setErr(e?.code === 4001 ? "You dismissed the signature request." : String(e?.message ?? e));
    } finally { setApproving(false); }
  };

  const submit = useCallback(async () => {
    if (!me) return;
    setErr(null); setMsg(null);
    if (!(amt > 0)) { setErr("Enter an amount first."); return; }
    setBusy(true);
    try {
      const res = await invest({
        investor: me,
        kind,
        ...(kind === "strat" ? { index_id: target } : { target }),
        amount_usd: amt,
        name,
        mode,
        risk,
      });
      setAmount("");
      onDone?.();
      const id = res.position?.id;
      if (id) router.push(`/invest/${id}`);
      else router.push(`/invest`);
    } catch (e: any) {
      setErr(String(e?.message ?? e).replace(/^\/invest \d+ /, ""));
    } finally { setBusy(false); }
  }, [me, amt, kind, target, name, mode, risk, onDone, router]);

  // ── vault-specific facts, straight from Hyperliquid ──
  const depositsOpen = kind !== "vault" || (vault?.allowDeposits !== false && !vault?.isClosed);
  const lockupDays = vault ? Math.round(Number(vault?.lockupPeriod ?? 0) / 86_400_000) : 0;
  const needsAgent = mode === "live" && approved === false;

  return (
    <div className={`panel ${compact ? "p-4" : "p-5"} space-y-4`}>
      {!compact && (
        <div>
          <div className="eyebrow">invest</div>
          <h2 className="text-[17px] font-semibold tracking-tight mt-0.5">
            {kind === "vault" ? `Put money into ${label}`
              : kind === "strat" ? `Back the ${label} basket`
              : `Back ${label}`}
          </h2>
          <p className="text-xs text-muted mt-1 max-w-xl">
            {kind === "vault"
              ? "Your USDC goes into the vault and its leader trades it. Hyperliquid does the accounting; you can take it out after the lockup."
              : kind === "strat"
              ? "Your money is split across this basket's traders by weight. Each one becomes its own position you can manage separately."
              : "Your own account holds what this trader holds, scaled to the amount you put in. Nothing is locked up — take it back whenever you want."}
          </p>
        </div>
      )}

      {/* ── the amount ── */}
      <div>
        <div className="label">amount</div>
        <div className="flex items-center gap-2 flex-wrap">
          <div className="relative">
            <span className="absolute left-3 top-1/2 -translate-y-1/2 text-muted text-sm">$</span>
            <input
              className="input num w-40 !pl-7 !text-[18px] !py-2"
              type="number" min={0} step={10} placeholder="0"
              value={amount} onChange={(e) => setAmount(e.target.value)} />
          </div>
          {QUICK.map((q) => (
            <button key={q} className={`btn ${amt === q ? "border-accent text-accent" : ""}`}
              onClick={() => setAmount(String(q))}>${q}</button>
          ))}
          {free != null && free > 1 && (
            <button className="btn" onClick={() => setAmount(String(Math.floor(free)))}>
              all ({fmtUsd(free)})
            </button>
          )}
        </div>
        {free != null && (
          <div className="text-[11px] text-muted mt-1.5">
            {free > 1
              ? <>You have <span className="text-ink num">{fmtUsd(free)}</span> free to invest.</>
              : <>No free balance — <a className="text-accent2 hover:text-accent" href="/wallet">add USDC</a> {kind !== "vault" && "or try this with paper money"}.</>}
          </div>
        )}
      </div>

      {/* ── what it buys ── */}
      {previewTarget && amt > 0 && (
        <PreviewCard preview={preview} loading={previewing} amount={amt} share={previewTarget.share} />
      )}
      {kind === "vault" && (
        <div className="rounded-lg border border-white/[0.07] bg-white/[0.02] p-3 text-xs space-y-1">
          {!depositsOpen && <div className="text-loss">This vault isn't taking deposits right now.</div>}
          {lockupDays > 0 && (
            <div className="text-muted">
              Lockup: <span className="text-ink">{lockupDays === 1 ? "1 day" : `${lockupDays} days`}</span> before you can withdraw.
            </div>
          )}
          <div className="text-muted">
            Hyperliquid reports this vault's own value and profit — nothing here is estimated.
          </div>
        </div>
      )}

      {/* ── paper / live ── */}
      {kind !== "vault" && (
        <div className="flex items-center gap-1">
          {(["live", "paper"] as const).map((m) => (
            <button key={m} onClick={() => setMode(m)}
              className={`btn ${mode === m ? "border-accent text-accent" : ""}`}>
              {m === "live" ? "real money" : "paper"}
            </button>
          ))}
          <span className="text-[11px] text-muted ml-2">
            {mode === "paper"
              ? "Tracked and priced live, but no orders are sent. Nothing at risk."
              : "Real orders in your own Hyperliquid account."}
          </span>
        </div>
      )}

      {/* ── the dials, for people who want them ── */}
      <div>
        <button className="text-[11px] uppercase tracking-wider text-muted hover:text-ink"
          onClick={() => setShowMore((v) => !v)}>
          {showMore ? "less" : "more"} · limits and safety
        </button>
        {showMore && (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-3">
            {kind !== "vault" && (
              <>
                <Dial label="max exposure" title="Gross position size as a multiple of what you invest. 1× means never hold more than your money, however much leverage the trader runs."
                  value={risk.max_leverage ?? 1} step={0.5} min={0.1} max={10}
                  suffix="×" onChange={(v) => setRisk({ ...risk, max_leverage: v })} />
                <Dial label="stop loss" title="Close everything automatically if this position is down by this much. 0 turns it off."
                  value={risk.stop_loss_pct ?? 0} step={5} min={0} max={100}
                  suffix="%" onChange={(v) => setRisk({ ...risk, stop_loss_pct: v })} />
                <Dial label="min trade" title="Ignore differences smaller than this. Hyperliquid's own floor is $10."
                  value={risk.min_order_usd ?? 12} step={1} min={10} max={1000}
                  prefix="$" onChange={(v) => setRisk({ ...risk, min_order_usd: v })} />
                <Dial label="slippage" title="How far past the mid an order may fill, in basis points."
                  value={risk.max_slippage_bps ?? 100} step={25} min={5} max={2000}
                  suffix="bps" onChange={(v) => setRisk({ ...risk, max_slippage_bps: v })} />
              </>
            )}
            {kind === "vault" && (
              <div className="col-span-full text-[11px] text-muted">
                A vault's leader sets its own limits — Hyperliquid enforces them, not this console.
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── the one action ── */}
      <div className="flex items-center gap-3 flex-wrap">
        <AuthGate action={kind === "vault" ? "invest in this vault" : "back this trader"}>
          {needsAgent ? (
            <button className="btn-primary" onClick={enableTrading} disabled={approving || !cfg}>
              {approving ? "check your wallet…" : "enable trading (1 signature)"}
            </button>
          ) : (
            <button className="btn-primary" onClick={submit}
              disabled={busy || !(amt > 0) || !depositsOpen}>
              {busy ? "working…"
                : mode === "paper" ? `invest ${fmtUsd(amt)} on paper`
                : `invest ${amt > 0 ? fmtUsd(amt) : ""}`.trim()}
            </button>
          )}
        </AuthGate>
        {needsAgent && canWrite && (
          <span className="text-[11px] text-muted max-w-sm">
            One signature lets this console place your orders. It can never move money
            out of your account — withdrawals always need your own signature.
          </span>
        )}
      </div>

      {msg && <div className="text-xs text-win">{msg}</div>}
      {err && <div className="text-xs text-loss break-words">{err}</div>}
    </div>
  );
}

/** "Your $250 buys this" — the whole reason the panel exists. */
function PreviewCard({
  preview, loading, amount, share,
}: { preview: Preview | null; loading: boolean; amount: number; share: number }) {
  if (loading && !preview) {
    return (
      <div className="rounded-lg border border-white/[0.07] bg-white/[0.02] p-3 space-y-2">
        <div className="skeleton h-3 w-40" />
        <div className="skeleton h-3 w-56" />
      </div>
    );
  }
  if (!preview) return null;

  const rows = preview.positions;
  const slice = share < 1 ? ` (its biggest leg, ${Math.round(share * 100)}% of your money)` : "";

  return (
    <div className="rounded-lg border border-accent/25 bg-accent/[0.04] p-3 space-y-2">
      <div className="flex items-baseline justify-between gap-2">
        <span className="eyebrow !mb-0">what {fmtUsd(amount)} buys{slice}</span>
        <span className="text-[10px] text-muted num">
          you'd be {preview.scale > 0 ? `${(preview.scale * 100).toFixed(3)}%` : "—"} of their book
        </span>
      </div>

      {rows.length === 0 ? (
        <div className="text-xs text-muted">{preview.note}</div>
      ) : (
        <>
          <div className="space-y-1">
            {rows.slice(0, 6).map((r) => (
              <div key={r.coin} className="flex items-center justify-between text-xs">
                <span className="flex items-center gap-2">
                  <span className={`pill !py-0 !px-1.5 ${r.side === "long" ? "text-win border-win/30" : "text-loss border-loss/30"}`}>
                    {r.side}
                  </span>
                  <span className="text-ink">{r.coin}</span>
                </span>
                <span className="num text-muted">
                  {Math.abs(r.size).toPrecision(3)} · <span className="text-ink">{fmtUsd(r.notional)}</span>
                </span>
              </div>
            ))}
            {rows.length > 6 && (
              <div className="text-[11px] text-muted">+ {rows.length - 6} more positions</div>
            )}
          </div>

          <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted pt-1 border-t border-white/[0.06]">
            <span>total exposure <span className="num text-ink">{fmtUsd(preview.gross)}</span></span>
            <span>that's <span className="num text-ink">{preview.leverage.toFixed(2)}×</span> your money</span>
            {preview.deleverage < 0.999 && (
              <span className="text-warn">
                trimmed to {Math.round(preview.deleverage * 100)}% of their shape — they run more
                leverage than your limit allows
              </span>
            )}
          </div>
        </>
      )}

      {preview.too_small.length > 0 && (
        <div className="text-[11px] text-warn">
          {preview.too_small.length} of their positions ({preview.too_small.map((t) => t.coin).join(", ")})
          would be under Hyperliquid's ${preview.min_order_usd} minimum at this size — you'd copy{" "}
          {fmtPct(preview.covered_pct, 0)} of their book. Invest more to cover them.
        </div>
      )}
    </div>
  );
}

function Dial({
  label, title, value, onChange, step, min, max, prefix = "", suffix = "",
}: {
  label: string; title: string; value: number; onChange: (v: number) => void;
  step: number; min: number; max: number; prefix?: string; suffix?: string;
}) {
  return (
    <label className="block" title={title}>
      <span className="label">{label}</span>
      <div className="flex items-center gap-1">
        {prefix && <span className="text-muted text-xs">{prefix}</span>}
        <input className="input num w-full" type="number" value={value}
          step={step} min={min} max={max}
          onChange={(e) => onChange(Number(e.target.value))} />
        {suffix && <span className="text-muted text-xs">{suffix}</span>}
      </div>
    </label>
  );
}
