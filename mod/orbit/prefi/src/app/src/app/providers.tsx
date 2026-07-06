'use client'

import { useState, useEffect } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

const queryClient = new QueryClient()

function ProvidersInner({ children }: { children: React.ReactNode }) {
  const [mounted, setMounted] = useState(false)
  const [config, setConfig] = useState<any>(null)

  useEffect(() => {
    // Defer all wagmi/rainbowkit setup to client-side only
    async function init() {
      const { WagmiConfig, createConfig, configureChains } = await import('wagmi')
      const { publicProvider } = await import('wagmi/providers/public')
      const { RainbowKitProvider, getDefaultWallets } = await import('@rainbow-me/rainbowkit')

      const ganache = {
        id: 1337,
        name: 'Ganache',
        network: 'ganache',
        nativeCurrency: { name: 'Ether', symbol: 'ETH', decimals: 18 },
        rpcUrls: {
          default: { http: [process.env.NEXT_PUBLIC_GANACHE_RPC || 'http://localhost:7545'] },
          public: { http: [process.env.NEXT_PUBLIC_GANACHE_RPC || 'http://localhost:7545'] },
        },
        testnet: true,
      }

      const base = {
        id: 8453,
        name: 'Base',
        network: 'base',
        nativeCurrency: { name: 'Ether', symbol: 'ETH', decimals: 18 },
        rpcUrls: {
          default: { http: [process.env.NEXT_PUBLIC_BASE_RPC || 'https://mainnet.base.org'] },
          public: { http: [process.env.NEXT_PUBLIC_BASE_RPC || 'https://mainnet.base.org'] },
        },
        blockExplorers: {
          default: { name: 'BaseScan', url: 'https://basescan.org' },
        },
      }

      const baseSepolia = {
        id: 84532,
        name: 'Base Sepolia',
        network: 'base-sepolia',
        nativeCurrency: { name: 'Ether', symbol: 'ETH', decimals: 18 },
        rpcUrls: {
          default: { http: [process.env.NEXT_PUBLIC_BASE_SEPOLIA_RPC || 'https://sepolia.base.org'] },
          public: { http: [process.env.NEXT_PUBLIC_BASE_SEPOLIA_RPC || 'https://sepolia.base.org'] },
        },
        blockExplorers: {
          default: { name: 'BaseScan', url: 'https://sepolia.basescan.org' },
        },
        testnet: true,
      }

      const { chains, publicClient } = configureChains(
        [baseSepolia, base, ganache],
        [publicProvider()]
      )

      const { connectors } = getDefaultWallets({
        appName: 'PreFi Prediction Market',
        projectId: process.env.NEXT_PUBLIC_WALLETCONNECT_PROJECT_ID || 'prefi_local_dev',
        chains,
      })

      const wagmiConfig = createConfig({
        autoConnect: true,
        connectors,
        publicClient,
      })

      setConfig({ WagmiConfig, RainbowKitProvider, wagmiConfig, chains })
      setMounted(true)
    }

    init()
  }, [])

  if (!mounted || !config) {
    // Loading skeleton
    return (
      <QueryClientProvider client={queryClient}>
        <div className="min-h-screen flex items-center justify-center">
          <div className="text-center space-y-4">
            <div className="text-6xl float">🎯</div>
            <div className="text-2xl font-bold gradient-text">Loading PreFi...</div>
          </div>
        </div>
      </QueryClientProvider>
    )
  }

  const { WagmiConfig, RainbowKitProvider, wagmiConfig, chains } = config

  return (
    <WagmiConfig config={wagmiConfig}>
      <QueryClientProvider client={queryClient}>
        <RainbowKitProvider chains={chains} initialChain={chains[0]}>
          {children}
        </RainbowKitProvider>
      </QueryClientProvider>
    </WagmiConfig>
  )
}

export function Providers({ children }: { children: React.ReactNode }) {
  return <ProvidersInner>{children}</ProvidersInner>
}
