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
  // Base-units string (1e6 = $1). Stringified to dodge JS Number precision.
  usdcBalance: string;
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
      setInfo(data);
      setPendingOp((prev) => {
        if (!prev) return null;
        if (data.usdcBalance !== prev.prevBalance) {
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

  const balanceUsd = info ? Number(info.usdcBalance) / 1_000_000 : 0;

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
        prevBalance: info.usdcBalance,
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
        prevBalance: info.usdcBalance,
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
      {/* Header row: label + address + live balance */}
      <div className="flex items-center gap-3 flex-wrap">
        <span className="text-xs uppercase tracking-wide text-pixel-muted">
          Trading Wallet
        </span>
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
            <span className="ml-auto text-sm flex items-center gap-2">
              <span className="text-pixel-muted">Balance:</span>
              <span className="font-mono text-green-400 text-base">
                ${balanceUsd.toFixed(2)}
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

      {!info?.deployed && info && (
        <div className="text-xs text-amber-400">
          Wallet not deployed on-chain yet — first trade attempt will
          deploy it automatically (gasless, takes ~10s).
        </div>
      )}

      {/* DEPOSIT */}
      <div className="border border-pixel-border rounded p-2 space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-xs uppercase tracking-wide text-pixel-muted">
            Deposit
          </span>
          <span className="text-[10px] text-pixel-muted">
            Sends USDC from your MetaMask
          </span>
        </div>
        <div className="flex gap-2">
          <div className="flex items-center flex-1 bg-pixel-bg border border-pixel-border rounded px-2">
            <span className="text-pixel-muted mr-1">$</span>
            <input
              type="text"
              inputMode="decimal"
              value={depositAmount}
              onChange={(e) => setDepositAmount(e.target.value)}
              placeholder="0.00"
              className="bg-transparent flex-1 py-1.5 outline-none font-mono"
              disabled={busy}
            />
          </div>
          {[10, 50, 100].map((preset) => (
            <button
              key={preset}
              onClick={() => setDepositAmount(String(preset))}
              className="px-2 text-xs border border-pixel-border rounded hover:bg-pixel-border-light"
              disabled={busy}
            >
              ${preset}
            </button>
          ))}
          <button
            onClick={handleDeposit}
            disabled={busy || !depositAmount}
            className="px-4 py-1.5 bg-green-700 hover:bg-green-600 disabled:opacity-50 disabled:cursor-not-allowed text-white font-bold text-sm rounded inline-flex items-center gap-2"
          >
            {busy && <Spinner />}
            DEPOSIT
          </button>
        </div>
      </div>

      {/* WITHDRAW */}
      <div className="border border-pixel-border rounded p-2 space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-xs uppercase tracking-wide text-pixel-muted">
            Withdraw
          </span>
          <span className="text-[10px] text-pixel-muted">
            Gasless — no MetaMask popup
          </span>
        </div>
        <div className="flex gap-2">
          <input
            type="text"
            value={withdrawDest}
            onChange={(e) => setWithdrawDest(e.target.value)}
            placeholder="0x… destination"
            className="bg-pixel-bg border border-pixel-border rounded px-2 py-1.5 flex-1 font-mono text-xs outline-none"
            disabled={busy}
          />
        </div>
        <div className="flex gap-2">
          <div className="flex items-center flex-1 bg-pixel-bg border border-pixel-border rounded px-2">
            <span className="text-pixel-muted mr-1">$</span>
            <input
              type="text"
              inputMode="decimal"
              value={withdrawAmount}
              onChange={(e) => setWithdrawAmount(e.target.value)}
              placeholder="0.00"
              className="bg-transparent flex-1 py-1.5 outline-none font-mono"
              disabled={busy}
            />
          </div>
          <button
            onClick={() => setWithdrawAmount(balanceUsd.toFixed(2))}
            className="px-2 text-xs border border-pixel-border rounded hover:bg-pixel-border-light"
            disabled={busy || balanceUsd <= 0}
            title="Withdraw all"
          >
            MAX
          </button>
          <button
            onClick={handleWithdraw}
            disabled={busy || !withdrawAmount || balanceUsd <= 0}
            className="px-4 py-1.5 bg-amber-700 hover:bg-amber-600 disabled:opacity-50 disabled:cursor-not-allowed text-white font-bold text-sm rounded inline-flex items-center gap-2"
          >
            {busy && <Spinner />}
            WITHDRAW
          </button>
        </div>
      </div>

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
