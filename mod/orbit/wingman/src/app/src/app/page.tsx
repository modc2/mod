'use client'
import { useState, useEffect, useCallback, useRef } from 'react'
import {
  getSets,
  newSet,
  getSet,
  deleteSet,
  uploadPhotos,
  deletePhoto,
  getAudit,
  getLineup,
  exportSet,
  imgUrl,
  downloadUrl,
  WingmanSet,
  Photo,
  PhotoAudit,
  LineupSlot,
} from './lib/api'

// ── sub-components ────────────────────────────────────────────────────────

function ScoreBadge({ score }: { score: number }) {
  const color = score >= 70 ? '#34d399' : score >= 50 ? '#fbbf24' : '#f87171'
  return (
    <span style={{
      background: color + '22',
      color,
      border: `1px solid ${color}44`,
      borderRadius: 4,
      padding: '1px 6px',
      fontSize: 11,
      fontWeight: 600,
    }}>
      {Math.round(score)}
    </span>
  )
}

function RoleBadge({ role }: { role: string }) {
  const map: Record<string, string> = {
    headshot: '#a78bfa',
    portrait: '#60a5fa',
    full: '#34d399',
    far: '#fbbf24',
    group: '#fb923c',
    scene: '#94a3b8',
  }
  const color = map[role] || '#888'
  return (
    <span style={{
      background: color + '22',
      color,
      border: `1px solid ${color}44`,
      borderRadius: 4,
      padding: '1px 6px',
      fontSize: 10,
      textTransform: 'uppercase',
      letterSpacing: '0.05em',
    }}>
      {role}
    </span>
  )
}

// ── main page ─────────────────────────────────────────────────────────────

type Tab = 'photos' | 'lineup' | 'export'

export default function WingmanPage() {
  const [sets, setSets] = useState<WingmanSet[]>([])
  const [selectedSet, setSelectedSet] = useState<WingmanSet | null>(null)
  const [tab, setTab] = useState<Tab>('photos')
  const [audits, setAudits] = useState<Record<string, PhotoAudit>>({})
  const [lineup, setLineup] = useState<LineupSlot[]>([])
  const [lineupGaps, setLineupGaps] = useState<string[]>([])
  const [preset, setPreset] = useState('tinder')
  const [exportResult, setExportResult] = useState<{ zip: string } | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [newName, setNewName] = useState('')
  const fileInputRef = useRef<HTMLInputElement>(null)

  // ── load sets on mount ────────────────────────────────────────────────

  const loadSets = useCallback(async () => {
    try {
      const data = await getSets()
      setSets(data)
    } catch (e: unknown) {
      // If 403 (not loopback) we just show an empty list; user can enter set ID
      if (!(e instanceof Error && e.message.includes('403'))) {
        setError(String(e))
      }
    }
  }, [])

  useEffect(() => { loadSets() }, [loadSets])

  // ── set selection ─────────────────────────────────────────────────────

  const selectSet = useCallback(async (id: string) => {
    setLoading(true)
    setError(null)
    setAudits({})
    setLineup([])
    setLineupGaps([])
    setExportResult(null)
    try {
      const s = await getSet(id)
      setSelectedSet(s)
      // Populate audits from the photos' embedded audit field
      const a: Record<string, PhotoAudit> = {}
      for (const p of s.photos) {
        if (p.audit) a[p.id] = p.audit as unknown as PhotoAudit
      }
      setAudits(a)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  // ── new set ───────────────────────────────────────────────────────────

  const handleNewSet = async () => {
    setLoading(true)
    setError(null)
    try {
      const s = await newSet(newName || undefined)
      setSets(prev => [s, ...prev])
      setSelectedSet(s)
      setNewName('')
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  // ── delete set ────────────────────────────────────────────────────────

  const handleDeleteSet = async (id: string) => {
    if (!confirm('Delete this set and all its photos?')) return
    setLoading(true)
    try {
      await deleteSet(id)
      setSets(prev => prev.filter(s => s.id !== id))
      if (selectedSet?.id === id) setSelectedSet(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  // ── upload photos ──────────────────────────────────────────────────────

  const handleUpload = async (files: FileList | File[]) => {
    if (!selectedSet) {
      setError('Select or create a set first.')
      return
    }
    setUploading(true)
    setError(null)
    try {
      await uploadPhotos(selectedSet.id, Array.from(files))
      await selectSet(selectedSet.id)
    } catch (e) {
      setError(String(e))
    } finally {
      setUploading(false)
    }
  }

  const handleFilePick = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files?.length) handleUpload(e.target.files)
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    if (e.dataTransfer.files?.length) handleUpload(e.dataTransfer.files)
  }

  // ── delete photo ───────────────────────────────────────────────────────

  const handleDeletePhoto = async (photoId: string) => {
    if (!selectedSet) return
    if (!confirm('Remove this photo from the set?')) return
    try {
      await deletePhoto(selectedSet.id, photoId)
      await selectSet(selectedSet.id)
    } catch (e) {
      setError(String(e))
    }
  }

  // ── run audit ──────────────────────────────────────────────────────────

  const handleAudit = async (force = false) => {
    if (!selectedSet) return
    setLoading(true)
    setError(null)
    try {
      const result = await getAudit(selectedSet.id, undefined, force)
      // result.photos is a record or array
      const photoMap: Record<string, PhotoAudit> = {}
      if (result && typeof result === 'object') {
        const photos = (result as unknown as Record<string, unknown>).photos
        if (photos && typeof photos === 'object' && !Array.isArray(photos)) {
          Object.assign(photoMap, photos)
        } else if (Array.isArray(photos)) {
          for (const p of photos) {
            if (p && p.id) photoMap[p.id] = p
          }
        }
      }
      setAudits(photoMap)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  // ── run lineup ─────────────────────────────────────────────────────────

  const handleLineup = async () => {
    if (!selectedSet) return
    setLoading(true)
    setError(null)
    try {
      const result = await getLineup(selectedSet.id)
      setLineup(Array.isArray(result.slots) ? result.slots : [])
      setLineupGaps(Array.isArray(result.gaps) ? result.gaps : [])
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  // ── export ─────────────────────────────────────────────────────────────

  const handleExport = async () => {
    if (!selectedSet) return
    setLoading(true)
    setError(null)
    try {
      const result = await exportSet(selectedSet.id, preset)
      setExportResult({ zip: downloadUrl(selectedSet.id, preset) })
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  // ── render ─────────────────────────────────────────────────────────────

  const photos = selectedSet?.photos ?? []

  // ── styles ──────────────────────────────────────────────────────────────

  const S = {
    root: {
      display: 'flex',
      height: '100vh',
      overflow: 'hidden',
      background: 'var(--bg)',
    } as React.CSSProperties,
    sidebar: {
      width: 240,
      minWidth: 200,
      background: 'var(--bg2)',
      borderRight: '1px solid var(--border)',
      display: 'flex',
      flexDirection: 'column' as const,
      overflow: 'hidden',
    },
    sidebarHead: {
      padding: '16px 14px 10px',
      borderBottom: '1px solid var(--border)',
    },
    sidebarTitle: {
      fontSize: 16,
      fontWeight: 700,
      color: 'var(--accent)',
      marginBottom: 10,
    },
    newSetRow: {
      display: 'flex',
      gap: 6,
      alignItems: 'center',
    },
    newSetInput: {
      flex: 1,
      minWidth: 0,
    },
    btnPrimary: {
      background: 'var(--accent)',
      color: '#fff',
      padding: '6px 10px',
      fontWeight: 600,
      borderRadius: 4,
    },
    btnGhost: {
      background: 'transparent',
      color: 'var(--text2)',
      padding: '4px 8px',
      border: '1px solid var(--border2)',
    },
    btnDanger: {
      background: 'transparent',
      color: 'var(--red)',
      padding: '3px 6px',
      border: '1px solid #f8717133',
      borderRadius: 4,
      fontSize: 11,
    },
    setList: {
      flex: 1,
      overflowY: 'auto' as const,
      padding: '8px 0',
    },
    setItem: (active: boolean): React.CSSProperties => ({
      padding: '8px 14px',
      cursor: 'pointer',
      background: active ? 'var(--bg3)' : 'transparent',
      borderLeft: active ? '2px solid var(--accent)' : '2px solid transparent',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
    }),
    setItemName: {
      fontSize: 13,
      overflow: 'hidden',
      textOverflow: 'ellipsis',
      whiteSpace: 'nowrap' as const,
    },
    setItemCount: {
      fontSize: 11,
      color: 'var(--text3)',
      marginLeft: 4,
      flexShrink: 0,
    },
    main: {
      flex: 1,
      display: 'flex',
      flexDirection: 'column' as const,
      overflow: 'hidden',
    },
    topBar: {
      padding: '10px 20px',
      borderBottom: '1px solid var(--border)',
      display: 'flex',
      alignItems: 'center',
      gap: 12,
      background: 'var(--bg2)',
    },
    tabRow: {
      display: 'flex',
      gap: 4,
    },
    tab: (active: boolean): React.CSSProperties => ({
      background: active ? 'var(--bg3)' : 'transparent',
      color: active ? 'var(--accent)' : 'var(--text2)',
      border: `1px solid ${active ? 'var(--border2)' : 'transparent'}`,
      padding: '5px 14px',
      borderRadius: 4,
      fontWeight: active ? 600 : 400,
      cursor: 'pointer',
    }),
    content: {
      flex: 1,
      overflow: 'auto',
      padding: 20,
    },
    dropzone: (dragging: boolean): React.CSSProperties => ({
      border: `2px dashed ${dragging ? 'var(--accent)' : 'var(--border2)'}`,
      borderRadius: 8,
      padding: '32px 20px',
      textAlign: 'center',
      cursor: 'pointer',
      color: dragging ? 'var(--accent)' : 'var(--text2)',
      background: dragging ? '#a78bfa0a' : 'transparent',
      marginBottom: 20,
      transition: 'all 0.15s',
    }),
    photoGrid: {
      display: 'grid',
      gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))',
      gap: 12,
    },
    photoCard: {
      background: 'var(--bg2)',
      border: '1px solid var(--border)',
      borderRadius: 6,
      overflow: 'hidden',
      position: 'relative' as const,
    },
    photoImg: {
      width: '100%',
      aspectRatio: '4/5',
      objectFit: 'cover' as const,
      display: 'block',
    },
    photoFooter: {
      padding: '6px 8px',
      display: 'flex',
      flexDirection: 'column' as const,
      gap: 3,
    },
    photoName: {
      fontSize: 11,
      color: 'var(--text2)',
      overflow: 'hidden',
      textOverflow: 'ellipsis',
      whiteSpace: 'nowrap' as const,
    },
    photoBadges: {
      display: 'flex',
      gap: 4,
      alignItems: 'center',
      flexWrap: 'wrap' as const,
    },
    issueList: {
      marginTop: 4,
      display: 'flex',
      flexDirection: 'column' as const,
      gap: 2,
    },
    issueItem: {
      fontSize: 10,
      color: 'var(--red)',
      overflow: 'hidden',
      textOverflow: 'ellipsis',
      whiteSpace: 'nowrap' as const,
    },
    deleteBtn: {
      position: 'absolute' as const,
      top: 4,
      right: 4,
      background: '#0008',
      color: '#fff',
      border: 'none',
      borderRadius: 3,
      padding: '2px 6px',
      fontSize: 11,
      cursor: 'pointer',
    },
    lineupGrid: {
      display: 'grid',
      gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
      gap: 16,
    },
    lineupCard: {
      background: 'var(--bg2)',
      border: '1px solid var(--border)',
      borderRadius: 6,
      overflow: 'hidden',
    },
    lineupSlotNum: {
      padding: '6px 10px',
      background: 'var(--bg3)',
      borderBottom: '1px solid var(--border)',
      fontSize: 11,
      color: 'var(--text2)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
    },
    lineupFooter: {
      padding: '8px 10px',
      display: 'flex',
      flexDirection: 'column' as const,
      gap: 4,
    },
    gapBox: {
      background: 'var(--bg2)',
      border: '1px solid var(--border)',
      borderRadius: 6,
      padding: 14,
      marginTop: 20,
    },
    gapTitle: {
      fontSize: 12,
      color: 'var(--text2)',
      marginBottom: 8,
      fontWeight: 600,
      textTransform: 'uppercase' as const,
      letterSpacing: '0.05em',
    },
    gapItem: {
      fontSize: 12,
      color: 'var(--yellow)',
      padding: '2px 0',
    },
    exportBox: {
      background: 'var(--bg2)',
      border: '1px solid var(--border)',
      borderRadius: 8,
      padding: 24,
      maxWidth: 480,
    },
    exportTitle: {
      fontSize: 16,
      fontWeight: 600,
      marginBottom: 16,
    },
    presetRow: {
      display: 'flex',
      gap: 8,
      marginBottom: 20,
    },
    presetBtn: (active: boolean): React.CSSProperties => ({
      background: active ? 'var(--accent)' : 'var(--bg3)',
      color: active ? '#fff' : 'var(--text2)',
      border: `1px solid ${active ? 'var(--accent2)' : 'var(--border2)'}`,
      padding: '6px 16px',
      borderRadius: 4,
      cursor: 'pointer',
      fontWeight: active ? 600 : 400,
    }),
    downloadLink: {
      display: 'inline-block',
      background: 'var(--green)',
      color: '#000',
      borderRadius: 4,
      padding: '8px 20px',
      fontWeight: 600,
      marginTop: 16,
      textDecoration: 'none',
    },
    emptyState: {
      textAlign: 'center' as const,
      padding: '60px 20px',
      color: 'var(--text3)',
    },
    errorBanner: {
      background: '#f8717122',
      border: '1px solid #f87171',
      borderRadius: 4,
      padding: '8px 12px',
      color: 'var(--red)',
      marginBottom: 16,
      fontSize: 13,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
    },
  }

  // ── tabs content ────────────────────────────────────────────────────────

  const photosTab = (
    <div>
      {/* Drop zone */}
      <div
        style={S.dropzone(dragging)}
        onDragOver={e => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        onClick={() => fileInputRef.current?.click()}
      >
        {uploading ? 'Uploading...' : 'Drop photos here or click to select'}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept="image/*"
          style={{ display: 'none' }}
          onChange={handleFilePick}
        />
      </div>

      {/* Action row */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 16, alignItems: 'center' }}>
        <button style={S.btnPrimary} onClick={() => handleAudit(false)} disabled={loading || !photos.length}>
          Audit
        </button>
        <button style={S.btnGhost} onClick={() => handleAudit(true)} disabled={loading || !photos.length}>
          Force re-audit
        </button>
        <span style={{ color: 'var(--text3)', fontSize: 12 }}>
          {photos.length} photo{photos.length !== 1 ? 's' : ''}
        </span>
      </div>

      {/* Photo grid */}
      {photos.length === 0 ? (
        <div style={{ color: 'var(--text3)', textAlign: 'center', padding: 40 }}>
          No photos yet. Drop some above.
        </div>
      ) : (
        <div style={S.photoGrid}>
          {photos.map(p => {
            const a = audits[p.id]
            return (
              <div key={p.id} style={S.photoCard}>
                <button
                  style={S.deleteBtn}
                  onClick={() => handleDeletePhoto(p.id)}
                  title="Remove photo"
                >
                  ×
                </button>
                <img
                  src={imgUrl(selectedSet!.id, p.id)}
                  alt={p.name}
                  style={S.photoImg}
                  loading="lazy"
                />
                <div style={S.photoFooter}>
                  <div style={S.photoName} title={p.name}>{p.name}</div>
                  {a && (
                    <>
                      <div style={S.photoBadges}>
                        <ScoreBadge score={a.score} />
                        <RoleBadge role={a.role} />
                      </div>
                      {a.issues?.length > 0 && (
                        <div style={S.issueList}>
                          {a.issues.slice(0, 2).map((issue, i) => {
                            const msg = typeof issue === 'string' ? issue : (issue as { msg?: string }).msg || String(issue)
                            return (
                              <div key={i} style={S.issueItem} title={msg}>
                                {msg}
                              </div>
                            )
                          })}
                        </div>
                      )}
                    </>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )

  const lineupTab = (
    <div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 20, alignItems: 'center' }}>
        <button style={S.btnPrimary} onClick={handleLineup} disabled={loading || !photos.length}>
          Run Lineup
        </button>
        <span style={{ color: 'var(--text3)', fontSize: 12 }}>
          Score-ranked, face-aware, variety-driven selection
        </span>
      </div>

      {lineup.length === 0 ? (
        <div style={{ color: 'var(--text3)', textAlign: 'center', padding: 40 }}>
          Press "Run Lineup" to rank photos for your dating profile.
        </div>
      ) : (
        <>
          <div style={S.lineupGrid}>
            {lineup.map(slot => (
              <div key={slot.slot} style={S.lineupCard}>
                <div style={S.lineupSlotNum}>
                  <span>#{slot.slot}</span>
                  <RoleBadge role={slot.role} />
                </div>
                <img
                  src={imgUrl(selectedSet!.id, slot.photo)}
                  alt={`slot ${slot.slot}`}
                  style={S.photoImg}
                  loading="lazy"
                />
                <div style={S.lineupFooter}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <ScoreBadge score={slot.score} />
                    <span style={{ fontSize: 11, color: 'var(--text3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {slot.why}
                    </span>
                  </div>
                </div>
              </div>
            ))}
          </div>

          {lineupGaps.length > 0 && (
            <div style={S.gapBox}>
              <div style={S.gapTitle}>What the set is missing</div>
              {lineupGaps.map((g, i) => (
                <div key={i} style={S.gapItem}>{g}</div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )

  const exportTab = (
    <div style={S.exportBox}>
      <div style={S.exportTitle}>Export for dating app</div>

      <div style={{ marginBottom: 12, color: 'var(--text2)', fontSize: 13 }}>
        Preset — card ratio and output size
      </div>
      <div style={S.presetRow}>
        {['tinder', 'hinge', 'bumble', 'square', 'story'].map(p => (
          <button
            key={p}
            style={S.presetBtn(preset === p)}
            onClick={() => { setPreset(p); setExportResult(null) }}
          >
            {p}
          </button>
        ))}
      </div>

      <div style={{ color: 'var(--text3)', fontSize: 12, marginBottom: 20 }}>
        {preset === 'tinder' && 'Tinder / Hinge — 4:5 → 1080×1350'}
        {preset === 'hinge' && 'Hinge — 4:5 → 1080×1350'}
        {preset === 'bumble' && 'Bumble — 3:4 → 1080×1440'}
        {preset === 'square' && 'Square — 1:1 → 1080×1080'}
        {preset === 'story' && 'Story — 9:16 → 1080×1920'}
      </div>

      <button
        style={{ ...S.btnPrimary, padding: '10px 24px', fontSize: 14 }}
        onClick={handleExport}
        disabled={loading || !photos.length}
      >
        {loading ? 'Exporting...' : 'Export lineup'}
      </button>

      {exportResult && (
        <div style={{ marginTop: 20 }}>
          <div style={{ color: 'var(--green)', fontSize: 13, marginBottom: 8 }}>
            Export complete. Face-aware crops, EXIF stripped.
          </div>
          <a
            href={exportResult.zip}
            style={S.downloadLink}
            download
          >
            Download {preset}.zip
          </a>
        </div>
      )}
    </div>
  )

  // ── layout ──────────────────────────────────────────────────────────────

  return (
    <div style={S.root}>
      {/* Sidebar */}
      <aside style={S.sidebar}>
        <div style={S.sidebarHead}>
          <div style={S.sidebarTitle}>Wingman</div>
          <div style={S.newSetRow}>
            <input
              style={S.newSetInput}
              type="text"
              placeholder="Set name (optional)"
              value={newName}
              onChange={e => setNewName(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleNewSet()}
            />
            <button style={S.btnPrimary} onClick={handleNewSet} disabled={loading}>
              +
            </button>
          </div>
        </div>

        <div style={S.setList}>
          {sets.length === 0 && (
            <div style={{ padding: '16px 14px', color: 'var(--text3)', fontSize: 12 }}>
              No sets yet. Create one above.
            </div>
          )}
          {sets.map(s => (
            <div
              key={s.id}
              style={S.setItem(selectedSet?.id === s.id)}
              onClick={() => selectSet(s.id)}
            >
              <span style={S.setItemName} title={s.name || s.id}>
                {s.name || s.id.slice(0, 8)}
              </span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                <span style={S.setItemCount}>
                  {s.photo_count ?? s.photos?.length ?? 0}
                </span>
                <button
                  style={S.btnDanger}
                  onClick={e => { e.stopPropagation(); handleDeleteSet(s.id) }}
                  title="Delete set"
                >
                  ×
                </button>
              </div>
            </div>
          ))}
        </div>
      </aside>

      {/* Main area */}
      <main style={S.main}>
        {/* Top bar */}
        <div style={S.topBar}>
          {selectedSet ? (
            <>
              <div style={{ fontWeight: 600, fontSize: 14, marginRight: 8 }}>
                {selectedSet.name || selectedSet.id.slice(0, 12)}
              </div>
              <div style={S.tabRow}>
                {(['photos', 'lineup', 'export'] as Tab[]).map(t => (
                  <button
                    key={t}
                    style={S.tab(tab === t)}
                    onClick={() => setTab(t)}
                  >
                    {t.charAt(0).toUpperCase() + t.slice(1)}
                  </button>
                ))}
              </div>
            </>
          ) : (
            <div style={{ color: 'var(--text3)', fontSize: 13 }}>
              Select or create a set to begin.
            </div>
          )}
          {loading && (
            <span style={{ color: 'var(--text3)', fontSize: 12, marginLeft: 'auto' }}>
              Loading...
            </span>
          )}
        </div>

        {/* Content */}
        <div style={S.content}>
          {error && (
            <div style={S.errorBanner}>
              <span>{error}</span>
              <button
                style={{ background: 'transparent', color: 'var(--red)', border: 'none', cursor: 'pointer', fontSize: 14 }}
                onClick={() => setError(null)}
              >
                ×
              </button>
            </div>
          )}

          {!selectedSet ? (
            <div style={S.emptyState}>
              <div style={{ fontSize: 40, marginBottom: 12 }}>&#9992;</div>
              <div style={{ fontSize: 16, marginBottom: 8 }}>Wingman</div>
              <div style={{ fontSize: 13 }}>
                Create a set, add your photos, run audit and lineup.<br />
                Face-aware crops for Tinder, Hinge, Bumble — measured, nothing retouched.
              </div>
            </div>
          ) : (
            <>
              {tab === 'photos' && photosTab}
              {tab === 'lineup' && lineupTab}
              {tab === 'export' && exportTab}
            </>
          )}
        </div>
      </main>
    </div>
  )
}
