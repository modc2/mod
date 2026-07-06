"use client";

import { ArrowPathIcon, ArrowsRightLeftIcon, PlusCircleIcon, XMarkIcon } from '@heroicons/react/24/outline'
import { motion, AnimatePresence } from 'framer-motion'
import { CopyButton } from '@/ui/CopyButton'
import { getContracts } from '@/network/chainConfig'

type TransferTokenType = 'MARKET' | 'USDC' | 'USDT'

interface CustomToken {
  address: string
  symbol: string
  decimals: number
}

interface PortfolioTabProps {
  show: boolean
  tokenBalances: Record<string, number>
  customTokens: CustomToken[]
  customTokenBalances: Record<string, number>
  marketCredit: number
  sendFromPortfolio: string | null
  setSendFromPortfolio: (v: string | null) => void
  transferRecipient: string
  setTransferRecipient: (v: string) => void
  transferAmount: string
  setTransferAmount: (v: string) => void
  isTransferring: boolean
  setTransferTokenType: (v: TransferTokenType) => void
  topUpError: string | null
  setTopUpError: (v: string | null) => void
  topUpSuccess: string | null
  setTopUpSuccess: (v: string | null) => void
  showAddToken: boolean
  setShowAddToken: (v: boolean) => void
  newTokenAddress: string
  setNewTokenAddress: (v: string) => void
  isAddingToken: boolean
  isRefreshing: boolean
  onRefreshBalances: () => void
  onTransfer: () => void
  onSendETH: () => void
  onSendCustomToken: (addr: string, symbol: string, decimals: number) => void
  onRemoveCustomToken: (addr: string) => void
  onAddCustomToken: () => void
}

const TOKEN_THEME: Record<string, { color: string; icon: string }> = {
  ETH: { color: '#627eea', icon: 'E' },
  MARKET: { color: '#a855f7', icon: 'M' },
  USDC: { color: '#2775ca', icon: '$' },
  USDT: { color: '#26a17b', icon: '$' },
  NativeToken: { color: '#f59e0b', icon: 'N' },
  BridgeToken: { color: '#06b6d4', icon: 'B' },
}

function getTokenTheme(symbol: string) {
  return TOKEN_THEME[symbol] || { color: '#8b5cf6', icon: symbol[0]?.toUpperCase() || '?' }
}

export function PortfolioTab({
  show, tokenBalances, customTokens, customTokenBalances, marketCredit,
  sendFromPortfolio, setSendFromPortfolio,
  transferRecipient, setTransferRecipient,
  transferAmount, setTransferAmount,
  isTransferring, setTransferTokenType,
  topUpError, setTopUpError, topUpSuccess, setTopUpSuccess,
  showAddToken, setShowAddToken, newTokenAddress, setNewTokenAddress,
  isAddingToken, isRefreshing,
  onRefreshBalances, onTransfer, onSendETH, onSendCustomToken,
  onRemoveCustomToken, onAddCustomToken
}: PortfolioTabProps) {
  const tokenAddressMap: Record<string, string | undefined> = {
    USDC: getContracts()?.USDC?.address,
    USDT: getContracts()?.USDT?.address,
    MARKET: getContracts()?.Market?.address,
    NativeToken: getContracts()?.NativeToken?.address,
    BridgeToken: getContracts()?.BridgeToken?.address,
  }

  const allTokens: { key: string; symbol: string; balance: number; address?: string; decimals?: number; isCustom?: boolean; isETH?: boolean }[] = []

  for (const [token, balance] of Object.entries(tokenBalances)) {
    let decimals = 18
    allTokens.push({
      key: token,
      symbol: token,
      balance: Number(balance) || 0,
      address: tokenAddressMap[token],
      decimals: (token === 'NativeToken' || token === 'BridgeToken') ? decimals : undefined,
      isETH: token === 'ETH',
    })
  }

  for (const ct of customTokens) {
    if (!allTokens.some(t => t.address?.toLowerCase() === ct.address.toLowerCase())) {
      allTokens.push({
        key: `custom-${ct.address}`,
        symbol: ct.symbol,
        balance: customTokenBalances[ct.symbol] || 0,
        address: ct.address,
        decimals: ct.decimals,
        isCustom: true,
      })
    }
  }

  // Compute total USD value (ETH excluded from dollar sum, stablecoins are 1:1)
  const totalValue = allTokens.reduce((sum, t) => {
    if (t.isETH) return sum
    if (t.symbol === 'USDC' || t.symbol === 'USDT' || t.symbol === 'MARKET') return sum + t.balance
    return sum
  }, 0)

  return (
    <AnimatePresence>
      {show && (
        <motion.div
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: 'auto', opacity: 1 }}
          exit={{ height: 0, opacity: 0 }}
          transition={{ duration: 0.35, ease: [0.25, 0.1, 0.25, 1] }}
          className="mt-4 overflow-hidden"
        >
          <div className="space-y-2">
            {/* Header */}
            <div className="flex items-center justify-between px-1 mb-3">
              <div className="flex items-center gap-2.5">
                <span className="text-xs uppercase tracking-widest font-bold" style={{
                  fontFamily: 'var(--font-digital)',
                  color: 'var(--text-tertiary)',
                  letterSpacing: '0.15em',
                  fontSize: '10px',
                }}>
                  Portfolio
                </span>
                <span className="text-xs font-bold tabular-nums" style={{
                  fontFamily: 'var(--font-digital)',
                  color: totalValue > 0 ? 'var(--text-primary)' : 'var(--text-tertiary)',
                  fontSize: '10px',
                }}>
                  ${totalValue.toFixed(2)}
                </span>
              </div>
              <div className="flex items-center gap-0.5">
                <button
                  onClick={() => { setShowAddToken(!showAddToken); setSendFromPortfolio(null) }}
                  className="p-1.5 rounded-md transition-all hover:bg-white/5"
                  title="Add custom token"
                >
                  <PlusCircleIcon className="w-3.5 h-3.5" style={{ color: showAddToken ? '#a855f7' : 'var(--text-tertiary)' }} />
                </button>
                <button
                  onClick={onRefreshBalances}
                  disabled={isRefreshing}
                  className="p-1.5 rounded-md transition-all hover:bg-white/5 disabled:opacity-50"
                  title="Refresh balances"
                >
                  <ArrowPathIcon className={`w-3.5 h-3.5 ${isRefreshing ? 'animate-spin' : ''}`} style={{ color: 'var(--text-tertiary)' }} />
                </button>
              </div>
            </div>

            {/* Add Custom Token Form */}
            <AnimatePresence>
              {showAddToken && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: 'auto', opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.15 }}
                  className="overflow-hidden"
                >
                  <div className="flex gap-1.5 mb-2">
                    <input
                      type="text"
                      value={newTokenAddress}
                      onChange={(e) => setNewTokenAddress(e.target.value)}
                      placeholder="Token address 0x..."
                      className="flex-1 px-3 py-2 text-xs font-mono placeholder-neutral-600 focus:outline-none transition-colors"
                      style={{ borderRadius: '8px', fontFamily: 'var(--font-digital)', backgroundColor: 'var(--bg-input)', border: '1px solid var(--border-color)', color: 'var(--text-secondary)' }}
                    />
                    <button
                      onClick={onAddCustomToken}
                      disabled={isAddingToken}
                      className="px-3 py-2 text-xs font-bold transition-all disabled:opacity-50"
                      style={{ borderRadius: '8px', fontFamily: 'var(--font-digital)', backgroundColor: '#a855f715', border: '1px solid #a855f730', color: '#a855f7' }}
                    >
                      {isAddingToken ? <ArrowPathIcon className="w-3.5 h-3.5 animate-spin" /> : 'ADD'}
                    </button>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {allTokens.length === 0 ? (
              <div className="text-center py-6 text-xs" style={{ color: 'var(--text-tertiary)' }}>
                {isRefreshing ? (
                  <div className="flex items-center justify-center gap-2">
                    <ArrowPathIcon className="w-4 h-4 animate-spin" />
                    <span>Loading balances...</span>
                  </div>
                ) : (
                  'No token balances available'
                )}
              </div>
            ) : (
              <div className="space-y-1">
                {allTokens.map((token, idx) => {
                  const isSelected = sendFromPortfolio === token.key
                  const theme = getTokenTheme(token.symbol)
                  const displaySymbol = token.symbol === 'BridgeToken' ? 'BRIDGE' : token.symbol === 'NativeToken' ? 'NATIVE' : token.symbol

                  return (
                    <motion.div
                      key={token.key}
                      initial={{ opacity: 0, y: 6 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: idx * 0.03, duration: 0.2 }}
                    >
                      <button
                        onClick={() => {
                          setSendFromPortfolio(isSelected ? null : token.key)
                          setTransferRecipient('')
                          setTransferAmount('')
                          setTopUpError(null)
                          setTopUpSuccess(null)
                        }}
                        className="w-full flex items-center gap-3 px-3 py-2.5 transition-all group"
                        style={{
                          borderRadius: isSelected ? '10px 10px 0 0' : '10px',
                          backgroundColor: isSelected ? `${theme.color}12` : 'transparent',
                          border: isSelected ? `1px solid ${theme.color}30` : '1px solid transparent',
                          borderBottom: isSelected ? 'none' : undefined,
                        }}
                      >
                        {/* Token Icon */}
                        <div
                          className="w-8 h-8 rounded-lg flex items-center justify-center text-xs font-black flex-shrink-0"
                          style={{
                            background: `${theme.color}20`,
                            color: theme.color,
                            fontFamily: 'var(--font-digital)',
                            border: `1px solid ${theme.color}25`,
                          }}
                        >
                          {theme.icon}
                        </div>

                        {/* Token Info */}
                        <div className="flex-1 flex items-center justify-between min-w-0">
                          <div className="flex items-center gap-1.5">
                            <span className="text-xs font-bold uppercase tracking-wider" style={{
                              color: 'var(--text-primary)',
                              fontFamily: 'var(--font-digital)',
                              fontSize: '11px',
                            }}>
                              {displaySymbol}
                            </span>
                            {token.address && (
                              <span onClick={(e) => e.stopPropagation()}>
                                <CopyButton text={token.address} size="sm" />
                              </span>
                            )}
                            {token.isCustom && (
                              <button
                                onClick={(e) => { e.stopPropagation(); onRemoveCustomToken(token.address!) }}
                                className="opacity-0 group-hover:opacity-100 transition-opacity"
                                title="Remove token"
                              >
                                <XMarkIcon className="w-3 h-3 text-red-500/50 hover:text-red-400" />
                              </button>
                            )}
                          </div>

                          <span className="text-xs font-bold tabular-nums" style={{
                            color: token.balance > 0 ? theme.color : 'var(--text-tertiary)',
                            fontFamily: 'var(--font-digital)',
                            fontSize: '11px',
                          }}>
                            {token.isETH
                              ? `${token.balance.toFixed(4)} ETH`
                              : token.symbol === 'BridgeToken'
                              ? `${token.balance.toFixed(4)} BT`
                              : token.isCustom || token.symbol === 'NativeToken'
                              ? `${token.balance.toFixed(4)} ${token.symbol}`
                              : `$${token.balance.toFixed(2)}`}
                          </span>
                        </div>
                      </button>

                      {/* Inline Send Form */}
                      <AnimatePresence>
                        {isSelected && (
                          <motion.div
                            initial={{ height: 0, opacity: 0 }}
                            animate={{ height: 'auto', opacity: 1 }}
                            exit={{ height: 0, opacity: 0 }}
                            transition={{ duration: 0.15 }}
                            className="overflow-hidden px-3 pb-3 pt-2 space-y-2"
                            style={{
                              borderRadius: '0 0 10px 10px',
                              fontFamily: 'var(--font-digital)',
                              backgroundColor: `${theme.color}08`,
                              border: `1px solid ${theme.color}30`,
                              borderTop: 'none',
                            }}
                          >
                            <input
                              type="text"
                              value={transferRecipient}
                              onChange={(e) => setTransferRecipient(e.target.value)}
                              disabled={isTransferring}
                              placeholder="Recipient address..."
                              className="w-full px-3 py-2 text-xs focus:outline-none transition-colors"
                              style={{
                                borderRadius: '8px',
                                backgroundColor: 'var(--bg-primary)',
                                border: '1px solid var(--border-color)',
                                color: 'var(--text-primary)',
                                fontFamily: 'var(--font-digital)',
                              }}
                            />
                            <input
                              type="number"
                              value={transferAmount}
                              onChange={(e) => setTransferAmount(e.target.value)}
                              disabled={isTransferring}
                              min="0"
                              step="0.01"
                              placeholder="Amount..."
                              className="w-full px-3 py-2 text-xs focus:outline-none transition-colors"
                              style={{
                                borderRadius: '8px',
                                backgroundColor: 'var(--bg-primary)',
                                border: '1px solid var(--border-color)',
                                color: 'var(--text-primary)',
                                fontFamily: 'var(--font-digital)',
                              }}
                            />
                            <button
                              onClick={() => {
                                if (token.isETH) {
                                  onSendETH()
                                } else if (token.symbol === 'BridgeToken' && token.address && token.decimals != null) {
                                  onSendCustomToken(token.address, token.symbol, token.decimals)
                                } else if (token.isCustom && token.address && token.decimals != null) {
                                  onSendCustomToken(token.address, token.symbol, token.decimals)
                                } else if (token.symbol === 'NativeToken' && token.address && token.decimals != null) {
                                  onSendCustomToken(token.address, token.symbol, token.decimals)
                                } else if (token.symbol === 'MARKET') {
                                  setTransferTokenType('MARKET')
                                  onTransfer()
                                } else if (token.symbol === 'USDC') {
                                  setTransferTokenType('USDC')
                                  onTransfer()
                                } else if (token.symbol === 'USDT') {
                                  setTransferTokenType('USDT')
                                  onTransfer()
                                }
                              }}
                              disabled={!transferAmount || !transferRecipient || isTransferring}
                              className="w-full py-2 text-xs font-bold uppercase transition-all disabled:opacity-30 flex items-center justify-center gap-2"
                              style={{
                                borderRadius: '8px',
                                fontFamily: 'var(--font-digital)',
                                backgroundColor: theme.color,
                                color: '#fff',
                                letterSpacing: '0.05em',
                              }}
                            >
                              {isTransferring ? (
                                <ArrowPathIcon className="w-3.5 h-3.5 animate-spin" />
                              ) : (
                                <>
                                  <ArrowsRightLeftIcon className="w-3.5 h-3.5" />
                                  SEND {displaySymbol}
                                </>
                              )}
                            </button>

                            {topUpError && (
                              <div className="text-[10px] text-red-400 bg-red-500/10 border border-red-500/20 px-2 py-1.5 font-mono" style={{ borderRadius: '6px' }}>
                                {topUpError}
                              </div>
                            )}
                            {topUpSuccess && (
                              <div className="text-[10px] text-green-400 bg-green-500/10 border border-green-500/20 px-2 py-1.5 font-mono" style={{ borderRadius: '6px' }}>
                                {topUpSuccess}
                              </div>
                            )}
                          </motion.div>
                        )}
                      </AnimatePresence>
                    </motion.div>
                  )
                })}
              </div>
            )}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
