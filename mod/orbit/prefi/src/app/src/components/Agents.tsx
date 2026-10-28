'use client'

/**
 * Agents — vibecoded traders in the local economy.
 *
 * Describe a trader in a sentence, the module writes its strategy
 * (claudecode, or a template when no model answers), it is funded with
 * play money and runs every minute: calls the close in the one-minute
 * paper pool and trades on the sim exchange. Everything here is PAPER.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'react-toastify'
import { API_BASE_URL } from '@/lib/contracts'
import { fmt, px, pct, short } from '@/lib/fmt'
import { Section, Stat, Empty, Tag, Label, Spinner, Avatar } from '@/components/ui'

const API = API_BASE_URL

const get = async (url: string) => {
  try { const r = await fetch(url); return r.ok ? r.json() : null } catch { return null }
}
const send = async (url: string, method: string, body?: any) => {
  const r = await fetch(url, {
    method, headers: body ? { 'content-type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  const data = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(data?.detail || data?.error || `${r.status}`)
  return data
}

const IDEAS = [
  'ride the trend: chase whatever moved up last minute',
  'fade every dip — buy below the 5-minute mean, sell above',
  'accumulate a little of everything, call the close flat',
  'a degen whale: all in on the most volatile asset',
  'random coin flips, the baseline to beat',
]

function Spark({ series, up }: { series: number[]; up: boolean }) {
  if (!series || series.length < 2) return <svg width="84" height="24" />
  const lo = Math.min(...series), hi = Math.max(...series)
  const w = 84, h = 24
  const pts = series.map((v, i) => `${(i / (series.length - 1)) * w},${hi === lo ? h / 2 : h - ((v - lo) / (hi - lo)) * (h - 2) - 1}`)
  return (
    <svg width={w} height={h} className="shrink-0">
      <polyline points={pts.join(' ')} fill="none" stroke={up ? 'var(--up)' : 'var(--down)'} strokeWidth="1.4" />
    </svg>
  )
}

function Countdown({ at }: { at: number }) {
  const [now, setNow] = useState(Date.now() / 1000)
  useEffect(() => { const t = setInterval(() => setNow(Date.now() / 1000), 1000); return () => clearInterval(t) }, [])
  const s = Math.max(0, Math.floor(at - now))
  return <span className="num">{s}s</span>
}

const ago = (t?: number | null) => (t ? `${Math.max(0, Math.floor(Date.now() / 1000 - t))}s ago` : '—')

export default function Agents({ address }: { address?: string }) {
  const [eco, setEco] = useState<any>(null)
  const [hist, setHist] = useState<Record<string, number[][]>>({})
  const [agents, setAgents] = useState<any[]>([])
  const [board, setBoard] = useState<any[]>([])
  const [sel, setSel] = useState<string | null>(null)
  const [detail, setDetail] = useState<any>(null)

  // composer
  const [name, setName] = useState('')
  const [prompt, setPrompt] = useState('')
  const [code, setCode] = useState('')
  const [draft, setDraft] = useState<any>(null)
  const [writing, setWriting] = useState(false)
  const [creating, setCreating] = useState(false)
  const [showCode, setShowCode] = useState(false)

  // detail editing
  const [editCode, setEditCode] = useState('')
  const [saving, setSaving] = useState(false)
  const [confirmDel, setConfirmDel] = useState(false)
  const codeRef = useRef<HTMLTextAreaElement>(null)

  const refresh = useCallback(async () => {
    const [e, p, a, b] = await Promise.all([
      get(`${API}/economy`), get(`${API}/economy/prices?history=true&ticks=48`),
      get(`${API}/agents`), get(`${API}/economy/leaderboard?limit=20`),
    ])
    if (e) setEco(e)
    if (p?.history) setHist(p.history)
    if (a) setAgents(a)
    if (b) setBoard(b)
  }, [])

  const loadDetail = useCallback(async (id: string) => {
    const d = await get(`${API}/agents/${id}`)
    if (d) { setDetail(d); setEditCode(c => (c && c !== d.code && saving ? c : d.code)) }
  }, [saving])

  useEffect(() => { refresh(); const t = setInterval(refresh, 5000); return () => clearInterval(t) }, [refresh])
  useEffect(() => {
    if (!sel) { setDetail(null); return }
    loadDetail(sel); const t = setInterval(() => loadDetail(sel), 5000); return () => clearInterval(t)
  }, [sel, loadDetail])
  useEffect(() => { if (detail?.code && !editCode) setEditCode(detail.code) }, [detail, editCode])

  const prices = eco?.prices || {}
  const running = agents.filter(a => a.status === 'running').length

  const write = async () => {
    if (!prompt.trim()) { toast.warn('Describe the agent first'); return }
    setWriting(true); setDraft(null)
    try {
      const r = await send(`${API}/agents/vibecode`, 'POST', { prompt })
      setDraft(r); setCode(r.code); setShowCode(true)
      toast.success(r.source === 'claudecode' ? 'Strategy written by Claude' : `Template: ${r.template}`)
    } catch (e: any) { toast.error(e.message) } finally { setWriting(false) }
  }

  const create = async () => {
    const nm = name.trim() || prompt.trim().split(/[,.:;—-]/)[0].slice(0, 28) || 'agent'
    if (!prompt.trim() && !code.trim()) { toast.warn('Describe the agent or paste code'); return }
    setCreating(true)
    try {
      const body: any = { name: nm, prompt, owner: address }
      if (code.trim()) body.code = code
      const r = await send(`${API}/agents`, 'POST', body)
      toast.success(`${r.name} is live — funded and running every minute`)
      setName(''); setPrompt(''); setCode(''); setDraft(null); setShowCode(false)
      setSel(r.id); refresh()
    } catch (e: any) { toast.error(e.message) } finally { setCreating(false) }
  }

  const act = async (id: string, what: 'pause' | 'resume' | 'run' | 'delete' | 'revibe') => {
    try {
      if (what === 'delete') { await send(`${API}/agents/${id}`, 'DELETE'); if (sel === id) setSel(null); toast.info('Agent removed') }
      else if (what === 'run') { const r = await send(`${API}/agents/${id}/run`, 'POST'); toast.success(`ran: ${r.actions.length} actions${r.error ? ` · ${r.error}` : ''}`) }
      else if (what === 'revibe') { toast.info('Rewriting from the prompt…'); await send(`${API}/agents/${id}`, 'POST', { revibe: true }); toast.success('Rewritten'); setEditCode('') }
      else await send(`${API}/agents/${id}`, 'POST', { status: what === 'pause' ? 'paused' : 'running' })
      refresh(); if (sel) loadDetail(sel)
    } catch (e: any) { toast.error(e.message) }
    setConfirmDel(false)
  }

  const saveCode = async () => {
    if (!detail) return
    setSaving(true)
    try { await send(`${API}/agents/${detail.id}`, 'POST', { code: editCode }); toast.success('Strategy saved'); loadDetail(detail.id) }
    catch (e: any) { toast.error(e.message) } finally { setSaving(false) }
  }

  const mcpHttp = typeof window !== 'undefined' ? `${location.origin}${API}/mcp` : `${API}/mcp`

  return (
    <div className="space-y-5">
      {/* Economy strip */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <Stat label="minute round" value={eco ? <span className="num">#{eco.round}</span> : '—'}
          sub={eco ? <>closes in <Countdown at={eco.round_closes} /></> : undefined} tone="accent" />
        <Stat label="feed" value={eco ? (eco.feed === 'live' ? 'live anchored' : 'simulated') : '—'}
          sub={eco ? `tick ${eco.ticks} · ${ago(eco.last_tick)}` : undefined} />
        <Stat label="agents" value={<span className="num">{running}<span className="t3 text-sm"> / {agents.length}</span></span>} sub="running every minute" />
        <Stat label="exchange" value={eco ? <span className="num">{fmt(eco.volume, 0)}</span> : '—'} sub={eco ? `${eco.trades} fills · ${eco.accounts} accounts` : undefined} />
        <Stat label="pool" value={eco ? <span className="num">{fmt(eco.pool?.minted || 0, 0)}</span> : '—'}
          sub={eco ? `PAPER minted · ${eco.pool?.entries} log entries` : undefined} tone="violet" />
      </div>

      {/* Prices */}
      <div className="flex gap-2 overflow-x-auto pb-1">
        {Object.entries(prices).map(([sym, p]: any) => {
          const series = (hist[sym] || []).map(x => x[1])
          const up = (p.change_1m || 0) >= 0
          return (
            <div key={sym} className="card-flat px-3 py-2 flex items-center gap-3 shrink-0 min-w-[200px]">
              <Avatar symbol={sym} size="sm" />
              <div className="leading-tight">
                <div className="text-xs font-semibold text-white">{sym}</div>
                <div className="num text-sm">{px(p.price)}</div>
              </div>
              <div className="ml-auto text-right">
                <Spark series={series} up={up} />
                <div className={`num text-[11px] ${up ? 'up' : 'down'}`}>{p.change_1m != null ? `${up ? '+' : ''}${(p.change_1m * 100).toFixed(2)}% 1m` : ''}</div>
              </div>
            </div>
          )
        })}
      </div>

      <div className="grid lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] gap-5 items-start">
        {/* Composer */}
        <Section title="Vibecode an agent" sub="a sentence becomes a strategy that runs every minute">
          <div className="p-4 space-y-3">
            <div>
              <Label hint="optional">Name</Label>
              <input className="input" value={name} onChange={e => setName(e.target.value)} placeholder="e.g. dipbuyer" />
            </div>
            <div>
              <Label hint={<span>predicts the close, buys and sells PAPER</span>}>What should it do?</Label>
              <textarea className="input min-h-[88px]" value={prompt} onChange={e => setPrompt(e.target.value)}
                placeholder="A cautious mean-reversion trader: buy BTC when it is 0.3% under its 5-minute average, call a bounce halfway back, stake 5 on every call…" />
              <div className="flex flex-wrap gap-1.5 mt-2">
                {IDEAS.map(i => <button key={i} className="chip" onClick={() => setPrompt(i)}>{i}</button>)}
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button className="btn btn-secondary" onClick={write} disabled={writing}>
                {writing ? <><Spinner /> writing…</> : 'Write the strategy'}
              </button>
              <button className="btn btn-primary" onClick={create} disabled={creating}>
                {creating ? <><Spinner /> creating…</> : 'Create & run'}
              </button>
              <button className="btn btn-ghost btn-sm ml-auto" onClick={() => setShowCode(s => !s)}>{showCode ? 'hide code' : 'code'}</button>
            </div>
            {draft && (
              <div className="note text-xs">
                <Tag tone={draft.source === 'claudecode' ? 'accent' : 'warn'}>{draft.source === 'claudecode' ? 'written by Claude' : `template · ${draft.template}`}</Tag>
                <span className="ml-2 t2">dry run → {draft.dry_run?.length || 0} actions</span>
                {draft.notes?.length ? <div className="t3 mt-1">{draft.notes.join(' · ')}</div> : null}
              </div>
            )}
            {showCode && (
              <div>
                <Label hint={<span>def decide(ctx) → actions · math, random in scope · no imports</span>}>Strategy</Label>
                <textarea ref={codeRef} className="input mono text-xs min-h-[260px]" value={code} onChange={e => setCode(e.target.value)}
                  placeholder={"def decide(ctx):\n    return [{'op': 'predict', 'asset': 'BTC', 'price': ctx['prices']['BTC'], 'stake': 5}]"} spellCheck={false} />
                <div className="text-[11px] t3 mt-1">Create with code filled in installs it as written; leave it empty to vibecode from the prompt on create.</div>
              </div>
            )}
          </div>
        </Section>

        {/* Roster */}
        <Section title="Agents" count={agents.length} sub={`${running} running · ${agents.length - running} paused`}>
          {agents.length === 0 ? (
            <Empty title="No agents yet" msg="Describe one on the left. It gets 1,000 PAPER cash and 1,000 PAPER pool balance, then plays every minute." />
          ) : (
            <div>
              <div className="thead grid grid-cols-[1fr_90px_70px_70px_auto] px-4"><span>agent</span><span className="text-right">net worth</span><span className="text-right">accuracy</span><span className="text-right">runs</span><span /></div>
              {agents.map(a => {
                const b = a.board || {}
                const pnl = (b.net_worth || 0) - 2000       // every agent starts with 1,000 cash + 1,000 pool
                return (
                  <div key={a.id} onClick={() => setSel(a.id)}
                    className={`trow grid grid-cols-[1fr_90px_70px_70px_auto] px-4 cursor-pointer items-center ${sel === a.id ? 'mine' : ''}`}>
                    <div className="flex items-center gap-2.5 min-w-0">
                      <span className={`dot ${a.status === 'running' ? 'dot-live' : ''}`} style={a.status !== 'running' ? { background: 'var(--t3)' } : undefined} />
                      <div className="min-w-0">
                        <div className="text-sm text-white font-medium flex items-center gap-2 min-w-0">
                          <span className="truncate">{a.name}</span>
                          {a.source === 'claudecode' && <span className="tag tag-accent shrink-0">claude</span>}
                          {a.source === 'template' && <span className="tag tag-neutral shrink-0">{a.template}</span>}
                          {a.errors > 0 && <span className="tag tag-warn shrink-0">{a.errors} err</span>}
                        </div>
                        <div className="text-[11px] t3 truncate">{a.prompt || short(a.address)} · {ago(a.last_run)}</div>
                      </div>
                    </div>
                    <div className={`num text-sm text-right ${pnl >= 0 ? 'up' : 'down'}`} title={`${pnl >= 0 ? '+' : ''}${fmt(pnl, 2)} since start`}>{fmt(b.net_worth || 0, 1)}</div>
                    <div className="num text-xs text-right">{b.accuracy != null ? pct(b.accuracy, 0) : '—'}</div>
                    <div className="num text-xs text-right t2">{a.runs}</div>
                    <div className="flex gap-1 justify-end pl-2" onClick={e => e.stopPropagation()}>
                      <button className="btn btn-ghost btn-xs" onClick={() => act(a.id, a.status === 'running' ? 'pause' : 'resume')}>{a.status === 'running' ? 'pause' : 'resume'}</button>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </Section>
      </div>

      {/* Detail */}
      {detail && (
        <Section title={<span className="flex items-center gap-2">{detail.name} <span className="t3 mono text-xs font-normal">{short(detail.address)}</span></span>}
          action={
            <div className="flex gap-1.5">
              <button className="btn btn-ghost btn-xs" onClick={() => act(detail.id, 'run')}>run now</button>
              <button className="btn btn-ghost btn-xs" onClick={() => act(detail.id, detail.status === 'running' ? 'pause' : 'resume')}>{detail.status === 'running' ? 'pause' : 'resume'}</button>
              {detail.prompt && <button className="btn btn-ghost btn-xs" onClick={() => act(detail.id, 'revibe')}>rewrite from prompt</button>}
              <button className={`btn btn-xs ${confirmDel ? 'btn-down' : 'btn-ghost'}`} onClick={() => confirmDel ? act(detail.id, 'delete') : setConfirmDel(true)} onBlur={() => setConfirmDel(false)}>
                {confirmDel ? 'sure? delete' : 'delete'}
              </button>
              <button className="btn btn-ghost btn-xs" onClick={() => setSel(null)}>close</button>
            </div>
          }>
          <div className="grid md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-0 divide-y md:divide-y-0 md:divide-x divide-white/[0.06]">
            <div className="p-4 space-y-3">
              {detail.prompt && <div className="text-sm t2 italic">“{detail.prompt}”</div>}
              <div className="grid grid-cols-3 gap-3">
                <Stat label="cash" value={<span className="num">{fmt(detail.account?.cash || 0, 0)}</span>} sub="PAPER on the exchange" />
                <Stat label="pool" value={<span className="num">{fmt(detail.account?.pool?.balance || 0, 0)}</span>}
                  sub={detail.account?.pool?.accuracy != null ? `${pct(detail.account.pool.accuracy, 0)} accuracy · ${detail.account.pool.resolved} scored` : 'no calls scored yet'} tone="violet" />
                <Stat label="net worth" value={<span className="num">{fmt(detail.account?.net_worth || 0, 0)}</span>}
                  sub={`${detail.account?.trades || 0} fills · realized ${fmt(detail.account?.realized || 0, 1)}`}
                  tone={(detail.account?.net_worth || 0) >= 2000 ? 'up' : 'down'} />
              </div>
              {detail.account?.holdings && Object.keys(detail.account.holdings).length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {Object.entries(detail.account.holdings).map(([s, h]: any) => (
                    <span key={s} className={`chip ${h.unrealized >= 0 ? 'up' : 'down'}`} title={`avg ${px(h.avg)}`}>{s} {fmt(h.value, 0)} <span className="t3">({h.unrealized >= 0 ? '+' : ''}{fmt(h.unrealized, 1)})</span></span>
                  ))}
                </div>
              )}
              <div>
                <Label hint={detail.last_error ? <span className="warn">{detail.last_error}</span> : <span>{detail.runs} runs · {detail.actions} actions</span>}>Log</Label>
                <div className="max-h-[300px] overflow-y-auto space-y-1 text-xs">
                  {[...(detail.log || [])].reverse().map((e: any, i: number) => (
                    <div key={i} className="receipt">
                      <div className="flex justify-between t3"><span>round #{e.round}</span><span>{ago(e.time)}</span></div>
                      {e.error && <div className="warn">{e.error}</div>}
                      {e.actions.length === 0 && !e.error && <div className="t3">held</div>}
                      {e.actions.map((a: any, j: number) => (
                        <div key={j} className={`flex gap-2 ${a.error ? 't3' : ''}`}>
                          <span className={`mono ${a.op === 'buy' ? 'up' : a.op === 'sell' ? 'down' : a.op === 'predict' ? 'violet' : 't2'}`}>{a.op}</span>
                          <span className="text-white">{a.asset}</span>
                          {a.op === 'predict' && <span className="num">{px(a.price)} · stake {fmt(a.stake, 0)}</span>}
                          {(a.op === 'buy' || a.op === 'sell') && <span className="num">{a.notional != null ? `${fmt(a.notional, 0)} PAPER` : `${a.qty} units`}{a.price ? ` @ ${px(a.price)}` : ''}</span>}
                          {a.op === 'note' && <span className="t2">{a.text}</span>}
                          {a.error && <span className="warn truncate">· {a.error}</span>}
                        </div>
                      ))}
                    </div>
                  ))}
                  {!detail.log?.length && <div className="t3">Not run yet — it fires within a second of the next minute.</div>}
                </div>
              </div>
            </div>
            <div className="p-4 space-y-2">
              <Label hint={<span>{detail.source === 'claudecode' ? 'written by Claude' : detail.source === 'template' ? `template · ${detail.template}` : 'hand-written'} · edit and save, it recompiles</span>}>Strategy</Label>
              <textarea className="input mono text-xs min-h-[420px]" value={editCode} onChange={e => setEditCode(e.target.value)} spellCheck={false} />
              <div className="flex gap-2">
                <button className="btn btn-primary btn-sm" onClick={saveCode} disabled={saving || editCode === detail.code}>{saving ? <Spinner /> : 'Save strategy'}</button>
                <button className="btn btn-ghost btn-sm" onClick={() => setEditCode(detail.code)} disabled={editCode === detail.code}>revert</button>
              </div>
              {detail.memory && Object.keys(detail.memory).length > 0 && (
                <div>
                  <Label>Memory</Label>
                  <pre className="mono text-[11px] t2 whitespace-pre-wrap break-all max-h-[120px] overflow-y-auto">{JSON.stringify(detail.memory, null, 1)}</pre>
                </div>
              )}
            </div>
          </div>
        </Section>
      )}

      <div className="grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-5 items-start">
        {/* Leaderboard */}
        <Section title="Leaderboard" sub="net worth = cash + holdings + pool">
          {board.length === 0 ? <Empty title="Nobody has played yet" /> : (
            <div>
              <div className="thead grid grid-cols-[28px_1fr_90px_70px_60px] px-4"><span>#</span><span>who</span><span className="text-right">net worth</span><span className="text-right">accuracy</span><span className="text-right">fills</span></div>
              {board.map((r, i) => (
                <div key={r.address} className={`trow grid grid-cols-[28px_1fr_90px_70px_60px] px-4 items-center ${agents.some(a => a.address === r.address && a.id === sel) ? 'mine' : ''}`}>
                  <span className={`rank ${i === 0 ? 'rank-1' : ''}`}>{i + 1}</span>
                  <span className="text-sm truncate">{r.name || short(r.address)}{r.name ? '' : <span className="tag tag-neutral ml-2">human</span>}</span>
                  <span className={`num text-sm text-right ${r.net_worth >= 2000 ? 'up' : 'down'}`}>{fmt(r.net_worth, 1)}</span>
                  <span className="num text-xs text-right">{r.accuracy != null ? pct(r.accuracy, 0) : '—'}</span>
                  <span className="num text-xs text-right t2">{r.trades}</span>
                </div>
              ))}
            </div>
          )}
        </Section>

        {/* MCP */}
        <Section title="MCP server" sub="drive the economy from any agent">
          <div className="p-4 space-y-3 text-xs">
            <div className="t2">Every tool the Agents tab uses is an MCP tool: list and vibecode agents, pause, run, trade, predict, read prices and the leaderboard, verify the log. Play money only.</div>
            <div>
              <Label>HTTP (this server)</Label>
              <pre className="receipt mono whitespace-pre-wrap break-all">claude mcp add --transport http prefi {mcpHttp}</pre>
            </div>
            <div>
              <Label>stdio (same store, on this host)</Label>
              <pre className="receipt mono whitespace-pre-wrap break-all">claude mcp add prefi -- python3 /root/mod/mod/orbit/prefi/src/mcp_server.py</pre>
            </div>
            <div className="t3">Tools: prefi_economy · prefi_prices · prefi_agents · prefi_agent · prefi_agent_create · prefi_agent_update · prefi_agent_run · prefi_agent_delete · prefi_vibecode · prefi_strategy_check · prefi_strategy_language · prefi_faucet · prefi_predict · prefi_trade · prefi_account · prefi_leaderboard · prefi_round · prefi_rounds · prefi_trades · prefi_tick · prefi_verify</div>
            <div className="t3">REST: <span className="mono">GET /economy</span>, <span className="mono">POST /agents {'{'}name, prompt | code{'}'}</span>, <span className="mono">POST /economy/trade</span>, <span className="mono">POST /economy/predict</span>.</div>
          </div>
        </Section>
      </div>
    </div>
  )
}
