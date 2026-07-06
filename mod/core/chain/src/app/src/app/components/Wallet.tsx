"use client";

// ── Wallet button (navbar) + connect / manage modal ─────────────────────────

import { useState, useEffect } from 'react'
import { toast } from 'react-toastify'
import {
  WalletIcon,
  XMarkIcon,
  ClipboardDocumentIcon,
  CheckIcon,
  ArrowRightOnRectangleIcon,
  KeyIcon,
  EyeIcon,
  EyeSlashIcon,
  ArrowDownTrayIcon,
  CubeTransparentIcon,
  BoltIcon,
} from '@heroicons/react/24/outline'
import { useWallet, NETWORKS } from '../lib/wallet'

const fmtAddr = (s: string, c = 4) =>
  s && s.length > 12 ? `${s.slice(0, c + 2)}…${s.slice(-c)}` : (s || '')

const KIND_LABEL: Record<string, string> = { metamask: 'MetaMask', local: 'Browser wallet' }

export function WalletButton() {
  const w = useWallet()
  const [open, setOpen] = useState(false)

  return (
    <>
      {w.kind ? (
        <button onClick={() => setOpen(true)} title={`${KIND_LABEL[w.kind]} — ${w.address}`}
          className="flex items-center gap-2 pl-2.5 pr-3 py-1.5 rounded-full bg-white/[0.04] border border-white/[0.1] hover:border-cyan-400/40 hover:bg-white/[0.07] transition-all">
          <span className="dot bg-emerald-400" style={{ boxShadow: '0 0 10px 1px rgba(52,211,153,0.6)' }} />
          <span className="text-[12px] font-medium text-white/80 font-mono tabular-nums">{fmtAddr(w.address)}</span>
          <span className="hidden sm:inline text-[10px] text-white/30">{w.kind === 'metamask' ? '🦊' : '🔑'}</span>
        </button>
      ) : (
        <button onClick={() => setOpen(true)} className="btn btn-primary !py-1.5 !px-4 !text-[12px] !rounded-full">
          <WalletIcon className="w-3.5 h-3.5" /> Connect
        </button>
      )}
      {open && <WalletModal onClose={() => setOpen(false)} />}
    </>
  )
}

function WalletModal({ onClose }: { onClose: () => void }) {
  const w = useWallet()
  const [busy, setBusy] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [showKey, setShowKey] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [importPk, setImportPk] = useState('')

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const run = async (id: string, fn: () => Promise<void>, ok?: string, close = false) => {
    setBusy(id)
    try {
      await fn()
      if (ok) toast.success(ok)
      if (close) onClose()
    } catch (e: any) {
      toast.error(e?.shortMessage || e?.message || 'Failed')
    }
    setBusy(null)
  }

  const copyAddr = () => {
    navigator.clipboard.writeText(w.address)
    setCopied(true); setTimeout(() => setCopied(false), 1500)
  }

  const injectedNet = w.injectedChainId
    ? Object.values(NETWORKS).find(n => n.chainId === w.injectedChainId)
    : null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4" onClick={onClose}>
      <div className="glass w-full max-w-md p-6 space-y-5 fade-up" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <span className="relative w-8 h-8 rounded-xl bg-gradient-to-br from-cyan-400/80 to-violet-500/80 shadow-[0_0_18px_-4px_rgba(34,211,238,0.7)]">
              <span className="absolute inset-[3px] rounded-[9px] bg-[#05050a]/85" />
              <WalletIcon className="absolute inset-0 m-auto w-4 h-4 text-cyan-200" />
            </span>
            <span className="text-[17px] font-semibold tracking-tight text-white/90">
              {w.kind ? 'Wallet' : 'Connect a wallet'}
            </span>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-white/10 transition-colors">
            <XMarkIcon className="w-4 h-4 text-white/40" />
          </button>
        </div>

        {!w.kind ? (
          <>
            <p className="text-[12.5px] text-white/40 leading-relaxed -mt-1">
              Sign transactions to the protocol contracts straight from your browser.
            </p>
            <div className="space-y-2.5">
              <button
                onClick={() => run('mm', () => w.connectMetaMask(), 'MetaMask connected', true)}
                disabled={!w.hasInjected || busy !== null}
                className="w-full flex items-center gap-3.5 rounded-2xl border border-white/[0.09] bg-white/[0.03] hover:border-orange-400/40 hover:bg-orange-400/[0.06] transition-all p-4 text-left disabled:opacity-40 disabled:cursor-not-allowed">
                <span className="text-[26px]">🦊</span>
                <span className="flex-1">
                  <span className="block text-[14px] font-semibold text-white/85">MetaMask</span>
                  <span className="block text-[11.5px] text-white/35 mt-0.5">
                    {w.hasInjected ? 'Use your injected browser wallet' : 'No injected wallet detected'}
                  </span>
                </span>
                <BoltIcon className="w-4 h-4 text-white/25" />
              </button>
              <button
                onClick={() => run('local', () => w.connectLocal(), 'Browser wallet ready', true)}
                disabled={busy !== null}
                className="w-full flex items-center gap-3.5 rounded-2xl border border-white/[0.09] bg-white/[0.03] hover:border-cyan-400/40 hover:bg-cyan-400/[0.06] transition-all p-4 text-left">
                <span className="text-[26px]">🔑</span>
                <span className="flex-1">
                  <span className="block text-[14px] font-semibold text-white/85">Browser wallet</span>
                  <span className="block text-[11.5px] text-white/35 mt-0.5">
                    Local keypair stored in this browser — no extension needed
                  </span>
                </span>
                <CubeTransparentIcon className="w-4 h-4 text-white/25" />
              </button>
            </div>
            <button onClick={() => setImportOpen(v => !v)}
              className="text-[11.5px] text-white/30 hover:text-white/60 transition-colors">
              Import a private key instead…
            </button>
            {importOpen && (
              <div className="space-y-2">
                <input value={importPk} onChange={e => setImportPk(e.target.value)} placeholder="0x… private key"
                  type="password" className="input !text-[12px]" />
                <button
                  onClick={() => run('import', async () => { await w.importLocalKey(importPk); setImportPk('') }, 'Key imported', true)}
                  disabled={!importPk.trim() || busy !== null}
                  className="btn btn-teal w-full !py-2 !text-[12px]">
                  <ArrowDownTrayIcon className="w-3.5 h-3.5" /> Import as browser wallet
                </button>
              </div>
            )}
          </>
        ) : (
          <>
            {/* Connected view */}
            <div className="rounded-2xl border hairline bg-white/[0.02] p-4 space-y-3">
              <div className="flex items-center justify-between">
                <span className="label">{KIND_LABEL[w.kind]}</span>
                <span className="chip chip-live">connected</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="font-mono text-[13px] text-white/85 tabular-nums break-all">{w.address}</span>
                <button onClick={copyAddr} className="p-1 rounded-md hover:bg-white/10 shrink-0 transition-colors">
                  {copied ? <CheckIcon className="w-3.5 h-3.5 text-emerald-400" /> : <ClipboardDocumentIcon className="w-3.5 h-3.5 text-white/30" />}
                </button>
              </div>
              {w.kind === 'metamask' && (
                <p className="text-[11px] text-white/30">
                  on chain{' '}
                  <span className="text-white/55 tabular-nums">{w.injectedChainId ?? '—'}</span>
                  {injectedNet && <span className="text-cyan-300/70"> · {injectedNet.name}</span>}
                  {' '}— switches automatically when you send
                </p>
              )}
            </div>

            {w.kind === 'local' && (
              <div className="rounded-2xl border border-amber-400/15 bg-amber-400/[0.03] p-4 space-y-2.5">
                <div className="flex items-center justify-between">
                  <span className="label !text-amber-200/60 flex items-center gap-1"><KeyIcon className="w-3 h-3" /> Private key</span>
                  <button onClick={() => setShowKey(v => !v)} className="p-1 rounded-md hover:bg-white/10 transition-colors">
                    {showKey ? <EyeSlashIcon className="w-3.5 h-3.5 text-white/40" /> : <EyeIcon className="w-3.5 h-3.5 text-white/40" />}
                  </button>
                </div>
                {showKey && (
                  <div className="flex items-center gap-2">
                    <code className="text-[11px] text-amber-200/80 font-mono break-all flex-1">{w.exportLocalKey()}</code>
                    <button onClick={() => { navigator.clipboard.writeText(w.exportLocalKey() || ''); toast.success('Key copied — keep it safe') }}
                      className="p-1 rounded-md hover:bg-white/10 shrink-0 transition-colors">
                      <ClipboardDocumentIcon className="w-3.5 h-3.5 text-white/40" />
                    </button>
                  </div>
                )}
                <p className="text-[10.5px] text-white/25 leading-relaxed">
                  This key lives only in this browser. Export it to back up the account, and fund the address with gas ETH before writing on-chain.
                </p>
              </div>
            )}

            <button onClick={() => { w.disconnect(); onClose(); toast.info('Wallet disconnected') }}
              className="btn btn-ghost w-full !py-2.5 !text-[12px] hover:!border-rose-400/40 hover:!text-rose-300">
              <ArrowRightOnRectangleIcon className="w-4 h-4" /> Disconnect
            </button>
          </>
        )}
      </div>
    </div>
  )
}
