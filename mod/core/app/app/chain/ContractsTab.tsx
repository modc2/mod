"use client"

// CONTRACTS — the high-score table. Every contract this console knows about on
// the selected chain (or all of them): what you deployed, what the fleet runs,
// and anything you've asked it to watch. Each row is manageable — rename it,
// check it's still there, take it to INTERACT, or forget it.
//
// "Forget" only ever drops the record. Nothing here can remove a contract from
// a chain, and the copy says so.

import { useState, useEffect, useCallback } from 'react'
import { ethers } from 'ethers'
import { toast } from 'react-toastify'
import {
  TERM_FONT, ACCENT, READ, chainApi, short, explorerUrl, txUrl, netInfo,
  readProvider, useIsMobile,
} from './shared'
import { Label, Btn, Input, Empty, panelStyle, Skeleton } from './ui'
import { PIXEL, PX, NEON, Led, type LedState } from './arcade'
import type { ChainWallet } from './WalletBar'
import type { InteractTarget } from './InteractTab'

interface Deployment {
  name: string
  network: string
  address: string
  tx_hash?: string
  abi?: any[]
  abi_cid?: string
  src_cid?: string
  created?: number
  watched?: boolean
  note?: string
}

interface FleetRow { name: string; kind: string; address: string }

/** Does this address still have code on that chain? The only check that matters. */
type CodeState = 'checking' | 'live' | 'empty' | 'unknown'

export function ContractsTab({
  wallet, network, onInteract, onNetwork,
}: {
  wallet: ChainWallet
  network: string
  onInteract: (t: InteractTarget) => void
  onNetwork: (key: string) => void
}) {
  const [mine, setMine] = useState<Deployment[]>([])
  const [fleet, setFleet] = useState<FleetRow[]>([])
  const [loading, setLoading] = useState(true)
  const [allChains, setAllChains] = useState(false)
  const [code, setCode] = useState<Record<string, CodeState>>({})
  const [renaming, setRenaming] = useState<string | null>(null)
  const [rename, setRename] = useState('')
  const [confirmDrop, setConfirmDrop] = useState<string | null>(null)

  const [watching, setWatching] = useState(false)
  const [watch, setWatch] = useState({ name: '', address: '', abi: '', cid: '' })
  const [adding, setAdding] = useState(false)

  const mobile = useIsMobile()
  const net = netInfo(network)
  const rowKey = (d: { network: string; address: string }) => `${d.network}:${d.address.toLowerCase()}`

  // ── is it still there? ──
  // A recorded deployment can outlive its chain: a ganache restart wipes every
  // address, and there's no way to tell from the record alone. So every row
  // gets asked, on arrival — a list of addresses nobody has checked is exactly
  // the list you can't trust.
  const checkCode = useCallback(async (rows: { network: string; address: string }[]) => {
    setCode(prev => ({
      ...prev, ...Object.fromEntries(rows.map(r => [rowKey(r), 'checking' as CodeState])),
    }))
    await Promise.all(rows.map(async r => {
      try {
        const bytes = await readProvider(r.network).getCode(r.address)
        setCode(prev => ({ ...prev, [rowKey(r)]: bytes && bytes !== '0x' ? 'live' : 'empty' }))
      } catch {
        setCode(prev => ({ ...prev, [rowKey(r)]: 'unknown' }))
      }
    }))
  }, [])

  const load = useCallback(() => {
    setLoading(true)
    const q = wallet.address ? `?address=${wallet.address}&` : '?'
    const scope = allChains ? '' : `network=${network}`
    Promise.all([
      chainApi(`/build/deployments${q}${scope}`).catch(() => ({ deployments: [] })),
      net.fleet
        ? chainApi('/contracts', { body: { network } }).catch(() => ({ contracts: {} }))
        : Promise.resolve({ contracts: {} }),
    ]).then(([b, c]) => {
      const deployments: Deployment[] = b.deployments || []
      const fleetRows = Object.entries(c.contracts || {}).map(([name, info]: [string, any]) => ({
        name, kind: info?.contract || '', address: info?.address || '',
      })).filter(r => r.address)
      setMine(deployments)
      setFleet(fleetRows)
      setLoading(false)
      checkCode([
        ...deployments.map(d => ({ network: d.network, address: d.address })),
        ...fleetRows.map(r => ({ network, address: r.address })),
      ])
    })
  }, [wallet.address, network, allChains, net.fleet, checkCode])

  useEffect(() => { load() }, [load])

  const drop = async (d: Deployment) => {
    try {
      await chainApi(
        `/build/deployments?contract_address=${d.address}&network=${d.network}`
        + (wallet.address ? `&address=${wallet.address}` : ''),
        { method: 'DELETE' },
      )
      toast.success(`${d.name} forgotten — the contract is still on ${netInfo(d.network).name}`)
      setConfirmDrop(null)
      load()
    } catch (e: any) {
      toast.error(e?.message || 'could not forget it')
    }
  }

  const saveName = async (d: Deployment) => {
    const name = rename.trim()
    if (!name) { toast.error('name cannot be empty'); return }
    try {
      await chainApi('/build/deployments/edit', {
        body: { address: wallet.address || undefined, network: d.network, contract_address: d.address, name },
      })
      setRenaming(null)
      load()
    } catch (e: any) {
      toast.error(e?.message || 'rename failed')
    }
  }

  const addWatch = async () => {
    const addr = watch.address.trim()
    if (!ethers.isAddress(addr)) { toast.error('that is not an address'); return }
    setAdding(true)
    try {
      let abi: any[] = []
      if (watch.cid.trim()) {
        abi = (await chainApi(`/build/abi/${watch.cid.trim()}`)).abi || []
      } else if (watch.abi.trim()) {
        abi = JSON.parse(watch.abi)
        if (!Array.isArray(abi)) throw new Error('an ABI is a JSON array')
      } else {
        throw new Error('paste an ABI or give its CID — without one there is nothing to call')
      }
      await chainApi('/build/deployments', {
        body: {
          address: wallet.address || undefined, network,
          name: watch.name.trim() || `contract-${short(addr, 4, 4)}`,
          contract_address: ethers.getAddress(addr), abi, watched: true,
        },
      })
      toast.success('added to your contracts')
      setWatch({ name: '', address: '', abi: '', cid: '' })
      setWatching(false)
      load()
    } catch (e: any) {
      toast.error(e?.message || 'could not add it')
    } finally {
      setAdding(false)
    }
  }

  const copy = (text: string, what: string) => {
    navigator.clipboard.writeText(text)
    toast.success(`${what} copied`)
  }

  const ledFor = (state: CodeState | undefined): LedState =>
    state === 'live' ? 'live' : state === 'empty' ? 'dead' : state === 'unknown' ? 'warn' : 'idle'

  const codeLabel = (state: CodeState | undefined) =>
    state === 'live' ? 'ON CHAIN'
      : state === 'empty' ? 'NO CODE AT THIS ADDRESS'
        : state === 'unknown' ? 'RPC WOULD NOT SAY'
          : state === 'checking' ? 'checking…' : ''

  // ── my deployments ──
  const deploymentCard = (d: Deployment) => {
    const key = rowKey(d)
    const state = code[key]
    const dnet = netInfo(d.network)
    return (
      <div key={key} style={{
        ...panelStyle, padding: '12px 14px',
        borderColor: state === 'empty' ? `${NEON.dead}88` : 'var(--border-color)',
        display: 'flex', flexDirection: 'column', gap: '9px',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          <Led state={ledFor(state)} />
          {renaming === key ? (
            <div style={{ display: 'flex', gap: '6px', alignItems: 'center', flex: 1, minWidth: '200px' }}>
              <Input value={rename} onChange={setRename} onEnter={() => saveName(d)} placeholder="name" />
              <Btn size="sm" onClick={() => saveName(d)}>OK</Btn>
              <Btn size="sm" active={false} onClick={() => setRenaming(null)}>✕</Btn>
            </div>
          ) : (
            <span style={{ fontFamily: PIXEL, fontSize: PX.md, color: 'var(--text-primary)' }}>
              {d.name}
            </span>
          )}
          {d.watched && (
            <span style={{
              fontFamily: PIXEL, fontSize: PX.xs, color: NEON.p2,
              border: `2px solid ${NEON.p2}`, padding: '2px 4px',
            }}>WATCHED</span>
          )}
          {allChains && (
            <button
              onClick={() => onNetwork(d.network)}
              className="arc-pixel"
              title={`switch the console to ${dnet.name}`}
              style={{
                fontFamily: PIXEL, fontSize: PX.xs, color: NEON.coin, cursor: 'pointer',
                border: `2px solid ${NEON.coin}`, padding: '2px 4px', background: 'transparent',
              }}
            >
              {dnet.name.toUpperCase()}
            </button>
          )}
        </div>

        <div style={{
          fontFamily: TERM_FONT, fontSize: '14px', color: 'var(--text-secondary)',
          display: 'flex', gap: '14px', flexWrap: 'wrap',
        }}>
          <span title={d.address} style={{ wordBreak: 'break-all' }}>{short(d.address, 10, 8)}</span>
          {d.created ? (
            <span style={{ color: 'var(--text-tertiary)' }}>
              {new Date(d.created * 1000).toLocaleDateString()}
            </span>
          ) : null}
          <span style={{ fontSize: '13px', color: 'var(--text-tertiary)' }}>{codeLabel(state)}</span>
          {d.abi_cid && (
            <button onClick={() => copy(d.abi_cid!, 'ABI CID')} title={d.abi_cid} style={{
              fontFamily: TERM_FONT, fontSize: '13px', color: READ, background: 'none',
              border: 'none', padding: 0, cursor: 'pointer',
            }}>
              abi {short(d.abi_cid, 6, 4)} ⧉
            </button>
          )}
        </div>

        <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginTop: 'auto' }}>
          <Btn size="sm" disabled={!d.abi?.length}
            title={d.abi?.length ? undefined : 'no ABI recorded for this one'}
            onClick={() => {
              if (d.network !== network) onNetwork(d.network)
              onInteract({ name: d.name, address: d.address, abi: d.abi || [], abiCid: d.abi_cid })
            }}>
            INTERACT
          </Btn>
          <Btn size="sm" active={false} onClick={() => copy(d.address, 'Address')}>COPY</Btn>
          <Btn size="sm" active={false} onClick={() => checkCode([d])}>VERIFY</Btn>
          <Btn size="sm" active={false} onClick={() => { setRenaming(key); setRename(d.name) }}>RENAME</Btn>
          {confirmDrop === key ? (
            <Btn size="sm" color={NEON.dead} onClick={() => drop(d)}>CONFIRM — FORGET IT</Btn>
          ) : (
            <Btn size="sm" active={false} color={NEON.dead} onClick={() => setConfirmDrop(key)}>FORGET</Btn>
          )}
          {explorerUrl(d.network, d.address) && (
            <a href={explorerUrl(d.network, d.address)} target="_blank" rel="noreferrer" style={linkStyle}>
              CONTRACT<Out />
            </a>
          )}
          {d.tx_hash && txUrl(d.network, d.tx_hash) && (
            <a href={txUrl(d.network, d.tx_hash)} target="_blank" rel="noreferrer" style={linkStyle}>
              TX<Out />
            </a>
          )}
        </div>
      </div>
    )
  }

  const fleetCard = (r: FleetRow) => {
    const key = rowKey({ network, address: r.address })
    return (
      <div key={key} style={{
        ...panelStyle, padding: '12px 14px',
        display: 'flex', flexDirection: 'column', gap: '9px',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0 }}
          title={codeLabel(code[key])}>
          <Led state={ledFor(code[key])} />
          <div style={{ minWidth: 0 }}>
            <div style={{ fontFamily: PIXEL, fontSize: PX.sm, color: 'var(--text-primary)' }}>{r.name}</div>
            <div style={{ fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-tertiary)' }}>
              {r.kind || 'fleet contract'} · {short(r.address, 8, 6)}
            </div>
          </div>
        </div>
        <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginTop: 'auto' }}>
          <Btn size="sm" active={false} onClick={() => copy(r.address, 'Address')}>COPY</Btn>
          <Btn size="sm" active={false} onClick={() => checkCode([{ network, address: r.address }])}>VERIFY</Btn>
          {explorerUrl(network, r.address) && (
            <a href={explorerUrl(network, r.address)} target="_blank" rel="noreferrer" style={linkStyle}>EXPLORER<Out /></a>
          )}
        </div>
      </div>
    )
  }

  if (loading) return <Skeleton rows={4} height={62} />

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '18px' }}>

      {/* scope + actions */}
      <div style={{ display: 'flex', gap: '6px', alignItems: 'center', flexWrap: 'wrap' }}>
        <Btn size="sm" active={!allChains} onClick={() => setAllChains(false)}>{net.name.toUpperCase()}</Btn>
        <Btn size="sm" active={allChains} onClick={() => setAllChains(true)}>ALL CHAINS</Btn>
        <div style={{ marginLeft: mobile ? 0 : 'auto', display: 'flex', gap: '6px' }}>
          <Btn size="sm" active={false}
            onClick={() => checkCode([
              ...mine.map(d => ({ network: d.network, address: d.address })),
              ...fleet.map(r => ({ network, address: r.address })),
            ])}>
            VERIFY ALL
          </Btn>
          <Btn size="sm" color={NEON.coin} active={!watching} onClick={() => setWatching(w => !w)}>
            + WATCH CONTRACT
          </Btn>
        </div>
      </div>

      {watching && (
        <div style={{ ...panelStyle, padding: '14px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <Label style={{ color: NEON.coin }} note={net.name}>WATCH A CONTRACT</Label>
          <div style={{ fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-tertiary)', lineHeight: 1.6 }}>
            Any contract, deployed by anyone. It joins your list and INTERACT can call it —
            give it an ABI by CID or paste the JSON.
          </div>
          <Input value={watch.address} onChange={v => setWatch(w => ({ ...w, address: v }))} placeholder="0x… contract address" />
          <Input value={watch.name} onChange={v => setWatch(w => ({ ...w, name: v }))} placeholder="name it (optional)" />
          <Input value={watch.cid} onChange={v => setWatch(w => ({ ...w, cid: v }))} placeholder="ABI CID (or paste the ABI below)" />
          <textarea
            value={watch.abi}
            onChange={e => setWatch(w => ({ ...w, abi: e.target.value }))}
            placeholder='[{"type":"function","name":"…"}]'
            spellCheck={false}
            style={{
              width: '100%', minHeight: '90px', fontFamily: TERM_FONT,
              fontSize: mobile ? '16px' : '13px', padding: '10px',
              border: '2px solid var(--border-color)', background: 'rgba(0,0,0,0.25)',
              color: 'var(--text-primary)', outline: 'none', resize: 'vertical',
            }}
          />
          <div style={{ display: 'flex', gap: '8px' }}>
            <Btn size="sm" onClick={addWatch} disabled={adding}>{adding ? 'ADDING…' : 'ADD'}</Btn>
            <Btn size="sm" active={false} onClick={() => setWatching(false)}>CANCEL</Btn>
          </div>
        </div>
      )}

      {/* mine */}
      <div>
        <Label style={{ color: ACCENT }}>
          MY CONTRACTS — {mine.length} {allChains ? 'ACROSS EVERY CHAIN' : `ON ${net.name.toUpperCase()}`}
        </Label>
        {mine.length === 0 ? (
          <Empty>
            {wallet.address
              ? 'Nothing here yet — deploy from BUILD, or WATCH a contract that already exists.'
              : 'Sign in above and your deployments show up here.'}
          </Empty>
        ) : (
          <div style={cardGrid(mobile)}>{mine.map(deploymentCard)}</div>
        )}
      </div>

      {/* fleet */}
      {net.fleet && (
        <div>
          <Label>FLEET — {net.name.toUpperCase()}</Label>
          {fleet.length === 0
            ? <Empty>No fleet contracts deployed on {net.name}.</Empty>
            : <div style={cardGrid(mobile)}>{fleet.map(fleetCard)}</div>}
        </div>
      )}
    </div>
  )
}

const cardGrid = (mobile: boolean) => ({
  display: 'grid', gap: '10px',
  gridTemplateColumns: mobile ? '1fr' : 'repeat(auto-fill, minmax(340px, 1fr))',
} as const)

const linkStyle = {
  fontFamily: PIXEL, fontSize: PX.xs, padding: '7px 10px', lineHeight: 1.6,
  border: '2px solid var(--border-color)', color: 'var(--text-tertiary)',
  textDecoration: 'none',
} as const

/**
 * The "opens elsewhere" arrow. Press Start 2P has no ↗, so set in linkStyle's
 * pixel face it draws as tofu — a comma-shaped box. It gets the terminal face
 * of its own, which does have the glyph.
 */
const Out = () => (
  <span style={{ fontFamily: TERM_FONT, fontSize: '13px', marginLeft: '5px' }}>↗</span>
)
