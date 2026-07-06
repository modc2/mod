import { ethers } from 'ethers'
import TokenABI from '@/contracts//token/Token.sol/Token.json'
import MarketABI from '@/contracts//market/Market.sol/Market.json'
import { getChainConfig, getRpcUrl } from './chainConfig'

function getReadProvider(): ethers.JsonRpcProvider {
  return new ethers.JsonRpcProvider(getRpcUrl())
}

function getWriteProvider(): ethers.BrowserProvider {
  if (typeof window === 'undefined' || !window.ethereum) {
    throw new Error('No Ethereum provider found. Please install MetaMask or another wallet.')
  }
  return new ethers.BrowserProvider(window.ethereum)
}

export class MarketAllowanceManager {
  private config: any

  constructor(config: any) {
    this.config = config
  }

  private getTokenAddress(tokenType: 'USDC' | 'USDT'): string {
    const chainCfg = getChainConfig()
    if (!chainCfg) {
      throw new Error('Chain config not found')
    }

    const tokenAddress = chainCfg.contracts[tokenType]?.address
    if (!tokenAddress) {
      throw new Error(`Token ${tokenType} not found in config`)
    }

    return tokenAddress
  }

  private async getTokenDecimals(tokenAddress: string): Promise<number> {
    try {
      const provider = getReadProvider()
      const tokenContract = new ethers.Contract(tokenAddress, TokenABI.abi, provider)
      const decimals = await tokenContract.decimals()
      return Number(decimals)
    } catch (error) {
      console.error('Error fetching token decimals:', error)
      return 18 // fallback to 18 if unable to fetch
    }
  }

  async checkMarketAllowance(userAddress: string, tokenType: 'USDC' | 'USDT' = 'USDC'): Promise<number> {
    try {
      const provider = getReadProvider()
      const tokenAddress = this.getTokenAddress(tokenType)
      const marketAddress = this.config.contracts.Market.address
      
      const tokenContract = new ethers.Contract(tokenAddress, TokenABI.abi, provider)
      const allowance = await tokenContract.allowance(userAddress, marketAddress)
      const decimals = await this.getTokenDecimals(tokenAddress)

      return parseFloat(ethers.formatUnits(allowance, decimals))
    } catch (error) {
      console.error('Error checking market allowance:', error)
      throw error
    }
  }

  async increaseMarketAllowance(userAddress: string, amount: number, tokenType: 'USDC' | 'USDT' = 'USDC'): Promise<any> {
    try {
      const provider = getWriteProvider()
      const signer = await provider.getSigner(userAddress)
      const tokenAddress = this.getTokenAddress(tokenType)
      const marketAddress = this.config.contracts.Market.address
      
      const tokenContract = new ethers.Contract(tokenAddress, TokenABI.abi, signer)
      const decimals = await this.getTokenDecimals(tokenAddress)
      const amountInWei = ethers.parseUnits(amount.toString(), decimals)
      
      const tx = await tokenContract.approve(marketAddress, amountInWei)
      const receipt = await tx.wait()
      
      return receipt
    } catch (error) {
      console.error('Error increasing market allowance:', error)
      throw error
    }
  }

  async addMarketCredit(userAddress: string, amount: number, tokenType: 'USDC' | 'USDT' = 'USDC'): Promise<any> {
    try {
      const provider = getWriteProvider()
      const signer = await provider.getSigner(userAddress)
      const marketAddress = this.config.contracts.Market.address
      const tokenAddress = this.getTokenAddress(tokenType)

      const marketContract = new ethers.Contract(marketAddress, MarketABI.abi, signer)
      // stableAmount is in Market decimals (8) — contract handles conversion to payment token amount
      const amountInWei = ethers.parseUnits(amount.toString(), 8)

      const tx = await marketContract.credit(tokenAddress, amountInWei, ethers.MaxUint256)
      const receipt = await tx.wait()

      return receipt
    } catch (error) {
      console.error('Error adding market credit:', error)
      throw error
    }
  }
}
