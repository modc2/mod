"use client";

import { useState, useEffect, useCallback, useMemo } from 'react'
import dynamic from 'next/dynamic'
import { toast } from 'react-toastify'
import {
  ShieldCheckIcon,
  ArrowPathIcon,
  ArrowUpRightIcon,
  BoltIcon,
  QueueListIcon,
  TrashIcon,
  ArrowDownTrayIcon,
  ClipboardDocumentIcon,
  KeyIcon,
  ExclamationTriangleIcon,
  CheckBadgeIcon,
  NoSymbolIcon,
} from '@heroicons/react/24/outline'
import { Shell, NetworkSelect, RefreshBtn } from '../components/Shell'

// ── Constants ────────────────────────────────────────────────────────────────

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8800'

const EXPLORER_URLS: Record<string, string> = {
  testnet: 'https://sepolia.basescan.org',
  mainnet: 'https://basescan.org',
  ganache: '',
}

const CHAIN_NAMES: Record<string, string> = {
  testnet: 'Base Sepolia',
  mainnet: 'Base',
  ganache: 'Ganache',
}

const CHAIN_IDS: Record<string, string> = {
  testnet: '84532',
  mainnet: '8453',
  ganache: '1337',
}

const ZERO = '0x0000000000000000000000000000000000000000'

// ── Admin spec: owner-only methods per contract ──────────────────────────────
//
// `key` matches the backend module key (lowercased config name). Each method's
// `inputs` carry Solidity types so we can render forms AND build Safe batch
// entries. `json` fields take a JSON literal (for struct arrays like setPoints).

type Input = { name: string; type: string; placeholder?: string; help?: string; json?: boolean }
type Method = { method: string; label: string; help?: string; danger?: boolean; inputs: Input[]; noArgs?: boolean }
type Group = { key: string; title: string; accent: string; methods: Method[] }

const ADDR = (name: string, ph = '0x...') => ({ name, type: 'address', placeholder: ph })

const GROUPS: Group[] = [
  {
    key: 'tokengate', title: 'TokenGate', accent: 'rose',
    methods: [
      { method: 'whitelistToken', label: 'Whitelist token', help: 'Allow a token as protocol payment.', inputs: [ADDR('token')] },
      { method: 'batchWhitelistTokens', label: 'Batch whitelist', help: 'Comma-separated addresses.', inputs: [{ name: 'tokens', type: 'address[]', placeholder: '0xaaa..., 0xbbb...' }] },
      { method: 'delistToken', label: 'Delist token', danger: true, inputs: [ADDR('token')] },
      { method: 'setDefaultOracle', label: 'Set default oracle', inputs: [ADDR('oracle')] },
      { method: 'registerTokenOracle', label: 'Register token oracle', inputs: [ADDR('token'), ADDR('oracle')] },
      { method: 'removeTokenOracle', label: 'Remove token oracle', danger: true, inputs: [ADDR('token')] },
    ],
  },
  {
    key: 'manualpriceoracle', title: 'Price Oracle', accent: 'amber',
    methods: [
      { method: 'setPrice', label: 'Set price', help: 'Price in oracle units; decimals = its precision.', inputs: [ADDR('token'), { name: 'price', type: 'uint256', placeholder: '100000000' }, { name: 'decimals', type: 'uint8', placeholder: '8' }] },
      { method: 'batchSetPrices', label: 'Batch set prices', help: 'Three comma-separated lists (same length).', inputs: [{ name: 'tokens', type: 'address[]', placeholder: '0x.., 0x..' }, { name: 'prices', type: 'uint256[]', placeholder: '100000000, 1000000' }, { name: 'decimals', type: 'uint8[]', placeholder: '8, 6' }] },
      { method: 'removePrice', label: 'Remove price', danger: true, inputs: [ADDR('token')] },
    ],
  },
  {
    key: 'market', title: 'Market', accent: 'orange',
    methods: [
      { method: 'setCreditFee', label: 'Set credit fee', help: 'Basis points (100 = 1%).', inputs: [{ name: 'creditFeeBps', type: 'uint256', placeholder: '100' }] },
      { method: 'pause', label: 'Pause market', danger: true, noArgs: true, inputs: [] },
      { method: 'unpause', label: 'Unpause market', noArgs: true, inputs: [] },
      { method: 'setTreasury', label: 'Set treasury', inputs: [ADDR('treasury')] },
      { method: 'setTokenGate', label: 'Set token gate', inputs: [ADDR('tokenGate')] },
      { method: 'setDebitContract', label: 'Set debit contract', inputs: [ADDR('debitContract')] },
    ],
  },
  {
    key: 'treasury', title: 'Treasury', accent: 'lime',
    methods: [
      { method: 'setOwnerPercentage', label: 'Set owner percentage', help: 'Basis points (10000 = 100%).', inputs: [{ name: 'percentage', type: 'uint256', placeholder: '500' }] },
      { method: 'setGovernanceToken', label: 'Set governance token', inputs: [ADDR('governanceToken')] },
      { method: 'setTokenGate', label: 'Set token gate', inputs: [ADDR('tokenGate')] },
      { method: 'ownerWithdraw', label: 'Owner withdraw', help: "Withdraw owner's share of a token.", inputs: [ADDR('token')] },
      { method: 'emergencyWithdraw', label: 'Emergency withdraw', danger: true, help: 'Amount in wei.', inputs: [ADDR('token'), { name: 'amount', type: 'uint256', placeholder: '0 (wei)' }] },
    ],
  },
  {
    key: 'bloctime', title: 'BlocTime', accent: 'sky',
    methods: [
      { method: 'setParams', label: 'Set params', help: 'Max lock blocks + distribution %.', inputs: [{ name: 'maxLockBlocks', type: 'uint256', placeholder: '100000' }, { name: 'distributionPercentage', type: 'uint256', placeholder: '5000' }] },
      { method: 'setPoints', label: 'Set curve points', help: 'JSON: [[blocks, multiplierBps], ...]', inputs: [{ name: 'points', type: 'tuple[]', json: true, placeholder: '[[100,10000],[100000,30000]]' }] },
      { method: 'emergencyWithdraw', label: 'Emergency withdraw', danger: true, help: 'Amount in wei.', inputs: [ADDR('token'), { name: 'amount', type: 'uint256', placeholder: '0 (wei)' }] },
    ],
  },
  {
    key: 'perms', title: 'Perms', accent: 'violet',
    methods: [
      { method: 'setMaxChildKeys', label: 'Set max child keys', inputs: [{ name: 'newMax', type: 'uint256', placeholder: '100' }] },
      { method: 'setMaxKeySize', label: 'Set max key size', inputs: [{ name: 'newMax', type: 'uint256', placeholder: '256' }] },
    ],
  },
]

// ── Types ────────────────────────────────────────────────────────────────────

interface OwnerInfo {
  key: string
  name: string
  address: string
  owner: string | null
  ownerless: boolean
  is_owner: boolean
}

interface CartItem {
  id: string
  label: string
  contract: string
  contractAddress: string
  method: string
  argsDisplay: string
  to: string
  data: string
  value: string
}

// ── API helper ────────────────────────────────────────────────────────────────

async function api(path: string, params: Record<string, any> = {}, method: 'GET' | 'POST' = 'GET') {
  let url = `${API_URL}/${path}`
  const opts: RequestInit = { method }
  if (method === 'GET') {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
        .map(([k, v]) => [k, String(v)])
    ).toString()
    if (qs) url += `?${qs}`
  } else {
    opts.headers = { 'Content-Type': 'application/json' }
    opts.body = JSON.stringify(params)
  }
  const res = await fetch(url, opts)
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || 'Request failed')
  }
  return res.json()
}

const fmtAddr = (s: string | null | undefined, chars = 5) =>
  s && s.length > 14 ? `${s.slice(0, chars + 2)}…${s.slice(-chars)}` : (s || '--')

// Parse a single form value into the JSON arg the backend expects.
function parseValue(inp: Input, raw: string): any {
  const v = raw.trim()
  if (inp.json) return JSON.parse(v)
  if (inp.type.endsWith('[]')) {
    const parts = v.split(',').map(s => s.trim()).filter(Boolean)
    return parts // backend coerces element types from ABI
  }
  return v
}

// ── UI bits ────────────────────────────────────────────────────────────────────

const ACCENTS: Record<string, { dot: string; glow: string }> = {
  rose:   { dot: 'bg-rose-400',   glow: 'rgba(251,113,133,0.5)' },
  amber:  { dot: 'bg-amber-400',  glow: 'rgba(251,191,36,0.5)' },
  orange: { dot: 'bg-orange-400', glow: 'rgba(251,146,60,0.5)' },
  lime:   { dot: 'bg-lime-400',   glow: 'rgba(163,230,53,0.5)' },
  sky:    { dot: 'bg-sky-400',    glow: 'rgba(56,189,248,0.5)' },
  violet: { dot: 'bg-violet-400', glow: 'rgba(167,139,250,0.5)' },
  cyan:   { dot: 'bg-cyan-400',   glow: 'rgba(34,211,238,0.5)' },
  teal:   { dot: 'bg-teal-400',   glow: 'rgba(45,212,191,0.5)' },
}

const inputCls = 'input'

function Card({ title, accent = 'cyan', right, hover, children }: { title?: string; accent?: string; right?: React.ReactNode; hover?: boolean; children: React.ReactNode }) {
  const a = ACCENTS[accent] || ACCENTS.cyan
  return (
    <div className={`glass ${hover ? 'glass-hover' : ''} overflow-hidden`}>
      {title && (
        <div className="px-5 py-4 border-b hairline flex items-center justify-between gap-2">
          <div className="flex items-center gap-2.5">
            <span className={`dot ${a.dot}`} style={{ boxShadow: `0 0 10px 1px ${a.glow}` }} />
            <span className="text-[14px] font-semibold text-white/85 tracking-tight">{title}</span>
          </div>
          {right}
        </div>
      )}
      <div className="p-5 space-y-4">{children}</div>
    </div>
  )
}

// ── Method row ─────────────────────────────────────────────────────────────────

function MethodRow({ group, m, ownerInfo, network, keyName, onQueue, onAfterSend }: {
  group: Group
  m: Method
  ownerInfo?: OwnerInfo
  network: string
  keyName: string
  onQueue: (item: CartItem) => void
  onAfterSend: () => void
}) {
  const [vals, setVals] = useState<string[]>(m.inputs.map(() => ''))
  const [busy, setBusy] = useState<'' | 'send' | 'queue'>('')

  const buildArgs = () => m.inputs.map((inp, i) => parseValue(inp, vals[i]))
  const argsDisplay = () => m.inputs.map((inp, i) => `${inp.name}=${vals[i] || '∅'}`).join(', ')

  const ready = m.noArgs || m.inputs.every((_, i) => vals[i].trim() !== '')

  const doSend = async () => {
    setBusy('send')
    try {
      const args = buildArgs()
      await api('admin/send', { contract: group.key, method: m.method, args, network, key: keyName || undefined }, 'POST')
      toast.success(`${group.title}.${m.method} executed`)
      onAfterSend()
    } catch (e: any) {
      toast.error(e?.message || 'Execution failed')
    }
    setBusy('')
  }

  const doQueue = async () => {
    setBusy('queue')
    try {
      const args = buildArgs()
      const enc = await api('admin/encode', { contract: group.key, method: m.method, args, network }, 'POST')
      onQueue({
        id: `${group.key}.${m.method}.${Math.round(Math.random() * 1e9)}`,
        label: `${group.title}.${m.method}`,
        contract: group.title,
        contractAddress: enc.to,
        method: m.method,
        argsDisplay: argsDisplay(),
        to: enc.to,
        data: enc.data,
        value: enc.value || '0',
      })
      toast.success(`Queued ${m.method} for Safe`)
    } catch (e: any) {
      toast.error(e?.message || 'Encode failed')
    }
    setBusy('')
  }

  return (
    <div className="rounded-xl border hairline bg-white/[0.02] p-3.5 space-y-2.5">
      <div className="flex items-center justify-between">
        <span className={`text-[13px] font-medium ${m.danger ? 'text-rose-300' : 'text-white/75'}`}>
          {m.label}
          {m.danger && <ExclamationTriangleIcon className="inline w-3.5 h-3.5 ml-1 -mt-0.5 text-rose-400/70" />}
        </span>
        <code className="text-[10px] text-white/20 font-mono">{m.method}</code>
      </div>
      {m.help && <p className="text-[11px] text-white/35 leading-relaxed">{m.help}</p>}
      {m.inputs.map((inp, i) => (
        inp.json ? (
          <textarea key={inp.name} value={vals[i]} placeholder={inp.placeholder}
            onChange={e => setVals(v => v.map((x, j) => j === i ? e.target.value : x))}
            className={`${inputCls} h-16 resize-y`} />
        ) : (
          <input key={inp.name} value={vals[i]} placeholder={inp.placeholder || inp.name}
            onChange={e => setVals(v => v.map((x, j) => j === i ? e.target.value : x))}
            className={inputCls} />
        )
      ))}
      <div className="flex gap-2 pt-0.5">
        <button onClick={doSend} disabled={busy !== '' || !ready}
          title={ownerInfo && !ownerInfo.is_owner ? 'Active key is not the current owner — this will revert' : 'Execute now with the deployer key'}
          className="btn btn-primary flex-1 !py-2 !text-[11px]">
          {busy === 'send' ? <ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> : <BoltIcon className="w-3.5 h-3.5" />}
          Execute
        </button>
        <button onClick={doQueue} disabled={busy !== '' || !ready}
          title="Encode and add to the Safe batch"
          className="btn btn-teal flex-1 !py-2 !text-[11px]">
          {busy === 'queue' ? <ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> : <QueueListIcon className="w-3.5 h-3.5" />}
          Queue
        </button>
      </div>
    </div>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────

function AdminInner() {
  const [network, setNetwork] = useState('testnet')
  const [keyName, setKeyName] = useState('')
  const [owners, setOwners] = useState<OwnerInfo[]>([])
  const [account, setAccount] = useState<string>('')
  const [deployer, setDeployer] = useState<string>('')
  const [loading, setLoading] = useState(false)
  const [safeAddr, setSafeAddr] = useState('')
  const [cart, setCart] = useState<CartItem[]>([])
  const [busyGlobal, setBusyGlobal] = useState<string | null>(null)

  const explorer = EXPLORER_URLS[network]
  const ownerByKey = useMemo(() => Object.fromEntries(owners.map(o => [o.key, o])), [owners])

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const r = await api('admin/owners', { network, key: keyName || undefined })
      setOwners(r.contracts || [])
      setAccount(r.account || '')
      setDeployer(r.deployer || '')
    } catch (e: any) {
      toast.error(e?.message || 'Failed to load owner status')
    }
    setLoading(false)
  }, [network, keyName])

  useEffect(() => { refresh() }, [refresh])

  // ── Cart ──
  const addToCart = (item: CartItem) => setCart(c => [...c, item])
  const removeFromCart = (id: string) => setCart(c => c.filter(i => i.id !== id))
  const clearCart = () => setCart([])

  const copy = (text: string, label = 'Copied') => {
    navigator.clipboard.writeText(text); toast.success(label)
  }

  const exportSafeBatch = () => {
    if (!cart.length) return
    const batch = {
      version: '1.0',
      chainId: CHAIN_IDS[network] || '0',
      createdAt: Date.now(),
      meta: {
        name: 'Mod Protocol — Owner Batch',
        description: `${cart.length} owner action(s) for the mod protocol on ${CHAIN_NAMES[network]}`,
        txBuilderVersion: '1.16.5',
        createdFromSafeAddress: safeAddr || '',
        createdFromOwnerAddress: '',
      },
      transactions: cart.map(i => ({
        to: i.to,
        value: i.value || '0',
        data: i.data,
        contractMethod: null,
        contractInputsValues: null,
      })),
    }
    const blob = new Blob([JSON.stringify(batch, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `mod-protocol-safe-batch-${network}.json`
    a.click()
    URL.revokeObjectURL(url)
    toast.success('Safe batch exported')
  }

  // ── Ownership actions ──
  const transferOne = async (o: OwnerInfo, to: string, mode: 'send' | 'queue') => {
    if (!to || to.length < 8) { toast.error('Enter a valid new owner address'); return }
    setBusyGlobal(`${o.key}-transfer`)
    try {
      if (mode === 'send') {
        await api('admin/send', { contract: o.key, method: 'transferOwnership', args: [to], network, key: keyName || undefined }, 'POST')
        toast.success(`${o.name} ownership → ${fmtAddr(to)}`)
        await refresh()
      } else {
        const enc = await api('admin/encode', { contract: o.key, method: 'transferOwnership', args: [to], network }, 'POST')
        addToCart({ id: `${o.key}.transfer.${Math.round(Math.random() * 1e9)}`, label: `${o.name}.transferOwnership`, contract: o.name, contractAddress: enc.to, method: 'transferOwnership', argsDisplay: `newOwner=${to}`, to: enc.to, data: enc.data, value: enc.value || '0' })
        toast.success(`Queued ${o.name} transfer`)
      }
    } catch (e: any) {
      toast.error(e?.message || 'Transfer failed')
    }
    setBusyGlobal(null)
  }

  const renounceOne = async (o: OwnerInfo, mode: 'send' | 'queue') => {
    if (mode === 'send' && !confirm(`Renounce ownership of ${o.name}? This is PERMANENT and cannot be undone.`)) return
    setBusyGlobal(`${o.key}-renounce`)
    try {
      if (mode === 'send') {
        await api('admin/send', { contract: o.key, method: 'setOwnerless', args: [], network, key: keyName || undefined }, 'POST')
        toast.success(`${o.name} is now ownerless`)
        await refresh()
      } else {
        const enc = await api('admin/encode', { contract: o.key, method: 'setOwnerless', args: [], network }, 'POST')
        addToCart({ id: `${o.key}.renounce.${Math.round(Math.random() * 1e9)}`, label: `${o.name}.setOwnerless`, contract: o.name, contractAddress: enc.to, method: 'setOwnerless', argsDisplay: '∅', to: enc.to, data: enc.data, value: enc.value || '0' })
        toast.success(`Queued ${o.name} renounce`)
      }
    } catch (e: any) {
      toast.error(e?.message || 'Renounce failed')
    }
    setBusyGlobal(null)
  }

  const transferAllToSafe = async () => {
    if (!safeAddr || safeAddr.length < 8) { toast.error('Enter the Safe address first'); return }
    if (!confirm(`Transfer ownership of ALL protocol contracts you own to the Safe (${fmtAddr(safeAddr)})?`)) return
    setBusyGlobal('transfer-all')
    try {
      const r = await api('admin/transfer-all', { to: safeAddr, network, key: keyName || undefined }, 'POST')
      const done = (r.results || []).filter((x: any) => x.status === 'transferred').length
      toast.success(`Transferred ${done} contract(s) to the Safe`)
      await refresh()
    } catch (e: any) {
      toast.error(e?.message || 'Transfer-all failed')
    }
    setBusyGlobal(null)
  }

  const ownedCount = owners.filter(o => o.is_owner).length
  const safeOwnsCount = owners.filter(o => safeAddr && o.owner && o.owner.toLowerCase() === safeAddr.toLowerCase()).length

  // ── Render ──
  return (
    <Shell
      active="admin"
      right={
        <>
          <NetworkSelect value={network} onChange={setNetwork} />
          <RefreshBtn onClick={refresh} loading={loading} />
        </>
      }
      footer={`${CHAIN_NAMES[network]} — owner console`}
    >
      {/* ═══ Hero ═══ */}
      <div className="fade-up pt-2 pb-1" style={{ '--i': 0 } as any}>
        <h1 className="text-[28px] font-semibold tracking-[-0.03em] text-white">Owner console</h1>
        <p className="mt-1 text-[13px] text-white/40">
          Execute owner-only protocol methods directly, or queue them into a Safe multisig batch.
        </p>
      </div>

      {/* ═══ Identity + key ═══ */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div className="glass p-4 fade-up" style={{ '--i': 1 } as any}>
          <p className="label mb-2">Active signer</p>
          <div className="flex items-center gap-1.5">
            <span className="text-[13px] text-white/80 tabular-nums font-mono">{fmtAddr(account, 7)}</span>
            {explorer && account && <a href={`${explorer}/address/${account}`} target="_blank" rel="noopener noreferrer" className="text-cyan-500/40 hover:text-cyan-400 transition-colors"><ArrowUpRightIcon className="w-3 h-3" /></a>}
          </div>
          <p className="text-[11px] text-white/30 mt-1.5">
            <span className="text-white/45 tabular-nums">{ownedCount}/{owners.length}</span> contracts owned by this signer
          </p>
        </div>
        <div className="glass p-4 fade-up flex flex-col justify-center" style={{ '--i': 2 } as any}>
          <p className="label mb-2 flex items-center gap-1"><KeyIcon className="w-3 h-3" /> Signing key (server)</p>
          <input value={keyName} onChange={e => setKeyName(e.target.value)} placeholder="default deployer"
            className="input !py-2 !text-[12px]" />
        </div>
        <div className="glass p-4 fade-up" style={{ '--i': 3 } as any}>
          <p className="label mb-2">Safe multisig</p>
          <input value={safeAddr} onChange={e => setSafeAddr(e.target.value)} placeholder="0x… Safe address"
            className="input !py-2 !text-[12px]" />
          {safeAddr && <p className="text-[11px] text-teal-300/70 mt-1.5 tabular-nums">{safeOwnsCount}/{owners.length} contracts owned by this Safe</p>}
        </div>
      </div>

      {/* ═══ Ownership matrix ═══ */}
      <div className="fade-up" style={{ '--i': 4 } as any}>
        <Card title="Ownership" accent="cyan" right={
          <button onClick={transferAllToSafe} disabled={busyGlobal === 'transfer-all' || !safeAddr}
            className="btn btn-teal !py-2 !text-[11px]">
            {busyGlobal === 'transfer-all' ? <ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> : <ShieldCheckIcon className="w-3.5 h-3.5" />}
            Transfer all → Safe
          </button>
        }>
          {owners.length === 0 ? (
            <p className="text-[12px] text-white/30 py-4 text-center">No owner-managed contracts on this network</p>
          ) : (
            <div className="space-y-2">
              {owners.map(o => {
                const isSafe = safeAddr && o.owner && o.owner.toLowerCase() === safeAddr.toLowerCase()
                return (
                  <div key={o.key} className="row flex flex-col sm:flex-row sm:items-center gap-2 rounded-xl border hairline bg-white/[0.02] px-4 py-3">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-[13px] font-semibold tracking-tight text-white/80">{o.name}</span>
                        {o.ownerless ? (
                          <span className="chip chip-off"><NoSymbolIcon className="w-2.5 h-2.5" /> ownerless</span>
                        ) : o.is_owner ? (
                          <span className="chip text-cyan-300 bg-cyan-500/10 border border-cyan-500/25"><CheckBadgeIcon className="w-2.5 h-2.5" /> you</span>
                        ) : isSafe ? (
                          <span className="chip text-teal-300 bg-teal-500/10 border border-teal-500/25"><ShieldCheckIcon className="w-2.5 h-2.5" /> safe</span>
                        ) : null}
                      </div>
                      <div className="flex items-center gap-1.5 mt-1 text-[11px] text-white/30">
                        <span>owner</span>
                        <span className="tabular-nums text-white/45 font-mono">{o.ownerless ? '—' : fmtAddr(o.owner, 5)}</span>
                        {explorer && o.owner && !o.ownerless && <a href={`${explorer}/address/${o.owner}`} target="_blank" rel="noopener noreferrer" className="text-cyan-500/40 hover:text-cyan-400 transition-colors"><ArrowUpRightIcon className="w-2.5 h-2.5" /></a>}
                      </div>
                    </div>
                    {!o.ownerless && (
                      <div className="flex items-center gap-1.5">
                        <button onClick={() => transferOne(o, safeAddr, 'send')} disabled={busyGlobal === `${o.key}-transfer` || !o.is_owner || !safeAddr}
                          title={!safeAddr ? 'Enter a Safe address' : !o.is_owner ? 'Active signer is not the owner' : 'Transfer to Safe now'}
                          className="btn btn-primary !px-3 !py-1.5 !text-[11px]">→ Safe</button>
                        <button onClick={() => transferOne(o, safeAddr, 'queue')} disabled={busyGlobal === `${o.key}-transfer` || !safeAddr}
                          title="Queue transfer for Safe batch"
                          className="btn btn-teal !px-3 !py-1.5 !text-[11px]">Queue</button>
                        <button onClick={() => renounceOne(o, 'send')} disabled={busyGlobal === `${o.key}-renounce` || !o.is_owner}
                          title="Renounce ownership (permanent)"
                          className="btn btn-danger !px-3 !py-1.5 !text-[11px]">Renounce</button>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
          <p className="text-[11px] text-white/35 leading-relaxed pt-1">
            <ExclamationTriangleIcon className="inline w-3.5 h-3.5 mr-1 -mt-0.5 text-amber-400/60" />
            Hand the protocol to a Safe by transferring each contract's ownership to the Safe address. Afterwards, use <span className="text-teal-300/80">Queue</span> on any action and export a Safe batch — the multisig signers execute it from the Safe UI.
          </p>
        </Card>
      </div>

      {/* ═══ Safe batch cart ═══ */}
      {cart.length > 0 && (
        <div className="fade-up" style={{ '--i': 5 } as any}>
          <Card title={`Safe batch (${cart.length})`} accent="teal" right={
            <div className="flex items-center gap-2">
              <button onClick={exportSafeBatch} className="btn btn-teal !py-2 !text-[11px]">
                <ArrowDownTrayIcon className="w-3.5 h-3.5" /> Export JSON
              </button>
              <button onClick={clearCart} className="btn btn-ghost !py-2 !text-[11px]">
                <TrashIcon className="w-3.5 h-3.5" /> Clear
              </button>
            </div>
          }>
            <div className="space-y-2">
              {cart.map((i, idx) => (
                <div key={i.id} className="row flex items-center gap-3 rounded-xl border hairline bg-white/[0.02] px-4 py-2.5">
                  <span className="text-[11px] text-white/20 tabular-nums w-5 font-mono">{idx + 1}</span>
                  <div className="flex-1 min-w-0">
                    <p className="text-[13px] font-medium text-teal-300/90 truncate">{i.label}</p>
                    <p className="text-[11px] text-white/30 truncate font-mono">{i.argsDisplay} → {fmtAddr(i.to)}</p>
                  </div>
                  <button onClick={() => copy(JSON.stringify({ to: i.to, value: i.value, data: i.data }, null, 2), 'Tx copied')}
                    title="Copy to / value / data" className="p-1 rounded-md hover:bg-white/10 transition-colors"><ClipboardDocumentIcon className="w-3.5 h-3.5 text-white/30 hover:text-white/60" /></button>
                  <button onClick={() => removeFromCart(i.id)} className="p-1 rounded-md hover:bg-white/10 transition-colors"><TrashIcon className="w-3.5 h-3.5 text-white/30 hover:text-rose-400" /></button>
                </div>
              ))}
            </div>
            <p className="text-[11px] text-white/35 leading-relaxed">
              Import the exported file in the Safe app → <span className="text-white/50">Transaction Builder</span> → <span className="text-white/50">Load batch</span>. All {cart.length} actions are proposed as one multisig transaction.
            </p>
          </Card>
        </div>
      )}

      {/* ═══ Contract method groups ═══ */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {GROUPS.filter(g => ownerByKey[g.key]).map((g, i) => (
          <div key={g.key} className="fade-up" style={{ '--i': Math.min(i + 6, 14) } as any}>
            <Card title={g.title} accent={g.accent} hover right={
              <span className="text-[11px] text-white/25 tabular-nums font-mono">{fmtAddr(ownerByKey[g.key]?.address, 4)}</span>
            }>
              {g.methods.map(m => (
                <MethodRow key={m.method} group={g} m={m} ownerInfo={ownerByKey[g.key]} network={network} keyName={keyName} onQueue={addToCart} onAfterSend={refresh} />
              ))}
            </Card>
          </div>
        ))}
      </div>
    </Shell>
  )
}

export default dynamic(() => Promise.resolve(AdminInner), { ssr: false })
