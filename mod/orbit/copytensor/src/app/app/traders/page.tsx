import Leaderboard from "../components/Leaderboard";
import PageHeader from "../components/PageHeader";

export default function TradersPage() {
  return (
    <div className="space-y-5">
      <PageHeader title="TRADERS">
        Coldkeys ranked by alpha PnL — validators and the nominators staking
        behind them, drawn from the on-chain delegate set. Click a row for the
        full position breakdown, COPY to mirror their allocations, or grow the
        pool to rank more of them.
      </PageHeader>
      <Leaderboard />
    </div>
  );
}
