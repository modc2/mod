"use client";

import { useEffect, useState, useCallback } from "react";

const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "/claude";
const CRED_URL = `${BASE_PATH}/api/credentials`;

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

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(CRED_URL, { cache: "no-store" });
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
        headers: { "Content-Type": "application/json" },
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
          The job runner spawns the <code>claude</code> CLI with these credentials. Paste a working{" "}
          <code>~/.claude/.credentials.json</code> here if jobs report “Not logged in”.
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

        {/* paste box */}
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

        {msg && (
          <div
            style={{
              marginTop: 4,
              marginBottom: 12,
              fontSize: 13,
              color: msg.kind === "ok" ? "#3ecf6b" : "#e0564b",
            }}
          >
            {msg.text}
          </div>
        )}

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
      </div>
    </div>
  );
}
