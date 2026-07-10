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
  ObjectInfo,
  PinInfo,
  PoolDetail,
  PoolSummary,
  Preview,
  Quota,
  StoredObject,
  TermsResponse,
} from "@/lib/api";
import { storageGet, storageRemove, storageSet } from "@/lib/safeStorage";

const TOKEN_KEY = "store:token";
const ADDR_KEY = "store:addr";
const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "";

type View = "files" | "add" | "shared" | "pins" | "pools" | "status";

const DURATIONS: { label: string; ttl: number | null }[] = [
  { label: "15 minutes", ttl: 15 * 60 },
  { label: "1 hour", ttl: 60 * 60 },
  { label: "1 day", ttl: 24 * 60 * 60 },
  { label: "7 days", ttl: 7 * 24 * 60 * 60 },
  { label: "30 days", ttl: 30 * 24 * 60 * 60 },
  { label: "Never (until revoked)", ttl: null },
];

const TICKET_TTLS = [10, 30, 60, 300];

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

function fmtDate(secs: number | null | undefined): string {
  if (!secs) return "—";
  return new Date(secs * 1000).toLocaleString();
}

/* deterministic gradient avatar for a 0x address */
function identiconStyle(addr: string): React.CSSProperties {
  let h = 0;
  for (let i = 2; i < addr.length; i++) h = (h * 31 + addr.charCodeAt(i)) >>> 0;
  const h1 = h % 360;
  const h2 = (h1 + 80 + ((h >> 8) % 140)) % 360;
  const ang = (h >> 16) % 360;
  return {
    background: `linear-gradient(${ang}deg, hsl(${h1} 72% 58%), hsl(${h2} 68% 42%))`,
  };
}

const LogoMark = () => (
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden>
    <defs>
      <linearGradient id="lg-brand" x1="0" y1="0" x2="24" y2="24">
        <stop offset="0" stopColor="#ffffff" />
        <stop offset="1" stopColor="#5b8cff" />
      </linearGradient>
    </defs>
    <path d="M12 2.6 20.6 7.4v9.2L12 21.4 3.4 16.6V7.4L12 2.6Z" stroke="url(#lg-brand)" strokeWidth="1.5" strokeLinejoin="round" />
    <path d="M3.6 7.6 12 12.3l8.4-4.7M12 12.4v8.8" stroke="url(#lg-brand)" strokeWidth="1.5" strokeLinejoin="round" />
  </svg>
);

const PhoneIcon = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden>
    <rect x="7" y="2.5" width="10" height="19" rx="2.5" />
    <path d="M10.5 18.5h3" />
  </svg>
);

const PowerIcon = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden>
    <path d="M12 3v8" />
    <path d="M6.6 6.6a7.5 7.5 0 1 0 10.8 0" />
  </svg>
);

const WalletIcon = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M20 7H5a2 2 0 0 1 0-4h13v4" />
    <path d="M4 6.5V18a2 2 0 0 0 2 2h14a1 1 0 0 0 1-1V8a1 1 0 0 0-1-1" />
    <circle cx="16.5" cy="13.5" r="1" fill="currentColor" stroke="none" />
  </svg>
);

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
  const [pins, setPins] = useState<PinInfo[]>([]);

  // search
  const [searchText, setSearchText] = useState("");
  const [semResults, setSemResults] = useState<StoredObject[] | null>(null);

  // upload form
  const [uploadKind, setUploadKind] = useState<"file" | "text" | "json" | "image">("file");
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [textName, setTextName] = useState("");
  const [jsonText, setJsonText] = useState("");
  const [jsonName, setJsonName] = useState("");
  const [jsonErr, setJsonErr] = useState<string | null>(null);
  const [backend, setBackend] = useState<"localfs" | "filecoin" | "hippius" | "both">("localfs");
  const [makePublic, setMakePublic] = useState(false);
  const [uploadPool, setUploadPool] = useState("");

  const [copied, setCopied] = useState<string | null>(null);
  const [serviceStatus, setServiceStatus] = useState<Record<string, unknown> | null>(null);
  const [svcDown, setSvcDown] = useState(false);

  // modals
  const [shareFor, setShareFor] = useState<StoredObject | null>(null);
  const [ticketFor, setTicketFor] = useState<StoredObject | null>(null);
  const [contentFor, setContentFor] = useState<StoredObject | null>(null);
  const [infoFor, setInfoFor] = useState<string | null>(null);
  const [linkPhone, setLinkPhone] = useState<{ code: string; expires: number } | null>(null);
  const [openPool, setOpenPool] = useState<PoolDetail | null>(null);
  const [termsDoc, setTermsDoc] = useState<TermsResponse | null>(null);

  const quota: Quota | null = me?.quota ?? null;
  const canStore = !!me?.authorized;
  // Storing requires a wallet-signed acceptance of the current terms of service.
  const termsSigned = me?.terms ? me.terms.accepted : true;

  const applySession = useCallback((t: string, r: MeResponse) => {
    storageSet(TOKEN_KEY, t);
    storageSet(ADDR_KEY, r.address);
    setToken(t);
    setAddress(r.address);
    setMe(r);
  }, []);

  // claim a QR handoff (?claim=code)
  useEffect(() => {
    setHasWallet(hasMetaMask());
    api.status().then(setServiceStatus).catch(() => setSvcDown(true));

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
    const t = storageGet(TOKEN_KEY);
    if (t) {
      api
        .me(t)
        .then((r) => applySession(t, r))
        .catch(() => {
          storageRemove(TOKEN_KEY);
          storageRemove(ADDR_KEY);
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
  const refreshPins = useCallback(async (t: string) => {
    try {
      setPins((await api.pins(t)).pins);
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

  const refreshAll = useCallback(
    (t: string) => {
      refreshFiles(t);
      refreshShared(t);
      refreshPools(t);
      refreshPins(t);
    },
    [refreshFiles, refreshShared, refreshPools, refreshPins]
  );

  useEffect(() => {
    if (token) refreshAll(token);
  }, [token, refreshAll]);

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
      setSuccess(r.authorized ? `signed in${r.admin ? " as admin" : ""}` : "signed in — not whitelisted to store");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const signOut = () => {
    storageRemove(TOKEN_KEY);
    storageRemove(ADDR_KEY);
    setToken(null);
    setMe(null);
    setObjects([]);
    setSharedObjects([]);
    setPools([]);
    setPins([]);
  };

  const upload = async () => {
    if (!token) return;
    let payload: File | null = file;
    if (uploadKind === "text") {
      if (!text.trim()) return;
      const name = (textName.trim() || `note-${Date.now()}`).replace(/[^\w.\-]/g, "_");
      payload = new File([text], name.endsWith(".txt") ? name : `${name}.txt`, { type: "text/plain" });
    }
    if (uploadKind === "json") {
      if (!jsonText.trim()) return;
      try {
        JSON.parse(jsonText);
      } catch (e) {
        setJsonErr((e as Error).message);
        return;
      }
      setJsonErr(null);
      const name = (jsonName.trim() || `data-${Date.now()}`).replace(/[^\w.\-]/g, "_");
      payload = new File([jsonText], name.endsWith(".json") ? name : `${name}.json`, { type: "application/json" });
    }
    if (!payload) return;
    setError(null);
    setSuccess(null);
    setBusy(`storing to ${backend}…`);
    try {
      const r = await api.put(token, payload, backend, { public: makePublic, pool: uploadPool || undefined });
      const cids = Object.entries(r.results)
        .map(([b, v]) => (v.cid ? `${b}: ${v.cid.slice(0, 16)}…` : `${b}: error — ${v.error}`))
        .join("  •  ");
      const mapped = r.refs && r.refs.length > 0 ? ` • mapped from ${r.refs.length} existing object${r.refs.length > 1 ? "s" : ""}` : "";
      setSuccess(`stored (${makePublic ? "public" : "private"})${uploadPool ? " → pool" : ""}: ${cids}${mapped}`);
      setFile(null);
      setText("");
      setTextName("");
      setJsonText("");
      setJsonName("");
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

  const doPin = async (cid: string, b: string) => {
    if (!token) return;
    setBusy(`pinning ${cid.slice(0, 12)}…`);
    try {
      await api.pin(token, cid, b);
      await refreshPins(token);
      setSuccess(`pinned ${cid.slice(0, 12)}…`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const openTerms = async () => {
    try {
      setTermsDoc(await api.terms(token));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const signTerms = async () => {
    if (!token) return;
    setBusy("recording signed acceptance…");
    try {
      await api.acceptTerms(token);
      await refreshMe(token);
      setTermsDoc(null);
      setSuccess("terms accepted — you can now store data");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const doRemove = async (o: StoredObject, takedown = false) => {
    if (!token) return;
    let reason: string | undefined;
    if (takedown) {
      const r = prompt("Takedown reason (recorded in the moderation audit log):", "illegal content");
      if (r === null) return;
      reason = r || "admin takedown";
    } else if (!confirm(`Remove ${o.cid.slice(0, 16)}…? This deletes the stored object.`)) {
      return;
    }
    setBusy(`removing ${o.cid.slice(0, 12)}…`);
    try {
      await api.rm(token, o.cid, reason);
      refreshFiles(token);
      refreshShared(token);
      setSuccess(takedown ? `taken down ${o.cid.slice(0, 12)}… (logged)` : `removed ${o.cid.slice(0, 12)}…`);
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

  const runSemanticSearch = async () => {
    if (!token || !searchText.trim()) return;
    setBusy("semantic search…");
    try {
      const r = await api.search(token, { semantic_q: searchText.trim(), scope: "all" });
      setSemResults(r.objects);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  // client-side substring filter for the instant text search
  const visibleObjects = useMemo(() => {
    if (semResults) return semResults;
    const q = searchText.trim().toLowerCase();
    if (!q) return objects;
    return objects.filter(
      (o) => o.cid.toLowerCase().includes(q) || (o.key || "").toLowerCase().includes(q)
    );
  }, [objects, searchText, semResults]);

  const usedPct = quota && quota.limit_bytes ? Math.min(100, (quota.used_bytes / quota.limit_bytes) * 100) : 0;

  return (
    <div className="wrap">
      <header className="header">
        <div className="brand-block">
          <div className="brand-row">
            <span className="brand-mark"><LogoMark /></span>
            <span className="brand">store</span>
            <span
              className={`svc-dot ${serviceStatus ? "up" : svcDown ? "down" : ""}`}
              title={serviceStatus ? "service online" : svcDown ? "service unreachable" : "connecting…"}
            />
          </div>
          <span className="brand-sub">
            private storage <i>·</i> timed sharing <i>·</i> pools <i>·</i> cid-agnostic
          </span>
        </div>
        <div className="identity">
          {!hasWallet && !token && (
            <span className="id-chip idle">
              <span className="dot warn" />
              <span className="muted" style={{ fontSize: 12.5 }}>MetaMask not detected</span>
            </span>
          )}
          {hasWallet && !token && (
            <button className="primary" onClick={signIn} disabled={!!busy}>
              <span className="btn-ico"><WalletIcon /></span> Sign in with wallet
            </button>
          )}
          {token && address && (
            <div className="id-chip">
              <span className="id-avatar" style={identiconStyle(address)}>
                <span className={`dot ${me?.authorized ? "ok" : "warn"}`} />
              </span>
              {me?.admin && <span className="pill admin">owner</span>}
              {!me?.admin && me?.via === "bloctime" && <span className="pill bloctime">⏱ bloctime</span>}
              {!me?.admin && me?.via !== "bloctime" && me?.authorized && <span className="pill">member</span>}
              {me && !me.authorized && <span className="pill private">view-only</span>}
              <button
                className="id-addr"
                title={`${address} — click to copy`}
                onClick={() => copyText(address, "hdr-addr")}
              >
                {copied === "hdr-addr" ? "✓ copied" : shortAddress(address)}
              </button>
              <span className="id-sep" />
              <button className="ghost icon" onClick={startLinkPhone} disabled={!!busy} title="Link a phone — QR sign-in, no wallet needed">
                <PhoneIcon />
              </button>
              <button className="ghost icon" onClick={signOut} title="Sign out">
                <PowerIcon />
              </button>
            </div>
          )}
        </div>
      </header>

      {busy && <div className="success-box">⟳ {busy}</div>}
      {error && <div className="error-box">✕ {error}</div>}
      {success && !busy && <div className="success-box">✓ {success}</div>}

      {!token && (
        <div className="gate-card">
          <div className="gate-glow" />
          <h2>Gated storage</h2>
          <p className="muted">
            Access is restricted to the <strong>mod owner</strong> and on-chain <strong>BlocTime</strong> holders.
            Sign in with your wallet to check your access — holding BlocTime grants entry automatically, no
            whitelisting needed.
          </p>
        </div>
      )}

      {token && !canStore && (
        <div className="error-box">
          Signed in as <code>{address}</code> but not authorized — this store is gated to the mod owner and
          on-chain BlocTime holders. Hold BlocTime to gain access, or ask the owner to whitelist you.
        </div>
      )}

      {/* at-a-glance stats */}
      {token && me && (
        <div className="statbar">
          {(
            [
              ["files", objects.length, "objects"],
              ["shared", sharedObjects.length, "shared with you"],
              ["pins", pins.length, "pins"],
              ["pools", pools.length, "pools"],
            ] as [View, number, string][]
          ).map(([v, n, label]) => (
            <button key={v} className={`stat ${view === v ? "active" : ""}`} onClick={() => setView(v)}>
              <span className="stat-n">{n}</span>
              <span className="stat-l">{label}</span>
            </button>
          ))}
          {quota && (
            <button
              className={`stat storage ${view === "status" ? "active" : ""}`}
              onClick={() => setView("status")}
              title={quota.unlimited ? "unlimited (admin)" : `${fmtBytes(quota.remaining_bytes)} left`}
            >
              <span className="stat-n">{fmtBytes(quota.used_bytes)}</span>
              <span className="stat-l">
                {quota.unlimited ? "stored · unlimited" : `of ${fmtBytes(quota.limit_bytes)} used`}
              </span>
              {!quota.unlimited && (
                <span className="stat-bar">
                  <span className={`stat-fill ${usedPct > 85 ? "hot" : ""}`} style={{ width: `${usedPct}%` }} />
                </span>
              )}
            </button>
          )}
        </div>
      )}

      {/* top nav */}
      {token && (
        <nav className="navbar">
          {(
            [
              ["files", "Your objects"],
              ["add", "＋ Add data"],
              ["shared", "Shared with you"],
              ["pins", "Pins"],
              ["pools", "Pools"],
              ["status", "Status"],
            ] as [View, string][]
          ).map(([v, label]) => (
            <button key={v} className={`navtab ${view === v ? "active" : ""}`} onClick={() => setView(v)}>
              {label}
            </button>
          ))}
        </nav>
      )}

      {/* terms of service gate */}
      {token && canStore && !termsSigned && (
        <div className="gate-card">
          <div className="gate-glow" />
          <h2>✍️ One signature to start storing</h2>
          <p className="muted">
            Before your first upload, sign the <strong>terms of service</strong>{" "}
            <span className="pill">v{me?.terms?.version}</span> — you own what you store and are responsible
            for it, and the operator may remove illegal content. Recorded once per version against your address.
          </p>
          <div className="row" style={{ marginTop: 16 }}>
            <button className="primary" onClick={openTerms} disabled={!!busy}>Read &amp; sign terms</button>
          </div>
        </div>
      )}

      {/* upload */}
      {token && canStore && termsSigned && view === "add" && (
        <div className="panel">
          <h2 className="panel-title">Add data</h2>
          <div className="tabs">
            {(["file", "text", "json", "image"] as const).map((k) => (
              <button key={k} className={`tab ${uploadKind === k ? "active" : ""}`} onClick={() => setUploadKind(k)}>
                {k === "file" ? "📄 File" : k === "text" ? "✍️ Text" : k === "json" ? "🧩 JSON" : "🖼️ Image"}
              </button>
            ))}
          </div>
          {uploadKind === "file" && (
            <input type="file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} disabled={!!busy} />
          )}
          {uploadKind === "image" && (
            <input type="file" accept="image/*" capture="environment" onChange={(e) => setFile(e.target.files?.[0] ?? null)} disabled={!!busy} />
          )}
          {uploadKind === "text" && (
            <div className="col">
              <input type="text" placeholder="name (optional, e.g. recipe.txt)" value={textName} onChange={(e) => setTextName(e.target.value)} disabled={!!busy} />
              <textarea placeholder="Paste or type any text — stored content-addressed like a file." value={text} onChange={(e) => setText(e.target.value)} rows={6} disabled={!!busy} />
            </div>
          )}
          {uploadKind === "json" && (
            <div className="col">
              <div className="row" style={{ justifyContent: "space-between" }}>
                <input type="text" placeholder="name (optional, e.g. bundle.json)" value={jsonName} onChange={(e) => setJsonName(e.target.value)} disabled={!!busy} />
                <label className="btn-link" style={{ cursor: "pointer" }}>
                  Load .json file…
                  <input
                    type="file"
                    accept=".json,application/json"
                    style={{ display: "none" }}
                    disabled={!!busy}
                    onChange={async (e) => {
                      const f = e.target.files?.[0];
                      if (!f) return;
                      setJsonText(await f.text());
                      setJsonName(f.name);
                      e.target.value = "";
                    }}
                  />
                </label>
              </div>
              <textarea
                placeholder="Paste or type JSON. Any CID string it embeds that matches an existing stored object is auto-linked — visible as a graph in that object's 'graph' tab."
                value={jsonText}
                onChange={(e) => { setJsonText(e.target.value); setJsonErr(null); }}
                rows={8}
                disabled={!!busy}
              />
              {jsonErr && <p className="error-box" style={{ margin: 0 }}>invalid JSON: {jsonErr}</p>}
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
              {pools.filter((p) => p.role === "owner" || p.role === "editor").map((p) => (
                <option key={p.id} value={p.id}>→ pool: {p.name}</option>
              ))}
            </select>
            <label className="check">
              <input type="checkbox" checked={makePublic} onChange={(e) => setMakePublic(e.target.checked)} disabled={!!busy} />
              public
            </label>
            <button
              className="primary"
              onClick={upload}
              disabled={!!busy || (uploadKind === "text" ? !text.trim() : uploadKind === "json" ? !jsonText.trim() : !file)}
            >
              Store
            </button>
          </div>
          <p className="muted hint">
            {makePublic ? "Public: anyone with the link/CID can read it." : "Private: only you — until you share it or add it to a pool."}
          </p>
        </div>
      )}

      {/* fetch by CID */}
      {token && view === "files" && (
        <FetchByCid token={token} onView={(cid) => setContentFor({ cid, backend: "" } as StoredObject)} onInfo={setInfoFor} />
      )}

      {/* your objects */}
      {token && view === "files" && (
        <div className="panel">
          <div className="row" style={{ justifyContent: "space-between", marginBottom: 12 }}>
            <h2 className="panel-title" style={{ margin: 0 }}>Your objects</h2>
            <div className="row search-row">
              <input
                type="text"
                placeholder="search cid / name…"
                value={searchText}
                onChange={(e) => {
                  setSearchText(e.target.value);
                  setSemResults(null);
                }}
                onKeyDown={(e) => e.key === "Enter" && runSemanticSearch()}
              />
              <button onClick={runSemanticSearch} disabled={!searchText.trim() || !!busy} title="Rank by semantic similarity (1-bit hash)">
                🧠 semantic
              </button>
              {semResults && (
                <button className="ghost" onClick={() => setSemResults(null)}>clear</button>
              )}
            </div>
          </div>
          {semResults && <p className="muted hint" style={{ marginTop: 0 }}>Ranked by semantic similarity to “{searchText}”. Nearest first.</p>}
          {visibleObjects.length === 0 && <p className="muted">No matching objects.</p>}
          <ul className="objects">
            {visibleObjects.map((o) => (
              <ObjectRow
                key={`${o.cid}-${o.backend}-${o.timestamp ?? 0}`}
                o={o}
                token={token}
                busy={!!busy}
                copied={copied}
                onCopy={copyText}
                onView={() => setContentFor(o)}
                onTicket={() => setTicketFor(o)}
                onShare={() => setShareFor(o)}
                onInfo={() => setInfoFor(o.cid)}
                onPublish={() => togglePublish(o)}
                onPin={() => doPin(o.cid, o.backend)}
                onRemove={() => doRemove(o)}
              />
            ))}
          </ul>
        </div>
      )}

      {/* shared with you */}
      {token && view === "shared" && (
        <div className="panel">
          <h2 className="panel-title">Shared with you</h2>
          <p className="muted hint">Objects others granted you (timed) or shared via a pool.</p>
          {sharedObjects.length === 0 && <p className="muted">Nothing shared with you yet.</p>}
          <ul className="objects">
            {sharedObjects.map((o) => (
              <ObjectRow
                key={`${o.cid}-shared`}
                o={o}
                token={token}
                busy={!!busy}
                copied={copied}
                shared
                onCopy={copyText}
                onView={() => setContentFor(o)}
                onTicket={() => setTicketFor(o)}
                onInfo={() => setInfoFor(o.cid)}
                onRemove={me?.admin ? () => doRemove(o, true) : undefined}
                removeLabel="⚖️ take down"
              />
            ))}
          </ul>
        </div>
      )}

      {/* pins */}
      {token && view === "pins" && (
        <div className="panel">
          <h2 className="panel-title">Pinned objects</h2>
          <p className="muted hint">Pins you’ve placed. Unpin to stop tracking it here.</p>
          {pins.length === 0 && <p className="muted">No pins yet — pin an object from “Your objects”.</p>}
          <ul className="objects">
            {pins.map((p) => (
              <li key={`${p.cid}-${p.backend}`} className="object-card compact">
                <div className="object-meta">
                  <div className="row">
                    <span className={`pill ${p.backend}`}>{p.backend}</span>
                    {p.visibility && <span className={`pill ${p.visibility}`}>{p.visibility === "private" ? "🔒" : "🌐"} {p.visibility}</span>}
                    {p.key && <span className="muted">{p.key}</span>}
                    {p.size != null && <span className="muted">{fmtBytes(p.size)}</span>}
                  </div>
                  <button className="cid-btn" onClick={() => copyText(p.cid, `pin-${p.cid}`)}>
                    <span className="cid">{p.cid}</span>
                    <span className="muted"> {copied === `pin-${p.cid}` ? "✓ copied" : "⧉"}</span>
                  </button>
                  <div className="row">
                    <button onClick={() => setContentFor({ cid: p.cid, backend: p.backend } as StoredObject)} disabled={!!busy}>view</button>
                    <button onClick={() => setInfoFor(p.cid)} disabled={!!busy}>info</button>
                    <button onClick={async () => { await api.unpin(token, p.cid, p.backend); refreshPins(token); }} disabled={!!busy}>unpin</button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* pools */}
      {token && view === "pools" && (
        <PoolsView token={token} pools={pools} onChanged={() => token && refreshPools(token)} onOpen={async (id) => setOpenPool(await api.pool(token, id))} setError={setError} />
      )}

      {token && view === "add" && (
        <RegisterExternal token={token} onDone={() => token && refreshFiles(token)} setError={setError} setSuccess={setSuccess} />
      )}

      {(!token || view === "status") && (
        <div className="panel">
          <h2 className="panel-title">Service status</h2>
          <pre className="status">{serviceStatus ? JSON.stringify(serviceStatus, null, 2) : "loading…"}</pre>
        </div>
      )}

      {/* modals */}
      {shareFor && token && <ShareModal token={token} object={shareFor} onClose={() => setShareFor(null)} setError={setError} setSuccess={setSuccess} />}
      {ticketFor && token && <TicketModal token={token} object={ticketFor} onClose={() => setTicketFor(null)} setError={setError} />}
      {contentFor && token && <ContentModal token={token} object={contentFor} onClose={() => setContentFor(null)} setError={setError} copyText={copyText} copied={copied} />}
      {infoFor && token && <InfoModal token={token} cid={infoFor} onClose={() => setInfoFor(null)} onNavigate={setInfoFor} />}
      {linkPhone && <LinkPhoneModal data={linkPhone} onClose={() => setLinkPhone(null)} />}
      {termsDoc && token && (
        <Modal title={`Terms of service — v${termsDoc.version}`} onClose={() => setTermsDoc(null)}>
          <div className="terms-text">
            <TermsBody text={termsDoc.text} />
          </div>
          <p className="terms-sign-note">
            Accepting records v{termsDoc.version} against {shortAddress(address ?? "")} together with your
            wallet-signed session proof.
          </p>
          <div className="row">
            <button className="primary" onClick={signTerms} disabled={!!busy}>✍️ Sign &amp; accept</button>
            <button className="ghost" onClick={() => setTermsDoc(null)} disabled={!!busy}>not now</button>
          </div>
        </Modal>
      )}
      {openPool && token && (
        <PoolModal
          token={token}
          pool={openPool}
          me={address}
          onClose={() => setOpenPool(null)}
          onDeleted={() => { setOpenPool(null); refreshPools(token); }}
          onChanged={async () => { setOpenPool(await api.pool(token, openPool.id)); refreshPools(token); refreshFiles(token); }}
          setError={setError}
        />
      )}
    </div>
  );
}

/* ──────────────────────────── object row ──────────────────────────── */

function ObjectRow({
  o, token, busy, copied, shared, onCopy, onView, onTicket, onShare, onInfo, onPublish, onPin, onRemove, removeLabel,
}: {
  o: StoredObject;
  token: string;
  busy: boolean;
  copied: string | null;
  shared?: boolean;
  onCopy: (s: string, tag: string) => void;
  onView: () => void;
  onTicket: () => void;
  onShare?: () => void;
  onInfo: () => void;
  onPublish?: () => void;
  onPin?: () => void;
  onRemove?: () => void;
  removeLabel?: string;
}) {
  const priv = o.visibility === "private";
  return (
    <li className="object-card compact">
      <div className="object-meta">
        <div className="row">
          {o.backend && <span className={`pill ${o.backend}`}>{o.backend}</span>}
          {o.scheme && o.scheme !== "ipfs" && <span className="pill">{o.scheme}</span>}
          {o.visibility && <span className={`pill ${priv ? "private" : "public"}`}>{priv ? "🔒 private" : "🌐 public"}</span>}
          {o.shared_via && <span className="pill">via {o.shared_via}</span>}
          {o.similarity != null && <span className="pill sim">{Math.round(o.similarity * 100)}% match</span>}
          {o.key && <span className="muted">{o.key}</span>}
          {o.size != null && <span className="muted">{fmtBytes(o.size)}</span>}
        </div>
        <button className="cid-btn" title="Copy CID" onClick={() => onCopy(o.cid, o.cid)}>
          <span className="cid">{o.cid}</span>
          <span className="muted"> {copied === o.cid ? "✓ copied" : "⧉"}</span>
        </button>
        {o.semhash && (
          <button className="sem-chip" title="1-bit semantic hash (click to copy)" onClick={() => onCopy(o.semhash!, `sem-${o.cid}`)}>
            🧠 <span className="mono">{o.semhash}</span>
            <span className="muted"> {copied === `sem-${o.cid}` ? "✓" : ""}</span>
          </button>
        )}
        <div className="row actions">
          <button onClick={onView} disabled={busy}>view</button>
          <button onClick={onTicket} disabled={busy} title="One-time QR / link for your phone">📱 QR</button>
          <a href={api.getUrl(o.cid, o.backend, priv ? token : null)} target="_blank" rel="noreferrer">download</a>
          <button onClick={onInfo} disabled={busy}>info</button>
          {!shared && onShare && <button onClick={onShare} disabled={busy}>share</button>}
          {!shared && onPublish && <button onClick={onPublish} disabled={busy}>{priv ? "make public" : "make private"}</button>}
          {!shared && onPin && <button onClick={onPin} disabled={busy}>pin</button>}
          {onRemove && <button className="danger" onClick={onRemove} disabled={busy} title="Delete the stored object">{removeLabel ?? "🗑 remove"}</button>}
        </div>
      </div>
    </li>
  );
}

/* ──────────────────────────── fetch by CID ──────────────────────────── */

function FetchByCid({ token, onView, onInfo }: { token: string; onView: (cid: string) => void; onInfo: (cid: string) => void }) {
  const [cid, setCid] = useState("");
  const c = cid.trim();
  return (
    <div className="panel">
      <h2 className="panel-title">Fetch by CID</h2>
      <div className="row">
        <input type="text" placeholder="paste any CID (ipfs / arweave / external)…" value={cid} onChange={(e) => setCid(e.target.value)} style={{ flex: 1 }} />
        <button onClick={() => c && onView(c)} disabled={!c}>view</button>
        <button onClick={() => c && onInfo(c)} disabled={!c}>info</button>
        <a className="btn-link" href={c ? api.getUrl(c, undefined, token) : undefined} target="_blank" rel="noreferrer" aria-disabled={!c}>
          download
        </a>
      </div>
    </div>
  );
}

/* ──────────────────────────── content modal ──────────────────────────── */

function ContentModal({
  token, object, onClose, setError, copyText, copied,
}: {
  token: string;
  object: StoredObject;
  onClose: () => void;
  setError: (s: string) => void;
  copyText: (s: string, tag: string) => void;
  copied: string | null;
}) {
  const [pv, setPv] = useState<Preview | null>(null);
  const [loading, setLoading] = useState(true);
  const imgUrl = api.getUrl(object.cid, object.backend || undefined, token);

  useEffect(() => {
    let live = true;
    api
      .preview(token, object.cid)
      .then((r) => live && setPv(r))
      .catch((e) => live && setError((e as Error).message))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [token, object.cid, setError]);

  const looksImage = (object.key || "").match(/\.(png|jpe?g|gif|webp|svg|bmp)$/i);

  const copyAll = async () => {
    let full = pv?.text || "";
    if (pv?.truncated) {
      try {
        const res = await fetch(imgUrl);
        full = await res.text();
      } catch (e) {
        setError((e as Error).message);
        return;
      }
    }
    copyText(full, `all-${object.cid}`);
  };

  return (
    <Modal title="Object content" onClose={onClose}>
      <p className="muted cid" style={{ marginTop: 0 }}>{object.cid}</p>
      {loading && <p className="muted">loading…</p>}
      {pv?.external && (
        <p className="muted">External object — <a href={pv.url || "#"} target="_blank" rel="noreferrer">open at gateway ↗</a></p>
      )}
      {pv && !pv.external && (
        <>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span className="muted">{fmtBytes(pv.size)} {pv.is_text ? "· text" : "· binary"}{pv.truncated ? " · truncated preview" : ""}</span>
            {pv.is_text && (
              <button className="primary" onClick={copyAll}>
                {copied === `all-${object.cid}` ? "✓ copied" : pv.truncated ? "copy all (full)" : "copy all"}
              </button>
            )}
          </div>
          {pv.is_text && <pre className="content-view">{pv.text}{pv.truncated ? "\n\n… (truncated — use “copy all” for the full content)" : ""}</pre>}
          {!pv.is_text && looksImage && (
            <div className="img-wrap"><img src={imgUrl} alt={object.cid} /></div>
          )}
          {!pv.is_text && !looksImage && (
            <p className="muted">Binary content ({fmtBytes(pv.size)}). <a href={imgUrl} target="_blank" rel="noreferrer">download ↗</a></p>
          )}
        </>
      )}
    </Modal>
  );
}

/* ──────────────────────── one-time ticket modal ───────────────────────── */

function TicketModal({
  token, object, onClose, setError,
}: {
  token: string;
  object: StoredObject;
  onClose: () => void;
  setError: (s: string) => void;
}) {
  const [ttl, setTtl] = useState(10);
  const [ticket, setTicket] = useState<{ code: string; expires: number } | null>(null);
  const [left, setLeft] = useState(0);

  const mint = useCallback(
    async (seconds: number) => {
      try {
        const t = await api.createTicket(token, object.cid, seconds, object.backend || undefined);
        setTicket({ code: t.code, expires: t.expires });
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [token, object.cid, object.backend, setError]
  );

  useEffect(() => {
    mint(ttl);
  }, [mint, ttl]);

  useEffect(() => {
    if (!ticket) return;
    const i = setInterval(() => setLeft(Math.max(0, ticket.expires - Math.floor(Date.now() / 1000))), 250);
    return () => clearInterval(i);
  }, [ticket]);

  const url = ticket ? api.ticketUrl(ticket.code) : "";

  return (
    <Modal title="One-time QR / link" onClose={onClose}>
      <p className="muted cid" style={{ marginTop: 0 }}>{object.cid}</p>
      <p className="muted hint" style={{ marginTop: 0 }}>
        Single-use, expiring link — scan it from your phone or copy it. It works <strong>once</strong> and only
        within the window, so it can’t be replayed.
      </p>
      <div className="row" style={{ marginBottom: 12 }}>
        <span className="muted">expires in</span>
        <select value={ttl} onChange={(e) => setTtl(Number(e.target.value))}>
          {TICKET_TTLS.map((s) => (
            <option key={s} value={s}>{s < 60 ? `${s}s` : `${s / 60}m`}</option>
          ))}
        </select>
        <button onClick={() => mint(ttl)}>↻ new code</button>
      </div>
      {url && (
        <div className="center">
          <div className="qr big center">
            <QRCodeSVG value={url} size={220} level="M" />
          </div>
          <p className={`hint center ${left <= 0 ? "expired-txt" : "muted"}`}>
            {left > 0 ? `valid for ${left}s` : "expired — generate a new code"}
          </p>
          <button onClick={() => navigator.clipboard.writeText(url)}>copy link</button>
        </div>
      )}
    </Modal>
  );
}

/* ──────────────────────────── object info modal ──────────────────────────── */

function shortCid(cid: string): string {
  return cid.length > 18 ? `${cid.slice(0, 8)}…${cid.slice(-6)}` : cid;
}

function InfoModal({
  token,
  cid,
  onClose,
  onNavigate,
}: {
  token: string;
  cid: string;
  onClose: () => void;
  onNavigate?: (cid: string) => void;
}) {
  const [info, setInfo] = useState<ObjectInfo | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState<"info" | "graph">("info");
  useEffect(() => {
    let live = true;
    setInfo(null);
    api.objectInfo(token, cid).then((r) => live && setInfo(r)).catch((e) => live && setErr((e as Error).message));
    return () => { live = false; };
  }, [token, cid]);

  const hasGraph = !!(info && (info.links.out.length > 0 || info.links.in.length > 0));

  return (
    <Modal title="Object info" onClose={onClose}>
      <p className="muted cid" style={{ marginTop: 0 }}>{cid}</p>
      {err && <p className="error-box">{err}</p>}
      {!info && !err && <p className="muted">loading…</p>}
      {info && (
        <>
          <div className="tabs">
            <button className={`tab ${tab === "info" ? "active" : ""}`} onClick={() => setTab("info")}>info</button>
            <button className={`tab ${tab === "graph" ? "active" : ""}`} onClick={() => setTab("graph")}>
              graph{hasGraph ? ` (${info.links.out.length + info.links.in.length})` : ""}
            </button>
          </div>

          {tab === "info" && (
            <>
              <div className="info-grid">
                <span className="muted">Owner</span><span className="mono">{info.owner ? shortAddress(info.owner) : "—"} {info.is_owner && <span className="pill admin">you</span>}</span>
                <span className="muted">Stored</span><span>{fmtDate(info.stored_at)}</span>
                <span className="muted">Name</span><span>{info.key || "—"}</span>
                <span className="muted">Size</span><span>{fmtBytes(info.size)}</span>
                <span className="muted">Backends</span><span>{info.backends.join(", ") || "—"}</span>
                <span className="muted">Scheme</span><span>{info.scheme}</span>
                <span className="muted">Visibility</span><span>{info.visibility === "private" ? "🔒 private" : "🌐 public"}</span>
                <span className="muted">Pinned</span><span>{info.pinned ? "yes" : "no"}</span>
                <span className="muted">Semantic hash</span><span className="mono" style={{ wordBreak: "break-all" }}>{info.semhash || "—"}</span>
                {info.external_url && (<><span className="muted">External</span><span><a href={info.external_url} target="_blank" rel="noreferrer">{info.external_url}</a></span></>)}
              </div>

              {info.is_owner && (
                <>
                  <h3 className="panel-title" style={{ marginTop: 18 }}>Who has access</h3>
                  {info.grants.length === 0 && info.pools.length === 0 && (
                    <p className="muted">{info.visibility === "public" ? "Public — anyone can read." : "Only you. Share it or add it to a pool to grant access."}</p>
                  )}
                  {info.grants.map((g) => (
                    <div key={g.id} className="row grant-row">
                      <span className="pill">grant</span>
                      <span className="muted">{shortAddress(g.grantee)}</span>
                      <span className="pill">{g.scope}</span>
                      <span className="muted">{g.expired ? "expired" : fmtDuration(g.expires_in)}</span>
                      {g.cid === "*" && <span className="pill">all objects</span>}
                    </div>
                  ))}
                  {info.pools.map((p) => (
                    <div key={p.id} className="row grant-row">
                      <span className="pill">pool</span>
                      <strong>{p.name}</strong>
                      <span className="muted">{p.members.filter((m) => !m.expired).length} members</span>
                    </div>
                  ))}
                </>
              )}
              {!info.is_owner && (
                <p className="muted" style={{ marginTop: 14 }}>{info.you_can_read ? "You have access to this object." : "You do not have access."}</p>
              )}
            </>
          )}

          {tab === "graph" && (
            hasGraph
              ? <CidGraph cid={cid} links={info.links} onNavigate={onNavigate} />
              : <p className="muted" style={{ marginTop: 14 }}>Not mapped from (or referenced by) any other object in the store.</p>
          )}
        </>
      )}
    </Modal>
  );
}

/* ──────────────────────────── CID graph ──────────────────────────── */

function CidGraph({
  cid,
  links,
  onNavigate,
}: {
  cid: string;
  links: { out: { cid: string; key: string | null }[]; in: { cid: string; key: string | null }[] };
  onNavigate?: (cid: string) => void;
}) {
  const rowH = 46;
  const colW = 240;
  const midY = Math.max(links.out.length, links.in.length, 1) * rowH / 2;
  const height = Math.max(links.out.length, links.in.length, 1) * rowH + 20;

  const node = (n: { cid: string; key: string | null }, i: number, col: "out" | "in") => {
    const x = col === "out" ? 20 : colW * 2 - 20;
    const y = 10 + i * rowH + rowH / 2;
    return (
      <g key={n.cid} transform={`translate(${x},${y})`}>
        <line x1={col === "out" ? 100 : -100} y1="0" x2={col === "out" ? 0 : 0} y2="0" stroke="#2a2a2a" />
        <foreignObject x={col === "out" ? -10 : -190} y={-16} width="200" height="32">
          <button
            className="graph-node"
            title={n.cid}
            onClick={() => onNavigate?.(n.cid)}
            disabled={!onNavigate}
          >
            {n.key || shortCid(n.cid)}
          </button>
        </foreignObject>
      </g>
    );
  };

  return (
    <div className="graph-wrap">
      <svg width="100%" viewBox={`0 0 ${colW * 2} ${height}`} height={height}>
        {links.out.map((n, i) => node(n, i, "out"))}
        {links.in.map((n, i) => node(n, i, "in"))}
        <line x1="100" y1={midY + 10} x2={colW * 2 - 100} y2={midY + 10} stroke="#2a2a2a" strokeDasharray="3,3" />
        <foreignObject x={colW - 90} y={midY - 6} width="180" height="32">
          <div className="graph-node self" title={cid}>{shortCid(cid)}</div>
        </foreignObject>
      </svg>
      <div className="graph-legend">
        <span className="muted">← mapped from (this object's content references these)</span>
        <span className="muted">mapped into (these were built from this) →</span>
      </div>
    </div>
  );
}

/* ──────────────────────────── share modal ──────────────────────────── */

function ShareModal({
  token, object, onClose, setError, setSuccess,
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
  useEffect(() => { load(); }, [load]);

  const create = async () => {
    const addr = grantee.trim().toLowerCase();
    if (!addr.startsWith("0x") || addr.length !== 42) {
      setError("enter a valid 0x address");
      return;
    }
    setBusy(true);
    try {
      await api.createGrant(token, { grantee: addr, cid: object.cid, scope, ttl_seconds: DURATIONS[ttlIdx].ttl ?? undefined });
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

  return (
    <Modal title="Share object" onClose={onClose}>
      <p className="muted cid" style={{ marginTop: 0 }}>{object.cid}</p>
      <h3 className="panel-title">Grant timed access to an address</h3>
      <div className="col">
        <input type="text" placeholder="grantee 0x address" value={grantee} onChange={(e) => setGrantee(e.target.value)} />
        <div className="row">
          <select value={scope} onChange={(e) => setScope(e.target.value as never)}>
            <option value="read">can read</option>
            <option value="write">can read + write</option>
          </select>
          <select value={ttlIdx} onChange={(e) => setTtlIdx(Number(e.target.value))}>
            {DURATIONS.map((d, i) => (<option key={i} value={i}>{d.label}</option>))}
          </select>
          <button className="primary" onClick={create} disabled={busy}>Grant access</button>
        </div>
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
            <button onClick={() => revoke(g.id)} disabled={busy}>revoke</button>
          </div>
        ))}
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
  const claimUrl = useMemo(() => `${window.location.origin}${BASE_PATH}/?claim=${data.code}`, [data.code]);
  return (
    <Modal title="Link a phone" onClose={onClose}>
      <p className="muted" style={{ marginTop: 0 }}>
        Scan with your phone to sign in there — no MetaMask needed. Single-use, expires in <strong>{left}s</strong>.
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
  token, pools, onChanged, onOpen, setError,
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
      <h2 className="panel-title">Data pools (buckets)</h2>
      <p className="muted hint">
        A pool is a permissioned bucket: every member gets mutual read access to objects pooled into it. Add
        members with roles (owner/editor/viewer) and optional time limits.
      </p>
      <div className="row">
        <input type="text" placeholder="pool name" value={name} onChange={(e) => setName(e.target.value)} />
        <input type="text" placeholder="description (optional)" value={desc} onChange={(e) => setDesc(e.target.value)} style={{ flex: 1 }} />
        <button className="primary" onClick={create} disabled={busy || !name.trim()}>Create pool</button>
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
              <span>{p.member_count} members</span><span>·</span><span>{p.object_count} objects</span>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

/* ──────────────────────────── pool modal ──────────────────────────── */

function PoolModal({
  token, pool, me, onClose, onChanged, onDeleted, setError,
}: {
  token: string;
  pool: PoolDetail;
  me: string | null;
  onClose: () => void;
  onChanged: () => void;
  onDeleted: () => void;
  setError: (s: string) => void;
}) {
  const [addr, setAddr] = useState("");
  const [role, setRole] = useState<"viewer" | "editor">("viewer");
  const [ttlIdx, setTtlIdx] = useState(5);
  const [cid, setCid] = useState("");
  const [busy, setBusy] = useState(false);
  const canManage = pool.role === "owner" || pool.role === "editor";
  const isOwner = !!me && pool.owner === me.toLowerCase();

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
      <div className="row" style={{ justifyContent: "space-between" }}>
        <p className="muted" style={{ marginTop: 0 }}>{pool.description || "No description."} · you are <strong>{pool.role}</strong></p>
        {isOwner && (
          <button onClick={() => { if (confirm(`Delete pool “${pool.name}”? This removes all members + pooled objects.`)) run(async () => { await api.deletePool(token, pool.id); onDeleted(); }); }} disabled={busy}>
            delete pool
          </button>
        )}
      </div>
      <div className="modal-grid">
        <div>
          <h3 className="panel-title">Members ({pool.members.length})</h3>
          {pool.members.map((m) => (
            <div key={m.address} className="row grant-row">
              <span className="muted">{shortAddress(m.address)}</span>
              <span className={`pill ${m.role === "owner" ? "admin" : ""}`}>{m.role}</span>
              <span className="muted">{m.expired ? "expired" : fmtDuration(m.expires_in)}</span>
              {canManage && m.role !== "owner" && (
                <button onClick={() => run(() => api.removeMember(token, pool.id, m.address))} disabled={busy}>remove</button>
              )}
            </div>
          ))}
          {canManage && (
            <div className="col" style={{ marginTop: 12 }}>
              <input type="text" placeholder="member 0x address" value={addr} onChange={(e) => setAddr(e.target.value)} />
              <div className="row">
                {isOwner && (
                  <select value={role} onChange={(e) => setRole(e.target.value as never)}>
                    <option value="viewer">viewer</option>
                    <option value="editor">editor</option>
                  </select>
                )}
                <select value={ttlIdx} onChange={(e) => setTtlIdx(Number(e.target.value))}>
                  {DURATIONS.map((d, i) => (<option key={i} value={i}>{d.label}</option>))}
                </select>
                <button className="primary" disabled={busy || !addr.trim()} onClick={() => run(async () => { await api.addMember(token, pool.id, { address: addr.trim(), role, ttl_seconds: DURATIONS[ttlIdx].ttl ?? undefined }); setAddr(""); })}>add</button>
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
              <a href={api.getUrl(o.cid, o.backend || undefined, token)} target="_blank" rel="noreferrer">open</a>
              {canManage && (<button onClick={() => run(() => api.removePoolObject(token, pool.id, o.cid))} disabled={busy}>remove</button>)}
            </div>
          ))}
          {canManage && (
            <div className="col" style={{ marginTop: 12 }}>
              <input type="text" placeholder="CID to pool (must be readable by you)" value={cid} onChange={(e) => setCid(e.target.value)} />
              <button className="primary" disabled={busy || !cid.trim()} onClick={() => run(async () => { await api.addPoolObject(token, pool.id, { cid: cid.trim() }); setCid(""); })}>add object</button>
            </div>
          )}
          {me && !isOwner && (
            <button style={{ marginTop: 16 }} onClick={() => run(async () => { await api.removeMember(token, pool.id, me); onClose(); })} disabled={busy}>Leave pool</button>
          )}
        </div>
      </div>
    </Modal>
  );
}

/* ─────────────────────── register external CID ─────────────────────── */

function RegisterExternal({
  token, onDone, setError, setSuccess,
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
      await api.registerExternal(token, { cid: cid.trim(), url: url.trim() || undefined, scheme: scheme.trim() || undefined, public: pub });
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
            The store is CID-agnostic: reference data living in another system so it becomes a first-class object
            you can share and pool. Provide a gateway URL to make it retrievable.
          </p>
          <input type="text" placeholder="cid / id (e.g. ar://… , bafy… , s3://…)" value={cid} onChange={(e) => setCid(e.target.value)} />
          <input type="text" placeholder="gateway url (optional, e.g. https://arweave.net/<tx>)" value={url} onChange={(e) => setUrl(e.target.value)} />
          <div className="row">
            <input type="text" placeholder="scheme (auto-detected if blank)" value={scheme} onChange={(e) => setScheme(e.target.value)} />
            <label className="check"><input type="checkbox" checked={pub} onChange={(e) => setPub(e.target.checked)} /> public</label>
            <button className="primary" onClick={submit} disabled={busy || !cid.trim()}>Register</button>
          </div>
        </div>
      )}
    </div>
  );
}

/* ──────────────────────────── modal shell ──────────────────────────── */

/* ──────────────────────────── terms body (mini markdown) ──────────────────────────── */

function boldSpans(line: string): React.ReactNode {
  // **bold** only — all the terms document needs.
  return line.split("**").map((seg, i) => (i % 2 ? <strong key={i}>{seg}</strong> : seg));
}

function TermsBody({ text }: { text: string }) {
  const blocks: React.ReactNode[] = [];
  let list: string[] = [];
  let para: string[] = [];
  const flush = () => {
    if (list.length) {
      blocks.push(<ul key={blocks.length}>{list.map((li, i) => <li key={i}>{boldSpans(li)}</li>)}</ul>);
      list = [];
    }
    if (para.length) {
      blocks.push(<p key={blocks.length}>{boldSpans(para.join(" "))}</p>);
      para = [];
    }
  };
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line) { flush(); continue; }
    if (line.startsWith("## ")) { flush(); blocks.push(<h2 key={blocks.length}>{line.slice(3)}</h2>); continue; }
    if (line.startsWith("# ")) { flush(); blocks.push(<h1 key={blocks.length}>{line.slice(2)}</h1>); continue; }
    if (line.startsWith("- ")) { if (para.length) flush(); list.push(line.slice(2)); continue; }
    if (list.length) { list[list.length - 1] += ` ${line}`; continue; } // wrapped bullet
    para.push(line);
  }
  flush();
  return <>{blocks}</>;
}

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
