'use client';
/* SUBNET: exactly what any chain reader sees — params, the epoch clock,
 * both member SETS (public keys, never names) and recent epochs' tags. */
import { useEffect, useState } from 'react';
import type { Board as B, Chain, Point, SimEvent, Tally } from '@/lib/api';
import { ago, fmt, get, hueOf, short } from '@/lib/api';

/* The node sleeps when idle; rather than an empty board, rebuild what a reader
 * saw at the end of the last recorded run (GET last_sim) and label it as such. */
type Snap = { at: number; board: B };
function snapOf(d: any, chain: Chain): Snap | null {
  const r = d?.result, ev: SimEvent[] = d?.events || [];
  if (!r?.tally) return null;
  const dep = ev.find((e) => e.kind === 'deploy') || {} as SimEvent;
  const reg = (role: string) => ev.filter((e) => e.kind === 'register' && e.role === role).map((e) => ({ x: e.x, y: '' }));
  const blocks = ev.map((e) => e.block).filter((b) => typeof b === 'number');
  const tally: Tally = { ...r.tally, open: false };
  return {
    at: d.at,
    board: {
      network: r.network, chain: chain.chain, deployed: true, devnet_up: false,
      contract: r.contract || dep.address, tempo: dep.tempo, min_ring: dep.min_ring,
      emission: dep.emission ?? r.yuma?.emission, block: blocks.length ? Math.max(...blocks) : undefined,
      epoch: r.epoch, miners: reg('miner'), validators: reg('validator'), epochs: [tally],
    },
  };
}

export function Ident({ hex }: { hex: string }) {
  const h = hueOf(hex);
  const bits = hex.replace(/^0x/, '').padEnd(16, '0').slice(-16);
  return (
    <span className="ident" aria-hidden>
      {Array.from(bits).map((c, i) => {
        const on = parseInt(c, 16) % 2 === 1;
        return <i key={i} style={{ background: on ? `hsl(${h} 70% ${55 + (i % 3) * 8}%)` : `hsl(${h} 30% 14%)` }} />;
      })}
    </span>
  );
}

function Clock({ left, tempo, epoch }: { left?: number; tempo?: number; epoch?: number }) {
  const p = tempo && left !== undefined ? 1 - left / tempo : 0;
  const R = 46, C = 2 * Math.PI * R;
  return (
    <svg viewBox="0 0 120 120" width={120} height={120}>
      <circle cx={60} cy={60} r={R} fill="none" stroke="rgba(255,255,255,0.07)" strokeWidth={8} />
      <circle cx={60} cy={60} r={R} fill="none" stroke="rgb(var(--c-rgb))" strokeWidth={8} strokeLinecap="round"
        strokeDasharray={`${C * p} ${C}`} transform="rotate(-90 60 60)" style={{ transition: 'stroke-dasharray 600ms' }} />
      <text x={60} y={58} textAnchor="middle" fill="var(--ink)" fontSize="22" fontWeight={800}>{epoch ?? '—'}</text>
      <text x={60} y={74} textAnchor="middle" fill="var(--ink3)" fontSize="8" fontWeight={700} letterSpacing="0.12em">
        {left !== undefined ? `${left} BLOCKS` : 'EPOCH'}
      </text>
      {left !== undefined && <text x={60} y={84} textAnchor="middle" fill="var(--ink3)" fontSize="8" fontWeight={700} letterSpacing="0.12em">LEFT</text>}
    </svg>
  );
}

function MemberSet({ title, list, past }: { title: string; list?: Point[]; past?: boolean }) {
  return (
    <div className={`card${past ? ' past' : ''}`}>
      <h3>{title} <span className="pill">{list?.length ?? 0}</span></h3>
      <p className="lede">The anonymity set. A signature proves &ldquo;one of these&rdquo;, never which.</p>
      {!list?.length ? <div className="empty sm">no members yet</div> : (
        <div className="members">
          {list.map((p) => (
            <div className="member" key={p.x} title={p.y ? `x ${p.x}\ny ${p.y}` : `x ${p.x}`}><Ident hex={p.x} />{short(p.x, 8, 5)}</div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function Board({ chain, board, net, setNet, err, onRun }: {
  chain?: Chain; board?: B | null; net: string; setNet: (n: string) => void; err?: string; onRun?: () => void;
}) {
  const asleep = !!board && !board.devnet_up;
  const [snap, setSnap] = useState<Snap | null>(null);
  useEffect(() => {
    setSnap(null);
    if (!asleep || !chain || net !== chain.net) return;
    let live = true;
    get('last_sim', { chain: chain.chain }).then((d) => { if (live) setSnap(snapOf(d, chain)); }).catch(() => {});
    return () => { live = false; };
  }, [asleep, chain, net]);
  const past = asleep && !!snap;
  const view: B | null | undefined = past ? { ...snap!.board, open: board?.open } : board;
  const known = new Set(['network', 'chain', 'deployed', 'devnet_up', 'contract', 'owner', 'tempo', 'min_ring', 'emission',
    'open', 'block', 'epoch', 'blocks_left', 'miners', 'validators', 'epochs']);
  const extra = board ? Object.entries(board).filter(([k, v]) => !known.has(k) && (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean')) : [];
  return (
    <div className="stack fade">
      <div className="card hi">
        <div className="row bhead" style={{ alignItems: 'flex-start', gap: 26 }}>
          <Clock left={board?.blocks_left} tempo={board?.tempo} epoch={board?.epoch ?? snap?.board.epoch} />
          <div className="bmain">
            <div className="row" style={{ marginBottom: 12 }}>
              <h3 style={{ margin: 0 }}><span className="n">SUBNET</span> {chain?.title}</h3>
              <div className="seg">
                {(chain?.nets || []).map((n) => <button key={n} className={net === n ? 'on' : ''} onClick={() => setNet(n)}>{n}</button>)}
              </div>
              {board && <span className={`pill ${board.deployed ? 'good' : 'warn'}`}>{board.deployed ? 'deployed' : 'not deployed'}</span>}
              {board && <span className={`pill ${board.devnet_up ? 'good' : 'idle'}`} title={board.devnet_up ? undefined : 'local nodes sleep when idle and wake on the next run'}>{board.devnet_up ? 'node up' : 'node asleep'}</span>}
              {board && <span className="pill">{board.open ? 'open registry' : 'owner-gated registry'}</span>}
            </div>
            {err && <div className="err">{err}</div>}
            {asleep ? (
              <div className="sleep">
                <span className="zz">zz</span>
                {past ? (
                  <p>The local {chain?.title ?? ''} node is asleep. Below is <b>epoch {snap!.board.epoch}</b> as it was left by the
                    last run, {ago(snap!.at)}. Running an epoch wakes the node and replaces it with live state.</p>
                ) : (
                  <p>The local {chain?.title ?? ''} node sleeps when idle, so there is nothing live to read yet. Running an epoch wakes it,
                    deploys the contract and fills this board with the real sets and books.</p>
                )}
                {onRun && <button className="btn go sm" onClick={onRun}>{past ? 'Run a new epoch' : 'Run an epoch'}</button>}
              </div>
            ) : (
            <div className="grid3" style={{ gap: 10 }}>
              {[
                ['Contract', short(board?.contract, 10, 6)], ['Owner', short(board?.owner, 8, 6)],
                ['Block', fmt(board?.block)], ['Tempo', board?.tempo ? `${board.tempo} blocks` : '—'],
                ['Min ring', board?.min_ring ?? '—'], ['Emission / epoch', fmt(board?.emission)],
                ...extra.map(([k, v]) => [k, typeof v === 'string' && v.startsWith('0x') ? short(v, 8, 6) : String(v)]),
              ].map(([k, v]) => (
                <div key={String(k)} className="stat" style={{ padding: '10px 12px' }}>
                  <div className="k">{k}</div>
                  <div className="mono" style={{ marginTop: 6, fontSize: 13 }}>{v === '' || v == null ? '—' : v}</div>
                </div>
              ))}
            </div>
            )}
          </div>
        </div>
      </div>

      <div className="grid2">
        <MemberSet title="Miners" list={view?.miners} past={past} />
        <MemberSet title="Validators" list={view?.validators} past={past} />
      </div>

      <div className={`card${past ? ' past' : ''}`}>
        <h3><span className="n">BOOKS</span> {past ? 'Last recorded epoch' : 'Recent epochs'}</h3>
        <p className="lede">Private ballots, public books: every tag&apos;s score sum, Zuma consensus and claim status — no identities.</p>
        {!view?.epochs?.length ? <div className="empty sm">no epochs with submissions yet</div> : (
          <div className="tscroll"><table className="t">
            <thead><tr><th>Epoch</th><th>Tag</th><th className="num">Votes</th><th className="num">Raw</th><th className="num">Consensus</th><th className="num">Rank</th><th className="num">Share</th><th>Status</th></tr></thead>
            <tbody>
              {view.epochs.flatMap((ep) => ep.tags.map((t, i) => {
                const share = ep.rank_total && t.rank !== undefined && view.emission
                  ? Math.floor((view.emission * t.rank) / ep.rank_total) : undefined;
                return (
                  <tr key={ep.epoch + t.tag}>
                    <td className="mono">{i === 0 ? ep.epoch : ''}</td>
                    <td><span className="row" style={{ gap: 8, flexWrap: 'nowrap', whiteSpace: 'nowrap' }}><Ident hex={t.tag} /><span className="mono">{short(t.tag, 8, 5)}</span></span></td>
                    <td className="num">{t.scores}</td>
                    <td className="num">{t.score_sum}</td>
                    <td className="num">{t.consensus ?? '—'}</td>
                    <td className="num">{t.rank ?? '—'}</td>
                    <td className="num">{share !== undefined ? fmt(share) : '—'}</td>
                    <td>
                      {t.claimed ? <span className="pill good">claimed</span>
                        : t.settled ? <span className="pill c">settled</span>
                          : ep.open ? <span className="pill">open</span> : <span className="pill warn">unsettled</span>}
                    </td>
                  </tr>
                );
              }))}
            </tbody>
          </table></div>
        )}
      </div>
    </div>
  );
}
