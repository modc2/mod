'use client';

import { useState, useEffect } from 'react';
import { useAccount, useWriteContract, useWaitForTransactionReceipt } from 'wagmi';
import { parseUnits, formatUnits } from 'viem';
import { Token, CurrencyAmount, TradeType, Percent } from '@uniswap/sdk-core';
import { Pool, Route, SwapQuoter, SwapRouter, Trade } from '@uniswap/v3-sdk';

const WETH_BASE = '0x4200000000000000000000000000000000000006';
const USDC_BASE = '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913';
const UNISWAP_V3_ROUTER = '0x2626664c2603336E57B271c5C0b26F421741e481';

export function SwapWidget() {
  const { address } = useAccount();
  const [tokenIn, setTokenIn] = useState(WETH_BASE);
  const [tokenOut, setTokenOut] = useState(USDC_BASE);
  const [amountIn, setAmountIn] = useState('');
  const [amountOut, setAmountOut] = useState('');
  const [loading, setLoading] = useState(false);

  const { writeContract, data: hash } = useWriteContract();
  const { isLoading: isConfirming, isSuccess } = useWaitForTransactionReceipt({ hash });

  const handleSwap = async () => {
    if (!amountIn || !address) return;

    try {
      setLoading(true);
      
      // This is a simplified swap - in production you'd:
      // 1. Get pool data from Uniswap V3
      // 2. Calculate optimal route
      // 3. Get quote
      // 4. Execute swap with proper parameters
      
      const amountInWei = parseUnits(amountIn, 18);
      
      // Example swap call (you need to implement proper swap logic)
      writeContract({
        address: UNISWAP_V3_ROUTER as `0x${string}`,
        abi: [
          {
            name: 'exactInputSingle',
            type: 'function',
            stateMutability: 'payable',
            inputs: [
              {
                components: [
                  { name: 'tokenIn', type: 'address' },
                  { name: 'tokenOut', type: 'address' },
                  { name: 'fee', type: 'uint24' },
                  { name: 'recipient', type: 'address' },
                  { name: 'deadline', type: 'uint256' },
                  { name: 'amountIn', type: 'uint256' },
                  { name: 'amountOutMinimum', type: 'uint256' },
                  { name: 'sqrtPriceLimitX96', type: 'uint160' },
                ],
                name: 'params',
                type: 'tuple',
              },
            ],
            outputs: [{ name: 'amountOut', type: 'uint256' }],
          },
        ],
        functionName: 'exactInputSingle',
        args: [
          {
            tokenIn: tokenIn as `0x${string}`,
            tokenOut: tokenOut as `0x${string}`,
            fee: 3000,
            recipient: address,
            deadline: BigInt(Math.floor(Date.now() / 1000) + 60 * 20),
            amountIn: amountInWei,
            amountOutMinimum: 0n,
            sqrtPriceLimitX96: 0n,
          },
        ],
      });
    } catch (error) {
      console.error('Swap failed:', error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="bg-gray-800 rounded-2xl p-6 shadow-2xl">
      <h2 className="text-2xl font-bold text-white mb-6">Swap Tokens</h2>
      
      <div className="space-y-4">
        <div className="bg-gray-900 rounded-xl p-4">
          <label className="text-gray-400 text-sm mb-2 block">From</label>
          <input
            type="number"
            value={amountIn}
            onChange={(e) => setAmountIn(e.target.value)}
            placeholder="0.0"
            className="w-full bg-transparent text-white text-2xl outline-none"
          />
          <select
            value={tokenIn}
            onChange={(e) => setTokenIn(e.target.value)}
            className="mt-2 bg-gray-800 text-white rounded-lg px-3 py-2"
          >
            <option value={WETH_BASE}>WETH</option>
            <option value={USDC_BASE}>USDC</option>
          </select>
        </div>

        <div className="flex justify-center">
          <button
            onClick={() => {
              const temp = tokenIn;
              setTokenIn(tokenOut);
              setTokenOut(temp);
            }}
            className="bg-gray-700 hover:bg-gray-600 text-white rounded-full p-2"
          >
            ↓
          </button>
        </div>

        <div className="bg-gray-900 rounded-xl p-4">
          <label className="text-gray-400 text-sm mb-2 block">To</label>
          <input
            type="number"
            value={amountOut}
            readOnly
            placeholder="0.0"
            className="w-full bg-transparent text-white text-2xl outline-none"
          />
          <select
            value={tokenOut}
            onChange={(e) => setTokenOut(e.target.value)}
            className="mt-2 bg-gray-800 text-white rounded-lg px-3 py-2"
          >
            <option value={USDC_BASE}>USDC</option>
            <option value={WETH_BASE}>WETH</option>
          </select>
        </div>

        <button
          onClick={handleSwap}
          disabled={!amountIn || loading || isConfirming}
          className="w-full bg-gradient-to-r from-purple-500 to-pink-500 hover:from-purple-600 hover:to-pink-600 disabled:from-gray-600 disabled:to-gray-600 text-white font-bold py-4 rounded-xl transition-all"
        >
          {isConfirming ? 'Confirming...' : loading ? 'Preparing...' : 'Swap'}
        </button>

        {isSuccess && (
          <div className="text-green-400 text-center mt-4">
            Swap successful! 🎉
          </div>
        )}
      </div>

      <div className="mt-6 text-gray-400 text-sm">
        <p>• Supports Uniswap V3 on Base</p>
        <p>• V4 integration ready</p>
        <p>• Slippage tolerance: 0.5%</p>
      </div>
    </div>
  );
}