"use client";

// THE LIQUIDITY STRIP — one line that answers "where is my money right now".
//
//   OTHER CHAINS ▸ WALLET ▸ TRADING ▸ IN PLAY
//
// Four pools, left to right in the order money actually flows: stablecoins
// sitting on other EVM chains (bridge them in), USDC.e in the Polygon wallet
// (deposit it), the tradable balance (allocate it), and what the strats hold
// in open positions. Each pool is a button that lands you on the block that
// moves it — CHAINS scrolls to the bridge, WALLET/TRADING to the move panel,
// IN PLAY switches the column to the INDEX tab where allocation lives.

import { useMemo } from "react";
import { useAuth } from "../context/AuthContext";
import { stablesOffPolygon, useChainBalances } from "../lib/chainBalances";
import { useStratStats, fmtUsd } from "../lib/stratStats";

// UserSidebar's SIDEBAR_TAB_EVENT — dispatched by literal name to avoid a
// MoneyBlock ↔ UserSidebar import cycle. Keep in sync with UserSidebar.tsx.
function openIndexTab() {
  try { localStorage.setItem("poly_sidebar_tab", "INDEX"); } catch {}
  window.dispatchEvent(new CustomEvent("poly-sidebar-tab", { detail: "INDEX" }));
}

function scrollToId(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

export default function LiquidityFlow() {
  const { auth } = useAuth();
  const { holdings, loading, refresh } = useChainBalances(auth.address);
  const { stats, cash } = useStratStats();

  const offPolygon = useMemo(() => stablesOffPolygon(holdings), [holdings]);
  const wallet = holdings["polygon"]?.usdc ?? null;
  const inPlay = Object.values(stats).reduce((s, m) => s + (m?.openValue ?? 0), 0);

  if (!auth.connected) return null;

  const pools: Array<{
    key: string;
    label: string;
    value: number | null;
    hint: string;
    onClick: () => void;
    accent?: "waiting";
  }> = [
    {
      key: "chains",
      label: "OTHER CHAINS",
      value: loading && offPolygon === 0 ? null : offPolygon,
      hint: "USDC + USDT on Ethereum, Base, Arbitrum and Optimism — bridge it in below",
      onClick: () => scrollToId("bridge-panel"),
      accent: offPolygon > 1 ? "waiting" : undefined,
    },
    {
      key: "wallet",
      label: "WALLET",
      value: wallet,
      hint: "USDC.e in your Polygon wallet — deposit it to trade",
      onClick: () => scrollToId("sidebar-wallet-panel"),
    },
    {
      key: "trading",
      label: "TRADING",
      value: cash,
      hint: "Your tradable Polymarket balance — free cash the strats draw on",
      onClick: () => scrollToId("sidebar-wallet-panel"),
    },
    {
      key: "inplay",
      label: "IN PLAY",
      value: inPlay,
      hint: "Open positions across your strats — manage the split on the INDEX tab",
      onClick: openIndexTab,
    },
  ];

  return (
    <div className="pixel-panel border-2 border-pixel-border px-2 py-2">
      <div className="flex items-center px-1 pb-1.5">
        <span className="text-[10px] uppercase tracking-[0.2em] text-pixel-gray">Your liquidity</span>
        <button
          onClick={refresh}
          className="ml-auto text-pixel-muted hover:text-green-400 text-[13px] leading-none"
          title="Re-read every chain"
        >
          ↻
        </button>
      </div>
      <div className="flex items-stretch">
        {pools.map((p, i) => (
          <div key={p.key} className="contents">
            {i > 0 && (
              <span className="self-center text-[11px] text-pixel-gray/50 px-0.5 shrink-0">▸</span>
            )}
            <button
              onClick={p.onClick}
              title={p.hint}
              className={`flex-1 min-w-0 rounded-[var(--radius-sm)] px-1 py-1.5 text-center transition-colors hover:bg-pixel-white/[0.06] ${
                p.accent === "waiting" ? "bg-amber-400/[0.08]" : ""
              }`}
            >
              <span
                className={`block text-[8.5px] tracking-[0.14em] ${
                  p.accent === "waiting" ? "text-amber-400" : "text-pixel-gray"
                }`}
              >
                {p.label}
              </span>
              <span
                className={`block font-mono text-[13px] tabular-nums truncate ${
                  p.value == null
                    ? "text-pixel-gray/50"
                    : p.value > 0.005
                      ? p.accent === "waiting"
                        ? "text-amber-400"
                        : "text-green-400"
                      : "text-pixel-gray"
                }`}
              >
                {p.value == null ? "…" : fmtUsd(p.value)}
              </span>
            </button>
          </div>
        ))}
      </div>
      {offPolygon > 1 && (
        <div className="px-1 pt-1 text-[9.5px] font-mono text-amber-400/90 leading-snug">
          {fmtUsd(offPolygon)} is sitting on other chains — bridge it below and it lands as Polygon USDC.
        </div>
      )}
    </div>
  );
}
