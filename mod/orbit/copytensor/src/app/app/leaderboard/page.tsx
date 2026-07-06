import Leaderboard from "../components/Leaderboard";

export default function LeaderboardPage() {
  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-display text-2xl font-bold mb-1">
          Bittensor copy-trading
        </h1>
        <p className="text-pixel-gray-light text-sm">
          Mirror subnet allocations of top performers based on N-day alpha PnL.
          All reads from public RPC — no third-party APIs, no wallet required.
        </p>
      </header>
      <Leaderboard />
    </div>
  );
}
