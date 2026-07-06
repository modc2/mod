"use client";

import { useState, useEffect } from 'react'
import { userContext } from '@/context'
import { Send, Zap, CheckCircle, AlertCircle, ArrowUpDown } from 'lucide-react'
import { CopyButton } from '@/ui/CopyButton'
import { text2color } from '@/utils'
import { ethers } from 'ethers'
import { getChainConfig } from '@/network/chainConfig'
import MarketABI from '@/contracts//market/Market.sol/Market.json'
import { TransferHeader } from './TransferHeader'

interface TransferHistory {
  to: string
  amount: number
  timestamp: string
  hash?: string
  networkUrl?: string
  tokenAddress?: string
  tokenSymbol?: string
}

const NETWORK_CONFIGS: Record<string, { chainId: string, params: any }> = {
  'local': {
    chainId: '0x7a69',
    params: {
      chainId: '0x7a69',
      chainName: 'Local Ganache',
      rpcUrls: ['http://localhost:8545'],
      nativeCurrency: { name: 'ETH', symbol: 'ETH', decimals: 18 }
    }
  },
  'base-sepolia': {
    chainId: '0x14a34',
    params: {
      chainId: '0x14a34',
      chainName: 'Base Sepolia',
      rpcUrls: ['https://sepolia.base.org'],
      nativeCurrency: { name: 'ETH', symbol: 'ETH', decimals: 18 },
      blockExplorerUrls: ['https://sepolia.basescan.org']
    }
  },
  'base-mainnet': {
    chainId: '0x2105',
    params: {
      chainId: '0x2105',
      chainName: 'Base',
      rpcUrls: ['https://mainnet.base.org'],
      nativeCurrency: { name: 'ETH', symbol: 'ETH', decimals: 18 },
      blockExplorerUrls: ['https://basescan.org']
    }
  }
}

const ERC20_ABI = [
  'function transfer(address to, uint256 amount) returns (bool)',
  'function balanceOf(address owner) view returns (uint256)',
  'function decimals() view returns (uint8)',
  'function symbol() view returns (string)'
]

interface TokenOption {
  address: string
  symbol: string
  decimals: number
}

export const Transfer: React.FC = () => {
  const { network, user } = userContext()
  const [toAddress, setToAddress] = useState('')
  const [amount, setAmount] = useState('')
  const [selectedToken, setSelectedToken] = useState<TokenOption | null>(null)
  const [response, setResponse] = useState<any>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [walletAddress, setWalletAddress] = useState('')
  const [balance, setBalance] = useState<string>('0')
  const [tokenBalance, setTokenBalance] = useState<string>('0')
  const [history, setHistory] = useState<TransferHistory[]>([])
  const [sortAsc, setSortAsc] = useState(false)
  const [currentNetwork, setCurrentNetwork] = useState<string>('')
  const [currentNetworkUrl, setCurrentNetworkUrl] = useState<string>('')
  const [usdcBalance, setUsdcBalance] = useState<string>('0')
  const [usdtBalance, setUsdtBalance] = useState<string>('0')
  const [marketBalance, setMarketBalance] = useState<string>('0')

  const userColor = text2color(user?.key || 'default')

  useEffect(() => {
    if (typeof window === 'undefined') return
    const address = user?.key || ''
    const mode = localStorage.getItem('wallet_mode')
    const networkUrl = localStorage.getItem('network_url') || 'http://localhost:8545'
    const selectedNetwork = localStorage.getItem('selected_network') || 'local'
    
    setCurrentNetwork(selectedNetwork)
    setCurrentNetworkUrl(networkUrl)
    
    if (address) {
      setWalletAddress(address)
      if (mode === 'metamask') {
        fetchBalance(address, networkUrl)
        fetchUsdcUsdtBalances(address)
        fetchMarketBalance(address)
      }
    }
    const savedHistory = localStorage.getItem(`transfer_history_${address}`)
    if (savedHistory) {
      setHistory(JSON.parse(savedHistory))
    }
  }, [user, network])

  useEffect(() => {
    if (selectedToken && selectedToken.address !== 'ETH' && ethers.isAddress(selectedToken.address)) {
      fetchTokenInfo(selectedToken.address)
    } else if (selectedToken?.address === 'ETH') {
      fetchBalance(walletAddress, currentNetworkUrl)
    }
  }, [selectedToken, walletAddress])

  const fetchBalance = async (address: string, networkUrl?: string) => {
    try {
      const url = networkUrl || localStorage.getItem('network_url') || 'http://localhost:8545'
      const provider = new ethers.JsonRpcProvider(url)
      const balanceWei = await provider.getBalance(address)
      const balanceEth = ethers.formatEther(balanceWei)
      setBalance(parseFloat(balanceEth).toFixed(6))
    } catch (err) {
      console.error('Failed to fetch balance:', err)
    }
  }

  const fetchUsdcUsdtBalances = async (address: string) => {
    try {
      const url = localStorage.getItem('network_url') || 'http://localhost:8545'
      const provider = new ethers.JsonRpcProvider(url)
      const chainConfig = getChainConfig()
      
      if (chainConfig?.contracts?.USDC?.address) {
        const usdcContract = new ethers.Contract(chainConfig.contracts.USDC.address, ERC20_ABI, provider)
        const usdcBal = await usdcContract.balanceOf(address)
        const usdcDecimals = await usdcContract.decimals()
        setUsdcBalance(parseFloat(ethers.formatUnits(usdcBal, usdcDecimals)).toFixed(2))
      }
      
      if (chainConfig?.contracts?.USDT?.address) {
        const usdtContract = new ethers.Contract(chainConfig.contracts.USDT.address, ERC20_ABI, provider)
        const usdtBal = await usdtContract.balanceOf(address)
        const usdtDecimals = await usdtContract.decimals()
        setUsdtBalance(parseFloat(ethers.formatUnits(usdtBal, usdtDecimals)).toFixed(2))
      }
    } catch (err) {
      console.error('Failed to fetch USDC/USDT balances:', err)
    }
  }

  const fetchMarketBalance = async (address: string) => {
    try {
      const url = localStorage.getItem('network_url') || 'http://localhost:8545'
      const provider = new ethers.JsonRpcProvider(url)
      const chainConfig = getChainConfig()
      
      if (chainConfig?.contracts?.Market?.address) {
        const marketContract = new ethers.Contract(chainConfig.contracts.Market.address, MarketABI.abi, provider)
        const marketBal = await marketContract.balanceOf(address)
        const decimals = await marketContract.decimals()
        setMarketBalance(parseFloat(ethers.formatUnits(marketBal, decimals)).toFixed(6))
      }
    } catch (err) {
      console.error('Failed to fetch Market balance:', err)
    }
  }

  const fetchTokenInfo = async (tokenAddress: string) => {
    try {
      const url = localStorage.getItem('network_url') || 'http://localhost:8545'
      const provider = new ethers.JsonRpcProvider(url)
      const contract = new ethers.Contract(tokenAddress, ERC20_ABI, provider)
      
      const [symbol, decimals, balance] = await Promise.all([
        contract.symbol(),
        contract.decimals(),
        contract.balanceOf(walletAddress)
      ])
      
      setTokenBalance(parseFloat(ethers.formatUnits(balance, decimals)).toFixed(6))
    } catch (err) {
      console.error('Failed to fetch token info:', err)
      setError('Invalid ERC20 token address')
    }
  }

  const switchToSelectedNetwork = async () => {
    const selectedNetwork = localStorage.getItem('selected_network') || 'local'
    const config = NETWORK_CONFIGS[selectedNetwork]
    
    if (!config) {
      throw new Error(`Unknown network: ${selectedNetwork}`)
    }

    if (typeof window.ethereum === 'undefined') {
      throw new Error('MetaMask is not installed')
    }

    try {
      await window.ethereum.request({
        method: 'wallet_switchEthereumChain',
        params: [{ chainId: config.chainId }],
      })
    } catch (switchError: any) {
      if (switchError.code === 4902) {
        await window.ethereum.request({
          method: 'wallet_addEthereumChain',
          params: [config.params],
        })
      } else {
        throw switchError
      }
    }
  }

  const executeTransfer = async () => {
    if (!toAddress || !amount) return setError('Please fill in all fields')
    if (!walletAddress) return setError('No wallet connected')
    if (!selectedToken) return setError('Please select a token')
    if (selectedToken.address !== 'ETH' && !ethers.isAddress(selectedToken.address)) return setError('Invalid token address')

    setIsLoading(true)
    setError(null)
    setResponse(null)

    try {
      const networkUrl = localStorage.getItem('network_url') || 'http://localhost:8545'
      
      if (typeof window.ethereum === 'undefined') {
        throw new Error('MetaMask is not installed')
      }
      
      await switchToSelectedNetwork()
      
      const browserProvider = new ethers.BrowserProvider(window.ethereum)
      const selectedAddress = user?.key || localStorage.getItem('wallet_address') || undefined
      const signer = await browserProvider.getSigner(selectedAddress)

      let tx, receipt
      
      if (selectedToken.address !== 'ETH') {
        const contract = new ethers.Contract(selectedToken.address, ERC20_ABI, signer)
        const tokenDecimals = await contract.decimals()
        const amountInWei = ethers.parseUnits(amount, tokenDecimals)

        console.log('Transferring', amountInWei.toString(), 'of', selectedToken.address, 'with', tokenDecimals, 'decimals')  

        tx = await contract.transfer(toAddress, amountInWei)
        receipt = await tx.wait()
      } else {
        tx = await signer.sendTransaction({
          to: toAddress,
          value: ethers.parseEther(amount),
          gasLimit: ethers.toBigInt(21000)
        })
        receipt = await tx.wait()
      }

      const newTransfer: TransferHistory = {
        to: toAddress,
        amount: parseFloat(amount),
        timestamp: new Date().toISOString(),
        hash: receipt?.hash || tx.hash,
        networkUrl: networkUrl,
        tokenAddress: selectedToken.address !== 'ETH' ? selectedToken.address : undefined,
        tokenSymbol: selectedToken.symbol
      }

      const updatedHistory = [newTransfer, ...history]
      setHistory(updatedHistory)
      localStorage.setItem(`transfer_history_${walletAddress}`, JSON.stringify(updatedHistory))

      setResponse({
        hash: receipt?.hash || tx.hash,
        blockNumber: receipt?.blockNumber,
        amount: parseFloat(amount),
        to: toAddress,
        from: walletAddress,
        status: 'success',
        networkUrl: networkUrl,
        network: currentNetwork,
        tokenAddress: selectedToken.address !== 'ETH' ? selectedToken.address : undefined,
        tokenSymbol: selectedToken.symbol
      })
      setToAddress('')
      setAmount('')
      
      if (selectedToken.address !== 'ETH') {
        await fetchTokenInfo(selectedToken.address)
      } else {
        await fetchBalance(walletAddress, networkUrl)
      }
      
      await fetchUsdcUsdtBalances(walletAddress)
      await fetchMarketBalance(walletAddress)
    } catch (err: any) {
      let msg = err?.message || String(err)
      if (msg.includes('insufficient funds')) 
        msg = 'Insufficient balance for transfer and fees.'
      else if (msg.toLowerCase().includes('user rejected'))
        msg = 'Transaction rejected by user.'
      else if (msg.includes('timeout'))
        msg = 'Transaction timeout. Please try again.'
      setError(msg)
    } finally {
      setIsLoading(false)
    }
  }

  const sortedHistory = [...history].sort((a, b) => {
    const timeA = new Date(a.timestamp).getTime()
    const timeB = new Date(b.timestamp).getTime()
    return sortAsc ? timeA - timeB : timeB - timeA
  })

  return (
    <div className="space-y-6 animate-fadeIn">
      <div className="space-y-5 p-6 rounded-xl bg-gradient-to-br from-green-500/10 via-emerald-500/10 to-teal-500/10 border-2 border-green-500/30 shadow-2xl">

        <div className="bg-black/40 border-2 border-green-500/30 rounded-lg p-3 mb-4">
          <div className="flex items-center gap-2">
            <span className="text-xs text-green-400 font-mono font-bold uppercase">RPC URL:</span>
            <code className="text-xs text-green-300 font-mono flex-1 truncate">{currentNetworkUrl}</code>
            <CopyButton text={currentNetworkUrl} size="sm" />
          </div>
        </div>

        <TransferHeader
          selectedToken={selectedToken}
          setSelectedToken={setSelectedToken}
          walletAddress={walletAddress}
          currentNetwork={currentNetwork}
        />

        <div className="space-y-4">

          <div className="space-y-2">
            <label className="text-sm text-green-400 font-mono uppercase font-bold tracking-wide">
              Destination Address
            </label>
            <input
              type="text"
              value={toAddress}
              onChange={(e) => setToAddress(e.target.value)}
              disabled={isLoading}
              placeholder="0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb"
              className="w-full bg-black/60 border-2 border-green-500/40 rounded-lg px-4 py-3 text-green-300 font-mono text-base placeholder-green-600/50 focus:outline-none focus:border-green-500 focus:ring-2 focus:ring-green-500/30 disabled:opacity-50 transition-all"
            />
          </div>

          <div className="space-y-2">
            <label className="text-sm text-green-400 font-mono uppercase font-bold tracking-wide">
              Amount ({selectedToken?.symbol || ''})
            </label>
            <input
              type="number"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              disabled={isLoading}
              min="0"
              step="any"
              placeholder="0.0"
              className="w-full bg-black/60 border-2 border-green-500/40 rounded-lg px-4 py-3 text-green-300 font-mono text-base placeholder-green-600/50 focus:outline-none focus:border-green-500 focus:ring-2 focus:ring-green-500/30 disabled:opacity-50 transition-all"
            />
            <p className="text-xs text-green-500/50 mt-1 font-mono">
              Available: {selectedToken?.address === 'ETH' ? `${balance} ETH` : `${tokenBalance} ${selectedToken?.symbol || ''}`}
            </p>
          </div>

          <button
            onClick={executeTransfer}
            disabled={!toAddress || !amount || isLoading || !walletAddress || !selectedToken}
            className="w-full py-4 border-2 border-green-500/60 bg-gradient-to-r from-green-500/20 to-emerald-500/20 text-green-400 hover:bg-green-500/30 hover:border-green-500 hover:scale-[1.02] transition-all duration-300 rounded-xl font-mono uppercase font-black text-lg disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100 flex items-center justify-center gap-3 shadow-lg"
          >
            {isLoading ? (
              <>
                <Zap size={20} className="animate-spin" />
                <span>PROCESSING...</span>
              </>
            ) : (
              <>
                <Send size={20} />
                <span>SEND TRANSFER</span>
              </>
            )}
          </button>
        </div>
      </div>

      {(response || error) && (
        <div
          className={`space-y-4 p-6 rounded-xl border-2 shadow-2xl ${
            error
              ? 'from-red-500/10 border-red-500/40 bg-gradient-to-br'
              : 'from-emerald-500/10 border-emerald-500/40 bg-gradient-to-br'
          }`}
        >
          <div className="flex items-center gap-3 text-base font-mono uppercase font-bold">
            {error ? (
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

          {error ? (
            <div className="text-red-400 font-mono text-base bg-black/60 p-4 rounded-lg border-2 border-red-500/30 whitespace-pre-wrap font-bold">
              {error}
            </div>
          ) : (
            <pre className="text-emerald-400 font-mono text-sm overflow-x-auto bg-black/60 p-4 rounded-lg border-2 border-emerald-500/30">
{JSON.stringify(response, null, 2)}
            </pre>
          )}
        </div>
      )}

      {history.length > 0 && (
        <div className="rounded-xl border-2 font-mono" style={{ backgroundColor: `${userColor}15`, borderColor: userColor }}>
          <div className="flex items-center justify-between p-3 border-b-2" style={{ borderColor: userColor }}>
            <h3 className="text-xl font-black" style={{ color: userColor, letterSpacing: '0.02em' }}>Transfer History</h3>
            <button
              onClick={() => setSortAsc(!sortAsc)}
              className="px-3 py-1.5 rounded-lg hover:opacity-80 transition-all flex items-center gap-2 text-xs font-semibold border"
              style={{ backgroundColor: 'rgba(0,0,0,0.4)', color: userColor, borderColor: `${userColor}40` }}
            >
              <ArrowUpDown className="w-4 h-4" />
              {sortAsc ? 'Oldest' : 'Newest'}
            </button>
          </div>
          <div className="max-h-96 overflow-y-auto space-y-2 p-3" style={{ scrollbarWidth: 'thin' }}>
            {sortedHistory.map((transfer, idx) => (
              <div
                key={idx}
                className="p-3 rounded-lg border-2 hover:bg-opacity-90 transition-all"
                style={{ backgroundColor: 'rgba(0,0,0,0.4)', borderColor: `${userColor}40` }}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="space-y-2 flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <div className="flex items-center gap-1 bg-black/40 border rounded px-2 py-1" style={{ borderColor: `${userColor}40` }}>
                        <Send className="w-4 h-4 flex-shrink-0" style={{ color: userColor }} />
                        <span className="font-black text-sm" style={{ color: userColor }}>{transfer.amount.toFixed(6)} {transfer.tokenSymbol || 'ETH'}</span>
                      </div>
                      <div className="flex items-center gap-1 bg-black/40 border border-blue-500/30 rounded px-2 py-1">
                        <span className="text-xs text-blue-400 font-mono">{new Date(transfer.timestamp).toLocaleString()}</span>
                      </div>
                    </div>
                    <div className="flex items-center gap-1 bg-black/40 border border-green-500/30 rounded px-2 py-1">
                      <code className="text-xs font-mono" style={{ color: '#10b981' }}>
                        To: {transfer.to.slice(0, 12)}...{transfer.to.slice(-8)}
                      </code>
                      <CopyButton text={transfer.to} size="sm" />
                    </div>
                    {transfer.tokenAddress && (
                      <div className="flex items-center gap-1 bg-black/40 border border-yellow-500/30 rounded px-2 py-1">
                        <code className="text-xs font-mono text-yellow-400">
                          Token: {transfer.tokenAddress.slice(0, 12)}...{transfer.tokenAddress.slice(-8)}
                        </code>
                        <CopyButton text={transfer.tokenAddress} size="sm" />
                      </div>
                    )}
                    {transfer.hash && (
                      <div className="flex items-center gap-1 bg-black/40 border border-purple-500/30 rounded px-2 py-1">
                        <code className="text-xs font-mono text-purple-400">
                          Hash: {transfer.hash.slice(0, 12)}...{transfer.hash.slice(-8)}
                        </code>
                        <CopyButton text={transfer.hash} size="sm" />
                      </div>
                    )}
                    {transfer.networkUrl && (
                      <div className="flex items-center gap-1 bg-black/40 border border-cyan-500/30 rounded px-2 py-1">
                        <code className="text-xs font-mono text-cyan-400">
                          Network: {transfer.networkUrl}
                        </code>
                        <CopyButton text={transfer.networkUrl} size="sm" />
                      </div>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

export default Transfer