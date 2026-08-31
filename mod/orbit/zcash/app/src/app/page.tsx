'use client'

import { ReactNode, useCallback, useEffect, useMemo, useState } from 'react'
import { call, get, getToken, num, setToken, timeAgo, usd, zatToZec, zec } from './api'
import { Button, C, Code, Copy, Field, Input, Note, Panel, Spinner, Stat } from './ui'
import { Ask, Learn } from './learn'
import { PrivateBridge } from './private'

// Spending functions need the module token (~/.mod/zcash/server.secret,
// printed by `m zcash/token`). Reads work without it.
function Unlock() {
  const [tok, setTok] = useState('')
  const [saved, setSaved] = useState(false)
  useEffect(() => { setTok(getToken()); setSaved(!!getToken()) }, [])
  return (
    <Panel title="Unlock spending">
      <Note kind={saved ? 'ok' : 'warn'}>
        {saved
          ? 'Token stored in this browser — wallet, send and bridge actions are unlocked.'
          : 'Reads work without a token. To create wallets, send ZEC or start a bridge, paste the module token below.'}
        <div style={{ marginTop: 6, fontSize: 11, opacity: 0.85 }}>
          Get it with <code>m zcash/token</code> or <code>cat ~/.mod/zcash/server.secret</code>
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

// An action suggested by a lesson or by the agent names a module function.
// This is the one place that maps a function back to the tab that performs it,
// so "open the tab" on an action lands somewhere useful instead of nowhere.
function tabFor(fn: string): Tab {
  if (fn.startsWith('bridge_shielded')) return 'private'
  if (fn.startsWith('bridge')) return 'bridge'
  if (fn.startsWith('shielded')) return 'shielded'
  if (fn.startsWith('wallet')) return 'wallet'
  if (fn === 'send' || fn === 'estimate_fee' || fn === 'broadcast_raw') return 'send'
  if (fn === 'learn' || fn === 'explain') return 'learn'
  if (fn === 'ask') return 'ask'
  if (fn === 'mcp') return 'mcp'
  return 'explorer'
}

type Tab = 'explorer' | 'learn' | 'ask' | 'wallet' | 'shielded' | 'send'
  | 'bridge' | 'private' | 'mcp'
const TABS: Tab[] = ['explorer', 'learn', 'ask', 'wallet', 'shielded', 'send',
  'bridge', 'private', 'mcp']

export default function Page() {
  const [tab, setTab] = useState<Tab>('explorer')
  // ?tab=learn deep-links a tab, so a lesson or an answer can be linked to
  // directly. The hash is kept in sync so the back button and a copied URL
  // both land where the reader was.
  useEffect(() => {
    const want = new URLSearchParams(window.location.search).get('tab')
      || window.location.hash.replace('#', '')
    if (want && (TABS as string[]).includes(want)) setTab(want as Tab)
  }, [])
  useEffect(() => {
    if (typeof window !== 'undefined') window.history.replaceState(null, '', `#${tab}`)
  }, [tab])
  const [caps, setCaps] = useState<any>(null)
  // null = still connecting. The API route starts the backend on demand, so a
  // cold first load takes a few seconds; say "starting" rather than "offline"
  // until we have actually given up.
  const [online, setOnline] = useState<boolean | null>(null)
  const [why, setWhy] = useState('')

  const connect = useCallback(async () => {
    setOnline(null); setWhy('')
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        setCaps(await call('capabilities'))
        setOnline(true)
        return
      } catch (e: any) {
        setWhy(e?.message || String(e))
        await new Promise(r => setTimeout(r, 2500))
      }
    }
    setOnline(false)
  }, [])

  useEffect(() => { connect() }, [connect])

  return (
    <main style={{
      background: C.bg, color: C.text, minHeight: '100vh',
      fontFamily: 'ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif',
      width: 0, minWidth: '100%',
    }}>
      <div style={{ maxWidth: 980, margin: '0 auto', padding: '28px 20px 60px' }}>
        <header style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 22 }}>
          <div style={{
            width: 34, height: 34, borderRadius: '50%', background: C.gold,
            color: '#12141a', display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontWeight: 800, fontSize: 18, fontFamily: 'Georgia, serif',
          }}>Z</div>
          <div style={{ flex: 1 }}>
            <h1 style={{ margin: 0, fontSize: 19, fontWeight: 700 }}>Zcash</h1>
            <div style={{ fontSize: 11.5, color: C.dim }}>
              explorer · wallet · shielded notes · cross-chain bridge
            </div>
          </div>
          <div style={{ fontSize: 11, color: online === false ? C.red : online ? C.green : C.dim }}>
            {online === null ? '○ starting' : online ? '● online' : '● API offline'}
          </div>
        </header>

        <nav style={{ display: 'flex', gap: 6, marginBottom: 20, flexWrap: 'wrap' }}>
          {TABS.map(t => (
            <button key={t} onClick={() => setTab(t)} style={{
              padding: '7px 15px', borderRadius: 6, fontSize: 12, fontWeight: 600,
              letterSpacing: 0.8, textTransform: 'uppercase', cursor: 'pointer',
              background: tab === t ? C.gold : C.panel,
              color: tab === t ? '#12141a' : C.dim,
              border: `1px solid ${tab === t ? C.gold : C.line}`,
            }}>{t}</button>
          ))}
        </nav>

        {online === false && (
          <Note kind="error">
            The zcash backend is not answering, and starting it from here did not
            work. Run <code>m zcash/serve</code> and check <code>/tmp/zcash/rest.log</code>.
            <div style={{ marginTop: 6, fontSize: 11, opacity: 0.85 }}>{why}</div>
            <div style={{ marginTop: 10 }}>
              <Button variant="ghost" onClick={connect}>Retry</Button>
            </div>
          </Note>
        )}

        {!['explorer', 'learn', 'ask'].includes(tab) && <Unlock />}
        {tab === 'explorer' && <Explorer online={online} />}
        {tab === 'wallet' && <Wallet />}
        {tab === 'shielded' && <Shielded caps={caps} />}
        {tab === 'send' && <Send />}
        {tab === 'bridge' && <Bridge caps={caps} />}
        {tab === 'private' && <PrivateBridge caps={caps} />}
        {tab === 'learn' && <Learn onAction={a => setTab(tabFor(a.fn))} />}
        {tab === 'ask' && <Ask onAction={a => setTab(tabFor(a.fn))} />}
        {tab === 'mcp' && <Mcp />}

        {caps && !['mcp', 'learn', 'ask'].includes(tab) && <Capabilities caps={caps} />}
      </div>
    </main>
  )
}

// ── Explorer ────────────────────────────────────────────────────────────────

function Explorer({ online }: { online: boolean | null }) {
  const [info, setInfo] = useState<any>(null)
  const [block, setBlock] = useState<any>(null)
  const [q, setQ] = useState('')
  const [result, setResult] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const load = useCallback(() => {
    call('info').then(i => { setInfo(i); setErr('') }).catch(e => setErr(e.message))
    call('block').then(setBlock).catch(() => {})
  }, [])

  // Wait for the connection check above; loading before the backend is up only
  // produces a second copy of the same error.
  useEffect(() => {
    if (!online) return
    load()
    const id = setInterval(load, 60000)
    return () => clearInterval(id)
  }, [load, online])

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
      {online === null && (
        <Note kind="info">Connecting to the local zcash backend…</Note>
      )}
      {info?.stale && (
        <Note kind="warn">
          Showing chain data from {Math.round(info.stale_seconds / 60) || 1} min ago —
          the upstream explorer is not answering right now.
          <div style={{ marginTop: 6, fontSize: 11, opacity: 0.85 }}>{info.stale_reason}</div>
        </Note>
      )}
      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
        gap: 10, marginBottom: 16,
      }}>
        <Stat label="Price" value={usd(info?.market_price_usd)} sub={`cap ${usd(info?.market_cap_usd)}`} />
        <Stat label="Height" value={num(info?.best_block_height)} sub={timeAgo(info?.best_block_time)} />
        <Stat label="Mempool" value={num(info?.mempool_transactions)} sub="pending txs" />
        <Stat label="Transactions" value={info ? `${(info.transactions / 1e6).toFixed(2)}M` : '—'} sub="all time" />
        <Stat
          label="Supply"
          value={info?.circulation_zec ? `${(info.circulation_zec / 1e6).toFixed(2)}M` : '—'}
          sub={info?.circulation_zec
            ? `of ${(info.max_supply_zec / 1e6).toFixed(0)}M ZEC`
            : 'ZEC'} />
      </div>

      <Panel title="Search">
        <div style={{ display: 'flex', gap: 8 }}>
          <input
            value={q} onChange={e => setQ(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && search()}
            placeholder="block height, txid, t-address, or zs1/u1 address"
            style={{
              flex: 1, padding: '9px 11px', background: C.bg, borderRadius: 6,
              border: `1px solid ${C.line}`, color: C.text, fontSize: 13, outline: 'none',
              fontFamily: 'ui-monospace, Menlo, monospace',
            }} />
          <Button onClick={search} disabled={busy}>{busy ? '…' : 'Search'}</Button>
        </div>
        {result && (
          <div style={{ marginTop: 14 }}>
            <div style={{ fontSize: 11, color: C.gold, textTransform: 'uppercase', marginBottom: 8 }}>
              {result.type}
            </div>
            <Code>{JSON.stringify(result.result, null, 2)}</Code>
          </div>
        )}
      </Panel>

      <Panel title="Latest block">
        {!block ? <Spinner /> : (
          <>
            <Field label="Height" value={num(block.height)} />
            <Field label="Hash" value={block.hash} mono />
            <Field label="Time" value={`${block.time} (${timeAgo(block.time)})`} />
            <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
              <Field label="Transactions" value={num(block.transaction_count)} />
              <Field label="Size" value={`${num(block.size)} bytes`} />
              <Field label="Output total" value={zatToZec(block.output_total)} />
            </div>
          </>
        )}
      </Panel>
    </>
  )
}

// ── Wallet ──────────────────────────────────────────────────────────────────

function Wallet() {
  const [wallets, setWallets] = useState<any[]>([])
  const [sel, setSel] = useState('')
  const [bal, setBal] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState<any>(null)

  const [name, setName] = useState('')
  const [pw, setPw] = useState('')
  const [mnemonic, setMnemonic] = useState('')
  const [wif, setWif] = useState('')

  const refresh = useCallback(async () => {
    try {
      const r = await call('wallet_list')
      setWallets(r.wallets || [])
      if (!sel && r.wallets?.length) setSel(r.wallets[0].name)
    } catch (e: any) { setErr(e.message) }
  }, [sel])

  useEffect(() => { refresh() }, [])

  useEffect(() => {
    if (!sel) { setBal(null); return }
    setBal(null)
    call('wallet_balance', { name: sel }).then(setBal).catch(e => setErr(e.message))
  }, [sel])

  const run = async (fn: string, args: any, after?: () => void) => {
    setBusy(true); setErr(''); setMsg(null)
    try {
      const r = await call(fn, args)
      setMsg(r); await refresh(); after?.()
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
                border: `1px solid ${sel === w.name ? C.gold : C.line}`,
                color: sel === w.name ? C.text : C.dim,
              }}>{w.name} <span style={{ color: C.dim }}>· {w.addresses} addr</span></button>
            ))}
          </div>
        )}
      </Panel>

      {sel && (
        <Panel title={`${sel} — balance`} right={
          <Button variant="ghost" onClick={() => call('wallet_balance', { name: sel }).then(setBal)}>
            reload
          </Button>
        }>
          {!bal ? <Spinner /> : (
            <>
              <div style={{ display: 'flex', gap: 10, marginBottom: 14, flexWrap: 'wrap' }}>
                <Stat label="Total" value={zec(bal.total_zec, 8)} sub={usd(bal.total_usd)} />
              </div>
              {bal.addresses?.map((a: any) => (
                <div key={a.address} style={{
                  display: 'flex', justifyContent: 'space-between', gap: 12,
                  padding: '9px 0', borderTop: `1px solid ${C.line}`, fontSize: 12.5,
                  flexWrap: 'wrap',
                }}>
                  <span style={{ fontFamily: 'ui-monospace, Menlo, monospace', wordBreak: 'break-all' }}>
                    {a.address}<Copy text={a.address} />
                    {a.label && <span style={{ color: C.gold, marginLeft: 8 }}>{a.label}</span>}
                  </span>
                  <span style={{ color: a.balance_zatoshi ? C.green : C.dim, whiteSpace: 'nowrap' }}>
                    {zec(a.balance_zec, 8)}
                  </span>
                </div>
              ))}
              <div style={{ marginTop: 14, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <Button disabled={busy || !pw}
                  onClick={() => run('wallet_new_address', { name: sel, password: pw })}>
                  New address
                </Button>
                <Button variant="ghost" disabled={busy || !pw}
                  onClick={() => run('wallet_reveal', { name: sel, password: pw })}>
                  Reveal seed
                </Button>
                <input value={pw} onChange={e => setPw(e.target.value)} type="password"
                  placeholder="wallet password" style={{
                    padding: '9px 11px', background: C.bg, border: `1px solid ${C.line}`,
                    borderRadius: 6, color: C.text, fontSize: 13, outline: 'none', flex: 1, minWidth: 160,
                  }} />
              </div>
            </>
          )}
        </Panel>
      )}

      {msg && (
        <Panel title="Result">
          {msg.mnemonic && (
            <Note kind="warn">
              <b>Write this down now.</b> It is the only way to recover the wallet
              and it will not be shown again.
              <div style={{
                marginTop: 8, fontFamily: 'ui-monospace, Menlo, monospace',
                fontSize: 13, color: C.text, lineHeight: 1.7,
              }}>{msg.mnemonic}<Copy text={msg.mnemonic} /></div>
            </Note>
          )}
          <Code>{JSON.stringify(msg, null, 2)}</Code>
        </Panel>
      )}

      <Panel title="Create or restore">
        <Input label="Wallet name" value={name} onChange={(e: any) => setName(e.target.value)}
          placeholder="savings" />
        <Input label="Password" type="password" value={pw}
          onChange={(e: any) => setPw(e.target.value)}
          hint="encrypts the seed at rest (AES-256-GCM); required to spend" />
        <Input label="Mnemonic (leave blank to create a new wallet)" value={mnemonic}
          onChange={(e: any) => setMnemonic(e.target.value)}
          placeholder="12 or 24 BIP39 words" />
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <Button disabled={busy || !name || !pw}
            onClick={() => run(mnemonic.trim() ? 'wallet_restore' : 'wallet_create',
              mnemonic.trim()
                ? { name, password: pw, mnemonic: mnemonic.trim() }
                : { name, password: pw })}>
            {mnemonic.trim() ? 'Restore wallet' : 'Create wallet'}
          </Button>
        </div>
      </Panel>

      <Panel title="Import a private key">
        <Input label="WIF private key" value={wif} onChange={(e: any) => setWif(e.target.value)}
          placeholder="L… or K…" />
        <Button disabled={busy || !name || !pw || !wif}
          onClick={() => run('wallet_import', { name, password: pw, wif })}>
          Import into “{name || '…'}”
        </Button>
      </Panel>
    </>
  )
}

// ── Send ────────────────────────────────────────────────────────────────────

// ── Shielded ────────────────────────────────────────────────────────────────

// Receive, read, and — since the prover landed — spend. The three shielded
// acts are three panels: an address to be paid at, a scan of what arrived,
// and ShieldedSend, which owns the whole prover/sync/spend ladder because
// each rung is meaningless without the one below it.
function Shielded({ caps }: { caps: any }) {
  const [wallets, setWallets] = useState<any[]>([])
  const [name, setName] = useState('')
  const [pw, setPw] = useState('')
  const [account, setAccount] = useState<any>(null)
  const [scan, setScan] = useState<any>(null)
  const [txid, setTxid] = useState('')
  const [txScan, setTxScan] = useState<any>(null)
  const [keys, setKeys] = useState<any>(null)
  const [depth, setDepth] = useState('500')
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')

  useEffect(() => {
    call('wallet_list').then(r => {
      setWallets(r.wallets || [])
      if (r.wallets?.length) setName(r.wallets[0].name)
    }).catch(() => {})
  }, [])

  useEffect(() => {
    setAccount(null); setScan(null); setKeys(null); setErr('')
    if (!name) return
    call('shielded_address', { name })
      .then(setAccount)
      .catch(e => setErr(e.message))
  }, [name])

  const run = async (label: string, fn: string, args: any, set: (v: any) => void) => {
    setBusy(label); setErr('')
    try { set(await call(fn, args)) } catch (e: any) { setErr(e.message) }
    finally { setBusy('') }
  }

  const node = caps?.node?.reachable
  const sapling = caps?.shielded_sapling

  return (
    <>
      {err && <Note kind="error">{err}</Note>}

      <Panel title="Shielded account">
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <label style={{ flex: 1, minWidth: 180 }}>
            <div style={{ fontSize: 11, color: C.dim, marginBottom: 4 }}>Wallet</div>
            <select value={name} onChange={e => setName(e.target.value)} style={{
              width: '100%', padding: '9px 11px', background: C.bg, color: C.text,
              border: `1px solid ${C.line}`, borderRadius: 6, fontSize: 13,
            }}>
              {wallets.length === 0 && <option value="">no wallets yet</option>}
              {wallets.map(w => <option key={w.name} value={w.name}>{w.name}</option>)}
            </select>
          </label>
          <div style={{ flex: 1, minWidth: 180 }}>
            <Input label="Password" type="password" value={pw}
              onChange={(e: any) => setPw(e.target.value)}
              hint="needed to read notes — a viewing key sees every payment" />
          </div>
        </div>

        {account?.addresses?.length > 0 && (
          <>
            <Field label="Unified address (give this out)" mono
              value={<>{account.receive}<Copy text={account.receive} /></>} />
            <Field label="Sapling address" mono
              value={<>{account.addresses[0].address}
                <Copy text={account.addresses[0].address} /></>} />
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 4 }}>
              <Button disabled={!pw || !!busy} onClick={() => run(
                'new', 'shielded_new_address', { name, password: pw },
                () => call('shielded_address', { name }).then(setAccount))}>
                New address
              </Button>
              <span style={{ fontSize: 11, color: C.dim, alignSelf: 'center' }}>
                account {account.account} · birthday {num(account.birthday)} ·{' '}
                {account.addresses.length} address{account.addresses.length > 1 ? 'es' : ''}
              </span>
            </div>
            <Note kind="info">
              Payments to this address are encrypted on chain: the amount, the
              memo and the recipient are visible only to a viewing key.
            </Note>
          </>
        )}
        {account && !account.addresses?.length && (
          <Note kind="warn">This wallet has no shielded account yet.</Note>
        )}
        {!account && name && <Spinner label="deriving" />}
      </Panel>

      <Panel title="Notes received" right={
        <span style={{ fontSize: 11, color: C.dim }}>
          {scan ? `${scan.scanned_blocks} blocks in ${scan.seconds}s` : ''}
        </span>
      }>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <div style={{ width: 160 }}>
            <Input label="Scan last N blocks" value={depth}
              onChange={(e: any) => setDepth(e.target.value)} />
          </div>
          <Button disabled={!name || !pw || !!busy} style={{ marginBottom: 12 }}
            onClick={() => run('scan', 'shielded_scan',
              { name, password: pw, blocks: Number(depth) }, setScan)}>
            {busy === 'scan' ? 'scanning…' : 'Scan'}
          </Button>
        </div>

        {busy === 'scan' && <Spinner label="trial-decrypting every Sapling output" />}

        {scan && (
          <>
            <div style={{ display: 'grid', gap: 10, gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))', marginBottom: 12 }}>
              <Stat label="Received" value={zec(scan.received_zec)} sub={`${scan.note_count} notes`} />
              <Stat label="Unspent"
                value={scan.unspent_zec == null ? '—' : zec(scan.unspent_zec)}
                sub={scan.spend_detection === 'nullifiers' ? 'nullifiers checked' : 'needs a node'} />
              <Stat label="Shielded txs seen" value={num(scan.shielded_transactions_seen)}
                sub={`${scan.from_height}–${scan.to_height}`} />
            </div>
            {scan.warning && <Note kind="warn">{scan.warning}</Note>}
            {scan.note && <Note kind="info">{scan.note}</Note>}
            {(scan.notes || []).map((n: any, i: number) => (
              <div key={i} style={{ borderTop: `1px solid ${C.line}`, padding: '9px 0', fontSize: 12.5 }}>
                <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                  <span style={{ color: n.direction === 'incoming' ? C.green : C.gold }}>
                    {n.direction === 'incoming' ? '← received' : '→ sent'}
                  </span>
                  <strong>{zec(n.value_zec)}</strong>
                  <span style={{ color: C.dim }}>block {num(n.height)}</span>
                  {n.spent === true && <span style={{ color: C.dim }}>spent</span>}
                </div>
                {n.memo && <div style={{ color: C.dim, marginTop: 3 }}>memo: {n.memo}</div>}
                <div style={{ color: C.dim, fontSize: 11, marginTop: 3, wordBreak: 'break-all' }}>{n.txid}</div>
              </div>
            ))}
            {scan.note_count === 0 && (
              <div style={{ fontSize: 12.5, color: C.dim }}>
                No notes for this account in that range. A shielded payment is
                only visible to its recipient, so an empty result here means
                nothing was paid to these addresses in those blocks.
              </div>
            )}
            <div style={{ fontSize: 11, color: C.dim, marginTop: 10 }}>
              Sapling only — Orchard notes are not read by this module.
            </div>
          </>
        )}
      </Panel>

      <Panel title="Open one transaction">
        <Input label="Transaction id" value={txid} placeholder="txid"
          onChange={(e: any) => setTxid(e.target.value)}
          hint="decrypts the outputs this wallet's viewing key can open" />
        <Button disabled={!txid || !name || !pw || !!busy}
          onClick={() => run('tx', 'shielded_scan_tx',
            { txid: txid.trim(), name, password: pw }, setTxScan)}>
          {busy === 'tx' ? 'reading…' : 'Read'}
        </Button>
        {txScan && (
          <div style={{ marginTop: 12 }}>
            <Field label="Sapling outputs" value={num(txScan.sapling_outputs)} />
            <Field label="Ours" value={`${txScan.found} note(s), ${zec(txScan.received_zec)}`}
              color={txScan.found ? C.green : C.dim} />
            {(txScan.notes || []).map((n: any, i: number) => (
              <Note key={i} kind="ok">
                {zec(n.value_zec)} {n.direction}{n.memo ? ` — memo: ${n.memo}` : ''}
              </Note>
            ))}
            {txScan.found === 0 && (
              <div style={{ fontSize: 12, color: C.dim }}>
                Nothing in this transaction opens with this wallet&apos;s keys.
                Its shielded outputs stay encrypted, as they should.
              </div>
            )}
          </div>
        )}
      </Panel>

      <ShieldedSend name={name} pw={pw} caps={caps} />

      <Panel title="Spend these notes somewhere else">
        <div style={{ fontSize: 12.5, color: C.dim, marginBottom: 10 }}>
          The same seed opens this account in any Zcash wallet. Export it if
          you would rather spend from Zashi, Ywallet, zingo or zcashd — the
          notes are the same notes.
        </div>
        {!keys ? (
          <Button variant="ghost" disabled={!name || !pw || !!busy}
            onClick={() => run('keys', 'shielded_export', { name, password: pw }, setKeys)}>
            Reveal spending &amp; viewing keys
          </Button>
        ) : (
          <>
            <Note kind="error">{keys.warning}</Note>
            <Field label="Extended spending key (spends everything — treat as the seed)" mono
              value={<>{keys.extended_spending_key}<Copy text={keys.extended_spending_key} /></>} />
            <Field label="Extended full viewing key (watch only)" mono
              value={<>{keys.extended_full_viewing_key}
                <Copy text={keys.extended_full_viewing_key} /></>} />
            <Field label="Derivation path" mono value={keys.path} />
            <Button variant="ghost" onClick={() => setKeys(null)}>Hide</Button>
          </>
        )}
      </Panel>
    </>
  )
}

// ── Shielded send ───────────────────────────────────────────────────────────

// Spending shielded ZEC is a ladder, and this panel is the ladder: a prover
// has to exist on the host, the wallet's light client has to have scanned to
// the tip, and only then is there a balance to spend. Showing a send form
// before the first two are true would just be a button that fails, so each
// rung renders only the one thing to do next.
function ShieldedSend({ name, pw, caps }: { name: string, pw: string, caps: any }) {
  const [backend, setBackend] = useState<any>(null)
  const [sync, setSync] = useState<any>(null)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [to, setTo] = useState('')
  const [amount, setAmount] = useState('')
  const [memo, setMemo] = useState('')
  const [preview, setPreview] = useState<any>(null)
  const [sent, setSent] = useState<any>(null)

  const loadBackend = () => call('shielded_backend').then(setBackend).catch(() => {})
  const loadSync = () => {
    if (!name) return Promise.resolve()
    return call('shielded_sync_status', { name })
      .then(setSync).catch(() => setSync(null))
  }

  useEffect(() => { loadBackend() }, [])
  useEffect(() => { setSync(null); setPreview(null); setSent(null); loadSync() }, [name])

  // While a scan is running the only honest thing to show is how far it got,
  // so poll until it stops rather than leaving a stale percentage on screen.
  useEffect(() => {
    if (!sync?.syncing) return
    const t = setInterval(loadSync, 4000)
    return () => clearInterval(t)
  }, [sync?.syncing, name])

  const install = async () => {
    setBusy('install'); setErr('')
    try {
      setBackend(await call('shielded_backend_install', {}))
    } catch (e: any) { setErr(e.message) } finally { setBusy('') }
  }

  const startSync = async () => {
    setBusy('sync'); setErr('')
    try { setSync(await call('shielded_sync_start', { name, password: pw })) }
    catch (e: any) { setErr(e.message) } finally { setBusy('') }
  }

  const send = async (broadcast: boolean) => {
    setBusy(broadcast ? 'send' : 'preview'); setErr('')
    if (broadcast) setPreview(null); else setSent(null)
    try {
      const r = await call('shielded_send', {
        name, password: pw, to: to.trim(), amount: Number(amount),
        memo: memo || undefined, broadcast,
      })
      if (broadcast) { setSent(r); loadSync() } else setPreview(r)
    } catch (e: any) { setErr(e.message) } finally { setBusy('') }
  }

  const node = caps?.node?.reachable
  const installed = backend?.installed
  const ready = sync?.initialized && sync?.synced && !sync?.syncing
  const spendable = sync?.balance?.shielded_spendable_zec

  return (
    <Panel title="Send shielded ZEC" right={
      installed ? <span style={{ fontSize: 11, color: C.green }}>prover ready</span>
        : node ? <span style={{ fontSize: 11, color: C.green }}>node</span> : null
    }>
      {err && <Note kind="error">{err}</Note>}

      {/* Rung 1 — is there a prover on this machine at all? */}
      {backend && !installed && !node && (
        <>
          <Note kind="warn">
            A shielded payment carries a zero-knowledge proof, and this machine
            has nothing that can build one yet. Installing the prover fixes
            that for good — it compiles a Zcash light client from source, once.
          </Note>
          <div style={{ fontSize: 12.5, color: C.dim, marginBottom: 10 }}>
            It takes several minutes and needs Rust on the host. Afterwards
            sending is a normal button, and no full node is involved: the
            wallet syncs compact blocks from a lightwalletd server, exactly
            like Zashi or Ywallet on a phone.
          </div>
          <Button disabled={busy === 'install'} onClick={install}>
            {busy === 'install' ? 'building the prover…' : 'Install the prover'}
          </Button>
          {busy === 'install' && (
            <div style={{ fontSize: 11.5, color: C.dim, marginTop: 8 }}>
              Compiling. This can take ten minutes; leave the tab open.
            </div>
          )}
        </>
      )}

      {/* Rung 2 — has this wallet's light client caught up with the chain? */}
      {installed && name && !ready && (
        <>
          {!sync?.initialized ? (
            <Note kind="info">
              This wallet has no light client yet. Setting one up restores the
              same seed into a scanner that tracks its notes — that is what
              makes them spendable. It needs the wallet password once.
            </Note>
          ) : sync?.syncing ? (
            <Note kind="info">
              Scanning the chain for this wallet&apos;s notes. A wallet with an
              old birthday has a lot of history to read; you can leave this.
            </Note>
          ) : (
            <Note kind="warn">
              The light client is behind the chain tip. Sync it before sending —
              a spend needs the note commitment tree right up to the top.
            </Note>
          )}

          {sync?.percent != null && (
            <>
              <div style={{
                height: 8, background: C.bg, borderRadius: 4, overflow: 'hidden',
                border: `1px solid ${C.line}`, marginBottom: 6,
              }}>
                <div style={{
                  width: `${Math.min(100, sync.percent)}%`, height: '100%',
                  background: sync.synced ? C.green : C.gold, transition: 'width .4s',
                }} />
              </div>
              <div style={{ fontSize: 11.5, color: C.dim, marginBottom: 10 }}>
                {sync.percent}% · scanned to block {num(sync.max_scanned_height)}
                {sync.blocks_remaining ? ` · ${num(sync.blocks_remaining)} blocks left` : ''}
                {sync.chain_tip_height ? ` · tip ${num(sync.chain_tip_height)}` : ''}
              </div>
            </>
          )}

          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <Button disabled={!pw || !!busy || sync?.syncing} onClick={startSync}>
              {busy === 'sync' ? 'starting…'
                : sync?.syncing ? 'scanning…'
                : sync?.initialized ? 'Sync now' : 'Set up and sync'}
            </Button>
            {sync?.syncing && (
              <Button variant="ghost"
                onClick={() => call('shielded_sync_stop', { name }).then(loadSync)}>
                Stop
              </Button>
            )}
          </div>
          {!pw && <div style={{ fontSize: 11, color: C.dim, marginTop: 8 }}>
            Enter the wallet password above first.
          </div>}
        </>
      )}

      {/* Rung 3 — the actual payment. */}
      {(ready || node) && (
        <>
          {ready && (
            <div style={{
              display: 'grid', gap: 10, marginBottom: 14,
              gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))',
            }}>
              <Stat label="Spendable" value={zec(spendable)} sub="shielded notes" />
              <Stat label="Transparent"
                value={zatToZec(sync?.balance?.transparent_spendable_zat)}
                sub="shield it to spend privately" />
              <Stat label="Synced to" value={num(sync?.max_scanned_height)}
                sub={`tip ${num(sync?.chain_tip_height)}`} />
            </div>
          )}

          <Input label="To" value={to} placeholder="zs1… or u1…"
            onChange={(e: any) => { setTo(e.target.value); setPreview(null); setSent(null) }}
            hint="a shielded or unified address — amount, memo and recipient stay encrypted" />
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            <div style={{ flex: 1, minWidth: 140 }}>
              <Input label="Amount (ZEC)" value={amount} placeholder="0.01"
                onChange={(e: any) => { setAmount(e.target.value); setPreview(null); setSent(null) }} />
            </div>
            <div style={{ flex: 2, minWidth: 180 }}>
              <Input label="Memo (optional)" value={memo}
                onChange={(e: any) => setMemo(e.target.value)}
                hint="encrypted; only the recipient can read it" />
            </div>
          </div>

          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <Button variant="ghost"
              disabled={!to || !amount || !name || !pw || !!busy}
              onClick={() => send(false)}>
              {busy === 'preview' ? 'checking…' : 'Preview'}
            </Button>
            <Button disabled={!preview || !!busy} onClick={() => send(true)}>
              {busy === 'send' ? 'proving & broadcasting…' : 'Send for real'}
            </Button>
          </div>
          {busy === 'send' && (
            <div style={{ fontSize: 11.5, color: C.dim, marginTop: 8 }}>
              Building the zero-knowledge proof. This takes a few seconds.
            </div>
          )}

          {preview && !sent && (
            <div style={{ marginTop: 14 }}>
              <Note kind="warn">
                Nothing has been sent. {zec(preview.amount_zec)} to a{' '}
                {preview.to_type} address, fee about {zec(preview.fee_zec)}.
                Press <strong>Send for real</strong> to prove and broadcast it.
              </Note>
              <Field label="To" mono value={preview.to} />
              <Field label="Spendable after" value={
                zec((preview.spendable_zec || 0) - (preview.amount_zec || 0))} />
            </div>
          )}

          {sent && (
            <div style={{ marginTop: 14 }}>
              <Note kind="ok">
                Sent {zec(sent.amount_zec)}, proved locally and broadcast in{' '}
                {sent.seconds}s. It is spendable by the recipient once mined.
              </Note>
              {sent.txid && <Field label="Transaction id" mono
                value={<>{sent.txid}<Copy text={sent.txid} /></>} />}
              <div style={{ fontSize: 11.5, color: C.dim }}>
                Sync again to pick up the change note.
              </div>
            </div>
          )}
        </>
      )}

      {!name && <div style={{ fontSize: 12.5, color: C.dim }}>
        Pick a wallet above.
      </div>}
      {!backend && <Spinner label="checking for a prover" />}
    </Panel>
  )
}

function Send() {
  const [wallets, setWallets] = useState<any[]>([])
  const [name, setName] = useState('')
  const [pw, setPw] = useState('')
  const [to, setTo] = useState('')
  const [amount, setAmount] = useState('')
  const [preview, setPreview] = useState<any>(null)
  const [sent, setSent] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    call('wallet_list').then(r => {
      setWallets(r.wallets || [])
      if (r.wallets?.length) setName(r.wallets[0].name)
    }).catch(() => {})
  }, [])

  const dryRun = async () => {
    setBusy(true); setErr(''); setPreview(null); setSent(null)
    try {
      setPreview(await call('send', {
        name, password: pw, to: to.trim(), amount: Number(amount), broadcast: false,
      }))
    } catch (e: any) { setErr(e.message) }
    finally { setBusy(false) }
  }

  const broadcast = async () => {
    setBusy(true); setErr('')
    try {
      setSent(await call('send', {
        name, password: pw, to: to.trim(), amount: Number(amount), broadcast: true,
      }))
      setPreview(null)
    } catch (e: any) { setErr(e.message) }
    finally { setBusy(false) }
  }

  return (
    <>
      {err && <Note kind="error">{err}</Note>}
      <Panel title="Send ZEC">
        <Note kind="info">
          Transparent (t-address) sends. Every send is previewed as a signed dry run
          first — nothing reaches the network until you confirm.
        </Note>
        <label style={{ display: 'block', marginBottom: 12 }}>
          <div style={{ fontSize: 11, color: C.dim, marginBottom: 4 }}>From wallet</div>
          <select value={name} onChange={e => setName(e.target.value)} style={{
            width: '100%', padding: '9px 11px', background: C.bg, color: C.text,
            border: `1px solid ${C.line}`, borderRadius: 6, fontSize: 13,
          }}>
            {wallets.length === 0 && <option value="">no wallets — create one first</option>}
            {wallets.map(w => <option key={w.name} value={w.name}>{w.name}</option>)}
          </select>
        </label>
        <Input label="Password" type="password" value={pw} onChange={(e: any) => setPw(e.target.value)} />
        <Input label="To address" value={to} onChange={(e: any) => setTo(e.target.value)}
          placeholder="t1…" hint="transparent addresses only — shielded sends need a proving backend" />
        <Input label="Amount (ZEC)" value={amount} onChange={(e: any) => setAmount(e.target.value)}
          placeholder="0.1" />
        <Button onClick={dryRun} disabled={busy || !name || !pw || !to || !amount}>
          {busy ? 'building…' : 'Preview (dry run)'}
        </Button>
      </Panel>

      {preview && (
        <Panel title="Signed — not yet broadcast">
          <Note kind="warn">
            <b>DRY RUN.</b> This transaction is fully signed and valid but has not been
            submitted. No funds have moved.
          </Note>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(140px,1fr))', gap: 10, marginBottom: 12 }}>
            <Stat label="Amount" value={zec(preview.amount_zec)} />
            <Stat label="Fee" value={zec(preview.fee_zec)} sub="ZIP-317" />
            <Stat label="Change" value={zec(preview.change_zec)} />
          </div>
          <Field label="txid (if broadcast)" value={<>{preview.txid}<Copy text={preview.txid} /></>} mono />
          <Field label="Inputs / outputs / size"
            value={`${preview.inputs} in · ${preview.outputs} out · ${preview.size_bytes} bytes`} />
          <Field label="Expires at height" value={num(preview.expiry_height)} />
          <details style={{ marginTop: 10 }}>
            <summary style={{ cursor: 'pointer', fontSize: 11, color: C.dim }}>raw transaction</summary>
            <div style={{ marginTop: 8 }}><Code>{preview.raw_transaction}</Code></div>
          </details>
          <div style={{ marginTop: 14 }}>
            <Button variant="danger" onClick={broadcast} disabled={busy}>
              {busy ? 'broadcasting…' : `Broadcast ${zec(preview.amount_zec)} for real`}
            </Button>
          </div>
        </Panel>
      )}

      {sent && (
        <Panel title="Broadcast">
          <Note kind="ok">Submitted via {sent.broadcast_via}.</Note>
          <Field label="txid" value={<>{sent.txid}<Copy text={sent.txid} /></>} mono />
          <a href={sent.explorer} target="_blank" rel="noreferrer"
            style={{ color: C.gold, fontSize: 12 }}>view on explorer →</a>
        </Panel>
      )}
    </>
  )
}

// ── Bridge ──────────────────────────────────────────────────────────────────
//
// A bridge is two legs and a direction, so it is drawn that way: what you send,
// what you get, and a flip between them. The receive leg is priced continuously
// from the asset table the router itself publishes (indicative, marked as such)
// and replaced by a real 1Click quote the moment both addresses look valid.

const EVM_RE = /^0x[0-9a-fA-F]{40}$/
const TADDR_RE = /^t[13][a-km-zA-HJ-NP-Z1-9]{20,40}$/
const POPULAR = ['eth:ETH', 'btc:BTC', 'eth:USDC', 'base:ETH', 'sol:SOL', 'near:NEAR']

type Asset = { id: string, chain: string, symbol: string, price?: number }

const ZEC_ASSET: Asset = { id: 'ZEC', chain: 'zec', symbol: 'ZEC' }

// 'eth:USDC' and 'USDC' both resolve server-side; keep whatever the user picked.
const symbolOf = (id: string) => (id.includes(':') ? id.split(':')[1] : id).toUpperCase()
const chainOf = (id: string) => (id.includes(':') ? id.split(':')[0] : '').toLowerCase()

// The router keys chains by short code; a person reads names.
const CHAIN_NAMES: Record<string, string> = {
  zec: 'Zcash', eth: 'Ethereum', base: 'Base', arb: 'Arbitrum', op: 'Optimism',
  pol: 'Polygon', bsc: 'BNB Chain', avax: 'Avalanche', sol: 'Solana', btc: 'Bitcoin',
  near: 'NEAR', ton: 'TON', tron: 'Tron', doge: 'Dogecoin', xrp: 'XRP', sui: 'Sui',
  apt: 'Aptos', ltc: 'Litecoin', bera: 'Berachain', gnosis: 'Gnosis', scroll: 'Scroll',
  zksync: 'zkSync', linea: 'Linea', monad: 'Monad', cardano: 'Cardano', bch: 'Bitcoin Cash',
  stellar: 'Stellar', aleo: 'Aleo', hyper: 'Hyperliquid',
}
const chainName = (c: string) => CHAIN_NAMES[c] || (c || '').toUpperCase()

// 188 assets sorted by chain code puts ABS and ADI at the top of the list and
// ETH 20 rows down. Rank the chains people actually bridge to first instead.
const MAJOR_CHAINS = ['eth', 'btc', 'sol', 'base', 'arb', 'near', 'pol', 'op', 'bsc',
  'avax', 'ton', 'tron', 'sui', 'apt', 'doge', 'xrp', 'ltc', 'bera']
const assetRank = (a: Asset) => {
  const pop = POPULAR.indexOf(a.id)
  if (pop >= 0) return pop
  const major = MAJOR_CHAINS.indexOf(a.chain)
  return major >= 0 ? 100 + major * 10 : 900
}

// Router amounts arrive at full token precision (0.349919587000071284 ETH); show
// enough to be exact about the size and keep the full value in the title.
function fmtAmt(v: any): string {
  const n = Number(v)
  if (v === '' || v == null || !isFinite(n)) return ''
  if (n === 0) return '0'
  const s = Math.abs(n) >= 1000
    ? n.toLocaleString(undefined, { maximumFractionDigits: 4 })
    : n.toPrecision(8)
  return s.includes('.') && !s.includes('e')
    ? s.replace(/0+$/, '').replace(/\.$/, '')
    : s
}

function AssetGlyph({ symbol, size = 30 }: { symbol: string, size?: number }) {
  // No icon set is shipped with the module, and a broken remote logo looks
  // worse than none -- so the token draws itself from its own ticker.
  const hue = [...symbol].reduce((h, ch) => (h * 31 + ch.charCodeAt(0)) % 360, 7)
  const zec = symbol === 'ZEC'
  return (
    <div style={{
      width: size, height: size, borderRadius: '50%', flexShrink: 0,
      background: zec ? C.gold : `hsl(${hue} 55% 26%)`,
      color: zec ? '#12141a' : `hsl(${hue} 85% 78%)`,
      border: `1px solid ${zec ? C.gold : `hsl(${hue} 50% 40%)`}`,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontSize: size * 0.36, fontWeight: 700, letterSpacing: -0.2,
    }}>{symbol.slice(0, zec ? 1 : 3)}</div>
  )
}

// The asset button + its dropdown. 188 assets across 35 chains is too many for
// a datalist you cannot see, so this is searchable, priced, and shows the chain.
function AssetSelect({ value, assets, onChange, locked }: {
  value: string, assets: Asset[], onChange: (id: string) => void, locked?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const sym = symbolOf(value)
  const chain = chainOf(value)

  const matches = useMemo(() => {
    const needle = q.trim().toLowerCase()
    const hit = (a: Asset) => !needle
      || a.symbol.toLowerCase().includes(needle)
      || a.chain.toLowerCase().includes(needle)
      || a.id.toLowerCase().includes(needle)
    const needleHit = (a: Asset) =>
      needle && a.symbol.toLowerCase().startsWith(needle) ? 0 : 1
    return assets.filter(hit).sort((x, y) =>
      needleHit(x) - needleHit(y) || assetRank(x) - assetRank(y)
      || x.symbol.localeCompare(y.symbol) || x.chain.localeCompare(y.chain)
    ).slice(0, 300)
  }, [assets, q])

  if (locked) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', gap: 9, padding: '7px 14px 7px 8px',
        borderRadius: 999, background: C.bg, border: `1px solid ${C.line}`,
      }}>
        <AssetGlyph symbol={sym} size={26} />
        <div>
          <div style={{ fontSize: 14, fontWeight: 700 }}>{sym}</div>
          <div style={{ fontSize: 9.5, color: C.dim, letterSpacing: 0.6 }}>Zcash</div>
        </div>
      </div>
    )
  }

  return (
    <div style={{ position: 'relative' }}>
      <button onClick={() => { setOpen(o => !o); setQ('') }} style={{
        display: 'flex', alignItems: 'center', gap: 9, cursor: 'pointer',
        padding: '7px 12px 7px 8px', borderRadius: 999, background: C.bg,
        border: `1px solid ${open ? C.gold : C.line}`, color: C.text,
      }}>
        <AssetGlyph symbol={sym} size={26} />
        <div style={{ textAlign: 'left' }}>
          <div style={{ fontSize: 14, fontWeight: 700 }}>{sym}</div>
          <div style={{ fontSize: 9.5, color: C.dim, letterSpacing: 0.6 }}>
            {chain ? chainName(chain) : 'pick a chain'}
          </div>
        </div>
        <span style={{ color: C.dim, fontSize: 10, marginLeft: 2 }}>▼</span>
      </button>

      {open && (
        <>
          <div onClick={() => setOpen(false)} style={{
            position: 'fixed', inset: 0, zIndex: 20,
          }} />
          <div style={{
            position: 'absolute', top: 'calc(100% + 8px)', right: 0, zIndex: 21,
            width: 320, maxWidth: '80vw', background: C.panel,
            border: `1px solid ${C.line}`, borderRadius: 10, padding: 10,
            boxShadow: '0 18px 40px rgba(0,0,0,.55)',
          }}>
            <input autoFocus value={q} onChange={e => setQ(e.target.value)}
              placeholder="search asset or chain…" style={{
                width: '100%', boxSizing: 'border-box', padding: '8px 10px',
                background: C.bg, border: `1px solid ${C.line}`, borderRadius: 6,
                color: C.text, fontSize: 12.5, outline: 'none', marginBottom: 8,
              }} />
            <div style={{ maxHeight: 268, overflowY: 'auto' }}>
              {matches.length === 0 && (
                <div style={{ fontSize: 11.5, color: C.dim, padding: '10px 4px' }}>
                  No asset matches “{q}”.
                </div>
              )}
              {matches.map(a => (
                <button key={a.id} onClick={() => { onChange(a.id); setOpen(false) }} style={{
                  display: 'flex', alignItems: 'center', gap: 10, width: '100%',
                  padding: '7px 8px', borderRadius: 6, cursor: 'pointer', textAlign: 'left',
                  background: a.id === value ? C.panel2 : 'transparent',
                  border: 'none', color: C.text,
                }}>
                  <AssetGlyph symbol={a.symbol} size={24} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 12.5, fontWeight: 600 }}>{a.symbol}</div>
                    <div style={{ fontSize: 10, color: C.dim }}>{chainName(a.chain)}</div>
                  </div>
                  {a.price != null && (
                    <div style={{ fontSize: 10.5, color: C.dim }}>{usd(a.price)}</div>
                  )}
                </button>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  )
}

// One side of the swap. `readOnly` legs show what the router says you get.
function Leg({ tag, amount, onAmount, assetNode, usdValue, note, readOnly, title }: {
  tag: string, amount: string, onAmount?: (v: string) => void, assetNode: ReactNode,
  usdValue?: ReactNode, note?: ReactNode, readOnly?: boolean, title?: string
}) {
  return (
    <div style={{
      background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 12,
      padding: '12px 14px',
    }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        fontSize: 10, letterSpacing: 1.1, color: C.dim, marginBottom: 4,
      }}>
        <span>{tag}</span>
        {note}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <input
            value={amount}
            readOnly={readOnly}
            title={title}
            onChange={e => onAmount?.(e.target.value)}
            inputMode="decimal"
            placeholder="0.0"
            style={{
              width: '100%', boxSizing: 'border-box', background: 'transparent',
              border: 'none', outline: 'none', padding: 0,
              color: readOnly ? C.dim : C.text, fontWeight: 600,
              // long router amounts must not push the asset chip off a phone
              fontSize: amount.length > 15 ? 19 : amount.length > 11 ? 22 : 26,
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
              letterSpacing: -0.5,
            }} />
          <div style={{ fontSize: 11, color: C.dim, marginTop: 2, height: 14 }}>
            {usdValue}
          </div>
        </div>
        {assetNode}
      </div>
    </div>
  )
}

// Address input with a live verdict dot -- an EVM recipient or a t-address is
// checkable here, so say so before the router has to reject it.
function AddrInput({ label, value, onChange, placeholder, ok, hint }: {
  label: string, value: string, onChange: (v: string) => void,
  placeholder?: string, ok: boolean | null, hint?: string
}) {
  const color = value ? (ok === false ? C.red : ok ? C.green : C.line) : C.line
  return (
    <label style={{ display: 'block' }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between',
        fontSize: 11, color: C.dim, marginBottom: 4,
      }}>
        <span>{label}</span>
        {value && ok != null && (
          <span style={{ color: ok ? C.green : C.red, fontSize: 10.5 }}>
            {ok ? 'looks valid' : 'wrong format'}
          </span>
        )}
      </div>
      <input value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder}
        spellCheck={false} style={{
          width: '100%', boxSizing: 'border-box', padding: '10px 12px',
          background: C.bg, border: `1px solid ${color}`, borderRadius: 8,
          color: C.text, fontSize: 12.5, outline: 'none',
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
        }} />
      {hint && <div style={{ fontSize: 10, color: C.dim, marginTop: 3 }}>{hint}</div>}
    </label>
  )
}

function RouteHealth() {
  const [m, setM] = useState<any>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => { call('bridge_maya').then(setM).catch(() => setFailed(true)) }, [])
  const mayaOk = m && m.available
  const pill = (label: string, ok: boolean | null, title?: string) => (
    <span title={title} style={{
      display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 10,
      color: C.dim, border: `1px solid ${C.line}`, borderRadius: 999,
      padding: '3px 9px', whiteSpace: 'nowrap',
    }}>
      <span style={{
        width: 6, height: 6, borderRadius: '50%',
        background: ok == null ? C.dim : ok ? C.green : C.red,
      }} />{label}
    </span>
  )
  return (
    <div style={{ display: 'flex', gap: 6 }}>
      {pill('NEAR Intents', true, 'primary route')}
      {pill('Maya', failed ? false : m ? !!mayaOk : null,
        m?.zec_inbound_address ? `ZEC inbound ${m.zec_inbound_address}` : 'ZEC.ZEC pool')}
    </div>
  )
}

const TRACK_STEPS = [
  { key: 'reserved', label: 'Reserved' },
  { key: 'deposit', label: 'Deposit seen' },
  { key: 'swap', label: 'Swapping' },
  { key: 'paid', label: 'Paid out' },
]

function trackStage(status: string): number {
  const s = (status || '').toUpperCase()
  if (s === 'SUCCESS') return 4
  if (s === 'PROCESSING' || s === 'PENDING_KYC') return 3
  if (s.includes('DEPOSIT') && s !== 'PENDING_DEPOSIT') return 2
  return 1
}

function Stepper({ status }: { status: string }) {
  const s = (status || '').toUpperCase()
  const bad = s === 'REFUNDED' || s === 'FAILED'
  const stage = trackStage(s)
  return (
    <div style={{ display: 'flex', alignItems: 'center', margin: '4px 0 14px' }}>
      {TRACK_STEPS.map((step, i) => {
        const done = !bad && stage > i + 1
        const now = !bad && stage === i + 1
        const c = bad ? C.red : done ? C.green : now ? C.gold : C.line
        return (
          <div key={step.key} style={{ display: 'flex', alignItems: 'center', flex: i < 3 ? 1 : 0 }}>
            <div style={{ textAlign: 'center' }}>
              <div style={{
                width: 12, height: 12, borderRadius: '50%', margin: '0 auto',
                background: done || now ? c : 'transparent', border: `2px solid ${c}`,
                boxShadow: now ? `0 0 0 4px ${c}22` : undefined,
              }} />
              <div style={{
                fontSize: 9.5, marginTop: 5, color: done || now ? C.text : C.dim,
                whiteSpace: 'nowrap',
              }}>{step.label}</div>
            </div>
            {i < 3 && <div style={{
              flex: 1, height: 2, background: done ? C.green : C.line, margin: '0 6px 16px',
            }} />}
          </div>
        )
      })}
    </div>
  )
}

function Bridge({ caps }: { caps: any }) {
  const [chains, setChains] = useState<any[]>([])
  const [dir, setDir] = useState<'out' | 'in'>('out')
  const [asset, setAsset] = useState('eth:ETH')
  const [amount, setAmount] = useState('1')
  const [recipient, setRecipient] = useState('')
  const [refund, setRefund] = useState('')
  const [quote, setQuote] = useState<any>(null)
  const [order, setOrder] = useState<any>(null)
  const [track, setTrack] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [quoting, setQuoting] = useState(false)
  const [zecUsd, setZecUsd] = useState<number | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    call('bridge_chains').then(r => setChains(r.chains || [])).catch(e => setErr(e.message))
    call('price').then((p: any) => setZecUsd(Number(p?.usd ?? p?.price_usd ?? p?.price))).catch(() => {})
  }, [])

  const assets: Asset[] = useMemo(() => chains.flatMap((c: any) =>
    c.chain === 'zec' ? [] : (c.assets || []).map((a: any) => ({
      id: `${c.chain}:${a.symbol}`, chain: c.chain, symbol: a.symbol, price: a.price_usd,
    }))), [chains])

  const picked = useMemo(
    () => assets.find(a => a.id === asset)
      || { id: asset, chain: chainOf(asset), symbol: symbolOf(asset) } as Asset,
    [assets, asset])

  const other = dir === 'out' ? picked : ZEC_ASSET   // what you receive
  const origin = dir === 'out' ? ZEC_ASSET : picked  // what you send

  // Address shape is checkable for EVM chains and for Zcash; elsewhere the
  // router is the only authority, so stay silent rather than guess.
  const verdict = (addr: string, a: Asset): boolean | null => {
    if (!addr) return null
    if (a.symbol === 'ZEC') return TADDR_RE.test(addr.trim())
    if (addr.startsWith('0x') || /^(eth|base|arb|op|pol|bsc|avax|linea|scroll|zksync|gnosis|abs)$/.test(a.chain))
      return EVM_RE.test(addr.trim())
    return null
  }
  const recipientOk = verdict(recipient, other)
  const refundOk = verdict(refund, origin)

  const args = useCallback(() => ({
    from_asset: origin.id, to_asset: other.id,
    amount: Number(amount), recipient: recipient.trim(), refund_to: refund.trim(),
  }), [origin.id, other.id, amount, recipient, refund])

  const ready = !!(Number(amount) > 0 && recipient.trim() && refund.trim()
    && recipientOk !== false && refundOk !== false)

  // Indicative price from the router's own asset table -- shown while the real
  // quote is missing or stale, and always labelled as an estimate.
  const indicative = useMemo(() => {
    const n = Number(amount)
    const inUsd = origin.symbol === 'ZEC' ? zecUsd : origin.price
    const outUsd = other.symbol === 'ZEC' ? zecUsd : other.price
    if (!n || !inUsd || !outUsd) return null
    return { out: (n * inUsd) / outUsd, usdIn: n * inUsd }
  }, [amount, origin, other, zecUsd])

  const quoteFresh = quote && quote.from === `${origin.chain}:${origin.symbol}`.replace(/^:/, '')
    && Number(quote.amount_in) === Number(amount)

  const doQuote = useCallback(async (silent = false) => {
    if (!silent) { setBusy(true); setOrder(null); setTrack(null) }
    setQuoting(true); setErr('')
    try { setQuote(await call('bridge_quote', args())) }
    catch (e: any) { if (!silent) setErr(e.message); setQuote(null) }
    finally { setBusy(false); setQuoting(false) }
  }, [args])

  // Quote as you type, once both addresses are plausible: a bridge that only
  // prices on a button press feels dead while you fill the form in.
  useEffect(() => {
    if (!ready) { setQuote(null); return }
    const t = setTimeout(() => { doQuote(true) }, 700)
    return () => clearTimeout(t)
  }, [ready, doQuote])

  const doStart = async () => {
    setBusy(true); setErr('')
    try { setOrder(await call('bridge_start', args())); setTrack(null) }
    catch (e: any) { setErr(e.message) }
    finally { setBusy(false) }
  }

  const doTrack = async (addr: string) => {
    setBusy(true); setErr('')
    try { setTrack(await call('bridge_status', { deposit_address: addr })) }
    catch (e: any) { setErr(e.message) }
    finally { setBusy(false) }
  }

  const flip = () => {
    setDir(d => (d === 'out' ? 'in' : 'out'))
    setQuote(null); setOrder(null); setTrack(null)
    setRecipient(refund); setRefund(recipient)
  }

  const receiveAmount = fmtAmt(quoteFresh ? quote.amount_out : indicative?.out)

  const rate = quoteFresh
    ? Number(quote.amount_out) / Number(quote.amount_in)
    : indicative ? indicative.out / Number(amount) : null

  const popular = POPULAR.filter(p => assets.some(a => a.id === p))

  return (
    <>
      {err && <Note kind="error">{err}</Note>}

      <Panel title="Bridge" right={<RouteHealth />}>
        <div style={{ display: 'grid', gap: 6 }}>
          <Leg
            tag={`YOU SEND · ${chainName(origin.chain).toUpperCase()}`}
            amount={amount}
            onAmount={v => setAmount(v.replace(/[^\d.]/g, ''))}
            usdValue={indicative ? usd(indicative.usdIn) : ''}
            assetNode={dir === 'out'
              ? <AssetSelect value="ZEC" assets={assets} onChange={() => {}} locked />
              : <AssetSelect value={asset} assets={assets} onChange={setAsset} />}
          />

          <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '2px 0' }}>
            <div style={{ flex: 1, height: 1, background: C.line }} />
            <button onClick={flip} title="reverse direction" style={{
              width: 32, height: 32, borderRadius: '50%', cursor: 'pointer',
              background: C.panel2, border: `1px solid ${C.line}`, color: C.gold,
              fontSize: 14, lineHeight: 1, display: 'flex',
              alignItems: 'center', justifyContent: 'center', flexShrink: 0,
            }}>↓</button>
            <div style={{ flex: 1, height: 1, background: C.line }} />
          </div>

          <Leg
            tag={`YOU RECEIVE · ${chainName(other.chain).toUpperCase()}`}
            amount={receiveAmount}
            readOnly
            title={quoteFresh ? `exactly ${quote.amount_out} ${other.symbol}` : undefined}
            note={quoting
              ? <span style={{ color: C.gold }}>pricing…</span>
              : quoteFresh
                ? <span style={{ color: C.green }}>quoted</span>
                : <span>estimate</span>}
            usdValue={quoteFresh ? usd(quote.amount_out_usd)
              : indicative ? `${usd(indicative.usdIn)} · indicative` : ''}
            assetNode={dir === 'out'
              ? <AssetSelect value={asset} assets={assets} onChange={setAsset} />
              : <AssetSelect value="ZEC" assets={assets} onChange={() => {}} locked />}
          />
        </div>

        {popular.length > 0 && (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 10 }}>
            {popular.map(p => (
              <button key={p} onClick={() => setAsset(p)} style={{
                fontSize: 11, padding: '4px 10px', borderRadius: 999, cursor: 'pointer',
                background: asset === p ? C.panel2 : 'transparent',
                border: `1px solid ${asset === p ? C.gold : C.line}`,
                color: asset === p ? C.text : C.dim,
              }}>{symbolOf(p)} <span style={{ opacity: 0.55 }}>{chainName(chainOf(p))}</span></button>
            ))}
            <span style={{ fontSize: 10.5, color: C.dim, alignSelf: 'center', marginLeft: 'auto' }}>
              {chains.length} chains · {assets.length} assets
            </span>
          </div>
        )}

        {rate != null && isFinite(rate) && (
          <div style={{
            display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'center',
            fontSize: 11.5, color: C.dim, marginTop: 12, padding: '9px 12px',
            background: C.bg, border: `1px solid ${C.line}`, borderRadius: 8,
          }}>
            <span style={{ color: C.text }}>
              1 {origin.symbol} ≈ {fmtAmt(rate)} {other.symbol}
            </span>
            {quoteFresh && <span>route <b style={{ color: C.text }}>{quote.route}</b></span>}
            {quoteFresh && quote.eta_seconds != null &&
              <span>~{Math.round(quote.eta_seconds / 60) || 1} min</span>}
            {quoteFresh && quote.price_impact_pct != null && (
              <span>impact <b style={{
                color: quote.price_impact_pct < -1 ? C.red : C.text,
              }}>{quote.price_impact_pct}%</b></span>
            )}
            {quoteFresh && quote.min_amount_out &&
              <span style={{ marginLeft: 'auto' }}>slippage guard 1%</span>}
          </div>
        )}

        <div style={{ display: 'grid', gap: 10, marginTop: 14 }}>
          <AddrInput
            label={`Recipient address on ${chainName(other.chain || other.symbol)}`}
            value={recipient} onChange={setRecipient} ok={recipientOk}
            placeholder={other.symbol === 'ZEC' ? 't1…' : '0x…'} />
          <AddrInput
            label={`Refund address on ${chainName(origin.chain || origin.symbol)}`}
            value={refund} onChange={setRefund} ok={refundOk}
            hint="Where the funds go if the swap misses its deadline."
            placeholder={origin.symbol === 'ZEC' ? 't1…' : '0x…'} />
        </div>

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 14, alignItems: 'center' }}>
          <Button onClick={doStart} disabled={busy || !ready} style={{ flex: 1, minWidth: 220 }}>
            {busy ? 'working…' : quoteFresh
              ? `Reserve deposit address for ${fmtAmt(quote.amount_out)} ${other.symbol}`
              : 'Reserve deposit address'}
          </Button>
          <Button variant="ghost" onClick={() => doQuote(false)} disabled={busy || !ready}>
            Refresh quote
          </Button>
        </div>
        {!ready && (
          <div style={{ fontSize: 11, color: C.dim, marginTop: 8 }}>
            Fill in both addresses to price the swap. Nothing moves until you fund
            the deposit address, and quoting reserves nothing.
          </div>
        )}
      </Panel>

      {order && (
        <Panel title="Deposit address reserved">
          <Note kind="warn">
            Send <b>exactly {order.amount_in} {symbolOf(order.from)}</b> to the address
            below before {order.deadline}. Anything late or short is refunded to{' '}
            {order.refund_to}.
          </Note>
          <div style={{
            background: C.bg, border: `1px solid ${C.gold}55`, borderRadius: 10,
            padding: 14, marginBottom: 12,
          }}>
            <div style={{ fontSize: 10, color: C.dim, letterSpacing: 1, marginBottom: 6 }}>
              DEPOSIT ADDRESS · {symbolOf(order.from)} ON {chainName(chainOf(order.from)).toUpperCase()}
            </div>
            <div style={{
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
              fontSize: 14, wordBreak: 'break-all', color: C.gold, lineHeight: 1.5,
            }}>{order.deposit_address}<Copy text={order.deposit_address} /></div>
          </div>
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))', gap: 10,
          }}>
            <Stat label="You send" value={`${fmtAmt(order.amount_in)} ${symbolOf(order.from)}`}
              sub={usd(order.amount_in_usd)} />
            <Stat label="You receive" value={`~${fmtAmt(order.amount_out)} ${symbolOf(order.to)}`}
              sub={usd(order.amount_out_usd)} />
            <Stat label="Paid to" value={<span style={{ fontSize: 12 }}>{order.recipient}</span>} />
          </div>
          <div style={{ marginTop: 12, display: 'flex', gap: 8 }}>
            <Button variant="ghost" onClick={() => doTrack(order.deposit_address)} disabled={busy}>
              {busy ? '…' : 'Check status'}
            </Button>
          </div>
          {order.from.startsWith('zec') && (
            <Note kind="info">
              To pay this from a wallet here, use the Send tab with this deposit
              address, or call <code>bridge_send</code> to quote and pay in one step.
            </Note>
          )}
        </Panel>
      )}

      {track && (
        <Panel title="Bridge status" right={
          <span style={{
            fontSize: 10.5, letterSpacing: 0.8, padding: '3px 10px', borderRadius: 999,
            border: `1px solid ${track.status === 'SUCCESS' ? C.green : C.gold}55`,
            color: track.status === 'SUCCESS' ? C.green : C.gold,
          }}>{track.status}</span>
        }>
          <Stepper status={track.status} />
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))', gap: 10,
            marginBottom: 12,
          }}>
            <Stat label="Deposited" value={fmtAmt(track.deposited) || '—'}
              sub={track.deposited ? undefined : 'nothing received yet'} />
            <Stat label="Received" value={fmtAmt(track.amount_out) || '—'} sub={usd(track.amount_out_usd)} />
            <Stat label="Updated" value={<span style={{ fontSize: 13 }}>
              {track.updated_at ? timeAgo(track.updated_at.replace('T', ' ').slice(0, 19)) : '—'}
            </span>} />
          </div>
          {track.origin_tx?.length > 0 &&
            <Field label="Origin tx" value={track.origin_tx.join(', ')} mono />}
          {track.destination_tx?.length > 0 &&
            <Field label="Destination tx" value={track.destination_tx.join(', ')} mono />}
          <Button variant="ghost" onClick={() => doTrack(track.deposit_address)} disabled={busy}>
            Refresh
          </Button>
        </Panel>
      )}

      {caps?.node && !caps.node.configured && (
        <Panel title="How this works">
          <div style={{ fontSize: 12.5, color: C.dim, lineHeight: 1.65 }}>
            ZEC moves across chains through solver networks — NEAR Intents (primary,
            ~35 chains) and Maya. You are given a deposit address on the origin chain;
            a solver watches it and pays the destination. Bridging <b style={{ color: C.text }}>out of</b> ZEC
            can be funded straight from a wallet on the Send tab; bridging{' '}
            <b style={{ color: C.text }}>into</b> ZEC is funded from your wallet on the origin chain.
            Quotes reserve nothing.
          </div>
        </Panel>
      )}
    </>
  )
}


// ── MCP ─────────────────────────────────────────────────────────────────────
//
// GET /mcp is the server describing itself -- tools, arguments, which ones the
// token gates, and client config for this exact endpoint. Rendering that
// document rather than a hardcoded list means the page cannot claim a tool the
// server does not serve.

function Mcp() {
  const [doc, setDoc] = useState<any>(null)
  const [err, setErr] = useState('')
  const [q, setQ] = useState('')
  const [open, setOpen] = useState<string | null>(null)
  const [probe, setProbe] = useState<any>(null)
  const [probing, setProbing] = useState(false)

  useEffect(() => {
    get('mcp').then(setDoc).catch(e => setErr(e?.message || String(e)))
  }, [])

  // Prove the endpoint answers from the browser, over the same path an agent
  // would use: a real JSON-RPC round trip, shown raw.
  const ping = async () => {
    setProbing(true)
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_BASE_PATH || ''}/api/mcp`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          jsonrpc: '2.0', id: 1, method: 'tools/call',
          params: { name: 'zec_estimate_fee', arguments: { inputs: 1, outputs: 2 } },
        }),
      })
      setProbe(await res.json())
    } catch (e: any) {
      setProbe({ error: e?.message || String(e) })
    }
    setProbing(false)
  }

  if (err) return <Note kind="error">{err}</Note>
  if (!doc) return <Panel title="MCP server"><Spinner /></Panel>

  const url: string = doc.transports?.http?.url || ''
  const tools: any[] = doc.tools || []
  const needle = q.trim().toLowerCase()
  const shown = needle
    ? tools.filter(t => (t.name + ' ' + t.description).toLowerCase().includes(needle))
    : tools
  const gated = tools.filter(t => t.auth === 'token').length

  return (
    <>
      <Panel title="MCP server" right={
        <span style={{ fontSize: 11, color: C.green }}>● serving</span>}>
        <Note kind="info">
          Every function of this module is also an MCP tool, over the same code
          the console calls. Point an agent at the endpoint below, or run it on
          this box over stdio.
        </Note>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10, marginBottom: 14 }}>
          <Stat label="Tools" value={doc.count} sub={`${gated} need the token`} />
          <Stat label="Transport" value="HTTP + stdio" sub="JSON-RPC 2.0" />
          <Stat label="Protocol" value={doc.protocol?.default} sub="Streamable HTTP" />
        </div>
        <Field label="Endpoint" mono value={<>{url}<Copy text={url} /></>} />
        <Field label="stdio" mono value={
          <>{doc.transports?.stdio?.command}<Copy text={doc.transports?.stdio?.command || ''} /></>} />
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 10 }}>
          <Button variant="ghost" onClick={ping} disabled={probing}>
            {probing ? 'calling…' : 'Call a tool from this page'}
          </Button>
          <span style={{ fontSize: 11, color: C.dim }}>
            runs zec_estimate_fee over the endpoint above
          </span>
        </div>
        {probe && <div style={{ marginTop: 10 }}><Code>{JSON.stringify(probe, null, 2)}</Code></div>}
      </Panel>

      <Panel title="Connect a client">
        {[['Claude Code', doc.config?.claude_cli],
          ['mcp.json (http)', JSON.stringify(doc.config?.http, null, 2)],
          ['mcp.json (stdio)', JSON.stringify(doc.config?.stdio, null, 2)],
          ['curl', doc.config?.curl]].map(([label, text]: any) => text && (
            <div key={label} style={{ marginBottom: 12 }}>
              <div style={{ fontSize: 11, color: C.dim, marginBottom: 4 }}>
                {label}<Copy text={text} />
              </div>
              <Code>{text}</Code>
            </div>
          ))}
        <Note kind="warn">
          {doc.auth?.token}. Reads — the explorer, wallet addresses and
          balances, bridge quotes — need no token at all.
        </Note>
      </Panel>

      <Panel title={`Tools (${shown.length}${shown.length !== tools.length ? ` of ${tools.length}` : ''})`}
        right={
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="filter"
            style={{
              padding: '5px 9px', background: C.bg, border: `1px solid ${C.line}`,
              borderRadius: 5, color: C.text, fontSize: 12, outline: 'none', width: 140,
            }} />
        }>
        {shown.map(t => {
          const props = t.inputSchema?.properties || {}
          const required: string[] = t.inputSchema?.required || []
          const isOpen = open === t.name
          return (
            <div key={t.name} style={{ borderTop: `1px solid ${C.line}`, padding: '9px 0' }}>
              <div onClick={() => setOpen(isOpen ? null : t.name)}
                style={{ display: 'flex', gap: 10, alignItems: 'baseline', cursor: 'pointer' }}>
                <code style={{ fontSize: 12.5, color: C.gold, minWidth: 190 }}>{t.name}</code>
                <span style={{
                  fontSize: 9.5, letterSpacing: 0.6, textTransform: 'uppercase',
                  color: t.auth === 'token' ? C.gold : C.green,
                  border: `1px solid ${t.auth === 'token' ? C.gold : C.green}55`,
                  borderRadius: 3, padding: '1px 5px', whiteSpace: 'nowrap',
                }}>{t.auth === 'token' ? 'token' : 'open'}</span>
                <span style={{ fontSize: 12, color: C.dim, flex: 1, lineHeight: 1.45 }}>
                  {isOpen ? t.description : t.description.split('. ')[0] + '.'}
                </span>
              </div>
              {isOpen && (
                <div style={{ marginTop: 8, paddingLeft: 4 }}>
                  {Object.keys(props).length === 0 && (
                    <div style={{ fontSize: 11.5, color: C.dim }}>no arguments</div>
                  )}
                  {Object.entries(props).map(([name, spec]: any) => (
                    <div key={name} style={{ display: 'flex', gap: 8, fontSize: 11.5, padding: '3px 0' }}>
                      <code style={{ color: C.text, minWidth: 130 }}>
                        {name}
                        {required.includes(name) &&
                          <span style={{ color: C.red }}> *</span>}
                      </code>
                      <span style={{ color: C.dim, minWidth: 52 }}>{spec.type}</span>
                      <span style={{ color: C.dim, flex: 1 }}>{spec.description}</span>
                    </div>
                  ))}
                  <div style={{ fontSize: 10.5, color: C.dim, marginTop: 6 }}>
                    module fn: {t.fns?.join(', ')}
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </Panel>

      <Panel title="What an agent is told">
        <Code>{doc.instructions}</Code>
      </Panel>
    </>
  )
}

// ── Capabilities footer ─────────────────────────────────────────────────────

function Capabilities({ caps }: { caps: any }) {
  return (
    <Panel title="Capabilities">
      {Object.entries(caps)
        // The shielded entries are not a single yes/no: receiving and reading
        // notes work, sending them does not, and saying "supported: false"
        // would hide half of what the module actually does.
        .filter(([, v]: any) => v && typeof v === 'object'
          && ['supported', 'receive', 'read', 'send'].some(k => k in v))
        .map(([k, v]: any) => {
          const parts = ['receive', 'read', 'send'].filter(p => p in v)
          const on = 'supported' in v ? v.supported : parts.some(p => v[p])
          return (
            <div key={k} style={{ display: 'flex', gap: 10, padding: '7px 0', fontSize: 12.5, borderTop: `1px solid ${C.line}` }}>
              <span style={{ color: on ? C.green : C.dim, width: 14 }}>{on ? '●' : '○'}</span>
              <span style={{ minWidth: 150, color: C.text }}>
                {k.replace(/_/g, ' ')}
                {parts.length > 0 && (
                  <span style={{ display: 'block', fontSize: 10.5, color: C.dim, marginTop: 2 }}>
                    {parts.map(p => `${v[p] ? '✓' : '✗'} ${p}`).join('  ')}
                  </span>
                )}
              </span>
              <span style={{ color: C.dim, flex: 1 }}>
                {v.details || v.reason}
                {v.cannot && <span style={{ display: 'block', marginTop: 3 }}>{v.cannot}</span>}
              </span>
            </div>
          )
        })}
      <div style={{ fontSize: 11, color: C.dim, marginTop: 12 }}>{caps.safety}</div>
    </Panel>
  )
}
