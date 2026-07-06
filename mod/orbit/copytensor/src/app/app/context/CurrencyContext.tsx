"use client";

import {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
  type ReactNode,
} from "react";

export type Currency = "TAO" | "USD";
const STORAGE_KEY = "copytensor:currency";
// Poll the backend hourly to match the server-side cache TTL — anything
// shorter just returns the same cached value from /tao_price.
const POLL_MS = 60 * 60 * 1000;

type Ctx = {
  currency: Currency;
  setCurrency: (c: Currency) => void;
  toggle: () => void;
  usdPerTao: number | null;
  stale: boolean;
  loading: boolean;
};

const C = createContext<Ctx | null>(null);

export function CurrencyProvider({ children }: { children: ReactNode }) {
  const [currency, setCurrencyState] = useState<Currency>("TAO");
  const [usdPerTao, setUsd] = useState<number | null>(null);
  const [stale, setStale] = useState(false);
  const [loading, setLoading] = useState(true);

  // Hydrate selection from localStorage
  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored === "TAO" || stored === "USD") setCurrencyState(stored);
    } catch {}
  }, []);

  const setCurrency = useCallback((c: Currency) => {
    setCurrencyState(c);
    try { localStorage.setItem(STORAGE_KEY, c); } catch {}
  }, []);

  const toggle = useCallback(() => {
    setCurrency(currency === "TAO" ? "USD" : "TAO");
  }, [currency, setCurrency]);

  // Poll the API for the live TAO/USD price (server-side cached).
  useEffect(() => {
    let cancelled = false;
    const base = process.env.NEXT_PUBLIC_API_URL || "/api/copytensor";
    const fetchPrice = async () => {
      try {
        const r = await fetch(`${base}/tao_price`, { cache: "no-store" });
        if (!r.ok) return;
        const d = await r.json();
        if (cancelled) return;
        if (typeof d.usd === "number") {
          setUsd(d.usd);
          setStale(Boolean(d.stale));
        }
      } catch {
        // Keep last known price on transient failure
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    fetchPrice();
    const id = setInterval(fetchPrice, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return (
    <C.Provider value={{ currency, setCurrency, toggle, usdPerTao, stale, loading }}>
      {children}
    </C.Provider>
  );
}

export function useCurrency() {
  const v = useContext(C);
  if (!v) throw new Error("useCurrency must be used inside CurrencyProvider");
  return v;
}

// Convert a TAO amount to display number+suffix in the active mode.
// If USD is selected but the price isn't loaded yet, falls back to TAO.
export function fmtValue(
  taoAmount: number,
  currency: Currency,
  usdPerTao: number | null,
): string {
  if (currency === "USD" && usdPerTao && usdPerTao > 0) {
    const usd = taoAmount * usdPerTao;
    const a = Math.abs(usd);
    const sign = usd < 0 ? "-" : "";
    if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(2)}B`;
    if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(2)}M`;
    if (a >= 1e3) return `${sign}$${(a / 1e3).toFixed(2)}K`;
    if (a >= 1) return `${sign}$${a.toFixed(2)}`;
    if (a >= 0.01) return `${sign}$${a.toFixed(4)}`;
    return `${sign}$${a.toExponential(2)}`;
  }
  const a = Math.abs(taoAmount);
  const s =
    a >= 1e6 ? `${(a / 1e6).toFixed(2)}M`
    : a >= 1e3 ? `${(a / 1e3).toFixed(2)}K`
    : a.toFixed(4);
  return `${taoAmount < 0 ? "-" : ""}${s} τ`;
}

export function fmtPnlValue(
  taoAmount: number,
  currency: Currency,
  usdPerTao: number | null,
): string {
  const v = fmtValue(taoAmount, currency, usdPerTao);
  return taoAmount >= 0 ? `+${v}` : v;
}

// Alpha price (τ per α). When in USD mode, convert via the live TAO/USD rate.
export function fmtAlphaPrice(
  taoPerAlpha: number,
  currency: Currency,
  usdPerTao: number | null,
): string {
  if (currency === "USD" && usdPerTao && usdPerTao > 0) {
    const usd = taoPerAlpha * usdPerTao;
    if (usd >= 1) return `$${usd.toFixed(3)}`;
    if (usd >= 0.001) return `$${usd.toFixed(4)}`;
    return `$${usd.toExponential(2)}`;
  }
  if (taoPerAlpha >= 1) return `${taoPerAlpha.toFixed(3)} τ`;
  return `${taoPerAlpha.toFixed(6)} τ`;
}
