"use client";

import { useEffect, useState, useCallback } from "react";

const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "/build-fork";
const CRED_URL = `${BASE_PATH}/api/credentials`;
const OAUTH_URL = `${BASE_PATH}/api/credentials/oauth`;

// Both routes are owner-only server-side — they read and write the Claude
// credentials every job on this host runs on. Carry the console's bearer token.
function authHeaders(json = false): Record<string, string> {
  const tok = typeof window === "undefined" ? null : localStorage.getItem("buildfork_jobs_token");
  return {
    ...(json ? { "Content-Type": "application/json" } : {}),
    ...(tok && tok !== "local" ? { Authorization: `Bearer ${tok}` } : {}),
  };
}

interface Status {
  loggedIn: boolean;
  expired?: boolean;
  expiresAt?: number;
  remainingMinutes?: number | null;
  subscriptionType?: string | null;
  tokenPreview?: string;
  source?: string;
  reason?: string;
}

function fmtRemaining(min: number | null | undefined): string {
  if (min == null) return "unknown";
  if (min <= 0) return "expired";
  const h = Math.floor(min / 60);
  const m = min % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

export default function AuthPage() {
  const [status, setStatus] = useState<Status | null>(null);
  const [loading, setLoading] = useState(true);
  const [paste, setPaste] = useState("");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  // OAuth ("Log in with Claude") flow state. `pkce` holds the verifier+state we
  // must echo back to complete the exchange; non-null means a login is pending.
  const [pkce, setPkce] = useState<{ verifier: string; state: string } | null>(null);
  const [code, setCode] = useState("");
  const [oauthBusy, setOauthBusy] = useState(false);
  const [showPaste, setShowPaste] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(CRED_URL, { cache: "no-store", headers: authHeaders() });
      setStatus(await r.json());
    } catch (e: any) {
      setStatus({ loggedIn: false, reason: e?.message || "request failed" });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function save() {
    setSaving(true);
    setMsg(null);
    try {
      const r = await fetch(CRED_URL, {
        method: "POST",
        headers: authHeaders(true),
        body: JSON.stringify({ json: paste }),
      });
      const data = await r.json();
      if (!r.ok || !data.ok) {
        setMsg({ kind: "err", text: data.error || "save failed" });
      } else {
        setMsg({ kind: "ok", text: "Credentials saved — new jobs will use them." });
        setPaste("");
        setStatus(data.status);
      }
    } catch (e: any) {
      setMsg({ kind: "err", text: e?.message || "request failed" });
    } finally {
      setSaving(false);
    }
  }

  async function startLogin() {
    setOauthBusy(true);
    setMsg(null);
    try {
      const r = await fetch(OAUTH_URL, { cache: "no-store", headers: authHeaders() });
      const data = await r.json();
      if (!r.ok || !data.url) {
        setMsg({ kind: "err", text: data.error || "could not start login" });
        return;
      }
      setPkce({ verifier: data.verifier, state: data.state });
      setCode("");
      window.open(data.url, "_blank", "noopener,noreferrer");
    } catch (e: any) {
      setMsg({ kind: "err", text: e?.message || "request failed" });
    } finally {
      setOauthBusy(false);
    }
  }

  async function completeLogin() {
    if (!pkce || !code.trim()) return;
    setOauthBusy(true);
    setMsg(null);
    try {
      const r = await fetch(OAUTH_URL, {
        method: "POST",
        headers: authHeaders(true),
        body: JSON.stringify({ code: code.trim(), verifier: pkce.verifier, state: pkce.state }),
      });
      const data = await r.json();
      if (!r.ok || !data.ok) {
        setMsg({ kind: "err", text: data.error || "login failed" });
      } else {
        setMsg({ kind: "ok", text: "Logged in — new jobs will use these credentials." });
        setPkce(null);
        setCode("");
        await refresh();
      }
    } catch (e: any) {
      setMsg({ kind: "err", text: e?.message || "request failed" });
    } finally {
      setOauthBusy(false);
    }
  }

  const ok = status?.loggedIn;

  return (
    <div
      style={{
        minHeight: "100vh",
        background: "#07070d",
        color: "#e7e7ea",
        fontFamily: "ui-sans-serif, system-ui, sans-serif",
        display: "flex",
        justifyContent: "center",
        padding: "48px 20px",
      }}
    >
      <div style={{ width: "100%", maxWidth: 640 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
          <div
            style={{
              width: 34,
              height: 34,
              borderRadius: 8,
              background: "#cc785c",
              color: "#07070d",
              display: "grid",
              placeItems: "center",
              fontWeight: 700,
              fontSize: 18,
            }}
          >
            C
          </div>
          <h1 style={{ fontSize: 22, fontWeight: 600, margin: 0 }}>Claude credentials</h1>
          <a
            href={BASE_PATH}
            style={{ marginLeft: "auto", fontSize: 13, color: "#8a8a93", textDecoration: "none" }}
          >
            ← back to app
          </a>
        </div>
        <p style={{ color: "#8a8a93", fontSize: 14, marginTop: 0, marginBottom: 24 }}>
          The job runner spawns the <code>claude</code> CLI with these credentials. Log in with your
          Claude account below — no need to copy a <code>~/.claude/.credentials.json</code> from
          another machine.
        </p>

        {/* status card */}
        <div
          style={{
            border: `1px solid ${ok ? "#2e5d3f" : "#5d2e2e"}`,
            background: ok ? "#0e1a12" : "#1a0e0e",
            borderRadius: 12,
            padding: 16,
            marginBottom: 24,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span
              style={{
                width: 9,
                height: 9,
                borderRadius: "50%",
                background: ok ? "#3ecf6b" : "#e0564b",
                boxShadow: ok ? "0 0 8px #3ecf6b" : "0 0 8px #e0564b",
              }}
            />
            <strong style={{ fontSize: 15 }}>
              {loading ? "Checking…" : ok ? "Authenticated" : "Not logged in"}
            </strong>
            <button
              onClick={refresh}
              style={{
                marginLeft: "auto",
                background: "transparent",
                border: "1px solid #33333d",
                color: "#b5b5bd",
                borderRadius: 6,
                padding: "3px 10px",
                fontSize: 12,
                cursor: "pointer",
              }}
            >
              Refresh
            </button>
          </div>
          {!loading && (
            <div style={{ marginTop: 10, fontSize: 13, color: "#a9a9b2", lineHeight: 1.7 }}>
              {ok ? (
                <>
                  <div>Expires in: <strong style={{ color: "#e7e7ea" }}>{fmtRemaining(status?.remainingMinutes)}</strong></div>
                  {status?.subscriptionType && <div>Plan: {status.subscriptionType}</div>}
                  {status?.tokenPreview && <div>Token: <code>{status.tokenPreview}</code></div>}
                  {status?.source && <div style={{ color: "#6f6f78" }}>Source: {status.source}</div>}
                </>
              ) : (
                <div>{status?.reason || (status?.expired ? "token expired" : "no valid credentials")}</div>
              )}
            </div>
          )}
        </div>

        {/* OAuth — "Log in with Claude" (primary path) */}
        <div
          style={{
            border: "1px solid #26262e",
            background: "#0d0d14",
            borderRadius: 12,
            padding: 16,
            marginBottom: 16,
          }}
        >
          {!pkce ? (
            <>
              <button
                onClick={startLogin}
                disabled={oauthBusy}
                style={{
                  background: "#cc785c",
                  color: "#07070d",
                  border: "none",
                  borderRadius: 8,
                  padding: "11px 20px",
                  fontSize: 14,
                  fontWeight: 600,
                  cursor: oauthBusy ? "default" : "pointer",
                }}
              >
                {oauthBusy ? "Opening…" : "Log in with Claude"}
              </button>
              <p style={{ color: "#6f6f78", fontSize: 12.5, marginTop: 10, marginBottom: 0 }}>
                Opens claude.ai to authorize. Use the same account whose subscription should run the
                jobs.
              </p>
            </>
          ) : (
            <>
              <div style={{ fontSize: 13, color: "#b5b5bd", marginBottom: 8 }}>
                A claude.ai tab opened. After approving, copy the code it shows and paste it here:
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <input
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && completeLogin()}
                  placeholder="paste authorization code"
                  spellCheck={false}
                  autoFocus
                  style={{
                    flex: 1,
                    background: "#07070d",
                    border: "1px solid #33333d",
                    borderRadius: 8,
                    color: "#e7e7ea",
                    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                    fontSize: 12.5,
                    padding: "9px 12px",
                    boxSizing: "border-box",
                  }}
                />
                <button
                  onClick={completeLogin}
                  disabled={oauthBusy || !code.trim()}
                  style={{
                    background: code.trim() ? "#cc785c" : "#3a2c26",
                    color: code.trim() ? "#07070d" : "#6f6f78",
                    border: "none",
                    borderRadius: 8,
                    padding: "9px 18px",
                    fontSize: 14,
                    fontWeight: 600,
                    cursor: oauthBusy || !code.trim() ? "default" : "pointer",
                    whiteSpace: "nowrap",
                  }}
                >
                  {oauthBusy ? "Finishing…" : "Complete login"}
                </button>
              </div>
              <button
                onClick={() => {
                  setPkce(null);
                  setCode("");
                }}
                style={{
                  marginTop: 10,
                  background: "transparent",
                  border: "none",
                  color: "#6f6f78",
                  fontSize: 12,
                  cursor: "pointer",
                  padding: 0,
                }}
              >
                cancel
              </button>
            </>
          )}
        </div>

        {msg && (
          <div
            style={{
              marginBottom: 16,
              fontSize: 13,
              color: msg.kind === "ok" ? "#3ecf6b" : "#e0564b",
            }}
          >
            {msg.text}
          </div>
        )}

        {/* paste fallback — collapsed by default */}
        <button
          onClick={() => setShowPaste((s) => !s)}
          style={{
            background: "transparent",
            border: "none",
            color: "#6f6f78",
            fontSize: 12.5,
            cursor: "pointer",
            padding: 0,
            marginBottom: showPaste ? 12 : 0,
          }}
        >
          {showPaste ? "▾" : "▸"} Paste credentials manually instead
        </button>

        {showPaste && (
        <>
        <label style={{ fontSize: 13, color: "#b5b5bd", display: "block", marginBottom: 8 }}>
          Paste credentials JSON
        </label>
        <textarea
          value={paste}
          onChange={(e) => setPaste(e.target.value)}
          placeholder='{"claudeAiOauth":{"accessToken":"…","refreshToken":"…","expiresAt":1781218923918}}'
          spellCheck={false}
          style={{
            width: "100%",
            minHeight: 160,
            background: "#0d0d14",
            border: "1px solid #26262e",
            borderRadius: 10,
            color: "#e7e7ea",
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
            fontSize: 12.5,
            padding: 12,
            resize: "vertical",
            boxSizing: "border-box",
          }}
        />
        <p style={{ color: "#6f6f78", fontSize: 12, marginTop: 8 }}>
          On a machine where <code>claude</code> is logged in, run{" "}
          <code style={{ color: "#cc785c" }}>cat ~/.claude/.credentials.json</code> and paste the output above.
        </p>

        <button
          onClick={save}
          disabled={saving || !paste.trim()}
          style={{
            marginTop: 8,
            background: paste.trim() ? "#cc785c" : "#3a2c26",
            color: paste.trim() ? "#07070d" : "#6f6f78",
            border: "none",
            borderRadius: 8,
            padding: "10px 20px",
            fontSize: 14,
            fontWeight: 600,
            cursor: saving || !paste.trim() ? "default" : "pointer",
          }}
        >
          {saving ? "Saving…" : "Save credentials"}
        </button>
        </>
        )}
      </div>
    </div>
  );
}
