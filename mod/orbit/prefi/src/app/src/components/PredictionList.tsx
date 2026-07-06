'use client'

import { useAccount } from 'wagmi'
import { usePreFi } from '@/hooks/usePreFi'
import { formatPrice } from '@/lib/contracts'

export default function PredictionList() {
  const { address } = useAccount()
  const { markets, userPredictions, claimReward, isLoading } = usePreFi()

  const getMarketAsset = (marketId: number) => {
    const market = markets.find(m => m.id === marketId)
    return market?.asset || `Market #${marketId}`
  }

  const getStatus = (pred: any) => {
    const market = markets.find(m => m.id === pred.marketId)
    if (!market) return 'active'
    if (!market.settled) return 'active'
    if (pred.claimed) return pred.reward > BigInt(0) ? 'won' : 'lost'
    return 'settled'
  }

  const statusStyles: Record<string, { bg: string; text: string; label: string }> = {
    active: { bg: 'bg-blue-500/10 border-blue-500/20', text: 'text-blue-400', label: 'Active' },
    settled: { bg: 'bg-amber-500/10 border-amber-500/20', text: 'text-amber-400', label: 'Claimable' },
    won: { bg: 'bg-emerald-500/10 border-emerald-500/20', text: 'text-emerald-400', label: 'Won' },
    lost: { bg: 'bg-red-500/10 border-red-500/20', text: 'text-red-400', label: 'Lost' },
  }

  return (
    <div className="glass-card-elevated rounded-2xl p-6">
      {userPredictions.length > 0 ? (
        <div className="space-y-3">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider">History</span>
            <span className="text-xs text-gray-500 tabular-nums">{userPredictions.length} total</span>
          </div>

          <div className="space-y-3 max-h-[500px] overflow-y-auto pr-1">
            {userPredictions.map((pred, idx) => {
              const status = getStatus(pred)
              const style = statusStyles[status] || statusStyles.active
              const canClaim = status === 'settled' && pred.reward > BigInt(0) && !pred.claimed

              return (
                <div
                  key={`${pred.marketId}-${idx}`}
                  className="p-4 bg-white/[0.02] border border-white/[0.04] rounded-xl hover:bg-white/[0.04] transition-colors"
                >
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold text-sm">{getMarketAsset(pred.marketId)}</span>
                      <span className="text-[11px] text-gray-600">#{pred.marketId}</span>
                    </div>
                    <span className={`tag border ${style.bg} ${style.text}`}>
                      {style.label}
                    </span>
                  </div>

                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <div className="text-[11px] text-gray-500 mb-0.5">Predicted</div>
                      <div className="text-sm font-bold text-blue-300 tabular-nums">
                        ${formatPrice(pred.predictedPrice)}
                      </div>
                    </div>
                    <div>
                      <div className="text-[11px] text-gray-500 mb-0.5">Staked</div>
                      <div className="text-sm font-bold text-gray-300 tabular-nums">
                        {formatPrice(pred.stakedAmount)}
                      </div>
                    </div>
                  </div>

                  {pred.reward > BigInt(0) && (
                    <div className="mt-3 pt-3 border-t border-white/[0.04] flex items-center justify-between">
                      <div>
                        <span className="text-[11px] text-gray-500">Reward </span>
                        <span className="text-sm font-bold text-emerald-400 tabular-nums">
                          +{formatPrice(pred.reward)}
                        </span>
                      </div>
                      {canClaim && (
                        <button
                          onClick={() => claimReward(pred.marketId)}
                          disabled={isLoading}
                          className="btn-primary text-white font-semibold py-1.5 px-4 rounded-lg text-xs disabled:opacity-50"
                        >
                          {isLoading ? '...' : 'Claim'}
                        </button>
                      )}
                      {pred.claimed && (
                        <span className="text-[11px] text-emerald-500 font-medium">Claimed</span>
                      )}
                    </div>
                  )}

                  {pred.timestamp > 0 && (
                    <div className="mt-2 text-[11px] text-gray-600 tabular-nums">
                      {new Date(pred.timestamp * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      ) : (
        <div className="text-center py-12">
          <div className="w-16 h-16 mx-auto rounded-2xl bg-white/[0.03] border border-white/[0.06] flex items-center justify-center mb-4">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-gray-500">
              <path d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/>
            </svg>
          </div>
          <h3 className="text-sm font-semibold text-gray-300 mb-1">No Predictions Yet</h3>
          <p className="text-xs text-gray-500 max-w-[200px] mx-auto">
            Place your first prediction to start tracking here.
          </p>
        </div>
      )}
    </div>
  )
}
