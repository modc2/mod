import Leaderboard from "../components/Leaderboard";

export default function TradersPage() {
  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-display text-2xl font-bold mb-1">Traders</h1>
        <p className="text-pixel-gray-light text-sm">
          Coldkeys ranked by alpha PnL — validators and the nominators staking
          behind them, drawn from the on-chain delegate set. Click a row for the
          full position breakdown, COPY to mirror their allocations, or grow the
          pool to rank more of them.
        </p>
      </header>
      <Leaderboard />
    </div>
  );
}
