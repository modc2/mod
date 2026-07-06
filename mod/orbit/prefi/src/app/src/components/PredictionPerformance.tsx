'use client'

import { useState, useEffect } from 'react'
import { motion } from 'framer-motion'
import { useAccount } from 'wagmi'
import { API_BASE_URL } from '@/lib/contracts'

interface PerfRecord {
  market_id: number
  asset: string
  predicted_price: number
  actual_price: number
  stake: number
  reward: number
  pnl: number
  error_pct: number
  address: string
  date: string
}

interface PerfData {
  days: number
  total_predictions: number
  total_staked: number
  total_reward: number
  total_pnl: number
  avg_error_pct: number
  win_rate: number
  records: PerfRecord[]
}

export default function PredictionPerformance() {
  const { address } = useAccount()
  const [perf, setPerf] = useState<PerfData | null>(null)
  const [loading, setLoading] = useState(true)
  const [viewMode, setViewMode] = useState<'my' | 'all'>('all')

  useEffect(() => {
    const fetchPerf = async () => {
      setLoading(true)
      try {
        const params = new URLSearchParams({ days: '30' })
        if (viewMode === 'my' && address) {
          params.set('address', address)
        }
        const res = await fetch(`${API_BASE_URL}/predictions/performance?${params}`)
        if (res.ok) setPerf(await res.json())
      } catch {
      } finally {
        setLoading(false)
      }
    }
    fetchPerf()
  }, [address, viewMode])

  if (loading) {
    return (
      <div className="glass-card rounded-2xl p-6">
        <div className="text-gray-600 text-sm animate-pulse text-center py-8">Loading performance...</div>
      </div>
    )
  }

  if (!perf || perf.total_predictions === 0) {
    return (
      <div className="glass-card rounded-2xl p-6">
        <h3 className="text-lg font-bold text-gray-200 mb-1">30-Day Performance</h3>
        <p className="text-xs text-gray-500 mb-6">Track prediction accuracy and P&L</p>
        <div className="text-center py-6 text-sm text-gray-500">
          No settled predictions in the last 30 days.
        </div>
      </div>
    )
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      className="glass-card rounded-2xl p-6"
    >
      <div className="flex items-center justify-between mb-5">
        <div>
          <h3 className="text-lg font-bold text-gray-200">30-Day Performance</h3>
          <p className="text-xs text-gray-500">{perf.total_predictions} predictions settled</p>
        </div>
        {address && (
          <div className="flex bg-black/20 rounded-lg p-0.5">
            {(['all', 'my'] as const).map(mode => (
              <button
                key={mode}
                onClick={() => setViewMode(mode)}
                className={`px-3 py-1 rounded-md text-xs font-semibold transition-all ${
                  viewMode === mode
                    ? 'bg-blue-500/20 text-blue-400'
                    : 'text-gray-500 hover:text-gray-300'
                }`}
              >
                {mode === 'all' ? 'All' : 'Mine'}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        {[
          { label: 'P&L', value: `${perf.total_pnl >= 0 ? '+' : ''}${perf.total_pnl.toFixed(2)}`, color: perf.total_pnl >= 0 ? 'text-emerald-400' : 'text-red-400' },
          { label: 'Win Rate', value: `${perf.win_rate}%`, color: 'text-blue-400' },
          { label: 'Avg Error', value: `${perf.avg_error_pct}%`, color: 'text-amber-400' },
          { label: 'Staked', value: perf.total_staked.toFixed(2), color: 'text-purple-400' },
        ].map(({ label, value, color }) => (
          <div key={label} className="p-3 bg-black/20 rounded-xl text-center">
            <div className="text-[11px] text-gray-500 mb-1">{label}</div>
            <div className={`text-lg font-bold tabular-nums ${color}`}>{value}</div>
          </div>
        ))}
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-[11px] text-gray-500 uppercase tracking-wider border-b border-white/[0.04]">
              <th className="text-left py-2.5 px-2 font-semibold">Date</th>
              <th className="text-left py-2.5 px-2 font-semibold">Asset</th>
              <th className="text-right py-2.5 px-2 font-semibold">Predicted</th>
              <th className="text-right py-2.5 px-2 font-semibold">Actual</th>
              <th className="text-right py-2.5 px-2 font-semibold">Error</th>
              <th className="text-right py-2.5 px-2 font-semibold">Stake</th>
              <th className="text-right py-2.5 px-2 font-semibold">P&L</th>
            </tr>
          </thead>
          <tbody>
            {perf.records.slice(0, 20).map((r, i) => (
              <tr key={i} className="border-b border-white/[0.02] hover:bg-white/[0.02] transition-colors">
                <td className="py-2.5 px-2 text-gray-400 tabular-nums">{r.date}</td>
                <td className="py-2.5 px-2 font-medium text-gray-200">{r.asset}</td>
                <td className="py-2.5 px-2 text-right text-gray-400 tabular-nums">
                  ${r.predicted_price.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                </td>
                <td className="py-2.5 px-2 text-right text-gray-400 tabular-nums">
                  ${r.actual_price.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                </td>
                <td className="py-2.5 px-2 text-right">
                  <span className={`tabular-nums ${
                    r.error_pct < 2 ? 'text-emerald-400' : r.error_pct < 5 ? 'text-amber-400' : 'text-red-400'
                  }`}>
                    {r.error_pct}%
                  </span>
                </td>
                <td className="py-2.5 px-2 text-right text-gray-400 tabular-nums">{r.stake.toFixed(2)}</td>
                <td className={`py-2.5 px-2 text-right font-semibold tabular-nums ${r.pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                  {r.pnl >= 0 ? '+' : ''}{r.pnl.toFixed(4)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </motion.div>
  )
}
