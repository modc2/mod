"use client"

// BUILD — edit the open project's files, compile them on the chain module
// (solc + OpenZeppelin), then deploy with whichever wallet is signed in. The
// key never leaves the browser: the API returns bytecode, the wallet signs the
// creation tx.

import { useState, useEffect, useCallback, useRef } from 'react'
import { ethers } from 'ethers'
import { toast } from 'react-toastify'
import {
  TERM_FONT, ACCENT, DANGER, READ, chainApi, coerceArgs, short, explorerUrl, txUrl, netInfo, useIsMobile,
} from './shared'
import { Panel, Label, Btn, Input, Log, Empty, Banner, Skeleton, panelStyle } from './ui'
import { PIXEL, PX, NEON, Sprite } from './arcade'
import type { ChainWallet } from './WalletBar'
import {
  isContract, isTest, uniqueName, stem, STARTER_CONTRACT, STARTER_TEST, type ProjectsApi,
} from './projects'

interface Template { key: string; name: string; description: string; files: Record<string, string> }

// Each template card wears its own neon — five cabinet colours, cycled, so a
// row of them reads as a shelf of different games rather than five grey boxes.
const CARD_COLORS = [ACCENT, NEON.p2, NEON.p1, NEON.coin, NEON.life]

// The landing strip: what this console does, in the order you'll do it.
const STEPS: { label: string; note: string; c: string }[] = [
  { label: 'PICK', note: 'a template below — or open PROJECT up top to start empty, upload a folder, or fork SHARED', c: ACCENT },
  { label: 'COMPILE', note: 'solc builds the whole project on the chain module', c: NEON.p2 },
  { label: 'DEPLOY', note: 'your wallet signs it — the key never leaves the browser', c: NEON.p1 },
  { label: 'PLAY', note: 'call it live, straight from the console', c: NEON.coin },
]
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
  /** where the store mod holds this build's ABI / source */
  abi_cid?: string
  src_cid?: string
  created: number
  /** someone else's contract you added to your list — not a build of yours */
  watched?: boolean
}

export function BuildTab({
  wallet, network, projects, onInteract,
}: {
  wallet: ChainWallet
  network: string
  projects: ProjectsApi
  onInteract: (target: { name: string; address: string; abi: any[]; abiCid?: string }) => void
}) {
  const [templates, setTemplates] = useState<Template[]>([])
  const [templatesErr, setTemplatesErr] = useState('')
  const [templatesLoading, setTemplatesLoading] = useState(true)
  const [fleetContracts, setFleetContracts] = useState<{ name: string; address: string }[]>([])
  const [openingFleet, setOpeningFleet] = useState('')
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
  const [deployed, setDeployed] = useState<
    { address: string; tx: string; abiCid?: string } | null>(null)

  const [addingFile, setAddingFile] = useState(false)
  const [newFile, setNewFile] = useState('')
  const gutterRef = useRef<HTMLDivElement>(null)
  const mobile = useIsMobile()
  const say = (line: string) => setLog(prev => [...prev, line])

  const project = projects.project
  const path = projects.activeFile
  const source = project?.files[path] ?? ''
  const artifact = artifacts[selected]

  // ── templates + past builds ──
  const loadTemplates = useCallback(() => {
    setTemplatesLoading(true)
    chainApi('/build/templates')
      .then(d => { setTemplates(d.templates || []); setTemplatesErr('') })
      // Silently empty is the worst answer here: the templates ARE the way in,
      // so say what went wrong instead of drawing nothing.
      .catch(e => setTemplatesErr(e?.message || 'could not load templates'))
      .finally(() => setTemplatesLoading(false))
  }, [])

  useEffect(() => { loadTemplates() }, [loadTemplates])

  // The fleet's own contracts, so the landing page can say what is already
  // deployed on this chain instead of showing an empty console to someone who
  // hasn't written anything yet. Off a fleet network this comes back empty and
  // the strip doesn't draw.
  useEffect(() => {
    let cancelled = false
    chainApi('/contracts', { body: { network } })
      .then(d => {
        if (cancelled) return
        setFleetContracts(Object.entries(d.contracts || {})
          .map(([name, c]: [string, any]) => ({ name, address: c.address }))
          .filter(c => c.address))
      })
      .catch(() => { if (!cancelled) setFleetContracts([]) })
    return () => { cancelled = true }
  }, [network])

  /** Take a fleet contract straight to PLAY — its ABI comes from the module. */
  const playFleet = async (name: string, address: string) => {
    setOpeningFleet(name)
    try {
      const d = await chainApi(`/contracts/abis?network=${network}`)
      const hit = (d.contracts || []).find((c: any) => c.name === name)
      if (!hit?.abi?.length) throw new Error(`no ABI stored for ${name}`)
      onInteract({ name, address, abi: hit.abi, abiCid: hit.abi_cid })
    } catch (e: any) {
      toast.error(e?.message || `could not open ${name}`)
    } finally {
      setOpeningFleet('')
    }
  }

  const loadBuilds = useCallback(() => {
    const q = wallet.address ? `?address=${wallet.address}&` : '?'
    chainApi(`/build/deployments${q}network=${network}`)
      // watched contracts belong to CONTRACTS — this list is what you shipped
      .then(d => setBuilds((d.deployments || []).filter((b: Built) => !b.watched)))
      .catch(() => {})
  }, [wallet.address, network])

  useEffect(() => { loadBuilds() }, [loadBuilds])

  // A different project is a different build — drop stale artifacts.
  useEffect(() => {
    setArtifacts([]); setErrors([]); setWarnings([]); setDeployed(null); setLog([])
  }, [project?.name])

  const fromTemplate = async (t: Template) => {
    const name = uniqueName(t.key, projects.list.map(p => p.name))
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

      // Recording the build also writes its ABI + source into the store mod;
      // the CID that comes back is what makes it loadable from anywhere.
      const rec = await chainApi('/build/deployments', {
        body: {
          address: from, network, name: artifact.name, contract_address: address,
          tx_hash: tx?.hash, abi: artifact.abi,
          source: project?.files[artifact.file] ?? '',
        },
      }).catch(() => null)
      const abiCid = rec?.deployment?.abi_cid
      if (abiCid) {
        say(`> ABI stored — ${abiCid}`)
        setDeployed({ address, tx: tx?.hash || '', abiCid })
      }
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

  // contracts, then tests, then everything else — the order you read a project in
  const rank = (f: string) => (isContract(f) ? 0 : isTest(f) ? 1 : 2)
  const files = Object.keys(project?.files || {}).sort((a, b) => rank(a) - rank(b) || a.localeCompare(b))

  const createFile = () => {
    let p = newFile.trim().replace(/[^A-Za-z0-9_./ -]/g, '').replace(/\s+/g, '-')
    if (!p || !project) return
    if (!/\.(sol|js|ts)$/.test(p)) p += '.sol'
    if (!p.includes('/')) p = `${isTest(p) ? 'test' : 'contracts'}/${p}`
    const name = stem(p).replace(/[^A-Za-z0-9_]/g, '') || 'Contract'
    projects.addFile(p, isTest(p) ? STARTER_TEST(name) : isContract(p) ? STARTER_CONTRACT(name) : '')
    setNewFile(''); setAddingFile(false)
  }

  // A template card says what it is — the old row of one-word buttons only did
  // on hover, which a phone never gets.
  const templateCards = (
    templatesErr ? (
      <Banner title="TEMPLATES DIDN'T LOAD" onRetry={loadTemplates}>{templatesErr}</Banner>
    ) : templatesLoading && templates.length === 0 ? (
      <Skeleton rows={2} height={92} />
    ) : templates.length === 0 ? (
      <Empty>No templates on this chain module.</Empty>
    ) : (
      <div style={{
        display: 'grid', gap: '10px',
        gridTemplateColumns: mobile ? '1fr' : 'repeat(auto-fill, minmax(200px, 1fr))',
      }}>
        {templates.map((t, i) => {
          const c = CARD_COLORS[i % CARD_COLORS.length]
          const files = Object.keys(t.files || {})
          const sol = files.filter(isContract).length
          return (
            <button
              key={t.key}
              onClick={() => fromTemplate(t)}
              className="arc-card"
              style={{
                textAlign: 'left', padding: '14px', cursor: 'pointer',
                // longhand borders: the left edge carries the card's colour,
                // the same bar the marquee pills wear
                borderStyle: 'solid', borderWidth: '3px 3px 3px 5px',
                borderColor: `var(--border-color) var(--border-color) var(--border-color) ${c}`,
                background: 'var(--bg-secondary)', boxShadow: '3px 3px 0 0 rgba(0,0,0,0.35)',
                fontFamily: TERM_FONT, color: 'var(--text-secondary)',
                display: 'flex', flexDirection: 'column', gap: '8px', minHeight: '124px',
                ['--c' as any]: c,
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <Sprite seed={t.key} size={22} />
                <span style={{
                  fontFamily: PIXEL, fontSize: PX.sm, lineHeight: 1.6,
                  color: c, letterSpacing: '0.06em',
                }}>
                  {t.name.toUpperCase()}
                </span>
              </div>
              <div style={{ fontSize: '14px', color: 'var(--text-secondary)', lineHeight: 1.5, flex: 1 }}>
                {t.description || 'contract + tests, ready to run'}
              </div>
              <div style={{
                display: 'flex', alignItems: 'center', gap: '8px',
                fontSize: '13px', color: 'var(--text-tertiary)',
              }}>
                <span>{sol} contract{sol === 1 ? '' : 's'} · {files.length} files</span>
                <span style={{ marginLeft: 'auto', color: c }}>START →</span>
              </div>
            </button>
          )
        })}
      </div>
    )
  )

  if (!project) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
        <div>
          <Label style={{ color: ACCENT }}>START HERE</Label>
          <div style={{
            display: 'grid', gap: '10px',
            gridTemplateColumns: mobile ? '1fr' : 'repeat(auto-fit, minmax(210px, 1fr))',
          }}>
            {STEPS.map((s, i) => (
              <div key={s.label} style={{
                ...panelStyle, padding: '12px 14px',
                display: 'flex', gap: '12px', alignItems: 'flex-start',
              }}>
                <span style={{
                  fontFamily: PIXEL, fontSize: PX.md, lineHeight: 1, color: '#000',
                  background: s.c, boxShadow: `0 0 12px ${s.c}66`,
                  width: '26px', height: '26px', flexShrink: 0,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                }}>
                  {i + 1}
                </span>
                <div style={{ minWidth: 0 }}>
                  <div style={{
                    fontFamily: PIXEL, fontSize: PX.sm, color: s.c,
                    letterSpacing: '0.08em', lineHeight: 1.6, marginBottom: '4px',
                  }}>
                    {s.label}
                  </div>
                  <div style={{ fontFamily: TERM_FONT, fontSize: '14px', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                    {s.note}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
        <div>
          <Label note="contract + tests, ready to run">TEMPLATES</Label>
          {templateCards}
        </div>

        {fleetContracts.length > 0 && (
          <div>
            <Label note={`${fleetContracts.length} already deployed here — click one to call it`}>
              ALREADY ON {netInfo(network).name.toUpperCase()}
            </Label>
            <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
              {fleetContracts.map(c => (
                <button
                  key={c.name}
                  onClick={() => playFleet(c.name, c.address)}
                  disabled={!!openingFleet}
                  className="arc-card"
                  title={c.address}
                  style={{
                    ...panelStyle, padding: '8px 12px', cursor: openingFleet ? 'wait' : 'pointer',
                    fontFamily: TERM_FONT, fontSize: '14px', color: 'var(--text-secondary)',
                    display: 'flex', alignItems: 'center', gap: '9px',
                    ['--c' as any]: ACCENT,
                  }}
                >
                  <Sprite seed={c.address} size={16} />
                  <span style={{ color: 'var(--text-primary)' }}>
                    {openingFleet === c.name ? 'opening…' : c.name}
                  </span>
                  <span style={{ fontSize: '13px', color: 'var(--text-tertiary)' }}>
                    {short(c.address, 6, 4)}
                  </span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>

      {/* ── Editor ── */}
      <div style={{ ...panelStyle }}>
        {/* Files are tabs across the top of the editor — contracts first, then
            tests. The rail that used to list them is gone; this is the list. */}
        <div style={{
          display: 'flex', alignItems: 'stretch', flexWrap: 'wrap',
          borderBottom: '2px solid var(--border-color)',
        }}>
          {files.map(f => {
            const active = f === path
            const color = isContract(f) ? ACCENT : isTest(f) ? READ : 'var(--text-secondary)'
            return (
              <div key={f} style={{ display: 'flex', alignItems: 'stretch' }}>
                <button
                  onClick={() => projects.setActiveFile(f)}
                  title={f}
                  style={{
                    fontFamily: TERM_FONT, fontSize: mobile ? '15px' : '14px',
                    padding: mobile ? '11px 12px' : '9px 12px', minHeight: '40px',
                    border: 'none', borderBottom: `3px solid ${active ? color : 'transparent'}`,
                    marginBottom: '-2px',
                    background: active ? `${color}14` : 'transparent',
                    color: active ? color : 'var(--text-tertiary)',
                    cursor: 'pointer', whiteSpace: 'nowrap',
                  }}
                >
                  {f.split('/').pop()}
                </button>
                {active && files.length > 1 && (
                  <button
                    onClick={() => { if (confirm(`Delete ${f}?`)) projects.deleteFile(f) }}
                    title={`delete ${f}`}
                    style={{
                      fontFamily: TERM_FONT, fontSize: '13px', padding: '0 8px', border: 'none',
                      background: `${color}14`, color: 'var(--text-tertiary)', cursor: 'pointer',
                      marginBottom: '-2px', borderBottom: `3px solid ${color}`,
                    }}
                  >
                    ✕
                  </button>
                )}
              </div>
            )
          })}
          <button
            onClick={() => setAddingFile(a => !a)}
            title="new file"
            style={{
              fontFamily: TERM_FONT, fontSize: '16px', padding: '0 12px', minHeight: '40px', border: 'none',
              background: 'transparent', color: addingFile ? ACCENT : 'var(--text-tertiary)', cursor: 'pointer',
            }}
          >
            +
          </button>
          <div style={{
            marginLeft: 'auto', display: 'flex', gap: '8px', alignItems: 'center',
            padding: '5px 10px', flexWrap: 'wrap',
          }}>
            <Btn size="sm" active={optimize} onClick={() => setOptimize(o => !o)}>
              OPT {optimize ? 'ON' : 'OFF'}
            </Btn>
            <span style={{
              fontFamily: TERM_FONT, fontSize: '13px',
              color: projects.dirty ? NEON.coin : 'var(--text-tertiary)',
            }}>
              {projects.saving ? 'saving…' : projects.dirty ? 'unsaved' : 'saved'}
            </span>
            <Btn size="sm" active={false} onClick={() => projects.save().catch(() => {})}>SAVE</Btn>
          </div>
        </div>

        {addingFile && (
          <div style={{
            display: 'flex', gap: '6px', alignItems: 'center', flexWrap: 'wrap',
            padding: '8px 10px', borderBottom: '1px solid var(--border-color)',
          }}>
            <div style={{ flex: 1, minWidth: '180px' }}>
              <Input value={newFile} onChange={setNewFile} placeholder="Vault.sol / Vault.test.js" onEnter={createFile} />
            </div>
            <Btn size="sm" onClick={createFile} disabled={!newFile.trim()}>ADD</Btn>
            <Btn size="sm" active={false} onClick={() => { setAddingFile(false); setNewFile('') }}>CANCEL</Btn>
          </div>
        )}

        <div style={{ display: 'flex', maxHeight: mobile ? '56vh' : '460px' }}>
          {/* the gutter costs a phone a tenth of its width — drop it there */}
          {!mobile && (
            <div
              ref={gutterRef}
              style={{
                fontFamily: TERM_FONT, fontSize: '14px', lineHeight: '20px',
                padding: '12px 8px 12px 12px', textAlign: 'right', userSelect: 'none',
                color: 'var(--text-tertiary)', opacity: 0.5, overflow: 'hidden',
                borderRight: '1px solid var(--border-color)', minWidth: '46px',
              }}
            >
              {Array.from({ length: lineCount }, (_, i) => <div key={i}>{i + 1}</div>)}
            </div>
          )}
          <textarea
            value={source}
            onChange={e => projects.writeFile(path, e.target.value)}
            onScroll={e => { if (gutterRef.current) gutterRef.current.scrollTop = e.currentTarget.scrollTop }}
            spellCheck={false}
            autoCapitalize="off"
            autoCorrect="off"
            disabled={!path}
            style={{
              flex: 1, minHeight: mobile ? '46vh' : '340px', maxHeight: mobile ? '56vh' : '460px',
              fontFamily: TERM_FONT,
              // 16px or iOS zooms the whole console the moment you tap in
              fontSize: mobile ? '16px' : '14px', lineHeight: mobile ? '22px' : '20px',
              padding: '12px', border: 'none', background: 'transparent',
              color: 'var(--text-primary)', outline: 'none', resize: 'vertical',
              whiteSpace: 'pre', overflow: 'auto',
            }}
          />
        </div>
      </div>

      {/* ── Compile ── */}
      <div style={{ display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }}>
        <Btn onClick={compile} disabled={compiling} full>
          {compiling ? 'COMPILING…' : '▶ COMPILE PROJECT'}
        </Btn>
        <span style={{ fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-tertiary)', lineHeight: 1.5 }}>
          solc 0.8.26 · @openzeppelin/contracts, @chainlink and hardhat/console.sol are installed ·
          {' '}foundry lib/ imports and ./Other.sol resolve inside the project
        </span>
      </div>

      {/* ── Diagnostics ── */}
      {(errors.length > 0 || warnings.length > 0) && (
        <div style={{
          ...panelStyle, padding: '12px', fontFamily: TERM_FONT, fontSize: '14px',
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
                    <div style={{ fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-tertiary)', marginBottom: '4px' }}>
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
            <Btn onClick={deploy} disabled={deploying || !wallet.kind} full>
              {deploying ? 'DEPLOYING…' : `▲ DEPLOY${wallet.kind ? ` [${wallet.kind.toUpperCase()}]` : ''}`}
            </Btn>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <span style={{ fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-tertiary)' }}>VALUE</span>
              <div style={{ width: '120px' }}>
                <Input value={value} onChange={setValue} placeholder="0.0 ETH" />
              </div>
            </div>
            {!wallet.kind && (
              <span style={{ fontFamily: TERM_FONT, fontSize: '13px', color: '#f59e0b' }}>
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
          <div style={{ fontFamily: TERM_FONT, fontSize: '14px', color: 'var(--text-secondary)', wordBreak: 'break-all' }}>
            {deployed.address}
          </div>
          {deployed.abiCid && (
            <div style={{
              fontFamily: TERM_FONT, fontSize: '13px', color: READ, marginTop: '8px',
              wordBreak: 'break-all', lineHeight: 1.5,
            }}>
              ABI in the store — {deployed.abiCid}
            </div>
          )}
          <div style={{ display: 'flex', gap: '8px', marginTop: '12px', flexWrap: 'wrap' }}>
            <Btn size="sm" active={false} onClick={() => {
              navigator.clipboard.writeText(deployed.address); toast.success('Address copied')
            }}>
              COPY
            </Btn>
            {deployed.abiCid && (
              <Btn size="sm" active={false} color={READ} title="load this ABI anywhere by CID"
                onClick={() => {
                  navigator.clipboard.writeText(deployed.abiCid!); toast.success('ABI CID copied')
                }}>
                COPY ABI CID
              </Btn>
            )}
            <Btn size="sm" onClick={() => onInteract({
              name: artifact.name, address: deployed.address, abi: artifact.abi, abiCid: deployed.abiCid,
            })}>
              → INTERACT
            </Btn>
            {explorerUrl(network, deployed.address) && (
              <a href={explorerUrl(network, deployed.address)} target="_blank" rel="noreferrer"
                style={{
                  fontFamily: TERM_FONT, fontSize: '13px', padding: '4px 10px',
                  border: '1px solid var(--border-color)', color: 'var(--text-tertiary)', textDecoration: 'none',
                }}>
                CONTRACT ↗
              </a>
            )}
            {deployed.tx && txUrl(network, deployed.tx) && (
              <a href={txUrl(network, deployed.tx)} target="_blank" rel="noreferrer"
                style={{
                  fontFamily: TERM_FONT, fontSize: '13px', padding: '4px 10px',
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
            <div style={{ minWidth: 0 }}>
              <div style={{ fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-primary)' }}>{b.name}</div>
              <div style={{ fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-tertiary)' }}>
                {short(b.address, 10, 8)}
              </div>
              {b.abi_cid && (
                <button
                  onClick={() => {
                    navigator.clipboard.writeText(b.abi_cid!); toast.success('ABI CID copied')
                  }}
                  title="copy this build's ABI CID"
                  style={{
                    fontFamily: TERM_FONT, fontSize: '13px', color: READ, background: 'none',
                    border: 'none', padding: '2px 0 0', cursor: 'pointer', textAlign: 'left',
                  }}
                >
                  abi {short(b.abi_cid, 8, 6)} ⧉
                </button>
              )}
            </div>
            <div style={{ display: 'flex', gap: '6px' }}>
              <Btn size="sm" active={false}
                onClick={() => onInteract({ name: b.name, address: b.address, abi: b.abi, abiCid: b.abi_cid })}>
                INTERACT
              </Btn>
              {explorerUrl(network, b.address) && (
                <a href={explorerUrl(network, b.address)} target="_blank" rel="noreferrer"
                  style={{
                    fontFamily: TERM_FONT, fontSize: '13px', padding: '4px 8px',
                    border: '1px solid var(--border-color)', color: 'var(--text-tertiary)', textDecoration: 'none',
                  }}>
                  EXPLORER ↗
                </a>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* ── Start another one ── */}
      <div>
        <Label>NEW PROJECT FROM TEMPLATE</Label>
        {templateCards}
      </div>
    </div>
  )
}
