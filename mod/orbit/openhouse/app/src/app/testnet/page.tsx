/* /testnet — try the protocol without anything being real.

   Three things, in the order an average visitor needs them:
     1. Walkthroughs  — pick a story, press Run, read what happened. Each one
                        plays in a throwaway store on the node (GET
                        /examples/<name>), so pressing Run changes nothing.
     2. Any bank      — how rent paid by ordinary bank transfer finds its
                        renter: a reference code, and the four kinds of bank
                        the node can read (GET /bank/kinds).
     3. For builders  — the same stories on a real chain (contracts/testnet.sh)
                        and the MCP endpoint an agent connects to. */

"use client";

import dynamic from 'next/dynamic'
import { useState } from 'react'
import { NextUp, PageHead, Shell } from '../../components/chrome'
import { Reveal } from '../../components/motion'
import { API_URL, BankKind, ExampleMeta, ExampleRun, api, useResource } from '../../lib/api'
import { LAUNCH } from '../../lib/whitepaper'

const LEVEL: Record<string, string> = { start: 'The basics', bank: 'Banks', civic: 'Cities' }

function Json({ value }: { value: any }) {
  return (
    <pre className="text-[11px] leading-relaxed text-white/70 bg-black/30 rounded-lg p-3 overflow-x-auto max-h-80 whitespace-pre-wrap break-all">
      {JSON.stringify(value, null, 2)}
    </pre>
  )
}

function Transcript({ run }: { run: ExampleRun }) {
  return (
    <div className="glass rounded-3xl p-6 md:p-8">
      <div className="flex flex-wrap items-baseline justify-between gap-3 mb-6">
        <h3 className="font-serif-ed text-2xl text-white">{run.title}</h3>
        <span className={`text-[11px] font-bold uppercase tracking-widest px-3 py-1 rounded-full border ${
          run.ok ? 'text-emerald-400 border-emerald-500/30' : 'text-pink border-pink/40'}`}>
          {run.ok ? 'Passed' : 'Failed'} · {run.summary}
        </span>
      </div>

      {run.checks.length > 0 && (
        <ul className="grid gap-2 md:grid-cols-2 mb-8">
          {run.checks.map((c, i) => (
            <li key={i} className="flex gap-2 text-sm text-white/80">
              <span className={c.ok ? 'text-emerald-400 font-bold' : 'text-pink font-bold'}>{c.ok ? '✓' : '×'}</span>
              <span>{c.claim}</span>
            </li>
          ))}
        </ul>
      )}
      {run.error && <p className="text-pink text-sm mb-6">{run.error}</p>}

      <ol className="space-y-3">
        {run.steps.map(s => (
          <li key={s.n} className="rounded-xl border border-white/[0.08] p-4">
            <div className="flex flex-wrap items-center gap-3">
              <span className="text-white/40 text-xs font-bold w-6">{s.n}</span>
              <span className="text-white text-sm flex-1 min-w-[12rem]">{s.note}</span>
              <code className="text-[10px] text-coral bg-coral/10 rounded px-2 py-0.5">{s.tool}</code>
              {s.error && <span className="text-[10px] font-bold uppercase tracking-widest text-pink">refused</span>}
            </div>
            <details className="mt-2 ml-9">
              <summary className="text-[11px] text-white/50 cursor-pointer hover:text-white/80">details</summary>
              <div className="grid gap-3 md:grid-cols-2 mt-2">
                <div><div className="text-[10px] uppercase tracking-widest text-white/40 mb-1">arguments</div><Json value={s.args} /></div>
                <div>
                  <div className="text-[10px] uppercase tracking-widest text-white/40 mb-1">{s.error ? 'refused with' : 'result'}</div>
                  {s.error ? <p className="text-sm text-pink">{s.error}</p> : <Json value={s.result} />}
                </div>
              </div>
            </details>
          </li>
        ))}
      </ol>
      <p className="text-[11px] text-white/45 mt-6 leading-relaxed">{run.store}. {run.replay}.</p>
    </div>
  )
}

function ReferenceLookup() {
  const [addr, setAddr] = useState('')
  const [code, setCode] = useState<string | null>(null)
  const [err, setErr] = useState('')
  const look = async () => {
    setErr(''); setCode(null)
    try { setCode((await api(`bank/reference/${encodeURIComponent(addr.trim())}`)).reference) }
    catch (e: any) { setErr(e?.message || 'lookup failed') }
  }
  return (
    <div className="flex flex-wrap gap-2 items-center">
      <input value={addr} onChange={e => setAddr(e.target.value)} placeholder="your 0x address"
        className="flex-1 min-w-[14rem] bg-white/[0.04] border border-white/12 rounded-xl px-4 py-3 text-sm text-white placeholder-white/35 focus:outline-none focus:border-coral/60" />
      <button onClick={look} disabled={!addr.trim()}
        className="px-5 py-3 rounded-xl bg-coral text-onaccent text-xs font-bold uppercase tracking-widest disabled:opacity-30">
        Get my code
      </button>
      {code && <div className="w-full text-sm text-white/80 mt-2">Put <code className="text-coral font-bold text-base">{code}</code> in your transfer memo.</div>}
      {err && <div className="w-full text-sm text-pink mt-2">{err}</div>}
    </div>
  )
}

function TestnetInner() {
  const { data: examples, error } = useResource<ExampleMeta[]>('examples', [])
  const { data: kinds } = useResource<BankKind[]>('bank/kinds', [])
  const [run, setRun] = useState<ExampleRun | null>(null)
  const [busy, setBusy] = useState('')

  const play = async (name: string) => {
    setBusy(name)
    try { setRun(await api(`examples/${name}`)) }
    catch (e: any) { setRun({ name, title: name, shows: '', level: '', ok: false, error: e?.message || 'failed', summary: '', store: '', replay: '', steps: [], checks: [] }) }
    setBusy('')
    setTimeout(() => document.getElementById('transcript')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50)
  }

  return (
    <Shell>
      <PageHead kicker={`Testnet · ${LAUNCH.chain}`} title="Try it. Nothing here is real.">
        Pick a story and press Run. It plays on the node in a throwaway sandbox — fake people,
        a fake bank, fake money — and shows you every step it took.
      </PageHead>

      <div className="max-w-6xl mx-auto px-5 md:px-8 space-y-20">
        {/* 1 — walkthroughs */}
        <section>
          {error && <p className="text-pink text-sm mb-4">The node is not answering right now ({error}).</p>}
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            {examples.map((e, i) => (
              <Reveal key={e.name} delay={i * 40}>
                <div className="glass glass-hover rounded-2xl p-5 h-full flex flex-col">
                  <div className="text-[10px] font-bold uppercase tracking-widest text-white/45 mb-2">{LEVEL[e.level] || e.level}</div>
                  <h3 className="font-display font-bold text-white text-base mb-2">{e.title}</h3>
                  <p className="text-sm text-white/65 leading-snug flex-1">{e.shows}</p>
                  <button onClick={() => play(e.name)} disabled={!!busy}
                    className="mt-4 w-full py-2.5 rounded-xl bg-gradient-to-r from-peach to-coral text-onaccent text-xs font-bold uppercase tracking-widest disabled:opacity-40">
                    {busy === e.name ? 'Running…' : 'Run'}
                  </button>
                </div>
              </Reveal>
            ))}
          </div>
          <div id="transcript" className="mt-8 scroll-mt-24">{run && <Transcript run={run} />}</div>
        </section>

        {/* 2 — any bank */}
        <section>
          <h2 className="headline text-4xl md:text-5xl text-white mb-3">Pay rent from any bank.</h2>
          <p className="font-serif-ed text-lg text-white/68 mb-8 max-w-3xl">
            No wallet needed. Every renter gets a short code. Send rent the way you already do —
            and the code tells the house whose money it is.
          </p>
          <div className="grid gap-4 md:grid-cols-3 mb-8">
            {[
              ['1', 'Get your code', 'It comes from your address, so it never changes.'],
              ['2', 'Send a normal transfer', 'Any bank, any rail — SEPA, ACH, Faster Payments, Zelle, wire. Put the code in the memo.'],
              ['3', 'It becomes equity', 'The owner’s node reads the bank, finds your code, and books it to you — once.'],
            ].map(([n, t, d]) => (
              <div key={n} className="glass rounded-2xl p-5">
                <div className="text-coral font-display font-black text-2xl mb-2">{n}</div>
                <div className="text-white font-bold mb-1">{t}</div>
                <p className="text-sm text-white/65 leading-snug">{d}</p>
              </div>
            ))}
          </div>
          <div className="glass rounded-2xl p-5 mb-8"><ReferenceLookup /></div>

          <details className="glass rounded-2xl p-5">
            <summary className="cursor-pointer text-white font-bold text-sm">How the owner’s node talks to banks ({kinds.length} kinds)</summary>
            <div className="grid gap-4 md:grid-cols-2 mt-5">
              {kinds.map(k => (
                <div key={k.kind} className="rounded-xl border border-white/[0.08] p-4">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-white font-bold text-sm">{k.label}</span>
                    <span className={`text-[10px] font-bold uppercase tracking-widest ${k.live ? 'text-coral' : 'text-emerald-400'}`}>{k.live ? 'real bank' : 'no API needed'}</span>
                  </div>
                  <p className="text-[13px] text-white/65 leading-snug">{k.doc}</p>
                  {k.fields.length > 0 && (
                    <p className="text-[11px] text-white/45 mt-2">needs: {k.fields.filter(f => f.required).map(f => f.name).join(', ') || 'nothing'}</p>
                  )}
                </div>
              ))}
            </div>
            <p className="text-[11px] text-white/45 mt-4 leading-relaxed">
              The sandbox is open to anyone. Every real bank connection needs the operator’s key, which lives only on the node’s own disk; credentials are stored there, never in the code.
            </p>
          </details>
        </section>

        {/* 3 — builders */}
        <section>
          <h2 className="headline text-4xl md:text-5xl text-white mb-8">For builders.</h2>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="glass rounded-2xl p-6">
              <div className="text-white font-bold mb-2">On a real chain</div>
              <p className="text-sm text-white/65 mb-3">Deploys the contracts and pays a month of rent — locally with zero setup, or on {LAUNCH.chain} with your own test keys.</p>
              <pre className="text-[11px] text-white/75 bg-black/30 rounded-lg p-3 overflow-x-auto">{`cd contracts && ./testnet.sh

RPC_URL=https://sepolia.base.org \\
OWNER_KEY=0x… BANK_KEY=0x… RENTER_KEY=0x… \\
./testnet.sh`}</pre>
            </div>
            <div className="glass rounded-2xl p-6">
              <div className="text-white font-bold mb-2">Drive it from an agent (MCP)</div>
              <p className="text-sm text-white/65 mb-3">Every walkthrough step above is a tool on this endpoint. Start with <code className="text-coral">openhouse_examples</code>.</p>
              <pre className="text-[11px] text-white/75 bg-black/30 rounded-lg p-3 overflow-x-auto">{`POST ${API_URL}/mcp
{"jsonrpc":"2.0","id":1,"method":"tools/call",
 "params":{"name":"openhouse_run_example",
           "arguments":{"name":"bank_transfer"}}}`}</pre>
            </div>
          </div>
        </section>
      </div>

      <NextUp here="/testnet" />
    </Shell>
  )
}

export default dynamic(() => Promise.resolve(TestnetInner), { ssr: false })
