"use client";
import { useState } from 'react';

export default function Home() {
  const [connected, setConnected] = useState(false);
  const [balance, setBalance] = useState('0');

  const connectWallet = async () => {
    if (typeof window.ethereum !== 'undefined') {
      try {
        await window.ethereum.request({ method: 'eth_requestAccounts' });
        setConnected(true);
      } catch (err) {
        console.error('Failed to connect:', err);
      }
    }
  };

  return (
    <main className="min-h-screen bg-gradient-to-br from-purple-900 to-black text-white p-8">
      <div className="max-w-4xl mx-auto">
        <h1 className="text-5xl font-bold mb-8 text-center">🚀 NEWMA</h1>
        <p className="text-xl text-center mb-12">Smart Contract on Base Network</p>
        
        <div className="bg-white/10 rounded-2xl p-8 backdrop-blur">
          {!connected ? (
            <button
              onClick={connectWallet}
              className="w-full bg-purple-600 hover:bg-purple-700 py-4 rounded-xl text-xl font-bold transition"
            >
              Connect Wallet
            </button>
          ) : (
            <div className="text-center">
              <p className="text-green-400 text-xl mb-4">✅ Connected</p>
              <p className="text-2xl">Balance: {balance} NEWMA</p>
            </div>
          )}
        </div>
      </div>
    </main>
  );
}
