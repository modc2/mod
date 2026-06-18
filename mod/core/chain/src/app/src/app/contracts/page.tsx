"use client";

import { useState, useEffect, useCallback, useMemo } from 'react'
import dynamic from 'next/dynamic'
import Link from 'next/link'
import { toast } from 'react-toastify'
import {
  CubeTransparentIcon,
  ArrowPathIcon,
  DocumentTextIcon,
  MagnifyingGlassIcon,
  ClipboardDocumentIcon,
  CheckIcon,
  ArrowTopRightOnSquareIcon,
} from '@heroicons/react/24/outline'

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

const fmtAddr = (s: string, c = 5) => s && s.length > 14 ? `${s.slice(0, c + 2)}...${s.slice(-c)}` : (s || '')

// ── Source viewer with line numbers ───────────────────────────────────────────

function SourceView({ source }: { source: string }) {
  const lines = useMemo(() => source.replace(/\n$/, '').split('\n'), [source])
  return (
    <div className="overflow-auto text-xs leading-relaxed">
      <table className="border-collapse w-full">
        <tbody>
          {lines.map((ln, i) => (
            <tr key={i} className="hover:bg-white/[0.02]">
              <td className="select-none text-right pr-4 pl-3 text-white/15 tabular-nums w-px whitespace-nowrap align-top">{i + 1}</td>
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
    <div className="min-h-screen bg-[#0a0a0f] text-[#e5e5e5] font-mono">
      <div className="p-4 md:p-6 max-w-7xl mx-auto space-y-4">

        {/* Header */}
        <div className="flex items-center justify-between border border-white/10 rounded-xl p-4 bg-white/[0.02]">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 flex items-center justify-center rounded-lg bg-gradient-to-br from-cyan-500/20 to-violet-500/20 border border-white/10">
              <DocumentTextIcon className="w-5 h-5 text-white/80" />
            </div>
            <div>
              <h1 className="text-xl font-bold tracking-wider uppercase">Contracts</h1>
              <Link href="/" className="text-[10px] text-cyan-500/50 hover:text-cyan-400 uppercase tracking-widest flex items-center gap-1">
                <CubeTransparentIcon className="w-3 h-3" /> Back to Chain Hub
              </Link>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <select value={mod} onChange={e => setMod(e.target.value)}
              title="Module"
              className="text-xs px-3 py-2 rounded-lg border border-cyan-500/30 bg-cyan-500/[0.08] text-cyan-200 focus:outline-none focus:border-cyan-500/50 font-mono cursor-pointer">
              {modList.map(mm => <option key={mm} value={mm}>{mm}</option>)}
            </select>
            <select value={network} onChange={e => setNetwork(e.target.value)}
              className="text-xs px-3 py-2 rounded-lg border border-white/10 bg-white/[0.04] text-white/70 focus:outline-none focus:border-white/20 font-mono cursor-pointer">
              <option value="testnet">Base Sepolia</option>
              <option value="ganache">Ganache</option>
              <option value="mainnet">Base Mainnet</option>
            </select>
            <button onClick={fetchList} disabled={loading}
              className="p-2 rounded-lg border border-white/10 bg-white/[0.04] text-white/40 hover:text-white/70 hover:bg-white/[0.08] disabled:opacity-30 transition-colors">
              <ArrowPathIcon className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            </button>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-4">
          {/* Sidebar list */}
          <div className="border border-white/[0.08] rounded-xl bg-white/[0.015] overflow-hidden flex flex-col">
            <div className="p-3 border-b border-white/[0.05]">
              <div className="relative">
                <MagnifyingGlassIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-white/20" />
                <input type="text" placeholder="Filter contracts..." value={search}
                  onChange={e => setSearch(e.target.value)}
                  className="w-full pl-9 pr-3 py-2 text-xs rounded-lg border border-white/10 bg-white/[0.03] text-white/70 placeholder:text-white/20 focus:outline-none focus:border-white/20 font-mono" />
              </div>
            </div>
            <div className="overflow-auto max-h-[70vh] p-2 space-y-3">
              {Object.keys(grouped).length === 0 && (
                <p className="text-[10px] text-white/25 py-6 text-center uppercase tracking-wider">
                  {loading ? 'Loading…' : 'No contracts'}
                </p>
              )}
              {Object.entries(grouped).map(([mod, items]) => (
                <div key={mod}>
                  <p className={`px-2 mb-1 text-[9px] font-bold uppercase tracking-widest ${MOD_TEXT[mod] || 'text-white/40'} opacity-70`}>{mod}</p>
                  {items.map(c => {
                    const active = selected?.file === c.file
                    return (
                      <button key={c.file} onClick={() => setSelected(c)}
                        className={`w-full text-left px-2 py-1.5 rounded-lg flex items-center justify-between gap-2 transition-colors ${
                          active ? 'bg-cyan-500/10 border border-cyan-500/30' : 'border border-transparent hover:bg-white/[0.03]'
                        }`}>
                        <span className={`text-xs truncate ${active ? 'text-cyan-300' : 'text-white/60'}`}>{c.name}</span>
                        <span className="flex items-center gap-1.5 shrink-0">
                          {c.address && <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" title="Deployed" />}
                          <span className="text-[9px] text-white/20 tabular-nums">{c.lines}</span>
                        </span>
                      </button>
                    )
                  })}
                </div>
              ))}
            </div>
          </div>

          {/* Source viewer */}
          <div className="border border-white/[0.08] rounded-xl bg-white/[0.015] overflow-hidden flex flex-col min-h-[60vh]">
            {selected ? (
              <>
                <div className="flex items-center justify-between px-4 py-3 border-b border-white/[0.05] flex-wrap gap-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <DocumentTextIcon className="w-4 h-4 text-white/30 shrink-0" />
                    <span className="text-sm font-bold text-white/70">{selected.name}</span>
                    <span className="text-[10px] text-white/25 truncate">{selected.file}</span>
                  </div>
                  <div className="flex items-center gap-3">
                    {selected.address && (
                      <span className="flex items-center gap-1.5 text-[10px]">
                        <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
                        <span className="text-white/40 tabular-nums">{fmtAddr(selected.address)}</span>
                        {explorer && (
                          <a href={`${explorer}/address/${selected.address}`} target="_blank" rel="noopener noreferrer"
                            className="text-cyan-500/40 hover:text-cyan-400"><ArrowTopRightOnSquareIcon className="w-3 h-3" /></a>
                        )}
                      </span>
                    )}
                    <button onClick={copySource} disabled={!source}
                      className="flex items-center gap-1 px-2 py-1 rounded-md border border-white/10 bg-white/[0.03] text-[9px] font-bold uppercase tracking-wider text-white/40 hover:text-white/70 hover:bg-white/[0.06] disabled:opacity-30 transition-colors">
                      {copied ? <CheckIcon className="w-3 h-3 text-emerald-400" /> : <ClipboardDocumentIcon className="w-3 h-3" />}
                      {copied ? 'Copied' : 'Copy'}
                    </button>
                  </div>
                </div>
                <div className="flex-1 bg-black/30">
                  {loadingSrc
                    ? <div className="flex items-center justify-center py-20 text-white/20"><ArrowPathIcon className="w-5 h-5 animate-spin" /></div>
                    : <SourceView source={source} />}
                </div>
              </>
            ) : (
              <div className="flex items-center justify-center flex-1 text-[10px] text-white/25 uppercase tracking-wider">
                Select a contract
              </div>
            )}
          </div>
        </div>

        <div className="text-center text-[10px] text-white/10 uppercase tracking-widest py-6">
          {list.length} contracts — {mod} module
        </div>
      </div>
    </div>
  )
}

export default dynamic(() => Promise.resolve(ContractsInner), { ssr: false })
