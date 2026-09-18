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
import { PayWithWallet, UseWallet, WalletChip, useMetaMask } from './wallet'

type Dir = 'in' | 'out'

export function PrivateBridge({ caps }: { caps: any }) {
  const [dir, setDir] = useState<Dir>('in')
  const [plan, setPlan] = useState<any>(null)
  const [err, setErr] = useState('')
  // Set when the last inbound quote came back on the public fallback route.
  // The privacy card describes the DIRECT route, and leaving it graded "good"
  // next to a quote that lands in the clear is the same lie twice.
  const [degraded, setDegraded] = useState(false)

  useEffect(() => {
    call('bridge_shielded_plan').then(setPlan).catch(e => setErr(e.message))
  }, [])

  return (
    <>
      {err && <Note kind="error">{err}</Note>}

      <Panel title="Private bridge">
        <Note kind="info">
          Money can arrive in your shielded pool straight off a bridge — no
          transparent hop, no second transaction — whenever the router will pay
          a z-address. Leaving the pool cannot be private: the value has to
          become transparent to exit Zcash at all. Both are below, labelled
          honestly, and a quote that is not the private route says so on its
          face.
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

      {plan && dir === 'in' && <BridgeIn plan={plan} onRoute={setDegraded} />}
      {plan && dir === 'out' && <BridgeOut plan={plan} caps={caps} />}

      {plan && <PrivacyCard privacy={plan.privacy?.[dir]}
        degraded={dir === 'in' && degraded} />}
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

function BridgeIn({ plan, onRoute }: { plan: any, onRoute: (d: boolean) => void }) {
  const [asset, setAsset] = useState('eth:USDC')
  const [amount, setAmount] = useState('100')
  const [recipient, setRecipient] = useState('')
  const [wallet, setWallet] = useState('')
  const [refund, setRefund] = useState('')
  const [viaT, setViaT] = useState('')
  const [target, setTarget] = useState<any>(null)
  const [quote, setQuote] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  // The inbound leg is paid on the ORIGIN chain, which is usually an EVM. This
  // module cannot sign there and never will -- but the wallet in this browser
  // can, on a transaction the module builds down to the calldata.
  const mm = useMetaMask()

  const checkAddress = async () => {
    setErr(''); setTarget(null)
    try {
      setTarget(await call('bridge_shielded_address',
        recipient.trim() ? { address: recipient.trim() } : { name: wallet.trim() }))
    } catch (e: any) { setErr(e.message) }
  }

  const go = async (reserve: boolean, acceptPublicLeg = false) => {
    setBusy(true); setErr(''); if (!reserve) setQuote(null)
    try {
      const args: any = {
        from_asset: asset, amount: Number(amount), refund_to: refund.trim(), reserve,
      }
      if (acceptPublicLeg) args.accept_public_leg = true
      if (recipient.trim()) args.recipient = recipient.trim()
      else args.name = wallet.trim()
      if (viaT.trim()) args.via_transparent = viaT.trim()
      const q = await call('bridge_shielded_in', args)
      onRoute(q?.shielded === false)
      setQuote(q)
    } catch (e: any) { setErr(e.message); onRoute(false) }
    finally { setBusy(false) }
  }

  const ready = amount && refund.trim() && (recipient.trim() || wallet.trim())

  return (
    <>
      {err && <Note kind="error">{err}</Note>}

      <Panel title="Bridge into your shielded pool" right={<WalletChip mm={mm} compact />}>
        <Note kind="ok">
          {plan.in?.how}
        </Note>
        {plan.in?.fallback && (
          <Note kind="info">
            If the router turns a z-address down — its decision, not this
            module&apos;s — nothing is reserved and you are offered{' '}
            {plan.in.fallback}
          </Note>
        )}

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

        <div style={{ position: 'relative' }}>
          <Input label="Refund address on the origin chain" value={refund}
            onChange={(e: any) => setRefund(e.target.value)}
            placeholder="0x…"
            hint="where the money returns if the swap fails. It cannot be a Zcash address — a refund is paid on the chain you sent from." />
          <span style={{ position: 'absolute', top: 0, right: 0 }}>
            <UseWallet mm={mm} onPick={setRefund} />
          </span>
        </div>

        <Input label="Fallback: a transparent address you own" value={viaT}
          onChange={(e: any) => setViaT(e.target.value)}
          placeholder="t1… (optional)"
          hint="only used if the router refuses to pay a z-address. Then the ZEC lands here in the open and you shield it afterwards — a wallet name already covers this." />

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
          {/* The module falls back to a public first leg when the router
              refuses a z-address. A card that still said "lands shielded"
              would be the single most expensive lie this tab could tell. */}
          {quote.shielded === false && (
            <Note kind="warn">
              Not the shielded route. {quote.shielded_direct_unavailable}
            </Note>
          )}

          <div style={{
            display: 'grid', gap: 10, marginBottom: 12,
            gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))',
          }}>
            <Stat label="You send" value={`${quote.amount_in}`}
              sub={`${quote.from} · ${usd(quote.amount_in_usd)}`} />
            <Stat label={quote.shielded === false ? 'Lands in the clear' : 'Lands shielded'}
              value={`${quote.amount_out} ZEC`} sub={usd(quote.amount_out_usd)} />
            <Stat label="ETA" value={`${quote.eta_seconds}s`}
              sub={`pool: ${quote.destination_pool}`} />
          </div>

          {(quote.legs || []).map((leg: any) => (
            <div key={leg.leg} style={{ display: 'flex', gap: 8, fontSize: 12.5,
              padding: '6px 0', borderTop: `1px solid ${C.line}` }}>
              <span style={{ color: leg.private ? C.green : C.gold, minWidth: 44 }}>
                leg {leg.leg}
              </span>
              <span style={{ flex: 1 }}>
                {leg.what}
                <span style={{ display: 'block', color: C.dim, fontSize: 11, marginTop: 2 }}>
                  {leg.why}
                </span>
              </span>
            </div>
          ))}

          {/* Reserving is the one irreversible-feeling step here, and the
              fallback is not the route they asked for -- so it stops and asks
              rather than handing back a public deposit address. */}
          {quote.not_reserved && (
            <>
              <Note kind="warn">{quote.not_reserved}</Note>
              <Button variant="ghost" disabled={busy}
                onClick={() => go(true, true)}>
                Reserve the public route anyway
              </Button>
            </>
          )}

          {quote.recipient_rewritten && (
            <Note kind="warn">{quote.recipient_note}</Note>
          )}

          {quote.mode === 'RESERVED' ? (
            <>
              <Note kind="warn">
                Send <b>exactly {quote.amount_in} {quote.from}</b> to the address
                below before {quote.deadline}. It is on {String(quote.from).split(':')[0]},
                not on Zcash, so this module cannot sign that payment — it can
                only tell your wallet exactly what to sign.
              </Note>
              <Field label="Deposit address" mono
                value={<>{quote.deposit_address}<Copy text={quote.deposit_address} /></>} />
              <Field label="Arrives at" mono value={quote.recipient} />
              {/* The one place the whole private-inbound route used to stop.
                  Paying from the browser wallet keeps the shielded output
                  intact: the solver still creates it, we only fund the leg. */}
              <PayWithWallet mm={mm} fromAsset={quote.from} amount={quote.amount_in}
                depositAddress={quote.deposit_address} />
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
  const mm = useMetaMask()
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
        <div style={{ position: 'relative' }}>
          <Input label="Recipient on the destination chain" value={recipient}
            onChange={(e: any) => setRecipient(e.target.value)} placeholder="0x…" />
          <span style={{ position: 'absolute', top: 0, right: 0 }}>
            <UseWallet mm={mm} onPick={setRecipient} />
          </span>
        </div>

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

function PrivacyCard({ privacy, degraded }: { privacy: any, degraded?: boolean }) {
  if (!privacy) return null
  const grade = degraded ? C.gold
    : privacy.grade === 'good' ? C.green : C.gold
  return (
    <Panel title="What this direction hides"
      right={<span style={{
        fontSize: 10, letterSpacing: 1, textTransform: 'uppercase', color: grade,
      }}>{degraded ? 'not this quote' : privacy.grade}</span>}>
      <div style={{ fontSize: 12, color: C.dim, marginBottom: 12 }}>
        {privacy.direction}
      </div>
      {degraded && (
        <Note kind="warn">
          This describes the direct shielded route. The quote above is the
          two-leg fallback: the ZEC arrives transparent and is public until
          you shield it, so nothing below applies to leg one.
        </Note>
      )}
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
