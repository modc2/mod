"use client";

import { useState, useRef, useEffect } from 'react'
import { userContext } from '@/context'
import { MagnifyingGlassIcon, CreditCardIcon, ArrowsRightLeftIcon, ArrowPathIcon, BanknotesIcon, FolderIcon, GlobeAltIcon } from '@heroicons/react/24/outline'
import { ethers } from 'ethers'
import { getChainConfig, getContracts, getRpcUrl } from '@/network/chainConfig'

const ERC20_ABI = ['function balanceOf(address) view returns (uint256)', 'function decimals() view returns (uint8)', 'function symbol() view returns (string)']

async function fetchOnChainBalances(address: string): Promise<Record<string, number>> {
  const provider = new ethers.JsonRpcProvider(getRpcUrl())
  const CONTRACTS = getContracts()
  const balances: Record<string, number> = {}

  const tokenDefs: { symbol: string; address: string; decimals?: number }[] = [
    { symbol: 'MARKET', address: CONTRACTS.Market?.address, decimals: 8 },
    { symbol: 'USDC', address: CONTRACTS.USDC?.address },
    { symbol: 'USDT', address: CONTRACTS.USDT?.address },
    { symbol: 'NativeToken', address: CONTRACTS.NativeToken?.address },
  ]

  const promises: Promise<void>[] = []

  promises.push(
    provider.getBalance(address).then(bal => {
      balances['ETH'] = parseFloat(ethers.formatEther(bal))
    }).catch(() => { balances['ETH'] = 0 })
  )

  for (const token of tokenDefs) {
    if (!token.address) continue
    promises.push(
      (async () => {
        try {
          const contract = new ethers.Contract(token.address, ERC20_ABI, provider)
          const [raw, decimals] = await Promise.all([
            contract.balanceOf(address),
            token.decimals != null ? Promise.resolve(token.decimals) : contract.decimals().then(Number),
          ])
          balances[token.symbol] = parseFloat(ethers.formatUnits(raw, decimals))
        } catch {
          balances[token.symbol] = 0
        }
      })()
    )
  }

  await Promise.all(promises)
  return balances
}

type ActionType = 'add' | 'send' | 'port' | 'token' | 'network' | 'txs'

interface BalanceItem {
  id: string
  symbol: string
  balance: string
  type: 'action' | 'balance'
  icon?: any
  action?: () => void
}

export function PortfolioControlBar() {
  const { user, client } = userContext()
  const [searchTerm, setSearchTerm] = useState('')
  const [balances, setBalances] = useState<Record<string, number>>({})
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [activeAction, setActiveAction] = useState<ActionType | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  const fetchBalances = async () => {
    if (!user?.key) return

    setIsRefreshing(true)
    try {
      const result = await fetchOnChainBalances(user.key)
      setBalances(result || {})
    } catch (err) {
      console.error('Error fetching balances:', err)
    } finally {
      setIsRefreshing(false)
    }
  }

  useEffect(() => {
    if (user?.key) {
      fetchBalances()
    }
  }, [user?.key])

  // Action items
  const actionItems: BalanceItem[] = [
    {
      id: 'add',
      symbol: 'ADD',
      balance: '',
      type: 'action',
      icon: CreditCardIcon,
      action: () => setActiveAction('add')
    },
    {
      id: 'send',
      symbol: 'SEND',
      balance: '',
      type: 'action',
      icon: ArrowsRightLeftIcon,
      action: () => setActiveAction('send')
    },
    {
      id: 'port',
      symbol: 'PORT',
      balance: '',
      type: 'action',
      icon: FolderIcon,
      action: () => setActiveAction('port')
    },
    {
      id: 'token',
      symbol: 'TOKEN',
      balance: '',
      type: 'action',
      icon: ArrowPathIcon,
      action: () => fetchBalances()
    },
    {
      id: 'network',
      symbol: 'NETWORK',
      balance: '',
      type: 'action',
      icon: GlobeAltIcon,
      action: () => setActiveAction('network')
    },
    {
      id: 'txs',
      symbol: 'TXS',
      balance: '',
      type: 'action',
      icon: BanknotesIcon,
      action: () => setActiveAction('txs')
    }
  ]

  // Balance items
  const balanceItems: BalanceItem[] = Object.entries(balances).map(([symbol, balance]) => ({
    id: symbol.toLowerCase(),
    symbol: symbol,
    balance: symbol === 'MARKET' || symbol === 'USDC' || symbol === 'USDT'
      ? `$${balance.toFixed(2)}`
      : balance.toFixed(6),
    type: 'balance'
  }))

  // Combine all items
  const allItems = [...actionItems, ...balanceItems]

  // Filter items based on search
  const filteredItems = allItems.filter(item =>
    item.symbol.toLowerCase().includes(searchTerm.toLowerCase())
  )

  return (
    <div className="space-y-2">
      {/* Search Bar */}
      <div className="relative">
        <MagnifyingGlassIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-purple-500" />
        <input
          type="text"
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
          placeholder="Search actions & tokens..."
          className="w-full pl-10 pr-4 py-2 bg-black border-2 border-purple-500/50 text-white placeholder-purple-500/50 focus:outline-none focus:border-purple-500 font-mono text-sm"
          style={{ borderRadius: 0 }}
        />
      </div>

      {/* Scrollable Items Bar */}
      <div
        ref={scrollRef}
        className="flex gap-0 overflow-x-auto scrollbar-thin scrollbar-thumb-purple-500 scrollbar-track-black pb-2"
        style={{ scrollbarWidth: 'thin', scrollbarColor: '#a855f7 #000000' }}
      >
        {filteredItems.map((item) => (
          <button
            key={item.id}
            onClick={item.action}
            disabled={item.type === 'balance' && !item.action}
            className={`flex-shrink-0 flex flex-col items-center justify-center gap-2 px-6 py-4 border-2 transition-all ${
              item.type === 'action'
                ? 'border-purple-500 bg-purple-500/10 hover:bg-purple-500/20 cursor-pointer'
                : 'border-purple-500/30 bg-black cursor-default'
            } ${activeAction === item.id ? 'bg-purple-500/30' : ''}`}
            style={{
              borderRadius: 0,
              minWidth: '100px',
              width: '100px',
              height: '100px'
            }}
          >
            {item.icon && (
              <item.icon
                className={`w-6 h-6 ${item.type === 'action' ? 'text-purple-500' : 'text-purple-500/50'} ${
                  item.id === 'token' && isRefreshing ? 'animate-spin' : ''
                }`}
              />
            )}
            <div className="flex flex-col items-center">
              <span className="text-xs font-black uppercase tracking-wider text-purple-500">
                {item.symbol}
              </span>
              {item.balance && (
                <span className="text-lg font-mono font-bold text-purple-400 tabular-nums">
                  {item.balance}
                </span>
              )}
            </div>
          </button>
        ))}
      </div>

      {filteredItems.length === 0 && (
        <div className="text-center py-8 text-purple-500/50 text-sm font-mono">
          No items found matching "{searchTerm}"
        </div>
      )}
    </div>
  )
}
