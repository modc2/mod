import TopTraders from "./components/TopTraders";

export default function Home() {
  return (
    <div className="space-y-8">
      <section className="relative overflow-hidden">
        <span className="pill border-accent/25 text-accent">
          <span className="h-1.5 w-1.5 rounded-full bg-accent live-dot mr-1.5" />
          live leaderboard
        </span>
        <h1 className="mt-4 font-display font-bold text-4xl sm:text-5xl leading-[1.05] tracking-tight">
          <span className="text-gradient">Copy the sharpest</span>
          <br />
          <span className="text-ink">traders on Hyperliquid.</span>
        </h1>
        <p className="mt-4 max-w-xl text-sm text-muted leading-relaxed">
          Rank wallets by N-day performance, follow them with one click, or compose
          the best into a vault-backed index — settled on-chain, signed by your agent.
        </p>
      </section>
      <TopTraders />
    </div>
  );
}
