"use client";

// ONE money panel. Both wallets side by side, one amount box, one button.
//
//   [ MY WALLET $705 ]  →  [ TRADING $273 ]
//          $ [____]  25% 50% MAX
//        [ DEPOSIT $50 → TRADING ]
//
// Tap a tile to pick the SOURCE — the other tile is the destination, the
// arrow flips, and the button relabels itself (DEPOSIT / WITHDRAW). A
// "send somewhere else" toggle swaps the destination for any 0x address
// (SEND), from either wallet. That's deposit, withdraw and transfer in a
// single flow instead of three separate forms.
//
// Plumbing (unchanged from the old three-form panel):
//   - deposit  = USDC.e transfer from MetaMask → deposit wallet, then a
//     gasless backend wrap into V2 collateral (Polymarket's tradable token)
//   - withdraw/send-from-trading = backend unwrap-and-send: burns V2
//     collateral, relayer pays gas, USDC.e lands at the destination with
//     no MetaMask popup
//   - send-from-MetaMask = plain USDC.e transfer (MetaMask popup)

import { useCallback, useEffect, useState } from "react";
import {
  BrowserProvider,
  Contract,
  JsonRpcProvider,
  formatUnits,
  parseUnits,
} from "ethers";
import { useAuth } from "../context/AuthContext";
import { USDC_E } from "../lib/polymarketContracts";
import { ensureChain, networkById, withRpcFallback } from "../lib/networks";

const ERC20_ABI = [
  "function balanceOf(address) view returns (uint256)",
  "function transfer(address to, uint256 amount) returns (bool)",
];

interface InfoResp {
  eoa: string;
  backendSigner: string;
  depositWallet: string;
  deployed: boolean;
  // Base-units strings (1e6 = $1). usdcBalance = WRAPPED V2 collateral
  // (the tradable balance). rawUsdceBalance = un-wrapped USDC.e sitting in
  // the wallet. nativeUsdcBalance = Circle-native USDC (not wrappable).
  // `null` = the on-chain read failed — distinct from "0" = confirmed
  // empty. Never render null as $0.00.
  usdcBalance: string | null;
  rawUsdceBalance?: string | null;
  nativeUsdcBalance?: string | null;
  balanceUnavailable?: boolean;
}

type Source = "eoa" | "trading";

function shortAddr(a: string): string {
  return `${a.slice(0, 6)}…${a.slice(-4)}`;
}

function isHexAddr(a: string): boolean {
  return /^0x[a-fA-F0-9]{40}$/.test(a.trim());
}

function Spinner({ className = "" }: { className?: string }) {
  return (
    <span
      role="status"
      aria-label="working"
      className={`inline-block w-3 h-3 border-2 border-current border-t-transparent rounded-full animate-spin align-[-2px] ${className}`}
    />
  );
}

export default function WalletPanel() {
  const { auth } = useAuth();
  const [info, setInfo] = useState<InfoResp | null>(null);
  const [eoaUsd, setEoaUsd] = useState<number | null>(null);
  const [source, setSource] = useState<Source>("eoa");
  const [amount, setAmount] = useState("");
  const [dest, setDest] = useState("");
  const [showDest, setShowDest] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<Source | null>(null);
  // After a deposit/withdraw the on-chain balance may lag (relayer batch
  // still mining). Stash the pre-op balance so the poll loop can detect
  // when it actually moves, then clear the spinner.
  const [pendingOp, setPendingOp] = useState<
    { prevBalance: string; label: string; startedAt: number } | null
  >(null);

  const eoa = auth.address;

  const refresh = useCallback(async () => {
    if (!eoa) return;
    const polygon = networkById("polygon")!;
    const [infoRes, eoaBal] = await Promise.all([
      fetch(`/api/polymarket/deposit-wallet/info?eoa=${eoa}`, { cache: "no-store" })
        .then(async (res) => {
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          return (await res.json()) as InfoResp;
        })
        .catch((e: unknown) => (e instanceof Error ? e : new Error(String(e)))),
      withRpcFallback(polygon, async (url) => {
        const provider = new JsonRpcProvider(url);
        const c = new Contract(USDC_E, ERC20_ABI, provider);
        const raw: bigint = await c.balanceOf(eoa);
        return Number(formatUnits(raw, 6));
      }).catch(() => null),
    ]);
    setEoaUsd((prev) => (eoaBal == null ? prev : eoaBal));
    if (infoRes instanceof Error) {
      setError(`Could not load trading wallet: ${infoRes.message}`);
      return;
    }
    const data = infoRes;
    const unavailable = data.balanceUnavailable || data.usdcBalance == null;
    // A failed on-chain read comes back null. Don't clobber a good
    // last-known value with $0.00 — keep prior balances, refresh the rest.
    setInfo((prev) =>
      unavailable && prev
        ? {
            ...data,
            usdcBalance: prev.usdcBalance,
            rawUsdceBalance: prev.rawUsdceBalance,
            nativeUsdcBalance: prev.nativeUsdcBalance,
            balanceUnavailable: true,
          }
        : data,
    );
    if (unavailable) {
      setError("Trading balance temporarily unavailable (RPC) — retrying…");
    } else {
      setError((prev) =>
        prev && prev.startsWith("Trading balance temporarily") ? null : prev,
      );
    }
    setPendingOp((prev) => {
      if (!prev) return null;
      if (!unavailable && data.usdcBalance !== prev.prevBalance) {
        setStatus(`${prev.label} ✓`);
        return null;
      }
      // Bail after 90s so a stuck relayer doesn't spin forever.
      if (Date.now() - prev.startedAt > 90_000) return null;
      return prev;
    });
  }, [eoa]);

  useEffect(() => {
    void refresh();
    // Poll fast while an op is settling, slow otherwise.
    const t = setInterval(() => { void refresh(); }, pendingOp ? 2_000 : 15_000);
    return () => clearInterval(t);
  }, [refresh, pendingOp]);

  const balanceUsd: number | null =
    info && info.usdcBalance != null ? Number(info.usdcBalance) / 1_000_000 : null;
  const rawUsd = info ? Number(info.rawUsdceBalance ?? "0") / 1_000_000 : 0;
  const nativeUsd = info ? Number(info.nativeUsdcBalance ?? "0") / 1_000_000 : 0;

  const srcBal = source === "eoa" ? eoaUsd : balanceUsd;
  const customDest = showDest && isHexAddr(dest) ? dest.trim() : null;
  // Guard against "send to where it already is": a custom dest equal to the
  // implicit counterparty is just a deposit/withdraw — treat it as one.
  const effectiveCustom =
    customDest &&
    info &&
    eoa &&
    customDest.toLowerCase() !== (source === "eoa" ? info.depositWallet : eoa).toLowerCase()
      ? customDest
      : null;

  const amt = parseFloat(amount);
  const amtOk = Number.isFinite(amt) && amt > 0;

  const actionLabel = !amtOk
    ? source === "eoa" ? "DEPOSIT" : "WITHDRAW"
    : effectiveCustom
      ? `SEND $${amt.toFixed(2)} → ${shortAddr(effectiveCustom)}`
      : source === "eoa"
        ? `DEPOSIT $${amt.toFixed(2)} → TRADING`
        : `WITHDRAW $${amt.toFixed(2)} → MY WALLET`;

  const copyTile = useCallback(async (which: Source) => {
    const addr = which === "eoa" ? eoa : info?.depositWallet;
    if (!addr) return;
    try {
      await navigator.clipboard.writeText(addr);
      setCopied(which);
      setTimeout(() => setCopied(null), 1200);
    } catch {}
  }, [eoa, info]);

  // Wrap any un-wrapped USDC.e into tradable V2 collateral (covers direct
  // sends / bridges that skipped the panel, or a deposit whose wrap failed).
  const handleWrap = useCallback(async () => {
    if (!info || !eoa) return;
    setError(null);
    setStatus("Wrapping for trading (gasless)…");
    setBusy(true);
    try {
      const res = await fetch("/api/polymarket/deposit-wallet/wrap", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ eoa }),
      });
      if (!res.ok) {
        const text = await res.text().catch(() => "");
        throw new Error(`Wrap failed: ${text.slice(0, 250)}`);
      }
      setPendingOp({
        prevBalance: info.usdcBalance ?? "",
        label: `Wrapped $${rawUsd.toFixed(2)}`,
        startedAt: Date.now(),
      });
      void refresh();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg.slice(0, 250));
    } finally {
      setBusy(false);
    }
  }, [info, eoa, rawUsd, refresh]);

  // The one action button. Source decides the rails:
  //   eoa     → MetaMask USDC.e transfer (+ gasless wrap when destination
  //             is the trading wallet)
  //   trading → backend unwrap-and-send, fully gasless
  const handleGo = useCallback(async () => {
    if (!info || !eoa) return;
    if (!amtOk) {
      setError("Enter an amount in USDC.");
      return;
    }
    if (srcBal == null) {
      setError("Balance unavailable right now — try again in a moment.");
      return;
    }
    if (amt > srcBal + 1e-6) {
      setError(`Only $${srcBal.toFixed(2)} available in ${source === "eoa" ? "your wallet" : "the trading wallet"}.`);
      return;
    }
    if (showDest && dest.trim() && !isHexAddr(dest)) {
      setError("Destination must be a 0x… address (40 hex chars).");
      return;
    }
    const destination = effectiveCustom ?? (source === "eoa" ? info.depositWallet : eoa);
    const isDeposit = !effectiveCustom && source === "eoa";
    setError(null);
    setStatus(null);
    setBusy(true);
    try {
      if (source === "eoa") {
        const ethereum = (window as unknown as { ethereum?: {
          request: (args: { method: string; params?: unknown[] }) => Promise<unknown>;
        } }).ethereum;
        if (!ethereum) throw new Error("No wallet found (install MetaMask).");
        await ensureChain(ethereum, networkById("polygon")!);
        const provider = new BrowserProvider(ethereum as never);
        // Pin the signer to the signed-in EOA. An argless getSigner() uses
        // whatever account MetaMask happens to have active — if that's a
        // different (empty) wallet the transfer estimates from it and
        // reverts with "ERC20: transfer amount exceeds balance" while the
        // tile correctly shows the signed-in wallet's balance.
        let signer;
        try {
          signer = await provider.getSigner(eoa);
        } catch {
          throw new Error(
            `MetaMask isn't connected as ${shortAddr(eoa)} (the wallet you signed in with). Switch to that account in MetaMask, or sign in with the active one, then retry.`,
          );
        }
        const signerAddr = (await signer.getAddress()).toLowerCase();
        if (signerAddr !== eoa.toLowerCase()) {
          throw new Error(
            `MetaMask is on ${shortAddr(signerAddr)} but you signed in as ${shortAddr(eoa)}. Switch accounts in MetaMask and retry.`,
          );
        }
        const usdc = new Contract(USDC_E, ERC20_ABI, signer);
        // Preflight the real on-chain balance so a stale tile turns into a
        // readable error instead of an estimateGas revert blob.
        try {
          const chainBal: bigint = await usdc.balanceOf(eoa);
          if (chainBal < parseUnits(amt.toString(), 6)) {
            throw new Error(
              `${shortAddr(eoa)} holds $${(Number(chainBal) / 1e6).toFixed(2)} USDC.e on Polygon — not enough for $${amt.toFixed(2)}.`,
            );
          }
        } catch (pf) {
          if (pf instanceof Error && pf.message.includes("USDC.e on Polygon")) throw pf;
          // Balance read failed (RPC hiccup) — let the transfer itself decide.
        }
        setStatus("Confirm in MetaMask…");
        const tx = await usdc.transfer(destination, parseUnits(amt.toString(), 6));
        setStatus(`Sending… tx ${tx.hash.slice(0, 10)}…`);
        await tx.wait();
        if (isDeposit) {
          // Polymarket V2 counts a *wrapped* collateral token, not raw
          // USDC.e — without this wrap the deposit reads as $0 tradable.
          setStatus(`Deposited $${amt.toFixed(2)} ✓ — wrapping for trading (gasless)…`);
          const wrapRes = await fetch("/api/polymarket/deposit-wallet/wrap", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ eoa }),
          });
          if (!wrapRes.ok) {
            const text = await wrapRes.text().catch(() => "");
            throw new Error(`Wrap failed: ${text.slice(0, 250)}`);
          }
          setPendingOp({
            prevBalance: info.usdcBalance ?? "",
            label: `Deposited $${amt.toFixed(2)}`,
            startedAt: Date.now(),
          });
        } else {
          setStatus(`Sent $${amt.toFixed(2)} → ${shortAddr(destination)} ✓`);
        }
      } else {
        // Trading balance is V2 collateral, not raw USDC.e — unwrap-and-send
        // burns collateral and mints USDC.e at the destination in one
        // relayer batch. Gasless, no popup.
        setStatus("Sending via Polymarket relayer (gasless)…");
        const res = await fetch("/api/polymarket/deposit-wallet/unwrap-and-send", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            eoa,
            destination,
            amountBaseUnits: parseUnits(amt.toString(), 6).toString(),
          }),
        });
        const text = await res.text();
        if (!res.ok) {
          let detail = text.slice(0, 250);
          try {
            const j = JSON.parse(text) as { error?: string };
            if (j.error) detail = j.error.slice(0, 250);
          } catch {}
          throw new Error(detail);
        }
        const label = effectiveCustom
          ? `Sent $${amt.toFixed(2)} → ${shortAddr(destination)}`
          : `Withdrew $${amt.toFixed(2)}`;
        setStatus(`${label} — finalizing…`);
        setPendingOp({
          prevBalance: info.usdcBalance ?? "",
          label,
          startedAt: Date.now(),
        });
      }
      setAmount("");
      void refresh();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg.slice(0, 250));
    } finally {
      setBusy(false);
    }
  }, [info, eoa, amt, amtOk, srcBal, source, showDest, dest, effectiveCustom, refresh]);

  if (!auth.connected) return null;

  const fmtBal = (v: number | null) => (v == null ? "…" : `$${v.toFixed(2)}`);

  const tile = (which: Source) => {
    const active = source === which;
    const label = which === "eoa" ? "MY WALLET" : "TRADING";
    const sub = which === "eoa" ? "MetaMask" : "Polymarket";
    const bal = which === "eoa" ? eoaUsd : balanceUsd;
    const addr = which === "eoa" ? eoa : info?.depositWallet;
    const unavailable = which === "trading" && balanceUsd == null && !!info;
    return (
      <button
        onClick={() => setSource(which)}
        className={`flex-1 min-w-0 text-left rounded border-2 px-3 py-2 transition-all ${
          active
            ? "border-green-400 bg-green-400/[0.07] shadow-[0_0_12px_rgba(74,222,128,0.25)]"
            : "border-pixel-border bg-pixel-black/30 hover:border-pixel-gray"
        }`}
        title={active ? `${label} is the source` : `Tap to move money FROM ${label}`}
        aria-pressed={active}
      >
        <div className="flex items-center gap-2">
          <span className={`text-[11px] uppercase tracking-[0.18em] ${active ? "text-green-400" : "text-pixel-muted"}`}>
            {label}
          </span>
          <span className="text-[10px] text-pixel-muted">{sub}</span>
          {active && <span className="text-[9px] text-green-400/80 tracking-wider ml-auto">FROM</span>}
        </div>
        <div
          className={`font-mono text-[24px] leading-tight ${unavailable ? "text-amber-400 text-[14px]" : active ? "text-green-400" : "text-pixel-fg"}`}
          title={unavailable ? "On-chain read failed — retrying" : undefined}
        >
          {unavailable ? "unavailable" : fmtBal(bal)}
        </div>
        {addr && (
          <span
            role="button"
            tabIndex={0}
            onClick={(e) => { e.stopPropagation(); void copyTile(which); }}
            onKeyDown={(e) => { if (e.key === "Enter") { e.stopPropagation(); void copyTile(which); } }}
            className="text-[11px] font-mono text-pixel-muted hover:text-green-400 transition-colors"
            title={`Click to copy ${addr}`}
          >
            {shortAddr(addr)} {copied === which ? "✓" : "⧉"}
          </span>
        )}
      </button>
    );
  };

  return (
    <div className="pixel-panel border-2 border-pixel-border p-3 space-y-2.5">
      {/* Header */}
      <div className="flex items-center gap-2">
        <span className="text-xs uppercase tracking-[0.2em] text-pixel-white">Money</span>
        <span className="text-[10px] text-pixel-muted">tap a wallet · type amount · go</span>
        <span className="ml-auto flex items-center gap-2">
          {(busy || pendingOp) && (
            <span className="text-green-400" title={pendingOp ? "Waiting for on-chain settlement…" : "Working…"}>
              <Spinner />
            </span>
          )}
          <button
            onClick={() => { void refresh(); }}
            className="text-pixel-muted hover:text-green-400 text-sm"
            title="Refresh balances"
          >
            ↻
          </button>
        </span>
      </div>

      {/* Two tiles + direction arrow. The arrow always points source → dest;
          clicking it flips the direction (same as tapping the other tile). */}
      <div className="flex items-stretch gap-2">
        {tile("eoa")}
        <button
          onClick={() => setSource((s) => (s === "eoa" ? "trading" : "eoa"))}
          className="self-center text-[22px] text-green-400 hover:scale-125 transition-transform px-0.5 shrink-0"
          title="Flip direction"
        >
          {source === "eoa" ? "→" : "←"}
        </button>
        {tile("trading")}
      </div>

      {!info?.deployed && info && (
        <div className="flex items-center gap-2 text-[11px] text-amber-400/90 leading-snug">
          <span className="w-1.5 h-1.5 rounded-full bg-amber-400/80 shrink-0" />
          <span>Trading wallet isn&apos;t on-chain yet — deploys automatically on your first trade (gasless, ~10s).</span>
        </div>
      )}

      {/* Un-wrapped USDC.e — real money that reads as $0 tradable. One click. */}
      {rawUsd > 0.005 && (
        <div className="border border-amber-600/50 rounded p-2 flex items-center gap-3 flex-wrap">
          <span className="text-xs text-amber-400">
            ${rawUsd.toFixed(2)} landed in the trading wallet but isn&apos;t wrapped for trading yet.
          </span>
          <button
            onClick={() => { void handleWrap(); }}
            disabled={busy || !!pendingOp}
            className="ml-auto px-3 py-1 bg-amber-700 hover:bg-amber-600 disabled:opacity-50 disabled:cursor-not-allowed text-white font-bold text-xs rounded inline-flex items-center gap-2"
          >
            {busy && <Spinner />}
            WRAP FOR TRADING
          </button>
        </div>
      )}

      {/* Native USDC can't be wrapped by Polymarket's Onramp — surface it
          instead of silently showing $0.00. */}
      {nativeUsd > 0.005 && (
        <div className="text-xs text-amber-400">
          ${nativeUsd.toFixed(2)} native USDC detected in the trading wallet.
          Polymarket trades USDC.e — swap native USDC to USDC.e (bridged) to make it tradable.
        </div>
      )}

      {/* Amount + quick fractions of the SOURCE balance */}
      <div className="flex items-center gap-1.5 flex-wrap">
        <div className="flex items-center flex-1 min-w-[120px] bg-pixel-bg border border-pixel-border rounded px-2">
          <span className="text-pixel-muted mr-1 font-mono">$</span>
          <input
            type="text"
            inputMode="decimal"
            value={amount}
            onChange={(e) => setAmount(e.target.value.replace(/[^0-9.]/g, ""))}
            placeholder="0.00"
            className="bg-transparent flex-1 min-w-0 py-1.5 outline-none font-mono text-[15px]"
            disabled={busy}
          />
        </div>
        {[0.25, 0.5, 1].map((pct) => (
          <button
            key={pct}
            onClick={() => srcBal != null && setAmount((srcBal * pct).toFixed(2))}
            className="px-2 py-1 text-[11px] font-mono border border-pixel-border rounded text-pixel-muted hover:text-green-400 hover:border-green-400/70 transition-colors shrink-0"
            disabled={busy || srcBal == null || srcBal <= 0}
            title={srcBal != null ? `$${(srcBal * pct).toFixed(2)}` : undefined}
          >
            {pct === 1 ? "MAX" : `${pct * 100}%`}
          </button>
        ))}
      </div>

      {/* THE button */}
      <button
        onClick={() => { void handleGo(); }}
        disabled={busy || !amtOk || srcBal == null || srcBal <= 0}
        className={`pixel-btn w-full py-2 text-[14px] font-mono tracking-wider disabled:opacity-30 disabled:cursor-not-allowed gap-2 ${
          effectiveCustom
            ? "border-amber-400/80 text-amber-400 hover:bg-amber-400/10"
            : "border-green-400/80 text-green-400 hover:bg-green-400/10"
        }`}
      >
        {busy && <Spinner />}
        {actionLabel}
      </button>
      <div className="text-[10px] text-pixel-muted text-center -mt-1">
        {source === "eoa"
          ? "MetaMask pops up once (needs a little POL for gas)"
          : "gasless — no MetaMask popup"}
      </div>

      {/* Transfer: swap the destination for any address. Collapsed by
          default so deposit/withdraw stay a two-tap flow. */}
      {!showDest ? (
        <button
          onClick={() => setShowDest(true)}
          className="text-[11px] text-pixel-muted hover:text-pixel-white font-mono tracking-[0.12em] underline-offset-2 hover:underline"
        >
          ⌄ SEND TO SOMEONE ELSE
        </button>
      ) : (
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] tracking-[0.15em] text-pixel-muted w-6 shrink-0">TO</span>
          <input
            type="text"
            value={dest}
            onChange={(e) => setDest(e.target.value)}
            placeholder="0x… any address"
            className="bg-pixel-bg border border-pixel-border rounded px-2 py-1.5 flex-1 min-w-0 font-mono text-xs outline-none"
            disabled={busy}
            spellCheck={false}
          />
          <button
            onClick={() => { setDest(""); setShowDest(false); }}
            className="px-2 py-1 text-[11px] font-mono border border-pixel-border rounded text-pixel-muted hover:text-green-400 hover:border-green-400/70 transition-colors shrink-0"
            disabled={busy}
            title="Back to moving between your own two wallets"
          >
            ✕
          </button>
        </div>
      )}

      {status && (
        <div className="text-xs text-green-400 font-mono flex items-center gap-2">
          {(busy || pendingOp) && <Spinner />}
          <span>{status}</span>
        </div>
      )}
      {error && (
        <div className="text-xs text-red-400 font-mono break-all">{error}</div>
      )}
    </div>
  );
}
