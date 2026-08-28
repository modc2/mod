"use client";

import { useEffect, useState } from "react";

/**
 * Task vault password sheet.
 *
 *   "enable" — pick a password, seal everything this wallet has run.
 *   "unlock" — the prompt after every sign-in, because signing out drops the
 *              key. Carries the optional rotate: new password, same data, one
 *              step, so changing it never needs a second trip to ACCOUNT.
 *
 * The modal owns only the fields. Every call and every error string comes from
 * the API through props — the vault's rules live server-side, not here.
 */

export type VaultMode = "enable" | "unlock";

interface VaultModalProps {
  open: boolean;
  mode: VaultMode;
  busy?: boolean;
  error?: string | null;
  /** How many of this wallet's tasks are already sealed (unlock mode). */
  sealed?: number;
  /** Total tasks this wallet owns (enable mode — what's about to be sealed). */
  total?: number;
  onSubmit: (password: string, newPassword: string | null) => void;
  /** Dismiss without unlocking — the ledger just stays sealed to you. */
  onDismiss: () => void;
}

const ACCENT = "var(--crt-green, #34d399)";
const MIN = 8;

export default function VaultModal({
  open,
  mode,
  busy,
  error,
  sealed = 0,
  total = 0,
  onSubmit,
  onDismiss,
}: VaultModalProps) {
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [rotate, setRotate] = useState(false);
  const [next, setNext] = useState("");
  const [nextConfirm, setNextConfirm] = useState("");

  // Fresh fields every time the sheet opens — a password should never sit in
  // component state across two different asks.
  useEffect(() => {
    if (!open) return;
    setPassword("");
    setConfirm("");
    setRotate(false);
    setNext("");
    setNextConfirm("");
  }, [open, mode]);

  const enabling = mode === "enable";
  const short = password.length > 0 && password.length < MIN;
  const mismatch = enabling && confirm.length > 0 && confirm !== password;
  const rotateShort = rotate && next.length > 0 && next.length < MIN;
  const rotateMismatch = rotate && nextConfirm.length > 0 && nextConfirm !== next;
  const ready =
    password.length >= MIN &&
    (!enabling || confirm === password) &&
    (!rotate || (next.length >= MIN && nextConfirm === next));

  const submit = () => {
    if (!ready || busy) return;
    onSubmit(password, rotate ? next : null);
  };

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onDismiss();
      if (e.key === "Enter") submit();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (!open) return null;

  const field = (
    label: string,
    value: string,
    set: (v: string) => void,
    note?: string | null,
    autoFocus?: boolean
  ) => (
    <label className="flex flex-col gap-1">
      <span className="text-[9px] font-bold uppercase tracking-[0.14em]" style={{ color: "var(--text-tertiary)" }}>
        {label}
      </span>
      <input
        type="password"
        value={value}
        autoFocus={autoFocus}
        onChange={(e) => set(e.target.value)}
        disabled={busy}
        className="focus-ring font-mono text-[12px] px-3 py-2 rounded-[10px] w-full"
        style={{
          color: "var(--text-primary)",
          background: "var(--bg-primary)",
          border: `1px solid ${note ? "color-mix(in srgb, var(--crt-red) 45%, var(--border-color))" : "var(--border-color)"}`,
        }}
        spellCheck={false}
        autoComplete={enabling ? "new-password" : "current-password"}
      />
      {note && (
        <span className="text-[9.5px]" style={{ color: "var(--crt-red)" }}>
          {note}
        </span>
      )}
    </label>
  );

  return (
    <div
      role="dialog"
      aria-modal="true"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onDismiss();
      }}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 210,
        display: "grid",
        placeItems: "center",
        padding: 24,
        background: "radial-gradient(120% 120% at 50% 0%, rgba(52,211,153,0.10), rgba(0,0,0,0.66) 60%)",
        backdropFilter: "blur(14px) saturate(120%)",
        WebkitBackdropFilter: "blur(14px) saturate(120%)",
      }}
    >
      <div
        style={{
          width: "min(430px, 100%)",
          borderRadius: "var(--radius-lg, 22px)",
          background: "var(--glass-bg-strong, var(--bg-secondary, rgba(28,26,32,0.94)))",
          border: "1px solid var(--border-color)",
          boxShadow: "0 24px 70px rgba(0,0,0,0.62)",
          overflow: "hidden",
        }}
      >
        <div style={{ height: 3, background: `linear-gradient(90deg, transparent, ${ACCENT}, transparent)` }} />
        <div className="flex flex-col gap-3 p-5">
          <div className="flex items-center gap-2">
            <span aria-hidden style={{ fontSize: 15, color: ACCENT }}>
              {enabling ? "◈" : "🔒"}
            </span>
            <span className="text-[12px] font-bold uppercase tracking-[0.12em]" style={{ color: "var(--text-primary)" }}>
              {enabling ? "Encrypt my tasks" : "Vault locked"}
            </span>
          </div>

          <p className="text-[10.5px] leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
            {enabling ? (
              <>
                The task ledger is public: prompts and output are readable by anyone
                who opens this console. Pick a password and yours stop being — every
                prompt, every line of output, sealed with a key only this password
                opens{total > 0 ? `, starting with the ${total} task${total === 1 ? "" : "s"} already on record` : ""}.
                The server keeps the key in memory only, so you'll enter this again
                after every sign-out. <b style={{ color: "var(--text-secondary)" }}>Lose it and the tasks stay sealed — there is no reset.</b>
              </>
            ) : (
              <>
                {sealed > 0 ? `${sealed} of your task${sealed === 1 ? " is" : "s are"} sealed. ` : ""}
                Signing out dropped the key. Enter your password to read them again
                and to start new ones.
              </>
            )}
          </p>

          {field(enabling ? "Password" : "Vault password", password, setPassword, short ? `at least ${MIN} characters` : null, true)}
          {enabling && field("Confirm", confirm, setConfirm, mismatch ? "passwords don't match" : null)}

          {!enabling && (
            <label className="flex items-center gap-2 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={rotate}
                onChange={(e) => setRotate(e.target.checked)}
                disabled={busy}
                style={{ accentColor: "var(--crt-green)" }}
              />
              <span className="text-[10px]" style={{ color: "var(--text-secondary)" }}>
                Also change my password
              </span>
            </label>
          )}
          {rotate && (
            <>
              {field("New password", next, setNext, rotateShort ? `at least ${MIN} characters` : null)}
              {field("Confirm new", nextConfirm, setNextConfirm, rotateMismatch ? "passwords don't match" : null)}
              <p className="text-[9.5px] leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
                Rotating re-wraps the same data key under the new password — your
                tasks aren't re-encrypted, and nothing has to be re-read.
              </p>
            </>
          )}

          {error && (
            <div
              role="alert"
              className="text-[10.5px] leading-relaxed px-3 py-2 rounded-[10px]"
              style={{
                color: "var(--crt-red)",
                border: "1px solid color-mix(in srgb, var(--crt-red) 35%, var(--border-color))",
                background: "color-mix(in srgb, var(--crt-red) 7%, transparent)",
              }}
            >
              {error}
            </div>
          )}

          <div className="flex items-center gap-2 pt-1">
            <button
              onClick={submit}
              disabled={!ready || busy}
              className="focus-ring flex-1 text-[10px] font-bold uppercase tracking-[0.14em] px-3 py-2 rounded-[10px] transition-all"
              style={{
                color: ready ? ACCENT : "var(--text-tertiary)",
                border: `1px solid color-mix(in srgb, ${ACCENT} ${ready ? 40 : 18}%, var(--border-color))`,
                background: `color-mix(in srgb, ${ACCENT} ${ready ? 10 : 4}%, transparent)`,
                cursor: busy ? "wait" : ready ? "pointer" : "not-allowed",
              }}
            >
              {busy ? "…" : enabling ? "Encrypt" : rotate ? "Unlock & rotate" : "Unlock"}
            </button>
            <button
              onClick={onDismiss}
              disabled={busy}
              className="focus-ring text-[10px] font-bold uppercase tracking-[0.14em] px-3 py-2 rounded-[10px] transition-all"
              style={{
                color: "var(--text-tertiary)",
                border: "1px solid var(--border-color)",
                background: "transparent",
              }}
              title={enabling ? "Leave the ledger as it is" : "Browse with your tasks sealed"}
            >
              {enabling ? "Not now" : "Stay locked"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
