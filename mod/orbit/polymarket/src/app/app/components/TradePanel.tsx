"use client";

import { useState } from "react";
import { useAuth } from "../context/AuthContext";
import { PolymarketMarket } from "../lib/types";
import { formatVolume } from "../lib/polymarket";

interface Props {
  market: PolymarketMarket | null;
  initialSide?: "YES" | "NO";
}

export default function TradePanel({ market, initialSide }: Props) {
  const { auth } = useAuth();
  const [side, setSide] = useState<"YES" | "NO">(initialSide || "YES");
  const [amount, setAmount] = useState("");
  const [orderType, setOrderType] = useState<"MARKET" | "LIMIT">("MARKET");
  const [limitPrice, setLimitPrice] = useState("");
  const [status, setStatus] = useState<string | null>(null);

  if (!market) {
    return (
      <div className="pixel-panel p-6 text-center">
        <div className="text-[15px] text-pixel-gray mb-2">SELECT A MARKET</div>
        <div className="text-[13px] text-pixel-gray-light">
          CLICK ON ANY MARKET TO START TRADING
        </div>
        <div className="mt-4 flex justify-center">
          <div className="w-12 h-12 border-2 border-pixel-border flex items-center justify-center">
            <span className="text-pixel-gray text-[26px] animate-float">?</span>
          </div>
        </div>
      </div>
    );
  }

  const yesPrice = market.outcomePrices[0] || 0;
  const noPrice = market.outcomePrices[1] || 1 - yesPrice;
  const currentPrice = side === "YES" ? yesPrice : noPrice;
  const shares = amount ? parseFloat(amount) / currentPrice : 0;
  const potentialPayout = shares;
  const potentialProfit = potentialPayout - (amount ? parseFloat(amount) : 0);

  const handleTrade = async () => {
    if (!auth.authenticated) {
      setStatus("ERROR: GET API KEY FIRST");
      return;
    }
    if (!amount || parseFloat(amount) <= 0) {
      setStatus("ERROR: ENTER AMOUNT");
      return;
    }

    // Order placement requires EIP-712 per-order maker signing (see
    // clobClient.placeOrder) and that infrastructure isn't wired up yet.
    // Surface that honestly instead of POSTing to a nonexistent endpoint.
    setStatus("ERROR: ORDER PLACEMENT NOT YET IMPLEMENTED — NEEDS EIP-712 SIGNER");
  };

  return (
    <div className="pixel-panel p-4 space-y-4">
      {/* Market info */}
      <div className="border-b border-pixel-border/40 pb-3">
        <div className="text-[13px] text-pixel-white leading-relaxed mb-2">
          {market.question}
        </div>
        <div className="flex items-center gap-3 text-[13px] font-mono">
          <span className="text-pixel-white">
            YES {Math.round(yesPrice * 100)}¢
          </span>
          <span className="text-pixel-gray-light">
            NO {Math.round(noPrice * 100)}¢
          </span>
          <span className="text-pixel-gray">
            {formatVolume(market.volume)}
          </span>
        </div>
      </div>

      {/* Side selector */}
      <div className="flex gap-2">
        <button
          onClick={() => setSide("YES")}
          className={`pixel-btn flex-1 text-[13px] ${
            side === "YES"
              ? "border-pixel-white text-pixel-white bg-pixel-white/10"
              : "border-pixel-border text-pixel-gray"
          }`}
        >
          BUY YES
        </button>
        <button
          onClick={() => setSide("NO")}
          className={`pixel-btn flex-1 text-[13px] ${
            side === "NO"
              ? "border-pixel-white text-pixel-white bg-pixel-white/10"
              : "border-pixel-border text-pixel-gray"
          }`}
        >
          BUY NO
        </button>
      </div>

      {/* Order type */}
      <div className="flex gap-2">
        <button
          onClick={() => setOrderType("MARKET")}
          className={`pixel-btn flex-1 text-[12px] ${
            orderType === "MARKET"
              ? "border-pixel-white text-pixel-white bg-pixel-white/10"
              : "border-pixel-border text-pixel-gray"
          }`}
        >
          MARKET
        </button>
        <button
          onClick={() => setOrderType("LIMIT")}
          className={`pixel-btn flex-1 text-[12px] ${
            orderType === "LIMIT"
              ? "border-pixel-white text-pixel-white bg-pixel-white/10"
              : "border-pixel-border text-pixel-gray"
          }`}
        >
          LIMIT
        </button>
      </div>

      {/* Amount */}
      <div>
        <label className="text-[12px] text-pixel-gray-light tracking-wider mb-1 block">
          AMOUNT (USDC)
        </label>
        <input
          type="number"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          placeholder="0.00"
          className="pixel-input w-full text-[13px]"
        />
      </div>

      {/* Limit price */}
      {orderType === "LIMIT" && (
        <div>
          <label className="text-[12px] text-pixel-gray-light tracking-wider mb-1 block">
            LIMIT PRICE (CENTS)
          </label>
          <input
            type="number"
            value={limitPrice}
            onChange={(e) => setLimitPrice(e.target.value)}
            placeholder={Math.round(currentPrice * 100).toString()}
            className="pixel-input w-full text-[13px]"
          />
        </div>
      )}

      {/* Quick amounts */}
      <div className="flex gap-1">
        {[5, 10, 25, 50, 100].map((v) => (
          <button
            key={v}
            onClick={() => setAmount(v.toString())}
            className="pixel-btn flex-1 border-pixel-border text-pixel-gray text-[12px] hover:text-pixel-white hover:border-pixel-white"
          >
            ${v}
          </button>
        ))}
      </div>

      {/* Trade preview */}
      {amount && parseFloat(amount) > 0 && (
        <div className="pixel-panel-cyan p-3 space-y-1.5 text-[12px]">
          <div className="flex justify-between">
            <span className="text-pixel-gray">PRICE</span>
            <span className="text-pixel-white font-mono">{Math.round(currentPrice * 100)}¢</span>
          </div>
          <div className="flex justify-between">
            <span className="text-pixel-gray">SHARES</span>
            <span className="text-pixel-white font-mono">{shares.toFixed(2)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-pixel-gray">POTENTIAL</span>
            <span className="text-pixel-white font-mono">
              +${potentialProfit.toFixed(2)}
            </span>
          </div>
        </div>
      )}

      {/* Submit */}
      <button
        onClick={handleTrade}
        disabled={!auth.authenticated}
        className={`pixel-btn w-full text-[13px] py-3 ${
          side === "YES"
            ? "border-pixel-green text-pixel-green bg-pixel-green/10 hover:bg-pixel-green/20"
            : "border-pixel-red text-pixel-red bg-pixel-red/10 hover:bg-pixel-red/20"
        } disabled:opacity-30 disabled:cursor-not-allowed`}
      >
        {auth.authenticated
          ? `PLACE ${side} ORDER`
          : "CONNECT & AUTH FIRST"}
      </button>

      {/* Status */}
      {status && (
        <div
          className={`text-[12px] text-center p-2 ${
            status.startsWith("ERROR")
              ? "text-pixel-red pixel-panel-red"
              : status.includes("PLACED")
              ? "text-pixel-green pixel-panel"
              : "text-pixel-amber pixel-panel-amber"
          }`}
        >
          {status}
        </div>
      )}
    </div>
  );
}
