'use client'
import React, { useState, useEffect } from 'react'
import { Gift, Loader2, CheckCircle, AlertCircle, Coins } from 'lucide-react'
import { useUserContext } from '@/bloc/context/UserContext'

export const Claim: React.FC = () => {
  const { network, user } = useUserContext()
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [walletAddress, setWalletAddress] = useState('')
  const [claimableAmount, setClaimableAmount] = useState<string>('0')

  useEffect(() => {
    if (typeof window === 'undefined') return
    const address = user?.key || ''
    const mode = localStorage.getItem('wallet_mode')
    if (mode === 'subwallet' && address) {
      setWalletAddress(address)
    }
  }, [user])

  const handleClaim = async () => {
    if (!walletAddress) {
      setError('No wallet connected. Please connect SubWallet first.')
      return
    }

    if (!network) {
      setError('Network not initialized')
      return
    }

    setIsLoading(true)
    setError(null)
    setSuccess(null)

    try {
      const result = await network.claim(walletAddress)

      setSuccess(`Successfully claimed MOD tokens! Block: ${result.blockHash || 'pending'}`)
    } catch (err: any) {
      let msg = err?.message || String(err)
      if (msg.includes('1010')) {
        msg = 'Insufficient balance for transaction fees.'
      } else if (msg.toLowerCase().includes('cancel')) {
        msg = 'Transaction cancelled by user.'
      } else if (msg.includes('timeout')) {
        msg = 'Transaction timeout. Please try again.'
      } else if (msg.toLowerCase().includes('no claimable')) {
        msg = 'No claimable tokens available at this time.'
      }
      setError(msg)
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="space-y-6 animate-fadeIn">
      {/* Claim Section */}
      <div className="space-y-4 p-6 rounded-xl bg-gradient-to-br from-purple-500/10 via-pink-500/10 to-cyan-500/10 border-2 border-purple-500/30 shadow-2xl">

        <div className="space-y-4">
          <div className="p-4 bg-black/60 rounded-lg border-2 border-purple-500/30">
            <div className="flex items-center gap-2 text-purple-300 mb-2">
              <Coins size={20} />
              <span className="font-mono text-sm uppercase font-bold">Claimable Amount</span>
            </div>
            <p className="text-3xl font-black text-purple-400 font-mono">
              {claimableAmount} MOD
            </p>
            <p className="text-xs text-purple-500/70 mt-2 font-mono">
              Claim your earned MOD tokens from the network
            </p>
          </div>

          <button
            onClick={handleClaim}
            disabled={isLoading || !walletAddress || !network}
            className="w-full py-4 border-2 border-purple-500/60 bg-gradient-to-r from-purple-500/20 to-pink-500/20 text-purple-400 hover:bg-purple-500/30 hover:border-purple-500 hover:scale-[1.02] transition-all duration-300 rounded-xl font-mono uppercase font-black text-lg disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100 flex items-center justify-center gap-3 shadow-lg"
          >
            {isLoading ? (
              <>
                <Loader2 size={20} className="animate-spin" />
                <span>CLAIMING...</span>
              </>
            ) : (
              <>
                <Gift size={20} />
                <span>CLAIM MOD TOKENS</span>
              </>
            )}
          </button>
        </div>
      </div>

      {/* Success Message */}
      {success && (
        <div className="space-y-4 p-6 rounded-xl border-2 border-emerald-500/40 bg-gradient-to-br from-emerald-500/10 shadow-2xl">
          <div className="flex items-center gap-3 text-base font-mono uppercase font-bold text-emerald-400">
            <CheckCircle size={20} />
            <span>SUCCESS</span>
          </div>
          <div className="text-emerald-400 font-mono text-sm bg-black/60 p-4 rounded-lg border-2 border-emerald-500/30">
            {success}
          </div>
        </div>
      )}

      {/* Error Message */}
      {error && (
        <div className="space-y-4 p-6 rounded-xl border-2 border-red-500/40 bg-gradient-to-br from-red-500/10 shadow-2xl">
          <div className="flex items-center gap-3 text-base font-mono uppercase font-bold text-red-400">
            <AlertCircle size={20} />
            <span>ERROR</span>
          </div>
          <div className="text-red-400 font-mono text-base bg-black/60 p-4 rounded-lg border-2 border-red-500/30 whitespace-pre-wrap font-bold">
            {error}
          </div>
        </div>
      )}

      {/* Info Section */}
      <div className="p-4 bg-purple-900/20 border-2 border-purple-500/30 rounded-xl">
        <h4 className="font-bold mb-2 text-purple-300 font-mono uppercase text-sm">ℹ️ CLAIM INFO</h4>
        <ul className="text-xs space-y-1 text-purple-400 font-mono">
          <li>• Claim your earned MOD tokens from network participation</li>
          <li>• Requires SubWallet connection to sign the transaction</li>
          <li>• Transaction will be submitted to {network?.url || 'the network'}</li>
          <li>• Small network fee required for claiming</li>
        </ul>
      </div>
    </div>
  )
}

export default Claim