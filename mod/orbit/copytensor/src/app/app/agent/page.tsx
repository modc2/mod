import PageHeader from "../components/PageHeader";
import StratAgent from "../components/StratAgent";

export default function AgentPage() {
  return (
    <div className="space-y-5">
      <PageHeader title="AGENT">
        Reads the same board, trade tape and screener you see here, and hands
        back a weighted basket with a reason for every name. Read-only —
        proposals land as cards, and going live is still your click.
      </PageHeader>
      <StratAgent />
    </div>
  );
}
