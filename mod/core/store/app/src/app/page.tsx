"use client";

import { useCallback, useEffect, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import {
  buildModToken,
  connectMetaMask,
  hasMetaMask,
  shortAddress,
} from "@/lib/wallet";
import { api, MeResponse, Quota, StoredObject } from "@/lib/api";

const TOKEN_KEY = "store:token";
const ADDR_KEY = "store:addr";

function fmtBytes(n: number | null): string {
  if (n === null) return "∞";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(1)} ${units[i]}`;
}

export default function Page() {
  const [hasWallet, setHasWallet] = useState(false);
  const [address, setAddress] = useState<string | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [me, setMe] = useState<MeResponse | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [objects, setObjects] = useState<StoredObject[]>([]);
  const [backend, setBackend] = useState<"localfs" | "filecoin" | "hippius" | "both">("localfs");
  const [file, setFile] = useState<File | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [serviceStatus, setServiceStatus] = useState<Record<string, unknown> | null>(null);

  const quota: Quota | null = me?.quota ?? null;

  useEffect(() => {
    setHasWallet(hasMetaMask());
    const t = typeof window !== "undefined" ? localStorage.getItem(TOKEN_KEY) : null;
    const a = typeof window !== "undefined" ? localStorage.getItem(ADDR_KEY) : null;
    if (t && a) {
      api
        .me(t)
        .then((r) => {
          setToken(t);
          setAddress(r.address);
          setMe(r);
        })
        .catch(() => {
          localStorage.removeItem(TOKEN_KEY);
          localStorage.removeItem(ADDR_KEY);
        });
    }
    api.status().then(setServiceStatus).catch(() => {});
  }, []);

  const refreshList = useCallback(async (t: string) => {
    try {
      const r = await api.list(t);
      setObjects(r.objects);
    } catch (e) {
      setError(`list failed: ${(e as Error).message}`);
    }
  }, []);

  const refreshMe = useCallback(async (t: string) => {
    try {
      setMe(await api.me(t));
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    if (token) refreshList(token);
  }, [token, refreshList]);

  // Sign in with the mod protocol auth system: connect wallet, sign a
  // time-bounded token, then validate (and learn authorization/quota) via /me.
  const signIn = async () => {
    setError(null);
    setBusy("connecting wallet…");
    try {
      const { address: addr } = await connectMetaMask();
      setBusy("waiting for signature…");
      const t = await buildModToken(addr, { domain: window.location.host, scope: "store" });
      setBusy("verifying…");
      const r = await api.me(t);
      localStorage.setItem(TOKEN_KEY, t);
      localStorage.setItem(ADDR_KEY, r.address);
      setToken(t);
      setAddress(r.address);
      setMe(r);
      setSuccess(
        r.authorized
          ? `signed in${r.admin ? " as admin" : ""}`
          : "signed in — but this address is not whitelisted to store"
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const signOut = () => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(ADDR_KEY);
    setToken(null);
    setMe(null);
    setObjects([]);
  };

  const upload = async () => {
    if (!file || !token) return;
    setError(null);
    setSuccess(null);
    setBusy(`uploading to ${backend}…`);
    try {
      const r = await api.put(token, file, backend);
      const cids = Object.entries(r.results)
        .map(([b, v]) => (v.cid ? `${b}: ${v.cid}` : `${b}: error — ${v.error}`))
        .join("  •  ");
      setSuccess(`uploaded: ${cids}`);
      setFile(null);
      await refreshList(token);
      await refreshMe(token);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const pin = async (cid: string, b: string) => {
    if (!token) return;
    setBusy(`pinning ${cid.slice(0, 12)}…`);
    try {
      await api.pin(token, cid, b);
      setSuccess(`pinned ${cid.slice(0, 12)}…`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const copyCid = async (cid: string) => {
    try {
      await navigator.clipboard.writeText(cid);
      setCopied(cid);
      setTimeout(() => setCopied((c) => (c === cid ? null : c)), 1500);
    } catch {
      /* ignore */
    }
  };

  const usedPct =
    quota && quota.limit_bytes
      ? Math.min(100, (quota.used_bytes / quota.limit_bytes) * 100)
      : 0;
  const canStore = !!me?.authorized;

  return (
    <div className="wrap">
      <header className="header">
        <div>
          <span className="brand">store</span>
          <span className="brand-sub">localfs + filecoin + hippius • mod protocol auth</span>
        </div>
        <div>
          {!hasWallet && <span className="muted">MetaMask not detected</span>}
          {hasWallet && !token && (
            <button className="primary" onClick={signIn} disabled={!!busy}>
              Sign in with wallet
            </button>
          )}
          {token && address && (
            <div className="row">
              <span className="pill">mod-auth</span>
              {me?.admin && <span className="pill admin">admin</span>}
              <span>{shortAddress(address)}</span>
              <button onClick={signOut}>Sign out</button>
            </div>
          )}
        </div>
      </header>

      {busy && <div className="success-box">⟳ {busy}</div>}
      {error && <div className="error-box">✕ {error}</div>}
      {success && !busy && <div className="success-box">✓ {success}</div>}

      {token && !canStore && (
        <div className="error-box">
          This address is signed in but not on the store whitelist — uploads are blocked.
          Ask the owner to whitelist <code>{address}</code>.
        </div>
      )}

      {token && quota && (
        <div className="panel">
          <h2 className="panel-title">Storage allowance</h2>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span className="muted">
              {fmtBytes(quota.used_bytes)} used
              {quota.unlimited ? " • unlimited (admin)" : ` of ${fmtBytes(quota.limit_bytes)}`}
            </span>
            {!quota.unlimited && (
              <span className="muted">{fmtBytes(quota.remaining_bytes)} left</span>
            )}
          </div>
          {!quota.unlimited && (
            <div className="quota-bar">
              <div className="quota-fill" style={{ width: `${usedPct}%` }} />
            </div>
          )}
        </div>
      )}

      <div className="panel">
        <h2 className="panel-title">Upload</h2>
        {!token && <p className="muted">Sign in to upload.</p>}
        {token && !canStore && <p className="muted">Whitelist required to upload.</p>}
        {token && canStore && (
          <div className="row">
            <input
              type="file"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              disabled={!!busy}
            />
            <select
              value={backend}
              onChange={(e) => setBackend(e.target.value as never)}
              disabled={!!busy}
            >
              <option value="localfs">localfs</option>
              <option value="filecoin">filecoin</option>
              <option value="hippius">hippius</option>
              <option value="both">both</option>
            </select>
            <button className="primary" onClick={upload} disabled={!file || !!busy}>
              Upload
            </button>
          </div>
        )}
      </div>

      <div className="panel">
        <h2 className="panel-title">Your objects</h2>
        {!token && <p className="muted">Sign in to see your objects.</p>}
        {token && objects.length === 0 && <p className="muted">No objects yet.</p>}
        {token && objects.length > 0 && (
          <ul className="objects">
            {objects.map((o) => (
              <li key={`${o.cid}-${o.backend}-${o.timestamp}`} className="object-card">
                <div className="qr">
                  <QRCodeSVG value={api.absoluteUrl(o.cid, o.backend)} size={96} level="M" />
                </div>
                <div className="object-meta">
                  <div className="row">
                    <span className={`pill ${o.backend}`}>{o.backend}</span>
                    {o.key && <span className="muted">{o.key}</span>}
                    {o.size != null && <span className="muted">{fmtBytes(o.size)}</span>}
                  </div>
                  <button
                    className="cid-btn"
                    title="Copy CID"
                    onClick={() => copyCid(o.cid)}
                  >
                    <span className="cid">{o.cid}</span>
                    <span className="muted"> {copied === o.cid ? "✓ copied" : "⧉"}</span>
                  </button>
                  <div className="row">
                    <a href={api.getUrl(o.cid, o.backend)} target="_blank" rel="noreferrer">
                      download
                    </a>
                    <button onClick={() => pin(o.cid, o.backend)} disabled={!!busy}>
                      pin
                    </button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="panel">
        <h2 className="panel-title">Service status</h2>
        <pre style={{ margin: 0, fontSize: 11, color: "#888", overflow: "auto" }}>
          {serviceStatus ? JSON.stringify(serviceStatus, null, 2) : "loading…"}
        </pre>
      </div>
    </div>
  );
}
