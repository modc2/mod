
import React, { useState } from 'react'
import {
  Send,
  Zap,
  CheckCircle,
  AlertCircle,
  ArrowRightLeft,
} from 'lucide-react'
import { ApiPromise, WsProvider } from '@polkadot/api'
import { web3Enable, web3FromAddress } from '@polkadot/extension-dapp'

export const Transfer: React.FC = () => {
  const [toAddress, setToAddress] = useState('')
  const [amount, setAmount] = useState('')
  const [network, setNetwork] = useState('test')
  const [response, setResponse] = useState<any>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [walletAddress, setWalletAddress] = useState('')
  const [balance, setBalance] = useState<string>('0')

  const networks = [
    { id: 'test', label: 'Modchain Devnet', url: 'wss://dev.api.modchain.ai' },
  ]

  React.useEffect(() => {
    if (typeof window === 'undefined') return
    
    const address = localStorage.getItem('wallet_address')
    const mode = localStorage.getItem('wallet_mode')
    if (mode === 'subwallet' && address) {
      setWalletAddress(address)
      fetchBalance(address)
    }
  }, [])

  const fetchBalance = async (address: string) => {
    try {
      const selectedNetwork = networks.find((n) => n.id === network)
      if (!selectedNetwork) return

      const provider = new WsProvider(selectedNetwork.url)
      const api = await ApiPromise.create({ provider })
      await api.isReady

      const accountInfo: any = await api.query.system.account(address)
      const freeBalance = accountInfo.data.free.toBigInt()
      const formattedBalance = Number(freeBalance) / 1e12
      setBalance(formattedBalance.toFixed(6))
      
      await api.disconnect()
    } catch (err) {
      console.error('Balance fetch error:', err)
    }
  }

  const executeTransfer = async () => {
    if (!toAddress || !amount) return setError('Please fill in all fields')
    if (!walletAddress) return setError('No wallet connected')

    setIsLoading(true)
    setError(null)
    setResponse(null)

    try {
      const selectedNetwork = networks.find((n) => n.id === network)
      if (!selectedNetwork) throw new Error('Network not found')

      const provider = new WsProvider(selectedNetwork.url)
      const api = await ApiPromise.create({ provider })
      await api.isReady

      const recipientAddress = api.registry.createType(
        'AccountId32',
        toAddress
      ).toString()

      const amountFloat = parseFloat(amount)
      if (isNaN(amountFloat) || amountFloat <= 0)
        throw new Error('Invalid amount')

      const transferAmount = BigInt(Math.floor(amountFloat * 1e12))

      const senderInfo: any = await api.query.system.account(walletAddress)
      const senderBalance = senderInfo.data.free.toBigInt()
      const feeBuffer = BigInt(100_000_000)
      if (senderBalance < transferAmount + feeBuffer)
        throw new Error('Insufficient balance')

      const extensions = await web3Enable('MOD')
      if (extensions.length === 0)
        throw new Error('SubWallet not found. Please install it.')
      
      const injector = await web3FromAddress(walletAddress)
      if (!injector?.signer)
        throw new Error('No signer available from SubWallet')

      const tx = api.tx.balances.transferKeepAlive(
        recipientAddress,
        transferAmount
      )

      const result = await new Promise<any>((resolve, reject) => {
        let unsub: (() => void) | undefined
        let resolved = false
        
        const timeout = setTimeout(() => {
          if (!resolved) {
            resolved = true
            if (unsub) unsub()
            reject(new Error('Transaction timeout after 120s'))
          }
        }, 120_000)

        tx.signAndSend(
          walletAddress,
          { signer: injector.signer },
          ({ status, dispatchError, txHash, events }) => {
            console.log('📊 Status:', status.type)

            if (dispatchError) {
              if (!resolved) {
                resolved = true
                clearTimeout(timeout)
                if (unsub) unsub()
                
                if (dispatchError.isModule) {
                  const decoded = api.registry.findMetaError(dispatchError.asModule)
                  const { section, name, docs } = decoded
                  reject(new Error(`${section}.${name}: ${docs.join(' ')}`))
                } else {
                  reject(new Error(dispatchError.toString()))
                }
              }
              return
            }

            if (status.isInBlock) {
              console.log('✅ In block:', status.asInBlock.toHex())
            }
            
            if (status.isFinalized) {
              if (!resolved) {
                resolved = true
                clearTimeout(timeout)
                console.log('🎉 Finalized:', status.asFinalized.toHex())
                resolve({
                  success: true,
                  blockHash: status.asFinalized.toHex(),
                  txHash: txHash.toHex(),
                  events: events?.map(e => e.toHuman()),
                })
                if (unsub) unsub()
              }
            }
          }
        ).then((unsubFn) => {
          unsub = unsubFn
        }).catch((err) => {
          if (!resolved) {
            resolved = true
            clearTimeout(timeout)
            reject(err)
          }
        })
      })

      setResponse({
        ...result,
        amount: parseFloat(amount),
        to: toAddress,
        from: walletAddress,
        timestamp: new Date().toISOString(),
      })

      await fetchBalance(walletAddress)
      setToAddress('')
      setAmount('')
      await api.disconnect()
    } catch (err: any) {
      console.error('❌ Transfer failed:', err)
      
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
                <span>SUBWALLET CONNECTED</span>
              </div>
              <div className="text-purple-300 text-sm font-mono">
                {balance} MOD
              </div>
            </div>
            <div className="text-purple-500/70 text-xs font-mono mt-1">
              {walletAddress.slice(0, 8)}...{walletAddress.slice(-6)}
            </div>
          </>
        ) : (
          <div className="flex items-center gap-2 text-red-400 text-sm font-mono">
            <AlertCircle size={16} />
            <span>NO WALLET CONNECTED</span>
          </div>
        )}
      </div>

      {/* Transfer Form */}
      <div className="space-y-3 p-4 rounded-lg bg-gradient-to-br from-green-500/5 border border-green-500/20">
        <div className="flex items-center gap-2 text-green-500/70 text-sm font-mono uppercase mb-3">
          <ArrowRightLeft size={16} />
          <span>Transfer Tokens</span>
        </div>

        <div className="space-y-3">
          <div>
            <label className="text-xs text-green-500/70 font-mono uppercase">
              Network
            </label>
            <select
              value={network}
              onChange={(e) => setNetwork(e.target.value)}
              disabled={isLoading}
              className="w-full mt-1 bg-black/50 border border-green-500/30 rounded px-3 py-2 text-green-400 font-mono text-sm focus:outline-none focus:border-green-500 disabled:opacity-50"
            >
              {networks.map((net) => (
                <option key={net.id} value={net.id}>
                  {net.label}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="text-xs text-green-500/70 font-mono uppercase">
              To Address
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
