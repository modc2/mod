"use client";

import { useCallback, useEffect, useState } from "react";
import {
  agentStatus, depositChains, userState, walletConfig,
  fmtUsd, shortAddr, type DepositChains, type WalletNetConfig,
} from "../lib/api";
import {
  approveAgentFlow, bridgeDepositFlow, crossChainWithdrawFlow, withdrawFlow,
  type WithdrawStep,
} from "../lib/hlActions";
import { useWallet } from "../lib/wallet";
import DepositPanel from "../components/DepositPanel";

export default function WalletPage() {
  const wallet = useWallet();
  const { address: eoa, kind, hasProvider, connect } = wallet;

  const [cfg, setCfg] = useState<WalletNetConfig | null>(null);
  const [state, setState] = useState<any>(null);
  const [agent, setAgent] = useState<{ agentAddress: string; approved: boolean } | null>(null);

  const [depositAmt, setDepositAmt] = useState("");
  const [withdrawAmt, setWithdrawAmt] = useState("");
  const [dest, setDest] = useState("");

  // Withdraw destination network — Arbitrum is HL's native payout chain;
  // anything else adds a LI.FI bridge hop after the withdrawal lands.
  const [wNets, setWNets] = useState<DepositChains | null>(null);
  const [netKey, setNetKey] = useState("arbitrum");
  const [wStep, setWStep] = useState<WithdrawStep | null>(null);

  const [busy, setBusy] = useState<null | "deposit" | "withdraw" | "approve">(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { walletConfig().then(setCfg).catch(() => {}); }, []);
  useEffect(() => { depositChains().then(setWNets).catch(() => {}); }, []);
  useEffect(() => { if (eoa && !dest) setDest(eoa); }, [eoa, dest]);

  const refresh = useCallback(async () => {
    if (!eoa) { setState(null); setAgent(null); return; }
    userState(eoa).then(setState).catch(() => setState(null));
    agentStatus(eoa).then((r) => setAgent({ agentAddress: r.agentAddress, approved: r.approved })).catch(() => setAgent(null));
  }, [eoa]);
  useEffect(() => { refresh(); }, [refresh]);

  const withdrawable = Number(state?.withdrawable ?? 0);
  const accountValue = Number(state?.marginSummary?.accountValue ?? 0);
  const canSign = kind === "metamask";

  const nets = wNets && !wNets.testnet ? wNets.chains : [];
  const arbChain = nets.find((c) => c.direct) ?? null;
  const toChain = nets.find((c) => c.key === netKey) ?? null;
  const crossChain = !!toChain && !toChain.direct;
  const netName = crossChain ? toChain!.name : cfg?.chainName ?? "Arbitrum";

  const run = async (what: "deposit" | "withdraw" | "approve", fn: () => Promise<void>) => {
    setBusy(what); setMsg(null); setErr(null);
    try { await fn(); }
    catch (e: any) {
      setErr(e?.code === 4001 ? "Rejected in MetaMask." : String(e?.message ?? e));
    }
    finally { setBusy(null); refresh(); }
  };

  const onDeposit = () => run("deposit", async () => {
    if (!cfg) throw new Error("net config not loaded");
    const amt = Number(depositAmt);
    if (!(amt > 0)) throw new Error("Enter a USDC amount.");
    const tx = await bridgeDepositFlow(wallet, cfg, amt);
    setMsg(`Deposit sent — tx ${shortAddr(tx)}. USDC is credited to your Hyperliquid account in ~1 minute.`);
    setDepositAmt("");
  });

  const onWithdraw = () => run("withdraw", async () => {
    if (!cfg) throw new Error("net config not loaded");
    const amt = Number(withdrawAmt);
    if (!(amt > 0)) throw new Error("Enter a USDC amount.");
    if (amt > withdrawable + 1e-9) throw new Error(`Max withdrawable is ${fmtUsd(withdrawable)}.`);
    if (!/^0x[a-fA-F0-9]{40}$/.test(dest.trim())) throw new Error("Destination must be a 0x address.");
    setWStep(null);
    try {
      if (crossChain && toChain && arbChain && eoa) {
        if (amt < 6) throw new Error(`Withdrawals to ${toChain.name} need at least $6 (≈$1 Hyperliquid fee + $5 bridge minimum). Pick Arbitrum for smaller amounts.`);
        const r = await crossChainWithdrawFlow(wallet, cfg, arbChain, toChain, dest.trim(), String(amt), eoa, setWStep);
        setMsg(`$${r.usdc.toFixed(2)} USDC delivered to ${shortAddr(dest.trim())} on ${toChain.name}.`);
      } else {
        await withdrawFlow(wallet, cfg, dest.trim(), String(amt));
        setMsg(`Withdrawal of ${fmtUsd(amt)} submitted — arrives on ${cfg.chainName} in a few minutes (fee ~$${cfg.withdrawalFeeUsd}).`);
      }
      setWithdrawAmt("");
    } finally {
      setWStep(null);
    }
  });

  const onApprove = () => run("approve", async () => {
    if (!cfg || !eoa) throw new Error("connect first");
    await approveAgentFlow(wallet, cfg, eoa);
    setMsg("Trading agent authorized — vault invest/withdraw and copy-trading are now enabled.");
  });

  if (!eoa) {
    return (
      <div className="max-w-xl space-y-4">
        <h1 className="text-xl text-ink">Wallet</h1>
        <div className="panel p-6 text-center space-y-3">
          <p className="text-xs text-muted">
            Connect MetaMask to deposit and withdraw funds, invest in vaults and strats, and enable copy-trading.
          </p>
          <button className="btn-primary" onClick={() => connect()} disabled={!hasProvider}>
            {hasProvider ? "connect metamask" : "MetaMask not detected"}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-3xl space-y-5">
      <div>
        <h1 className="text-xl text-ink">Wallet</h1>
        <p className="text-xs text-muted mt-1">
          <span className="font-mono text-accent2">{shortAddr(eoa)}</span>
          {kind === "watch" && <span className="text-warn ml-2">watch-only — connect MetaMask to sign</span>}
          {cfg?.testnet && <span className="text-warn ml-2">TESTNET</span>}
        </p>
      </div>

      {/* Balances */}
      <div className="panel p-4 grid grid-cols-2 sm:grid-cols-3 gap-4">
        <Stat label="hyperliquid account" value={fmtUsd(accountValue)} />
        <Stat label="withdrawable (perp)" value={fmtUsd(withdrawable)} />
        <Stat label="network" value={cfg?.chainName ?? "…"} cls="text-muted" />
      </div>

      {(msg || err) && (
        <div className={`panel p-3 text-xs ${err ? "text-loss" : "text-win"} break-words`}>{err ?? msg}</div>
      )}

      <div className="grid md:grid-cols-2 gap-4">
        {/* Deposit — cross-chain on mainnet (Ethereum/Base/Polygon/Arbitrum via
            LI.FI bridge), plain Arbitrum USDC form on testnet. */}
        {cfg && !cfg.testnet ? (
          <DepositPanel wallet={wallet} cfg={cfg} eoa={eoa} canSign={canSign}
            onDone={(m) => { setMsg(m); setErr(null); refresh(); }} />
        ) : (
          <div className="panel p-5 space-y-3">
            <div>
              <h2 className="text-base text-ink">Deposit</h2>
              <p className="text-[11px] text-muted mt-0.5">
                Sends USDC on {cfg?.chainName ?? "Arbitrum"} to the Hyperliquid bridge — one MetaMask
                transaction; credited in ~1 minute. Minimum ${cfg?.minDepositUsd ?? 5}.
              </p>
            </div>
            <div>
              <div className="label">amount (USDC)</div>
              <input className="input w-full num" type="number" min={cfg?.minDepositUsd ?? 5} step={10}
                placeholder="0.00" value={depositAmt} onChange={(e) => setDepositAmt(e.target.value)} />
            </div>
            <button className="btn-primary w-full" onClick={onDeposit} disabled={!canSign || busy !== null}>
              {busy === "deposit" ? "check MetaMask…" : "deposit via metamask"}
            </button>
            {!canSign && <p className="text-[10px] text-warn">Watch-only connection can't sign.</p>}
          </div>
        )}

        {/* Withdraw */}
        <div className="panel p-5 space-y-3">
          <div>
            <h2 className="text-base text-ink">Withdraw</h2>
            <p className="text-[11px] text-muted mt-0.5">
              Withdraws USDC from Hyperliquid to {netName} — signed by
              <span className="text-ink"> your wallet only</span> (the agent can't do this).
              {crossChain
                ? <> Pays out on Arbitrum, then auto-bridges to {toChain!.name} — fee ~$
                    {cfg?.withdrawalFeeUsd ?? 1} + bridge costs; needs a little ETH on Arbitrum for gas.</>
                : <> Fee ~${cfg?.withdrawalFeeUsd ?? 1}, arrives in a few minutes.</>}
            </p>
          </div>
          {nets.length > 1 && (
            <div>
              <div className="label">network</div>
              <div className="flex flex-wrap gap-1.5">
                {nets.map((c) => {
                  const on = c.key === netKey;
                  return (
                    <button key={c.key} onClick={() => setNetKey(c.key)} disabled={busy !== null}
                      className={`pill font-mono text-[11px] transition-colors ${on
                        ? "border-accent/50 text-accent bg-accent/10"
                        : "border-white/10 text-muted hover:text-ink"}`}>
                      {c.direct ? cfg?.chainName ?? c.name : c.name}
                    </button>
                  );
                })}
              </div>
            </div>
          )}
          <div>
            <div className="label">amount (USDC)</div>
            <div className="flex gap-2 items-center">
              <input className="input flex-1 num" type="number" min={0} step={10} placeholder="0.00"
                value={withdrawAmt} onChange={(e) => setWithdrawAmt(e.target.value)} />
              {withdrawable > 0 &&
                <button className="btn" onClick={() => setWithdrawAmt(String(withdrawable))}>max</button>}
            </div>
          </div>
          <div>
            <div className="label">destination ({netName})</div>
            <input className="input w-full font-mono text-xs" placeholder="0x…"
              value={dest} onChange={(e) => setDest(e.target.value)} />
          </div>
          <button className="btn-primary w-full" onClick={onWithdraw} disabled={!canSign || busy !== null}>
            {busy === "withdraw"
              ? (crossChain ? "working — watch MetaMask" : "check MetaMask…")
              : "withdraw via metamask"}
          </button>
          {busy === "withdraw" && wStep && (
            <div className="space-y-1">
              <p className="text-[11px] text-accent">{wStep.msg}</p>
              <p className="text-[10px] text-muted/70">
                Keep this tab open — funds only ever sit in your own wallet between the two hops.
              </p>
            </div>
          )}
          {!canSign && <p className="text-[10px] text-warn">Watch-only connection can't sign.</p>}
        </div>
      </div>

      {/* Trading agent */}
      <div className="panel p-5 space-y-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="text-base text-ink">Trading agent</h2>
            <p className="text-[11px] text-muted mt-0.5 max-w-xl">
              Vault/strat invest-withdraw and the live copy-trade engine are signed by a per-account
              agent key held by this app. Authorize it once with a MetaMask signature. Agents can
              trade and move funds between your account and vaults, but can never withdraw out of
              your Hyperliquid account.
            </p>
            {agent && (
              <p className="text-[11px] mt-1 font-mono text-muted">
                agent {shortAddr(agent.agentAddress)}{" "}
                {agent.approved
                  ? <span className="text-win">approved ✓</span>
                  : <span className="text-warn">not approved</span>}
              </p>
            )}
          </div>
          {agent && !agent.approved && (
            <button className="btn-primary shrink-0" onClick={onApprove} disabled={!canSign || busy !== null}>
              {busy === "approve" ? "check MetaMask…" : "authorize (1 signature)"}
            </button>
          )}
        </div>
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
