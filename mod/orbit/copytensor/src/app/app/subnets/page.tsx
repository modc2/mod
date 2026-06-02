import SubnetsGrid from "../components/SubnetsGrid";

export default function SubnetsPage() {
  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-display text-2xl font-bold mb-1">Subnets</h1>
        <p className="text-pixel-gray-light text-sm">
          Live alpha pools across every Bittensor subnet. Click a subnet for
          validator rankings and recent flow.
        </p>
      </header>
      <SubnetsGrid />
    </div>
  );
}
