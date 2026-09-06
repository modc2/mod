'use client'

import { useCallback, useEffect, useState } from 'react'
import {
  atomicToXmr, bytes, call, getToken, hashrate, num, setToken, short, timeAgo, usd, xmr,
} from './api'
import { Bar, Button, C, Code, Copy, Field, Input, Note, Panel, Select, Spinner, Stat } from './ui'

type Tab = 'explorer' | 'wallet' | 'scan' | 'send' | 'swap'
const TABS: Tab[] = ['explorer', 'wallet', 'scan', 'send', 'swap']

// Anything touching a key needs the module token (~/.mod/monero/server.secret,
// printed by `m monero/token`). Explorer reads work without it.
function Unlock() {
  const [tok, setTok] = useState('')
  const [saved, setSaved] = useState(false)
  useEffect(() => { setTok(getToken()); setSaved(!!getToken()) }, [])
  return (
    <Panel title="Unlock">
      <Note kind={saved ? 'ok' : 'warn'}>
        {saved
          ? 'Token stored in this browser — wallet, scan, send and swap actions are unlocked.'
          : 'Explorer reads work without a token. Anything that uses a key — creating a wallet, scanning with a view key, sending — needs the module token.'}
        <div style={{ marginTop: 6, fontSize: 11, opacity: 0.85 }}>
          Get it with <code>m monero/token</code> or <code>cat ~/.mod/monero/server.secret</code>
        </div>
      </Note>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <input value={tok} onChange={e => setTok(e.target.value)} type="password"
          placeholder="module token" style={{
            flex: 1, minWidth: 200, padding: '9px 11px', background: C.bg,
            border: `1px solid ${C.line}`, borderRadius: 6, color: C.text, fontSize: 13,
            outline: 'none', fontFamily: 'ui-monospace, Menlo, monospace',
          }} />
        <Button onClick={() => { setToken(tok); setSaved(!!tok.trim()) }}>
          {saved ? 'Update' : 'Unlock'}
        </Button>
        {saved && <Button variant="ghost" onClick={() => { setToken(''); setTok(''); setSaved(false) }}>
          Forget
        </Button>}
      </div>
    </Panel>
  )
}

export default function Page() {
  const [tab, setTab] = useState<Tab>('explorer')
  const [caps, setCaps] = useState<any>(null)
  const [online, setOnline] = useState<boolean | null>(null)

  useEffect(() => {
    call('capabilities')
      .then(c => { setCaps(c); setOnline(true) })
      .catch(() => setOnline(false))
  }, [])

  return (
    <main style={{
      background: C.bg, color: C.text, minHeight: '100vh',
      fontFamily: 'ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif',
      width: 0, minWidth: '100%',
    }}>
      <div style={{ maxWidth: 980, margin: '0 auto', padding: '28px 20px 60px' }}>
        <header style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 22 }}>
          <div style={{
            width: 34, height: 34, borderRadius: '50%', background: C.orange,
            color: '#150d08', display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontWeight: 800, fontSize: 18, fontFamily: 'Georgia, serif',
          }}>M</div>
          <div style={{ flex: 1 }}>
            <h1 style={{ margin: 0, fontSize: 19, fontWeight: 700 }}>Monero</h1>
            <div style={{ fontSize: 11.5, color: C.dim }}>
              explorer · wallet · view-key scanner · swaps
            </div>
          </div>
          <div style={{ fontSize: 11, color: online === false ? C.red : online ? C.green : C.dim }}>
            {online === null ? 'connecting' : online ? '● online' : '● API offline'}
          </div>
        </header>

        <nav style={{ display: 'flex', gap: 6, marginBottom: 20, flexWrap: 'wrap' }}>
          {TABS.map(t => (
            <button key={t} onClick={() => setTab(t)} style={{
              padding: '7px 15px', borderRadius: 6, fontSize: 12, fontWeight: 600,
              letterSpacing: 0.8, textTransform: 'uppercase', cursor: 'pointer',
              background: tab === t ? C.orange : C.panel,
              color: tab === t ? '#150d08' : C.dim,
              border: `1px solid ${tab === t ? C.orange : C.line}`,
            }}>{t}</button>
          ))}
        </nav>

        {online === false && (
          <Note kind="error">
            The monero API is not reachable. Start it with <code>m monero/serve</code>.
          </Note>
        )}

        {tab !== 'explorer' && <Unlock />}
        {tab === 'explorer' && <Explorer />}
        {tab === 'wallet' && <Wallet />}
        {tab === 'scan' && <Scan />}
        {tab === 'send' && <Send caps={caps} />}
        {tab === 'swap' && <Swap />}

        {caps && <Capabilities caps={caps} />}
      </div>
    </main>
  )
}

// ── Explorer ────────────────────────────────────────────────────────────────

function Explorer() {
  const [info, setInfo] = useState<any>(null)
  const [block, setBlock] = useState<any>(null)
  const [fee, setFee] = useState<any>(null)
  const [pool, setPool] = useState<any>(null)
  const [q, setQ] = useState('')
  const [result, setResult] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const load = useCallback(() => {
    call('info').then(setInfo).catch(e => setErr(e.message))
    call('block').then(setBlock).catch(() => {})
    call('fee').then(setFee).catch(() => {})
    call('mempool', { limit: 6 }).then(setPool).catch(() => {})
  }, [])

  useEffect(() => {
    load()
    const id = setInterval(load, 60000)
    return () => clearInterval(id)
  }, [load])

  const search = async () => {
    if (!q.trim()) return
    setBusy(true); setErr(''); setResult(null)
    try { setResult(await call('search', { query: q.trim() })) }
    catch (e: any) { setErr(e.message) }
    finally { setBusy(false) }
  }

  return (
    <>
      {err && <Note kind="error">{err}</Note>}
      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
        gap: 10, marginBottom: 16,
      }}>
        <Stat label="Price" value={usd(info?.price_usd)} sub={`cap ${usd(info?.market_cap_usd)}`} />
        <Stat label="Height" value={num(info?.height)} sub={info?.network || '—'} />
        <Stat label="Mempool" value={num(info?.tx_pool_size)} sub="pending txs" />
        <Stat label="Hashrate" value={hashrate(info?.hashrate)} sub={`v${info?.hard_fork_version ?? '—'}`} />
      </div>

      <Panel title="Search">
        <div style={{ display: 'flex', gap: 8 }}>
          <input
            value={q} onChange={e => setQ(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && search()}
            placeholder="block height, block hash, txid, or address"
            style={{
              flex: 1, padding: '9px 11px', background: C.bg, borderRadius: 6,
              border: `1px solid ${C.line}`, color: C.text, fontSize: 13, outline: 'none',
              fontFamily: 'ui-monospace, Menlo, monospace',
            }} />
          <Button onClick={search} disabled={busy}>{busy ? '…' : 'Search'}</Button>
        </div>
        {result && (
          <div style={{ marginTop: 14 }}>
            <div style={{ fontSize: 11, color: C.orange, textTransform: 'uppercase', marginBottom: 8 }}>
              {result.type}
            </div>
            {result.result?.note && <Note kind="info">{result.result.note}</Note>}
            <Code>{JSON.stringify(result.result, null, 2)}</Code>
          </div>
        )}
      </Panel>

      <Panel title="Latest block">
        {!block ? <Spinner /> : (
          <>
            <Field label="Height" value={num(block.height)} />
            <Field label="Hash" value={block.hash} mono />
            <Field label="Time" value={timeAgo(block.timestamp)} />
            <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
              <Field label="Transactions" value={num(block.num_txes)} />
              <Field label="Size" value={bytes(block.size)} />
              <Field label="Reward" value={xmr(block.reward_xmr, 6)} />
            </div>
          </>
        )}
      </Panel>

      <Panel title="Fee">
        {!fee ? <Spinner /> : (
          <>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(140px,1fr))', gap: 10 }}>
              <Stat label="Normal priority" value={xmr(fee.fee_xmr, 6)} sub={usd(fee.fee_usd)} />
              <Stat label="Per byte" value={num(fee.fee_per_byte)} sub="piconero" />
              <Stat label="Assumed size" value={bytes(fee.size_bytes)} sub="2-in / 2-out" />
            </div>
            <div style={{ fontSize: 11, color: C.dim, marginTop: 10 }}>{fee.note}</div>
          </>
        )}
      </Panel>

      <Panel title="Mempool">
        {!pool ? <Spinner /> : pool.transactions?.length === 0 ? (
          <div style={{ color: C.dim, fontSize: 13 }}>Empty right now.</div>
        ) : pool.transactions.map((t: any) => (
          <div key={t.hash} style={{
            display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap',
            padding: '8px 0', borderTop: `1px solid ${C.line}`, fontSize: 12.5,
          }}>
            <span style={{ fontFamily: 'ui-monospace, Menlo, monospace' }}>{short(t.hash, 12)}</span>
            <span style={{ color: C.dim }}>
              ring {t.ring_size} · {bytes(t.size)} · fee {xmr(t.fee_xmr, 6)}
            </span>
          </div>
        ))}
      </Panel>
    </>
  )
}

// ── Wallet ──────────────────────────────────────────────────────────────────

// Secrets get their own panel with the warning attached. Showing them a second
// time in a raw JSON dump only widens the blast radius of a screenshot.
const SECRET_KEYS = ['seed_phrase', 'spend_secret_key', 'view_secret_key']

const redact = (o: any) => Object.fromEntries(Object.entries(o || {}).map(
  ([k, v]) => [k, SECRET_KEYS.includes(k) && v ? '— shown above —' : v]))

function Wallet() {
  const [wallets, setWallets] = useState<any[]>([])
  const [sel, setSel] = useState('')
  const [info, setInfo] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState<any>(null)

  const [name, setName] = useState('')
  const [pw, setPw] = useState('')
  const [phrase, setPhrase] = useState('')
  const [watchAddr, setWatchAddr] = useState('')
  const [watchKey, setWatchKey] = useState('')

  const refresh = useCallback(async () => {
    try {
      const r = await call('wallet_list')
      setWallets(r.wallets || [])
      if (!sel && r.wallets?.length) setSel(r.wallets[0].name)
    } catch (e: any) { setErr(e.message) }
  }, [sel])

  useEffect(() => { refresh() }, [])

  useEffect(() => {
    if (!sel) { setInfo(null); return }
    call('wallet_info', { name: sel }).then(setInfo).catch(e => setErr(e.message))
  }, [sel])

  const run = async (fn: string, args: any) => {
    setBusy(true); setErr(''); setMsg(null)
    try {
      const r = await call(fn, args)
      setMsg(r)
      await refresh()
      if (sel) call('wallet_info', { name: sel }).then(setInfo).catch(() => {})
    } catch (e: any) { setErr(e.message) }
    finally { setBusy(false) }
  }

  return (
    <>
      {err && <Note kind="error">{err}</Note>}

      <Panel title="Wallets" right={<Button variant="ghost" onClick={refresh}>refresh</Button>}>
        {wallets.length === 0 ? (
          <div style={{ color: C.dim, fontSize: 13 }}>No wallets yet — create one below.</div>
        ) : (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {wallets.map(w => (
              <button key={w.name} onClick={() => setSel(w.name)} style={{
                padding: '7px 13px', borderRadius: 6, fontSize: 12, cursor: 'pointer',
                background: sel === w.name ? C.panel2 : 'transparent',
                border: `1px solid ${sel === w.name ? C.orange : C.line}`,
                color: sel === w.name ? C.text : C.dim,
              }}>
                {w.name}
                <span style={{ color: C.dim }}> · {w.view_only ? 'view-only' : 'full'}</span>
              </button>
            ))}
          </div>
        )}
      </Panel>

      {info && (
        <Panel title={`${info.name} — addresses`}>
          {info.view_only && <Note kind="info">
            View-only: this wallet can find incoming payments and nothing else.
          </Note>}
          <Field label="Main address" value={<>{info.address}<Copy text={info.address} /></>} mono />
          <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
            <Field label="Network" value={info.network} />
            <Field label="Restore height" value={num(info.restore_height)} />
            <Field label="Subaddresses" value={num(info.subaddresses?.length || 0)} />
          </div>

          {info.subaddresses?.map((s: any) => (
            <div key={s.address} style={{
              padding: '9px 0', borderTop: `1px solid ${C.line}`, fontSize: 12.5,
              fontFamily: 'ui-monospace, Menlo, monospace', wordBreak: 'break-all',
            }}>
              <span style={{ color: C.orange, marginRight: 8 }}>{s.major}/{s.minor}</span>
              {s.address}<Copy text={s.address} />
              {s.label && <span style={{ color: C.dim, marginLeft: 8 }}>{s.label}</span>}
            </div>
          ))}

          <div style={{ marginTop: 14, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <Button disabled={busy || !pw}
              onClick={() => run('wallet_new_address', { name: sel, password: pw })}>
              New subaddress
            </Button>
            <Button variant="ghost" disabled={busy}
              onClick={() => run('wallet_integrated', { name: sel })}>
              Integrated address
            </Button>
            <Button variant="ghost" disabled={busy || !pw}
              onClick={() => run('wallet_reveal', { name: sel, password: pw })}>
              Reveal keys
            </Button>
            <input value={pw} onChange={e => setPw(e.target.value)} type="password"
              placeholder="wallet password" style={{
                padding: '9px 11px', background: C.bg, border: `1px solid ${C.line}`,
                borderRadius: 6, color: C.text, fontSize: 13, outline: 'none',
                flex: 1, minWidth: 160,
              }} />
          </div>
          <div style={{ fontSize: 11, color: C.dim, marginTop: 8 }}>
            A fresh subaddress per payer is the whole point — reusing one address is
            the only thing a Monero receiver can really get wrong.
          </div>
        </Panel>
      )}

      {msg && (
        <Panel title="Result">
          {msg.seed_phrase && (
            <Note kind="warn">
              <b>Write this down now.</b> It is the only way to recover the wallet,
              it is not shown again, and anyone who reads it can spend the funds.
              <div style={{
                marginTop: 8, fontFamily: 'ui-monospace, Menlo, monospace',
                fontSize: 13, color: C.text, lineHeight: 1.7,
              }}>{msg.seed_phrase}<Copy text={msg.seed_phrase} /></div>
            </Note>
          )}
          {(msg.spend_secret_key || msg.view_secret_key) && (
            <Note kind="warn">
              <b>Private keys.</b> The spend key owns the funds. The view key alone
              reveals every payment this wallet has ever received.
              {msg.spend_secret_key && <Field label="Spend key" mono
                value={<>{msg.spend_secret_key}<Copy text={msg.spend_secret_key} /></>} />}
              {msg.view_secret_key && <Field label="View key" mono
                value={<>{msg.view_secret_key}<Copy text={msg.view_secret_key} /></>} />}
            </Note>
          )}
          {/* The phrase has its own panel above; repeating it in the dump just
              puts the secret on screen twice. */}
          <Code>{JSON.stringify(redact(msg), null, 2)}</Code>
        </Panel>
      )}

      <Panel title="Create or restore">
        <Input label="Wallet name" value={name} onChange={(e: any) => setName(e.target.value)}
          placeholder="savings" />
        <Input label="Password" type="password" value={pw}
          onChange={(e: any) => setPw(e.target.value)}
          hint="encrypts the seed phrase and both private keys at rest (AES-256-GCM)" />
        <Input label="Seed phrase (leave blank to create a new wallet)" value={phrase}
          onChange={(e: any) => setPhrase(e.target.value)}
          placeholder="25 Monero words" />
        <Button disabled={busy || !name || !pw}
          onClick={() => run(phrase.trim() ? 'wallet_restore' : 'wallet_create',
            phrase.trim() ? { name, password: pw, seed_phrase: phrase.trim() }
              : { name, password: pw })}>
          {phrase.trim() ? 'Restore wallet' : 'Create wallet'}
        </Button>
      </Panel>

      <Panel title="Watch a wallet you do not hold">
        <Note kind="info">
          An address plus its private view key finds incoming payments without any
          ability to spend. Good for watching a cold wallet from here.
        </Note>
        <Input label="Main address" value={watchAddr}
          onChange={(e: any) => setWatchAddr(e.target.value)} placeholder="4…" />
        <Input label="Private view key" value={watchKey}
          onChange={(e: any) => setWatchKey(e.target.value)}
          placeholder="64 hex characters" />
        <Button disabled={busy || !name || !pw || !watchAddr || !watchKey}
          onClick={() => run('wallet_watch', {
            name, password: pw, address: watchAddr.trim(),
            view_secret_key: watchKey.trim(),
          })}>
          Add “{name || '…'}” as view-only
        </Button>
      </Panel>
    </>
  )
}

// ── Scan ────────────────────────────────────────────────────────────────────

function Scan() {
  const [wallets, setWallets] = useState<any[]>([])
  const [name, setName] = useState('')
  const [pw, setPw] = useState('')
  const [start, setStart] = useState('')
  const [blocks, setBlocks] = useState('20')
  const [result, setResult] = useState<any>(null)
  const [found, setFound] = useState<any[]>([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    call('wallet_list').then(r => {
      setWallets(r.wallets || [])
      if (r.wallets?.length) {
        setName(r.wallets[0].name)
        if (r.wallets[0].restore_height) setStart(String(r.wallets[0].restore_height))
      }
    }).catch(() => {})
  }, [])

  const scan = async (from?: number) => {
    setBusy(true); setErr('')
    try {
      const r = await call('wallet_scan', {
        name, password: pw, blocks: Number(blocks),
        ...(from != null ? { start_height: from }
          : start ? { start_height: Number(start) } : {}),
      })
      setResult(r)
      setFound(f => [...(r.outputs || []), ...f])
      setStart(String(r.next_start_height))
    } catch (e: any) { setErr(e.message) }
    finally { setBusy(false) }
  }

  return (
    <>
      {err && <Note kind="error">{err}</Note>}
      <Panel title="Scan with a view key">
        <Note kind="info">
          Monero has no address balances. Finding your own money means testing every
          output in every block against your view key — which happens here, on this
          host. Nothing is sent anywhere.
        </Note>
        <Select label="Wallet" value={name} onChange={(e: any) => setName(e.target.value)}>
          {wallets.length === 0 && <option value="">no wallets — create one first</option>}
          {wallets.map(w => <option key={w.name} value={w.name}>
            {w.name}{w.view_only ? ' (view-only)' : ''}
          </option>)}
        </Select>
        <Input label="Password" type="password" value={pw}
          onChange={(e: any) => setPw(e.target.value)}
          hint="the view key is encrypted at rest, so scanning needs it" />
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          <div style={{ flex: 1, minWidth: 140 }}>
            <Input label="Start height" value={start}
              onChange={(e: any) => setStart(e.target.value)}
              placeholder="wallet restore height" />
          </div>
          <div style={{ flex: 1, minWidth: 140 }}>
            <Input label="Blocks" value={blocks}
              onChange={(e: any) => setBlocks(e.target.value)} hint="a window at a time" />
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <Button onClick={() => scan()} disabled={busy || !name || !pw}>
            {busy ? 'scanning…' : 'Scan window'}
          </Button>
          {result && <Button variant="ghost" disabled={busy}
            onClick={() => scan(result.next_start_height)}>
            Continue from {num(result.next_start_height)}
          </Button>}
        </div>
      </Panel>

      {result && (
        <Panel title="Scan result">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(140px,1fr))', gap: 10, marginBottom: 12 }}>
            <Stat label="Received" value={xmr(result.received_xmr, 6)}
              sub={`${result.outputs_found} outputs`} />
            <Stat label="Blocks scanned" value={num(result.blocks_scanned)}
              sub={`${num(result.transactions_scanned)} txs`} />
            <Stat label="Rate" value={`${result.blocks_per_second ?? '—'}/s`}
              sub={`${result.seconds}s`} />
          </div>
          <Field label="Range"
            value={`${num(result.from_height)} → ${num(result.to_height)} of ${num(result.tip_height)}`} />
          <Bar value={result.to_height} max={result.tip_height} />
          <div style={{ fontSize: 11, color: C.dim, marginTop: 8 }}>
            {num(result.tip_height - result.to_height)} blocks to the tip
            {result.eta_full_chain_hours
              ? ` · a whole-chain scan at this rate would take about ${result.eta_full_chain_hours}h`
              : ''}
          </div>
          <Note kind="warn">{result.caveat}</Note>
          {result.node_note && <Note kind="info">{result.node_note}</Note>}
        </Panel>
      )}

      {found.length > 0 && (
        <Panel title={`Outputs found (${found.length})`}>
          {found.map((o: any, i: number) => (
            <div key={`${o.tx_hash}-${o.output_index}-${i}`} style={{
              display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap',
              padding: '9px 0', borderTop: `1px solid ${C.line}`, fontSize: 12.5,
            }}>
              <span style={{ fontFamily: 'ui-monospace, Menlo, monospace' }}>
                {short(o.tx_hash, 10)} <span style={{ color: C.dim }}>#{o.output_index}</span>
                <span style={{ color: C.orange, marginLeft: 8 }}>{o.to}</span>
                {o.coinbase && <span style={{ color: C.dim, marginLeft: 8 }}>coinbase</span>}
              </span>
              <span style={{ color: C.green }}>{xmr(o.amount_xmr, 6)}</span>
            </div>
          ))}
        </Panel>
      )}
    </>
  )
}

// ── Send ────────────────────────────────────────────────────────────────────

function Send({ caps }: { caps: any }) {
  const [rpc, setRpc] = useState<any>(null)
  const [balance, setBalance] = useState<any>(null)
  const [to, setTo] = useState('')
  const [amount, setAmount] = useState('')
  const [priority, setPriority] = useState('1')
  const [preview, setPreview] = useState<any>(null)
  const [sent, setSent] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const load = useCallback(() => {
    call('rpc_status').then(r => {
      setRpc(r)
      if (r.available) call('balance').then(setBalance).catch(() => {})
    }).catch(e => setErr(e.message))
  }, [])
  useEffect(() => { load() }, [load])

  const dryRun = async () => {
    setBusy(true); setErr(''); setPreview(null); setSent(null)
    try {
      setPreview(await call('send', {
        to: to.trim(), amount: Number(amount),
        priority: Number(priority), broadcast: false,
      }))
    } catch (e: any) { setErr(e.message) }
    finally { setBusy(false) }
  }

  const confirm = async () => {
    setBusy(true); setErr('')
    try {
      // Relay the exact transaction that was previewed, not a fresh one.
      setSent(await call('send_confirm', { tx_metadata: preview.tx_metadata[0] }))
      setPreview(null)
      call('balance').then(setBalance).catch(() => {})
    } catch (e: any) { setErr(e.message) }
    finally { setBusy(false) }
  }

  if (rpc && !rpc.available) {
    return (
      <Panel title="Sending needs monero-wallet-rpc">
        <Note kind="warn">
          This module does not build Monero transactions itself — a spend needs a
          CLSAG ring signature and a Bulletproofs+ range proof, and shipping an
          unverifiable version of those would be worse than not having them.
          Everything else here works without this.
        </Note>
        <div style={{ fontSize: 12, color: C.dim, marginBottom: 8 }}>
          Start the reference wallet daemon and this tab lights up:
        </div>
        <Code>{`monero-wallet-rpc \\
  --wallet-file ~/wallets/mine \\
  --rpc-bind-port 18083 --disable-rpc-login \\
  --daemon-address node.example:18081`}</Code>
        <div style={{ fontSize: 12, color: C.dim, marginTop: 10 }}>
          Then hand it a wallet from this module: <code>m monero/rpc_load_wallet name=savings password=…</code>
        </div>
        <div style={{ marginTop: 12 }}>
          <Button variant="ghost" onClick={load}>Check again</Button>
        </div>
      </Panel>
    )
  }

  return (
    <>
      {err && <Note kind="error">{err}</Note>}
      {balance && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))', gap: 10, marginBottom: 16 }}>
          <Stat label="Unlocked" value={xmr(balance.unlocked_xmr, 6)} sub="spendable now" />
          <Stat label="Total" value={xmr(balance.balance_xmr, 6)}
            sub={balance.blocks_to_unlock ? `${balance.blocks_to_unlock} blocks to unlock` : 'all unlocked'} />
        </div>
      )}

      <Panel title="Send XMR">
        <Note kind="info">
          Every send is previewed as a real signed transaction first — the fee and
          weight below are exact — and nothing reaches the network until you confirm.
        </Note>
        <Input label="To address" value={to} onChange={(e: any) => setTo(e.target.value)}
          placeholder="4… or 8… or an integrated address"
          hint="checked locally before anything is built" />
        <Input label="Amount (XMR)" value={amount}
          onChange={(e: any) => setAmount(e.target.value)} placeholder="0.1" />
        <Select label="Priority" value={priority} onChange={(e: any) => setPriority(e.target.value)}>
          <option value="0">slow — cheapest</option>
          <option value="1">normal</option>
          <option value="2">high</option>
          <option value="3">priority — fastest</option>
        </Select>
        <Button onClick={dryRun} disabled={busy || !to || !amount}>
          {busy ? 'building…' : 'Preview (dry run)'}
        </Button>
      </Panel>

      {preview && (
        <Panel title="Signed — not yet relayed">
          <Note kind="warn">
            <b>DRY RUN.</b> This transaction exists and is signed, but it has not
            been relayed and no funds have moved.
          </Note>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(140px,1fr))', gap: 10, marginBottom: 12 }}>
            <Stat label="Amount" value={xmr(preview.amount_xmr, 6)} />
            <Stat label="Fee" value={xmr(preview.fee_xmr, 6)} sub={`weight ${num(preview.weight)}`} />
            <Stat label="Transactions" value={num(preview.transactions)} />
          </div>
          <Field label="txid (once relayed)"
            value={<>{preview.txids?.[0]}<Copy text={preview.txids?.[0] || ''} /></>} mono />
          <Note kind="warn">
            Monero payments are final once mined. Check the address one more time.
          </Note>
          <Button variant="danger" onClick={confirm} disabled={busy || !preview.tx_metadata?.length}>
            {busy ? 'relaying…' : `Relay ${xmr(preview.amount_xmr, 6)} for real`}
          </Button>
        </Panel>
      )}

      {sent && (
        <Panel title="Relayed">
          <Note kind="ok">Published to the network.</Note>
          <Field label="txid" value={<>{sent.txid}<Copy text={sent.txid} /></>} mono />
        </Panel>
      )}
    </>
  )
}

// ── Swap ────────────────────────────────────────────────────────────────────

function Swap() {
  const [routes, setRoutes] = useState<any>(null)
  const [dir, setDir] = useState<'out' | 'in'>('out')
  const [asset, setAsset] = useState('BTC')
  const [amount, setAmount] = useState('1')
  const [recipient, setRecipient] = useState('')
  const [refund, setRefund] = useState('')
  const [quote, setQuote] = useState<any>(null)
  const [order, setOrder] = useState<any>(null)
  const [track, setTrack] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => { call('bridge_routes').then(setRoutes).catch(e => setErr(e.message)) }, [])

  const args = () => dir === 'out'
    ? { from_asset: 'XMR', to_asset: asset, amount: Number(amount) }
    : { from_asset: asset, to_asset: 'XMR', amount: Number(amount) }

  const doQuote = async () => {
    setBusy(true); setErr(''); setQuote(null); setOrder(null); setTrack(null)
    try { setQuote(await call('bridge_quote', args())) }
    catch (e: any) { setErr(e.message) }
    finally { setBusy(false) }
  }

  const doStart = async () => {
    setBusy(true); setErr('')
    try {
      setOrder(await call('bridge_start', { ...args(), recipient, refund_to: refund }))
      setQuote(null)
    } catch (e: any) { setErr(e.message) }
    finally { setBusy(false) }
  }

  const doTrack = async (id: string) => {
    setBusy(true); setErr('')
    try { setTrack(await call('bridge_status', { order_id: id })) }
    catch (e: any) { setErr(e.message) }
    finally { setBusy(false) }
  }

  return (
    <>
      {err && <Note kind="error">{err}</Note>}
      <Panel title="Swap">
        <Note kind="warn">
          There is no trustless bridge for Monero and there cannot be one: a bridge
          contract has to observe a deposit, and Monero has no contracts and no
          public amounts. What this does is a <b>custodial</b> swap — a provider
          holds your coins between the deposit and the payout.
        </Note>

        <div style={{ display: 'flex', gap: 6, marginBottom: 14, flexWrap: 'wrap' }}>
          {(['out', 'in'] as const).map(d => (
            <button key={d} onClick={() => { setDir(d); setQuote(null); setOrder(null) }} style={{
              padding: '6px 13px', borderRadius: 6, fontSize: 12, cursor: 'pointer',
              background: dir === d ? C.panel2 : 'transparent',
              border: `1px solid ${dir === d ? C.orange : C.line}`,
              color: dir === d ? C.text : C.dim,
            }}>{d === 'out' ? 'XMR → other chain' : 'other chain → XMR'}</button>
          ))}
        </div>

        <Input label={dir === 'out' ? 'Receive asset' : 'Pay with'} value={asset}
          onChange={(e: any) => setAsset(e.target.value)}
          placeholder="BTC, ETH, TRX:USDT, ETH:USDC"
          hint="qualify with a chain when an asset lives on several" />
        <Input label={`Amount (${dir === 'out' ? 'XMR' : asset})`} value={amount}
          onChange={(e: any) => setAmount(e.target.value)} />
        <Input label={dir === 'out' ? `Recipient address on ${asset.split(':')[0] || asset}` : 'Your Monero address'}
          value={recipient} onChange={(e: any) => setRecipient(e.target.value)}
          placeholder={dir === 'out' ? '0x… / bc1…' : '4…'} />
        <Input label={dir === 'out' ? 'Refund Monero address (if the swap fails)' : `Refund address on ${asset.split(':')[0] || asset}`}
          value={refund} onChange={(e: any) => setRefund(e.target.value)}
          placeholder={dir === 'out' ? '4…' : '0x… / bc1…'} />

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <Button onClick={doQuote} disabled={busy || !amount}>
            {busy ? '…' : 'Get quote'}
          </Button>
          <Button variant="ghost" onClick={doStart}
            disabled={busy || !recipient || !refund || !amount}>
            Reserve deposit address
          </Button>
        </div>
      </Panel>

      {quote && (
        <Panel title="Quote (nothing reserved)">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))', gap: 10, marginBottom: 12 }}>
            <Stat label="You send" value={`${quote.amount_in} ${quote.from.split(':')[1]}`} />
            <Stat label="You receive" value={`${quote.amount_out}`} sub={quote.to} />
            <Stat label="Rate" value={quote.rate} sub={quote.rate_type} />
          </div>
          <Field label="Limits" value={`min ${quote.min_amount} · max ${quote.max_amount}`} />
          <div style={{ fontSize: 11, color: C.dim }}>{quote.note}</div>
        </Panel>
      )}

      {order && (
        <Panel title="Deposit address reserved">
          <Note kind="warn">{order.warning}</Note>
          <Field label="Deposit address"
            value={<>{order.deposit_address}<Copy text={order.deposit_address} /></>} mono />
          <Field label="Send" value={`${order.amount_in} ${order.from.split(':')[1]}`} />
          <Field label="You receive" value={`~${order.amount_out} ${order.to} at ${short(order.recipient, 12)}`} />
          <Field label="Order id" value={<>{order.order_id}<Copy text={order.order_id} /></>} mono />
          <Button variant="ghost" onClick={() => doTrack(order.order_id)} disabled={busy}>
            Check status
          </Button>
        </Panel>
      )}

      {track && (
        <Panel title="Swap status">
          <Field label="Status" value={track.status}
            color={track.status === 'success' ? C.green : C.orange} />
          <Field label="Deposit seen" value={track.hash_in || '— nothing received yet'} mono />
          <Field label="Payout" value={track.hash_out || '—'} mono />
        </Panel>
      )}

      {routes && (
        <Panel title="Why there is only one route">
          {Object.entries(routes.not_available || {}).map(([k, v]: any) => (
            <div key={k} style={{
              display: 'flex', gap: 10, padding: '7px 0', fontSize: 12.5,
              borderTop: `1px solid ${C.line}`, flexWrap: 'wrap',
            }}>
              <span style={{ color: C.dim, minWidth: 140 }}>{k.replace(/_/g, ' ')}</span>
              <span style={{ color: C.dim, flex: 1 }}>{v}</span>
            </div>
          ))}
          <div style={{ fontSize: 11, color: C.dim, marginTop: 10 }}>{routes.note}</div>
        </Panel>
      )}
    </>
  )
}

// ── Capabilities footer ─────────────────────────────────────────────────────

function Capabilities({ caps }: { caps: any }) {
  return (
    <Panel title="Capabilities">
      {Object.entries(caps).filter(([, v]: any) => v && typeof v === 'object' && 'supported' in v)
        .map(([k, v]: any) => (
          <div key={k} style={{
            display: 'flex', gap: 10, padding: '7px 0', fontSize: 12.5,
            borderTop: `1px solid ${C.line}`, flexWrap: 'wrap',
          }}>
            <span style={{ color: v.supported ? C.green : C.dim, width: 14 }}>
              {v.supported ? '●' : '○'}
            </span>
            <span style={{ minWidth: 160, color: C.text }}>{k.replace(/_/g, ' ')}</span>
            <span style={{ color: C.dim, flex: 1, minWidth: 200 }}>{v.details || v.reason}</span>
          </div>
        ))}
      <div style={{ fontSize: 11, color: C.dim, marginTop: 12 }}>{caps.safety}</div>
    </Panel>
  )
}
