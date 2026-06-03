"use client";

import { useCurrency } from "../context/CurrencyContext";

export default function CurrencyToggle() {
  const { currency, toggle, usdPerTao, stale, loading } = useCurrency();
  const usdReady = usdPerTao !== null && usdPerTao > 0;

  const title = usdReady
    ? `TAO/USD: $${usdPerTao!.toFixed(2)}${stale ? " (cached)" : ""}`
    : loading
      ? "loading TAO/USD…"
      : "USD price unavailable";

  return (
    <button
      onClick={toggle}
      title={title}
      aria-label={`Toggle currency (currently ${currency})`}
      className={`pixel-btn text-[11px] px-2 py-1.5 font-mono ${
        currency === "USD"
          ? "border-green-400 text-green-400"
          : "text-pixel-gray-light"
      }`}
      disabled={!usdReady && currency === "TAO" && loading}
    >
      {currency === "TAO" ? "τ" : "$"}
      <span className="ml-1 opacity-60">{currency}</span>
    </button>
  );
}
