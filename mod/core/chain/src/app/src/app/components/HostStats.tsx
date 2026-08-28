"use client";

// ── Host readout — owner-only ────────────────────────────────────────────────
//
// The machine the protocol runs on: per-core CPU, memory, disk, network
// traffic and the busiest processes. Everything here comes from the gated
// /system/stats endpoint, so the panel renders nothing at all unless the
// connected wallet is a chain owner — non-owners never see that it exists.
//
// Meter colours are STATUS semantics (ok / hot / critical) and the number is
// always printed beside the bar, so state is never carried by colour alone.

import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'react-toastify'
import { CpuChipIcon, LockOpenIcon, ArrowPathIcon } from '@heroicons/react/24/outline'
import { useWallet } from '../lib/wallet'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8800'
const LS_TOKEN = 'chain.host.token'
const POLL_MS = 4000 // the endpoint samples /proc for ~500ms of that

const safeGet = (k: string) => { try { return localStorage.getItem(k) } catch { return null } }
const safeSet = (k: string, v: string) => { try { localStorage.setItem(k, v) } catch {} }
const safeDel = (k: string) => { try { localStorage.removeItem(k) } catch {} }

interface Iface {
  name: string
  rx_bytes: number; tx_bytes: number
  rx_rate: number; tx_rate: number
  rx_pps: number; tx_pps: number
  errs: number; drops: number
  loopback: boolean
}

interface Stats {
  host: { name: string; kernel: string; arch: string }
  cpu: { cores: number; pct: number; per_core: number[]; load1: number; load5: number; load15: number }
  mem: { total_mb: number; used_mb: number; available_mb: number; cached_mb: number; swap_total_mb: number; swap_used_mb: number }
  disk: { total_mb: number; available_mb: number }
  net: {
    interfaces: Iface[]
    total: { rx_bytes: number; tx_bytes: number; rx_rate: number; tx_rate: number }
    connections: { tcp: { established: number; listen: number; other: number }; udp: number }
  }
  uptime_secs: number
  tasks: { total: number; running: number }
  procs_total: number
  procs: { pid: number; user: string; state: string; cpu_pct: number; mem_mb: number; command: string }[]
}

// ── Formatting ───────────────────────────────────────────────────────────────

const fmtMB = (mb: number) => mb >= 1024 ? `${(mb / 1024).toFixed(1)}G` : `${mb}M`

const fmtBytes = (b: number) => {
  const units = ['B', 'K', 'M', 'G', 'T']
  let i = 0
  while (b >= 1024 && i < units.length - 1) { b /= 1024; i++ }
  return `${b >= 100 || i === 0 ? Math.round(b) : b.toFixed(1)}${units[i]}`
}

const fmtRate = (bps: number) => `${fmtBytes(bps)}/s`

const fmtUptime = (s: number) => {
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60)
  return d > 0 ? `${d}d ${h}h` : h > 0 ? `${h}h ${m}m` : `${m}m`
}

// ok → hot → critical
const utilColor = (pct: number) =>
  pct >= 85 ? 'rgb(251,113,133)' : pct >= 60 ? 'rgb(251,191,36)' : 'rgb(52,211,153)'

function Meter({ label, pct, value, width = 26 }: { label: string; pct: number; value: string; width?: number }) {
  const clamped = Math.max(0, Math.min(100, pct))
  return (
    <div className="flex items-center gap-2 min-w-0">
      <span className="text-[10px] font-mono text-white/30 shrink-0 text-right" style={{ width }}>{label}</span>
      <div className="flex-1 h-[7px] rounded-full overflow-hidden bg-white/[0.05] border border-white/[0.06]"
        role="meter" aria-valuenow={Math.round(clamped)} aria-valuemin={0} aria-valuemax={100} aria-label={label}>
        <div className="h-full rounded-full" style={{ width: `${clamped}%`, background: utilColor(clamped), transition: 'width 0.5s ease' }} />
      </div>
      <span className="text-[10px] font-mono text-white/55 shrink-0 text-right tabular-nums" style={{ width: 66 }}>{value}</span>
    </div>
  )
}

// ── Panel ────────────────────────────────────────────────────────────────────

export function HostStats({ network }: { network: string }) {
  const { address, getSigner } = useWallet()
  const [isOwner, setIsOwner] = useState(false)
  const [token, setToken] = useState<string | null>(null)
  const [data, setData] = useState<Stats | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [unlocking, setUnlocking] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // ── Is this wallet allowed to see the box at all? ──
  useEffect(() => {
    let live = true
    const saved = safeGet(LS_TOKEN)
    if (!address) { setIsOwner(false); setToken(null); setData(null); return }
    fetch(`${API_URL}/system/access?address=${address}`, saved ? { headers: { Authorization: `Bearer ${saved}` } } : {})
      .then(r => r.json())
      .then(r => {
        if (!live) return
        setIsOwner(!!r.is_owner)
        // Drop a stored token that has expired, or that belongs to a wallet
        // we're no longer connected as.
        const usable = !!(saved && r.authed && r.is_owner)
        setToken(usable ? saved : null)
        if (saved && !usable) safeDel(LS_TOKEN)
      })
      .catch(() => { if (live) setIsOwner(false) })
    return () => { live = false }
  }, [address])

  // ── Poll while unlocked ──
  const poll = useCallback(async () => {
    if (!token) return
    try {
      const r = await fetch(`${API_URL}/system/stats`, { headers: { Authorization: `Bearer ${token}` } })
      if (r.status === 403) {          // token expired — back to the unlock button
        safeDel(LS_TOKEN); setToken(null); setData(null)
        setError('Session expired — unlock again.')
        return
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      setData(await r.json())
      setError(null)
    } catch {
      setError('Could not reach the API.')
    } finally {
      // Chained timeout, not an interval: each call costs ~500ms of sampling,
      // so a fixed interval could stack requests on a busy host.
      timer.current = setTimeout(poll, POLL_MS)
    }
  }, [token])

  useEffect(() => {
    if (!token) return
    poll()
    return () => { if (timer.current) clearTimeout(timer.current) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  const unlock = async () => {
    setUnlocking(true)
    try {
      const ch = await fetch(`${API_URL}/system/challenge?address=${address}`).then(r => {
        if (!r.ok) throw new Error('Not an owner of this host')
        return r.json()
      })
      const signer = await getSigner(network)
      const signature = await signer.signMessage(ch.message)
      const res = await fetch(`${API_URL}/system/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ address, signature, nonce: ch.nonce }),
      })
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'Sign-in failed')
      const { token: t } = await res.json()
      safeSet(LS_TOKEN, t); setToken(t); setError(null)
      toast.success('Host stats unlocked')
    } catch (e: any) {
      toast.error(e?.message || 'Sign-in failed')
    }
    setUnlocking(false)
  }

  const lock = () => {
    safeDel(LS_TOKEN); setToken(null); setData(null); setError(null)
    if (timer.current) clearTimeout(timer.current)
  }

  // Non-owners get nothing — not even a hint that the panel exists.
  if (!isOwner) return null

  const memPct = data && data.mem.total_mb > 0 ? (data.mem.used_mb / data.mem.total_mb) * 100 : 0
  const swapPct = data && data.mem.swap_total_mb > 0 ? (data.mem.swap_used_mb / data.mem.swap_total_mb) * 100 : 0
  const diskUsed = data ? data.disk.total_mb - data.disk.available_mb : 0
  const diskPct = data && data.disk.total_mb > 0 ? (diskUsed / data.disk.total_mb) * 100 : 0
  const peak = data ? Math.max(1, ...data.net.interfaces.map(i => Math.max(i.rx_rate, i.tx_rate))) : 1

  return (
    <div className="glass overflow-hidden">
      <div className="px-5 py-4 border-b hairline flex items-center justify-between gap-2">
        <div className="flex items-center gap-2.5">
          <span className="dot bg-emerald-400" style={{ boxShadow: '0 0 10px 1px rgba(52,211,153,0.5)' }} />
          <span className="text-[14px] font-semibold text-white/85 tracking-tight">Host</span>
          {data && (
            <span className="text-[11px] text-white/25 font-mono hidden sm:inline">
              {data.host.name} · {data.host.kernel} · {data.host.arch}
            </span>
          )}
        </div>
        {token ? (
          <div className="flex items-center gap-2">
            <span className="chip chip-live"><span className="dot dot-live" /> live</span>
            <button onClick={lock} className="btn btn-ghost !py-1.5 !px-3 !text-[11px]" title="Forget this session's host token">Lock</button>
          </div>
        ) : (
          <button onClick={unlock} disabled={unlocking} className="btn btn-primary !py-1.5 !px-3 !text-[11px]"
            title="Sign a challenge with the owner wallet to read this host">
            {unlocking ? <ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> : <LockOpenIcon className="w-3.5 h-3.5" />}
            Unlock stats
          </button>
        )}
      </div>

      <div className="p-5 space-y-4">
        {!token ? (
          <p className="text-[12px] text-white/35 leading-relaxed">
            <CpuChipIcon className="inline w-4 h-4 mr-1.5 -mt-0.5 text-white/25" />
            CPU, memory, disk, processes and network traffic for the machine this API runs on.
            Sign with <span className="text-white/55 font-mono">{address.slice(0, 6)}…{address.slice(-4)}</span> to read it.
          </p>
        ) : !data ? (
          <p className="text-[12px] text-white/30">Sampling /proc…</p>
        ) : (
          <>
            {/* Headline — load / uptime / tasks */}
            <div className="flex items-center justify-between gap-2 flex-wrap text-[11px] font-mono text-white/55">
              <span title="Load average 1 / 5 / 15 min">
                <span className="text-white/30">load </span>
                {data.cpu.load1.toFixed(2)} {data.cpu.load5.toFixed(2)} {data.cpu.load15.toFixed(2)}
              </span>
              <span><span className="text-white/30">up </span>{fmtUptime(data.uptime_secs)}</span>
              <span>
                <span className="text-white/30">tasks </span>{data.tasks.total}
                <span className="text-emerald-300/80"> ({data.tasks.running} run)</span>
              </span>
            </div>

            {/* Per-core meters, two banks like htop */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1">
              {data.cpu.per_core.map((pct, i) => (
                <Meter key={i} label={`${i}`} pct={pct} value={`${pct.toFixed(1)}%`} />
              ))}
            </div>

            <div className="space-y-1">
              <Meter label="MEM" pct={memPct} value={`${fmtMB(data.mem.used_mb)}/${fmtMB(data.mem.total_mb)}`} />
              {data.mem.swap_total_mb > 0 && (
                <Meter label="SWP" pct={swapPct} value={`${fmtMB(data.mem.swap_used_mb)}/${fmtMB(data.mem.swap_total_mb)}`} />
              )}
              <Meter label="DSK" pct={diskPct} value={`${fmtMB(diskUsed)}/${fmtMB(data.disk.total_mb)}`} />
              <p className="text-[10px] font-mono text-white/25 text-right pt-0.5">
                {fmtMB(data.mem.cached_mb)} cached · {data.cpu.cores} cores · cpu {data.cpu.pct.toFixed(1)}%
              </p>
            </div>

            {/* ── Network traffic ── */}
            <div className="rounded-xl border hairline bg-white/[0.02] p-3.5 space-y-2.5">
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <span className="label">Network</span>
                <span className="text-[11px] font-mono text-white/55 tabular-nums">
                  <span className="text-emerald-300/90">↓ {fmtRate(data.net.total.rx_rate)}</span>
                  <span className="text-white/15"> · </span>
                  <span className="text-cyan-300/90">↑ {fmtRate(data.net.total.tx_rate)}</span>
                  <span className="text-white/25"> · {fmtBytes(data.net.total.rx_bytes)} in / {fmtBytes(data.net.total.tx_bytes)} out</span>
                </span>
              </div>

              <div className="space-y-1.5">
                {data.net.interfaces.map(i => (
                  <div key={i.name} className="flex items-center gap-2 min-w-0">
                    <span className={`text-[10px] font-mono shrink-0 truncate ${i.loopback ? 'text-white/20' : 'text-white/45'}`} style={{ width: 96 }}
                      title={`${i.name} — ${i.rx_pps.toFixed(0)}/${i.tx_pps.toFixed(0)} pps · ${i.errs} errs · ${i.drops} drops`}>
                      {i.name}
                    </span>
                    {/* Rate bars are relative to the busiest interface in this
                        sample — absolute link speed isn't knowable from /proc. */}
                    <div className="flex-1 flex flex-col gap-[2px] min-w-0">
                      <div className="h-[5px] rounded-full overflow-hidden bg-white/[0.04]">
                        <div className="h-full rounded-full bg-emerald-400/80" style={{ width: `${(i.rx_rate / peak) * 100}%`, transition: 'width 0.5s ease' }} />
                      </div>
                      <div className="h-[5px] rounded-full overflow-hidden bg-white/[0.04]">
                        <div className="h-full rounded-full bg-cyan-400/80" style={{ width: `${(i.tx_rate / peak) * 100}%`, transition: 'width 0.5s ease' }} />
                      </div>
                    </div>
                    <span className="text-[10px] font-mono text-white/55 shrink-0 text-right tabular-nums" style={{ width: 84 }}>
                      ↓{fmtRate(i.rx_rate)}
                    </span>
                    <span className="text-[10px] font-mono text-white/55 shrink-0 text-right tabular-nums hidden sm:block" style={{ width: 84 }}>
                      ↑{fmtRate(i.tx_rate)}
                    </span>
                    <span className="text-[10px] font-mono text-white/25 shrink-0 text-right tabular-nums hidden md:block" style={{ width: 110 }}>
                      {fmtBytes(i.rx_bytes)}/{fmtBytes(i.tx_bytes)}
                    </span>
                  </div>
                ))}
              </div>

              <div className="flex items-center gap-1.5 flex-wrap pt-0.5">
                <span className="chip chip-off">tcp est {data.net.connections.tcp.established}</span>
                <span className="chip chip-off">listen {data.net.connections.tcp.listen}</span>
                <span className="chip chip-off">other {data.net.connections.tcp.other}</span>
                <span className="chip chip-off">udp {data.net.connections.udp}</span>
              </div>
            </div>

            {/* ── Processes ── */}
            <div className="rounded-xl border hairline bg-white/[0.02] overflow-hidden">
              <div className="flex items-center gap-2 px-3.5 py-2 border-b hairline text-[10px] font-mono text-white/25">
                <span className="shrink-0 text-right" style={{ width: 48 }}>PID</span>
                <span className="shrink-0" style={{ width: 56 }}>USER</span>
                <span className="shrink-0 text-right" style={{ width: 40 }}>CPU%</span>
                <span className="shrink-0 text-right" style={{ width: 44 }}>MEM</span>
                <span className="flex-1 min-w-0 pl-2">COMMAND</span>
              </div>
              <div className="max-h-[320px] overflow-y-auto">
                {data.procs.map(p => (
                  <div key={p.pid} className="row flex items-center gap-2 px-3.5 py-[3px] text-[10.5px] font-mono" title={p.command}>
                    <span className="shrink-0 text-right text-white/30 tabular-nums" style={{ width: 48 }}>{p.pid}</span>
                    <span className="shrink-0 truncate text-white/45" style={{ width: 56 }}>{p.user}</span>
                    <span className="shrink-0 text-right tabular-nums" style={{ width: 40, color: p.cpu_pct >= 50 ? utilColor(p.cpu_pct) : 'rgba(255,255,255,0.7)' }}>
                      {p.cpu_pct.toFixed(1)}
                    </span>
                    <span className="shrink-0 text-right text-white/70 tabular-nums" style={{ width: 44 }}>{fmtMB(p.mem_mb)}</span>
                    <span className="flex-1 min-w-0 truncate text-white/45 pl-2">{p.command}</span>
                  </div>
                ))}
              </div>
              <p className="px-3.5 py-1.5 text-[10px] font-mono text-white/20 border-t hairline">
                top {data.procs.length} of {data.procs_total} processes · refreshes every {POLL_MS / 1000}s
              </p>
            </div>
          </>
        )}

        {error && <p className="text-[11px] text-amber-300/70">{error}</p>}
      </div>
    </div>
  )
}
