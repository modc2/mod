'use client'

import { useState } from 'react'
import { useAccount, useNetwork } from 'wagmi'
import { motion } from 'framer-motion'

export default function SwapWidget() {
  const { isConnected } = useAccount()
  const { chain } = useNetwork()
  const [fromAmount, setFromAmount] = useState('')
  const [toAmount, setToAmount] = useState('')

  const handleSwap = () => {
    // TODO: Integrate Uniswap SDK
    console.log('Swap:', fromAmount, toAmount)
  }

  if (!isConnected) return null

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="glass-card rounded-2xl p-6 space-y-4"
    >
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-xl font-bold gradient-text">Quick Swap</h3>
        <span className="text-xs bg-blue-500/20 text-blue-300 px-3 py-1 rounded-full">
          Uniswap V3
        </span>
      </div>

      {/* From Token */}
      <div className="space-y-2">
        <label className="text-sm text-gray-400">From</label>
        <div className="bg-gray-900/50 border border-gray-700 rounded-xl p-4">
          <div className="flex items-center justify-between">
            <input
              type="number"
              value={fromAmount}
              onChange={(e) => setFromAmount(e.target.value)}
              placeholder="0.0"
              className="bg-transparent text-2xl font-bold outline-none w-full"
            />
            <button className="flex items-center gap-2 bg-gray-800 hover:bg-gray-700 px-4 py-2 rounded-lg transition">
              <span className="text-2xl">🔷</span>
              <span className="font-bold">ETH</span>
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </button>
          </div>
        </div>
      </div>

      {/* Swap Arrow */}
      <div className="flex justify-center">
        <button className="bg-gray-800 hover:bg-blue-500 p-3 rounded-xl transition transform hover:scale-110">
          <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16V4m0 0L3 8m4-4l4 4m6 0v12m0 0l4-4m-4 4l-4-4" />
          </svg>
        </button>
      </div>

      {/* To Token */}
      <div className="space-y-2">
        <label className="text-sm text-gray-400">To</label>
        <div className="bg-gray-900/50 border border-gray-700 rounded-xl p-4">
          <div className="flex items-center justify-between">
            <input
              type="number"
              value={toAmount}
              onChange={(e) => setToAmount(e.target.value)}
              placeholder="0.0"
              className="bg-transparent text-2xl font-bold outline-none w-full"
            />
            <button className="flex items-center gap-2 bg-gray-800 hover:bg-gray-700 px-4 py-2 rounded-lg transition">
              <span className="text-2xl">💵</span>
              <span className="font-bold">USDC</span>
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </button>
          </div>
        </div>
      </div>

      <button
        onClick={handleSwap}
        className="w-full btn-shine text-white font-bold py-4 px-8 rounded-xl transition transform hover:scale-105"
      >
        Swap on Uniswap
      </button>

      <p className="text-xs text-center text-gray-500">
        Powered by Uniswap V3 • Best rates guaranteed
      </p>
    </motion.div>
  )
}
