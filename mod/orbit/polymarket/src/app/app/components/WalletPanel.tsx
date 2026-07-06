"use client";

// Deposit-wallet panel for Polymarket V2 (POLY_1271 sigType=3 trading
// account).
//
// Why a new component when PolymarketAccountPanel already manages the
// V1 Safe: V2 phased Safes out. Trading now happens through a
// CREATE2-derived deposit wallet owned by the per-user *backend signer*
// (not the user's EOA). The wallet is on a totally different address
// than the V1 Safe, has a different deposit/withdraw flow, and is the
// only thing Polymarket's V2 matcher accepts for new orders.
//
// This panel surfaces just two things, in the simplest possible form
// for a non-technical user:
//   - "Your trading balance" (USDC.e in the deposit wallet)
//   - DEPOSIT button → MetaMask popup that sends USDC.e to the wallet
//   - WITHDRAW button → backend signs a Batch + relayer pays gas; USDC.e
//     lands at the destination address with no MetaMask prompt
//
// The PolymarketAccountPanel (V1 Safe) is kept around as a legacy
// "migrate funds out" UI for users who still have USDC sitting in the
// old Safe — but it's no longer the trading address.

import { useCallback, useEffect, useState } from "react";
import { BrowserProvider, Contract, formatUnits, parseUnits } from "ethers";
import { useAuth } from "../context/AuthContext";
import { USDC_E } from "../lib/polymarketContracts";
import { ensureChain, networkById } from "../lib/networks";

const ERC20_TRANSFER_ABI = [
  "function transfer(address to, uint256 amount) returns (bool)",
];

interface InfoResp {
  eoa: string;
  backendSigner: string;
  depositWallet: string;
  deployed: boolean;
  // Base-units strings (1e6 = $1). Stringified to dodge JS Number precision.
  // usdcBalance = WRAPPED V2 collateral (the tradable balance Polymarket
  // counts). rawUsdceBalance = USDC.e sitting in the wallet un-wrapped
  // (deposited outside the panel flow, or a wrap that never ran) — real
  // money that shows as $0.00 tradable until wrapped. nativeUsdcBalance =
  // Circle-native USDC, which the Onramp cannot wrap; surfaced so the
  // funds are at least visible.
  // `null` = the on-chain read failed (backend/RPC unreachable) — distinct
  // from "0" = confirmed empty. Never render null as $0.00; show it as
  // "unavailable" so a flaky RPC doesn't make a funded wallet look drained.
  usdcBalance: string | null;
  rawUsdceBalance?: string | null;
  nativeUsdcBalance?: string | null;
  balanceUnavailable?: boolean;
}

function shortAddr(a: string): string {
  return `${a.slice(0, 6)}…${a.slice(-4)}`;
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
  const [loading, setLoading] = useState(false);
  const [depositAmount, setDepositAmount] = useState("");
  const [withdrawAmount, setWithdrawAmount] = useState("");
  const [withdrawDest, setWithdrawDest] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  // When a deposit or withdrawal returns success, the on-chain balance
  // hasn't necessarily landed yet (relayer batch may still be mining).
  // Stash the pre-op balance so the polling loop can detect when the
  // wrapped-collateral balance actually moves, then clear the spinner.
  const [pendingOp, setPendingOp] = useState<
    { prevBalance: string; label: string; startedAt: number } | null
  >(null);
  // Collapsed by default — the header keeps the balance visible, and the
  // deposit/withdraw forms (rarely needed once funded) fold away. Persisted
  // so the choice sticks across reloads.
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    if (typeof window === "undefined") return true;
    try { return localStorage.getItem("poly_wallet_collapsed") !== "false"; } catch { return true; }
  });
  useEffect(() => {
    try { localStorage.setItem("poly_wallet_collapsed", String(collapsed)); } catch {}
  }, [collapsed]);

  const eoa = auth.address;

  // Pre-fill withdraw destination with the user's connected EOA — the
  // "send it back to me" default that 90% of users want.
  useEffect(() => {
    if (eoa && !withdrawDest) setWithdrawDest(eoa);
  }, [eoa, withdrawDest]);

  const refresh = useCallback(async () => {
    if (!eoa) return;
    setLoading(true);
    try {
      const res = await fetch(
        `/api/polymarket/deposit-wallet/info?eoa=${eoa}`,
        { cache: "no-store" },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as InfoResp;
      const unavailable = data.balanceUnavailable || data.usdcBalance == null;
      // A failed on-chain read comes back with a null balance. Don't let it
      // clobber a good last-known value with $0.00 — keep the prior balance
      // fields and just refresh address/deployed. The next poll self-heals.
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
        setError("Balance temporarily unavailable (RPC) — retrying…");
      } else {
        setError(null);
      }
      setPendingOp((prev) => {
        if (!prev) return null;
        // Only treat a KNOWN balance as a settled update.
        if (!unavailable && data.usdcBalance !== prev.prevBalance) {
          setStatus(`${prev.label} ✓ balance updated.`);
          return null;
        }
        // Bail out after 90s so a stuck relayer doesn't spin forever.
        if (Date.now() - prev.startedAt > 90_000) return null;
        return prev;
      });
    } catch (e) {
      setError(`Could not load deposit wallet: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLoading(false);
    }
  }, [eoa]);

  useEffect(() => {
    refresh();
    // Poll fast while we're waiting for a deposit/withdraw to land,
    // slow otherwise.
    const t = setInterval(refresh, pendingOp ? 2_000 : 15_000);
    return () => clearInterval(t);
  }, [refresh, pendingOp]);

  // null = balance unknown (read failed, no last-known value). Render this
  // as "unavailable", never as $0.00.
  const balanceUsd: number | null =
    info && info.usdcBalance != null ? Number(info.usdcBalance) / 1_000_000 : null;
  const rawUsd = info ? Number(info.rawUsdceBalance ?? "0") / 1_000_000 : 0;
  const nativeUsd = info ? Number(info.nativeUsdcBalance ?? "0") / 1_000_000 : 0;

  // Wrap any un-wrapped USDC.e sitting in the wallet into tradable V2
  // collateral. Covers deposits that arrived outside the panel's own
  // flow (direct sends, bridges) or a deposit whose auto-wrap failed.
  const handleWrap = useCallback(async () => {
    if (!info || !eoa) return;
    setError(null);
    setStatus("Wrapping USDC.e for trading (gasless)…");
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
      setStatus(`Wrapping $${rawUsd.toFixed(2)} — finalizing on-chain…`);
      setPendingOp({
        prevBalance: info.usdcBalance ?? "",
        label: `Wrapped $${rawUsd.toFixed(2)}`,
        startedAt: Date.now(),
      });
      refresh();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg.length > 250 ? msg.slice(0, 250) + "…" : msg);
    } finally {
      setBusy(false);
    }
  }, [info, eoa, rawUsd, refresh]);

  const copyAddr = useCallback(async () => {
    if (!info) return;
    try {
      await navigator.clipboard.writeText(info.depositWallet);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {}
  }, [info]);

  const handleDeposit = useCallback(async () => {
    if (!info || !eoa) return;
    const amt = parseFloat(depositAmount);
    if (!Number.isFinite(amt) || amt <= 0) {
      setError("Enter a deposit amount in USDC.");
      return;
    }
    setError(null);
    setStatus(null);
    setBusy(true);
    try {
      // Make sure MetaMask is on Polygon — otherwise the USDC.e contract
      // address doesn't exist and the transfer fails cryptically.
      const ethereum = (window as unknown as { ethereum?: {
        request: (args: { method: string; params?: unknown[] }) => Promise<unknown>;
      } }).ethereum;
      if (!ethereum) throw new Error("No wallet found (install MetaMask).");
      await ensureChain(ethereum, networkById("polygon")!);
      const provider = new BrowserProvider(ethereum as never);
      const signer = await provider.getSigner();
      const usdc = new Contract(USDC_E, ERC20_TRANSFER_ABI, signer);
      const amountBase = parseUnits(amt.toString(), 6);
      setStatus("Confirm the transfer in MetaMask…");
      const tx = await usdc.transfer(info.depositWallet, amountBase);
      setStatus(`Sending… tx ${tx.hash.slice(0, 10)}…`);
      await tx.wait();
      setDepositAmount("");

      // Polymarket V2 reads trading balance from a *wrapped* collateral
      // token, not raw USDC.e. Without this step the user would see
      // their USDC.e land in the wallet on Polygonscan but Polymarket
      // would still report "balance: 0" and reject orders. Auto-wrap
      // makes the deposit feel like a single click.
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
      setStatus(`Deposited + wrapped $${amt.toFixed(2)} — finalizing on-chain…`);
      setPendingOp({
        prevBalance: info.usdcBalance ?? "",
        label: `Deposited $${amt.toFixed(2)}`,
        startedAt: Date.now(),
      });
      refresh();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg.length > 200 ? msg.slice(0, 200) + "…" : msg);
    } finally {
      setBusy(false);
    }
  }, [info, eoa, depositAmount, refresh]);

  const handleWithdraw = useCallback(async () => {
    if (!info || !eoa) return;
    const amt = parseFloat(withdrawAmount);
    if (!Number.isFinite(amt) || amt <= 0) {
      setError("Enter a withdraw amount in USDC.");
      return;
    }
    if (balanceUsd == null) {
      setError("Balance unavailable right now — try again in a moment.");
      return;
    }
    if (amt > balanceUsd + 1e-6) {
      setError(`You only have $${balanceUsd.toFixed(2)} in the deposit wallet.`);
      return;
    }
    const dst = withdrawDest.trim();
    if (!/^0x[a-fA-F0-9]{40}$/.test(dst)) {
      setError("Destination must be a 0x… address (40 hex chars).");
      return;
    }
    setError(null);
    setStatus("Signing + sending via Polymarket relayer (gasless)…");
    setBusy(true);
    try {
      const amountBase = parseUnits(amt.toString(), 6).toString();
      // Wallet balance is V2 collateral (`0xC011…`), not raw USDC.e. The
      // plain /withdraw endpoint does USDC.e.transfer and reverts here
      // (the wallet has 0 raw USDC.e after deposit auto-wraps). unwrap-
      // and-send burns V2 collateral and mints USDC.e straight to dest
      // in one relayer batch.
      const res = await fetch("/api/polymarket/deposit-wallet/unwrap-and-send", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          eoa,
          destination: dst,
          amountBaseUnits: amountBase,
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
      let data: { transactionHash?: string } = {};
      try { data = JSON.parse(text); } catch {}
      setStatus(
        `Withdrawing $${amt.toFixed(2)} to ${shortAddr(dst)} — finalizing…`
        + (data.transactionHash ? ` (${data.transactionHash.slice(0, 10)}…)` : ""),
      );
      setWithdrawAmount("");
      setPendingOp({
        prevBalance: info.usdcBalance ?? "",
        label: `Withdrew $${amt.toFixed(2)} to ${shortAddr(dst)}`,
        startedAt: Date.now(),
      });
      refresh();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg.length > 250 ? msg.slice(0, 250) + "…" : msg);
    } finally {
      setBusy(false);
    }
  }, [info, eoa, withdrawAmount, withdrawDest, balanceUsd, refresh]);

  if (!auth.connected) return null;

  return (
    <div className="pixel-panel border-2 border-pixel-border p-3 space-y-3">
      {/* Header row: collapse toggle + label + address + live balance */}
      <div className="flex items-center gap-3 flex-wrap">
        <button
          onClick={() => setCollapsed((c) => !c)}
          className="flex items-center gap-1.5 hover:opacity-80"
          title={collapsed ? "Expand deposit / withdraw" : "Collapse"}
          aria-expanded={!collapsed}
        >
          <span className="text-pixel-muted text-[10px] w-2 inline-block">
            {collapsed ? "▸" : "▾"}
          </span>
          <span className="text-xs uppercase tracking-wide text-pixel-muted">
            Trading Wallet
          </span>
        </button>
        {info ? (
          <>
            <button
              onClick={copyAddr}
              className="text-xs font-mono text-pixel-fg hover:text-green-400 transition-colors"
              title={info.depositWallet}
            >
              {shortAddr(info.depositWallet)} {copied ? "✓" : "📋"}
            </button>
            <a
              href={`https://polygonscan.com/address/${info.depositWallet}`}
              target="_blank"
              rel="noreferrer noopener"
              className="text-xs text-pixel-muted hover:text-green-400"
              title="View on Polygonscan"
            >
              ↗
            </a>
            <span className="ml-auto flex items-baseline gap-2">
              <span className="text-[10px] uppercase tracking-[0.15em] text-pixel-muted">Balance</span>
              <span
                className={`font-mono text-[20px] leading-none ${balanceUsd == null ? "text-amber-400" : "text-green-400"}`}
                title={balanceUsd == null ? "On-chain read failed — retrying" : undefined}
              >
                {balanceUsd == null ? "unavailable" : `$${balanceUsd.toFixed(2)}`}
              </span>
              {(busy || pendingOp) && (
                <span
                  className="text-green-400"
                  title={pendingOp ? "Waiting for on-chain settlement…" : "Working…"}
                >
                  <Spinner />
                </span>
              )}
            </span>
          </>
        ) : (
          <span className="text-xs text-pixel-muted">
            {loading ? "loading…" : "—"}
          </span>
        )}
      </div>

      {!collapsed && (
        <>
      {!info?.deployed && info && (
        <div className="flex items-center gap-2 text-[11px] text-amber-400/90 leading-snug">
          <span className="w-1.5 h-1.5 rounded-full bg-amber-400/80 shrink-0" />
          <span>Not on-chain yet — deploys automatically on your first trade (gasless, ~10s).</span>
        </div>
      )}

      {/* Un-wrapped USDC.e detected — money is in the wallet but not
          tradable until wrapped into V2 collateral. One click fixes it. */}
      {rawUsd > 0.005 && (
        <div className="border border-amber-600/50 rounded p-2 flex items-center gap-3 flex-wrap">
          <span className="text-xs text-amber-400">
            ${rawUsd.toFixed(2)} USDC.e is in this wallet but not wrapped
            for trading yet.
          </span>
          <button
            onClick={handleWrap}
            disabled={busy || !!pendingOp}
            className="ml-auto px-3 py-1 bg-amber-700 hover:bg-amber-600 disabled:opacity-50 disabled:cursor-not-allowed text-white font-bold text-xs rounded inline-flex items-center gap-2"
          >
            {busy && <Spinner />}
            WRAP FOR TRADING
          </button>
        </div>
      )}

      {/* Native USDC can't be wrapped by Polymarket's Onramp — tell the
          user their funds exist instead of silently showing $0.00. */}
      {nativeUsd > 0.005 && (
        <div className="text-xs text-amber-400">
          ${nativeUsd.toFixed(2)} native USDC detected in this wallet.
          Polymarket trades USDC.e — swap native USDC to USDC.e (bridged)
          to make it tradable.
        </div>
      )}

      {/* DEPOSIT — amount + presets on one wrappable row, full-width action
          button below so nothing overflows in narrow sidebar mounts. */}
      <div className="border border-pixel-border/70 rounded bg-pixel-black/30 p-2 space-y-1.5">
        <div className="flex items-baseline justify-between gap-3">
          <span className="text-[11px] uppercase tracking-[0.18em] text-pixel-white">
            Deposit
          </span>
          <span className="text-[10px] text-pixel-muted text-right leading-tight min-w-0">
            USDC from your MetaMask
          </span>
        </div>
        <div className="flex items-center gap-1.5 flex-wrap">
          <div className="flex items-center flex-1 min-w-[120px] bg-pixel-bg border border-pixel-border rounded px-2">
            <span className="text-pixel-muted mr-1 font-mono">$</span>
            <input
              type="text"
              inputMode="decimal"
              value={depositAmount}
              onChange={(e) => setDepositAmount(e.target.value)}
              placeholder="0.00"
              className="bg-transparent flex-1 min-w-0 py-1.5 outline-none font-mono"
              disabled={busy}
            />
          </div>
          {[10, 50, 100].map((preset) => (
            <button
              key={preset}
              onClick={() => setDepositAmount(String(preset))}
              className="px-2 py-1 text-[11px] font-mono border border-pixel-border rounded text-pixel-muted hover:text-green-400 hover:border-green-400/70 transition-colors shrink-0"
              disabled={busy}
            >
              ${preset}
            </button>
          ))}
        </div>
        <button
          onClick={handleDeposit}
          disabled={busy || !depositAmount}
          className="pixel-btn w-full py-1.5 text-[13px] font-mono tracking-wider border-green-400/80 text-green-400 hover:bg-green-400/10 disabled:opacity-30 disabled:cursor-not-allowed gap-2"
        >
          {busy && <Spinner />}
          DEPOSIT{depositAmount ? ` $${depositAmount}` : ""}
        </button>
      </div>

      {/* WITHDRAW — destination row (with ME reset when edited), amount + MAX,
          full-width gasless action button. */}
      <div className="border border-pixel-border/70 rounded bg-pixel-black/30 p-2 space-y-1.5">
        <div className="flex items-baseline justify-between gap-3">
          <span className="text-[11px] uppercase tracking-[0.18em] text-pixel-white">
            Withdraw
          </span>
          <span className="text-[10px] text-pixel-muted text-right leading-tight min-w-0">
            Gasless — no MetaMask popup
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] tracking-[0.15em] text-pixel-muted w-6 shrink-0">TO</span>
          <input
            type="text"
            value={withdrawDest}
            onChange={(e) => setWithdrawDest(e.target.value)}
            placeholder="0x… destination"
            className="bg-pixel-bg border border-pixel-border rounded px-2 py-1.5 flex-1 min-w-0 font-mono text-xs outline-none"
            disabled={busy}
          />
          {eoa && withdrawDest.trim().toLowerCase() !== eoa.toLowerCase() && (
            <button
              onClick={() => setWithdrawDest(eoa)}
              className="px-2 py-1 text-[11px] font-mono border border-pixel-border rounded text-pixel-muted hover:text-green-400 hover:border-green-400/70 transition-colors shrink-0"
              disabled={busy}
              title="Send back to your connected wallet"
            >
              ME
            </button>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          <div className="flex items-center flex-1 min-w-0 bg-pixel-bg border border-pixel-border rounded px-2">
            <span className="text-pixel-muted mr-1 font-mono">$</span>
            <input
              type="text"
              inputMode="decimal"
              value={withdrawAmount}
              onChange={(e) => setWithdrawAmount(e.target.value)}
              placeholder="0.00"
              className="bg-transparent flex-1 min-w-0 py-1.5 outline-none font-mono"
              disabled={busy}
            />
          </div>
          <button
            onClick={() => balanceUsd != null && setWithdrawAmount(balanceUsd.toFixed(2))}
            className="px-2 py-1 text-[11px] font-mono border border-pixel-border rounded text-pixel-muted hover:text-green-400 hover:border-green-400/70 transition-colors shrink-0"
            disabled={busy || balanceUsd == null || balanceUsd <= 0}
            title="Withdraw all"
          >
            MAX
          </button>
        </div>
        <button
          onClick={handleWithdraw}
          disabled={busy || !withdrawAmount || balanceUsd == null || balanceUsd <= 0}
          className="pixel-btn w-full py-1.5 text-[13px] font-mono tracking-wider border-amber-400/80 text-amber-400 hover:bg-amber-400/10 disabled:opacity-30 disabled:cursor-not-allowed gap-2"
        >
          {busy && <Spinner />}
          WITHDRAW{withdrawAmount ? ` $${withdrawAmount}` : ""}
        </button>
      </div>
        </>
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
