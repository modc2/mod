"use client"

// AGENT — hand the open project to Claude Code. The run goes through the
// orbit/agent module (its shipped `chain-mod` agent → the chain module's own
// harness runner), the same road the build console's runs take: the agent
// module gates it, files it in its task ledger, and streams the steps back
// here. Claude works in a sandboxed Hardhat copy of the project — edits
// accepted there and nowhere else, a shell that runs hardhat and nothing
// else — and when it finishes, its edits are written back into the project
// and the editor reloads them.

import { useState, useEffect, useCallback, useRef } from 'react'
import { toast } from 'react-toastify'
import {
  TERM_FONT, ACCENT, DANGER, READ, WRITE, CHAIN_API_BASE, chainApi, agentToken, forgetAgentToken,
  short, useIsMobile,
} from './shared'
import { Label, Btn, Empty, Banner, panelStyle } from './ui'
import { PIXEL, PX, NEON } from './arcade'
import type { ChainWallet } from './WalletBar'
import type { ProjectsApi } from './projects'

interface Status {
  available: boolean
  cli?: string | null
  model?: string | null
  agent_up: boolean
  registered: boolean
  owner?: string | null
  owner_only: boolean
  running: number
  concurrency?: number | null
  error?: string | null
  agent: string
  harness: string
}

interface Step {
  tool: string
  params: Record<string, any>
  result?: any
  error?: string
  run?: string
}

interface Run {
  id: string
  project: string
  query: string
  status: string
  started: number
  ended?: number
  summary?: string
  changed?: string[]
  cost_usd?: number | null
  error?: string | null
}

const MODELS = ['sonnet', 'opus', 'haiku']

// One press each — what a builder actually asks an agent for.
const PROMPTS: { label: string; text: string }[] = [
  { label: 'WRITE TESTS', text: 'Write a thorough Mocha/Chai test suite for every contract in this project — happy paths, reverts and edge cases. Run it, and fix anything the run turns up in the tests themselves, not the contracts.' },
  { label: 'FIX FAILING', text: 'Run the tests. For every failure find the root cause, fix it with the smallest change that is right, and re-run until the suite passes.' },
  { label: 'REVIEW', text: 'Review the contracts for security issues — reentrancy, access control, unchecked calls, arithmetic, front-running — and for gas waste. Change nothing: report findings ranked by severity, each with the line and a suggested fix.' },
  { label: 'GAS', text: 'Find gas optimisations in the contracts, apply the ones that are clearly safe, and prove with the tests that behaviour did not change.' },
  { label: 'EXPLAIN', text: 'Explain what each contract in this project does, function by function, in plain language a builder new to Solidity would follow.' },
]

const toolColor = (tool: string) =>
  tool === 'error' ? DANGER
    : tool === 'finish' || tool === 'project' || tool === 'workspace' ? ACCENT
      : tool === 'edit' || tool === 'write' ? WRITE
        : tool === 'bash' ? NEON.coin
          : tool === 'read' || tool === 'glob' || tool === 'grep' || tool === 'ls' ? READ
            : 'var(--text-secondary)'

/** The one line that says what a step was aimed at. */
function stepAim(step: Step): string {
  const p = step.params || {}
  if (step.tool === 'workspace') return `${(p.files || []).length} file(s) laid out as a hardhat project`
  if (step.tool === 'project') return `wrote back: ${(p.changed || []).join(', ')}`
  if (step.tool === 'finish') return p.turns ? `${p.turns} turns${p.cost_usd ? ` · $${Number(p.cost_usd).toFixed(3)}` : ''}` : ''
  if (step.tool === 'response') return ''
  return p.command || p.file_path || p.pattern || p.path || p.description || p.query || p.detail || ''
}

const ago = (t: number) => {
  const s = Math.max(0, Date.now() / 1000 - t)
  if (s < 60) return `${Math.floor(s)}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

export function AgentTab({
  wallet, network, projects,
}: {
  wallet: ChainWallet
  network: string
  projects: ProjectsApi
}) {
  const [status, setStatus] = useState<Status | null>(null)
  const [statusErr, setStatusErr] = useState('')
  const [query, setQuery] = useState('')
  const [model, setModel] = useState('sonnet')
  const [running, setRunning] = useState(false)
  const [steps, setSteps] = useState<Step[]>([])
  const [answer, setAnswer] = useState('')
  const [changed, setChanged] = useState<string[]>([])
  const [runErr, setRunErr] = useState('')
  const [refused, setRefused] = useState(false)
  const [open, setOpen] = useState<Set<number>>(new Set())
  const [runs, setRuns] = useState<Run[]>([])
  const [showRuns, setShowRuns] = useState(false)
  const abort = useRef<AbortController | null>(null)
  const tail = useRef<HTMLDivElement>(null)
  const mobile = useIsMobile()

  const project = projects.project
  const address = wallet.address
  const ownerMismatch = !!(status?.owner_only && status.owner && address
    && status.owner.toLowerCase() !== address.toLowerCase())

  const loadStatus = useCallback(() => {
    chainApi('/agent/status')
      .then(s => { setStatus(s); setStatusErr('') })
      .catch(e => setStatusErr(e?.message || 'could not read agent status'))
  }, [])

  const loadRuns = useCallback(() => {
    chainApi(`/agent/runs?limit=8${address ? `&address=${address}` : ''}`)
      .then(d => setRuns(d.runs || []))
      .catch(() => setRuns([]))
  }, [address])

  useEffect(() => { loadStatus() }, [loadStatus])
  useEffect(() => { loadRuns() }, [loadRuns])
  useEffect(() => {
    if (status?.model && MODELS.includes(status.model)) setModel(status.model)
  }, [status?.model])

  // keep the newest step in view while the run is live
  useEffect(() => {
    if (running) tail.current?.scrollIntoView({ block: 'nearest' })
  }, [steps, running])

  const stop = () => { abort.current?.abort() }

  const run = useCallback(async (text?: string) => {
    const q = (text ?? query).trim()
    if (!q || !project || !address || !wallet.kind) return
    setRunning(true)
    setSteps([])
    setAnswer('')
    setChanged([])
    setRunErr('')
    setRefused(false)
    setOpen(new Set())
    let token = ''
    try {
      token = await agentToken(wallet.kind, address)
    } catch (e: any) {
      setRunning(false)
      setRunErr(e?.message || 'could not sign the agent token')
      return
    }
    // what runs is what's on screen
    await projects.save().catch(() => {})
    const ctl = new AbortController()
    abort.current = ctl
    try {
      const res = await fetch(`${CHAIN_API_BASE}/agent/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: token, query: q, project: project.name, network, model }),
        signal: ctl.signal,
      })
      if (!res.ok || !res.body) {
        let detail = `agent bridge ${res.status}`
        try { detail = (await res.json())?.detail || detail } catch {}
        if (res.status === 401) forgetAgentToken()
        throw new Error(detail)
      }
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      let finished = false
      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        let idx: number
        while ((idx = buf.indexOf('\n\n')) >= 0) {
          const frame = buf.slice(0, idx)
          buf = buf.slice(idx + 2)
          for (const line of frame.split('\n')) {
            if (!line.startsWith('data:')) continue
            let ev: any
            try { ev = JSON.parse(line.slice(5).trim()) } catch { continue }
            if (ev.type === 'step' && ev.step) {
              const step: Step = ev.step
              setSteps(prev => [...prev, step])
              if (step.tool === 'finish') {
                setAnswer(step.params?.summary || '')
                setChanged(step.params?.changed || [])
              }
              if (step.tool === 'error') setRunErr(step.error || 'the run failed')
            } else if (ev.type === 'error') {
              setRunErr(ev.error || 'the run failed')
              if (ev.code === 403 || /owner-only|owner only/i.test(ev.error || '')) setRefused(true)
            } else if (ev.type === 'done') {
              finished = true
            }
          }
        }
      }
      if (finished) {
        // the agent's edits are in the project now — show them
        await projects.open(project.name).catch(() => {})
        toast.success('agent finished')
      }
    } catch (e: any) {
      if (e?.name === 'AbortError') setRunErr('stopped — the run may still be finishing on the host')
      else setRunErr(e?.message || 'the run failed')
    } finally {
      abort.current = null
      setRunning(false)
      loadRuns()
      loadStatus()
    }
  }, [query, project, address, wallet.kind, network, model, projects, loadRuns, loadStatus])

  const toggle = (i: number) => setOpen(prev => {
    const next = new Set(prev)
    if (next.has(i)) next.delete(i); else next.add(i)
    return next
  })

  const mono: React.CSSProperties = { fontFamily: TERM_FONT, fontSize: '14px' }

  if (!project) return <Empty>No project open — pick or start one from the PROJECT pill up top.</Empty>

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>

      {/* who's answering, and whether it can */}
      {statusErr && <Banner tone="warn" title="AGENT STATUS UNKNOWN" onRetry={loadStatus}>{statusErr}</Banner>}
      {status && !status.available && (
        <Banner tone="bad" title="THE AGENT CAN'T RUN HERE" onRetry={loadStatus}>
          {!status.cli ? 'the claude CLI is not installed on this host'
            : !status.agent_up ? `the agent module is not answering at ${(status as any).agent_api}`
              : !status.registered ? `the agent module has no "${status.harness}" harness — restart agent-api`
                : status.error || 'unavailable'}
        </Banner>
      )}
      {!address && (
        <Banner tone="warn" title="SIGN IN TO RUN THE AGENT">
          The run is filed under your address, and the agent module wants a wallet-signed token — pick an account from the ACCOUNT pill up top.
        </Banner>
      )}
      {ownerMismatch && (
        <Banner tone="warn" title="OWNER-ONLY ON THIS HOST">
          The agent runs on this host&apos;s own Claude account, so the agent module only runs it for its owner
          ({short(status!.owner!)}). You are {short(address)} — a run from here will be refused.
        </Banner>
      )}

      <div style={{ ...panelStyle, padding: '12px 14px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap', marginBottom: '10px' }}>
          <Label style={{ marginBottom: 0 }}>ASK THE AGENT</Label>
          <span style={{ ...mono, fontSize: '13px', color: 'var(--text-tertiary)' }}>
            {status?.cli ? `claude code ${status.cli.replace(/\s*\(Claude Code\)/, '')}` : 'claude code'}
            {' · via orbit/agent'}
            {status?.running ? ` · ${status.running} running` : ''}
          </span>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: '6px', alignItems: 'center' }}>
            {MODELS.map(mname => (
              <Btn key={mname} size="sm" active={model === mname} onClick={() => setModel(mname)}
                disabled={running}>{mname.toUpperCase()}</Btn>
            ))}
          </div>
        </div>

        <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginBottom: '10px' }}>
          {PROMPTS.map(p => (
            <Btn key={p.label} size="sm" active={false} disabled={running}
              title={p.text} onClick={() => setQuery(p.text)}>{p.label}</Btn>
          ))}
        </div>

        <textarea
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') run() }}
          placeholder={`what should Claude do with ${project.name}? — e.g. "add a test that transfer() reverts on insufficient balance"`}
          rows={mobile ? 4 : 3}
          disabled={running}
          style={{
            ...mono, width: '100%', resize: 'vertical', padding: '10px 12px',
            border: '2px solid var(--border-color)', background: 'var(--bg-primary)',
            color: 'var(--text-primary)', outline: 'none', lineHeight: 1.5, boxSizing: 'border-box',
          }}
        />

        <div style={{ display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap', marginTop: '10px' }}>
          {running
            ? <Btn onClick={stop} color={DANGER} full>■ STOP</Btn>
            : <Btn onClick={() => run()} disabled={!query.trim() || !address || !status?.available} full>▶ RUN AGENT</Btn>}
          <span style={{ ...mono, fontSize: '13px', color: 'var(--text-tertiary)' }}>
            {running
              ? 'working in a sandboxed copy of the project · edits land back here when it finishes'
              : `on ${project.name} · ${Object.keys(project.files).length} files · ⌘⏎ to run`}
          </span>
        </div>
      </div>

      {(runErr || refused) && !running && (
        <Banner tone="bad" title={refused ? 'THE AGENT MODULE REFUSED THE RUN' : 'THE RUN FAILED'}>
          {runErr}
          {refused && status?.owner && (
            <div style={{ marginTop: '6px' }}>
              Harness runs are owner-only on this host — sign in as {short(status.owner)} to run it.
            </div>
          )}
        </Banner>
      )}

      {(steps.length > 0 || running) && (
        <div style={{ ...panelStyle }}>
          <div style={{
            display: 'flex', alignItems: 'center', gap: '8px', padding: '7px 12px',
            borderBottom: '2px solid var(--border-color)',
            fontFamily: PIXEL, fontSize: PX.xs, letterSpacing: '0.14em', color: 'var(--text-tertiary)',
          }}>
            TRACE
            {running && <span className="arc-blink" style={{ color: NEON.coin }}>● LIVE</span>}
            <span style={{ ...mono, fontSize: '13px', letterSpacing: 'normal', marginLeft: 'auto' }}>
              {steps.length} step{steps.length === 1 ? '' : 's'}
            </span>
          </div>
          <div style={{ padding: '8px 12px', maxHeight: '420px', overflowY: 'auto' }}>
            {steps.map((s, i) => {
              const aim = stepAim(s)
              const body = s.error ?? (s.tool === 'response' ? s.result : undefined)
              const detail = typeof s.result === 'string' ? s.result : s.result != null ? JSON.stringify(s.result, null, 2) : ''
              const expandable = s.tool !== 'response' && s.tool !== 'finish' && (detail || s.params?.old_string || s.params?.content)
              return (
                <div key={i} style={{ padding: '5px 0', borderBottom: '1px solid var(--border-color)' }}>
                  <div
                    onClick={() => expandable && toggle(i)}
                    style={{ display: 'flex', gap: '10px', alignItems: 'baseline', cursor: expandable ? 'pointer' : 'default', flexWrap: 'wrap' }}
                  >
                    <span style={{ fontFamily: PIXEL, fontSize: PX.xs, color: toolColor(s.tool), letterSpacing: '0.08em', minWidth: '64px' }}>
                      {s.tool.toUpperCase()}
                    </span>
                    {aim && (
                      <span style={{ ...mono, color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1, minWidth: 0 }}>
                        {aim}
                      </span>
                    )}
                    {s.error && <span style={{ fontFamily: PIXEL, fontSize: PX.xs, color: DANGER }}>ERR</span>}
                    {expandable && (
                      <span style={{ ...mono, fontSize: '12px', color: 'var(--text-tertiary)' }}>{open.has(i) ? '▲' : '▼'}</span>
                    )}
                  </div>
                  {body && (
                    <pre style={{
                      ...mono, margin: '4px 0 0', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                      color: s.error ? DANGER : 'var(--text-primary)', lineHeight: 1.5,
                    }}>{body}</pre>
                  )}
                  {open.has(i) && !s.error && (
                    <pre style={{
                      ...mono, fontSize: '13px', margin: '6px 0 0', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                      color: 'var(--text-secondary)', maxHeight: '260px', overflow: 'auto',
                      background: 'var(--bg-primary)', padding: '8px', border: '1px solid var(--border-color)',
                    }}>
                      {s.params?.old_string ? `- ${s.params.old_string}\n+ ${s.params.new_string || ''}\n\n` : ''}
                      {s.params?.content ? `${s.params.content}\n\n` : ''}
                      {detail}
                    </pre>
                  )}
                </div>
              )
            })}
            {running && (
              <div style={{ ...mono, color: ACCENT, padding: '6px 0' }}>
                {'> '}{steps.length ? 'thinking' : 'starting claude code'}<span className="arc-blink">…</span>
              </div>
            )}
            <div ref={tail} />
          </div>
        </div>
      )}

      {answer && (
        <div style={{ ...panelStyle, padding: '14px', borderColor: ACCENT }}>
          <Label style={{ color: ACCENT }} note={changed.length ? `${changed.length} file(s) changed — the editor has them` : 'nothing in the project changed'}>
            THE AGENT SAYS
          </Label>
          <pre style={{ ...mono, fontSize: '15px', margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word', lineHeight: 1.55, color: 'var(--text-primary)' }}>
            {answer}
          </pre>
          {changed.length > 0 && (
            <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginTop: '10px' }}>
              {changed.map(f => (
                <Btn key={f} size="sm" active={false} onClick={() => projects.setActiveFile(f)} title="open in BUILD">
                  {f}
                </Btn>
              ))}
            </div>
          )}
        </div>
      )}

      {runs.length > 0 && (
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <Label style={{ marginBottom: 0 }} note="on your projects, newest first">PAST RUNS</Label>
            <Btn size="sm" active={showRuns} onClick={() => setShowRuns(v => !v)} style={{ marginLeft: 'auto' }}>
              {showRuns ? 'HIDE' : `SHOW ${runs.length}`}
            </Btn>
          </div>
          {showRuns && (
            <div style={{ marginTop: '10px', display: 'grid', gap: '6px' }}>
              {runs.map(r => (
                <div key={r.id} style={{ ...panelStyle, padding: '8px 12px', display: 'flex', gap: '10px', alignItems: 'baseline', flexWrap: 'wrap' }}>
                  <span style={{
                    fontFamily: PIXEL, fontSize: PX.xs, letterSpacing: '0.08em',
                    color: r.status === 'completed' ? ACCENT : r.status === 'running' ? NEON.coin : DANGER,
                  }}>
                    {r.status.toUpperCase()}
                  </span>
                  <span style={{ ...mono, fontSize: '13px', color: 'var(--text-tertiary)' }}>{r.project}</span>
                  <span
                    title={r.query}
                    onClick={() => setQuery(r.query)}
                    style={{ ...mono, color: 'var(--text-secondary)', cursor: 'pointer', flex: 1, minWidth: '160px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                  >
                    {r.query}
                  </span>
                  <span style={{ ...mono, fontSize: '13px', color: 'var(--text-tertiary)' }}>
                    {r.changed?.length ? `${r.changed.length} changed · ` : ''}{ago(r.started)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
