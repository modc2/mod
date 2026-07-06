"use client"

import { useState, useEffect } from 'react'
import { CopyButton } from '@/ui/CopyButton'
import { TerminalCard } from './GlowCard'
import type { SafeInfo } from '@/network/safe'
import { ethers } from 'ethers'
import { getChainConfig } from '@/network/chainConfig'

const TERM_FONT = "var(--font-digital), 'JetBrains Mono', 'Courier New', monospace"

const ERC20_ABI = [
  'function balanceOf(address owner) view returns (uint256)',
  'function decimals() view returns (uint8)',
  'function symbol() view returns (string)',
]

interface TokenBalance {
  symbol: string
  name: string
  balance: number
  address: string
}

export function OverviewTab({
  safeInfo, walletAddress, ethBalance, loading,
}: {
  safeInfo: SafeInfo | null
  walletAddress: string
  ethBalance: string
  loading: boolean
}) {
  const [tokenBalances, setTokenBalances] = useState<TokenBalance[]>([])
  const [loadingBalances, setLoadingBalances] = useState(false)

  const fetchTokenBalances = async () => {
    if (!safeInfo?.address || typeof window === 'undefined' || !window.ethereum) return
    setLoadingBalances(true)
    try {
      const provider = new ethers.BrowserProvider(window.ethereum)
      const chainConfig = getChainConfig()
      if (!chainConfig?.contracts) return

      const tokens: { name: string; address: string }[] = []
      for (const [name, val] of Object.entries(chainConfig.contracts) as [string, any][]) {
        if (['USDC', 'USDT', 'NativeToken', 'Market'].includes(name) && val.address) {
          tokens.push({ name, address: val.address })
        }
      }

      const results: TokenBalance[] = []
      for (const token of tokens) {
        try {
          const contract = new ethers.Contract(token.address, ERC20_ABI, provider)
          const [raw, decimals, symbol] = await Promise.all([
            contract.balanceOf(safeInfo.address),
            contract.decimals(),
            contract.symbol(),
          ])
          const balance = parseFloat(ethers.formatUnits(raw, decimals))
          results.push({ symbol, name: token.name, balance, address: token.address })
        } catch (err) {
          console.error(`Failed to fetch ${token.name} balance:`, err)
        }
      }
      setTokenBalances(results)
    } catch (err) {
      console.error('Failed to fetch token balances:', err)
    } finally {
      setLoadingBalances(false)
    }
  }

  useEffect(() => {
    if (safeInfo?.address) fetchTokenBalances()
  }, [safeInfo?.address]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!safeInfo && !loading) {
    return (
      <div className="py-12 text-center" style={{ fontFamily: TERM_FONT, fontSize: '14px', color: 'var(--text-tertiary)' }}>
        <span style={{ opacity: 0.4 }}>{'>'}</span> enter a safe address and press load
      </div>
    )
  }

  if (!safeInfo) return null

  return (
    <div className="space-y-6">
      {/* Owners */}
      <TerminalCard label="SIGNERS">
        <div className="space-y-2">
          {safeInfo.owners.map((owner, i) => {
            const isMe = owner.toLowerCase() === walletAddress.toLowerCase()
            return (
              <div key={i} className="flex items-center gap-3 group" style={{ fontSize: '14px' }}>
                <span style={{ fontFamily: TERM_FONT, color: 'var(--text-tertiary)', width: '20px' }}>{i}</span>
                <span
                  className="flex-1 break-all"
                  style={{
                    fontFamily: TERM_FONT,
                    color: isMe ? 'var(--accent-primary, #10b981)' : 'var(--text-primary)',
                    textShadow: isMe ? '0 0 8px var(--accent-primary, #10b981)' : 'none',
                  }}
                >
                  {owner}
                </span>
                <CopyButton text={owner} size="sm" />
                {isMe && <span style={{ fontFamily: TERM_FONT, fontSize: '12px', color: 'var(--accent-primary, #10b981)', opacity: 0.6 }}>you</span>}
              </div>
            )
          })}
        </div>
      </TerminalCard>

      {/* Stats row */}
      <div className="grid grid-cols-3 gap-4">
        <TerminalCard label="THRESHOLD">
          <div style={{ fontFamily: TERM_FONT, fontSize: '24px', color: 'var(--accent-primary, #10b981)', textShadow: '0 0 10px var(--accent-primary, #10b981)' }}>
            {safeInfo.threshold}<span style={{ color: 'var(--text-tertiary)', fontSize: '18px' }}>/{safeInfo.owners.length}</span>
          </div>
          <div style={{ fontFamily: TERM_FONT, fontSize: '12px', color: 'var(--text-tertiary)', marginTop: '4px' }}>confirmations</div>
        </TerminalCard>
        <TerminalCard label="NONCE">
          <div style={{ fontFamily: TERM_FONT, fontSize: '24px', color: 'var(--accent-primary, #10b981)', textShadow: '0 0 10px var(--accent-primary, #10b981)' }}>
            {safeInfo.nonce}
          </div>
          <div style={{ fontFamily: TERM_FONT, fontSize: '12px', color: 'var(--text-tertiary)', marginTop: '4px' }}>executed</div>
        </TerminalCard>
        <TerminalCard label="BALANCE">
          <div style={{ fontFamily: TERM_FONT, fontSize: '24px', color: 'var(--accent-primary, #10b981)', textShadow: '0 0 10px var(--accent-primary, #10b981)' }}>
            {parseFloat(ethBalance).toFixed(4)}
          </div>
          <div style={{ fontFamily: TERM_FONT, fontSize: '12px', color: 'var(--text-tertiary)', marginTop: '4px' }}>ETH</div>
        </TerminalCard>
      </div>

      {/* Portfolio */}
      <TerminalCard label="PORTFOLIO">
        <div className="flex items-center justify-between mb-4">
          <span style={{ fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-tertiary)' }}>token balances</span>
          <button
            onClick={fetchTokenBalances}
            disabled={loadingBalances}
            className="transition-all"
            style={{
              fontFamily: TERM_FONT,
              fontSize: '12px',
              padding: '3px 10px',
              color: 'var(--text-tertiary)',
              border: '1px solid var(--border-color)',
              background: 'transparent',
              cursor: loadingBalances ? 'not-allowed' : 'pointer',
            }}
          >
            {loadingBalances ? '...' : 'refresh'}
          </button>
        </div>

        {loadingBalances && tokenBalances.length === 0 ? (
          <div className="py-6 text-center" style={{ fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-tertiary)' }}>scanning tokens...</div>
        ) : (
          <div className="space-y-1">
            {/* ETH */}
            <div className="flex items-center justify-between py-2" style={{ borderBottom: '1px solid var(--border-color)', fontSize: '14px' }}>
              <span style={{ fontFamily: TERM_FONT, color: 'var(--text-secondary)' }}>ETH</span>
              <span style={{ fontFamily: TERM_FONT, color: 'var(--accent-primary, #10b981)' }}>{parseFloat(ethBalance).toFixed(4)}</span>
            </div>
            {tokenBalances.map((token) => (
              <div key={token.address} className="flex items-center justify-between py-2" style={{ borderBottom: '1px solid var(--border-color)', fontSize: '14px' }}>
                <div className="flex items-center gap-2">
                  <span style={{ fontFamily: TERM_FONT, color: 'var(--text-secondary)' }}>{token.name === 'Market' ? 'MARKET' : token.symbol}</span>
                  <CopyButton text={token.address} size="sm" />
                </div>
                <span style={{ fontFamily: TERM_FONT, color: 'var(--accent-primary, #10b981)' }}>
                  {token.name === 'NativeToken' ? token.balance.toFixed(4) : `$${token.balance.toFixed(2)}`}
                </span>
              </div>
            ))}
            {tokenBalances.length === 0 && !loadingBalances && (
              <div className="py-4 text-center" style={{ fontFamily: TERM_FONT, fontSize: '13px', color: 'var(--text-tertiary)', opacity: 0.5 }}>no tokens</div>
            )}
          </div>
        )}
      </TerminalCard>

      {/* Address */}
      <TerminalCard label="CONTRACT">
        <div className="flex items-center gap-3" style={{ fontSize: '14px' }}>
          <span style={{ fontFamily: TERM_FONT, color: 'var(--text-tertiary)' }}>address:</span>
          <span className="break-all" style={{ fontFamily: TERM_FONT, color: 'var(--text-primary)' }}>{safeInfo.address}</span>
          <CopyButton text={safeInfo.address} size="sm" />
        </div>
      </TerminalCard>
    </div>
  )
}
