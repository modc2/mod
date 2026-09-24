'use client'

// SkillLab — the arena's SKILLS view.
//
// A skill is a named bundle of tasks, each with a weight, and its board is
// the benchmark those weights cumulate into: an agent's skill score is the
// weighted mean of its last score on the bundle's tasks, over the tasks it
// actually played. A class bundles skills the same way one level up, so a
// score rolls task -> skill -> class.
//
// The composer's search box is how a bundle is assembled: the task pool
// ranked against a plain-language query by the module's own local BM25
// (GET /arena/tasks/search) — no embedding service, nothing leaves the box.

import { useState, useEffect, useCallback, useRef } from 'react'
import { API_URL } from '../config'

type WTask = { key: string; weight: number; title?: string }
type Skill = {
  id: string; name: string; description: string; owner?: string
  tasks: WTask[]; participants: number
  best_agent?: string | null; best_agent_score?: number | null
  best_model?: string | null; best_model_score?: number | null
  updated?: number
}
type Cls = {
  id: string; name: string; description: string; owner?: string
  skills: { id: string; weight: number }[]; participants: number
  best_agent?: string | null; best_agent_score?: number | null
  best_model?: string | null; best_model_score?: number | null
}
type Hit = {
  score: number; key: string; suite: string; title: string
  prompt: string; checks: number; custom: boolean; owner?: string | null
}
type SkillRow = {
  rank: number; agent: string; icon: string; model: string
  weighted_score: number; tasks_played: number; tasks_total: number
  coverage: number; elo: number; matches: number
}
type ClsRow = {
  rank: number; agent: string; icon: string
  weighted_score: number; skills_played: number; skills_total: number
  coverage: number; elo: number; matches: number
  per_skill: { id: string; name: string; weight: number
               score: number | null; coverage: number }[]
}
type SkillBoard = {
  skill: Skill; leaderboard: SkillRow[]
  best_model?: { model: string; weighted_score: number } | null
  model_board: { rank: number; model: string; weighted_score: number
                 matches: number; coverage: number }[]
  tasks: { key: string; weight: number; title: string }[]
}
type ClsBoard = {
  class: Cls; leaderboard: ClsRow[]
  best_model?: { model: string; weighted_score: number } | null
  model_board: { rank: number; model: string; weighted_score: number
                 matches: number; coverage: number }[]
  skills: { id: string; weight: number; name: string; tasks: number; missing: boolean }[]
}

const pct = (x: number) => `${Math.round(x * 100)}%`
const sc = (x: number | null | undefined) =>
  x === null || x === undefined ? '—' : x.toFixed(3)

export default function SkillLab({ token, isHost, address, onSignIn }: {
  token?: string | null
  isHost: boolean
  address?: string | null
  onSignIn?: () => void
}) {
  const [skills, setSkills] = useState<Skill[]>([])
  const [classes, setClasses] = useState<Cls[]>([])
  const [sel, setSel] = useState<{ kind: 'skill' | 'class'; id: string } | null>(null)
  const [skillBoard, setSkillBoard] = useState<SkillBoard | null>(null)
  const [clsBoard, setClsBoard] = useState<ClsBoard | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // composer — one overlay for both kinds; `edit.id` null = creating
  const [edit, setEdit] = useState<{ kind: 'skill' | 'class'; id: string | null } | null>(null)
  const [form, setForm] = useState<{ name: string; description: string }>({ name: '', description: '' })
  const [chosen, setChosen] = useState<WTask[]>([])           // skill members
  const [chosenSkills, setChosenSkills] = useState<{ id: string; weight: number }[]>([])
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<Hit[]>([])
  const [searching, setSearching] = useState(false)
  const queryRef = useRef<HTMLInputElement>(null)

  const load = useCallback(() => {
    const signal = AbortSignal.timeout(10000)
    fetch(`${API_URL}/arena/skills`, { signal }).then(r => r.json())
      .then(d => setSkills(d.skills || [])).catch(() => {})
    fetch(`${API_URL}/arena/classes`, { signal }).then(r => r.json())
      .then(d => setClasses(d.classes || [])).catch(() => {})
  }, [])
  useEffect(() => { load() }, [load])

  // one detail board at a time, for whichever bundle is open
  useEffect(() => {
    setSkillBoard(null); setClsBoard(null)
    if (!sel) return
    const path = sel.kind === 'skill' ? 'skills' : 'classes'
    fetch(`${API_URL}/arena/${path}/${encodeURIComponent(sel.id)}`)
      .then(r => r.json())
      .then(d => {
        if (d?.error) return
        if (sel.kind === 'skill') setSkillBoard(d)
        else setClsBoard(d)
      }).catch(() => {})
  }, [sel])

  // the search that assembles a bundle — debounced, read off the ref so a
  // submit in the same tick as a keystroke never scans the previous query
  useEffect(() => {
    if (edit?.kind !== 'skill') return
    const q = query.trim()
    if (!q) { setHits([]); return }
    const t = setTimeout(() => {
      setSearching(true)
      fetch(`${API_URL}/arena/tasks/search?q=${encodeURIComponent(q)}&k=12`)
        .then(r => r.json())
        .then(d => setHits(d.results || []))
        .catch(() => {})
        .finally(() => setSearching(false))
    }, 300)
    return () => clearTimeout(t)
  }, [query, edit?.kind])

  const canEdit = (owner?: string) =>
    isHost || (!!address && !!owner && owner.toLowerCase() === address.toLowerCase())

  const openComposer = (kind: 'skill' | 'class', id: string | null) => {
    if (!token) { onSignIn?.(); return }
    setErr(null)
    if (id) {
      if (kind === 'skill') {
        const s = skills.find(x => x.id === id)
        if (!s) return
        setForm({ name: s.name, description: s.description || '' })
        setChosen(s.tasks.map(t => ({ ...t })))
      } else {
        const c = classes.find(x => x.id === id)
        if (!c) return
        setForm({ name: c.name, description: c.description || '' })
        setChosenSkills(c.skills.map(s => ({ ...s })))
      }
    } else {
      setForm({ name: '', description: '' })
      setChosen([]); setChosenSkills([])
    }
    setQuery(''); setHits([])
    setEdit({ kind, id })
  }

  const save = async () => {
    if (!edit) return
    setBusy(true); setErr(null)
    const isSkill = edit.kind === 'skill'
    const body: any = {
      key: token, name: form.name, description: form.description,
      ...(isSkill ? { tasks: chosen.map(({ key, weight }) => ({ key, weight })) }
                  : { skills: chosenSkills }),
    }
    const path = isSkill ? 'skills' : 'classes'
    const url = edit.id ? `${API_URL}/arena/${path}/${encodeURIComponent(edit.id)}`
                        : `${API_URL}/arena/${path}`
    try {
      const r = await fetch(url, {
        method: edit.id ? 'PUT' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const d = await r.json()
      if (d?.error) { setErr(d.error); return }
      setEdit(null); load()
      if (d?.id) setSel({ kind: edit.kind, id: d.id })
    } catch (e: any) {
      setErr(String(e?.message || e))
    } finally { setBusy(false) }
  }

  const remove = async (kind: 'skill' | 'class', id: string) => {
    if (!token) { onSignIn?.(); return }
    const path = kind === 'skill' ? 'skills' : 'classes'
    await fetch(`${API_URL}/arena/${path}/${encodeURIComponent(id)}?key=${encodeURIComponent(token)}`,
      { method: 'DELETE' }).catch(() => {})
    if (sel?.id === id) setSel(null)
    load()
  }

  const bundleCard = (kind: 'skill' | 'class', b: Skill | Cls, members: string) => (
    <button key={b.id} onClick={() => setSel(sel?.id === b.id ? null : { kind, id: b.id })}
      className={`w-full text-left rounded-lg border px-3 py-2.5 transition ${
        sel?.id === b.id ? 'border-emerald-500/40 bg-emerald-500/[0.06]'
                         : 'border-white/[0.07] bg-white/[0.02] hover:border-white/15'}`}>
      <div className="flex items-center gap-2">
        <span className="text-[11px] text-gray-200 truncate">{b.name}</span>
        <span className="text-[9px] uppercase tracking-wider text-gray-600 ml-auto shrink-0">{members}</span>
      </div>
      {b.description && (
        <div className="text-[10px] text-gray-500 mt-0.5 line-clamp-1">{b.description}</div>
      )}
      <div className="flex items-center gap-3 mt-1 text-[9px] text-gray-600">
        {b.best_agent
          ? <span>◆ <span className="text-emerald-300/80">{b.best_agent}</span> {sc(b.best_agent_score)}</span>
          : <span className="text-gray-700">no agent has played it yet</span>}
        {b.best_model && <span className="truncate">⌁ {b.best_model.split('/').pop()}</span>}
        {b.participants > 0 && <span className="ml-auto shrink-0">{b.participants} on the board</span>}
      </div>
    </button>
  )

  // ── the benchmark tables ───────────────────────────────────────────

  const benchBar = (x: number) => (
    <div className="flex items-center gap-2 min-w-[110px]">
      <div className="flex-1 h-1 rounded bg-white/[0.06] overflow-hidden">
        <div className="h-full bg-emerald-400/60" style={{ width: pct(Math.max(0, Math.min(1, x))) }} />
      </div>
      <span className="text-emerald-200 tabular-nums">{sc(x)}</span>
    </div>
  )

  const ownerRow = (kind: 'skill' | 'class', b: { id: string; owner?: string }) => (
    <div className="flex items-center gap-2 text-[9px] text-gray-600">
      <span>{b.owner ? `by ${b.owner.slice(0, 6)}…${b.owner.slice(-4)}` : 'by host'}</span>
      {canEdit(b.owner) && (
        <>
          <button onClick={() => openComposer(kind, b.id)}
            className="uppercase tracking-wider text-gray-500 hover:text-emerald-300 transition">edit</button>
          <button onClick={() => remove(kind, b.id)}
            className="uppercase tracking-wider text-gray-600 hover:text-red-400 transition">remove</button>
        </>
      )}
    </div>
  )

  const skillDetail = skillBoard && sel?.kind === 'skill' && (
    <div className="rounded-lg border border-white/[0.07] bg-white/[0.02] p-3 space-y-3">
      <div className="flex items-start gap-2">
        <div className="min-w-0">
          <div className="text-[12px] text-gray-100">{skillBoard.skill.name}</div>
          {skillBoard.skill.description && (
            <div className="text-[10px] text-gray-500 mt-0.5">{skillBoard.skill.description}</div>
          )}
        </div>
        <div className="ml-auto shrink-0">{ownerRow('skill', skillBoard.skill)}</div>
      </div>

      {/* the bundle itself: every task and the weight it carries */}
      <div className="flex flex-wrap gap-1.5">
        {skillBoard.tasks.map(t => (
          <span key={t.key} title={t.key}
            className="text-[9px] px-1.5 py-0.5 rounded border border-white/[0.08] text-gray-400">
            {t.title || t.key}
            <span className="text-gray-600 ml-1">×{t.weight}</span>
          </span>
        ))}
      </div>

      {skillBoard.leaderboard.length ? (
        <table className="w-full text-[10px]">
          <thead>
            <tr className="text-left text-[9px] uppercase tracking-wider text-gray-600">
              <th className="py-1 pr-2 font-normal">#</th>
              <th className="py-1 pr-2 font-normal">agent</th>
              <th className="py-1 pr-2 font-normal">benchmark</th>
              <th className="py-1 pr-2 font-normal" title="tasks in the bundle the agent has actually played — an unplayed task is left out of the score, not counted as a zero">coverage</th>
              <th className="py-1 pr-2 font-normal">elo</th>
            </tr>
          </thead>
          <tbody>
            {skillBoard.leaderboard.map(r => (
              <tr key={r.agent} className="border-t border-white/[0.04] text-gray-400">
                <td className="py-1.5 pr-2 text-gray-600">{r.rank}</td>
                <td className="py-1.5 pr-2"><span className="mr-1.5">{r.icon}</span>
                  <span className="text-gray-200">{r.agent}</span></td>
                <td className="py-1.5 pr-2">{benchBar(r.weighted_score)}</td>
                <td className="py-1.5 pr-2 tabular-nums">{r.tasks_played}/{r.tasks_total}</td>
                <td className="py-1.5 pr-2 tabular-nums">{r.elo}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="text-[10px] text-gray-600">
          no agent has played these tasks yet — the benchmark fills in as rounds run
        </div>
      )}

      {skillBoard.model_board.length > 0 && (
        <div className="text-[9px] text-gray-600">
          best model here: <span className="text-gray-400">{skillBoard.best_model?.model}</span>
          {' '}at {sc(skillBoard.best_model?.weighted_score)}
        </div>
      )}
    </div>
  )

  const clsDetail = clsBoard && sel?.kind === 'class' && (
    <div className="rounded-lg border border-white/[0.07] bg-white/[0.02] p-3 space-y-3">
      <div className="flex items-start gap-2">
        <div className="min-w-0">
          <div className="text-[12px] text-gray-100">{clsBoard.class.name}</div>
          {clsBoard.class.description && (
            <div className="text-[10px] text-gray-500 mt-0.5">{clsBoard.class.description}</div>
          )}
        </div>
        <div className="ml-auto shrink-0">{ownerRow('class', clsBoard.class)}</div>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {clsBoard.skills.map(s => (
          <span key={s.id}
            className={`text-[9px] px-1.5 py-0.5 rounded border ${
              s.missing ? 'border-red-500/30 text-red-400/80'
                        : 'border-white/[0.08] text-gray-400'}`}>
            {s.name}{s.missing && ' (deleted)'}
            <span className="text-gray-600 ml-1">×{s.weight}</span>
          </span>
        ))}
      </div>

      {clsBoard.leaderboard.length ? (
        <table className="w-full text-[10px]">
          <thead>
            <tr className="text-left text-[9px] uppercase tracking-wider text-gray-600">
              <th className="py-1 pr-2 font-normal">#</th>
              <th className="py-1 pr-2 font-normal">agent</th>
              <th className="py-1 pr-2 font-normal">benchmark</th>
              <th className="py-1 pr-2 font-normal" title="skills in the class the agent has any coverage in">coverage</th>
              <th className="py-1 pr-2 font-normal">per skill</th>
            </tr>
          </thead>
          <tbody>
            {clsBoard.leaderboard.map(r => (
              <tr key={r.agent} className="border-t border-white/[0.04] text-gray-400 align-top">
                <td className="py-1.5 pr-2 text-gray-600">{r.rank}</td>
                <td className="py-1.5 pr-2"><span className="mr-1.5">{r.icon}</span>
                  <span className="text-gray-200">{r.agent}</span></td>
                <td className="py-1.5 pr-2">{benchBar(r.weighted_score)}</td>
                <td className="py-1.5 pr-2 tabular-nums">{r.skills_played}/{r.skills_total}</td>
                <td className="py-1.5 pr-2">
                  <div className="flex flex-wrap gap-1">
                    {r.per_skill.map(p => (
                      <span key={p.id} title={`${p.name} · weight ${p.weight}`}
                        className={`text-[9px] px-1 py-0.5 rounded border tabular-nums ${
                          p.score === null ? 'border-white/[0.05] text-gray-700'
                                           : 'border-white/[0.08] text-gray-400'}`}>
                        {p.name.slice(0, 12)} {sc(p.score)}
                      </span>
                    ))}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="text-[10px] text-gray-600">
          no agent has coverage in these skills yet
        </div>
      )}

      {clsBoard.model_board.length > 0 && (
        <div className="text-[9px] text-gray-600">
          best model here: <span className="text-gray-400">{clsBoard.best_model?.model}</span>
          {' '}at {sc(clsBoard.best_model?.weighted_score)}
        </div>
      )}
    </div>
  )

  // ── the composer ───────────────────────────────────────────────────

  const setWeight = (key: string, w: number) =>
    setChosen(cs => cs.map(t => t.key === key ? { ...t, weight: w } : t))
  const setSkillWeight = (id: string, w: number) =>
    setChosenSkills(cs => cs.map(s => s.id === id ? { ...s, weight: w } : s))

  const composer = edit && (
    <div className="fixed inset-0 z-40 flex items-center justify-center p-4"
      onClick={() => setEdit(null)}>
      <div className="absolute inset-0 bg-black/60" />
      <div onClick={e => e.stopPropagation()}
        className="relative w-full max-w-2xl max-h-[85vh] overflow-y-auto rounded-xl border border-white/10 bg-surface-1 p-4 space-y-3">
        <div className="flex items-center">
          <span className="text-[10px] uppercase tracking-[0.2em] text-emerald-300">
            {edit.id ? `edit ${edit.kind}` : `new ${edit.kind}`}
          </span>
          <button onClick={() => setEdit(null)}
            className="ml-auto text-gray-500 hover:text-gray-300 text-[12px]">✕</button>
        </div>

        <input value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
          placeholder={edit.kind === 'skill' ? 'skill name — e.g. Python refactoring' : 'class name — e.g. Software engineering'}
          className="w-full bg-white/[0.03] border border-white/[0.08] rounded-lg px-3 py-2 text-[11px] text-gray-200 outline-none focus:border-emerald-500/40" />
        <input value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
          placeholder="what this measures (optional)"
          className="w-full bg-white/[0.03] border border-white/[0.08] rounded-lg px-3 py-2 text-[11px] text-gray-200 outline-none focus:border-emerald-500/40" />

        {edit.kind === 'skill' ? (
          <>
            {/* semantic search over the pool — the door tasks come in through */}
            <div className="relative">
              <input ref={queryRef} value={query} onChange={e => setQuery(e.target.value)}
                placeholder="search the task pool — 'refactor a python file', 'read a directory'…"
                className="w-full bg-white/[0.03] border border-white/[0.08] rounded-lg px-3 py-2 text-[11px] text-gray-200 outline-none focus:border-emerald-500/40" />
              {searching && <span className="absolute right-3 top-2 text-[9px] text-gray-600">…</span>}
            </div>
            {hits.length > 0 && (
              <div className="space-y-1 max-h-48 overflow-y-auto">
                {hits.map(h => {
                  const on = chosen.some(t => t.key === h.key)
                  return (
                    <div key={h.key} className="flex items-center gap-2 rounded border border-white/[0.06] px-2 py-1.5">
                      <span className="text-[9px] text-emerald-300/70 tabular-nums w-9 shrink-0"
                        title="how much of the query this task's spec covers">{h.score.toFixed(2)}</span>
                      <div className="min-w-0 flex-1">
                        <div className="text-[10px] text-gray-300 truncate">{h.title}</div>
                        <div className="text-[9px] text-gray-600 truncate">{h.suite} · {h.checks} checks · {h.prompt}</div>
                      </div>
                      <button onClick={() => !on && setChosen(cs => [...cs, { key: h.key, weight: 1.0, title: h.title }])}
                        disabled={on}
                        className={`shrink-0 text-[9px] uppercase tracking-wider px-2 py-1 rounded border transition ${
                          on ? 'border-white/[0.05] text-gray-700'
                             : 'border-emerald-500/30 text-emerald-300 hover:bg-emerald-500/10'}`}>
                        {on ? 'in' : '+ add'}
                      </button>
                    </div>
                  )
                })}
              </div>
            )}
            {query.trim() && !searching && hits.length === 0 && (
              <div className="text-[9px] text-gray-600">nothing in the pool matches that — try other words, or write the task in AGENTS ▸ TASK</div>
            )}

            <div className="text-[9px] uppercase tracking-wider text-gray-600">
              bundle · {chosen.length} tasks — weight is how much each one counts in the benchmark
            </div>
            {chosen.length === 0 && (
              <div className="text-[10px] text-gray-600">search above and add tasks — a skill is the bundle of them</div>
            )}
            {chosen.map(t => (
              <div key={t.key} className="flex items-center gap-2 rounded border border-white/[0.06] px-2 py-1.5">
                <div className="min-w-0 flex-1">
                  <div className="text-[10px] text-gray-300 truncate">{t.title || t.key}</div>
                  <div className="text-[9px] text-gray-600 truncate">{t.key}</div>
                </div>
                <input type="number" step={0.5} min={0} value={t.weight}
                  onChange={e => setWeight(t.key, parseFloat(e.target.value) || 0)}
                  className="w-16 shrink-0 bg-white/[0.03] border border-white/[0.08] rounded px-2 py-1 text-[10px] text-gray-200 outline-none text-right" />
                <button onClick={() => setChosen(cs => cs.filter(x => x.key !== t.key))}
                  className="shrink-0 text-gray-600 hover:text-red-400 text-[11px]">✕</button>
              </div>
            ))}
          </>
        ) : (
          <>
            <div className="text-[9px] uppercase tracking-wider text-gray-600">
              skills in this class — weight is how much each one counts in the class benchmark
            </div>
            {skills.length === 0 && (
              <div className="text-[10px] text-gray-600">no skills yet — make one first; a class is a bundle of them</div>
            )}
            {skills.map(s => {
              const m = chosenSkills.find(x => x.id === s.id)
              return (
                <div key={s.id} className="flex items-center gap-2 rounded border border-white/[0.06] px-2 py-1.5">
                  <button onClick={() => m
                      ? setChosenSkills(cs => cs.filter(x => x.id !== s.id))
                      : setChosenSkills(cs => [...cs, { id: s.id, weight: 1.0 }])}
                    className={`shrink-0 w-4 h-4 rounded border text-[9px] leading-none transition ${
                      m ? 'border-emerald-500/50 bg-emerald-500/20 text-emerald-300'
                        : 'border-white/[0.15] text-transparent hover:border-white/30'}`}>✓</button>
                  <div className="min-w-0 flex-1">
                    <div className="text-[10px] text-gray-300 truncate">{s.name}</div>
                    <div className="text-[9px] text-gray-600 truncate">{s.tasks.length} tasks</div>
                  </div>
                  {m && (
                    <input type="number" step={0.5} min={0} value={m.weight}
                      onChange={e => setSkillWeight(s.id, parseFloat(e.target.value) || 0)}
                      className="w-16 shrink-0 bg-white/[0.03] border border-white/[0.08] rounded px-2 py-1 text-[10px] text-gray-200 outline-none text-right" />
                  )}
                </div>
              )
            })}
          </>
        )}

        {err && <div className="text-[10px] text-red-400">{err}</div>}
        <div className="flex items-center gap-2 pt-1">
          <button onClick={save} disabled={busy || !form.name.trim()}
            className="lit-btn text-[10px] uppercase tracking-wider px-3 py-1.5 rounded-lg border border-emerald-500/40 text-emerald-200 disabled:opacity-40 hover:bg-emerald-500/10 transition">
            {busy ? 'saving…' : edit.id ? 'save' : 'create'}
          </button>
          <button onClick={() => setEdit(null)}
            className="text-[10px] uppercase tracking-wider text-gray-500 hover:text-gray-300 transition">cancel</button>
        </div>
      </div>
    </div>
  )

  // ── layout: two shelves and the open board ─────────────────────────

  return (
    <div className="flex-1 min-h-0 overflow-y-auto p-4 space-y-4">
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-2">
          <div className="flex items-center">
            <span className="text-[10px] uppercase tracking-[0.2em] text-gray-500">skills · bundles of tasks</span>
            <button onClick={() => openComposer('skill', null)}
              title={token ? 'bundle tasks into a skill — search the pool, weight each task'
                           : 'sign in — a skill is filed under the address that made it'}
              className="ml-auto text-[10px] uppercase tracking-wider text-gray-500 hover:text-emerald-300 transition">
              + skill
            </button>
          </div>
          {skills.length === 0 && (
            <div className="text-[10px] text-gray-600 rounded-lg border border-dashed border-white/[0.08] p-4">
              a skill is a weighted bundle of arena tasks, and its board is the benchmark
              they cumulate into. + SKILL opens a composer with semantic search over the
              whole task pool.
            </div>
          )}
          {skills.map(s => bundleCard('skill', s, `${s.tasks.length} tasks`))}
        </div>

        <div className="space-y-2">
          <div className="flex items-center">
            <span className="text-[10px] uppercase tracking-[0.2em] text-gray-500">classes · bundles of skills</span>
            <button onClick={() => openComposer('class', null)}
              title={token ? 'bundle skills into a class — scores roll up task → skill → class'
                           : 'sign in — a class is filed under the address that made it'}
              className="ml-auto text-[10px] uppercase tracking-wider text-gray-500 hover:text-emerald-300 transition">
              + class
            </button>
          </div>
          {classes.length === 0 && (
            <div className="text-[10px] text-gray-600 rounded-lg border border-dashed border-white/[0.08] p-4">
              a class rolls skills up one more level: each skill carries a weight, and an
              agent&apos;s class benchmark is its skill scores cumulated by those weights.
            </div>
          )}
          {classes.map(c => bundleCard('class', c, `${c.skills.length} skills`))}
        </div>
      </div>

      {skillDetail}
      {clsDetail}
      {composer}
    </div>
  )
}
