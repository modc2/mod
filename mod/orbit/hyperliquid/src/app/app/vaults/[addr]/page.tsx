"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { vaultDetails, vaultTransfer, fmtUsd, fmtPnl, fmtPct, shortAddr } from "../../lib/api";
import { useWallet } from "../../lib/wallet";

export default function VaultDetailPage() {
  const params = useParams();
  const addr = String(params.addr || "").toLowerCase();
  const { address: eoa } = useWallet();

  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [amount, setAmount] = useState("");
  const [mode, setMode] = useState<"deposit" | "withdraw">("deposit");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      setD(await vaultDetails(addr, eoa ?? undefined));
    } catch (e: any) { setErr(e.message ?? String(e)); }
    finally { setLoading(false); }
  }, [addr, eoa]);

  useEffect(() => { load(); }, [load]);

  const fs = d?.followerState;
  const myEquity = fs ? Number(fs.vaultEquity) : 0;
  const myPnl = fs ? Number(fs.pnl) : 0;
  const maxWithdraw = Number(d?.maxWithdrawable ?? 0);
  const lockupUntil = fs ? Number(fs.lockupUntil) : 0;
  const locked = lockupUntil > Date.now();

  const submit = async () => {
    setMsg(null); setErr(null);
    if (!eoa) { setErr("Connect your wallet first."); return; }
    const amt = Number(amount);
    if (!(amt > 0)) { setErr("Enter an amount in USD."); return; }
    if (mode === "withdraw" && amt > maxWithdraw + 1e-9) {
      setErr(`Max withdrawable right now is ${fmtUsd(maxWithdraw)}.`); return;
    }
    setBusy(true);
    try {
      const res = await vaultTransfer({ eoa, vault: addr, is_deposit: mode === "deposit", amount_usd: amt });
      if (res?.status === "err" || res?.error) {
        setErr(typeof res.error === "string" ? res.error : JSON.stringify(res));
      } else {
        setMsg(`${mode === "deposit" ? "Deposited" : "Withdrew"} ${fmtUsd(amt)} — ${mode === "deposit" ? "into" : "from"} ${d?.name || shortAddr(addr)}.`);
        setAmount("");
      }
      await load();
    } catch (e: any) { setErr(e.message ?? String(e)); }
    finally { setBusy(false); }
  };

  if (loading && !d) return <div className="text-xs text-muted">loading vault…</div>;

  const apr = Number(d?.apr ?? 0) * 100; // detail endpoint returns a fraction
  const commission = Number(d?.leaderCommission ?? 0) * 100;
  const depositsOpen = d?.allowDeposits !== false && !d?.isClosed;

  return (
    <div className="max-w-3xl space-y-5">
      <Link href="/vaults" className="text-[11px] text-muted hover:text-ink">← all vaults</Link>

      <div>
        <h1 className="text-xl text-ink">{d?.name || shortAddr(addr)}</h1>
        {d?.description && <p className="text-xs text-muted mt-1">{d.description}</p>}
        <div className="text-[11px] text-muted mt-1">
          leader <Link href={`/trader/${d?.leader}`} className="font-mono text-accent2 hover:text-accent">{shortAddr(d?.leader || "")}</Link>
          {" · "}<span className="font-mono">{shortAddr(addr)}</span>
        </div>
      </div>

      {/* Stats */}
      <div className="panel p-4 grid grid-cols-2 sm:grid-cols-4 gap-4">
        <Stat label="APR" value={`${apr >= 0 ? "+" : ""}${fmtPct(apr, 0)}`} cls={apr >= 0 ? "text-win" : "text-loss"} />
        <Stat label="TVL" value={fmtUsd(Number(d?.maxDistributable ?? 0))} />
        <Stat label="leader fee" value={fmtPct(commission, 0)} />
        <Stat label="deposits" value={depositsOpen ? "open" : "closed"} cls={depositsOpen ? "text-win" : "text-loss"} />
      </div>

      {/* Your position */}
      {eoa && (
        <div className="panel p-4">
          <div className="label">your position</div>
          {myEquity > 0 ? (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mt-2">
              <Stat label="equity" value={fmtUsd(myEquity)} />
              <Stat label="pnl" value={fmtPnl(myPnl)} cls={myPnl >= 0 ? "text-win" : "text-loss"} />
              <Stat label="withdrawable" value={fmtUsd(maxWithdraw)} />
              <Stat label="lockup"
                value={locked ? `until ${new Date(lockupUntil).toLocaleDateString()}` : "none"}
                cls={locked ? "text-warn" : "text-muted"} />
            </div>
          ) : (
            <p className="text-xs text-muted mt-1">You have no deposit in this vault yet.</p>
          )}
        </div>
      )}

      {/* Invest / withdraw */}
      <div className="panel p-5 space-y-4">
        <div className="flex gap-1">
          <button onClick={() => setMode("deposit")}
            className={`btn ${mode === "deposit" ? "border-accent text-accent" : ""}`}>invest</button>
          <button onClick={() => setMode("withdraw")}
            className={`btn ${mode === "withdraw" ? "border-accent text-accent" : ""}`}>withdraw</button>
        </div>

        {!eoa ? (
          <p className="text-xs text-muted">Connect your wallet (top right) to invest.</p>
        ) : (
          <>
            <div>
              <div className="label">amount (USDC)</div>
              <div className="flex gap-2 items-center">
                <input className="input w-48 num" type="number" min={0} step={10} placeholder="0.00"
                  value={amount} onChange={(e) => setAmount(e.target.value)} />
                {mode === "withdraw" && maxWithdraw > 0 && (
                  <button className="btn" onClick={() => setAmount(String(maxWithdraw))}>max</button>
                )}
              </div>
            </div>

            {mode === "deposit" && !depositsOpen &&
              <div className="text-xs text-loss">This vault is not accepting deposits.</div>}
            {mode === "withdraw" && locked &&
              <div className="text-xs text-warn">Funds are locked until {new Date(lockupUntil).toLocaleString()}.</div>}

            <button className="btn-primary" onClick={submit}
              disabled={busy || (mode === "deposit" && !depositsOpen)}>
              {busy ? "submitting…" : mode === "deposit" ? "invest" : "withdraw"}
            </button>

            <p className="text-[10px] text-muted">
              Signed by your backend agent wallet. If this fails with an auth error, approve the agent
              once under <Link href="/live" className="text-accent2 hover:text-accent">Live</Link>.
            </p>
          </>
        )}

        {msg && <div className="text-xs text-win">{msg}</div>}
        {err && <div className="text-xs text-loss break-words">{err}</div>}
      </div>
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
