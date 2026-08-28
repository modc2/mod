"use client";

import { useState } from "react";

import { credits, MICRO, post, Round, short, signMessage, Wallet } from "../lib/api";
import { Card, Note, Ok, Problem } from "./ui";

/**
 * A round's per-model token. It exists for the length of one round: minted
 * when a bet is opened or an edge position lands, transferable once the round
 * is sealed, and redeemed for the payout when the round settles.
 */
export default function TokensPanel({
  round,
  wallet,
  account,
  reload,
}: {
  round: Round | null;
  wallet: Wallet | null;
  account: any;
  reload: () => Promise<void>;
}) {
  const [to, setTo] = useState("");
  const [units, setUnits] = useState("1");
  const [picked, setPicked] = useState<{ round: string; model: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  const positions = account?.positions || [];
  const tradable = round?.phase === "sealed";

  const send = async () => {
    if (!wallet || !picked) return;
    setBusy(true);
    setError(null);
    setOk(null);
    try {
      const micro = Math.round(parseFloat(units || "0") * MICRO);
      const nonce = account?.next_nonce ?? 0;
      const message = `prerank:transfer:${picked.round}:${picked.model}:${to.toLowerCase()}:${micro}:${nonce}`;
      const signature = await signMessage(wallet, message);
      await post("/transfer", {
        address: wallet.address,
        signature,
        round: picked.round,
        model: picked.model,
        to,
        units: micro,
        nonce,
      });
      setOk(`sent ${credits(micro)} units of ${picked.model} to ${short(to)}`);
      await reload();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid gap-4">
      <Card title="your positions">
        {!account && <Note>sign in to see what you hold.</Note>}
        {account && !positions.length && <Note>you hold no round tokens.</Note>}
        {positions.length > 0 && (
          <table className="grid">
            <thead>
              <tr>
                <th>round</th>
                <th>token</th>
                <th className="text-right">units</th>
                <th className="text-right"></th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p: any) => (
                <tr key={`${p.round}:${p.model}`}>
                  <td>{p.round}</td>
                  <td>
                    PRE-{p.model.toUpperCase()}-{p.round}
                  </td>
                  <td className="text-right">{credits(p.units)}</td>
                  <td className="text-right">
                    <button
                      className="btn text-[11px] py-1"
                      onClick={() => setPicked({ round: p.round, model: p.model })}
                    >
                      send
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title="send a token">
        {!picked && <Note>pick a position above.</Note>}
        {picked && (
          <>
            <div className="text-[12px] mb-3">
              PRE-{picked.model.toUpperCase()}-{picked.round}
            </div>
            <div className="grid md:grid-cols-[1fr_140px_auto] gap-3 items-end">
              <div>
                <div className="text-[10px] tracking-[0.14em] text-muted uppercase mb-1">to</div>
                <input className="field" value={to} onChange={(e) => setTo(e.target.value)} placeholder="0x…" />
              </div>
              <div>
                <div className="text-[10px] tracking-[0.14em] text-muted uppercase mb-1">units</div>
                <input className="field" value={units} onChange={(e) => setUnits(e.target.value)} inputMode="decimal" />
              </div>
              <button className="btn btn-primary" disabled={!wallet || !tradable || busy} onClick={send}>
                send
              </button>
            </div>
          </>
        )}
        <Note tone={tradable ? "muted" : "warn"}>
          {tradable
            ? "the round is sealed and the pools are public — this is the window where a position can change hands."
            : "tokens trade only between the seal and the settlement. moving one while bets are still open would announce which model you are on, which is exactly what your commitment is hiding."}
        </Note>
        <Problem error={error} />
        <Ok message={ok} />
      </Card>
    </div>
  );
}
