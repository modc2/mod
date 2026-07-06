import Leaderboard from "../components/Leaderboard";

export default function TradersPage() {
  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-display text-2xl font-bold mb-1">Validators</h1>
        <p className="text-pixel-gray-light text-sm">
          Coldkeys ranked by alpha PnL. Click a row for the full position
          breakdown, or COPY to mirror their allocations.
        </p>
      </header>
      <Leaderboard />
    </div>
  );
}
