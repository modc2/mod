"use client"

// Projects — a named bag of files (contracts/*.sol next to test/*.test.js)
// stored per wallet address by the chain module. One hook owns the active
// project so the sidebar, the editor and the test runner all see the same
// files; edits autosave a beat after you stop typing.

import { useState, useEffect, useCallback, useRef } from 'react'
import { chainApi } from './shared'

export interface ProjectSummary { name: string; updated: number; files: string[] }
export interface Project { name: string; files: Record<string, string>; updated: number }

const AUTOSAVE_MS = 1200

export const isContract = (path: string) => path.endsWith('.sol')
export const isTest = (path: string) => /\.(test|spec)\.(js|ts)$/.test(path)

/** contracts/Foo.sol → Foo */
export const stem = (path: string) =>
  (path.split('/').pop() || path).replace(/\.(sol|test\.js|test\.ts|js|ts)$/, '')

export const STARTER_CONTRACT = (name: string) => `// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract ${name} {
    string public greeting = "gm";

    function setGreeting(string calldata g) external {
        greeting = g;
    }
}
`

export const STARTER_TEST = (name: string) => `const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("${name}", function () {
  it("greets", async function () {
    const c = await (await ethers.getContractFactory("${name}")).deploy();
    expect(await c.greeting()).to.equal("gm");
  });
});
`

/** `token`, then `token-2`, `token-3`… — a name no existing project holds. */
export function uniqueName(base: string, taken: string[]): string {
  const used = new Set(taken)
  let name = base
  for (let i = 2; used.has(name); i++) name = `${base}-${i}`
  return name
}

export function newProjectFiles(name: string): Record<string, string> {
  return {
    [`contracts/${name}.sol`]: STARTER_CONTRACT(name),
    [`test/${name}.test.js`]: STARTER_TEST(name),
  }
}

export interface ProjectsApi {
  list: ProjectSummary[]
  project: Project | null
  activeFile: string
  loading: boolean
  saving: boolean
  dirty: boolean
  open: (name: string) => Promise<void>
  create: (name: string, files?: Record<string, string>) => Promise<void>
  remove: (name: string) => Promise<void>
  save: () => Promise<void>
  setActiveFile: (path: string) => void
  writeFile: (path: string, content: string) => void
  addFile: (path: string, content?: string) => void
  deleteFile: (path: string) => void
  renameFile: (path: string, next: string) => void
  reload: () => void
}

const LS_LAST = 'chain_last_project'

/**
 * Signing in shouldn't cost you the work you did before you did. The first time
 * an address has no projects of its own, whatever the anonymous session built
 * moves over to it.
 */
async function adoptAnonProjects(address: string): Promise<boolean> {
  const anon = await chainApi('/build/projects')
  const rows: ProjectSummary[] = anon.projects || []
  if (!rows.length) return false
  for (const row of rows) {
    const full = await chainApi(`/build/projects/${encodeURIComponent(row.name)}`)
    await chainApi('/build/projects', { body: { address, name: row.name, files: full.files } })
    await chainApi(`/build/projects?name=${encodeURIComponent(row.name)}`, { method: 'DELETE' })
  }
  return true
}

export function useProjects(address: string): ProjectsApi {
  const [list, setList] = useState<ProjectSummary[]>([])
  const [project, setProject] = useState<Project | null>(null)
  const [activeFile, setActiveFile] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const latest = useRef<Project | null>(null)

  latest.current = project

  const q = address ? `?address=${address}` : ''

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const d = await chainApi(`/build/projects${q}`)
      setList(d.projects || [])
      return (d.projects || []) as ProjectSummary[]
    } catch {
      setList([])
      return []
    } finally {
      setLoading(false)
    }
  }, [q])

  const open = useCallback(async (name: string) => {
    const d = await chainApi(`/build/projects/${encodeURIComponent(name)}${q}`)
    const proj: Project = { name, files: d.files || {}, updated: d.updated || 0 }
    setProject(proj)
    setDirty(false)
    setActiveFile(Object.keys(proj.files).find(isContract) || Object.keys(proj.files)[0] || '')
    try { localStorage.setItem(LS_LAST, name) } catch {}
  }, [q])

  // On mount (and whenever the signed-in address changes) pull the list and
  // reopen whatever was last open.
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      let rows = await reload()
      if (!rows.length && address && await adoptAnonProjects(address).catch(() => false)) {
        rows = await reload()
      }
      if (cancelled) return
      let want = ''
      try { want = localStorage.getItem(LS_LAST) || '' } catch {}
      const pick = rows.find(r => r.name === want) || rows[0]
      if (pick) open(pick.name).catch(() => {})
      else { setProject(null); setActiveFile('') }
    })()
    return () => { cancelled = true }
  }, [reload, open, address])

  const persist = useCallback(async (proj: Project) => {
    setSaving(true)
    try {
      await chainApi('/build/projects', {
        body: { address: address || undefined, name: proj.name, files: proj.files },
      })
      setDirty(false)
      reload()
    } finally {
      setSaving(false)
    }
  }, [address, reload])

  const save = useCallback(async () => {
    if (latest.current) await persist(latest.current)
  }, [persist])

  /** Every mutation goes through here: update state, then autosave. */
  const touch = useCallback((next: Project) => {
    setProject(next)
    setDirty(true)
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => { persist(next).catch(() => {}) }, AUTOSAVE_MS)
  }, [persist])

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])

  const create = useCallback(async (name: string, files?: Record<string, string>) => {
    const proj: Project = { name, files: files || newProjectFiles(name), updated: 0 }
    await persist(proj)
    setProject(proj)
    setActiveFile(Object.keys(proj.files).find(isContract) || Object.keys(proj.files)[0] || '')
    try { localStorage.setItem(LS_LAST, name) } catch {}
  }, [persist])

  const remove = useCallback(async (name: string) => {
    await chainApi(`/build/projects?name=${encodeURIComponent(name)}${address ? `&address=${address}` : ''}`,
      { method: 'DELETE' })
    const rows = await reload()
    if (project?.name === name) {
      const next = rows[0]
      if (next) await open(next.name)
      else { setProject(null); setActiveFile('') }
    }
  }, [address, reload, open, project])

  const writeFile = useCallback((path: string, content: string) => {
    if (!latest.current) return
    touch({ ...latest.current, files: { ...latest.current.files, [path]: content } })
  }, [touch])

  const addFile = useCallback((path: string, content = '') => {
    if (!latest.current || latest.current.files[path] !== undefined) {
      setActiveFile(path)
      return
    }
    touch({ ...latest.current, files: { ...latest.current.files, [path]: content } })
    setActiveFile(path)
  }, [touch])

  const deleteFile = useCallback((path: string) => {
    if (!latest.current) return
    const files = { ...latest.current.files }
    delete files[path]
    touch({ ...latest.current, files })
    setActiveFile(prev => (prev === path ? Object.keys(files)[0] || '' : prev))
  }, [touch])

  const renameFile = useCallback((path: string, next: string) => {
    if (!latest.current || next === path || !next.trim()) return
    const files: Record<string, string> = {}
    for (const [p, c] of Object.entries(latest.current.files)) files[p === path ? next : p] = c
    touch({ ...latest.current, files })
    setActiveFile(prev => (prev === path ? next : prev))
  }, [touch])

  return {
    list, project, activeFile, loading, saving, dirty,
    open, create, remove, save, setActiveFile,
    writeFile, addFile, deleteFile, renameFile, reload,
  }
}
