"use client";

import { BuildingLibraryIcon, ChevronDownIcon, ChevronUpIcon } from '@heroicons/react/24/outline'
import { useState, useEffect } from 'react'
import { CopyButton } from '@/ui/CopyButton'
import { ethers } from 'ethers'
import { useRouter } from 'next/navigation'
import { Market } from '@/network/Market'
import { getChainConfig } from '@/network/chainConfig'

export function TreasuryHeader() {
  const router = useRouter()
  const [treasuryAddress, setTreasuryAddress] = useState('')
  const [totalUsdValue, setTotalUsdValue] = useState('0')
  const [showTreasuryDetails, setShowTreasuryDetails] = useState(false)
  const [usdcBalance, setUsdcBalance] = useState('0')
  const [usdtBalance, setUsdtBalance] = useState('0')
  const market = new Market(getChainConfig())

  useEffect(() => {
    const chainConfig = getChainConfig()
    if (chainConfig?.contracts?.Treasury?.address) {
      setTreasuryAddress(chainConfig.contracts.Treasury.address)
    }
  }, [])

  useEffect(() => {
    const fetchTreasuryBalance = async () => {
      if (!treasuryAddress || typeof window.ethereum === 'undefined') return
      
      try {
        let  totalUsd = await market.checkMarketBalance(treasuryAddress)
        
        setTotalUsdValue(totalUsd.toFixed(2))
      } catch (error) {
        console.error('Error fetching treasury balance:', error)
      }
    }

    if (treasuryAddress) {
      fetchTreasuryBalance()
      // Removed interval - only fetches once
    }
  }, [treasuryAddress])

  const handleClick = () => {
    router.push('/user/billing')
  }

  if (!treasuryAddress) return null

  return (
    <div
      className="relative flex items-center gap-3 px-4 py-2 border-4 transition-all cursor-pointer uppercase"
      style={{
        height: '44px',
        minWidth: '80px',
        backgroundColor: 'var(--bg-secondary)',
        borderColor: 'var(--border-strong)',
        fontFamily: 'var(--font-digital)'
      }}
      onMouseEnter={() => setShowTreasuryDetails(true)}
      onMouseLeave={() => setShowTreasuryDetails(false)}
      onClick={handleClick}
      title={`Treasury Balance: $${totalUsdValue} - Click to go to Billing`}
    >
      <div className="w-8 h-8 flex items-center justify-center border-4 transition-all" style={{ borderColor: 'var(--border-strong)', backgroundColor: 'var(--bg-primary)' }}>
        <BuildingLibraryIcon className="w-6 h-6" style={{ color: 'var(--text-primary)' }} />
      </div>
      <span className="text-xl font-digital font-bold tracking-wide" style={{ color: 'var(--text-primary)' }}>
        ${totalUsdValue}
      </span>

      {showTreasuryDetails && (
        <>
          <div className="absolute top-full left-0 mt-2 border-4 p-5 z-50 min-w-[320px]" style={{ backgroundColor: 'var(--bg-secondary)', borderColor: 'var(--border-strong)' }}>
            <div className="flex items-center justify-between mb-4">
              <span className="text-xl uppercase font-bold tracking-[0.2em]" style={{ color: 'var(--text-secondary)', fontFamily: 'var(--font-digital)' }}>▸ TREASURY</span>
              <ChevronUpIcon className="w-6 h-6" style={{ color: 'var(--text-primary)' }} />
            </div>

            <div className="flex items-center gap-3 mb-4 border-4 p-3" style={{ borderColor: 'var(--border-strong)', backgroundColor: 'var(--bg-primary)' }}>
              <span className="text-base font-mono uppercase tracking-wide font-bold" style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-digital)' }}>
                {treasuryAddress.slice(0, 6)}...{treasuryAddress.slice(-4)}
              </span>
              <CopyButton text={treasuryAddress} size="sm" />
            </div>

            <div className="flex items-center justify-between pt-4 border-t-4" style={{ borderColor: 'var(--border-strong)' }}>
              <span className="text-xl uppercase font-bold tracking-[0.15em]" style={{ color: 'var(--text-secondary)', fontFamily: 'var(--font-digital)' }}>TOTAL</span>
              <span className="text-2xl font-mono font-bold tabular-nums" style={{ color: 'var(--text-primary)', fontFamily: 'var(--font-digital)' }}>
                ${totalUsdValue}
              </span>
            </div>
          </div>
        </>
      )}
    </div>
  )
}