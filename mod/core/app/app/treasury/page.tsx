"use client";

import { useState, useEffect, useCallback } from 'react'
import {
  BuildingLibraryIcon,
  ShieldCheckIcon,
  ArrowPathIcon,
  WalletIcon,
  ChevronDownIcon,
  SparklesIcon,
  BanknotesIcon,
  CubeTransparentIcon,
  LockClosedIcon,
  ExclamationTriangleIcon,
} from '@heroicons/react/24/outline'
import { ethers, EventLog } from 'ethers'
import TokenABI from '@/contracts/token/Token.sol/Token.json'
import MarketABI from '@/contracts/market/Market.sol/Market.json'
import { getChainConfig } from '@/network/chainConfig'
import { CopyButton } from '@/ui/CopyButton'
import { motion, AnimatePresence } from 'framer-motion'
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip } from 'recharts'
import { userContext } from '@/context'
import { Treasury, type TreasuryInfo, type HolderInfo, type TokenBalance } from '@/network/Treasury'
import { isSafeContract, getSafeOwners, getSafeThreshold, isUserSafeOwner, proposeSafeTransaction, type SafeInfo } from '@/network/safe'
import { toast } from 'react-toastify'
import BlocTimeABI from '@/contracts/bloctime/BlocTime.sol/BlocTime.json'

export const dynamic = 'force-dynamic'

type Tab = 'overview' | 'member' | 'owner'

interface DepositEvent {
  funder: string
  token: string
  amount: string
  timestamp: number
  txHash: string
  blockNumber: number
}

interface ClaimEvent {
  holder: string
  token: string
  amount: string
  timestamp: number
  txHash: string
  blockNumber: number
}

function fmt(n: number): string {
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`
  if (n >= 1_000) return `$${(n / 1_000).toFixed(1)}K`
  return `$${n.toFixed(2)}`
}

// Sleek card wrapper
function Card({ children, className = '', glow, ...props }: { children: React.ReactNode; className?: string; glow?: string; [key: string]: any }) {
  return (
    <div
      className={`rounded-2xl border backdrop-blur-sm transition-all duration-300 ${className}`}
      style={{
        backgroundColor: 'var(--bg-secondary)',
        borderColor: 'var(--border-color)',
        boxShadow: glow || 'var(--card-shadow)',
        ...props.style,
      }}
      {...(({ style, ...rest }) => rest)(props)}
    >
      {children}
    </div>
  )
}

// Glass stat box
function StatBox({ label, value, sub, color = 'var(--text-primary)', icon }: { label: string; value: string; sub?: string; color?: string; icon?: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5 p-4 rounded-xl" style={{ backgroundColor: 'var(--hover-bg)' }}>
      <div className="flex items-center gap-2">
        {icon && <span style={{ color }}>{icon}</span>}
        <span className="text-[11px] font-medium uppercase tracking-wider" style={{ color: 'var(--text-tertiary)' }}>{label}</span>
      </div>
      <span className="text-xl font-bold font-mono" style={{ color }}>{value}</span>
      {sub && <span className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>{sub}</span>}
    </div>
  )
}

export default function TreasuryPage() {
  const { user } = userContext()
  const [activeTab, setActiveTab] = useState<Tab>('overview')
  const [treasury] = useState(() => new Treasury())

  // ── State ──
  const [loading, setLoading] = useState(true)
  const [tokenBalances, setTokenBalances] = useState<TokenBalance[]>([])
  const [treasuryInfo, setTreasuryInfo] = useState<TreasuryInfo | null>(null)
  const [governanceToken, setGovernanceToken] = useState('')
  const [tokenGate, setTokenGate] = useState('')
  const [ownerAddress, setOwnerAddress] = useState('')
  const [lastUpdate, setLastUpdate] = useState<Date>(new Date())

  const [modTokenBalance, setModTokenBalance] = useState<string>('0')
  const [modTokenSupply, setModTokenSupply] = useState<string>('0')
  const [modTokenSymbol, setModTokenSymbol] = useState<string>('MOD')
  const [modTokenDecimals, setModTokenDecimals] = useState<number>(18)

  const [userNatBalance, setUserNatBalance] = useState<string>('0')
  const [userBlocTimeBalance, setUserBlocTimeBalance] = useState<string>('0')

  const [marketUsdcBalance, setMarketUsdcBalance] = useState<string>('0')
  const [marketUsdtBalance, setMarketUsdtBalance] = useState<string>('0')
  const [marketTotalFeesAccrued, setMarketTotalFeesAccrued] = useState<string>('0')
  const [marketUnclaimedFees, setMarketUnclaimedFees] = useState<string>('0')

  const [isOwner, setIsOwner] = useState(false)
  const [isSafeSigner, setIsSafeSigner] = useState(false)
  const [isSafeOwned, setIsSafeOwned] = useState(false)
  const [safeOwners, setSafeOwners] = useState<string[]>([])
  const [safeThreshold, setSafeThreshold] = useState(0)

  const [holderInfo, setHolderInfo] = useState<HolderInfo | null>(null)
  const [actualTokenBalances, setActualTokenBalances] = useState<Map<string, bigint>>(new Map())

  const [deposits, setDeposits] = useState<DepositEvent[]>([])
  const [depositsLoading, setDepositsLoading] = useState(false)
  const [claims, setClaims] = useState<ClaimEvent[]>([])
  const [claimsLoading, setClaimsLoading] = useState(false)

  // ── BlocTime state ──
  const [blocTimeBalance, setBlocTimeBalance] = useState<string>('0')
  const [blocTimeTotalSupply, setBlocTimeTotalSupply] = useState<string>('0')
  const [stakeAmount, setStakeAmount] = useState('')
  const [lockBlocks, setLockBlocks] = useState('')
  const [userStakes, setUserStakes] = useState<any[]>([])
  const [maxLockBlocks, setMaxLockBlocks] = useState(0)
  const [blocTimeLoading, setBlocTimeLoading] = useState(false)
  const [showBlocTimeDetails, setShowBlocTimeDetails] = useState(false)

  // ── Admin form state ──
  const [adminLoading, setAdminLoading] = useState<string | null>(null)
  const [newOwnerPct, setNewOwnerPct] = useState('')
  const [newGovToken, setNewGovToken] = useState('')
  const [newTokenGate, setNewTokenGate] = useState('')
  const [newOwner, setNewOwner] = useState('')
  const [emergencyToken, setEmergencyToken] = useState('')
  const [emergencyAmount, setEmergencyAmount] = useState('')
  const [fundToken, setFundToken] = useState('')
  const [fundAmount, setFundAmount] = useState('')

  const walletAddress = user?.key || ''

  // ── Fetch treasury data ──
  const fetchData = useCallback(async () => {
    if (typeof window === 'undefined' || !window.ethereum) return

    try {
      setLoading(true)
      const provider = new ethers.BrowserProvider(window.ethereum)

      const [info, owner, govToken, tGate, balances] = await Promise.all([
        treasury.getTreasuryInfo(),
        treasury.getOwner(),
        treasury.getGovernanceToken(),
        treasury.getTokenGate(),
        treasury.getTokenBalances(),
      ])

      setTreasuryInfo(info)
      setOwnerAddress(owner)
      setGovernanceToken(govToken)
      setTokenGate(tGate)
      setTokenBalances(balances)
      setLastUpdate(new Date())

      try {
        const nativeAddr = getChainConfig()?.contracts?.NativeToken?.address
        if (nativeAddr) {
          const tokenContract = new ethers.Contract(nativeAddr, TokenABI.abi, provider)
          const [bal, supply, sym, dec] = await Promise.all([
            tokenContract.balanceOf(treasury.address),
            tokenContract.totalSupply(),
            tokenContract.symbol(),
            tokenContract.decimals(),
          ])
          const decimals = Number(dec)
          setModTokenDecimals(decimals)
          setModTokenBalance(ethers.formatUnits(bal, decimals))
          setModTokenSupply(ethers.formatUnits(supply, decimals))
          setModTokenSymbol(sym)

          if (walletAddress) {
            const userBal = await tokenContract.balanceOf(walletAddress)
            setUserNatBalance(ethers.formatUnits(userBal, decimals))
          }
        }
      } catch (err) {
        console.warn('Could not fetch MOD token info:', err)
      }

      try {
        const blocTimeAddr = getChainConfig()?.contracts?.BlocTime?.address
        if (blocTimeAddr && walletAddress) {
          const blocTimeContract = new ethers.Contract(blocTimeAddr, BlocTimeABI.abi, provider)
          const balance = await blocTimeContract.balanceOf(walletAddress)
          setUserBlocTimeBalance(ethers.formatUnits(balance, 18))
        }
      } catch (err) {
        console.warn('Could not fetch BlocTime balance:', err)
      }

      try {
        const chainConfig = getChainConfig()
        const marketAddr = chainConfig?.contracts?.Market?.address
        const usdcAddr = chainConfig?.contracts?.USDC?.address
        const usdtAddr = chainConfig?.contracts?.USDT?.address
        if (marketAddr) {
          const marketContract = new ethers.Contract(marketAddr, MarketABI.abi, provider)

          if (usdcAddr) {
            const usdcContract = new ethers.Contract(usdcAddr, TokenABI.abi, provider)
            const usdcBal = await usdcContract.balanceOf(marketAddr)
            const usdcDec = await usdcContract.decimals()
            setMarketUsdcBalance(ethers.formatUnits(usdcBal, Number(usdcDec)))
          }
          if (usdtAddr) {
            const usdtContract = new ethers.Contract(usdtAddr, TokenABI.abi, provider)
            const usdtBal = await usdtContract.balanceOf(marketAddr)
            const usdtDec = await usdtContract.decimals()
            setMarketUsdtBalance(ethers.formatUnits(usdtBal, Number(usdtDec)))
          }

          const [totalFees, unclaimedFees] = await Promise.all([
            marketContract.totalTreasuryFeesAccrued(),
            marketContract.getUnclaimedTreasuryFFeesUSD(),
          ])
          setMarketTotalFeesAccrued(ethers.formatUnits(totalFees, 8))
          setMarketUnclaimedFees(ethers.formatUnits(unclaimedFees, 8))
        }
      } catch (err) {
        console.warn('Could not fetch Market balances:', err)
      }

      if (walletAddress) {
        const isDirectOwner = owner.toLowerCase() === walletAddress.toLowerCase()
        setIsOwner(isDirectOwner)

        if (!isDirectOwner) {
          const safeCheck = await isSafeContract(owner, provider)
          setIsSafeOwned(safeCheck)
          if (safeCheck) {
            const [owners, threshold, userIsSigner] = await Promise.all([
              getSafeOwners(owner, provider),
              getSafeThreshold(owner, provider),
              isUserSafeOwner(owner, walletAddress, provider),
            ])
            setSafeOwners(owners)
            setSafeThreshold(threshold)
            setIsSafeSigner(userIsSigner)
          }
        } else {
          const safeCheck = await isSafeContract(owner, provider)
          setIsSafeOwned(safeCheck)
          if (safeCheck) {
            const [owners, threshold] = await Promise.all([
              getSafeOwners(owner, provider),
              getSafeThreshold(owner, provider),
            ])
            setSafeOwners(owners)
            setSafeThreshold(threshold)
          }
        }

        try {
          const hInfo = await treasury.getHolderInfo(walletAddress)
          setHolderInfo(hInfo)

          const balancesMap = new Map<string, bigint>()
          for (const tokenAddr of hInfo.tokens) {
            try {
              const tokenContract = new ethers.Contract(tokenAddr, TokenABI.abi, provider)
              const balance = await tokenContract.balanceOf(treasury.address)
              balancesMap.set(tokenAddr.toLowerCase(), balance)
            } catch (err) {
              console.warn(`Could not fetch balance for token ${tokenAddr}:`, err)
              balancesMap.set(tokenAddr.toLowerCase(), BigInt(0))
            }
          }
          setActualTokenBalances(balancesMap)
        } catch {
          setHolderInfo(null)
        }
      }
    } catch (err) {
      console.error('Error fetching treasury:', err)
    } finally {
      setLoading(false)
    }
  }, [walletAddress, treasury])

  useEffect(() => {
    fetchData()
    const iv = setInterval(fetchData, 30000)
    return () => clearInterval(iv)
  }, [fetchData])

  const fetchDeposits = useCallback(async () => {
    if (typeof window === 'undefined' || !window.ethereum) return
    try {
      setDepositsLoading(true)
      const provider = new ethers.BrowserProvider(window.ethereum)
      const TreasuryABI = ['event TreasuryFunded(address indexed funder, address indexed token, uint256 amount)']
      const contract = new ethers.Contract(treasury.address, TreasuryABI, provider)
      const currentBlock = await provider.getBlockNumber()
      const fromBlock = Math.max(0, currentBlock - 10000)
      const events = await contract.queryFilter(contract.filters.TreasuryFunded(), fromBlock, currentBlock)
      const parsed = events.filter((e): e is EventLog => 'args' in e)
      const items: DepositEvent[] = await Promise.all(
        parsed.map(async (ev) => {
          const block = await ev.getBlock()
          let sym = 'UNKNOWN'; let dec = 18
          try {
            const tc = new ethers.Contract(ev.args.token, ['function symbol() view returns (string)', 'function decimals() view returns (uint8)'], provider)
            sym = await tc.symbol(); dec = await tc.decimals()
          } catch {}
          return {
            funder: ev.args.funder, token: sym,
            amount: ethers.formatUnits(ev.args.amount, dec),
            timestamp: block.timestamp, txHash: ev.transactionHash, blockNumber: ev.blockNumber,
          }
        })
      )
      items.sort((a, b) => b.timestamp - a.timestamp)
      setDeposits(items)
    } catch (err) {
      console.error('Error fetching deposits:', err)
    } finally {
      setDepositsLoading(false)
    }
  }, [treasury])

  const fetchClaims = useCallback(async () => {
    if (typeof window === 'undefined' || !window.ethereum) return
    try {
      setClaimsLoading(true)
      const provider = new ethers.BrowserProvider(window.ethereum)
      const TreasuryABI = ['event TokensWithdrawn(address indexed holder, address indexed token, uint256 amount)']
      const contract = new ethers.Contract(treasury.address, TreasuryABI, provider)
      const currentBlock = await provider.getBlockNumber()
      const fromBlock = Math.max(0, currentBlock - 10000)
      const events = await contract.queryFilter(contract.filters.TokensWithdrawn(), fromBlock, currentBlock)
      const parsed = events.filter((e): e is EventLog => 'args' in e)
      const items: ClaimEvent[] = await Promise.all(
        parsed.map(async (ev) => {
          const block = await ev.getBlock()
          let sym = 'UNKNOWN'; let dec = 18
          try {
            const tc = new ethers.Contract(ev.args.token, ['function symbol() view returns (string)', 'function decimals() view returns (uint8)'], provider)
            sym = await tc.symbol(); dec = await tc.decimals()
          } catch {}
          return {
            holder: ev.args.holder, token: sym,
            amount: ethers.formatUnits(ev.args.amount, dec),
            timestamp: block.timestamp, txHash: ev.transactionHash, blockNumber: ev.blockNumber,
          }
        })
      )
      items.sort((a, b) => b.timestamp - a.timestamp)
      setClaims(items)
    } catch (err) {
      console.error('Error fetching claims:', err)
    } finally {
      setClaimsLoading(false)
    }
  }, [treasury])

  useEffect(() => {
    if (activeTab === 'member' && walletAddress) {
      fetchDeposits()
      fetchClaims()
    }
  }, [activeTab, walletAddress, fetchDeposits, fetchClaims])

  const fetchBlocTimeData = useCallback(async () => {
    if (typeof window === 'undefined' || !window.ethereum || !walletAddress) return
    try {
      setBlocTimeLoading(true)
      const provider = new ethers.BrowserProvider(window.ethereum)
      const blocTimeAddr = getChainConfig()?.contracts?.BlocTime?.address
      const nativeAddr = getChainConfig()?.contracts?.NativeToken?.address

      if (!blocTimeAddr) return

      const blocTimeContract = new ethers.Contract(blocTimeAddr, BlocTimeABI.abi, provider)

      const [balance, totalSupply, params] = await Promise.all([
        blocTimeContract.balanceOf(walletAddress),
        blocTimeContract.totalSupply(),
        blocTimeContract.params(),
      ])

      setBlocTimeBalance(ethers.formatUnits(balance, 18))
      setBlocTimeTotalSupply(ethers.formatUnits(totalSupply, 18))
      setMaxLockBlocks(Number(params.maxLockBlocks))

      const stakeIds = await blocTimeContract.getUserStakeIds(walletAddress)
      const stakes = await Promise.all(
        stakeIds.map(async (id: bigint) => {
          const stake = await blocTimeContract.getStakePosition(walletAddress, id)
          return {
            id: id.toString(),
            amount: stake.amount,
            startBlock: stake.startBlock,
            lockBlocks: stake.lockBlocks,
            blocTimeBalance: stake.blocTimeBalance,
            blocksRemaining: stake.blocksRemaining,
          }
        })
      )
      setUserStakes(stakes)
    } catch (err) {
      console.error('Error fetching BlocTime data:', err)
    } finally {
      setBlocTimeLoading(false)
    }
  }, [walletAddress])

  useEffect(() => {
    if ((showBlocTimeDetails || activeTab === 'member') && walletAddress) fetchBlocTimeData()
  }, [showBlocTimeDetails, activeTab, walletAddress, fetchBlocTimeData])

  const canAdmin = isOwner || isSafeSigner
  const totalBalance = tokenBalances.reduce((s, b) => s + b.balance, 0)
  const ownerPctDisplay = treasuryInfo ? (Number(treasuryInfo.ownerPct) / 100).toFixed(2) : '0'

  async function execAdminAction(label: string, fn: () => Promise<any>) {
    if (!walletAddress) { toast.error('Connect wallet first'); return }
    setAdminLoading(label)
    try {
      await fn()
      toast.success(`${label} successful`)
      fetchData()
    } catch (err: any) {
      console.error(err)
      toast.error(err?.message || `${label} failed`)
    } finally {
      setAdminLoading(null)
    }
  }

  async function handleSafePropose(fnName: string, args: any[], label: string) {
    if (!walletAddress) { toast.error('Connect wallet first'); return }
    setAdminLoading(label)
    try {
      if (!window.ethereum) throw new Error('No wallet connected')
      const provider = new ethers.BrowserProvider(window.ethereum)
      const signer = await provider.getSigner(walletAddress)
      const data = treasury.encodeFunctionData(fnName, args)
      const network = await provider.getNetwork()
      const { safeTxHash } = await proposeSafeTransaction(ownerAddress, treasury.address, data, signer, network.chainId)
      toast.success(`Safe transaction proposed: ${safeTxHash.slice(0, 10)}...`)
      fetchData()
    } catch (err: any) {
      console.error(err)
      toast.error(err?.message || `${label} failed`)
    } finally {
      setAdminLoading(null)
    }
  }

  function handleAdminTx(fnName: string, args: any[], label: string, directFn: () => Promise<any>) {
    if (isSafeSigner && !isOwner) {
      handleSafePropose(fnName, args, label)
    } else {
      execAdminAction(label, directFn)
    }
  }

  async function handleWithdrawToken(tokenAddr: string, symbol: string) {
    if (!holderInfo) { toast.error('Holder info not loaded'); return }

    const actualBalance = actualTokenBalances.get(tokenAddr.toLowerCase()) || BigInt(0)
    if (actualBalance === BigInt(0)) { toast.error(`Treasury has no ${symbol} balance available`); return }

    const tokenIndex = holderInfo.tokens.findIndex(t => t.toLowerCase() === tokenAddr.toLowerCase())
    if (tokenIndex === -1) { toast.error('Token not found in treasury'); return }

    const contractClaimable = holderInfo.claimableAmounts[tokenIndex]

    if (contractClaimable > actualBalance) {
      const bal = tokenBalances.find(b => b.address.toLowerCase() === tokenAddr.toLowerCase())
      const contractAmount = parseFloat(ethers.formatUnits(contractClaimable, bal?.decimals || 18))
      const availableAmount = parseFloat(ethers.formatUnits(actualBalance, bal?.decimals || 18))

      toast.error(
        `Cannot claim: Contract calculated $${contractAmount.toFixed(2)} but treasury only has $${availableAmount.toFixed(2)}. ` +
        `This is a contract issue - wait for more deposits or other users to claim their historical shares.`,
        { autoClose: 8000 }
      )
      return
    }

    execAdminAction(`Withdraw ${symbol}`, () => treasury.withdrawToken(walletAddress, tokenAddr))
  }

  async function handleWithdrawAll() {
    execAdminAction('Withdraw All', () => treasury.withdrawAll(walletAddress))
  }

  async function handleFundTreasury() {
    if (!fundToken || !fundAmount) { toast.error('Enter token and amount'); return }
    let tokenAddr = getTokenAddressFromSymbol(fundToken)

    if (fundToken === 'NativeToken') {
      tokenAddr = getChainConfig()?.contracts?.NativeToken?.address
    }

    if (!tokenAddr) { toast.error('Unknown token'); return }

    let decimals = tokenBalances.find(t => t.symbol === fundToken)?.decimals
    if (!decimals) decimals = 18

    const amount = ethers.parseUnits(fundAmount, decimals)

    execAdminAction('Fund Treasury', async () => {
      if (!window.ethereum) throw new Error('No wallet connected')
      const provider = new ethers.BrowserProvider(window.ethereum)
      const signer = await provider.getSigner(walletAddress)
      const tokenContract = new ethers.Contract(tokenAddr, [
        'function approve(address,uint256) returns (bool)',
      ], signer)
      const approveTx = await tokenContract.approve(treasury.address, amount)
      await approveTx.wait()
      await treasury.fundTreasury(walletAddress, tokenAddr, amount)
    })
  }

  function getTokenAddressFromSymbol(symbol: string): string | null {
    const chainConfig = getChainConfig()
    if (!chainConfig) return null
    for (const [, val] of Object.entries(chainConfig.contracts)) {
      const v = val as any
      if (v.address) {
        const match = tokenBalances.find(t => t.address.toLowerCase() === v.address.toLowerCase() && t.symbol === symbol)
        if (match) return match.address
      }
    }
    return tokenBalances.find(t => t.symbol === symbol)?.address || null
  }

  async function handleStakeToBlocTime() {
    if (!stakeAmount || !lockBlocks) { toast.error('Enter amount and lock blocks'); return }
    if (!walletAddress) { toast.error('Connect wallet first'); return }

    execAdminAction('Stake to BlocTime', async () => {
      if (!window.ethereum) throw new Error('No wallet connected')
      const provider = new ethers.BrowserProvider(window.ethereum)
      const signer = await provider.getSigner(walletAddress)

      const blocTimeAddr = getChainConfig()?.contracts?.BlocTime?.address
      const nativeAddr = getChainConfig()?.contracts?.NativeToken?.address

      if (!blocTimeAddr || !nativeAddr) throw new Error('BlocTime or NativeToken address not found')

      const amount = ethers.parseUnits(stakeAmount, modTokenDecimals)
      const tokenContract = new ethers.Contract(nativeAddr, TokenABI.abi, signer)
      const blocTimeContract = new ethers.Contract(blocTimeAddr, BlocTimeABI.abi, signer)

      const approveTx = await tokenContract.approve(blocTimeAddr, amount)
      await approveTx.wait()

      const stakeTx = await blocTimeContract.stake(amount, lockBlocks)
      await stakeTx.wait()

      fetchBlocTimeData()
      setStakeAmount('')
      setLockBlocks('')
    })
  }

  async function handleUnstake(stakeId: string) {
    if (!walletAddress) { toast.error('Connect wallet first'); return }

    execAdminAction(`Unstake ${stakeId}`, async () => {
      if (!window.ethereum) throw new Error('No wallet connected')
      const provider = new ethers.BrowserProvider(window.ethereum)
      const signer = await provider.getSigner(walletAddress)

      const blocTimeAddr = getChainConfig()?.contracts?.BlocTime?.address
      if (!blocTimeAddr) throw new Error('BlocTime address not found')

      const blocTimeContract = new ethers.Contract(blocTimeAddr, BlocTimeABI.abi, signer)
      const unstakeTx = await blocTimeContract.unstake(stakeId)
      await unstakeTx.wait()

      fetchBlocTimeData()
    })
  }

  const tabs: { key: Tab; label: string; icon: React.ReactNode; show: boolean }[] = [
    { key: 'overview', label: 'Overview', icon: <CubeTransparentIcon className="w-4 h-4" />, show: true },
    { key: 'member', label: 'Member', icon: <WalletIcon className="w-4 h-4" />, show: !!walletAddress },
    { key: 'owner', label: 'Owner', icon: <ShieldCheckIcon className="w-4 h-4" />, show: canAdmin },
  ]

  if (!treasury.address) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: 'var(--bg-primary)' }}>
        <div className="text-center space-y-3">
          <BuildingLibraryIcon className="w-12 h-12 mx-auto text-purple-500/40" />
          <div className="text-purple-400 text-lg font-medium">Treasury not configured</div>
        </div>
      </div>
    )
  }

  const inputStyle = {
    backgroundColor: 'var(--bg-input)',
    border: '1px solid var(--border-color)',
    color: 'var(--text-primary)',
  } as const

  return (
    <div style={{ backgroundColor: 'var(--bg-primary)', color: 'var(--text-primary)' }}>
      <div className="relative z-10 p-4 md:p-6 max-w-5xl mx-auto space-y-4">

        {/* ── Header ── */}
        <motion.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex items-center justify-between"
        >
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl flex items-center justify-center bg-gradient-to-br from-purple-500/20 to-violet-600/20 border border-purple-500/30">
              <BuildingLibraryIcon className="w-5 h-5 text-purple-400" />
            </div>
            <div>
              <h1 className="text-2xl font-bold tracking-tight" style={{ color: 'var(--text-primary)' }}>Treasury</h1>
              <div className="flex items-center gap-2 mt-0.5">
                <span className="font-mono text-xs" style={{ color: 'var(--text-tertiary)' }}>
                  {treasury.address.slice(0, 6)}...{treasury.address.slice(-4)}
                </span>
                <CopyButton text={treasury.address} size="sm" />
                {isSafeOwned && (
                  <span className="inline-flex items-center gap-1 px-2 py-0.5 text-[10px] font-semibold rounded-full bg-emerald-500/10 border border-emerald-500/30 text-emerald-400">
                    <ShieldCheckIcon className="w-3 h-3" /> Multisig
                  </span>
                )}
              </div>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <span className="text-[10px] font-mono" style={{ color: 'var(--text-tertiary)' }}>
              {lastUpdate.toLocaleTimeString()}
            </span>
            <button
              onClick={() => fetchData()}
              disabled={loading}
              className="p-2.5 rounded-xl border hover:border-purple-500/40 hover:bg-purple-500/5 transition-all duration-200"
              style={{ borderColor: 'var(--border-color)' }}
            >
              <ArrowPathIcon className={`w-4 h-4 ${loading ? 'animate-spin text-purple-400' : ''}`} style={loading ? {} : { color: 'var(--text-tertiary)' }} />
            </button>
          </div>
        </motion.div>

        {/* ── Tabs ── */}
        <div className="flex items-center gap-1 p-1 rounded-xl" style={{ backgroundColor: 'var(--hover-bg)' }}>
          {tabs.filter(t => t.show).map(tab => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`relative flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-lg transition-all duration-200 ${
                activeTab === tab.key ? 'text-purple-400' : ''
              }`}
              style={activeTab !== tab.key ? { color: 'var(--text-tertiary)' } : {}}
            >
              {activeTab === tab.key && (
                <motion.div
                  layoutId="treasuryActiveTab"
                  className="absolute inset-0 rounded-lg border border-purple-500/30"
                  style={{ backgroundColor: 'var(--bg-secondary)', boxShadow: '0 0 20px rgba(168, 85, 247, 0.1)' }}
                  transition={{ type: 'spring', stiffness: 400, damping: 30 }}
                />
              )}
              <span className="relative z-10 flex items-center gap-2">
                {tab.icon}
                {tab.label}
              </span>
            </button>
          ))}
        </div>

        {/* ── Tab Content ── */}
        <AnimatePresence mode="wait">
          {activeTab === 'overview' && (
            <motion.div
              key="overview"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.2 }}
              className="space-y-3"
            >
              {/* ── Hero Stats ── */}
              <Card className="p-5 overflow-hidden relative">
                <div className="absolute inset-0 bg-gradient-to-br from-purple-500/5 via-transparent to-violet-500/5" />
                <div className="relative z-10">
                  {(() => {
                    const COLORS = ['#a855f7', '#3b82f6', '#22c55e', '#f59e0b', '#ec4899']
                    const pieData = tokenBalances
                      .filter(tb => tb.balance > 0)
                      .map(tb => ({ name: tb.symbol, value: tb.balance }))
                    const modBal = parseFloat(modTokenBalance)
                    if (modBal > 0) pieData.push({ name: modTokenSymbol, value: modBal })
                    const hasPieData = pieData.length > 0 && !loading

                    return (
                      <div className="flex items-center gap-6">
                        {/* Pie chart - only show when there's data */}
                        {hasPieData && (
                          <div className="w-28 h-28 flex-shrink-0">
                            <ResponsiveContainer width="100%" height="100%">
                              <PieChart>
                                <Pie
                                  data={pieData}
                                  cx="50%"
                                  cy="50%"
                                  innerRadius={32}
                                  outerRadius={52}
                                  dataKey="value"
                                  strokeWidth={2}
                                  stroke="var(--bg-primary)"
                                >
                                  {pieData.map((_, idx) => (
                                    <Cell key={idx} fill={COLORS[idx % COLORS.length]} />
                                  ))}
                                </Pie>
                                <Tooltip
                                  contentStyle={{
                                    backgroundColor: 'var(--bg-secondary)',
                                    border: '1px solid var(--border-color)',
                                    borderRadius: '12px',
                                    color: 'var(--text-primary)',
                                    fontSize: '12px',
                                    boxShadow: 'var(--card-shadow)',
                                  }}
                                  formatter={(value: any) => [`$${parseFloat(value).toFixed(2)}`, '']}
                                />
                              </PieChart>
                            </ResponsiveContainer>
                          </div>
                        )}

                        <div className="flex-1 min-w-0">
                          {/* Value + Pending Fees row */}
                          <div className="flex items-center justify-between mb-3">
                            <div>
                              <span className="text-[10px] font-medium uppercase tracking-wider block mb-1" style={{ color: 'var(--text-tertiary)' }}>Total Treasury Value</span>
                              <span className="text-3xl font-bold tracking-tight" style={{ color: 'var(--text-primary)' }}>
                                {loading ? (
                                  <span className="inline-block w-28 h-8 rounded-lg animate-pulse" style={{ backgroundColor: 'var(--hover-bg)' }} />
                                ) : fmt(totalBalance)}
                              </span>
                            </div>
                            {parseFloat(marketUnclaimedFees) > 0 && (
                              <div className="text-right px-3 py-2 rounded-lg bg-emerald-500/10 border border-emerald-500/20">
                                <span className="text-[9px] block mb-0.5 uppercase font-medium tracking-wider text-emerald-400/70">Pending Fees</span>
                                <span className="text-lg font-bold text-emerald-400">{fmt(parseFloat(marketUnclaimedFees))}</span>
                              </div>
                            )}
                          </div>

                          {/* Token legend */}
                          {hasPieData && (
                            <div className="space-y-0.5">
                              {pieData.map((item, idx) => {
                                const pct = totalBalance > 0 ? ((item.value / totalBalance) * 100).toFixed(1) : '0'
                                return (
                                  <div key={item.name} className="flex items-center justify-between py-1.5 px-2 rounded-lg hover:bg-purple-500/5 transition-all duration-200">
                                    <div className="flex items-center gap-2">
                                      <div className="w-2 h-2 rounded-full" style={{ backgroundColor: COLORS[idx % COLORS.length] }} />
                                      <span className="text-sm font-medium" style={{ color: 'var(--text-secondary)' }}>{item.name}</span>
                                      <span className="text-[10px] px-1.5 py-0.5 rounded-full font-medium" style={{ backgroundColor: 'var(--hover-bg)', color: 'var(--text-tertiary)' }}>{pct}%</span>
                                    </div>
                                    <span className="font-mono text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>{fmt(item.value)}</span>
                                  </div>
                                )
                              })}
                            </div>
                          )}
                        </div>
                      </div>
                    )
                  })()}
                </div>
              </Card>

              {/* ── Contract Addresses ── */}
              <div className="grid grid-cols-3 gap-2">
                {[
                  { label: 'Treasury', addr: treasury.address, color: '#a855f7' },
                  { label: 'NAT Token', addr: getChainConfig()?.contracts?.NativeToken?.address || '', color: '#3b82f6' },
                  { label: 'BlocTime', addr: getChainConfig()?.contracts?.BlocTime?.address || '', color: '#06b6d4' },
                ].map(({ label, addr, color }) => (
                  <Card key={label} className="px-3 py-2.5 group hover:border-opacity-50 transition-all duration-200" style={{ borderColor: `${color}20` }}>
                    <div className="flex items-center justify-between">
                      <div>
                        <div className="text-[9px] mb-1 uppercase font-medium tracking-wider" style={{ color: 'var(--text-tertiary)' }}>{label}</div>
                        <div className="flex items-center gap-1.5">
                          <div className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: color }} />
                          <span className="font-mono text-xs" style={{ color: 'var(--text-primary)' }}>
                            {addr ? `${addr.slice(0, 6)}...${addr.slice(-4)}` : 'N/A'}
                          </span>
                        </div>
                      </div>
                      {addr && <CopyButton text={addr} size="sm" />}
                    </div>
                  </Card>
                ))}
              </div>

              {/* ── Safe Info ── */}
              {isSafeOwned && (
                <Card className="p-4 overflow-hidden relative" style={{ borderColor: 'rgba(34, 197, 94, 0.15)' }}>
                  <div className="absolute inset-0 bg-gradient-to-br from-emerald-500/5 via-transparent to-green-500/5" />
                  <div className="relative z-10">
                    <div className="flex items-center justify-between mb-3">
                      <div className="flex items-center gap-2">
                        <ShieldCheckIcon className="w-5 h-5 text-emerald-400" />
                        <span className="text-sm font-semibold text-emerald-400">Multisig Treasury</span>
                      </div>
                      <div className="px-3 py-1.5 rounded-full bg-emerald-500/10 border border-emerald-500/20">
                        <span className="text-xs font-semibold text-emerald-400">
                          {safeThreshold} of {safeOwners.length} required
                        </span>
                      </div>
                    </div>
                    <div className="space-y-1.5">
                      {safeOwners.map(addr => {
                        const isYou = addr.toLowerCase() === walletAddress.toLowerCase()
                        return (
                          <div key={addr} className={`flex items-center gap-3 p-3 rounded-xl transition-all ${isYou ? 'bg-emerald-500/10 border border-emerald-500/20' : ''}`}>
                            <div className={`w-2 h-2 rounded-full ${isYou ? 'bg-emerald-400 shadow-lg shadow-emerald-500/50' : 'bg-gray-500/30'}`} />
                            <span className="font-mono text-xs flex-1" style={{ color: 'var(--text-secondary)' }}>{addr.slice(0, 6)}...{addr.slice(-4)}</span>
                            {isYou && (
                              <span className="text-emerald-400 text-[10px] font-semibold px-2 py-0.5 rounded-full bg-emerald-500/15 border border-emerald-500/25">You</span>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  </div>
                </Card>
              )}
            </motion.div>
          )}

          {/* ── Member Tab ── */}
          {activeTab === 'member' && walletAddress && (
            <motion.div
              key="member"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.2 }}
              className="space-y-3"
            >
              {/* Token Balances */}
              <Card className="p-4 overflow-hidden relative">
                <div className="absolute inset-0 bg-gradient-to-br from-blue-500/5 via-transparent to-purple-500/5" />
                <div className="relative z-10">
                  <div className="flex items-center justify-between mb-4">
                    <span className="text-xs font-medium uppercase tracking-wider" style={{ color: 'var(--text-tertiary)' }}>
                      {governanceToken !== ethers.ZeroAddress ? 'Governance Tokens' : 'Tokens'}
                    </span>
                    <button
                      onClick={() => setShowBlocTimeDetails(!showBlocTimeDetails)}
                      className="flex items-center gap-1.5 text-xs px-3.5 py-2 rounded-lg border transition-all duration-200 font-medium hover:border-purple-500/40 hover:text-purple-400"
                      style={{ borderColor: 'var(--border-color)', color: 'var(--text-secondary)' }}
                    >
                      <SparklesIcon className="w-3.5 h-3.5" />
                      {showBlocTimeDetails ? 'Hide Staking' : 'Stake NAT'}
                      <ChevronDownIcon className={`w-3 h-3 transition-transform duration-200 ${showBlocTimeDetails ? 'rotate-180' : ''}`} />
                    </button>
                  </div>

                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    {/* NAT Balance */}
                    <div className="p-4 rounded-xl" style={{ backgroundColor: 'var(--hover-bg)' }}>
                      <div className="flex items-center gap-2 mb-3">
                        <div className="w-8 h-8 rounded-lg bg-blue-500/15 flex items-center justify-center">
                          <BanknotesIcon className="w-4 h-4 text-blue-400" />
                        </div>
                        <h3 className="text-lg font-bold" style={{ color: 'var(--text-primary)' }}>{modTokenSymbol}</h3>
                      </div>
                      <div className="space-y-3">
                        <div className="flex items-center justify-between">
                          <span className="text-xs font-medium" style={{ color: 'var(--text-tertiary)' }}>Your Balance</span>
                          <span className="font-mono text-xl font-bold" style={{ color: 'var(--text-primary)' }}>
                            {parseFloat(userNatBalance).toLocaleString()}
                          </span>
                        </div>
                        <div className="flex items-center justify-between">
                          <span className="text-xs font-medium" style={{ color: 'var(--text-tertiary)' }}>Total Supply</span>
                          <span className="font-mono text-sm" style={{ color: 'var(--text-secondary)' }}>
                            {parseFloat(modTokenSupply).toLocaleString()}
                          </span>
                        </div>
                        {modTokenSupply !== '0' && userNatBalance !== '0' && (
                          <div className="flex items-center justify-between pt-3 border-t" style={{ borderColor: 'var(--border-color)' }}>
                            <span className="text-xs font-medium" style={{ color: 'var(--text-tertiary)' }}>% of Supply</span>
                            <span className="font-mono text-sm font-bold text-blue-400">
                              {((parseFloat(userNatBalance) / parseFloat(modTokenSupply)) * 100).toFixed(2)}%
                            </span>
                          </div>
                        )}
                      </div>
                    </div>

                    {/* BlocTime Balance */}
                    <div className="p-4 rounded-xl" style={{ backgroundColor: 'var(--hover-bg)' }}>
                      <div className="flex items-center gap-2 mb-3">
                        <div className="w-8 h-8 rounded-lg bg-cyan-500/15 flex items-center justify-center">
                          <LockClosedIcon className="w-4 h-4 text-cyan-400" />
                        </div>
                        <h3 className="text-lg font-bold" style={{ color: 'var(--text-primary)' }}>BTime</h3>
                      </div>
                      <div className="space-y-3">
                        <div className="flex items-center justify-between">
                          <span className="text-xs font-medium" style={{ color: 'var(--text-tertiary)' }}>Your Balance</span>
                          <span className="font-mono text-xl font-bold" style={{ color: 'var(--text-primary)' }}>
                            {parseFloat(userBlocTimeBalance).toLocaleString()}
                          </span>
                        </div>
                        <div className="flex items-center justify-between">
                          <span className="text-xs font-medium" style={{ color: 'var(--text-tertiary)' }}>Total Supply</span>
                          <span className="font-mono text-sm" style={{ color: 'var(--text-secondary)' }}>
                            {blocTimeTotalSupply !== '0' ? parseFloat(blocTimeTotalSupply).toLocaleString() : '...'}
                          </span>
                        </div>
                        {blocTimeTotalSupply !== '0' && userBlocTimeBalance !== '0' && (
                          <>
                            <div className="flex items-center justify-between pt-3 border-t" style={{ borderColor: 'var(--border-color)' }}>
                              <span className="text-xs font-medium" style={{ color: 'var(--text-tertiary)' }}>Treasury Claim</span>
                              <span className="font-mono text-sm font-bold text-emerald-400">
                                {((parseFloat(userBlocTimeBalance) / parseFloat(blocTimeTotalSupply)) * 100).toFixed(2)}%
                              </span>
                            </div>
                            <div className="pt-1">
                              <div className="w-full h-1.5 rounded-full overflow-hidden" style={{ backgroundColor: 'var(--bg-input)' }}>
                                <motion.div
                                  className="h-full rounded-full bg-gradient-to-r from-emerald-500 to-cyan-400"
                                  initial={{ width: 0 }}
                                  animate={{ width: `${Math.min(100, (parseFloat(userBlocTimeBalance) / parseFloat(blocTimeTotalSupply)) * 100)}%` }}
                                  transition={{ duration: 1, ease: 'easeOut' }}
                                />
                              </div>
                              <div className="flex items-center justify-between mt-1.5">
                                <span className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>Your share</span>
                                <span className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                                  {((parseFloat(blocTimeTotalSupply) - parseFloat(userBlocTimeBalance)) / parseFloat(blocTimeTotalSupply) * 100).toFixed(1)}% others
                                </span>
                              </div>
                            </div>
                          </>
                        )}
                      </div>
                    </div>
                  </div>

                  {/* Expandable Staking */}
                  <AnimatePresence>
                    {showBlocTimeDetails && (
                      <motion.div
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: 'auto', opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        transition={{ duration: 0.3 }}
                        className="overflow-hidden"
                      >
                        <div className="pt-6 mt-6 space-y-5 border-t" style={{ borderColor: 'var(--border-color)' }}>
                          {/* Stake Form */}
                          <div className="p-5 rounded-xl bg-gradient-to-br from-cyan-500/8 to-purple-500/8 border border-cyan-500/15">
                            <div className="flex items-center gap-2 mb-5">
                              <SparklesIcon className="w-5 h-5 text-cyan-400" />
                              <span className="text-sm font-semibold text-cyan-400">Stake NAT for BlocTime</span>
                              {blocTimeLoading && <ArrowPathIcon className="w-4 h-4 text-cyan-400 animate-spin ml-auto" />}
                            </div>

                            <div className="mb-5">
                              <div className="flex items-center justify-between mb-2">
                                <label className="text-xs font-medium" style={{ color: 'var(--text-tertiary)' }}>Amount</label>
                                <button
                                  onClick={() => setStakeAmount(userNatBalance)}
                                  className="text-[10px] px-2.5 py-1 rounded-md bg-cyan-500/10 border border-cyan-500/20 text-cyan-400 hover:bg-cyan-500/20 transition-all font-medium"
                                >
                                  MAX: {parseFloat(userNatBalance).toLocaleString()}
                                </button>
                              </div>
                              <input
                                type="text"
                                placeholder="0.0"
                                value={stakeAmount}
                                onChange={e => setStakeAmount(e.target.value)}
                                className="w-full text-lg px-4 py-3 rounded-xl focus:outline-none focus:ring-2 focus:ring-cyan-500/40 font-mono transition-all text-center"
                                style={inputStyle}
                              />
                            </div>

                            <div className="mb-5">
                              <div className="flex items-center justify-between mb-3">
                                <label className="text-xs font-medium" style={{ color: 'var(--text-tertiary)' }}>Lock Duration (Blocks)</label>
                                <span className="text-xs font-mono px-2.5 py-1 rounded-md bg-purple-500/10 border border-purple-500/20 text-purple-400 font-medium">
                                  {lockBlocks ? Number(lockBlocks).toLocaleString() : '0'} blocks
                                </span>
                              </div>

                              <div className="relative mb-4">
                                <input
                                  type="range"
                                  min="1"
                                  max={maxLockBlocks || 100000}
                                  value={lockBlocks || 1}
                                  onChange={e => setLockBlocks(e.target.value)}
                                  className="w-full h-2 rounded-full appearance-none cursor-pointer"
                                  style={{
                                    background: `linear-gradient(to right,
                                      rgb(6 182 212) 0%,
                                      rgb(168 85 247) ${((Number(lockBlocks) || 1) / (maxLockBlocks || 100000)) * 100}%,
                                      var(--bg-input) ${((Number(lockBlocks) || 1) / (maxLockBlocks || 100000)) * 100}%,
                                      var(--bg-input) 100%)`,
                                  }}
                                />
                              </div>

                              <div className="grid grid-cols-4 gap-2 mb-3">
                                {[0.25, 0.5, 0.75, 1].map((pct) => {
                                  const blocks = Math.floor((maxLockBlocks || 100000) * pct)
                                  const isActive = Number(lockBlocks) === blocks
                                  return (
                                    <button
                                      key={pct}
                                      onClick={() => setLockBlocks(blocks.toString())}
                                      className={`px-2 py-2 text-[10px] font-medium rounded-lg transition-all duration-200 border ${
                                        isActive ? 'bg-cyan-500/15 border-cyan-500/30 text-cyan-400' : 'border-transparent hover:bg-purple-500/5'
                                      }`}
                                      style={!isActive ? { color: 'var(--text-tertiary)', backgroundColor: 'var(--hover-bg)' } : {}}
                                    >
                                      {pct === 1 ? 'MAX' : `${pct * 100}%`}
                                    </button>
                                  )
                                })}
                              </div>

                              <input
                                type="number"
                                placeholder={maxLockBlocks > 0 ? `1 to ${maxLockBlocks.toLocaleString()}` : '100000'}
                                value={lockBlocks}
                                onChange={e => {
                                  const val = e.target.value
                                  const num = Number(val)
                                  const max = maxLockBlocks || 100000
                                  if (val === '' || (num >= 1 && num <= max)) setLockBlocks(val)
                                }}
                                className="w-full text-sm px-3 py-2.5 rounded-xl focus:outline-none focus:ring-2 focus:ring-cyan-500/40 font-mono transition-all text-center"
                                style={inputStyle}
                              />
                              <div className="flex items-center justify-between mt-1.5 px-1">
                                <span className="text-[9px] font-medium" style={{ color: 'var(--text-tertiary)' }}>Min: 1</span>
                                <span className="text-[9px] font-medium" style={{ color: 'var(--text-tertiary)' }}>Max: {(maxLockBlocks || 100000).toLocaleString()}</span>
                              </div>
                            </div>

                            {lockBlocks && stakeAmount && parseFloat(stakeAmount) > 0 && (
                              <div className="mb-4 p-3.5 rounded-xl bg-purple-500/8 border border-purple-500/15">
                                <div className="flex items-center justify-between text-xs">
                                  <span className="font-medium" style={{ color: 'var(--text-tertiary)' }}>Estimated BlocTime</span>
                                  <span className="font-mono font-bold text-purple-400">
                                    {(() => {
                                      const multiplier = (Number(lockBlocks) / (maxLockBlocks || 100000))
                                      const estimated = parseFloat(stakeAmount) * (1 + multiplier)
                                      return estimated.toLocaleString(undefined, { maximumFractionDigits: 2 })
                                    })()}
                                  </span>
                                </div>
                                <div className="mt-1 text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                                  Longer locks = higher multiplier (up to 2x at max)
                                </div>
                              </div>
                            )}

                            <button
                              onClick={handleStakeToBlocTime}
                              disabled={adminLoading !== null || !stakeAmount || !lockBlocks || parseFloat(stakeAmount) <= 0}
                              className="w-full px-4 py-3.5 text-sm font-semibold rounded-xl bg-gradient-to-r from-cyan-500 to-purple-500 text-white hover:from-cyan-400 hover:to-purple-400 disabled:opacity-30 disabled:cursor-not-allowed transition-all duration-200 shadow-lg shadow-cyan-500/20"
                            >
                              {adminLoading === 'Stake to BlocTime' ? (
                                <span className="flex items-center justify-center gap-2">
                                  <ArrowPathIcon className="w-4 h-4 animate-spin" />
                                  Processing...
                                </span>
                              ) : 'Stake NAT'}
                            </button>
                          </div>

                          {/* User Stakes */}
                          {userStakes.length > 0 && (
                            <div>
                              <div className="flex items-center justify-between mb-3">
                                <span className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>Your Stakes</span>
                                <span className="text-xs px-2.5 py-1 rounded-full font-medium" style={{ backgroundColor: 'var(--hover-bg)', color: 'var(--text-tertiary)' }}>
                                  {userStakes.length}
                                </span>
                              </div>
                              <div className="space-y-2 max-h-64 overflow-y-auto custom-scrollbar">
                                {userStakes.map((stake) => {
                                  const amountFormatted = ethers.formatUnits(stake.amount, modTokenDecimals)
                                  const blocTimeFormatted = ethers.formatUnits(stake.blocTimeBalance, 18)
                                  const isUnlocked = stake.blocksRemaining === BigInt(0)

                                  return (
                                    <div key={stake.id} className="p-3.5 rounded-xl border transition-all" style={{ backgroundColor: 'var(--hover-bg)', borderColor: 'var(--border-color)' }}>
                                      <div className="flex items-center justify-between mb-2">
                                        <div className="flex items-center gap-2">
                                          <span className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>
                                            {parseFloat(amountFormatted).toLocaleString()} {modTokenSymbol}
                                          </span>
                                          <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${isUnlocked ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/25' : 'bg-amber-500/15 text-amber-400 border border-amber-500/25'}`}>
                                            {isUnlocked ? 'Unlocked' : 'Locked'}
                                          </span>
                                        </div>
                                        <button
                                          onClick={() => handleUnstake(stake.id)}
                                          disabled={adminLoading !== null || !isUnlocked}
                                          className="px-3 py-1.5 text-[11px] font-medium rounded-lg bg-cyan-500/10 border border-cyan-500/20 text-cyan-400 hover:bg-cyan-500/20 disabled:opacity-30 transition-all"
                                        >
                                          {adminLoading === `Unstake ${stake.id}` ? '...' : 'Unstake'}
                                        </button>
                                      </div>
                                      <div className="flex items-center justify-between text-xs" style={{ color: 'var(--text-tertiary)' }}>
                                        <span>BTime: {parseFloat(blocTimeFormatted).toLocaleString()}</span>
                                        <span>Blocks left: {stake.blocksRemaining.toString()}</span>
                                      </div>
                                    </div>
                                  )
                                })}
                              </div>
                            </div>
                          )}

                          {!blocTimeLoading && userStakes.length === 0 && stakeAmount === '' && (
                            <div className="py-6 text-center">
                              <div className="text-xs font-medium" style={{ color: 'var(--text-tertiary)' }}>No active stakes</div>
                            </div>
                          )}
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </div>
              </Card>

              {/* Claimable Rewards */}
              {holderInfo && holderInfo.tokens.length > 0 && (
                <Card className="p-4 overflow-hidden relative" style={{ borderColor: 'rgba(16, 185, 129, 0.15)' }}>
                  <div className="absolute inset-0 bg-gradient-to-br from-emerald-500/5 via-transparent to-green-500/5" />
                  <div className="relative z-10">
                    <div className="flex items-center justify-between mb-3">
                      <div className="flex items-center gap-2">
                        <div className="w-8 h-8 rounded-lg bg-emerald-500/15 flex items-center justify-center">
                          <WalletIcon className="w-4 h-4 text-emerald-400" />
                        </div>
                        <span className="text-sm font-semibold text-emerald-400">Claimable Rewards</span>
                      </div>
                      <span className="text-xs font-mono" style={{ color: 'var(--text-tertiary)' }}>
                        {walletAddress.slice(0, 6)}...{walletAddress.slice(-4)}
                      </span>
                    </div>

                    {/* Ownership */}
                    <div className="mb-3 p-3 rounded-xl" style={{ backgroundColor: 'var(--hover-bg)' }}>
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-xs font-medium" style={{ color: 'var(--text-tertiary)' }}>Your Treasury Ownership</span>
                        <span className="text-lg font-bold text-emerald-400">
                          {(Number(holderInfo.ownershipPercentage) / 100).toFixed(2)}%
                        </span>
                      </div>
                      <div className="grid grid-cols-2 gap-3 text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                        <div>
                          <div className="font-medium mb-0.5">Your BlocTime</div>
                          <div className="font-mono font-semibold" style={{ color: 'var(--text-secondary)' }}>
                            {parseFloat(ethers.formatUnits(holderInfo.governanceBalance, 18)).toLocaleString()}
                          </div>
                        </div>
                        <div className="text-right">
                          <div className="font-medium mb-0.5">Total Supply</div>
                          <div className="font-mono font-semibold" style={{ color: 'var(--text-secondary)' }}>
                            {blocTimeTotalSupply !== '0' ? parseFloat(blocTimeTotalSupply).toLocaleString() : '...'}
                          </div>
                        </div>
                      </div>
                      <div className="mt-3 pt-3 border-t" style={{ borderColor: 'var(--border-color)' }}>
                        <div className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                          Claims based on proportional share (minus {ownerPctDisplay}% owner fee)
                        </div>
                      </div>
                    </div>

                    <div className="space-y-2">
                      {holderInfo.tokens.map((token, i) => {
                        const bal = tokenBalances.find(b => b.address.toLowerCase() === token.toLowerCase())
                        const alreadyClaimed = holderInfo.claimedAmounts[i]
                        const contractClaimable = holderInfo.claimableAmounts[i]
                        const actualBalance = actualTokenBalances.get(token.toLowerCase()) || BigInt(0)
                        const contractExceedsBalance = contractClaimable > actualBalance
                        const ownershipPct = Number(holderInfo.ownershipPercentage) / 10000
                        const ownerFeePct = Number(treasuryInfo?.ownerPct || 0) / 10000
                        const distributablePct = 1 - ownerFeePct
                        const actualFormatted = ethers.formatUnits(actualBalance, bal?.decimals || 18)
                        const actualNum = parseFloat(actualFormatted)
                        const userShare = actualNum * distributablePct * ownershipPct
                        const claimedFormatted = ethers.formatUnits(alreadyClaimed, bal?.decimals || 18)
                        const claimedNum = parseFloat(claimedFormatted)

                        return (
                          <div key={token} className="p-3.5 rounded-xl border transition-all" style={{ backgroundColor: 'var(--hover-bg)', borderColor: contractExceedsBalance ? 'rgba(239, 68, 68, 0.2)' : 'var(--border-color)' }}>
                            <div className="flex items-center justify-between">
                              <div className="flex items-center gap-2">
                                <span className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>{bal?.symbol || token.slice(0, 8)}</span>
                                {contractExceedsBalance && (
                                  <span className="text-[9px] px-2 py-0.5 rounded-full bg-red-500/15 text-red-400 border border-red-500/25 font-medium">
                                    Error
                                  </span>
                                )}
                              </div>
                              <div className="flex items-center gap-3">
                                <div className="text-right">
                                  <div className="font-bold font-mono text-emerald-400">${userShare.toFixed(2)}</div>
                                  <div className="text-[9px]" style={{ color: 'var(--text-tertiary)' }}>of ${actualNum.toFixed(2)}</div>
                                </div>
                                <button
                                  onClick={() => handleWithdrawToken(token, bal?.symbol || 'Token')}
                                  disabled={adminLoading !== null || actualBalance === BigInt(0) || contractExceedsBalance}
                                  className="px-3.5 py-2 text-[11px] font-semibold rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 hover:bg-emerald-500/20 disabled:opacity-30 transition-all"
                                >
                                  {adminLoading === `Withdraw ${bal?.symbol || 'Token'}` ? '...' : 'Claim'}
                                </button>
                              </div>
                            </div>

                            <details className="text-[10px] mt-2" style={{ color: 'var(--text-tertiary)' }}>
                              <summary className="cursor-pointer hover:text-emerald-400 transition-colors font-medium">
                                View calculation
                              </summary>
                              <div className="mt-2 p-3 space-y-1.5 rounded-lg" style={{ backgroundColor: 'var(--bg-secondary)' }}>
                                <div className="flex justify-between">
                                  <span>Treasury balance:</span>
                                  <span className="font-mono">${actualNum.toFixed(2)}</span>
                                </div>
                                <div className="flex justify-between">
                                  <span>Owner fee ({(ownerFeePct * 100).toFixed(2)}%):</span>
                                  <span className="font-mono">-${(actualNum * ownerFeePct).toFixed(2)}</span>
                                </div>
                                <div className="flex justify-between pt-1.5 border-t" style={{ borderColor: 'var(--border-color)' }}>
                                  <span>Distributable:</span>
                                  <span className="font-mono">${(actualNum * distributablePct).toFixed(2)}</span>
                                </div>
                                <div className="flex justify-between">
                                  <span>Your share ({(ownershipPct * 100).toFixed(2)}%):</span>
                                  <span className="font-mono font-bold text-emerald-400">${userShare.toFixed(2)}</span>
                                </div>
                                {claimedNum > 0 && (
                                  <div className="flex justify-between pt-1.5 border-t" style={{ borderColor: 'var(--border-color)' }}>
                                    <span>Already claimed:</span>
                                    <span className="font-mono">${claimedNum.toFixed(2)}</span>
                                  </div>
                                )}
                                {contractExceedsBalance && (
                                  <div className="pt-2 mt-2 border-t" style={{ borderColor: 'var(--border-color)' }}>
                                    <div className="text-red-400 font-semibold mb-1">Contract Calculation Error</div>
                                    <div className="text-[9px]">Contract thinks: ${ethers.formatUnits(contractClaimable, bal?.decimals || 18)} {bal?.symbol}</div>
                                    <div className="text-[9px]">Actual balance: ${actualNum.toFixed(2)}</div>
                                  </div>
                                )}
                              </div>
                            </details>

                            {contractExceedsBalance && (
                              <div className="text-[10px] mt-2 px-3 py-2 rounded-lg bg-red-500/8 border border-red-500/15" style={{ color: 'var(--text-tertiary)' }}>
                                <span className="text-red-400 font-semibold">Cannot claim:</span> Contract calculation exceeds treasury balance.
                              </div>
                            )}
                          </div>
                        )
                      })}
                      {holderInfo.tokens.length > 1 && (
                        <div className="flex justify-end pt-2">
                          <button
                            onClick={handleWithdrawAll}
                            disabled={adminLoading !== null}
                            className="px-5 py-2.5 text-xs font-semibold rounded-xl bg-gradient-to-r from-emerald-500 to-green-500 text-white hover:from-emerald-400 hover:to-green-400 disabled:opacity-30 transition-all shadow-lg shadow-emerald-500/20"
                          >
                            {adminLoading === 'Withdraw All' ? '...' : 'Claim All'}
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                </Card>
              )}

              {/* Empty rewards state */}
              {!holderInfo?.tokens.length && (
                <Card className="p-6 text-center">
                  <div className="w-12 h-12 mx-auto mb-3 rounded-xl bg-purple-500/10 flex items-center justify-center">
                    <WalletIcon className="w-6 h-6 text-purple-500/30" />
                  </div>
                  <div className="text-sm mb-1 font-medium" style={{ color: 'var(--text-secondary)' }}>No claimable rewards</div>
                  <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>Rewards appear when you hold governance tokens</div>
                </Card>
              )}

              {/* Contribute */}
              <Card className="p-4">
                <div className="flex items-center gap-2 mb-3">
                  <div className="w-8 h-8 rounded-lg bg-purple-500/15 flex items-center justify-center">
                    <BuildingLibraryIcon className="w-4 h-4 text-purple-400" />
                  </div>
                  <span className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>Contribute to Treasury</span>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <select
                    value={fundToken}
                    onChange={e => setFundToken(e.target.value)}
                    className="text-sm px-4 py-3 rounded-xl focus:outline-none focus:ring-2 focus:ring-purple-500/30 transition-all font-medium"
                    style={{ fontFamily: 'inherit', ...inputStyle }}
                  >
                    <option value="">Select Token</option>
                    {tokenBalances.map(t => (
                      <option key={t.address} value={t.symbol}>{t.symbol}</option>
                    ))}
                    <option value="NativeToken">NativeToken</option>
                  </select>
                  <input
                    type="text"
                    placeholder="Amount"
                    value={fundAmount}
                    onChange={e => setFundAmount(e.target.value)}
                    className="text-sm px-4 py-3 rounded-xl focus:outline-none focus:ring-2 focus:ring-purple-500/30 font-mono transition-all"
                    style={inputStyle}
                  />
                </div>
                <button
                  onClick={handleFundTreasury}
                  disabled={adminLoading !== null || !fundToken || !fundAmount}
                  className="w-full mt-3 px-4 py-3 text-sm font-semibold rounded-xl border transition-all duration-200 hover:border-purple-500/40 hover:bg-purple-500/5 disabled:opacity-30"
                  style={{ borderColor: 'var(--border-color)', color: 'var(--text-primary)' }}
                >
                  {adminLoading === 'Fund Treasury' ? 'Processing...' : 'Deposit to Treasury'}
                </button>
              </Card>

              {/* Deposits */}
              <Card className="p-4">
                <div className="flex items-center justify-between mb-3">
                  <span className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>Deposit History</span>
                  <button
                    onClick={fetchDeposits}
                    disabled={depositsLoading}
                    className="p-1.5 rounded-lg hover:bg-purple-500/10 transition-colors"
                  >
                    <ArrowPathIcon className={`w-4 h-4 ${depositsLoading ? 'animate-spin text-purple-400' : ''}`} style={depositsLoading ? {} : { color: 'var(--text-tertiary)' }} />
                  </button>
                </div>

                {depositsLoading ? (
                  <div className="py-4 text-center text-sm" style={{ color: 'var(--text-tertiary)' }}>Loading...</div>
                ) : deposits.filter(dep => dep.funder.toLowerCase() === walletAddress.toLowerCase()).length === 0 ? (
                  <div className="py-4 text-center">
                    <div className="text-sm mb-1 font-medium" style={{ color: 'var(--text-secondary)' }}>No deposits yet</div>
                    <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>Your deposits will appear here</div>
                  </div>
                ) : (
                  <div className="space-y-2">
                    {deposits
                      .filter(dep => dep.funder.toLowerCase() === walletAddress.toLowerCase())
                      .map((dep, idx) => (
                        <div key={idx} className="flex items-center justify-between p-3.5 rounded-xl" style={{ backgroundColor: 'var(--hover-bg)' }}>
                          <div>
                            <div className="flex items-center gap-2">
                              <span className="text-purple-400 font-semibold text-sm">{dep.token}</span>
                              <span className="text-xs font-mono" style={{ color: 'var(--text-tertiary)' }}>
                                {new Date(dep.timestamp * 1000).toLocaleDateString()}
                              </span>
                            </div>
                            <span className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>Block #{dep.blockNumber}</span>
                          </div>
                          <span className="text-emerald-400 font-bold font-mono">${parseFloat(dep.amount).toFixed(2)}</span>
                        </div>
                      ))}
                  </div>
                )}
              </Card>

              {/* Claims History */}
              <Card className="p-4">
                <div className="flex items-center justify-between mb-3">
                  <span className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>Claims History</span>
                  <button
                    onClick={fetchClaims}
                    disabled={claimsLoading}
                    className="p-1.5 rounded-lg hover:bg-pink-500/10 transition-colors"
                  >
                    <ArrowPathIcon className={`w-4 h-4 ${claimsLoading ? 'animate-spin text-pink-400' : ''}`} style={claimsLoading ? {} : { color: 'var(--text-tertiary)' }} />
                  </button>
                </div>

                {claimsLoading ? (
                  <div className="py-4 text-center text-sm" style={{ color: 'var(--text-tertiary)' }}>Loading...</div>
                ) : claims.filter(claim => claim.holder.toLowerCase() === walletAddress.toLowerCase()).length === 0 ? (
                  <div className="py-4 text-center">
                    <div className="text-sm mb-1 font-medium" style={{ color: 'var(--text-secondary)' }}>No claims yet</div>
                    <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>Your reward claims will appear here</div>
                  </div>
                ) : (
                  <div className="space-y-2">
                    {claims
                      .filter(claim => claim.holder.toLowerCase() === walletAddress.toLowerCase())
                      .map((claim, idx) => (
                        <div key={idx} className="flex items-center justify-between p-3.5 rounded-xl relative overflow-hidden" style={{ backgroundColor: 'var(--hover-bg)' }}>
                          <div className="absolute left-0 top-0 bottom-0 w-1 rounded-l-xl bg-gradient-to-b from-pink-500 to-purple-500" />
                          <div className="pl-2">
                            <div className="flex items-center gap-2">
                              <span className="text-pink-400 font-semibold text-sm">{claim.token}</span>
                              <span className="text-[10px] px-2 py-0.5 rounded-full bg-pink-500/10 text-pink-400 border border-pink-500/20 font-medium">
                                Claimed
                              </span>
                              <span className="text-xs font-mono" style={{ color: 'var(--text-tertiary)' }}>
                                {new Date(claim.timestamp * 1000).toLocaleDateString()}
                              </span>
                            </div>
                            <span className="text-[10px]" style={{ color: 'var(--text-tertiary)' }}>Block #{claim.blockNumber}</span>
                          </div>
                          <div className="text-right">
                            <div className="text-pink-400 font-bold font-mono">${parseFloat(claim.amount).toFixed(2)}</div>
                            <a
                              href={`https://sepolia.basescan.org/tx/${claim.txHash}`}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-[9px] hover:text-pink-400 transition-colors font-medium"
                              style={{ color: 'var(--text-tertiary)' }}
                            >
                              View Tx
                            </a>
                          </div>
                        </div>
                      ))}
                  </div>
                )}
              </Card>
            </motion.div>
          )}

          {/* ── Owner Tab ── */}
          {activeTab === 'owner' && canAdmin && (
            <motion.div
              key="owner"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.2 }}
              className="space-y-3"
            >
              {/* Admin Badge */}
              <Card className="p-3 overflow-hidden relative" style={{ borderColor: 'rgba(245, 158, 11, 0.15)' }}>
                <div className="absolute inset-0 bg-gradient-to-r from-amber-500/5 via-transparent to-orange-500/5" />
                <div className="relative z-10 flex items-center gap-3">
                  <div className="w-10 h-10 rounded-xl bg-amber-500/15 flex items-center justify-center">
                    <ShieldCheckIcon className="w-5 h-5 text-amber-400" />
                  </div>
                  <div className="flex-1">
                    <span className="font-semibold" style={{ color: 'var(--text-primary)' }}>Owner Controls</span>
                    <div className="text-xs mt-0.5" style={{ color: 'var(--text-tertiary)' }}>
                      {isOwner ? 'Direct Owner' : `Safe signer (${safeThreshold}/${safeOwners.length})`}
                    </div>
                  </div>
                </div>
              </Card>

              {/* Owner Withdrawals */}
              <Card className="p-4">
                <p className="text-xs font-medium mb-3 uppercase tracking-wider" style={{ color: 'var(--text-tertiary)' }}>Owner Withdrawals</p>
                <div className="space-y-2">
                  {tokenBalances.map(tb => (
                    <div key={tb.address} className="flex items-center justify-between p-3 rounded-xl transition-all hover:bg-amber-500/5" style={{ backgroundColor: 'var(--hover-bg)' }}>
                      <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded-lg bg-amber-500/10 flex items-center justify-center">
                          <BanknotesIcon className="w-4 h-4 text-amber-400" />
                        </div>
                        <div>
                          <span className="font-semibold text-sm" style={{ color: 'var(--text-primary)' }}>{tb.symbol}</span>
                          <span className="text-xs ml-2" style={{ color: 'var(--text-tertiary)' }}>${tb.balance.toFixed(2)}</span>
                        </div>
                      </div>
                      <button
                        onClick={() => handleAdminTx(
                          'ownerWithdraw', [tb.address],
                          `Owner Withdraw ${tb.symbol}`,
                          () => treasury.ownerWithdraw(walletAddress, tb.address)
                        )}
                        disabled={adminLoading !== null}
                        className="px-3.5 py-2 text-xs font-medium rounded-lg bg-amber-500/10 border border-amber-500/20 text-amber-400 hover:bg-amber-500/20 disabled:opacity-30 transition-all"
                      >
                        {adminLoading === `Owner Withdraw ${tb.symbol}` ? '...' : isSafeSigner && !isOwner ? 'Propose' : 'Withdraw'}
                      </button>
                    </div>
                  ))}
                </div>
              </Card>

              {/* Settings */}
              <Card className="p-4">
                <p className="text-xs font-medium mb-4 uppercase tracking-wider" style={{ color: 'var(--text-tertiary)' }}>Settings</p>
                <div className="space-y-4">
                  {[
                    { label: `Owner % (now: ${ownerPctDisplay}%)`, placeholder: 'e.g. 500 = 5%', value: newOwnerPct, onChange: setNewOwnerPct, type: 'number',
                      onSubmit: () => handleAdminTx('setOwnerPercentage', [Number(newOwnerPct)], 'Set Owner %', () => treasury.setOwnerPercentage(walletAddress, Number(newOwnerPct))),
                      loading: adminLoading === 'Set Owner %', disabled: !newOwnerPct },
                    { label: 'Governance Token', placeholder: '0x...', value: newGovToken, onChange: setNewGovToken, type: 'text',
                      onSubmit: () => handleAdminTx('setGovernanceToken', [newGovToken], 'Set Gov Token', () => treasury.setGovernanceToken(walletAddress, newGovToken)),
                      loading: adminLoading === 'Set Gov Token', disabled: !newGovToken },
                    { label: 'Token Gate', placeholder: '0x...', value: newTokenGate, onChange: setNewTokenGate, type: 'text',
                      onSubmit: () => handleAdminTx('setTokenGate', [newTokenGate], 'Set Token Gate', () => treasury.setTokenGate(walletAddress, newTokenGate)),
                      loading: adminLoading === 'Set Token Gate', disabled: !newTokenGate },
                  ].map(({ label, placeholder, value, onChange, type, onSubmit, loading: isLoading, disabled }) => (
                    <div key={label}>
                      <label className="text-xs mb-2 block font-medium" style={{ color: 'var(--text-tertiary)' }}>{label}</label>
                      <div className="flex gap-2">
                        <input
                          type={type}
                          placeholder={placeholder}
                          value={value}
                          onChange={e => onChange(e.target.value)}
                          className="text-sm px-3 py-2.5 rounded-xl focus:outline-none focus:ring-2 focus:ring-purple-500/30 flex-1 font-mono"
                          style={inputStyle}
                        />
                        <button
                          onClick={onSubmit}
                          disabled={adminLoading !== null || disabled}
                          className="px-4 py-2.5 text-xs font-medium rounded-xl bg-purple-500/10 border border-purple-500/20 text-purple-400 hover:bg-purple-500/20 disabled:opacity-30 transition-all"
                        >
                          {isLoading ? '...' : 'Set'}
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </Card>

              {/* Emergency Withdraw */}
              <Card className="p-4 overflow-hidden relative" style={{ borderColor: 'rgba(239, 68, 68, 0.15)' }}>
                <div className="absolute inset-0 bg-gradient-to-br from-red-500/5 via-transparent to-rose-500/5" />
                <div className="relative z-10">
                  <div className="flex items-center gap-2 mb-3">
                    <ExclamationTriangleIcon className="w-4 h-4 text-red-400/60" />
                    <p className="text-xs font-medium text-red-400/60 uppercase tracking-wider">Emergency Withdraw</p>
                  </div>
                  <div className="flex gap-2">
                    <select
                      value={emergencyToken}
                      onChange={e => setEmergencyToken(e.target.value)}
                      className="text-sm px-3 py-2.5 rounded-xl focus:outline-none focus:ring-2 focus:ring-red-500/30 flex-1 font-medium"
                      style={{ fontFamily: 'inherit', ...inputStyle }}
                    >
                      <option value="">Token</option>
                      {tokenBalances.map(t => (
                        <option key={t.address} value={t.address}>{t.symbol}</option>
                      ))}
                    </select>
                    <input
                      type="text"
                      placeholder="Amount"
                      value={emergencyAmount}
                      onChange={e => setEmergencyAmount(e.target.value)}
                      className="text-sm px-3 py-2.5 rounded-xl focus:outline-none focus:ring-2 focus:ring-red-500/30 flex-1 font-mono"
                      style={inputStyle}
                    />
                    <button
                      onClick={() => {
                        const tb = tokenBalances.find(t => t.address === emergencyToken)
                        const amt = ethers.parseUnits(emergencyAmount, tb?.decimals || 18)
                        handleAdminTx(
                          'emergencyWithdraw', [emergencyToken, amt],
                          'Emergency Withdraw',
                          () => treasury.emergencyWithdraw(walletAddress, emergencyToken, amt)
                        )
                      }}
                      disabled={adminLoading !== null || !emergencyToken || !emergencyAmount}
                      className="px-4 py-2.5 text-xs font-medium rounded-xl bg-red-500/10 border border-red-500/20 text-red-400 hover:bg-red-500/20 disabled:opacity-30 transition-all"
                    >
                      {adminLoading === 'Emergency Withdraw' ? '...' : 'Execute'}
                    </button>
                  </div>
                </div>
              </Card>

              {/* Transfer Ownership */}
              <Card className="p-4 overflow-hidden relative" style={{ borderColor: 'rgba(239, 68, 68, 0.15)' }}>
                <div className="absolute inset-0 bg-gradient-to-br from-red-500/5 via-transparent to-rose-500/5" />
                <div className="relative z-10">
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-2">
                      <ExclamationTriangleIcon className="w-4 h-4 text-red-400/60" />
                      <p className="text-xs font-medium text-red-400/60 uppercase tracking-wider">Transfer Ownership</p>
                    </div>
                    <span className="text-[10px] px-2 py-0.5 rounded-full bg-red-500/10 text-red-400/50 border border-red-500/15 font-medium">Irreversible</span>
                  </div>
                  <div className="flex gap-2">
                    <input
                      type="text"
                      placeholder="New owner (0x...)"
                      value={newOwner}
                      onChange={e => setNewOwner(e.target.value)}
                      className="text-sm px-3 py-2.5 rounded-xl focus:outline-none focus:ring-2 focus:ring-red-500/30 flex-1 font-mono"
                      style={inputStyle}
                    />
                    <button
                      onClick={() => handleAdminTx(
                        'transferOwnership', [newOwner],
                        'Transfer Ownership',
                        () => treasury.transferOwnership(walletAddress, newOwner)
                      )}
                      disabled={adminLoading !== null || !newOwner}
                      className="px-4 py-2.5 text-xs font-medium rounded-xl bg-red-500/10 border border-red-500/20 text-red-400 hover:bg-red-500/20 disabled:opacity-30 transition-all"
                    >
                      {adminLoading === 'Transfer Ownership' ? '...' : 'Transfer'}
                    </button>
                  </div>
                </div>
              </Card>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}
