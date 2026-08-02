"use client"

// Sidebar — your projects and the files inside the open one. Contracts on top,
// tests under them; clicking a file opens it in the editor to the right.

import { useState } from 'react'
import { toast } from 'react-toastify'
import { TERM_FONT, ACCENT, READ } from './shared'
import { Btn, Input, panelStyle } from './ui'
import { isContract, isTest, newProjectFiles, STARTER_CONTRACT, STARTER_TEST, stem } from './projects'
import type { ProjectsApi } from './projects'

const slug = (s: string) => s.trim().replace(/[^A-Za-z0-9_. -]/g, '').replace(/\s+/g, '-')

function Row({
  label, active, muted, onClick, onDelete, title, indent,
}: {
  label: string
  active?: boolean
  muted?: boolean
  onClick: () => void
  onDelete?: () => void
  title?: string
  indent?: boolean
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'stretch' }}>
      <button
        onClick={onClick}
        title={title || label}
        style={{
          flex: 1, textAlign: 'left', fontFamily: TERM_FONT, fontSize: '12px',
          padding: indent ? '4px 8px 4px 18px' : '5px 8px',
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
            fontFamily: TERM_FONT, fontSize: '11px', padding: '0 8px', border: 'none',
            background: 'transparent', color: 'var(--text-tertiary)', cursor: 'pointer', opacity: 0.7,
          }}
        >
          ✕
        </button>
      )}
    </div>
  )
}

function Heading({ children, action }: { children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: '10px 8px 6px', fontFamily: TERM_FONT, fontSize: '10px',
      letterSpacing: '0.14em', color: 'var(--text-tertiary)',
    }}>
      <span>{children}</span>
      {action}
    </div>
  )
}

export function Sidebar({ projects, address }: { projects: ProjectsApi; address: string }) {
  const [newProject, setNewProject] = useState('')
  const [newFile, setNewFile] = useState('')
  const [adding, setAdding] = useState<'project' | 'file' | null>(null)

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
    } catch (e: any) {
      toast.error(e?.message || 'could not create project')
    }
  }

  const createFile = () => {
    let path = slug(newFile)
    if (!path || !proj) return
    if (!/\.(sol|js|ts)$/.test(path)) path += '.sol'
    if (!path.includes('/')) path = `${isTest(path) ? 'test' : 'contracts'}/${path}`
    const name = stem(path).replace(/[^A-Za-z0-9_]/g, '') || 'Contract'
    projects.addFile(path, isTest(path) ? STARTER_TEST(name) : isContract(path) ? STARTER_CONTRACT(name) : '')
    setNewFile(''); setAdding(null)
  }

  const fileRows = (label: string, list: string[], color: string) => list.length > 0 && (
    <>
      <div style={{
        fontFamily: TERM_FONT, fontSize: '10px', letterSpacing: '0.12em',
        color, padding: '8px 8px 3px', opacity: 0.8,
      }}>
        {label}
      </div>
      {list.map(path => (
        <Row
          key={path}
          indent
          label={path.split('/').pop() || path}
          title={path}
          active={projects.activeFile === path}
          onClick={() => projects.setActiveFile(path)}
          onDelete={files.length > 1 ? () => projects.deleteFile(path) : undefined}
        />
      ))}
    </>
  )

  return (
    <div style={{
      ...panelStyle, width: '232px', flexShrink: 0, alignSelf: 'flex-start',
      position: 'sticky', top: '16px', maxHeight: 'calc(100vh - 32px)', overflowY: 'auto',
    }}>
      <Heading action={
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

      {projects.loading && projects.list.length === 0 ? (
        <div style={{ fontFamily: TERM_FONT, fontSize: '11px', color: 'var(--text-tertiary)', padding: '4px 10px' }}>
          loading…
        </div>
      ) : projects.list.length === 0 ? (
        <div style={{ fontFamily: TERM_FONT, fontSize: '11px', color: 'var(--text-tertiary)', padding: '4px 10px 10px', lineHeight: 1.5 }}>
          No projects yet. Hit <span style={{ color: ACCENT }}>+</span> to start one, or load a template in BUILD.
        </div>
      ) : projects.list.map(p => (
        <Row
          key={p.name}
          label={p.name}
          active={proj?.name === p.name}
          onClick={() => projects.open(p.name).catch(e => toast.error(e?.message || 'open failed'))}
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
            fontFamily: TERM_FONT, fontSize: '10px', color: 'var(--text-tertiary)',
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
