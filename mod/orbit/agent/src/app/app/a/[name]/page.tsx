'use client'

// /agent/a/<name> — one agent, standing on its own page.
//
// The hub's AGENTS shelf is the registry browsed and edited inside the
// console; this route is the same agent made linkable — everything the
// folder under src/agents/<name>/ holds (prompt, tools, model, memory,
// owner, harness), its arena standing, and its source, at a URL you can
// hand to someone. Reads only, over the same public endpoints any auditor
// uses; changing the agent is the console's job, one press away.

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams } from 'next/navigation'
import { API_URL } from '../../config'
import { agentFile, type AgentSchema, type Provider } from '../../components/AgentsPanel'

const BASE = process.env.NEXT_PUBLIC_BASE_PATH ?? '/agent'

interface BoardRow {
  agent: string
  rank: number
  elo: number
  matches: number
  win_rate: number
  pass_rate: number
  avg_score: number
}

const modelHref = (providers: Provider[], model: string) => {
  const p = providers.find(pr => pr.models.includes(model))
  if (!p) return null
  return `${BASE}/m/${p.key}/${model.split('/').map(encodeURIComponent).join('/')}`
}

export default function AgentPage() {
  const params = useParams<{ name: string }>()
  const name = (() => {
    try { return decodeURIComponent(params?.name || '') } catch { return params?.name || '' }
  })()

  const [agents, setAgents] = useState<Record<string, AgentSchema>>({})
  const [order, setOrder] = useState<string[]>([])
  const [providers, setProviders] = useState<Provider[]>([])
  const [board, setBoard] = useState<BoardRow | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [source, setSource] = useState<string | null>(null)
  const [srcBusy, setSrcBusy] = useState(false)

  const getJson = useCallback(async (path: string) => {
    const r = await fetch(`${API_URL}${path}`, { signal: AbortSignal.timeout(10000) })
    if (!r.ok) throw new Error(`${path} → ${r.status}`)
    return r.json()
  }, [])

  useEffect(() => {
    if (!name) return
    getJson('/agents')
      .then(d => { setAgents(d.schemas || {}); setOrder(d.agents || Object.keys(d.schemas || {})) })
      .catch(e => setError((e as Error).message))
      .finally(() => setLoading(false))
    getJson('/providers').then(d => setProviders(d.providers || [])).catch(() => {})
    getJson('/arena')
      .then(d => {
        const rows: BoardRow[] = d.leaderboard || d.board || []
        setBoard(rows.find(r => r.agent === name) || null)
      })
      .catch(() => {})
  }, [name, getJson])

  const agent = agents[name]

  const openCode = async () => {
    if (source !== null) { setSource(null); return }
    setSrcBusy(true)
    try {
      const d = await getJson(`/modules/agent/file?path=${encodeURIComponent(agentFile(name))}`)
      if (d?.error) throw new Error(d.error)
      setSource(d.text || '')
    } catch (e) {
      setError(`can't read the source — ${(e as Error).message}`)
    } finally {
      setSrcBusy(false)
    }
  }

  // the console reads this key on load — set it, land there, and the chat
  // is already talking to this agent
  const useInConsole = () => {
    try { localStorage.setItem('agent_type', name) } catch {}
    window.location.href = `${BASE}/`
  }

  const boundModelHref = useMemo(
    () => (agent?.model ? modelHref(providers, agent.model) : null),
    [agent, providers],
  )

  const siblings = useMemo(() => order.filter(n => n !== name), [order, name])

  const card = 'rounded-xl border border-white/[0.06] bg-surface-1'
  const legend = 'text-[9px] uppercase tracking-wider text-gray-600 mb-1.5'

  return (
    <main className="min-h-screen bg-surface-0 text-gray-200">
      <header className="site-header border-b border-white/[0.06] px-4 min-h-12 py-1.5 flex items-center gap-3 bg-surface-0">
        <a href={`${BASE}/`} className="flex items-center gap-2.5 shrink-0" title="Back to the console">
          <div className="brand-mark w-7 h-7 flex items-center justify-center shrink-0">
            <span className="select-none">{'>'}_</span>
          </div>
          <span className="title-gradient uppercase select-none hidden sm:block">agent</span>
        </a>
        <span className="text-[10px] uppercase tracking-wider text-gray-600">/ agents / {name}</span>
        <div className="ml-auto flex items-center gap-2">
          <a href={`${BASE}/m`}
            className="px-2.5 py-1 rounded-md text-[10px] uppercase tracking-wider border border-white/[0.08] text-gray-400 hover:text-gray-200 transition">
            models
          </a>
          <a href={`${BASE}/`}
            className="px-2.5 py-1 rounded-md text-[10px] uppercase tracking-wider border border-white/[0.08] text-gray-400 hover:text-gray-200 transition">
            console
          </a>
        </div>
      </header>

      <div className="max-w-3xl mx-auto p-4 space-y-3">
        {loading && <div className="p-4 text-[11px] text-gray-600 animate-pulse">loading the registry…</div>}
        {error && (
          <div className="px-3 py-2 rounded-lg text-[11px] text-red-300 border border-red-500/25 bg-red-500/[0.07]">
            {error}
          </div>
        )}
        {!loading && !agent && !error && (
          <div className={`${card} p-4 text-xs text-gray-500`}>
            No agent named <span className="font-mono text-gray-300">{name}</span> in the registry.{' '}
            <a href={`${BASE}/`} className="text-emerald-300 hover:text-emerald-200 underline underline-offset-2">back to the console</a>
          </div>
        )}

        {agent && (
          <>
            {/* identity */}
            <div className={`${card} p-4 flex items-start gap-3 flex-wrap`}>
              <span className="w-12 h-12 shrink-0 rounded-lg border border-white/[0.08] bg-white/[0.03] flex items-center justify-center font-mono text-xl text-emerald-300">
                {agent.icon || '>_'}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <h1 className="text-base font-medium text-gray-100">{name}</h1>
                  {agent.builtin && (
                    <span className="text-[8px] uppercase tracking-wider px-1.5 py-0.5 rounded border border-white/[0.08] text-gray-500">shipped</span>
                  )}
                  {agent.harness && (
                    <span className="text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded border border-violet-400/25 bg-violet-400/10 text-violet-300"
                      title="This agent hands the whole run to an external CLI instead of this module's loop">
                      {agent.harness} harness
                    </span>
                  )}
                </div>
                <p className="mt-1 text-xs text-gray-400 leading-relaxed">{agent.description || 'no description'}</p>
                <div className="mt-1.5 font-mono text-[9px] text-gray-600 truncate">
                  {agentFile(name)}
                  {agent.owner && <> · owned by {agent.owner.slice(0, 10)}…{agent.owner_source === 'host' ? ' (host)' : ''}</>}
                </div>
              </div>
              <div className="flex items-center gap-1.5 shrink-0">
                <button onClick={useInConsole}
                  className="px-3 py-1.5 rounded-md text-[11px] uppercase tracking-wider border border-emerald-500/25 bg-emerald-500/15 text-emerald-200 hover:bg-emerald-500/25 transition">
                  use in console
                </button>
                <button onClick={openCode} disabled={srcBusy}
                  className="px-3 py-1.5 rounded-md text-[11px] uppercase tracking-wider border border-white/[0.08] text-gray-400 hover:text-gray-200 transition disabled:opacity-40">
                  {srcBusy ? 'reading…' : source !== null ? 'hide code' : 'open code'}
                </button>
              </div>
            </div>

            {source !== null && (
              <pre className="text-[11px] leading-relaxed text-gray-300 bg-white/[0.03] border border-white/[0.06] rounded-xl p-3 overflow-x-auto whitespace-pre">
                {source}
              </pre>
            )}

            {/* what it runs on */}
            <div className="grid sm:grid-cols-3 gap-3">
              <div className={`${card} p-3`}>
                <div className={legend}>Model</div>
                {agent.model ? (
                  boundModelHref ? (
                    <a href={boundModelHref} className="font-mono text-[11px] text-emerald-300 hover:text-emerald-200 underline underline-offset-2 break-all">
                      {agent.model}
                    </a>
                  ) : (
                    <span className="font-mono text-[11px] text-emerald-300 break-all">{agent.model}</span>
                  )
                ) : (
                  <span className="text-[11px] text-gray-500">
                    {agent.harness ? `the ${agent.harness} CLI's own` : 'module default'}
                  </span>
                )}
                <a href={`${BASE}/m`} className="block mt-1.5 text-[9px] uppercase tracking-wider text-gray-600 hover:text-gray-400 transition">
                  all models →
                </a>
              </div>
              <div className={`${card} p-3`}>
                <div className={legend}>Tools</div>
                {agent.tools?.length ? (
                  <div className="flex flex-wrap gap-1">
                    {agent.tools.map(t => (
                      <span key={t} className="px-1.5 py-0.5 rounded border border-white/[0.07] bg-white/[0.02] font-mono text-[9px] text-gray-400">{t}</span>
                    ))}
                  </div>
                ) : (
                  <span className="text-[11px] text-gray-500">unrestricted — every tool</span>
                )}
              </div>
              <div className={`${card} p-3`}>
                <div className={legend}>Memory</div>
                <span className="font-mono text-[11px] text-violet-300">{agent.memory || 'module default'}</span>
              </div>
            </div>

            {/* the system prompt — what the agent IS */}
            <div className={`${card} p-4`}>
              <div className={legend}>Goal — the system prompt</div>
              <pre className="font-mono text-[11px] leading-relaxed text-gray-300 whitespace-pre-wrap">{agent.goal || '—'}</pre>
            </div>

            {/* arena standing */}
            <div className={`${card} p-4`}>
              <div className="flex items-center gap-2">
                <span className={`${legend} mb-0`}>Arena</span>
                <a href={`${BASE}/arena`} className="ml-auto text-[9px] uppercase tracking-wider text-gray-600 hover:text-gray-400 transition">
                  full board →
                </a>
              </div>
              {board ? (
                <div className="mt-2 grid grid-cols-2 sm:grid-cols-5 gap-1.5">
                  {([
                    ['rank', `#${board.rank}`],
                    ['elo', board.elo],
                    ['matches', board.matches],
                    ['win rate', `${Math.round(board.win_rate * 100)}%`],
                    ['avg score', board.avg_score],
                  ] as const).map(([k, v]) => (
                    <div key={k} className="px-2.5 py-1.5 rounded-md border border-white/[0.06] bg-white/[0.02]">
                      <div className="text-[9px] uppercase tracking-wider text-gray-600">{k}</div>
                      <div className="font-mono text-xs text-emerald-300">{String(v)}</div>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="mt-2 text-[11px] text-gray-600">
                  {agent.arena === false ? 'opted out of the board.' : 'not rated yet — the scheduler qualifies new agents within a day.'}
                </p>
              )}
            </div>

            {/* the rest of the registry, one hop away */}
            {siblings.length > 0 && (
              <div className={`${card} p-4`}>
                <div className={legend}>More agents</div>
                <div className="flex flex-wrap gap-1.5">
                  {siblings.map(n => (
                    <a key={n} href={`${BASE}/a/${encodeURIComponent(n)}`}
                      className="flex items-center gap-1.5 px-2.5 py-1 rounded-md border border-white/[0.07] bg-white/[0.02] text-[11px] text-gray-400 hover:text-gray-200 hover:border-white/[0.14] transition">
                      <span className="font-mono text-[10px] text-gray-600">{agents[n]?.icon || '>_'}</span>
                      {n}
                    </a>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </main>
  )
}
