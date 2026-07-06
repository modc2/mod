'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

type BridgeOk<T> = { ok: true; data: T }
type BridgeErr = { ok: false; error: string; trace?: string }
type BridgeResult<T> = BridgeOk<T> | BridgeErr

async function call<T = unknown>(method: string, args: Record<string, unknown> = {}): Promise<BridgeResult<T>> {
  const res = await fetch('/api/localfs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ method, args }),
  })
  return (await res.json()) as BridgeResult<T>
}

function bytesHuman(n: number): string {
  if (!Number.isFinite(n)) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let v = n
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`
}

type Tab = 'store' | 'retrieve' | 'pins' | 'stats'

export default function Page() {
  const [tab, setTab] = useState<Tab>('store')
  const [stats, setStats] = useState<{ blocks: number; pinned: number; total_size: number; storage_path: string } | null>(null)
  const [toast, setToast] = useState<{ kind: 'ok' | 'err'; msg: string } | null>(null)

  const flash = useCallback((kind: 'ok' | 'err', msg: string) => {
    setToast({ kind, msg })
    setTimeout(() => setToast(null), 2800)
  }, [])

  const refreshStats = useCallback(async () => {
    const r = await call<{ blocks: number; pinned: number; total_size: number; storage_path: string }>('stats')
    if (r.ok) setStats(r.data)
  }, [])

  useEffect(() => {
    refreshStats()
    const id = setInterval(refreshStats, 8000)
    return () => clearInterval(id)
  }, [refreshStats])

  return (
    <main style={{ padding: '32px 24px', maxWidth: 1100, margin: '0 auto' }}>
      <header style={{ marginBottom: 28 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 16, flexWrap: 'wrap' }}>
          <h1 style={{ fontSize: 22, color: '#6ee7b7', letterSpacing: '0.02em' }}>localfs</h1>
          <span style={{ color: '#6b7280', fontSize: 11 }}>content-addressable local storage</span>
        </div>
        {stats && (
          <div style={{ marginTop: 10, display: 'flex', gap: 18, fontSize: 11, color: '#6b7280', flexWrap: 'wrap' }}>
            <span>{stats.blocks.toLocaleString()} blocks</span>
            <span>{stats.pinned.toLocaleString()} pinned</span>
            <span>{bytesHuman(stats.total_size)}</span>
            <span style={{ opacity: 0.7 }}>{stats.storage_path}</span>
          </div>
        )}
      </header>

      <nav style={{ borderBottom: '1px solid #1f1f2e', marginBottom: 24, display: 'flex', gap: 4 }}>
        {(['store', 'retrieve', 'pins', 'stats'] as Tab[]).map((t) => (
          <button key={t} className={`tab ${tab === t ? 'active' : ''}`} onClick={() => setTab(t)}>
            {t}
          </button>
        ))}
      </nav>

      {tab === 'store' && <StoreTab flash={flash} onChanged={refreshStats} />}
      {tab === 'retrieve' && <RetrieveTab flash={flash} />}
      {tab === 'pins' && <PinsTab flash={flash} onChanged={refreshStats} />}
      {tab === 'stats' && <StatsTab flash={flash} stats={stats} refresh={refreshStats} />}

      {toast && (
        <div
          style={{
            position: 'fixed',
            bottom: 24,
            right: 24,
            padding: '10px 14px',
            borderRadius: 6,
            background: toast.kind === 'ok' ? 'rgba(110,231,183,0.12)' : 'rgba(248,113,113,0.12)',
            border: `1px solid ${toast.kind === 'ok' ? '#6ee7b7' : '#f87171'}`,
            color: toast.kind === 'ok' ? '#6ee7b7' : '#f87171',
            fontSize: 12,
            maxWidth: 420,
          }}
        >
          {toast.msg}
        </div>
      )}
    </main>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Store tab — put text/JSON, upload files, dry-run CID
// ─────────────────────────────────────────────────────────────────────────────

function StoreTab({
  flash,
  onChanged,
}: {
  flash: (k: 'ok' | 'err', m: string) => void
  onChanged: () => void
}) {
  const [text, setText] = useState('')
  const [pin, setPin] = useState(true)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ cid: string; size?: number; name?: string } | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const [uploadInfo, setUploadInfo] = useState<{ name: string; size: number } | null>(null)

  const parseInput = useCallback((raw: string): unknown => {
    const trimmed = raw.trim()
    if (!trimmed) return ''
    if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
      try {
        return JSON.parse(trimmed)
      } catch {
        // fall through to plain text
      }
    }
    return raw
  }, [])

  const onPut = useCallback(async () => {
    setBusy(true)
    setResult(null)
    const data = parseInput(text)
    const r = await call<{ cid: string }>('put', { data, pin })
    setBusy(false)
    if (r.ok) {
      setResult({ cid: r.data.cid, size: new Blob([text]).size })
      flash('ok', `stored — ${r.data.cid}`)
      onChanged()
    } else {
      flash('err', r.error)
    }
  }, [text, pin, parseInput, flash, onChanged])

  const onUpload = useCallback(async () => {
    const f = fileRef.current?.files?.[0]
    if (!f) {
      flash('err', 'no file selected')
      return
    }
    setBusy(true)
    setResult(null)
    setUploadInfo({ name: f.name, size: f.size })
    const fd = new FormData()
    fd.append('file', f)
    fd.append('pin', String(pin))
    const res = await fetch('/api/upload', { method: 'POST', body: fd })
    const json = (await res.json()) as BridgeResult<{ cid: string; name: string; size: number }>
    setBusy(false)
    if (json.ok) {
      setResult(json.data)
      flash('ok', `uploaded ${json.data.name} — ${json.data.cid}`)
      onChanged()
    } else {
      flash('err', json.error)
    }
  }, [pin, flash, onChanged])

  return (
    <div style={{ display: 'grid', gap: 18 }}>
      <section className="card">
        <h2 style={{ fontSize: 13, color: '#e5e5e5', marginBottom: 14, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
          Put — text or JSON
        </h2>
        <label className="label">data</label>
        <textarea
          className="textarea"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder='hello world  /  {"key":"value"}'
          rows={6}
        />
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 12, flexWrap: 'wrap' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: '#6b7280' }}>
            <input type="checkbox" checked={pin} onChange={(e) => setPin(e.target.checked)} />
            pin
          </label>
          <button className="btn btn-primary" disabled={busy || !text} onClick={onPut}>
            {busy ? 'storing…' : 'put'}
          </button>
          <span style={{ fontSize: 11, color: '#6b7280' }}>
            JSON-shaped input is parsed. Plain text is stored as-is.
          </span>
        </div>
      </section>

      <section className="card">
        <h2 style={{ fontSize: 13, color: '#e5e5e5', marginBottom: 14, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
          Upload — file from disk
        </h2>
        <input ref={fileRef} type="file" className="input" onChange={() => setResult(null)} />
        {uploadInfo && (
          <div style={{ fontSize: 11, color: '#6b7280', marginTop: 8 }}>
            {uploadInfo.name} · {bytesHuman(uploadInfo.size)}
          </div>
        )}
        <div style={{ marginTop: 12 }}>
          <button className="btn btn-primary" disabled={busy} onClick={onUpload}>
            {busy ? 'uploading…' : 'upload'}
          </button>
        </div>
      </section>

      {result && (
        <section className="card glow">
          <div className="label">result cid</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <span className="cid">{result.cid}</span>
            <button
              className="btn"
              onClick={() => {
                navigator.clipboard.writeText(result.cid)
                flash('ok', 'copied')
              }}
            >
              copy
            </button>
          </div>
          {(result.size !== undefined || result.name) && (
            <div style={{ fontSize: 11, color: '#6b7280', marginTop: 10 }}>
              {result.name ? `${result.name} · ` : ''}
              {result.size !== undefined ? bytesHuman(result.size) : ''}
            </div>
          )}
        </section>
      )}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Retrieve tab — get/cat by CID, with rm
// ─────────────────────────────────────────────────────────────────────────────

type GetResult =
  | { kind: 'json'; data: unknown }
  | { kind: 'text'; data: string }
  | { kind: 'bytes'; data_b64: string }

function RetrieveTab({ flash }: { flash: (k: 'ok' | 'err', m: string) => void }) {
  const [cid, setCid] = useState('')
  const [busy, setBusy] = useState(false)
  const [out, setOut] = useState<GetResult | null>(null)
  const [rawSize, setRawSize] = useState<number | null>(null)

  const onGet = useCallback(async () => {
    if (!cid.trim()) return
    setBusy(true)
    setOut(null)
    setRawSize(null)
    const r = await call<GetResult>('get', { cid: cid.trim() })
    setBusy(false)
    if (r.ok) {
      setOut(r.data)
      flash('ok', 'fetched')
    } else {
      flash('err', r.error)
    }
  }, [cid, flash])

  const onCat = useCallback(async () => {
    if (!cid.trim()) return
    setBusy(true)
    setOut(null)
    const r = await call<{ data_b64: string; size: number }>('cat', { cid: cid.trim() })
    setBusy(false)
    if (r.ok) {
      setOut({ kind: 'bytes', data_b64: r.data.data_b64 })
      setRawSize(r.data.size)
      flash('ok', `${bytesHuman(r.data.size)} loaded`)
    } else {
      flash('err', r.error)
    }
  }, [cid, flash])

  const onRm = useCallback(async () => {
    if (!cid.trim()) return
    if (!confirm(`Remove ${cid}? Pinned blocks will not be GC'd until unpinned.`)) return
    setBusy(true)
    const r = await call<{ Status: string }>('rm', { cid: cid.trim() })
    setBusy(false)
    if (r.ok) {
      setOut(null)
      flash('ok', 'removed')
    } else {
      flash('err', r.error)
    }
  }, [cid, flash])

  const downloadBytes = useCallback(() => {
    if (!out || out.kind !== 'bytes') return
    const bin = atob(out.data_b64)
    const arr = new Uint8Array(bin.length)
    for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i)
    const blob = new Blob([arr])
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = cid.trim() || 'block.bin'
    a.click()
    URL.revokeObjectURL(url)
  }, [out, cid])

  return (
    <div style={{ display: 'grid', gap: 18 }}>
      <section className="card">
        <label className="label">cid</label>
        <input
          className="input"
          value={cid}
          onChange={(e) => setCid(e.target.value)}
          placeholder="Qm... or bafy..."
          onKeyDown={(e) => e.key === 'Enter' && onGet()}
        />
        <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
          <button className="btn btn-primary" disabled={busy || !cid.trim()} onClick={onGet}>
            get
          </button>
          <button className="btn" disabled={busy || !cid.trim()} onClick={onCat}>
            cat (raw bytes)
          </button>
          <button className="btn btn-danger" disabled={busy || !cid.trim()} onClick={onRm}>
            rm
          </button>
        </div>
      </section>

      {out && (
        <section className="card">
          <div className="label">{out.kind}</div>
          {out.kind === 'json' && (
            <pre
              style={{
                background: '#0a0a0f',
                border: '1px solid #1f1f2e',
                padding: 12,
                borderRadius: 4,
                fontSize: 11,
                overflow: 'auto',
                maxHeight: 400,
              }}
            >
              {JSON.stringify(out.data, null, 2)}
            </pre>
          )}
          {out.kind === 'text' && (
            <pre
              style={{
                background: '#0a0a0f',
                border: '1px solid #1f1f2e',
                padding: 12,
                borderRadius: 4,
                fontSize: 11,
                overflow: 'auto',
                maxHeight: 400,
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}
            >
              {String(out.data)}
            </pre>
          )}
          {out.kind === 'bytes' && (
            <div>
              <div style={{ fontSize: 11, color: '#6b7280', marginBottom: 10 }}>
                {rawSize !== null ? bytesHuman(rawSize) : 'binary content'}
              </div>
              <button className="btn btn-primary" onClick={downloadBytes}>
                download
              </button>
            </div>
          )}
        </section>
      )}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Pins tab — list, pin, unpin, check
// ─────────────────────────────────────────────────────────────────────────────

function PinsTab({
  flash,
  onChanged,
}: {
  flash: (k: 'ok' | 'err', m: string) => void
  onChanged: () => void
}) {
  const [keys, setKeys] = useState<Record<string, { Type: string }>>({})
  const [loading, setLoading] = useState(false)
  const [filter, setFilter] = useState('')
  const [cid, setCid] = useState('')
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    const r = await call<{ Keys: Record<string, { Type: string }> }>('pins')
    setLoading(false)
    if (r.ok) setKeys(r.data.Keys || {})
    else flash('err', r.error)
  }, [flash])

  useEffect(() => {
    refresh()
  }, [refresh])

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase()
    const all = Object.entries(keys)
    if (!q) return all
    return all.filter(([k]) => k.toLowerCase().includes(q))
  }, [keys, filter])

  const doPin = useCallback(
    async (target: string) => {
      if (!target) return
      setBusy(true)
      const r = await call('pin_add', { cid: target })
      setBusy(false)
      if (r.ok) {
        flash('ok', 'pinned')
        refresh()
        onChanged()
      } else {
        flash('err', r.error)
      }
    },
    [flash, refresh, onChanged],
  )

  const doUnpin = useCallback(
    async (target: string) => {
      if (!target) return
      setBusy(true)
      const r = await call('pin_rm', { cid: target })
      setBusy(false)
      if (r.ok) {
        flash('ok', 'unpinned')
        refresh()
        onChanged()
      } else {
        flash('err', r.error)
      }
    },
    [flash, refresh, onChanged],
  )

  return (
    <div style={{ display: 'grid', gap: 18 }}>
      <section className="card">
        <h2 style={{ fontSize: 13, color: '#e5e5e5', marginBottom: 14, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
          Pin / unpin a cid
        </h2>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <input
            className="input"
            style={{ flex: 1, minWidth: 280 }}
            value={cid}
            onChange={(e) => setCid(e.target.value)}
            placeholder="Qm... or bafy..."
          />
          <button className="btn btn-primary" disabled={busy || !cid.trim()} onClick={() => doPin(cid.trim())}>
            pin
          </button>
          <button className="btn" disabled={busy || !cid.trim()} onClick={() => doUnpin(cid.trim())}>
            unpin
          </button>
        </div>
      </section>

      <section className="card">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14, flexWrap: 'wrap' }}>
          <h2 style={{ fontSize: 13, color: '#e5e5e5', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
            Pinned blocks
          </h2>
          <span style={{ fontSize: 11, color: '#6b7280' }}>
            {loading ? 'loading…' : `${filtered.length} of ${Object.keys(keys).length}`}
          </span>
          <button className="btn" style={{ marginLeft: 'auto' }} onClick={refresh} disabled={loading}>
            refresh
          </button>
        </div>
        <input
          className="input"
          style={{ marginBottom: 12 }}
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="filter by cid…"
        />
        <div style={{ maxHeight: 480, overflow: 'auto', border: '1px solid #1f1f2e', borderRadius: 4 }}>
          {filtered.length === 0 && (
            <div style={{ padding: 20, textAlign: 'center', color: '#6b7280', fontSize: 12 }}>
              {loading ? 'loading pins…' : 'no pinned blocks'}
            </div>
          )}
          {filtered.map(([k, meta]) => (
            <div
              key={k}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                padding: '8px 12px',
                borderBottom: '1px solid #1f1f2e',
                fontSize: 11,
              }}
            >
              <span style={{ color: '#6ee7b7', flex: 1, wordBreak: 'break-all' }}>{k}</span>
              <span style={{ color: '#6b7280', textTransform: 'uppercase', fontSize: 9, letterSpacing: '0.1em' }}>
                {meta?.Type ?? 'recursive'}
              </span>
              <button
                className="btn"
                style={{ padding: '4px 10px' }}
                onClick={() => {
                  navigator.clipboard.writeText(k)
                  flash('ok', 'copied')
                }}
              >
                copy
              </button>
              <button
                className="btn btn-danger"
                style={{ padding: '4px 10px' }}
                disabled={busy}
                onClick={() => doUnpin(k)}
              >
                unpin
              </button>
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Stats tab — storage stats + GC
// ─────────────────────────────────────────────────────────────────────────────

function StatsTab({
  flash,
  stats,
  refresh,
}: {
  flash: (k: 'ok' | 'err', m: string) => void
  stats: { blocks: number; pinned: number; total_size: number; storage_path: string } | null
  refresh: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [aggressive, setAggressive] = useState(false)
  const [last, setLast] = useState<{ Removed: string[]; Count: number } | null>(null)

  const runGc = useCallback(async () => {
    if (!confirm(`Garbage-collect unpinned blocks${aggressive ? ' (aggressive)' : ''}?`)) return
    setBusy(true)
    const r = await call<{ Removed: string[]; Count: number }>('gc', { aggressive })
    setBusy(false)
    if (r.ok) {
      setLast(r.data)
      flash('ok', `gc removed ${r.data.Count}`)
      refresh()
    } else {
      flash('err', r.error)
    }
  }, [aggressive, flash, refresh])

  return (
    <div style={{ display: 'grid', gap: 18 }}>
      <section className="card">
        <h2 style={{ fontSize: 13, color: '#e5e5e5', marginBottom: 14, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
          Storage
        </h2>
        <dl className="kv">
          <dt>blocks</dt>
          <dd>{stats?.blocks.toLocaleString() ?? '—'}</dd>
          <dt>pinned</dt>
          <dd>{stats?.pinned.toLocaleString() ?? '—'}</dd>
          <dt>unpinned</dt>
          <dd>{stats ? (stats.blocks - stats.pinned).toLocaleString() : '—'}</dd>
          <dt>total size</dt>
          <dd>{stats ? bytesHuman(stats.total_size) : '—'}</dd>
          <dt>path</dt>
          <dd style={{ wordBreak: 'break-all' }}>{stats?.storage_path ?? '—'}</dd>
        </dl>
        <div style={{ marginTop: 16 }}>
          <button className="btn" onClick={refresh} disabled={busy}>
            refresh
          </button>
        </div>
      </section>

      <section className="card">
        <h2 style={{ fontSize: 13, color: '#e5e5e5', marginBottom: 14, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
          Garbage collect
        </h2>
        <p style={{ fontSize: 11, color: '#6b7280', marginBottom: 12 }}>
          Removes unpinned blocks. Pinned content is never GC&apos;d.
        </p>
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11, color: '#6b7280', marginBottom: 12 }}>
          <input type="checkbox" checked={aggressive} onChange={(e) => setAggressive(e.target.checked)} />
          aggressive
        </label>
        <button className="btn btn-danger" onClick={runGc} disabled={busy}>
          {busy ? 'collecting…' : 'run gc'}
        </button>
        {last && (
          <div style={{ marginTop: 14, fontSize: 11, color: '#6b7280' }}>
            removed {last.Count} block{last.Count === 1 ? '' : 's'}
            {last.Count > 0 && (
              <details style={{ marginTop: 8 }}>
                <summary style={{ cursor: 'pointer' }}>show cids</summary>
                <div
                  style={{
                    marginTop: 8,
                    maxHeight: 200,
                    overflow: 'auto',
                    fontSize: 10,
                    fontFamily: 'inherit',
                    color: '#e5e5e5',
                  }}
                >
                  {last.Removed.map((c) => (
                    <div key={c} style={{ padding: '2px 0', wordBreak: 'break-all' }}>
                      {c}
                    </div>
                  ))}
                </div>
              </details>
            )}
          </div>
        )}
      </section>
    </div>
  )
}
