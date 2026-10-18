/* /proofs — proof of rent paid, without the tenant.

   What a visitor needs, in order:
     1. The idea      — three cards: the bank sees, the chain sees, you hold the key.
     2. The rail      — the statement's hash, this node's bank keys, where the two
                        verifiers live (NEAR testnet account, Ethereum address).
     3. The receipts  — every receipt this node issued; pick one to read it,
                        verify it, open it.
     3b. Quantum      — which parts a quantum computer breaks and which it cannot;
                        the bank's post-quantum keys, leaves left, countersign
                        old receipts.
     4. Try it        — issue a testnet receipt, paste any receipt to verify it,
                        open a commitment with a tenant + salt.
     5. For builders  — the three source files and the MCP tools. */

"use client";

import dynamic from 'next/dynamic'
import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { NextUp, PageHead, Shell } from '../../components/chrome'
import { Reveal } from '../../components/motion'
import { ProofPQ, ProofReceipt, ProofStatus, ProofVerdict, api, timeAgo, useResource } from '../../lib/api'
import { LAUNCH } from '../../lib/whitepaper'

const short = (h?: string, n = 10) => (h ? `${h.slice(0, n)}…${h.slice(-6)}` : '—')
const period = (p?: number) => (p ? `${String(p).slice(0, 4)}-${String(p).slice(4)}` : '—')
const units = (u?: number) => (u == null ? '—' : `${(u / 1e6).toLocaleString(undefined, { maximumFractionDigits: 6 })} Ξ`)
const thisPeriod = () => { const d = new Date(); return d.getUTCFullYear() * 100 + d.getUTCMonth() + 1 }

const INPUT = 'bg-white/[0.04] border border-white/12 rounded-xl px-4 py-3 text-sm text-white placeholder-white/35 focus:outline-none focus:border-coral/60 w-full'
const BTN = 'px-5 py-3 rounded-xl bg-coral text-onaccent text-xs font-bold uppercase tracking-widest disabled:opacity-30'
const GHOST = 'px-4 py-2 rounded-xl border border-white/15 text-white/80 text-[11px] font-bold uppercase tracking-widest hover:border-coral/50 hover:text-white disabled:opacity-30'

function Mono({ value, full = false }: { value?: string; full?: boolean }) {
  const [copied, setCopied] = useState(false)
  if (!value) return <span className="text-white/40">—</span>
  return (
    <button type="button" title={value} className="font-mono text-[11px] text-white/80 hover:text-coral break-all text-left"
      onClick={() => { navigator.clipboard?.writeText(value); setCopied(true); setTimeout(() => setCopied(false), 900) }}>
      {full ? value : short(value)}{copied && <span className="ml-2 text-coral not-italic">copied</span>}
    </button>
  )
}

function Checks({ v }: { v: ProofVerdict }) {
  return (
    <div>
      <div className={`text-[11px] font-bold uppercase tracking-widest mb-3 ${v.ok ? 'text-emerald-400' : 'text-pink'}`}>
        {v.ok ? 'Verified' : 'Not verified'}{v.known_here === false && ' · issued elsewhere'}
        {v.policy && <span className="text-white/45"> · policy {v.policy}</span>}
      </div>
      {v.quantum && <p className={`text-sm mb-3 font-medium ${v.quantum.safe ? 'text-emerald-400' : 'text-white/60'}`}>{v.quantum.says}</p>}
      <ul className="grid gap-1.5 md:grid-cols-2">
        {v.checks.map((c, i) => (
          <li key={i} className={`flex gap-2 text-sm ${c.ignored ? 'text-white/35 line-through' : 'text-white/80'}`} title={c.ignored || ''}>
            <span className={c.ignored ? 'text-white/35' : c.ok ? 'text-emerald-400 font-bold' : c.ok === null ? 'text-white/40' : 'text-pink font-bold'}>{c.ok ? '✓' : c.ok === null ? '·' : '×'}</span>
            <span className="min-w-0 [overflow-wrap:anywhere]">{c.claim}{c.pq && <span className="ml-1.5 text-[9px] font-bold uppercase tracking-widest text-emerald-400 no-underline">pq</span>}</span>
          </li>
        ))}
      </ul>
      {v.error && <p className="text-pink text-sm mt-3">{v.error}</p>}
    </div>
  )
}

function ChainBadge({ label, info, tx }: { label: string; info?: { configured?: boolean; paid?: boolean; error?: string }; tx?: { ok?: boolean; explorer?: string; hash?: string } }) {
  const state = tx?.ok || info?.paid ? 'on-chain' : info?.configured === false ? 'no verifier' : info?.error ? 'unreachable' : 'not yet'
  const cls = state === 'on-chain' ? 'text-emerald-400 border-emerald-500/30' : state === 'no verifier' ? 'text-white/40 border-white/10' : 'text-white/60 border-white/15'
  const inner = <span className={`text-[10px] font-bold uppercase tracking-widest px-2 py-0.5 rounded-full border ${cls}`}>{label} · {state}</span>
  return tx?.explorer ? <a href={tx.explorer} target="_blank" rel="noreferrer" className="hover:opacity-80">{inner}</a> : inner
}

function QuantumBadge({ r }: { r: ProofReceipt }) {
  const safe = !!r.sig?.pq?.ohx || !!r.sig?.pq?.mldsa65
  return (
    <span title={safe ? 'carries a hash-based (OHX-KECCAK) and lattice (ML-DSA-65) signature a quantum computer cannot forge' : 'ed25519 + ECDSA only — forgeable by a large quantum computer'}
      className={`text-[10px] font-bold uppercase tracking-widest px-2 py-0.5 rounded-full border ${safe ? 'text-emerald-400 border-emerald-500/30' : 'text-amber-300 border-amber-400/30'}`}>
      {safe ? 'quantum-safe' : 'classical only'}
    </span>
  )
}

function ReceiptCard({ r, onVerify, verdict, onSubmit, onUpgrade, busy }: {
  r: ProofReceipt; onVerify: (policy?: 'pq') => void; verdict: ProofVerdict | null
  onSubmit: (chain: 'near' | 'eth') => void; onUpgrade: () => void; busy: string
}) {
  const [raw, setRaw] = useState(false)
  const hasPQ = !!r.sig?.pq?.ohx
  return (
    <div className="glass rounded-3xl p-6 md:p-8">
      <div className="flex flex-wrap items-baseline justify-between gap-3 mb-5">
        <h3 className="font-serif-ed text-2xl text-white">Property {r.property_id} · {period(r.period)}</h3>
        <div className="flex gap-2 flex-wrap">
          <QuantumBadge r={r} />
          <ChainBadge label="NEAR" info={r.chains?.near} tx={r.chain?.near} />
          <ChainBadge label="Ethereum" info={r.chains?.eth} tx={r.chain?.eth} />
        </div>
      </div>
      <dl className="grid gap-x-6 gap-y-3 md:grid-cols-2 text-sm">
        {([
          ['Rent due (attested)', units(r.rent_due)],
          ['Statement', `v${r.version} · ${r.kind}`],
          ['Commitment', <Mono key="c" value={r.commitment} />],
          ['Nullifier', <Mono key="n" value={r.nullifier} />],
          ['Fact', <Mono key="f" value={r.fact} />],
          ['Program hash', <Mono key="p" value={r.program_hash} />],
          ['Bank (NEAR key)', <Mono key="bn" value={r.bank?.near} />],
          ['Bank (Ethereum)', <Mono key="be" value={r.bank?.eth} />],
          ['Bank (post-quantum)', r.bank?.pq ? <Mono key="bq" value={r.bank.pq.ohx.id} /> : <span key="bq" className="text-white/50">none — countersign it below</span>],
          ['PQ signature', r.sig?.pq?.ohx ? `OHX-KECCAK leaf ${parseInt(r.sig.pq.ohx.slice(2, 10), 16)}${r.sig.pq.mldsa65 ? ' + ML-DSA-65' : ''}${r.pq_upgraded ? ' · countersigned later' : ''}` : '—'],
          ['Issued', `${timeAgo(r.issued)}${r.source?.txn ? ` · from bank txn ${r.source.txn}` : ''}`],
          ['Who paid', <span key="w" className="text-white/50">not in the receipt — only the tenant can open the commitment</span>],
        ] as [string, any][]).map(([k, v]) => (
          <div key={k}><dt className="text-[10px] uppercase tracking-widest text-white/40 mb-0.5">{k}</dt><dd className="text-white/85">{v}</dd></div>
        ))}
      </dl>
      <div className="flex flex-wrap gap-2 mt-6">
        <button className={BTN} onClick={() => onVerify()} disabled={!!busy}>{busy === 'verify' ? 'Checking…' : 'Verify offline'}</button>
        {hasPQ
          ? <button className={GHOST} onClick={() => onVerify('pq')} disabled={!!busy} title="ignore ed25519/ECDSA, as you would after Q-day">Verify as after Q-day</button>
          : <button className={GHOST} onClick={onUpgrade} disabled={!!busy}>{busy === 'upgrade' ? 'Signing…' : 'Make quantum-safe'}</button>}
        <button className={GHOST} onClick={() => onSubmit('near')} disabled={!!busy || !!r.chain?.near?.ok}>{busy === 'near' ? 'Sending…' : r.chain?.near?.ok ? 'On NEAR' : 'Submit to NEAR'}</button>
        <button className={GHOST} onClick={() => onSubmit('eth')} disabled={!!busy || !!r.chain?.eth?.ok}>{busy === 'eth' ? 'Sending…' : r.chain?.eth?.ok ? 'On Ethereum' : 'Submit to Ethereum'}</button>
        <button className={GHOST} onClick={() => setRaw(!raw)}>{raw ? 'Hide JSON' : 'Receipt JSON'}</button>
      </div>
      {verdict && <div className="mt-6 pt-6 border-t border-white/[0.08]"><Checks v={verdict} /></div>}
      {raw && <pre className="mt-5 text-[11px] leading-relaxed text-white/70 bg-black/30 rounded-lg p-3 overflow-x-auto max-h-96 whitespace-pre-wrap break-all">{JSON.stringify(r, null, 2)}</pre>}
    </div>
  )
}

function QuantumSection({ pq, onUpgradeAll, busy }: { pq: ProofPQ | null; onUpgradeAll: () => void; busy: string }) {
  if (!pq) return null
  const used = pq.leaves.used / pq.leaves.capacity
  const old = pq.receipts.classical_only.length
  return (
    <section>
      <h2 className="headline text-4xl md:text-5xl text-white mb-3">Ready for the quantum computer.</h2>
      <p className="font-serif-ed text-lg text-white/68 mb-8 max-w-3xl">
        ed25519 and ECDSA fall to Shor&apos;s algorithm: whoever runs a large enough quantum computer could forge a bank&apos;s
        signature and &ldquo;prove&rdquo; rent nobody paid. Hashes don&apos;t fall that way. So every receipt also carries a
        signature built from keccak alone — which Ethereum and NEAR can already check — and a NIST lattice signature.
      </p>
      <div className="grid gap-4 md:grid-cols-3 mb-4">
        <div className="glass rounded-2xl p-5">
          <div className="text-[10px] uppercase tracking-widest text-white/40 mb-2">On-chain, today</div>
          <div className="text-white font-bold text-sm mb-1">{pq.schemes.ohx?.name} · hash-based</div>
          <p className="text-[12px] text-white/55 leading-snug mb-2">WOTS+ one-time keys under a Merkle hypertree. {pq.schemes.ohx?.sig_bytes?.toLocaleString()} bytes, checked by Solidity and the NEAR contract.</p>
          <div className="text-[11px] text-white/50">key id</div><Mono value={pq.keys.ohx.id} />
        </div>
        <div className="glass rounded-2xl p-5">
          <div className="text-[10px] uppercase tracking-widest text-white/40 mb-2">Off-chain, standard</div>
          <div className="text-white font-bold text-sm mb-1">ML-DSA-65 · NIST FIPS 204</div>
          <p className="text-[12px] text-white/55 leading-snug mb-2">Lattice-based and stateless. Too heavy for a chain today; anyone verifies it offline.</p>
          {pq.keys.mldsa65 ? <><div className="text-[11px] text-white/50">fingerprint</div><Mono value={pq.keys.mldsa65.fingerprint} /></>
            : <p className="text-[12px] text-amber-300">not available on this node</p>}
        </div>
        <div className="glass rounded-2xl p-5">
          <div className="text-[10px] uppercase tracking-widest text-white/40 mb-2">One-time signatures left</div>
          <div className="text-white font-display font-black text-3xl">{pq.leaves.left.toLocaleString()}</div>
          <div className="h-1.5 rounded-full bg-white/10 mt-2 overflow-hidden"><div className="h-full bg-emerald-400" style={{ width: `${Math.max(used * 100, 0.5)}%` }} /></div>
          <p className="text-[12px] text-white/55 mt-2">of {pq.leaves.capacity.toLocaleString()} · each signs once, then the key rotates{pq.leaves.retired_keys ? ` · ${pq.leaves.retired_keys} retired` : ''}</p>
          {pq.leaves.warning && <p className="text-[12px] text-amber-300 mt-1">{pq.leaves.warning}</p>}
        </div>
      </div>
      <div className="glass rounded-2xl p-5 md:p-6">
        <div className="flex flex-wrap items-baseline justify-between gap-3 mb-4">
          <div className="text-white font-bold">What a quantum computer breaks here</div>
          <div className="text-[12px] text-white/55">{pq.receipts.pq} of {pq.receipts.total} receipts quantum-safe</div>
        </div>
        <ul className="divide-y divide-white/[0.06]">
          {pq.threats.map(t => (
            <li key={t.part} className="py-2.5 grid gap-1 md:grid-cols-[16rem_7rem_1fr] md:items-baseline">
              <span className="text-sm text-white">{t.part}</span>
              <span className={`text-[10px] font-bold uppercase tracking-widest ${t.quantum === 'safe' ? 'text-emerald-400' : 'text-amber-300'}`}>{t.quantum}</span>
              <span className="text-[12px] text-white/55">{t.why}</span>
            </li>
          ))}
        </ul>
        <div className="flex flex-wrap items-center gap-3 mt-5">
          {old > 0
            ? <button className={BTN} onClick={onUpgradeAll} disabled={!!busy}>{busy === 'upgrade' ? 'Signing…' : `Make ${old} older receipt${old === 1 ? '' : 's'} quantum-safe`}</button>
            : <span className="text-[12px] text-emerald-300">Every receipt here carries a post-quantum signature.</span>}
          <span className="text-[12px] text-white/45">The switch: <code className="text-white/70">requirePQ()</code> on Ethereum, <code className="text-white/70">require_pq</code> on NEAR — shuts the classical door for good.</span>
        </div>
      </div>
    </section>
  )
}

function ProofsInner() {
  const { data: status, error, reload: reloadStatus } = useResource<ProofStatus | null>('proofs/status', null)
  const { data: receipts, reload } = useResource<ProofReceipt[]>('proofs', [])
  const { data: pq, reload: reloadPQ } = useResource<ProofPQ | null>('proofs/pq', null)
  const [picked, setPicked] = useState<string | null>(null)
  const [verdicts, setVerdicts] = useState<Record<string, ProofVerdict>>({})
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  // issue
  const [tenant, setTenant] = useState('')
  const [amount, setAmount] = useState('')
  const [per, setPer] = useState(String(thisPeriod()))
  // verify foreign
  const [paste, setPaste] = useState('')
  const [pasteVerdict, setPasteVerdict] = useState<ProofVerdict | null>(null)
  // open
  const [who, setWho] = useState('')
  const [salt, setSalt] = useState('')
  const [opened, setOpened] = useState<any>(null)

  useEffect(() => { if (status?.rent_due && !amount) setAmount(String(status.rent_due)) }, [status, amount])
  const current = useMemo(() => receipts.find(r => r.nullifier === picked) || receipts[0] || null, [receipts, picked])

  const say = (ok: boolean, text: string) => { setMsg({ ok, text }); setTimeout(() => setMsg(null), 6000) }

  const issue = async () => {
    setBusy('issue')
    try {
      const r: ProofReceipt = await api('proofs/issue', { method: 'POST', body: { tenant: tenant.trim(), amount: Number(amount), period: Number(per) } })
      await reload(); await reloadStatus(); await reloadPQ(); setPicked(r.nullifier)
      say(true, `Receipt issued for ${period(r.period)} — nothing about ${tenant.trim().slice(0, 8)}… is in it.`)
      document.getElementById('receipt')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    } catch (e: any) { say(false, e?.message || 'refused') }
    setBusy('')
  }
  const verify = async (r: ProofReceipt, policy: string = 'auto') => {
    setBusy('verify')
    try { setVerdicts(v => ({ ...v, [r.nullifier]: null as any })); const v = await api('proofs/verify', { method: 'POST', body: { nullifier: r.nullifier, policy } }); setVerdicts(x => ({ ...x, [r.nullifier]: v })) }
    catch (e: any) { say(false, e?.message || 'failed') }
    setBusy('')
  }
  const upgrade = async (nullifier = '') => {
    setBusy('upgrade')
    try {
      const res = await api('proofs/pq/upgrade', { method: 'POST', body: { nullifier } })
      await reload(); await reloadPQ(); await reloadStatus()
      say(true, res.upgraded.length ? `${res.upgraded.length} receipt${res.upgraded.length === 1 ? '' : 's'} now carry a post-quantum signature. The facts did not change.` : 'Nothing to countersign.')
    } catch (e: any) { say(false, e?.message || 'refused') }
    setBusy('')
  }
  const submit = async (r: ProofReceipt, chain: 'near' | 'eth') => {
    setBusy(chain)
    try {
      const res = await api('proofs/submit', { method: 'POST', body: { nullifier: r.nullifier, chain } })
      await reload(); await reloadStatus()
      say(true, `${chain === 'near' ? 'NEAR' : 'Ethereum'} accepted it${res.hash ? ` — ${short(res.hash)}` : ''}`)
    } catch (e: any) { say(false, e?.message || 'refused') }
    setBusy('')
  }
  const verifyPaste = async () => {
    setBusy('paste'); setPasteVerdict(null)
    try { setPasteVerdict(await api('proofs/verify', { method: 'POST', body: { receipt: JSON.parse(paste) } })) }
    catch (e: any) { setPasteVerdict({ ok: false, checks: [], error: e?.message || 'not a receipt' }) }
    setBusy('')
  }
  const getSalt = async () => {
    setBusy('salt')
    try { setSalt((await api('proofs/salt', { method: 'POST', body: { tenant: who.trim() } })).salt) }
    catch (e: any) { say(false, e?.message || 'refused') }
    setBusy('')
  }
  const open = async () => {
    if (!current) return
    setBusy('open'); setOpened(null)
    try { setOpened(await api('proofs/open', { method: 'POST', body: { tenant: who.trim(), salt: salt.trim(), nullifier: current.nullifier } })) }
    catch (e: any) { setOpened({ error: e?.message || 'failed' }) }
    setBusy('')
  }

  const near = status?.chains.near, eth = status?.chains.eth

  return (
    <Shell>
      <PageHead kicker={`Proofs · ${LAUNCH.chain} + NEAR testnet`} title="Prove the rent was paid. Say nothing else.">
        The bank sees who paid. The chain only needs to know that this home, this month, was paid.
        A small Cairo program turns one into the other — and the tenant keeps the only key that connects them.
      </PageHead>

      <div className="max-w-6xl mx-auto px-5 md:px-8 space-y-20">
        {/* 1 — the idea */}
        <section className="grid gap-4 md:grid-cols-3">
          {[
            ['The bank sees everything', 'Who paid, from which account, how much, when. It runs the statement over those facts, in private, and signs the result.'],
            ['The chain sees a receipt', 'Six numbers: version, property, month, rent due, a commitment, a nullifier. The verifier checks the signature, pins the program, burns the nullifier.'],
            ['You hold the key', 'The commitment is a hash of you and a salt. Show the salt to a landlord or a court and it opens — to you, and to no one else.'],
          ].map(([t, d], i) => (
            <Reveal key={t} delay={i * 60}>
              <div className="glass rounded-2xl p-6 h-full">
                <div className="text-coral font-display font-black text-2xl mb-2">{i + 1}</div>
                <div className="text-white font-bold mb-1">{t}</div>
                <p className="text-sm text-white/65 leading-snug">{d}</p>
              </div>
            </Reveal>
          ))}
        </section>

        {/* 2 — the rail */}
        <section>
          <h2 className="headline text-4xl md:text-5xl text-white mb-3">One statement, two chains.</h2>
          <p className="font-serif-ed text-lg text-white/68 mb-8 max-w-3xl">
            Every receipt is about one Cairo program, pinned by its hash. A NEAR contract compiled to WebAssembly
            and an Ethereum contract both hold that hash, this node&apos;s bank keys, and the property&apos;s rent due.
          </p>
          {error && <p className="text-pink text-sm mb-4">The node is not answering right now ({error}).</p>}
          {status && (
            <div className="grid gap-4 md:grid-cols-3">
              <div className="glass rounded-2xl p-5">
                <div className="text-[10px] uppercase tracking-widest text-white/40 mb-2">The statement</div>
                <div className="text-white font-bold text-sm mb-1">proof/src/lib.cairo · v{status.program.version}</div>
                <div className="mb-2"><Mono value={status.program.program_hash || undefined} full /></div>
                <p className="text-[12px] text-white/55 leading-snug">outputs: {status.program.public_outputs.join(' · ')}</p>
                <p className="text-[12px] text-white/55 leading-snug mt-1">private: {status.program.private_inputs.join(' · ')}</p>
                {!status.program.built && <p className="text-pink text-[12px] mt-2">not built on this node{status.program.scarb ? '' : ' — scarb missing'}</p>}
              </div>
              <div className="glass rounded-2xl p-5">
                <div className="text-[10px] uppercase tracking-widest text-white/40 mb-2">This node as the bank</div>
                <div className="text-white font-bold text-sm mb-1">{status.issuer?.name || '—'}</div>
                <div className="text-[11px] text-white/50">ed25519 (NEAR)</div><Mono value={status.issuer?.near} full />
                <div className="text-[11px] text-white/50 mt-2">secp256k1 (Ethereum)</div><Mono value={status.issuer?.eth} full />
                <p className="text-[12px] text-white/55 mt-3">property {status.property_id} · rent due {units(status.rent_due)} · {status.receipts} receipt{status.receipts === 1 ? '' : 's'}</p>
              </div>
              <div className="glass rounded-2xl p-5">
                <div className="text-[10px] uppercase tracking-widest text-white/40 mb-2">The verifiers</div>
                <div className="text-white font-bold text-sm">NEAR <span className="text-white/40 font-normal">· {near?.network} · WASM</span></div>
                {near?.account
                  ? <a className="font-mono text-[11px] text-coral break-all" href={`${near.explorer}/address/${near.account}`} target="_blank" rel="noreferrer">{near.account}</a>
                  : <div className="text-[12px] text-white/50">not deployed yet</div>}
                <div className="text-[12px] text-white/55 mb-3">{status.submitted.near} receipt{status.submitted.near === 1 ? '' : 's'} on-chain</div>
                <div className="text-white font-bold text-sm">Ethereum <span className="text-white/40 font-normal">· {LAUNCH.chain} · Solidity</span></div>
                {eth?.address
                  ? <a className="font-mono text-[11px] text-coral break-all" href={`${eth.explorer}/address/${eth.address}`} target="_blank" rel="noreferrer">{eth.address}</a>
                  : <div className="text-[12px] text-white/50">not deployed on {LAUNCH.chain} yet — runs on anvil in the test suite</div>}
                <div className="text-[12px] text-white/55">{status.submitted.eth} receipt{status.submitted.eth === 1 ? '' : 's'} on-chain</div>
              </div>
            </div>
          )}
        </section>

        {/* 3b — quantum */}
        <QuantumSection pq={pq} busy={busy} onUpgradeAll={() => upgrade()} />

        {/* 3 — receipts */}
        <section id="receipt" className="scroll-mt-24">
          <h2 className="headline text-4xl md:text-5xl text-white mb-3">The receipts.</h2>
          <p className="font-serif-ed text-lg text-white/68 mb-8 max-w-3xl">
            Everything this node has attested. Pick one — read it, verify it offline, relay it to a chain.
            Notice what is not there.
          </p>
          {msg && <div className={`rounded-xl px-4 py-3 text-sm mb-4 border ${msg.ok ? 'text-emerald-300 border-emerald-500/30' : 'text-pink border-pink/40'}`}>{msg.text}</div>}
          {receipts.length === 0 ? (
            <div className="glass rounded-2xl p-6 text-sm text-white/60">No receipts yet. Issue one below — it&apos;s testnet.</div>
          ) : (
            <div className="grid gap-6 lg:grid-cols-[18rem_1fr]">
              <ul className="space-y-2 lg:max-h-[36rem] lg:overflow-y-auto">
                {receipts.map(r => (
                  <li key={r.nullifier}>
                    <button onClick={() => setPicked(r.nullifier)}
                      className={`w-full text-left rounded-xl border p-3 transition-colors ${current?.nullifier === r.nullifier ? 'border-coral/60 bg-coral/10' : 'border-white/[0.08] hover:border-white/20'}`}>
                      <div className="flex justify-between text-sm text-white"><span>#{r.property_id} · {period(r.period)}</span><span className="text-white/50">{units(r.rent_due)}</span></div>
                      <div className="font-mono text-[10px] text-white/45 mt-1">{short(r.nullifier, 8)}</div>
                      <div className="flex gap-1.5 mt-2 flex-wrap">
                        {r.chain?.near?.ok && <span className="text-[9px] font-bold uppercase tracking-widest text-emerald-400">near</span>}
                        {r.chain?.eth?.ok && <span className="text-[9px] font-bold uppercase tracking-widest text-emerald-400">eth</span>}
                        {r.sig?.pq?.ohx && <span className="text-[9px] font-bold uppercase tracking-widest text-emerald-400">pq</span>}
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
              {current && <ReceiptCard r={current} verdict={verdicts[current.nullifier] ?? null} busy={busy}
                onVerify={p => verify(current, p || 'auto')} onSubmit={c => submit(current, c)}
                onUpgrade={() => upgrade(current.nullifier)} />}
            </div>
          )}
        </section>

        {/* 4 — try it */}
        <section>
          <h2 className="headline text-4xl md:text-5xl text-white mb-8">Try it. Nothing here is real.</h2>
          <div className="grid gap-4 md:grid-cols-3">
            <div className="glass rounded-2xl p-5 flex flex-col gap-3">
              <div className="text-white font-bold">Be the bank</div>
              <p className="text-[13px] text-white/60 leading-snug">Issue a receipt for a payment. The tenant goes in; only a commitment comes out. Amounts are in units (Ξ × 10⁶).</p>
              <input className={INPUT} value={tenant} onChange={e => setTenant(e.target.value)} placeholder="tenant — a 0x address, a NEAR account, anything stable" />
              <div className="grid grid-cols-2 gap-2">
                <input className={INPUT} value={amount} onChange={e => setAmount(e.target.value)} placeholder="amount paid, units" inputMode="numeric" />
                <input className={INPUT} value={per} onChange={e => setPer(e.target.value)} placeholder="period YYYYMM" inputMode="numeric" />
              </div>
              <button className={BTN} onClick={issue} disabled={!!busy || !tenant.trim() || !amount}>{busy === 'issue' ? 'Proving…' : 'Issue receipt'}</button>
              <p className="text-[11px] text-white/45">Rent due is {units(status?.rent_due)}. Pay less and the program itself refuses.</p>
            </div>
            <div className="glass rounded-2xl p-5 flex flex-col gap-3">
              <div className="text-white font-bold">Be anyone</div>
              <p className="text-[13px] text-white/60 leading-snug">Paste a receipt from any node. The fact is recomputed and every bank signature checked — classical and post-quantum — offline.</p>
              <textarea className={`${INPUT} min-h-[9rem] font-mono text-[11px]`} value={paste} onChange={e => setPaste(e.target.value)} placeholder='{"kind":"attested","outputs":[…],"sig":{…}}' />
              <button className={BTN} onClick={verifyPaste} disabled={!!busy || !paste.trim()}>{busy === 'paste' ? 'Checking…' : 'Verify'}</button>
              {pasteVerdict && <Checks v={pasteVerdict} />}
            </div>
            <div className="glass rounded-2xl p-5 flex flex-col gap-3">
              <div className="text-white font-bold">Be the tenant</div>
              <p className="text-[13px] text-white/60 leading-snug">Open the selected receipt&apos;s commitment with your identity and salt. It matches you or it doesn&apos;t — that is the whole disclosure.</p>
              <input className={INPUT} value={who} onChange={e => setWho(e.target.value)} placeholder="tenant" />
              <div className="flex gap-2">
                <input className={INPUT} value={salt} onChange={e => setSalt(e.target.value)} placeholder="salt (0x…)" />
                <button className={GHOST} onClick={getSalt} disabled={!!busy || !who.trim()} title="testnet: the bank hands it out on request">{busy === 'salt' ? '…' : 'Get salt'}</button>
              </div>
              <button className={BTN} onClick={open} disabled={!!busy || !who.trim() || !salt.trim() || !current}>{busy === 'open' ? 'Opening…' : `Open ${current ? period(current.period) : ''}`}</button>
              {opened && (
                opened.error ? <p className="text-pink text-sm">{opened.error}</p>
                : <p className={`text-sm ${opened.matches ? 'text-emerald-300' : 'text-pink'}`}>{opened.says}</p>
              )}
            </div>
          </div>
        </section>

        {/* 5 — builders */}
        <section>
          <h2 className="headline text-4xl md:text-5xl text-white mb-8">For builders.</h2>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="glass rounded-2xl p-6">
              <div className="text-white font-bold mb-2">Three files, one fact</div>
              <ul className="text-sm text-white/70 space-y-2">
                <li><code className="text-coral">proof/src/lib.cairo</code> — the statement; run with <code>scarb cairo-run</code>, test with <code>scarb cairo-test</code>.</li>
                <li><code className="text-coral">near/src/lib.rs</code> — the NEAR verifier (near-sdk 5, <code>env::ed25519_verify</code>); <code>cargo test</code>, <code>cargo build --target wasm32-unknown-unknown</code>.</li>
                <li><code className="text-coral">contracts/OpenHouseProofs.sol</code> — the Ethereum verifier (<code>ecrecover</code> + an <code>IFactRegistry</code> door for SHARP + <code>submitPQ</code>); <code>forge test</code>.</li>
                <li><code className="text-coral">pq.py</code> + <code className="text-coral">contracts/OHX.sol</code> — the post-quantum signature, keccak only; one vector (<code>tests/vectors/ohx.json</code>) pinned in Python, Solidity and Rust.</li>
              </ul>
              <p className="text-[12px] text-white/50 mt-3">fact = keccak256(program_hash ‖ keccak256(outputs as 32-byte words)). One vector is pinned in all three test suites, so they cannot drift.</p>
              <Link href="/code" className="inline-block mt-4 text-[11px] font-bold uppercase tracking-widest text-coral hover:underline">Read the source →</Link>
            </div>
            <div className="glass rounded-2xl p-6">
              <div className="text-white font-bold mb-2">Over MCP</div>
              <pre className="text-[11px] text-white/75 bg-black/30 rounded-lg p-3 overflow-x-auto">{`openhouse_proof_status     openhouse_proof_program
openhouse_proof_issue      openhouse_proofs · openhouse_proof
openhouse_proof_verify     openhouse_proof_open · openhouse_proof_salt
openhouse_proof_submit     chain = near | eth · door = classical | pq
openhouse_proof_pq         keys · leaves left · threat model
openhouse_proof_pq_upgrade countersign old receipts
openhouse_bank_reconcile   issue_proofs = true`}</pre>
              <p className="text-[12px] text-white/50 mt-3">Issuing is open on testnet and needs the operator bank key anywhere else. Receipts carry no tenant data, so reading them is always public.</p>
              <Link href="/testnet" className="inline-block mt-4 text-[11px] font-bold uppercase tracking-widest text-coral hover:underline">Run the “Prove the rent was paid” walkthrough →</Link>
            </div>
          </div>
        </section>
      </div>

      <NextUp here="/proofs" />
    </Shell>
  )
}

export default dynamic(() => Promise.resolve(ProofsInner), { ssr: false })
