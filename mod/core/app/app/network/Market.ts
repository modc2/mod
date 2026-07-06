import { ethers } from 'ethers'
import TokenABI from '@/contracts/token/Token.sol/Token.json'
import MarketABI from '@/contracts/market/Market.sol/Market.json'
import { getChainConfig, getRpcUrl } from './chainConfig'

function getReadProvider(): ethers.JsonRpcProvider {
  return new ethers.JsonRpcProvider(getRpcUrl())
}

function getWriteProvider(): ethers.BrowserProvider {
  if (typeof window === 'undefined') {
    throw new Error('Window is undefined - running in server context')
  }
  if (!window.ethereum) {
    throw new Error('No Ethereum provider found. Please install MetaMask or another wallet.')
  }
  return new ethers.BrowserProvider(window.ethereum)
}

export class Market {
  private config: any

  constructor(config: any) {
    this.config = config
  }

  public getTokenAddress(tokenType: string): string {
    const chainCfg = getChainConfig()
    if (!chainCfg) {
      throw new Error('Chain config not found')
    }

    const tokenAddress = (chainCfg.contracts as Record<string, any>)[tokenType]?.address
    if (!tokenAddress) {
      throw new Error(`Token ${tokenType} not found in config`)
    }

    return tokenAddress
  }

  public async getTokenDecimals(tokenAddress: string): Promise<number> {
    try {
      const provider = getReadProvider()
      const tokenContract = new ethers.Contract(tokenAddress, TokenABI.abi, provider)
      const decimals = await tokenContract.decimals()
      return Number(decimals)
    } catch (error) {
      console.error('Error fetching token decimals:', error)
      return 18
    }
  }

  async getTokenABI(tokenAddress: string): Promise<any> {
    if (tokenAddress.toLowerCase() === this.config.contracts.Market.address.toLowerCase()) {
      return MarketABI.abi
    }
    return TokenABI.abi
  }

  async checkBalance(userAddress: string, tokenType: string = 'USDC'): Promise<number> {
    try {
      const provider = getReadProvider()
      const tokenAddress = this.getTokenAddress(tokenType)

      const abi = await this.getTokenABI(tokenAddress)
      const tokenContract = new ethers.Contract(tokenAddress, abi, provider)
      const balance = await tokenContract.balanceOf(userAddress)
      const decimals = await this.getTokenDecimals(tokenAddress)
      console.log(`Balance for ${tokenType} at ${tokenAddress}:`, balance.toString())
      return parseFloat(ethers.formatUnits(balance, decimals))
    } catch (error) {
      console.error('Error checking balance:', error)
      throw error
    }
  }

  async checkMarketBalance(userAddress: string): Promise<number> {
    try {
      const provider = getReadProvider()
      const marketAddress = this.config.contracts.Market.address

      if (!marketAddress) {
        console.error('Market address not found in config')
        return 0
      }

      const marketContract = new ethers.Contract(marketAddress, MarketABI.abi, provider)

      // Market contract uses 8 decimals (USD format, matching oracle price decimals)
      const balance = await marketContract.balanceOf(userAddress)
      const balanceFormatted = parseFloat(ethers.formatUnits(balance, 8))

      // console.log(`Market balance for ${userAddress}:`, balanceFormatted)
      return balanceFormatted
    } catch (error) {
      console.error('Error checking market balance:', error)
      // Return 0 instead of throwing to prevent UI breaks
      return 0
    }
  }

  async checkMarketAllowance(userAddress: string, tokenType: string = 'USDC'): Promise<number> {
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

      // maxPaymentAmount: use MaxUint256 to skip slippage protection
      const tx = await marketContract.credit(tokenAddress, amountInWei, ethers.MaxUint256)
      const receipt = await tx.wait()

      return receipt
    } catch (error) {
      console.error('Error adding market credit:', error)
      throw error
    }
  }

  async withdrawMarketCredit(userAddress: string, amount: number, tokenType: 'USDC' | 'USDT' = 'USDC'): Promise<any> {
    try {
      const provider = getWriteProvider()
      const signer = await provider.getSigner(userAddress)
      const marketAddress = this.config.contracts.Market.address
      const tokenAddress = this.getTokenAddress(tokenType)

      const marketContract = new ethers.Contract(marketAddress, MarketABI.abi, signer)
      // stableAmount is in Market decimals (8) — contract handles conversion to payment token amount
      const amountInWei = ethers.parseUnits(amount.toString(), 8)

      // minReceiveAmount: use 0 to skip slippage protection
      const tx = await marketContract.withdraw(tokenAddress, amountInWei, BigInt(0))
      const receipt = await tx.wait()

      return receipt
    } catch (error) {
      console.error('Error withdrawing market credit:', error)
      throw error
    }
  }

  async transferMarketCredit(fromAddress: string, toAddress: string, amount: number): Promise<any> {
    try {
      const provider = getWriteProvider()
      const signer = await provider.getSigner(fromAddress)
      const marketAddress = this.config.contracts.Market.address

      const marketContract = new ethers.Contract(marketAddress, MarketABI.abi, signer)
      // Market credit uses 8 decimals (USD format)
      const amountInWei = ethers.parseUnits(amount.toString(), 8)

      const tx = await marketContract.transfer(toAddress, amountInWei)
      const receipt = await tx.wait()

      return receipt
    } catch (error) {
      console.error('Error transferring market credit:', error)
      throw error
    }
  }
}
