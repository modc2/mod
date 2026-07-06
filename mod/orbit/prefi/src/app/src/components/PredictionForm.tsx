'use client'

import { useState, useEffect } from 'react'
import { useAccount } from 'wagmi'
import { usePreFi } from '@/hooks/usePreFi'

interface PredictionFormProps {
  selectedMarketId?: number | null
  onPredictionPlaced?: () => void
  currentPrice?: number
}

export default function PredictionForm({ selectedMarketId, onPredictionPlaced, currentPrice }: PredictionFormProps) {
  const { address } = useAccount()
  const { markets, placePrediction, isLoading, getTokenBalance } = usePreFi()
  const [predictedPrice, setPredictedPrice] = useState('')
  const [collateralAmount, setCollateralAmount] = useState('')
  const [marketId, setMarketId] = useState<number | null>(null)
  const [balance, setBalance] = useState('0')

  const activeMarkets = markets.filter(m => !m.settled && m.endTime > Date.now() / 1000)

  useEffect(() => {
    if (selectedMarketId) {
      setMarketId(selectedMarketId)
    } else if (activeMarkets.length > 0 && !marketId) {
      setMarketId(activeMarkets[0].id)
    }
  }, [selectedMarketId, activeMarkets.length])

  useEffect(() => {
    if (address) {
      getTokenBalance().then(setBalance)
    }
  }, [address])

  const selectedMarket = markets.find(m => m.id === marketId)
  const balanceNum = parseFloat(balance)

  const handleQuickFill = (pct: number) => {
    if (balanceNum > 0) {
      setCollateralAmount((balanceNum * pct).toFixed(6))
    }
  }

  const handleUseCurrentPrice = () => {
    if (currentPrice) {
      setPredictedPrice(currentPrice.toString())
    }
  }

  // Calculate distance from current price
  const priceDistance = currentPrice && predictedPrice
    ? ((parseFloat(predictedPrice) - currentPrice) / currentPrice * 100)
    : null

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!marketId || !predictedPrice || !collateralAmount) return

    const success = await placePrediction(marketId, predictedPrice, collateralAmount)
    if (success) {
      setPredictedPrice('')
      setCollateralAmount('')
      onPredictionPlaced?.()
    }
  }

  // Time remaining for selected market
  const timeLeft = selectedMarket ? Math.max(0, selectedMarket.endTime - Date.now() / 1000) : 0
  const hours = Math.floor(timeLeft / 3600)
  const minutes = Math.floor((timeLeft % 3600) / 60)

  return (
    <form onSubmit={handleSubmit} className="glass-card-elevated rounded-2xl p-6 space-y-5">
      {/* Market selector */}
      <div className="space-y-2">
        <label className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Market</label>
        {activeMarkets.length > 0 ? (
          <select
            value={marketId || ''}
            onChange={(e) => setMarketId(Number(e.target.value))}
            className="input-field cursor-pointer"
          >
            {activeMarkets.map(m => (
              <option key={m.id} value={m.id}>
                #{m.id} — {m.asset}
              </option>
            ))}
          </select>
        ) : (
          <div className="input-field text-gray-500">
            No active markets available
          </div>
        )}
      </div>

      {/* Market info bar */}
      {selectedMarket && (
        <div className="flex items-center justify-between px-3 py-2.5 bg-blue-500/[0.06] border border-blue-500/10 rounded-lg text-sm">
          <div className="flex items-center gap-4">
            <span className="text-gray-400">Asset</span>
            <span className="font-semibold text-blue-300">{selectedMarket.asset}</span>
          </div>
          <div className="flex items-center gap-2 text-gray-400">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>
            </svg>
            <span className="font-semibold text-emerald-400 tabular-nums">{hours}h {minutes}m</span>
          </div>
        </div>
      )}

      {/* Price input */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <label className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Predicted Price</label>
          {currentPrice && (
            <button
              type="button"
              onClick={handleUseCurrentPrice}
              className="text-xs text-blue-400 hover:text-blue-300 transition-colors"
            >
              Current: ${currentPrice.toLocaleString(undefined, { maximumFractionDigits: 2 })}
            </button>
          )}
        </div>
        <div className="relative">
          <input
            type="number"
            step="0.01"
            value={predictedPrice}
            onChange={(e) => setPredictedPrice(e.target.value)}
            placeholder={currentPrice ? currentPrice.toLocaleString(undefined, { maximumFractionDigits: 2 }) : '0.00'}
            className="input-field pr-16 text-lg font-semibold tabular-nums"
            required
          />
          <span className="absolute right-4 top-1/2 -translate-y-1/2 text-xs font-semibold text-gray-500">USD</span>
        </div>
        {priceDistance !== null && predictedPrice && (
          <div className={`text-xs font-medium tabular-nums ${
            Math.abs(priceDistance) < 1 ? 'text-emerald-400' :
            Math.abs(priceDistance) < 5 ? 'text-yellow-400' : 'text-red-400'
          }`}>
            {priceDistance >= 0 ? '+' : ''}{priceDistance.toFixed(2)}% from current price
          </div>
        )}
      </div>

      {/* Stake input */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <label className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Stake Amount</label>
          <span className="text-xs text-gray-500 tabular-nums">
            Balance: {balanceNum.toFixed(4)}
          </span>
        </div>
        <div className="relative">
          <input
            type="number"
            step="0.000001"
            value={collateralAmount}
            onChange={(e) => setCollateralAmount(e.target.value)}
            placeholder="0.0"
            className="input-field pr-20 text-lg font-semibold tabular-nums"
            required
          />
          <span className="absolute right-4 top-1/2 -translate-y-1/2 text-xs font-semibold text-gray-500">TOKEN</span>
        </div>

        {/* Quick fill buttons */}
        <div className="flex items-center gap-2">
          {[
            { label: '25%', pct: 0.25 },
            { label: '50%', pct: 0.5 },
            { label: '75%', pct: 0.75 },
            { label: 'MAX', pct: 1 },
          ].map(({ label, pct }) => (
            <button
              key={label}
              type="button"
              onClick={() => handleQuickFill(pct)}
              disabled={balanceNum <= 0}
              className="flex-1 py-1.5 text-xs font-semibold text-gray-400 bg-white/[0.03] border border-white/[0.06] rounded-lg hover:bg-white/[0.06] hover:text-gray-300 transition-all disabled:opacity-30 disabled:cursor-not-allowed"
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Scoring info */}
      <div className="flex items-start gap-3 px-3 py-3 bg-white/[0.02] border border-white/[0.04] rounded-lg">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-blue-400 mt-0.5 flex-shrink-0">
          <circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>
        </svg>
        <p className="text-xs text-gray-400 leading-relaxed">
          <span className="text-gray-300 font-medium">L2 Distance Scoring:</span> Score = stake / (1 + distance²). Closer predictions earn a larger share of the reward pool.
        </p>
      </div>

      {/* Submit */}
      <button
        type="submit"
        disabled={isLoading || !predictedPrice || !collateralAmount || !marketId}
        className="w-full btn-shine text-white font-semibold py-3.5 px-8 rounded-xl disabled:opacity-30 disabled:cursor-not-allowed"
      >
        {isLoading ? (
          <span className="flex items-center justify-center gap-2">
            <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
            </svg>
            Placing...
          </span>
        ) : (
          'Place Prediction'
        )}
      </button>

      <p className="text-[11px] text-gray-600 text-center">
        Collateral is locked until market settlement
      </p>
    </form>
  )
}
