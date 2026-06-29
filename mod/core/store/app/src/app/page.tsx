"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import {
  buildModToken,
  connectMetaMask,
  hasMetaMask,
  shortAddress,
} from "@/lib/wallet";
import {
  api,
  Grant,
  MeResponse,
  PoolDetail,
  PoolSummary,
  Quota,
  StoredObject,
} from "@/lib/api";

const TOKEN_KEY = "store:token";
const ADDR_KEY = "store:addr";
const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "";

type View = "files" | "shared" | "pools";

const DURATIONS: { label: string; ttl: number | null }[] = [
  { label: "15 minutes", ttl: 15 * 60 },
  { label: "1 hour", ttl: 60 * 60 },
  { label: "1 day", ttl: 24 * 60 * 60 },
  { label: "7 days", ttl: 7 * 24 * 60 * 60 },
  { label: "30 days", ttl: 30 * 24 * 60 * 60 },
  { label: "Never (until revoked)", ttl: null },
];

function fmtBytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return "∞";
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

function fmtDuration(secs: number | null): string {
  if (secs === null) return "no expiry";
  if (secs <= 0) return "expired";
  const d = Math.floor(secs / 86400);
  const h = Math.floor((secs % 86400) / 3600);
  const m = Math.floor((secs % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m`;
  return `${secs}s`;
}

export default function Page() {
  const [hasWallet, setHasWallet] = useState(false);
  const [address, setAddress] = useState<string | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [me, setMe] = useState<MeResponse | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const [view, setView] = useState<View>("files");
  const [objects, setObjects] = useState<StoredObject[]>([]);
  const [sharedObjects, setSharedObjects] = useState<StoredObject[]>([]);
  const [pools, setPools] = useState<PoolSummary[]>([]);

  // upload form
  const [uploadKind, setUploadKind] = useState<"file" | "text" | "image">("file");
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [textName, setTextName] = useState("");
  const [backend, setBackend] = useState<"localfs" | "filecoin" | "hippius" | "both">("localfs");
  const [makePublic, setMakePublic] = useState(false);
  const [uploadPool, setUploadPool] = useState("");

  const [copied, setCopied] = useState<string | null>(null);
  const [serviceStatus, setServiceStatus] = useState<Record<string, unknown> | null>(null);

  // modals
  const [shareFor, setShareFor] = useState<StoredObject | null>(null);
  const [linkPhone, setLinkPhone] = useState<{ code: string; expires: number } | null>(null);
  const [openPool, setOpenPool] = useState<PoolDetail | null>(null);

  const quota: Quota | null = me?.quota ?? null;
  const canStore = !!me?.authorized;

  const applySession = useCallback((t: string, r: MeResponse) => {
    localStorage.setItem(TOKEN_KEY, t);
    localStorage.setItem(ADDR_KEY, r.address);
    setToken(t);
    setAddress(r.address);
    setMe(r);
  }, []);

  // ── claim a QR handoff (?claim=code): pick up a token from another device ──
  useEffect(() => {
    setHasWallet(hasMetaMask());
    api.status().then(setServiceStatus).catch(() => {});

    const url = new URL(window.location.href);
    const code = url.searchParams.get("claim");
    if (code) {
      setBusy("linking this device…");
      api
        .claimHandoff(code)
        .then(async (r) => {
          const me2 = await api.me(r.token);
          applySession(r.token, me2);
          setSuccess(`linked as ${shortAddress(me2.address)} — no wallet needed`);
        })
        .catch((e) => setError(`link failed: ${(e as Error).message}`))
        .finally(() => {
          url.searchParams.delete("claim");
          window.history.replaceState({}, "", url.toString());
          setBusy(null);
        });
      return;
    }

    const t = localStorage.getItem(TOKEN_KEY);
    if (t) {
      api
        .me(t)
        .then((r) => applySession(t, r))
        .catch(() => {
          localStorage.removeItem(TOKEN_KEY);
          localStorage.removeItem(ADDR_KEY);
        });
    }
  }, [applySession]);

  const refreshFiles = useCallback(async (t: string) => {
    try {
      setObjects((await api.list(t)).objects);
    } catch (e) {
      setError(`list failed: ${(e as Error).message}`);
    }
  }, []);
  const refreshShared = useCallback(async (t: string) => {
    try {
      setSharedObjects((await api.shared(t)).objects);
    } catch {
      /* ignore */
    }
  }, []);
  const refreshPools = useCallback(async (t: string) => {
    try {
      setPools((await api.pools(t)).pools);
    } catch {
      /* ignore */
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
    if (!token) return;
    refreshFiles(token);
    refreshShared(token);
    refreshPools(token);
  }, [token, refreshFiles, refreshShared, refreshPools]);

  const signIn = async () => {
    setError(null);
    setBusy("connecting wallet…");
    try {
      const { address: addr } = await connectMetaMask();
      setBusy("waiting for signature…");
      const t = await buildModToken(addr, { domain: window.location.host, scope: "store" });
      setBusy("verifying…");
      const r = await api.me(t);
      applySession(t, r);
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
    setSharedObjects([]);
    setPools([]);
  };

  const upload = async () => {
    if (!token) return;
    let payload: File | null = file;
    if (uploadKind === "text") {
      if (!text.trim()) return;
      const name = (textName.trim() || `note-${Date.now()}`).replace(/[^\w.\-]/g, "_");
      payload = new File([text], name.endsWith(".txt") ? name : `${name}.txt`, {
        type: "text/plain",
      });
    }
    if (!payload) return;
    setError(null);
    setSuccess(null);
    setBusy(`storing to ${backend}…`);
    try {
      const r = await api.put(token, payload, backend, {
        public: makePublic,
        pool: uploadPool || undefined,
      });
      const cids = Object.entries(r.results)
        .map(([b, v]) => (v.cid ? `${b}: ${v.cid.slice(0, 16)}…` : `${b}: error — ${v.error}`))
        .join("  •  ");
      setSuccess(`stored (${makePublic ? "public" : "private"})${uploadPool ? " → pool" : ""}: ${cids}`);
      setFile(null);
      setText("");
      setTextName("");
      await refreshFiles(token);
      await refreshMe(token);
      if (uploadPool) await refreshPools(token);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const togglePublish = async (o: StoredObject) => {
    if (!token) return;
    const next = o.visibility !== "public";
    setBusy(`${next ? "publishing" : "making private"}…`);
    try {
      await api.publish(token, o.cid, next);
      await refreshFiles(token);
      setSuccess(`${o.cid.slice(0, 12)}… is now ${next ? "public" : "private"}`);
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

  const copyText = async (s: string, tag: string) => {
    try {
      await navigator.clipboard.writeText(s);
      setCopied(tag);
      setTimeout(() => setCopied((c) => (c === tag ? null : c)), 1500);
    } catch {
      /* ignore */
    }
  };

  const startLinkPhone = async () => {
    if (!token) return;
    setBusy("minting link code…");
    try {
      const h = await api.createHandoff(token, 180);
      setLinkPhone({ code: h.code, expires: h.expires });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const usedPct =
    quota && quota.limit_bytes ? Math.min(100, (quota.used_bytes / quota.limit_bytes) * 100) : 0;

  return (
    <div className="wrap">
      <header className="header">
        <div>
          <span className="brand">store</span>
          <span className="brand-sub">private-by-default · timed sharing · data pools · cid-agnostic</span>
        </div>
        <div className="row">
          {!hasWallet && !token && <span className="muted">MetaMask not detected</span>}
          {hasWallet && !token && (
            <button className="primary" onClick={signIn} disabled={!!busy}>
              Sign in with wallet
            </button>
          )}
          {token && address && (
            <>
              <span className="pill">mod-auth</span>
              {me?.admin && <span className="pill admin">admin</span>}
              <span className="muted">{shortAddress(address)}</span>
              <button onClick={startLinkPhone} disabled={!!busy} title="Move this session to your phone">
                📱 Link phone
              </button>
              <button onClick={signOut}>Sign out</button>
            </>
          )}
        </div>
      </header>

      {busy && <div className="success-box">⟳ {busy}</div>}
      {error && <div className="error-box">✕ {error}</div>}
      {success && !busy && <div className="success-box">✓ {success}</div>}

      {token && !canStore && (
        <div className="error-box">
          Signed in but not whitelisted to store. Ask the owner to whitelist <code>{address}</code>.
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
            {!quota.unlimited && <span className="muted">{fmtBytes(quota.remaining_bytes)} left</span>}
          </div>
          {!quota.unlimited && (
            <div className="quota-bar">
              <div className="quota-fill" style={{ width: `${usedPct}%` }} />
            </div>
          )}
        </div>
      )}

      {/* ── upload ── */}
      {token && canStore && (
        <div className="panel">
          <h2 className="panel-title">Add data</h2>
          <div className="tabs">
            {(["file", "text", "image"] as const).map((k) => (
              <button
                key={k}
                className={`tab ${uploadKind === k ? "active" : ""}`}
                onClick={() => setUploadKind(k)}
              >
                {k === "file" ? "📄 File" : k === "text" ? "✍️ Text" : "🖼️ Image"}
              </button>
            ))}
          </div>

          {uploadKind === "file" && (
            <input type="file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} disabled={!!busy} />
          )}
          {uploadKind === "image" && (
            <input
              type="file"
              accept="image/*"
              capture="environment"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              disabled={!!busy}
            />
          )}
          {uploadKind === "text" && (
            <div className="col">
              <input
                type="text"
                placeholder="name (optional, e.g. recipe.txt)"
                value={textName}
                onChange={(e) => setTextName(e.target.value)}
                disabled={!!busy}
              />
              <textarea
                placeholder="Paste or type any text — it's stored content-addressed just like a file."
                value={text}
                onChange={(e) => setText(e.target.value)}
                rows={6}
                disabled={!!busy}
              />
            </div>
          )}

          <div className="row" style={{ marginTop: 12 }}>
            <select value={backend} onChange={(e) => setBackend(e.target.value as never)} disabled={!!busy}>
              <option value="localfs">localfs</option>
              <option value="filecoin">filecoin</option>
              <option value="hippius">hippius</option>
              <option value="both">both</option>
            </select>
            <select value={uploadPool} onChange={(e) => setUploadPool(e.target.value)} disabled={!!busy}>
              <option value="">no pool</option>
              {pools
                .filter((p) => p.role === "owner" || p.role === "editor")
                .map((p) => (
                  <option key={p.id} value={p.id}>
                    → pool: {p.name}
                  </option>
                ))}
            </select>
            <label className="check">
              <input
                type="checkbox"
                checked={makePublic}
                onChange={(e) => setMakePublic(e.target.checked)}
                disabled={!!busy}
              />
              public
            </label>
            <button
              className="primary"
              onClick={upload}
              disabled={!!busy || (uploadKind === "text" ? !text.trim() : !file)}
            >
              Store
            </button>
          </div>
          <p className="muted hint">
            {makePublic
              ? "Public: anyone with the link/CID can read it."
              : "Private: only you — until you share it or add it to a pool."}
          </p>
        </div>
      )}

      {/* ── nav ── */}
      {token && (
        <div className="navbar">
          {(["files", "shared", "pools"] as const).map((v) => (
            <button key={v} className={`navtab ${view === v ? "active" : ""}`} onClick={() => setView(v)}>
              {v === "files" ? "Your objects" : v === "shared" ? "Shared with you" : `Pools (${pools.length})`}
            </button>
          ))}
        </div>
      )}

      {/* ── your objects ── */}
      {token && view === "files" && (
        <div className="panel">
          <h2 className="panel-title">Your objects</h2>
          {objects.length === 0 && <p className="muted">No objects yet — add some above.</p>}
          <ul className="objects">
            {objects.map((o) => {
              const priv = o.visibility === "private";
              const qr = api.absoluteUrl(o.cid, o.backend, priv ? token : null);
              return (
                <li key={`${o.cid}-${o.backend}-${o.timestamp}`} className="object-card">
                  <div className="qr">
                    <QRCodeSVG value={qr} size={96} level="M" />
                  </div>
                  <div className="object-meta">
                    <div className="row">
                      <span className={`pill ${o.backend}`}>{o.backend}</span>
                      {o.scheme && o.scheme !== "ipfs" && <span className="pill">{o.scheme}</span>}
                      <span className={`pill ${priv ? "private" : "public"}`}>{priv ? "🔒 private" : "🌐 public"}</span>
                      {o.key && <span className="muted">{o.key}</span>}
                      {o.size != null && <span className="muted">{fmtBytes(o.size)}</span>}
                    </div>
                    <button className="cid-btn" title="Copy CID" onClick={() => copyText(o.cid, o.cid)}>
                      <span className="cid">{o.cid}</span>
                      <span className="muted"> {copied === o.cid ? "✓ copied" : "⧉"}</span>
                    </button>
                    <div className="row">
                      <a href={api.getUrl(o.cid, o.backend, priv ? token : null)} target="_blank" rel="noreferrer">
                        download
                      </a>
                      <button onClick={() => setShareFor(o)} disabled={!!busy}>
                        share
                      </button>
                      <button onClick={() => togglePublish(o)} disabled={!!busy}>
                        {priv ? "make public" : "make private"}
                      </button>
                      <button onClick={() => pin(o.cid, o.backend)} disabled={!!busy}>
                        pin
                      </button>
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {/* ── shared with you ── */}
      {token && view === "shared" && (
        <div className="panel">
          <h2 className="panel-title">Shared with you</h2>
          <p className="muted hint">Objects others granted you (timed) or shared via a pool.</p>
          {sharedObjects.length === 0 && <p className="muted">Nothing shared with you yet.</p>}
          <ul className="objects">
            {sharedObjects.map((o) => (
              <li key={`${o.cid}-shared`} className="object-card">
                <div className="qr">
                  <QRCodeSVG value={api.absoluteUrl(o.cid, o.backend, token)} size={96} level="M" />
                </div>
                <div className="object-meta">
                  <div className="row">
                    <span className={`pill ${o.backend}`}>{o.backend}</span>
                    {o.shared_via && <span className="pill">via {o.shared_via}</span>}
                    {o.owner && <span className="muted">from {shortAddress(o.owner)}</span>}
                    {o.size != null && <span className="muted">{fmtBytes(o.size)}</span>}
                  </div>
                  <button className="cid-btn" onClick={() => copyText(o.cid, `s-${o.cid}`)}>
                    <span className="cid">{o.cid}</span>
                    <span className="muted"> {copied === `s-${o.cid}` ? "✓ copied" : "⧉"}</span>
                  </button>
                  <a href={api.getUrl(o.cid, o.backend, token)} target="_blank" rel="noreferrer">
                    download
                  </a>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* ── pools ── */}
      {token && view === "pools" && (
        <PoolsView
          token={token}
          pools={pools}
          onChanged={() => token && refreshPools(token)}
          onOpen={async (id) => setOpenPool(await api.pool(token, id))}
          setError={setError}
        />
      )}

      {token && (
        <RegisterExternal token={token} onDone={() => token && refreshFiles(token)} setError={setError} setSuccess={setSuccess} />
      )}

      <div className="panel">
        <h2 className="panel-title">Service status</h2>
        <pre className="status">{serviceStatus ? JSON.stringify(serviceStatus, null, 2) : "loading…"}</pre>
      </div>

      {/* ── modals ── */}
      {shareFor && token && (
        <ShareModal
          token={token}
          object={shareFor}
          onClose={() => setShareFor(null)}
          setError={setError}
          setSuccess={setSuccess}
        />
      )}
      {linkPhone && (
        <LinkPhoneModal data={linkPhone} onClose={() => setLinkPhone(null)} />
      )}
      {openPool && token && (
        <PoolModal
          token={token}
          pool={openPool}
          me={address}
          onClose={() => setOpenPool(null)}
          onChanged={async () => {
            setOpenPool(await api.pool(token, openPool.id));
            refreshPools(token);
            refreshFiles(token);
          }}
          setError={setError}
        />
      )}
    </div>
  );
}

/* ──────────────────────────── share modal ──────────────────────────── */

function ShareModal({
  token,
  object,
  onClose,
  setError,
  setSuccess,
}: {
  token: string;
  object: StoredObject;
  onClose: () => void;
  setError: (s: string) => void;
  setSuccess: (s: string) => void;
}) {
  const [grantee, setGrantee] = useState("");
  const [scope, setScope] = useState<"read" | "write">("read");
  const [ttlIdx, setTtlIdx] = useState(2);
  const [grants, setGrants] = useState<Grant[]>([]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const g = await api.grants(token);
      setGrants(g.granted.filter((x) => x.cid === object.cid || x.cid === "*"));
    } catch {
      /* ignore */
    }
  }, [token, object.cid]);
  useEffect(() => {
    load();
  }, [load]);

  const create = async () => {
    const addr = grantee.trim().toLowerCase();
    if (!addr.startsWith("0x") || addr.length !== 42) {
      setError("enter a valid 0x address");
      return;
    }
    setBusy(true);
    try {
      await api.createGrant(token, {
        grantee: addr,
        cid: object.cid,
        scope,
        ttl_seconds: DURATIONS[ttlIdx].ttl ?? undefined,
      });
      setSuccess(`shared with ${shortAddress(addr)} for ${DURATIONS[ttlIdx].label}`);
      setGrantee("");
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (id: string) => {
    setBusy(true);
    try {
      await api.revokeGrant(token, id);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const qrUrl = api.absoluteUrl(object.cid, object.backend, object.visibility === "private" ? token : null);

  return (
    <Modal title="Share object" onClose={onClose}>
      <p className="muted cid" style={{ marginTop: 0 }}>{object.cid}</p>
      <div className="modal-grid">
        <div>
          <h3 className="panel-title">Grant timed access</h3>
          <div className="col">
            <input
              type="text"
              placeholder="grantee 0x address"
              value={grantee}
              onChange={(e) => setGrantee(e.target.value)}
            />
            <div className="row">
              <select value={scope} onChange={(e) => setScope(e.target.value as never)}>
                <option value="read">can read</option>
                <option value="write">can read + write</option>
              </select>
              <select value={ttlIdx} onChange={(e) => setTtlIdx(Number(e.target.value))}>
                {DURATIONS.map((d, i) => (
                  <option key={i} value={i}>
                    {d.label}
                  </option>
                ))}
              </select>
            </div>
            <button className="primary" onClick={create} disabled={busy}>
              Grant access
            </button>
          </div>
          <div style={{ marginTop: 16 }}>
            <h3 className="panel-title">Active grants</h3>
            {grants.length === 0 && <p className="muted">None yet.</p>}
            {grants.map((g) => (
              <div key={g.id} className="row grant-row">
                <span className="muted">{shortAddress(g.grantee)}</span>
                <span className="pill">{g.scope}</span>
                <span className="muted">{fmtDuration(g.expires_in)}</span>
                {g.cid === "*" && <span className="pill">all objects</span>}
                <button onClick={() => revoke(g.id)} disabled={busy}>
                  revoke
                </button>
              </div>
            ))}
          </div>
        </div>
        <div className="qr-share">
          <h3 className="panel-title">Scan to open</h3>
          <div className="qr big">
            <QRCodeSVG value={qrUrl} size={150} level="M" />
          </div>
          <p className="muted hint">
            {object.visibility === "private"
              ? "Token-bearing link — opens this private object on any device."
              : "Public link — anyone can open."}
          </p>
        </div>
      </div>
    </Modal>
  );
}

/* ──────────────────────── link-phone (handoff) modal ─────────────────── */

function LinkPhoneModal({ data, onClose }: { data: { code: string; expires: number }; onClose: () => void }) {
  const [left, setLeft] = useState(Math.max(0, data.expires - Math.floor(Date.now() / 1000)));
  useEffect(() => {
    const t = setInterval(() => setLeft(Math.max(0, data.expires - Math.floor(Date.now() / 1000))), 1000);
    return () => clearInterval(t);
  }, [data.expires]);
  const claimUrl = useMemo(
    () => `${window.location.origin}${BASE_PATH}/?claim=${data.code}`,
    [data.code]
  );
  return (
    <Modal title="Link a phone" onClose={onClose}>
      <p className="muted" style={{ marginTop: 0 }}>
        Scan with your phone camera to sign in there — no MetaMask needed on the phone. The code is
        single-use and expires in <strong>{left}s</strong>.
      </p>
      <div className="qr big center">
        <QRCodeSVG value={claimUrl} size={220} level="M" />
      </div>
      <p className="muted hint center">{left > 0 ? "waiting for scan…" : "expired — close and try again"}</p>
    </Modal>
  );
}

/* ──────────────────────────── pools view ──────────────────────────── */

function PoolsView({
  token,
  pools,
  onChanged,
  onOpen,
  setError,
}: {
  token: string;
  pools: PoolSummary[];
  onChanged: () => void;
  onOpen: (id: string) => void;
  setError: (s: string) => void;
}) {
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [busy, setBusy] = useState(false);

  const create = async () => {
    if (!name.trim()) return;
    setBusy(true);
    try {
      await api.createPool(token, { name: name.trim(), description: desc.trim() || undefined });
      setName("");
      setDesc("");
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel">
      <h2 className="panel-title">Data pools</h2>
      <p className="muted hint">
        A pool is shared space: every member gets mutual read access to objects pooled into it. Add
        members with roles and optional time limits.
      </p>
      <div className="row">
        <input type="text" placeholder="pool name" value={name} onChange={(e) => setName(e.target.value)} />
        <input
          type="text"
          placeholder="description (optional)"
          value={desc}
          onChange={(e) => setDesc(e.target.value)}
          style={{ flex: 1 }}
        />
        <button className="primary" onClick={create} disabled={busy || !name.trim()}>
          Create pool
        </button>
      </div>

      {pools.length === 0 && <p className="muted" style={{ marginTop: 16 }}>No pools yet.</p>}
      <div className="pool-grid">
        {pools.map((p) => (
          <button key={p.id} className="pool-card" onClick={() => onOpen(p.id)}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <strong>{p.name}</strong>
              <span className={`pill ${p.role === "owner" ? "admin" : ""}`}>{p.role}</span>
            </div>
            {p.description && <p className="muted" style={{ margin: "6px 0" }}>{p.description}</p>}
            <div className="row muted" style={{ fontSize: 12 }}>
              <span>{p.member_count} members</span>
              <span>·</span>
              <span>{p.object_count} objects</span>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

/* ──────────────────────────── pool modal ──────────────────────────── */

function PoolModal({
  token,
  pool,
  me,
  onClose,
  onChanged,
  setError,
}: {
  token: string;
  pool: PoolDetail;
  me: string | null;
  onClose: () => void;
  onChanged: () => void;
  setError: (s: string) => void;
}) {
  const [addr, setAddr] = useState("");
  const [role, setRole] = useState<"viewer" | "editor">("viewer");
  const [ttlIdx, setTtlIdx] = useState(5);
  const [cid, setCid] = useState("");
  const [busy, setBusy] = useState(false);
  const canManage = pool.role === "owner" || pool.role === "editor";

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={`Pool — ${pool.name}`} onClose={onClose}>
      <p className="muted" style={{ marginTop: 0 }}>
        {pool.description || "No description."} · you are <strong>{pool.role}</strong>
      </p>

      <div className="modal-grid">
        <div>
          <h3 className="panel-title">Members ({pool.members.length})</h3>
          {pool.members.map((m) => (
            <div key={m.address} className="row grant-row">
              <span className="muted">{shortAddress(m.address)}</span>
              <span className={`pill ${m.role === "owner" ? "admin" : ""}`}>{m.role}</span>
              <span className="muted">{m.expired ? "expired" : fmtDuration(m.expires_in)}</span>
              {canManage && m.role !== "owner" && (
                <button onClick={() => run(() => api.removeMember(token, pool.id, m.address))} disabled={busy}>
                  remove
                </button>
              )}
            </div>
          ))}
          {canManage && (
            <div className="col" style={{ marginTop: 12 }}>
              <input type="text" placeholder="member 0x address" value={addr} onChange={(e) => setAddr(e.target.value)} />
              <div className="row">
                {pool.role === "owner" && (
                  <select value={role} onChange={(e) => setRole(e.target.value as never)}>
                    <option value="viewer">viewer</option>
                    <option value="editor">editor</option>
                  </select>
                )}
                <select value={ttlIdx} onChange={(e) => setTtlIdx(Number(e.target.value))}>
                  {DURATIONS.map((d, i) => (
                    <option key={i} value={i}>
                      {d.label}
                    </option>
                  ))}
                </select>
                <button
                  className="primary"
                  disabled={busy || !addr.trim()}
                  onClick={() =>
                    run(async () => {
                      await api.addMember(token, pool.id, {
                        address: addr.trim(),
                        role,
                        ttl_seconds: DURATIONS[ttlIdx].ttl ?? undefined,
                      });
                      setAddr("");
                    })
                  }
                >
                  add
                </button>
              </div>
            </div>
          )}
        </div>

        <div>
          <h3 className="panel-title">Objects ({pool.objects.length})</h3>
          {pool.objects.map((o) => (
            <div key={o.cid} className="row grant-row">
              <span className="cid" style={{ fontSize: 11 }}>{o.cid.slice(0, 20)}…</span>
              {o.backend && <span className="pill">{o.backend}</span>}
              <a href={api.getUrl(o.cid, o.backend || undefined, token)} target="_blank" rel="noreferrer">
                open
              </a>
              {canManage && (
                <button onClick={() => run(() => api.removePoolObject(token, pool.id, o.cid))} disabled={busy}>
                  remove
                </button>
              )}
            </div>
          ))}
          {canManage && (
            <div className="col" style={{ marginTop: 12 }}>
              <input type="text" placeholder="CID to pool (must be readable by you)" value={cid} onChange={(e) => setCid(e.target.value)} />
              <button
                className="primary"
                disabled={busy || !cid.trim()}
                onClick={() =>
                  run(async () => {
                    await api.addPoolObject(token, pool.id, { cid: cid.trim() });
                    setCid("");
                  })
                }
              >
                add object
              </button>
            </div>
          )}
          {me && pool.owner !== me.toLowerCase() && (
            <button
              style={{ marginTop: 16 }}
              onClick={() => run(async () => { await api.removeMember(token, pool.id, me); onClose(); })}
              disabled={busy}
            >
              Leave pool
            </button>
          )}
        </div>
      </div>
    </Modal>
  );
}

/* ─────────────────────── register external CID ─────────────────────── */

function RegisterExternal({
  token,
  onDone,
  setError,
  setSuccess,
}: {
  token: string;
  onDone: () => void;
  setError: (s: string) => void;
  setSuccess: (s: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [cid, setCid] = useState("");
  const [url, setUrl] = useState("");
  const [scheme, setScheme] = useState("");
  const [pub, setPub] = useState(false);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!cid.trim()) return;
    setBusy(true);
    try {
      await api.registerExternal(token, {
        cid: cid.trim(),
        url: url.trim() || undefined,
        scheme: scheme.trim() || undefined,
        public: pub,
      });
      setSuccess(`registered ${cid.slice(0, 16)}… — now shareable & poolable`);
      setCid("");
      setUrl("");
      setScheme("");
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel">
      <button className="link-toggle" onClick={() => setOpen((o) => !o)}>
        {open ? "▾" : "▸"} Register an external CID (arweave, ipfs elsewhere, s3, any system)
      </button>
      {open && (
        <div className="col" style={{ marginTop: 12 }}>
          <p className="muted hint" style={{ margin: 0 }}>
            The store is CID-agnostic: reference data living in another system so it becomes a
            first-class object you can share and pool. Provide a gateway URL to make it retrievable.
          </p>
          <input type="text" placeholder="cid / id (e.g. ar://… , bafy… , s3://…)" value={cid} onChange={(e) => setCid(e.target.value)} />
          <input type="text" placeholder="gateway url (optional, e.g. https://arweave.net/<tx>)" value={url} onChange={(e) => setUrl(e.target.value)} />
          <div className="row">
            <input type="text" placeholder="scheme (auto-detected if blank)" value={scheme} onChange={(e) => setScheme(e.target.value)} />
            <label className="check">
              <input type="checkbox" checked={pub} onChange={(e) => setPub(e.target.checked)} /> public
            </label>
            <button className="primary" onClick={submit} disabled={busy || !cid.trim()}>
              Register
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/* ──────────────────────────── modal shell ──────────────────────────── */

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <strong>{title}</strong>
          <button onClick={onClose}>✕</button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}
