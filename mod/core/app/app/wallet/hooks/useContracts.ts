"use client";

import { useState, useCallback } from 'react'
import { ethers } from 'ethers'
import { toast } from 'react-toastify'
import { getSigner, getProvider } from '@/network/signer'
import RegistryABI from '@/contracts/registry/Registry.sol/Registry.json'
import MarketABI from '@/contracts/market/Market.sol/Market.json'
import TreasuryABI from '@/contracts/treasury/Treasury.sol/Treasury.json'
import BlocTimeABI from '@/contracts/bloctime/BlocTime.sol/BlocTime.json'
import { getContracts } from '@/network/chainConfig'

function getAddress(name: string): string {
  return getContracts()[name]?.address || ''
}

interface RegisteredMod {
  id: number
  owner: string
  name: string
  data: string
}

interface StakePosition {
  stakeId: number
  amount: number
  startBlock: number
  lockBlocks: number
  blocTimeBalance: number
  blocksRemaining: number
}

export function useContracts(userKey: string | undefined) {
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  // Registry state
  const [userMods, setUserMods] = useState<RegisteredMod[]>([])
  const [registryLoading, setRegistryLoading] = useState(false)

  // Treasury state
  const [treasuryInfo, setTreasuryInfo] = useState<any>(null)
  const [holderInfo, setHolderInfo] = useState<any>(null)

  // BlocTime state
  const [stakes, setStakes] = useState<StakePosition[]>([])

  const clearFeedback = () => { setError(null); setSuccess(null) }

  // ─── REGISTRY ───────────────────────────────────────────────

  const fetchUserMods = useCallback(async () => {
    if (!userKey) return
    const addr = getAddress('Registry')
    if (!addr) return
    setRegistryLoading(true)
    try {
      const provider = getProvider()
      const registry = new ethers.Contract(addr, RegistryABI.abi, provider)
      const modIds: bigint[] = await registry.getUserMods(userKey)
      const mods: RegisteredMod[] = []
      for (const id of modIds) {
        const [owner, name, data] = await registry.getMod(id)
        mods.push({ id: Number(id), owner, name, data })
      }
      setUserMods(mods)
    } catch (err: any) {
      console.error('Failed to fetch user mods:', err)
    } finally {
      setRegistryLoading(false)
    }
  }, [userKey])

  const registerMod = async (name: string, data: string) => {
    if (!userKey) return
    clearFeedback()
    setIsLoading(true)
    try {
      const addr = getAddress('Registry')
      if (!addr) throw new Error('Registry contract not found')
      const signer = await getSigner(userKey)
      const registry = new ethers.Contract(addr, RegistryABI.abi, signer)
      const tx = await registry.registerMod(name, data)
      toast.success(`TX sent: ${tx.hash.slice(0, 12)}...`)
      await tx.wait()
      setSuccess(`Registered "${name}" on-chain`)
      toast.success(`Module "${name}" registered!`)
      fetchUserMods()
    } catch (err: any) {
      const msg = err?.reason || err?.message || 'Failed to register'
      setError(msg)
      toast.error(msg)
    } finally {
      setIsLoading(false)
    }
  }

  const updateMod = async (modId: number, data: string) => {
    if (!userKey) return
    clearFeedback()
    setIsLoading(true)
    try {
      const addr = getAddress('Registry')
      if (!addr) throw new Error('Registry contract not found')
      const signer = await getSigner(userKey)
      const registry = new ethers.Contract(addr, RegistryABI.abi, signer)
      const tx = await registry.updateMod(modId, data)
      toast.success(`TX sent: ${tx.hash.slice(0, 12)}...`)
      await tx.wait()
      setSuccess('Module updated on-chain')
      toast.success('Module updated!')
      fetchUserMods()
    } catch (err: any) {
      const msg = err?.reason || err?.message || 'Failed to update'
      setError(msg)
      toast.error(msg)
    } finally {
      setIsLoading(false)
    }
  }

  const removeMod = async (modId: number) => {
    if (!userKey) return
    clearFeedback()
    setIsLoading(true)
    try {
      const addr = getAddress('Registry')
      if (!addr) throw new Error('Registry contract not found')
      const signer = await getSigner(userKey)
      const registry = new ethers.Contract(addr, RegistryABI.abi, signer)
      const tx = await registry.removeMod(modId)
      toast.success(`TX sent: ${tx.hash.slice(0, 12)}...`)
      await tx.wait()
      setSuccess('Module removed from registry')
      toast.success('Module removed!')
      fetchUserMods()
    } catch (err: any) {
      const msg = err?.reason || err?.message || 'Failed to remove'
      setError(msg)
      toast.error(msg)
    } finally {
      setIsLoading(false)
    }
  }

  const transferModOwnership = async (modId: number, newOwner: string) => {
    if (!userKey) return
    clearFeedback()
    setIsLoading(true)
    try {
      const addr = getAddress('Registry')
      if (!addr) throw new Error('Registry contract not found')
      if (!ethers.isAddress(newOwner)) throw new Error('Invalid address')
      const signer = await getSigner(userKey)
      const registry = new ethers.Contract(addr, RegistryABI.abi, signer)
      const tx = await registry.transferOwnership(modId, newOwner)
      toast.success(`TX sent: ${tx.hash.slice(0, 12)}...`)
      await tx.wait()
      setSuccess('Ownership transferred')
      toast.success('Ownership transferred!')
      fetchUserMods()
    } catch (err: any) {
      const msg = err?.reason || err?.message || 'Failed to transfer'
      setError(msg)
      toast.error(msg)
    } finally {
      setIsLoading(false)
    }
  }

  // ─── MARKET (mint with ETH) ─────────────────────────────────

  const mintWithETH = async (ethAmount: string) => {
    if (!userKey) return
    clearFeedback()
    setIsLoading(true)
    try {
      const addr = getAddress('Market')
      if (!addr) throw new Error('Market contract not found')
      const signer = await getSigner(userKey)
      const market = new ethers.Contract(addr, MarketABI.abi, signer)
      const value = ethers.parseEther(ethAmount)
      const tx = await market.mintWithETH({ value })
      toast.success(`TX sent: ${tx.hash.slice(0, 12)}...`)
      await tx.wait()
      setSuccess(`Minted with ${ethAmount} ETH`)
      toast.success('Minted successfully!')
    } catch (err: any) {
      const msg = err?.reason || err?.message || 'Failed to mint'
      setError(msg)
      toast.error(msg)
    } finally {
      setIsLoading(false)
    }
  }

  // ─── TREASURY ───────────────────────────────────────────────

  const fetchTreasuryInfo = useCallback(async () => {
    const addr = getAddress('Treasury')
    if (!addr) return
    try {
      const provider = getProvider()
      const treasury = new ethers.Contract(addr, TreasuryABI.abi, provider)
      const info = await treasury.getTreasuryInfo()
      setTreasuryInfo(info)
      if (userKey) {
        const hInfo = await treasury.getHolderInfo(userKey)
        setHolderInfo(hInfo)
      }
    } catch (err: any) {
      console.error('Failed to fetch treasury info:', err)
    }
  }, [userKey])

  const withdrawFromTreasury = async (tokenAddress?: string) => {
    if (!userKey) return
    clearFeedback()
    setIsLoading(true)
    try {
      const addr = getAddress('Treasury')
      if (!addr) throw new Error('Treasury contract not found')
      const signer = await getSigner(userKey)
      const treasury = new ethers.Contract(addr, TreasuryABI.abi, signer)
      let tx
      if (tokenAddress) {
        tx = await treasury.withdrawToken(tokenAddress)
      } else {
        tx = await treasury.withdrawAll()
      }
      toast.success(`TX sent: ${tx.hash.slice(0, 12)}...`)
      await tx.wait()
      setSuccess('Withdrawn from treasury')
      toast.success('Treasury withdrawal complete!')
      fetchTreasuryInfo()
    } catch (err: any) {
      const msg = err?.reason || err?.message || 'Failed to withdraw'
      setError(msg)
      toast.error(msg)
    } finally {
      setIsLoading(false)
    }
  }

  // ─── BLOCTIME ───────────────────────────────────────────────

  const fetchStakes = useCallback(async () => {
    if (!userKey) return
    const addr = getAddress('BlocTime')
    if (!addr) return
    try {
      const provider = getProvider()
      const blocTime = new ethers.Contract(addr, BlocTimeABI.abi, provider)
      const stakeIds: bigint[] = await blocTime.getUserStakeIds(userKey)
      const positions: StakePosition[] = []
      for (const sid of stakeIds) {
        const [amount, startBlock, lockBlocks, blocTimeBalance, blocksRemaining] =
          await blocTime.getStakePosition(userKey, sid)
        positions.push({
          stakeId: Number(sid),
          amount: parseFloat(ethers.formatEther(amount)),
          startBlock: Number(startBlock),
          lockBlocks: Number(lockBlocks),
          blocTimeBalance: parseFloat(ethers.formatEther(blocTimeBalance)),
          blocksRemaining: Number(blocksRemaining),
        })
      }
      setStakes(positions)
    } catch (err: any) {
      console.error('Failed to fetch stakes:', err)
    }
  }, [userKey])

  const stake = async (amount: string, lockBlocks: number) => {
    if (!userKey) return
    clearFeedback()
    setIsLoading(true)
    try {
      const addr = getAddress('BlocTime')
      if (!addr) throw new Error('BlocTime contract not found')
      const signer = await getSigner(userKey)
      const blocTime = new ethers.Contract(addr, BlocTimeABI.abi, signer)
      const amountWei = ethers.parseEther(amount)
      const tx = await blocTime.stake(amountWei, lockBlocks)
      toast.success(`TX sent: ${tx.hash.slice(0, 12)}...`)
      await tx.wait()
      setSuccess(`Staked ${amount} tokens for ${lockBlocks} blocks`)
      toast.success('Staked successfully!')
      fetchStakes()
    } catch (err: any) {
      const msg = err?.reason || err?.message || 'Failed to stake'
      setError(msg)
      toast.error(msg)
    } finally {
      setIsLoading(false)
    }
  }

  const unstake = async (stakeId: number) => {
    if (!userKey) return
    clearFeedback()
    setIsLoading(true)
    try {
      const addr = getAddress('BlocTime')
      if (!addr) throw new Error('BlocTime contract not found')
      const signer = await getSigner(userKey)
      const blocTime = new ethers.Contract(addr, BlocTimeABI.abi, signer)
      const tx = await blocTime.unstake(stakeId)
      toast.success(`TX sent: ${tx.hash.slice(0, 12)}...`)
      await tx.wait()
      setSuccess('Unstaked successfully')
      toast.success('Unstaked!')
      fetchStakes()
    } catch (err: any) {
      const msg = err?.reason || err?.message || 'Failed to unstake'
      setError(msg)
      toast.error(msg)
    } finally {
      setIsLoading(false)
    }
  }

  // ─── ETH PRICE ──────────────────────────────────────────────

  const fetchEthPrice = useCallback(async (): Promise<number> => {
    const tgAddr = getAddress('TokenGate')
    if (!tgAddr) return 0
    try {
      const provider = getProvider()
      // Use the TokenGate to get ETH price — address(0) or the WETH address
      // TokenGate.getTokenPrice uses the oracle for the token
      // For ETH we can use the manual oracle directly
      const oracleAddr = getAddress('ManualPriceOracle')
      if (!oracleAddr) return 0
      const oracleABI = [
        'function getPrice(address token) view returns (uint256 price, uint8 decimals, uint256 timestamp)',
      ]
      const oracle = new ethers.Contract(oracleAddr, oracleABI, provider)
      // ETH is typically address(0) or a sentinel — try WETH or zero address
      const [price, decimals] = await oracle.getPrice(ethers.ZeroAddress)
      return parseFloat(ethers.formatUnits(price, decimals))
    } catch {
      return 0
    }
  }, [])

  return {
    isLoading, error, success, clearFeedback,
    // Registry
    userMods, registryLoading, fetchUserMods,
    registerMod, updateMod, removeMod, transferModOwnership,
    // Market
    mintWithETH,
    // Treasury
    treasuryInfo, holderInfo, fetchTreasuryInfo, withdrawFromTreasury,
    // BlocTime
    stakes, fetchStakes, stake, unstake,
    // Price
    fetchEthPrice,
  }
}
