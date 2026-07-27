import MarketStrip from "../components/MarketStrip";
import SubnetsGrid from "../components/SubnetsGrid";

export default function SubnetsPage() {
  return (
    <div className="space-y-5">
      <header>
        <h1 className="font-display text-2xl font-bold mb-1">Subnets</h1>
        <p className="text-pixel-gray-light text-sm">
          Live alpha pools across every Bittensor subnet — price, 24h move,
          market cap and volume from the local index. Click a subnet for its
          chart and validator rankings.
        </p>
      </header>
      <MarketStrip />
      <SubnetsGrid />
    </div>
  );
}
