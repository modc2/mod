import MarketStrip from "../components/MarketStrip";
import SubnetsGrid from "../components/SubnetsGrid";
import PageHeader from "../components/PageHeader";

export default function SubnetsPage() {
  return (
    <div className="space-y-5">
      <PageHeader title="SUBNETS">
        Every subnet&rsquo;s alpha pool, live from the local index. Click one
        for its chart and validator rankings.
      </PageHeader>
      <MarketStrip />
      <SubnetsGrid />
    </div>
  );
}
