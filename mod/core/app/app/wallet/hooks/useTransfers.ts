"use client";

import { useState } from 'react'
import { ethers } from 'ethers'
import { getChainConfig } from '@/network/chainConfig'

type TransferTokenType = 'MARKET' | 'USDC' | 'USDT'
type TokenType = 'USDC' | 'USDT'

const ERC20_ABI = [
  'function transfer(address to, uint256 amount) returns (bool)',
  'function balanceOf(address owner) view returns (uint256)',
  'function decimals() view returns (uint8)',
  'function symbol() view returns (string)'
]

interface UseTransfersArgs {
  userKey: string | undefined
  marketCredit: number
  tokenBalances: Record<string, number>
  fetchMarketCredit: () => Promise<void>
  fetchCustomTokenBalances: () => Promise<void>
}

export function useTransfers({
  userKey,
  marketCredit, tokenBalances,
  fetchMarketCredit, fetchCustomTokenBalances,
}: UseTransfersArgs) {
  const [transferRecipient, setTransferRecipient] = useState('')
  const [transferAmount, setTransferAmount] = useState('')
  const [isTransferring, setIsTransferring] = useState(false)
  const [transferTokenType, setTransferTokenType] = useState<TransferTokenType>('MARKET')
  const [sendFromPortfolio, setSendFromPortfolio] = useState<string | null>(null)

  // Shared feedback state
  const [topUpError, setTopUpError] = useState<string | null>(null)
  const [topUpSuccess, setTopUpSuccess] = useState<string | null>(null)

  // Top-up
  const [selectedToken, setSelectedToken] = useState<TokenType>('USDC')
  const [topUpAmount, setTopUpAmount] = useState('')
  const [isProcessing, setIsProcessing] = useState(false)
  const [showTopUpForm, setShowTopUpForm] = useState(false)

  // Withdraw
  const [withdrawAmount, setWithdrawAmount] = useState('')
  const [isWithdrawing, setIsWithdrawing] = useState(false)
  const [withdrawTokenType, setWithdrawTokenType] = useState<TokenType>('USDC')
  const [showWithdrawForm, setShowWithdrawForm] = useState(false)

  const handleTopUpTransaction = async () => {
    if (!topUpAmount || !userKey) {
      setTopUpError('Please enter an amount')
      return
    }
    const amount = parseFloat(topUpAmount)
    if (amount <= 0) {
      setTopUpError('Amount must be greater than 0')
      return
    }
    setIsProcessing(true)
    setTopUpError(null)
    setTopUpSuccess(null)
    try {
      if (typeof window === 'undefined' || !window.ethereum) {
        throw new Error('No wallet connected. Please install MetaMask or another wallet.')
      }
      const { MarketAllowanceManager } = await import('@/network/marketAllowance')
      const chainConfig = getChainConfig()
      if (!chainConfig) throw new Error('Chain config not found')
      const allowanceManager = new MarketAllowanceManager(chainConfig)
      await allowanceManager.increaseMarketAllowance(userKey, amount, selectedToken)
      await allowanceManager.addMarketCredit(userKey, amount, selectedToken)
      await fetchMarketCredit()
      setTopUpSuccess(`Successfully added $${amount.toFixed(2)} using ${selectedToken}!`)
      setTopUpAmount('')
      setTimeout(() => { setShowTopUpForm(false); setTopUpSuccess(null) }, 3000)
    } catch (err: any) {
      let msg = err?.message || String(err)
      if (msg.includes('1010')) msg = 'Insufficient balance for transaction.'
      else if (msg.toLowerCase().includes('cancel') || msg.toLowerCase().includes('user rejected')) msg = 'Transaction cancelled by user.'
      setTopUpError(msg)
    } finally {
      setIsProcessing(false)
    }
  }

  const handleTransfer = async () => {
    if (!transferAmount || !transferRecipient || !userKey) {
      setTopUpError('Please enter recipient address and amount')
      return
    }
    if (!ethers.isAddress(transferRecipient)) {
      setTopUpError('Invalid recipient address')
      return
    }
    const amount = parseFloat(transferAmount)
    if (amount <= 0) {
      setTopUpError('Amount must be greater than 0')
      return
    }
    if (transferTokenType === 'MARKET' && amount > marketCredit) {
      setTopUpError('Insufficient MARKET balance')
      return
    }
    if (transferTokenType === 'USDC' && amount > (tokenBalances?.USDC || 0)) {
      setTopUpError('Insufficient USDC balance')
      return
    }
    if (transferTokenType === 'USDT' && amount > (tokenBalances?.USDT || 0)) {
      setTopUpError('Insufficient USDT balance')
      return
    }
    setIsTransferring(true)
    setTopUpError(null)
    setTopUpSuccess(null)
    try {
      if (typeof window === 'undefined' || !window.ethereum) throw new Error('No wallet connected. Please install MetaMask or another wallet.')
      if (transferTokenType === 'MARKET') {
        const { Market } = await import('@/network/Market')
        const chainConfig = getChainConfig()
        if (!chainConfig) throw new Error('Chain config not found')
        const market = new Market(chainConfig)
        await market.transferMarketCredit(userKey, transferRecipient, amount)
      } else {
        const chainConfig = getChainConfig()
        if (!chainConfig) throw new Error('Chain config not found')
        const tokenAddress = chainConfig.contracts?.[transferTokenType]?.address
        if (!tokenAddress) throw new Error(`${transferTokenType} contract not found`)
        const browserProvider = new ethers.BrowserProvider(window.ethereum)
        const signer = await browserProvider.getSigner(userKey)
        const contract = new ethers.Contract(tokenAddress, ERC20_ABI, signer)
        const tokenDecimals = await contract.decimals()
        const amountInWei = ethers.parseUnits(amount.toString(), tokenDecimals)
        const tx = await contract.transfer(transferRecipient, amountInWei)
        await tx.wait()
      }
      await fetchMarketCredit()
      setTopUpSuccess(`Successfully transferred $${amount.toFixed(2)} ${transferTokenType} to ${transferRecipient.slice(0, 8)}...${transferRecipient.slice(-6)}!`)
      setTransferAmount('')
      setTransferRecipient('')
      setTimeout(() => { setSendFromPortfolio(null); setTopUpSuccess(null) }, 3000)
    } catch (err: any) {
      let msg = err?.message || String(err)
      if (msg.toLowerCase().includes('cancel') || msg.toLowerCase().includes('user rejected')) msg = 'Transaction cancelled by user.'
      else if (msg.includes('insufficient funds')) msg = 'Insufficient balance for transfer and gas fees.'
      setTopUpError(msg)
    } finally {
      setIsTransferring(false)
    }
  }

  const handleSendCustomToken = async (tokenAddr: string, symbol: string, decimals: number) => {
    if (!transferAmount || !transferRecipient || !userKey) {
      setTopUpError('Enter recipient and amount')
      return
    }
    if (!ethers.isAddress(transferRecipient)) {
      setTopUpError('Invalid recipient address')
      return
    }
    const amount = parseFloat(transferAmount)
    if (amount <= 0) {
      setTopUpError('Amount must be greater than 0')
      return
    }
    setIsTransferring(true)
    setTopUpError(null)
    setTopUpSuccess(null)
    try {
      if (!window.ethereum) throw new Error('No wallet connected')
      const provider = new ethers.BrowserProvider(window.ethereum)
      const signer = await provider.getSigner(userKey)
      const contract = new ethers.Contract(tokenAddr, ERC20_ABI, signer)
      const amountWei = ethers.parseUnits(amount.toString(), decimals)
      const tx = await contract.transfer(transferRecipient, amountWei)
      await tx.wait()
      await fetchMarketCredit()
      await fetchCustomTokenBalances()
      setTopUpSuccess(`Sent ${amount} ${symbol} to ${transferRecipient.slice(0, 8)}...`)
      setTransferAmount('')
      setTransferRecipient('')
      setTimeout(() => { setSendFromPortfolio(null); setTopUpSuccess(null) }, 3000)
    } catch (err: any) {
      let msg = err?.message || String(err)
      if (msg.toLowerCase().includes('cancel') || msg.toLowerCase().includes('user rejected')) msg = 'Transaction cancelled.'
      setTopUpError(msg)
    } finally {
      setIsTransferring(false)
    }
  }

  const handleSendETH = async () => {
    if (!transferAmount || !transferRecipient || !userKey) {
      setTopUpError('Enter recipient and amount')
      return
    }
    if (!ethers.isAddress(transferRecipient)) {
      setTopUpError('Invalid recipient address')
      return
    }
    const amount = parseFloat(transferAmount)
    if (amount <= 0) {
      setTopUpError('Amount must be greater than 0')
      return
    }
    setIsTransferring(true)
    setTopUpError(null)
    setTopUpSuccess(null)
    try {
      if (!window.ethereum) throw new Error('No wallet connected')
      const provider = new ethers.BrowserProvider(window.ethereum)
      const signer = await provider.getSigner(userKey)
      const tx = await signer.sendTransaction({
        to: transferRecipient,
        value: ethers.parseEther(amount.toString()),
      })
      await tx.wait()
      await fetchMarketCredit()
      setTopUpSuccess(`Sent ${amount} ETH to ${transferRecipient.slice(0, 8)}...`)
      setTransferAmount('')
      setTransferRecipient('')
      setTimeout(() => { setSendFromPortfolio(null); setTopUpSuccess(null) }, 3000)
    } catch (err: any) {
      let msg = err?.message || String(err)
      if (msg.toLowerCase().includes('cancel') || msg.toLowerCase().includes('user rejected')) msg = 'Transaction cancelled.'
      setTopUpError(msg)
    } finally {
      setIsTransferring(false)
    }
  }

  const handleWithdraw = async () => {
    if (!withdrawAmount || !userKey) {
      setTopUpError('Please enter an amount')
      return
    }
    const amount = parseFloat(withdrawAmount)
    if (amount <= 0) {
      setTopUpError('Amount must be greater than 0')
      return
    }
    if (amount > marketCredit) {
      setTopUpError('Insufficient market credit balance')
      return
    }
    setIsWithdrawing(true)
    setTopUpError(null)
    setTopUpSuccess(null)
    try {
      if (typeof window === 'undefined' || !window.ethereum) {
        throw new Error('MetaMask is required for withdrawal')
      }
      const { Market } = await import('@/network/Market')
      const chainConfig = getChainConfig()
      if (!chainConfig) throw new Error('Chain config not found')
      const market = new Market(chainConfig)
      await market.withdrawMarketCredit(userKey, amount, withdrawTokenType)
      await fetchMarketCredit()
      setTopUpSuccess(`Successfully withdrew $${amount.toFixed(2)} as ${withdrawTokenType}!`)
      setWithdrawAmount('')
      setTimeout(() => { setShowWithdrawForm(false); setTopUpSuccess(null) }, 3000)
    } catch (err: any) {
      let msg = err?.message || String(err)
      if (msg.toLowerCase().includes('cancel') || msg.toLowerCase().includes('user rejected')) msg = 'Transaction cancelled by user.'
      else if (msg.includes('insufficient') || msg.includes('1010')) msg = 'Insufficient balance for withdrawal.'
      setTopUpError(msg)
    } finally {
      setIsWithdrawing(false)
    }
  }

  return {
    // Transfer
    transferRecipient, setTransferRecipient,
    transferAmount, setTransferAmount,
    isTransferring, transferTokenType, setTransferTokenType,
    sendFromPortfolio, setSendFromPortfolio,
    handleTransfer, handleSendCustomToken, handleSendETH,
    // Feedback
    topUpError, setTopUpError, topUpSuccess, setTopUpSuccess,
    // Top-up
    selectedToken, setSelectedToken,
    topUpAmount, setTopUpAmount,
    isProcessing, showTopUpForm, setShowTopUpForm,
    handleTopUpTransaction,
    // Withdraw
    withdrawAmount, setWithdrawAmount,
    isWithdrawing, withdrawTokenType, setWithdrawTokenType,
    showWithdrawForm, setShowWithdrawForm,
    handleWithdraw,
  }
}
