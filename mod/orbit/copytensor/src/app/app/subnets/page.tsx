import MarketStrip from "../components/MarketStrip";
import SubnetsGrid from "../components/SubnetsGrid";
import PageHeader from "../components/PageHeader";

export default function SubnetsPage() {
  return (
    <div className="space-y-5">
      <PageHeader title="SUBNETS">
        Live alpha pools across every Bittensor subnet — price, 24h move,
        market cap and volume from the local index. Click a subnet for its
        chart and validator rankings.
      </PageHeader>
      <MarketStrip />
      <SubnetsGrid />
    </div>
  );
}
