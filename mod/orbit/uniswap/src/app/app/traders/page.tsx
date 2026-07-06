"use client";
import { useState, useCallback, useEffect, useRef } from "react";
import Header from "../components/Header";
import TraderTable from "../components/TraderTable";
import ScrapeProgress from "../components/ScrapeProgress";
import { fetchTradersStream } from "../lib/api";
import { Chain, Trader, ScrapeProgress as ProgressType } from "../lib/types";

export default function TradersPage() {
  const [chain, setChain] = useState<Chain>("base");
  const [days, setDays] = useState(30);
  const [traders, setTraders] = useState<Trader[]>([]);
  const [progress, setProgress] = useState<ProgressType | null>(null);
  const [loading, setLoading] = useState(false);
  const [source, setSource] = useState<string>("");
  const [error, setError] = useState<string>("");
  const mounted = useRef(false);

  const loadTraders = useCallback(async (c: Chain, d: number) => {
    setLoading(true);
    setError("");
    setProgress(null);
    setTraders([]);

    try {
      const result = await fetchTradersStream(
        { chain: c, days: d, pool: 2000 },
        (p) => setProgress(p),
        (partial) => setTraders(partial)
      );
      setTraders(result.traders);
      setSource(result.source);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setLoading(false);
      setProgress(null);
    }
  }, []);

  // Auto-load on mount: Base network, 30 days
  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true;
      loadTraders("base", 30);
    }
  }, [loadTraders]);

  const handleChainChange = (c: Chain) => {
    setChain(c);
    loadTraders(c, days);
  };

  const handleDaysChange = (d: number) => {
    setDays(d);
    loadTraders(chain, d);
  };

  return (
    <div className="min-h-screen flex flex-col">
      <Header
        chain={chain}
        days={days}
        onChainChange={handleChainChange}
        onDaysChange={handleDaysChange}
      />

      <main className="flex-1 p-6">
        {/* Controls */}
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-sm font-bold">Top Traders</h2>
            <p className="text-[10px] text-uni-muted">
              {chain} / last {days}d
              {source && ` / ${source}`}
            </p>
          </div>

          <button
            className={`btn ${loading ? "" : "btn-active"}`}
            onClick={() => loadTraders(chain, days)}
            disabled={loading}
          >
            {loading ? "Scanning..." : "Scan Traders"}
          </button>
        </div>

        {/* Progress */}
        {progress && <div className="mb-4"><ScrapeProgress progress={progress} /></div>}

        {/* Error */}
        {error && (
          <div className="card p-3 mb-4 border-uni-red/50">
            <span className="text-xs text-uni-red">{error}</span>
          </div>
        )}

        {/* Results */}
        {traders.length > 0 ? (
          <div className="card">
            <TraderTable traders={traders} chain={chain} />
          </div>
        ) : loading ? (
          <div className="card p-12 text-center">
            <div className="animate-pulse-glow text-uni-pink text-sm mb-2">
              Scanning traders on {chain}...
            </div>
            <p className="text-[10px] text-uni-muted">
              Fetching {days}d of swap data from The Graph
            </p>
          </div>
        ) : !error ? (
          <div className="card p-12 text-center">
            <p className="text-uni-muted text-sm">
              Click &quot;Scan Traders&quot; to discover top Uniswap traders
            </p>
            <p className="text-[10px] text-uni-muted mt-2">
              Scrapes The Graph for swap data across {chain}
            </p>
          </div>
        ) : null}
      </main>
    </div>
  );
}
