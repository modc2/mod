"use client";

import { useState, useEffect } from 'react'
import { userContext } from '@/context'
import { DollarSign, CreditCard, AlertCircle, CheckCircle, Zap, RefreshCw, ArrowRightLeft } from 'lucide-react'
import { ArrowDownTrayIcon } from '@heroicons/react/24/outline'
import { MarketAllowanceManager } from '@/network/marketAllowance'
import { Market } from '@/network/Market'
import { getChainConfig } from '@/network/chainConfig'
import { ethers } from 'ethers'
import MarketABI from '@/contracts//market/Market.sol/Market.json'
import WithdrawalPanel from './WithdrawalPanel'

type TokenType = 'USDC' | 'USDT'
type TabType = 'add' | 'withdraw' | 'transfer'

export const Billing: React.FC = () => {
  const { user, client } = userContext()
  const [marketCredit, setMarketCredit] = useState<number>(0)
  const [addAmount, setAddAmount] = useState<string>('')
  const [isProcessing, setIsProcessing] = useState(false)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [billingError, setBillingError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [allowance, setAllowance] = useState<number>(0)
  const [needsApproval, setNeedsApproval] = useState(false)
  const [allowanceManager, setAllowanceManager] = useState<MarketAllowanceManager | null>(null)
  const [market, setMarket] = useState<Market | null>(null)
  const [selectedToken, setSelectedToken] = useState<TokenType>('USDC')
  const [useClientAddress, setUseClientAddress] = useState(false)
  const [transferAddress, setTransferAddress] = useState<string>('')
  const [activeTab, setActiveTab] = useState<TabType>('add')
  const [transferRecipient, setTransferRecipient] = useState<string>('')
  const [transferAmount, setTransferAmount] = useState<string>('')
  const [isTransferring, setIsTransferring] = useState(false)

  useEffect(() => {
    const chainConfig = getChainConfig()
    if (chainConfig) {
      setAllowanceManager(new MarketAllowanceManager(chainConfig))
      setMarket(new Market(chainConfig))
    }
  }, [])

  useEffect(() => {
    if (useClientAddress && client?.key?.address) {
      setTransferAddress(client.key.address)
    } else if (!useClientAddress) {
      setTransferAddress('')
    }
  }, [useClientAddress, client?.key?.address])

  const fetchCredit = async () => {
    if (!user?.key || !market || !allowanceManager) return

    try {
      setIsRefreshing(true)
      const balance = await market.checkMarketBalance(user.key)
      setMarketCredit(balance)
      
      const currentAllowance = await allowanceManager.checkMarketAllowance(user.key, selectedToken)
      setAllowance(currentAllowance)
    } catch (err) {
      console.error('Error fetching market credit:', err)
      setMarketCredit(0)
      setAllowance(0)
    } finally {
      setIsRefreshing(false)
    }
  }

  useEffect(() => {
    fetchCredit()
  }, [user, allowanceManager, market, selectedToken])

  const handleRefresh = async () => {
    await fetchCredit()
  }

  const handleCombinedTransaction = async () => {
    if (!addAmount || !allowanceManager || !user?.key || !market) {
      setBillingError('Please enter an amount and ensure wallet is connected')
      return
    }

    const amount = parseFloat(addAmount)
    if (amount <= 0) {
      setBillingError('Amount must be greater than 0')
      return
    }

    setIsProcessing(true)
    setBillingError(null)
    setSuccess(null)

    try {
      await allowanceManager.increaseMarketAllowance(user.key, amount, selectedToken)
      
      const targetAddress = useClientAddress && transferAddress ? transferAddress : user.key
      
      if (useClientAddress && !ethers.isAddress(transferAddress)) {
        throw new Error('Invalid client address')
      }

      await allowanceManager.addMarketCredit(targetAddress, amount, selectedToken)
      
      await fetchCredit()
      
      const successMsg = useClientAddress 
        ? `Successfully added $${amount.toFixed(2)} credit using ${selectedToken} to client address ${transferAddress.slice(0, 8)}...${transferAddress.slice(-6)}!`
        : `Successfully added $${amount.toFixed(2)} credit using ${selectedToken} to your account!`
      
      setSuccess(successMsg)
      setAddAmount('')
    } catch (err: any) {
      let msg = err?.message || String(err)
      if (msg.includes('1010')) msg = 'Insufficient balance for transaction.'
      else if (msg.toLowerCase().includes('cancel')) msg = 'Transaction cancelled by user.'
      setBillingError(msg)
    } finally {
      setIsProcessing(false)
    }
  }

  const handleTransfer = async () => {
    if (!transferAmount || !transferRecipient || !market || !user?.key) {
      setBillingError('Please enter recipient address and amount')
      return
    }

    if (!ethers.isAddress(transferRecipient)) {
      setBillingError('Invalid recipient address')
      return
    }

    const amount = parseFloat(transferAmount)
    if (amount <= 0) {
      setBillingError('Amount must be greater than 0')
      return
    }

    if (amount > marketCredit) {
      setBillingError('Insufficient balance')
      return
    }

    setIsTransferring(true)
    setBillingError(null)
    setSuccess(null)

    try {
      await market.transferMarketCredit(user!.key, transferRecipient, amount)

      await fetchCredit()

      setSuccess(`Successfully transferred $${amount.toFixed(2)} to ${transferRecipient.slice(0, 8)}...${transferRecipient.slice(-6)}!`)
      setTransferAmount('')
      setTransferRecipient('')
    } catch (err: any) {
      let msg = err?.message || String(err)
      if (msg.toLowerCase().includes('cancel')) msg = 'Transaction cancelled by user.'
      setBillingError(msg)
    } finally {
      setIsTransferring(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex gap-4 mb-6">
        <button
          onClick={() => setActiveTab('add')}
          className={`px-6 py-3 rounded-lg font-bold uppercase transition-all ${
            activeTab === 'add'
              ? 'bg-green-500/20 border-2 border-green-500 text-green-400'
              : 'bg-black/40 border-2 border-white/20 text-white/60 hover:border-white/40'
          }`}
        >
          <DollarSign className="inline mr-2" size={20} />
          Add Credit
        </button>
        <button
          onClick={() => setActiveTab('withdraw')}
          className={`px-6 py-3 rounded-lg font-bold uppercase transition-all ${
            activeTab === 'withdraw'
              ? 'bg-purple-500/20 border-2 border-purple-500 text-purple-400'
              : 'bg-black/40 border-2 border-white/20 text-white/60 hover:border-white/40'
          }`}
        >
          <ArrowDownTrayIcon className="inline mr-2 w-5 h-5" />
          Withdraw
        </button>
        <button
          onClick={() => setActiveTab('transfer')}
          className={`px-6 py-3 rounded-lg font-bold uppercase transition-all ${
            activeTab === 'transfer'
              ? 'bg-blue-500/20 border-2 border-blue-500 text-blue-400'
              : 'bg-black/40 border-2 border-white/20 text-white/60 hover:border-white/40'
          }`}
        >
          <ArrowRightLeft className="inline mr-2" size={20} />
          Transfer
        </button>
      </div>

      {activeTab === 'withdraw' ? (
        <WithdrawalPanel />
      ) : activeTab === 'transfer' ? (
        <>
          <div className="p-6 bg-gradient-to-br from-yellow-500/10 via-orange-500/10 to-red-500/10 border-2 border-yellow-500/50 rounded-xl shadow-2xl">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-yellow-500/70 font-bold text-sm uppercase mb-2">Current Balance</div>
                <div className="text-yellow-500 font-black text-5xl">${marketCredit.toFixed(2)}</div>
              </div>
              <div className="flex items-center gap-4">
                <button
                  onClick={handleRefresh}
                  disabled={isRefreshing}
                  className="p-3 bg-yellow-500/20 border-2 border-yellow-500/50 rounded-lg hover:bg-yellow-500/30 hover:border-yellow-500 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                  title="Refresh Balance"
                >
                  <RefreshCw size={24} className={`text-yellow-500 ${isRefreshing ? 'animate-spin' : ''}`} />
                </button>
                <ArrowRightLeft size={64} className="text-yellow-500/30" />
              </div>
            </div>
          </div>

          <div className="p-6 bg-gradient-to-br from-blue-500/10 via-cyan-500/10 to-teal-500/10 border-2 border-blue-500/50 rounded-xl shadow-2xl">
            <h2 className="text-2xl font-black text-blue-500 uppercase mb-6 flex items-center gap-2">
              <ArrowRightLeft size={24} />
              Transfer Credit
            </h2>

            <div className="space-y-4">
              <div>
                <label className="text-blue-500 font-bold text-sm uppercase mb-2 block">
                  Recipient Address
                </label>
                <input
                  type="text"
                  value={transferRecipient}
                  onChange={(e) => setTransferRecipient(e.target.value)}
                  disabled={isTransferring}
                  placeholder="0x..."
                  className="w-full bg-black/60 border-2 border-blue-500/40 rounded-lg px-4 py-3 text-blue-300 font-mono text-sm placeholder-blue-600/50 focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 disabled:opacity-50 transition-all"
                />
                {transferRecipient && !ethers.isAddress(transferRecipient) && (
                  <div className="mt-2 text-xs text-red-400 font-mono">
                    ✗ Invalid Ethereum address
                  </div>
                )}
                {transferRecipient && ethers.isAddress(transferRecipient) && (
                  <div className="mt-2 text-xs text-emerald-400 font-mono">
                    ✓ Valid address
                  </div>
                )}
              </div>

              <div>
                <label className="text-blue-500 font-bold text-sm uppercase mb-2 block">
                  Amount (USD)
                </label>
                <input
                  type="number"
                  value={transferAmount}
                  onChange={(e) => setTransferAmount(e.target.value)}
                  disabled={isTransferring}
                  min="0"
                  step="0.01"
                  placeholder="10.00"
                  className="w-full bg-black/60 border-2 border-blue-500/40 rounded-lg px-4 py-3 text-blue-300 font-mono text-lg placeholder-blue-600/50 focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 disabled:opacity-50 transition-all"
                />
                {transferAmount && parseFloat(transferAmount) > marketCredit && (
                  <div className="mt-2 text-xs text-red-400 font-mono">
                    ✗ Amount exceeds your balance (${marketCredit.toFixed(2)})
                  </div>
                )}
                {transferAmount && parseFloat(transferAmount) > 0 && parseFloat(transferAmount) <= marketCredit && (
                  <div className="mt-2 text-xs text-emerald-400 font-mono">
                    ✓ Valid amount
                  </div>
                )}
              </div>

              <button
                onClick={handleTransfer}
                disabled={!transferAmount || !transferRecipient || isTransferring || parseFloat(transferAmount) > marketCredit || !ethers.isAddress(transferRecipient) || parseFloat(transferAmount) <= 0}
                className="w-full py-4 border-2 border-blue-500/60 bg-gradient-to-r from-blue-500/20 to-cyan-500/20 text-blue-400 hover:bg-blue-500/30 hover:border-blue-500 hover:scale-[1.02] transition-all duration-300 rounded-xl font-mono uppercase font-black text-lg disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100 flex items-center justify-center gap-3 shadow-lg"
              >
                {isTransferring ? (
                  <>
                    <ArrowRightLeft size={20} className="animate-spin" />
                    <span>TRANSFERRING...</span>
                  </>
                ) : (
                  <>
                    <ArrowRightLeft size={20} />
                    <span>TRANSFER CREDIT</span>
                  </>
                )}
              </button>

              <div className="text-blue-500/60 text-sm space-y-2">
                <p><strong>Transfer Credit:</strong> Send your market credit to another address.</p>
                <p>• The recipient will receive the credit instantly on-chain</p>
                <p>• This will prompt you once in MetaMask to confirm the transfer</p>
                <p>• Make sure to verify the recipient address before confirming</p>
              </div>
            </div>
          </div>

          {(success || billingError) && (
            <div
              className={`p-6 rounded-xl border-2 shadow-2xl ${
                billingError
                  ? 'from-red-500/10 border-red-500/40 bg-gradient-to-br'
                  : 'from-emerald-500/10 border-emerald-500/40 bg-gradient-to-br'
              }`}
            >
              <div className="flex items-center gap-3 text-base font-mono uppercase font-bold mb-4">
                {billingError ? (
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
                billingError ? 'text-red-400 border-red-500/30' : 'text-emerald-400 border-emerald-500/30'
              } whitespace-pre-wrap font-bold`}>
                {billingError || success}
              </div>
            </div>
          )}
        </>
      ) : (
        <>
          <div className="p-6 bg-gradient-to-br from-yellow-500/10 via-orange-500/10 to-red-500/10 border-2 border-yellow-500/50 rounded-xl shadow-2xl">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-yellow-500/70 font-bold text-sm uppercase mb-2">Current Balance</div>
                <div className="text-yellow-500 font-black text-5xl">${marketCredit.toFixed(2)}</div>
              </div>
              <div className="flex items-center gap-4">
                <button
                  onClick={handleRefresh}
                  disabled={isRefreshing}
                  className="p-3 bg-yellow-500/20 border-2 border-yellow-500/50 rounded-lg hover:bg-yellow-500/30 hover:border-yellow-500 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                  title="Refresh Balance"
                >
                  <RefreshCw size={24} className={`text-yellow-500 ${isRefreshing ? 'animate-spin' : ''}`} />
                </button>
                <CreditCard size={64} className="text-yellow-500/30" />
              </div>
            </div>
          </div>

          <div className="p-6 bg-gradient-to-br from-green-500/10 via-emerald-500/10 to-teal-500/10 border-2 border-green-500/50 rounded-xl shadow-2xl">
            <h2 className="text-2xl font-black text-green-500 uppercase mb-6 flex items-center gap-2">
              <Zap size={24} />
              Add Credit
            </h2>

            <div className="space-y-4">
              <div>
                <label className="text-green-500 font-bold text-sm uppercase mb-2 block">
                  Select Token
                </label>
                <select
                  value={selectedToken}
                  onChange={(e) => setSelectedToken(e.target.value as TokenType)}
                  className="w-full bg-black/60 border-2 border-green-500/40 rounded-lg px-4 py-3 text-green-300 font-mono text-lg focus:outline-none focus:border-green-500 focus:ring-2 focus:ring-green-500/30 transition-all"
                >
                  <option value="USDC">USDC</option>
                  <option value="USDT">USDT</option>
                </select>
              </div>

              <div className="flex items-center gap-3 p-3 bg-black/40 border-2 border-cyan-500/30 rounded-lg">
                <input
                  type="checkbox"
                  checked={useClientAddress}
                  onChange={(e) => setUseClientAddress(e.target.checked)}
                  className="w-5 h-5 accent-cyan-500"
                />
                <label className="text-sm text-cyan-400 font-mono uppercase font-bold tracking-wide">
                  Transfer to Client Address
                </label>
              </div>

              {useClientAddress && client?.key?.address && (
                <div className="p-3 bg-cyan-500/10 border-2 border-cyan-500/30 rounded-lg">
                  <div className="text-xs text-cyan-400 font-mono uppercase mb-1">Client Key Address:</div>
                  <div className="text-sm text-cyan-300 font-mono font-bold break-all">{client.key.address}</div>
                </div>
              )}

              <div>
                <label className="text-green-500 font-bold text-sm uppercase mb-2 block">
                  Amount (USD)
                </label>
                <input
                  type="number"
                  value={addAmount}
                  onChange={(e) => setAddAmount(e.target.value)}
                  disabled={isProcessing}
                  min="0"
                  step="0.01"
                  placeholder="100.00"
                  className="w-full bg-black/60 border-2 border-green-500/40 rounded-lg px-4 py-3 text-green-300 font-mono text-lg placeholder-green-600/50 focus:outline-none focus:border-green-500 focus:ring-2 focus:ring-green-500/30 disabled:opacity-50 transition-all"
                />
              </div>

              <button
                onClick={handleCombinedTransaction}
                disabled={!addAmount || isProcessing}
                className="w-full py-4 border-2 border-green-500/60 bg-gradient-to-r from-green-500/20 to-emerald-500/20 text-green-400 hover:bg-green-500/30 hover:border-green-500 hover:scale-[1.02] transition-all duration-300 rounded-xl font-mono uppercase font-black text-lg disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100 flex items-center justify-center gap-3 shadow-lg"
              >
                {isProcessing ? (
                  <>
                    <Zap size={20} className="animate-spin" />
                    <span>PROCESSING...</span>
                  </>
                ) : (
                  <>
                    <DollarSign size={20} />
                    <span>APPROVE & ADD CREDIT</span>
                  </>
                )}
              </button>

              <div className="text-green-500/60 text-sm space-y-2">
                <p><strong>Combined Transaction:</strong> This will prompt you twice in MetaMask:</p>
                <p>1. First approval to authorize the market contract to spend {selectedToken} tokens</p>
                <p>2. Second transaction to add credit to {useClientAddress ? 'your client address' : 'your market account'}</p>
              </div>
            </div>
          </div>

          {(success || billingError) && (
            <div
              className={`p-6 rounded-xl border-2 shadow-2xl ${
                billingError
                  ? 'from-red-500/10 border-red-500/40 bg-gradient-to-br'
                  : 'from-emerald-500/10 border-emerald-500/40 bg-gradient-to-br'
              }`}
            >
              <div className="flex items-center gap-3 text-base font-mono uppercase font-bold mb-4">
                {billingError ? (
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
                billingError ? 'text-red-400 border-red-500/30' : 'text-emerald-400 border-emerald-500/30'
              } whitespace-pre-wrap font-bold`}>
                {billingError || success}
              </div>
            </div>
          )}

          <div className="p-6 bg-black/40 border-2 border-white/20 rounded-xl">
            <h3 className="text-xl font-black text-white/70 uppercase mb-4">How It Works</h3>
            <div className="space-y-3 text-white/60 text-sm">
              <p>• Your market credit is stored on-chain and can be used for purchasing modules and services</p>
              <p>• Select between USDC and USDT as payment tokens from the dropdown menu</p>
              <p>• Click "APPROVE & ADD CREDIT" to execute both transactions in sequence</p>
              <p>• You will be prompted twice in MetaMask: first for approval, then for adding credit</p>
              <p>• Enable "Transfer to Client Address" to add credit directly to your client address</p>
              <p>• All transactions are secured by smart contracts on the blockchain</p>
              <p>• Press the refresh button to update your balance at any time</p>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

export default Billing