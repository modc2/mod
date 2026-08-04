"use client"

// SHARED — the project gallery. Fork what somebody else built into your own
// sidebar, or publish whatever you have open so somebody else can. The fleet's
// own contracts (BlocTime and friends) ship in here read-only.

import { useState, useEffect, useCallback } from 'react'
import { toast } from 'react-toastify'
import { TERM_FONT, ACCENT, chainApi, short } from './shared'
import { Btn, Input, panelStyle } from './ui'
import { Heading } from './Sidebar'
import { uniqueName, type ProjectsApi } from './projects'

export interface SharedEntry {
  id: string
  name: string
  author: string
  description: string
  files: string[]
  updated: number
  builtin: boolean
}

export function Gallery({ projects, address }: { projects: ProjectsApi; address: string }) {
  const [entries, setEntries] = useState<SharedEntry[]>([])
  const [publishing, setPublishing] = useState(false)
  const [description, setDescription] = useState('')
  const [busy, setBusy] = useState('')

  const me = (address || '').toLowerCase()
  const proj = projects.project

  const load = useCallback(() => {
    chainApi('/build/shared').then(d => setEntries(d.shared || [])).catch(() => {})
  }, [])

  useEffect(() => { load() }, [load])

  const fork = async (entry: SharedEntry) => {
    setBusy(entry.id)
    try {
      const d = await chainApi(`/build/shared/${encodeURI(entry.id)}`)
      const name = uniqueName(entry.name, projects.list.map(p => p.name))
      await projects.create(name, d.files || {})
      toast.success(`Forked ${entry.name} → ${name}`)
    } catch (e: any) {
      toast.error(e?.message || 'could not fork that project')
    } finally {
      setBusy('')
    }
  }

  const publish = async () => {
    if (!proj) return
    try {
      await projects.save()
      await chainApi('/build/shared', {
        body: { address, name: proj.name, description, files: proj.files },
      })
      setPublishing(false); setDescription('')
      toast.success(`${proj.name} is in the gallery`)
      load()
    } catch (e: any) {
      toast.error(e?.message || 'could not share that project')
    }
  }

  const unshare = async (entry: SharedEntry) => {
    if (!confirm(`Remove "${entry.name}" from the gallery?`)) return
    try {
      await chainApi(`/build/shared?id=${encodeURIComponent(entry.id)}&address=${address}`,
        { method: 'DELETE' })
      load()
    } catch (e: any) {
      toast.error(e?.message || 'could not unshare')
    }
  }

  return (
    <div style={{ ...panelStyle }}>
      <Heading action={
        <button
          onClick={() => setPublishing(p => !p)}
          title={proj ? `share ${proj.name}` : 'open a project to share it'}
          disabled={!proj}
          style={{
            fontFamily: TERM_FONT, fontSize: '13px', border: 'none', background: 'transparent',
            color: proj ? ACCENT : 'var(--text-tertiary)', cursor: proj ? 'pointer' : 'not-allowed',
            padding: 0, lineHeight: 1, opacity: proj ? 1 : 0.5,
          }}
        >
          ↑
        </button>
      }>
        SHARED
      </Heading>

      {publishing && proj && (
        <div style={{ padding: '0 8px 8px' }}>
          <div style={{ fontFamily: TERM_FONT, fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '5px' }}>
            share <span style={{ color: ACCENT }}>{proj.name}</span>
          </div>
          <Input value={description} onChange={setDescription} placeholder="what it does" />
          <div style={{ display: 'flex', gap: '6px', marginTop: '6px' }}>
            <Btn size="sm" onClick={publish} disabled={!address}>SHARE</Btn>
            <Btn size="sm" active={false} onClick={() => { setPublishing(false); setDescription('') }}>CANCEL</Btn>
          </div>
          {!address && (
            <div style={{ fontFamily: TERM_FONT, fontSize: '10px', color: '#f59e0b', marginTop: '6px', lineHeight: 1.4 }}>
              sign in with a wallet to share
            </div>
          )}
        </div>
      )}

      {entries.length === 0 ? (
        <div style={{ fontFamily: TERM_FONT, fontSize: '11px', color: 'var(--text-tertiary)', padding: '4px 10px 10px', lineHeight: 1.5 }}>
          Nothing shared yet.
        </div>
      ) : entries.map(entry => (
        <div key={entry.id} style={{ display: 'flex', alignItems: 'stretch' }}>
          <button
            onClick={() => fork(entry)}
            disabled={!!busy}
            title={entry.description || `fork ${entry.name} into your projects`}
            style={{
              flex: 1, textAlign: 'left', fontFamily: TERM_FONT, padding: '5px 8px',
              border: 'none', borderLeft: '2px solid transparent', background: 'transparent',
              color: 'var(--text-secondary)', cursor: busy ? 'wait' : 'pointer', overflow: 'hidden',
            }}
          >
            <div style={{ fontSize: '12px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {busy === entry.id ? 'forking…' : entry.name}
            </div>
            <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', opacity: 0.8 }}>
              {entry.builtin ? 'fleet' : entry.author === me ? 'you' : short(entry.author, 6, 4)}
              {' · '}{entry.files.length} file{entry.files.length === 1 ? '' : 's'}
            </div>
          </button>
          {!entry.builtin && entry.author === me && (
            <button
              onClick={() => unshare(entry)}
              title="unshare"
              style={{
                fontFamily: TERM_FONT, fontSize: '11px', padding: '0 8px', border: 'none',
                background: 'transparent', color: 'var(--text-tertiary)', cursor: 'pointer', opacity: 0.7,
              }}
            >
              ✕
            </button>
          )}
        </div>
      ))}

      <div style={{
        fontFamily: TERM_FONT, fontSize: '10px', color: 'var(--text-tertiary)',
        padding: '8px 10px 10px', borderTop: '1px solid var(--border-color)', marginTop: '8px',
        lineHeight: 1.5,
      }}>
        Click one to fork it into your projects.
      </div>
    </div>
  )
}
