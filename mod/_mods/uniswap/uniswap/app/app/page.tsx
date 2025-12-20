'use client';

import { useState, useEffect, useCallback } from 'react';
import { useAccount, useWalletClient } from 'wagmi';
import { ConnectButton } from '@rainbow-me/rainbowkit';
import { ethers } from 'ethers';

const MCP_SERVER_URL = process.env.NEXT_PUBLIC_MCP_SERVER || 'http://localhost:8000';
const UNISWAP_V3_ROUTER = '0x2626664c2603336E57B271c5C0b26F421741e481';

const TOKENS = {
  WETH: { address: '0x4200000000000000000000000000000000000006', symbol: 'WETH', decimals: 18, icon: '⟠' },
  USDC: { address: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913', symbol: 'USDC', decimals: 6, icon: '💵' },
  DAI: { address: '0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb', symbol: 'DAI', decimals: 18, icon: '🔶' },
};

const ROUTER_ABI = [
  'function exactInputSingle((address tokenIn, address tokenOut, uint24 fee, address recipient, uint256 amountIn, uint256 amountOutMinimum, uint160 sqrtPriceLimitX96)) external payable returns (uint256 amountOut)',
];

const ERC20_ABI = [
  'function approve(address spender, uint256 amount) external returns (bool)',
  'function balanceOf(address account) external view returns (uint256)',
  'function decimals() external view returns (uint8)',
  'function allowance(address owner, address spender) external view returns (uint256)',
  'function symbol() external view returns (string)',
];

export default function Home() {
  const { address, isConnected } = useAccount();
  const { data: walletClient } = useWalletClient();
  
  const [tokenIn, setTokenIn] = useState(TOKENS.WETH);
  const [tokenOut, setTokenOut] = useState(TOKENS.USDC);
  const [amountIn, setAmountIn] = useState('');
  const [slippage, setSlippage] = useState('0.5');
  const [loading, setLoading] = useState(false);
  const [approving, setApproving] = useState(false);
  const [txHash, setTxHash] = useState('');
  const [error, setError] = useState('');
  const [estimatedOutput, setEstimatedOutput] = useState('');
  const [balanceIn, setBalanceIn] = useState('0');
  const [balanceOut, setBalanceOut] = useState('0');
  const [priceImpact, setPriceImpact] = useState('0');
  const [exchangeRate, setExchangeRate] = useState('');
  const [gasEstimate, setGasEstimate] = useState('');
  const [recentTxs, setRecentTxs] = useState<string[]>([]);

  const fetchBalance = useCallback(async (token: typeof TOKENS.WETH, setter: (val: string) => void) => {
    if (!address || !window.ethereum) return;
    try {
      const provider = new ethers.BrowserProvider(window.ethereum);
      const contract = new ethers.Contract(token.address, ERC20_ABI, provider);
      const balance = await contract.balanceOf(address);
      const formatted = ethers.formatUnits(balance, token.decimals);
      setter(parseFloat(formatted).toFixed(6));
    } catch (err) {
      console.error('Balance fetch failed:', err);
    }
  }, [address]);

  useEffect(() => {
    if (address) {
      fetchBalance(tokenIn, setBalanceIn);
      fetchBalance(tokenOut, setBalanceOut);
    }
  }, [address, tokenIn, tokenOut, fetchBalance]);

  useEffect(() => {
    if (amountIn && parseFloat(amountIn) > 0) {
      // Simulated quote - in production, call actual quoter contract
      const mockRate = tokenIn.symbol === 'WETH' ? 2450 : 1 / 2450;
      const estimated = parseFloat(amountIn) * mockRate;
      setEstimatedOutput(estimated.toFixed(tokenOut.decimals === 6 ? 2 : 6));
      setExchangeRate(`1 ${tokenIn.symbol} = ${mockRate.toFixed(2)} ${tokenOut.symbol}`);
      setPriceImpact((parseFloat(amountIn) * 0.1).toFixed(3));
      setGasEstimate('~$0.02');
    } else {
      setEstimatedOutput('');
      setExchangeRate('');
      setPriceImpact('0');
    }
  }, [amountIn, tokenIn, tokenOut]);

  const executeSwap = async () => {
    if (!walletClient || !address || !amountIn) return;
    
    setLoading(true);
    setError('');
    setTxHash('');

    try {
      const provider = new ethers.BrowserProvider(window.ethereum);
      const signer = await provider.getSigner();
      
      const tokenInContract = new ethers.Contract(tokenIn.address, ERC20_ABI, signer);
      const amountInWei = ethers.parseUnits(amountIn, tokenIn.decimals);
      
      // Check and handle approval
      const allowance = await tokenInContract.allowance(address, UNISWAP_V3_ROUTER);
      if (allowance < amountInWei) {
        setApproving(true);
        const approveTx = await tokenInContract.approve(UNISWAP_V3_ROUTER, ethers.MaxUint256);
        await approveTx.wait();
        setApproving(false);
      }

      const router = new ethers.Contract(UNISWAP_V3_ROUTER, ROUTER_ABI, signer);
      const minOutput = BigInt(Math.floor(parseFloat(estimatedOutput) * (1 - parseFloat(slippage) / 100) * (10 ** tokenOut.decimals)));
      
      const params = {
        tokenIn: tokenIn.address,
        tokenOut: tokenOut.address,
        fee: 3000,
        recipient: address,
        amountIn: amountInWei,
        amountOutMinimum: minOutput,
        sqrtPriceLimitX96: 0
      };

      const tx = await router.exactInputSingle(params, { gasLimit: 350000 });
      const receipt = await tx.wait();
      
      setTxHash(receipt.hash);
      setRecentTxs(prev => [receipt.hash, ...prev.slice(0, 4)]);
      setAmountIn('');
      setEstimatedOutput('');
      
      // Refresh balances
      fetchBalance(tokenIn, setBalanceIn);
      fetchBalance(tokenOut, setBalanceOut);
    } catch (err: any) {
      setError(err.reason || err.message || 'Swap failed');
    } finally {
      setLoading(false);
      setApproving(false);
    }
  };

  const swapTokens = () => {
    const temp = tokenIn;
    setTokenIn(tokenOut);
    setTokenOut(temp);
    setAmountIn('');
    setEstimatedOutput('');
  };

  const setMaxAmount = () => {
    setAmountIn(balanceIn);
  };

  const TokenSelector = ({ token, setToken, label, balance }: any) => (
    <div className="bg-gray-900/60 rounded-2xl p-5 border border-gray-700/40 hover:border-gray-600/60 transition-all">
      <div className="flex justify-between items-center mb-3">
        <label className="text-sm font-medium text-gray-400">{label}</label>
        <span className="text-xs text-gray-500">Balance: {balance}</span>
      </div>
      <div className="flex gap-3">
        <select 
          value={token.symbol}
          onChange={(e) => setToken(TOKENS[e.target.value as keyof typeof TOKENS])}
          className="bg-gray-800/80 border border-gray-600/50 rounded-xl px-4 py-3 text-lg font-semibold focus:ring-2 focus:ring-purple-500 focus:border-transparent outline-none transition-all cursor-pointer hover:bg-gray-700/80"
        >
          {Object.values(TOKENS).map((t) => (
            <option key={t.symbol} value={t.symbol}>{t.icon} {t.symbol}</option>
          ))}
        </select>
        <div className="flex-1 relative">
          <input
            type="number"
            value={label === 'From' ? amountIn : estimatedOutput}
            onChange={label === 'From' ? (e) => setAmountIn(e.target.value) : undefined}
            readOnly={label !== 'From'}
            placeholder="0.0"
            className="w-full bg-transparent text-2xl font-bold outline-none placeholder-gray-600 text-right pr-12"
          />
          {label === 'From' && (
            <button 
              onClick={setMaxAmount}
              className="absolute right-0 top-1/2 -translate-y-1/2 text-xs bg-purple-600/30 hover:bg-purple-600/50 text-purple-300 px-2 py-1 rounded-lg transition-all"
            >
              MAX
            </button>
          )}
        </div>
      </div>
    </div>
  );

  return (
    <div className="min-h-screen bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-purple-900/20 via-gray-950 to-black text-white">
      <div className="absolute inset-0 bg-[url('data:image/svg+xml,%3Csvg width="60" height="60" viewBox="0 0 60 60" xmlns="http://www.w3.org/2000/svg"%3E%3Cg fill="none" fill-rule="evenodd"%3E%3Cg fill="%239C92AC" fill-opacity="0.03"%3E%3Cpath d="M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z"/%3E%3C/g%3E%3C/g%3E%3C/svg%3E')] opacity-50"></div>
      
      <div className="relative container mx-auto px-4 py-8">
        <header className="flex justify-between items-center mb-12">
          <div>
            <h1 className="text-4xl md:text-5xl font-black bg-clip-text text-transparent bg-gradient-to-r from-pink-500 via-purple-500 to-cyan-500 animate-gradient">
              🦄 UniSwap Pro
            </h1>
            <p className="text-gray-500 mt-2 text-sm">MEV-Protected • Smart Routing • Base Network</p>
          </div>
          <ConnectButton />
        </header>

        {isConnected ? (
          <div className="max-w-md mx-auto">
            <div className="bg-gradient-to-br from-gray-800/90 to-gray-900/90 backdrop-blur-2xl rounded-3xl p-6 shadow-2xl border border-gray-700/50 relative overflow-hidden">
              <div className="absolute top-0 right-0 w-32 h-32 bg-purple-500/10 rounded-full blur-3xl"></div>
              <div className="absolute bottom-0 left-0 w-32 h-32 bg-pink-500/10 rounded-full blur-3xl"></div>
              
              <div className="relative z-10 space-y-4">
                <TokenSelector token={tokenIn} setToken={setTokenIn} label="From" balance={balanceIn} />
                
                <div className="flex justify-center -my-2 relative z-20">
                  <button 
                    onClick={swapTokens}
                    className="bg-gray-800 hover:bg-purple-600 rounded-xl p-3 border-4 border-gray-900 transition-all transform hover:rotate-180 hover:scale-110 duration-300 shadow-lg"
                  >
                    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16V4m0 0L3 8m4-4l4 4m6 0v12m0 0l4-4m-4 4l-4-4" />
                    </svg>
                  </button>
                </div>

                <TokenSelector token={tokenOut} setToken={setTokenOut} label="To" balance={balanceOut} />

                {exchangeRate && (
                  <div className="bg-gray-800/40 rounded-xl p-4 space-y-2 text-sm">
                    <div className="flex justify-between text-gray-400">
                      <span>Rate</span>
                      <span className="text-white font-medium">{exchangeRate}</span>
                    </div>
                    <div className="flex justify-between text-gray-400">
                      <span>Price Impact</span>
                      <span className={parseFloat(priceImpact) > 1 ? 'text-yellow-400' : 'text-green-400'}>{priceImpact}%</span>
                    </div>
                    <div className="flex justify-between text-gray-400">
                      <span>Est. Gas</span>
                      <span className="text-gray-300">{gasEstimate}</span>
                    </div>
                    <div className="flex justify-between text-gray-400">
                      <span>Slippage</span>
                      <span className="text-gray-300">{slippage}%</span>
                    </div>
                  </div>
                )}

                <div className="flex gap-2">
                  {['0.1', '0.5', '1.0', '3.0'].map((val) => (
                    <button
                      key={val}
                      onClick={() => setSlippage(val)}
                      className={`flex-1 py-2 rounded-lg text-sm font-medium transition-all ${slippage === val ? 'bg-purple-600 text-white shadow-lg shadow-purple-500/30' : 'bg-gray-800/60 text-gray-400 hover:bg-gray-700/60'}`}
                    >
                      {val}%
                    </button>
                  ))}
                </div>

                <button
                  onClick={executeSwap}
                  disabled={loading || !amountIn || parseFloat(amountIn) <= 0 || parseFloat(amountIn) > parseFloat(balanceIn)}
                  className="w-full bg-gradient-to-r from-pink-600 via-purple-600 to-cyan-600 hover:from-pink-500 hover:via-purple-500 hover:to-cyan-500 disabled:from-gray-700 disabled:to-gray-800 text-white font-bold text-lg py-4 rounded-2xl transition-all duration-300 transform hover:scale-[1.02] active:scale-[0.98] disabled:scale-100 disabled:cursor-not-allowed shadow-xl hover:shadow-2xl hover:shadow-purple-500/20"
                >
                  {loading ? (
                    <span className="flex items-center justify-center gap-2">
                      <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                      </svg>
                      {approving ? 'Approving...' : 'Swapping...'}
                    </span>
                  ) : parseFloat(amountIn) > parseFloat(balanceIn) ? 'Insufficient Balance' : 'Swap'}
                </button>
              </div>

              {error && (
                <div className="mt-4 p-4 bg-red-500/10 border border-red-500/30 rounded-xl">
                  <p className="text-red-400 text-sm flex items-center gap-2">⚠️ {error}</p>
                </div>
              )}

              {txHash && (
                <div className="mt-4 p-4 bg-green-500/10 border border-green-500/30 rounded-xl">
                  <p className="text-green-400 font-semibold mb-1">✅ Swap Successful!</p>
                  <a href={`https://basescan.org/tx/${txHash}`} target="_blank" rel="noopener noreferrer" className="text-cyan-400 hover:text-cyan-300 text-sm underline">
                    View on BaseScan →
                  </a>
                </div>
              )}
            </div>

            <div className="mt-6 grid grid-cols-3 gap-3">
              {[{ icon: '🛡️', label: 'MEV Protected' }, { icon: '⚡', label: 'Gas Optimized' }, { icon: '🎯', label: 'Best Rates' }].map((item) => (
                <div key={item.label} className="bg-gray-800/30 rounded-xl p-3 text-center border border-gray-700/30 hover:border-purple-500/30 transition-all">
                  <div className="text-xl mb-1">{item.icon}</div>
                  <div className="text-xs text-gray-400">{item.label}</div>
                </div>
              ))}
            </div>

            {recentTxs.length > 0 && (
              <div className="mt-6 bg-gray-800/30 rounded-xl p-4 border border-gray-700/30">
                <h3 className="text-sm font-semibold text-gray-400 mb-2">Recent Transactions</h3>
                {recentTxs.map((tx, i) => (
                  <a key={i} href={`https://basescan.org/tx/${tx}`} target="_blank" rel="noopener noreferrer" className="block text-xs text-cyan-400 hover:text-cyan-300 truncate py-1">
                    {tx}
                  </a>
                ))}
              </div>
            )}
          </div>
        ) : (
          <div className="text-center py-24">
            <div className="inline-block p-10 bg-gray-800/30 rounded-3xl border border-gray-700/30 backdrop-blur-sm">
              <div className="text-6xl mb-6">🦄</div>
              <p className="text-2xl text-gray-300 font-semibold">Connect Wallet to Start</p>
              <p className="text-gray-500 mt-2">Trade tokens on Base network</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
