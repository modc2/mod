import { ethers } from 'ethers'

// PreFiV3 ABI - matches contracts/PreFiV3.sol
export const PREFI_V3_ABI = [
  // Read functions
  'function marketCounter() view returns (uint256)',
  'function stakeToken() view returns (address)',
  'function oracle() view returns (address)',
  'function minStake() view returns (uint256)',
  'function platformFee() view returns (uint256)',
  'function owner() view returns (address)',
  'function getMarketInfo(uint256 marketId) view returns (string asset, address tokenAddress, uint256 startTime, uint256 endTime, bool settled, uint256 actualPrice, uint256 totalStaked, uint256 playersCount)',
  'function getPrediction(uint256 marketId, address player) view returns (uint256 predictedPrice, uint256 stakedAmount, uint256 timestamp, bool claimed, uint256 reward)',
  'function calculateScore(uint256 predicted, uint256 actual, uint256 stake) pure returns (uint256 score)',

  // Write functions
  'function createMarket(string asset, address tokenAddress, uint256 duration) returns (uint256)',
  'function predict(uint256 marketId, uint256 predictedPrice, uint256 stakeAmount)',
  'function resolveMarket(uint256 marketId, uint256 actualPrice)',
  'function claimReward(uint256 marketId)',
  'function setOracle(address _oracle)',
  'function setMinStake(uint256 _minStake)',
  'function setPlatformFee(uint256 _platformFee)',
  'function withdrawFees()',
  'function pause()',
  'function unpause()',

  // Events
  'event MarketCreated(uint256 indexed marketId, string asset, address tokenAddress, uint256 startTime, uint256 endTime)',
  'event PredictionPlaced(uint256 indexed marketId, address indexed player, uint256 predictedPrice, uint256 stakedAmount)',
  'event MarketSettled(uint256 indexed marketId, uint256 actualPrice, uint256 totalRewards)',
  'event RewardClaimed(uint256 indexed marketId, address indexed player, uint256 reward)',
]

// Standard ERC20 ABI
export const ERC20_ABI = [
  'function name() view returns (string)',
  'function symbol() view returns (string)',
  'function decimals() view returns (uint8)',
  'function totalSupply() view returns (uint256)',
  'function balanceOf(address owner) view returns (uint256)',
  'function allowance(address owner, address spender) view returns (uint256)',
  'function approve(address spender, uint256 amount) returns (bool)',
  'function transfer(address to, uint256 amount) returns (bool)',
  'function transferFrom(address from, address to, uint256 amount) returns (bool)',
  'function mint(address to, uint256 amount)',
]

// Contract addresses per chain
const CONTRACT_ADDRESSES: Record<number, Record<string, string>> = {
  // Base Mainnet
  8453: {
    preFiV3: process.env.NEXT_PUBLIC_PREFI_V3_ADDRESS || '',
    stakeToken: process.env.NEXT_PUBLIC_STAKE_TOKEN_ADDRESS || '',
    oracle: process.env.NEXT_PUBLIC_ORACLE_ADDRESS || '',
  },
  // Base Sepolia
  84532: {
    preFiV3: process.env.NEXT_PUBLIC_PREFI_V3_ADDRESS || '',
    stakeToken: process.env.NEXT_PUBLIC_STAKE_TOKEN_ADDRESS || '',
    oracle: process.env.NEXT_PUBLIC_ORACLE_ADDRESS || '',
  },
  // Ganache local
  1337: {
    preFiV3: process.env.NEXT_PUBLIC_PREFI_V3_ADDRESS || '',
    stakeToken: process.env.NEXT_PUBLIC_STAKE_TOKEN_ADDRESS || '',
    oracle: process.env.NEXT_PUBLIC_ORACLE_ADDRESS || '',
  },
  // Hardhat local
  31337: {
    preFiV3: process.env.NEXT_PUBLIC_PREFI_V3_ADDRESS || '',
    stakeToken: process.env.NEXT_PUBLIC_STAKE_TOKEN_ADDRESS || '',
    oracle: process.env.NEXT_PUBLIC_ORACLE_ADDRESS || '',
  },
}

export function getContractAddress(chainId: number, contract: string): string {
  return CONTRACT_ADDRESSES[chainId]?.[contract] || ''
}

export function parsePrice(value: string, decimals: number = 18): bigint {
  return ethers.parseUnits(value, decimals)
}

export function formatPrice(value: bigint | string | number, decimals: number = 18): string {
  try {
    return ethers.formatUnits(value, decimals)
  } catch {
    return '0'
  }
}

// API base URL for the FastAPI backend
export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8830'
