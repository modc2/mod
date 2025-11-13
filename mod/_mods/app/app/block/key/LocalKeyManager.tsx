'use client'

import React, { useState } from 'react';
import { Eye, EyeOff, RefreshCw, Copy, AlertTriangle } from 'lucide-react';
import { blake2AsHex } from '@polkadot/util-crypto';

interface LocalKeyManagerProps {
  onKeyGenerated?: (seed: string) => void;
}

export const LocalKeyManager: React.FC<LocalKeyManagerProps> = ({ onKeyGenerated }) => {
  const [seed, setSeed] = useState<string>(() => generateRandomSeed());
  const [isVisible, setIsVisible] = useState(false);
  const [showWarning, setShowWarning] = useState(false);
  const [copied, setCopied] = useState(false);

  function generateRandomSeed(): string {
    const randomBytes = new Uint8Array(32);
    crypto.getRandomValues(randomBytes);
    return blake2AsHex(randomBytes, 256);
  }

  const handleGenerateNew = () => {
    const newSeed = generateRandomSeed();
    setSeed(newSeed);
    setShowWarning(true);
    setCopied(false);
    if (onKeyGenerated) {
      onKeyGenerated(newSeed);
    }
  };

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(seed);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  };

  return (
    <div className="w-full max-w-md space-y-3 p-4 rounded-lg bg-gradient-to-br from-yellow-500/5 to-transparent border border-yellow-500/20">
      <div className="flex items-center gap-2 text-yellow-500/70 text-xs font-mono uppercase">
        <AlertTriangle size={14} />
        <span>Local Key Manager</span>
      </div>

      {showWarning && (
        <div className="bg-red-500/10 border border-red-500/30 rounded p-2 text-xs text-red-400 font-mono">
          ⚠️ WARNING: Save this seed now! You won't be able to retrieve it again.
        </div>
      )}

      <div className="space-y-2">
        <label className="text-xs text-yellow-500/60 font-mono uppercase">Random Seed</label>
        
        <div className="relative">
          <input
            type={isVisible ? 'text' : 'password'}
            value={seed}
            readOnly
            className="w-full bg-black/50 border border-yellow-500/30 rounded px-3 py-2 pr-20 text-yellow-400 font-mono text-xs focus:outline-none focus:border-yellow-500"
          />
          
          <div className="absolute right-2 top-1/2 -translate-y-1/2 flex gap-1">
            <button
              onClick={() => setIsVisible(!isVisible)}
              className="p-1 hover:bg-yellow-500/10 rounded transition-colors"
              title={isVisible ? 'Hide seed' : 'Show seed'}
            >
              {isVisible ? <EyeOff size={16} className="text-yellow-500" /> : <Eye size={16} className="text-yellow-500/60" />}
            </button>
          </div>
        </div>

        <div className="flex gap-2">
          <button
            onClick={handleGenerateNew}
            className="flex-1 flex items-center justify-center gap-2 bg-yellow-500/10 hover:bg-yellow-500/20 border border-yellow-500/30 rounded px-3 py-2 text-yellow-400 font-mono text-xs transition-colors"
          >
            <RefreshCw size={14} />
            Generate New
          </button>
          
          <button
            onClick={handleCopy}
            className="flex-1 flex items-center justify-center gap-2 bg-green-500/10 hover:bg-green-500/20 border border-green-500/30 rounded px-3 py-2 text-green-400 font-mono text-xs transition-colors"
          >
            <Copy size={14} />
            {copied ? 'Copied!' : 'Copy Seed'}
          </button>
        </div>
      </div>

      <div className="text-[10px] text-yellow-500/50 font-mono space-y-1">
        <p>• This seed is generated locally in your browser</p>
        <p>• Store it securely - it cannot be recovered</p>
        <p>• Anyone with this seed has full access to your key</p>
      </div>
    </div>
  );
};