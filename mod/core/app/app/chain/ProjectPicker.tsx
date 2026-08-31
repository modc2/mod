"use client"

// PROJECT — the fourth pill on the marquee. What used to be a rail down the
// left (your projects, the shared gallery, upload) is one dropdown next to
// NET / PLAYER / BALANCE, so the console is the same machine on a phone as on
// a desk: pick a project here, edit its files as tabs in BUILD.

import { useState, useEffect, useCallback, useRef } from 'react'
import { toast } from 'react-toastify'
import { TERM_FONT, ACCENT, chainApi, short } from './shared'
import { Btn, Input, Pill, Hint, Dropdown, DropHead, DropRow, DropRule, Quiet } from './ui'
import { PIXEL, NEON } from './arcade'
import {
  isContract, isTest, newProjectFiles, uniqueName, stem, type ProjectsApi,
} from './projects'

export const PROJECT_COLOR = NEON.p2

const slug = (s: string) => s.trim().replace(/[^A-Za-z0-9_. -]/g, '').replace(/\s+/g, '-')

/** Same, but keeps the directories — for paths coming off an upload. */
const slugPath = (s: string) => s.split('/').map(slug).filter(p => p && p !== '.').join('/')

const UPLOAD_EXT = /\.(sol|js|ts|json|md|txt)$/i
const UPLOAD_SKIP = /(^|\/)(node_modules|artifacts|cache|\.git)(\/|$)/
const UPLOAD_MAX = 512 * 1024

interface SharedEntry {
  id: string
  name: string
  author: string
  description: string
  files: string[]
  updated: number
  builtin: boolean
}

export function ProjectPicker({ projects, address }: { projects: ProjectsApi; address: string }) {
  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState<'list' | 'new' | 'upload' | 'share'>('list')
  const [newName, setNewName] = useState('')
  const [description, setDescription] = useState('')
  const [shared, setShared] = useState<SharedEntry[]>([])
  const [sharedErr, setSharedErr] = useState('')
  const [busy, setBusy] = useState('')
  const uploadRef = useRef<HTMLInputElement>(null)

  const proj = projects.project
  const me = (address || '').toLowerCase()
  const close = useCallback(() => { setOpen(false); setMode('list') }, [])

  const loadShared = useCallback(() => {
    chainApi('/build/shared')
      .then(d => { setShared(d.shared || []); setSharedErr('') })
      .catch(e => setSharedErr(e?.message || 'could not load the gallery'))
  }, [])

  useEffect(() => { if (open) loadShared() }, [open, loadShared])

  // ── create ──
  const create = async () => {
    const name = slug(newName)
    if (!name) return
    try {
      await projects.create(name, newProjectFiles(name.replace(/[^A-Za-z0-9_]/g, '') || 'Contract'))
      setNewName('')
      toast.success(`Project ${name} created`)
      close()
    } catch (e: any) {
      toast.error(e?.message || 'could not create project')
    }
  }

  // ── upload ──
  /**
   * Upload a project off your machine. Picking a folder keeps its layout minus
   * the top directory (which names the project); loose files get sorted into
   * contracts/ and test/ the way the test runner expects them.
   */
  const upload = async (picked: File[]) => {
    const files: Record<string, string> = {}
    let root = ''
    let skipped = 0
    for (const f of picked) {
      const rel = slugPath((f as any).webkitRelativePath || '')
      const raw = rel || slug(f.name)
      if (!raw || !UPLOAD_EXT.test(raw) || UPLOAD_SKIP.test(raw) || f.size > UPLOAD_MAX) {
        skipped++
        continue
      }
      const parts = raw.split('/')
      if (parts.length > 1) {
        root = root || parts[0]
        files[parts.slice(1).join('/')] = await f.text()
      } else {
        files[`${isTest(raw) ? 'test' : isContract(raw) ? 'contracts' : 'files'}/${raw}`] = await f.text()
      }
    }
    if (!Object.keys(files).length) {
      toast.error('nothing to upload — .sol/.js/.ts/.json/.md files under 512KB only')
      return
    }
    const sorted = Object.keys(files).sort()
    const base = slug(root || stem(sorted.find(isContract) || sorted[0])) || 'upload'
    const name = uniqueName(base, projects.list.map(p => p.name))
    try {
      await projects.create(name, files)
      toast.success(`Uploaded ${sorted.length} file${sorted.length === 1 ? '' : 's'} → ${name}`
        + (skipped ? ` · ${skipped} skipped` : ''))
      close()
    } catch (e: any) {
      toast.error(e?.message || 'upload failed')
    }
  }

  /** One hidden input serves both pickers — the directory flag is set per click. */
  const pick = (directory: boolean) => {
    const input = uploadRef.current
    if (!input) return
    if (directory) input.setAttribute('webkitdirectory', '')
    else input.removeAttribute('webkitdirectory')
    input.click()
  }

  // ── gallery ──
  const fork = async (entry: SharedEntry) => {
    setBusy(entry.id)
    try {
      const d = await chainApi(`/build/shared/${encodeURI(entry.id)}`)
      const name = uniqueName(entry.name, projects.list.map(p => p.name))
      await projects.create(name, d.files || {})
      toast.success(`Forked ${entry.name} → ${name}`)
      close()
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
      setDescription('')
      toast.success(`${proj.name} is in the gallery`)
      loadShared()
      setMode('list')
    } catch (e: any) {
      toast.error(e?.message || 'could not share that project')
    }
  }

  const unshare = async (entry: SharedEntry) => {
    if (!confirm(`Remove "${entry.name}" from the gallery?`)) return
    try {
      await chainApi(`/build/shared?id=${encodeURIComponent(entry.id)}&address=${address}`,
        { method: 'DELETE' })
      loadShared()
    } catch (e: any) {
      toast.error(e?.message || 'could not unshare')
    }
  }

  const state = proj
    ? (projects.saving ? 'saving…' : projects.dirty ? 'unsaved' : 'saved')
    : ''

  const trigger = (
    <Pill
      label="PROJECT"
      color={PROJECT_COLOR}
      open={open}
      onClick={() => (open ? close() : setOpen(true))}
      blink={!proj && !projects.loading}
      tip={proj ? `${Object.keys(proj.files).length} files · ${state}` : 'start, upload or fork a project'}
      style={{ width: '100%' }}
    >
      <span>{proj ? proj.name : projects.loading ? '…' : 'START ONE'}</span>
      {proj && (
        <Hint>
          <span style={{ fontSize: '13px', color: projects.dirty ? NEON.coin : 'var(--text-tertiary)' }}>
            {state}
          </span>
        </Hint>
      )}
    </Pill>
  )

  const smallNote = (text: string) => (
    <div style={{ fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-tertiary)', lineHeight: 1.5, padding: '2px 12px 8px' }}>
      {text}
    </div>
  )

  return (
    <Dropdown open={open} onClose={close} trigger={trigger} width={380} align="right" color={PROJECT_COLOR} grow={2}>
      <input
        ref={uploadRef}
        type="file"
        multiple
        accept=".sol,.js,.ts,.json,.md,.txt"
        style={{ display: 'none' }}
        onChange={e => {
          const picked = Array.from(e.target.files || [])
          e.target.value = ''
          if (picked.length) upload(picked)
        }}
      />

      {/* ── action strip ── */}
      <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', padding: '12px 12px 6px' }}>
        <Btn size="sm" color={PROJECT_COLOR} active={mode === 'new'} onClick={() => setMode(m => m === 'new' ? 'list' : 'new')}>+ NEW</Btn>
        <Btn size="sm" color={PROJECT_COLOR} active={mode === 'upload'} onClick={() => setMode(m => m === 'upload' ? 'list' : 'upload')}>↑ UPLOAD</Btn>
        <Btn size="sm" color={PROJECT_COLOR} active={mode === 'share'} disabled={!proj}
          title={proj ? `share ${proj.name} to the gallery` : 'open a project to share it'}
          onClick={() => setMode(m => m === 'share' ? 'list' : 'share')}>
          ⇡ SHARE
        </Btn>
      </div>

      {mode === 'new' && (
        <div style={{ padding: '4px 12px 10px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
          <Input value={newName} onChange={setNewName} placeholder="my-token" onEnter={create} />
          <div style={{ display: 'flex', gap: '6px' }}>
            <Btn size="sm" color={PROJECT_COLOR} onClick={create} disabled={!slug(newName)}>CREATE</Btn>
            <Btn size="sm" active={false} onClick={() => { setMode('list'); setNewName('') }}>CANCEL</Btn>
          </div>
          {smallNote('starts with a contract and a test that already passes')}
        </div>
      )}

      {mode === 'upload' && (
        <div style={{ padding: '4px 12px 10px' }}>
          <div style={{ display: 'flex', gap: '6px' }}>
            <Btn size="sm" color={PROJECT_COLOR} onClick={() => pick(false)}>FILES</Btn>
            <Btn size="sm" color={PROJECT_COLOR} onClick={() => pick(true)}>FOLDER</Btn>
            <Btn size="sm" active={false} onClick={() => setMode('list')}>CANCEL</Btn>
          </div>
          {smallNote('.sol / .js / .ts / .json / .md under 512KB — hardhat and foundry layouts both compile')}
        </div>
      )}

      {mode === 'share' && proj && (
        <div style={{ padding: '4px 12px 10px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
          <Input value={description} onChange={setDescription} placeholder={`what ${proj.name} does`} onEnter={publish} />
          <div style={{ display: 'flex', gap: '6px' }}>
            <Btn size="sm" color={PROJECT_COLOR} onClick={publish} disabled={!address}>SHARE {proj.name.toUpperCase()}</Btn>
            <Btn size="sm" active={false} onClick={() => { setMode('list'); setDescription('') }}>CANCEL</Btn>
          </div>
          {!address && (
            <div style={{ fontFamily: TERM_FONT, fontSize: '13px', color: NEON.coin, lineHeight: 1.4 }}>
              sign in as a PLAYER to share
            </div>
          )}
        </div>
      )}

      <DropRule />

      {/* ── mine ── */}
      <DropHead color={PROJECT_COLOR} right={
        <span style={{ fontFamily: TERM_FONT, fontSize: '13px', letterSpacing: 0, color: 'var(--text-tertiary)' }}>
          {address ? short(address, 6, 4) : 'local session'}
        </span>
      }>
        MINE
      </DropHead>
      {projects.loading && projects.list.length === 0 ? (
        <div className="arc-pulse">{smallNote('loading…')}</div>
      ) : projects.list.length === 0 ? (
        smallNote('No projects yet — hit + NEW, ↑ UPLOAD, or fork one from SHARED below.')
      ) : projects.list.map(p => (
        <DropRow
          key={p.name}
          color={PROJECT_COLOR}
          active={proj?.name === p.name}
          onClick={() => projects.open(p.name).then(close).catch(e => toast.error(e?.message || 'open failed'))}
          title={p.files.join('\n')}
          right={
            <Quiet
              title={`delete ${p.name}`}
              onClick={() => {
                if (confirm(`Delete project "${p.name}"? This cannot be undone.`)) {
                  projects.remove(p.name).catch(e => toast.error(e?.message || 'delete failed'))
                }
              }}
            >
              ✕
            </Quiet>
          }
        >
          <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {p.name}
          </span>
          <span style={{ fontSize: '13px', color: 'var(--text-tertiary)', flexShrink: 0 }}>
            {p.files.filter(isContract).length} sol · {p.files.length} files
          </span>
        </DropRow>
      ))}

      <DropRule />

      {/* ── shared ── */}
      <DropHead right={
        <span style={{ fontFamily: TERM_FONT, fontSize: '13px', letterSpacing: 0, color: 'var(--text-tertiary)' }}>
          tap to fork
        </span>
      }>
        SHARED
      </DropHead>
      {sharedErr ? (
        <div style={{ fontFamily: TERM_FONT, fontSize: '13px', color: NEON.dead, padding: '2px 12px 10px' }}>{sharedErr}</div>
      ) : shared.length === 0 ? (
        smallNote('Nothing shared yet.')
      ) : shared.map(entry => (
        <DropRow
          key={entry.id}
          color={PROJECT_COLOR}
          disabled={!!busy}
          onClick={() => fork(entry)}
          title={entry.description || `fork ${entry.name} into your projects`}
          right={!entry.builtin && entry.author === me && (
            <Quiet title="unshare" onClick={() => unshare(entry)}>✕</Quiet>
          )}
        >
          <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {busy === entry.id ? 'forking…' : entry.name}
            {entry.builtin && (
              <span style={{ fontFamily: PIXEL, fontSize: '7px', color: ACCENT, marginLeft: '8px' }}>FLEET</span>
            )}
          </span>
          <span style={{ fontSize: '13px', color: 'var(--text-tertiary)', flexShrink: 0 }}>
            {entry.builtin ? '' : entry.author === me ? 'you · ' : `${short(entry.author, 6, 4)} · `}
            {entry.files.length} file{entry.files.length === 1 ? '' : 's'}
          </span>
        </DropRow>
      ))}
      <div style={{ height: '8px' }} />
    </Dropdown>
  )
}
