"use client";

// Authorize the backend signer to place orders on the user's proxy without
// requiring a MetaMask prompt for every trade.
//
// Polymarket proxies are Gnosis Safes deployed at a deterministic address
// from the EOA. The CTF Exchange supports three signature types:
//   sigType=0 EOA              — direct ECDSA from a normal wallet
//   sigType=1 POLY_PROXY       — ECDSA from the EOA that owns the proxy;
//                                exchange recomputes proxy address and
//                                checks it matches `order.maker`. Only the
//                                EOA itself can authorize — useless for
//                                backend delegation.
//   sigType=2 POLY_GNOSIS_SAFE — exchange calls Safe.isValidSignature(),
//                                which returns OK if the recovered signer
//                                is any current Safe owner.
//
// We use (2). The one-time setup is: add the backend EOA as an additional
// owner of the user's Safe with threshold=1. After that the backend can
// sign every order on the proxy's behalf with no MetaMask popup.
//
// All Safe interaction goes through Interface.encodeFunctionData() +
// window.ethereum — no @safe-global SDK (it breaks Next.js).

import { useCallback, useEffect, useState } from "react";
import {
  BrowserProvider,
  Contract,
  Interface,
  JsonRpcProvider,
  formatEther,
  parseEther,
  zeroPadValue,
  concat,
} from "ethers";
import { useAuth } from "../context/AuthContext";
import { getProxyAddress } from "../lib/polymarketProxy";
import { ensureChain, networkById, withRpcFallback } from "../lib/networks";

// Subset of Gnosis Safe v1.x ABI we need. `addOwnerWithThreshold` is the
// canonical "add another co-signer" entrypoint — only callable by the Safe
// itself (i.e. via execTransaction). `getOwners` is a free view for
// checking whether the backend is already authorized.
const SAFE_ABI = [
  "function getOwners() view returns (address[])",
  "function addOwnerWithThreshold(address owner, uint256 _threshold)",
  "function execTransaction(address to, uint256 value, bytes data, uint8 operation, uint256 safeTxGas, uint256 baseGas, uint256 gasPrice, address gasToken, address refundReceiver, bytes signatures) payable returns (bool)",
];

// Backend's per-EOA signing key, served by polymarket-api (/signer/address).
async function fetchBackendSigner(eoa: string): Promise<string> {
  const res = await fetch(
    `/api/polymarket/signer/address?eoa=${eoa.toLowerCase()}`,
  );
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`signer/address ${res.status} ${text.slice(0, 100)}`);
  }
  const j = (await res.json()) as { signer?: string; error?: string };
  if (!j.signer) throw new Error(j.error || "no signer in response");
  return j.signer;
}

// Mirror of the Gnosis Safe pre-validated signature layout used in
// PolymarketAccountPanel.handleWithdraw — accepted by the Safe when
// msg.sender == the owner whose address is encoded in `r`. We pad the
// EOA, leave `s` zero, and set v=1 to signal "pre-validated".
function prevalidatedSignature(owner: string): string {
  const r = zeroPadValue(owner, 32);
  const s = "0x" + "00".repeat(32);
  const v = "0x01";
  return concat([r, s, v]);
}

interface BackendState {
  // null while we're still resolving / fetching for the first time
  proxy: string | null;
  proxyDeployed: boolean | null;
  backendSigner: string | null;
  owners: string[] | null;
  /// MATIC balance of the backend signer EOA, in ether. Tracked so a low
  /// gas balance is surfaced before it can quietly break anything. With
  /// the current order-placement flow the backend never actually sends a
  /// tx (orders are HMAC + EIP-712 only, Polymarket pays settlement gas)
  /// — but we monitor anyway in case a future flow needs it, and so the
  /// user has a clear "the wallet is alive" signal.
  signerGas: number | null;
}

function isOwnedBy(owners: string[] | null, addr: string | null): boolean {
  if (!owners || !addr) return false;
  const t = addr.toLowerCase();
  return owners.some((o) => o.toLowerCase() === t);
}

export default function BackendSignerPanel() {
  const { auth } = useAuth();
  const [s, setS] = useState<BackendState>({
    proxy: null,
    proxyDeployed: null,
    backendSigner: null,
    owners: null,
    signerGas: null,
  });
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copiedBackend, setCopiedBackend] = useState(false);
  const [fundAmount, setFundAmount] = useState("0.1");

  const copyBackendAddr = useCallback(async () => {
    if (!s.backendSigner) return;
    try {
      await navigator.clipboard.writeText(s.backendSigner);
      setCopiedBackend(true);
      setTimeout(() => setCopiedBackend(false), 1500);
    } catch {}
  }, [s.backendSigner]);

  // Two thresholds calibrated to typical Polygon gas: a single Safe owner
  // tx is ~$0.005 worth of MATIC at recent prices. 0.05 MATIC ≈ ~50
  // future operations; below that we red-banner. 0.5 MATIC is the
  // "comfortable" green zone — we still surface the balance for visibility.
  const LOW_GAS = 0.05;
  const OK_GAS = 0.5;
  const gas = s.signerGas;
  const gasColor =
    gas === null
      ? "text-pixel-gray"
      : gas < LOW_GAS
      ? "text-red-400"
      : gas < OK_GAS
      ? "text-amber-400"
      : "text-green-400";
  const gasNeedsFunding = gas !== null && gas < LOW_GAS;

  const refresh = useCallback(async () => {
    if (!auth.address) return;
    try {
      const proxy = await getProxyAddress(auth.address);
      const backendSigner = await fetchBackendSigner(auth.address);

      // On-chain reads: code + owners (when deployed) + backend signer
      // MATIC. If the RPC call THROWS, we preserve previous values to
      // avoid a flaky network flipping AUTO-TRADING from ON to OFF —
      // that previously had users hitting TURN ON twice (and paying gas
      // twice) because a single drpc.org timeout would clear the owners
      // array and `isOwnedBy(null, x)` returns false.
      const polygon = networkById("polygon")!;
      try {
        const { proxyDeployed, owners, signerGas } = await withRpcFallback(
          polygon,
          async (url) => {
            const provider = new JsonRpcProvider(url);
            const code = await provider.getCode(proxy);
            const deployed = code !== "0x";
            let owners: string[] | null = null;
            if (deployed) {
              try {
                const c = new Contract(proxy, SAFE_ABI, provider);
                owners = (await c.getOwners()) as string[];
              } catch {
                owners = null;
              }
            }
            const gasWei = await provider.getBalance(backendSigner);
            const signerGas = Number(formatEther(gasWei));
            return { proxyDeployed: deployed, owners, signerGas };
          },
        );
        setS({ proxy, proxyDeployed, backendSigner, owners, signerGas });
      } catch (rpcErr) {
        // All RPCs failed — keep last-known owners/gas so the ON state
        // persists. Still refresh proxy + backendSigner since those came
        // from non-RPC sources.
        setS((prev) => ({
          ...prev,
          proxy,
          backendSigner,
        }));
        // Quiet log; don't surface as a hard error — it's transient.
        // eslint-disable-next-line no-console
        console.warn("[BackendSignerPanel] RPC fallback exhausted; preserved last-known on-chain state:", rpcErr);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [auth.address]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Re-check every 30s so a freshly-confirmed tx (deploy or addOwner) lands
  // in the panel without the user having to hit refresh manually.
  useEffect(() => {
    if (!auth.address) return;
    const t = setInterval(() => {
      void refresh();
    }, 30_000);
    return () => clearInterval(t);
  }, [auth.address, refresh]);

  const enabled = isOwnedBy(s.owners, s.backendSigner);

  // One-click fund: sends MATIC from the user's wallet (auth.address) to
  // the backend signer EOA on Polygon. Reuses the same pattern as
  // PolymarketAccountPanel.handleDeposit so wallet quirks (chain switch,
  // tx populator bugs) behave consistently. Declared after `refresh` so
  // the useCallback dep array doesn't hit a TDZ ReferenceError on first
  // render — `const` bindings aren't hoisted.
  const handleFund = useCallback(async () => {
    setError(null);
    setStatus(null);
    if (!auth.address || !s.backendSigner) {
      setError("MISSING STATE");
      return;
    }
    const n = parseFloat(fundAmount);
    if (!Number.isFinite(n) || n <= 0) {
      setError("ENTER AMOUNT > 0");
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
      const valueHex = "0x" + parseEther(fundAmount).toString(16);
      setStatus("CONFIRM IN WALLET...");
      const txHash = (await ethereum.request({
        method: "eth_sendTransaction",
        params: [{ from: auth.address, to: s.backendSigner, value: valueHex }],
      })) as string;
      setStatus(`SENT ${txHash.slice(0, 10)}... WAITING CONFIRM`);
      const provider = new BrowserProvider(ethereum as never);
      const receipt = await provider.waitForTransaction(txHash);
      if (receipt && receipt.status === 0) {
        setError(`REVERTED ${txHash.slice(0, 10)}...`);
        return;
      }
      setStatus(`FUNDED ${n.toFixed(4)} MATIC → backend wallet`);
      void refresh();
    } catch (e) {
      setError((e instanceof Error ? e.message : String(e)).slice(0, 200));
    } finally {
      setBusy(false);
    }
  }, [auth.address, s.backendSigner, fundAmount, refresh]);

  // POST /admin/restart — the backend replies 200 then exits, docker's
  // restart: unless-stopped policy brings it back in ~2s. We poll /health
  // so the user sees a clear "back" state rather than wondering if it
  // worked. State (this signer, the live engine config, persisted strats)
  // resumes automatically because everything important lives in the
  // mounted /data volume.
  const handleRestartApi = useCallback(async () => {
    setError(null);
    setStatus(null);
    setBusy(true);
    try {
      const res = await fetch("/api/polymarket/admin/restart", { method: "POST" });
      if (!res.ok) {
        setError(`RESTART HTTP ${res.status}`);
        return;
      }
      setStatus("API restarting…");
      // Brief grace before polling — the process replies 200 then exits
      // ~150ms later, and docker takes another ~1-2s to respawn.
      await new Promise((r) => setTimeout(r, 1500));
      const deadline = Date.now() + 30_000;
      while (Date.now() < deadline) {
        try {
          const h = await fetch("/api/polymarket/health", { cache: "no-store" });
          if (h.ok) {
            setStatus("API back ✓");
            void refresh();
            return;
          }
        } catch {
          // health endpoint is briefly unreachable while the container
          // respawns — keep polling.
        }
        await new Promise((r) => setTimeout(r, 1000));
      }
      setError("API did not come back within 30s — check docker logs");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  const handleEnable = useCallback(async () => {
    setError(null);
    setStatus(null);
    if (!auth.address || !s.proxy || !s.backendSigner) {
      setError("MISSING STATE");
      return;
    }
    if (s.proxyDeployed === false) {
      setError("DEPLOY PROXY FIRST (see PROXY panel above)");
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

      // Build the inner addOwnerWithThreshold call. Threshold stays at 1
      // so either the EOA or the backend can sign for the Safe (NOT 2,
      // which would force a co-sign for every tx and re-introduce
      // MetaMask popups — defeating the whole point of this panel).
      const safeIface = new Interface(SAFE_ABI);
      const innerData = safeIface.encodeFunctionData(
        "addOwnerWithThreshold",
        [s.backendSigner, 1],
      );

      // Wrap in execTransaction. `to` is the Safe itself because
      // addOwnerWithThreshold is a Safe method. Pre-validated signature
      // layout works here for the same reason as withdrawals: msg.sender
      // is the EOA owner.
      const sig = prevalidatedSignature(auth.address);
      const outerData = safeIface.encodeFunctionData("execTransaction", [
        s.proxy, // to: the Safe itself
        0, // value
        innerData, // inner addOwnerWithThreshold call
        0, // operation: CALL
        0, // safeTxGas
        0, // baseGas
        0, // gasPrice
        "0x0000000000000000000000000000000000000000", // gasToken
        "0x0000000000000000000000000000000000000000", // refundReceiver
        sig,
      ]);

      // Pre-flight simulation so we surface real revert reasons before
      // asking the user to confirm a tx. Same pattern PolymarketAccount
      // Panel uses for the deploy step.
      const provider = new BrowserProvider(ethereum as never);
      setStatus("SIMULATING...");
      try {
        await provider.call({ from: auth.address, to: s.proxy, data: outerData });
      } catch (simErr: unknown) {
        const e = simErr as {
          shortMessage?: string;
          reason?: string;
          info?: { error?: { message?: string } };
          message?: string;
        };
        const detail =
          e.reason ||
          e.shortMessage ||
          e.info?.error?.message ||
          e.message ||
          "unknown";
        setError(`SIM REVERT: ${detail.slice(0, 200)}`);
        return;
      }

      setStatus("CONFIRM IN WALLET...");
      const txHash = (await ethereum.request({
        method: "eth_sendTransaction",
        params: [{ from: auth.address, to: s.proxy, data: outerData }],
      })) as string;
      setStatus(`SENT ${txHash.slice(0, 10)}... WAITING CONFIRM`);
      const receipt = await provider.waitForTransaction(txHash);
      if (receipt && receipt.status === 0) {
        setError(`REVERTED ${txHash.slice(0, 10)}... (the EOA may not be an owner of this Safe)`);
        return;
      }
      setStatus("BACKEND TRADING ENABLED ✓ — no MetaMask popups for trades from here on");
      void refresh();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg.slice(0, 200));
    } finally {
      setBusy(false);
    }
  }, [auth.address, s.proxy, s.proxyDeployed, s.backendSigner, refresh]);

  if (!auth.address) return null;

  // Newbie copy: "AUTO-TRADING" reads as a feature toggle. "Backend signer"
  // is the technical truth (it's an additional Safe owner used as a server-
  // side signer) — surfaced in the address row + advanced disclosure but
  // not in the headline. The single primary CTA is "TURN ON AUTO-TRADING".

  return (
    <div className="pixel-panel border-2 border-pixel-border">
      <div className="px-3 py-1.5 border-b border-pixel-border/60 flex items-center gap-2 bg-pixel-black/40">
        <div
          className={`w-1.5 h-1.5 shrink-0 ${
            enabled ? "bg-green-400" : "bg-amber-400"
          }`}
        />
        <span className="text-[13px] text-pixel-white tracking-[0.18em]">
          AUTO-TRADING
        </span>
        <span
          className={`text-[11px] font-mono px-1 py-0 border ml-1 ${
            enabled
              ? "border-green-400/60 text-green-400 bg-green-400/10"
              : "border-amber-400/60 text-amber-400 bg-amber-400/5"
          }`}
        >
          {enabled ? "ON" : "OFF"}
        </span>
        <button
          onClick={() => {
            void refresh();
          }}
          className="text-[14px] text-pixel-gray hover:text-green-400 px-1 ml-auto"
          title="Re-check on-chain"
        >
          ↻
        </button>
      </div>

      <div className="px-3 py-2 space-y-1.5 bg-pixel-black/20">
        {/* Gas row — always visible so the user knows the backend wallet
            is alive and funded. Polymarket pays settlement gas, but we
            still surface a low-balance prompt because future operations
            (proxy approvals, recovery) may require backend-paid txs.
            One-click FUND beside the balance lets the user top up from
            their connected wallet without copy-pasting addresses. */}
        {s.backendSigner && (
          <div className="px-2 py-1.5 border border-pixel-border/40 bg-pixel-black/40 space-y-1">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[11px] text-pixel-gray tracking-[0.15em] shrink-0">
                GAS
              </span>
              <span className={`text-[13px] font-mono tabular-nums ${gasColor}`}>
                {gas === null ? "—" : `${gas.toFixed(4)} MATIC`}
              </span>
              <span className="text-[10px] text-pixel-gray/60 shrink-0">
                (backend wallet)
              </span>
              <button
                onClick={() => {
                  void copyBackendAddr();
                }}
                className="ml-auto text-[10px] tracking-[0.12em] text-pixel-gray hover:text-green-400 border border-pixel-border/60 hover:border-green-400/60 px-1.5 py-0.5 shrink-0"
                title="Copy backend wallet address"
              >
                {copiedBackend ? "✓" : "COPY"}
              </button>
            </div>
            <div className="flex items-center gap-1.5">
              <input
                type="text"
                inputMode="decimal"
                placeholder="0.1"
                value={fundAmount}
                onChange={(e) => setFundAmount(e.target.value.replace(/[^0-9.]/g, ""))}
                className="pixel-input-sm font-mono text-[12px] h-[22px] flex-1 min-w-0"
              />
              <span className="text-[10px] text-pixel-gray font-mono">MATIC</span>
              {/* Quick presets so the user doesn't have to type for the
                  common amounts. 0.1 / 0.5 / 1 cover the usual range. */}
              {[0.1, 0.5, 1].map((v) => (
                <button
                  key={v}
                  onClick={() => setFundAmount(String(v))}
                  className="text-[10px] font-mono text-pixel-gray hover:text-green-400 border border-pixel-border/60 hover:border-green-400/60 px-1.5 py-0 h-[22px] shrink-0"
                  title={`Set to ${v} MATIC`}
                >
                  {v}
                </button>
              ))}
              <button
                onClick={() => {
                  void handleFund();
                }}
                disabled={busy || !fundAmount || parseFloat(fundAmount) <= 0}
                className="pixel-btn text-[11px] px-2 h-[22px] border-green-400 text-green-400 hover:bg-green-400/10 disabled:opacity-30 disabled:cursor-not-allowed shrink-0"
                title="Send MATIC from your connected wallet to the backend signer"
              >
                FUND
              </button>
            </div>
          </div>
        )}

        {/* Low-gas funding prompt — shown when backend wallet is below
            the LOW_GAS threshold. Gives the exact address to send to and
            a recommended amount so the user can fund it from any source
            without thinking about it. */}
        {gasNeedsFunding && s.backendSigner && (
          <div className="border border-red-400/50 bg-red-400/5 px-2 py-1.5 space-y-1">
            <div className="text-[12px] text-red-400 tracking-[0.12em]">
              FUND BACKEND WALLET WITH POLYGON MATIC
            </div>
            <div className="text-[11px] text-pixel-gray leading-snug">
              The backend signer has{" "}
              <span className="text-red-400 font-mono">
                {gas === null ? "?" : gas.toFixed(4)} MATIC
              </span>{" "}
              — send <span className="text-pixel-white">≥ 0.1 MATIC</span> on{" "}
              <span className="text-pixel-white">Polygon</span> to the
              address below so it can pay gas if needed. (Order placement
              uses Polymarket&apos;s gas; this is a safety buffer.)
            </div>
            <div className="flex items-center gap-2">
              <span
                className="font-mono text-[11px] text-pixel-white truncate flex-1"
                title={s.backendSigner}
              >
                {s.backendSigner}
              </span>
              <button
                onClick={() => {
                  void copyBackendAddr();
                }}
                className="text-[11px] tracking-[0.12em] text-amber-400 hover:bg-amber-400/10 border border-amber-400/60 px-2 py-0.5 shrink-0"
              >
                {copiedBackend ? "✓ COPIED" : "COPY"}
              </button>
              <a
                href={`https://polygonscan.com/address/${s.backendSigner}`}
                target="_blank"
                rel="noreferrer"
                className="text-[12px] text-pixel-gray hover:text-green-400 px-1 shrink-0"
                title="View on Polygonscan"
              >
                ↗
              </a>
            </div>
          </div>
        )}

        {!enabled && (
          <>
            <div className="text-[12px] text-pixel-gray leading-snug">
              Sign once with your wallet to let the strat copy-trade for
              you. After this you&apos;ll never see another MetaMask popup
              per trade — the engine signs orders server-side and keeps
              running even if you close your browser.
            </div>
            <div className="text-[11px] text-pixel-gray/80 leading-snug">
              You keep custody: you can still WITHDRAW from your proxy at
              any time. The server can sign trades on the proxy but cannot
              move funds out.
            </div>
            <button
              onClick={() => {
                void handleEnable();
              }}
              disabled={
                busy ||
                !s.proxy ||
                !s.backendSigner ||
                s.proxyDeployed === false
              }
              className="pixel-btn w-full text-[14px] px-3 py-1.5 border-amber-400 text-amber-400 hover:bg-amber-400/10 disabled:opacity-30 disabled:cursor-not-allowed mt-1"
              title={
                s.proxyDeployed === false
                  ? "Deploy your proxy first (PROXY panel above)"
                  : "Sign once with MetaMask to enable auto-trading"
              }
            >
              {s.proxyDeployed === false
                ? "DEPLOY PROXY FIRST ↑"
                : "TURN ON AUTO-TRADING"}
            </button>
          </>
        )}

        {enabled && (
          <div className="flex items-center gap-2 border border-green-400/40 bg-green-400/5 px-2 py-1.5">
            <span className="text-[14px] text-green-400 shrink-0">✓</span>
            <span className="text-[12px] text-pixel-gray flex-1 leading-snug">
              Auto-trading on. The engine signs every copy trade for you —
              no popups. Works in the background until you stop it.
            </span>
          </div>
        )}

        {busy && (
          <div className="text-[12px] text-pixel-gray font-mono animate-pulse">
            working…
          </div>
        )}
        {status && (
          <div className="text-[12px] text-amber-400 font-mono break-all">
            {status}
          </div>
        )}
        {error && (
          <div className="text-[12px] text-red-400 font-mono break-all">
            {error}
          </div>
        )}

        {/* Advanced — collapsed by default. Power users can see the
            backend signer address + Polygonscan link to verify which key
            was authorized on their Safe. RESTART API is hidden here too so
            it's reachable when the engine wedges but not a one-click
            footgun for a casual user. */}
        {s.backendSigner && (
          <details className="text-[11px] text-pixel-gray/70 pt-1 mt-1 border-t border-pixel-border/30">
            <summary className="cursor-pointer hover:text-green-400 tracking-[0.12em]">
              advanced
            </summary>
            <div className="mt-1.5 space-y-1.5">
              <div className="flex items-center gap-2">
                <span className="tracking-[0.12em] text-pixel-gray/80 w-16 shrink-0">
                  signer
                </span>
                <span
                  className="font-mono text-[11px] truncate flex-1"
                  title={s.backendSigner}
                >
                  {s.backendSigner}
                </span>
                <a
                  href={`https://polygonscan.com/address/${s.backendSigner}`}
                  target="_blank"
                  rel="noreferrer"
                  className="text-pixel-gray hover:text-green-400 px-1 shrink-0"
                  title="View on Polygonscan"
                >
                  ↗
                </a>
              </div>
              <div className="flex items-center gap-2">
                <span className="tracking-[0.12em] text-pixel-gray/80 w-16 shrink-0">
                  api
                </span>
                <span className="text-[11px] text-pixel-gray/80 flex-1 leading-snug">
                  Recycle the backend process if the engine seems wedged.
                  Persisted state (this signer, your live config, traders)
                  resumes automatically.
                </span>
                <button
                  onClick={() => {
                    void handleRestartApi();
                  }}
                  disabled={busy}
                  className="pixel-btn text-[11px] px-2 py-0.5 border-pixel-gray/60 text-pixel-gray hover:text-amber-400 hover:border-amber-400 disabled:opacity-30 disabled:cursor-not-allowed shrink-0"
                  title="POST /admin/restart — exits the api process; docker auto-respawns"
                >
                  RESTART API
                </button>
              </div>
              <div className="flex items-center gap-2">
                <span className="tracking-[0.12em] text-pixel-gray/80 w-16 shrink-0">
                  storage
                </span>
                <span className="text-[11px] text-pixel-gray/80 flex-1 leading-snug">
                  Clear the engine&apos;s persisted history (log, dedup,
                  cursors). Use when localStorage fills up and writes
                  start throwing QuotaExceededError. Strats + signer keys
                  are untouched.
                </span>
                <button
                  onClick={() => {
                    if (typeof window === "undefined") return;
                    let cleared = 0;
                    const keep = new Set(["poly_active_index", "poly_indexes", "poly_proxy_addresses"]);
                    // Walk every key, drop the engine bloat. Two passes
                    // since removeItem mutates the index.
                    const all: string[] = [];
                    for (let i = 0; i < window.localStorage.length; i++) {
                      const k = window.localStorage.key(i);
                      if (k) all.push(k);
                    }
                    for (const k of all) {
                      if (keep.has(k)) continue;
                      if (k.startsWith("poly_copy_") || k.startsWith("poly_engine_") || k === "poly_live_session") {
                        try { window.localStorage.removeItem(k); cleared++; } catch {}
                      }
                    }
                    setStatus(`cleared ${cleared} engine keys from localStorage`);
                  }}
                  className="pixel-btn text-[11px] px-2 py-0.5 border-pixel-gray/60 text-pixel-gray hover:text-amber-400 hover:border-amber-400 shrink-0"
                  title="Remove poly_copy_*, poly_engine_*, poly_live_session — strats + signer untouched"
                >
                  CLEAR LOCAL
                </button>
              </div>
            </div>
          </details>
        )}
      </div>
    </div>
  );
}
