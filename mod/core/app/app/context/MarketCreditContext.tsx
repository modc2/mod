"use client";

import { createContext, useContext, useState, useEffect, ReactNode } from 'react'
import { userContext } from './UserContext'
import { MarketAllowanceManager } from '@/network/marketAllowance'
import { getChainConfig } from '@/network/chainConfig'

interface MarketCreditContextType {
  marketCredit: number
  loading: boolean
  refreshCredit: () => Promise<void>
}

const MarketCreditContext = createContext<MarketCreditContextType | undefined>(undefined)

export function MarketCreditProvider({ children }: { children: ReactNode }) {
  const { user, network } = userContext()
  const [marketCredit, setMarketCredit] = useState(0)
  const [loading, setLoading] = useState(true)

  const refreshCredit = async () => {
    if (!user?.key || typeof window === 'undefined' || !window.ethereum) {
      setMarketCredit(0)
      setLoading(false)
      return
    }

    try {
      setLoading(true)
      const manager = new MarketAllowanceManager(getChainConfig())
      const credit = await manager.checkMarketAllowance(user.key, 'USDC')
      setMarketCredit(credit)
    } catch (error) {
      console.error('Failed to fetch market tokens:', error)
      setMarketCredit(0)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refreshCredit()
  }, [user?.key])

  return (
    <MarketCreditContext.Provider value={{ marketCredit, loading, refreshCredit }}>
      {children}
    </MarketCreditContext.Provider>
  )
}

export function useMarketCredit() {
  const context = useContext(MarketCreditContext)
  if (!context) throw new Error('useMarketCredit must be used within MarketCreditProvider')
  return context
}
