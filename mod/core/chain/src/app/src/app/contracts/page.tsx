"use client";

import { useState, useEffect, useCallback, useMemo } from 'react'
import dynamic from 'next/dynamic'
import { toast } from 'react-toastify'
import {
  ArrowPathIcon,
  DocumentTextIcon,
  MagnifyingGlassIcon,
  ClipboardDocumentIcon,
  CheckIcon,
  ArrowTopRightOnSquareIcon,
} from '@heroicons/react/24/outline'
import { Shell, NetworkSelect, RefreshBtn } from '../components/Shell'

// ── Constants ────────────────────────────────────────────────────────────────

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8800'

const EXPLORER_URLS: Record<string, string> = {
  testnet: 'https://sepolia.basescan.org',
  mainnet: 'https://basescan.org',
  ganache: '',
}

const MOD_TEXT: Record<string, string> = {
  token: 'text-emerald-400', oracles: 'text-amber-400', registry: 'text-cyan-400',
  perms: 'text-violet-400', tokengate: 'text-rose-400', bloctime: 'text-sky-400',
  treasury: 'text-lime-400', market: 'text-orange-400', safe: 'text-teal-400',
}

interface ContractMeta {
  name: string
  mod: string
  file: string
  lines: number
  address: string
}

// ── API ──────────────────────────────────────────────────────────────────────

async function api(path: string, params: Record<string, any> = {}) {
  const qs = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
      .map(([k, v]) => [k, String(v)])
  ).toString()
  const res = await fetch(`${API_URL}/${path}${qs ? `?${qs}` : ''}`)
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || 'Request failed')
  }
  return res.json()
}

const fmtAddr = (s: string, c = 5) => s && s.length > 14 ? `${s.slice(0, c + 2)}…${s.slice(-c)}` : (s || '')

// ── Source viewer with line numbers ───────────────────────────────────────────

function SourceView({ source }: { source: string }) {
  const lines = useMemo(() => source.replace(/\n$/, '').split('\n'), [source])
  return (
    <div className="overflow-auto text-[12px] leading-[1.7]">
      <table className="border-collapse w-full">
        <tbody>
          {lines.map((ln, i) => (
            <tr key={i} className="hover:bg-white/[0.02]">
              <td className="select-none text-right pr-4 pl-3 text-white/15 font-mono tabular-nums w-px whitespace-nowrap align-top">{i + 1}</td>
              <td className="pr-4 text-white/70 whitespace-pre align-top font-mono">{ln || ' '}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Main ───────────────────────────────────────────────────────────────────

function ContractsInner() {
  const [network, setNetwork] = useState('testnet')
  const [modList, setModList] = useState<string[]>(['chain'])
  const [mod, setMod] = useState('chain')
  const [list, setList] = useState<ContractMeta[]>([])
  const [selected, setSelected] = useState<ContractMeta | null>(null)
  const [source, setSource] = useState('')
  const [loading, setLoading] = useState(false)
  const [loadingSrc, setLoadingSrc] = useState(false)
  const [search, setSearch] = useState('')
  const [copied, setCopied] = useState(false)

  const explorer = EXPLORER_URLS[network]

  // discover which modules ship contracts
  useEffect(() => {
    api('contracts/mods')
      .then(r => setModList(r.mods?.length ? r.mods : ['chain']))
      .catch(() => {})
  }, [])

  const fetchList = useCallback(async () => {
    setLoading(true)
    try {
      const r = await api('contracts/source', { mod, network })
      const contracts: ContractMeta[] = r.contracts || []
      setList(contracts)
      setSelected(prev => prev && contracts.find(c => c.file === prev.file) || contracts[0] || null)
    } catch (e: any) {
      setList([]); setSelected(null)
      toast.error(e?.message || 'Failed to load contracts')
    }
    setLoading(false)
  }, [mod, network])

  const fetchSource = useCallback(async (c: ContractMeta) => {
    setLoadingSrc(true)
    setSource('')
    try {
      const r = await api('contracts/source', { file: c.file, mod, network })
      setSource(r.source || '')
    } catch (e: any) {
      toast.error(e?.message || 'Failed to load source')
    }
    setLoadingSrc(false)
  }, [mod, network])

  useEffect(() => { setSelected(null) }, [mod])
  useEffect(() => { fetchList() }, [fetchList])
  useEffect(() => { if (selected) fetchSource(selected) }, [selected, fetchSource])

  const filtered = useMemo(() =>
    list.filter(c => !search ||
      c.name.toLowerCase().includes(search.toLowerCase()) ||
      c.mod.toLowerCase().includes(search.toLowerCase())),
    [list, search]
  )

  const grouped = useMemo(() => {
    const g: Record<string, ContractMeta[]> = {}
    for (const c of filtered) (g[c.mod] ||= []).push(c)
    return g
  }, [filtered])

  const copySource = () => {
    navigator.clipboard.writeText(source)
    setCopied(true); setTimeout(() => setCopied(false), 1500)
  }

  return (
    <Shell
      active="contracts"
      right={
        <>
          <select value={mod} onChange={e => setMod(e.target.value)} title="Module"
            className="input !w-auto !py-1.5 !px-3 !text-[12px] !font-sans !rounded-full">
            {modList.map(mm => <option key={mm} value={mm}>{mm}</option>)}
          </select>
          <NetworkSelect value={network} onChange={setNetwork} />
          <RefreshBtn onClick={fetchList} loading={loading} />
        </>
      }
      footer={`${list.length} contracts — ${mod} module`}
    >
      {/* ═══ Hero ═══ */}
      <div className="fade-up" style={{ '--i': 0 } as any}>
        <h1 className="text-[28px] font-semibold tracking-[-0.03em] text-white">Contracts</h1>
        <p className="mt-1 text-[13px] text-white/40">
          Browse Solidity source for every module, with live deployment addresses.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-4">
        {/* Sidebar list */}
        <div className="glass overflow-hidden flex flex-col fade-up" style={{ '--i': 1 } as any}>
          <div className="p-3 border-b hairline">
            <div className="relative">
              <MagnifyingGlassIcon className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-white/25" />
              <input type="text" placeholder="Filter contracts…" value={search}
                onChange={e => setSearch(e.target.value)}
                className="input !pl-10 !font-sans" />
            </div>
          </div>
          <div className="overflow-auto max-h-[70vh] p-2 space-y-3">
            {Object.keys(grouped).length === 0 && (
              <p className="text-[12px] text-white/30 py-6 text-center">
                {loading ? 'Loading…' : 'No contracts'}
              </p>
            )}
            {Object.entries(grouped).map(([mod, items]) => (
              <div key={mod}>
                <p className={`px-2 mb-1 text-[10px] font-semibold uppercase tracking-[0.09em] ${MOD_TEXT[mod] || 'text-white/40'}`}>{mod}</p>
                {items.map(c => {
                  const active = selected?.file === c.file
                  return (
                    <button key={c.file} onClick={() => setSelected(c)}
                      className={`w-full text-left px-2.5 py-1.5 rounded-lg flex items-center justify-between gap-2 transition-colors ${
                        active ? 'bg-cyan-500/10 border border-cyan-400/30 text-cyan-200' : 'border border-transparent text-white/60 hover:bg-white/[0.04]'
                      }`}>
                      <span className="text-[12.5px] truncate">{c.name}</span>
                      <span className="flex items-center gap-1.5 shrink-0">
                        {c.address && (
                          <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" title="Deployed"
                            style={{ boxShadow: '0 0 8px 1px rgba(52,211,153,0.6)' }} />
                        )}
                        <span className="text-[10px] text-white/25 font-mono tabular-nums">{c.lines}</span>
                      </span>
                    </button>
                  )
                })}
              </div>
            ))}
          </div>
        </div>

        {/* Source viewer */}
        <div className="glass overflow-hidden flex flex-col min-h-[60vh] fade-up" style={{ '--i': 2 } as any}>
          {selected ? (
            <>
              <div className="flex items-center justify-between px-4 py-3 border-b hairline flex-wrap gap-2">
                <div className="flex items-center gap-2 min-w-0">
                  <DocumentTextIcon className="w-4 h-4 text-white/30 shrink-0" />
                  <span className="text-[15px] font-semibold tracking-tight text-white/85">{selected.name}</span>
                  <span className="text-[11px] text-white/25 font-mono truncate">{selected.file}</span>
                </div>
                <div className="flex items-center gap-3">
                  {selected.address && (
                    <span className="flex items-center gap-1.5 text-[11px]">
                      <span className="w-1.5 h-1.5 rounded-full bg-emerald-400"
                        style={{ boxShadow: '0 0 8px 1px rgba(52,211,153,0.6)' }} />
                      <span className="text-white/40 font-mono tabular-nums">{fmtAddr(selected.address)}</span>
                      {explorer && (
                        <a href={`${explorer}/address/${selected.address}`} target="_blank" rel="noopener noreferrer"
                          className="text-cyan-500/40 hover:text-cyan-400"><ArrowTopRightOnSquareIcon className="w-3 h-3" /></a>
                      )}
                    </span>
                  )}
                  <button onClick={copySource} disabled={!source}
                    className="btn btn-ghost !py-1 !px-2.5 !text-[11px]">
                    {copied ? <CheckIcon className="w-3 h-3 text-emerald-400" /> : <ClipboardDocumentIcon className="w-3 h-3" />}
                    {copied ? 'Copied' : 'Copy'}
                  </button>
                </div>
              </div>
              <div className="flex-1 bg-black/30">
                {loadingSrc
                  ? (
                    <div className="p-4 space-y-2.5">
                      {[...Array(10)].map((_, i) => (
                        <div key={i} className="skeleton h-3" style={{ width: `${[72, 88, 60, 94, 45, 80, 66, 90, 52, 76][i]}%` }} />
                      ))}
                    </div>
                  )
                  : <SourceView source={source} />}
              </div>
            </>
          ) : (
            <div className="flex items-center justify-center flex-1 text-[12px] text-white/30">
              Select a contract
            </div>
          )}
        </div>
      </div>
    </Shell>
  )
}

export default dynamic(() => Promise.resolve(ContractsInner), { ssr: false })
