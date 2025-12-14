'use client'
import React, { useState, useEffect } from 'react'
import { ShoppingCart, Zap, CheckCircle, AlertCircle, DollarSign } from 'lucide-react'
import { useUserContext } from '@/bloc/context/UserContext'

export const BuyBlocktime: React.FC = () => {
  const { network, user } = useUserContext()
  const [amount, setAmount] = useState('')
  const [basePrice, setBasePrice] = useState<number>(0.01)
  const [totalCost, setTotalCost] = useState<number>(0)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [walletAddress, setWalletAddress] = useState('')
  const [balance, setBalance] = useState<string>('0')
  const [myBlocktime, setMyBlocktime] = useState<number>(0)

  useEffect(() => {
    if (typeof window === 'undefined') return
    const address = user?.key || ''
    const mode = localStorage.getItem('wallet_mode')
    if (mode === 'subwallet' && address) {
      setWalletAddress(address)
      fetchBalance(address)
      fetchMyBlocktime(address)
    }
  }, [user])

  useEffect(() => {
    const blocktimeAmount = parseInt(amount) || 0
    setTotalCost(blocktimeAmount * basePrice)
  }, [amount, basePrice])

  const fetchBalance = async (address: string) => {
    try {
      const formattedBalance: string = (await network.balance(address)).toFixed(6)
      setBalance(formattedBalance)
    } catch (err) {
      console.error('Balance fetch error:', err)
    }
  }

  const fetchMyBlocktime = async (address: string) => {
    try {
      const blocktime = await network.getBlocktime(address)
      setMyBlocktime(blocktime || 0)
    } catch (err) {
      console.error('Failed to fetch blocktime:', err)
    }
  }

  const handleBuyBlocktime = async () => {
    if (!amount || parseInt(amount) <= 0) {
      setError('Please enter a valid amount')
      return
    }
    if (!walletAddress) {
      setError('No wallet connected')
      return
    }

    const blocktimeAmount = parseInt(amount)
    const cost = blocktimeAmount * basePrice

    if (parseFloat(balance) < cost) {
      setError('Insufficient balance for purchase')
      return
    }

    setIsLoading(true)
    setError(null)
    setSuccess(null)

    try {
      const result = await network.buyBlocktime(
        walletAddress,
        blocktimeAmount,
        cost
      )

      setSuccess(`Successfully purchased ${blocktimeAmount} blocktime for ${cost.toFixed(4)} MOD!`)
      setAmount('')
      await fetchBalance(walletAddress)
      await fetchMyBlocktime(walletAddress)
    } catch (err: any) {
      let msg = err?.message || String(err)
      if (msg.includes('1010')) msg = 'Insufficient balance for transaction.'
      else if (msg.toLowerCase().includes('cancel')) msg = 'Transaction cancelled by user.'
      setError(msg)
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="space-y-6 animate-fadeIn">
      <div className="p-4 rounded-xl border-2 bg-gradient-to-br from-cyan-500/10 border-cyan-500/40">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-cyan-400 text-sm font-mono font-bold">
            <DollarSign size={18} />
            <span>MY BLOCKTIME</span>
          </div>
          <div className="text-cyan-300 text-xl font-mono font-black">
            {myBlocktime}
          </div>
        </div>
      </div>

      <div className="p-4 rounded-xl border-2 bg-gradient-to-br from-emerald-500/10 border-emerald-500/40">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-emerald-400 text-sm font-mono font-bold">
            <CheckCircle size={18} />
            <span>BALANCE</span>
          </div>
          <div className="text-emerald-300 text-xl font-mono font-black">
            {balance} <span className="text-emerald-500">MOD</span>
          </div>
        </div>
      </div>

      <div className="space-y-5 p-6 rounded-xl bg-gradient-to-br from-blue-500/10 via-purple-500/10 to-pink-500/10 border-2 border-blue-500/30 shadow-2xl">
        <div className="flex items-center gap-3 pb-4 border-b-2 border-blue-500/30">
          <ShoppingCart size={24} className="text-blue-400" />
          <h3 className="text-2xl font-black text-blue-400 font-mono uppercase tracking-wide">Buy Blocktime</h3>
        </div>

        <div className="space-y-2">
          <label className="text-sm text-blue-400 font-mono uppercase font-bold tracking-wide">
            Base Price (MOD per Blocktime)
          </label>
          <input
            type="number"
            value={basePrice}
            onChange={(e) => setBasePrice(parseFloat(e.target.value) || 0.01)}
            disabled={isLoading}
            min="0.001"
            step="0.001"
            placeholder="0.01"
            className="w-full bg-black/60 border-2 border-blue-500/40 rounded-lg px-4 py-3 text-blue-300 font-mono text-base placeholder-blue-600/50 focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 disabled:opacity-50 transition-all"
          />
        </div>

        <div className="space-y-2">
          <label className="text-sm text-blue-400 font-mono uppercase font-bold tracking-wide">
            Amount (Blocktime)
          </label>
          <input
            type="number"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            disabled={isLoading}
            min="1"
            placeholder="100"
            className="w-full bg-black/60 border-2 border-blue-500/40 rounded-lg px-4 py-3 text-blue-300 font-mono text-base placeholder-blue-600/50 focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 disabled:opacity-50 transition-all"
          />
        </div>

        <div className="p-4 rounded-xl border-2 border-purple-500/40 bg-gradient-to-br from-purple-500/10">
          <div className="flex items-center justify-between">
            <span className="text-purple-400 font-mono uppercase font-bold text-sm">Total Cost:</span>
            <span className="text-purple-300 font-mono font-black text-xl">
              {totalCost.toFixed(4)} <span className="text-purple-500">MOD</span>
            </span>
          </div>
        </div>

        <button
          onClick={handleBuyBlocktime}
          disabled={!amount || isLoading || !walletAddress}
          className="w-full py-4 border-2 border-blue-500/60 bg-gradient-to-r from-blue-500/20 to-purple-500/20 text-blue-400 hover:bg-blue-500/30 hover:border-blue-500 hover:scale-[1.02] transition-all duration-300 rounded-xl font-mono uppercase font-black text-lg disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100 flex items-center justify-center gap-3 shadow-lg"
        >
          {isLoading ? (
            <>
              <Zap size={20} className="animate-spin" />
              <span>PROCESSING...</span>
            </>
          ) : (
            <>
              <ShoppingCart size={20} />
              <span>BUY BLOCKTIME</span>
            </>
          )}
        </button>
      </div>

      {(success || error) && (
        <div
          className={`space-y-4 p-6 rounded-xl border-2 shadow-2xl ${
            error
              ? 'from-red-500/10 border-red-500/40 bg-gradient-to-br'
              : 'from-emerald-500/10 border-emerald-500/40 bg-gradient-to-br'
          }`}
        >
          <div className="flex items-center gap-3 text-base font-mono uppercase font-bold">
            {error ? (
              <>
                <AlertCircle size={20} className="text-red-500" />
                <span className="text-red-500">ERROR</span>
              </>
            ) : (
              <>
                <CheckCircle size={20} className="text-emerald-500" />
                <span className="text-emerald-500">SUCCESS</span>
              </>
            )}
          </div>
          <div className={`font-mono text-base bg-black/60 p-4 rounded-lg border-2 ${
            error ? 'text-red-400 border-red-500/30' : 'text-emerald-400 border-emerald-500/30'
          } whitespace-pre-wrap font-bold`}>
            {error || success}
          </div>
        </div>
      )}
    </div>
  )
}

export default BuyBlocktime