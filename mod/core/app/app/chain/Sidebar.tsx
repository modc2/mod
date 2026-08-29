"use client"

// Sidebar — your projects and the files inside the open one. Contracts on top,
// tests under them; clicking a file opens it in the editor to the right. You
// can start a project from scratch, or upload one off your machine.

import { useState, useRef } from 'react'
import { toast } from 'react-toastify'
import { TERM_FONT, ACCENT, READ, useIsMobile } from './shared'
import { Btn, Input, panelStyle } from './ui'
import { PIXEL, PX } from './arcade'
import {
  isContract, isTest, newProjectFiles, uniqueName, STARTER_CONTRACT, STARTER_TEST, stem,
} from './projects'
import type { ProjectsApi } from './projects'

const slug = (s: string) => s.trim().replace(/[^A-Za-z0-9_. -]/g, '').replace(/\s+/g, '-')

/** Same, but keeps the directories — for paths coming off an upload. */
const slugPath = (s: string) => s.split('/').map(slug).filter(p => p && p !== '.').join('/')

const UPLOAD_EXT = /\.(sol|js|ts|json|md|txt)$/i
const UPLOAD_SKIP = /(^|\/)(node_modules|artifacts|cache|\.git)(\/|$)/
const UPLOAD_MAX = 512 * 1024

function Row({
  label, active, muted, onClick, onDelete, title, indent, tall,
}: {
  label: string
  active?: boolean
  muted?: boolean
  onClick: () => void
  onDelete?: () => void
  title?: string
  indent?: boolean
  /** taller rows for touch */
  tall?: boolean
}) {
  const pad = tall ? '11px' : indent ? '4px' : '5px'
  return (
    <div style={{ display: 'flex', alignItems: 'stretch' }}>
      <button
        onClick={onClick}
        title={title || label}
        style={{
          flex: 1, textAlign: 'left', fontFamily: TERM_FONT, fontSize: tall ? '14px' : '13px',
          padding: `${pad} 8px ${pad} ${indent ? '18px' : '8px'}`,
          border: 'none', borderLeft: `2px solid ${active ? ACCENT : 'transparent'}`,
          background: active ? `${ACCENT}14` : 'transparent',
          color: active ? ACCENT : muted ? 'var(--text-tertiary)' : 'var(--text-secondary)',
          cursor: 'pointer', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}
      >
        {label}
      </button>
      {onDelete && (
        <button
          onClick={onDelete}
          title="delete"
          style={{
            fontFamily: TERM_FONT, fontSize: '13px', padding: tall ? '0 14px' : '0 8px', border: 'none',
            background: 'transparent', color: 'var(--text-tertiary)', cursor: 'pointer', opacity: 0.7,
          }}
        >
          ✕
        </button>
      )}
    </div>
  )
}

export function Heading({ children, action }: { children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: '11px 8px 7px', fontFamily: PIXEL, fontSize: PX.xs,
      letterSpacing: '0.14em', color: 'var(--text-tertiary)',
    }}>
      <span>{children}</span>
      {action}
    </div>
  )
}

export function Sidebar({ projects, address, onNavigate }: {
  projects: ProjectsApi
  address: string
  /** called when a pick has landed — the mobile drawer closes on it */
  onNavigate?: () => void
}) {
  const [newProject, setNewProject] = useState('')
  const [newFile, setNewFile] = useState('')
  const [adding, setAdding] = useState<'project' | 'file' | 'upload' | null>(null)
  const uploadRef = useRef<HTMLInputElement>(null)
  const mobile = useIsMobile()

  const proj = projects.project
  const files = Object.keys(proj?.files || {}).sort()
  const contracts = files.filter(isContract)
  const tests = files.filter(isTest)
  const other = files.filter(f => !isContract(f) && !isTest(f))

  const createProject = async () => {
    const name = slug(newProject)
    if (!name) return
    try {
      await projects.create(name, newProjectFiles(name.replace(/[^A-Za-z0-9_]/g, '') || 'Contract'))
      setNewProject(''); setAdding(null)
      toast.success(`Project ${name} created`)
      onNavigate?.()
    } catch (e: any) {
      toast.error(e?.message || 'could not create project')
    }
  }

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
        // a folder pick: drop the top directory, keep everything under it
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
      onNavigate?.()
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

  const createFile = () => {
    let path = slug(newFile)
    if (!path || !proj) return
    if (!/\.(sol|js|ts)$/.test(path)) path += '.sol'
    if (!path.includes('/')) path = `${isTest(path) ? 'test' : 'contracts'}/${path}`
    const name = stem(path).replace(/[^A-Za-z0-9_]/g, '') || 'Contract'
    projects.addFile(path, isTest(path) ? STARTER_TEST(name) : isContract(path) ? STARTER_CONTRACT(name) : '')
    setNewFile(''); setAdding(null)
    onNavigate?.()
  }

  const fileRows = (label: string, list: string[], color: string) => list.length > 0 && (
    <>
      <div style={{
        fontFamily: TERM_FONT, fontSize: '13px', letterSpacing: '0.12em',
        color, padding: '8px 8px 3px', opacity: 0.8,
      }}>
        {label}
      </div>
      {list.map(path => (
        <Row
          key={path}
          indent
          tall={mobile}
          label={path.split('/').pop() || path}
          title={path}
          active={projects.activeFile === path}
          onClick={() => { projects.setActiveFile(path); onNavigate?.() }}
          onDelete={files.length > 1 ? () => projects.deleteFile(path) : undefined}
        />
      ))}
    </>
  )

  return (
    <div style={{ ...panelStyle }}>
      <Heading action={
        <span style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <button
            onClick={() => setAdding(a => (a === 'upload' ? null : 'upload'))}
            title="upload a project from your machine"
            style={{
              fontFamily: TERM_FONT, fontSize: '13px', border: 'none', background: 'transparent',
              color: ACCENT, cursor: 'pointer', padding: 0, lineHeight: 1,
            }}
          >
            ↑
          </button>
          <button
            onClick={() => setAdding(a => (a === 'project' ? null : 'project'))}
            title="new project"
            style={{
              fontFamily: TERM_FONT, fontSize: '13px', border: 'none', background: 'transparent',
              color: ACCENT, cursor: 'pointer', padding: 0, lineHeight: 1,
            }}
          >
            +
          </button>
        </span>
      }>
        PROJECTS
      </Heading>

      {adding === 'project' && (
        <div style={{ padding: '0 8px 8px' }}>
          <Input value={newProject} onChange={setNewProject} placeholder="my-token" />
          <div style={{ display: 'flex', gap: '6px', marginTop: '6px' }}>
            <Btn size="sm" onClick={createProject}>CREATE</Btn>
            <Btn size="sm" active={false} onClick={() => { setAdding(null); setNewProject('') }}>CANCEL</Btn>
          </div>
        </div>
      )}

      {adding === 'upload' && (
        <div style={{ padding: '0 8px 8px' }}>
          <div style={{ display: 'flex', gap: '6px' }}>
            <Btn size="sm" onClick={() => pick(false)}>FILES</Btn>
            <Btn size="sm" onClick={() => pick(true)}>FOLDER</Btn>
            <Btn size="sm" active={false} onClick={() => setAdding(null)}>✕</Btn>
          </div>
          <div style={{
            fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-tertiary)',
            marginTop: '6px', lineHeight: 1.5,
          }}>
            .sol / .js / .ts / .json / .md
          </div>
        </div>
      )}

      <input
        ref={uploadRef}
        type="file"
        multiple
        accept=".sol,.js,.ts,.json,.md,.txt"
        style={{ display: 'none' }}
        onChange={e => {
          const picked = Array.from(e.target.files || [])
          e.target.value = ''
          setAdding(null)
          if (picked.length) upload(picked)
        }}
      />

      {projects.loading && projects.list.length === 0 ? (
        <div className="arc-pulse" style={{ fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-tertiary)', padding: '4px 10px 10px' }}>
          loading…
        </div>
      ) : projects.list.length === 0 ? (
        <div style={{ fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-tertiary)', padding: '4px 10px 10px', lineHeight: 1.5 }}>
          No projects yet. Hit <span style={{ color: ACCENT }}>+</span> to start one,
          {' '}<span style={{ color: ACCENT }}>↑</span> to upload one, or fork one from SHARED below.
        </div>
      ) : projects.list.map(p => (
        <Row
          key={p.name}
          tall={mobile}
          label={p.name}
          active={proj?.name === p.name}
          onClick={() => projects.open(p.name)
            .then(() => onNavigate?.())
            .catch(e => toast.error(e?.message || 'open failed'))}
          onDelete={() => {
            if (confirm(`Delete project "${p.name}"? This cannot be undone.`)) {
              projects.remove(p.name).catch(e => toast.error(e?.message || 'delete failed'))
            }
          }}
        />
      ))}

      {proj && (
        <>
          <div style={{ borderTop: '2px solid var(--border-color)', marginTop: '10px' }} />
          <Heading action={
            <button
              onClick={() => setAdding(a => (a === 'file' ? null : 'file'))}
              title="new file"
              style={{
                fontFamily: TERM_FONT, fontSize: '13px', border: 'none', background: 'transparent',
                color: ACCENT, cursor: 'pointer', padding: 0, lineHeight: 1,
              }}
            >
              +
            </button>
          }>
            {proj.name.toUpperCase()}
          </Heading>

          {adding === 'file' && (
            <div style={{ padding: '0 8px 8px' }}>
              <Input value={newFile} onChange={setNewFile} placeholder="Vault.sol / Vault.test.js" />
              <div style={{ display: 'flex', gap: '6px', marginTop: '6px' }}>
                <Btn size="sm" onClick={createFile}>ADD</Btn>
                <Btn size="sm" active={false} onClick={() => { setAdding(null); setNewFile('') }}>CANCEL</Btn>
              </div>
            </div>
          )}

          {fileRows('CONTRACTS', contracts, ACCENT)}
          {fileRows('TESTS', tests, READ)}
          {fileRows('OTHER', other, 'var(--text-tertiary)')}

          <div style={{
            fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-tertiary)',
            padding: '10px 10px 12px', borderTop: '1px solid var(--border-color)', marginTop: '8px',
          }}>
            {projects.saving ? 'saving…' : projects.dirty ? 'unsaved changes' : 'saved'}
            {' · '}
            {address ? `${address.slice(0, 6)}…${address.slice(-4)}` : 'local session'}
          </div>
        </>
      )}
    </div>
  )
}
