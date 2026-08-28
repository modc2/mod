"use client";

/**
 * Object detail page — every piece of data in the store gets a permanent,
 * shareable URL: /o/<cid>. Works signed out for public objects; private ones
 * use the session token persisted by the main page.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { QRCodeSVG } from "qrcode.react";
import { api, ApiError, MarketListing, ObjectInfo, Preview } from "@/lib/api";
import { storageGet } from "@/lib/safeStorage";
import { shortAddress } from "@/lib/wallet";
import { errorText, fmtAgo, fmtBytes, fmtDate, fmtDuration, identiconStyle, shortCid } from "@/lib/format";
import { SubHeader } from "@/components/SubHeader";

const TOKEN_KEY = "store:token";

export default function ObjectPage() {
  const params = useParams<{ cid: string }>();
  const cid = decodeURIComponent(String(params.cid ?? ""));

  const [token, setToken] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [info, setInfo] = useState<ObjectInfo | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [listing, setListing] = useState<MarketListing | null>(null);
  const [gated, setGated] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Session is optional here: public objects render for anyone with the link.
  useEffect(() => {
    setToken(storageGet(TOKEN_KEY));
    setReady(true);
  }, []);

  const load = useCallback(async () => {
    if (!cid) return;
    setGated(false);
    setError(null);
    const auth = storageGet(TOKEN_KEY);
    // The three sources are independent — a private object can still show its
    // market listing, and a listed-but-unreadable one still gets a page.
    const [infoR, pvR, mkR] = await Promise.allSettled([
      api.objectInfo(auth ?? "", cid),
      api.preview(auth ?? "", cid),
      api.market({ q: cid }, auth),
    ]);
    if (infoR.status === "fulfilled") setInfo(infoR.value);
    if (pvR.status === "fulfilled") setPreview(pvR.value);
    if (mkR.status === "fulfilled") {
      setListing(mkR.value.listings.find((l) => l.cid === cid) ?? null);
    }
    if (infoR.status === "rejected" && pvR.status === "rejected") {
      const e = infoR.reason;
      if (e instanceof ApiError && (e.status === 401 || e.status === 403)) setGated(true);
      else setError(errorText(e));
    }
  }, [cid]);

  useEffect(() => {
    if (ready) load();
  }, [ready, load]);

  const copy = async (s: string, tag: string) => {
    try {
      await navigator.clipboard.writeText(s);
      setCopied(tag);
      setTimeout(() => setCopied((c) => (c === tag ? null : c)), 1500);
    } catch {
      /* ignore */
    }
  };

  const acquire = async () => {
    if (!token || !listing) return;
    setBusy(true);
    setError(null);
    try {
      await api.marketAcquire(token, cid);
      setNotice(listing.price_bloc > 0 ? "unlocked — your BlocTime is the ticket 🎫" : "it's yours ⚡");
      await load();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const copyAll = async () => {
    let full = preview?.text || "";
    if (preview?.truncated) {
      try {
        const res = await fetch(downloadUrl);
        full = await res.text();
      } catch (e) {
        setError(errorText(e));
        return;
      }
    }
    copy(full, "all");
  };

  const like = async () => {
    if (!token || !listing) return;
    try {
      const r = await api.marketLike(token, cid);
      setListing((l) => (l ? { ...l, liked: r.liked, likes: r.likes } : l));
    } catch (e) {
      setError(errorText(e));
    }
  };

  const priv = info?.visibility === "private" || (listing?.visibility === "private" && !info);
  const canRead = !!preview || !!info?.you_can_read || !!info?.is_owner;
  const name = info?.key || listing?.title || shortCid(cid);
  const backend = info?.backends[0] || undefined;
  const downloadUrl = api.getUrl(cid, backend, priv ? token : null);
  const pageUrl = useMemo(
    () => (typeof window === "undefined" ? "" : window.location.href),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [cid, ready]
  );
  const looksImage =
    !!(info?.key || listing?.key || "").match(/\.(png|jpe?g|gif|webp|svg|bmp)$/i) &&
    preview &&
    !preview.is_text;

  const hasGraph = !!(info && (info.links.out.length > 0 || info.links.in.length > 0));

  return (
    <div className="detail-wrap">
      <SubHeader />

      <nav className="detail-crumbs">
        <Link href="/">store</Link>
        <span>/</span>
        <span>object</span>
        <span>/</span>
        <span className="mono">{shortCid(cid)}</span>
      </nav>

      {error && (
        <div className="error-box">
          <span className="box-msg">✕ {error}</span>
          <button className="box-close" onClick={() => setError(null)} title="Dismiss" aria-label="Dismiss">✕</button>
        </div>
      )}
      {notice && <div className="success-box">✓ {notice}</div>}

      {gated && !listing && (
        <div className="gate-card">
          <div className="gate-glow" />
          <h2>🔒 Private object</h2>
          <p className="muted">
            This object exists but your {token ? "account doesn't have" : "browser isn't signed in for"} read
            access. <Link href="/">Sign in on the main page</Link> with an authorized wallet, or ask the owner
            for a share grant.
          </p>
        </div>
      )}

      {!gated && !info && !preview && !listing && !error && (
        <div className="panel"><p className="muted">loading object…</p></div>
      )}

      {(info || preview || listing) && (
        <>
          <div className="detail-hero">
            <h1 className="detail-name">{name}</h1>
            <div className="detail-pills">
              {info?.backends.map((b) => <span key={b} className={`pill ${b}`}>{b}</span>)}
              {info?.scheme && info.scheme !== "ipfs" && <span className="pill">{info.scheme}</span>}
              {(info || listing) && (
                <span className={`pill ${priv ? "private" : "public"}`}>{priv ? "🔒 private" : "🌐 public"}</span>
              )}
              {info?.pinned && <span className="pill">📌 pinned</span>}
              {info?.is_owner && <span className="pill admin">yours</span>}
              {listing && (
                <span className={`price-badge ${listing.price_bloc <= 0 ? "free" : "bloc"}`}>
                  {listing.price_bloc <= 0 ? "FREE" : `⏱ ${listing.price_bloc} BLOC`}
                </span>
              )}
            </div>
            <div className="detail-cid-row">
              <button className="cid-btn" title="Copy CID" onClick={() => copy(cid, "cid")}>
                <span className="cid">{cid}</span>
                <span className="muted"> {copied === "cid" ? "✓ copied" : "⧉"}</span>
              </button>
            </div>
            <div className="detail-actions">
              {canRead && <a className="btn-link" href={downloadUrl} target="_blank" rel="noreferrer">⇣ download</a>}
              <button onClick={() => copy(pageUrl, "url")}>{copied === "url" ? "✓ copied" : "copy page link"}</button>
              {listing && token && !listing.can_read && (
                <button className={`primary ${listing.price_bloc > 0 ? "unlock" : ""}`} onClick={acquire} disabled={busy}>
                  {busy ? "…" : listing.price_bloc <= 0 ? "⚡ Get it" : `🔓 Unlock · ${listing.price_bloc} BLOC`}
                </button>
              )}
              {listing && (
                <button className={`like-btn ${listing.liked ? "liked" : ""}`} onClick={like} disabled={!token}
                  title={token ? (listing.liked ? "unlike" : "like") : "sign in on the main page to like"}>
                  {listing.liked ? "♥" : "♡"} {listing.likes}
                </button>
              )}
            </div>
          </div>

          <div className="detail-cols">
            <div className="panel">
              <h2 className="panel-title">Details</h2>
              <div className="info-grid">
                <span className="muted">Owner</span>
                <span className="mono">
                  {info?.owner ? shortAddress(info.owner) : listing ? shortAddress(listing.seller) : "—"}
                </span>
                <span className="muted">Stored</span><span>{fmtDate(info?.stored_at)}</span>
                <span className="muted">Size</span><span>{fmtBytes(info?.size ?? listing?.size)}</span>
                <span className="muted">Backends</span><span>{info?.backends.join(", ") || "—"}</span>
                <span className="muted">Scheme</span><span>{info?.scheme ?? listing?.scheme ?? "—"}</span>
                {info?.semhash && (
                  <>
                    <span className="muted">Semantic hash</span>
                    <span className="mono" style={{ wordBreak: "break-all" }}>{info.semhash}</span>
                  </>
                )}
                {info?.external_url && (
                  <>
                    <span className="muted">External</span>
                    <span><a href={info.external_url} target="_blank" rel="noreferrer">{info.external_url}</a></span>
                  </>
                )}
              </div>
            </div>

            <div className="panel">
              <h2 className="panel-title">Share this page</h2>
              <div className="row" style={{ alignItems: "flex-start" }}>
                {pageUrl && (
                  <span className="qr" title="Scan to open this object on another device">
                    <QRCodeSVG value={pageUrl} size={92} level="M" />
                  </span>
                )}
                <p className="muted" style={{ margin: 0, flex: 1, minWidth: 160, fontSize: 12.5 }}>
                  Anyone with the link lands right here.{" "}
                  {priv
                    ? "It's private — they'll need their own access (grant, pool, or market unlock)."
                    : "It's public — the content is readable by anyone."}
                </p>
              </div>
            </div>

            {listing && (
              <div className="panel detail-full">
                <h2 className="panel-title">On the market</h2>
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <div style={{ flex: 1, minWidth: 220 }}>
                    <strong>{listing.title}</strong>
                    {listing.description && <p className="muted" style={{ margin: "6px 0" }}>{listing.description}</p>}
                    <div className="mk-tags" style={{ marginTop: 6 }}>
                      {listing.tags.map((t) => <span key={t} className="mk-tag">#{t}</span>)}
                    </div>
                  </div>
                  <div className="col" style={{ gap: 6, alignItems: "flex-end" }}>
                    <span className="mk-seller" style={{ cursor: "default" }}>
                      <span className="mk-avatar" style={identiconStyle(listing.seller)} />
                      {shortAddress(listing.seller)}
                    </span>
                    <span className="muted" style={{ fontSize: 12 }}>
                      ⇣ {listing.downloads} · listed {fmtAgo(listing.created)}
                    </span>
                  </div>
                </div>
              </div>
            )}

            {(preview || canRead) && (
              <div className="panel detail-full">
                <h2 className="panel-title">Content</h2>
                {!preview && <p className="muted">loading preview…</p>}
                {preview?.external && (
                  <p className="muted">
                    External object — <a href={preview.url || "#"} target="_blank" rel="noreferrer">open at its gateway ↗</a>
                  </p>
                )}
                {preview && !preview.external && (
                  <>
                    <div className="row" style={{ justifyContent: "space-between", margin: "0 0 4px" }}>
                      <span className="muted" style={{ fontSize: 12 }}>
                        {fmtBytes(preview.size)} {preview.is_text ? "· text" : "· binary"}
                        {preview.truncated ? " · truncated preview" : ""}
                      </span>
                      {preview.is_text && (
                        <button className="primary" onClick={copyAll}>
                          {copied === "all" ? "✓ copied" : preview.truncated ? "copy all (full)" : "copy all"}
                        </button>
                      )}
                    </div>
                    {preview.is_text && (
                      <pre className="content-view" style={{ marginTop: 8 }}>
                        {preview.text}
                        {preview.truncated ? "\n\n… (truncated — use “copy all” for the full content)" : ""}
                      </pre>
                    )}
                    {looksImage && (
                      /* eslint-disable-next-line @next/next/no-img-element */
                      <div className="img-wrap"><img src={downloadUrl} alt={cid} /></div>
                    )}
                    {!preview.is_text && !looksImage && (
                      <p className="muted">
                        Binary content ({fmtBytes(preview.size)}). <a href={downloadUrl} target="_blank" rel="noreferrer">download ↗</a>
                      </p>
                    )}
                  </>
                )}
              </div>
            )}

            {hasGraph && info && (
              <div className="panel detail-full">
                <h2 className="panel-title">Linked objects</h2>
                <p className="muted" style={{ margin: "0 0 10px", fontSize: 12.5 }}>
                  CID references detected in stored content — each link has its own page.
                </p>
                <div className="link-chip-list">
                  {info.links.out.map((n) => (
                    <Link key={`out-${n.cid}`} href={`/o/${encodeURIComponent(n.cid)}`} className="link-chip">
                      <span className="lc-name">{n.key || shortCid(n.cid)}</span>
                      <span className="lc-dir">referenced by this object →</span>
                    </Link>
                  ))}
                  {info.links.in.map((n) => (
                    <Link key={`in-${n.cid}`} href={`/o/${encodeURIComponent(n.cid)}`} className="link-chip">
                      <span className="lc-name">{n.key || shortCid(n.cid)}</span>
                      <span className="lc-dir">← references this object</span>
                    </Link>
                  ))}
                </div>
              </div>
            )}

            {info?.is_owner && (
              <div className="panel detail-full">
                <h2 className="panel-title">Who has access</h2>
                {info.grants.length === 0 && info.pools.length === 0 && (
                  <p className="muted">
                    {info.visibility === "public"
                      ? "Public — anyone can read."
                      : "Only you. Share it or add it to a pool from the main page to grant access."}
                  </p>
                )}
                {info.grants.map((g) => (
                  <div key={g.id} className="row grant-row">
                    <span className="pill">grant</span>
                    <span className="muted mono">{shortAddress(g.grantee)}</span>
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
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
