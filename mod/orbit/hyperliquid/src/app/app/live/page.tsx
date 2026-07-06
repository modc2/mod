import LivePanel from "../components/LivePanel";

export default function LiveRoute() {
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl tracking-wider text-ink">Live copy-trade engine</h1>
        <p className="text-xs text-muted mt-1">
          One backend agent wallet per master EOA, signing every order. Approve
          the agent once, configure leaders + sizing, and the engine mirrors
          fills until you stop it — survives API restarts.
        </p>
      </div>
      <LivePanel />
    </div>
  );
}
