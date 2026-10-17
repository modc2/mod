'use client';
import { createContext, useContext, useEffect, useState } from 'react';
import type { Network } from './api';

const KEY = 'starknet.network';
const Ctx = createContext<{ net: Network; setNet: (n: Network) => void }>({
  net: 'mainnet', setNet: () => {},
});

export function NetworkProvider({ children }: { children: React.ReactNode }) {
  const [net, setNetState] = useState<Network>('mainnet');
  useEffect(() => {
    const saved = window.localStorage.getItem(KEY);
    if (saved === 'mainnet' || saved === 'sepolia') setNetState(saved);
  }, []);
  const setNet = (n: Network) => {
    window.localStorage.setItem(KEY, n);
    setNetState(n);
  };
  return <Ctx.Provider value={{ net, setNet }}>{children}</Ctx.Provider>;
}

export const useNet = () => useContext(Ctx);
