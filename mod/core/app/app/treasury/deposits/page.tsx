"use client";

import { useState, useEffect } from 'react'
import { BuildingLibraryIcon, ArrowDownTrayIcon, ClockIcon, UserIcon } from '@heroicons/react/24/outline'
import { ethers, EventLog } from 'ethers'
import { getChainConfig } from '@/network/chainConfig'
import { CopyButton } from '@/ui/CopyButton'
import { motion } from 'framer-motion'

export const dynamic = 'force-dynamic'

interface DepositEvent {
  funder: string
  token: string
  amount: string
  timestamp: number
  txHash: string
  blockNumber: number
}

export default function TreasuryDepositsPage() {
  const [treasuryAddress, setTreasuryAddress] = useState('')
  const [deposits, setDeposits] = useState<DepositEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [totalDeposits, setTotalDeposits] = useState('0')
  const [uniqueFunders, setUniqueFunders] = useState(0)

  useEffect(() => {
    const chainConfig = getChainConfig()
    if (chainConfig?.contracts?.Treasury?.address) {
      setTreasuryAddress(chainConfig.contracts.Treasury.address)
    }
  }, [])

  useEffect(() => {
    const fetchDeposits = async () => {
      if (typeof window === 'undefined') return;
      if (!treasuryAddress || typeof window.ethereum === 'undefined') return
      
      try {
        setLoading(true)
        const provider = new ethers.BrowserProvider(window.ethereum)
        const chainConfig = getChainConfig()
        
        const TreasuryABI = [
          'event TreasuryFunded(address indexed funder, address indexed token, uint256 amount)'
        ]
        const treasuryContract = new ethers.Contract(treasuryAddress, TreasuryABI, provider)
        
        const currentBlock = await provider.getBlockNumber()
        const fromBlock = Math.max(0, currentBlock - 10000)
        
        const filter = treasuryContract.filters.TreasuryFunded()
        const events = await treasuryContract.queryFilter(filter, fromBlock, currentBlock)
        
        
        const parsedEvents = events.filter((e): e is EventLog => 'args' in e);
        const depositEvents: DepositEvent[] = await Promise.all(
          parsedEvents.map(async (event) => {
            const block = await event.getBlock()
            const args = event.args
            
            let tokenSymbol = 'UNKNOWN'
            let decimals = 18
            
            try {
              const tokenContract = new ethers.Contract(
                args.token,
                ['function symbol() view returns (string)', 'function decimals() view returns (uint8)'],
                provider
              )
              tokenSymbol = await tokenContract.symbol()
              decimals = await tokenContract.decimals()
            } catch (err) {
              console.error('Error fetching token info:', err)
            }
            
            return {
              funder: args.funder,
              token: tokenSymbol,
              amount: ethers.formatUnits(args.amount, decimals),
              timestamp: block.timestamp,
              txHash: event.transactionHash,
              blockNumber: event.blockNumber
            }
          })
        )
        
        depositEvents.sort((a, b) => b.timestamp - a.timestamp)
        setDeposits(depositEvents)
        
        const total = depositEvents.reduce((sum, d) => sum + parseFloat(d.amount), 0)
        setTotalDeposits(total.toFixed(2))
        
        const funders = new Set(depositEvents.map(d => d.funder))
        setUniqueFunders(funders.size)
        
      } catch (error) {
        console.error('Error fetching deposits:', error)
      } finally {
        setLoading(false)
      }
    }

    if (treasuryAddress) {
      fetchDeposits()
      const interval = setInterval(fetchDeposits, 30000)
      return () => clearInterval(interval)
    }
  }, [treasuryAddress])

  if (!treasuryAddress) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: 'var(--bg-primary)' }}>
        <div className="text-purple-400 text-xl">Treasury not configured</div>
      </div>
    )
  }

  return (
    <div className="min-h-screen p-8" style={{ backgroundColor: 'var(--bg-primary)' }}>
      <div className="max-w-7xl mx-auto space-y-8">
        <motion.div
          initial={{ opacity: 0, y: -20 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex items-center gap-4 mb-8"
        >
          <div className="w-16 h-16 flex items-center justify-center bg-purple-500/20 border-2 border-purple-500/60 rounded-xl backdrop-blur-sm">
            <ArrowDownTrayIcon className="w-10 h-10 text-purple-400" />
          </div>
          <div>
            <h1 className="text-4xl font-bold text-white">Treasury Deposits</h1>
            <p className="text-purple-300 text-lg mt-1">Track all funding activity</p>
          </div>
        </motion.div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <motion.div
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: 0.1 }}
            className="bg-gradient-to-br from-green-500/20 to-emerald-500/20 border-2 border-green-500/40 rounded-xl p-6 backdrop-blur-xl"
          >
            <div className="flex items-center gap-3 mb-4">
              <BuildingLibraryIcon className="w-8 h-8 text-green-400" />
              <h3 className="text-green-300 text-sm uppercase tracking-wide font-bold">Total Deposits</h3>
            </div>
            <p className="text-4xl font-bold text-white">${loading ? '...' : totalDeposits}</p>
            <p className="text-green-400 text-sm mt-2">All-time funding</p>
          </motion.div>

          <motion.div
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: 0.2 }}
            className="bg-gradient-to-br from-blue-500/20 to-cyan-500/20 border-2 border-blue-500/40 rounded-xl p-6 backdrop-blur-xl"
          >
            <div className="flex items-center gap-3 mb-4">
              <ClockIcon className="w-8 h-8 text-blue-400" />
              <h3 className="text-blue-300 text-sm uppercase tracking-wide font-bold">Total Events</h3>
            </div>
            <p className="text-4xl font-bold text-white">{loading ? '...' : deposits.length}</p>
            <p className="text-blue-400 text-sm mt-2">Deposit transactions</p>
          </motion.div>

          <motion.div
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: 0.3 }}
            className="bg-gradient-to-br from-purple-500/20 to-pink-500/20 border-2 border-purple-500/40 rounded-xl p-6 backdrop-blur-xl"
          >
            <div className="flex items-center gap-3 mb-4">
              <UserIcon className="w-8 h-8 text-purple-400" />
              <h3 className="text-purple-300 text-sm uppercase tracking-wide font-bold">Unique Funders</h3>
            </div>
            <p className="text-4xl font-bold text-white">{loading ? '...' : uniqueFunders}</p>
            <p className="text-purple-400 text-sm mt-2">Contributors</p>
          </motion.div>
        </div>

        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4 }}
          className="bg-gradient-to-r from-purple-500/10 to-pink-500/10 border-2 border-purple-500/30 rounded-xl backdrop-blur-xl overflow-hidden"
        >
          <div className="p-6 border-b border-purple-500/30">
            <h2 className="text-2xl font-bold text-purple-300">Recent Deposits</h2>
          </div>
          
          {loading ? (
            <div className="p-12 text-center text-purple-400">Loading deposits...</div>
          ) : deposits.length === 0 ? (
            <div className="p-12 text-center text-purple-400">No deposits found</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead className="bg-purple-500/10">
                  <tr>
                    <th className="px-6 py-4 text-left text-purple-300 text-sm uppercase tracking-wide font-bold">Funder</th>
                    <th className="px-6 py-4 text-left text-purple-300 text-sm uppercase tracking-wide font-bold">Token</th>
                    <th className="px-6 py-4 text-right text-purple-300 text-sm uppercase tracking-wide font-bold">Amount</th>
                    <th className="px-6 py-4 text-left text-purple-300 text-sm uppercase tracking-wide font-bold">Time</th>
                    <th className="px-6 py-4 text-left text-purple-300 text-sm uppercase tracking-wide font-bold">Tx Hash</th>
                  </tr>
                </thead>
                <tbody>
                  {deposits.map((deposit, idx) => (
                    <tr key={idx} className="border-t border-purple-500/20 hover:bg-purple-500/5 transition-colors">
                      <td className="px-6 py-4">
                        <div className="flex items-center gap-2">
                          <span className="text-white font-mono text-sm">
                            {deposit.funder.slice(0, 6)}...{deposit.funder.slice(-4)}
                          </span>
                          <CopyButton text={deposit.funder} size="sm" />
                        </div>
                      </td>
                      <td className="px-6 py-4">
                        <span className="text-purple-400 font-bold">{deposit.token}</span>
                      </td>
                      <td className="px-6 py-4 text-right">
                        <span className="text-green-400 font-mono font-bold">${parseFloat(deposit.amount).toFixed(2)}</span>
                      </td>
                      <td className="px-6 py-4">
                        <span className="text-purple-300 text-sm">
                          {new Date(deposit.timestamp * 1000).toLocaleString()}
                        </span>
                      </td>
                      <td className="px-6 py-4">
                        <div className="flex items-center gap-2">
                          <span className="text-white font-mono text-sm">
                            {deposit.txHash.slice(0, 8)}...{deposit.txHash.slice(-6)}
                          </span>
                          <CopyButton text={deposit.txHash} size="sm" />
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </motion.div>
      </div>
    </div>
  )
}
