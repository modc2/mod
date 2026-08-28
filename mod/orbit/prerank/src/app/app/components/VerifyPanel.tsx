"use client";

import { useEffect, useState } from "react";

import { api, credits, loadBets, Round } from "../lib/api";
import { Card, Note, Problem, Stat } from "./ui";

/**
 * The audit tab. Three things a visitor can check without trusting the
 * server's word: that the log links, that the state is its fold, and that
 * their own bet is in the sealed set.
 *
 * The Merkle check below is done here, in the tab, against the published
 * root — the server hands over a sibling path and this code walks it.
 */
export default function VerifyPanel({ round }: { round: Round | null }) {
  const [report, setReport] = useState<any>(null);
  const [chain, setChain] = useState<any>(null);
  const [proof, setProof] = useState<any>(null);
  const [localCheck, setLocalCheck] = useState<boolean | null>(null);
  const [commitment, setCommitment] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    try {
      const [v, c] = await Promise.all([api("/verify"), api("/chain?limit=25")]);
      setReport(v);
      setChain(c);
    } catch (e: any) {
      setError(e.message);
    }
  };

  useEffect(() => {
    load();
    const mine = loadBets();
    if (mine.length) setCommitment(mine[mine.length - 1].commitment);
  }, [round?.phase]);

  const checkInclusion = async () => {
    setError(null);
    setProof(null);
    setLocalCheck(null);
    try {
      const target = round?.id;
      const p = await api(`/proof/${target}/${commitment.trim()}`);
      setProof(p);
      setLocalCheck(await walkPath(commitment.trim(), p.path, p.root));
    } catch (e: any) {
      setError(e.message);
    }
  };

  return (
    <div className="grid gap-4">
      <Card
        title="does this market check out"
        right={
          <button className="btn text-[11px] py-1" onClick={load}>
            re-check
          </button>
        }
      >
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <Stat
            label="verdict"
            value={
              report ? (
                <span style={{ color: report.ok ? "var(--up)" : "var(--down)" }}>
                  {report.ok ? "sound" : "broken"}
                </span>
              ) : (
                "…"
              )
            }
          />
          <Stat label="events" value={report?.events ?? "—"} hint="in the log" />
          <Stat label="rounds" value={report?.rounds ?? "—"} />
          <Stat
            label="credits accounted"
            value={report ? credits(report.conserved) : "—"}
            hint={report ? `of ${credits(report.issued)} issued` : ""}
          />
        </div>
        {report?.problems?.length > 0 && (
          <div className="mt-3">
            {report.problems.map((p: string, i: number) => (
              <div key={i} className="text-[12px] text-down">
                · {p}
              </div>
            ))}
          </div>
        )}
        <Note>
          The server keeps no state of its own: it keeps a hash-linked log, and
          everything you can read is the fold of that log. This check replays
          it from genesis, re-derives every balance, pool and payout, re-hashes
          every entry, and re-computes every sealed round&apos;s Merkle root. A
          disagreement anywhere shows up here rather than being smoothed over.
        </Note>
      </Card>

      <Card title="prove your bet was in the sealed set">
        <div className="grid md:grid-cols-[1fr_auto] gap-3 items-end">
          <div>
            <div className="text-[10px] tracking-[0.14em] text-muted uppercase mb-1">commitment</div>
            <input className="field" value={commitment} onChange={(e) => setCommitment(e.target.value)} />
          </div>
          <button className="btn" onClick={checkInclusion} disabled={!round || !commitment}>
            check inclusion
          </button>
        </div>
        {proof && (
          <div className="mt-3 text-[12px] grid gap-1">
            <div>
              <span className="text-muted">root </span>
              <span className="mono-dim">{proof.root}</span>
            </div>
            <div>
              <span className="text-muted">leaf {proof.index + 1} of {proof.leaves}, </span>
              <span className="text-muted">{proof.path.length} step path</span>
            </div>
            <div style={{ color: localCheck ? "var(--up)" : "var(--down)" }}>
              {localCheck
                ? "verified in this tab against the published root"
                : "the path does not reach the root"}
            </div>
            {!proof.sealed && <Note tone="warn">this round has not sealed yet, so the root is provisional.</Note>}
          </div>
        )}
        <Problem error={error} />
      </Card>

      <Card title="the log" right={<span className="mono-dim text-[11px]">head {report?.chain?.head?.slice(0, 16)}…</span>}>
        <table className="grid">
          <thead>
            <tr>
              <th>#</th>
              <th>event</th>
              <th>round</th>
              <th>hash</th>
            </tr>
          </thead>
          <tbody>
            {(chain?.entries || [])
              .slice()
              .reverse()
              .map((e: any) => (
                <tr key={e.seq}>
                  <td className="text-muted">{e.seq}</td>
                  <td>{e.kind}</td>
                  <td className="text-muted">{e.round || "—"}</td>
                  <td className="mono-dim">{e.hash.slice(0, 20)}…</td>
                </tr>
              ))}
          </tbody>
        </table>
        {chain && (
          <Note>
            showing {chain.count} of {chain.length}. the whole log is at{" "}
            <span className="text-fg">GET /chain</span> — pull it and fold it yourself.
          </Note>
        )}
      </Card>
    </div>
  );
}

/** Walk a sibling path up to a root, the same way `crypto.rs` does. */
async function walkPath(leaf: string, path: any[], root: string): Promise<boolean> {
  let acc = hexToBytes(leaf);
  for (const step of path) {
    const sib = hexToBytes(step.sibling);
    const buf = new Uint8Array(64);
    if (step.sibling_is_left) {
      buf.set(sib, 0);
      buf.set(acc, 32);
    } else {
      buf.set(acc, 0);
      buf.set(sib, 32);
    }
    acc = new Uint8Array(await crypto.subtle.digest("SHA-256", buf));
  }
  return bytesToHex(acc) === root.toLowerCase();
}

function hexToBytes(hex: string): Uint8Array {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16);
  return out;
}

function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes)
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
