"use client";

import { useState, useEffect } from 'react'
import { getChainConfig } from '@/network/chainConfig'
import { ethers } from 'ethers'
import { toast } from 'react-toastify'

interface TokenOption {
  address: string
  symbol: string
  decimals: number
}

interface TransferHeaderProps {
  selectedToken: TokenOption | null
  setSelectedToken: (token: TokenOption | null) => void
  walletAddress: string
  currentNetwork: string
}

export const TransferHeader: React.FC<TransferHeaderProps> = ({
  selectedToken,
  setSelectedToken,
  walletAddress,
  currentNetwork
}) => {
  const [availableTokens, setAvailableTokens] = useState<TokenOption[]>([])
  const [customTokenAddress, setCustomTokenAddress] = useState('')

  useEffect(() => {
    const tokens: TokenOption[] = [
      { address: 'ETH', symbol: 'ETH', decimals: 18 }
    ]

    const chainConfig = getChainConfig()
    if (chainConfig?.contracts) {
      if (chainConfig.contracts.USDC?.address) {
        tokens.push({
          address: chainConfig.contracts.USDC.address,
          symbol: 'USDC',
          decimals: 6
        })
      }
      if (chainConfig.contracts.USDT?.address) {
        tokens.push({
          address: chainConfig.contracts.USDT.address,
          symbol: 'USDT',
          decimals: 6
        })
      }
      if (chainConfig.contracts.Market?.address) {
        tokens.push({
          address: chainConfig.contracts.Market.address,
          symbol: 'MARKET',
          decimals: 18
        })
      }
    }

    setAvailableTokens(tokens)
    if (!selectedToken && tokens.length > 0) {
      setSelectedToken(tokens[0])
    }
  }, [currentNetwork])

  const handleAddCustomToken = async () => {
    if (!customTokenAddress || !ethers.isAddress(customTokenAddress)) {
      toast.error('Invalid token address')
      return
    }

    try {
      const url = localStorage.getItem('network_url') || 'http://localhost:8545'
      const provider = new ethers.JsonRpcProvider(url)
      const contract = new ethers.Contract(
        customTokenAddress,
        ['function symbol() view returns (string)', 'function decimals() view returns (uint8)'],
        provider
      )

      const [symbol, decimals] = await Promise.all([
        contract.symbol(),
        contract.decimals()
      ])

      const newToken: TokenOption = {
        address: customTokenAddress,
        symbol,
        decimals
      }

      setAvailableTokens(prev => [...prev, newToken])
      setSelectedToken(newToken)
      setCustomTokenAddress('')
    } catch (err) {
      console.error('Failed to add custom token:', err)
      toast.error('Failed to fetch token info. Make sure it is a valid ERC20 token.')
    }
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <label className="text-sm text-green-400 font-mono uppercase font-bold tracking-wide">
          Select Token
        </label>
        <select
          value={selectedToken?.address || ''}
          onChange={(e) => {
            const token = availableTokens.find(t => t.address === e.target.value)
            setSelectedToken(token || null)
          }}
          className="w-full bg-black/60 border-2 border-green-500/40 rounded-lg px-4 py-3 text-green-300 font-mono text-base focus:outline-none focus:border-green-500 focus:ring-2 focus:ring-green-500/30 transition-all"
        >
          {availableTokens.map((token) => (
            <option key={token.address} value={token.address}>
              {token.symbol} {token.address !== 'ETH' ? `(${token.address.slice(0, 6)}...${token.address.slice(-4)})` : ''}
            </option>
          ))}
        </select>
      </div>

      <div className="space-y-2">
        <label className="text-sm text-green-400 font-mono uppercase font-bold tracking-wide">
          Add Custom Token (ERC20)
        </label>
        <div className="flex gap-2">
          <input
            type="text"
            value={customTokenAddress}
            onChange={(e) => setCustomTokenAddress(e.target.value)}
            placeholder="0x... (Token Contract Address)"
            className="flex-1 bg-black/60 border-2 border-green-500/40 rounded-lg px-4 py-3 text-green-300 font-mono text-base placeholder-green-600/50 focus:outline-none focus:border-green-500 focus:ring-2 focus:ring-green-500/30 transition-all"
          />
          <button
            onClick={handleAddCustomToken}
            className="px-6 py-3 border-2 border-green-500/60 bg-gradient-to-r from-green-500/20 to-emerald-500/20 text-green-400 hover:bg-green-500/30 hover:border-green-500 transition-all rounded-lg font-mono uppercase font-bold text-sm"
          >
            Add
          </button>
        </div>
      </div>
    </div>
  )
}

export default TransferHeader