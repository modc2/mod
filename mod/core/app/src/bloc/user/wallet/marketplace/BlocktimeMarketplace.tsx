'use client'
import React, { useState, useEffect } from 'react'
import { DollarSign, ShoppingCart, Tag, CheckCircle, AlertCircle, Zap } from 'lucide-react'
import { useUserContext } from '@/bloc/context/UserContext'

interface BlocktimeListing {
  id: string
  seller: string
  blocktime: number
  price: number
  timestamp: number
}

export const BlocktimeMarketplace: React.FC = () => {
  const { network, user, client } = useUserContext()
  const [listings, setListings] = useState<BlocktimeListing[]>([])
  const [myBlocktime, setMyBlocktime] = useState<number>(0)
  const [sellPrice, setSellPrice] = useState<string>('')
  const [sellAmount, setSellAmount] = useState<string>('')
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [walletAddress, setWalletAddress] = useState('')

  useEffect(() => {
    if (typeof window === 'undefined') return
    const address = user?.key || ''
    const mode = localStorage.getItem('wallet_mode')
    if (mode === 'subwallet' && address) {
      setWalletAddress(address)
      fetchMyBlocktime(address)
    }
  }, [user])

  useEffect(() => {
    fetchListings()
  }, [client])

  const fetchMyBlocktime = async (address: string) => {
    try {
      const blocktime = await network.getBlocktime(address)
      setMyBlocktime(blocktime || 0)
    } catch (err) {
      console.error('Failed to fetch blocktime:', err)
    }
  }

  const fetchListings = async () => {
    if (!client) return
    try {
      const response = await client.call('get_blocktime_listings', {})
      setListings(response || [])
    } catch (err) {
      console.error('Failed to fetch listings:', err)
    }
  }

  const handleCreateListing = async () => {
    if (!sellPrice || !sellAmount) {
      setError('Please enter both price and amount')
      return
    }
    if (!walletAddress) {
      setError('No wallet connected')
      return
    }

    const amount = parseInt(sellAmount)
    const price = parseFloat(sellPrice)

    if (amount > myBlocktime) {
      setError('Insufficient blocktime balance')
      return
    }

    setIsLoading(true)
    setError(null)
    setSuccess(null)

    try {
      const result = await network.createBlocktimeListing(
        walletAddress,
        amount,
        price
      )

      setSuccess(`Listing created successfully! ${amount} blocktime at ${price} MOD each`)
      setSellPrice('')
      setSellAmount('')
      await fetchMyBlocktime(walletAddress)
      await fetchListings()
    } catch (err: any) {
      let msg = err?.message || String(err)
      if (msg.includes('1010')) msg = 'Insufficient balance for fees.'
      else if (msg.toLowerCase().includes('cancel')) msg = 'Transaction cancelled by user.'
      setError(msg)
    } finally {
      setIsLoading(false)
    }
  }

  const handleBuyListing = async (listing: BlocktimeListing) => {
    if (!walletAddress) {
      setError('No wallet connected')
      return
    }

    setIsLoading(true)
    setError(null)
    setSuccess(null)

    try {
      const result = await network.buyBlocktimeListing(
        walletAddress,
        listing.id,
        listing.price
      )

      setSuccess(`Successfully purchased ${listing.blocktime} blocktime for ${listing.price} MOD!`)
      await fetchMyBlocktime(walletAddress)
      await fetchListings()
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
            <Tag size={18} />
            <span>MY BLOCKTIME</span>
          </div>
          <div className="text-cyan-300 text-xl font-mono font-black">
            {myBlocktime}
          </div>
        </div>
      </div>

      <div className="space-y-5 p-6 rounded-xl bg-gradient-to-br from-green-500/10 via-emerald-500/10 to-teal-500/10 border-2 border-green-500/30 shadow-2xl">
        <div className="flex items-center gap-3 pb-4 border-b-2 border-green-500/30">
          <DollarSign size={24} className="text-green-400" />
          <h3 className="text-2xl font-black text-green-400 font-mono uppercase tracking-wide">Sell Blocktime</h3>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="space-y-2">
            <label className="text-sm text-green-400 font-mono uppercase font-bold tracking-wide">
              Amount (Blocktime)
            </label>
            <input
              type="number"
              value={sellAmount}
              onChange={(e) => setSellAmount(e.target.value)}
              disabled={isLoading}
              min="1"
              placeholder="100"
              className="w-full bg-black/60 border-2 border-green-500/40 rounded-lg px-4 py-3 text-green-300 font-mono text-base placeholder-green-600/50 focus:outline-none focus:border-green-500 focus:ring-2 focus:ring-green-500/30 disabled:opacity-50 transition-all"
            />
          </div>

          <div className="space-y-2">
            <label className="text-sm text-green-400 font-mono uppercase font-bold tracking-wide">
              Price (MOD per unit)
            </label>
            <input
              type="number"
              value={sellPrice}
              onChange={(e) => setSellPrice(e.target.value)}
              disabled={isLoading}
              min="0"
              step="0.01"
              placeholder="0.5"
              className="w-full bg-black/60 border-2 border-green-500/40 rounded-lg px-4 py-3 text-green-300 font-mono text-base placeholder-green-600/50 focus:outline-none focus:border-green-500 focus:ring-2 focus:ring-green-500/30 disabled:opacity-50 transition-all"
            />
          </div>
        </div>

        <button
          onClick={handleCreateListing}
          disabled={!sellPrice || !sellAmount || isLoading || !walletAddress}
          className="w-full py-4 border-2 border-green-500/60 bg-gradient-to-r from-green-500/20 to-emerald-500/20 text-green-400 hover:bg-green-500/30 hover:border-green-500 hover:scale-[1.02] transition-all duration-300 rounded-xl font-mono uppercase font-black text-lg disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100 flex items-center justify-center gap-3 shadow-lg"
        >
          {isLoading ? (
            <>
              <Zap size={20} className="animate-spin" />
              <span>CREATING...</span>
            </>
          ) : (
            <>
              <Tag size={20} />
              <span>CREATE LISTING</span>
            </>
          )}
        </button>
      </div>

      <div className="space-y-4">
        <h3 className="text-xl font-black text-purple-400 font-mono uppercase tracking-wide flex items-center gap-2">
          <ShoppingCart size={20} />
          ACTIVE LISTINGS
        </h3>
        {listings.length === 0 ? (
          <div className="p-6 rounded-xl border-2 border-purple-500/30 bg-gradient-to-br from-purple-500/10 text-center">
            <p className="text-purple-400/60 font-mono">No active listings available</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4">
            {listings.map((listing) => (
              <div
                key={listing.id}
                className="p-5 rounded-xl border-2 border-purple-500/30 bg-gradient-to-br from-purple-500/10 hover:border-purple-500/50 transition-all"
              >
                <div className="flex items-center justify-between">
                  <div className="space-y-2">
                    <div className="text-purple-300 font-mono text-sm">
                      <span className="text-purple-500/70">SELLER:</span> {listing.seller.slice(0, 8)}...{listing.seller.slice(-6)}
                    </div>
                    <div className="text-purple-300 font-mono text-lg font-bold">
                      {listing.blocktime} BLOCKTIME @ {listing.price} MOD
                    </div>
                    <div className="text-purple-500/50 font-mono text-xs">
                      Total: {(listing.blocktime * listing.price).toFixed(2)} MOD
                    </div>
                  </div>
                  <button
                    onClick={() => handleBuyListing(listing)}
                    disabled={isLoading || listing.seller === walletAddress}
                    className="px-6 py-3 border-2 border-purple-500/60 bg-gradient-to-r from-purple-500/20 to-pink-500/20 text-purple-400 hover:bg-purple-500/30 hover:border-purple-500 transition-all rounded-lg font-mono uppercase font-bold disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                  >
                    <ShoppingCart size={16} />
                    BUY
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
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

export default BlocktimeMarketplace