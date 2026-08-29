"use client";

// One-button deposit into Hyperliquid from Ethereum, Arbitrum, Base, OP,
// Polygon, BNB Chain or Avalanche, spending whichever token the wallet
// actually holds. We scan every chain, the user picks a balance and an
// amount, and a single signed transaction lands the money in their
// Hyperliquid account — LI.FI routes to Hyperliquid Core directly, so
// there is no Arbitrum layover and no second wallet prompt.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  depositBalances, depositChains, depositQuote, fmtUsd,
  type DepositChain, type DepositChains, type DepositQuote, type DepositSource,
  type WalletNetConfig,
} from "../lib/api";
import { bridgeDepositFlow, crossChainDepositFlow, type DepositStep } from "../lib/hlActions";

type Signer = {
  signTypedData: (typedData: any) => Promise<string>;
  ensureChain: (cfg: { chainIdHex: string; chainName: string; rpcUrl: string; explorerUrl: string }) => Promise<void>;
  sendTransaction: (tx: { to: string; data?: string; value?: string }) => Promise<string>;
};

const STEPS = ["route", "sign", "credit"] as const;
const stepIndex = (s: DepositStep["step"]): number =>
  s === "quote" ? 0
  : s === "switch" || s === "approve" || s === "send" ? 1
  : 2;

const fmtEta = (sec: number) => (sec < 90 ? "under a minute" : `~${Math.round(sec / 60)} min`);
const fmtBal = (n: number) => (n >= 1000 ? n.toFixed(2) : n >= 1 ? n.toFixed(4) : n.toPrecision(4));

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
  const [sources, setSources] = useState<DepositSource[] | null>(null);
  const [scanning, setScanning] = useState(false);

  const [sel, setSel] = useState<string | null>(null); // `${chainId}:${address}`
  const [amount, setAmount] = useState("");

  const [quote, setQuote] = useState<DepositQuote | null>(null);
  const [quoting, setQuoting] = useState(false);
  const [quoteErr, setQuoteErr] = useState<string | null>(null);

  const [busy, setBusy] = useState(false);
  const [step, setStep] = useState<DepositStep | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { depositChains().then(setMeta).catch(() => {}); }, []);

  const scan = useCallback(async () => {
    if (!eoa || !meta) return;
    setScanning(true);
    try {
      const byId = new Map<number, DepositChain>(meta.chains.map((c) => [c.chainId, c]));
      const rows = (await depositBalances(eoa)).sources ?? [];
      setSources(rows.flatMap((r) => {
        const chain = byId.get(r.chainId);
        return chain ? [{ ...r, chain }] : [];
      }));
    } catch { setSources([]); }
    finally { setScanning(false); }
  }, [eoa, meta]);
  useEffect(() => { scan(); }, [scan]);

  // Dust below $1 can't clear the $5 minimum, so it only adds noise. A
  // token we couldn't price (usd === null) is still shown — hiding real
  // money because a mid was missing is worse than showing it unpriced.
  const spendable = useMemo(
    () => (sources ?? []).filter((s) => s.usd === null || s.usd >= 1),
    [sources],
  );

  // Auto-pick the biggest pot so the default path is: open → type → go.
  useEffect(() => {
    if (!sel && spendable.length) setSel(`${spendable[0].chainId}:${spendable[0].address}`);
  }, [sel, spendable]);

  const src = spendable.find((s) => `${s.chainId}:${s.address}` === sel) ?? null;
  const amtNum = Number(amount) || 0;
  const amtUsd = src?.priceUsd != null ? amtNum * src.priceUsd : null;
  const minUsd = meta?.minDepositUsd ?? 5;
  const direct = !!src?.direct;
  const overMax = !!src && amtNum > src.max + 1e-12;

  // ── live route preview ──────────────────────────────────────────────
  // Quote as the user types (debounced) so the fee, the arriving amount and
  // the ETA are on screen BEFORE MetaMask opens — and reuse that quote for
  // the actual send, so nobody is quoted twice on either side of the click.
  const quoteSeq = useRef(0);
  useEffect(() => {
    setQuote(null); setQuoteErr(null);
    if (!src || direct || busy || !(amtNum > 0) || overMax) return;
    const seq = ++quoteSeq.current;
    setQuoting(true);
    const t = setTimeout(async () => {
      try {
        const q = await depositQuote({
          from_chain_id: src.chainId, token: src.address, amount: String(amtNum), eoa,
        });
        if (seq === quoteSeq.current) setQuote(q);
      } catch (e: any) {
        if (seq === quoteSeq.current) setQuoteErr(String(e?.message ?? e).replace(/^li\.fi: /, ""));
      } finally {
        if (seq === quoteSeq.current) setQuoting(false);
      }
    }, 500);
    return () => { clearTimeout(t); setQuoting(false); };
  }, [src, direct, busy, amtNum, overMax, eoa]);

  const run = async () => {
    if (!cfg || !src) return;
    setErr(null); setBusy(true); setStep(null);
    try {
      if (!(amtNum > 0)) throw new Error("Enter an amount.");
      if (overMax) {
        throw new Error(`Max is ${src.max.toFixed(6)} ${src.symbol}${src.native ? " (a little is kept for gas)" : ""}.`);
      }
      let msg: string;
      if (direct) {
        // Arbitrum USDC — straight to Hyperliquid's own bridge, no router.
        setStep({ step: "send", msg: "Confirm the deposit in MetaMask…" });
        const tx = await bridgeDepositFlow(wallet, cfg, amtNum);
        setStep({ step: "done", msg: "", txHash: tx, usdc: amtNum });
        msg = `Deposited $${amtNum.toFixed(2)} — credited to your Hyperliquid account in ~1 minute.`;
      } else {
        const r = await crossChainDepositFlow(wallet, src, String(amtNum), eoa, minUsd, setStep, quote);
        msg = `Deposited $${r.usdc.toFixed(2)} from ${src.chainName} — it's in your Hyperliquid account.`;
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
  const chainCount = meta?.chains.length ?? 7;
  const tooSmall = quote ? quote.toUsdcMin < minUsd : false;

  return (
    <div className="panel p-5 space-y-4">
      <div>
        <h2 className="text-base text-ink">Deposit</h2>
        <p className="text-[11px] text-muted mt-0.5">
          From {chainCount} chains and whatever token you already hold — one transaction,
          straight into <span className="text-ink">your</span> Hyperliquid account.
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
        {sources === null || (scanning && !spendable.length) ? (
          <p className="text-[11px] text-muted">Scanning {chainCount} chains…</p>
        ) : spendable.length === 0 ? (
          <p className="text-[11px] text-warn">
            Nothing depositable found for this wallet on {meta?.chains.map((c) => c.name).join(", ")}.
          </p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {spendable.map((s) => {
              const k = `${s.chainId}:${s.address}`;
              const on = k === sel;
              return (
                <button key={k} onClick={() => { setSel(k); setAmount(""); }} disabled={busy}
                  className={`pill font-mono text-[11px] transition-colors ${on
                    ? "border-accent/50 text-accent bg-accent/10"
                    : "border-white/10 text-muted hover:text-ink"}`}>
                  {s.chainName} · {fmtBal(s.balance)} {s.symbol}
                  {s.usd != null && (
                    <span className={on ? "text-accent/70" : "text-muted/70"}> ({fmtUsd(s.usd)})</span>
                  )}
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* 2 · how much */}
      {src && (
        <div>
          <div className="label">amount ({src.symbol} on {src.chainName})</div>
          <div className="flex gap-2 items-center">
            <input className="input flex-1 num" type="number" min={0} placeholder="0.00"
              value={amount} onChange={(e) => setAmount(e.target.value)} disabled={busy} />
            <button className="btn" disabled={busy}
              onClick={() => setAmount(String(Math.floor(src.max * 1e6) / 1e6))}>max</button>
          </div>
          {overMax ? (
            <p className="text-[10px] text-warn mt-1">
              You have {fmtBal(src.max)} {src.symbol} to spend
              {src.native && <> (a little is kept for gas)</>}.
            </p>
          ) : (
            <p className="text-[10px] text-muted mt-1">
              {direct
                ? <>Already on Arbitrum — straight to Hyperliquid&apos;s bridge, no routing fee.</>
                : amtUsd != null
                  ? <>≈ {fmtUsd(amtUsd)} — swapped and delivered as USDC in your Hyperliquid account.</>
                  : <>Delivered as USDC in your Hyperliquid account.</>}
              {" "}Minimum ${minUsd}.
            </p>
          )}
        </div>
      )}

      {/* route preview — what actually arrives, before anything is signed */}
      {src && !direct && !overMax && amtNum > 0 && !busy && (
        <div className="rounded border border-white/10 bg-white/[0.02] px-3 py-2 text-[11px] space-y-1">
          {quoting && !quote ? (
            <span className="text-muted">Pricing the route…</span>
          ) : quoteErr ? (
            <span className="text-warn">{quoteErr}</span>
          ) : quote ? (
            <>
              <div className="flex items-baseline justify-between">
                <span className="text-muted">you receive</span>
                <span className="num text-ink">
                  {fmtUsd(quote.toUsdc)} <span className="text-muted">USDC on Hyperliquid</span>
                </span>
              </div>
              <div className="flex items-baseline justify-between">
                <span className="text-muted">cost</span>
                <span className="num text-muted">
                  {fmtUsd(quote.feeUsd + quote.gasUsd)} fee + gas · {fmtEta(quote.durationSec)}
                </span>
              </div>
              {quote.tool && (
                <div className="flex items-baseline justify-between">
                  <span className="text-muted">via</span>
                  <span className="text-muted">{quote.tool}</span>
                </div>
              )}
              {tooSmall && (
                <div className="text-warn">
                  Arrives as ~{fmtUsd(quote.toUsdcMin)} — under Hyperliquid&apos;s ${minUsd} minimum. Try more.
                </div>
              )}
            </>
          ) : null}
        </div>
      )}

      {/* 3 · one button */}
      <button className="btn-primary w-full" onClick={run}
        disabled={!canSign || busy || !src || !cfg || !(amtNum > 0) || overMax || tooSmall}>
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
                  {label === "route" ? "route" : label === "sign" ? "sign once" : "credit → HL"}
                </span>
              </div>
            ))}
          </div>
          <p className="text-[11px] text-muted">{step.msg}</p>
          {step.step === "arriving" && src && (
            <a className="text-[10px] text-muted/70 underline hover:text-ink"
              href={`${src.chain.explorerUrl}/tx/${(step as any).txHash}`} target="_blank" rel="noreferrer">
              view the transaction on {src.chainName}
            </a>
          )}
        </div>
      )}

      {err && <div className="text-[11px] text-loss break-words">{err}</div>}
    </div>
  );
}
