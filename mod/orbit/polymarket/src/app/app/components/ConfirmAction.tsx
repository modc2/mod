"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

interface Props {
  title: string;
  body: string;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
  /** If set, CONFIRM is disabled for this many ms after mount to prevent accidental double-clicks. */
  armedDelayMs?: number;
}

export default function ConfirmAction({ title, body, confirmLabel, onConfirm, onCancel, armedDelayMs }: Props) {
  const [ready, setReady] = useState(!armedDelayMs);
  useEffect(() => {
    if (!armedDelayMs) return;
    const t = setTimeout(() => setReady(true), armedDelayMs);
    return () => clearTimeout(t);
  }, [armedDelayMs]);

  return createPortal(
    <div className="fixed inset-0 z-[70] grid place-items-center p-4" onClick={onCancel}>
      <div className="absolute inset-0" style={{ background: "rgb(var(--pixel-black-rgb)/0.6)" }} />
      <div
        role="alertdialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") onCancel();
          if (e.key === "Enter" && ready) onConfirm();
        }}
        tabIndex={-1}
        ref={(el) => el?.focus()}
        className="relative w-full max-w-[380px] rounded-[var(--radius)] backdrop-blur-md p-4 outline-none"
        style={{
          background:
            "linear-gradient(180deg, rgb(var(--pixel-black-rgb)/0.98), rgb(var(--pixel-bg-rgb)/0.96))",
          border: "1px solid var(--border)",
          boxShadow: "0 24px 64px rgba(0,0,0,0.6)",
          animation: "drawer-in-left 0.14s ease-out",
        }}
      >
        <div className="text-[11px] font-mono font-bold tracking-[0.16em] text-red-400/90">
          {title}
        </div>
        <div className="mt-2 text-[10.5px] font-mono text-pixel-gray leading-relaxed">
          {body}
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
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
