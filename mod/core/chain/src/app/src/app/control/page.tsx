"use client";

import { useState, useEffect, useCallback } from 'react'
import dynamic from 'next/dynamic'
import { toast } from 'react-toastify'
import {
  ArrowPathIcon,
  RocketLaunchIcon,
  CheckBadgeIcon,
  ClipboardDocumentIcon,
  ArrowTopRightOnSquareIcon,
  ExclamationTriangleIcon,
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
  mainnet: 'Base Mainnet',
  ganache: 'Ganache',
}

interface ContractRow {
  name: string
  contract: string
  address: string
  owner: string | null
  ownerless: boolean | null
  is_owner: boolean
}

// ── API ──────────────────────────────────────────────────────────────────────

async function api(path: string, params: Record<string, any> = {}, method = 'GET') {
  const opts: RequestInit = method === 'GET'
    ? { method: 'GET' }
    : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(params) }
  const res = await fetch(`${API_URL}/${path}`, opts)
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || 'Request failed')
  }
  return res.json()
}

const fmtAddr = (s?: string | null, c = 5) =>
  s && s.length > 14 ? `${s.slice(0, c + 2)}…${s.slice(-c)}` : (s || '--')

// ── Mainnet confirm modal ──────────────────────────────────────────────────

function MainnetConfirm({ open, label, onClose, onConfirm }: {
  open: boolean
  label: string
  onClose: () => void
  onConfirm: () => void
}) {
  const [text, setText] = useState('')
  useEffect(() => { if (!open) setText('') }, [open])
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="glass !border-rose-400/30 w-full max-w-sm p-6 space-y-4 fade-up">
        <div className="flex items-center gap-2.5">
          <ExclamationTriangleIcon className="w-5 h-5 text-rose-300" />
          <span className="text-[16px] font-semibold text-rose-200 tracking-tight">Mainnet action</span>
        </div>
        <p className="text-[12.5px] text-white/50 leading-relaxed">
          &ldquo;{label}&rdquo; runs on <span className="text-rose-300 font-semibold">Base Mainnet</span> with real funds.
          Type <code className="text-rose-300">MAINNET</code> to confirm.
        </p>
        <input value={text} onChange={e => setText(e.target.value)} placeholder="MAINNET" autoFocus
          className="input !border-rose-400/30 focus:!border-rose-400/50" />
        <div className="flex gap-2 pt-1">
          <button onClick={onClose} className="btn btn-ghost flex-1">
            Cancel
          </button>
          <button onClick={onConfirm} disabled={text !== 'MAINNET'} className="btn btn-danger flex-1">
            Confirm
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Main ───────────────────────────────────────────────────────────────────

function ControlInner() {
  const [network, setNetwork] = useState('testnet')
  const [rows, setRows] = useState<ContractRow[]>([])
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [output, setOutput] = useState<{ title: string; text: string } | null>(null)
  const [pendingAction, setPendingAction] = useState<(() => void) | null>(null)
  const [pendingLabel, setPendingLabel] = useState('')

  const explorer = EXPLORER_URLS[network]

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const r = await api('control/status', { network })
      setRows(r.contracts || [])
    } catch (e: any) {
      toast.error(e?.message || 'Failed to load status')
    }
    setLoading(false)
  }, [network])

  useEffect(() => { refresh() }, [refresh])

  const runMaybeGated = (action: () => void, label: string) => {
    if (network === 'mainnet') {
      setPendingLabel(label)
      setPendingAction(() => action)
    } else {
      action()
    }
  }

  const doVerify = async (row: ContractRow) => {
    setBusy(`verify:${row.name}`)
    try {
      const r = await api('control/verify', { network, contract: row.name }, 'POST')
      setOutput({ title: `Verify ${row.name}`, text: r.output || '' })
      toast[r.status === 'failed' ? 'error' : 'success'](`${row.name}: ${r.status}`)
    } catch (e: any) {
      toast.error(e?.message || 'Verify failed')
      setOutput({ title: `Verify ${row.name}`, text: e?.message || '' })
    }
    setBusy(null)
  }

  const doDeployCore = async () => {
    setBusy('deploy:core')
    try {
      const r = await api('deploy', { network, mods: null, confirm: network === 'mainnet' }, 'POST')
      setOutput({ title: 'Deploy core contracts', text: JSON.stringify(r.result, null, 2) })
      toast.success('Deploy complete')
      await refresh()
    } catch (e: any) {
      toast.error(e?.message || 'Deploy failed')
      setOutput({ title: 'Deploy core contracts', text: e?.message || '' })
    }
    setBusy(null)
  }

  const doDeployDefi = async () => {
    setBusy('deploy:defi')
    try {
      const r = await api('control/deploy-script', { network, script: 'deploy-defi.js', confirm: network === 'mainnet' }, 'POST')
      setOutput({ title: 'Deploy DeFi vault', text: r.output || '' })
      toast.success('DeFi vault deployed')
      await refresh()
    } catch (e: any) {
      toast.error(e?.message || 'Deploy failed')
      setOutput({ title: 'Deploy DeFi vault', text: e?.message || '' })
    }
    setBusy(null)
  }

  return (
    <Shell
      active="control"
      right={
        <>
          <NetworkSelect value={network} onChange={setNetwork} />
          <RefreshBtn onClick={refresh} loading={loading} />
        </>
      }
      footer={`${rows.length} contracts — ${CHAIN_NAMES[network] || network}`}
    >
      <MainnetConfirm
        open={!!pendingAction}
        label={pendingLabel}
        onClose={() => setPendingAction(null)}
        onConfirm={() => { const fn = pendingAction; setPendingAction(null); fn && fn() }}
      />

      {/* ═══ Hero ═══ */}
      <div className="fade-up" style={{ '--i': 0 } as any}>
        <h1 className="text-[28px] font-semibold tracking-[-0.03em] text-white">Control panel</h1>
        <p className="mt-1 text-[13px] text-white/40">
          Deploy protocol contracts and verify them on the block explorer.
        </p>
      </div>

      {/* ═══ Mainnet warning ═══ */}
      {network === 'mainnet' && (
        <div className="glass !border-rose-400/30 flex items-center gap-2.5 px-5 py-3.5 fade-up" style={{ '--i': 1 } as any}>
          <ExclamationTriangleIcon className="w-4 h-4 shrink-0 text-rose-300" />
          <span className="text-[12.5px] text-rose-200/90">
            Mainnet actions move real funds — deploys require typing MAINNET to confirm.
          </span>
        </div>
      )}

      {/* ═══ Deploy actions ═══ */}
      <div className="glass p-5 flex flex-wrap items-center gap-3 fade-up" style={{ '--i': 2 } as any}>
        <span className="text-[14px] font-semibold text-white/85 tracking-tight mr-1">Deploy</span>
        <button onClick={() => runMaybeGated(doDeployCore, 'Deploy core contracts')} disabled={busy !== null}
          className="btn btn-primary">
          {busy === 'deploy:core' ? <ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> : <RocketLaunchIcon className="w-3.5 h-3.5" />}
          Deploy missing core contracts
        </button>
        <button onClick={() => runMaybeGated(doDeployDefi, 'Deploy DeFi vault')} disabled={busy !== null}
          className="btn btn-teal">
          {busy === 'deploy:defi' ? <ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> : <RocketLaunchIcon className="w-3.5 h-3.5" />}
          Deploy DeFi vault
        </button>
      </div>

      {/* ═══ Status table ═══ */}
      <div className="glass overflow-hidden fade-up" style={{ '--i': 3 } as any}>
        <table className="w-full">
          <thead>
            <tr className="border-b hairline text-[10px] font-semibold uppercase tracking-[0.09em] text-white/30">
              <th className="text-left px-5 py-3">Contract</th>
              <th className="text-left px-5 py-3">Address</th>
              <th className="text-left px-5 py-3">Owner</th>
              <th className="text-right px-5 py-3">Actions</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr><td colSpan={4} className="text-center py-10 text-[12px] text-white/25">
                {loading ? 'Loading…' : 'No contracts deployed'}
              </td></tr>
            )}
            {rows.map(row => (
              <tr key={row.name} className="row border-b hairline">
                <td className="px-5 py-3">
                  <div className="flex items-center gap-2.5">
                    <span
                      className={`w-1.5 h-1.5 rounded-full ${row.address ? 'bg-emerald-400' : 'bg-white/15'}`}
                      style={row.address ? { boxShadow: '0 0 8px 1px rgba(52,211,153,0.6)' } : undefined}
                    />
                    <span className="text-[13px] font-medium text-white/80">{row.name}</span>
                  </div>
                </td>
                <td className="px-5 py-3">
                  <div className="flex items-center gap-1.5">
                    <span className="font-mono text-[12px] text-white/45 tabular-nums">{fmtAddr(row.address)}</span>
                    {row.address && (
                      <>
                        <button onClick={() => { navigator.clipboard.writeText(row.address); toast.success('Copied') }}
                          className="text-white/20 hover:text-white/60 transition-colors"><ClipboardDocumentIcon className="w-3 h-3" /></button>
                        {explorer && (
                          <a href={`${explorer}/address/${row.address}`} target="_blank" rel="noopener noreferrer"
                            className="text-cyan-500/40 hover:text-cyan-400 transition-colors">
                            <ArrowTopRightOnSquareIcon className="w-3 h-3" />
                          </a>
                        )}
                      </>
                    )}
                  </div>
                </td>
                <td className="px-5 py-3">
                  {row.owner ? (
                    <span className="flex items-center gap-1.5 font-mono text-[12px] text-white/45 tabular-nums">
                      {fmtAddr(row.owner)}
                      {row.is_owner && (
                        <span className="chip chip-live !text-[9px]">you</span>
                      )}
                    </span>
                  ) : <span className="text-white/15">—</span>}
                </td>
                <td className="px-5 py-3 text-right">
                  <button onClick={() => doVerify(row)} disabled={!row.address || busy !== null}
                    className="btn btn-ghost !py-1.5 !px-3 !text-[11px]">
                    {busy === `verify:${row.name}` ? <ArrowPathIcon className="w-3 h-3 animate-spin" /> : <CheckBadgeIcon className="w-3 h-3" />}
                    Verify
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* ═══ Output panel ═══ */}
      {output && (
        <div className="glass overflow-hidden fade-up" style={{ '--i': 4 } as any}>
          <div className="flex items-center justify-between px-5 py-3.5 border-b hairline">
            <span className="text-[13px] font-semibold text-white/80">{output.title}</span>
            <button onClick={() => setOutput(null)} className="btn btn-ghost !py-1 !px-2.5 !text-[11px]">Close</button>
          </div>
          <pre className="p-5 text-[11.5px] leading-relaxed text-white/55 font-mono whitespace-pre-wrap max-h-80 overflow-auto bg-black/30">{output.text}</pre>
        </div>
      )}
    </Shell>
  )
}

export default dynamic(() => Promise.resolve(ControlInner), { ssr: false })
