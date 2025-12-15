'use client';

import { useState } from 'react';
import { useAccount, useConnect, useDisconnect } from 'wagmi';
import { SwapWidget } from '@/components/SwapWidget';
import { ConnectButton } from '@rainbow-me/rainbowkit';

export default function Home() {
  const { address, isConnected } = useAccount();

  return (
    <main className="min-h-screen bg-gradient-to-br from-blue-900 via-purple-900 to-black">
      <div className="container mx-auto px-4 py-8">
        <header className="flex justify-between items-center mb-12">
          <h1 className="text-4xl font-bold text-white">Uniswap V3/V4 on Base</h1>
          <ConnectButton />
        </header>

        {isConnected ? (
          <div className="max-w-md mx-auto">
            <SwapWidget />
          </div>
        ) : (
          <div className="text-center text-white mt-20">
            <h2 className="text-2xl mb-4">Connect your wallet to start trading</h2>
            <p className="text-gray-400">Support for Uniswap V3 and V4 on Base Network</p>
          </div>
        )}
      </div>
    </main>
  );
}