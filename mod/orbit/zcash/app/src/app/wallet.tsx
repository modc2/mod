'use client'

// METAMASK — the other half of a bridge.
//
// Bridging into ZEC hands back a deposit address on Ethereum, Base, Arbitrum…
// and until now the console's honest answer was "this module cannot pay it for
// you". It still cannot: it holds Zcash keys, not EVM ones. What it can do is
// say exactly what to sign — bridge_payment returns the chain id, the `to`,
// the value in base units and the ERC-20 calldata — and hand that to a wallet
// in the same browser.
//
// Everything below is EIP-1193 (`request`) plus EIP-6963 for discovery, so it
// works with MetaMask and with anything that announces itself the same way.
// No private key, seed or signature ever reaches the module.

import { useCallback, useEffect, useRef, useState } from 'react'
import { call } from './api'
import { Button, C, Copy, Field, Note } from './ui'

type Eip1193 = {
  request: (a: { method: string, params?: any }) => Promise<any>
  on?: (e: string, h: (...a: any[]) => void) => void
  removeListener?: (e: string, h: (...a: any[]) => void) => void
}
type Discovered = { info: { uuid: string, name: string, icon: string, rdns: string }, provider: Eip1193 }

export type PayIntent = {
  chain: string, chain_name: string, chain_id: number, chain_id_hex: string
  symbol: string, decimals: number, kind: 'native' | 'erc20', contract: string | null
  amount: string, amount_base_units: string, deposit_address: string
  native_symbol: string, explorer_tx: string, explorer_address: string
  add_chain: any, note: string, tx: { to: string, value: string, data: string }
}

// EIP-6963: wallets announce themselves in response to a request event. It is
// the only way to tell MetaMask from whatever else overwrote window.ethereum
// when several extensions are installed.
function discover(): Promise<Discovered[]> {
  return new Promise(resolve => {
    if (typeof window === 'undefined') return resolve([])
    const found: Discovered[] = []
    const on = (e: any) => {
      const d = e.detail as Discovered
      if (d?.provider && !found.some(f => f.info.uuid === d.info.uuid)) found.push(d)
    }
    window.addEventListener('eip6963:announceProvider', on as any)
    window.dispatchEvent(new Event('eip6963:requestProvider'))
    setTimeout(() => {
      window.removeEventListener('eip6963:announceProvider', on as any)
      const legacy = (window as any).ethereum
      if (!found.length && legacy) {
        found.push({
          info: {
            uuid: 'legacy', rdns: 'legacy', icon: '',
            name: legacy.isMetaMask ? 'MetaMask' : 'Browser wallet',
          },
          provider: legacy,
        })
      }
      resolve(found)
    }, 220)
  })
}

const preferred = (ws: Discovered[]) =>
  ws.find(w => w.info.rdns === 'io.metamask') || ws.find(w => /metamask/i.test(w.info.name)) || ws[0]

const REMEMBER = 'zcash_evm_wallet'

export function useMetaMask() {
  const [wallets, setWallets] = useState<Discovered[]>([])
  const [chosen, setChosen] = useState<Discovered | null>(null)
  const [account, setAccount] = useState('')
  const [chainId, setChainId] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const mounted = useRef(true)

  useEffect(() => () => { mounted.current = false }, [])

  useEffect(() => {
    discover().then(async ws => {
      if (!mounted.current) return
      setWallets(ws)
      const w = preferred(ws)
      if (!w) return
      setChosen(w)
      // Reconnect silently only if this browser connected before: eth_accounts
      // never prompts, so a first-time visitor sees no popup they did not ask
      // for and a returning one does not have to click Connect again.
      if (!localStorage.getItem(REMEMBER)) return
      try {
        const accts = await w.provider.request({ method: 'eth_accounts' })
        if (accts?.[0] && mounted.current) {
          setAccount(accts[0])
          setChainId(parseInt(await w.provider.request({ method: 'eth_chainId' }), 16))
        }
      } catch { /* a wallet that will not answer eth_accounts is simply absent */ }
    })
  }, [])

  // Switching account or network in the extension has to move the page with
  // it; otherwise the address shown here is not the address that would sign.
  useEffect(() => {
    const p = chosen?.provider
    if (!p?.on) return
    const onAccounts = (a: string[]) => setAccount(a?.[0] || '')
    const onChain = (c: string) => setChainId(parseInt(c, 16))
    p.on('accountsChanged', onAccounts)
    p.on('chainChanged', onChain)
    return () => {
      p.removeListener?.('accountsChanged', onAccounts)
      p.removeListener?.('chainChanged', onChain)
    }
  }, [chosen])

  const connect = useCallback(async (which?: Discovered) => {
    const w = which || chosen || preferred(await discover())
    if (!w) {
      setError('No EVM wallet found in this browser. Install MetaMask, or pay the deposit address from a wallet elsewhere.')
      return
    }
    setChosen(w); setBusy(true); setError('')
    try {
      const accts = await w.provider.request({ method: 'eth_requestAccounts' })
      setAccount(accts?.[0] || '')
      setChainId(parseInt(await w.provider.request({ method: 'eth_chainId' }), 16))
      localStorage.setItem(REMEMBER, w.info.rdns || 'legacy')
    } catch (e: any) {
      setError(e?.code === 4001 ? 'Connection rejected in the wallet.' : (e?.message || String(e)))
    } finally { setBusy(false) }
  }, [chosen])

  const forget = useCallback(() => {
    localStorage.removeItem(REMEMBER)
    setAccount(''); setChainId(null); setError('')
  }, [])

  // The wallet has to be on the deposit's own chain: the same address on the
  // wrong network sends real money into a contract that is not there.
  const ensureChain = useCallback(async (intent: PayIntent) => {
    const p = chosen?.provider
    if (!p) throw new Error('no wallet connected')
    const now = parseInt(await p.request({ method: 'eth_chainId' }), 16)
    if (now === intent.chain_id) return
    try {
      await p.request({
        method: 'wallet_switchEthereumChain',
        params: [{ chainId: intent.chain_id_hex }],
      })
    } catch (e: any) {
      // 4902: the wallet has never heard of this chain. Offer to add it with
      // the parameters the module vouches for, rather than leaving the user to
      // find a chain id on a forum.
      if (e?.code === 4902 || e?.data?.originalError?.code === 4902) {
        await p.request({ method: 'wallet_addEthereumChain', params: [intent.add_chain] })
      } else throw e
    }
    setChainId(intent.chain_id)
  }, [chosen])

  // What the connected account actually holds of the asset being sent. A
  // deposit that arrives short is refunded at best, and the wallet's own
  // "insufficient funds" arrives after the network has already been switched.
  const balanceOf = useCallback(async (intent: PayIntent): Promise<bigint | null> => {
    const p = chosen?.provider
    if (!p || !account) return null
    try {
      if (intent.kind === 'native') {
        return BigInt(await p.request({ method: 'eth_getBalance', params: [account, 'latest'] }))
      }
      const data = '0x70a08231' + account.toLowerCase().replace(/^0x/, '').padStart(64, '0')
      const r = await p.request({
        method: 'eth_call', params: [{ to: intent.contract, data }, 'latest'],
      })
      return r && r !== '0x' ? BigInt(r) : 0n
    } catch { return null }
  }, [chosen, account])

  const pay = useCallback(async (intent: PayIntent): Promise<string> => {
    const p = chosen?.provider
    if (!p || !account) throw new Error('connect a wallet first')
    await ensureChain(intent)
    return await p.request({
      method: 'eth_sendTransaction',
      params: [{ from: account, to: intent.tx.to, value: intent.tx.value, data: intent.tx.data }],
    })
  }, [chosen, account, ensureChain])

  return {
    wallets, wallet: chosen, name: chosen?.info.name || 'MetaMask',
    icon: chosen?.info.icon || '', account, chainId, busy, error,
    available: wallets.length > 0, connect, forget, pay, ensureChain, balanceOf,
    setError,
  }
}

export const shortAddr = (a: string) => a ? `${a.slice(0, 6)}…${a.slice(-4)}` : ''

const FOX = (
  <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden style={{ flexShrink: 0 }}>
    <path fill="#e2761b" d="M21.7 2.5 13.3 8.7l1.6-3.7z" />
    <path fill="#e4761b" d="M2.3 2.5l8.3 6.3-1.5-3.8zM18.8 16.3l-2.2 3.4 4.8 1.3 1.4-4.6zM1.2 16.4l1.4 4.6 4.8-1.3-2.2-3.4z" />
    <path fill="#f6851b" d="M7.1 10.8 5.7 12.9l4.7.2-.2-5.1zm9.8 0-3.2-2.9-.1 5.2 4.7-.2zM7.2 19.7l2.9-1.4-2.5-1.9zm6.7-1.4 2.9 1.4-.4-3.3z" />
    <path fill="#d7c1b3" d="m16.8 19.7-2.9-1.4.2 1.9v.8zM7.2 19.7l2.7 1.3v-.8l.2-1.9z" />
  </svg>
)

/** The connect chip that lives in a panel header. */
export function WalletChip({ mm, compact }: { mm: ReturnType<typeof useMetaMask>, compact?: boolean }) {
  if (!mm.account) {
    return (
      <button onClick={() => mm.connect()} disabled={mm.busy} title="Connect an EVM wallet in this browser"
        style={{
          display: 'flex', alignItems: 'center', gap: 6, fontSize: 11,
          padding: '4px 10px', borderRadius: 999, cursor: mm.busy ? 'wait' : 'pointer',
          background: 'transparent', border: `1px solid ${C.line}`, color: C.dim,
        }}>
        {FOX}{mm.busy ? 'connecting…' : compact ? 'Connect' : `Connect ${mm.name}`}
      </button>
    )
  }
  return (
    <span title={`${mm.account} · ${mm.name}`} style={{
      display: 'flex', alignItems: 'center', gap: 6, fontSize: 11,
      padding: '4px 10px', borderRadius: 999,
      background: `${C.green}14`, border: `1px solid ${C.green}55`, color: C.green,
      fontFamily: 'ui-monospace, Menlo, monospace',
    }}>
      {FOX}{shortAddr(mm.account)}
      <button onClick={mm.forget} title="disconnect this page"
        style={{ background: 'none', border: 'none', color: C.dim, cursor: 'pointer', fontSize: 12, padding: 0 }}>
        ×
      </button>
    </span>
  )
}

/** "use my wallet" next to an address field that wants an EVM address. */
export function UseWallet({ mm, onPick }: { mm: ReturnType<typeof useMetaMask>, onPick: (a: string) => void }) {
  if (!mm.available) return null
  return (
    <button
      onClick={() => (mm.account ? onPick(mm.account) : mm.connect())}
      title={mm.account ? `paste ${mm.account}` : 'connect a wallet and paste its address'}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 10.5,
        padding: '2px 8px', borderRadius: 999, cursor: 'pointer',
        background: 'transparent', border: `1px solid ${C.line}`, color: C.dim,
      }}>
      {FOX}{mm.account ? shortAddr(mm.account) : 'connect'}
    </button>
  )
}

const fromUnits = (v: bigint, decimals: number) => {
  const s = v.toString().padStart(decimals + 1, '0')
  const whole = s.slice(0, s.length - decimals)
  const frac = decimals ? s.slice(s.length - decimals).replace(/0+$/, '') : ''
  return frac ? `${whole}.${frac}` : whole
}

/**
 * PAY — the card that closes the loop.
 *
 * It appears once a deposit address exists on an EVM chain. `bridge_payment`
 * builds the transaction server-side (the amount is converted by the same code
 * that priced the quote), the wallet signs it, and the tx hash comes back here
 * so the same panel can go straight to tracking the bridge.
 */
export function PayWithWallet({ mm, fromAsset, amount, depositAddress, onPaid }: {
  mm: ReturnType<typeof useMetaMask>
  fromAsset: string
  amount: string | number
  depositAddress: string
  onPaid?: (hash: string) => void
}) {
  const [intent, setIntent] = useState<PayIntent | null>(null)
  const [why, setWhy] = useState('')          // why no intent: an honest reason
  const [bal, setBal] = useState<bigint | null>(null)
  const [hash, setHash] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    let live = true
    setIntent(null); setWhy(''); setHash(''); setErr('')
    if (!depositAddress || !amount) return
    call('bridge_payment', { from_asset: fromAsset, amount: Number(amount), deposit_address: depositAddress })
      .then((i: PayIntent) => { if (live) setIntent(i) })
      .catch((e: any) => { if (live) setWhy(e.message.replace(/^bridge_payment: /, '')) })
    return () => { live = false }
  }, [fromAsset, amount, depositAddress])

  useEffect(() => {
    if (!intent || !mm.account) { setBal(null); return }
    let live = true
    mm.balanceOf(intent).then(b => { if (live) setBal(b) })
    return () => { live = false }
  }, [intent, mm.account, mm.chainId]) // eslint-disable-line react-hooks/exhaustive-deps

  // Not an EVM chain (or one without a verified chain id): say so once, plainly.
  if (why) {
    return <Note kind="info">{why}</Note>
  }
  if (!intent) return null

  const need = BigInt(intent.amount_base_units)
  const short = bal != null && bal < need
  const wrongChain = mm.account && mm.chainId != null && mm.chainId !== intent.chain_id

  const go = async () => {
    setBusy(true); setErr('')
    try {
      const h = await mm.pay(intent)
      setHash(h); onPaid?.(h)
    } catch (e: any) {
      setErr(e?.code === 4001 ? 'Rejected in the wallet — nothing was sent.'
        : (e?.data?.message || e?.message || String(e)))
    } finally { setBusy(false) }
  }

  return (
    <div style={{
      border: `1px solid ${C.line}`, borderRadius: 10, padding: 14, marginTop: 12,
      background: C.bg,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        {FOX}
        <span style={{ fontSize: 11, letterSpacing: 1, color: C.dim, textTransform: 'uppercase' }}>
          Pay this from your wallet
        </span>
        <span style={{ marginLeft: 'auto' }}><WalletChip mm={mm} compact /></span>
      </div>

      <div style={{ fontSize: 12.5, color: C.dim, lineHeight: 1.6, marginBottom: 10 }}>
        {intent.amount} <b style={{ color: C.text }}>{intent.symbol}</b> on{' '}
        <b style={{ color: C.text }}>{intent.chain_name}</b> → the deposit address.{' '}
        {intent.kind === 'erc20'
          ? `A token transfer: the transaction goes to the ${intent.symbol} contract, and the deposit address is its argument.`
          : 'A plain transfer of the native coin.'}{' '}
        Your keys stay in the wallet — this module never sees them.
      </div>

      {mm.error && <Note kind="error">{mm.error}</Note>}
      {err && <Note kind="error">{err}</Note>}

      {hash ? (
        <>
          <Note kind="ok">
            Sent. The solver pays out once it sees this confirm.
          </Note>
          <Field label="Transaction" mono value={
            <>
              <a href={`${intent.explorer_tx}${hash}`} target="_blank" rel="noreferrer"
                style={{ color: C.blue, wordBreak: 'break-all' }}>{hash}</a>
              <Copy text={hash} />
            </>
          } />
        </>
      ) : (
        <>
          {short && (
            <Note kind="warn">
              This account holds {fromUnits(bal!, intent.decimals)} {intent.symbol} on{' '}
              {intent.chain_name} — {intent.amount} is needed. Sending less than the
              quoted amount gets refunded, not credited.
            </Note>
          )}
          {wrongChain && (
            <Note kind="info">
              Your wallet is on another network. Paying switches it to {intent.chain_name} first.
            </Note>
          )}
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            {mm.account ? (
              <Button onClick={go} disabled={busy || mm.busy}>
                {busy ? 'confirm in your wallet…' : `Send ${intent.amount} ${intent.symbol} on ${intent.chain_name}`}
              </Button>
            ) : (
              <Button onClick={() => mm.connect()} disabled={mm.busy}>
                {mm.busy ? 'connecting…' : mm.available ? `Connect ${mm.name} to pay` : 'No wallet in this browser'}
              </Button>
            )}
            {bal != null && !short && (
              <span style={{ fontSize: 11, color: C.dim }}>
                balance {fromUnits(bal, intent.decimals)} {intent.symbol}
              </span>
            )}
            {!mm.available && (
              <span style={{ fontSize: 11, color: C.dim }}>
                Or send it from any wallet — the deposit address is above.
              </span>
            )}
          </div>
        </>
      )}
    </div>
  )
}

/** The list of chains a browser wallet can be pointed at, for the how-it-works copy. */
export function WalletNetworks() {
  const [nets, setNets] = useState<any>(null)
  useEffect(() => { call('bridge_networks').then(setNets).catch(() => {}) }, [])
  if (!nets) return null
  return (
    <span style={{ fontSize: 11, color: C.dim }}>
      payable from a browser wallet on {nets.networks.map((n: any) => n.name).join(', ')}
    </span>
  )
}

