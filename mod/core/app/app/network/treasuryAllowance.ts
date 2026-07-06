import { ethers } from 'ethers'
import TokenABI from '@/contracts//token/Token.sol/Token.json'
import TreasuryABI from '@/contracts//treasury/Treasury.sol/Treasury.json'
import { getChainConfig } from './chainConfig'

export class TreasuryAllowanceManager {
  private config: any

  constructor(config: any) {
    this.config = config
  }

  private getTokenAddress(tokenType: 'USDC' | 'USDT'): string {
    const chainConfig = getChainConfig()
    if (!chainConfig) {
      throw new Error('Chain config not found')
    }
    
    const tokenAddress = chainConfig.contracts[tokenType]?.address
    if (!tokenAddress) {
      throw new Error(`Token ${tokenType} not found in config`)
    }
    
    return tokenAddress
  }

  private async getTokenDecimals(tokenAddress: string): Promise<number> {
    try {
      const provider = new ethers.BrowserProvider((window as any).ethereum)
      const tokenContract = new ethers.Contract(tokenAddress, TokenABI.abi, provider)
      const decimals = await tokenContract.decimals()
      return Number(decimals)
    } catch (error) {
      console.error('Error fetching token decimals:', error)
      return 18 // fallback to 18 if unable to fetch
    }
  }

  async checkTreasuryAllowance(userAddress: string, tokenType: 'USDC' | 'USDT' = 'USDC'): Promise<number> {
    try {
      const provider = new ethers.BrowserProvider((window as any).ethereum)
      const tokenAddress = this.getTokenAddress(tokenType)
      const treasuryAddress = this.config.contracts.Treasury.address
      
      const tokenContract = new ethers.Contract(tokenAddress, TokenABI.abi, provider)
      const allowance = await tokenContract.allowance(userAddress, treasuryAddress)
      const decimals = await this.getTokenDecimals(tokenAddress)

      return parseFloat(ethers.formatUnits(allowance, decimals))
    } catch (error) {
      console.error('Error checking treasury allowance:', error)
      throw error
    }
  }

  async setTreasuryAllowance(userAddress: string, amount: number, tokenType: 'USDC' | 'USDT' = 'USDC'): Promise<any> {
    try {
      const provider = new ethers.BrowserProvider((window as any).ethereum)
      const signer = await provider.getSigner(userAddress)
      const tokenAddress = this.getTokenAddress(tokenType)
      const treasuryAddress = this.config.contracts.Treasury.address
      
      const tokenContract = new ethers.Contract(tokenAddress, TokenABI.abi, signer)
      const decimals = await this.getTokenDecimals(tokenAddress)
      const amountInWei = ethers.parseUnits(amount.toString(), decimals)
      
      const tx = await tokenContract.approve(treasuryAddress, amountInWei)
      const receipt = await tx.wait()
      
      return receipt
    } catch (error) {
      console.error('Error setting treasury allowance:', error)
      throw error
    }
  }

  async getTreasuryBalance(userAddress: string, tokenType: 'USDC' | 'USDT' = 'USDC'): Promise<number> {
    try {
      const provider = new ethers.BrowserProvider((window as any).ethereum)
      const tokenAddress = this.getTokenAddress(tokenType)
      
      const tokenContract = new ethers.Contract(tokenAddress, TokenABI.abi, provider)
      const balance = await tokenContract.balanceOf(userAddress)
      const decimals = await this.getTokenDecimals(tokenAddress)

      return parseFloat(ethers.formatUnits(balance, decimals))
    } catch (error) {
      console.error('Error fetching treasury balance:', error)
      throw error
    }
  }
}
