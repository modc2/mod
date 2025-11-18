import React, { useState } from 'react'
import {
  RefreshCw,
  Zap,
  CheckCircle,
  AlertCircle,
} from 'lucide-react'
import {useUserContext} from '@/app/context/UserContext'

export const UpdateMod: React.FC = () => {
  const { network, user } = useUserContext()
  const [modName, setModName] = useState('')
  const [modData, setModData] = useState('')
  const [modUrl, setModUrl] = useState('')
  const [take, setTake] = useState('0')
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
      fetchBalance(address)
    }
  }, [])

  const fetchBalance = async (address: string) => {
    try {
      const formatedBalance : string = (await network.balance(address)).toFixed(6)
      setBalance(formatedBalance)
    } catch (err) {
      console.error('Balance fetch error:', err)
    }
  }

  const executeUpdate = async () => {
    if (!modName || !modData || !modUrl) return setError('Please fill in all required fields')
    if (!walletAddress) return setError('No wallet connected')

    setIsLoading(true)
    setError(null)
    setResponse(null)

    try {
      const result = await network.update(
        walletAddress,
        modName,
        modData,
        modUrl,
        parseInt(take)
      )

      setResponse({
        ...result,
        name: modName,
        data: modData,
        url: modUrl,
        take: parseInt(take),
      })
      await fetchBalance(walletAddress)
      setModName('')
      setModData('')
      setModUrl('')
      setTake('0')
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
        {walletAddress ? (
          <>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-purple-400 text-sm font-mono">
                <CheckCircle size={16} />
              </div>
              <div className="text-purple-300 text-sm font-mono">
                {balance} MOD
              </div>
            </div>
          </>
        ) : (
          <div className="flex items-center gap-2 text-red-400 text-sm font-mono">
            <AlertCircle size={16} />
            <span>NO WALLET CONNECTED</span>
          </div>
        )}
      </div>

      {/* Update Form */}
      <div className="space-y-3 p-4 rounded-lg bg-gradient-to-br from-yellow-500/5 border border-yellow-500/20">
        <div className="space-y-3">
          <div>
            <label className="text-xs text-yellow-500/70 font-mono uppercase">
              Module Name
            </label>
            <input
              type="text"
              value={modName}
              onChange={(e) => setModName(e.target.value)}
              disabled={isLoading}
              placeholder="my-module"
              className="w-full mt-1 bg-black/50 border border-yellow-500/30 rounded px-3 py-2 text-yellow-400 font-mono text-sm placeholder-yellow-600/50 focus:outline-none focus:border-yellow-500 disabled:opacity-50"
            />
          </div>

          <div>
            <label className="text-xs text-yellow-500/70 font-mono uppercase">
              Data (CID)
            </label>
            <input
              type="text"
              value={modData}
              onChange={(e) => setModData(e.target.value)}
              disabled={isLoading}
              placeholder="QmXxx..."
              className="w-full mt-1 bg-black/50 border border-yellow-500/30 rounded px-3 py-2 text-yellow-400 font-mono text-sm placeholder-yellow-600/50 focus:outline-none focus:border-yellow-500 disabled:opacity-50"
            />
          </div>

          <div>
            <label className="text-xs text-yellow-500/70 font-mono uppercase">
              URL
            </label>
            <input
              type="text"
              value={modUrl}
              onChange={(e) => setModUrl(e.target.value)}
              disabled={isLoading}
              placeholder="https://..."
              className="w-full mt-1 bg-black/50 border border-yellow-500/30 rounded px-3 py-2 text-yellow-400 font-mono text-sm placeholder-yellow-600/50 focus:outline-none focus:border-yellow-500 disabled:opacity-50"
            />
          </div>

          <div>
            <label className="text-xs text-yellow-500/70 font-mono uppercase">
              Take (%)
            </label>
            <input
              type="number"
              value={take}
              onChange={(e) => setTake(e.target.value)}
              disabled={isLoading}
              min="0"
              max="100"
              placeholder="0"
              className="w-full mt-1 bg-black/50 border border-yellow-500/30 rounded px-3 py-2 text-yellow-400 font-mono text-sm placeholder-yellow-600/50 focus:outline-none focus:border-yellow-500 disabled:opacity-50"
            />
          </div>

          <button
            onClick={executeUpdate}
            disabled={!modName || !modData || !modUrl || isLoading || !walletAddress}
            className="w-full py-2 border border-yellow-500/50 text-yellow-400 hover:bg-yellow-500/10 hover:border-yellow-500 transition-all rounded font-mono uppercase disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
          >
            {isLoading ? (
              <>
                <Zap size={16} className="animate-spin" />
                <span>Processing...</span>
              </>
            ) : (
              <>
                <RefreshCw size={16} />
                <span>Update Module</span>
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
              : 'from-yellow-500/5 border-yellow-500/20'
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
                <CheckCircle size={16} className="text-yellow-500" />
                <span className="text-yellow-500">Success</span>
              </>
            )}
          </div>

          {error ? (
            <div className="text-red-400 font-mono text-sm bg-black/50 p-3 rounded border border-red-500/20 whitespace-pre-wrap">
              {error}
            </div>
          ) : (
            <pre className="text-yellow-400 font-mono text-xs overflow-x-auto bg-black/50 p-3 rounded border border-yellow-500/20">
              {JSON.stringify(response, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}

export default UpdateMod
