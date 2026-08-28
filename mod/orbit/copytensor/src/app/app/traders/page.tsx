import Leaderboard from "../components/Leaderboard";
import PageHeader from "../components/PageHeader";

export default function TradersPage() {
  return (
    <div className="space-y-5">
      <PageHeader title="TRADERS">
        Coldkeys ranked by alpha PnL — price gains split from stake flow, so
        a deposit never outranks a trade. Click a row for the full breakdown,
        COPY to mirror its allocations.
      </PageHeader>
      <Leaderboard />
    </div>
  );
}
