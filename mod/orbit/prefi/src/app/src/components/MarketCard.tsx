'use client'

import { motion } from 'framer-motion'
import { formatPrice } from '@/lib/contracts'

interface MarketCardProps {
  market: {
    id: number
    asset: string
    startTime: number
    endTime: number
    settled: boolean
    actualPrice: bigint
    totalStaked: bigint
    playersCount: number
  }
  onPredict?: () => void
}

export default function MarketCard({ market, onPredict }: MarketCardProps) {
  const timeLeft = Math.max(0, market.endTime - Date.now() / 1000)
  const totalDuration = market.endTime - market.startTime
  const elapsed = totalDuration - timeLeft
  const progressPct = totalDuration > 0 ? Math.min(100, (elapsed / totalDuration) * 100) : 0

  const hours = Math.floor(timeLeft / 3600)
  const minutes = Math.floor((timeLeft % 3600) / 60)

  const isActive = !market.settled && timeLeft > 0

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className="glass-card rounded-xl p-5 card-hover"
    >
      <div className="flex items-start justify-between mb-4">
        <div>
          <h3 className="text-lg font-bold mb-0.5">{market.asset}</h3>
          <p className="text-[11px] text-gray-500">Market #{market.id}</p>
        </div>
        <span className={`tag border ${
          isActive
            ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400'
            : market.settled
            ? 'bg-blue-500/10 border-blue-500/20 text-blue-400'
            : 'bg-gray-500/10 border-gray-500/20 text-gray-400'
        }`}>
          {isActive ? 'Live' : market.settled ? 'Settled' : 'Ended'}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-3 mb-4">
        <div className="p-2.5 bg-black/20 rounded-lg">
          <div className="text-[11px] text-gray-500 mb-0.5">Staked</div>
          <div className="text-sm font-bold text-emerald-400 tabular-nums">
            {formatPrice(market.totalStaked, 18)} ETH
          </div>
        </div>
        <div className="p-2.5 bg-black/20 rounded-lg">
          <div className="text-[11px] text-gray-500 mb-0.5">Players</div>
          <div className="text-sm font-bold text-blue-400 tabular-nums">
            {market.playersCount}
          </div>
        </div>
      </div>

      {market.settled ? (
        <div className="p-3 bg-blue-500/[0.06] border border-blue-500/10 rounded-lg">
          <div className="text-[11px] text-gray-400 mb-0.5">Final Price</div>
          <div className="text-xl font-bold text-blue-300 tabular-nums">
            ${formatPrice(market.actualPrice, 18)}
          </div>
        </div>
      ) : isActive ? (
        <div className="space-y-3">
          {/* Time progress */}
          <div>
            <div className="flex items-center justify-between text-[11px] mb-1.5">
              <span className="text-gray-500">Time remaining</span>
              <span className="font-semibold text-emerald-400 tabular-nums">{hours}h {minutes}m</span>
            </div>
            <div className="progress-bar">
              <div
                className="progress-bar-fill bg-gradient-to-r from-emerald-500 to-blue-500"
                style={{ width: `${progressPct}%` }}
              />
            </div>
          </div>

          {onPredict && (
            <button
              onClick={onPredict}
              className="w-full btn-primary text-white font-semibold py-2.5 px-4 rounded-lg text-sm"
            >
              Predict
            </button>
          )}
        </div>
      ) : (
        <div className="text-center py-2 text-xs text-gray-500">
          Awaiting settlement
        </div>
      )}
    </motion.div>
  )
}
