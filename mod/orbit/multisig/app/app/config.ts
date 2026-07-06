export const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:50100'

export const CHAINS = {
  base: {
    name: 'Base',
    symbol: 'ETH',
    decimals: 18,
    wallet: 'MetaMask',
    color: '#0052FF',
    chainId: 8453,
    rpc: 'https://mainnet.base.org',
    explorer: 'https://basescan.org',
  },
  tao: {
    name: 'TAO',
    symbol: 'TAO',
    decimals: 9,
    wallet: 'SubWallet',
    color: '#1A1A2E',
    explorer: 'https://tao.explorer.opentensor.ai',
  },
  solana: {
    name: 'Solana',
    symbol: 'SOL',
    decimals: 9,
    wallet: 'Phantom',
    color: '#9945FF',
    explorer: 'https://solscan.io',
  },
} as const

export type ChainId = keyof typeof CHAINS
