"use client";

import { useCallback, useEffect, useState } from "react";
import {
  agentStatus, vaultDetails, vaultTransfer, walletConfig,
  fmtUsd, fmtPnl, shortAddr, type WalletNetConfig,
} from "../lib/api";
import { approveAgentFlow } from "../lib/hlActions";
import { useWallet } from "../lib/wallet";

// Deposit / withdraw USDC between the connected account and a Hyperliquid
// vault (native vaults and strat-linked vaults are the same thing on HL).
//
// vaultTransfer is an L1 action, so it's signed by the user's backend agent
// key — which the user authorizes ONCE with a MetaMask signature (the
// "enable transfers" step below). Withdrawals return funds to the user's HL
// perp balance; cashing out to Arbitrum lives on the Wallet page.
export default function VaultTransferPanel({ vault, vaultName }: { vault: string; vaultName?: string }) {
  const { address: eoa, kind, signTypedData, ensureChain, sendTransaction } = useWallet();

  const [d, setD] = useState<any>(null);
  const [cfg, setCfg] = useState<WalletNetConfig | null>(null);
  const [approved, setApproved] = useState<boolean | null>(null);

  const [amount, setAmount] = useState("");
  const [mode, setMode] = useState<"deposit" | "withdraw">("deposit");
  const [busy, setBusy] = useState(false);
  const [approving, setApproving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try { setD(await vaultDetails(vault, eoa ?? undefined)); } catch { /* stats are optional */ }
  }, [vault, eoa]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { walletConfig().then(setCfg).catch(() => {}); }, []);
  useEffect(() => {
    if (!eoa) { setApproved(null); return; }
    agentStatus(eoa).then((r) => setApproved(r.approved)).catch(() => setApproved(null));
  }, [eoa]);

  const fs = d?.followerState;
  const myEquity = fs ? Number(fs.vaultEquity) : 0;
  const myPnl = fs ? Number(fs.pnl) : 0;
  const maxWithdraw = Number(d?.maxWithdrawable ?? 0);
  const lockupUntil = fs ? Number(fs.lockupUntil) : 0;
  const locked = lockupUntil > Date.now();
  const depositsOpen = d?.allowDeposits !== false && !d?.isClosed;
  const name = vaultName || d?.name || shortAddr(vault);

  const enableTransfers = async () => {
    if (!eoa || !cfg) return;
    setApproving(true); setErr(null);
    try {
      await approveAgentFlow({ signTypedData, ensureChain, sendTransaction }, cfg, eoa);
      setApproved(true);
      setMsg("Transfers enabled — your wallet authorized the trading agent.");
    } catch (e: any) {
      setErr(e?.code === 4001 ? "Signature rejected in MetaMask." : String(e?.message ?? e));
    } finally { setApproving(false); }
  };

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
      const res = await vaultTransfer({ eoa, vault, is_deposit: mode === "deposit", amount_usd: amt });
      if (res?.status === "err" || res?.error) {
        setErr(typeof res.error === "string" ? res.error : JSON.stringify(res));
      } else {
        setMsg(`${mode === "deposit" ? "Deposited" : "Withdrew"} ${fmtUsd(amt)} — ${mode === "deposit" ? "into" : "from"} ${name}.`);
        setAmount("");
      }
      await load();
    } catch (e: any) { setErr(e.message ?? String(e)); }
    finally { setBusy(false); }
  };

  const needsApproval = approved === false;
  const canSignApproval = kind === "metamask";

  return (
    <div className="panel p-5 space-y-4">
      {/* Your position */}
      {eoa && (
        myEquity > 0 ? (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <Stat label="your equity" value={fmtUsd(myEquity)} />
            <Stat label="pnl" value={fmtPnl(myPnl)} cls={myPnl >= 0 ? "text-win" : "text-loss"} />
            <Stat label="withdrawable" value={fmtUsd(maxWithdraw)} />
            <Stat label="lockup"
              value={locked ? `until ${new Date(lockupUntil).toLocaleDateString()}` : "none"}
              cls={locked ? "text-warn" : "text-muted"} />
          </div>
        ) : (
          <p className="text-xs text-muted">You have no deposit in {name} yet.</p>
        )
      )}

      <div className="flex gap-1">
        <button onClick={() => setMode("deposit")}
          className={`btn ${mode === "deposit" ? "border-accent text-accent" : ""}`}>invest</button>
        <button onClick={() => setMode("withdraw")}
          className={`btn ${mode === "withdraw" ? "border-accent text-accent" : ""}`}>withdraw</button>
      </div>

      {!eoa ? (
        <p className="text-xs text-muted">Connect your wallet (top right) to invest.</p>
      ) : needsApproval ? (
        <div className="space-y-2">
          <p className="text-xs text-muted max-w-xl">
            One-time setup: sign a MetaMask message authorizing this app's agent wallet to move
            funds between your Hyperliquid account and vaults. The agent can trade and rebalance
            for you but can <span className="text-ink">never withdraw funds out of your account</span> —
            withdrawals to your wallet always require your own signature.
          </p>
          {canSignApproval ? (
            <button className="btn-primary" onClick={enableTransfers} disabled={approving || !cfg}>
              {approving ? "check MetaMask…" : "enable transfers (1 signature)"}
            </button>
          ) : (
            <p className="text-xs text-warn">You're in watch-only mode — connect MetaMask (top right) to sign.</p>
          )}
        </div>
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
            {mode === "deposit"
              ? "Deposits come from your Hyperliquid perp balance — top it up on the Wallet page."
              : "Withdrawals return to your Hyperliquid perp balance; cash out to Arbitrum from the Wallet page."}
          </p>
        </>
      )}

      {msg && <div className="text-xs text-win">{msg}</div>}
      {err && <div className="text-xs text-loss break-words">{err}</div>}
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
