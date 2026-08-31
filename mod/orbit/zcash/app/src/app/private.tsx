'use client'

// PRIVATE BRIDGE — the shielded half of the BRIDGE tab.
//
// It is a separate surface from the ordinary bridge because the two directions
// are not symmetric and pretending otherwise is how someone unshields by
// accident. Inbound works with nothing extra and lands encrypted; outbound
// needs a proving node and is public at the moment it leaves the pool. The UI
// says which of those you are looking at at all times, and the outbound side
// leads with the privacy cost rather than burying it under the quote.
//
// Every claim here comes from bridge_shielded_plan rather than being hardcoded,
// so a deployment that later configures ZCASH_RPC_URL stops being told it
// cannot send.

import { useEffect, useState } from 'react'
import { call, usd } from './api'
import { Button, C, Code, Copy, Field, Input, Note, Panel, Spinner, Stat } from './ui'

type Dir = 'in' | 'out'

export function PrivateBridge({ caps }: { caps: any }) {
  const [dir, setDir] = useState<Dir>('in')
  const [plan, setPlan] = useState<any>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    call('bridge_shielded_plan').then(setPlan).catch(e => setErr(e.message))
  }, [])

  return (
    <>
      {err && <Note kind="error">{err}</Note>}

      <Panel title="Private bridge">
        <Note kind="info">
          Money can arrive in your shielded pool straight off a bridge — no
          transparent hop, no second transaction. Leaving it cannot be private:
          the value has to become transparent to exit Zcash at all. Both are
          below, labelled honestly.
        </Note>

        <div style={{ display: 'flex', gap: 6, marginBottom: 4, flexWrap: 'wrap' }}>
          <DirTab active={dir === 'in'} onClick={() => setDir('in')}
            title="Other chain → shielded ZEC"
            note={plan?.in?.supported === false ? 'unavailable' : 'private'}
            ok={plan?.in?.supported !== false} />
          <DirTab active={dir === 'out'} onClick={() => setDir('out')}
            title="Shielded ZEC → other chain"
            note={plan?.out?.supported ? 'node ready' : 'needs a proving wallet'}
            ok={!!plan?.out?.supported} />
        </div>
      </Panel>

      {!plan && <Panel><Spinner label="checking what this deployment can do" /></Panel>}

      {plan && dir === 'in' && <BridgeIn plan={plan} />}
      {plan && dir === 'out' && <BridgeOut plan={plan} caps={caps} />}

      {plan && <PrivacyCard privacy={plan.privacy?.[dir]} />}
    </>
  )
}

function DirTab({ active, onClick, title, note, ok }: {
  active: boolean, onClick: () => void, title: string, note: string, ok: boolean,
}) {
  return (
    <button onClick={onClick} style={{
      flex: 1, minWidth: 200, textAlign: 'left', cursor: 'pointer',
      padding: '10px 13px', borderRadius: 7,
      background: active ? C.panel2 : 'transparent',
      border: `1px solid ${active ? C.gold : C.line}`,
      color: active ? C.text : C.dim,
    }}>
      <div style={{ fontSize: 12.5, fontWeight: 620 }}>{title}</div>
      <div style={{ fontSize: 10.5, color: ok ? C.green : C.gold, marginTop: 3 }}>
        {note}
      </div>
    </button>
  )
}

// ── inbound ─────────────────────────────────────────────────────────────────

function BridgeIn({ plan }: { plan: any }) {
  const [asset, setAsset] = useState('eth:USDC')
  const [amount, setAmount] = useState('100')
  const [recipient, setRecipient] = useState('')
  const [wallet, setWallet] = useState('')
  const [refund, setRefund] = useState('')
  const [target, setTarget] = useState<any>(null)
  const [quote, setQuote] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const checkAddress = async () => {
    setErr(''); setTarget(null)
    try {
      setTarget(await call('bridge_shielded_address',
        recipient.trim() ? { address: recipient.trim() } : { name: wallet.trim() }))
    } catch (e: any) { setErr(e.message) }
  }

  const go = async (reserve: boolean) => {
    setBusy(true); setErr(''); if (!reserve) setQuote(null)
    try {
      const args: any = {
        from_asset: asset, amount: Number(amount), refund_to: refund.trim(), reserve,
      }
      if (recipient.trim()) args.recipient = recipient.trim()
      else args.name = wallet.trim()
      setQuote(await call('bridge_shielded_in', args))
    } catch (e: any) { setErr(e.message) }
    finally { setBusy(false) }
  }

  const ready = amount && refund.trim() && (recipient.trim() || wallet.trim())

  return (
    <>
      {err && <Note kind="error">{err}</Note>}

      <Panel title="Bridge into your shielded pool">
        <Note kind="ok">
          {plan.in?.how}
        </Note>

        <Input label="Pay with" value={asset}
          onChange={(e: any) => setAsset(e.target.value)}
          placeholder="eth:USDC, ETH, BTC, base:ETH…"
          hint="the asset you are sending from the other chain" />

        <Input label={`Amount (${asset})`} value={amount}
          onChange={(e: any) => setAmount(e.target.value)} />

        <Input label="Your shielded address" value={recipient}
          onChange={(e: any) => setRecipient(e.target.value)}
          placeholder="zs1… or u1…"
          hint="where the ZEC lands. A bare zs1 is fine — it gets wrapped." />

        <Input label="…or a wallet name" value={wallet}
          onChange={(e: any) => setWallet(e.target.value)}
          placeholder="my-wallet"
          hint="uses that wallet's own shielded address instead" />

        <Input label="Refund address on the origin chain" value={refund}
          onChange={(e: any) => setRefund(e.target.value)}
          placeholder="0x…"
          hint="where the money returns if the swap fails. It cannot be a Zcash address — a refund is paid on the chain you sent from." />

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <Button variant="ghost" onClick={checkAddress}
            disabled={!recipient.trim() && !wallet.trim()}>
            Check my address
          </Button>
          <Button onClick={() => go(false)} disabled={busy || !ready}>
            {busy ? '…' : 'Get quote'}
          </Button>
          <Button variant="ghost" onClick={() => go(true)} disabled={busy || !ready}>
            Reserve deposit address
          </Button>
        </div>
      </Panel>

      {target && <TargetCard target={target} />}

      {quote && (
        <Panel title={quote.mode === 'RESERVED' ? 'Deposit address reserved' : 'Quote — nothing reserved'}>
          <div style={{
            display: 'grid', gap: 10, marginBottom: 12,
            gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))',
          }}>
            <Stat label="You send" value={`${quote.amount_in}`}
              sub={`${quote.from} · ${usd(quote.amount_in_usd)}`} />
            <Stat label="Lands shielded" value={`${quote.amount_out} ZEC`}
              sub={usd(quote.amount_out_usd)} />
            <Stat label="ETA" value={`${quote.eta_seconds}s`}
              sub={`pool: ${quote.destination_pool}`} />
          </div>

          {quote.recipient_rewritten && (
            <Note kind="warn">{quote.recipient_note}</Note>
          )}

          {quote.mode === 'RESERVED' ? (
            <>
              <Note kind="warn">
                Send <b>exactly {quote.amount_in} {quote.from}</b> to the address
                below before {quote.deadline}. It is on {String(quote.from).split(':')[0]},
                not on Zcash, so this module cannot pay it for you.
              </Note>
              <Field label="Deposit address" mono
                value={<>{quote.deposit_address}<Copy text={quote.deposit_address} /></>} />
              <Field label="Arrives at" mono value={quote.recipient} />
              <Note kind="info">
                Once it lands it is an encrypted note — nothing will show in an
                explorer. Find it with a shielded scan covering the blocks around
                its arrival.
              </Note>
            </>
          ) : (
            <Note kind="info">{quote.note}</Note>
          )}
        </Panel>
      )}
    </>
  )
}

function TargetCard({ target }: { target: any }) {
  return (
    <Panel title="The address the bridge will be given">
      <Field label="What you gave" value={target.given} mono />
      <Field label="What gets used" value={<>{target.recipient}<Copy text={target.recipient} /></>} mono
        color={target.rewritten ? C.gold : C.green} />
      <Field label="Pools it can be paid into" value={(target.pools || [target.pool]).join(', ')} />
      {target.rewritten
        ? <Note kind="warn">{target.why}</Note>
        : <Note kind="ok">{target.why}</Note>}
      {target.unreadable_receivers?.length > 0 && (
        <Note kind="error">
          A {target.unreadable_receivers.join(', ')} receiver was removed because
          this module cannot decrypt notes in that pool. Funds paid there would
          be real, confirmed, and invisible to every balance shown here.
        </Note>
      )}
    </Panel>
  )
}

// ── outbound ────────────────────────────────────────────────────────────────

function BridgeOut({ plan, caps }: { plan: any, caps: any }) {
  const [wallet, setWallet] = useState('')
  const [password, setPassword] = useState('')
  const [asset, setAsset] = useState('ETH')
  const [amount, setAmount] = useState('0.5')
  const [recipient, setRecipient] = useState('')
  const [result, setResult] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const go = async (broadcast: boolean) => {
    setBusy(true); setErr(''); setResult(null)
    try {
      setResult(await call('bridge_shielded_out', {
        name: wallet.trim(), password, to_asset: asset,
        amount: Number(amount), recipient: recipient.trim(), broadcast,
      }))
    } catch (e: any) { setErr(e.message) }
    finally { setBusy(false) }
  }

  const ready = wallet.trim() && password && amount && recipient.trim()

  return (
    <>
      {err && <Note kind="error">{err}</Note>}

      <Panel title="Bridge out of the shielded pool">
        <Note kind="warn">
          <b>This is the public direction.</b> The solver&apos;s deposit address
          is an ordinary t-address, so the amount is in the clear the moment it
          leaves the pool and links to your destination address by timing. The
          spend still hides <i>which</i> notes paid. If the link matters,
          unshield to a fresh t-address, wait, and bridge from there instead.
        </Note>

        {!plan.out?.supported && (
          <Note kind="error">
            No proving node is configured, so this module cannot spend a
            shielded note itself. It will still reserve the deposit address and
            hand you the exact payment to make from Zashi, Ywallet or zingo —
            that is a completable swap, not a failure. {plan.out?.needs}.
          </Note>
        )}

        <Input label="Wallet" value={wallet}
          onChange={(e: any) => setWallet(e.target.value)} placeholder="my-wallet" />
        <Input label="Password" type="password" value={password}
          onChange={(e: any) => setPassword(e.target.value)} />
        <Input label="Receive asset" value={asset}
          onChange={(e: any) => setAsset(e.target.value)}
          placeholder="ETH, eth:USDC, BTC…" />
        <Input label="Amount (ZEC from your shielded notes)" value={amount}
          onChange={(e: any) => setAmount(e.target.value)} />
        <Input label="Recipient on the destination chain" value={recipient}
          onChange={(e: any) => setRecipient(e.target.value)} placeholder="0x…" />

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <Button onClick={() => go(false)} disabled={busy || !ready}>
            {busy ? '…' : 'Dry run'}
          </Button>
          <Button variant="danger" onClick={() => go(true)} disabled={busy || !ready}>
            {plan.out?.supported ? 'Reserve and send' : 'Reserve deposit address'}
          </Button>
        </div>
      </Panel>

      {result && (
        <Panel title={result.mode}>
          <Note kind={result.mode === 'BROADCAST' ? 'ok'
            : result.mode === 'DRY RUN' ? 'info' : 'warn'}>
            {result.note}
          </Note>

          {result.bridge && (
            <div style={{
              display: 'grid', gap: 10, margin: '12px 0',
              gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))',
            }}>
              <Stat label="You send" value={`${result.bridge.amount_in} ZEC`}
                sub={usd(result.bridge.amount_in_usd)} />
              <Stat label="You receive" value={`${result.bridge.amount_out}`}
                sub={`${result.bridge.to} · ${usd(result.bridge.amount_out_usd)}`} />
              <Stat label="Proving" value={result.proving === 'node' ? 'node' : 'you'}
                sub={result.bridge.eta_seconds ? `${result.bridge.eta_seconds}s eta` : ''} />
            </div>
          )}

          {result.manual_payment && (
            <>
              <Field label="Pay from (your shielded address)" mono
                value={result.manual_payment.from} />
              <Field label="Pay to (deposit address)" mono
                value={<>{result.manual_payment.to}<Copy text={result.manual_payment.to} /></>} />
              <Field label="Exact amount" value={`${result.manual_payment.amount_zec} ZEC`} />
              <Field label="Before" value={result.manual_payment.before} />
            </>
          )}

          {result.how && (
            <div style={{ marginTop: 12 }}>
              <div style={{
                fontSize: 11, color: C.dim, textTransform: 'uppercase',
                letterSpacing: 1, marginBottom: 8,
              }}>How to finish it</div>
              <ol style={{ margin: 0, paddingLeft: 20 }}>
                {result.how.map((step: string, i: number) => (
                  <li key={i} style={{
                    fontSize: 12.5, lineHeight: 1.6, color: C.text, marginBottom: 7,
                  }}>{step}</li>
                ))}
              </ol>
            </div>
          )}

          {result.payment?.operation_id && (
            <Field label="Node operation" mono value={result.payment.operation_id} />
          )}
        </Panel>
      )}
    </>
  )
}

// ── privacy ─────────────────────────────────────────────────────────────────

function PrivacyCard({ privacy }: { privacy: any }) {
  if (!privacy) return null
  const grade = privacy.grade === 'good' ? C.green : C.gold
  return (
    <Panel title="What this direction hides"
      right={<span style={{
        fontSize: 10, letterSpacing: 1, textTransform: 'uppercase', color: grade,
      }}>{privacy.grade}</span>}>
      <div style={{ fontSize: 12, color: C.dim, marginBottom: 12 }}>
        {privacy.direction}
      </div>
      <PrivacyList label="Hidden" items={privacy.hidden} color={C.green} />
      <PrivacyList label="Still visible" items={privacy.visible} color={C.red} />
      <PrivacyList label="Do better" items={privacy.better} color={C.blue} />
      {privacy.note && <Note kind="info">{privacy.note}</Note>}
    </Panel>
  )
}

function PrivacyList({ label, items, color }: {
  label: string, items?: string[], color: string,
}) {
  if (!items?.length) return null
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{
        fontSize: 10.5, letterSpacing: 1, textTransform: 'uppercase',
        color, marginBottom: 5,
      }}>{label}</div>
      {items.map((t, i) => (
        <div key={i} style={{
          fontSize: 12.5, lineHeight: 1.6, color: C.text, marginBottom: 5,
          paddingLeft: 11, borderLeft: `2px solid ${color}44`,
        }}>{t}</div>
      ))}
    </div>
  )
}
