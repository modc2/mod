"use client";

// In-app delete confirmation for a strat — portals to <body> so it floats
// above the strat sidebar and the hub grid, both of which route deletes
// through useStratManager. Replaces the native window.confirm() popup, which
// rendered as an ugly "modc2.com says" browser dialog.

import { createPortal } from "react-dom";

interface Props {
  /** Strat name to confirm, or null when nothing is pending. */
  name: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}

export default function ConfirmDeleteStrat({ name, onConfirm, onCancel }: Props) {
  if (name === null) return null;
  return createPortal(
    <div className="fixed inset-0 z-[70] grid place-items-center p-4" onClick={onCancel}>
      <div className="absolute inset-0" style={{ background: "rgb(var(--pixel-black-rgb)/0.6)" }} />
      <div
        role="alertdialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") onCancel();
          if (e.key === "Enter") onConfirm();
        }}
        tabIndex={-1}
        ref={(el) => el?.focus()}
        className="relative w-full max-w-[360px] rounded-[var(--radius)] backdrop-blur-md p-4 outline-none"
        style={{
          background:
            "linear-gradient(180deg, rgb(var(--pixel-black-rgb)/0.98), rgb(var(--pixel-bg-rgb)/0.96))",
          border: "1px solid var(--border)",
          boxShadow: "0 24px 64px rgba(0,0,0,0.6)",
          animation: "drawer-in-left 0.14s ease-out",
        }}
      >
        <div className="text-[11px] font-mono font-bold tracking-[0.16em] text-red-400/90">
          DELETE STRAT
        </div>
        <div className="mt-2 text-[12.5px] font-mono text-pixel-white leading-relaxed">
          Delete strat <span className="text-green-400 font-semibold">“{name}”</span>?
        </div>
        <div className="mt-1 text-[10.5px] font-mono text-pixel-gray">
          This removes the strat and its file from disk. This can’t be undone.
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="rounded-[var(--radius-sm)] border border-pixel-border px-3 py-1.5 text-[11px] font-mono font-semibold tracking-[0.06em] text-pixel-gray hover:text-pixel-white hover:border-pixel-white/40 transition-colors"
          >
            CANCEL
          </button>
          <button
            onClick={onConfirm}
            autoFocus
            className="rounded-[var(--radius-sm)] border border-red-400/50 bg-red-400/10 px-3 py-1.5 text-[11px] font-mono font-semibold tracking-[0.06em] text-red-400 hover:bg-red-400/20 hover:border-red-400 transition-colors"
          >
            DELETE
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
