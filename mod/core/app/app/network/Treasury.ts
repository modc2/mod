import { ethers } from 'ethers'
import TreasuryABI from '@/contracts//treasury/Treasury.sol/Treasury.json'
import TokenABI from '@/contracts//token/Token.sol/Token.json'
import { getChainConfig } from './chainConfig'

function getEthereumProvider(): ethers.BrowserProvider {
  if (typeof window === 'undefined') {
    throw new Error('Window is undefined - running in server context')
  }
  if (!window.ethereum) {
    throw new Error('No Ethereum provider found. Please install MetaMask or another wallet.')
  }
  return new ethers.BrowserProvider(window.ethereum)
}

function getTreasuryAddress(): string {
  const chainConfig = getChainConfig()
  if (!chainConfig?.contracts?.Treasury?.address) {
    throw new Error('Treasury address not found in config')
  }
  return chainConfig.contracts.Treasury.address
}

function getTokenAddress(tokenType: string): string {
  const chainConfig = getChainConfig()
  if (!chainConfig) throw new Error('Chain config not found')
  const addr = chainConfig.contracts?.[tokenType]?.address
  if (!addr) throw new Error(`Token ${tokenType} not found in config`)
  return addr
}

export interface TreasuryInfo {
  govToken: string
  tokens: string[]
  balances: bigint[]
  totalClaimedAmounts: bigint[]
  ownerPct: bigint
}

export interface HolderInfo {
  governanceBalance: bigint
  ownershipPercentage: bigint
  tokens: string[]
  claimedAmounts: bigint[]
  claimableAmounts: bigint[]
}

export interface TokenBalance {
  address: string
  symbol: string
  decimals: number
  balance: number
  balanceRaw: bigint
}

export class Treasury {
  private treasuryAddress: string

  constructor() {
    this.treasuryAddress = getTreasuryAddress()
  }

  private getContract(signerOrProvider?: ethers.Signer | ethers.Provider): ethers.Contract {
    return new ethers.Contract(this.treasuryAddress, TreasuryABI.abi, signerOrProvider)
  }

  get address(): string {
    return this.treasuryAddress
  }

  // ── Read Methods ──

  async getOwner(): Promise<string> {
    const provider = getEthereumProvider()
    const contract = this.getContract(provider)
    return await contract.owner()
  }

  async getTreasuryInfo(): Promise<TreasuryInfo> {
    const provider = getEthereumProvider()
    const contract = this.getContract(provider)
    const [govToken, tokens, balances, totalClaimedAmounts, ownerPct] = await contract.getTreasuryInfo()
    return { govToken, tokens, balances, totalClaimedAmounts, ownerPct }
  }

  async getHolderInfo(holder: string): Promise<HolderInfo> {
    const provider = getEthereumProvider()
    const contract = this.getContract(provider)
    const [governanceBalance, ownershipPercentage, tokens, claimedAmounts, claimableAmounts] = await contract.getHolderInfo(holder)
    return { governanceBalance, ownershipPercentage, tokens, claimedAmounts, claimableAmounts }
  }

  async getAllClaimableAmounts(holder: string): Promise<{ tokens: string[]; amounts: bigint[] }> {
    const provider = getEthereumProvider()
    const contract = this.getContract(provider)
    const [tokens, amounts] = await contract.getAllClaimableAmounts(holder)
    return { tokens, amounts }
  }

  async getOwnerClaimableAmount(token: string): Promise<bigint> {
    const provider = getEthereumProvider()
    const contract = this.getContract(provider)
    return await contract.getOwnerClaimableAmount(token)
  }

  async getTreasuryTokens(): Promise<string[]> {
    const provider = getEthereumProvider()
    const contract = this.getContract(provider)
    return await contract.getTreasuryTokens()
  }

  async getOwnerPercentage(): Promise<bigint> {
    const provider = getEthereumProvider()
    const contract = this.getContract(provider)
    return await contract.ownerPercentage()
  }

  async getGovernanceToken(): Promise<string> {
    const provider = getEthereumProvider()
    const contract = this.getContract(provider)
    return await contract.governanceToken()
  }

  async getTokenGate(): Promise<string> {
    const provider = getEthereumProvider()
    const contract = this.getContract(provider)
    return await contract.tokenGate()
  }

  async getTokenInfo(tokenAddress: string): Promise<{ symbol: string; decimals: number }> {
    try {
      const provider = getEthereumProvider()
      const tokenContract = new ethers.Contract(tokenAddress, TokenABI.abi, provider)
      const [symbol, decimals] = await Promise.all([
        tokenContract.symbol(),
        tokenContract.decimals(),
      ])
      return { symbol, decimals: Number(decimals) }
    } catch {
      return { symbol: 'UNKNOWN', decimals: 18 }
    }
  }

  async getTokenBalances(): Promise<TokenBalance[]> {
    const provider = getEthereumProvider()
    const info = await this.getTreasuryInfo()
    const results: TokenBalance[] = []

    for (let i = 0; i < info.tokens.length; i++) {
      const tokenAddress = info.tokens[i]
      const { symbol, decimals } = await this.getTokenInfo(tokenAddress)
      const balanceRaw = info.balances[i]
      const balance = parseFloat(ethers.formatUnits(balanceRaw, decimals))
      results.push({ address: tokenAddress, symbol, decimals, balance, balanceRaw })
    }

    return results
  }

  // ── Owner Write Methods ──

  async ownerWithdraw(userAddress: string, token: string): Promise<ethers.TransactionReceipt> {
    const provider = getEthereumProvider()
    const signer = await provider.getSigner(userAddress)
    const contract = this.getContract(signer)
    const tx = await contract.ownerWithdraw(token)
    return await tx.wait()
  }

  async emergencyWithdraw(userAddress: string, token: string, amount: bigint): Promise<ethers.TransactionReceipt> {
    const provider = getEthereumProvider()
    const signer = await provider.getSigner(userAddress)
    const contract = this.getContract(signer)
    const tx = await contract.emergencyWithdraw(token, amount)
    return await tx.wait()
  }

  async setOwnerPercentage(userAddress: string, percentage: number): Promise<ethers.TransactionReceipt> {
    const provider = getEthereumProvider()
    const signer = await provider.getSigner(userAddress)
    const contract = this.getContract(signer)
    const tx = await contract.setOwnerPercentage(percentage)
    return await tx.wait()
  }

  async setGovernanceToken(userAddress: string, tokenAddress: string): Promise<ethers.TransactionReceipt> {
    const provider = getEthereumProvider()
    const signer = await provider.getSigner(userAddress)
    const contract = this.getContract(signer)
    const tx = await contract.setGovernanceToken(tokenAddress)
    return await tx.wait()
  }

  async setTokenGate(userAddress: string, tokenGateAddress: string): Promise<ethers.TransactionReceipt> {
    const provider = getEthereumProvider()
    const signer = await provider.getSigner(userAddress)
    const contract = this.getContract(signer)
    const tx = await contract.setTokenGate(tokenGateAddress)
    return await tx.wait()
  }

  async transferOwnership(userAddress: string, newOwner: string): Promise<ethers.TransactionReceipt> {
    const provider = getEthereumProvider()
    const signer = await provider.getSigner(userAddress)
    const contract = this.getContract(signer)
    const tx = await contract.transferOwnership(newOwner)
    return await tx.wait()
  }

  // ── Holder Write Methods ──

  async withdrawToken(userAddress: string, token: string): Promise<ethers.TransactionReceipt> {
    const provider = getEthereumProvider()
    const signer = await provider.getSigner(userAddress)
    const contract = this.getContract(signer)
    const tx = await contract.withdrawToken(token)
    return await tx.wait()
  }

  async withdrawAll(userAddress: string): Promise<ethers.TransactionReceipt> {
    const provider = getEthereumProvider()
    const signer = await provider.getSigner(userAddress)
    const contract = this.getContract(signer)
    const tx = await contract.withdrawAll()
    return await tx.wait()
  }

  async fundTreasury(userAddress: string, token: string, amount: bigint): Promise<ethers.TransactionReceipt> {
    const provider = getEthereumProvider()
    const signer = await provider.getSigner(userAddress)
    const contract = this.getContract(signer)
    const tx = await contract.fundTreasury(token, amount)
    return await tx.wait()
  }

  // ── Helpers for Safe transaction encoding ──

  encodeFunctionData(functionName: string, args: any[]): string {
    const iface = new ethers.Interface(TreasuryABI.abi)
    return iface.encodeFunctionData(functionName, args)
  }
}
