'use client'

/**
 * Score functions — the rule that splits a pot, as a program you can read,
 * edit, test, save, share and import.
 *
 *   accuracy = f(e)        e = |called − actual| / actual
 *
 * `f` is a one-line expression over `e` and named parameters, in a small
 * sandboxed language the server owns (curves.py). Nothing here evaluates an
 * expression: every curve on screen is sampled by the API, so what is drawn
 * is what pays. Saving is wallet-signed like a free call (the server hands
 * back the exact text, the wallet signs it); the pool owner switches the pot
 * to a function with the same signed config flow the Rules tab uses.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { toast } from 'react-toastify'
import { API_BASE_URL } from '@/lib/contracts'
import { signAction } from '@/lib/hyperevm'
import { usd, short } from '@/lib/fmt'
import { Section, Empty, Tag, Label, Spinner } from '@/components/ui'

const API = API_BASE_URL

const get = async (url: string) => {
  try { const r = await fetch(url); return r.ok ? r.json() : null } catch { return null }
}

async function send(method: string, url: string, body?: any, headers: Record<string, string> = {}) {
  const r = await fetch(url, {
    method,
    headers: body ? { 'content-type': 'application/json', ...headers } : headers,
    body: body ? JSON.stringify(body) : undefined,
  })
  const out = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(out?.detail || out?.error || `HTTP ${r.status}`)
  return out
}

/** base64url JSON, matching python's urlsafe_b64encode(...).rstrip(b"="). */
function b64urlJson(obj: unknown): string {
  const b64 = btoa(unescape(encodeURIComponent(JSON.stringify(obj))))
  return b64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

/**
 * A mod-protocol token, minted by the wallet: `{data, time}` signed with
 * personal_sign, then the envelope base64url'd. This is what the store
 * checks when a function is published — the store's own whitelist and terms
 * apply to the signer, not to this module.
 */
async function mintToken(walletClient: any, address: string): Promise<string> {
  const time = (Date.now() / 1000).toString()
  const data = { mod: 'prefi' }
  const signature = await signAction(walletClient, JSON.stringify({ data, time }))
  return b64urlJson({ data, time, key: address, signature })
}

const paramsText = (p: any) => JSON.stringify(p || {})
const parseParams = (text: string) => {
  const t = (text || '').trim()
  if (!t) return {}
  return JSON.parse(t)
}

type Fn = {
  name: string; description: string; expr: string; params: Record<string, number>
  author?: string; owner?: string; builtin: boolean; origin_cid?: string; cid?: string
  digest: string; sample?: { e: number; score: number }[]
}


/* ─── Curves ──────────────────────────────────────────────────── */

function Sparkline({ sample, active }: { sample?: { e: number; score: number }[]; active?: boolean }) {
  if (!sample?.length) return <span className="inline-block w-[64px] h-[20px]" />
  const n = sample.length - 1
  const d = sample.map((p, i) => `${i === 0 ? 'M' : 'L'}${(i / n * 64).toFixed(1)},${((1 - p.score) * 18 + 1).toFixed(1)}`).join(' ')
  return (
    <svg width="64" height="20" viewBox="0 0 64 20" className="shrink-0">
      <path d={d} fill="none" stroke="currentColor" strokeWidth="1.5"
            className={active ? 'accent' : 't2'} strokeLinejoin="round" />
    </svg>
  )
}

function Curve({ sample }: { sample?: { e: number; score: number }[] }) {
  if (!sample?.length) return null
  const W = 520, H = 150, L = 30, B = 22
  const n = sample.length - 1
  const x = (i: number) => L + i / n * (W - L - 8)
  const y = (s: number) => 6 + (1 - s) * (H - B - 6)
  const d = sample.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(p.score).toFixed(1)}`).join(' ')
  const ticks = sample.map((p, i) => ({ i, e: p.e })).filter(t => [0, 0.01, 0.05, 0.1, 0.5, 1, 2].includes(t.e))
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto">
      {[0, 0.5, 1].map(s => (
        <g key={s}>
          <line x1={L} x2={W - 8} y1={y(s)} y2={y(s)} stroke="currentColor" className="t3" strokeOpacity="0.25" strokeDasharray="2 3" />
          <text x={L - 4} y={y(s) + 3} textAnchor="end" fontSize="9" fill="currentColor" className="t3">{s}</text>
        </g>
      ))}
      {ticks.map(t => (
        <text key={t.e} x={x(t.i)} y={H - 6} textAnchor="middle" fontSize="9" fill="currentColor" className="t3">
          {t.e === 0 ? '0' : t.e < 1 ? `${t.e * 100}%` : `${t.e * 100}%`}
        </text>
      ))}
      <path d={d} fill="none" stroke="currentColor" strokeWidth="2" className="accent" strokeLinejoin="round" />
      {sample.map((p, i) => <circle key={i} cx={x(i)} cy={y(p.score)} r="2" fill="currentColor" className="accent" />)}
      <text x={W - 8} y={12} textAnchor="end" fontSize="9" fill="currentColor" className="t3">accuracy vs miss</text>
    </svg>
  )
}


/* ─── The tab ─────────────────────────────────────────────────── */

export default function Functions({ address, walletClient, cfg, owner, signFor, onDone }: any) {
  const [fns, setFns] = useState<Fn[]>([])
  const [active, setActive] = useState<{ pool?: string; predict?: string }>({})
  const [language, setLanguage] = useState<any>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [draft, setDraft] = useState({ name: '', description: '', expr: 'max(0, 1 - e/tol)', params: '{"tol": 1.0}' })
  const [origin, setOrigin] = useState<{ origin_cid?: string; author?: string } | null>(null)
  const [test, setTest] = useState<any>(null)
  const [share, setShare] = useState<any>(null)
  const [importSrc, setImportSrc] = useState('')
  const [preview, setPreview] = useState<any>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [showLang, setShowLang] = useState(false)

  const me = (address || '').toLowerCase()
  const isOwner = owner?.owner && me && owner.owner.toLowerCase() === me
  const unclaimed = !owner?.claimed

  const refresh = useCallback(async () => {
    const out = await get(`${API}/functions`)
    if (!out) return
    setFns(out.functions || [])
    setActive(out.active || {})
    setLanguage(out.language || null)
  }, [])
  useEffect(() => { refresh() }, [refresh])

  const current = useMemo(() => fns.find(f => f.name === selected) || null, [fns, selected])
  const mine = !!current && !current.builtin && current.owner === me
  const nameTaken = useMemo(() => fns.find(f => f.name === draft.name.trim().toLowerCase()), [fns, draft.name])

  // Load a function into the editor. A default or somebody else's gets a
  // fresh name so saving makes a copy instead of a collision.
  const open = (f: Fn) => {
    setSelected(f.name)
    const own = !f.builtin && f.owner === me
    setDraft({
      name: own ? f.name : `${f.name}_${me ? me.slice(2, 6) : 'v2'}`,
      description: f.description || '', expr: f.expr, params: paramsText(f.params),
    })
    const credit = f.author || f.owner
    setOrigin(own || !credit?.startsWith('0x') ? null : { author: credit })
    setTest(null); setShare(null); setPreview(null)
  }

  const runTest = async () => {
    setBusy('test')
    try {
      const params = parseParams(draft.params)
      const out = await send('POST', `${API}/functions/test`, { expr: draft.expr, params, name: draft.name || undefined })
      setTest(out)
    } catch (err: any) {
      setTest({ error: err.message })
    } finally { setBusy(null) }
  }

  const save = async () => {
    if (!address) return toast.error('Connect a wallet to save a function')
    setBusy('save')
    try {
      const params = parseParams(draft.params)
      const body: any = { address, name: draft.name.trim().toLowerCase(), expr: draft.expr, params,
                          description: draft.description, ...(origin || {}) }
      const req = await send('POST', `${API}/functions/sign`, body)
      if (req.required) body.signature = await signAction(walletClient, req.message)
      body.nonce = req.nonce
      const out = await send('POST', `${API}/functions`, body)
      toast.success(`Saved ${out.function.name}`)
      setOrigin(null)
      await refresh()
      setSelected(out.function.name)
      setShare({ code: out.code })
    } catch (err: any) {
      toast.error(err.message || 'Save failed')
    } finally { setBusy(null) }
  }

  const remove = async () => {
    if (!current || !mine) return
    if (!confirm(`Delete ${current.name}? Rounds that opened under it keep their copy.`)) return
    setBusy('delete')
    try {
      const { signature, nonce } = await signFor('fn_delete', { name: current.name })
      const q = new URLSearchParams({ address, nonce: String(nonce) })
      if (signature) q.set('signature', signature)
      await send('DELETE', `${API}/functions/${current.name}?${q}`)
      toast.success(`Deleted ${current.name}`)
      setSelected(null); setShare(null)
      await refresh()
    } catch (err: any) {
      toast.error(err.message || 'Delete failed')
    } finally { setBusy(null) }
  }

  const loadShare = async (name: string) => {
    const out = await get(`${API}/functions/${name}/share`)
    if (out) setShare(out)
  }

  const publish = async () => {
    if (!current) return
    if (!address) return toast.error('Connect a wallet — the store records who published it')
    setBusy('publish')
    try {
      const token = await mintToken(walletClient, address)
      const out = await send('POST', `${API}/functions/${current.name}/publish`, undefined,
                             { Authorization: `Bearer ${token}` })
      toast.success(`Published — CID ${out.cid.slice(0, 12)}…`)
      setShare((s: any) => ({ ...(s || {}), cid: out.cid, url: out.url }))
      await refresh()
    } catch (err: any) {
      toast.error(err.message || 'Publish failed')
    } finally { setBusy(null) }
  }

  const doPreview = async () => {
    setBusy('preview')
    try {
      const out = await send('POST', `${API}/functions/import`, { source: importSrc.trim() })
      setPreview(out)
    } catch (err: any) {
      setPreview({ error: err.message })
    } finally { setBusy(null) }
  }

  const adoptPreview = () => {
    const p = preview?.preview
    if (!p) return
    setSelected(null)
    setDraft({ name: preview.name_taken ? `${p.name}_${me ? me.slice(2, 6) : 'copy'}` : p.name,
               description: p.description || '', expr: p.expr, params: paramsText(p.params) })
    setOrigin({ origin_cid: p.origin_cid || undefined, author: p.author || undefined })
    setTest({ fn: { name: p.name, expr: p.expr, params: p.params }, report: p.report, pot: null })
    setPreview(null)
    toast.info('Loaded into the editor — test it, then save it under your address')
  }

  const useFor = async (layer: 'pool' | 'predict', name: string) => {
    setBusy(`use-${layer}`)
    try {
      if (layer === 'predict') {
        await send('POST', `${API}/scoring?model=${encodeURIComponent(name)}`)
        toast.success(`Predictions now score with ${name}`)
      } else {
        const fields = { model: name }
        const { signature } = unclaimed ? { signature: '' } : await signFor('set_config', fields)
        const q = new URLSearchParams(fields)
        if (signature) { q.set('signature', signature); q.set('owner', address) }
        await send('POST', `${API}/pool/config?${q}`)
        toast.success(`The pool now scores with ${name} — from the next round`)
      }
      await refresh()
      onDone?.()
    } catch (err: any) {
      toast.error(err.message || 'Could not switch')
    } finally { setBusy(null) }
  }

  const copy = (text: string) => {
    navigator.clipboard?.writeText(text).then(() => toast.success('Copied'), () => toast.error('Copy failed'))
  }

  const defaults = fns.filter(f => f.builtin)
  const saved = fns.filter(f => !f.builtin)

  return (
    <div className="grid lg:grid-cols-[300px_1fr] gap-4 items-start">

      {/* ── Library ───────────────────────────────────────────── */}
      <div className="space-y-4">
        <Section title="Defaults" count={defaults.length}>
          {defaults.map(f => <FnRow key={f.name} f={f} active={active} me={me} selected={selected === f.name} onOpen={open} />)}
        </Section>
        <Section title="Library" count={saved.length} sub="saved here">
          {saved.length === 0
            ? <Empty msg="Nothing saved yet — write one on the right, or paste a share code." />
            : saved.map(f => <FnRow key={f.name} f={f} active={active} me={me} selected={selected === f.name} onOpen={open} />)}
        </Section>
        <div className="note text-[11px] leading-relaxed">
          The pool is scoring with <span className="mono t2">{active.pool || cfg?.model}</span>, predictions with{' '}
          <span className="mono t2">{active.predict}</span>. A round freezes its function the moment it opens —
          switching only touches the next one.
        </div>
      </div>

      {/* ── Editor ────────────────────────────────────────────── */}
      <div className="space-y-4">
        <Section title={selected ? <>Editing <span className="mono">{selected}</span></> : 'New function'}
                 action={
                   <div className="flex items-center gap-2">
                     {current && (
                       <>
                         {isOwner || unclaimed
                           ? <button className="btn btn-secondary btn-xs" disabled={busy != null || active.pool === current.name}
                                     onClick={() => useFor('pool', current.name)}>
                               {active.pool === current.name ? 'pool uses this' : 'use for the pool'}
                             </button>
                           : null}
                         <button className="btn btn-secondary btn-xs" disabled={busy != null || active.predict === current.name}
                                 onClick={() => useFor('predict', current.name)}>
                           {active.predict === current.name ? 'predictions use this' : 'use for predictions'}
                         </button>
                       </>
                     )}
                     <button className="btn btn-ghost btn-xs" onClick={() => setShowLang(s => !s)}>{showLang ? 'hide' : 'language'}</button>
                   </div>
                 }>
          <div className="p-[18px] space-y-4">
            {showLang && language && (
              <div className="note text-[11px] leading-relaxed space-y-1">
                <div><span className="t2">variable</span> <span className="mono">e</span> — {language.variables?.e}</div>
                <div><span className="t2">operators</span> <span className="mono">{language.operators?.join('  ')}</span></div>
                <div><span className="t2">functions</span> <span className="mono">{Object.values(language.functions || {}).join('  ·  ')}</span></div>
                {language.notes?.map((n: string) => <div key={n}>· {n}</div>)}
              </div>
            )}

            <div className="grid sm:grid-cols-2 gap-4">
              <div>
                <Label hint={nameTaken && nameTaken.name !== selected ? <span className="warn">taken{nameTaken.builtin ? ' (default)' : ''}</span> : 'a-z, 0-9, _'}>Name</Label>
                <input className="input mono" value={draft.name} placeholder="my_curve"
                       onChange={e => setDraft(d => ({ ...d, name: e.target.value }))} />
              </div>
              <div>
                <Label>Description</Label>
                <input className="input" value={draft.description} placeholder="what it rewards, in a sentence"
                       onChange={e => setDraft(d => ({ ...d, description: e.target.value }))} />
              </div>
            </div>

            <div>
              <Label hint={<>accuracy = <span className="mono">f(e)</span>, clamped to 0..1</>}>Expression</Label>
              <input className="input mono" value={draft.expr} placeholder="max(0, 1 - e/tol)" spellCheck={false}
                     onChange={e => setDraft(d => ({ ...d, expr: e.target.value }))} />
            </div>

            <div>
              <Label hint={<>JSON · <span className="mono">tol</span> is what the pool&apos;s tolerance sets</>}>Parameters</Label>
              <input className="input mono" value={draft.params} placeholder='{"tol": 1.0}' spellCheck={false}
                     onChange={e => setDraft(d => ({ ...d, params: e.target.value }))} />
            </div>

            {origin && (origin.author || origin.origin_cid) && (
              <div className="text-[11px] t3">
                Saving keeps the credit: author <span className="mono t2">{short(origin.author)}</span>
                {origin.origin_cid && <> · from <span className="mono t2">{origin.origin_cid.slice(0, 16)}…</span></>}
              </div>
            )}

            <div className="flex items-center gap-2 flex-wrap">
              <button className="btn btn-secondary btn-sm" disabled={busy != null || !draft.expr.trim()} onClick={runTest}>
                {busy === 'test' ? <Spinner /> : 'Test'}
              </button>
              <button className="btn btn-primary btn-sm" disabled={busy != null || !draft.expr.trim() || !draft.name.trim()} onClick={save}
                      title={address ? 'Signed with your wallet' : 'Connect a wallet'}>
                {busy === 'save' ? <Spinner /> : mine && draft.name === selected ? 'Save changes' : 'Save as mine'}
              </button>
              {mine && (
                <button className="btn btn-ghost btn-sm" disabled={busy != null} onClick={remove}>
                  {busy === 'delete' ? <Spinner /> : 'Delete'}
                </button>
              )}
              {current && (
                <button className="btn btn-ghost btn-sm" onClick={() => loadShare(current.name)}>Share</button>
              )}
              <button className="btn btn-ghost btn-sm" onClick={() => {
                setSelected(null); setOrigin(null); setTest(null); setShare(null)
                setDraft({ name: '', description: '', expr: 'max(0, 1 - e/tol)', params: '{"tol": 1.0}' })
              }}>New</button>
            </div>
          </div>
        </Section>

        {/* ── Test result ─────────────────────────────────────── */}
        {test && (
          <Section title="What it does" sub={test.fn ? <span className="mono">{test.fn.expr}</span> : undefined}>
            {test.error ? (
              <div className="p-[18px] text-sm down">{test.error}</div>
            ) : (
              <div className="p-[18px] space-y-4">
                <Curve sample={test.report?.sample} />
                <div className="flex flex-wrap gap-2 text-[11px]">
                  <Tag tone={test.report?.at_zero === 1 ? 'up' : 'warn'}>perfect call → {test.report?.at_zero}</Tag>
                  <Tag tone={test.report?.monotone ? 'neutral' : 'warn'}>{test.report?.monotone ? 'monotone' : 'not monotone'}</Tag>
                  <Tag tone="neutral">half at {test.report?.half_at == null ? 'never' : `${(test.report.half_at * 100).toFixed(1)}% miss`}</Tag>
                  <Tag tone="neutral">zero from {test.report?.zero_from == null ? 'never' : `${(test.report.zero_from * 100).toFixed(1)}% miss`}</Tag>
                </div>
                {test.report?.warnings?.length > 0 && (
                  <ul className="text-[11px] warn space-y-0.5">
                    {test.report.warnings.map((w: string) => <li key={w}>· {w}</li>)}
                  </ul>
                )}
                {test.pot && (
                  <div>
                    <div className="text-[11px] t3 mb-2">
                      A mock pot: five stakers at {usd(test.pot.stake, 0)} each, actual {usd(test.pot.actual, 0)}
                      {test.pot.fee_bps ? `, ${test.pot.fee_bps / 100}% fee` : ''} — {test.pot.mode === 'refund' ? 'everybody missed, all refunded' : 'split by dollars × accuracy'}
                    </div>
                    <div className="card-flat overflow-hidden">
                      <div className="thead grid-cols-[1fr_80px_90px_90px_90px]">
                        <span>Called</span><span className="text-right">Miss</span><span className="text-right">Accuracy</span>
                        <span className="text-right">Payout</span><span className="text-right">Net</span>
                      </div>
                      {test.pot.entries.map((e: any, i: number) => (
                        <div key={i} className="trow grid-cols-[1fr_80px_90px_90px_90px]">
                          <span className="mono text-xs">{usd(e.called)}</span>
                          <span className="text-right num text-xs t2">{e.miss_pct}%</span>
                          <span className="text-right num text-xs">{(e.accuracy * 100).toFixed(1)}%</span>
                          <span className="text-right num text-xs">{usd(e.payout)}</span>
                          <span className={`text-right num text-xs ${e.net >= 0 ? 'up' : 'down'}`}>{e.net >= 0 ? '+' : ''}{usd(e.net)}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </Section>
        )}

        {/* ── Share ───────────────────────────────────────────── */}
        {share && current && (
          <Section title={<>Share <span className="mono">{current.name}</span></>}
                   sub={current.author && current.author !== 'prefi' ? <>by <span className="mono">{short(current.author)}</span></> : undefined}>
            <div className="p-[18px] space-y-3">
              <div>
                <Label hint="works anywhere PreFi runs — paste it into Import">Share code</Label>
                <div className="flex gap-2">
                  <input className="input mono text-xs" readOnly value={share.code || ''} onFocus={e => e.currentTarget.select()} />
                  <button className="btn btn-secondary btn-sm" onClick={() => copy(share.code)}>Copy</button>
                </div>
              </div>
              <div>
                <Label hint="a public object in the fleet's store, under your address">Store CID</Label>
                {share.cid ? (
                  <div className="flex gap-2">
                    <input className="input mono text-xs" readOnly value={share.cid} onFocus={e => e.currentTarget.select()} />
                    <button className="btn btn-secondary btn-sm" onClick={() => copy(share.cid)}>Copy</button>
                  </div>
                ) : (
                  <button className="btn btn-secondary btn-sm" disabled={busy != null} onClick={publish}>
                    {busy === 'publish' ? <Spinner /> : 'Publish to the store'}
                  </button>
                )}
              </div>
            </div>
          </Section>
        )}

        {/* ── Import ──────────────────────────────────────────── */}
        <Section title="Import" sub="a share code or a store CID">
          <div className="p-[18px] space-y-3">
            <div className="flex gap-2">
              <input className="input mono text-xs" value={importSrc} placeholder="prefi.fn.…  or  Qm…" spellCheck={false}
                     onChange={e => setImportSrc(e.target.value)} />
              <button className="btn btn-secondary btn-sm" disabled={busy != null || !importSrc.trim()} onClick={doPreview}>
                {busy === 'preview' ? <Spinner /> : 'Preview'}
              </button>
            </div>
            {preview?.error && <div className="text-xs down">{preview.error}</div>}
            {preview?.preview && (
              <div className="card-flat p-3 space-y-2">
                <div className="flex items-center justify-between gap-2 flex-wrap">
                  <div className="flex items-center gap-2">
                    <span className="mono text-sm">{preview.preview.name}</span>
                    {preview.name_taken && <Tag tone="warn">name taken here</Tag>}
                    {preview.preview.author && <Tag tone="neutral">by {short(preview.preview.author)}</Tag>}
                  </div>
                  <Sparkline sample={preview.preview.sample} active />
                </div>
                <div className="mono text-xs t2">{preview.preview.expr}</div>
                <div className="mono text-[11px] t3">{paramsText(preview.preview.params)}</div>
                {preview.preview.description && <div className="text-xs t2">{preview.preview.description}</div>}
                {preview.preview.report?.warnings?.map((w: string) => <div key={w} className="text-[11px] warn">· {w}</div>)}
                <button className="btn btn-primary btn-xs" onClick={adoptPreview}>Load into the editor</button>
              </div>
            )}
          </div>
        </Section>
      </div>
    </div>
  )
}


function FnRow({ f, active, me, selected, onOpen }: { f: Fn; active: any; me: string; selected: boolean; onOpen: (f: Fn) => void }) {
  const own = !f.builtin && f.owner === me
  return (
    <button onClick={() => onOpen(f)}
            className={`trow grid-cols-[1fr_64px] w-full text-left ${selected ? 'mine' : ''}`}>
      <span className="min-w-0">
        <span className="flex items-center gap-1.5 flex-wrap">
          <span className="mono text-xs">{f.name}</span>
          {active.pool === f.name && <Tag tone="accent">pool</Tag>}
          {active.predict === f.name && <Tag tone="violet">predict</Tag>}
          {own && <Tag tone="up">yours</Tag>}
          {f.cid && <Tag tone="neutral" title={f.cid}>cid</Tag>}
        </span>
        <span className="block text-[11px] t3 truncate mt-0.5" title={f.expr}>{f.expr}</span>
      </span>
      <Sparkline sample={f.sample} active={selected} />
    </button>
  )
}
