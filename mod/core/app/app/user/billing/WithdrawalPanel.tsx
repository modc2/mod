import { CheckCircle, RefreshCw, Zap, Download as ArrowDownTrayIcon } from 'lucide-react'

import { useState, useEffect } from 'react'
import { userContext } from '@/context'
import { AlertCircle } from 'lucide-react'
import { Market } from '@/network/Market'
import { getChainConfig } from '@/network/chainConfig'
import { ethers } from 'ethers'

type TokenType = 'USDC' | 'USDT'

interface Deposit {
  amount: string
  timestamp: number
  unlocked: boolean
}

export const WithdrawalPanel: React.FC = () => {
  const { user } = userContext()
  const [selectedToken, setSelectedToken] = useState<TokenType>('USDC')
  const [withdrawAmount, setWithdrawAmount] = useState<string>('')
  const [isProcessing, setIsProcessing] = useState(false)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [withdrawableAmount, setWithdrawableAmount] = useState<number>(0)
  const [totalBalance, setTotalBalance] = useState<number>(0)
  const [deposits, setDeposits] = useState<Deposit[]>([])
  const [market, setMarket] = useState<Market | null>(null)
  const LOCKUP_DAYS = 7

  useEffect(() => {
    const chainConfig = getChainConfig()
    if (chainConfig) {
      setMarket(new Market(chainConfig))
    }
  }, [])

  const fetchWithdrawalInfo = async () => {
    if (!user?.key || !market) return

    try {
      setIsRefreshing(true)
      
      // Get total balance
      const balance = await market.checkBalance(user.key, selectedToken)
      setTotalBalance(balance)

      // Get withdrawable amount (unlocked deposits)
      if (!window.ethereum) {
        setError('No Ethereum provider found. Please install MetaMask.')
        return
      }
      const provider = new ethers.BrowserProvider(window.ethereum)
      const marketAddress = getChainConfig().contracts.Market.address
      const MarketABI = (await import('@/contracts//market/Market.sol/Market.json')).default
      const marketContract = new ethers.Contract(marketAddress, MarketABI.abi, provider)
      
      const withdrawable = await marketContract.getWithdrawableAmount(user.key)
      setWithdrawableAmount(parseFloat(ethers.formatUnits(withdrawable, 8)))

      // Get deposit history
      const depositCount = await marketContract.getDepositCount(user.key)
      const depositList: Deposit[] = []
      
      for (let i = 0; i < Number(depositCount); i++) {
        const [amount, timestamp, unlocked] = await marketContract.getDeposit(user.key, i)
        depositList.push({
          amount: ethers.formatUnits(amount, 8),
          timestamp: Number(timestamp),
          unlocked: unlocked
        })
      }
      
      setDeposits(depositList)
    } catch (err) {
      console.error('Error fetching withdrawal info:', err)
      setWithdrawableAmount(0)
      setTotalBalance(0)
      setDeposits([])
    } finally {
      setIsRefreshing(false)
    }
  }

  useEffect(() => {
    fetchWithdrawalInfo()
  }, [user, market, selectedToken])

  const handleRefresh = async () => {
    await fetchWithdrawalInfo()
  }

  const handleWithdraw = async () => {
    if (!withdrawAmount || !market || !user?.key) {
      setError('Please enter an amount and ensure wallet is connected')
      return
    }

    const amount = parseFloat(withdrawAmount)
    if (amount <= 0) {
      setError('Amount must be greater than 0')
      return
    }

    if (amount > withdrawableAmount) {
      setError(`Cannot withdraw more than unlocked amount: $${withdrawableAmount.toFixed(2)}`)
      return
    }

    setIsProcessing(true)
    setError(null)
    setSuccess(null)

    try {
      if (!window.ethereum) {
        setError('No Ethereum provider found. Please install MetaMask.')
        return
      }
      const provider = new ethers.BrowserProvider(window.ethereum)
      const signer = await provider.getSigner(user.key)
      const marketAddress = getChainConfig().contracts.Market.address
      const MarketABI = (await import('@/contracts//market/Market.sol/Market.json')).default
      const marketContract = new ethers.Contract(marketAddress, MarketABI.abi, signer)

      const tokenAddress = market.getTokenAddress(selectedToken)
      const amountInWei = ethers.parseUnits(amount.toString(), 8)

      const tx = await marketContract.withdraw(tokenAddress, amountInWei, BigInt(0))
      await tx.wait()

      await fetchWithdrawalInfo()
      
      setSuccess(`Successfully withdrew $${amount.toFixed(2)} ${selectedToken} to your wallet!`)
      setWithdrawAmount('')
    } catch (err: any) {
      let msg = err?.message || String(err)
      if (msg.includes('Amount locked')) {
        msg = `Funds are still locked. Please wait for the ${LOCKUP_DAYS}-day lockup period to expire.`
      } else if (msg.includes('Insufficient balance')) {
        msg = 'Insufficient balance for withdrawal.'
      } else if (msg.toLowerCase().includes('cancel')) {
        msg = 'Transaction cancelled by user.'
      }
      setError(msg)
    } finally {
      setIsProcessing(false)
    }
  }

  const formatTimeRemaining = (timestamp: number): string => {
    const unlockTime = timestamp + (LOCKUP_DAYS * 24 * 60 * 60)
    const now = Math.floor(Date.now() / 1000)
    const remaining = unlockTime - now

    if (remaining <= 0) return 'Unlocked'

    const days = Math.floor(remaining / (24 * 60 * 60))
    const hours = Math.floor((remaining % (24 * 60 * 60)) / (60 * 60))
    
    return `${days}d ${hours}h remaining`
  }

  return (
    <div className="space-y-6">
      {/* Withdrawal Stats */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="p-6 bg-gradient-to-br from-blue-500/10 via-cyan-500/10 to-teal-500/10 border-2 border-blue-500/50 rounded-xl shadow-2xl">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-blue-500/70 font-bold text-sm uppercase mb-2">Total Balance</div>
              <div className="text-blue-500 font-black text-4xl">${totalBalance.toFixed(2)}</div>
            </div>
            <ArrowDownTrayIcon className="w-12 h-12 text-blue-500/30" />
          </div>
        </div>

        <div className="p-6 bg-gradient-to-br from-green-500/10 via-emerald-500/10 to-teal-500/10 border-2 border-green-500/50 rounded-xl shadow-2xl">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-green-500/70 font-bold text-sm uppercase mb-2">Withdrawable</div>
              <div className="text-green-500 font-black text-4xl">${withdrawableAmount.toFixed(2)}</div>
            </div>
            <div className="flex items-center gap-4">
              <button
                onClick={handleRefresh}
                disabled={isRefreshing}
                className="p-3 bg-green-500/20 border-2 border-green-500/50 rounded-lg hover:bg-green-500/30 hover:border-green-500 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                title="Refresh Balance"
              >
                <RefreshCw className={`w-6 h-6 text-green-500 ${isRefreshing ? 'animate-spin' : ''}`} />
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Withdrawal Form */}
      <div className="p-6 bg-gradient-to-br from-purple-500/10 via-pink-500/10 to-red-500/10 border-2 border-purple-500/50 rounded-xl shadow-2xl">
        <h2 className="text-2xl font-black text-purple-500 uppercase mb-6 flex items-center gap-2">
          <ArrowDownTrayIcon className="w-6 h-6" />
          Withdraw Credit
        </h2>

        <div className="space-y-4">
          <div>
            <label className="text-purple-500 font-bold text-sm uppercase mb-2 block">
              Select Token
            </label>
            <select
              value={selectedToken}
              onChange={(e) => setSelectedToken(e.target.value as TokenType)}
              className="w-full bg-black/60 border-2 border-purple-500/40 rounded-lg px-4 py-3 text-purple-300 font-mono text-lg focus:outline-none focus:border-purple-500 focus:ring-2 focus:ring-purple-500/30 transition-all"
            >
              <option value="USDC">USDC</option>
              <option value="USDT">USDT</option>
            </select>
          </div>

          <div>
            <label className="text-purple-500 font-bold text-sm uppercase mb-2 block">
              Amount (USD)
            </label>
            <input
              type="number"
              value={withdrawAmount}
              onChange={(e) => setWithdrawAmount(e.target.value)}
              disabled={isProcessing}
              min="0"
              max={withdrawableAmount}
              step="0.01"
              placeholder="0.00"
              className="w-full bg-black/60 border-2 border-purple-500/40 rounded-lg px-4 py-3 text-purple-300 font-mono text-lg placeholder-purple-600/50 focus:outline-none focus:border-purple-500 focus:ring-2 focus:ring-purple-500/30 disabled:opacity-50 transition-all"
            />
            <div className="text-purple-500/60 text-sm mt-2">
              Maximum withdrawable: ${withdrawableAmount.toFixed(2)}
            </div>
          </div>

          <button
            onClick={handleWithdraw}
            disabled={!withdrawAmount || isProcessing || withdrawableAmount === 0}
            className="w-full py-4 border-2 border-purple-500/60 bg-gradient-to-r from-purple-500/20 to-pink-500/20 text-purple-400 hover:bg-purple-500/30 hover:border-purple-500 hover:scale-[1.02] transition-all duration-300 rounded-xl font-mono uppercase font-black text-lg disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100 flex items-center justify-center gap-3 shadow-lg"
          >
            {isProcessing ? (
              <>
                <Zap className="w-5 h-5 animate-spin" />
                <span>PROCESSING...</span>
              </>
            ) : (
              <>
                <ArrowDownTrayIcon className="w-5 h-5" />
                <span>WITHDRAW TO WALLET</span>
              </>
            )}
          </button>

          <div className="text-purple-500/60 text-sm space-y-2">
            <p><strong>Note:</strong> Withdrawals are subject to a {LOCKUP_DAYS}-day lockup period from deposit.</p>
            <p>Only unlocked deposits can be withdrawn (FIFO - First In, First Out).</p>
          </div>
        </div>
      </div>

      {/* Deposit History */}
      {deposits.length > 0 && (
        <div className="p-6 bg-black/40 border-2 border-white/20 rounded-xl">
          <h3 className="text-xl font-black text-white/70 uppercase mb-4">Deposit History</h3>
          <div className="space-y-3">
            {deposits.map((deposit, idx) => (
              <div
                key={idx}
                className={`p-4 rounded-lg border-2 ${
                  deposit.unlocked
                    ? 'bg-green-500/10 border-green-500/30'
                    : 'bg-yellow-500/10 border-yellow-500/30'
                }`}
              >
                <div className="flex items-center justify-between">
                  <div>
                    <div className="font-mono text-lg font-bold text-white">
                      ${parseFloat(deposit.amount).toFixed(2)}
                    </div>
                    <div className="text-sm text-white/60">
                      {new Date(deposit.timestamp * 1000).toLocaleString()}
                    </div>
                  </div>
                  <div className={`px-3 py-1 rounded-lg font-bold text-sm ${
                    deposit.unlocked
                      ? 'bg-green-500/20 text-green-400 border border-green-500/40'
                      : 'bg-yellow-500/20 text-yellow-400 border border-yellow-500/40'
                  }`}>
                    {deposit.unlocked ? 'Unlocked' : formatTimeRemaining(deposit.timestamp)}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Status Messages */}
      {(success || error) && (
        <div
          className={`p-6 rounded-xl border-2 shadow-2xl ${
            error
              ? 'from-red-500/10 border-red-500/40 bg-gradient-to-br'
              : 'from-emerald-500/10 border-emerald-500/40 bg-gradient-to-br'
          }`}
        >
          <div className="flex items-center gap-3 text-base font-mono uppercase font-bold mb-4">
            {error ? (
              <>
                <AlertCircle className="w-5 h-5 text-red-500" />
                <span className="text-red-500">ERROR</span>
              </>
            ) : (
              <>
                <CheckCircle className="w-5 h-5 text-emerald-500" />
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

export default WithdrawalPanel