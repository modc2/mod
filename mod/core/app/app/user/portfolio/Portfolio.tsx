"use client";

import { useState, useEffect } from 'react'
import { userContext } from '@/context'
import { ethers } from 'ethers'
import { Wallet, TrendingUp, DollarSign } from 'lucide-react'
import { getChainConfig } from '@/network/chainConfig'
import { PortfolioControlBar } from './PortfolioControlBar'

const ERC20_ABI = ['function balanceOf(address) view returns (uint256)', 'function decimals() view returns (uint8)', 'function symbol() view returns (string)']

interface TokenBalance {
  symbol: string
  balance: string
  usdValue: string
  address: string
}

export function Portfolio() {
  const { user } = userContext()
  const [balances, setBalances] = useState<TokenBalance[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [totalValue, setTotalValue] = useState('0.00')
  const [networkUrl, setNetworkUrl] = useState('')

  useEffect(() => {
    if (typeof window !== 'undefined') {
      const savedUrl = localStorage.getItem('network_url') || 'http://127.0.0.1:8545'
      setNetworkUrl(savedUrl)

      const handleStorageChange = () => {
        const updatedUrl = localStorage.getItem('network_url') || 'http://127.0.0.1:8545'
        setNetworkUrl(updatedUrl)
      }

      window.addEventListener('storage', handleStorageChange)
      return () => window.removeEventListener('storage', handleStorageChange)
    }
  }, [])

  const checkBalances = async () => {
    if (!user?.key) {
      setError('No wallet connected')
      return
    }

    if (!networkUrl) {
      setError('Network URL not configured')
      return
    }

    setLoading(true)
    setError('')
    const results: TokenBalance[] = []

    try {
      const provider = new ethers.JsonRpcProvider(networkUrl)
      const chainConfig = getChainConfig()

      if (!chainConfig) {
        throw new Error('Chain config not found')
      }

      // ETH (native chain balance)
      try {
        const ethBalance = await provider.getBalance(user.key)
        const ethFormatted = ethers.formatEther(ethBalance)
        results.push({
          symbol: 'ETH',
          balance: parseFloat(ethFormatted).toFixed(4),
          usdValue: parseFloat(ethFormatted).toFixed(4),
          address: 'native'
        })
      } catch (err) {
        console.error('Error fetching ETH balance:', err)
        results.push({ symbol: 'ETH', balance: 'Error', usdValue: '0.00', address: '' })
      }

      // Native Token
      try {
        const nativeTokenAddress = chainConfig.contracts.NativeToken.address
        const nativeContract = new ethers.Contract(nativeTokenAddress, ERC20_ABI, provider)
        const nativeBalance = await nativeContract.balanceOf(user.key)
        const nativeDecimals = await nativeContract.decimals()
        const nativeSymbol = await nativeContract.symbol()
        const nativeFormatted = ethers.formatUnits(nativeBalance, nativeDecimals)
        results.push({
          symbol: nativeSymbol,
          balance: parseFloat(nativeFormatted).toFixed(2),
          usdValue: parseFloat(nativeFormatted).toFixed(2),
          address: nativeTokenAddress
        })
      } catch (err) {
        console.error('Error fetching native token:', err)
        results.push({ symbol: 'NATIVE', balance: 'Error', usdValue: '0.00', address: '' })
      }

      // USDC
      try {
        const usdcAddress = chainConfig.contracts?.USDC?.address || '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48'
        const usdcContract = new ethers.Contract(usdcAddress, ERC20_ABI, provider)
        const usdcBalance = await usdcContract.balanceOf(user.key)
        const usdcDecimals = await usdcContract.decimals()
        const usdcFormatted = ethers.formatUnits(usdcBalance, usdcDecimals)
        results.push({
          symbol: 'USDC',
          balance: parseFloat(usdcFormatted).toFixed(2),
          usdValue: parseFloat(usdcFormatted).toFixed(2),
          address: usdcAddress
        })
      } catch (err) {
        console.error('Error fetching USDC:', err)
        results.push({ symbol: 'USDC', balance: 'Error', usdValue: '0.00', address: '' })
      }

      // USDT
      try {
        const usdtAddress = chainConfig.contracts?.USDT?.address || '0xdAC17F958D2ee523a2206206994597C13D831ec7'
        const usdtContract = new ethers.Contract(usdtAddress, ERC20_ABI, provider)
        const usdtBalance = await usdtContract.balanceOf(user.key)
        const usdtDecimals = await usdtContract.decimals()
        const usdtFormatted = ethers.formatUnits(usdtBalance, usdtDecimals)
        results.push({
          symbol: 'USDT',
          balance: parseFloat(usdtFormatted).toFixed(2),
          usdValue: parseFloat(usdtFormatted).toFixed(2),
          address: usdtAddress
        })
      } catch (err) {
        console.error('Error fetching USDT:', err)
        results.push({ symbol: 'USDT', balance: 'Error', usdValue: '0.00', address: '' })
      }

      setBalances(results)
      const total = results.reduce((sum, token) => sum + (parseFloat(token.usdValue) || 0), 0)
      setTotalValue(total.toFixed(2))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch balances')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (user?.key && networkUrl) {
      checkBalances()
    }
  }, [user?.key, networkUrl])

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Wallet className="w-8 h-8 text-purple-500" />
          <h2 className="text-3xl font-black text-purple-500 uppercase">Portfolio</h2>
        </div>
        <button
          onClick={checkBalances}
          disabled={loading || !user?.key}
          className="px-6 py-3 bg-purple-500/20 border-2 border-purple-500 text-purple-500 font-black uppercase rounded-lg hover:bg-purple-500/30 disabled:opacity-50 transition-all"
        >
          {loading ? 'REFRESHING...' : 'REFRESH'}
        </button>
      </div>

      {/* Unified Control Bar */}
      <PortfolioControlBar />

      {error && (
        <div className="p-4 bg-red-500/20 border-2 border-red-500 text-red-500 rounded-lg font-mono">
          {error}
        </div>
      )}

      <div className="p-6 bg-gradient-to-br from-purple-500/10 via-pink-500/10 to-blue-500/10 border-2 border-purple-500/50 rounded-xl">
        <div className="flex items-center justify-between mb-6">
          <div className="text-purple-500/70 font-bold text-sm uppercase">Total Portfolio Value</div>
          <TrendingUp className="w-6 h-6 text-purple-500" />
        </div>
        <div className="text-purple-500 font-black text-5xl" suppressHydrationWarning>${totalValue}</div>
      </div>

      <div className="bg-black border-2 border-purple-500/30 rounded-xl overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="bg-purple-500/10 border-b-2 border-purple-500/30">
              <th className="px-6 py-4 text-left text-purple-500 font-black uppercase text-sm">Token</th>
              <th className="px-6 py-4 text-right text-purple-500 font-black uppercase text-sm">Balance</th>
              <th className="px-6 py-4 text-right text-purple-500 font-black uppercase text-sm">USD Value</th>
            </tr>
          </thead>
          <tbody>
            {balances.length === 0 && !loading ? (
              <tr>
                <td colSpan={3} className="px-6 py-8 text-center text-white/50 font-mono">
                  No balances to display. Click refresh to load.
                </td>
              </tr>
            ) : (
              balances.map((token, idx) => (
                <tr key={idx} className="border-b border-purple-500/20 hover:bg-purple-500/5 transition-colors">
                  <td className="px-6 py-4">
                    <div className="flex items-center gap-3">
                      <DollarSign className="w-5 h-5 text-purple-500" />
                      <span className="text-purple-500 font-bold text-lg">{token.symbol}</span>
                    </div>
                  </td>
                  <td className="px-6 py-4 text-right text-purple-400 font-mono text-lg">
                    {token.balance}
                  </td>
                  <td className="px-6 py-4 text-right text-purple-400 font-mono text-lg font-bold">
                    ${token.usdValue}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {user?.key && (
        <div className="p-4 bg-black/40 border border-purple-500/20 rounded-lg">
          <div className="text-purple-500/60 text-sm font-mono">
            <p className="mb-2"><strong>Wallet:</strong> {user.key.slice(0, 10)}...{user.key.slice(-8)}</p>
            <p className="mb-2"><strong>Network URL:</strong> {networkUrl}</p>
          </div>
        </div>
      )}
    </div>
  )
}
