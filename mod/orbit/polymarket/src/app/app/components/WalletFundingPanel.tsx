"use client";

import { useState, useEffect, useCallback, useMemo, type ReactNode } from "react";
import {
  BrowserProvider,
  Contract,
  Interface,
  JsonRpcProvider,
  parseUnits,
  formatUnits,
  formatEther,
  isAddress,
} from "ethers";
import { useAuth } from "../context/AuthContext";
import { NETWORKS, NetworkConfig, NATIVE_TOKEN_ADDRESS, ensureChain, withRpcFallback, networkById } from "../lib/networks";
import { getLifiQuote, executeLifiBridge } from "../lib/lifi";

const USDC_ABI = [
  "function balanceOf(address) view returns (uint256)",
  "function transfer(address to, uint256 amount) returns (bool)",
];

const PRESETS = [10, 100, 1000, 5000, 10000];

const POLYGON = networkById("polygon")!;

type SrcAsset = "usdc" | "usdt" | "native";

// Tailwind-safe per-asset accents for the bubbly chip selector. USDC stays
// green (matches site primary), USDT teal (Tether brand), native gets the
// chain's own color via inline style — handled in the chip below.
const ASSET_META: Record<"usdc" | "usdt", { label: string; color: string; ring: string; bg: string }> = {
  usdc: { label: "USDC", color: "#2775CA", ring: "ring-[#2775CA]", bg: "bg-[#2775CA]" },
  usdt: { label: "USDT", color: "#26A17B", ring: "ring-[#26A17B]", bg: "bg-[#26A17B]" },
};

// Tiny circular token mark — colored disc with the ticker stamped on it.
// Lighter than shipping a 1x1 image asset and stays sharp at any DPI.
function TokenBadge({ kind, sym, size = 16 }: { kind: "usdc" | "usdt" | "native"; sym?: string; size?: number }) {
  const fill = kind === "usdc" ? "#2775CA" : kind === "usdt" ? "#26A17B" : "#9CA3AF";
  const label = kind === "usdc" ? "$" : kind === "usdt" ? "₮" : (sym || "Ξ").slice(0, 1);
  return (
    <span
      className="inline-flex items-center justify-center rounded-full font-mono leading-none shrink-0"
      style={{
        width: size,
        height: size,
        background: fill,
        color: "#fff",
        fontSize: Math.floor(size * 0.6),
        boxShadow: `0 0 6px ${fill}66`,
      }}
      aria-label={kind.toUpperCase()}
    >
      {label}
    </span>
  );
}

interface Props {
  capital?: number;
  onCapitalChange?: (n: number) => void;
}

export default function WalletFundingPanel({ capital, onCapitalChange }: Props) {
  const { auth } = useAuth();
  const [srcId, setSrcId] = useState<string>("polygon");
  const [srcAsset, setSrcAsset] = useState<SrcAsset>("usdc");
  const [usdcBal, setUsdcBal] = useState<Record<string, number | null>>({});
  const [usdtBal, setUsdtBal] = useState<Record<string, number | null>>({});
  const [nativeBal, setNativeBal] = useState<Record<string, number | null>>({});
  const [recipient, setRecipient] = useState("");
  const [showRecipient, setShowRecipient] = useState(false);
  const [sendAmount, setSendAmount] = useState("");
  const [customCapital, setCustomCapital] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  // Cross-chain bridge / asset-swap form is a first-time setup path. Most
  // sessions land here with USDC already on Polygon, so keep the whole
  // FUND POLYGON USDC block collapsed and surface a single + ADD FUNDS
  // toggle behind the balance row.
  const [showFund, setShowFund] = useState(false);

  const src = useMemo<NetworkConfig>(
    () => NETWORKS.find((n) => n.id === srcId) || NETWORKS[0],
    [srcId],
  );
  // Destination is always Polygon USDC — that's where Polymarket settles.
  // Bridge fires when source isn't already Polygon USDC.
  const isBridge = src.id !== "polygon" || srcAsset !== "usdc";

  // Read USDC / USDT / native balances for `nets`. Split from the polling
  // loop below so the background tick can stay on the SELECTED chain — the
  // full sweep is five chains × three tokens of RPC per tick, and only the
  // selected one is on screen (the others just fill the FROM dropdown's
  // labels, which a load-once + manual ↻ keeps current enough).
  const fetchBalances = useCallback(async (nets: NetworkConfig[]) => {
    if (!auth.address) return;
    await Promise.all(
      nets.map(async (net) => {
        try {
          const raw: bigint = await withRpcFallback(net, async (url) => {
            const provider = new JsonRpcProvider(url);
            const usdc = new Contract(net.usdc, USDC_ABI, provider);
            return usdc.balanceOf(auth.address!);
          });
          setUsdcBal((p) => ({ ...p, [net.id]: Number(formatUnits(raw, 6)) }));
        } catch {
          setUsdcBal((p) => ({ ...p, [net.id]: null }));
        }
        try {
          const raw: bigint = await withRpcFallback(net, async (url) => {
            const provider = new JsonRpcProvider(url);
            const usdt = new Contract(net.usdt, USDC_ABI, provider);
            return usdt.balanceOf(auth.address!);
          });
          setUsdtBal((p) => ({ ...p, [net.id]: Number(formatUnits(raw, 6)) }));
        } catch {
          setUsdtBal((p) => ({ ...p, [net.id]: null }));
        }
        try {
          const raw: bigint = await withRpcFallback(net, async (url) => {
            const provider = new JsonRpcProvider(url);
            return provider.getBalance(auth.address!);
          });
          setNativeBal((p) => ({ ...p, [net.id]: Number(formatEther(raw)) }));
        } catch {
          setNativeBal((p) => ({ ...p, [net.id]: null }));
        }
      }),
    );
  }, [auth.address]);

  const fetchAllBalances = useCallback(
    () => fetchBalances(NETWORKS),
    [fetchBalances],
  );

  // One full sweep per sign-in so every chain in the FROM list has a number.
  useEffect(() => { void fetchAllBalances(); }, [fetchAllBalances]);

  // Then keep only what's on screen live — the SELECTED chain, plus Polygon
  // (the headline "in your MetaMask" balance and the bridge destination).
  // Re-reads on chain switch and every 30s.
  useEffect(() => {
    if (!auth.address) return;
    const watched = src.id === POLYGON.id ? [POLYGON] : [POLYGON, src];
    void fetchBalances(watched);
    const t = setInterval(() => { void fetchBalances(watched); }, 30_000);
    return () => clearInterval(t);
  }, [auth.address, src, fetchBalances]);

  useEffect(() => {
    if (auth.address && !recipient) setRecipient(auth.address);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth.address]);

  const handleCopy = async () => {
    if (!auth.address) return;
    try {
      await navigator.clipboard.writeText(auth.address);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {}
  };

  const handleSend = async () => {
    setStatus(null);
    if (!auth.address || typeof window === "undefined" || !window.ethereum) {
      setStatus("NO WALLET");
      return;
    }
    if (!isAddress(recipient)) {
      setStatus("INVALID RECIPIENT");
      return;
    }
    const n = parseFloat(sendAmount);
    if (!Number.isFinite(n) || n <= 0) {
      setStatus("ENTER AMOUNT > 0");
      return;
    }
    const srcBalances =
      srcAsset === "usdc" ? usdcBal :
      srcAsset === "usdt" ? usdtBal :
      nativeBal;
    const bal = srcBalances[src.id];
    if (bal !== null && bal !== undefined && n > bal) {
      const sym =
        srcAsset === "usdc" ? "USDC" :
        srcAsset === "usdt" ? "USDT" :
        src.nativeCurrency.symbol;
      const dec = srcAsset === "native" ? 4 : 2;
      setStatus(`INSUFFICIENT ${sym} — BAL ${bal.toFixed(dec)}`);
      return;
    }

    setBusy(true);
    try {
      setStatus(`SWITCHING TO ${src.name}...`);
      await ensureChain(window.ethereum as never, src);
      const provider = new BrowserProvider(window.ethereum as never);
      const signer = await provider.getSigner(auth.address);

      if (isBridge) {
        setStatus("FETCHING LIFI QUOTE...");
        const fromToken =
          srcAsset === "native" ? NATIVE_TOKEN_ADDRESS :
          srcAsset === "usdt" ? src.usdt :
          src.usdc;
        const fromDecimals = srcAsset === "native" ? 18 : 6;
        const fromAmountUnits = parseUnits(sendAmount, fromDecimals).toString();
        const quote = await getLifiQuote({
          fromChain: src.chainId,
          fromToken,
          toChain: POLYGON.chainId,
          toToken: POLYGON.usdc,
          fromAmount: fromAmountUnits,
          fromAddress: auth.address,
          toAddress: recipient,
        });
        const outAmt = Number(quote.estimate.toAmount) / 10 ** quote.action.toToken.decimals;
        const etaMin = Math.max(1, Math.round(quote.estimate.executionDuration / 60));
        setStatus(`QUOTE: ~${outAmt.toFixed(2)} USDC via ${quote.toolDetails.name} ~${etaMin}min`);
        const result = await executeLifiBridge(signer, quote, { onProgress: setStatus });
        if (!result.finalStatus || result.finalStatus.status !== "DONE") {
          setStatus(`BRIDGED ${result.txHash.slice(0, 10)}... — DEST PENDING`);
        }
        setSendAmount("");
        void fetchAllBalances();
        return;
      }

      setStatus("CONFIRM IN WALLET...");
      // Bypass Contract.transfer() — ethers v6.16.0 chokes parsing the
      // populated tx on some wallet/chain combos ("value.nonce" BigInt
      // error). Manual eth_sendTransaction lets the wallet own nonce/gas.
      const iface = new Interface(USDC_ABI);
      const data = iface.encodeFunctionData("transfer", [
        recipient,
        parseUnits(sendAmount, 6),
      ]);
      // Non-bridge branch is only reachable when srcAsset === "usdc" and
      // src.id === "polygon" — USDT and any non-Polygon source force the
      // LiFi swap path via isBridge above. So token addr is the Polygon USDC.
      const ethereum = window.ethereum as {
        request: (a: { method: string; params?: unknown[] }) => Promise<unknown>;
      };
      const txHash = (await ethereum.request({
        method: "eth_sendTransaction",
        params: [{ from: auth.address, to: src.usdc, data }],
      })) as string;
      setStatus(`SENT ${txHash.slice(0, 10)}... — WAITING CONFIRM`);
      const receipt = await provider.waitForTransaction(txHash);
      if (receipt && receipt.status === 0) {
        setStatus(`REVERTED ${txHash.slice(0, 10)}...`);
      } else {
        setStatus(`CONFIRMED ${txHash.slice(0, 10)}...`);
      }
      setSendAmount("");
      void fetchAllBalances();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setStatus(`ERROR: ${msg.slice(0, 200)}`);
    } finally {
      setBusy(false);
    }
  };

  if (!auth.address) {
    return (
      <div className="pixel-panel border-2 border-pixel-border px-3 py-3 text-center">
        <span className="text-[15px] tracking-wider text-pixel-gray">CONNECT WALLET TO VIEW FUNDS</span>
      </div>
    );
  }

  const shortAddr = `${auth.address.slice(0, 6)}...${auth.address.slice(-4)}`;
  const loaded = usdcBal["polygon"];
  const srcUsdc = usdcBal[src.id];
  const srcUsdt = usdtBal[src.id];
  const srcNative = nativeBal[src.id];
  const srcBal =
    srcAsset === "usdc" ? srcUsdc :
    srcAsset === "usdt" ? srcUsdt :
    srcNative;
  const srcSym =
    srcAsset === "usdc" ? "USDC" :
    srcAsset === "usdt" ? "USDT" :
    src.nativeCurrency.symbol;
  const isSelfRecipient = auth.address && recipient.toLowerCase() === auth.address.toLowerCase();

  return (
    <div className="pixel-panel border-2 border-pixel-border overflow-hidden">
      {/* Dropped the standalone WALLET header bar — the address is already
          visible in the TOKEN/KEY row at the top of the page. Refresh moved
          into the balance row to keep one-click access without a header. */}

      {/* ── WALLET balance on Polygon. Note: this is NOT tradeable on
           Polymarket yet — funds must be DEPOSITed from this EOA to the
           Polymarket proxy (see POLYMARKET ACCOUNT panel below) before the
           CLOB sees them. ───────────────────────────────────── */}
      <div className="px-3 py-2 flex items-center justify-between border-b border-pixel-border/60 bg-gradient-to-r from-green-400/[0.03] to-transparent">
        <div className="flex items-center gap-2 min-w-0">
          <ChainBadge net={POLYGON} size={22} />
          <div className="leading-tight min-w-0">
            <div className="text-[13px] text-pixel-gray tracking-[0.15em]">WALLET · POLYGON</div>
            {/* Inline truncated address as a click-to-copy chip — replaces
                the standalone WALLET header bar that duplicated the KEY row. */}
            <button
              onClick={handleCopy}
              className="text-[12px] text-pixel-gray/80 font-mono hover:text-green-400 transition-colors truncate max-w-[180px] block"
              title={copied ? "Copied!" : `Click to copy ${auth.address}`}
            >
              {shortAddr} {copied ? "✓" : ""}
            </button>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => { void fetchAllBalances(); }}
            title="Refresh balances"
            className="text-[16px] text-pixel-gray hover:text-green-400 px-1"
          >
            ↻
          </button>
          <div className="text-right leading-tight">
            <div className="text-[28px] font-mono text-green-400">
              {loaded === null || loaded === undefined ? "..." : `$${loaded.toFixed(2)}`}
            </div>
            {loaded !== null && loaded !== undefined && loaded === 0 ? (
              <div className="text-[13px] text-amber-400/80 tracking-wider">FUND BELOW ↓</div>
            ) : loaded !== null && loaded !== undefined && loaded > 0 ? (
              <div className="text-[11px] text-pixel-gray tracking-wider">
                in your MetaMask — use TRADING WALLET to deposit
              </div>
            ) : null}
          </div>
        </div>
      </div>

      {/* ── + ADD FUNDS toggle ──
          Collapses the cross-chain bridge / swap form by default. Click to
          expand the full FROM / asset chips / AMT / SEND form when funding
          for the first time or bridging from another chain. */}
      <button
        onClick={() => setShowFund((v) => !v)}
        className="w-full px-3 py-1.5 border-b border-pixel-border/40 flex items-center justify-between text-[13px] tracking-[0.18em] hover:bg-pixel-white/5 transition-colors"
      >
        <span className={showFund ? "text-green-400" : "text-pixel-gray"}>
          {showFund ? "− HIDE FUND" : "+ ADD FUNDS"}
        </span>
        <span className="text-[11px] text-pixel-gray font-mono">
          {showFund ? "bridge / swap / send" : "bridge or send USDC into Polygon"}
        </span>
      </button>

      {/* ── Fund section ────────────────────────────────────── */}
      {showFund && (
      <div className="px-3 py-2 space-y-1.5 bg-pixel-black/40">
        <div className="flex items-center justify-between">
          <span className="text-[14px] text-pixel-white tracking-[0.15em]">FUND POLYGON USDC</span>
          <span className={`text-[14px] font-mono ${isBridge ? "text-amber-400" : "text-pixel-gray"}`}>
            {isBridge ? "bridge · via li.fi" : "direct transfer"}
          </span>
        </div>

        {/* Two-row layout: network selector on one line, asset chips on the
            next. Keeps the chips visible in narrow sidebar mounts where the
            inline layout used to push them off-screen. */}
        <div className="flex items-center gap-2">
          <span className="text-[13px] text-pixel-gray tracking-[0.15em] w-10 shrink-0">FROM</span>
          <div className="relative flex-1 min-w-0">
            <div
              className="flex items-center gap-2 rounded-full border bg-pixel-black/80 pl-1 pr-3 h-[28px] hover:bg-pixel-black transition-colors"
              style={{ borderColor: `${src.color}66`, boxShadow: `inset 0 0 0 1px ${src.color}22` }}
            >
              <ChainBadge net={src} size={22} />
              <span className="text-[15px] text-pixel-white font-mono flex-1 truncate">{src.name}</span>
              <span className="text-[14px] text-green-400/80 font-mono">
                {srcUsdc === null || srcUsdc === undefined ? "..." : `$${srcUsdc.toFixed(2)}`}
              </span>
              <span className="text-[14px] text-pixel-gray">▾</span>
            </div>
            <select
              value={src.id}
              onChange={(e) => setSrcId(e.target.value)}
              className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
            >
              {NETWORKS.map((n) => {
                const u = usdcBal[n.id];
                const t = usdtBal[n.id];
                const v = nativeBal[n.id];
                const uS = u === null || u === undefined ? "..." : `$${u.toFixed(2)}`;
                const tS = t === null || t === undefined ? "..." : `$${t.toFixed(2)}`;
                const vS = v === null || v === undefined ? "..." : `${v.toFixed(3)} ${n.nativeCurrency.symbol}`;
                return (
                  <option key={n.id} value={n.id}>
                    {n.glyph} {n.name} — {uS} USDC / {tS} USDT / {vS}
                  </option>
                );
              })}
            </select>
          </div>
        </div>
        {/* Asset picker — three rounded chips with token icons + live balance.
            Lives on its own row so it's always visible regardless of panel
            width. The 10px left indent visually anchors it under "FROM". */}
        <div className="flex items-center flex-wrap gap-2 pl-12">
          <AssetChip
            active={srcAsset === "usdc"}
            onClick={() => setSrcAsset("usdc")}
            icon={<TokenBadge kind="usdc" size={14} />}
            label="USDC"
            tint={ASSET_META.usdc.color}
            balance={usdcBal[src.id]}
          />
          <AssetChip
            active={srcAsset === "usdt"}
            onClick={() => setSrcAsset("usdt")}
            icon={<TokenBadge kind="usdt" size={14} />}
            label="USDT"
            tint={ASSET_META.usdt.color}
            balance={usdtBal[src.id]}
          />
          <AssetChip
            active={srcAsset === "native"}
            onClick={() => setSrcAsset("native")}
            icon={<TokenBadge kind="native" sym={src.nativeCurrency.symbol} size={14} />}
            label={src.nativeCurrency.symbol}
            tint={src.color}
            balance={nativeBal[src.id]}
          />
        </div>

        {/* AMT row with inline % chips (no longer needs its own line below). */}
        <div className="flex items-center gap-2">
          <span className="text-[13px] text-pixel-gray tracking-[0.15em] w-10 shrink-0">AMT</span>
          <div className="flex-1 relative">
            <input
              type="text"
              inputMode="decimal"
              placeholder="0.00"
              value={sendAmount}
              onChange={(e) => setSendAmount(e.target.value.replace(/[^0-9.]/g, ""))}
              className="pixel-input-sm w-full font-mono text-[16px] pr-12 h-[34px]"
            />
            <span className="absolute right-2 top-1/2 -translate-y-1/2 text-[14px] text-pixel-gray font-mono pointer-events-none">
              {srcSym}
            </span>
          </div>
          {srcBal !== null && srcBal !== undefined && srcBal > 0 && (
            <div className="flex items-center gap-0.5 shrink-0">
              {[0.25, 0.5, 0.75, 1].map((pct) => (
                <button
                  key={pct}
                  onClick={() => {
                    const v = srcBal * pct;
                    setSendAmount(srcAsset === "usdc" ? v.toFixed(2) : v.toFixed(4));
                  }}
                  className="text-[14px] px-1.5 h-[34px] inline-flex items-center justify-center border border-pixel-border bg-pixel-black text-pixel-gray hover:text-green-400 hover:border-green-400 font-mono tracking-wider transition-colors"
                >
                  {pct === 1 ? "MAX" : `${pct * 100}%`}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Action button — full width, prominent */}
        <button
          onClick={() => { void handleSend(); }}
          disabled={busy || !sendAmount || !recipient}
          className={`w-full pixel-btn text-[16px] py-1.5 font-mono tracking-wider disabled:opacity-30 disabled:cursor-not-allowed ${
            isBridge
              ? "border-amber-400 text-amber-400 hover:bg-amber-400/10"
              : "border-green-400 text-green-400 hover:bg-green-400/10"
          }`}
        >
          {busy
            ? "WORKING..."
            : isBridge
              ? `BRIDGE ${sendAmount || "—"} ${srcSym} → POLYGON USDC`
              : `SEND ${sendAmount ? `$${sendAmount} USDC` : "—"}`}
        </button>

        {/* Status */}
        {status && (
          <div className={`text-[14px] font-mono break-all px-1 py-1 border-l-2 ${
            status.startsWith("ERROR") || status.startsWith("INVALID") || status.startsWith("INSUFFICIENT") || status.includes("REJECTED")
              ? "text-red-400 border-red-400"
              : status.startsWith("CONFIRMED") || status.startsWith("SENT") || status.startsWith("BRIDGED")
                ? "text-green-400 border-green-400"
                : "text-amber-400 border-amber-400"
          }`}>
            {status}
          </div>
        )}

        {/* Recipient — collapsed by default when ME */}
        {!showRecipient && isSelfRecipient ? (
          <button
            onClick={() => setShowRecipient(true)}
            className="text-[14px] text-pixel-gray hover:text-pixel-white font-mono tracking-[0.15em] underline-offset-2 hover:underline"
          >
            ⌄ SEND TO A DIFFERENT ADDRESS
          </button>
        ) : (
          <div className="flex items-center gap-2">
            <span className="text-[13px] text-pixel-gray tracking-[0.15em] w-12 shrink-0">TO</span>
            <input
              type="text"
              placeholder="RECIPIENT 0x..."
              value={recipient}
              onChange={(e) => setRecipient(e.target.value)}
              className={`pixel-input-sm flex-1 font-mono text-[14px] ${
                isSelfRecipient ? "text-green-400" : ""
              }`}
              spellCheck={false}
            />
            <button
              onClick={() => { if (auth.address) { setRecipient(auth.address); setShowRecipient(false); } }}
              title="Send to self (collapse)"
              className="pixel-btn text-[14px] px-2 py-1 border-pixel-border text-pixel-gray hover:text-green-400 hover:border-green-400 shrink-0"
            >
              ME
            </button>
          </div>
        )}
      </div>
      )}

      {/* ── Capital cap (LIVE tab only) ─────────────────────── */}
      {capital !== undefined && onCapitalChange && (
        <div className="px-3 py-1.5 border-t border-pixel-border/60 flex items-center gap-2 flex-wrap">
          <span className="text-[14px] text-pixel-gray tracking-wider shrink-0">CAPITAL CAP</span>
          <span className="text-[16px] text-pixel-white font-mono shrink-0">${capital.toLocaleString()}</span>
          <div className="flex items-center gap-1 flex-wrap ml-auto">
            {PRESETS.map((p) => (
              <button
                key={p}
                onClick={() => { onCapitalChange(p); setCustomCapital(""); }}
                className={`pixel-btn text-[14px] px-1.5 py-0.5 transition-colors ${
                  capital === p
                    ? "border-green-400 text-green-400 bg-green-400/10"
                    : "border-pixel-border text-pixel-gray hover:text-pixel-white"
                }`}
              >
                ${p >= 1000 ? `${p / 1000}K` : p}
              </button>
            ))}
            {loaded !== null && loaded !== undefined && loaded > 0 && (
              <button
                onClick={() => { onCapitalChange(Math.floor(loaded)); setCustomCapital(""); }}
                className="pixel-btn text-[14px] px-1.5 py-0.5 border-pixel-border text-pixel-gray hover:text-amber-400 hover:border-amber-400"
                title="Use full Polygon USDC balance"
              >
                MAX
              </button>
            )}
            <input
              type="text"
              inputMode="numeric"
              value={customCapital}
              onChange={(e) => {
                const v = e.target.value.replace(/[^0-9]/g, "");
                setCustomCapital(v);
                const num = Number(v);
                if (num > 0) onCapitalChange(num);
              }}
              placeholder="CUSTOM"
              className="pixel-input-sm w-16 font-mono text-[14px] text-center"
            />
          </div>
        </div>
      )}
    </div>
  );
}

// ── ChainBadge ──────────────────────────────────────────────────

function ChainBadge({ net, size = 22 }: { net: NetworkConfig; size?: number }) {
  return (
    <div
      className="shrink-0 flex items-center justify-center font-mono leading-none rounded-full"
      style={{
        width: size,
        height: size,
        background: `${net.color}1f`,
        border: `1px solid ${net.color}`,
        color: net.color,
        boxShadow: `0 0 6px ${net.color}55`,
        fontSize: Math.floor(size * 0.55),
      }}
      aria-label={net.name}
    >
      {net.glyph}
    </div>
  );
}

// Rounded "bubble" asset toggle. Filled when active (tinted glow), outline
// only otherwise. Inline icon + label keeps the row dense without losing
// the visual hint of which token is selected.
function AssetChip({
  active,
  onClick,
  icon,
  label,
  tint,
  balance,
}: {
  active: boolean;
  onClick: () => void;
  icon: ReactNode;
  label: string;
  tint: string;
  balance: number | null | undefined;
}) {
  // Compact balance formatting: "12.34" / "1.2K" / "3.4M". Hide entirely
  // when we have no balance for that token (treat 0 still as a display
  // signal so the user can tell "checked, empty" from "not yet loaded").
  const fmt = (n: number) => {
    if (n >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
    if (n >= 1e3) return `${(n / 1e3).toFixed(2)}K`;
    if (n >= 100) return n.toFixed(0);
    if (n >= 1)   return n.toFixed(2);
    if (n > 0)    return n.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
    return "0";
  };
  const balText = balance == null ? "…" : fmt(balance);
  const isEmpty = balance != null && balance <= 0;
  return (
    <button
      onClick={onClick}
      className="inline-flex items-center gap-1.5 rounded-full h-[28px] px-2.5 text-[14px] font-mono tracking-wider transition-all"
      style={
        active
          ? {
              background: `${tint}26`,
              border: `1px solid ${tint}`,
              color: tint,
              boxShadow: `0 0 8px ${tint}55`,
            }
          : {
              background: "rgba(0,0,0,0.4)",
              border: "1px solid rgba(255,255,255,0.12)",
              color: "#9CA3AF",
            }
      }
      aria-pressed={active}
      title={balance == null ? `${label} — loading balance` : `${label} balance: ${balance}`}
    >
      {icon}
      <span>{label}</span>
      <span
        className="text-[13px] tabular-nums"
        style={{ opacity: active ? 0.95 : isEmpty ? 0.4 : 0.7 }}
      >
        {balText}
      </span>
    </button>
  );
}
