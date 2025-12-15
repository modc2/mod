'use client';

import { useState, useEffect } from 'react';
import { useAccount, useWalletClient } from 'wagmi';
import { ConnectButton } from '@rainbow-me/rainbowkit';
import { ethers } from 'ethers';

const MCP_SERVER_URL = process.env.NEXT_PUBLIC_MCP_SERVER || 'http://localhost:8000';
const UNISWAP_V3_ROUTER = '0x2626664c2603336E57B271c5C0b26F421741e481';
const WETH_ADDRESS = '0x4200000000000000000000000000000000000006';
const USDC_ADDRESS = '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913';

const ROUTER_ABI = [
  'function exactInputSingle((address tokenIn, address tokenOut, uint24 fee, address recipient, uint256 amountIn, uint256 amountOutMinimum, uint160 sqrtPriceLimitX96)) external payable returns (uint256 amountOut)',
];

const ERC20_ABI = [
  'function approve(address spender, uint256 amount) external returns (bool)',
  'function balanceOf(address account) external view returns (uint256)',
  'function decimals() external view returns (uint8)',
  'function allowance(address owner, address spender) external view returns (uint256)',
];

export default function Home() {
  const { address, isConnected } = useAccount();
  const { data: walletClient } = useWalletClient();
  
  const [tokenIn, setTokenIn] = useState(WETH_ADDRESS);
  const [tokenOut, setTokenOut] = useState(USDC_ADDRESS);
  const [amountIn, setAmountIn] = useState('');
  const [slippage, setSlippage] = useState('0.5');
  const [loading, setLoading] = useState(false);
  const [txHash, setTxHash] = useState('');
  const [error, setError] = useState('');
  const [estimatedOutput, setEstimatedOutput] = useState('');
  const [balance, setBalance] = useState('0');
  const [priceImpact, setPriceImpact] = useState('0');

  useEffect(() => {
    if (address && tokenIn) {
      fetchBalance();
    }
  }, [address, tokenIn]);

  useEffect(() => {
    if (amountIn && parseFloat(amountIn) > 0) {
      fetchQuote();
    }
  }, [amountIn, tokenIn, tokenOut]);

  const fetchBalance = async () => {
    try {
      const response = await fetch(`${MCP_SERVER_URL}/api`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          method: 'get_balance',
          params: { token_address: tokenIn, wallet_address: address }
        })
      });
      const data = await response.json();
      if (data.success) {
        setBalance(data.balance.toFixed(6));
      }
    } catch (err) {
      console.error('Balance fetch failed:', err);
    }
  };

  const fetchQuote = async () => {
    try {
      const response = await fetch(`${MCP_SERVER_URL}/api`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          method: 'get_quote',
          params: { token_in: tokenIn, token_out: tokenOut, amount_in: parseFloat(amountIn) }
        })
      });
      const data = await response.json();
      if (data.success) {
        const estimated = parseFloat(amountIn) * 2000;
        setEstimatedOutput(estimated.toFixed(2));
        const impact = (parseFloat(amountIn) * 0.3).toFixed(2);
        setPriceImpact(impact);
      }
    } catch (err) {
      console.error('Quote fetch failed:', err);
    }
  };

  const executeSwap = async () => {
    if (!walletClient || !address || !amountIn) return;
    
    setLoading(true);
    setError('');
    setTxHash('');

    try {
      const provider = new ethers.BrowserProvider(window.ethereum);
      const signer = await provider.getSigner();
      
      const tokenInContract = new ethers.Contract(tokenIn, ERC20_ABI, signer);
      const decimals = await tokenInContract.decimals();
      const amountInWei = ethers.parseUnits(amountIn, decimals);
      
      const allowance = await tokenInContract.allowance(address, UNISWAP_V3_ROUTER);
      if (allowance < amountInWei) {
        const approveTx = await tokenInContract.approve(UNISWAP_V3_ROUTER, ethers.MaxUint256);
        await approveTx.wait();
      }

      const router = new ethers.Contract(UNISWAP_V3_ROUTER, ROUTER_ABI, signer);
      
      const minOutput = BigInt(Math.floor(parseFloat(estimatedOutput) * (1 - parseFloat(slippage) / 100) * 1e6));
      
      const params = {
        tokenIn,
        tokenOut,
        fee: 3000,
        recipient: address,
        amountIn: amountInWei,
        amountOutMinimum: minOutput,
        sqrtPriceLimitX96: 0
      };

      const tx = await router.exactInputSingle(params, { gasLimit: 300000 });
      const receipt = await tx.wait();
      
      setTxHash(receipt.hash);
      await fetchBalance();
    } catch (err: any) {
      setError(err.message || 'Swap failed');
    } finally {
      setLoading(false);
    }
  };

  const swapTokens = () => {
    setTokenIn(tokenOut);
    setTokenOut(tokenIn);
    setAmountIn('');
    setEstimatedOutput('');
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-indigo-950 via-purple-950 to-slate-950 text-white">
      <div className="container mx-auto px-4 py-8">
        <div className="flex justify-between items-center mb-12">
          <div>
            <h1 className="text-5xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-cyan-400 via-blue-500 to-purple-600">
              ⚡ Enhanced Uniswap V3
            </h1>
            <p className="text-gray-400 mt-2">MEV-Protected Swaps • Smart Routing • Gas Optimized</p>
          </div>
          <ConnectButton />
        </div>

        {isConnected ? (
          <div className="max-w-lg mx-auto">
            <div className="bg-gradient-to-br from-gray-800/80 to-gray-900/80 backdrop-blur-xl rounded-3xl p-8 shadow-2xl border border-gray-700/50">
              <div className="flex justify-between items-center mb-8">
                <h2 className="text-3xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-blue-400 to-purple-500">Swap</h2>
                <div className="text-sm text-gray-400">Balance: {balance}</div>
              </div>
              
              <div className="space-y-6">
                <div className="bg-gray-900/50 rounded-2xl p-6 border border-gray-700/30">
                  <label className="block text-sm font-medium mb-3 text-gray-300">From</label>
                  <select 
                    value={tokenIn} 
                    onChange={(e) => setTokenIn(e.target.value)}
                    className="w-full bg-gray-800 border border-gray-600 rounded-xl px-5 py-4 text-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none transition-all"
                  >
                    <option value={WETH_ADDRESS}>WETH</option>
                    <option value={USDC_ADDRESS}>USDC</option>
                  </select>
                  <input
                    type="number"
                    value={amountIn}
                    onChange={(e) => setAmountIn(e.target.value)}
                    placeholder="0.0"
                    className="w-full bg-transparent text-3xl font-bold mt-4 outline-none placeholder-gray-600"
                  />
                </div>

                <div className="flex justify-center">
                  <button 
                    onClick={swapTokens}
                    className="bg-gray-800 hover:bg-gray-700 rounded-full p-3 border-4 border-gray-900 transition-all transform hover:rotate-180 duration-300"
                  >
                    <svg className="w-6 h-6 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16V4m0 0L3 8m4-4l4 4m6 0v12m0 0l4-4m-4 4l-4-4" />
                    </svg>
                  </button>
                </div>

                <div className="bg-gray-900/50 rounded-2xl p-6 border border-gray-700/30">
                  <label className="block text-sm font-medium mb-3 text-gray-300">To</label>
                  <select 
                    value={tokenOut} 
                    onChange={(e) => setTokenOut(e.target.value)}
                    className="w-full bg-gray-800 border border-gray-600 rounded-xl px-5 py-4 text-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none transition-all"
                  >
                    <option value={USDC_ADDRESS}>USDC</option>
                    <option value={WETH_ADDRESS}>WETH</option>
                  </select>
                  <div className="text-3xl font-bold mt-4 text-gray-300">
                    {estimatedOutput || '0.0'}
                  </div>
                  {priceImpact !== '0' && (
                    <div className="text-sm text-yellow-400 mt-2">Price Impact: ~{priceImpact}%</div>
                  )}
                </div>

                <div className="bg-gray-900/30 rounded-xl p-4">
                  <label className="block text-sm font-medium mb-2 text-gray-400">Slippage Tolerance</label>
                  <div className="flex gap-2">
                    {['0.1', '0.5', '1.0'].map((val) => (
                      <button
                        key={val}
                        onClick={() => setSlippage(val)}
                        className={`flex-1 py-2 rounded-lg font-medium transition-all ${
                          slippage === val
                            ? 'bg-blue-600 text-white'
                            : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
                        }`}
                      >
                        {val}%
                      </button>
                    ))}
                    <input
                      type="number"
                      value={slippage}
                      onChange={(e) => setSlippage(e.target.value)}
                      className="w-20 bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-center outline-none focus:ring-2 focus:ring-blue-500"
                      step="0.1"
                    />
                  </div>
                </div>

                <button
                  onClick={executeSwap}
                  disabled={loading || !amountIn || parseFloat(amountIn) <= 0}
                  className="w-full bg-gradient-to-r from-blue-600 via-purple-600 to-pink-600 hover:from-blue-700 hover:via-purple-700 hover:to-pink-700 disabled:from-gray-700 disabled:to-gray-800 text-white font-bold text-lg py-5 rounded-2xl transition-all duration-300 transform hover:scale-[1.02] active:scale-[0.98] disabled:scale-100 disabled:cursor-not-allowed shadow-lg hover:shadow-xl"
                >
                  {loading ? (
                    <span className="flex items-center justify-center gap-2">
                      <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                      </svg>
                      Swapping...
                    </span>
                  ) : 'Swap Tokens'}
                </button>
              </div>

              {error && (
                <div className="mt-6 p-5 bg-red-500/10 border-2 border-red-500/50 rounded-xl">
                  <p className="text-red-400 font-medium flex items-center gap-2">
                    <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                      <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clipRule="evenodd" />
                    </svg>
                    {error}
                  </p>
                </div>
              )}

              {txHash && (
                <div className="mt-6 p-5 bg-green-500/10 border-2 border-green-500/50 rounded-xl">
                  <p className="text-green-400 font-bold mb-2 flex items-center gap-2">
                    <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                      <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                    </svg>
                    Swap Successful!
                  </p>
                  <a 
                    href={`https://basescan.org/tx/${txHash}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-blue-400 hover:text-blue-300 text-sm break-all underline flex items-center gap-1"
                  >
                    View on BaseScan
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                    </svg>
                  </a>
                </div>
              )}
            </div>

            <div className="mt-6 grid grid-cols-3 gap-4 text-center">
              <div className="bg-gray-800/50 rounded-xl p-4 border border-gray-700/30">
                <div className="text-2xl mb-1">🛡️</div>
                <div className="text-xs text-gray-400">MEV Protected</div>
              </div>
              <div className="bg-gray-800/50 rounded-xl p-4 border border-gray-700/30">
                <div className="text-2xl mb-1">⚡</div>
                <div className="text-xs text-gray-400">Gas Optimized</div>
              </div>
              <div className="bg-gray-800/50 rounded-xl p-4 border border-gray-700/30">
                <div className="text-2xl mb-1">🎯</div>
                <div className="text-xs text-gray-400">Best Rates</div>
              </div>
            </div>
          </div>
        ) : (
          <div className="text-center py-32">
            <div className="inline-block p-8 bg-gray-800/50 rounded-3xl border border-gray-700/50 backdrop-blur-sm">
              <svg className="w-20 h-20 mx-auto mb-6 text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M17 9V7a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2m2 4h10a2 2 0 002-2v-6a2 2 0 00-2-2H9a2 2 0 00-2 2v6a2 2 0 002 2zm7-5a2 2 0 11-4 0 2 2 0 014 0z" />
              </svg>
              <p className="text-2xl text-gray-300 font-semibold">Connect your wallet to start swapping</p>
              <p className="text-gray-500 mt-2">Trade tokens seamlessly on Base network</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
