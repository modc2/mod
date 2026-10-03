import PageHeader from "../components/PageHeader";
import StratAgent from "../components/StratAgent";

export default function AgentPage() {
  return (
    <div className="space-y-5">
      <PageHeader title="AGENT">
        Reads the same board, trade tape and screener you see here, hands back
        a weighted basket with a reason for every name — and works the book
        for you: starts copies, re-sizes them, syncs to the chain. It asks
        first, every time. Every write stops as a card here with the numbers
        on it and does not run until you approve it; decline and your reason
        goes back to the agent as the answer.
      </PageHeader>
      <StratAgent />
    </div>
  );
}
