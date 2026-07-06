"use client";

import {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
} from "react";

type Wallet = { address: string | null; setAddress: (a: string | null) => void };

const C = createContext<Wallet>({ address: null, setAddress: () => {} });

const KEY = "hl_wallet";

export function WalletProvider({ children }: { children: React.ReactNode }) {
  const [address, _setAddress] = useState<string | null>(null);

  useEffect(() => {
    try {
      const v = localStorage.getItem(KEY);
      if (v) _setAddress(v.toLowerCase());
    } catch {}
  }, []);

  const setAddress = useCallback((a: string | null) => {
    _setAddress(a ? a.toLowerCase() : null);
    try {
      if (a) localStorage.setItem(KEY, a.toLowerCase());
      else localStorage.removeItem(KEY);
    } catch {}
  }, []);

  const value = useMemo(() => ({ address, setAddress }), [address, setAddress]);
  return <C.Provider value={value}>{children}</C.Provider>;
}

export const useWallet = () => useContext(C);
