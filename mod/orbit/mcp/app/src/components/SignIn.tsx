"use client";

import { useState } from "react";
import { exportLocalKey, forgetLocalKey, hasLocalKey, importLocalKey, loadLocalKey } from "@/lib/localKey";
import { Session, signInWithLocalKey, signInWithWallet } from "@/lib/session";
import { hasWallet, shortAddress } from "@/lib/wallet";

/**
 * The sign-in sheet: two doors to the same protocol token.
 *
 * Publishing is the only thing here that needs an identity, so the copy says
 * what the signature is for and what each key costs you if you lose it.
 */
export default function SignIn({
  onDone,
  onClose,
}: {
  onDone: (s: Session) => void;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState<"" | "wallet" | "local">("");
  const [error, setError] = useState("");
  const [warn, setWarn] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [importing, setImporting] = useState("");

  async function go(mode: "wallet" | "local") {
    setBusy(mode);
    setError("");
    setWarn("");
    try {
      if (mode === "wallet") {
        onDone(await signInWithWallet());
      } else {
        const s = await signInWithLocalKey();
        if (!s.persisted)
          setWarn("this browser refused to store the key — it will vanish when the tab closes");
        onDone(s);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  }

  const pk = showKey ? exportLocalKey() : null;

  return (
    <div className="sheet" onClick={onClose}>
      <div className="sheet-inner" onClick={(e) => e.stopPropagation()}>
        <h2>Sign in to publish</h2>
        <p className="muted small" style={{ marginTop: 0 }}>
          One signature — no transaction, no gas. It mints a time-bounded mod protocol token; the
          hub and the store mod both verify it, so a manifest you publish is pinned under{" "}
          <em>your</em> address.
        </p>

        {error && (
          <div className="note bad" style={{ marginTop: 12 }}>
            {error}
          </div>
        )}
        {warn && (
          <div className="note warn" style={{ marginTop: 12 }}>
            {warn}
          </div>
        )}

        <div className="col" style={{ marginTop: 18 }}>
          <div className="note">
            <strong>Browser wallet</strong>
            <div className="small" style={{ marginTop: 4 }}>
              MetaMask or any injected EIP-1193 wallet signs the token. The key never leaves the
              extension.
            </div>
            <div className="row" style={{ marginTop: 10 }}>
              <button className="primary" disabled={!hasWallet() || busy !== ""} onClick={() => go("wallet")}>
                {busy === "wallet" ? "waiting for signature…" : "Connect wallet"}
              </button>
              {!hasWallet() && <span className="muted small">no wallet detected in this browser</span>}
            </div>
          </div>

          <div className="note">
            <strong>Locally derived key</strong>
            <div className="small" style={{ marginTop: 4 }}>
              No extension needed — this page derives a keypair and keeps it in browser storage.
              Anything with access to this browser profile can read it, and clearing site data
              destroys it, so back it up before you publish anything you care about.
            </div>
            <div className="row" style={{ marginTop: 10 }}>
              <button disabled={busy !== ""} onClick={() => go("local")}>
                {busy === "local"
                  ? "signing…"
                  : hasLocalKey()
                  ? "Use this browser's key"
                  : "Create a key in this browser"}
              </button>
              {hasLocalKey() && (
                <>
                  <button className="ghost sm" onClick={() => setShowKey((v) => !v)}>
                    {showKey ? "hide backup" : "back up"}
                  </button>
                  <button
                    className="ghost sm danger"
                    onClick={() => {
                      if (confirm("Forget this key? Servers published under it become unclaimable."))
                        forgetLocalKey();
                      setShowKey(false);
                    }}
                  >
                    forget
                  </button>
                </>
              )}
            </div>
            {showKey && pk && (
              <div style={{ marginTop: 10 }}>
                <div className="muted small" style={{ marginBottom: 5 }}>
                  private key for {shortAddress(loadLocalKey()?.address ?? "")} — store it somewhere
                  safe
                </div>
                <pre className="code">{pk}</pre>
              </div>
            )}
            <div className="row" style={{ marginTop: 10 }}>
              <input
                placeholder="restore from a backed-up private key"
                value={importing}
                onChange={(e) => setImporting(e.target.value)}
                style={{ flex: "1 1 260px" }}
              />
              <button
                className="sm"
                disabled={!importing.trim()}
                onClick={() => {
                  try {
                    importLocalKey(importing.trim());
                    setImporting("");
                    void go("local");
                  } catch {
                    setError("that isn't a valid private key");
                  }
                }}
              >
                restore
              </button>
            </div>
          </div>
        </div>

        <div className="row" style={{ marginTop: 18, justifyContent: "flex-end" }}>
          <button className="ghost" onClick={onClose}>
            close
          </button>
        </div>
      </div>
    </div>
  );
}
