"use client";

import { useEffect, useMemo, useState } from "react";

import {
  commitmentOf,
  countdown,
  credits,
  freshSalt,
  loadBets,
  LocalBet,
  markRevealed,
  MICRO,
  post,
  Round,
  saveBet,
  signMessage,
  Wallet,
} from "../lib/api";
import { Card, Note, Ok, Problem } from "./ui";

/**
 * Placing a bet, in three steps that all happen here:
 *
 *   1. a salt is generated in this tab
 *   2. the commitment is hashed in this tab
 *   3. only the hash, the amount and a signature are sent
 *
 * The salt goes to localStorage. If it is lost before the reveal window the
 * stake forfeits — that is not a bug to be smoothed over, it is the cost of
 * the server not being able to open your bet for you.
 */
export default function BetPanel({
  round,
  wallet,
  account,
  now,
  reload,
}: {
  round: Round | null;
  wallet: Wallet | null;
  account: any;
  now: number;
  reload: () => Promise<void>;
}) {
  const [model, setModel] = useState("");
  const [amount, setAmount] = useState("5");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [bets, setBets] = useState<LocalBet[]>([]);

  useEffect(() => setBets(loadBets()), []);
  useEffect(() => {
    if (round && !model && round.entrants.length) setModel(round.entrants[0]);
  }, [round, model]);

  const mine = useMemo(
    () =>
      bets
        .filter((b) => !wallet || b.owner === wallet.address)
        .sort((a, b) => b.placedAt - a.placedAt),
    [bets, wallet],
  );

  if (!round) return <Card title="no round">nothing is taking bets right now.</Card>;

  const micro = Math.round(parseFloat(amount || "0") * MICRO);
  const canBet = round.phase === "open" && wallet && micro >= round.params.min_bet;

  const placeBet = async () => {
    if (!wallet) return;
    setBusy(true);
    setError(null);
    setOk(null);
    try {
      const salt = freshSalt();
      const commitment = await commitmentOf(round.id, wallet.address, model, micro, salt);
      const message = `prerank:commit:${round.id}:${commitment}:${micro}:${account?.next_nonce ?? 0}`;
      const signature = await signMessage(wallet, message);
      await post("/commit", {
        address: wallet.address,
        signature,
        round: round.id,
        commitment,
        amount: micro,
        nonce: account?.next_nonce ?? 0,
      });
      // Store the salt only once the server has accepted the commitment —
      // a salt for a bet that was refused is just clutter.
      saveBet({
        round: round.id,
        owner: wallet.address,
        model,
        amount: micro,
        salt,
        commitment,
        placedAt: Math.floor(Date.now() / 1000),
      });
      setBets(loadBets());
      setOk(`sealed ${credits(micro)} credits on a model only this browser knows`);
      await reload();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const revealBet = async (bet: LocalBet) => {
    setBusy(true);
    setError(null);
    setOk(null);
    try {
      await post("/reveal", {
        round: bet.round,
        commitment: bet.commitment,
        model: bet.model,
        salt: bet.salt,
      });
      markRevealed(bet.commitment);
      setBets(loadBets());
      setOk(`opened ${credits(bet.amount)} on ${bet.model}`);
      await reload();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid gap-4">
      <Card
        title="place a sealed bet"
        right={<span className="text-[11px] text-muted">min {credits(round.params.min_bet)} credits</span>}
      >
        {!wallet && <Note tone="warn">sign in to bet.</Note>}
        <div className="grid md:grid-cols-[1fr_140px_auto] gap-3 items-end mt-2">
          <div>
            <div className="text-[10px] tracking-[0.14em] text-muted uppercase mb-1">model</div>
            <select className="field" value={model} onChange={(e) => setModel(e.target.value)}>
              {round.entrants.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </div>
          <div>
            <div className="text-[10px] tracking-[0.14em] text-muted uppercase mb-1">credits</div>
            <input
              className="field"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              inputMode="decimal"
            />
          </div>
          <button className="btn btn-primary" disabled={!canBet || busy} onClick={placeBet}>
            {busy ? "sealing…" : "seal it"}
          </button>
        </div>

        {round.phase !== "open" && (
          <Note tone="warn">
            bets closed for {round.id}. this round is {round.phase}.
          </Note>
        )}
        {round.phase === "open" && (
          <Note>
            the amount is public and locked from your balance the moment you
            commit; the model is a hash until you open it between{" "}
            {new Date(round.reveal_at * 1000).toUTCString().slice(17, 22)} and{" "}
            {new Date(round.seal_at * 1000).toUTCString().slice(17, 22)} UTC —{" "}
            {countdown(round.reveal_at, now)} from now. an unopened bet forfeits
            its stake to the pool.
          </Note>
        )}
        <Problem error={error} />
        <Ok message={ok} />
      </Card>

      <Card
        title="your sealed bets"
        right={
          round.phase === "reveal" ? (
            <span className="text-[11px] text-warn">reveal window is open</span>
          ) : null
        }
      >
        {!mine.length && <Note>nothing sealed from this browser yet.</Note>}
        {mine.length > 0 && (
          <table className="grid">
            <thead>
              <tr>
                <th>round</th>
                <th>model</th>
                <th className="text-right">credits</th>
                <th>commitment</th>
                <th className="text-right"></th>
              </tr>
            </thead>
            <tbody>
              {mine.map((b) => (
                <tr key={b.commitment}>
                  <td>{b.round}</td>
                  <td>{b.model}</td>
                  <td className="text-right">{credits(b.amount)}</td>
                  <td className="mono-dim">{b.commitment.slice(0, 14)}…</td>
                  <td className="text-right">
                    {b.revealed ? (
                      <span className="text-up text-[11px]">opened</span>
                    ) : b.round === round.id && round.phase === "reveal" ? (
                      <button className="btn text-[11px] py-1" disabled={busy} onClick={() => revealBet(b)}>
                        open it
                      </button>
                    ) : b.round === round.id && round.phase === "open" ? (
                      <span className="text-muted text-[11px]">
                        opens in {countdown(round.reveal_at, now)}
                      </span>
                    ) : (
                      <span className="text-down text-[11px]">forfeited</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <Note tone="warn">
          the salts for these bets are in this browser&apos;s local storage and
          nowhere else. clear it before the reveal window and the stake is gone.
        </Note>
      </Card>
    </div>
  );
}
