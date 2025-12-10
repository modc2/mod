'use client';

import { useState, useEffect } from 'react';
import { useConnection, useWallet } from '@solana/wallet-adapter-react';
import { WalletMultiButton } from '@solana/wallet-adapter-react-ui';
import { Program, AnchorProvider, web3, BN } from '@project-serum/anchor';
import { useAccount, useWriteContract, useReadContract } from 'wagmi';

interface Market {
  id: string;
  pairAddress: string;
  targetPrice: string;
  expiryTimestamp: number;
  totalYesStake: string;
  totalNoStake: string;
  isSettled: boolean;
  chain: 'solana' | 'base';
}

export default function Home() {
  const [markets, setMarkets] = useState<Market[]>([]);
  const [selectedMarket, setSelectedMarket] = useState<Market | null>(null);
  const [predictionAmount, setPredictionAmount] = useState('');
  const [predictYes, setPredictYes] = useState(true);
  const [activeChain, setActiveChain] = useState<'solana' | 'base'>('solana');

  // Solana hooks
  const { connection } = useConnection();
  const solanaWallet = useWallet();

  // Base/Ethereum hooks
  const { address: baseAddress } = useAccount();
  const { writeContract } = useWriteContract();

  useEffect(() => {
    loadMarkets();
  }, [activeChain]);

  const loadMarkets = async () => {
    // Mock data - replace with actual contract calls
    const mockMarkets: Market[] = [
      {
        id: '1',
        pairAddress: 'SOL/USDC',
        targetPrice: '150',
        expiryTimestamp: Date.now() + 86400000,
        totalYesStake: '1000',
        totalNoStake: '800',
        isSettled: false,
        chain: 'solana'
      },
      {
        id: '2',
        pairAddress: 'ETH/USDC',
        targetPrice: '3500',
        expiryTimestamp: Date.now() + 172800000,
        totalYesStake: '5000',
        totalNoStake: '4500',
        isSettled: false,
        chain: 'base'
      }
    ];
    setMarkets(mockMarkets.filter(m => m.chain === activeChain));
  };

  const placePredictionSolana = async () => {
    if (!solanaWallet.publicKey || !selectedMarket) return;

    try {
      // Implement Solana prediction logic
      console.log('Placing Solana prediction:', {
        market: selectedMarket.id,
        amount: predictionAmount,
        predictYes
      });
      alert('Prediction placed on Solana!');
    } catch (error) {
      console.error('Error placing Solana prediction:', error);
    }
  };

  const placePredictionBase = async () => {
    if (!baseAddress || !selectedMarket) return;

    try {
      // Implement Base prediction logic
      console.log('Placing Base prediction:', {
        market: selectedMarket.id,
        amount: predictionAmount,
        predictYes
      });
      alert('Prediction placed on Base!');
    } catch (error) {
      console.error('Error placing Base prediction:', error);
    }
  };

  const handlePlacePrediction = () => {
    if (activeChain === 'solana') {
      placePredictionSolana();
    } else {
      placePredictionBase();
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-purple-900 via-blue-900 to-black text-white">
      <nav className="p-6 flex justify-between items-center border-b border-purple-500/30">
        <h1 className="text-3xl font-bold bg-gradient-to-r from-purple-400 to-blue-400 bg-clip-text text-transparent">
          3Fi Prediction Market
        </h1>
        <div className="flex gap-4 items-center">
          <div className="flex gap-2 bg-black/30 rounded-lg p-1">
            <button
              onClick={() => setActiveChain('solana')}
              className={`px-4 py-2 rounded-md transition-all ${
                activeChain === 'solana'
                  ? 'bg-purple-600 text-white'
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              Solana
            </button>
            <button
              onClick={() => setActiveChain('base')}
              className={`px-4 py-2 rounded-md transition-all ${
                activeChain === 'base'
                  ? 'bg-blue-600 text-white'
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              Base
            </button>
          </div>
          {activeChain === 'solana' ? (
            <WalletMultiButton className="!bg-purple-600 hover:!bg-purple-700" />
          ) : (
            <w3m-button />
          )}
        </div>
      </nav>

      <main className="container mx-auto p-6">
        <div className="grid md:grid-cols-2 gap-6">
          {/* Markets List */}
          <div className="space-y-4">
            <h2 className="text-2xl font-bold mb-4">Active Markets</h2>
            {markets.map((market) => (
              <div
                key={market.id}
                onClick={() => setSelectedMarket(market)}
                className={`p-6 rounded-xl cursor-pointer transition-all ${
                  selectedMarket?.id === market.id
                    ? 'bg-purple-600/30 border-2 border-purple-500'
                    : 'bg-black/30 border border-purple-500/20 hover:border-purple-500/50'
                }`}
              >
                <div className="flex justify-between items-start mb-4">
                  <div>
                    <h3 className="text-xl font-bold">{market.pairAddress}</h3>
                    <p className="text-gray-400">Target: ${market.targetPrice}</p>
                  </div>
                  <span className="px-3 py-1 bg-purple-500/20 rounded-full text-sm">
                    {new Date(market.expiryTimestamp).toLocaleDateString()}
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div className="bg-green-500/10 p-3 rounded-lg">
                    <p className="text-sm text-gray-400">YES Pool</p>
                    <p className="text-lg font-bold text-green-400">${market.totalYesStake}</p>
                  </div>
                  <div className="bg-red-500/10 p-3 rounded-lg">
                    <p className="text-sm text-gray-400">NO Pool</p>
                    <p className="text-lg font-bold text-red-400">${market.totalNoStake}</p>
                  </div>
                </div>
              </div>
            ))}
          </div>

          {/* Prediction Form */}
          <div className="bg-black/30 border border-purple-500/20 rounded-xl p-6">
            <h2 className="text-2xl font-bold mb-6">Place Prediction</h2>
            {selectedMarket ? (
              <div className="space-y-6">
                <div>
                  <label className="block text-sm text-gray-400 mb-2">Selected Market</label>
                  <p className="text-xl font-bold">{selectedMarket.pairAddress}</p>
                  <p className="text-gray-400">Target: ${selectedMarket.targetPrice}</p>
                </div>

                <div>
                  <label className="block text-sm text-gray-400 mb-2">Amount</label>
                  <input
                    type="number"
                    value={predictionAmount}
                    onChange={(e) => setPredictionAmount(e.target.value)}
                    className="w-full bg-black/50 border border-purple-500/30 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-purple-500"
                    placeholder="Enter amount"
                  />
                </div>

                <div>
                  <label className="block text-sm text-gray-400 mb-2">Prediction</label>
                  <div className="grid grid-cols-2 gap-4">
                    <button
                      onClick={() => setPredictYes(true)}
                      className={`py-4 rounded-lg font-bold transition-all ${
                        predictYes
                          ? 'bg-green-600 text-white'
                          : 'bg-green-600/20 text-green-400 hover:bg-green-600/30'
                      }`}
                    >
                      YES (Above Target)
                    </button>
                    <button
                      onClick={() => setPredictYes(false)}
                      className={`py-4 rounded-lg font-bold transition-all ${
                        !predictYes
                          ? 'bg-red-600 text-white'
                          : 'bg-red-600/20 text-red-400 hover:bg-red-600/30'
                      }`}
                    >
                      NO (Below Target)
                    </button>
                  </div>
                </div>

                <button
                  onClick={handlePlacePrediction}
                  disabled={!predictionAmount}
                  className="w-full bg-gradient-to-r from-purple-600 to-blue-600 hover:from-purple-700 hover:to-blue-700 disabled:from-gray-600 disabled:to-gray-700 disabled:cursor-not-allowed py-4 rounded-lg font-bold transition-all"
                >
                  Place Prediction
                </button>
              </div>
            ) : (
              <p className="text-center text-gray-400 py-12">Select a market to place a prediction</p>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}