"use client";

/**
 * Your money in one Hyperliquid vault.
 *
 * Investing runs through the book (`InvestPanel` → `POST /invest`), so a vault
 * deposit shows up on the Invest page next to everything else you own. Taking
 * money out does *not* go through the book, deliberately: Hyperliquid is the
 * authority on what you hold in a vault, and you may well have deposited into
 * one before this console existed. The withdraw side therefore talks straight
 * to `vaultTransfer` against whatever `followerState` reports, so no position
 * can ever become unreachable just because we have no row for it.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { fmtPnl, fmtUsd, shortAddr, vaultDetails, vaultTransfer } from "../lib/api";
import { portfolio, type Position } from "../lib/invest";
import { useSession } from "../lib/auth";
import InvestPanel from "./InvestPanel";
import AuthGate from "./AuthGate";

export default function VaultTransferPanel({ vault, vaultName }: { vault: string; vaultName?: string }) {
  const { me } = useSession();

  const [d, setD] = useState<any>(null);
  const [held, setHeld] = useState<Position | null>(null);
  const [amount, setAmount] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try { setD(await vaultDetails(vault, me ?? undefined)); } catch { /* stats are optional */ }
    if (!me) { setHeld(null); return; }
    try {
      const book = await portfolio(me);
      setHeld(book.positions.find(
        (p) => p.kind === "vault" && p.target.toLowerCase() === vault.toLowerCase()) ?? null);
    } catch { setHeld(null); }
  }, [vault, me]);

  useEffect(() => { load(); }, [load]);

  const fs = d?.followerState;
  const myEquity = fs ? Number(fs.vaultEquity) : 0;
  const myPnl = fs ? Number(fs.pnl) : 0;
  const maxWithdraw = Number(d?.maxWithdrawable ?? 0);
  const lockupUntil = fs ? Number(fs.lockupUntil) : 0;
  const locked = lockupUntil > Date.now();
  const name = vaultName || d?.name || shortAddr(vault);

  const takeOut = async () => {
    setMsg(null); setErr(null);
    const amt = Number(amount);
    if (!(amt > 0)) { setErr("Enter an amount."); return; }
    if (amt > maxWithdraw + 1e-9) {
      setErr(`Hyperliquid will only release ${fmtUsd(maxWithdraw)} right now.`); return;
    }
    setBusy(true);
    try {
      const res = await vaultTransfer({ eoa: me!, vault, is_deposit: false, amount_usd: amt });
      if (res?.status === "err" || res?.error) {
        setErr(typeof res.error === "string" ? res.error : JSON.stringify(res));
      } else {
        setMsg(`Withdrew ${fmtUsd(amt)} from ${name} — it's back in your Hyperliquid balance.`);
        setAmount("");
      }
      await load();
    } catch (e: any) { setErr(e.message ?? String(e)); }
    finally { setBusy(false); }
  };

  return (
    <div className="space-y-4">
      {/* What you already hold here, per Hyperliquid itself. */}
      {me && myEquity > 0 && (
        <div className="panel p-4 space-y-3">
          <div className="flex items-center justify-between gap-3">
            <span className="eyebrow !mb-0">your position in this vault</span>
            {held && (
              <Link href={`/invest/${held.id}`} className="text-[11px] text-accent2 hover:text-accent">
                manage →
              </Link>
            )}
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <Stat label="worth now" value={fmtUsd(myEquity)} />
            <Stat label="profit" value={fmtPnl(myPnl)} cls={myPnl >= 0 ? "text-win" : "text-loss"} />
            <Stat label="you can take out" value={fmtUsd(maxWithdraw)} />
            <Stat label="lockup"
              value={locked ? `until ${new Date(lockupUntil).toLocaleDateString()}` : "none"}
              cls={locked ? "text-warn" : "text-muted"} />
          </div>

          <div className="flex flex-wrap items-center gap-2 pt-1">
            <AuthGate action="withdraw from this vault">
              <>
                <div className="relative">
                  <span className="absolute left-3 top-1/2 -translate-y-1/2 text-muted text-sm">$</span>
                  <input className="input num w-36 !pl-7" type="number" min={0} step={10}
                    placeholder="0" value={amount} onChange={(e) => setAmount(e.target.value)} />
                </div>
                {maxWithdraw > 0 && (
                  <button className="btn" onClick={() => setAmount(String(maxWithdraw))}>all</button>
                )}
                <button className="btn" onClick={takeOut} disabled={busy || maxWithdraw <= 0}>
                  {busy ? "working…" : "take money out"}
                </button>
              </>
            </AuthGate>
            {locked && (
              <span className="text-[11px] text-warn">
                Locked until {new Date(lockupUntil).toLocaleString()}.
              </span>
            )}
          </div>

          {msg && <div className="text-xs text-win">{msg}</div>}
          {err && <div className="text-xs text-loss break-words">{err}</div>}
        </div>
      )}

      {/* Putting money in — the same panel used everywhere else. */}
      <InvestPanel kind="vault" target={vault} name={name} onDone={load} />
    </div>
  );
}

function Stat({ label, value, cls = "text-ink" }: { label: string; value: string; cls?: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-muted">{label}</div>
      <div className={`num text-sm mt-0.5 ${cls}`}>{value}</div>
    </div>
  );
}
