'use client'

// /agent/m — the model catalog as a page, and /agent/m/<provider>/<model…>
// one page per model. A model id may itself contain slashes
// (anthropic/claude-opus-5), which is why this is a catch-all: the first
// segment is the provider, the rest joined back together is the model.
//
// Same deal as the agent pages: public reads off /providers and /agents,
// a URL you can hand to someone, and "use in console" writes the pick into
// the localStorage keys the console already restores from.

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams } from 'next/navigation'
import { API_URL } from '../../config'
import { type AgentSchema, type Provider } from '../../components/AgentsPanel'

const BASE = process.env.NEXT_PUBLIC_BASE_PATH ?? '/agent'

const dec = (s: string) => { try { return decodeURIComponent(s) } catch { return s } }

const modelPageHref = (provider: string, model: string) =>
  `${BASE}/m/${encodeURIComponent(provider)}/${model.split('/').map(encodeURIComponent).join('/')}`

const statusOf = (p: Provider) =>
  p.keyless ? ['keyless', 'text-emerald-300'] as const
  : p.configured ? (p.encrypted && !p.unlocked ? ['locked', 'text-amber-300'] : ['ready', 'text-emerald-300'] as const)
  : ['no key', 'text-amber-300'] as const

export default function ModelPage() {
  const params = useParams<{ id?: string[] }>()
  const segs = (params?.id || []).map(dec)
  const providerKey = segs[0] || null
  const modelId = segs.length > 1 ? segs.slice(1).join('/') : null

  const [providers, setProviders] = useState<Provider[]>([])
  const [agents, setAgents] = useState<Record<string, AgentSchema>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const getJson = useCallback(async (path: string) => {
    const r = await fetch(`${API_URL}${path}`, { signal: AbortSignal.timeout(10000) })
    if (!r.ok) throw new Error(`${path} → ${r.status}`)
    return r.json()
  }, [])

  useEffect(() => {
    getJson('/providers')
      .then(d => setProviders(d.providers || []))
      .catch(e => setError((e as Error).message))
      .finally(() => setLoading(false))
    getJson('/agents').then(d => setAgents(d.schemas || {})).catch(() => {})
  }, [getJson])

  const provider = providerKey ? providers.find(p => p.key === providerKey) || null : null
  const allModels = useMemo(() => providers.reduce((n, p) => n + p.models.length, 0), [providers])

  // every agent whose folder binds this exact model
  const boundAgents = useMemo(() => {
    if (!modelId) return []
    return Object.entries(agents)
      .filter(([, a]) => a.model === modelId)
      .map(([n, a]) => ({ name: n, icon: a.icon }))
  }, [agents, modelId])

  // the console restores provider + model from these keys on load
  const useInConsole = () => {
    if (!provider || !modelId) return
    try {
      localStorage.setItem('agent_provider', provider.key)
      localStorage.setItem('agent_model', modelId)
    } catch {}
    window.location.href = `${BASE}/`
  }

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
        <span className="text-[10px] uppercase tracking-wider text-gray-600 truncate min-w-0">
          {modelId ? <>/ <a href={`${BASE}/m`} className="hover:text-gray-400 transition">models</a> / {modelId}</> : '/ models'}
        </span>
        <div className="ml-auto flex items-center gap-2 shrink-0">
          <a href={`${BASE}/`}
            className="px-2.5 py-1 rounded-md text-[10px] uppercase tracking-wider border border-white/[0.08] text-gray-400 hover:text-gray-200 transition">
            console
          </a>
        </div>
      </header>

      <div className="max-w-3xl mx-auto p-4 space-y-3">
        {loading && <div className="p-4 text-[11px] text-gray-600 animate-pulse">loading providers…</div>}
        {error && (
          <div className="px-3 py-2 rounded-lg text-[11px] text-red-300 border border-red-500/25 bg-red-500/[0.07]">
            {error}
          </div>
        )}

        {/* ── one model, one page ── */}
        {!loading && modelId && (
          provider && provider.models.includes(modelId) ? (
            <>
              <div className={`${card} p-4 flex items-start gap-3 flex-wrap`}>
                <div className="min-w-0 flex-1">
                  <h1 className="font-mono text-sm text-emerald-300 break-all">{modelId}</h1>
                  <div className="mt-1.5 flex items-center gap-2 flex-wrap text-[10px]">
                    <span className="text-gray-400">{provider.key}</span>
                    {(() => { const [label, tone] = statusOf(provider); return (
                      <span className={`uppercase tracking-wider px-1.5 py-0.5 rounded border border-white/[0.08] ${tone}`}>{label}</span>
                    ) })()}
                    {provider.free && <span className="uppercase tracking-wider text-emerald-300/80">free</span>}
                    {provider.runtime && <span className="text-gray-600">runtime: {provider.runtime}</span>}
                    {provider.default_model === modelId && (
                      <span className="text-gray-500" title="This provider's default">★ provider default</span>
                    )}
                  </div>
                </div>
                <button onClick={useInConsole}
                  className="shrink-0 px-3 py-1.5 rounded-md text-[11px] uppercase tracking-wider border border-emerald-500/25 bg-emerald-500/15 text-emerald-200 hover:bg-emerald-500/25 transition">
                  use in console
                </button>
              </div>

              <div className={`${card} p-4`}>
                <div className={legend}>Agents bound to it</div>
                {boundAgents.length ? (
                  <div className="flex flex-wrap gap-1.5">
                    {boundAgents.map(a => (
                      <a key={a.name} href={`${BASE}/a/${encodeURIComponent(a.name)}`}
                        className="flex items-center gap-1.5 px-2.5 py-1 rounded-md border border-white/[0.07] bg-white/[0.02] text-[11px] text-gray-400 hover:text-gray-200 hover:border-white/[0.14] transition">
                        <span className="font-mono text-[10px] text-gray-600">{a.icon || '>_'}</span>
                        {a.name}
                      </a>
                    ))}
                  </div>
                ) : (
                  <p className="text-[11px] text-gray-600">
                    No agent binds this model directly — agents without a bound model run on the module default.
                  </p>
                )}
              </div>

              <div className={`${card} p-4`}>
                <div className={legend}>Siblings on {provider.key}</div>
                <div className="flex flex-wrap gap-1.5">
                  {provider.models.filter(m => m !== modelId).map(m => (
                    <a key={m} href={modelPageHref(provider.key, m)}
                      className="px-2.5 py-1 rounded-md font-mono text-[10px] border border-white/[0.07] bg-white/[0.02] text-gray-400 hover:text-gray-200 hover:border-white/[0.14] transition">
                      {m}
                    </a>
                  ))}
                </div>
              </div>
            </>
          ) : (
            <div className={`${card} p-4 text-xs text-gray-500`}>
              No model <span className="font-mono text-gray-300">{modelId}</span>
              {providerKey && <> on <span className="font-mono text-gray-300">{providerKey}</span></>}.{' '}
              <a href={`${BASE}/m`} className="text-emerald-300 hover:text-emerald-200 underline underline-offset-2">the catalog</a>
            </div>
          )
        )}

        {/* ── the catalog ── */}
        {!loading && !modelId && (
          <>
            <div className="flex items-baseline gap-2">
              <h1 className="text-[11px] uppercase tracking-wider text-emerald-300 font-medium">Models</h1>
              <span className="text-[10px] text-gray-500">
                <b className="text-gray-300">{allModels}</b> across <b className="text-gray-300">{providers.length}</b> providers
                — each one its own page
              </span>
            </div>
            {providers.map(p => {
              const [label, tone] = statusOf(p)
              return (
                <div key={p.key} className={`${card} p-4 space-y-2`}>
                  <div className="flex items-center gap-2">
                    <span className="text-[11px] text-gray-200 font-medium">{p.key}</span>
                    <span className={`text-[8px] uppercase tracking-wider px-1.5 py-0.5 rounded border border-white/[0.08] ${tone}`}>{label}</span>
                    {p.free && <span className="text-[8px] uppercase tracking-wider text-emerald-300/80">free</span>}
                    {p.runtime && <span className="text-[9px] text-gray-600">{p.runtime}</span>}
                    <span className="ml-auto text-[9px] text-gray-600">{p.models.length} models</span>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {p.models.map(m => (
                      <a key={m} href={modelPageHref(p.key, m)}
                        title={p.default_model === m ? 'provider default' : undefined}
                        className="px-2.5 py-1 rounded-md font-mono text-[10px] border border-white/[0.07] bg-white/[0.02] text-gray-400 hover:text-gray-200 hover:border-white/[0.14] transition">
                        {m}{p.default_model === m && <span className="text-gray-600"> ★</span>}
                      </a>
                    ))}
                  </div>
                </div>
              )
            })}
          </>
        )}
      </div>
    </main>
  )
}
