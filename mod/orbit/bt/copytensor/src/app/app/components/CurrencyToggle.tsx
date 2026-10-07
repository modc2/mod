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
      /* VT323 sets ~30% small for its nominal size, so the mono chips run
         a step above the Silkscreen caps beside them to match. */
      className={`pixel-btn topbar-ctl px-3 font-mono text-[15px] ${
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
