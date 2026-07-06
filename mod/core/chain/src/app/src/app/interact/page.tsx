"use client";

// ── Interact: wallet-signed calls against every deployed contract ───────────
//
// ABIs + addresses come from the API (/contracts/abis). Reads go through the
// network RPC directly; writes are signed client-side by the connected wallet
// (MetaMask or the browser-local key) — the server never touches your keys.

import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import dynamic from 'next/dynamic'
import { toast } from 'react-toastify'
import {
  ArrowPathIcon,
  MagnifyingGlassIcon,
  ArrowTopRightOnSquareIcon,
  ClipboardDocumentIcon,
  CheckIcon,
  BoltIcon,
  EyeIcon,
  PencilSquareIcon,
  WalletIcon,
  ExclamationTriangleIcon,
  ChevronDownIcon,
  ChevronRightIcon,
} from '@heroicons/react/24/outline'
import { Shell, NetworkSelect, RefreshBtn } from '../components/Shell'
import { useWallet, NETWORKS, getReadProvider } from '../lib/wallet'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8800'

// ── Types ────────────────────────────────────────────────────────────────────

interface AbiInput { name: string; type: string; components?: AbiInput[] }
interface AbiFn {
  type: string
  name?: string
  inputs?: AbiInput[]
  outputs?: AbiInput[]
  stateMutability?: string
}
interface DeployedContract {
  name: string
  contract: string
  address: string
  abi: AbiFn[]
  abi_cid?: string | null
  src_cid?: string | null
}

interface TxState {
  status: 'signing' | 'pending' | 'confirmed' | 'failed'
  hash?: string
  block?: number
  gas?: string
  error?: string
}

// ── Helpers ──────────────────────────────────────────────────────────────────

const fmtAddr = (s: string, c = 5) =>
  s && s.length > 14 ? `${s.slice(0, c + 2)}…${s.slice(-c)}` : (s || '—')

const fnSig = (f: AbiFn) =>
  `${f.name}(${(f.inputs || []).map(i => i.type).join(',')})`

// Parse one human-entered value into what ethers expects for a solidity type.
// Numbers accept unit suffixes: "1.5 ether", "3 gwei", plain decimals of the
// native 18-dp token, or raw integers.
async function parseArg(type: string, raw: string): Promise<any> {
  const v = raw.trim()
  const { parseUnits, getAddress } = await import('ethers')

  if (type.endsWith(']')) {
    // array — JSON if it looks like it, else comma-split, elements parsed by base type
    const base = type.slice(0, type.lastIndexOf('['))
    const items: string[] = v.startsWith('[')
      ? JSON.parse(v).map((x: any) => String(x))
      : v.split(',').map(s => s.trim()).filter(Boolean)
    return Promise.all(items.map(x => parseArg(base, x)))
  }
  if (type.startsWith('tuple')) return JSON.parse(v)
  if (type === 'address') return getAddress(v)
  if (type === 'bool') return /^(true|1|yes)$/i.test(v)
  if (type.startsWith('uint') || type.startsWith('int')) {
    const m = v.match(/^([\d_.,]+)\s*(ether|eth|gwei|wei)$/i)
    if (m) {
      const unit = m[2].toLowerCase()
      const num = m[1].replace(/[_,]/g, '')
      return parseUnits(num, unit === 'wei' ? 0 : unit === 'gwei' ? 9 : 18)
    }
    if (v.includes('.')) return parseUnits(v, 18) // decimal → assume 18-dp token units
    return BigInt(v.replace(/[_,]/g, ''))
  }
  return v // string, bytes, bytesN — pass through
}

// Render any call result (bigint / arrays / Result tuples) as readable text.
function fmtResult(value: any, outputs?: AbiInput[]): string {
  const plain = (x: any): any => {
    if (typeof x === 'bigint') return x.toString()
    if (Array.isArray(x)) return x.map(plain)
    if (x && typeof x === 'object' && typeof x.toArray === 'function') return x.toArray().map(plain)
    return x
  }
  const p = plain(value)
  if (Array.isArray(p) && outputs && outputs.length > 1) {
    return outputs.map((o, i) => `${o.name || `[${i}]`}: ${typeof p[i] === 'object' ? JSON.stringify(p[i]) : p[i]}`).join('\n')
  }
  return typeof p === 'object' ? JSON.stringify(p, null, 2) : String(p)
}

async function fetchAbis(network: string): Promise<DeployedContract[]> {
  const res = await fetch(`${API_URL}/contracts/abis?network=${network}`)
  if (!res.ok) throw new Error('Failed to load contract ABIs')
  const data = await res.json()
  return data.contracts || []
}

// ── Function card ────────────────────────────────────────────────────────────

function FnCard({ fn, contract, network, mode }: {
  fn: AbiFn
  contract: DeployedContract
  network: string
  mode: 'read' | 'write'
}) {
  const wallet = useWallet()
  const [open, setOpen] = useState(false)
  const [vals, setVals] = useState<string[]>((fn.inputs || []).map(() => ''))
  const [payValue, setPayValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  const [tx, setTx] = useState<TxState | null>(null)

  const inputs = fn.inputs || []
  const payable = fn.stateMutability === 'payable'
  const explorer = NETWORKS[network]?.explorer
  const ready = vals.every(v => v.trim() !== '')

  // zero-arg views auto-expand feel snappy — everything else opens on click
  const noArgs = inputs.length === 0

  const buildArgs = () => Promise.all(inputs.map((inp, i) => parseArg(inp.type, vals[i])))

  const doRead = async () => {
    setBusy(true); setResult(null)
    try {
      const [{ Contract }, provider, args] = await Promise.all([
        import('ethers'), getReadProvider(network), buildArgs(),
      ])
      const c = new Contract(contract.address, contract.abi as any, provider)
      const r = await c[fnSig(fn)](...args)
      setResult(fmtResult(r, fn.outputs))
    } catch (e: any) {
      setResult(null)
      toast.error(e?.shortMessage || e?.reason || e?.message || 'Call failed')
    }
    setBusy(false)
  }

  const doWrite = async () => {
    if (!wallet.kind) { toast.error('Connect a wallet first'); return }
    setBusy(true); setTx({ status: 'signing' })
    try {
      const [{ Contract, parseEther }, signer, args] = await Promise.all([
        import('ethers'), wallet.getSigner(network), buildArgs(),
      ])
      const c = new Contract(contract.address, contract.abi as any, signer)
      const overrides: any = {}
      if (payable && payValue.trim()) overrides.value = parseEther(payValue.trim())
      const sent = await c[fnSig(fn)](...args, overrides)
      setTx({ status: 'pending', hash: sent.hash })
      const receipt = await sent.wait()
      setTx({
        status: receipt?.status === 1 ? 'confirmed' : 'failed',
        hash: sent.hash,
        block: receipt?.blockNumber,
        gas: receipt?.gasUsed?.toString(),
      })
      if (receipt?.status === 1) toast.success(`${fn.name} confirmed`)
      else toast.error(`${fn.name} reverted`)
    } catch (e: any) {
      const msg = e?.shortMessage || e?.reason || e?.info?.error?.message || e?.message || 'Transaction failed'
      setTx({ status: 'failed', error: msg })
      toast.error(msg)
    }
    setBusy(false)
  }

  const accent = mode === 'read' ? 'text-sky-300' : payable ? 'text-amber-300' : 'text-orange-300'

  return (
    <div className="rounded-xl border hairline bg-white/[0.02] overflow-hidden">
      <button onClick={() => setOpen(v => !v)}
        className="w-full flex items-center gap-2.5 px-4 py-2.5 text-left hover:bg-white/[0.03] transition-colors">
        {open ? <ChevronDownIcon className="w-3 h-3 text-white/25 shrink-0" /> : <ChevronRightIcon className="w-3 h-3 text-white/25 shrink-0" />}
        <span className={`text-[13px] font-medium font-mono ${accent}`}>{fn.name}</span>
        <span className="text-[11px] text-white/25 font-mono truncate flex-1">
          ({inputs.map(i => `${i.type}${i.name ? ` ${i.name}` : ''}`).join(', ')})
        </span>
        {payable && <span className="chip !text-[9px] text-amber-300 bg-amber-500/10 border border-amber-500/25">payable</span>}
        {mode === 'read' && (fn.outputs?.length || 0) > 0 && (
          <span className="text-[10px] text-white/20 font-mono hidden sm:inline">→ {(fn.outputs || []).map(o => o.type).join(', ')}</span>
        )}
      </button>

      {open && (
        <div className="px-4 pb-3.5 pt-1 space-y-2.5 border-t hairline bg-black/20">
          {inputs.map((inp, i) => (
            <label key={i} className="block">
              <span className="label block mb-1 !text-[9.5px]">
                {inp.name || `arg${i}`} <span className="text-white/20 normal-case tracking-normal font-mono">{inp.type}</span>
              </span>
              <input value={vals[i]} onChange={e => setVals(v => v.map((x, j) => j === i ? e.target.value : x))}
                placeholder={inp.type === 'address' ? '0x…' : inp.type.endsWith(']') ? 'a, b, c  or  [ … ]' : inp.type.startsWith('uint') ? '0  ·  1.5 ether  ·  3 gwei' : inp.type}
                className="input !py-2 !text-[12px]" />
            </label>
          ))}
          {payable && (
            <label className="block">
              <span className="label block mb-1 !text-[9.5px] !text-amber-200/60">value (ETH)</span>
              <input value={payValue} onChange={e => setPayValue(e.target.value)} placeholder="0.0"
                className="input !py-2 !text-[12px] !border-amber-400/20 focus:!border-amber-400/50" />
            </label>
          )}

          {mode === 'read' ? (
            <button onClick={doRead} disabled={busy || (!noArgs && !ready)}
              className="btn btn-primary w-full !py-2 !text-[12px]">
              {busy ? <ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> : <EyeIcon className="w-3.5 h-3.5" />}
              Query
            </button>
          ) : (
            <button onClick={doWrite} disabled={busy || (!noArgs && !ready)}
              title={wallet.kind ? `Sign with ${wallet.kind === 'metamask' ? 'MetaMask' : 'browser wallet'}` : 'Connect a wallet to write'}
              className={`btn w-full !py-2 !text-[12px] ${wallet.kind ? 'btn-primary' : 'btn-ghost'}`}>
              {busy ? <ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> : <BoltIcon className="w-3.5 h-3.5" />}
              {wallet.kind ? 'Write' : 'Connect wallet to write'}
            </button>
          )}

          {result !== null && (
            <pre className="rounded-lg bg-black/40 border hairline px-3.5 py-2.5 text-[11.5px] leading-relaxed text-emerald-300/90 font-mono whitespace-pre-wrap break-all max-h-48 overflow-auto">{result}</pre>
          )}

          {tx && (
            <div className={`rounded-lg border px-3.5 py-2.5 text-[11.5px] font-mono space-y-1 ${
              tx.status === 'confirmed' ? 'border-emerald-500/25 bg-emerald-500/[0.05] text-emerald-300'
              : tx.status === 'failed' ? 'border-rose-500/25 bg-rose-500/[0.05] text-rose-300'
              : 'border-amber-500/25 bg-amber-500/[0.05] text-amber-300'
            }`}>
              <div className="flex items-center gap-2">
                {(tx.status === 'signing' || tx.status === 'pending') && <ArrowPathIcon className="w-3 h-3 animate-spin" />}
                <span className="uppercase tracking-wider text-[10px] font-sans font-semibold">
                  {tx.status === 'signing' ? 'awaiting signature' : tx.status}
                </span>
                {tx.block !== undefined && <span className="text-white/40">block {tx.block}</span>}
                {tx.gas && <span className="text-white/40">gas {tx.gas}</span>}
              </div>
              {tx.hash && (
                <div className="flex items-center gap-1.5 text-white/50 break-all">
                  {fmtAddr(tx.hash, 10)}
                  <button onClick={() => { navigator.clipboard.writeText(tx.hash!); toast.success('Tx hash copied') }}
                    className="p-0.5 rounded hover:bg-white/10"><ClipboardDocumentIcon className="w-3 h-3" /></button>
                  {explorer && (
                    <a href={`${explorer}/tx/${tx.hash}`} target="_blank" rel="noopener noreferrer"
                      className="text-cyan-400/60 hover:text-cyan-300"><ArrowTopRightOnSquareIcon className="w-3 h-3" /></a>
                  )}
                </div>
              )}
              {tx.error && <p className="text-rose-300/80 font-sans whitespace-pre-wrap break-words">{tx.error}</p>}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Main ─────────────────────────────────────────────────────────────────────

function InteractInner() {
  const wallet = useWallet()
  const [network, setNetwork] = useState('testnet')
  const [contracts, setContracts] = useState<DeployedContract[]>([])
  const [selected, setSelected] = useState<string>('')
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')
  const [tab, setTab] = useState<'read' | 'write'>('read')
  const [ethBal, setEthBal] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const balSeq = useRef(0)

  const net = NETWORKS[network]
  const explorer = net?.explorer

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const list = await fetchAbis(network)
      setContracts(list)
      setSelected(prev => list.find(c => c.name === prev) ? prev : (list[0]?.name || ''))
    } catch (e: any) {
      setContracts([])
      toast.error(e?.message || 'Failed to load contracts')
    }
    setLoading(false)
  }, [network])

  useEffect(() => { refresh() }, [refresh])

  // connected wallet's gas balance on the active network
  useEffect(() => {
    if (!wallet.address) { setEthBal(null); return }
    const seq = ++balSeq.current
    getReadProvider(network)
      .then(p => p.getBalance(wallet.address))
      .then(async b => {
        if (seq !== balSeq.current) return
        const { formatEther } = await import('ethers')
        setEthBal(Number(formatEther(b)).toFixed(5))
      })
      .catch(() => { if (seq === balSeq.current) setEthBal(null) })
  }, [wallet.address, network])

  const filtered = useMemo(() =>
    contracts.filter(c => !search || c.name.toLowerCase().includes(search.toLowerCase()) || c.contract.toLowerCase().includes(search.toLowerCase())),
    [contracts, search])

  const current = contracts.find(c => c.name === selected) || null

  const { reads, writes } = useMemo(() => {
    const fns = (current?.abi || []).filter(f => f.type === 'function' && f.name)
    return {
      reads: fns.filter(f => f.stateMutability === 'view' || f.stateMutability === 'pure'),
      writes: fns.filter(f => f.stateMutability !== 'view' && f.stateMutability !== 'pure'),
    }
  }, [current])

  const wrongChain = wallet.kind === 'metamask' && wallet.injectedChainId !== null && net && wallet.injectedChainId !== net.chainId

  return (
    <Shell
      active="interact"
      right={
        <>
          <NetworkSelect value={network} onChange={setNetwork} />
          <RefreshBtn onClick={refresh} loading={loading} />
        </>
      }
      footer={`${contracts.length} contracts — ${net?.name || network}`}
    >
      {/* ═══ Hero ═══ */}
      <div className="fade-up" style={{ '--i': 0 } as any}>
        <h1 className="text-[28px] font-semibold tracking-[-0.03em] text-white">
          Interact{' '}
          <span className="bg-gradient-to-r from-cyan-300 via-sky-300 to-violet-300 bg-clip-text text-transparent">directly</span>
        </h1>
        <p className="mt-1 text-[13px] text-white/40">
          Read and write every deployed contract on {net?.name} — signed by your own wallet, keys never leave the browser.
        </p>
      </div>

      {/* ═══ Wallet strip ═══ */}
      <div className="glass px-5 py-3.5 flex flex-wrap items-center gap-x-5 gap-y-2 fade-up" style={{ '--i': 1 } as any}>
        <div className="flex items-center gap-2.5">
          <WalletIcon className="w-4 h-4 text-white/30" />
          {wallet.kind ? (
            <>
              <span className="dot bg-emerald-400" style={{ boxShadow: '0 0 10px 1px rgba(52,211,153,0.6)' }} />
              <span className="font-mono text-[12.5px] text-white/80 tabular-nums">{fmtAddr(wallet.address, 6)}</span>
              <span className="chip !text-[9px] text-white/40 bg-white/[0.04] border border-white/[0.08]">
                {wallet.kind === 'metamask' ? '🦊 MetaMask' : '🔑 browser'}
              </span>
            </>
          ) : (
            <span className="text-[12.5px] text-white/35">No wallet — reads work, writes need a signer (top-right <span className="text-cyan-300/80">Connect</span>)</span>
          )}
        </div>
        {wallet.kind && (
          <div className="flex items-center gap-1.5 text-[12px] text-white/40">
            <span className="label !mb-0">gas</span>
            <span className={`font-mono tabular-nums ${ethBal !== null && Number(ethBal) === 0 ? 'text-rose-300' : 'text-white/70'}`}>
              {ethBal ?? '…'} ETH
            </span>
            {ethBal !== null && Number(ethBal) === 0 && (
              <span className="text-[11px] text-rose-300/70 flex items-center gap-1">
                <ExclamationTriangleIcon className="w-3 h-3" /> fund this address to write
              </span>
            )}
          </div>
        )}
        {wrongChain && (
          <span className="text-[11px] text-amber-300/80 flex items-center gap-1">
            <ExclamationTriangleIcon className="w-3 h-3" />
            MetaMask is on chain {wallet.injectedChainId} — it will be asked to switch to {net.name} ({net.chainId}) when you write
          </span>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[270px_1fr] gap-4">
        {/* ═══ Contract list ═══ */}
        <div className="glass overflow-hidden flex flex-col fade-up" style={{ '--i': 2 } as any}>
          <div className="p-3 border-b hairline">
            <div className="relative">
              <MagnifyingGlassIcon className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-white/25" />
              <input type="text" placeholder="Filter contracts…" value={search}
                onChange={e => setSearch(e.target.value)} className="input !pl-10 !font-sans" />
            </div>
          </div>
          <div className="overflow-auto max-h-[65vh] p-2 space-y-1">
            {filtered.length === 0 && (
              <p className="text-[12px] text-white/30 py-6 text-center">{loading ? 'Loading…' : 'No deployed contracts'}</p>
            )}
            {filtered.map(c => {
              const active = c.name === selected
              const fns = c.abi.filter(f => f.type === 'function')
              return (
                <button key={c.name} onClick={() => { setSelected(c.name); setTab('read') }}
                  className={`w-full text-left px-3 py-2 rounded-lg transition-colors ${
                    active ? 'bg-cyan-500/10 border border-cyan-400/30' : 'border border-transparent hover:bg-white/[0.04]'
                  }`}>
                  <div className="flex items-center justify-between gap-2">
                    <span className={`text-[13px] font-medium ${active ? 'text-cyan-200' : 'text-white/70'}`}>{c.name}</span>
                    <span className="text-[10px] text-white/25 font-mono tabular-nums">{fns.length} fn</span>
                  </div>
                  <div className="text-[10.5px] text-white/25 font-mono mt-0.5">{fmtAddr(c.address, 4)}{c.contract !== c.name && <span className="text-white/15"> · {c.contract}</span>}</div>
                </button>
              )
            })}
          </div>
        </div>

        {/* ═══ Function panel ═══ */}
        <div className="glass overflow-hidden flex flex-col min-h-[55vh] fade-up" style={{ '--i': 3 } as any}>
          {current ? (
            <>
              <div className="flex items-center justify-between px-5 py-4 border-b hairline flex-wrap gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-[16px] font-semibold tracking-tight text-white/90">{current.name}</span>
                    <span className="chip !text-[9px] text-white/40 bg-white/[0.04] border border-white/[0.08]">{current.contract}</span>
                  </div>
                  <div className="flex items-center gap-1.5 mt-1">
                    <span className="text-[11.5px] text-white/35 font-mono tabular-nums">{current.address}</span>
                    <button onClick={() => { navigator.clipboard.writeText(current.address); setCopied(true); setTimeout(() => setCopied(false), 1200) }}
                      className="p-0.5 rounded hover:bg-white/10 transition-colors">
                      {copied ? <CheckIcon className="w-3 h-3 text-emerald-400" /> : <ClipboardDocumentIcon className="w-3 h-3 text-white/25" />}
                    </button>
                    {explorer && (
                      <a href={`${explorer}/address/${current.address}`} target="_blank" rel="noopener noreferrer"
                        className="text-cyan-500/40 hover:text-cyan-400"><ArrowTopRightOnSquareIcon className="w-3 h-3" /></a>
                    )}
                  </div>
                  {(current.abi_cid || current.src_cid) && (
                    <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
                      {current.abi_cid && (
                        <button onClick={() => { navigator.clipboard.writeText(current.abi_cid!); toast.success('ABI CID copied') }}
                          title={`ABI pinned in localfs — ${current.abi_cid}`}
                          className="chip !text-[9px] text-violet-300/80 bg-violet-500/10 border border-violet-500/20 hover:border-violet-400/45 transition-colors font-mono">
                          abi {fmtAddr(current.abi_cid, 4)}
                        </button>
                      )}
                      {current.src_cid && (
                        <button onClick={() => { navigator.clipboard.writeText(current.src_cid!); toast.success('Source CID copied') }}
                          title={`Solidity source pinned in localfs — ${current.src_cid}`}
                          className="chip !text-[9px] text-sky-300/80 bg-sky-500/10 border border-sky-500/20 hover:border-sky-400/45 transition-colors font-mono">
                          src {fmtAddr(current.src_cid, 4)}
                        </button>
                      )}
                    </div>
                  )}
                </div>

                {/* Read / Write tabs */}
                <div className="glass !rounded-full p-1 flex gap-1">
                  {(['read', 'write'] as const).map(t => (
                    <button key={t} onClick={() => setTab(t)}
                      className={`flex items-center gap-1.5 px-4 py-1.5 rounded-full text-[12px] font-medium transition-all ${
                        tab === t
                          ? 'text-white bg-gradient-to-b from-white/10 to-white/5 shadow-[inset_0_1px_0_rgba(255,255,255,0.1),0_0_18px_-8px_rgba(34,211,238,0.5)]'
                          : 'text-white/40 hover:text-white/75'
                      }`}>
                      {t === 'read' ? <EyeIcon className="w-3.5 h-3.5" /> : <PencilSquareIcon className="w-3.5 h-3.5" />}
                      {t === 'read' ? `Read (${reads.length})` : `Write (${writes.length})`}
                    </button>
                  ))}
                </div>
              </div>

              <div className="p-4 space-y-2 overflow-auto max-h-[65vh]">
                {(tab === 'read' ? reads : writes).length === 0 && (
                  <p className="text-[12px] text-white/25 py-8 text-center">No {tab} functions</p>
                )}
                {(tab === 'read' ? reads : writes).map(f => (
                  <FnCard key={fnSig(f)} fn={f} contract={current} network={network} mode={tab} />
                ))}
              </div>
            </>
          ) : (
            <div className="flex items-center justify-center flex-1 text-[12px] text-white/30">
              {loading ? 'Loading contracts…' : 'Select a contract'}
            </div>
          )}
        </div>
      </div>
    </Shell>
  )
}

export default dynamic(() => Promise.resolve(InteractInner), { ssr: false })
