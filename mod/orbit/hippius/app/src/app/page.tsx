'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Creds, DEFAULT_ENDPOINT, DEFAULT_REGION, ENDPOINTS,
  api, apiBase, upload, downloadObject,
  loadCreds, saveCreds, clearCreds, fmtBytes, fmtDate,
} from '@/lib/api'

type Bucket = { name: string; created: string | null; public?: boolean }
type Obj = { key: string; size: number; modified: string | null; etag: string }
type Toast = { msg: string; error?: boolean }

export default function Page() {
  const [ready, setReady] = useState(false)
  const [creds, setCreds] = useState<Creds | null>(null)
  const [netUp, setNetUp] = useState<boolean | null>(null)

  useEffect(() => {
    setCreds(loadCreds())
    setReady(true)
    api(null, '/status')
      .then((s) => setNetUp(!!s.reachable))
      .catch(() => setNetUp(false))
  }, [])

  if (!ready) return null

  return (
    <div className="shell">
      <header className="topbar">
        <div className="logo">
          <div className="logo-mark">H</div>
          HIPPIUS
        </div>
        <span className="tag">BYOK STORAGE CONSOLE</span>
        <div className="topbar-right">
          <span className="net-label">
            <span className={`net-dot ${netUp === null ? 'unknown' : netUp ? 'up' : 'down'}`} />
            {netUp === null ? 'probing network' : netUp ? 'network up' : 'network unreachable'}
          </span>
        </div>
      </header>

      {creds
        ? <Workspace creds={creds} onSignOut={() => { clearCreds(); setCreds(null) }} onUpdateCreds={setCreds} />
        : <Onboard onSaved={setCreds} />}

      <p className="footer-note">
        Your access key and secret live only in this browser (localStorage) and are attached
        per-request — the API signs each S3 call and forgets them.
        <br />
        Hippius speaks standard S3: the same keys work with aws-cli, boto3 and rclone.
        {' '}<a href="https://docs.hippius.com/storage/s3/integration" target="_blank" rel="noreferrer">S3 docs</a>
        {' · '}<a href="https://console.hippius.com" target="_blank" rel="noreferrer">get keys</a>
      </p>
    </div>
  )
}

/* ── Onboarding / key entry ─────────────────────────────────────────── */

function Onboard({ onSaved }: { onSaved: (c: Creds) => void }) {
  const [key, setKey] = useState('')
  const [secret, setSecret] = useState('')
  const [endpoint, setEndpoint] = useState(DEFAULT_ENDPOINT)
  const [region, setRegion] = useState(DEFAULT_REGION)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const connect = async () => {
    setErr(null)
    const c: Creds = { key: key.trim(), secret: secret.trim(), endpoint: endpoint.trim(), region: region.trim() }
    if (!c.key || !c.secret) { setErr('Access key and secret are required.'); return }
    setBusy(true)
    try {
      const v = await api<{ buckets: number }>(c, '/verify', { method: 'POST' })
      if (!saveCreds(c)) {
        setErr('Verified, but browser storage is unavailable — keys will last this session only.')
      }
      onSaved(c)
    } catch (e: any) {
      setErr(`Verification failed: ${e.message || e}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="onboard">
      <h1>Connect your Hippius keys</h1>
      <p className="sub">
        Hippius is decentralized S3-compatible storage — files are erasure-coded into shards
        and spread across miners, encrypted before they leave the gateway. Bring your own
        S3 access keys from <a href="https://console.hippius.com" target="_blank" rel="noreferrer">console.hippius.com</a> to
        browse buckets, upload, and share.
      </p>
      <div className="byok-note">
        <span>🔑</span>
        <span>
          <b>Bring your own key.</b> Credentials are stored only in your browser and sent
          per-request to sign S3 calls. Nothing is written server-side — clearing your
          browser storage removes them completely.
        </span>
      </div>
      <div className="card">
        {err && <div className="err">{err}</div>}
        <div className="field">
          <label>Access key ID</label>
          <input className="mono" value={key} onChange={(e) => setKey(e.target.value)}
            placeholder="your Hippius access key" autoComplete="off" />
        </div>
        <div className="field">
          <label>Secret access key</label>
          <input className="mono" type="password" value={secret} onChange={(e) => setSecret(e.target.value)}
            placeholder="your Hippius secret key" autoComplete="off" />
        </div>
        <div className="row2">
          <div className="field">
            <label>Endpoint</label>
            <select value={endpoint} onChange={(e) => setEndpoint(e.target.value)}>
              {ENDPOINTS.map((u) => <option key={u} value={u}>{u.replace('https://', '')}</option>)}
            </select>
          </div>
          <div className="field">
            <label>Region</label>
            <input className="mono" value={region} onChange={(e) => setRegion(e.target.value)} />
          </div>
        </div>
        <button className="btn primary" style={{ width: '100%', marginTop: 4 }} disabled={busy} onClick={connect}>
          {busy ? 'Verifying…' : 'Verify & connect'}
        </button>
      </div>
    </div>
  )
}

/* ── Workspace ──────────────────────────────────────────────────────── */

function Workspace({
  creds, onSignOut, onUpdateCreds,
}: {
  creds: Creds
  onSignOut: () => void
  onUpdateCreds: (c: Creds) => void
}) {
  const [buckets, setBuckets] = useState<Bucket[]>([])
  const [active, setActive] = useState<string | null>(null)
  const [objects, setObjects] = useState<Obj[]>([])
  const [nextToken, setNextToken] = useState<string | null>(null)
  const [prefix, setPrefix] = useState('')
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [toast, setToast] = useState<Toast | null>(null)
  const [uploading, setUploading] = useState<string | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const [meta, setMeta] = useState<any | null>(null)
  const [showNewBucket, setShowNewBucket] = useState(false)
  const [bucketPublic, setBucketPublic] = useState<boolean | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const toastTimer = useRef<ReturnType<typeof setTimeout>>()

  const notify = (msg: string, error = false) => {
    setToast({ msg, error })
    clearTimeout(toastTimer.current)
    toastTimer.current = setTimeout(() => setToast(null), 3500)
  }

  const loadBuckets = useCallback(async () => {
    try {
      const r = await api<{ buckets: Bucket[] }>(creds, '/buckets')
      setBuckets(r.buckets)
      setErr(null)
      if (r.buckets.length && !active) setActive(r.buckets[0].name)
      if (!r.buckets.length) setActive(null)
    } catch (e: any) {
      setErr(`Failed to list buckets: ${e.message}`)
      if (e.status === 401 || e.status === 403) onSignOut()
    }
  }, [creds, active, onSignOut])

  const loadObjects = useCallback(async (bucket: string, token?: string | null, pfx?: string) => {
    setLoading(true)
    try {
      const q = new URLSearchParams()
      if (pfx) q.set('prefix', pfx)
      if (token) q.set('token', token)
      const r = await api<{ objects: Obj[]; next_token: string | null }>(
        creds, `/buckets/${encodeURIComponent(bucket)}/objects?${q}`)
      setObjects((prev) => (token ? [...prev, ...r.objects] : r.objects))
      setNextToken(r.next_token)
      setErr(null)
    } catch (e: any) {
      setErr(`Failed to list objects in ${bucket}: ${e.message}`)
      setObjects([])
    } finally {
      setLoading(false)
    }
  }, [creds])

  const loadPolicy = useCallback(async (bucket: string) => {
    setBucketPublic(null)
    try {
      const r = await api<{ public: boolean }>(creds, `/buckets/${encodeURIComponent(bucket)}/policy`)
      setBucketPublic(r.public)
    } catch {
      setBucketPublic(null)
    }
  }, [creds])

  useEffect(() => { loadBuckets() }, [])   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (active) { setObjects([]); setNextToken(null); loadObjects(active, null, prefix); loadPolicy(active) }
  }, [active])                              // eslint-disable-line react-hooks/exhaustive-deps

  const doUpload = async (files: FileList | File[]) => {
    if (!active) { notify('Create or select a bucket first', true); return }
    for (const f of Array.from(files)) {
      setUploading(f.name)
      try {
        const r = await upload(creds, active, f)
        notify(`Uploaded ${r.key}${r.cid ? ` · CID ${r.cid.slice(0, 14)}…` : ''}`)
      } catch (e: any) {
        notify(`Upload failed: ${e.message}`, true)
      }
    }
    setUploading(null)
    loadObjects(active, null, prefix)
  }

  const doDelete = async (key: string) => {
    if (!active) return
    if (!confirm(`Delete ${key} from ${active}?`)) return
    try {
      await api(creds, `/buckets/${encodeURIComponent(active)}/objects?key=${encodeURIComponent(key)}`, { method: 'DELETE' })
      setObjects((os) => os.filter((o) => o.key !== key))
      notify(`Deleted ${key}`)
    } catch (e: any) {
      notify(`Delete failed: ${e.message}`, true)
    }
  }

  const copyLink = async (key: string) => {
    if (!active) return
    try {
      if (bucketPublic) {
        const url = `${creds.endpoint}/${active}/${key}`
        await navigator.clipboard.writeText(url)
        notify('Public URL copied')
      } else {
        const r = await api<{ url: string }>(
          creds, `/buckets/${encodeURIComponent(active)}/presign?key=${encodeURIComponent(key)}&op=get&expires=86400`)
        await navigator.clipboard.writeText(r.url)
        notify('Presigned URL copied (valid 24h)')
      }
    } catch (e: any) {
      notify(`Copy failed: ${e.message}`, true)
    }
  }

  const showMeta = async (key: string) => {
    if (!active) return
    try {
      const r = await api(creds, `/buckets/${encodeURIComponent(active)}/objects/meta?key=${encodeURIComponent(key)}`)
      setMeta(r)
    } catch (e: any) {
      notify(`Metadata failed: ${e.message}`, true)
    }
  }

  const togglePublic = async () => {
    if (!active || bucketPublic === null) return
    const next = !bucketPublic
    try {
      await api(creds, `/buckets/${encodeURIComponent(active)}/policy`, {
        method: 'PUT',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ public: next }),
      })
      setBucketPublic(next)
      notify(next ? `${active} is now public — objects readable at ${creds.endpoint}/${active}/…` : `${active} is now private`)
    } catch (e: any) {
      notify(`Policy change failed: ${e.message}`, true)
    }
  }

  const deleteBucket = async () => {
    if (!active) return
    if (!confirm(`Delete bucket ${active}? It must be empty.`)) return
    try {
      await api(creds, `/buckets/${encodeURIComponent(active)}`, { method: 'DELETE' })
      notify(`Bucket ${active} deleted`)
      setActive(null)
      loadBuckets()
    } catch (e: any) {
      notify(`Delete failed: ${e.message}`, true)
    }
  }

  return (
    <>
      <div className="workspace">
        {/* bucket rail */}
        <div className="card">
          <h2>Buckets</h2>
          {buckets.map((b) => (
            <button key={b.name} className={`bucket-item ${active === b.name ? 'active' : ''}`}
              onClick={() => setActive(b.name)}>
              <span className="b-ic">▣</span>
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{b.name}</span>
            </button>
          ))}
          {!buckets.length && <div className="empty" style={{ padding: '18px 0' }}>no buckets yet</div>}
          <button className="btn sm" style={{ width: '100%', marginTop: 10 }} onClick={() => setShowNewBucket(true)}>
            + new bucket
          </button>
          <div style={{ borderTop: '1px solid var(--line-soft)', marginTop: 16, paddingTop: 12 }}>
            <div className="keyline" title={creds.endpoint}>
              {creds.key.length > 14 ? `${creds.key.slice(0, 8)}…${creds.key.slice(-4)}` : creds.key}
            </div>
            <div className="keyline" style={{ marginBottom: 8 }}>{creds.endpoint.replace('https://', '')}</div>
            <button className="btn ghost sm" onClick={onSignOut}>disconnect keys</button>
          </div>
        </div>

        {/* objects pane */}
        <div className="card">
          {err && <div className="err">{err}</div>}
          {active ? (
            <>
              <div className="obj-head">
                <h3>{active}</h3>
                {bucketPublic !== null && (
                  <button className="btn sm" onClick={togglePublic}
                    style={bucketPublic ? { color: 'var(--accent)', borderColor: 'var(--accent)' } : {}}>
                    {bucketPublic ? 'public' : 'private'}
                  </button>
                )}
                <button className="btn ghost sm" onClick={deleteBucket} title="delete bucket">delete</button>
                <div className="spacer" />
                <input className="search" placeholder="filter by prefix…" value={prefix}
                  onChange={(e) => setPrefix(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter' && active) { setObjects([]); loadObjects(active, null, prefix) } }} />
                <button className="btn sm" onClick={() => active && loadObjects(active, null, prefix)}>↻</button>
              </div>

              <div
                className={`dropzone ${dragOver ? 'over' : ''}`}
                onClick={() => fileInput.current?.click()}
                onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
                onDragLeave={() => setDragOver(false)}
                onDrop={(e) => { e.preventDefault(); setDragOver(false); doUpload(e.dataTransfer.files) }}
              >
                {uploading
                  ? <>uploading <b>{uploading}</b>…<div className="progress-track"><div className="progress-fill" style={{ width: '70%' }} /></div></>
                  : <>drop files here or <b>click to upload</b> → erasure-coded across the Hippius miner network</>}
                <input ref={fileInput} type="file" multiple hidden
                  onChange={(e) => { if (e.target.files?.length) doUpload(e.target.files); e.target.value = '' }} />
              </div>

              {objects.length > 0 && (
                <table className="objects">
                  <thead>
                    <tr>
                      <th>Key</th><th style={{ width: 90 }}>Size</th>
                      <th style={{ width: 130 }}>Modified</th><th style={{ width: 130 }} />
                    </tr>
                  </thead>
                  <tbody>
                    {objects.map((o) => (
                      <tr key={o.key}>
                        <td className="okey">{o.key}</td>
                        <td className="dim">{fmtBytes(o.size)}</td>
                        <td className="dim">{fmtDate(o.modified)}</td>
                        <td>
                          <div className="actions">
                            <button className="icon-btn" title="download" onClick={() => downloadObject(creds, active, o.key)}>⭳</button>
                            <button className="icon-btn" title={bucketPublic ? 'copy public URL' : 'copy presigned URL (24h)'} onClick={() => copyLink(o.key)}>🔗</button>
                            <button className="icon-btn" title="details / CID" onClick={() => showMeta(o.key)}>ⓘ</button>
                            <button className="icon-btn danger" title="delete" onClick={() => doDelete(o.key)}>✕</button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {!objects.length && !loading && <div className="empty">bucket is empty — drop a file above</div>}
              {loading && <div className="empty">loading…</div>}
              {nextToken && (
                <button className="btn sm" style={{ marginTop: 12 }} onClick={() => loadObjects(active, nextToken, prefix)}>
                  load more
                </button>
              )}
            </>
          ) : (
            <div className="empty">
              create a bucket to start storing files
              <div style={{ marginTop: 14 }}>
                <button className="btn primary sm" onClick={() => setShowNewBucket(true)}>+ new bucket</button>
              </div>
            </div>
          )}
        </div>
      </div>

      {showNewBucket && (
        <NewBucketModal
          creds={creds}
          onClose={() => setShowNewBucket(false)}
          onCreated={(name) => { setShowNewBucket(false); notify(`Bucket ${name} created`); loadBuckets().then(() => setActive(name)) }}
          onError={(m) => notify(m, true)}
        />
      )}

      {meta && (
        <div className="modal-back" onClick={() => setMeta(null)}>
          <div className="card modal" onClick={(e) => e.stopPropagation()}>
            <h2>Object details</h2>
            <div className="meta-grid">
              <span className="k">Key</span><span className="v">{meta.key}</span>
              <span className="k">Size</span><span className="v">{fmtBytes(meta.size)}</span>
              <span className="k">Type</span><span className="v">{meta.content_type || '—'}</span>
              <span className="k">Modified</span><span className="v">{fmtDate(meta.modified)}</span>
              <span className="k">ETag</span><span className="v">{meta.etag || '—'}</span>
              {meta.cid && (<><span className="k">CID</span><span className="v" style={{ color: 'var(--accent)' }}>{meta.cid}</span></>)}
              {Object.entries(meta.metadata || {}).filter(([k]) => !['cid', 'ipfs-cid'].includes(k)).map(([k, v]) => (
                <span key={`kv-${k}`} style={{ display: 'contents' }}>
                  <span className="k">{k}</span><span className="v">{String(v)}</span>
                </span>
              ))}
              <span className="k">URL</span><span className="v">{meta.public_url}</span>
            </div>
            <button className="btn sm" style={{ marginTop: 16 }} onClick={() => setMeta(null)}>close</button>
          </div>
        </div>
      )}

      {toast && <div className={`toast ${toast.error ? 'error' : ''}`}>{toast.msg}</div>}
    </>
  )
}

/* ── New bucket modal ───────────────────────────────────────────────── */

function NewBucketModal({
  creds, onClose, onCreated, onError,
}: {
  creds: Creds
  onClose: () => void
  onCreated: (name: string) => void
  onError: (msg: string) => void
}) {
  const [name, setName] = useState('')
  const [pub, setPub] = useState(false)
  const [busy, setBusy] = useState(false)

  const create = async () => {
    const n = name.trim().toLowerCase()
    if (!n) return
    setBusy(true)
    try {
      await api(creds, '/buckets', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ name: n, public: pub }),
      })
      onCreated(n)
    } catch (e: any) {
      onError(`Create failed: ${e.message}`)
      setBusy(false)
    }
  }

  return (
    <div className="modal-back" onClick={onClose}>
      <div className="card modal" onClick={(e) => e.stopPropagation()}>
        <h2>New bucket</h2>
        <div className="field">
          <label>Bucket name</label>
          <input className="mono" value={name} onChange={(e) => setName(e.target.value)}
            placeholder="my-bucket" autoFocus
            onKeyDown={(e) => { if (e.key === 'Enter') create() }} />
        </div>
        <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13, color: 'var(--text-dim)', marginBottom: 16, cursor: 'pointer' }}>
          <input type="checkbox" checked={pub} onChange={(e) => setPub(e.target.checked)} />
          public read — objects served at {creds.endpoint.replace('https://', '')}/&lt;bucket&gt;/&lt;key&gt;
        </label>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn primary" disabled={busy || !name.trim()} onClick={create}>
            {busy ? 'creating…' : 'create'}
          </button>
          <button className="btn" onClick={onClose}>cancel</button>
        </div>
      </div>
    </div>
  )
}
