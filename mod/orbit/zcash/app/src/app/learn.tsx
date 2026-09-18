'use client'

// LEARN and ASK.
//
// Both read from the module rather than from strings kept here, on purpose:
// the lessons and the agent's answers describe what this module can and cannot
// do, and a copy of that text in the front end is a copy that goes stale
// exactly when it matters (someone reading "this module cannot spend shielded
// ZEC" after a node was configured, or worse, the reverse).
//
// The ASK tab renders the agent's `actions` as buttons. Guarded ones are
// marked rather than hidden -- knowing that scanning your notes needs the
// token is part of the explanation.

import { useEffect, useState } from 'react'
import { call } from './api'
import { Button, C, Code, Note, Panel, Spinner } from './ui'

type Lesson = {
  id: string, title: string, level: string, minutes: number,
  summary: string, terms: string[], body?: string[],
  try?: Action[], next?: string | null,
}

type Action = {
  label: string, fn: string, args?: Record<string, any>, guarded?: boolean,
}

const LEVEL_COLOR: Record<string, string> = {
  start: C.green, core: C.gold, deep: C.blue,
}

// ── LEARN ───────────────────────────────────────────────────────────────────

export function Learn({ onAction }: { onAction?: (a: Action) => void }) {
  const [list, setList] = useState<Lesson[]>([])
  const [paths, setPaths] = useState<Record<string, string[]>>({})
  const [path, setPath] = useState<string>('')
  const [open, setOpen] = useState<Lesson | null>(null)
  const [terms, setTerms] = useState<any[] | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(true)

  const load = (p: string) => {
    setBusy(true); setErr(''); setOpen(null); setTerms(null)
    call('learn', p ? { path: p } : {})
      .then((r: any) => { setList(r.lessons || []); setPaths(r.paths || {}) })
      .catch((e: any) => setErr(e.message))
      .finally(() => setBusy(false))
  }

  useEffect(() => { load('') }, [])

  const openLesson = async (id: string) => {
    setErr(''); setTerms(null)
    try { setOpen(await call('learn', { topic: id })) }
    catch (e: any) { setErr(e.message) }
  }

  const showGlossary = async () => {
    setErr(''); setOpen(null)
    try { setTerms((await call('learn', { glossary: true })).terms) }
    catch (e: any) { setErr(e.message) }
  }

  if (open) return (
    <LessonView lesson={open} onBack={() => setOpen(null)}
      onOpen={openLesson} onAction={onAction} />
  )

  return (
    <>
      {err && <Note kind="error">{err}</Note>}

      <Panel title="Learn Zcash">
        <Note kind="info">
          Zcash is Bitcoin-shaped money with an optional invisibility cloak.
          These lessons assume you know nothing, and every one of them ends with
          something you can actually run here. About {list.reduce((n, l) => n + l.minutes, 0)} minutes
          for all of them.
        </Note>

        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 14 }}>
          <Chip active={path === ''} onClick={() => { setPath(''); load('') }}>
            everything
          </Chip>
          {Object.keys(paths).map(p => (
            <Chip key={p} active={path === p} onClick={() => { setPath(p); load(p) }}>
              {p}
            </Chip>
          ))}
          <Chip active={!!terms} onClick={showGlossary}>glossary</Chip>
        </div>

        {busy && <Spinner />}

        {!busy && !terms && list.map(l => (
          <button key={l.id} onClick={() => openLesson(l.id)} style={{
            display: 'block', width: '100%', textAlign: 'left', cursor: 'pointer',
            background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 8,
            padding: '13px 15px', marginBottom: 8, color: C.text,
          }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 9, marginBottom: 4 }}>
              <span style={{ fontSize: 14, fontWeight: 650 }}>{l.title}</span>
              <span style={{
                fontSize: 9.5, letterSpacing: 0.9, textTransform: 'uppercase',
                color: LEVEL_COLOR[l.level] || C.dim,
              }}>{l.level}</span>
              <span style={{ fontSize: 10.5, color: C.dim, marginLeft: 'auto' }}>
                {l.minutes} min
              </span>
            </div>
            <div style={{ fontSize: 12.5, color: C.dim, lineHeight: 1.5 }}>{l.summary}</div>
          </button>
        ))}
      </Panel>

      {terms && (
        <Panel title={`Glossary — ${terms.length} terms`}>
          {terms.map((t: any) => (
            <div key={t.term} style={{
              padding: '9px 0', borderBottom: `1px solid ${C.line}`,
            }}>
              <div style={{ fontSize: 12.5, fontWeight: 650, color: C.gold }}>{t.term}</div>
              <div style={{ fontSize: 12.5, color: C.dim, lineHeight: 1.55, marginTop: 2 }}>
                {t.definition}
              </div>
              <button onClick={() => openLesson(t.lesson)} style={{
                background: 'none', border: 'none', color: C.blue, fontSize: 11,
                cursor: 'pointer', padding: '4px 0 0',
              }}>read “{t.lesson}” →</button>
            </div>
          ))}
        </Panel>
      )}
    </>
  )
}

function LessonView({ lesson, onBack, onOpen, onAction }: {
  lesson: Lesson, onBack: () => void, onOpen: (id: string) => void,
  onAction?: (a: Action) => void,
}) {
  return (
    <>
      <Panel
        title={lesson.title}
        right={<button onClick={onBack} style={{
          background: 'none', border: `1px solid ${C.line}`, borderRadius: 5,
          color: C.dim, fontSize: 11, padding: '3px 9px', cursor: 'pointer',
        }}>all lessons</button>}
      >
        <div style={{
          fontSize: 13, color: C.gold, marginBottom: 14, lineHeight: 1.5,
          borderLeft: `2px solid ${C.gold}`, paddingLeft: 11,
        }}>{lesson.summary}</div>

        {(lesson.body || []).map((p, i) => (
          <p key={i} style={{
            fontSize: 13.5, lineHeight: 1.7, color: C.text, margin: '0 0 14px',
          }}>{p}</p>
        ))}

        {lesson.terms?.length > 0 && (
          <div style={{ marginTop: 6, fontSize: 11.5, color: C.dim }}>
            Terms: {lesson.terms.join(' · ')}
          </div>
        )}
      </Panel>

      {lesson.try?.length ? (
        <Panel title="Try it">
          {lesson.try.map((a, i) => (
            <ActionRow key={i} action={a} onAction={onAction} />
          ))}
        </Panel>
      ) : null}

      {lesson.next && (
        <Panel>
          <button onClick={() => onOpen(lesson.next!)} style={{
            background: 'none', border: 'none', color: C.blue, fontSize: 13,
            cursor: 'pointer', padding: 0,
          }}>Next lesson: {lesson.next} →</button>
        </Panel>
      )}
    </>
  )
}

// ── ASK ─────────────────────────────────────────────────────────────────────

const SAMPLES = [
  'How do I bridge USDC into a shielded address?',
  "Why can't I send shielded ZEC?",
  "What's the difference between t1 and zs1?",
  'Is my viewing key safe to share?',
  'My shielded balance is zero',
  'How private is Zcash really?',
]

export function Ask({ onAction }: { onAction?: (a: Action) => void }) {
  const [q, setQ] = useState('')
  const [answer, setAnswer] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [status, setStatus] = useState<any>(null)

  useEffect(() => { call('agent_status').then(setStatus).catch(() => {}) }, [])

  const send = async (question: string) => {
    if (!question.trim()) return
    setBusy(true); setErr(''); setAnswer(null)
    try { setAnswer(await call('ask', { question })) }
    catch (e: any) { setErr(e.message) }
    finally { setBusy(false) }
  }

  return (
    <>
      {err && <Note kind="error">{err}</Note>}

      <Panel title="Ask">
        <Note kind="info">
          Answers come from this module&apos;s own lessons and live reads, so
          they describe what it actually does rather than Zcash in general. It
          will never send a transaction to answer a question — anything that
          spends comes back as a button for you to press deliberately.
        </Note>

        <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
          <input
            value={q}
            onChange={e => setQ(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') send(q) }}
            placeholder="ask anything about Zcash or this module…"
            style={{
              flex: 1, minWidth: 0, padding: '10px 12px', background: C.bg,
              border: `1px solid ${C.line}`, borderRadius: 6, color: C.text,
              fontSize: 13, outline: 'none',
            }} />
          <Button onClick={() => send(q)} disabled={busy || !q.trim()}>
            {busy ? '…' : 'Ask'}
          </Button>
        </div>

        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {SAMPLES.map(s => (
            <Chip key={s} onClick={() => { setQ(s); send(s) }}>{s}</Chip>
          ))}
        </div>
      </Panel>

      {busy && <Panel><Spinner label="thinking" /></Panel>}

      {answer && (
        <>
          <Panel
            title="Answer"
            right={<span style={{ fontSize: 10, color: C.dim }}>
              {answer.confidence === 'none' ? 'no match' : `${answer.confidence} confidence`}
              {answer.source ? ` · ${answer.source}` : ''}
            </span>}
          >
            {(answer.answer || '').split('\n\n').map((p: string, i: number) => (
              <p key={i} style={{
                fontSize: 13.5, lineHeight: 1.7, margin: '0 0 12px',
                whiteSpace: 'pre-wrap',
              }}>{p}</p>
            ))}
            {answer.model_error && (
              <Note kind="warn">
                The language model was unreachable, so this is the written
                answer from the lessons: {answer.model_error}
              </Note>
            )}
          </Panel>

          {answer.actions?.length > 0 && (
            <Panel title="What to do next">
              {answer.actions.map((a: Action, i: number) => (
                <ActionRow key={i} action={a} onAction={onAction} />
              ))}
            </Panel>
          )}

          {answer.grounded && Object.keys(answer.grounded).length > 0 && (
            <Panel title="Live data used in this answer">
              <Code>{JSON.stringify(answer.grounded, null, 1)}</Code>
            </Panel>
          )}

          {answer.terms?.length > 0 && (
            <Panel title="Terms">
              {answer.terms.map((t: any) => (
                <div key={t.term} style={{ marginBottom: 9 }}>
                  <span style={{ fontSize: 12.5, fontWeight: 650, color: C.gold }}>
                    {t.term}
                  </span>
                  <span style={{ fontSize: 12.5, color: C.dim, lineHeight: 1.55 }}>
                    {' '}— {t.definition}
                  </span>
                </div>
              ))}
            </Panel>
          )}

          {answer.lessons?.length > 0 && (
            <Panel title="Read more">
              {answer.lessons.map((l: any) => (
                <div key={l.id} style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: 13, fontWeight: 620 }}>{l.title}</div>
                  {l.summary && <div style={{ fontSize: 12, color: C.dim, lineHeight: 1.5 }}>
                    {l.summary}
                  </div>}
                </div>
              ))}
            </Panel>
          )}
        </>
      )}

      {status && (
        <Panel title="How this answers">
          <div style={{ fontSize: 12, color: C.dim, lineHeight: 1.6 }}>
            {status.intents} question patterns over {status.lessons} written
            lessons and {status.glossary_terms} defined terms.{' '}
            {status.model?.configured
              ? `A language model (${status.model.model || 'configured'}) writes the prose over those sources.`
              : 'No language model is configured, so answers are the written lessons themselves — which is the default and works offline.'}
            <div style={{ marginTop: 6 }}>{status.limits}</div>
          </div>
        </Panel>
      )}
    </>
  )
}

// ── shared bits ─────────────────────────────────────────────────────────────

export function ActionRow({ action, onAction }: {
  action: Action, onAction?: (a: Action) => void,
}) {
  const [out, setOut] = useState<any>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  // Anything with a placeholder argument cannot be run from here -- it would
  // just fail on "<your wallet>". Show the call instead, so it can be copied.
  const placeholders = Object.values(action.args || {})
    .some(v => typeof v === 'string' && v.trim().startsWith('<'))
  const runnable = !action.guarded && !placeholders

  const run = async () => {
    setBusy(true); setErr(''); setOut(null)
    try { setOut(await call(action.fn, action.args || {})) }
    catch (e: any) { setErr(e.message) }
    finally { setBusy(false) }
  }

  return (
    <div style={{
      background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 7,
      padding: '11px 13px', marginBottom: 8,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 9, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13, flex: 1, minWidth: 140 }}>{action.label}</span>
        {action.guarded && (
          <span style={{
            fontSize: 9.5, letterSpacing: 0.8, textTransform: 'uppercase',
            color: C.gold, border: `1px solid ${C.gold}55`, borderRadius: 4,
            padding: '2px 6px',
          }}>needs token</span>
        )}
        {runnable
          ? <Button variant="ghost" onClick={run} disabled={busy}
              style={{ padding: '5px 12px', fontSize: 12 }}>
              {busy ? '…' : 'Run'}
            </Button>
          : <button onClick={() => onAction?.(action)} style={{
              background: 'none', border: `1px solid ${C.line}`, borderRadius: 5,
              color: C.dim, fontSize: 11, padding: '4px 10px', cursor: 'pointer',
            }}>open the tab</button>}
      </div>
      <div style={{
        fontSize: 11, color: C.dim, marginTop: 6,
        fontFamily: 'ui-monospace, Menlo, monospace', wordBreak: 'break-all',
      }}>
        {action.fn}({action.args ? JSON.stringify(action.args) : ''})
      </div>
      {err && <div style={{ fontSize: 11.5, color: C.red, marginTop: 7 }}>{err}</div>}
      {out !== null && (
        <div style={{ marginTop: 8 }}>
          <Code>{JSON.stringify(out, null, 1)}</Code>
        </div>
      )}
    </div>
  )
}

export function Chip({ children, active, onClick }: {
  children: any, active?: boolean, onClick: () => void,
}) {
  return (
    <button onClick={onClick} style={{
      padding: '5px 11px', borderRadius: 20, fontSize: 11.5, cursor: 'pointer',
      background: active ? C.gold : 'transparent',
      color: active ? '#12141a' : C.dim,
      border: `1px solid ${active ? C.gold : C.line}`,
      textAlign: 'left',
    }}>{children}</button>
  )
}
