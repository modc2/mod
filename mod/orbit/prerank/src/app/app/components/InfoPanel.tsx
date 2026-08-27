"use client";

import { useEffect, useState } from "react";

import { api, credits } from "../lib/api";
import { Card, Note } from "./ui";

export default function InfoPanel({ status }: { status: any }) {
  const [info, setInfo] = useState<any>(null);

  useEffect(() => {
    api("/").then(setInfo).catch(() => {});
  }, []);

  return (
    <div className="grid gap-4">
      <Card title="what this is">
        <div className="text-[12px] leading-relaxed grid gap-3">
          <p>
            Once a day a round opens over a field of models. You back one to
            finish first. Your stake is locked when you commit and your choice
            is a hash until the reveal window — so the odds you take are the
            odds of being early, not of having waited to see where everyone
            else went. When the round seals, graders rank the field; a quorum
            has to agree. The winners divide the pool.
          </p>
          <p>
            Alongside the bets there is a second way into a model:{" "}
            <span className="text-fg">use it</span>. Every metered call has a
            spend and a cost, and the difference is the house&apos;s margin. That
            margin is handed back to you as a claim on the model you used,
            weighted by how early you were — the first credits through a model
            are worth their face value, and by the time it has absorbed K
            credits the same margin buys half as much. You never get back more
            than the house made on you, which is what makes it safe to give
            away and pointless to farm.
          </p>
        </div>
      </Card>

      <Card title="why it cannot be cheated">
        <div className="grid gap-2 text-[12px]">
          {(info?.cheat_proofing || []).map((line: string, i: number) => (
            <div key={i} className="flex gap-2">
              <span className="text-accent">·</span>
              <span>{line}</span>
            </div>
          ))}
        </div>
        <Note>
          Every one of those is a test in <span className="text-fg">tests/cheatproof.rs</span>,
          written as an attempt to break it.
        </Note>
      </Card>

      <Card title="the api">
        <table className="grid">
          <tbody>
            {Object.entries(info?.endpoints || {}).map(([name, value]: any) => (
              <tr key={name}>
                <td className="text-muted w-32">{name}</td>
                <td className="mono-dim">{value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <Card title="this instance">
        <table className="grid">
          <tbody>
            <tr>
              <td className="text-muted w-40">chain</td>
              <td className="mono-dim">{info?.chain_id}</td>
            </tr>
            <tr>
              <td className="text-muted">head</td>
              <td className="mono-dim">{info?.head}</td>
            </tr>
            <tr>
              <td className="text-muted">owner</td>
              <td className="mono-dim">{info?.owner}</td>
            </tr>
            <tr>
              <td className="text-muted">events</td>
              <td>{info?.events}</td>
            </tr>
            <tr>
              <td className="text-muted">round length</td>
              <td>
                {info?.schedule?.day_secs}s · reveals at{" "}
                {((info?.schedule?.reveal_bps ?? 0) / 100).toFixed(0)}% · seals at{" "}
                {((info?.schedule?.seal_bps ?? 0) / 100).toFixed(0)}%
              </td>
            </tr>
            <tr>
              <td className="text-muted">house fee</td>
              <td>{(info?.params?.fee_bps ?? 0) / 100}%</td>
            </tr>
            <tr>
              <td className="text-muted">grader quorum</td>
              <td>{info?.params?.quorum}</td>
            </tr>
            <tr>
              <td className="text-muted">earliness K</td>
              <td>{credits(info?.params?.earliness_k)} credits</td>
            </tr>
            <tr>
              <td className="text-muted">edge cap</td>
              <td>{credits(info?.params?.edge_cap)} credits per model per round</td>
            </tr>
            <tr>
              <td className="text-muted">treasury</td>
              <td>{credits(status?.treasury)} credits</td>
            </tr>
            <tr>
              <td className="text-muted">open mode</td>
              <td style={{ color: info?.open_mode ? "var(--warn)" : "var(--muted)" }}>
                {info?.open_mode ? "ON — unsigned actions are accepted" : "off"}
              </td>
            </tr>
          </tbody>
        </table>
      </Card>
    </div>
  );
}
