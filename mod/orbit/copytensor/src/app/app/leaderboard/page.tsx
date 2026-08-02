import Leaderboard from "../components/Leaderboard";
import PageHeader from "../components/PageHeader";

export default function LeaderboardPage() {
  return (
    <div className="space-y-5">
      <PageHeader title="BITTENSOR COPY-TRADING">
        Mirror subnet allocations of top performers based on N-day alpha PnL,
        ranked out of the bt module&rsquo;s local index — PnL split into what
        the book earned on price and what was staked in or out, so a deposit
        never outranks a trade. Open data, no third-party APIs, no wallet
        required.
      </PageHeader>
      <Leaderboard />
    </div>
  );
}
