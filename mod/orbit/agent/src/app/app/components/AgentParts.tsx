'use client'

import { useEffect, useState } from 'react'
import { API_URL } from '../config'

// ── AGENT PARTS — what one agent is made of, inline ─────────────────
// The agent box requires four components — prompt · model · toolbox ·
// memory — and the registry (`GET /agents`) already says how each one is
// filled. This renders that answer as a disclosure under a roster row, so
// the console's AGENTS tab shows the components without a trip to the HUB.
//
// Self-contained on purpose: page.tsx mounts `<AgentParts name={slug}/>`
// and nothing else. All instances share ONE fetch of /agents and /skills
// via module-level caches, so 20 rows cost the same as one.

interface Schema {
  name: string
  description: string
  goal: string
  icon: string | null
  tools: string[] | null
  model: string | null
  provider?: string | null
  memory: string | null
  harness: string | null
  arena?: boolean
  builtin: boolean
  owner: string | null
  owner_source?: string
  requires?: string[]
}

// one fetch each, shared by every row on the page
let schemasP: Promise<Record<string, Schema>> | null = null
let skillCountP: Promise<number> | null = null

const fetchSchemas = () => {
  if (!schemasP) {
    schemasP = fetch(`${API_URL}/agents`, { signal: AbortSignal.timeout(8000) })
      .then(r => r.json())
      .then(d => (d.schemas || {}) as Record<string, Schema>)
      .catch(() => { schemasP = null; return {} })
  }
  return schemasP
}

const fetchSkillCount = () => {
  if (!skillCountP) {
    skillCountP = fetch(`${API_URL}/tools`, { signal: AbortSignal.timeout(8000) })
      .then(r => r.json())
      .then(d => (d.tools || d.skills || []).length || 0)
      .catch(() => { skillCountP = null; return 0 })
  }
  return skillCountP
}

/** other panels (roster, market) may refetch after an edit — drop the cache */
export const agentPartsInvalidate = () => { schemasP = null }

const PORT_TONE: Record<string, string> = {
  prompt: 'text-amber-300/90',
  model: 'text-emerald-300/90',
  toolbox: 'text-sky-300/90',
  memory: 'text-violet-300/90',
}
const PORT_ICON: Record<string, string> = { prompt: '¶', model: '◇', toolbox: '⚒', memory: '⬢' }

const GOAL_CLIP = 420

/** `version` re-reads the schema when the caller's registry copy changes —
    pass anything whose identity moves on refetch (the options array itself) */
export default function AgentParts({ name, version }: { name: string; version?: unknown }) {
  const [a, setA] = useState<Schema | null>(null)
  const [allTools, setAllTools] = useState(0)
  const [open, setOpen] = useState(false)
  const [fullGoal, setFullGoal] = useState(false)

  useEffect(() => {
    let live = true
    fetchSchemas().then(s => { if (live) setA(s[name] || null) })
    fetchSkillCount().then(n => { if (live) setAllTools(n) })
    return () => { live = false }
  }, [name, version])

  if (!a) return null

  const ports = a.requires?.length ? a.requires : ['prompt', 'model', 'toolbox', 'memory']
  const toolWord = a.tools?.length
    ? `${a.tools.length} tool${a.tools.length > 1 ? 's' : ''}`
    : allTools ? `all ${allTools} tools` : 'all tools'
  const modelWord = a.harness
    ? `${a.harness} CLI`
    : a.model
      ? a.model + (a.provider ? ` · ${a.provider}` : '')
      : 'default model'
  const memWord = a.memory || 'default memory'

  // the collapsed line already answers the question — the expansion is detail
  const summary = [modelWord, toolWord, memWord]

  const port = (kind: string) => {
    const tone = PORT_TONE[kind] || 'text-gray-400'
    const body =
      kind === 'prompt' ? (
        a.goal ? (
          <>
            <pre className="whitespace-pre-wrap font-mono text-[10px] leading-relaxed text-gray-300">
              {fullGoal || a.goal.length <= GOAL_CLIP ? a.goal : a.goal.slice(0, GOAL_CLIP) + '…'}
            </pre>
            {a.goal.length > GOAL_CLIP && (
              <button onClick={() => setFullGoal(v => !v)}
                className="mt-1 text-[9px] uppercase tracking-wider text-amber-300/80 hover:text-amber-200 transition">
                {fullGoal ? 'less' : `show all ${a.goal.length} chars`}
              </button>
            )}
          </>
        ) : <span className="text-[10px] text-gray-600">no goal written — the module default speaks</span>
      ) : kind === 'model' ? (
        a.harness ? (
          <span className="text-[10px] text-violet-300/90">
            runs on the <b className="font-mono">{a.harness}</b> CLI on the host&apos;s own shell — not this module&apos;s loop
          </span>
        ) : a.model ? (
          <span className="font-mono text-[10px] text-emerald-300">
            {a.model}{a.provider && <span className="text-gray-500"> · {a.provider}</span>}
          </span>
        ) : <span className="text-[10px] text-gray-500">unbound — the console&apos;s model picker decides each run</span>
      ) : kind === 'toolbox' ? (
        a.tools?.length ? (
          <span className="flex flex-wrap gap-1">
            {a.tools.slice(0, 14).map(t => (
              <span key={t} className="px-1.5 py-0.5 rounded font-mono text-[9px] border border-sky-400/20 bg-sky-400/[0.06] text-sky-300/90">{t}</span>
            ))}
            {a.tools.length > 14 && <span className="text-[9px] text-gray-600 self-center">+{a.tools.length - 14} more</span>}
          </span>
        ) : <span className="text-[10px] text-gray-500">unrestricted — {allTools ? `every one of the ${allTools} tools` : 'every tool'} is offered</span>
      ) : (
        <span className="text-[10px] text-gray-400">
          {a.memory
            ? <span className="font-mono text-violet-300/90">{a.memory}</span>
            : 'default — the layered memory module (episodes · facts · conversation)'}
        </span>
      )
    return (
      <div key={kind} className="flex gap-2 items-start">
        <span className={`shrink-0 w-20 flex items-center gap-1.5 text-[9px] uppercase tracking-wider ${tone}`}>
          <span className="font-mono">{PORT_ICON[kind] || '·'}</span>{kind}
        </span>
        <div className="min-w-0 flex-1">{body}</div>
      </div>
    )
  }

  return (
    <div className="pl-7 pr-1">
      <button onClick={() => setOpen(v => !v)}
        className="w-full text-left flex items-center gap-1.5 py-0.5 text-[9px] text-gray-600 hover:text-gray-400 transition"
        title="What this agent is built from — its prompt, model, toolbox and memory">
        <span className={`inline-block transition-transform ${open ? 'rotate-90' : ''}`}>▸</span>
        <span className="uppercase tracking-wider">components</span>
        {!open && summary.map((s, i) => (
          <span key={i} className="font-mono truncate max-w-[11rem]">
            <span className="text-gray-700 pr-1.5">·</span>{s}
          </span>
        ))}
      </button>
      {open && (
        <div className="mb-1.5 px-3 py-2.5 rounded-lg border border-white/[0.07] bg-white/[0.02] space-y-2.5">
          {ports.map(port)}
          <div className="pt-1.5 border-t border-white/[0.05] flex items-center gap-2 flex-wrap font-mono text-[9px] text-gray-600">
            <span className="truncate">src/agents/{name}/mod.py</span>
            {a.owner && <span className="truncate" title={a.owner}>owner {a.owner.slice(0, 8)}…{a.owner_source === 'host' ? ' (host)' : ''}</span>}
            {a.builtin && <span className="uppercase tracking-wider">shipped</span>}
            {a.arena === false && <span className="uppercase tracking-wider" title="opted out of the arena board">off-board</span>}
          </div>
        </div>
      )}
    </div>
  )
}
