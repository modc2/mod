'use client';

import { useState } from 'react';
import { useAccount, useWriteContract, useReadContract } from 'wagmi';
import { parseUnits, formatUnits } from 'viem';
import { ConnectButton } from '@rainbow-me/rainbowkit';

const TOKENS = {
  USDC: { address: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913', decimals: 6 },
  USDT: { address: '0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2', decimals: 6 },
  ETH: { address: '0x4200000000000000000000000000000000000006', decimals: 18 },
};

const PROTOCOLS = ['aave', 'compound'];
const CONTRACT_ADDRESS = '0x0000000000000000000000000000000000000000'; // Deploy and update

export default function Home() {
  const { address, isConnected } = useAccount();
  const [selectedToken, setSelectedToken] = useState('USDC');
  const [selectedProtocol, setSelectedProtocol] = useState('aave');
  const [amount, setAmount] = useState('');
  const [action, setAction] = useState<'deposit' | 'withdraw'>('deposit');

  const { writeContract } = useWriteContract();

  const handleDeposit = async () => {
    if (!amount || !isConnected) return;
    
    const token = TOKENS[selectedToken as keyof typeof TOKENS];
    const amountWei = parseUnits(amount, token.decimals);

    writeContract({
      address: CONTRACT_ADDRESS as `0x${string}`,
      abi: [
        {
          name: 'depositToProtocol',
          type: 'function',
          stateMutability: 'nonpayable',
          inputs: [
            { name: 'token', type: 'address' },
            { name: 'amount', type: 'uint256' },
            { name: 'protocol', type: 'string' },
          ],
          outputs: [],
        },
      ],
      functionName: 'depositToProtocol',
      args: [token.address as `0x${string}`, amountWei, selectedProtocol],
    });
  };

  const handleWithdraw = async () => {
    if (!amount || !isConnected) return;
    
    const token = TOKENS[selectedToken as keyof typeof TOKENS];
    const amountWei = parseUnits(amount, token.decimals);

    writeContract({
      address: CONTRACT_ADDRESS as `0x${string}`,
      abi: [
        {
          name: 'withdrawFromProtocol',
          type: 'function',
          stateMutability: 'nonpayable',
          inputs: [
            { name: 'token', type: 'address' },
            { name: 'amount', type: 'uint256' },
            { name: 'protocol', type: 'string' },
          ],
          outputs: [],
        },
      ],
      functionName: 'withdrawFromProtocol',
      args: [token.address as `0x${string}`, amountWei, selectedProtocol],
    });
  };

  return (
    <main className="min-h-screen bg-gradient-to-br from-blue-900 via-purple-900 to-black text-white">
      <div className="container mx-auto px-4 py-8">
        <div className="flex justify-between items-center mb-12">
          <h1 className="text-5xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-blue-400 to-purple-400">
            ModFi - Lending Aggregator
          </h1>
          <ConnectButton />
        </div>

        {isConnected ? (
          <div className="max-w-2xl mx-auto bg-white/10 backdrop-blur-lg rounded-2xl p-8 shadow-2xl">
            <div className="mb-6">
              <label className="block text-sm font-medium mb-2">Action</label>
              <div className="flex gap-4">
                <button
                  onClick={() => setAction('deposit')}
                  className={`flex-1 py-3 rounded-lg font-semibold transition ${
                    action === 'deposit'
                      ? 'bg-blue-600 text-white'
                      : 'bg-white/5 text-gray-300 hover:bg-white/10'
                  }`}
                >
                  Deposit
                </button>
                <button
                  onClick={() => setAction('withdraw')}
                  className={`flex-1 py-3 rounded-lg font-semibold transition ${
                    action === 'withdraw'
                      ? 'bg-purple-600 text-white'
                      : 'bg-white/5 text-gray-300 hover:bg-white/10'
                  }`}
                >
                  Withdraw
                </button>
              </div>
            </div>

            <div className="mb-6">
              <label className="block text-sm font-medium mb-2">Token</label>
              <select
                value={selectedToken}
                onChange={(e) => setSelectedToken(e.target.value)}
                className="w-full bg-white/5 border border-white/10 rounded-lg px-4 py-3 text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                {Object.keys(TOKENS).map((token) => (
                  <option key={token} value={token} className="bg-gray-900">
                    {token}
                  </option>
                ))}
              </select>
            </div>

            <div className="mb-6">
              <label className="block text-sm font-medium mb-2">Protocol</label>
              <select
                value={selectedProtocol}
                onChange={(e) => setSelectedProtocol(e.target.value)}
                className="w-full bg-white/5 border border-white/10 rounded-lg px-4 py-3 text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                {PROTOCOLS.map((protocol) => (
                  <option key={protocol} value={protocol} className="bg-gray-900">
                    {protocol.charAt(0).toUpperCase() + protocol.slice(1)}
                  </option>
                ))}
              </select>
            </div>

            <div className="mb-6">
              <label className="block text-sm font-medium mb-2">Amount</label>
              <input
                type="number"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                placeholder="0.0"
                className="w-full bg-white/5 border border-white/10 rounded-lg px-4 py-3 text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>

            <button
              onClick={action === 'deposit' ? handleDeposit : handleWithdraw}
              className="w-full bg-gradient-to-r from-blue-600 to-purple-600 hover:from-blue-700 hover:to-purple-700 text-white font-bold py-4 rounded-lg transition transform hover:scale-105"
            >
              {action === 'deposit' ? 'Deposit' : 'Withdraw'}
            </button>
          </div>
        ) : (
          <div className="text-center py-20">
            <p className="text-2xl text-gray-400">Connect your wallet to get started</p>
          </div>
        )}
      </div>
    </main>
  );
}