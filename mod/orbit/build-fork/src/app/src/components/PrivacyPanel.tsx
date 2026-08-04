"use client";

import { useCallback, useEffect, useState } from "react";

/**
 * Public ⇄ private for one module.
 *
 * Every module ships PUBLIC: it sits on the hub for anyone who opens the
 * console, signed in or not. This sheet is the opt-out. Turning privacy on
 * hides the module from every other caller (hub, files, task ledger) and
 * switches its snapshots to a single encrypted blob — the encryption is the
 * server's (see api/src/privacy.rs), so nothing here handles ciphertext.
 *
 * The key has two homes and the owner controls both:
 *   · the server's copy — generated on enable, readable here, deletable here.
 *     Once deleted only a verifier remains; the server can check a key it is
 *     handed but can never produce one again.
 *   · this device — an optional localStorage copy so the console can keep
 *     operating (going public again, snapshotting) after the server copy is
 *     gone. "Forget on this device" wipes it client-side.
 *
 * Delete both and the module's encrypted history is only as recoverable as
 * the key the owner wrote down. That is the point.
 */

interface PrivacyPanelProps {
  open: boolean;
  module: string;
  apiBase: string;
  /** Bearer header for the owner's session; undefined in local mode. */
  authHeader?: Record<string, string>;
  onClose: () => void;
  /** Fires after any successful flip so the hub can re-pull its cards. */
  onChanged?: (isPrivate: boolean) => void;
}

const ACCENT = "var(--crt-purple, #c084fc)";

const keyStoreId = (module: string) => `buildfork_privacy_key_${module}`;

function readLocalKey(module: string): string | null {
  try {
    return localStorage.getItem(keyStoreId(module));
  } catch {
    return null;
  }
}

function writeLocalKey(module: string, key: string | null) {
  try {
    if (key) localStorage.setItem(keyStoreId(module), key);
    else localStorage.removeItem(keyStoreId(module));
  } catch {
    /* private-mode / quota — the key just isn't kept here */
  }
}

interface Status {
  private: boolean;
  password_held: boolean;
  owner?: string | null;
}

export default function PrivacyPanel({
  open,
  module,
  apiBase,
  authHeader,
  onClose,
  onChanged,
}: PrivacyPanelProps) {
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  // The key in hand right now — from enable, from a reveal, or from this
  // device. Never persisted unless the owner asks for it.
  const [key, setKey] = useState<string | null>(null);
  const [keepLocal, setKeepLocal] = useState(false);
  const [copied, setCopied] = useState(false);
  const [typedKey, setTypedKey] = useState("");
  const [confirmDeleteServer, setConfirmDeleteServer] = useState(false);

  const call = useCallback(
    async (path: string, init?: RequestInit) => {
      const res = await fetch(`${apiBase}/modules/${module}/privacy${path}`, {
        ...init,
        headers: { "Content-Type": "application/json", ...(authHeader || {}), ...(init?.headers || {}) },
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.error || `${res.status} ${res.statusText}`);
      return data;
    },
    [apiBase, module, authHeader],
  );

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const data = await call("");
      setStatus({ private: !!data.private, password_held: !!data.password_held, owner: data.owner });
    } catch (e) {
      setStatus(null);
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [call]);

  useEffect(() => {
    if (!open) return;
    setNote(null);
    setCopied(false);
    setTypedKey("");
    setConfirmDeleteServer(false);
    const local = readLocalKey(module);
    setKey(local);
    setKeepLocal(!!local);
    refresh();
  }, [open, module, refresh]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, busy, onClose]);

  if (!open) return null;

  /** The key to send with an operation: in hand first, else what was typed. */
  const activeKey = () => key || typedKey.trim() || undefined;

  const run = async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    setError(null);
    setNote(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const goPrivate = () =>
    run("enable", async () => {
      const data = await call("", { method: "POST" });
      if (data.password) {
        setKey(data.password);
        if (keepLocal) writeLocalKey(module, data.password);
      }
      setNote("Private. Only you see it on the hub from now on, and snapshots publish encrypted.");
      await refresh();
      onChanged?.(true);
    });

  const goPublic = () =>
    run("disable", async () => {
      await call("", {
        method: "DELETE",
        body: JSON.stringify({ password: activeKey() ?? null }),
      });
      setNote("Public again — back on everyone's hub. The key is kept, so old encrypted snapshots still restore.");
      await refresh();
      onChanged?.(false);
    });

  const revealKey = () =>
    run("reveal", async () => {
      const data = await call("/password");
      setKey(data.password || null);
      if (keepLocal && data.password) writeLocalKey(module, data.password);
    });

  const deleteServerKey = () =>
    run("delete-server", async () => {
      await call("/password", { method: "DELETE" });
      setConfirmDeleteServer(false);
      setNote(
        key
          ? "Server copy deleted. The key you're holding is the only one left — keep it somewhere safe."
          : "Server copy deleted. Nothing here holds the key now; paste it when an operation needs it.",
      );
      await refresh();
    });

  const copyKey = async () => {
    if (!key) return;
    try {
      await navigator.clipboard.writeText(key);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      setError("clipboard refused — select the key and copy it by hand");
    }
  };

  const toggleKeepLocal = (on: boolean) => {
    setKeepLocal(on);
    writeLocalKey(module, on ? key : null);
    setNote(on ? "Key kept in this browser." : "Key removed from this browser.");
  };

  const forgetLocal = () => {
    writeLocalKey(module, null);
    setKey(null);
    setKeepLocal(false);
    setNote("Key forgotten on this device. It's gone from here — nowhere else.");
  };

  const isPrivate = !!status?.private;
  const localHeld = keepLocal && !!key;

  const btn = (
    label: string,
    onClick: () => void,
    opts?: { color?: string; disabled?: boolean; title?: string; solid?: boolean },
  ) => {
    const color = opts?.color || ACCENT;
    const disabled = opts?.disabled || !!busy;
    return (
      <button
        onClick={onClick}
        disabled={disabled}
        title={opts?.title}
        className="focus-ring text-[10px] font-bold uppercase tracking-[0.14em] px-3 py-2 rounded-[10px] transition-all"
        style={{
          color: opts?.solid ? "#fff" : disabled ? "var(--text-tertiary)" : color,
          border: `1px solid color-mix(in srgb, ${color} ${disabled ? 18 : 40}%, var(--border-color))`,
          background: opts?.solid ? color : `color-mix(in srgb, ${color} ${disabled ? 4 : 10}%, transparent)`,
          cursor: busy ? "wait" : disabled ? "not-allowed" : "pointer",
        }}
      >
        {label}
      </button>
    );
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 210,
        display: "grid",
        placeItems: "center",
        padding: 24,
        background: "radial-gradient(120% 120% at 50% 0%, rgba(192,132,252,0.10), rgba(0,0,0,0.66) 60%)",
        backdropFilter: "blur(14px) saturate(120%)",
        WebkitBackdropFilter: "blur(14px) saturate(120%)",
      }}
    >
      <div
        style={{
          width: "min(470px, 100%)",
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
              {isPrivate ? "🔒" : "🌐"}
            </span>
            <span className="text-[12px] font-bold uppercase tracking-[0.12em]" style={{ color: "var(--text-primary)" }}>
              {module} · {isPrivate ? "private" : "public"}
            </span>
            <span className="flex-1" />
            <button
              onClick={onClose}
              disabled={!!busy}
              className="focus-ring text-[13px] leading-none px-2 py-1 rounded-md"
              style={{ color: "var(--text-tertiary)", background: "transparent", border: "1px solid transparent" }}
              title="Close"
            >
              ✕
            </button>
          </div>

          <p className="text-[10.5px] leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
            {isPrivate ? (
              <>
                Hidden from everyone but you — no hub card, no files, no tasks in the public
                ledger. Snapshots publish as one encrypted blob, and the on-chain registry
                entry stops updating. Its app, if it&apos;s running, is still served publicly
                by the gateway: this hides the <b style={{ color: "var(--text-secondary)" }}>code</b>, not a live website.
              </>
            ) : (
              <>
                On the hub for anyone, signed in or not — the default for every module.
                Going private hides it from every other caller and encrypts every snapshot
                from then on under a key that is yours to keep or destroy.
              </>
            )}
          </p>

          {status?.owner && (
            <div className="text-[9.5px] font-mono" style={{ color: "var(--text-tertiary)", opacity: 0.65 }}>
              owner {status.owner}
            </div>
          )}

          {/* Key desk — only meaningful once the module is private. */}
          {isPrivate && (
            <div
              className="flex flex-col gap-2.5 p-3 rounded-[12px]"
              style={{
                border: "1px solid var(--border-color)",
                background: "color-mix(in srgb, var(--bg-primary) 60%, transparent)",
              }}
            >
              <div className="flex items-center gap-2">
                <span className="text-[9px] font-bold uppercase tracking-[0.14em]" style={{ color: "var(--text-secondary)" }}>
                  Key
                </span>
                <span
                  className="text-[9px] font-mono px-1.5 py-0.5 rounded"
                  style={{
                    color: status?.password_held ? "var(--crt-amber)" : "var(--crt-green)",
                    border: `1px solid color-mix(in srgb, ${status?.password_held ? "var(--crt-amber)" : "var(--crt-green)"} 30%, transparent)`,
                  }}
                  title={
                    status?.password_held
                      ? "The server still holds a copy — it can snapshot unattended, and it could be read by anyone with this machine"
                      : "The server holds only a verifier; it cannot produce this key again"
                  }
                >
                  {status?.password_held ? "server copy: held" : "server copy: deleted"}
                </span>
                {localHeld && (
                  <span
                    className="text-[9px] font-mono px-1.5 py-0.5 rounded"
                    style={{ color: ACCENT, border: `1px solid color-mix(in srgb, ${ACCENT} 30%, transparent)` }}
                    title="A copy is in this browser's localStorage"
                  >
                    this device
                  </span>
                )}
              </div>

              {key ? (
                <div className="flex items-center gap-2">
                  <code
                    className="flex-1 min-w-0 truncate text-[11px] font-mono px-2 py-1.5 rounded-md"
                    style={{ color: "var(--text-primary)", background: "var(--bg-primary)", border: "1px solid var(--border-color)" }}
                    title={key}
                  >
                    {key}
                  </code>
                  {btn(copied ? "copied" : "copy", copyKey, { color: "var(--crt-blue)" })}
                </div>
              ) : status?.password_held ? (
                btn("Reveal the key", revealKey, { title: "Read the server-held copy" })
              ) : (
                <input
                  type="text"
                  value={typedKey}
                  onChange={(e) => setTypedKey(e.target.value)}
                  placeholder="paste your key to act on this module…"
                  className="focus-ring font-mono text-[11px] px-3 py-2 rounded-[10px] w-full"
                  style={{ color: "var(--text-primary)", background: "var(--bg-primary)", border: "1px solid var(--border-color)" }}
                  spellCheck={false}
                />
              )}

              <label className="flex items-center gap-2 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={keepLocal}
                  onChange={(e) => toggleKeepLocal(e.target.checked)}
                  disabled={!key || !!busy}
                  style={{ accentColor: "var(--crt-purple, #c084fc)" }}
                />
                <span className="text-[10px]" style={{ color: "var(--text-secondary)" }}>
                  Keep a copy in this browser
                </span>
              </label>

              <div className="flex items-center gap-2 flex-wrap">
                {status?.password_held ? (
                  confirmDeleteServer ? (
                    <>
                      <span className="text-[10px] font-bold uppercase tracking-wider" style={{ color: "var(--crt-red)" }}>
                        Delete the server&apos;s copy?
                      </span>
                      {btn("Confirm", deleteServerKey, { color: "var(--crt-red)", solid: true })}
                      {btn("Cancel", () => setConfirmDeleteServer(false), { color: "var(--border-color)" })}
                    </>
                  ) : (
                    btn("Delete server copy", () => setConfirmDeleteServer(true), {
                      color: "var(--crt-red)",
                      title:
                        "Wipe the key from ~/.mod/build-fork/private — irreversible. Copy it first: after this you must supply it for anything that touches the encrypted history.",
                    })
                  )
                ) : (
                  <span className="text-[9.5px]" style={{ color: "var(--text-tertiary)" }}>
                    The server can&apos;t recover this key — only verify one you supply.
                  </span>
                )}
                {localHeld && btn("Forget on this device", forgetLocal, { color: "var(--crt-amber)" })}
              </div>
              {status?.password_held && (
                <p className="text-[9.5px] leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
                  While the server holds it, background edit snapshots keep encrypting on
                  their own. Delete it and that pauses until you supply the key per request.
                </p>
              )}
            </div>
          )}

          {note && (
            <div
              className="text-[10.5px] leading-relaxed px-3 py-2 rounded-[10px]"
              style={{
                color: "var(--crt-green)",
                border: "1px solid color-mix(in srgb, var(--crt-green) 30%, var(--border-color))",
                background: "color-mix(in srgb, var(--crt-green) 6%, transparent)",
              }}
            >
              {note}
            </div>
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
            {isPrivate
              ? btn(busy === "disable" ? "…" : "Make public", goPublic, {
                  color: "var(--crt-green)",
                  title: "Back on the public hub; plaintext snapshots resume",
                })
              : btn(busy === "enable" ? "…" : "Make private", goPrivate, {
                  title: "Hide from everyone else and encrypt every snapshot from now on",
                })}
            <span className="flex-1" />
            {btn("Done", onClose, { color: "var(--border-color)" })}
          </div>
        </div>
      </div>
    </div>
  );
}
