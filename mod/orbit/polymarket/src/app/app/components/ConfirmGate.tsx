"use client";

// In-app gate-arming confirmation — portals to <body>. Replaces the native
// window.confirm() in lib/armGate.ts so the arming step matches the themed UI.

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { describeGate, type CompiledGate } from "../lib/semanticFilter";

interface Props {
  gate: CompiledGate;
  names: string[];
  onConfirm: () => void;
  onCancel: () => void;
}

export default function ConfirmGate({ gate, names, onConfirm, onCancel }: Props) {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setReady(true), 300);
    return () => clearTimeout(t);
  }, []);

  const desc = describeGate(gate);
  const mq = gate.marketQuery;
  const viewOnly = gate.viewOnly ?? [];

  return createPortal(
    <div className="fixed inset-0 z-[70] grid place-items-center p-4" onClick={onCancel}>
      <div className="absolute inset-0" style={{ background: "rgb(var(--pixel-black-rgb)/0.6)" }} />
      <div
        role="alertdialog"
        aria-modal="true"
        aria-label="Confirm gate arming"
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
          border: "1px solid var(--border)",
          boxShadow: "0 24px 64px rgba(0,0,0,0.6)",
          animation: "drawer-in-left 0.14s ease-out",
        }}
      >
        <div className="text-[11px] font-mono font-bold tracking-[0.16em] text-pixel-white/80">
          ARM GATE — {names.length} TRADER{names.length === 1 ? "" : "S"}
        </div>
        <div className="mt-2 text-[12px] font-mono text-pixel-white leading-relaxed">
          {names.join(", ")}
        </div>
        <div className="mt-2 text-[11px] font-mono text-green-400">{desc}</div>
        {mq && (
          <div className="mt-1 text-[10.5px] font-mono text-pixel-gray">
            markets matching: {mq.length > 200 ? mq.slice(0, 200) + "…" : mq}
          </div>
        )}
        {!mq && (
          <div className="mt-1 text-[10.5px] font-mono text-pixel-gray">any market</div>
        )}
        {viewOnly.length > 0 && (
          <div className="mt-1.5 text-[10px] font-mono text-amber-400/80 leading-relaxed">
            NOT armed — engine does not enforce: {viewOnly.join(", ")}
          </div>
        )}
        <div className="mt-1.5 text-[10.5px] font-mono text-pixel-gray">
          Running sessions are reconfigured in place. Trades outside the gate stop being copied.
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
            className="rounded-[var(--radius-sm)] border border-green-400/50 bg-green-400/10 px-3 py-1.5 text-[11px] font-mono font-semibold tracking-[0.06em] text-green-400 hover:bg-green-400/20 hover:border-green-400 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            ARM GATE
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
