"use client";

// One-button deposit into Hyperliquid from Ethereum, Base, Polygon or
// Arbitrum. We scan all four chains for the user's USDC + native coin,
// they pick a balance and an amount, and a single flow handles chain
// switching, approval, the LI.FI bridge into Arbitrum USDC, and the final
// transfer to Hyperliquid's bridge — with a live stepper the whole way.

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  depositBalances, depositChains, fmtUsd,
  type DepositBalance, type DepositChain, type DepositChains, type WalletNetConfig,
} from "../lib/api";
import { bridgeDepositFlow, crossChainDepositFlow, type DepositStep } from "../lib/hlActions";

type Signer = {
  signTypedData: (typedData: any) => Promise<string>;
  ensureChain: (cfg: { chainIdHex: string; chainName: string; rpcUrl: string; explorerUrl: string }) => Promise<void>;
  sendTransaction: (tx: { to: string; data?: string; value?: string }) => Promise<string>;
};

/** A spendable balance: one chain + one token the user actually holds. */
type Source = {
  chain: DepositChain;
  token: "usdc" | "native";
  symbol: string;
  balance: number;
  usd: number;
  max: number; // balance minus gas reserve for natives
};

const STEPS = ["route", "bridge", "deposit"] as const;
const stepIndex = (s: DepositStep["step"]): number =>
  s === "quote" || s === "switch" || s === "approve" ? 0
  : s === "bridge" || s === "bridging" ? 1
  : 2;

export default function DepositPanel({
  wallet, cfg, eoa, canSign, onDone,
}: {
  wallet: Signer;
  cfg: WalletNetConfig | null;
  eoa: string;
  canSign: boolean;
  onDone: (msg: string) => void;
}) {
  const [meta, setMeta] = useState<DepositChains | null>(null);
  const [balances, setBalances] = useState<DepositBalance[] | null>(null);
  const [scanning, setScanning] = useState(false);

  const [sel, setSel] = useState<string | null>(null); // `${chainKey}:${token}`
  const [amount, setAmount] = useState("");

  const [busy, setBusy] = useState(false);
  const [step, setStep] = useState<DepositStep | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { depositChains().then(setMeta).catch(() => {}); }, []);

  const scan = useCallback(async () => {
    if (!eoa) return;
    setScanning(true);
    try { setBalances((await depositBalances(eoa)).chains); }
    catch { setBalances([]); }
    finally { setScanning(false); }
  }, [eoa]);
  useEffect(() => { scan(); }, [scan]);

  // Every (chain, token) pair worth offering, richest first. Dust below $1
  // can't clear the $5 bridge minimum anyway, so it only adds noise.
  const sources: Source[] = useMemo(() => {
    if (!meta || !balances) return [];
    const byId = new Map(meta.chains.map((c) => [c.chainId, c]));
    const out: Source[] = [];
    for (const b of balances) {
      const chain = byId.get(b.chainId);
      if (!chain) continue;
      if (b.usdc.balance * 1 >= 1) {
        out.push({ chain, token: "usdc", symbol: "USDC", balance: b.usdc.balance, usd: b.usdc.usd, max: b.usdc.balance });
      }
      const freeNative = Math.max(0, b.native.balance - b.native.gasReserve);
      if (freeNative * b.native.priceUsd >= 1) {
        out.push({ chain, token: "native", symbol: b.native.symbol, balance: b.native.balance, usd: b.native.usd, max: freeNative });
      }
    }
    return out.sort((a, b) => b.usd - a.usd);
  }, [meta, balances]);

  // Auto-pick the biggest pot so the default path is: open → type amount → go.
  useEffect(() => {
    if (!sel && sources.length) setSel(`${sources[0].chain.key}:${sources[0].token}`);
  }, [sel, sources]);

  const src = sources.find((s) => `${s.chain.key}:${s.token}` === sel) ?? null;
  const priceUsd = src ? (src.token === "usdc" ? 1 : src.usd / (src.balance || 1)) : 0;
  const amtNum = Number(amount) || 0;
  const amtUsd = amtNum * priceUsd;
  const minUsd = meta?.minDepositUsd ?? 5;
  const direct = !!src && src.chain.direct && src.token === "usdc";

  const run = async () => {
    if (!cfg || !src) return;
    setErr(null); setBusy(true); setStep(null);
    try {
      if (!(amtNum > 0)) throw new Error("Enter an amount.");
      if (amtNum > src.max + 1e-12) throw new Error(`Max is ${src.max.toFixed(6)} ${src.symbol}${src.token === "native" ? " (a little is kept for gas)" : ""}.`);
      let msg: string;
      if (direct) {
        // Already Arbitrum USDC — one transfer straight to the HL bridge.
        setStep({ step: "deposit", msg: "Confirm the deposit in MetaMask…" });
        const tx = await bridgeDepositFlow(wallet, cfg, amtNum);
        setStep({ step: "done", msg: "", txHash: tx, usdc: amtNum });
        msg = `Deposited $${amtNum.toFixed(2)} — credited to your Hyperliquid account in ~1 minute.`;
      } else {
        if (amtUsd < minUsd) throw new Error(`Minimum deposit is $${minUsd} — that's about ${(minUsd / (priceUsd || 1)).toFixed(6)} ${src.symbol}.`);
        const r = await crossChainDepositFlow(
          wallet, cfg, src.chain, src.token, String(amtNum), eoa, minUsd, setStep,
        );
        msg = `Deposited $${r.usdc.toFixed(2)} from ${src.chain.name} — credited to your Hyperliquid account in ~1 minute.`;
      }
      setAmount("");
      onDone(msg);
      scan();
    } catch (e: any) {
      setErr(e?.code === 4001 ? "Rejected in MetaMask." : String(e?.message ?? e));
      setStep(null);
    } finally {
      setBusy(false);
    }
  };

  // Testnet (or chains endpoint down): cross-chain routing doesn't exist —
  // the caller keeps its plain Arbitrum form, we render nothing extra.
  if (meta?.testnet) return null;

  const active = step ? stepIndex(step.step) : -1;
  const doneStep = step?.step === "done";

  return (
    <div className="panel p-5 space-y-4">
      <div>
        <h2 className="text-base text-ink">Deposit</h2>
        <p className="text-[11px] text-muted mt-0.5">
          From Ethereum, Base, Polygon or Arbitrum — we detect your money, you press one button.
          Everything is signed by your wallet and lands in <span className="text-ink">your</span> Hyperliquid account.
        </p>
      </div>

      {/* 1 · where the money is */}
      <div className="space-y-1.5">
        <div className="label flex items-center justify-between">
          <span>your funds</span>
          <button className="text-[10px] text-muted hover:text-ink" onClick={scan} disabled={scanning}>
            {scanning ? "scanning…" : "rescan"}
          </button>
        </div>
        {balances === null || (scanning && !sources.length) ? (
          <p className="text-[11px] text-muted">Scanning 4 chains…</p>
        ) : sources.length === 0 ? (
          <p className="text-[11px] text-warn">
            No USDC, ETH or POL found for this wallet on Ethereum, Base, Polygon or Arbitrum.
          </p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {sources.map((s) => {
              const k = `${s.chain.key}:${s.token}`;
              const on = k === sel;
              return (
                <button key={k} onClick={() => { setSel(k); setAmount(""); }} disabled={busy}
                  className={`pill font-mono text-[11px] transition-colors ${on
                    ? "border-accent/50 text-accent bg-accent/10"
                    : "border-white/10 text-muted hover:text-ink"}`}>
                  {s.chain.name} · {s.token === "usdc" ? s.balance.toFixed(2) : s.balance.toFixed(4)} {s.symbol}
                  <span className={on ? "text-accent/70" : "text-muted/70"}> ({fmtUsd(s.usd)})</span>
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* 2 · how much */}
      {src && (
        <div>
          <div className="label">amount ({src.symbol} on {src.chain.name})</div>
          <div className="flex gap-2 items-center">
            <input className="input flex-1 num" type="number" min={0} placeholder="0.00"
              value={amount} onChange={(e) => setAmount(e.target.value)} disabled={busy} />
            <button className="btn" disabled={busy}
              onClick={() => setAmount(String(Math.floor(src.max * 1e6) / 1e6))}>max</button>
          </div>
          <p className="text-[10px] text-muted mt-1">
            {src.token === "native"
              ? <>≈ {fmtUsd(amtUsd)} — swapped &amp; bridged to USDC on Arbitrum automatically.</>
              : direct
                ? <>Already on Arbitrum — one transaction, credited in ~1 minute.</>
                : <>Bridged to Arbitrum USDC automatically, then deposited.</>}
            {" "}Minimum ${minUsd}.
          </p>
        </div>
      )}

      {/* 3 · one button */}
      <button className="btn-primary w-full" onClick={run}
        disabled={!canSign || busy || !src || !cfg || !(amtNum > 0)}>
        {busy ? "working — watch MetaMask" : "deposit to hyperliquid"}
      </button>
      {!canSign && <p className="text-[10px] text-warn">Watch-only connection can&apos;t sign — connect MetaMask.</p>}

      {/* live progress */}
      {step && !doneStep && (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            {STEPS.map((label, i) => (
              <div key={label} className="flex items-center gap-2 flex-1">
                <span className={`h-5 w-5 shrink-0 grid place-items-center rounded-full text-[10px] font-bold
                  ${i < active ? "bg-accent text-bg" : i === active ? "bg-accent/20 text-accent animate-pulse" : "bg-white/5 text-muted"}`}>
                  {i < active ? "✓" : i + 1}
                </span>
                <span className={`text-[10px] uppercase tracking-wider ${i === active ? "text-accent" : "text-muted"}`}>
                  {label === "route" ? "route" : label === "bridge" ? "bridge → arbitrum" : "deposit → HL"}
                </span>
              </div>
            ))}
          </div>
          <p className="text-[11px] text-muted">{step.msg}</p>
          <p className="text-[10px] text-muted/70">
            Safe to keep this tab open — funds only ever move to your own address until the final deposit.
          </p>
        </div>
      )}

      {err && <div className="text-[11px] text-loss break-words">{err}</div>}
    </div>
  );
}
