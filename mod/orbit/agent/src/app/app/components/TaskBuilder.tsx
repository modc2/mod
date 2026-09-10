'use client'

// TaskBuilder — the Builder's TASK mode.
//
// An agent is only as interesting as what you can measure it on, so the same
// tab that builds agents writes the tasks they compete on.
//
// Two schemas, one form, because they measure different things:
//
//   AGENT     a prompt, an optional fixture seeded into the match's scratch
//             dir, and checks over the trace and the files left behind. This
//             is the schema for "can it do the job" — edit this file, use that
//             tool, leave that artifact. Stored here, under `custom`.
//   OPENARENA a statement and a set of graded test cases, some of them hidden.
//             The schema for "is the program right": the agent writes a
//             program, and openarena's sandbox runs it against every case,
//             including the ones nobody here can see. Stored in the openarena
//             module — the same task its own competitors play.
//
// Two ways in for either, and they meet in the same form:
//   DESCRIBE — say what you want measured in plain words and the task-builder
//              agent drafts the whole spec, cases or checks included.
//   BY HAND  — fill the form yourself.
//
// Saved tasks join the arena pool and are played by every agent on the board,
// so the form is opinionated about the mistake that makes a task worthless. In
// the AGENT schema that is a check a lazy agent passes by doing nothing; in the
// OPENARENA schema it is an `expect` nobody computed, and cases that are all
// visible, which an entrant can hardcode.

import { useState, useEffect, useCallback, useMemo } from 'react'
import { API_URL } from '../config'
import Select from './Select'

type Scorer = { type: string; path?: string; text?: string; pattern?: string; name?: string; n?: number }
type CustomTask = {
  slug: string; title: string; description?: string; prompt: string
  steps?: number | null; setup?: { files?: Record<string, string> }
  scorers?: Scorer[]; owner?: string | null; updated?: number
}
type PoolTask = { key: string; suite: string; title: string; steps?: number | null }
type FixtureFile = { name: string; body: string }

// what each check needs filled in, and what it actually measures
const CHECKS: Record<string, { fields: ('path' | 'text' | 'pattern' | 'name' | 'n')[]; hint: string; artifact?: boolean }> = {
  file_exists:       { fields: ['path'],            hint: 'the agent created this file', artifact: true },
  file_contains:     { fields: ['path', 'text'],    hint: 'the file contains this text', artifact: true },
  file_not_contains: { fields: ['path', 'text'],    hint: 'this text is GONE from the file — what a no-op fails', artifact: true },
  // re.search over the whole file, no MULTILINE — an author who wants line
  // anchors has to say so inline, which is what the placeholder shows
  file_regex:        { fields: ['path', 'pattern'], hint: 'python regex over the whole file — (?m) for line anchors', artifact: true },
  contains:          { fields: ['text'],            hint: 'the agent said this' },
  regex:             { fields: ['pattern'],         hint: 'regex over what the agent said' },
  tool_used:         { fields: ['name'],            hint: 'it used this tool' },
  tool_not_used:     { fields: ['name'],            hint: 'it avoided this tool' },
  no_errors:         { fields: [],                  hint: 'no step errored' },
  finished:          { fields: [],                  hint: 'it ended by finishing' },
  max_steps:         { fields: ['n'],               hint: 'it took at most n steps' },
  step_count_at_least: { fields: ['n'],             hint: 'it took at least n steps' },
}
const CHECK_ORDER = ['file_exists', 'file_regex', 'file_contains', 'file_not_contains',
                     'contains', 'regex', 'tool_used', 'tool_not_used',
                     'no_errors', 'finished', 'max_steps', 'step_count_at_least']

const FIELD_META: Record<string, { label: string; placeholder: string }> = {
  path:    { label: 'file',    placeholder: 'out.txt — relative to the scratch dir' },
  text:    { label: 'text',    placeholder: 'exact substring' },
  pattern: { label: 'regex',   placeholder: '^\\s*42\\s*$  ·  (?m)^timeout=45$ to anchor a line' },
  name:    { label: 'tool',    placeholder: 'write' },
  n:       { label: 'n',       placeholder: '6' },
}

const blank = (): CustomTask => ({ slug: '', title: '', description: '', prompt: '', steps: 6, scorers: [] })

const shortAddr = (a?: string | null) => (a ? `${a.slice(0, 6)}…${a.slice(-4)}` : '')

// ── the openarena schema ──────────────────────────────────────────────
//
// A case is either io (stdin in, exact stdout out) or unit (a program that
// imports the submission and asserts). `hidden` cases are graded and never
// shown — not in the task, not in the brief the competitor reads. That is the
// whole defence against an entrant that memorises the examples.
type OaCase = {
  name: string; hidden: boolean
  stdin?: string; expect?: string; compare?: string   // io
  program?: string                                     // unit
}
type OaTask = {
  slug?: string; title: string; statement: string
  mode: 'io' | 'unit'; language: string; starter?: string
  tags?: string[]; tests: OaCase[]
}
// one task as the bridge lists it
type OaRow = {
  slug: string; title: string; mode: string; language: string
  cases: number; hidden: number; author?: string; tags?: string[]
}

const OA_MODES = ['io', 'unit'] as const
const OA_LANGS = ['any', 'python', 'javascript', 'bash']
const OA_COMPARE = ['trim', 'exact', 'contains']

const blankOa = (): OaTask => ({
  title: '', statement: '', mode: 'io', language: 'any', starter: '', tags: [],
  tests: [{ name: 'example', hidden: false, stdin: '', expect: '', compare: 'trim' }],
})

const blankCase = (mode: 'io' | 'unit', hidden = false): OaCase =>
  mode === 'unit'
    ? { name: '', hidden, program: '' }
    : { name: '', hidden, stdin: '', expect: '', compare: 'trim' }

// The two ways an openarena task is quietly worthless. Neither is a syntax
// error, so neither is caught by the server — they are caught by reading, and
// this is the reading, done out loud.
const oaRisk = (t: OaTask): string | null => {
  const cases = t.tests || []
  if (!cases.length) return null
  if (cases.every(c => !c.hidden))
    return 'Every case is visible. An entrant that hardcodes the examples scores full marks — hide at least one, and hide the ones that would catch a fake.'
  if (cases.every(c => c.hidden))
    return 'Every case is hidden, so a competitor has nothing to check its answer against. Leave at least one visible.'
  if (t.mode === 'io' && cases.some(c => !String(c.expect || '').trim()))
    return 'A case has no expected output. Compute it — a case with a wrong or empty expectation fails every correct program.'
  if (t.mode === 'unit' && cases.some(c => !String(c.program || '').trim()))
    return 'A unit case has no grader program. Each one must import the submission and assert something about it.'
  return null
}

// The trap the arena is built around: file_exists / file_contains / file_regex
// all pass on a fixture handed back untouched, so a task that seeds a file and
// only checks that it still looks like itself scores ~1.0 for doing nothing.
const noOpRisk = (files: FixtureFile[], scorers: Scorer[]): string | null => {
  const seeded = new Set(files.map(f => f.name.trim()).filter(Boolean))
  if (!seeded.size || !scorers.length) return null
  const touchesFixture = scorers.filter(s => s.path && seeded.has(s.path.trim()))
  if (!touchesFixture.length) return null
  const proves = touchesFixture.some(s => s.type === 'file_not_contains')
  const newArtifact = scorers.some(s => s.path && !seeded.has(s.path.trim()))
  if (proves || newArtifact) return null
  return 'Every check here passes on the fixture as seeded — an agent that does nothing scores full marks. Add a file_not_contains for the text that must disappear, or check a file the agent has to create.'
}

type Props = {
  token?: string | null
  address?: string | null
  isHost?: boolean
  onSignIn?: () => void
  // jump to the board — where a saved task is actually played
  onOpenArena?: () => void
}

export default function TaskBuilder({ token, address, isHost, onSignIn, onOpenArena }: Props) {
  const canSave = !!token || !!isHost

  const [mine, setMine] = useState<CustomTask[]>([])
  const [pool, setPool] = useState<PoolTask[]>([])
  const [task, setTask] = useState<CustomTask>(blank())
  const [files, setFiles] = useState<FixtureFile[]>([])
  const [scorers, setScorers] = useState<Scorer[]>([])
  const [loaded, setLoaded] = useState<string | null>(null)   // slug being edited
  const [dirty, setDirty] = useState(false)

  // which schema this form is editing. The two are stored in different places
  // and grade different things, so the switch swaps the whole composer rather
  // than hiding fields.
  const [schema, setSchema] = useState<'agent' | 'openarena'>('agent')
  const [oa, setOa] = useState<OaTask>(blankOa())
  const [oaRows, setOaRows] = useState<OaRow[]>([])
  const [oaUp, setOaUp] = useState<boolean | null>(null)
  // openarena has no update path — a task there is immutable once created — so
  // opening one loads a copy and says so
  const [copiedFrom, setCopiedFrom] = useState<string | null>(null)

  const [describe, setDescribe] = useState('')
  const [drafting, setDrafting] = useState(false)
  const [draftNote, setDraftNote] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [playing, setPlaying] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const flash = (ok: boolean, text: string) => {
    setMsg({ ok, text })
    window.setTimeout(() => setMsg(m => (m && m.text === text ? null : m)), 5000)
  }

  const load = useCallback(() => {
    fetch(`${API_URL}/arena/tasks`, { signal: AbortSignal.timeout(10000) })
      .then(r => r.json())
      .then(d => { setMine(d.custom || []); setPool(d.tasks || []) })
      .catch(() => {})
  }, [])
  useEffect(() => { load() }, [load])

  // the openarena side of the rail — only asked for when that schema is open
  const loadOa = useCallback(() => {
    fetch(`${API_URL}/arena/openarena`, { signal: AbortSignal.timeout(20000) })
      .then(r => r.json())
      .then(d => { setOaUp(!!d.available); setOaRows(d.pool || []) })
      .catch(() => { setOaUp(false); setOaRows([]) })
  }, [])
  useEffect(() => { if (schema === 'openarena') loadOa() }, [schema, loadOa])

  const patch = (p: Partial<CustomTask>) => { setTask(t => ({ ...t, ...p })); setDirty(true) }
  const patchOa = (p: Partial<OaTask>) => { setOa(t => ({ ...t, ...p })); setDirty(true) }
  const patchCase = (i: number, p: Partial<OaCase>) => {
    setOa(t => ({ ...t, tests: t.tests.map((c, j) => (j === i ? { ...c, ...p } : c)) }))
    setDirty(true)
  }

  const reset = () => {
    setTask(blank()); setFiles([]); setScorers([])
    setOa(blankOa()); setCopiedFrom(null)
    setLoaded(null); setDirty(false); setDraftNote(null); setDescribe('')
  }

  // one openarena task into the form. It is a copy, not an edit: openarena
  // refuses a slug it already holds, so saving this without renaming it fails
  // — the banner in the composer says exactly that.
  const openOa = async (slug: string) => {
    try {
      const d = await fetch(`${API_URL}/arena/openarena/tasks/${encodeURIComponent(slug)}`,
                            { signal: AbortSignal.timeout(15000) }).then(r => r.json())
      if (d?.error) { flash(false, d.error); return }
      const mode: 'io' | 'unit' = d.mode === 'unit' ? 'unit' : 'io'
      setOa({
        title: d.title || '', statement: d.statement || '', mode,
        language: d.language || 'any', starter: d.starter || '',
        tags: d.tags || [],
        tests: (d.tests || []).map((c: any) => ({
          name: c.name || '', hidden: !!c.hidden,
          ...(mode === 'unit'
            ? { program: c.program || '' }
            : { stdin: c.stdin || '', expect: c.expect || '', compare: c.compare || 'trim' }),
        })),
      })
      setCopiedFrom(slug)
      setLoaded(null)
      setDirty(false)
      setDraftNote(null)
    } catch (e: any) { flash(false, e?.message || 'could not read that task') }
  }

  const removeOa = async (slug: string, title: string) => {
    if (!confirm(`Delete "${title}" from openarena? Matches already played keep their scores.`)) return
    try {
      const q = token ? `?key=${encodeURIComponent(token)}` : ''
      const r = await fetch(`${API_URL}/arena/openarena/tasks/${encodeURIComponent(slug)}${q}`,
                            { method: 'DELETE' }).then(x => x.json())
      if (r.error) { flash(false, r.error); return }
      if (copiedFrom === slug) setCopiedFrom(null)
      loadOa(); load()
      flash(true, `deleted "${title}"`)
    } catch (e: any) { flash(false, e?.message || 'delete failed') }
  }

  const open = (t: CustomTask) => {
    setTask({ ...t, steps: t.steps ?? 6 })
    setFiles(Object.entries(t.setup?.files || {}).map(([name, body]) => ({ name, body })))
    setScorers((t.scorers || []).map(s => ({ ...s })))
    setLoaded(t.slug)
    setDirty(false)
    setDraftNote(null)
  }

  const canEdit = (t: CustomTask) =>
    !!isHost || (!!t.owner && !!address && t.owner.toLowerCase() === address.toLowerCase()) || !t.owner

  // ── the task-builder agent writes the spec ──
  const draft = async () => {
    if (!describe.trim() || drafting) return
    if (!canSave) { flash(false, 'sign in to run the task-builder'); return }
    setDrafting(true)
    setDraftNote(null)
    try {
      const r = await fetch(`${API_URL}/arena/tasks/draft`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description: describe.trim(), key: token, free: !isHost,
                               schema }),
        signal: AbortSignal.timeout(180000),
      }).then(x => x.json())
      if (r.error) { flash(false, r.error); return }
      const d = r.draft
      if (!d) { flash(false, 'the task-builder came back empty — try again'); return }
      if (schema === 'openarena') {
        const mode: 'io' | 'unit' = d.mode === 'unit' ? 'unit' : 'io'
        setOa({
          title: d.title || '', statement: d.statement || d.prompt || '', mode,
          language: d.language || 'any', starter: d.starter || '', tags: d.tags || [],
          tests: (d.tests || []).map((c: any) => ({
            name: c.name || '', hidden: !!c.hidden,
            ...(mode === 'unit'
              ? { program: c.program || '' }
              : { stdin: c.stdin || '', expect: c.expect || '', compare: c.compare || 'trim' }),
          })),
        })
        setCopiedFrom(null)
        setLoaded(null)
        setDirty(true)
        setDraftNote(r.invalid
          ? `drafted, but it needs a fix before it can be saved: ${r.invalid}`
          : 'drafted by the task-builder — check every `expect` yourself. A wrong expectation fails every correct program, and the model computed these in its head.')
        return
      }
      setTask({
        slug: '', title: d.title || '', description: d.description || '',
        prompt: d.prompt || '', steps: d.steps || 6,
      })
      setFiles(Object.entries(d.setup?.files || {}).map(([name, body]) => ({ name, body: String(body) })))
      setScorers((d.scorers || []).map((s: Scorer) => ({ ...s })))
      setLoaded(null)
      setDirty(true)
      setDraftNote(r.invalid
        ? `drafted, but it needs a fix before it can be saved: ${r.invalid}`
        : 'drafted by the task-builder — read the checks before you save. Would an agent that does nothing pass them?')
    } catch (e: any) {
      flash(false, e?.name === 'TimeoutError' ? 'the task-builder took too long' : (e?.message || 'draft failed'))
    } finally {
      setDrafting(false)
    }
  }

  // ── save / delete / play ──
  //
  // An openarena task is saved over there, in openarena's own registry: the
  // point of the bridge is one task with one set of hidden cases and one judge,
  // not a copy on each board that drifts apart.
  const saveOa = async (): Promise<string | null> => {
    if (!canSave) { flash(false, 'sign in — a task is filed under the address that wrote it'); return null }
    setSaving(true)
    try {
      const body = {
        title: oa.title, statement: oa.statement, mode: oa.mode,
        language: oa.language, starter: oa.starter || '',
        tags: (oa.tags || []).filter(Boolean),
        tests: oa.tests.map(c => (oa.mode === 'unit'
          ? { name: c.name, hidden: c.hidden, program: c.program || '' }
          : { name: c.name, hidden: c.hidden, stdin: c.stdin || '',
              expect: c.expect || '', compare: c.compare || 'trim' })),
        key: token,
      }
      const r = await fetch(`${API_URL}/arena/openarena/tasks`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }).then(x => x.json())
      if (r.error) { flash(false, r.error); return null }
      const slug = r.task?.slug
      setCopiedFrom(null)
      setDirty(false)
      setDraftNote(null)
      loadOa(); load()
      flash(true, `saved "${r.task?.title}" to openarena — it's in the pool as openarena#${slug}`)
      return slug ? `openarena#${slug}` : null
    } catch (e: any) {
      flash(false, e?.message || 'save failed')
      return null
    } finally {
      setSaving(false)
    }
  }

  const save = async (): Promise<string | null> => {
    if (schema === 'openarena') return saveOa()
    if (!canSave) { flash(false, 'sign in — a task is filed under the address that wrote it'); return null }
    setSaving(true)
    try {
      const body = {
        title: task.title, description: task.description, prompt: task.prompt,
        steps: task.steps || null,
        files: Object.fromEntries(files.filter(f => f.name.trim()).map(f => [f.name.trim(), f.body])),
        scorers: scorers.map(s => {
          const keep = CHECKS[s.type]?.fields || []
          const out: Scorer = { type: s.type }
          keep.forEach(f => { if (s[f] !== undefined && s[f] !== '') (out as any)[f] = f === 'n' ? Number(s[f]) : s[f] })
          return out
        }),
        ...(loaded ? { slug: loaded } : {}),
        key: token,
      }
      const r = await fetch(`${API_URL}/arena/tasks`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }).then(x => x.json())
      if (r.error) { flash(false, r.error); return null }
      const slug = r.task?.index || r.task?.key?.split('#')[1] || null
      setLoaded(slug)
      setDirty(false)
      setDraftNote(null)
      load()
      flash(true, `saved "${r.task?.title}" — it's in the pool as ${r.task?.key}`)
      return r.task?.key || null
    } catch (e: any) {
      flash(false, e?.message || 'save failed')
      return null
    } finally {
      setSaving(false)
    }
  }

  const remove = async (slug: string, title: string) => {
    if (!confirm(`Delete the task "${title}"? Matches already played keep their scores.`)) return
    try {
      const q = token ? `?key=${encodeURIComponent(token)}` : ''
      const r = await fetch(`${API_URL}/arena/tasks/${encodeURIComponent(slug)}${q}`, { method: 'DELETE' })
        .then(x => x.json())
      if (r.error) { flash(false, r.error); return }
      if (loaded === slug) reset()
      load()
      flash(true, `deleted "${title}"`)
    } catch (e: any) { flash(false, e?.message || 'delete failed') }
  }

  // saving and then playing is the whole loop: write a task, watch the field
  // take it. Only the host can spend the board's steps on demand.
  const saveAndPlay = async () => {
    const key = await save()
    if (!key) return
    setPlaying(true)
    try {
      const r = await fetch(`${API_URL}/arena/run`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task: key, key: token }),
        signal: AbortSignal.timeout(600000),
      }).then(x => x.json())
      if (r.error) { flash(false, r.error); return }
      flash(true, 'round played — the board has the scores')
      onOpenArena?.()
    } catch (e: any) {
      flash(false, e?.message || 'the round did not finish')
    } finally { setPlaying(false) }
  }

  const risk = useMemo(
    () => (schema === 'openarena' ? oaRisk(oa) : noOpRisk(files, scorers)),
    [schema, oa, files, scorers])
  const ready = schema === 'openarena'
    ? oa.title.trim().length > 0 && oa.statement.trim().length > 11 &&
      oa.tests.length > 0 && oa.tests.some(c => !c.hidden) &&
      oa.tests.every(c => (oa.mode === 'unit'
        ? !!String(c.program || '').trim()
        : !!String(c.expect || '').trim()))
    : task.title.trim().length > 0 && task.prompt.trim().length > 11 && scorers.length > 0

  // ── the rail: your tasks, and what else is in the pool ──
  const rail = (
    <div className="w-56 shrink-0 border-r border-white/[0.06] flex flex-col min-h-0 bg-surface-1">
      <div className="px-3 py-2.5 border-b border-white/[0.06] shrink-0">
        <div className="text-[10px] text-gray-500 uppercase tracking-wider font-medium">
          {schema === 'openarena' ? 'On openarena' : 'Your tasks'}
        </div>
        <div className="text-[9px] text-gray-700 mt-0.5">
          {schema === 'openarena'
            ? 'a program, graded by its sandbox'
            : 'every agent on the board plays these'}
        </div>
      </div>
      {/* the schema switch: the two things a task can measure. The pixel skin's
          font is wide and the rail is 224px, so the buttons must be allowed to
          shrink — flex-1 alone floors at the label's width and overflows. */}
      <div className="px-2 pt-2 shrink-0">
        <div className="tab-strip gap-0.5 bg-white/[0.03] border border-white/[0.07] rounded-lg p-0.5 flex overflow-hidden">
          {(['agent', 'openarena'] as const).map(s => (
            <button key={s} onClick={() => { setSchema(s); setDraftNote(null); setDirty(false) }}
              title={s === 'agent'
                ? 'grades the trace and the files left behind'
                : 'grades the program against test cases, in openarena\'s sandbox'}
              className={`tab-btn flex-1 min-w-0 truncate px-1 py-1 rounded-md uppercase text-[9px] transition ${
                schema === s ? 'bg-emerald-500/15 text-emerald-200' : 'text-gray-600 hover:text-gray-300'
              }`}>
              {s}
            </button>
          ))}
        </div>
      </div>
      <div className="p-2 shrink-0">
        <button onClick={reset}
          className="w-full flex items-center gap-2 px-2.5 py-2 rounded-lg border border-emerald-500/25 bg-emerald-500/[0.07] hover:bg-emerald-500/[0.12] transition text-left">
          <span className="text-emerald-300 text-sm">✦</span>
          <span className="text-xs text-gray-200">new task</span>
        </button>
      </div>

      {schema === 'openarena' ? (
        <div className="flex-1 overflow-y-auto min-h-0 px-2 pb-2 space-y-1">
          {oaUp === false && (
            <div className="px-2 py-3 text-[10px] text-amber-400/80 leading-relaxed">
              openarena is not answering — start it with{' '}
              <span className="text-gray-400">m openarena/serve</span>. You can still write a task
              here; it cannot be saved until the module is up.
            </div>
          )}
          {oaUp && oaRows.length === 0 && (
            <div className="px-2 py-3 text-[10px] text-gray-600 leading-relaxed">
              no tasks over there yet. Write one on the right, or import a benchmark from the
              board&apos;s OPENARENA rail.
            </div>
          )}
          {oaRows.map(t => (
            <div key={t.slug} onClick={() => openOa(t.slug)}
              className={`group px-2 py-1.5 rounded-md border cursor-pointer transition ${
                copiedFrom === t.slug
                  ? 'border-emerald-500/30 bg-emerald-500/[0.08]'
                  : 'border-transparent hover:border-white/[0.12] hover:bg-white/[0.04]'
              }`}>
              <div className="flex items-center gap-1.5">
                <span className="text-[11px] text-gray-300 truncate flex-1">{t.title}</span>
                <button onClick={e => { e.stopPropagation(); removeOa(t.slug, t.title) }}
                  className="text-gray-700 hover:text-red-400 transition text-[10px] opacity-0 group-hover:opacity-100"
                  title="Delete it in openarena — its author, or the host">✕</button>
              </div>
              <div className="text-[9px] text-gray-600 truncate">
                {t.mode} · {t.cases} case{t.cases === 1 ? '' : 's'}
                {t.hidden ? ` · ${t.hidden} hidden` : ''}
                {t.author?.startsWith('0x') ? ` · ${shortAddr(t.author)}` : t.author ? ` · ${t.author}` : ''}
              </div>
            </div>
          ))}
        </div>
      ) : (
      <div className="flex-1 overflow-y-auto min-h-0 px-2 pb-2 space-y-1">
        {mine.length === 0 && (
          <div className="px-2 py-3 text-[10px] text-gray-600 leading-relaxed">
            none yet. Describe one on the right and the task-builder writes the spec.
          </div>
        )}
        {mine.map(t => (
          <div key={t.slug}
            onClick={() => open(t)}
            className={`group px-2 py-1.5 rounded-md border cursor-pointer transition ${
              loaded === t.slug
                ? 'border-emerald-500/30 bg-emerald-500/[0.08]'
                : 'border-transparent hover:border-white/[0.12] hover:bg-white/[0.04]'
            }`}>
            <div className="flex items-center gap-1.5">
              <span className="text-[11px] text-gray-300 truncate flex-1">{t.title}</span>
              {canEdit(t) && (
                <button onClick={e => { e.stopPropagation(); remove(t.slug, t.title) }}
                  className="text-gray-700 hover:text-red-400 transition text-[10px] opacity-0 group-hover:opacity-100"
                  title="Delete this task">✕</button>
              )}
            </div>
            <div className="text-[9px] text-gray-600 truncate">
              custom#{t.slug} · {(t.scorers || []).length} check{(t.scorers || []).length === 1 ? '' : 's'}
              {t.owner && !canEdit(t) ? ` · ${shortAddr(t.owner)}` : ''}
            </div>
          </div>
        ))}
      </div>
      )}
      <div className="px-3 py-2 border-t border-white/[0.06] shrink-0 text-[9px] text-gray-700">
        pool: {pool.length} task{pool.length === 1 ? '' : 's'} across{' '}
        {new Set(pool.map(t => t.suite)).size} suites
        {onOpenArena && (
          <button onClick={onOpenArena} className="ml-1 text-emerald-500/70 hover:text-emerald-300 transition">
            board →
          </button>
        )}
      </div>
    </div>
  )

  const card = (title: string, hint: string, body: React.ReactNode, accent = 'text-gray-500') => (
    <section className="rounded-xl border border-white/[0.07] bg-surface-1/60 overflow-hidden">
      <div className="px-3 py-2 border-b border-white/[0.05] flex items-baseline gap-2">
        <span className={`text-[10px] uppercase tracking-wider font-medium ${accent}`}>{title}</span>
        <span className="text-[9px] text-gray-700 truncate">{hint}</span>
      </div>
      <div className="p-3 space-y-2">{body}</div>
    </section>
  )

  const input = 'w-full bg-white/[0.04] border border-white/[0.08] rounded-md px-2.5 py-1.5 text-xs text-gray-200 outline-none placeholder:text-gray-700 focus:border-emerald-500/40 transition'

  return (
    <div className="h-full flex min-h-0">
      {rail}

      <div className="flex-1 flex flex-col min-h-0 min-w-0">
        {/* toolbar */}
        <div className="border-b border-white/[0.06] px-3 py-2 flex items-center gap-2 shrink-0 bg-surface-1">
          <span className="text-[10px] uppercase tracking-wider text-gray-500">
            {schema === 'openarena'
              ? (copiedFrom ? `copied from openarena#${copiedFrom}` : 'new openarena task')
              : loaded ? `editing custom#${loaded}` : 'new task'}
          </span>
          {msg && (
            <span className={`text-[11px] px-2 py-1 rounded-md truncate min-w-0 ${msg.ok ? 'text-emerald-300 bg-emerald-500/10' : 'text-red-400 bg-red-500/10'}`}>
              {msg.text}
            </span>
          )}
          <div className="ml-auto flex items-center gap-1.5 shrink-0">
            {dirty && <span className="text-[10px] text-amber-300/70" title="Unsaved changes">● unsaved</span>}
            {!canSave && (
              <button onClick={onSignIn}
                className="px-3 py-1.5 rounded-md text-xs font-medium border border-sky-500/30 bg-sky-500/10 text-sky-200 hover:bg-sky-500/20 transition">
                sign in to save
              </button>
            )}
            {isHost && (
              <button onClick={saveAndPlay} disabled={!ready || saving || playing}
                title="Save, then run the whole field on it now"
                className="px-3 py-1.5 rounded-md text-xs border border-white/[0.1] text-gray-400 hover:text-gray-200 hover:border-white/25 disabled:opacity-40 disabled:cursor-not-allowed transition">
                {playing ? 'playing…' : 'save + play'}
              </button>
            )}
            <button onClick={save} disabled={!ready || saving || !canSave}
              title={ready ? undefined : schema === 'openarena'
                ? 'a task needs a title, a statement, and cases with an expected result — at least one of them visible'
                : 'a task needs a title, a prompt and at least one check'}
              className="px-3 py-1.5 rounded-md text-xs font-medium bg-emerald-600/90 hover:bg-emerald-500 disabled:opacity-40 disabled:cursor-not-allowed text-white transition">
              {saving ? 'saving…'
                : schema === 'openarena' ? 'add to openarena'
                : loaded ? 'save changes' : 'add to the pool'}
            </button>
          </div>
        </div>

        {/* composer */}
        <div className="flex-1 min-h-0 overflow-y-auto">
          <div className="max-w-3xl mx-auto w-full p-4 space-y-3">

            {/* describe → the task-builder agent writes it */}
            {card('Describe',
              schema === 'openarena' ? 'plain words in, a statement and its cases out'
                                     : 'plain words in, a gradeable task out', (
              <>
                <textarea
                  value={describe}
                  onChange={e => setDescribe(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) draft() }}
                  rows={3}
                  placeholder={schema === 'openarena'
                    ? 'Given a list of integers on stdin, print the length of the longest run of equal values.'
                    : 'Can the agent read a CSV, drop the rows with a missing price, and write the total to total.txt?'}
                  className={`${input} resize-none leading-relaxed`} />
                <div className="flex items-center gap-2">
                  <button onClick={draft} disabled={drafting || describe.trim().length < 8}
                    className="px-3 py-1.5 rounded-md text-xs font-medium border border-emerald-500/25 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/20 disabled:opacity-40 disabled:cursor-not-allowed transition flex items-center gap-1.5">
                    <span>◎</span>
                    {drafting ? 'the task-builder is writing…' : 'draft with task-builder'}
                  </button>
                  <span className="text-[9px] text-gray-700">
                    {drafting ? 'one agent run — it thinks about how to grade it' : '⌘↵ · fills the form below, nothing is saved'}
                  </span>
                </div>
                {draftNote && (
                  <div className="text-[10px] text-amber-300/80 bg-amber-500/[0.07] border border-amber-500/20 rounded-md px-2 py-1.5 leading-relaxed">
                    {draftNote}
                  </div>
                )}
              </>
            ), 'text-emerald-300/80')}

            {/* ── the openarena schema ─────────────────────────────── */}
            {schema === 'openarena' && (
              <>
                {copiedFrom && (
                  <div className="text-[10px] text-sky-300/90 bg-sky-500/[0.07] border border-sky-500/20 rounded-md px-2 py-1.5 leading-relaxed">
                    This is a copy of <span className="font-mono">openarena#{copiedFrom}</span>. A task
                    over there cannot be edited in place — change the title so it gets its own slug, or
                    delete the original from the rail first. Saving under a name openarena already holds
                    is refused.
                  </div>
                )}
                {oaUp === false && (
                  <div className="text-[10px] text-amber-300/90 bg-amber-500/[0.07] border border-amber-500/20 rounded-md px-2 py-1.5">
                    openarena is not answering — this task cannot be saved until it is up.
                  </div>
                )}

                {card('Task', 'the name, how it is graded, and what it runs as', (
                  <>
                    <input value={oa.title} onChange={e => patchOa({ title: e.target.value })}
                      placeholder="reverse the words in a sentence" className={input} />
                    <div className="flex flex-wrap gap-2">
                      <label className="flex items-center gap-1.5">
                        <span className="text-[9px] text-gray-600 uppercase tracking-wider">mode</span>
                        <Select size="sm" accent="emerald" className="w-40"
                          value={oa.mode}
                          onChange={v => {
                            const mode = v as 'io' | 'unit'
                            // the case shape changes with the mode, so the cases
                            // are rebuilt rather than left half-filled
                            setOa(t => ({ ...t, mode,
                              tests: t.tests.map(c => ({ ...blankCase(mode, c.hidden), name: c.name })) }))
                            setDirty(true)
                          }}
                          options={OA_MODES.map(m => ({
                            value: m, label: m,
                            hint: m === 'io' ? 'reads stdin, prints stdout — any language'
                                             : 'imported by graders that assert — python',
                          }))} />
                      </label>
                      <label className="flex items-center gap-1.5">
                        <span className="text-[9px] text-gray-600 uppercase tracking-wider">language</span>
                        <Select size="sm" accent="emerald" className="w-36"
                          value={oa.mode === 'unit' ? 'python' : oa.language}
                          onChange={v => patchOa({ language: v })}
                          options={(oa.mode === 'unit' ? ['python'] : OA_LANGS).map(l => ({
                            value: l, label: l,
                            hint: l === 'any' ? 'the competitor picks' : undefined,
                          }))} />
                      </label>
                      <label className="flex items-center gap-1.5 flex-1 min-w-[160px]">
                        <span className="text-[9px] text-gray-600 uppercase tracking-wider">tags</span>
                        <input value={(oa.tags || []).join(' ')}
                          onChange={e => patchOa({ tags: e.target.value.split(/[\s,]+/).filter(Boolean) })}
                          placeholder="strings parsing" className={`${input} py-1`} />
                      </label>
                    </div>
                  </>
                ))}

                {card('Statement', 'the problem, as a competitor reads it', (
                  <>
                    <textarea value={oa.statement} onChange={e => patchOa({ statement: e.target.value })}
                      rows={5}
                      placeholder={oa.mode === 'unit'
                        ? 'Define a class Stack with push, pop and is_empty. Popping an empty stack raises IndexError.'
                        : 'Read a line from stdin and print its words in reverse order, separated by single spaces.'}
                      className={`${input} resize-none leading-relaxed font-mono text-[11px]`} />
                    <div className="text-[9px] text-gray-700 leading-relaxed">
                      {oa.mode === 'unit'
                        ? 'The submission is saved as solution.py and imported by each grader, so say what to define — not what to print.'
                        : 'Say what is read and what is printed. No files, no directories, no steps: the program is the answer.'}
                      {' '}The visible cases below are shown to the competitor too.
                    </div>
                    <textarea value={oa.starter || ''} onChange={e => patchOa({ starter: e.target.value })}
                      rows={2} spellCheck={false}
                      placeholder="optional starter code handed to the competitor"
                      className={`${input} resize-y leading-relaxed font-mono text-[11px]`} />
                  </>
                ), 'text-amber-300/80')}

                {card('Cases', 'the whole grade — hidden ones are graded, never shown', (
                  <>
                    {oa.tests.map((c, i) => (
                      <div key={i} className="rounded-lg border border-white/[0.07] overflow-hidden">
                        <div className="flex items-center gap-2 px-2 py-1.5 bg-white/[0.02] border-b border-white/[0.05]">
                          <input value={c.name}
                            onChange={e => patchCase(i, { name: e.target.value })}
                            placeholder={`case ${i + 1}`}
                            className="flex-1 bg-transparent text-[11px] font-mono text-gray-300 outline-none placeholder:text-gray-700" />
                          {oa.mode === 'io' && (
                            <Select size="sm" accent="emerald" className="w-28"
                              value={c.compare || 'trim'}
                              onChange={v => patchCase(i, { compare: v })}
                              options={OA_COMPARE.map(k => ({
                                value: k, label: k,
                                hint: k === 'trim' ? 'edge whitespace is formatting'
                                    : k === 'exact' ? 'byte for byte'
                                    : 'expected text appears somewhere',
                              }))} />
                          )}
                          <label className="flex items-center gap-1 cursor-pointer shrink-0"
                            title="graded, and never shown to the competitor">
                            <input type="checkbox" checked={c.hidden}
                              onChange={e => patchCase(i, { hidden: e.target.checked })}
                              className="accent-emerald-500" />
                            <span className={`text-[9px] uppercase tracking-wider ${c.hidden ? 'text-emerald-300/80' : 'text-gray-600'}`}>
                              hidden
                            </span>
                          </label>
                          <button onClick={() => { setOa(t => ({ ...t, tests: t.tests.filter((_, j) => j !== i) })); setDirty(true) }}
                            className="text-gray-600 hover:text-red-400 transition text-[11px]">✕</button>
                        </div>
                        {oa.mode === 'unit' ? (
                          <textarea value={c.program || ''}
                            onChange={e => patchCase(i, { program: e.target.value })}
                            rows={4} spellCheck={false}
                            placeholder={'from solution import Stack\ns = Stack()\nassert s.is_empty()'}
                            className="w-full bg-transparent px-2.5 py-2 text-[11px] font-mono text-gray-400 outline-none placeholder:text-gray-700 resize-y leading-relaxed" />
                        ) : (
                          <div className="grid grid-cols-2 divide-x divide-white/[0.05]">
                            <textarea value={c.stdin || ''}
                              onChange={e => patchCase(i, { stdin: e.target.value })}
                              rows={3} spellCheck={false} placeholder="stdin"
                              className="bg-transparent px-2.5 py-2 text-[11px] font-mono text-gray-400 outline-none placeholder:text-gray-700 resize-y leading-relaxed" />
                            <textarea value={c.expect || ''}
                              onChange={e => patchCase(i, { expect: e.target.value })}
                              rows={3} spellCheck={false} placeholder="expected stdout — compute it"
                              className="bg-transparent px-2.5 py-2 text-[11px] font-mono text-gray-400 outline-none placeholder:text-gray-700 resize-y leading-relaxed" />
                          </div>
                        )}
                      </div>
                    ))}
                    <div className="flex items-center gap-2">
                      <button onClick={() => { setOa(t => ({ ...t, tests: [...t.tests, blankCase(t.mode)] })); setDirty(true) }}
                        className="text-[10px] text-gray-600 hover:text-emerald-300 transition">
                        + visible case
                      </button>
                      <button onClick={() => { setOa(t => ({ ...t, tests: [...t.tests, blankCase(t.mode, true)] })); setDirty(true) }}
                        className="text-[10px] text-gray-600 hover:text-emerald-300 transition">
                        + hidden case
                      </button>
                      <span className="text-[9px] text-gray-700 ml-auto">
                        {oa.tests.filter(c => !c.hidden).length} visible · {oa.tests.filter(c => c.hidden).length} hidden
                      </span>
                    </div>
                    {risk && (
                      <div className="text-[10px] text-amber-300/90 bg-amber-500/[0.07] border border-amber-500/20 rounded-md px-2 py-1.5 leading-relaxed">
                        <span className="uppercase tracking-wider text-[9px] text-amber-300">read this</span>
                        <div className="mt-0.5">{risk}</div>
                      </div>
                    )}
                  </>
                ), 'text-sky-300/80')}

                <div className="text-[9px] text-gray-700 leading-relaxed pb-2">
                  correctness is the weighted fraction of cases that pass, judged in openarena&apos;s
                  sandbox — so 7 of 10 is 0.7, not a zero. The other 0.3 of the score is the same as
                  ever: 0.2 reliability, 0.1 unspent budget. A saved task joins the rotation on both
                  boards.
                </div>
              </>
            )}

            {/* ── the agent schema ─────────────────────────────────── */}
            {schema === 'agent' && <>
            {/* the task itself */}
            {card('Task', 'what it is called, and how long the agent gets', (
              <>
                <div className="flex gap-2">
                  <input value={task.title} onChange={e => patch({ title: e.target.value })}
                    placeholder="count the lines of a file" className={`${input} flex-1`} />
                  <div className="flex items-center gap-1.5 shrink-0">
                    <span className="text-[10px] text-gray-600 uppercase tracking-wider">steps</span>
                    <input type="number" min={1} max={30} value={task.steps ?? 6}
                      onChange={e => patch({ steps: Number(e.target.value) })}
                      className={`${input} w-16 text-center`} />
                  </div>
                </div>
                <input value={task.description || ''} onChange={e => patch({ description: e.target.value })}
                  placeholder="one line on what this measures" className={input} />
              </>
            ))}

            {/* prompt */}
            {card('Prompt', 'exactly what the agent is told', (
              <>
                <textarea value={task.prompt} onChange={e => patch({ prompt: e.target.value })}
                  rows={5}
                  placeholder={'Your working directory is {workdir}. It contains notes.txt. Count the lines in it and write {workdir}/count.txt containing that number and nothing else. Then finish.'}
                  className={`${input} resize-none leading-relaxed font-mono text-[11px]`} />
                <div className="flex items-center gap-2 text-[9px] text-gray-700">
                  <button
                    onClick={() => patch({ prompt: `${task.prompt}{workdir}` })}
                    className="px-1.5 py-0.5 rounded border border-white/[0.08] text-gray-500 hover:text-emerald-300 hover:border-emerald-500/30 transition font-mono">
                    + {'{workdir}'}
                  </button>
                  <span>replaced with the match&apos;s scratch dir · end with &ldquo;Then finish.&rdquo;</span>
                </div>
              </>
            ), 'text-amber-300/80')}

            {/* fixture */}
            {card('Fixture', 'seeded into the scratch dir before the agent starts', (
              <>
                {files.length === 0 && (
                  <div className="text-[10px] text-gray-600">
                    empty — the agent starts in a bare directory. Add a file if the task has to read something.
                  </div>
                )}
                {files.map((f, i) => (
                  <div key={i} className="rounded-lg border border-white/[0.07] overflow-hidden">
                    <div className="flex items-center gap-2 px-2 py-1.5 bg-white/[0.02] border-b border-white/[0.05]">
                      <input value={f.name}
                        onChange={e => { const v = e.target.value; setFiles(fs => fs.map((x, j) => j === i ? { ...x, name: v } : x)); setDirty(true) }}
                        placeholder="notes.txt"
                        className="flex-1 bg-transparent text-[11px] font-mono text-gray-300 outline-none placeholder:text-gray-700" />
                      <span className="text-[9px] text-gray-700">{f.body.length} chars</span>
                      <button onClick={() => { setFiles(fs => fs.filter((_, j) => j !== i)); setDirty(true) }}
                        className="text-gray-600 hover:text-red-400 transition text-[11px]">✕</button>
                    </div>
                    <textarea value={f.body}
                      onChange={e => { const v = e.target.value; setFiles(fs => fs.map((x, j) => j === i ? { ...x, body: v } : x)); setDirty(true) }}
                      rows={4} spellCheck={false}
                      placeholder="the contents this file starts with"
                      className="w-full bg-transparent px-2.5 py-2 text-[11px] font-mono text-gray-400 outline-none placeholder:text-gray-700 resize-y leading-relaxed" />
                  </div>
                ))}
                <button onClick={() => { setFiles(fs => [...fs, { name: '', body: '' }]); setDirty(true) }}
                  className="text-[10px] text-gray-600 hover:text-emerald-300 transition">
                  + add a file
                </button>
              </>
            ), 'text-violet-300/80')}

            {/* checks */}
            {card('Checks', 'deterministic — no judge, no opinion', (
              <>
                {scorers.length === 0 && (
                  <div className="text-[10px] text-gray-600">
                    no checks yet. Without one every agent scores the same and the match measures nothing.
                  </div>
                )}
                {scorers.map((s, i) => {
                  const meta = CHECKS[s.type] || { fields: [], hint: '' }
                  return (
                    <div key={i} className="rounded-lg border border-white/[0.07] p-2 space-y-1.5">
                      <div className="flex items-center gap-2">
                        <Select
                          size="sm" accent="emerald" className="w-52"
                          value={s.type}
                          onChange={v => { setScorers(ss => ss.map((x, j) => j === i ? { type: v } : x)); setDirty(true) }}
                          options={CHECK_ORDER.map(k => ({
                            value: k, label: k, icon: CHECKS[k].artifact ? '◆' : '◇', hint: CHECKS[k].hint,
                          }))} />
                        <span className="text-[9px] text-gray-600 truncate flex-1">{meta.hint}</span>
                        <button onClick={() => { setScorers(ss => ss.filter((_, j) => j !== i)); setDirty(true) }}
                          className="text-gray-600 hover:text-red-400 transition text-[11px]">✕</button>
                      </div>
                      {!!meta.fields.length && (
                        <div className="flex flex-wrap gap-1.5">
                          {meta.fields.map(f => (
                            <label key={f} className="flex items-center gap-1.5 flex-1 min-w-[160px]">
                              <span className="text-[9px] text-gray-600 uppercase tracking-wider w-9 shrink-0">{FIELD_META[f].label}</span>
                              <input
                                value={(s as any)[f] ?? ''}
                                onChange={e => {
                                  const v = e.target.value
                                  setScorers(ss => ss.map((x, j) => j === i ? { ...x, [f]: v } : x))
                                  setDirty(true)
                                }}
                                placeholder={FIELD_META[f].placeholder}
                                className={`${input} font-mono text-[11px] py-1`} />
                            </label>
                          ))}
                        </div>
                      )}
                    </div>
                  )
                })}
                <div className="flex items-center gap-2">
                  <Select
                    size="sm" accent="emerald" className="w-52" placeholder="+ add a check…"
                    value=""
                    onChange={v => { setScorers(ss => [...ss, { type: v }]); setDirty(true) }}
                    options={CHECK_ORDER.map(k => ({
                      value: k, label: k, icon: CHECKS[k].artifact ? '◆' : '◇', hint: CHECKS[k].hint,
                    }))} />
                  <span className="text-[9px] text-gray-700">◆ grades the files left behind · ◇ grades the run</span>
                </div>
                {risk && (
                  <div className="text-[10px] text-amber-300/90 bg-amber-500/[0.07] border border-amber-500/20 rounded-md px-2 py-1.5 leading-relaxed">
                    <span className="uppercase tracking-wider text-[9px] text-amber-300">no-op warning</span>
                    <div className="mt-0.5">{risk}</div>
                  </div>
                )}
              </>
            ), 'text-sky-300/80')}

            <div className="text-[9px] text-gray-700 leading-relaxed pb-2">
              score = 0.7 correctness (these checks) + 0.2 reliability (no errors, it finished)
              + 0.1 efficiency (budget left unspent). A saved task joins the rotation and is
              played by every agent on the board.
            </div>
            </>}
          </div>
        </div>
      </div>
    </div>
  )
}
