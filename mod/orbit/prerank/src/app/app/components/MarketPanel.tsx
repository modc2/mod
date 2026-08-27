"use client";

import { useEffect, useState } from "react";

import { api, countdown, credits, MICRO, Round } from "../lib/api";
import { Card, Note, PhaseBadge, Stat } from "./ui";

export default function MarketPanel({
  round: live,
  status,
  now,
}: {
  round: Round | null;
  status: any;
  now: number;
}) {
  const [history, setHistory] = useState<Round[]>([]);
  const [board, setBoard] = useState<any>(null);
  const [picked, setPicked] = useState<string | null>(null);

  useEffect(() => {
    api<{ rounds: Round[] }>("/rounds")
      .then((r) => setHistory(r.rounds || []))
      .catch(() => {});
    api("/leaderboard").then(setBoard).catch(() => {});
  }, [live?.phase, live?.id]);

  // Once a round settles the next one opens immediately, so a settled round
  // is never the current one for long. Picking it from the history is the
  // only way to read its payouts.
  const round = (picked && history.find((r) => r.id === picked)) || live;

  if (!round) {
    return (
      <Card title="no round">
        <Note>
          A round needs a field of at least two models. The owner sets it on the
          ACCOUNT tab, or it forms on its own out of whatever models have had
          credits spent on them.
        </Note>
      </Card>
    );
  }

  const sealed = round.phase !== "open";
  const settled = round.phase === "settled" || round.phase === "voided";

  return (
    <div className="grid gap-4">
      <Card
        title={`round ${round.id}`}
        right={
          <div className="flex items-center gap-2">
            {picked && picked !== live?.id && (
              <button className="btn text-[11px] py-1" onClick={() => setPicked(null)}>
                back to the open round
              </button>
            )}
            <PhaseBadge phase={round.phase} />
            <span className="text-[11px] text-muted">
              fee {round.params.fee_bps / 100}% · quorum {round.quorum}
            </span>
          </div>
        }
      >
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <Stat
            label="staked"
            value={credits(sealed ? round.pool : round.staked_visible)}
            hint={sealed ? "the pool, in credits" : "committed so far — direction hidden"}
          />
          <Stat label="sealed bets" value={round.commitments} hint={`${round.revealed} opened`} />
          <Stat
            label="edge from usage"
            value={sealed ? credits(round.edge_money) : "—"}
            hint="the house's margin, staked for early users"
          />
          <Stat label="field" value={round.entrants.length} hint={round.entrants.join(" · ")} />
        </div>

        <div className="mt-4 grid grid-cols-4 gap-1 text-[10px] text-muted">
          {[
            ["open", round.opens_at, round.reveal_at],
            ["reveal", round.reveal_at, round.seal_at],
            ["sealed", round.seal_at, round.settle_at],
            ["settled", round.settle_at, round.settle_at],
          ].map(([label, from, to]: any) => {
            const active = now >= from && (now < to || label === "settled");
            return (
              <div key={label}>
                <div
                  className="h-1 rounded"
                  style={{ background: active ? "var(--accent)" : "var(--edge)" }}
                />
                <div className="mt-1 uppercase tracking-[0.1em]" style={{ color: active ? "var(--fg)" : undefined }}>
                  {label}
                </div>
                {/* "ends in" for the phase we are in, "in" for one still
                    ahead — otherwise the phase that is closing and the one
                    that is opening both read "in 9s". */}
                {now < from ? (
                  <div>in {countdown(from, now)}</div>
                ) : now < to ? (
                  <div>ends in {countdown(to, now)}</div>
                ) : null}
              </div>
            );
          })}
        </div>
      </Card>

      <Card
        title="the field"
        right={
          !sealed ? (
            <span className="text-[11px] text-warn">pools hidden until the reveal</span>
          ) : null
        }
      >
        <table className="grid">
          <thead>
            <tr>
              <th>model</th>
              <th className="text-right">claim units</th>
              <th className="text-right">of which early-user</th>
              <th className="text-right">holders</th>
              <th className="text-right">pays per unit</th>
            </tr>
          </thead>
          <tbody>
            {round.books.map((b) => {
              const winner = settled && round.result?.winner === b.model;
              return (
                <tr key={b.model} style={winner ? { color: "var(--up)" } : undefined}>
                  <td>
                    {b.model}
                    {winner && <span className="ml-2 pill" style={{ color: "var(--up)", borderColor: "var(--up)" }}>won</span>}
                  </td>
                  <td className="text-right">{sealed ? credits(b.units) : "sealed"}</td>
                  <td className="text-right text-muted">{sealed ? credits(b.edge_units) : "—"}</td>
                  <td className="text-right text-muted">{sealed ? (b.holders ?? 0) : "—"}</td>
                  <td className="text-right">
                    {b.implied_odds ? `${b.implied_odds.toFixed(2)}×` : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {!sealed && (
          <Note>
            Nobody — including this server — can read which model a bet backs
            until its owner opens it. That is what makes being early worth
            something: you cannot wait to see where the money went.
          </Note>
        )}
      </Card>

      {round.attestations.length > 0 && (
        <Card title="graders">
          <table className="grid">
            <thead>
              <tr>
                <th>grader</th>
                <th>rank hash</th>
                <th>ranking</th>
                <th className="text-right">counted</th>
              </tr>
            </thead>
            <tbody>
              {round.attestations.map((a) => (
                <tr key={a.attestor}>
                  <td className="mono-dim">{a.attestor.slice(0, 10)}…</td>
                  <td className="mono-dim">{a.rank_hash.slice(0, 16)}…</td>
                  <td>{a.ranking ? a.ranking.join(" > ") : <span className="text-muted">sealed</span>}</td>
                  <td className="text-right" style={{ color: a.counted ? "var(--up)" : "var(--down)" }}>
                    {a.counted ? "yes" : "no"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {round.attestations.some((a) => a.note) && (
            <Note tone="warn">
              {round.attestations.find((a) => a.note)?.note}
            </Note>
          )}
        </Card>
      )}

      {round.result && (
        <Card title="result">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <Stat label="outcome" value={round.result.outcome} />
            <Stat label="winner" value={round.result.winner || "—"} />
            <Stat label="pool" value={credits(round.result.total_pool)} />
            <Stat label="house fee" value={credits(round.result.fee)} />
          </div>
          {round.result.reason && <Note tone="warn">{round.result.reason}</Note>}
          <div className="mt-3">
            <table className="grid">
              <thead>
                <tr>
                  <th>paid</th>
                  <th className="text-right">credits</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(round.result.payouts || {}).map(([addr, amount]: any) => (
                  <tr key={addr}>
                    <td className="mono-dim">{addr}</td>
                    <td className="text-right">{credits(amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <div className="grid md:grid-cols-2 gap-4">
        <Card title="past rounds">
          <table className="grid">
            <thead>
              <tr>
                <th>round</th>
                <th>phase</th>
                <th>winner</th>
                <th className="text-right">pool</th>
              </tr>
            </thead>
            <tbody>
              {history.slice(0, 12).map((r) => (
                <tr
                  key={r.id}
                  onClick={() => setPicked(r.id)}
                  className="cursor-pointer"
                  style={r.id === round.id ? { color: "var(--accent)" } : undefined}
                >
                  <td>{r.id}</td>
                  <td>
                    <PhaseBadge phase={r.phase} />
                  </td>
                  <td>{r.result?.winner || "—"}</td>
                  <td className="text-right">{credits(r.result?.total_pool ?? r.pool)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>

        <Card title="who wins" right={<span className="text-[11px] text-muted">{board?.settled_rounds ?? 0} settled</span>}>
          <table className="grid">
            <thead>
              <tr>
                <th>model</th>
                <th className="text-right">wins</th>
                <th className="text-right">rounds</th>
                <th className="text-right">rate</th>
              </tr>
            </thead>
            <tbody>
              {(board?.leaderboard || []).map((row: any) => (
                <tr key={row.model}>
                  <td>{row.model}</td>
                  <td className="text-right">{row.wins}</td>
                  <td className="text-right text-muted">{row.rounds}</td>
                  <td className="text-right">{(row.win_rate * 100).toFixed(0)}%</td>
                </tr>
              ))}
              {!board?.leaderboard?.length && (
                <tr>
                  <td colSpan={4} className="text-muted">
                    no round has settled yet
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </Card>
      </div>
    </div>
  );
}
