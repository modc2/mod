import LivePanel from "../components/LivePanel";
import { LegacyNote } from "../components/BoardBits";

export default function LiveRoute() {
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-gradient text-[24px] font-bold tracking-tight leading-tight">Live copy-trade engine</h1>
        <p className="text-xs text-muted mt-1">
          One backend agent wallet per master EOA, signing every order. Approve
          the agent once, configure leaders + sizing, and the engine mirrors
          fills until you stop it — survives API restarts.
        </p>
      </div>
      <LegacyNote>
        Mirrors a leader’s fills from the moment you start it. To buy into a
        trader’s whole book instead, and have it kept in sync, use
      </LegacyNote>

      <LivePanel />
    </div>
  );
}
