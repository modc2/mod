"use client";

import { useEffect, useState } from "react";

import { api, credits, MICRO } from "../lib/api";
import { Card, Note, Stat } from "./ui";

/**
 * The early-user side of the market: what a credit of margin buys on each
 * model right now, and what this account has already earned.
 */
/** The API sends the weight as an exact `num/den` so it can be checked; the
 *  table wants something readable. */
function weightPct(fraction: string): string {
  const [num, den] = (fraction || "").split("/").map(Number);
  if (!den) return "—";
  return `${((num / den) * 100).toFixed(1)}%`;
}

export default function EdgePanel({ account, status }: { account: any; status: any }) {
  const [models, setModels] = useState<any>(null);

  useEffect(() => {
    api("/models").then(setModels).catch(() => {});
  }, [account?.address]);

  const k = status?.current?.params?.earliness_k ?? 100 * MICRO;
  const pending = account?.pending_edge || [];
  const receipts = account?.receipts || [];
  const earned = pending.reduce((a: number, p: any) => a + p.units, 0);

  return (
    <div className="grid gap-4">
      <Card title="how the edge works">
        <div className="grid md:grid-cols-3 gap-4">
          <Stat
            label="what you get"
            value="the margin"
            hint="not your spend — the house's cut of it, handed back as a position"
          />
          <Stat
            label="what it's worth"
            value={`K / (K + c)`}
            hint={`c is the credits the model has already taken; K is ${credits(k)}`}
          />
          <Stat
            label="when it lands"
            value="the next round"
            hint="never the round that was already taking bets when you spent"
          />
        </div>
        <Note>
          Spending credits on a model buys you a claim on it winning, sized by
          the house&apos;s margin on that spend and weighted by how early you
          were. The first credits through a model are worth their face value;
          by the time it has absorbed K credits the same margin buys half as
          much. Because the claim comes out of the margin and not out of your
          spend, buying usage to build a position always costs more than the
          position it builds — which is the reason it is safe to give away.
        </Note>
      </Card>

      <Card title="models" right={<span className="text-[11px] text-muted">by credits spent</span>}>
        <table className="grid">
          <thead>
            <tr>
              <th>model</th>
              <th className="text-right">credits spent</th>
              <th className="text-right">house margin</th>
              <th className="text-right">weight now</th>
              <th className="text-right">units per credit of margin</th>
              <th className="text-right">in field</th>
            </tr>
          </thead>
          <tbody>
            {(models?.models || []).map((m: any) => (
              <tr key={m.model}>
                <td>{m.model}</td>
                <td className="text-right">{credits(m.credits)}</td>
                <td className="text-right text-muted">{credits(m.margin)}</td>
                <td className="text-right">{weightPct(m.edge_weight)}</td>
                <td className="text-right">{(m.units_per_credit_of_margin / MICRO).toFixed(3)}</td>
                <td className="text-right">{m.in_roster ? "yes" : "—"}</td>
              </tr>
            ))}
            {!models?.models?.length && (
              <tr>
                <td colSpan={6} className="text-muted">
                  no metered usage yet — a meter posts it at POST /usage
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>

      <Card
        title="your edge"
        right={<span className="text-[11px] text-muted">{credits(earned)} units waiting</span>}
      >
        {!account && <Note>sign in to see your usage.</Note>}
        {account && !pending.length && !receipts.length && (
          <Note>no metered usage on this address yet.</Note>
        )}
        {pending.length > 0 && (
          <table className="grid">
            <thead>
              <tr>
                <th>model</th>
                <th className="text-right">margin</th>
                <th className="text-right">weight</th>
                <th className="text-right">units</th>
                <th className="text-right">rounds waited</th>
              </tr>
            </thead>
            <tbody>
              {pending.map((p: any) => (
                <tr key={p.receipt}>
                  <td>{p.model}</td>
                  <td className="text-right">{credits(p.margin)}</td>
                  <td className="text-right text-muted">{p.weight}</td>
                  <td className="text-right">{credits(p.units)}</td>
                  <td className="text-right text-muted">{p.rounds_waited}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {receipts.length > 0 && (
          <>
            <div className="text-[10px] tracking-[0.14em] text-muted uppercase mt-4 mb-1">
              metered calls
            </div>
            <table className="grid">
              <thead>
                <tr>
                  <th>receipt</th>
                  <th>model</th>
                  <th className="text-right">spend</th>
                  <th className="text-right">cost</th>
                  <th className="text-right">margin</th>
                </tr>
              </thead>
              <tbody>
                {receipts.slice(-20).reverse().map((r: any) => (
                  <tr key={r.id}>
                    <td className="mono-dim">{r.id}</td>
                    <td>{r.model}</td>
                    <td className="text-right">{credits(r.spend)}</td>
                    <td className="text-right text-muted">{credits(r.cost)}</td>
                    <td className="text-right text-up">{credits(r.margin)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </Card>
    </div>
  );
}
