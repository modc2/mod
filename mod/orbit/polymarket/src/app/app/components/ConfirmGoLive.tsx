"use client";

// In-app live-trading confirmation — portals to <body> so it floats above
// every panel. Replaces the native window.confirm() popup for the highest-
// stakes action in the module: the moment a user authorises real Polymarket
// orders. Matches the visual language of ConfirmDeleteStrat.tsx.

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

interface Props {
  subject: string;
  amountUsd: number | null;
  onConfirm: () => void;
  onCancel: () => void;
}

function fmtUsd(v: number | null | undefined): string | null {
  if (v === null || v === undefined || !Number.isFinite(v)) return null;
  return `$${Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export default function ConfirmGoLive({ subject, amountUsd, onConfirm, onCancel }: Props) {
  // 300ms linger prevents accidental double-clicks on the confirm button.
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setReady(true), 300);
    return () => clearTimeout(t);
  }, []);

  const money = fmtUsd(amountUsd);

  return createPortal(
    <div className="fixed inset-0 z-[70] grid place-items-center p-4" onClick={onCancel}>
      <div className="absolute inset-0" style={{ background: "rgb(var(--pixel-black-rgb)/0.6)" }} />
      <div
        role="alertdialog"
        aria-modal="true"
        aria-label="Confirm live trading"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") onCancel();
          if (e.key === "Enter" && ready) onConfirm();
        }}
        tabIndex={-1}
        ref={(el) => el?.focus()}
        className="relative w-full max-w-[400px] rounded-[var(--radius)] backdrop-blur-md p-4 outline-none"
        style={{
          background:
            "linear-gradient(180deg, rgb(var(--pixel-black-rgb)/0.98), rgb(var(--pixel-bg-rgb)/0.96))",
          border: "1px solid rgb(248 113 113 / 0.5)",
          boxShadow: "0 24px 64px rgba(0,0,0,0.6)",
          animation: "drawer-in-left 0.14s ease-out",
        }}
      >
        <div className="text-[11px] font-mono font-bold tracking-[0.16em] text-red-400/90">
          REAL MONEY — LIVE TRADING
        </div>
        <div className="mt-2 text-[12.5px] font-mono text-pixel-white leading-relaxed">
          <span className="font-semibold">{subject}</span>
          {money && (
            <>, sized against <span className="text-green-400 font-semibold">{money}</span>,</>
          )}{" "}
          will start placing <span className="text-red-400 font-semibold">REAL</span> orders on
          Polymarket.
        </div>
        <div className="mt-1.5 text-[10.5px] font-mono text-pixel-gray leading-relaxed">
          Fills are real. Losses are real. An order that fills cannot be taken back. Switching back
          to PAPER stops new orders — it does not close positions already open.
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="rounded-[var(--radius-sm)] border border-pixel-border px-3 py-1.5 text-[11px] font-mono font-semibold tracking-[0.06em] text-pixel-gray hover:text-pixel-white hover:border-pixel-white/40 transition-colors"
          >
            CANCEL
          </button>
          <button
            onClick={ready ? onConfirm : undefined}
            disabled={!ready}
            className="rounded-[var(--radius-sm)] border border-red-400/50 bg-red-400/10 px-3 py-1.5 text-[11px] font-mono font-semibold tracking-[0.06em] text-red-400 hover:bg-red-400/20 hover:border-red-400 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            CONFIRM LIVE
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
