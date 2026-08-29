"use client"

// PLAYER — who's signing. One dropdown holds every identity this browser can
// sign as: the accounts the injected wallet has permitted, and the keys held
// locally. Pick one and it signs every deploy and write from then on.

import { useState, useEffect, useCallback } from 'react'
import { ethers } from 'ethers'
import { toast } from 'react-toastify'
import {
  TERM_FONT, ACCENT, chainName, netInfo, readProvider, short, explorerUrl, type LocalKey,
} from './shared'
import { Btn, Input, Pill, Dropdown, DropHead, DropRow, DropRule, Quiet } from './ui'
import { PIXEL, PX, NEON, Sprite } from './arcade'
import type { ChainWallet } from './WalletBar'

const P1 = NEON.p1
const MM = '#f59e0b'

const fmt = (v?: string) => {
  if (v === undefined) return ''
  const n = Number(v)
  return n === 0 ? '0.000' : n < 0.0001 ? '<0.0001' : n.toFixed(4)
}

export function AccountPicker({ wallet, network }: { wallet: ChainWallet; network: string }) {
  const [open, setOpen] = useState(false)
  const [balances, setBalances] = useState<Record<string, string>>({})
  const [importing, setImporting] = useState(false)
  const [form, setForm] = useState({ label: '', pk: '' })
  const [confirmExport, setConfirmExport] = useState<string | null>(null)
  const net = netInfo(network)
  const close = useCallback(() => { setOpen(false); setImporting(false); setConfirmExport(null) }, [])

  const wrongChain = wallet.kind === 'browser'
    && wallet.injectedChainId !== null
    && wallet.injectedChainId !== net.chainId

  // Balances for every row, read when the list opens — a roster you can't
  // compare is just a list of hex.
  useEffect(() => {
    if (!open) return
    let cancelled = false
    const provider = readProvider(network)
    const addrs = [...wallet.accounts, ...wallet.localKeys.map(k => k.address)]
    addrs.forEach(a => {
      provider.getBalance(a)
        .then(b => { if (!cancelled) setBalances(prev => ({ ...prev, [a.toLowerCase()]: ethers.formatEther(b) })) })
        .catch(() => {})
    })
    return () => { cancelled = true }
  }, [open, network, wallet.accounts, wallet.localKeys])

  const copy = (text: string, what: string) => {
    navigator.clipboard.writeText(text)
    toast.success(`${what} copied`)
  }

  const fail = (e: any) => toast.error(e?.message || 'failed')

  const pickBrowser = (addr: string) => wallet.connect('browser', addr).then(close).catch(fail)
  const pickLocal = (id: string) => wallet.connect('local', id).then(close).catch(fail)

  const generate = () => {
    const key = wallet.newKey(`KEY ${wallet.localKeys.filter(k => k.source === 'imported').length + 1}`)
    toast.success(`${key.label} minted — ${short(key.address)}`)
    wallet.connect('local', key.id).catch(fail)
  }

  const doImport = () => {
    try {
      const key = wallet.newKey(form.label, form.pk.trim())
      setForm({ label: '', pk: '' }); setImporting(false)
      toast.success(`${key.label} imported`)
      wallet.connect('local', key.id).catch(fail)
    } catch (e) { fail(e) }
  }

  const exportKey = (k: LocalKey) => {
    if (confirmExport === k.id) { copy(k.pk, 'Private key'); setConfirmExport(null) }
    else setConfirmExport(k.id)
  }

  const isMe = (addr: string) => !!wallet.address && addr.toLowerCase() === wallet.address.toLowerCase()
  const bal = (addr: string) => balances[addr.toLowerCase()]

  const balanceCell = (addr: string) => (
    <span style={{ marginLeft: 'auto', fontFamily: TERM_FONT, fontSize: '14px', color: 'var(--text-tertiary)', flexShrink: 0 }}>
      {bal(addr) !== undefined ? `${fmt(bal(addr))} ${net.currency}` : '…'}
    </span>
  )

  const tag = (text: string, color: string) => (
    <span style={{
      fontFamily: PIXEL, fontSize: '7px', letterSpacing: '0.1em', color,
      border: `1px solid ${color}`, padding: '3px 4px', lineHeight: 1, flexShrink: 0,
    }}>
      {text}
    </span>
  )

  const trigger = (
    <Pill
      label="PLAYER"
      color={P1}
      open={open}
      onClick={() => (open ? close() : setOpen(true))}
      blink={!wallet.kind}
      title={wallet.address || 'sign in'}
    >
      {wallet.kind ? (
        <>
          <Sprite seed={wallet.address} size={18} />
          <span>{short(wallet.address, 6, 4)}</span>
          {tag(wallet.kind === 'browser' ? 'METAMASK' : (wallet.localKeys.find(k => k.address === wallet.address)?.label || 'LOCAL'),
            wallet.kind === 'browser' ? MM : ACCENT)}
        </>
      ) : (
        <span style={{ color: NEON.coin }}>INSERT COIN</span>
      )}
    </Pill>
  )

  return (
    <>
      <Dropdown open={open} onClose={close} trigger={trigger} color={P1} width={380}>
        {/* who's signing now */}
        {wallet.kind && (
          <div style={{
            display: 'flex', alignItems: 'center', gap: '10px', padding: '12px 12px 10px',
            borderBottom: '1px solid var(--border-color)',
          }}>
            <Sprite seed={wallet.address} size={34} />
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ fontFamily: PIXEL, fontSize: PX.xs, letterSpacing: '0.12em', color: P1, marginBottom: '5px' }}>
                SIGNING AS
              </div>
              <div style={{
                fontFamily: TERM_FONT, fontSize: '14px', color: 'var(--text-primary)',
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>
                {wallet.address}
              </div>
            </div>
            <Quiet onClick={() => copy(wallet.address, 'Address')} title="copy address">COPY</Quiet>
            {explorerUrl(network, wallet.address) && (
              <a href={explorerUrl(network, wallet.address)} target="_blank" rel="noreferrer"
                style={{ fontFamily: TERM_FONT, fontSize: '16px', color: 'var(--text-tertiary)', textDecoration: 'none', padding: '0 4px' }}
                title={`open on ${net.name} explorer`}>
                ↗
              </a>
            )}
          </div>
        )}

        {wrongChain && (
          <div style={{
            margin: '10px 12px 4px', padding: '10px 12px', border: `2px solid ${NEON.coin}`,
            background: `${NEON.coin}12`, display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap',
          }}>
            <span style={{ fontFamily: TERM_FONT, fontSize: '14px', color: 'var(--text-secondary)', flex: 1, minWidth: '140px' }}>
              MetaMask is on <b style={{ color: NEON.coin }}>{chainName(wallet.injectedChainId!)}</b>, the console is on {net.name}.
            </span>
            <Btn size="sm" color={NEON.coin} onClick={() => wallet.switchChain().catch(fail)}>SWITCH</Btn>
          </div>
        )}

        {/* browser wallet */}
        <DropHead color={MM} right={
          wallet.injected && (
            <Quiet color={MM} onClick={() => wallet.connectMore().catch(fail)} title="let MetaMask expose more accounts">
              {wallet.connecting === 'browser' ? '…' : wallet.accounts.length ? '+ MORE' : '+ CONNECT'}
            </Quiet>
          )
        }>
          METAMASK
        </DropHead>
        {!wallet.injected ? (
          <div style={{ fontFamily: TERM_FONT, fontSize: '14px', color: 'var(--text-tertiary)', padding: '2px 12px 8px' }}>
            no browser wallet found — a local key signs on its own
          </div>
        ) : wallet.accounts.length === 0 ? (
          <div style={{ fontFamily: TERM_FONT, fontSize: '14px', color: 'var(--text-tertiary)', padding: '2px 12px 8px' }}>
            not connected — hit + CONNECT to pick accounts
          </div>
        ) : wallet.accounts.map(a => (
          <DropRow key={a} active={wallet.kind === 'browser' && isMe(a)} color={MM} onClick={() => pickBrowser(a)} title={a}>
            <Sprite seed={a} size={22} />
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{short(a, 8, 6)}</span>
            {balanceCell(a)}
          </DropRow>
        ))}

        <DropRule />

        {/* local keys */}
        <DropHead color={ACCENT} right={
          <span style={{ display: 'flex' }}>
            <Quiet color={ACCENT} onClick={generate} title="mint a fresh key in this browser">+ NEW</Quiet>
            <Quiet color={ACCENT} onClick={() => setImporting(i => !i)} title="paste a private key">IMPORT</Quiet>
          </span>
        }>
          LOCAL KEYS
        </DropHead>
        {wallet.localKeys.map(k => (
          <DropRow
            key={k.id}
            active={wallet.kind === 'local' && isMe(k.address)}
            onClick={() => pickLocal(k.id)}
            title={k.address}
            right={
              <>
                <Quiet onClick={() => exportKey(k)}
                  color={confirmExport === k.id ? NEON.dead : undefined}
                  title={confirmExport === k.id ? 'click again to copy the private key' : 'export private key'}>
                  {confirmExport === k.id ? 'COPY PK?' : 'EXPORT'}
                </Quiet>
                {k.source === 'imported' && (
                  <Quiet onClick={() => wallet.dropKey(k.id)} title="forget this key">✕</Quiet>
                )}
              </>
            }
          >
            <Sprite seed={k.address} size={22} />
            <span style={{ display: 'flex', flexDirection: 'column', gap: '3px', minWidth: 0 }}>
              <span style={{ fontFamily: PIXEL, fontSize: '7px', letterSpacing: '0.1em', color: ACCENT }}>
                {k.label}
              </span>
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{short(k.address, 8, 6)}</span>
            </span>
            {balanceCell(k.address)}
          </DropRow>
        ))}

        {importing && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', padding: '6px 12px 10px' }}>
            <Input value={form.label} onChange={v => setForm(f => ({ ...f, label: v }))} placeholder="label (optional)" />
            <Input value={form.pk} onChange={v => setForm(f => ({ ...f, pk: v }))} placeholder="private key 0x…" onEnter={doImport} />
            <div style={{ display: 'flex', gap: '6px' }}>
              <Btn size="sm" onClick={doImport}>SAVE</Btn>
              <Btn size="sm" active={false} onClick={() => setImporting(false)}>CANCEL</Btn>
            </div>
            <span style={{ fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-tertiary)' }}>
              stays in this browser&apos;s storage — never sent anywhere
            </span>
          </div>
        )}

        {wallet.kind && (
          <>
            <DropRule />
            <div style={{ padding: '4px 12px 12px', display: 'flex', justifyContent: 'flex-end' }}>
              <Btn size="sm" active={false} onClick={() => { wallet.disconnect(); close() }}>SIGN OUT</Btn>
            </div>
          </>
        )}
      </Dropdown>

      {/* the wrong-chain nudge sits beside the pill too — one glance, not a
          click, and amber rather than red: nothing is broken, just misaimed */}
      {wrongChain && (
        <Btn size="sm" color={NEON.coin} onClick={() => wallet.switchChain().catch(fail)}
          title={`MetaMask is on ${chainName(wallet.injectedChainId!)}`}>
          SWITCH → {net.name.toUpperCase()}
        </Btn>
      )}
    </>
  )
}
