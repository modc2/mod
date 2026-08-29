'use client'

import { useCallback, useEffect, useState } from 'react'
import { call, get, getToken, num, setToken, timeAgo, usd, zatToZec, zec } from './api'
import { Button, C, Code, Copy, Field, Input, Note, Panel, Spinner, Stat } from './ui'

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

type Tab = 'explorer' | 'wallet' | 'shielded' | 'send' | 'bridge' | 'mcp'
const TABS: Tab[] = ['explorer', 'wallet', 'shielded', 'send', 'bridge', 'mcp']

export default function Page() {
  const [tab, setTab] = useState<Tab>('explorer')
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

        {tab !== 'explorer' && <Unlock />}
        {tab === 'explorer' && <Explorer online={online} />}
        {tab === 'wallet' && <Wallet />}
        {tab === 'shielded' && <Shielded caps={caps} />}
        {tab === 'send' && <Send />}
        {tab === 'bridge' && <Bridge caps={caps} />}
        {tab === 'mcp' && <Mcp />}

        {caps && tab !== 'mcp' && <Capabilities caps={caps} />}
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

// What this tab can honestly offer: a real Sapling address to receive on, and
// the viewing keys that read what arrives. Spending needs a Groth16 prover,
// which lives in a node or another wallet -- the panel says so rather than
// offering a button that cannot work.
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

      <Panel title="Sending shielded ZEC">
        <Note kind={node ? 'ok' : 'warn'}>
          {node
            ? 'A Zcash node is configured, so shielded_send can ask it to prove '
              + 'and broadcast a spend. Run shielded_node_import once first.'
            : 'This module cannot create a shielded spend: that needs a Groth16 '
              + 'proof, which is not feasible in pure Python. Export the '
              + 'spending key below into Zashi, Ywallet, zingo or zcashd and '
              + 'send from there — or set ZCASH_RPC_URL to a node.'}
        </Note>
        {sapling?.cannot && (
          <div style={{ fontSize: 11.5, color: C.dim, marginBottom: 10 }}>{sapling.cannot}</div>
        )}
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

function Bridge({ caps }: { caps: any }) {
  const [chains, setChains] = useState<any[]>([])
  const [dir, setDir] = useState<'out' | 'in'>('out')
  const [asset, setAsset] = useState('ETH')
  const [amount, setAmount] = useState('1')
  const [recipient, setRecipient] = useState('')
  const [refund, setRefund] = useState('')
  const [quote, setQuote] = useState<any>(null)
  const [order, setOrder] = useState<any>(null)
  const [track, setTrack] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    call('bridge_chains').then(r => setChains(r.chains || [])).catch(e => setErr(e.message))
  }, [])

  const args = () => dir === 'out'
    ? { from_asset: 'ZEC', to_asset: asset, amount: Number(amount), recipient, refund_to: refund }
    : { from_asset: asset, to_asset: 'ZEC', amount: Number(amount), recipient, refund_to: refund }

  const doQuote = async () => {
    setBusy(true); setErr(''); setQuote(null); setOrder(null); setTrack(null)
    try { setQuote(await call('bridge_quote', args())) }
    catch (e: any) { setErr(e.message) }
    finally { setBusy(false) }
  }

  const doStart = async () => {
    setBusy(true); setErr('')
    try { setOrder(await call('bridge_start', args())); setQuote(null) }
    catch (e: any) { setErr(e.message) }
    finally { setBusy(false) }
  }

  const doTrack = async (addr: string) => {
    setBusy(true); setErr('')
    try { setTrack(await call('bridge_status', { deposit_address: addr })) }
    catch (e: any) { setErr(e.message) }
    finally { setBusy(false) }
  }

  const options = chains.flatMap(c =>
    c.chain === 'zec' ? [] : c.assets.map((a: any) => `${c.chain}:${a.symbol}`))

  return (
    <>
      {err && <Note kind="error">{err}</Note>}
      <Panel title="Bridge">
        <Note kind="info">
          ZEC moves across chains through solver networks (NEAR Intents, Maya) — you
          get a deposit address and the solver pays the destination. Bridging{' '}
          <b>out of</b> ZEC can be paid straight from a wallet here; bridging{' '}
          <b>into</b> ZEC is funded from your wallet on the origin chain.
        </Note>

        <div style={{ display: 'flex', gap: 6, marginBottom: 14 }}>
          {(['out', 'in'] as const).map(d => (
            <button key={d} onClick={() => { setDir(d); setQuote(null); setOrder(null) }} style={{
              padding: '6px 13px', borderRadius: 6, fontSize: 12, cursor: 'pointer',
              background: dir === d ? C.panel2 : 'transparent',
              border: `1px solid ${dir === d ? C.gold : C.line}`,
              color: dir === d ? C.text : C.dim,
            }}>{d === 'out' ? 'ZEC → other chain' : 'other chain → ZEC'}</button>
          ))}
        </div>

        <label style={{ display: 'block', marginBottom: 12 }}>
          <div style={{ fontSize: 11, color: C.dim, marginBottom: 4 }}>
            {dir === 'out' ? 'Receive asset' : 'Pay with'}
          </div>
          <input list="assets" value={asset} onChange={e => setAsset(e.target.value)}
            placeholder="ETH, eth:USDC, BTC, base:ETH…" style={{
              width: '100%', boxSizing: 'border-box', padding: '9px 11px', background: C.bg,
              border: `1px solid ${C.line}`, borderRadius: 6, color: C.text, fontSize: 13,
              fontFamily: 'ui-monospace, Menlo, monospace',
            }} />
          <datalist id="assets">{options.map(o => <option key={o} value={o} />)}</datalist>
          <div style={{ fontSize: 10, color: C.dim, marginTop: 3 }}>
            {chains.length} chains available
          </div>
        </label>

        <Input label={`Amount (${dir === 'out' ? 'ZEC' : asset})`} value={amount}
          onChange={(e: any) => setAmount(e.target.value)} />
        <Input label={dir === 'out' ? `Recipient address on ${asset.split(':')[0] || asset}` : 'Your Zcash t-address'}
          value={recipient} onChange={(e: any) => setRecipient(e.target.value)}
          placeholder={dir === 'out' ? '0x…' : 't1…'} />
        <Input label={dir === 'out' ? 'Refund t-address (if the swap fails)' : `Refund address on ${asset.split(':')[0] || asset}`}
          value={refund} onChange={(e: any) => setRefund(e.target.value)}
          placeholder={dir === 'out' ? 't1…' : '0x…'} />

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <Button onClick={doQuote} disabled={busy || !recipient || !refund || !amount}>
            {busy ? '…' : 'Get quote'}
          </Button>
          <Button variant="ghost" onClick={doStart} disabled={busy || !recipient || !refund || !amount}>
            Reserve deposit address
          </Button>
        </div>
      </Panel>

      {quote && (
        <Panel title="Quote (nothing reserved)">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))', gap: 10, marginBottom: 12 }}>
            <Stat label="You send" value={`${quote.amount_in} ${quote.from}`} sub={usd(quote.amount_in_usd)} />
            <Stat label="You receive" value={`${quote.amount_out}`} sub={`${quote.to} · ${usd(quote.amount_out_usd)}`} />
            <Stat label="ETA" value={`${quote.eta_seconds}s`} sub={`slippage ${quote.price_impact_pct ?? '—'}%`} />
          </div>
          <Field label="Route" value={quote.route} />
        </Panel>
      )}

      {order && (
        <Panel title="Deposit address reserved">
          <Note kind="warn">
            Send <b>exactly {order.amount_in} {order.from}</b> to the address below
            before {order.deadline}. Anything late or short is refunded to {order.refund_to}.
          </Note>
          <Field label="Deposit address"
            value={<>{order.deposit_address}<Copy text={order.deposit_address} /></>} mono />
          <Field label="You receive" value={`~${order.amount_out} ${order.to} at ${order.recipient}`} />
          <div style={{ marginTop: 12, display: 'flex', gap: 8 }}>
            <Button variant="ghost" onClick={() => doTrack(order.deposit_address)} disabled={busy}>
              Check status
            </Button>
          </div>
          {order.route === 'near-intents' && order.from.startsWith('zec') && (
            <Note kind="info">
              To pay this from a wallet here, use the Send tab with this deposit
              address, or call <code>bridge_send</code> to quote and pay in one step.
            </Note>
          )}
        </Panel>
      )}

      {track && (
        <Panel title="Bridge status">
          <Field label="Status" value={track.status}
            color={track.status === 'SUCCESS' ? C.green : C.gold} />
          <Field label="Deposited" value={track.deposited ?? '— nothing received yet'} />
          <Field label="Received" value={track.amount_out ?? '—'} />
          {track.destination_tx?.length > 0 &&
            <Field label="Destination tx" value={track.destination_tx.join(', ')} mono />}
        </Panel>
      )}

      {caps?.node && !caps.node.configured && (
        <Panel title="Maya route">
          <MayaStatus />
        </Panel>
      )}
    </>
  )
}

function MayaStatus() {
  const [m, setM] = useState<any>(null)
  useEffect(() => { call('bridge_maya').then(setM).catch(() => {}) }, [])
  if (!m) return <Spinner />
  return (
    <>
      <Field label="Availability"
        value={m.available ? 'open' : 'halted — quotes will fail'}
        color={m.available ? C.green : C.red} />
      {m.zec_inbound_address && <Field label="ZEC inbound" value={m.zec_inbound_address} mono />}
      {m.halted_chains?.length > 0 &&
        <Field label="Halted chains" value={m.halted_chains.join(', ')} />}
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
