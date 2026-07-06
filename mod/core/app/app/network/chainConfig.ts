import modConfig from '@config'

export function getChainConfig() {
  const env = typeof window !== 'undefined'
    ? localStorage.getItem('network_env') || 'testnet'
    : 'testnet'
  return (modConfig.chain as any)?.[env] || (modConfig.chain as any)?.testnet
}

export function getContracts() {
  return getChainConfig()?.contracts || {}
}

export function getRpcUrl(): string {
  return getChainConfig()?.url || 'https://sepolia.base.org'
}
