
import React, { useState } from 'react'
import {
  Send,
  Zap,
  CheckCircle,
  AlertCircle,
} from 'lucide-react'
import {useUserContext} from '@/bloc/context/UserContext'
export const Transfer: React.FC = () => {

  const { network, user } = useUserContext()
  const [toAddress, setToAddress] = useState('')
  const [amount, setAmount] = useState('')
  const [response, setResponse] = useState<any>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [walletAddress, setWalletAddress] = useState('')
  const [balance, setBalance] = useState<string>('0')

  React.useEffect(() => {
    if (typeof window === 'undefined') return
    const address = user?.key || ''
    const mode = localStorage.getItem('wallet_mode')
    if (mode === 'subwallet' && address) {
      setWalletAddress(address)
    }
  }, [])


  const executeTransfer = async () => {
    if (!toAddress || !amount) return setError('Please fill in all fields')
    if (!walletAddress) return setError('No wallet connected')

    setIsLoading(true)
    setError(null)
    setResponse(null)

    try {

      const result = await network.transfer(
        walletAddress,
        toAddress,
        parseFloat(amount)
      )

      setResponse({
        ...result,
        amount: parseFloat(amount),
        to: toAddress,
        from: walletAddress,
      })
      setToAddress('')
      setAmount('')
    } catch (err: any) {      
      let msg = err?.message || String(err)
      if (msg.includes('1010')) 
        msg = 'Insufficient balance for fees.'
      else if (msg.toLowerCase().includes('cancel'))
        msg = 'Transaction cancelled by user.'
      else if (msg.includes('timeout'))
        msg = 'Transaction timeout. Please try again.'
      setError(msg)
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="space-y-6 animate-fadeIn">
      {/* Wallet Status */}
      <div
        className={`p-3 rounded-lg border ${
          walletAddress
            ? 'bg-gradient-to-br from-purple-500/10 border-purple-500/30'
            : 'bg-gradient-to-br from-red-500/10 border-red-500/30'
        }`}
      >
        {walletAddress && network ? (
          <div className="flex items-center gap-2 text-green-400 text-sm font-mono">
            <CheckCircle size={16} />
            <span>CONNECTED {network.url}</span>
          </div>
        ) : (
          <div className="flex items-center gap-2 text-red-400 text-sm font-mono">
            <AlertCircle size={16} />
            <span>NO WALLET CONNECTED</span>
          </div>
        )}
      </div>

      {/* Transfer Form */}
      <div className="space-y-3 p-4 rounded-lg bg-gradient-to-br from-green-500/5 border border-green-500/20">

        <div className="space-y-3">

          <div>
            <label className="text-xs text-green-500/70 font-mono uppercase">
              dest
            </label>
            <input
              type="text"
              value={toAddress}
              onChange={(e) => setToAddress(e.target.value)}
              disabled={isLoading}
              placeholder="5GrwvaEF5zXb26..."
              className="w-full mt-1 bg-black/50 border border-green-500/30 rounded px-3 py-2 text-green-400 font-mono text-sm placeholder-green-600/50 focus:outline-none focus:border-green-500 disabled:opacity-50"
            />
          </div>

          <div>
            <label className="text-xs text-green-500/70 font-mono uppercase">
              Amount (MOD)
            </label>
            <input
              type="number"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              disabled={isLoading}
              min="0"
              step="0.000000001"
              placeholder="0.0"
              className="w-full mt-1 bg-black/50 border border-green-500/30 rounded px-3 py-2 text-green-400 font-mono text-sm placeholder-green-600/50 focus:outline-none focus:border-green-500 disabled:opacity-50"
            />
            <p className="text-xs text-green-500/50 mt-1 font-mono">
              Available: {balance} MOD
            </p>
          </div>

          <button
            onClick={executeTransfer}
            disabled={!toAddress || !amount || isLoading || !walletAddress}
            className="w-full py-2 border border-green-500/50 text-green-400 hover:bg-green-500/10 hover:border-green-500 transition-all rounded font-mono uppercase disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
          >
            {isLoading ? (
              <>
                <Zap size={16} className="animate-spin" />
                <span>Processing...</span>
              </>
            ) : (
              <>
                <Send size={16} />
                <span>Send Transfer</span>
              </>
            )}
          </button>
        </div>
      </div>

      {/* Response */}
      {(response || error) && (
        <div
          className={`space-y-3 p-4 rounded-lg border ${
            error
              ? 'from-red-500/5 border-red-500/20'
              : 'from-green-500/5 border-green-500/20'
          }`}
        >
          <div className="flex items-center gap-2 text-sm font-mono uppercase">
            {error ? (
              <>
                <AlertCircle size={16} className="text-red-500" />
                <span className="text-red-500">Error</span>
              </>
            ) : (
              <>
                <CheckCircle size={16} className="text-green-500" />
                <span className="text-green-500">Success</span>
              </>
            )}
          </div>

          {error ? (
            <div className="text-red-400 font-mono text-sm bg-black/50 p-3 rounded border border-red-500/20 whitespace-pre-wrap">
              {error}
            </div>
          ) : (
            <pre className="text-green-400 font-mono text-xs overflow-x-auto bg-black/50 p-3 rounded border border-green-500/20">
              {JSON.stringify(response, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}

export default Transfer
