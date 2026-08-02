"use client"

// BUILD — edit the open project's files, compile them on the chain module
// (solc + OpenZeppelin), then deploy with whichever wallet is signed in. The
// key never leaves the browser: the API returns bytecode, the wallet signs the
// creation tx.

import { useState, useEffect, useCallback, useRef } from 'react'
import { ethers } from 'ethers'
import { toast } from 'react-toastify'
import {
  TERM_FONT, ACCENT, DANGER, chainApi, coerceArgs, short, explorerUrl, txUrl,
} from './shared'
import { Panel, Label, Btn, Input, Log, Empty, panelStyle } from './ui'
import type { ChainWallet } from './WalletBar'
import { isContract, type ProjectsApi } from './projects'

interface Template { key: string; name: string; description: string; files: Record<string, string> }
interface Artifact {
  name: string
  file: string
  abi: any[]
  bytecode: string
  size: number
  abstract: boolean
  constructor: { name: string; type: string }[]
}
interface Diagnostic { severity: string; message: string }
interface Built {
  name: string
  network: string
  address: string
  tx_hash?: string
  abi: any[]
  created: number
}

export function BuildTab({
  wallet, network, projects, onInteract,
}: {
  wallet: ChainWallet
  network: string
  projects: ProjectsApi
  onInteract: (target: { name: string; address: string; abi: any[] }) => void
}) {
  const [templates, setTemplates] = useState<Template[]>([])
  const [builds, setBuilds] = useState<Built[]>([])

  const [optimize, setOptimize] = useState(true)
  const [compiling, setCompiling] = useState(false)
  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [errors, setErrors] = useState<Diagnostic[]>([])
  const [warnings, setWarnings] = useState<Diagnostic[]>([])
  const [selected, setSelected] = useState(0)

  const [args, setArgs] = useState<Record<string, string>>({})
  const [value, setValue] = useState('')
  const [deploying, setDeploying] = useState(false)
  const [log, setLog] = useState<string[]>([])
  const [deployed, setDeployed] = useState<{ address: string; tx: string } | null>(null)

  const gutterRef = useRef<HTMLDivElement>(null)
  const say = (line: string) => setLog(prev => [...prev, line])

  const project = projects.project
  const path = projects.activeFile
  const source = project?.files[path] ?? ''
  const artifact = artifacts[selected]

  // ── templates + past builds ──
  useEffect(() => {
    chainApi('/build/templates').then(d => setTemplates(d.templates || [])).catch(() => {})
  }, [])

  const loadBuilds = useCallback(() => {
    const q = wallet.address ? `?address=${wallet.address}&` : '?'
    chainApi(`/build/deployments${q}network=${network}`)
      .then(d => setBuilds(d.deployments || [])).catch(() => {})
  }, [wallet.address, network])

  useEffect(() => { loadBuilds() }, [loadBuilds])

  // A different project is a different build — drop stale artifacts.
  useEffect(() => {
    setArtifacts([]); setErrors([]); setWarnings([]); setDeployed(null); setLog([])
  }, [project?.name])

  const fromTemplate = async (t: Template) => {
    const taken = new Set(projects.list.map(p => p.name))
    let name = t.key
    for (let i = 2; taken.has(name); i++) name = `${t.key}-${i}`
    try {
      await projects.create(name, t.files)
      toast.success(`Project ${name} created from ${t.name}`)
    } catch (e: any) {
      toast.error(e?.message || 'could not create project')
    }
  }

  // ── compile the whole project ──
  const compile = useCallback(async () => {
    if (!project) return
    const sources = Object.fromEntries(
      Object.entries(project.files).filter(([p]) => isContract(p)),
    )
    if (!Object.keys(sources).length) { toast.error('no .sol files in this project'); return }

    setCompiling(true)
    setDeployed(null)
    setLog([`> solc ${Object.keys(sources).length} file(s)${optimize ? ' --optimize' : ''}`])
    try {
      const res = await chainApi('/build/compile', { body: { sources, optimize } })
      setErrors(res.errors || [])
      setWarnings(res.warnings || [])
      const deployable: Artifact[] = (res.contracts || []).filter((c: Artifact) => !c.abstract)
      setArtifacts(deployable)
      setSelected(0)
      setArgs({})
      if (res.ok) {
        say(`> ✓ compiled ${deployable.length} contract${deployable.length === 1 ? '' : 's'}`)
        deployable.forEach(c => say(`>   ${c.name} — ${c.size} bytes`))
        if (!deployable.length) say('> no deployable contract (all abstract / interface)')
      } else {
        say(`> ✗ FAILED — ${(res.errors || []).length} error(s)`)
      }
    } catch (e: any) {
      setArtifacts([])
      setErrors([{ severity: 'error', message: e?.message || 'compile failed' }])
      say(`> ERROR: ${e?.message || 'compile failed'}`)
    } finally {
      setCompiling(false)
    }
  }, [project, optimize])

  // ── deploy with the signed-in wallet ──
  const deploy = useCallback(async () => {
    if (!artifact) return
    if (!wallet.kind) { toast.error('Sign in with a wallet first'); return }
    setDeploying(true)
    setDeployed(null)
    try {
      const ctorArgs = coerceArgs(artifact.constructor, args)
      const signer = await wallet.signer()
      const from = await signer.getAddress()
      say(`> deploying ${artifact.name} to ${network} as ${short(from, 8, 6)} [${wallet.kind}]`)

      const factory = new ethers.ContractFactory(artifact.abi, artifact.bytecode, signer)
      const overrides = value.trim() ? { value: ethers.parseEther(value.trim()) } : {}
      const contract = await factory.deploy(...ctorArgs, overrides)
      const tx = contract.deploymentTransaction()
      say(`> tx ${tx?.hash || '—'} — waiting for confirmation…`)

      await contract.waitForDeployment()
      const address = await contract.getAddress()
      say(`> ✓ deployed at ${address}`)
      setDeployed({ address, tx: tx?.hash || '' })
      toast.success(`${artifact.name} deployed`)

      await chainApi('/build/deployments', {
        body: {
          address: from, network, name: artifact.name, contract_address: address,
          tx_hash: tx?.hash, abi: artifact.abi,
          source: project?.files[artifact.file] ?? '',
        },
      }).catch(() => {})
      loadBuilds()
      wallet.refresh()
    } catch (e: any) {
      const msg = e?.shortMessage || e?.message || 'deploy failed'
      say(`> ERROR: ${msg}`)
      toast.error(msg)
    } finally {
      setDeploying(false)
    }
  }, [artifact, args, value, wallet, network, project, loadBuilds])

  const lineCount = source.split('\n').length

  if (!project) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
        <Empty>No project open. Start one from the sidebar, or pick a template below.</Empty>
        <div>
          <Label>TEMPLATES — contract + tests, ready to run</Label>
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
            {templates.map(t => (
              <Btn key={t.key} size="sm" active={false} title={t.description}
                onClick={() => fromTemplate(t)}>
                {t.name.toUpperCase()}
              </Btn>
            ))}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>

      {/* ── Templates ── */}
      <div>
        <Label>NEW PROJECT FROM TEMPLATE</Label>
        <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
          {templates.map(t => (
            <Btn key={t.key} size="sm" active={false} title={t.description}
              onClick={() => fromTemplate(t)}>
              {t.name.toUpperCase()}
            </Btn>
          ))}
        </div>
      </div>

      {/* ── Editor ── */}
      <div style={{ ...panelStyle }}>
        <div style={{
          display: 'flex', alignItems: 'center', gap: '10px', padding: '10px 12px',
          borderBottom: '2px solid var(--border-color)', flexWrap: 'wrap',
        }}>
          <span style={{ fontFamily: TERM_FONT, fontSize: '12px', color: 'var(--text-tertiary)' }}>
            {project.name} /
          </span>
          <span style={{ fontFamily: TERM_FONT, fontSize: '13px', color: ACCENT }}>
            {path || '—'}
          </span>
          <Btn size="sm" active={optimize} onClick={() => setOptimize(o => !o)}>
            OPTIMIZER {optimize ? 'ON' : 'OFF'}
          </Btn>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: '8px', alignItems: 'center' }}>
            <span style={{ fontFamily: TERM_FONT, fontSize: '11px', color: 'var(--text-tertiary)' }}>
              {projects.saving ? 'saving…' : projects.dirty ? 'unsaved' : 'saved'}
            </span>
            <Btn size="sm" active={false} onClick={() => projects.save().catch(() => {})}>SAVE</Btn>
          </div>
        </div>

        <div style={{ display: 'flex', maxHeight: '460px' }}>
          <div
            ref={gutterRef}
            style={{
              fontFamily: TERM_FONT, fontSize: '12.5px', lineHeight: '20px',
              padding: '12px 8px 12px 12px', textAlign: 'right', userSelect: 'none',
              color: 'var(--text-tertiary)', opacity: 0.5, overflow: 'hidden',
              borderRight: '1px solid var(--border-color)', minWidth: '46px',
            }}
          >
            {Array.from({ length: lineCount }, (_, i) => <div key={i}>{i + 1}</div>)}
          </div>
          <textarea
            value={source}
            onChange={e => projects.writeFile(path, e.target.value)}
            onScroll={e => { if (gutterRef.current) gutterRef.current.scrollTop = e.currentTarget.scrollTop }}
            spellCheck={false}
            disabled={!path}
            style={{
              flex: 1, minHeight: '340px', maxHeight: '460px',
              fontFamily: TERM_FONT, fontSize: '12.5px', lineHeight: '20px',
              padding: '12px', border: 'none', background: 'transparent',
              color: 'var(--text-primary)', outline: 'none', resize: 'vertical',
              whiteSpace: 'pre', overflow: 'auto',
            }}
          />
        </div>
      </div>

      {/* ── Compile ── */}
      <div style={{ display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }}>
        <Btn onClick={compile} disabled={compiling}>
          {compiling ? 'COMPILING…' : '▶ COMPILE PROJECT'}
        </Btn>
        <span style={{ fontFamily: TERM_FONT, fontSize: '11px', color: 'var(--text-tertiary)' }}>
          solc 0.8.26 · imports resolve from @openzeppelin/contracts · ./Other.sol resolves inside the project
        </span>
      </div>

      {/* ── Diagnostics ── */}
      {(errors.length > 0 || warnings.length > 0) && (
        <div style={{
          ...panelStyle, padding: '12px', fontFamily: TERM_FONT, fontSize: '12px',
          maxHeight: '240px', overflowY: 'auto',
          borderColor: errors.length ? DANGER : 'var(--border-color)',
        }}>
          {errors.concat(warnings).map((d, i) => (
            <pre key={i} style={{
              margin: '0 0 8px 0', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
              color: d.severity === 'error' ? DANGER : '#f59e0b',
            }}>
              {d.message}
            </pre>
          ))}
        </div>
      )}

      {/* ── Artifact + deploy ── */}
      {artifacts.length > 0 && artifact && (
        <Panel>
          <Label>ARTIFACT</Label>
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginBottom: '12px' }}>
            {artifacts.map((c, i) => (
              <Btn key={`${c.file}:${c.name}`} size="sm" active={i === selected} title={c.file}
                onClick={() => { setSelected(i); setArgs({}); setDeployed(null) }}>
                {c.name} · {c.size}b
              </Btn>
            ))}
          </div>

          {artifact.constructor.length > 0 && (
            <div style={{ marginBottom: '12px' }}>
              <Label>CONSTRUCTOR</Label>
              {artifact.constructor.map((inp, i) => {
                const key = inp.name || `arg${i}`
                return (
                  <div key={key} style={{ marginBottom: '8px' }}>
                    <div style={{ fontFamily: TERM_FONT, fontSize: '11px', color: 'var(--text-tertiary)', marginBottom: '4px' }}>
                      {inp.name || `arg${i}`} <span style={{ opacity: 0.6 }}>({inp.type})</span>
                    </div>
                    <Input
                      value={args[key] || ''}
                      onChange={v => setArgs(prev => ({ ...prev, [key]: v }))}
                      placeholder={inp.type}
                    />
                  </div>
                )
              })}
            </div>
          )}

          <div style={{ display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }}>
            <Btn onClick={deploy} disabled={deploying || !wallet.kind}>
              {deploying ? 'DEPLOYING…' : `▲ DEPLOY${wallet.kind ? ` [${wallet.kind.toUpperCase()}]` : ''}`}
            </Btn>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <span style={{ fontFamily: TERM_FONT, fontSize: '11px', color: 'var(--text-tertiary)' }}>VALUE</span>
              <div style={{ width: '120px' }}>
                <Input value={value} onChange={setValue} placeholder="0.0 ETH" />
              </div>
            </div>
            {!wallet.kind && (
              <span style={{ fontFamily: TERM_FONT, fontSize: '11px', color: '#f59e0b' }}>
                sign in above to deploy
              </span>
            )}
          </div>
        </Panel>
      )}

      {/* ── Log ── */}
      <Log lines={log} live={compiling || deploying} />

      {/* ── Result ── */}
      {deployed && artifact && (
        <Panel style={{ borderColor: ACCENT }}>
          <Label style={{ color: ACCENT }}>DEPLOYED</Label>
          <div style={{ fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-primary)', marginBottom: '4px' }}>
            {artifact.name}
          </div>
          <div style={{ fontFamily: TERM_FONT, fontSize: '12px', color: 'var(--text-secondary)', wordBreak: 'break-all' }}>
            {deployed.address}
          </div>
          <div style={{ display: 'flex', gap: '8px', marginTop: '12px', flexWrap: 'wrap' }}>
            <Btn size="sm" active={false} onClick={() => {
              navigator.clipboard.writeText(deployed.address); toast.success('Address copied')
            }}>
              COPY
            </Btn>
            <Btn size="sm" onClick={() => onInteract({ name: artifact.name, address: deployed.address, abi: artifact.abi })}>
              → INTERACT
            </Btn>
            {explorerUrl(network, deployed.address) && (
              <a href={explorerUrl(network, deployed.address)} target="_blank" rel="noreferrer"
                style={{
                  fontFamily: TERM_FONT, fontSize: '11px', padding: '4px 10px',
                  border: '1px solid var(--border-color)', color: 'var(--text-tertiary)', textDecoration: 'none',
                }}>
                CONTRACT ↗
              </a>
            )}
            {deployed.tx && txUrl(network, deployed.tx) && (
              <a href={txUrl(network, deployed.tx)} target="_blank" rel="noreferrer"
                style={{
                  fontFamily: TERM_FONT, fontSize: '11px', padding: '4px 10px',
                  border: '1px solid var(--border-color)', color: 'var(--text-tertiary)', textDecoration: 'none',
                }}>
                TX ↗
              </a>
            )}
          </div>
        </Panel>
      )}

      {/* ── Past builds ── */}
      <div>
        <Label>MY BUILDS — {network}</Label>
        {builds.length === 0 ? (
          <Empty>Nothing deployed from this wallet yet.</Empty>
        ) : builds.map(b => (
          <div key={`${b.address}-${b.created}`} style={{
            ...panelStyle, padding: '10px 14px', marginBottom: '6px',
            display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '10px', flexWrap: 'wrap',
          }}>
            <div>
              <div style={{ fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-primary)' }}>{b.name}</div>
              <div style={{ fontFamily: TERM_FONT, fontSize: '11px', color: 'var(--text-tertiary)' }}>
                {short(b.address, 10, 8)}
              </div>
            </div>
            <div style={{ display: 'flex', gap: '6px' }}>
              <Btn size="sm" active={false}
                onClick={() => onInteract({ name: b.name, address: b.address, abi: b.abi })}>
                INTERACT
              </Btn>
              {explorerUrl(network, b.address) && (
                <a href={explorerUrl(network, b.address)} target="_blank" rel="noreferrer"
                  style={{
                    fontFamily: TERM_FONT, fontSize: '11px', padding: '4px 8px',
                    border: '1px solid var(--border-color)', color: 'var(--text-tertiary)', textDecoration: 'none',
                  }}>
                  ↗
                </a>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
