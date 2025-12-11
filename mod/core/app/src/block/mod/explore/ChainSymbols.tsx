'use client'

import { Globe, Link2, Database, Coins, Cpu, Zap } from 'lucide-react'

export type ChainType = 'local' | 'ethereum' | 'bitcoin' | 'solana' | 'polkadot' | 'avalanche' | 'polygon' | 'arbitrum' | 'optimism' | 'base'

interface ChainConfig {
  id: ChainType
  label: string
  icon: any
  color: string
  bgColor: string
  borderColor: string
}

export const CHAIN_CONFIGS: Record<ChainType, ChainConfig> = {
  local: {
    id: 'local',
    label: 'Local',
    icon: Cpu,
    color: '#22c55e',
    bgColor: 'rgba(34, 197, 94, 0.08)',
    borderColor: 'rgba(34, 197, 94, 0.6)'
  },
  ethereum: {
    id: 'ethereum',
    label: 'Ethereum',
    icon: Coins,
    color: '#627eea',
    bgColor: 'rgba(98, 126, 234, 0.08)',
    borderColor: 'rgba(98, 126, 234, 0.6)'
  },
  bitcoin: {
    id: 'bitcoin',
    label: 'Bitcoin',
    icon: Coins,
    color: '#f7931a',
    bgColor: 'rgba(247, 147, 26, 0.08)',
    borderColor: 'rgba(247, 147, 26, 0.6)'
  },
  solana: {
    id: 'solana',
    label: 'Solana',
    icon: Zap,
    color: '#14f195',
    bgColor: 'rgba(20, 241, 149, 0.08)',
    borderColor: 'rgba(20, 241, 149, 0.6)'
  },
  polkadot: {
    id: 'polkadot',
    label: 'Polkadot',
    icon: Link2,
    color: '#e6007a',
    bgColor: 'rgba(230, 0, 122, 0.08)',
    borderColor: 'rgba(230, 0, 122, 0.6)'
  },
  avalanche: {
    id: 'avalanche',
    label: 'Avalanche',
    icon: Database,
    color: '#e84142',
    bgColor: 'rgba(232, 65, 66, 0.08)',
    borderColor: 'rgba(232, 65, 66, 0.6)'
  },
  polygon: {
    id: 'polygon',
    label: 'Polygon',
    icon: Globe,
    color: '#8247e5',
    bgColor: 'rgba(130, 71, 229, 0.08)',
    borderColor: 'rgba(130, 71, 229, 0.6)'
  },
  arbitrum: {
    id: 'arbitrum',
    label: 'Arbitrum',
    icon: Link2,
    color: '#28a0f0',
    bgColor: 'rgba(40, 160, 240, 0.08)',
    borderColor: 'rgba(40, 160, 240, 0.6)'
  },
  optimism: {
    id: 'optimism',
    label: 'Optimism',
    icon: Coins,
    color: '#ff0420',
    bgColor: 'rgba(255, 4, 32, 0.08)',
    borderColor: 'rgba(255, 4, 32, 0.6)'
  },
  base: {
    id: 'base',
    label: 'Base',
    icon: Database,
    color: '#0052ff',
    bgColor: 'rgba(0, 82, 255, 0.08)',
    borderColor: 'rgba(0, 82, 255, 0.6)'
  }
}

export const getChainConfig = (network: string): ChainConfig => {
  const normalizedNetwork = network?.toLowerCase() || 'local'
  return CHAIN_CONFIGS[normalizedNetwork as ChainType] || CHAIN_CONFIGS.local
}

interface ChainSymbolProps {
  network: string
  size?: number
  showLabel?: boolean
  className?: string
}

export const ChainSymbol = ({ network, size = 16, showLabel = true, className = '' }: ChainSymbolProps) => {
  const config = getChainConfig(network)
  const Icon = config.icon

  return (
    <div 
      className={`flex items-center gap-2 px-3 py-2 rounded-md border ${className}`}
      style={{ 
        backgroundColor: config.bgColor, 
        borderColor: config.borderColor 
      }}
    >
      <Icon size={size} style={{ color: config.color }} />
      {showLabel && (
        <code 
          className="text-lg font-mono font-bold" 
          style={{ color: config.color, fontFamily: "'Courier New', 'Consolas', 'Monaco', monospace" }}
        >
          {config.label}
        </code>
      )}
    </div>
  )
}
