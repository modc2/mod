"use client";

// Polymarket Proxy account panel. The user's "trading address" on
// Polymarket is a Gnosis Safe deployed at a deterministic address from
// their EOA. Funds at the EOA aren't visible to the CLOB. This panel
// surfaces the proxy, shows both balances, and lets the user move USDC.e
// in (DEPOSIT — ERC-20 transfer) and out (WITHDRAW — Safe.execTransaction
// with a pre-validated owner signature, no MetaMask EIP-712 prompt).

import { useCallback, useEffect, useState } from "react";
import {
  BrowserProvider,
  Contract,
  Interface,
  JsonRpcProvider,
  Signature,
  formatUnits,
  parseUnits,
  zeroPadValue,
  concat,
} from "ethers";
import { useAuth } from "../context/AuthContext";
import { getProxyAddress, POLY_PROXY_FACTORY } from "../lib/polymarketProxy";
import { USDC_E } from "../lib/polymarketContracts";
import { ensureChain, networkById, withRpcFallback } from "../lib/networks";

const ERC20_BAL_ABI = [
  "function balanceOf(address) view returns (uint256)",
  "function transfer(address to, uint256 amount) returns (bool)",
];

// Subset of Gnosis Safe v1.x ABI we need to withdraw from a deployed
// proxy. execTransaction with a pre-validated signature lets the owner
// EOA relay an inner call (USDC.transfer back to itself) without an
// EIP-712 sign prompt — Safe accepts v=1 signatures when msg.sender ==
// the owner whose address is encoded in r.
const SAFE_ABI = [
  "function execTransaction(address to, uint256 value, bytes data, uint8 operation, uint256 safeTxGas, uint256 baseGas, uint256 gasPrice, address gasToken, address refundReceiver, bytes signatures) payable returns (bool)",
];

// Polymarket Contract Proxy Factory. createProxy deploys a Gnosis Safe
// at computeProxyAddress(signer) and authorizes via EIP-712 signature over
// `CreateProxy(address paymentToken, uint256 payment, address paymentReceiver)`.
// The factory ecrecovers the EOA from that signature and uses the recovered
// address as the Safe's single owner. Pass paymentToken=0, payment=0,
// paymentReceiver=0 to self-deploy gas-only — Polymarket's own UI deploys
// lazily during the first trade, but the EOA can also self-deploy directly.
const POLY_FACTORY_ABI = [
  "function createProxy(address paymentToken, uint256 payment, address paymentReceiver, (uint8 v, bytes32 r, bytes32 s) createSig)",
];
const CREATE_PROXY_DOMAIN = {
  name: "Polymarket Contract Proxy Factory",
  chainId: 137,
  verifyingContract: POLY_PROXY_FACTORY,
};
// Must match the contract verbatim:
//   keccak256("CreateProxy(address paymentToken,uint256 payment,address paymentReceiver)")
// — NOT `CreateProxy(address user)`, despite that being the more intuitive
// shape. Mismatching here makes the contract ecrecover a wrong address and
// the deploy tx reverts during the inner setup() call.
const CREATE_PROXY_TYPES = {
  CreateProxy: [
    { name: "paymentToken", type: "address" },
    { name: "payment", type: "uint256" },
    { name: "paymentReceiver", type: "address" },
  ],
};
const ZERO_ADDR = "0x0000000000000000000000000000000000000000";

function prevalidatedSignature(owner: string): string {
  // Gnosis Safe pre-validated signature layout (65 bytes):
  //   32B r = owner address left-padded
  //   32B s = unused
  //    1B v = 0x01 (signals pre-validated)
  const r = zeroPadValue(owner, 32);
  const s = "0x" + "00".repeat(32);
  const v = "0x01";
  return concat([r, s, v]);
}

interface Balances {
  eoa: number | null;
  proxy: number | null;
}

export default function PolymarketAccountPanel() {
  const { auth } = useAuth();
  const [proxy, setProxy] = useState<string | null>(null);
  const [proxyResolving, setProxyResolving] = useState(false);
  const [proxyError, setProxyError] = useState<string | null>(null);
  const [proxyDeployed, setProxyDeployed] = useState<boolean | null>(null);
  const [bal, setBal] = useState<Balances>({ eoa: null, proxy: null });
  const [depositAmount, setDepositAmount] = useState("");
  const [withdrawAmount, setWithdrawAmount] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  // Resolve proxy address via the on-chain factory. Pulled out so the
  // refresh button can re-trigger it on demand.
  const resolveProxy = useCallback(async () => {
    if (!auth.address) return;
    setProxyResolving(true);
    setProxyError(null);
    try {
      const p = await getProxyAddress(auth.address);
      setProxy(p);
    } catch (e) {
      setProxyError(e instanceof Error ? e.message : String(e));
    } finally {
      setProxyResolving(false);
    }
  }, [auth.address]);

  useEffect(() => {
    if (!auth.address) { setProxy(null); return; }
    void resolveProxy();
  }, [auth.address, resolveProxy]);

  const refresh = useCallback(async () => {
    if (!auth.address || !proxy) return;
    const polygon = networkById("polygon")!;
    const readBal = async (addr: string) => {
      try {
        const raw: bigint = await withRpcFallback(polygon, async (url) => {
          const provider = new JsonRpcProvider(url);
          const c = new Contract(USDC_E, ERC20_BAL_ABI, provider);
          return c.balanceOf(addr);
        });
        return Number(formatUnits(raw, 6));
      } catch {
        return null;
      }
    };
    const readDeployed = async () => {
      try {
        return await withRpcFallback(polygon, async (url) => {
          const provider = new JsonRpcProvider(url);
          const code = await provider.getCode(proxy);
          return code !== "0x";
        });
      } catch {
        return null;
      }
    };
    const [eoa, prx, deployed] = await Promise.all([
      readBal(auth.address),
      readBal(proxy),
      readDeployed(),
    ]);
    setBal({ eoa, proxy: prx });
    setProxyDeployed(deployed);
  }, [auth.address, proxy]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (!auth.address || !proxy) return;
    const t = setInterval(() => { void refresh(); }, 15_000);
    return () => clearInterval(t);
  }, [auth.address, proxy, refresh]);

  const handleCopyProxy = async () => {
    if (!proxy) return;
    try {
      await navigator.clipboard.writeText(proxy);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {}
  };

  const handleDeposit = async () => {
    setError(null);
    setStatus(null);
    if (!auth.address || !proxy) return;
    const n = parseFloat(depositAmount);
    if (!Number.isFinite(n) || n <= 0) {
      setError("ENTER AMOUNT > 0");
      return;
    }
    if (bal.eoa !== null && n > bal.eoa) {
      setError(`AMOUNT EXCEEDS WALLET BALANCE $${bal.eoa.toFixed(2)}`);
      return;
    }
    if (typeof window === "undefined" || !window.ethereum) {
      setError("NO_WALLET");
      return;
    }
    setBusy(true);
    try {
      const polygon = networkById("polygon")!;
      const ethereum = window.ethereum as {
        request: (a: { method: string; params?: unknown[] }) => Promise<unknown>;
      };
      setStatus("SWITCHING TO POLYGON...");
      await ensureChain(ethereum, polygon);
      // Bypass ethers Contract.transfer — wallet quirks on Polygon can
      // trip its tx populator (see WalletFundingPanel). Encode + send raw.
      const iface = new Interface(ERC20_BAL_ABI);
      const data = iface.encodeFunctionData("transfer", [proxy, parseUnits(depositAmount, 6)]);
      setStatus("CONFIRM IN WALLET...");
      const txHash = (await ethereum.request({
        method: "eth_sendTransaction",
        params: [{ from: auth.address, to: USDC_E, data }],
      })) as string;
      setStatus(`SENT ${txHash.slice(0, 10)}... WAITING CONFIRM`);
      const provider = new BrowserProvider(ethereum as never);
      const receipt = await provider.waitForTransaction(txHash);
      if (receipt && receipt.status === 0) {
        setStatus(`REVERTED ${txHash.slice(0, 10)}...`);
      } else {
        setStatus(`DEPOSITED $${n.toFixed(2)} → PROXY`);
        setDepositAmount("");
      }
      void refresh();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg.slice(0, 200));
    } finally {
      setBusy(false);
    }
  };

  // Inner deploy step (no busy/error wrapping — callable from either the
  // dedicated DEPLOY PROXY button or auto-invoked by handleWithdraw when
  // the proxy is counterfactual). Returns true on success, false on revert.
  const deployProxyInner = async (
    ethereum: {
      request: (a: { method: string; params?: unknown[] }) => Promise<unknown>;
    },
    provider: BrowserProvider,
    eoa: string,
  ): Promise<boolean> => {
    const signer = await provider.getSigner(eoa);
    setStatus("SIGN DEPLOY AUTHORIZATION IN WALLET...");
    // Sign over the same (paymentToken, payment, paymentReceiver) we're
    // about to pass to createProxy — the contract recovers the signer
    // from this struct and uses it as the Safe owner. eoa never appears
    // in the typed message; it's only derived via ecrecover on-chain.
    const raw = await signer.signTypedData(
      CREATE_PROXY_DOMAIN,
      CREATE_PROXY_TYPES,
      { paymentToken: ZERO_ADDR, payment: 0, paymentReceiver: ZERO_ADDR },
    );
    const split = Signature.from(raw);

    const factoryIface = new Interface(POLY_FACTORY_ABI);
    const data = factoryIface.encodeFunctionData("createProxy", [
      ZERO_ADDR,
      0,
      ZERO_ADDR,
      { v: split.v, r: split.r, s: split.s },
    ]);

    // Pre-flight eth_call so we surface the actual revert reason instead
    // of MetaMask's opaque "transaction was canceled to save you gas".
    // Also lets us sanity-check that the recovered signer matches the EOA
    // BEFORE asking the user to confirm a deploy tx that would revert.
    setStatus("SIMULATING DEPLOY...");
    try {
      await provider.call({ from: eoa, to: POLY_PROXY_FACTORY, data });
    } catch (simErr: unknown) {
      const e = simErr as { shortMessage?: string; reason?: string; info?: { error?: { message?: string } }; data?: string; message?: string };
      const detail =
        e.reason ||
        e.shortMessage ||
        e.info?.error?.message ||
        e.message ||
        "unknown";
      // Recover the signer locally so we can tell the user whether the
      // signature is the problem or something downstream in the factory.
      let recovered = "?";
      try {
        const digest = (await import("ethers")).TypedDataEncoder.hash(
          CREATE_PROXY_DOMAIN,
          CREATE_PROXY_TYPES,
          { paymentToken: ZERO_ADDR, payment: 0, paymentReceiver: ZERO_ADDR },
        );
        const sigRecover = (await import("ethers")).recoverAddress(digest, raw);
        recovered = sigRecover;
      } catch {}
      setError(
        `DEPLOY SIM REVERT: ${detail.slice(0, 180)} | recovered=${recovered} eoa=${eoa}`,
      );
      return false;
    }

    setStatus("CONFIRM DEPLOY TX IN WALLET...");
    const txHash = (await ethereum.request({
      method: "eth_sendTransaction",
      params: [{ from: eoa, to: POLY_PROXY_FACTORY, data }],
    })) as string;
    setStatus(`DEPLOY SENT ${txHash.slice(0, 10)}... WAITING CONFIRM`);
    const receipt = await provider.waitForTransaction(txHash);
    if (receipt && receipt.status === 0) {
      setError(`DEPLOY REVERTED ${txHash.slice(0, 10)}... (signature recovered to a different address than your EOA — open an issue)`);
      return false;
    }
    setProxyDeployed(true);
    return true;
  };

  // Self-deploy the Polymarket proxy (Gnosis Safe) so the user can move
  // pre-funded USDC.e out before placing any trade. Flow:
  //   1) sign EIP-712 CreateProxy{user: EOA} against the factory domain
  //   2) submit factory.createProxy(0,0,0, sig) — gas-only, no payment
  //   3) refresh proxyDeployed once the receipt lands
  // After this the existing handleWithdraw path (Safe.execTransaction with
  // a pre-validated owner signature) works as designed.
  const handleDeployProxy = async () => {
    setError(null);
    setStatus(null);
    if (!auth.address || !proxy) return;
    if (typeof window === "undefined" || !window.ethereum) {
      setError("NO_WALLET");
      return;
    }
    setBusy(true);
    try {
      const polygon = networkById("polygon")!;
      const ethereum = window.ethereum as {
        request: (a: { method: string; params?: unknown[] }) => Promise<unknown>;
      };
      setStatus("SWITCHING TO POLYGON...");
      await ensureChain(ethereum, polygon);
      const provider = new BrowserProvider(ethereum as never);
      const ok = await deployProxyInner(ethereum, provider, auth.address);
      if (ok) setStatus("PROXY DEPLOYED ✓ — you can WITHDRAW now");
      void refresh();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg.slice(0, 200));
    } finally {
      setBusy(false);
    }
  };

  const handleWithdraw = async () => {
    setError(null);
    setStatus(null);
    if (!auth.address || !proxy) return;
    const n = parseFloat(withdrawAmount);
    if (!Number.isFinite(n) || n <= 0) {
      setError("ENTER AMOUNT > 0");
      return;
    }
    if (bal.proxy !== null && n > bal.proxy) {
      setError(`AMOUNT EXCEEDS PROXY BALANCE $${bal.proxy.toFixed(2)}`);
      return;
    }
    if (typeof window === "undefined" || !window.ethereum) {
      setError("NO_WALLET");
      return;
    }
    setBusy(true);
    try {
      const polygon = networkById("polygon")!;
      const ethereum = window.ethereum as {
        request: (a: { method: string; params?: unknown[] }) => Promise<unknown>;
      };
      setStatus("SWITCHING TO POLYGON...");
      await ensureChain(ethereum, polygon);

      // Withdraw uses Safe.execTransaction: the inner call is a regular
      // USDC.transfer(eoa, amount), wrapped so the Safe is the msg.sender
      // of the transfer (it's where the USDC lives).
      const provider = new BrowserProvider(ethereum as never);
      let code = await provider.getCode(proxy);
      if (code === "0x") {
        // Safe is counterfactual — auto-deploy it here so the user gets
        // the proxy + withdraw in a single WITHDRAW click (two wallet
        // prompts: deploy-tx confirm, then withdraw-tx confirm). Skipping
        // the dedicated DEPLOY button is fine because the deploy is the
        // strict prerequisite — the user can't be confused about ordering.
        setProxyDeployed(false);
        setStatus("PROXY NOT DEPLOYED — DEPLOYING FIRST...");
        const ok = await deployProxyInner(ethereum, provider, auth.address);
        if (!ok) return;
        // Re-check code after deploy receipt — withdraw call below needs
        // the contract to actually exist now.
        code = await provider.getCode(proxy);
        if (code === "0x") {
          setError("Deploy receipt confirmed but proxy still has no code — please refresh and try again.");
          return;
        }
      }

      const erc20Iface = new Interface(ERC20_BAL_ABI);
      const innerData = erc20Iface.encodeFunctionData("transfer", [
        auth.address,
        parseUnits(withdrawAmount, 6),
      ]);

      const safeIface = new Interface(SAFE_ABI);
      const sig = prevalidatedSignature(auth.address);
      const safeCall = safeIface.encodeFunctionData("execTransaction", [
        USDC_E,           // to
        0,                // value
        innerData,        // data: USDC.transfer(eoa, amount)
        0,                // operation: CALL
        0,                // safeTxGas
        0,                // baseGas
        0,                // gasPrice
        "0x0000000000000000000000000000000000000000", // gasToken
        "0x0000000000000000000000000000000000000000", // refundReceiver
        sig,              // signatures: pre-validated EOA owner sig
      ]);

      setStatus("CONFIRM IN WALLET...");
      const txHash = (await ethereum.request({
        method: "eth_sendTransaction",
        params: [{ from: auth.address, to: proxy, data: safeCall }],
      })) as string;
      setStatus(`SENT ${txHash.slice(0, 10)}... WAITING CONFIRM`);
      const receipt = await provider.waitForTransaction(txHash);
      if (receipt && receipt.status === 0) {
        setStatus(`REVERTED ${txHash.slice(0, 10)}... (likely the EOA isn't a Safe owner — Polymarket may have configured a co-signer)`);
      } else {
        setStatus(`WITHDREW $${n.toFixed(2)} ← PROXY`);
        setWithdrawAmount("");
      }
      void refresh();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg.slice(0, 200));
    } finally {
      setBusy(false);
    }
  };

  if (!auth.address) return null;

  // V1 Safe is dead weight for users who never had funds there. Stay
  // hidden by default (including while the balance read is in-flight)
  // and only render once we've confirmed > 0 USDC.e sits in the proxy.
  // Avoids the confusing "two wallets" UI for everyone on V2.
  if (bal.proxy === null || bal.proxy <= 0) return null;

  const shortProxy = proxy ? `${proxy.slice(0, 6)}…${proxy.slice(-4)}` : "...";

  return (
    <div className="pixel-panel border-2 border-pixel-border p-3 space-y-3">
      {/* Header: label + address + balance — mirrors the V2 TRADING WALLET
          card so users can recognize "this is the *other* wallet I have on
          Polymarket" at a glance instead of squinting at a one-line strip. */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="w-1.5 h-1.5 bg-purple-400 shrink-0" />
        <span className="text-xs uppercase tracking-wide text-pixel-muted">
          Legacy Proxy (V1 Safe)
        </span>
        {proxy ? (
          <>
            <button
              onClick={handleCopyProxy}
              className="text-xs font-mono text-pixel-fg hover:text-purple-400 transition-colors"
              title={proxy}
            >
              {shortProxy} {copied ? "✓" : "📋"}
            </button>
            <a
              href={`https://polygonscan.com/address/${proxy}`}
              target="_blank"
              rel="noreferrer noopener"
              className="text-xs text-pixel-muted hover:text-purple-400"
              title="View on Polygonscan"
            >
              ↗
            </a>
          </>
        ) : proxyError ? (
          <>
            <span
              className="text-[11px] text-red-400 font-mono truncate max-w-[160px]"
              title={proxyError}
            >
              {proxyError.slice(0, 40)}
            </span>
            <button
              onClick={() => { void resolveProxy(); }}
              className="text-[11px] text-amber-400 font-mono px-1.5 border border-amber-400/40 rounded"
            >
              RETRY
            </button>
          </>
        ) : proxyResolving ? (
          <span className="text-xs text-pixel-muted animate-pulse">resolving…</span>
        ) : (
          <button
            onClick={() => { void resolveProxy(); }}
            className="text-[11px] text-green-400 font-mono px-1.5 border border-green-400 rounded"
          >
            LOAD
          </button>
        )}
        <button
          onClick={() => { void resolveProxy(); void refresh(); }}
          disabled={proxyResolving}
          className="text-xs text-pixel-muted hover:text-purple-400 disabled:opacity-40"
          title="Refresh balances"
        >
          {proxyResolving ? "…" : "↻"}
        </button>
        <span className="ml-auto text-sm">
          <span className="text-pixel-muted mr-1">Balance:</span>
          <span className="font-mono text-purple-400 text-base">
            {bal.proxy === null ? "…" : `$${bal.proxy.toFixed(2)}`}
          </span>
        </span>
      </div>

      {/* DEPOSIT — send USDC.e from connected EOA into the Safe.
          Useful for users still topping up the legacy Safe (e.g. if they
          have positions held there). V2 trading goes through the new
          deposit wallet instead — see WalletPanel above. */}
      <div className="border border-pixel-border rounded p-2 space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-xs uppercase tracking-wide text-pixel-muted">
            Deposit to Proxy
          </span>
          <span className="text-[10px] text-pixel-muted">
            Sends USDC.e from your MetaMask
          </span>
        </div>
        <div className="flex gap-2">
          <div className="flex items-center flex-1 bg-pixel-bg border border-pixel-border rounded px-2">
            <span className="text-pixel-muted mr-1">$</span>
            <input
              type="text"
              inputMode="decimal"
              value={depositAmount}
              onChange={(e) => setDepositAmount(e.target.value.replace(/[^0-9.]/g, ""))}
              placeholder="0.00"
              className="bg-transparent flex-1 py-1.5 outline-none font-mono"
              disabled={busy}
            />
          </div>
          {bal.eoa !== null && bal.eoa > 0 && (
            <button
              onClick={() => setDepositAmount(bal.eoa!.toFixed(2))}
              className="px-2 text-xs border border-pixel-border rounded hover:bg-pixel-border-light"
              disabled={busy}
              title={`Deposit your full wallet balance ($${bal.eoa.toFixed(2)})`}
            >
              MAX
            </button>
          )}
          <button
            onClick={() => { void handleDeposit(); }}
            disabled={busy || !proxy || !depositAmount || (bal.eoa !== null && bal.eoa <= 0)}
            className="px-4 py-1.5 bg-purple-700 hover:bg-purple-600 disabled:opacity-50 disabled:cursor-not-allowed text-white font-bold text-sm rounded"
          >
            DEPOSIT
          </button>
        </div>
      </div>

      {/* WITHDRAW — Safe.execTransaction USDC.transfer(eoa, amount).
          The owner EOA signs a pre-validated signature so MetaMask only
          prompts for the tx send, not an EIP-712 sign popup. */}
      <div className="border border-pixel-border rounded p-2 space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-xs uppercase tracking-wide text-pixel-muted">
            Withdraw from Proxy
          </span>
          <span className="text-[10px] text-pixel-muted">
            Sends USDC.e back to your MetaMask
          </span>
        </div>
        <div className="flex gap-2">
          <div className="flex items-center flex-1 bg-pixel-bg border border-pixel-border rounded px-2">
            <span className="text-pixel-muted mr-1">$</span>
            <input
              type="text"
              inputMode="decimal"
              value={withdrawAmount}
              onChange={(e) => setWithdrawAmount(e.target.value.replace(/[^0-9.]/g, ""))}
              placeholder="0.00"
              className="bg-transparent flex-1 py-1.5 outline-none font-mono"
              disabled={busy}
            />
          </div>
          {bal.proxy !== null && bal.proxy > 0 && (
            <button
              onClick={() => setWithdrawAmount(bal.proxy!.toFixed(2))}
              className="px-2 text-xs border border-pixel-border rounded hover:bg-pixel-border-light"
              disabled={busy}
              title="Withdraw all"
            >
              MAX
            </button>
          )}
          <button
            onClick={() => { void handleWithdraw(); }}
            disabled={busy || !proxy || !withdrawAmount || (bal.proxy !== null && bal.proxy <= 0)}
            className="px-4 py-1.5 bg-amber-700 hover:bg-amber-600 disabled:opacity-50 disabled:cursor-not-allowed text-white font-bold text-sm rounded"
          >
            WITHDRAW
          </button>
        </div>
      </div>

      {proxyDeployed === false && !busy && (
        <div className="flex items-center gap-2 text-[11px]">
          <button
            onClick={() => { void handleDeployProxy(); }}
            disabled={busy || !proxy}
            className="px-2 py-0.5 border border-purple-400 text-purple-400 hover:bg-purple-400/10 disabled:opacity-30 font-mono rounded"
          >
            DEPLOY PROXY
          </button>
          <span className="text-pixel-muted">
            (auto-deploys on first WITHDRAW too — click only if you want it ready ahead of time)
          </span>
        </div>
      )}
      {busy && <div className="text-xs text-pixel-muted font-mono animate-pulse">working…</div>}
      {status && (
        <div className="text-xs text-amber-400 font-mono break-all" title={status}>
          {status}
        </div>
      )}
      {error && (
        <div className="text-xs text-red-400 font-mono break-all" title={error}>
          {error}
        </div>
      )}
    </div>
  );
}
