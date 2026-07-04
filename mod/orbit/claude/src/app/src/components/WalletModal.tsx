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
  onNetworkChange?: () => void;
}

export default function WalletModal({
  address,
  walletType,
  onClose,
  onDisconnect,
  inline = false,
  embedded = false,
  flow = false,
  onNetworkChange,
}: WalletModalProps) {
  const [balance, setBalance] = useState<string>("0.00");
  const [network, setNetwork] = useState<string>("Unknown");
  const [chainId, setChainId] = useState<number>(0);
  const [nativeSymbol, setNativeSymbol] = useState<string>("ETH");
  const [loading, setLoading] = useState(false);
  const [showSeedPhrase, setShowSeedPhrase] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const [showNetworkSelector, setShowNetworkSelector] = useState(false);
  const [switchingNetwork, setSwitchingNetwork] = useState(false);
  const [showTestnets, setShowTestnets] = useState(false);
  const [networkError, setNetworkError] = useState<string | null>(null);

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
    }
  };

  const handleCopy = async (text: string, label?: string) => {
    await utilCopyToClipboard(text);
    setCopied(label || text);
    setTimeout(() => setCopied(null), 1500);
  };

  const getSeedPhrase = () => {
    if (walletType === "local") {
      return localStorage.getItem("claude_jobs_seed") || "No seed phrase found";
    }
    return "Not available for this wallet type";
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

  const walletIcon = walletType === "metamask" ? "🦊" : walletType === "subwallet" ? "◆" : walletType === "password" ? "🔑" : "💾";

  const content = (
    <div className={flow ? "flex flex-col" : "h-full flex flex-col overflow-hidden"}>
      {/* Header: title + close — omitted when embedded inside the merged
          account sidebar, which supplies its own header. Balance/address live
          in the body card so they appear exactly once. */}
      {!embedded && (
      <div
        className="flex items-center justify-between px-5 py-3 shrink-0"
        style={{
          borderBottom: "1px solid rgba(0,170,255,0.12)",
          background: "linear-gradient(180deg, rgba(0,170,255,0.06) 0%, transparent 100%)",
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
            (e.currentTarget as HTMLElement).style.borderColor = "rgba(239,68,68,0.4)";
            (e.currentTarget as HTMLElement).style.color = "var(--crt-red)";
            (e.currentTarget as HTMLElement).style.background = "rgba(239,68,68,0.08)";
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
        {loading && (
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

        {!loading && (
          <>
            {/* Identity Card — balance + address, shown exactly once */}
            <div
              className="p-4 space-y-3"
              style={{
                border: "1px solid rgba(255,255,255,0.06)",
                background: "rgba(255,255,255,0.015)",
                borderRadius: "10px",
              }}
            >
              <div className="flex items-baseline justify-between">
                <div className="flex items-baseline gap-1.5">
                  <span
                    className="text-[24px] font-bold font-mono tabular-nums"
                    style={{
                      color: "var(--crt-green)",
                      textShadow: "0 0 30px rgba(16,185,129,0.2), 0 0 8px rgba(16,185,129,0.1)",
                      letterSpacing: "-1px",
                    }}
                  >
                    {parseFloat(balance).toFixed(4)}
                  </span>
                  <span className="text-[11px] font-bold" style={{ color: "var(--crt-green)", opacity: 0.4 }}>
                    {nativeSymbol}
                  </span>
                </div>
                <span className="text-[10px]" style={{ color: "var(--text-tertiary)", opacity: 0.7 }}>
                  {walletIcon} {walletType?.toUpperCase()}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <div
                  className="flex-1 px-3 py-2.5 font-mono text-[11px] overflow-hidden text-ellipsis"
                  style={{
                    background: "rgba(0,0,0,0.25)",
                    border: "1px solid rgba(255,255,255,0.04)",
                    color: "var(--text-primary)",
                    letterSpacing: "0.3px",
                    borderRadius: "8px",
                  }}
                >
                  {address}
                </div>
                <button
                  onClick={() => handleCopy(address, "address")}
                  className="shrink-0 px-3 py-2.5 text-[9px] transition-all tracking-wider"
                  style={{
                    border: copied === "address" ? "1px solid var(--crt-green)" : "1px solid var(--border-color)",
                    color: copied === "address" ? "var(--crt-green)" : "var(--text-tertiary)",
                    background: copied === "address" ? "rgba(16,185,129,0.08)" : "transparent",
                    borderRadius: "8px",
                  }}
                >
                  {copied === "address" ? "COPIED" : "COPY"}
                </button>
              </div>
              {/* Network row — click to switch */}
              <button
                onClick={() => setShowNetworkSelector(!showNetworkSelector)}
                className="w-full flex items-center justify-between px-3 py-2.5 transition-all text-left"
                style={{
                  border: "1px solid rgba(255,255,255,0.05)",
                  background: "rgba(0,0,0,0.15)",
                  borderRadius: "8px",
                }}
                onMouseEnter={(e) => { e.currentTarget.style.borderColor = "rgba(245,158,11,0.25)"; }}
                onMouseLeave={(e) => { e.currentTarget.style.borderColor = "rgba(255,255,255,0.05)"; }}
              >
                <div className="flex items-center gap-2">
                  <span
                    className="w-[18px] h-[18px] flex items-center justify-center shrink-0"
                    style={{ color: NETWORK_LOGOS[chainId]?.color || "var(--crt-amber)" }}
                    dangerouslySetInnerHTML={{
                      __html: `<svg viewBox="0 0 24 24" width="18" height="18">${NETWORK_LOGOS[chainId]?.svg || '<circle cx="12" cy="12" r="8" fill="currentColor" opacity="0.3"/>'}</svg>`
                    }}
                  />
                  <span className="text-[12px] font-bold" style={{ color: "var(--crt-amber)" }}>{network}</span>
                  <span className="text-[9px] font-mono" style={{ color: "var(--text-tertiary)", opacity: 0.6 }}>#{chainId}</span>
                </div>
                <span className="text-[8px] tracking-wider" style={{ color: "var(--crt-amber)" }}>SWITCH ▾</span>
              </button>
            </div>

            {/* Inline Network Selector */}
            {showNetworkSelector && (
              <div
                className="p-4 space-y-3"
                style={{
                  border: "1px solid rgba(245,158,11,0.15)",
                  background: "rgba(245,158,11,0.02)",
                  borderRadius: "10px",
                }}
              >
                <div className="text-[9px] tracking-[2px] flex items-center justify-between" style={{ color: "var(--text-tertiary)" }}>
                  <span>SELECT NETWORK</span>
                  <button
                    onClick={() => setShowNetworkSelector(false)}
                    className="text-[9px] px-2 py-0.5 transition-all"
                    style={{ color: "var(--crt-amber)", border: "1px solid rgba(245,158,11,0.15)", borderRadius: "8px" }}
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
                      color: !showTestnets ? "var(--crt-amber)" : "var(--text-tertiary)",
                      background: !showTestnets ? "rgba(245,158,11,0.1)" : "transparent",
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
                      color: showTestnets ? "var(--crt-amber)" : "var(--text-tertiary)",
                      background: showTestnets ? "rgba(245,158,11,0.1)" : "transparent",
                      fontWeight: showTestnets ? "bold" : "normal",
                      opacity: showTestnets ? 1 : 0.5,
                    }}
                  >
                    TESTNET
                  </button>
                </div>

                <div className="grid grid-cols-3 gap-2">
                  {EVM_NETWORKS.filter(n => n.testnet === showTestnets).map(n => (
                    <button
                      key={n.chainId}
                      onClick={() => handleSwitchNetwork(n.chainId)}
                      disabled={switchingNetwork}
                      className="p-3 text-center transition-all flex flex-col items-center gap-2"
                      style={{
                        border: n.chainId === chainId ? `1px solid ${NETWORK_LOGOS[n.chainId]?.color || "rgba(245,158,11,0.3)"}40` : "1px solid rgba(255,255,255,0.06)",
                        background: n.chainId === chainId ? `${NETWORK_LOGOS[n.chainId]?.color || "rgba(245,158,11,"}10` : "rgba(0,0,0,0.15)",
                        borderRadius: "10px",
                        opacity: switchingNetwork ? 0.5 : 1,
                      }}
                      onMouseEnter={(e) => {
                        if (n.chainId !== chainId) {
                          e.currentTarget.style.borderColor = `${NETWORK_LOGOS[n.chainId]?.color || "#ffb000"}40`;
                          e.currentTarget.style.background = "rgba(255,255,255,0.03)";
                        }
                      }}
                      onMouseLeave={(e) => {
                        if (n.chainId !== chainId) {
                          e.currentTarget.style.borderColor = "rgba(255,255,255,0.06)";
                          e.currentTarget.style.background = "rgba(0,0,0,0.15)";
                        }
                      }}
                    >
                      <span
                        className="w-[28px] h-[28px] flex items-center justify-center"
                        style={{ color: NETWORK_LOGOS[n.chainId]?.color || "#888" }}
                        dangerouslySetInnerHTML={{
                          __html: `<svg viewBox="0 0 24 24" width="28" height="28">${NETWORK_LOGOS[n.chainId]?.svg || '<circle cx="12" cy="12" r="8" fill="currentColor" opacity="0.3"/>'}</svg>`
                        }}
                      />
                      <div className="text-[10px] font-bold tracking-wide" style={{ color: n.chainId === chainId ? NETWORK_LOGOS[n.chainId]?.color || "var(--crt-amber)" : "var(--text-secondary)" }}>
                        {n.name}
                      </div>
                      <div className="text-[8px] font-mono" style={{ color: "var(--text-tertiary)", opacity: 0.5 }}>{n.symbol}</div>
                    </button>
                  ))}
                </div>

                {networkError && (
                  <div
                    className="text-[10px] text-center py-2 tracking-wider"
                    style={{ color: "var(--crt-red)", border: "1px solid rgba(239,68,68,0.2)", background: "rgba(239,68,68,0.06)", borderRadius: "8px" }}
                  >
                    {networkError}
                  </div>
                )}
              </div>
            )}

            {/* Seed Phrase */}
            {walletType === "local" && (
              <div
                className="p-4"
                style={{
                  border: "1px solid rgba(245,158,11,0.12)",
                  background: "rgba(245,158,11,0.02)",
                  borderRadius: "10px",
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
                  {showSeedPhrase ? "HIDE SEED PHRASE" : "SHOW SEED PHRASE"}
                </button>
                {showSeedPhrase && (
                  <div className="mt-3 space-y-3">
                    <div
                      className="px-3 py-2 text-[10px] tracking-wider"
                      style={{
                        background: "rgba(239,68,68,0.06)",
                        border: "1px solid rgba(239,68,68,0.2)",
                        color: "var(--crt-red)",
                        borderRadius: "8px",
                      }}
                    >
                      KEEP THIS SECRET — NEVER SHARE
                    </div>
                    <div
                      className="p-3 font-mono text-[11px] break-all leading-relaxed"
                      style={{
                        background: "rgba(0,0,0,0.3)",
                        border: "1px solid rgba(239,68,68,0.1)",
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
                        border: copied === "seed" ? "1px solid var(--crt-green)" : "1px solid rgba(239,68,68,0.25)",
                        color: copied === "seed" ? "var(--crt-green)" : "var(--crt-red)",
                        borderRadius: "8px",
                      }}
                    >
                      {copied === "seed" ? "COPIED" : "COPY SEED"}
                    </button>
                  </div>
                )}
              </div>
            )}

            {/* Actions */}
            <div className="flex gap-3 pt-1">
              <button
                onClick={loadWalletData}
                className="flex-1 py-3 text-[10px] transition-all tracking-[1.5px]"
                style={{
                  background: "var(--crt-blue)",
                  color: "#000",
                  fontWeight: "bold",
                  borderRadius: "8px",
                  boxShadow: "0 2px 12px rgba(0,170,255,0.2)",
                  border: "none",
                }}
                onMouseEnter={(e) => { e.currentTarget.style.boxShadow = "0 2px 20px rgba(0,170,255,0.35)"; }}
                onMouseLeave={(e) => { e.currentTarget.style.boxShadow = "0 2px 12px rgba(0,170,255,0.2)"; }}
              >
                REFRESH
              </button>
              <button
                onClick={onDisconnect}
                className="flex-1 py-3 text-[10px] transition-all tracking-[1.5px]"
                style={{
                  background: "transparent",
                  color: "var(--crt-red)",
                  border: "1px solid rgba(239,68,68,0.25)",
                  fontWeight: "bold",
                  borderRadius: "8px",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = "rgba(239,68,68,0.1)";
                  e.currentTarget.style.borderColor = "rgba(239,68,68,0.4)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = "transparent";
                  e.currentTarget.style.borderColor = "rgba(239,68,68,0.25)";
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
          borderLeft: "1px solid rgba(0,170,255,0.2)",
          boxShadow: "-12px 0 60px rgba(0,0,0,0.5), -2px 0 20px rgba(0,170,255,0.08)",
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
