'use client'

import { useState, useEffect } from 'react'
import { motion } from 'framer-motion'
import { API_BASE_URL } from '@/lib/contracts'

interface PricePoint {
  timestamp: number
  price: number
}

interface PriceChartProps {
  asset: string
  currentPrice?: string
}

export default function PriceChart({ asset, currentPrice }: PriceChartProps) {
  const [history, setHistory] = useState<PricePoint[]>([])
  const [stats, setStats] = useState<{ high: number | null; low: number | null; change_pct: number }>({ high: null, low: null, change_pct: 0 })
  const [loading, setLoading] = useState(true)
  const [hoverInfo, setHoverInfo] = useState<{ price: number; date: string; x: number; y: number } | null>(null)

  useEffect(() => {
    const fetchHistory = async () => {
      try {
        const ticker = asset.split('/')[0] || 'ETH'
        const res = await fetch(`${API_BASE_URL}/prices/history/${ticker}?days=30`)
        if (res.ok) {
          const data = await res.json()
          if (data.prices) {
            setHistory(data.prices)
            setStats({ high: data.high, low: data.low, change_pct: data.change_pct || 0 })
          }
        }
      } catch {
      } finally {
        setLoading(false)
      }
    }
    fetchHistory()
  }, [asset])

  const renderChart = () => {
    if (history.length < 2) return null

    const prices = history.map(p => p.price)
    const min = Math.min(...prices)
    const max = Math.max(...prices)
    const range = max - min || 1

    const w = 800
    const h = 180
    const padY = 8

    const points = prices.map((price, i) => {
      const x = (i / (prices.length - 1)) * w
      const y = padY + ((max - price) / range) * (h - padY * 2)
      return { x, y, price, idx: i }
    })

    const isPositive = prices[prices.length - 1] >= prices[0]
    const color = isPositive ? '#34d399' : '#f87171'

    const linePath = `M${points.map(p => `${p.x},${p.y}`).join(' L')}`
    const areaPath = `M0,${h} L${points.map(p => `${p.x},${p.y}`).join(' L')} L${w},${h} Z`

    // Grid lines
    const gridLines = [0.25, 0.5, 0.75].map(pct => padY + pct * (h - padY * 2))

    const handleMouseMove = (e: React.MouseEvent<SVGSVGElement>) => {
      const svg = e.currentTarget
      const rect = svg.getBoundingClientRect()
      const mouseX = ((e.clientX - rect.left) / rect.width) * w
      const idx = Math.round((mouseX / w) * (prices.length - 1))
      if (idx >= 0 && idx < prices.length) {
        const date = new Date(history[idx].timestamp).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
        setHoverInfo({ price: prices[idx], date, x: points[idx].x, y: points[idx].y })
      }
    }

    return (
      <svg
        viewBox={`0 0 ${w} ${h}`}
        className="w-full h-full cursor-crosshair"
        preserveAspectRatio="none"
        onMouseMove={handleMouseMove}
        onMouseLeave={() => setHoverInfo(null)}
      >
        <defs>
          <linearGradient id="chartGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.15" />
            <stop offset="100%" stopColor={color} stopOpacity="0.01" />
          </linearGradient>
        </defs>

        {/* Grid */}
        {gridLines.map((y, i) => (
          <line key={i} x1="0" y1={y} x2={w} y2={y} stroke="rgba(255,255,255,0.03)" strokeWidth="1" />
        ))}

        <path d={areaPath} fill="url(#chartGrad)" />
        <path d={linePath} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />

        {/* Current price dot */}
        <circle cx={points[points.length - 1].x} cy={points[points.length - 1].y} r="3" fill={color}>
          <animate attributeName="r" values="3;5;3" dur="2s" repeatCount="indefinite" />
          <animate attributeName="opacity" values="1;0.6;1" dur="2s" repeatCount="indefinite" />
        </circle>

        {/* Hover crosshair */}
        {hoverInfo && (
          <>
            <line x1={hoverInfo.x} y1="0" x2={hoverInfo.x} y2={h} stroke="rgba(255,255,255,0.1)" strokeWidth="1" strokeDasharray="4 4" />
            <circle cx={hoverInfo.x} cy={hoverInfo.y} r="4" fill={color} stroke="rgba(0,0,0,0.5)" strokeWidth="1.5" />
          </>
        )}
      </svg>
    )
  }

  const renderDates = () => {
    if (history.length < 2) return null
    const first = new Date(history[0].timestamp)
    const last = new Date(history[history.length - 1].timestamp)
    const fmt = (d: Date) => d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
    return (
      <div className="flex justify-between text-[11px] text-gray-600 mt-1 px-0.5">
        <span>{fmt(first)}</span>
        <span>{fmt(last)}</span>
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
          <h3 className="text-lg font-bold text-gray-200">{asset} Price</h3>
          <p className="text-xs text-gray-500">30-Day History</p>
        </div>
        <div className="text-right">
          {hoverInfo ? (
            <>
              <div className="text-xs text-gray-500">{hoverInfo.date}</div>
              <div className="text-xl font-bold text-white tabular-nums">
                ${hoverInfo.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}
              </div>
            </>
          ) : (
            <>
              <div className="text-xs text-gray-500">Current</div>
              <div className="text-xl font-bold text-white tabular-nums">
                ${currentPrice || '—'}
              </div>
              {stats.change_pct !== 0 && (
                <div className={`text-xs font-semibold tabular-nums ${stats.change_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                  {stats.change_pct >= 0 ? '+' : ''}{stats.change_pct}%
                </div>
              )}
            </>
          )}
        </div>
      </div>

      <div className="h-44 rounded-xl overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center h-full">
            <div className="text-gray-600 text-sm animate-pulse">Loading...</div>
          </div>
        ) : history.length >= 2 ? (
          <div className="h-full">{renderChart()}</div>
        ) : (
          <div className="flex items-center justify-center h-full text-sm text-gray-600">
            No data available
          </div>
        )}
      </div>
      {renderDates()}

      {/* Stats row */}
      <div className="grid grid-cols-3 gap-3 mt-4">
        <div className="text-center p-2.5 bg-black/20 rounded-lg">
          <div className="text-[11px] text-gray-500 mb-0.5">30d High</div>
          <div className="text-sm font-bold text-emerald-400 tabular-nums">
            {stats.high ? `$${stats.high.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : '—'}
          </div>
        </div>
        <div className="text-center p-2.5 bg-black/20 rounded-lg">
          <div className="text-[11px] text-gray-500 mb-0.5">30d Low</div>
          <div className="text-sm font-bold text-red-400 tabular-nums">
            {stats.low ? `$${stats.low.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : '—'}
          </div>
        </div>
        <div className="text-center p-2.5 bg-black/20 rounded-lg">
          <div className="text-[11px] text-gray-500 mb-0.5">30d Change</div>
          <div className={`text-sm font-bold tabular-nums ${stats.change_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
            {stats.change_pct !== 0 ? `${stats.change_pct >= 0 ? '+' : ''}${stats.change_pct}%` : '—'}
          </div>
        </div>
      </div>
    </motion.div>
  )
}
