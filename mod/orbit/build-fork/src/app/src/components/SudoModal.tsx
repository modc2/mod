"use client";

import { useEffect } from "react";

export type SudoStatus = "review" | "signing" | "success" | "error";

interface SudoModalProps {
  open: boolean;
  action: string;
  target: string;
  status: SudoStatus;
  error?: string | null;
  signerLabel?: string | null; // short owner address, shown as the authorizing key
  // Minutes this signature will keep sudo unlocked (owner policy). null/0 =
  // per-operation signing; the modal then keeps the classic one-shot copy.
  sessionMins?: number | null;
  onAuthorize: () => void;
  onCancel: () => void;
}

const ACCENT = "#cc785c"; // claude clay — privileged, premium, not "danger red"

// One-liner describing the privileged op in human terms.
function describe(action: string, target: string): { verb: string; noun: string } {
  switch (action) {
    case "write":
      return { verb: "Write to", noun: target };
    case "delete":
      return { verb: "Delete module", noun: target };
    case "rename":
      return { verb: "Rename module", noun: target };
    case "restore":
      return { verb: "Restore module", noun: target };
    case "kill":
      return { verb: "Kill process", noun: target };
    default:
      return { verb: action, noun: target };
  }
}

export default function SudoModal({
  open,
  action,
  target,
  status,
  error,
  signerLabel,
  sessionMins,
  onAuthorize,
  onCancel,
}: SudoModalProps) {
  // Esc cancels, Enter authorizes — only while reviewing.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && status !== "signing") onCancel();
      if (e.key === "Enter" && status === "review") onAuthorize();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, status, onAuthorize, onCancel]);

  if (!open) return null;

  const { verb, noun } = describe(action, target);
  const busy = status === "signing";

  const mins = sessionMins && sessionMins > 0 ? Math.round(sessionMins) : 0;
  const sessionLabel =
    mins >= 60 ? `${Math.floor(mins / 60)} hour${mins >= 120 ? "s" : ""}${mins % 60 ? ` ${mins % 60} min` : ""}` : `${mins} minutes`;
  const guarantees = [
    { k: "owner", label: "Signed by the owner key", sub: signerLabel || "your connected wallet" },
    { k: "once", label: "One-time use", sub: "replay-protected — the signature can't be reused" },
    { k: "fresh", label: "Expires in 120 seconds", sub: "bound to this exact operation" },
    // What this signature buys beyond the one operation, per the owner policy.
    action === "policy"
      ? { k: "session", label: "Always asks", sub: "auth policy changes never ride a cached session" }
      : mins > 0
      ? { k: "session", label: `Unlocks sudo for ${sessionLabel}`, sub: "no re-signing until it expires — tune or lock in ACCOUNT" }
      : null,
  ].filter(Boolean) as { k: string; label: string; sub: string }[];

  return (
    <div
      role="dialog"
      aria-modal="true"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onCancel();
      }}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 200,
        display: "grid",
        placeItems: "center",
        padding: "24px",
        background: "radial-gradient(120% 120% at 50% 0%, rgba(204,120,92,0.10), rgba(0,0,0,0.66) 60%)",
        backdropFilter: "blur(14px) saturate(120%)",
        WebkitBackdropFilter: "blur(14px) saturate(120%)",
        animation: "sudoFade 180ms cubic-bezier(0.22,1,0.36,1)",
      }}
    >
      <div
        style={{
          width: "min(460px, 100%)",
          borderRadius: "var(--radius-lg, 22px)",
          background: "var(--glass-bg-strong, rgba(28,26,32,0.92))",
          border: "1px solid var(--glass-border-strong, rgba(255,255,255,0.16))",
          boxShadow:
            "0 0 0 1px rgba(204,120,92,0.28), 0 24px 70px rgba(0,0,0,0.62), 0 0 60px rgba(204,120,92,0.12)",
          overflow: "hidden",
          animation: "sudoRise 260ms cubic-bezier(0.22,1,0.36,1)",
        }}
      >
        {/* Accent hairline */}
        <div
          style={{
            height: "3px",
            background: `linear-gradient(90deg, transparent, ${ACCENT}, transparent)`,
            opacity: 0.9,
          }}
        />

        <div style={{ padding: "26px 26px 22px" }}>
          {/* Crest */}
          <div style={{ display: "flex", alignItems: "center", gap: "14px", marginBottom: "20px" }}>
            <div
              style={{
                width: "46px",
                height: "46px",
                borderRadius: "14px",
                display: "grid",
                placeItems: "center",
                background: `linear-gradient(145deg, rgba(204,120,92,0.22), rgba(204,120,92,0.06))`,
                border: `1px solid rgba(204,120,92,0.4)`,
                boxShadow: busy ? `0 0 0 0 rgba(204,120,92,0.5)` : "none",
                animation: busy ? "sudoPulse 1.4s ease-in-out infinite" : "none",
                flexShrink: 0,
              }}
            >
              {/* key glyph */}
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke={ACCENT} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="8" cy="15" r="4" />
                <path d="M10.85 12.15 19 4" />
                <path d="m18 5 2 2" />
                <path d="m15 8 2 2" />
              </svg>
            </div>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: "15px", fontWeight: 650, color: "var(--text-primary,#fff)", letterSpacing: "-0.01em" }}>
                Sudo Authorization
              </div>
              <div style={{ fontSize: "12px", color: "var(--text-tertiary,#8a8aa2)", marginTop: "2px" }}>
                Privileged cross-module operation
              </div>
            </div>
          </div>

          {/* Operation card */}
          <div
            style={{
              borderRadius: "14px",
              background: "rgba(0,0,0,0.28)",
              border: "1px solid var(--glass-border, rgba(255,255,255,0.09))",
              padding: "14px 16px",
              marginBottom: "18px",
            }}
          >
            <div
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: "6px",
                fontSize: "10px",
                fontWeight: 700,
                letterSpacing: "0.12em",
                textTransform: "uppercase",
                color: ACCENT,
                background: "rgba(204,120,92,0.12)",
                border: "1px solid rgba(204,120,92,0.3)",
                borderRadius: "999px",
                padding: "3px 9px",
                marginBottom: "10px",
              }}
            >
              {action}
            </div>
            <div style={{ fontSize: "12px", color: "var(--text-tertiary,#8a8aa2)", marginBottom: "3px" }}>{verb}</div>
            <div
              title={noun}
              style={{
                fontFamily: "var(--font-mono, ui-monospace, monospace)",
                fontSize: "13px",
                color: "var(--text-secondary,#d8d8e4)",
                wordBreak: "break-all",
                lineHeight: 1.45,
              }}
            >
              {noun}
            </div>
          </div>

          {/* Guarantees */}
          <div style={{ display: "flex", flexDirection: "column", gap: "11px", marginBottom: "22px" }}>
            {guarantees.map((g) => (
              <div key={g.k} style={{ display: "flex", alignItems: "flex-start", gap: "10px" }}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#34d399" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" style={{ marginTop: "1px", flexShrink: 0 }}>
                  <path d="M20 6 9 17l-5-5" />
                </svg>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: "12.5px", color: "var(--text-primary,#fff)", fontWeight: 550 }}>{g.label}</div>
                  <div style={{ fontSize: "11px", color: "var(--text-tertiary,#8a8aa2)", marginTop: "1px" }}>{g.sub}</div>
                </div>
              </div>
            ))}
          </div>

          {/* Error */}
          {status === "error" && error && (
            <div
              style={{
                fontSize: "12px",
                color: "#fb7185",
                background: "rgba(251,113,133,0.08)",
                border: "1px solid rgba(251,113,133,0.25)",
                borderRadius: "10px",
                padding: "9px 12px",
                marginBottom: "16px",
              }}
            >
              {error}
            </div>
          )}

          {/* Actions */}
          {status === "success" ? (
            <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: "9px", padding: "8px 0", color: "#34d399", fontSize: "13px", fontWeight: 600 }}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#34d399" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" style={{ animation: "sudoPop 320ms cubic-bezier(0.22,1,0.36,1)" }}>
                <path d="M20 6 9 17l-5-5" />
              </svg>
              Authorized
            </div>
          ) : (
            <div style={{ display: "flex", gap: "10px" }}>
              <button
                onClick={onCancel}
                disabled={busy}
                style={{
                  flex: "0 0 auto",
                  padding: "11px 18px",
                  borderRadius: "12px",
                  background: "transparent",
                  border: "1px solid var(--glass-border-strong, rgba(255,255,255,0.16))",
                  color: "var(--text-secondary,#d8d8e4)",
                  fontSize: "13px",
                  fontWeight: 550,
                  cursor: busy ? "not-allowed" : "pointer",
                  opacity: busy ? 0.5 : 1,
                  transition: "background 140ms, border-color 140ms",
                }}
              >
                Cancel
              </button>
              <button
                onClick={onAuthorize}
                disabled={busy}
                style={{
                  flex: 1,
                  padding: "11px 18px",
                  borderRadius: "12px",
                  background: busy
                    ? "rgba(204,120,92,0.35)"
                    : `linear-gradient(145deg, ${ACCENT}, #b5654a)`,
                  border: "1px solid rgba(204,120,92,0.55)",
                  color: "#fff",
                  fontSize: "13px",
                  fontWeight: 650,
                  letterSpacing: "0.01em",
                  cursor: busy ? "progress" : "pointer",
                  boxShadow: busy ? "none" : "0 6px 20px rgba(204,120,92,0.32)",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: "9px",
                  transition: "transform 120ms, box-shadow 140ms",
                }}
              >
                {busy ? (
                  <>
                    <span
                      style={{
                        width: "14px",
                        height: "14px",
                        borderRadius: "50%",
                        border: "2px solid rgba(255,255,255,0.4)",
                        borderTopColor: "#fff",
                        display: "inline-block",
                        animation: "sudoSpin 0.7s linear infinite",
                      }}
                    />
                    Waiting for signature…
                  </>
                ) : status === "error" ? (
                  "Re-sign & authorize"
                ) : (
                  "Authorize & sign"
                )}
              </button>
            </div>
          )}
        </div>
      </div>

      <style jsx global>{`
        @keyframes sudoFade {
          from { opacity: 0; }
          to { opacity: 1; }
        }
        @keyframes sudoRise {
          from { opacity: 0; transform: translateY(14px) scale(0.97); }
          to { opacity: 1; transform: translateY(0) scale(1); }
        }
        @keyframes sudoSpin {
          to { transform: rotate(360deg); }
        }
        @keyframes sudoPop {
          0% { transform: scale(0.4); opacity: 0; }
          60% { transform: scale(1.15); }
          100% { transform: scale(1); opacity: 1; }
        }
        @keyframes sudoPulse {
          0%, 100% { box-shadow: 0 0 0 0 rgba(204,120,92,0.45); }
          50% { box-shadow: 0 0 0 8px rgba(204,120,92,0); }
        }
      `}</style>
    </div>
  );
}
