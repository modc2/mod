"use client"

// PLAY — call any contract the console knows about: the deployed fleet,
// anything you built here, or a pasted address + ABI. Reads go through the
// network RPC; writes are signed by the wallet in the strip above.
//
// Every contract is a card in a deck. The deck is yours to manage: pin the
// ones you keep coming back to, hide the noise, rename anything, and keep
// contracts you loaded by hand. The book lives in this browser, per network.

import { useState, useEffect, useCallback, useMemo } from 'react'
import { ethers } from 'ethers'
import { toast } from 'react-toastify'
import {
  TERM_FONT, ACCENT, READ, WRITE, DANGER, chainApi, coerceArgs, jsonify,
  placeholderFor, readProvider, short, explorerUrl, txUrl, useIsMobile,
  cardBook, saveContractCard, forgetContractCard, nameContractCard, toggleContractCard,
  type CardBook,
} from './shared'
import { Panel, Label, Btn, Input, Empty, panelStyle, Quiet } from './ui'
import { PIXEL, PX, NEON } from './arcade'
import type { ChainWallet } from './WalletBar'

export interface InteractTarget {
  name: string
  address: string
  abi: any[]
  /** where the store mod holds this ABI, when it holds it */
  abiCid?: string
}

interface AbiFn {
  name: string
  inputs: { name: string; type: string }[]
  outputs: { name: string; type: string }[]
  stateMutability: string
}

type Source = 'build' | 'fleet' | 'saved'

/** One card in the deck: a target plus what the book says about it. */
interface Card extends InteractTarget {
  source: Source
  pinned: boolean
  hidden: boolean
  /** the name the API gave it, before you renamed it */
  origName: string
}

const isRead = (f: AbiFn) => f.stateMutability === 'view' || f.stateMutability === 'pure'
const lc = (a: string) => a.toLowerCase()

/** `transfer(address to, uint256 amount) → bool` — the line under the method name. */
const signature = (f: AbiFn) => {
  const ins = f.inputs.map(i => `${i.type}${i.name ? ` ${i.name}` : ''}`).join(', ')
  const outs = f.outputs.map(o => `${o.type}${o.name ? ` ${o.name}` : ''}`).join(', ')
  return `${f.name}(${ins})${outs ? ` → ${f.outputs.length > 1 ? `(${outs})` : outs}` : ''}`
}

/** A small hard-edged link — EXPLORER ↗, TX ↗. */
function Ext({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noreferrer" className="arc-press arc-pixel"
      style={{
        display: 'inline-flex', alignItems: 'center', fontFamily: PIXEL, fontSize: PX.xs,
        letterSpacing: '0.08em', padding: '7px 10px', minHeight: '30px',
        border: '2px solid var(--border-color)', color: 'var(--text-tertiary)', textDecoration: 'none',
      }}>
      {children}
    </a>
  )
}

const SOURCE_TAG: Record<Source, { label: string; color: string }> = {
  build: { label: 'BUILD', color: ACCENT },
  fleet: { label: 'FLEET', color: NEON.p2 },
  saved: { label: 'SAVED', color: NEON.coin },
}

export function InteractTab({
  wallet, network, target, setTarget,
}: {
  wallet: ChainWallet
  network: string
  target: InteractTarget | null
  setTarget: (t: InteractTarget | null) => void
}) {
  const [fleet, setFleet] = useState<InteractTarget[]>([])
  const [builds, setBuilds] = useState<InteractTarget[]>([])
  const [loading, setLoading] = useState(true)

  // the book is localStorage — only readable after mount, re-read after every edit
  const [book, setBook] = useState<CardBook>({ saved: [], pinned: [], hidden: [], names: {} })
  const reload = useCallback(() => setBook(cardBook(network)), [network])
  useEffect(() => { reload() }, [reload])

  const [manage, setManage] = useState(false)
  const [showHidden, setShowHidden] = useState(false)
  const [renaming, setRenaming] = useState<string | null>(null)
  const [rename, setRename] = useState('')
  const [confirmForget, setConfirmForget] = useState<string | null>(null)

  const [fn, setFn] = useState<string>('')
  const [args, setArgs] = useState<Record<string, string>>({})
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<any>(null)
  const [error, setError] = useState<string | null>(null)
  const [took, setTook] = useState<number | null>(null)

  const [manualName, setManualName] = useState('')
  const [manualAddr, setManualAddr] = useState('')
  const [manualAbi, setManualAbi] = useState('')
  const [manualCid, setManualCid] = useState('')
  const [fetchingCid, setFetchingCid] = useState(false)
  const [showManual, setShowManual] = useState(false)
  const mobile = useIsMobile()

  // ── load callable contracts ──
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    const q = wallet.address ? `?address=${wallet.address}&` : '?'
    Promise.all([
      chainApi(`/contracts/abis?network=${network}`).catch(() => ({ contracts: [] })),
      chainApi(`/build/deployments${q}network=${network}`).catch(() => ({ deployments: [] })),
    ]).then(([abis, mine]) => {
      if (cancelled) return
      setFleet((abis.contracts || []).map((c: any) => (
        { name: c.name, address: c.address, abi: c.abi, abiCid: c.abi_cid })))
      setBuilds((mine.deployments || [])
        .filter((d: any) => d.abi?.length)
        .map((d: any) => ({ name: d.name, address: d.address, abi: d.abi, abiCid: d.abi_cid })))
      setLoading(false)
    })
    return () => { cancelled = true }
  }, [network, wallet.address])

  // ── the deck ──
  const cards: Card[] = useMemo(() => {
    const dress = (t: InteractTarget, source: Source): Card => ({
      ...t,
      source,
      origName: t.name,
      name: book.names[lc(t.address)] || t.name,
      pinned: book.pinned.includes(lc(t.address)),
      hidden: book.hidden.includes(lc(t.address)),
    })
    return [
      ...builds.map(t => dress(t, 'build')),
      ...fleet.map(t => dress(t, 'fleet')),
      ...book.saved.map(s => dress({ name: s.name, address: s.address, abi: s.abi, abiCid: s.abiCid }, 'saved')),
    ]
  }, [builds, fleet, book])

  const hiddenCount = cards.filter(c => c.hidden).length
  const visible = (c: Card) => showHidden || !c.hidden
  const deck = {
    pinned: cards.filter(c => c.pinned && visible(c)),
    build: cards.filter(c => c.source === 'build' && !c.pinned && visible(c)),
    fleet: cards.filter(c => c.source === 'fleet' && !c.pinned && visible(c)),
    saved: cards.filter(c => c.source === 'saved' && !c.pinned && visible(c)),
  }

  const fns: AbiFn[] = useMemo(() => {
    if (!target?.abi) return []
    return target.abi
      .filter((f: any) => f.type === 'function')
      .map((f: any) => ({
        name: f.name, inputs: f.inputs || [], outputs: f.outputs || [],
        stateMutability: f.stateMutability || 'nonpayable',
      }))
      .sort((a: AbiFn, b: AbiFn) =>
        Number(isRead(b)) - Number(isRead(a)) || a.name.localeCompare(b.name))
  }, [target])

  const reads = fns.filter(isRead)
  const writes = fns.filter(f => !isRead(f))
  const current = fns.find(f => f.name === fn) || null

  useEffect(() => { setFn(''); setArgs({}); setResult(null); setError(null); setValue('') }, [target?.address])
  useEffect(() => { setArgs({}); setResult(null); setError(null); setTook(null) }, [fn])

  const pick = (t: InteractTarget) => setTarget({ name: t.name, address: t.address, abi: t.abi, abiCid: t.abiCid })
  const targetCard = target ? cards.find(c => lc(c.address) === lc(target.address)) : undefined

  // ── managing the deck ──
  const pin = (c: InteractTarget) => { toggleContractCard(network, 'pinned', c.address); reload() }
  const hide = (c: Card) => {
    toggleContractCard(network, 'hidden', c.address); reload()
    if (!c.hidden && target && lc(target.address) === lc(c.address)) setTarget(null)
  }
  const forget = (c: Card) => {
    forgetContractCard(network, c.address); reload(); setConfirmForget(null)
    if (target && lc(target.address) === lc(c.address)) setTarget(null)
    toast.success(`${c.name} forgotten — the contract is still on chain`)
  }
  const saveName = (c: Card) => {
    nameContractCard(network, c.address, rename)
    reload(); setRenaming(null)
    if (target && lc(target.address) === lc(c.address)) setTarget({ ...target, name: rename.trim() || c.origName })
  }

  /** A contract loaded by hand joins the deck as a SAVED card and is selected. */
  const adopt = (t: InteractTarget) => {
    saveContractCard(network, t)
    reload()
    setTarget(t)
    setShowManual(false)
    setManualName(''); setManualAddr(''); setManualAbi(''); setManualCid('')
  }

  const addManual = () => {
    try {
      if (!ethers.isAddress(manualAddr.trim())) throw new Error('invalid address')
      const abi = JSON.parse(manualAbi)
      if (!Array.isArray(abi)) throw new Error('ABI must be a JSON array')
      const address = ethers.getAddress(manualAddr.trim())
      adopt({ name: manualName.trim() || short(address), address, abi })
    } catch (e: any) {
      toast.error(e?.message || 'invalid ABI')
    }
  }

  /**
   * Load an ABI the store mod is holding. Deploying from BUILD stores one for
   * every contract, so a CID is all it takes to drive a contract deployed from
   * another project, another wallet or another machine.
   */
  const loadCid = async () => {
    const cid = manualCid.trim()
    if (!cid) return
    if (!ethers.isAddress(manualAddr.trim())) { toast.error('enter the contract address too'); return }
    setFetchingCid(true)
    try {
      const d = await chainApi(`/build/abi/${encodeURIComponent(cid)}`)
      const address = ethers.getAddress(manualAddr.trim())
      adopt({ name: manualName.trim() || short(address), address, abi: d.abi, abiCid: cid })
      toast.success(`ABI loaded — ${d.abi.length} entries`)
    } catch (e: any) {
      toast.error(e?.message || 'could not load that CID')
    } finally {
      setFetchingCid(false)
    }
  }

  const execute = useCallback(async () => {
    if (!target || !current) return
    setBusy(true); setResult(null); setError(null); setTook(null)
    const t0 = performance.now()
    try {
      const callArgs = coerceArgs(current.inputs, args)
      if (isRead(current)) {
        const contract = new ethers.Contract(target.address, target.abi, readProvider(network))
        const out = await contract[current.name](...callArgs)
        setResult(out)
      } else {
        if (!wallet.kind) throw new Error('Sign in with a wallet to send a transaction')
        const signer = await wallet.signer()
        const contract = new ethers.Contract(target.address, target.abi, signer)
        const overrides = current.stateMutability === 'payable' && value.trim()
          ? { value: ethers.parseEther(value.trim()) }
          : {}
        const tx = await contract[current.name](...callArgs, overrides)
        const receipt = await tx.wait()
        setResult({ tx_hash: tx.hash, status: receipt?.status === 1 ? 'success' : 'failed',
          block: receipt?.blockNumber, gas_used: receipt?.gasUsed })
        toast.success(`${current.name} confirmed`)
        wallet.refresh()
      }
      setTook(performance.now() - t0)
    } catch (e: any) {
      setError(e?.shortMessage || e?.reason || e?.message || 'call failed')
    } finally {
      setBusy(false)
    }
  }, [target, current, args, value, network, wallet])

  const canSend = !!current && (isRead(current) || !!wallet.kind)
  const color = current ? (isRead(current) ? READ : WRITE) : ACCENT
  const resultText = result === null ? '' : typeof result === 'string' ? result : jsonify(result)

  const method = (f: AbiFn) => {
    const c = isRead(f) ? READ : WRITE
    const on = fn === f.name
    return (
      <button
        key={f.name}
        onClick={() => setFn(f.name)}
        className="arc-press"
        title={signature(f)}
        style={{
          display: 'flex', alignItems: 'center', gap: '8px',
          fontFamily: TERM_FONT, fontSize: mobile ? '16px' : '15px',
          padding: mobile ? '10px 12px' : '7px 12px', minHeight: mobile ? '44px' : '36px',
          border: `2px solid ${on ? c : 'var(--border-color)'}`,
          background: on ? `${c}1f` : 'rgba(0,0,0,0.2)',
          color: on ? c : 'var(--text-secondary)',
          boxShadow: on ? `3px 3px 0 0 ${c}` : '2px 2px 0 0 rgba(0,0,0,0.3)',
          cursor: 'pointer', maxWidth: '100%',
          flex: mobile ? '1 1 calc(50% - 6px)' : undefined,
        }}
      >
        <span style={{
          width: '8px', height: '8px', flexShrink: 0,
          background: on ? c : 'transparent', border: `2px solid ${c}`,
          boxShadow: on ? `0 0 8px ${c}` : 'none',
        }} />
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{f.name}</span>
        {f.inputs.length > 0 && (
          <span style={{ fontSize: '12px', color: on ? c : 'var(--text-tertiary)', opacity: 0.8, flexShrink: 0 }}>
            ×{f.inputs.length}
          </span>
        )}
        {f.stateMutability === 'payable' && (
          <span style={{ fontFamily: PIXEL, fontSize: '7px', color: NEON.coin, flexShrink: 0 }}>$</span>
        )}
      </button>
    )
  }

  // ── cards ──
  // Plain render functions, not components: a component defined inside render
  // remounts on every keystroke, and the rename field would lose focus.
  const card = (c: Card) => {
    const key = `${c.source}-${lc(c.address)}`
    const active = !!target && lc(target.address) === lc(c.address)
    const fnList = (c.abi || []).filter((f: any) => f.type === 'function')
    const reads = fnList.filter((f: any) => f.stateMutability === 'view' || f.stateMutability === 'pure').length
    const writes = fnList.length - reads
    const tag = SOURCE_TAG[c.source]
    const tagColor = c.hidden ? 'var(--text-tertiary)' : c.pinned ? NEON.coin : tag.color
    const edge = active ? ACCENT : c.pinned ? NEON.coin : 'var(--border-color)'
    return (
      <div
        key={key}
        role="button"
        tabIndex={0}
        title={c.address}
        onClick={() => pick(c)}
        onKeyDown={e => {
          // keys typed into the rename field bubble up here — those are not ours
          if (e.target !== e.currentTarget) return
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(c) }
        }}
        className="arc-press"
        style={{
          ...panelStyle,
          borderColor: edge,
          boxShadow: active ? `3px 3px 0 0 ${ACCENT}` : panelStyle.boxShadow,
          background: active ? `${ACCENT}14` : panelStyle.background,
          opacity: c.hidden ? 0.5 : 1,
          padding: '10px 12px',
          flex: mobile ? '1 1 100%' : '1 1 230px',
          maxWidth: mobile ? undefined : '340px',
          minWidth: 0,
          display: 'flex', flexDirection: 'column', gap: '6px',
          cursor: 'pointer', outline: 'none',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0 }}>
          {renaming === key ? (
            <div onClick={e => e.stopPropagation()} style={{ display: 'flex', gap: '6px', alignItems: 'center', flex: 1, minWidth: 0 }}>
              <Input value={rename} onChange={setRename} onEnter={() => saveName(c)} placeholder={c.origName} autoFocus />
              <Btn size="sm" onClick={() => saveName(c)}>OK</Btn>
              <Btn size="sm" active={false} onClick={() => setRenaming(null)}>✕</Btn>
            </div>
          ) : (
            <span style={{
              fontFamily: PIXEL, fontSize: PX.sm, lineHeight: 1.6,
              color: active ? ACCENT : 'var(--text-primary)',
              flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }}>
              {c.name}
            </span>
          )}
          <span style={{
            fontFamily: PIXEL, fontSize: '7px', letterSpacing: '0.1em', color: tagColor,
            border: `2px solid ${tagColor}`, padding: '2px 4px', flexShrink: 0, opacity: 0.9,
          }}>
            {c.hidden ? 'HIDDEN' : c.pinned ? 'PINNED' : tag.label}
          </span>
        </div>

        <div style={{
          fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-tertiary)',
          display: 'flex', gap: '10px', flexWrap: 'wrap',
        }}>
          <span>{short(c.address, 8, 6)}</span>
          <span>
            <span style={{ color: READ }}>○ {reads}</span>
            {' · '}
            <span style={{ color: WRITE }}>● {writes}</span>
          </span>
          {c.abiCid && <span style={{ color: READ, opacity: 0.8 }}>abi {short(c.abiCid, 5, 4)}</span>}
        </div>

        {manage && (
          <div onClick={e => e.stopPropagation()} style={{
            display: 'flex', gap: '2px', flexWrap: 'wrap', alignItems: 'center',
            marginTop: '2px', marginLeft: '-6px', borderTop: '1px solid var(--border-color)', paddingTop: '4px',
          }}>
            <Quiet color={c.pinned ? NEON.coin : undefined} onClick={() => pin(c)}
              title={c.pinned ? 'unpin' : 'pin to the top'}>
              {c.pinned ? 'UNPIN' : 'PIN'}
            </Quiet>
            <Quiet onClick={() => { setRenaming(key); setRename(c.name === c.origName ? '' : c.name) }} title="rename this card">
              RENAME
            </Quiet>
            {c.source !== 'saved' && (
              <Quiet onClick={() => hide(c)} title={c.hidden ? 'show it in the deck again' : 'hide it from the deck'}>
                {c.hidden ? 'SHOW' : 'HIDE'}
              </Quiet>
            )}
            {c.source === 'saved' && (
              confirmForget === key ? (
                <Quiet color={NEON.dead} onClick={() => forget(c)} title="drop the card — the contract stays on chain">
                  CONFIRM FORGET
                </Quiet>
              ) : (
                <Quiet color={NEON.dead} onClick={() => setConfirmForget(key)} title="drop the card">FORGET</Quiet>
              )
            )}
            {c.name !== c.origName && (
              <Quiet onClick={() => { nameContractCard(network, c.address, ''); reload() }} title={`back to “${c.origName}”`}>
                RESET NAME
              </Quiet>
            )}
          </div>
        )}
      </div>
    )
  }

  const group = (label: string, items: Card[], color?: string) => (
    items.length === 0 ? null : (
      <div style={{ marginBottom: '14px' }}>
        <Label style={color ? { color } : undefined} note={`${items.length}`}>{label}</Label>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          {items.map(card)}
        </div>
      </div>
    )
  )

  return (
    <div>
      {/* deck toolbar */}
      <div style={{ display: 'flex', gap: '6px', alignItems: 'center', flexWrap: 'wrap', marginBottom: '14px' }}>
        <Btn size="sm" color={NEON.coin} active={manage} onClick={() => { setManage(m => !m); setRenaming(null); setConfirmForget(null) }}
          title="pin, rename, hide or forget cards">
          {manage ? 'DONE' : 'MANAGE'}
        </Btn>
        <Btn size="sm" active={showManual} onClick={() => setShowManual(s => !s)}>
          + ANY CONTRACT
        </Btn>
        {hiddenCount > 0 && (
          <Btn size="sm" active={showHidden} onClick={() => setShowHidden(s => !s)}>
            {showHidden ? 'HIDE' : 'SHOW'} {hiddenCount} HIDDEN
          </Btn>
        )}
        {manage && (
          <span style={{ fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-tertiary)', lineHeight: 1.5 }}>
            cards are yours to arrange — nothing here touches the chain
          </span>
        )}
      </div>

      {showManual && (
        <Panel style={{ marginBottom: '16px' }}>
          <Label style={{ color: NEON.coin }} note="joins the deck as a SAVED card">ANY CONTRACT</Label>
          <Label>NAME</Label>
          <Input value={manualName} onChange={setManualName} placeholder="what to call it (optional)" />

          <Label style={{ marginTop: '14px' }}>ADDRESS</Label>
          <Input value={manualAddr} onChange={setManualAddr} placeholder="0x…" />

          <Label style={{ marginTop: '14px', color: READ }} note="from the store">ABI CID</Label>
          <Input value={manualCid} onChange={setManualCid} placeholder="Qm…" onEnter={loadCid} />
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginTop: '8px', flexWrap: 'wrap' }}>
            <Btn size="sm" color={READ} onClick={loadCid} disabled={fetchingCid || !manualCid.trim()}>
              {fetchingCid ? 'LOADING…' : 'LOAD FROM CID'}
            </Btn>
            <span style={{ fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-tertiary)', lineHeight: 1.5 }}>
              every deploy stores its ABI — the CID is on the build
            </span>
          </div>

          <Label style={{ marginTop: '14px' }} note="paste the JSON instead">OR ABI</Label>
          <textarea
            value={manualAbi}
            onChange={e => setManualAbi(e.target.value)}
            spellCheck={false}
            autoCapitalize="off"
            autoCorrect="off"
            placeholder='[{"type":"function","name":"…"}]'
            style={{
              width: '100%', height: '120px', fontFamily: TERM_FONT,
              fontSize: mobile ? '16px' : '14px',
              padding: '8px', border: '1px solid var(--border-color)', background: 'transparent',
              color: 'var(--text-primary)', outline: 'none', resize: 'vertical',
            }}
          />
          <div style={{ marginTop: '10px', display: 'flex', gap: '6px' }}>
            <Btn size="sm" onClick={addManual} disabled={!manualAbi.trim()}>LOAD PASTED ABI</Btn>
            <Btn size="sm" active={false} onClick={() => setShowManual(false)}>CANCEL</Btn>
          </div>
        </Panel>
      )}

      {loading ? <Empty>Loading contracts…</Empty> : (
        <>
          {group('PINNED', deck.pinned, NEON.coin)}
          {group(`MY BUILDS — ${network}`, deck.build, ACCENT)}
          {group(`FLEET CONTRACTS — ${network}`, deck.fleet)}
          {group(`SAVED — ${network}`, deck.saved)}
          {cards.length === 0 && (
            <Empty>No contracts on {network} yet — deploy from BUILD, or add one with + ANY CONTRACT.</Empty>
          )}
          {cards.length > 0 && deck.pinned.length + deck.build.length + deck.fleet.length + deck.saved.length === 0 && (
            <Empty>Every card is hidden — SHOW {hiddenCount} HIDDEN brings them back.</Empty>
          )}
        </>
      )}

      {!target ? (
        <div style={{
          ...panelStyle, padding: mobile ? '20px 16px' : '28px 24px', textAlign: 'center',
          fontFamily: TERM_FONT, color: 'var(--text-tertiary)', lineHeight: 1.6,
        }}>
          <div style={{ fontFamily: PIXEL, fontSize: PX.sm, color: ACCENT, letterSpacing: '0.1em', marginBottom: '8px', lineHeight: 1.7 }}>
            INSERT CONTRACT
          </div>
          <div style={{ fontSize: '14px' }}>
            Pick one above — a build of yours, one of the fleet&apos;s, or any address with an ABI.
          </div>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>

          {/* ── the contract ── */}
          <div style={{ ...panelStyle, borderColor: ACCENT, boxShadow: `4px 4px 0 0 ${ACCENT}55` }}>
            <div style={{
              display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap',
              padding: mobile ? '12px' : '12px 16px', borderBottom: '2px solid var(--border-color)',
            }}>
              <span style={{ fontFamily: PIXEL, fontSize: PX.md, color: ACCENT, letterSpacing: '0.06em', lineHeight: 1.6, textShadow: `0 0 10px ${ACCENT}66` }}>
                {target.name}
              </span>
              <span style={{ fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-tertiary)' }}>
                {reads.length} read · {writes.length} write
              </span>
              <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginLeft: 'auto' }}>
                <Btn size="sm" active={false} onClick={() => {
                  navigator.clipboard.writeText(target.address); toast.success('Address copied')
                }}>COPY</Btn>
                <Btn size="sm" active={!!targetCard?.pinned} color={NEON.coin} onClick={() => pin(target)}>
                  {targetCard?.pinned ? 'PINNED' : 'PIN'}
                </Btn>
                {target.abiCid && (
                  <Btn size="sm" active={false} color={READ} onClick={() => {
                    navigator.clipboard.writeText(target.abiCid!); toast.success('ABI CID copied')
                  }}>ABI CID</Btn>
                )}
                {explorerUrl(network, target.address) && (
                  <Ext href={explorerUrl(network, target.address)}>EXPLORER ↗</Ext>
                )}
              </div>
            </div>
            <div style={{ padding: mobile ? '10px 12px' : '10px 16px', fontFamily: TERM_FONT, fontSize: mobile ? '13px' : '14px', lineHeight: 1.6 }}>
              <div style={{ color: 'var(--text-secondary)', wordBreak: 'break-all' }}>{target.address}</div>
              {target.abiCid && (
                <div style={{ color: READ, wordBreak: 'break-all', opacity: 0.9 }}>abi {target.abiCid}</div>
              )}
            </div>
          </div>

          {/* ── methods ── */}
          {fns.length === 0 ? (
            <Empty>This ABI has no callable functions.</Empty>
          ) : (
            <div style={{
              display: 'grid', gap: '16px',
              gridTemplateColumns: mobile || !writes.length || !reads.length ? '1fr' : '1fr 1fr',
            }}>
              {reads.length > 0 && (
                <div>
                  <Label style={{ color: READ }} note="free — asks the RPC">READ</Label>
                  <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>{reads.map(method)}</div>
                </div>
              )}
              {writes.length > 0 && (
                <div>
                  <Label style={{ color: WRITE }} note="signed by your wallet · costs gas">WRITE</Label>
                  <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>{writes.map(method)}</div>
                </div>
              )}
            </div>
          )}

          {/* ── the call ── */}
          {current && (
            <div style={{ ...panelStyle, borderColor: color, boxShadow: `4px 4px 0 0 ${color}66` }}>
              <div style={{
                display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap',
                padding: mobile ? '10px 12px' : '10px 16px', borderBottom: `2px solid ${color}44`,
                background: `${color}0d`,
              }}>
                <span style={{ fontFamily: PIXEL, fontSize: PX.xs, color, letterSpacing: '0.12em' }}>
                  {isRead(current) ? 'READ' : current.stateMutability === 'payable' ? 'WRITE · PAYABLE' : 'WRITE'}
                </span>
                <span style={{ fontFamily: TERM_FONT, fontSize: mobile ? '14px' : '15px', color: 'var(--text-primary)', wordBreak: 'break-word' }}>
                  {signature(current)}
                </span>
              </div>

              <div style={{ padding: mobile ? '12px' : '14px 16px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
                {current.inputs.length > 0 && (
                  <div style={{
                    display: 'grid', gap: '10px',
                    gridTemplateColumns: mobile ? '1fr' : `repeat(auto-fit, minmax(240px, 1fr))`,
                  }}>
                    {current.inputs.map((inp, i) => {
                      const key = inp.name || `arg${i}`
                      return (
                        <div key={key}>
                          <div style={{ fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-tertiary)', marginBottom: '4px' }}>
                            <span style={{ color }}>{inp.name || `arg${i}`}</span>
                            <span style={{ opacity: 0.7 }}> · {inp.type}</span>
                          </div>
                          <Input
                            value={args[key] || ''}
                            onChange={v => setArgs(prev => ({ ...prev, [key]: v }))}
                            placeholder={placeholderFor(inp.type)}
                            onEnter={canSend && !busy ? execute : undefined}
                          />
                        </div>
                      )
                    })}
                  </div>
                )}

                {current.stateMutability === 'payable' && (
                  <div style={{ maxWidth: mobile ? '100%' : '240px' }}>
                    <div style={{ fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-tertiary)', marginBottom: '4px' }}>
                      <span style={{ color: NEON.coin }}>value</span>
                      <span style={{ opacity: 0.7 }}> · ETH sent with the call</span>
                    </div>
                    <Input value={value} onChange={setValue} placeholder="0.0" />
                  </div>
                )}

                <div style={{ display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }}>
                  <Btn onClick={execute} disabled={busy || !canSend} full color={color}>
                    {busy
                      ? <span className="arc-blink">{isRead(current) ? 'CALLING…' : 'SENDING…'}</span>
                      : isRead(current) ? `▶ CALL ${current.name}` : `▲ SEND ${current.name}`}
                  </Btn>
                  {!isRead(current) && (
                    <span style={{ fontFamily: TERM_FONT, fontSize: '13px', color: wallet.kind ? 'var(--text-tertiary)' : WRITE }}>
                      {wallet.kind
                        ? `signs as ${short(wallet.address, 6, 4)} [${wallet.kind.toUpperCase()}]`
                        : 'pick a PLAYER up top to send'}
                    </span>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* ── outcome ── */}
          {error && (
            <div style={{
              ...panelStyle, borderColor: DANGER, boxShadow: `4px 4px 0 0 ${DANGER}66`,
              padding: mobile ? '12px' : '12px 16px',
            }}>
              <Label style={{ color: DANGER }}>REVERTED</Label>
              <div style={{ fontFamily: TERM_FONT, fontSize: '14px', color: DANGER, wordBreak: 'break-word', lineHeight: 1.5 }}>
                {error}
              </div>
            </div>
          )}

          {result !== null && !error && (
            <div style={{ ...panelStyle, borderColor: color, boxShadow: `4px 4px 0 0 ${color}66` }}>
              <div style={{
                display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap',
                padding: mobile ? '10px 12px' : '10px 16px', borderBottom: `2px solid ${color}44`,
              }}>
                <span style={{ fontFamily: PIXEL, fontSize: PX.xs, color, letterSpacing: '0.12em' }}>
                  {result?.tx_hash ? (result.status === 'success' ? 'CONFIRMED' : 'FAILED') : 'RESULT'}
                </span>
                {took !== null && (
                  <span style={{ fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-tertiary)' }}>
                    {took < 1000 ? `${Math.round(took)}ms` : `${(took / 1000).toFixed(1)}s`}
                  </span>
                )}
                <div style={{ marginLeft: 'auto', display: 'flex', gap: '6px' }}>
                  <Btn size="sm" active={false} onClick={() => {
                    navigator.clipboard.writeText(resultText); toast.success('Result copied')
                  }}>COPY</Btn>
                  {result?.tx_hash && txUrl(network, result.tx_hash) && (
                    <Ext href={txUrl(network, result.tx_hash)}>TX ↗</Ext>
                  )}
                </div>
              </div>
              <pre style={{
                fontFamily: TERM_FONT, fontSize: mobile ? '15px' : '16px', color: 'var(--text-primary)',
                whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0,
                padding: mobile ? '12px' : '14px 16px', lineHeight: 1.5,
                maxHeight: '50vh', overflowY: 'auto',
              }}>
                {resultText}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
