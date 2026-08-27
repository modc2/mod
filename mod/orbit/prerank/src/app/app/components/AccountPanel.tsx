"use client";

import { useState } from "react";

import { credits, MICRO, post, Round, short, signMessage, Wallet } from "../lib/api";
import { Card, Note, Ok, Problem, Stat } from "./ui";

/**
 * Your account, plus the two roles that can do more than bet: a grader
 * submits a ranking for a sealed round, and the owner sets the field and the
 * people allowed to grade and to meter.
 */
export default function AccountPanel({
  wallet,
  account,
  status,
  round,
  reload,
}: {
  wallet: Wallet | null;
  account: any;
  status: any;
  round: Round | null;
  reload: () => Promise<void>;
}) {
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [ranking, setRanking] = useState<string[]>([]);
  const [roster, setRoster] = useState("");
  const [grantTo, setGrantTo] = useState("");
  const [grantAmount, setGrantAmount] = useState("100");
  const [roleAddr, setRoleAddr] = useState("");

  const run = async (label: string, fn: () => Promise<any>) => {
    setBusy(true);
    setError(null);
    setOk(null);
    try {
      await fn();
      setOk(label);
      await reload();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const signed = async (message: string, path: string, body: any) => {
    if (!wallet) throw new Error("sign in first");
    const signature = await signMessage(wallet, message);
    return post(path, { address: wallet.address, signature, ...body });
  };

  const field = round?.entrants || [];
  const order = ranking.length ? ranking : field;

  const move = (i: number, delta: number) => {
    const next = [...order];
    const j = i + delta;
    if (j < 0 || j >= next.length) return;
    [next[i], next[j]] = [next[j], next[i]];
    setRanking(next);
  };

  return (
    <div className="grid gap-4">
      <Card title="account">
        {!wallet && <Note>sign in to see your balance.</Note>}
        {wallet && (
          <>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <Stat label="balance" value={credits(account?.balance)} hint="credits" />
              <Stat label="locked" value={credits(account?.locked)} hint="in sealed bets" />
              <Stat label="next nonce" value={account?.next_nonce ?? 0} hint="each signature is good once" />
              <Stat
                label="roles"
                value={
                  [
                    account?.is_owner && "owner",
                    account?.is_attestor && "grader",
                    account?.is_meter && "meter",
                  ]
                    .filter(Boolean)
                    .join(" · ") || "bettor"
                }
              />
            </div>
            <div className="mt-2 mono-dim text-[11px]">{wallet.address}</div>
          </>
        )}
      </Card>

      {account?.is_attestor && (
        <Card
          title="grade this round"
          right={
            <span className="text-[11px] text-muted">
              {round?.attestations?.length ?? 0} of {round?.quorum ?? 2} needed
            </span>
          }
        >
          <Note>
            Put the field in order, best first. Your ranking is published as a
            hash immediately and in full only once the round settles, so a
            second grader cannot copy the first. A quorum of graders must land
            on the same order; two different answers void the round.
          </Note>
          <div className="mt-3 grid gap-1">
            {order.map((m, i) => (
              <div key={m} className="flex items-center gap-2 text-[12px]">
                <span className="text-muted w-5">{i + 1}.</span>
                <span className="flex-1">{m}</span>
                <button className="btn text-[10px] py-0.5 px-2" onClick={() => move(i, -1)}>
                  ↑
                </button>
                <button className="btn text-[10px] py-0.5 px-2" onClick={() => move(i, 1)}>
                  ↓
                </button>
              </div>
            ))}
          </div>
          <button
            className="btn btn-primary mt-3"
            disabled={busy || !round || round.phase !== "sealed"}
            onClick={() =>
              run("ranking submitted", async () => {
                const hash = await rankHash(round!.id, order);
                return signed(`prerank:attest:${round!.id}:${hash}`, "/attest", {
                  round: round!.id,
                  ranking: order,
                });
              })
            }
          >
            submit ranking
          </button>
          {round && round.phase !== "sealed" && (
            <Note tone="warn">grading opens when the round seals and closes when it settles.</Note>
          )}
        </Card>
      )}

      {account?.is_owner && (
        <>
          <Card title="the field">
            <div className="grid md:grid-cols-[1fr_auto] gap-3 items-end">
              <div>
                <div className="text-[10px] tracking-[0.14em] text-muted uppercase mb-1">
                  models, comma separated
                </div>
                <input
                  className="field"
                  value={roster}
                  onChange={(e) => setRoster(e.target.value)}
                  placeholder={status?.roster?.join(", ") || "opus, sonnet, haiku"}
                />
              </div>
              <button
                className="btn"
                disabled={busy}
                onClick={() =>
                  run("field set", () => {
                    const models = roster
                      .split(",")
                      .map((m) => m.trim())
                      .filter(Boolean)
                      .sort();
                    return signed(`prerank:roster:${models.join(",")}`, "/roster", { models });
                  })
                }
              >
                set field
              </button>
            </div>
            <Note>
              current: {status?.roster?.join(" · ") || "none — the field forms from metered usage"}.
              a change takes effect at the next round; a round already taking
              bets keeps the field it opened with.
            </Note>
          </Card>

          <Card title="credits">
            <div className="grid md:grid-cols-[1fr_140px_auto] gap-3 items-end">
              <div>
                <div className="text-[10px] tracking-[0.14em] text-muted uppercase mb-1">
                  account, or the word treasury
                </div>
                <input className="field" value={grantTo} onChange={(e) => setGrantTo(e.target.value)} placeholder="0x… | treasury" />
              </div>
              <div>
                <div className="text-[10px] tracking-[0.14em] text-muted uppercase mb-1">credits</div>
                <input className="field" value={grantAmount} onChange={(e) => setGrantAmount(e.target.value)} />
              </div>
              <button
                className="btn"
                disabled={busy}
                onClick={() =>
                  run("credited", () => {
                    const amount = Math.round(parseFloat(grantAmount || "0") * MICRO);
                    // The engine normalises the target before checking the
                    // signature, so a checksummed address here would sign a
                    // message the server never reads.
                    const account = grantTo.trim() === "treasury" ? "treasury" : grantTo.trim().toLowerCase();
                    return signed(`prerank:credit:${account}:${amount}`, "/credits/grant", {
                      account,
                      amount,
                      memo: "console",
                    });
                  })
                }
              >
                credit
              </button>
            </div>
            <Note>
              treasury {credits(status?.treasury)} · issued {credits(status?.issued)}. the treasury
              is what funds early-user positions, so it has to hold enough to
              cover the margin being handed back.
            </Note>
          </Card>

          <Card title="graders and meters">
            <div className="grid md:grid-cols-[1fr_auto_auto] gap-3 items-end">
              <div>
                <div className="text-[10px] tracking-[0.14em] text-muted uppercase mb-1">address</div>
                <input className="field" value={roleAddr} onChange={(e) => setRoleAddr(e.target.value)} placeholder="0x…" />
              </div>
              <button
                className="btn"
                disabled={busy}
                onClick={() =>
                  run("grader added", () =>
                    signed(`prerank:attestor:add:${roleAddr.toLowerCase()}`, "/attestors", {
                      target: roleAddr,
                      label: "console",
                    }),
                  )
                }
              >
                add grader
              </button>
              <button
                className="btn"
                disabled={busy}
                onClick={() =>
                  run("meter added", () =>
                    signed(`prerank:meter:add:${roleAddr.toLowerCase()}`, "/meters", {
                      target: roleAddr,
                      label: "console",
                    }),
                  )
                }
              >
                add meter
              </button>
            </div>
            <div className="mt-3 grid md:grid-cols-2 gap-4 text-[12px]">
              <div>
                <div className="text-[10px] tracking-[0.14em] text-muted uppercase mb-1">graders</div>
                {Object.entries(status?.attestors || {}).map(([addr, label]: any) => (
                  <div key={addr} className="mono-dim">
                    {short(addr)} <span className="text-muted">{label}</span>
                  </div>
                ))}
                {!Object.keys(status?.attestors || {}).length && (
                  <div className="text-muted">none — no round can settle</div>
                )}
              </div>
              <div>
                <div className="text-[10px] tracking-[0.14em] text-muted uppercase mb-1">meters</div>
                {Object.entries(status?.meters || {}).map(([addr, label]: any) => (
                  <div key={addr} className="mono-dim">
                    {short(addr)} <span className="text-muted">{label}</span>
                  </div>
                ))}
                {!Object.keys(status?.meters || {}).length && (
                  <div className="text-muted">none — no usage can be banked</div>
                )}
              </div>
            </div>
            <Note tone="warn">
              a quorum of {round?.quorum ?? 2} graders has to agree before a round pays out, so
              fewer than that many registered graders means every round voids.
            </Note>
          </Card>
        </>
      )}

      <Problem error={error} />
      <Ok message={ok} />
    </div>
  );
}

/** Same layout as `rank_hash` in types.rs. */
async function rankHash(round: string, ranking: string[]): Promise<string> {
  const text = ["prerank:rank", round, ranking.join(">")].join("|");
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
