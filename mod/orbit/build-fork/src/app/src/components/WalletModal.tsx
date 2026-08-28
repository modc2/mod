"use client";

import { useState, useEffect } from "react";
import { ethers } from "ethers";
import {
  getNetworkName,
  getNativeSymbol,
  copyToClipboard as utilCopyToClipboard,
  switchNetwork,
  getProvider,
  getStoredChainId,
  EVM_NETWORKS,
  NETWORK_LOGOS,
} from "../utils/wallet";
import { qrSvg } from "../app/lib/qr";
import AddressChip from "./AddressChip";

interface WalletModalProps {
  address: string;
  walletType: "metamask" | "subwallet" | "local" | "password" | null;
  onClose: () => void;
  onDisconnect: () => void;
  inline?: boolean;
  /** When true the component's own balance/address/close header is omitted —
      used when the wallet UI is embedded inside another panel (e.g. the merged
      account sidebar) that already supplies a header. */
  embedded?: boolean;
  /** When true the component renders at its natural content height with no
      internal scroll/`h-full`, so it can flow inside a parent's single scroll
      container (e.g. the merged account sidebar). */
  flow?: boolean;
  /** When true the component renders as one contracted header-height strip —
      balance · address · QR · network · refresh · disconnect — sized to sit
      in the account sidebar's header zone on a phone. The QR / network /
      secrets panels still expand below the strip on demand. Implies inline. */
  compact?: boolean;
  onNetworkChange?: () => void;
}

/** How each identity is held, drawn in the console's own line-art language
    (an emoji here renders as a tofu box wherever the font is missing). */
const WALLET_KINDS: Record<string, { label: string; path: string }> = {
  metamask: { label: "Browser wallet", path: "M3 7a2 2 0 0 1 2-2h13a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM16 12h4" },
  subwallet: { label: "SubWallet", path: "M12 3l8 9-8 9-8-9z" },
  password: { label: "Password key — throwaway", path: "M9.5 14.5a3.5 3.5 0 1 1-2.5-6 3.5 3.5 0 0 1 2.5 6zM10 12l9-9M17 5l2 2M15 7l2 2" },
  local: { label: "Seed phrase on this device — throwaway", path: "M4 6h16v12H4zM7 9h.01M7 15h9" },
};

/** Balance the way a person reads it: no "0.0000" trailing-zero noise, a floor
    for dust so tiny balances don't round to nothing, grouping once it's big. */
function fmtBalance(v: string): string {
  const n = parseFloat(v);
  if (!isFinite(n) || n === 0) return "0";
  if (n < 0.0001) return "<0.0001";
  return n.toLocaleString("en-US", { maximumFractionDigits: n >= 1 ? 4 : 6 });
}

export default function WalletModal({
  address,
  walletType,
  onClose,
  onDisconnect,
  inline = false,
  embedded = false,
  flow = false,
  compact = false,
  onNetworkChange,
}: WalletModalProps) {
  const [balance, setBalance] = useState<string>("0.00");
  const [network, setNetwork] = useState<string>("Unknown");
  const [chainId, setChainId] = useState<number>(0);
  const [nativeSymbol, setNativeSymbol] = useState<string>("ETH");
  const [loading, setLoading] = useState(false);
  /** First load draws the big spinner; every refresh after it just dims the
      balance, so hitting REFRESH doesn't blank the panel you're reading. */
  const [loadedOnce, setLoadedOnce] = useState(false);
  const [showSeedPhrase, setShowSeedPhrase] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const [showNetworkSelector, setShowNetworkSelector] = useState(false);
  const [switchingNetwork, setSwitchingNetwork] = useState(false);
  const [showTestnets, setShowTestnets] = useState(false);
  const [networkError, setNetworkError] = useState<string | null>(null);
  const [showQr, setShowQr] = useState(false);

  // Load wallet data
  useEffect(() => {
    loadWalletData();

    // Listen for network changes from wallet
    const ethereum = (window as any).ethereum;
    if (ethereum?.on) {
      const handleChainChanged = () => loadWalletData();
      ethereum.on("chainChanged", handleChainChanged);
      return () => ethereum.removeListener("chainChanged", handleChainChanged);
    }
  }, [address]);

  const loadWalletData = async () => {
    if (address === "local") return;

    setLoading(true);
    try {
      const ethereum = (window as any).ethereum;
      const provider = ethereum
        ? new ethers.BrowserProvider(ethereum)
        : getProvider();

      // Get balance
      const bal = await provider.getBalance(address);
      setBalance(ethers.formatEther(bal));

      // Get network info — for JsonRpcProvider use stored chain ID
      let cid: number;
      if (ethereum) {
        const net = await provider.getNetwork();
        cid = Number(net.chainId);
      } else {
        cid = getStoredChainId();
      }
      setChainId(cid);
      setNetwork(getNetworkName(cid));
      setNativeSymbol(getNativeSymbol(cid));
      const currentNet = EVM_NETWORKS.find(n => n.chainId === cid);
      if (currentNet) setShowTestnets(currentNet.testnet);
    } catch (e) {
      console.error("Failed to load wallet data:", e);
    } finally {
      setLoading(false);
      setLoadedOnce(true);
    }
  };

  const handleCopy = async (text: string, label?: string) => {
    await utilCopyToClipboard(text);
    setCopied(label || text);
    setTimeout(() => setCopied(null), 1500);
  };

  const getSeedPhrase = () => {
    if (walletType === "local") {
      return localStorage.getItem("buildfork_jobs_seed") || "No seed phrase found";
    }
    return "Not available for this wallet type";
  };

  // Throwaway-wallet secrets — these key types are meant to be quick,
  // disposable identities, so the raw secret is viewable/copyable anytime.
  const getPassword = () => {
    if (walletType === "password") {
      return localStorage.getItem("buildfork_jobs_password") || "";
    }
    return "";
  };

  const getPrivateKey = () => {
    try {
      if (walletType === "password") {
        const pw = getPassword();
        // Same derivation as sign-in: private key = keccak256(password)
        return pw ? ethers.id(pw) : "";
      }
      if (walletType === "local") {
        const mnemonic = localStorage.getItem("buildfork_jobs_seed");
        return mnemonic ? ethers.Wallet.fromPhrase(mnemonic).privateKey : "";
      }
    } catch {
      /* malformed stored secret — just show nothing */
    }
    return "";
  };

  const handleSwitchNetwork = async (targetChainId: number) => {
    if (targetChainId === chainId) {
      setShowNetworkSelector(false);
      return;
    }
    setSwitchingNetwork(true);
    setNetworkError(null);
    const ok = await switchNetwork(targetChainId);
    setSwitchingNetwork(false);
    if (ok) {
      setShowNetworkSelector(false);
      loadWalletData();
      onNetworkChange?.();
    } else {
      const net = EVM_NETWORKS.find(n => n.chainId === targetChainId);
      setNetworkError(`Failed to switch to ${net?.name || "network"}`);
      setTimeout(() => setNetworkError(null), 3000);
    }
  };

  const wallet = WALLET_KINDS[walletType || "local"];
  const isZero = !(parseFloat(balance) > 0);
  const chainColor = NETWORK_LOGOS[chainId]?.color || "var(--crt-amber)";

  // Expandable panels — shared between the full body and the contracted
  // header strip so each renders from exactly one source.
  const qrPanel = (
    <div
      className="p-4 flex flex-col items-center gap-3"
      style={{
        border: "1px solid color-mix(in srgb, var(--crt-green) 14%, var(--border-color))",
        background:
          "linear-gradient(180deg, color-mix(in srgb, var(--crt-green) 2.5%, transparent), transparent 55%), color-mix(in srgb, var(--glass-bg) 92%, transparent)",
        borderRadius: "14px",
      }}
    >
      <div className="w-full text-[9px] tracking-[2px] flex items-center justify-between" style={{ color: "var(--text-tertiary)" }}>
        <span>ADDRESS QR</span>
        <button
          onClick={() => setShowQr(false)}
          className="text-[9px] px-2 py-0.5 transition-all"
          style={{ color: "var(--crt-green)", border: "1px solid color-mix(in srgb, var(--crt-green) 15%, transparent)", borderRadius: "8px" }}
        >
          CLOSE
        </button>
      </div>
      <div
        className="p-2"
        style={{ background: "#ffffff", borderRadius: "12px", boxShadow: "0 2px 16px rgba(0,0,0,0.35)" }}
        dangerouslySetInnerHTML={{ __html: qrSvg(address, 200) }}
      />
      <button
        onClick={() => handleCopy(address, "address")}
        title="Click to copy"
        className="w-full px-3 py-2 font-mono text-[10px] text-center break-all transition-all"
        style={{
          background: "var(--bg-secondary)",
          border: copied === "address" ? "1px solid var(--crt-green)" : "1px solid var(--border-color)",
          color: copied === "address" ? "var(--crt-green)" : "var(--text-primary)",
          letterSpacing: "0.3px",
          borderRadius: "8px",
        }}
      >
        {copied === "address" ? "COPIED" : address}
      </button>
    </div>
  );

  const content = (
    <div className={flow ? "flex flex-col" : "h-full flex flex-col overflow-hidden"}>
      {/* Header: title + close — omitted when embedded inside the merged
          account sidebar, which supplies its own header. Balance/address live
          in the body card so they appear exactly once. */}
      {!embedded && (
      <div
        className="flex items-center justify-between px-5 py-3 shrink-0"
        style={{
          borderBottom: "1px solid color-mix(in srgb, var(--crt-blue) 12%, var(--border-color))",
          background: "linear-gradient(180deg, color-mix(in srgb, var(--crt-blue) 6%, transparent) 0%, transparent 100%)",
        }}
      >
        <span className="text-[11px] font-bold tracking-[2px]" style={{ color: "var(--text-secondary)" }}>
          ACCOUNT
        </span>
        <button
          onClick={onClose}
          className="w-7 h-7 flex items-center justify-center text-[12px] transition-all"
          style={{
            color: "var(--text-tertiary)",
            border: "1px solid var(--border-color)",
            borderRadius: "8px",
          }}
          onMouseEnter={(e) => {
            (e.currentTarget as HTMLElement).style.borderColor = "color-mix(in srgb, var(--crt-red) 40%, transparent)";
            (e.currentTarget as HTMLElement).style.color = "var(--crt-red)";
            (e.currentTarget as HTMLElement).style.background = "color-mix(in srgb, var(--crt-red) 8%, transparent)";
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLElement).style.borderColor = "var(--border-color)";
            (e.currentTarget as HTMLElement).style.color = "var(--text-tertiary)";
            (e.currentTarget as HTMLElement).style.background = "transparent";
          }}
        >
          ✕
        </button>
      </div>
      )}

      {/* Content — single view: the identity card. */}
      <div className={flow ? "p-3 space-y-3" : "flex-1 overflow-y-auto p-3 space-y-3"}>
        {loading && !loadedOnce && (
          <div className="text-center py-16">
            <div className="inline-flex items-center gap-3">
              <span className="w-2 h-2 rounded-full led-pulse" style={{ background: "var(--crt-blue)", boxShadow: "0 0 8px var(--crt-blue)" }} />
              <p className="text-[11px] tracking-[2px]" style={{ color: "var(--crt-blue)" }}>
                LOADING WALLET DATA
              </p>
              <span className="w-2 h-2 rounded-full led-pulse" style={{ background: "var(--crt-blue)", boxShadow: "0 0 8px var(--crt-blue)" }} />
            </div>
          </div>
        )}

        {(!loading || loadedOnce) && (
          <>
            {/* Identity Card — one row, read left to right: who you are (wallet
                kind) · what you hold · which address · the controls that act on
                it. Every control is a .wallet-ctl so the row lines up. */}
            <div
              className="flex flex-wrap items-center gap-2 px-3 py-2.5"
              style={{
                border: "1px solid color-mix(in srgb, var(--crt-green) 14%, var(--border-color))",
                background:
                  "linear-gradient(180deg, color-mix(in srgb, var(--crt-green) 2.5%, transparent), transparent 55%), color-mix(in srgb, var(--glass-bg) 92%, transparent)",
                borderRadius: "14px",
                boxShadow: "0 1px 0 inset color-mix(in srgb, var(--crt-green) 6%, transparent), var(--shadow-sm)",
              }}
            >
              <span
                className="shrink-0 flex items-center justify-center"
                title={wallet.label}
                style={{
                  width: 30,
                  height: 30,
                  borderRadius: 9,
                  color: "var(--text-tertiary)",
                  background: "var(--bg-secondary)",
                  border: "1px solid var(--border-color)",
                }}
              >
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <path d={wallet.path} />
                </svg>
              </span>
              <span
                className="shrink-0 flex items-baseline gap-1 transition-opacity"
                title={`${balance} ${nativeSymbol} on ${network}`}
                style={{ opacity: loading ? 0.45 : 1 }}
              >
                <span
                  className="text-[15px] font-bold font-mono tabular-nums leading-none"
                  style={
                    isZero
                      ? { color: "var(--text-tertiary)" }
                      : { color: "var(--crt-green)", textShadow: "0 0 12px color-mix(in srgb, var(--crt-green) 20%, transparent)" }
                  }
                >
                  {fmtBalance(balance)}
                </span>
                <span className="text-[9px] font-bold tracking-[0.14em]" style={{ color: "var(--text-tertiary)" }}>
                  {nativeSymbol}
                </span>
              </span>
              <span className="shrink-0 w-px h-5" style={{ background: "var(--border-color)" }} />
              {/* Address — the chip copies, the ↗ beside it opens the address on
                  the current network's explorer. */}
              <AddressChip
                address={address}
                size={11}
                height={30}
                color="var(--text-primary)"
                label={`${network} address`}
                explorerUrl={chainId ? `${EVM_NETWORKS.find(n => n.chainId === chainId)?.explorer || "https://etherscan.io"}/address/${address}` : undefined}
                className="shrink-0"
              />
              {/* Controls, anchored right — the row breathes in the middle
                  instead of stretching the address across it. */}
              <div className="ml-auto shrink-0 flex items-center gap-1.5">
                <button
                  onClick={() => setShowQr(!showQr)}
                  title="Show address QR code"
                  aria-label="Show address QR code"
                  aria-expanded={showQr}
                  className="wallet-ctl square focus-ring"
                >
                  <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <rect x="3" y="3" width="7" height="7" rx="1" />
                    <rect x="14" y="3" width="7" height="7" rx="1" />
                    <rect x="3" y="14" width="7" height="7" rx="1" />
                    <path d="M14 14h3v3h-3zM20 14h1M14 20h1M20 20h1" />
                  </svg>
                </button>
                <button
                  onClick={() => setShowNetworkSelector(!showNetworkSelector)}
                  title={`${network} · chain ${chainId} — switch network`}
                  aria-label={`${network} — switch network`}
                  aria-expanded={showNetworkSelector}
                  className="wallet-ctl focus-ring"
                  style={{ "--ctl-accent": chainColor, color: chainColor } as React.CSSProperties}
                >
                  <span
                    className="w-[14px] h-[14px] flex items-center justify-center shrink-0"
                    dangerouslySetInnerHTML={{
                      __html: `<svg viewBox="0 0 24 24" width="14" height="14">${NETWORK_LOGOS[chainId]?.svg || '<circle cx="12" cy="12" r="8" fill="currentColor" opacity="0.3"/>'}</svg>`
                    }}
                  />
                  <span className="text-[10px] font-bold tracking-wide">{network}</span>
                  <span className="wallet-ctl__caret text-[8px]" style={{ opacity: 0.7 }}>▾</span>
                </button>
              </div>
            </div>

            {/* Address QR — rendered locally (qrSvg), never sent to a third-party service */}
            {showQr && qrPanel}

            {/* Inline Network Selector */}
            {showNetworkSelector && (
              <div
                className="p-4 space-y-3"
                style={{
                  border: `1px solid color-mix(in srgb, ${chainColor} 14%, var(--border-color))`,
                  background:
                    `linear-gradient(180deg, color-mix(in srgb, ${chainColor} 2.5%, transparent), transparent 55%), color-mix(in srgb, var(--glass-bg) 92%, transparent)`,
                  borderRadius: "14px",
                }}
              >
                <div className="text-[9px] tracking-[2px] flex items-center justify-between" style={{ color: "var(--text-tertiary)" }}>
                  <span>SELECT NETWORK</span>
                  <button
                    onClick={() => setShowNetworkSelector(false)}
                    className="text-[9px] px-2 py-0.5 transition-all"
                    style={{ color: chainColor, border: `1px solid color-mix(in srgb, ${chainColor} 15%, transparent)`, borderRadius: "8px" }}
                  >
                    CLOSE
                  </button>
                </div>

                {/* Mainnet / Testnet Toggle */}
                <div className="flex" style={{ border: "1px solid var(--border-color)", borderRadius: "8px" }}>
                  <button
                    onClick={() => setShowTestnets(false)}
                    className="flex-1 py-2 text-[9px] tracking-[2px] transition-all"
                    style={{
                      color: !showTestnets ? chainColor : "var(--text-tertiary)",
                      background: !showTestnets ? `color-mix(in srgb, ${chainColor} 10%, transparent)` : "transparent",
                      borderRight: "1px solid var(--border-color)",
                      fontWeight: !showTestnets ? "bold" : "normal",
                      opacity: !showTestnets ? 1 : 0.5,
                    }}
                  >
                    MAINNET
                  </button>
                  <button
                    onClick={() => setShowTestnets(true)}
                    className="flex-1 py-2 text-[9px] tracking-[2px] transition-all"
                    style={{
                      color: showTestnets ? chainColor : "var(--text-tertiary)",
                      background: showTestnets ? `color-mix(in srgb, ${chainColor} 10%, transparent)` : "transparent",
                      fontWeight: showTestnets ? "bold" : "normal",
                      opacity: showTestnets ? 1 : 0.5,
                    }}
                  >
                    TESTNET
                  </button>
                </div>

                <div className="grid grid-cols-3 gap-2">
                  {EVM_NETWORKS.filter(n => n.testnet === showTestnets).map((n) => {
                    // Each network wears its own brand colour, active or not.
                    const c = NETWORK_LOGOS[n.chainId]?.color || "var(--crt-amber)";
                    return (
                      <button
                        key={n.chainId}
                        onClick={() => handleSwitchNetwork(n.chainId)}
                        disabled={switchingNetwork}
                        className="p-3 text-center transition-all flex flex-col items-center gap-2"
                        style={{
                          border: n.chainId === chainId
                            ? `1px solid color-mix(in srgb, ${c} 30%, transparent)`
                            : "1px solid var(--border-color)",
                          background: n.chainId === chainId
                            ? `color-mix(in srgb, ${c} 8%, transparent)`
                            : "var(--bg-secondary)",
                          borderRadius: "10px",
                          opacity: switchingNetwork ? 0.5 : 1,
                        }}
                        onMouseEnter={(e) => {
                          if (n.chainId !== chainId) {
                            e.currentTarget.style.borderColor = `color-mix(in srgb, ${c} 30%, transparent)`;
                            e.currentTarget.style.background = "color-mix(in srgb, var(--text-primary) 4%, transparent)";
                          }
                        }}
                        onMouseLeave={(e) => {
                          if (n.chainId !== chainId) {
                            e.currentTarget.style.borderColor = "var(--border-color)";
                            e.currentTarget.style.background = "var(--bg-secondary)";
                          }
                        }}
                      >
                        <span
                          className="w-[28px] h-[28px] flex items-center justify-center"
                          style={{ color: c }}
                          dangerouslySetInnerHTML={{
                            __html: `<svg viewBox="0 0 24 24" width="28" height="28">${NETWORK_LOGOS[n.chainId]?.svg || '<circle cx="12" cy="12" r="8" fill="currentColor" opacity="0.3"/>'}</svg>`
                          }}
                        />
                        <div className="text-[10px] font-bold tracking-wide" style={{ color: n.chainId === chainId ? c : "var(--text-secondary)" }}>
                          {n.name}
                        </div>
                        <div className="text-[8px] font-mono" style={{ color: "var(--text-tertiary)", opacity: 0.5 }}>{n.symbol}</div>
                      </button>
                    );
                  })}
                </div>

                {networkError && (
                  <div
                    className="text-[10px] text-center py-2 tracking-wider"
                    style={{ color: "var(--crt-red)", border: "1px solid color-mix(in srgb, var(--crt-red) 20%, transparent)", background: "color-mix(in srgb, var(--crt-red) 6%, transparent)", borderRadius: "8px" }}
                  >
                    {networkError}
                  </div>
                )}
              </div>
            )}

            {/* Throwaway-wallet secrets — local (seed) and password wallets are
                quick disposable identities, so the seed / password / private
                key are viewable and copyable at any time. */}
            {(walletType === "local" || walletType === "password") && (
              <div
                className="p-4"
                style={{
                  border: "1px solid color-mix(in srgb, var(--crt-amber) 14%, var(--border-color))",
                  background:
                    "linear-gradient(180deg, color-mix(in srgb, var(--crt-amber) 2.5%, transparent), transparent 55%), color-mix(in srgb, var(--glass-bg) 92%, transparent)",
                  borderRadius: "14px",
                }}
              >
                <button
                  onClick={() => setShowSeedPhrase(!showSeedPhrase)}
                  className="flex items-center gap-2 text-[10px] transition-all w-full tracking-wider"
                  style={{ color: "var(--crt-amber)" }}
                >
                  <span style={{ fontSize: "12px", transition: "transform 0.15s" }}>
                    {showSeedPhrase ? "▾" : "▸"}
                  </span>
                  {showSeedPhrase
                    ? "HIDE WALLET SECRETS"
                    : walletType === "password"
                      ? "SHOW PASSWORD & PRIVATE KEY"
                      : "SHOW SEED & PRIVATE KEY"}
                </button>
                {showSeedPhrase && (
                  <div className="mt-3 space-y-3">
                    <div
                      className="px-3 py-2 text-[10px] tracking-wider leading-relaxed"
                      style={{
                        background: "color-mix(in srgb, var(--crt-red) 6%, transparent)",
                        border: "1px solid color-mix(in srgb, var(--crt-red) 20%, transparent)",
                        color: "var(--crt-red)",
                        borderRadius: "8px",
                      }}
                    >
                      THROWAWAY WALLET — ANYONE WITH THIS SECRET IS THIS IDENTITY
                    </div>

                    {walletType === "local" && (
                      <>
                        <div className="text-[9px] tracking-[2px]" style={{ color: "var(--text-tertiary)" }}>
                          SEED PHRASE
                        </div>
                        <div
                          className="p-3 font-mono text-[11px] break-all leading-relaxed"
                          style={{
                            background: "var(--bg-secondary)",
                            border: "1px solid color-mix(in srgb, var(--crt-red) 10%, transparent)",
                            color: "var(--text-primary)",
                            borderRadius: "8px",
                          }}
                        >
                          {getSeedPhrase()}
                        </div>
                        <button
                          onClick={() => handleCopy(getSeedPhrase(), "seed")}
                          className="text-[9px] px-3 py-1.5 transition-all tracking-wider"
                          style={{
                            border: copied === "seed" ? "1px solid var(--crt-green)" : "1px solid color-mix(in srgb, var(--crt-red) 25%, transparent)",
                            color: copied === "seed" ? "var(--crt-green)" : "var(--crt-red)",
                            borderRadius: "8px",
                          }}
                        >
                          {copied === "seed" ? "COPIED" : "COPY SEED"}
                        </button>
                      </>
                    )}

                    {walletType === "password" && (
                      <>
                        <div className="text-[9px] tracking-[2px]" style={{ color: "var(--text-tertiary)" }}>
                          PASSWORD
                        </div>
                        <div
                          className="p-3 font-mono text-[11px] break-all leading-relaxed"
                          style={{
                            background: "var(--bg-secondary)",
                            border: "1px solid color-mix(in srgb, var(--crt-red) 10%, transparent)",
                            color: "var(--text-primary)",
                            borderRadius: "8px",
                          }}
                        >
                          {getPassword() || "Password not stored on this device — sign in with it again to re-save."}
                        </div>
                        {getPassword() && (
                          <button
                            onClick={() => handleCopy(getPassword(), "pw")}
                            className="text-[9px] px-3 py-1.5 transition-all tracking-wider"
                            style={{
                              border: copied === "pw" ? "1px solid var(--crt-green)" : "1px solid color-mix(in srgb, var(--crt-red) 25%, transparent)",
                              color: copied === "pw" ? "var(--crt-green)" : "var(--crt-red)",
                              borderRadius: "8px",
                            }}
                          >
                            {copied === "pw" ? "COPIED" : "COPY PASSWORD"}
                          </button>
                        )}
                      </>
                    )}

                    {getPrivateKey() && (
                      <>
                        <div className="text-[9px] tracking-[2px]" style={{ color: "var(--text-tertiary)" }}>
                          PRIVATE KEY
                        </div>
                        <div
                          className="p-3 font-mono text-[11px] break-all leading-relaxed"
                          style={{
                            background: "var(--bg-secondary)",
                            border: "1px solid color-mix(in srgb, var(--crt-red) 10%, transparent)",
                            color: "var(--text-primary)",
                            borderRadius: "8px",
                          }}
                        >
                          {getPrivateKey()}
                        </div>
                        <button
                          onClick={() => handleCopy(getPrivateKey(), "pk")}
                          className="text-[9px] px-3 py-1.5 transition-all tracking-wider"
                          style={{
                            border: copied === "pk" ? "1px solid var(--crt-green)" : "1px solid color-mix(in srgb, var(--crt-red) 25%, transparent)",
                            color: copied === "pk" ? "var(--crt-green)" : "var(--crt-red)",
                            borderRadius: "8px",
                          }}
                        >
                          {copied === "pk" ? "COPIED" : "COPY PRIVATE KEY"}
                        </button>
                      </>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* Actions */}
            <div className="flex gap-3 pt-1">
              <button
                onClick={loadWalletData}
                disabled={loading}
                className="flex-1 py-2.5 text-[10px] transition-all tracking-[1.5px]"
                style={{
                  opacity: loading ? 0.6 : 1,
                  background: "var(--crt-blue)",
                  color: "var(--bg-primary)",
                  fontWeight: "bold",
                  borderRadius: "10px",
                  boxShadow: "0 2px 12px color-mix(in srgb, var(--crt-blue) 20%, transparent)",
                  border: "none",
                }}
                onMouseEnter={(e) => { e.currentTarget.style.boxShadow = "0 2px 20px color-mix(in srgb, var(--crt-blue) 35%, transparent)"; }}
                onMouseLeave={(e) => { e.currentTarget.style.boxShadow = "0 2px 12px color-mix(in srgb, var(--crt-blue) 20%, transparent)"; }}
              >
                {loading ? "REFRESHING…" : "REFRESH"}
              </button>
              <button
                onClick={onDisconnect}
                className="flex-1 py-2.5 text-[10px] transition-all tracking-[1.5px]"
                style={{
                  background: "transparent",
                  color: "var(--crt-red)",
                  border: "1px solid color-mix(in srgb, var(--crt-red) 25%, transparent)",
                  fontWeight: "bold",
                  borderRadius: "10px",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = "color-mix(in srgb, var(--crt-red) 10%, transparent)";
                  e.currentTarget.style.borderColor = "color-mix(in srgb, var(--crt-red) 40%, transparent)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = "transparent";
                  e.currentTarget.style.borderColor = "color-mix(in srgb, var(--crt-red) 25%, transparent)";
                }}
              >
                DISCONNECT
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );

  if (inline) {
    return content;
  }

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40 animate-fadeIn"
        style={{ background: "rgba(0,0,0,0.65)", backdropFilter: "blur(4px)" }}
        onClick={onClose}
      />
      {/* Sidebar */}
      <div
        className="fixed top-0 right-0 z-50 h-full w-[680px] max-w-[92vw] flex flex-col animate-slideIn"
        style={{
          background: "var(--bg-primary)",
          borderLeft: "1px solid color-mix(in srgb, var(--crt-blue) 20%, transparent)",
          boxShadow: "-12px 0 60px rgba(0,0,0,0.5), -2px 0 20px color-mix(in srgb, var(--crt-blue) 8%, transparent)",
        }}
      >
        {content}
      </div>
      <style jsx>{`
        @keyframes slideIn {
          from { transform: translateX(100%); opacity: 0.8; }
          to { transform: translateX(0); opacity: 1; }
        }
        .animate-slideIn {
          animation: slideIn 0.2s cubic-bezier(0.16, 1, 0.3, 1) forwards;
        }
        @keyframes fadeIn {
          from { opacity: 0; }
          to { opacity: 1; }
        }
        .animate-fadeIn {
          animation: fadeIn 0.15s ease-out forwards;
        }
      `}</style>
    </>
  );
}
