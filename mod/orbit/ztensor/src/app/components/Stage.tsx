'use client';
/* The ring stage: an anonymous epoch drawn from its event stream.
 *
 * Outer ring = the miner SET, inner ring = the validator SET, orbs = tags.
 * When a miner submits or a validator scores, EVERY node of that set lights
 * and beams at once — that is the whole point: the chain learns "one of
 * these", never which. Pure function of (events[0..cursor], board). */
import { useMemo } from 'react';
import type { Board, Chain, SimEvent } from '@/lib/api';
import { short, fmt } from '@/lib/api';

const W = 560, H = 480, CX = 280, CY = 240;
const RM = 204, RV = 138, RT = 82;

export type TagState = {
  tag: string; label: string; sum: number; n: number;
  consensus?: number; rank?: number; raw?: number; share?: number;
  claimed?: boolean; earned?: number;
};

export type EpochState = {
  miners: string[]; validators: string[]; tags: TagState[];
  contract?: string; epoch?: number; closed: boolean; rejected?: string;
  done?: number; error?: string; last?: SimEvent; idx: number;
};

const LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';

export function derive(events: SimEvent[], cursor: number, board?: Board | null): EpochState {
  const s: EpochState = { miners: [], validators: [], tags: [], closed: false, idx: cursor - 1 };
  const byTag = new Map<string, TagState>();
  const tagOf = (t: string) => {
    let x = byTag.get(t);
    if (!x) { x = { tag: t, label: LETTERS[byTag.size % 26], sum: 0, n: 0 }; byTag.set(t, x); s.tags.push(x); }
    return x;
  };
  const evs = events.slice(0, cursor);
  for (const e of evs) {
    switch (e.kind) {
      case 'deploy': s.contract = e.address; break;
      case 'register':
        (e.role === 'validator' ? s.validators : s.miners).push(e.x || String(s.miners.length + s.validators.length));
        break;
      case 'submit': tagOf(e.tag); s.epoch = e.epoch; break;
      case 'score': { const t = tagOf(e.tag); t.sum += Number(e.score) || 0; t.n += 1; break; }
      case 'close': s.closed = true; break;
      case 'settle': {
        const t = tagOf(e.tag);
        Object.assign(t, { consensus: e.consensus, rank: e.rank, raw: e.raw, share: e.share });
        break;
      }
      case 'claim': { const t = tagOf(e.tag); t.claimed = true; t.earned = e.earned; break; }
      case 'replay': s.rejected = e.result || (e.ok ? 'rejected' : 'ACCEPTED'); break;
      case 'done': s.done = e.seconds; break;
      case 'error': s.error = e.error; break;
    }
    s.last = e;
  }
  // no run streamed yet: draw the chain as it stands (board = public sets)
  if (!evs.length && board) {
    s.miners = (board.miners || []).map((p) => p.x);
    s.validators = (board.validators || []).map((p) => p.x);
    s.contract = board.contract;
    s.epoch = board.epoch;
    const ep = (board.epochs || [])[0];
    for (const t of ep?.tags || []) {
      const x = tagOf(t.tag);
      Object.assign(x, { sum: t.score_sum, n: t.scores, consensus: t.consensus, rank: t.rank, claimed: t.claimed });
    }
  }
  return s;
}

const polar = (r: number, i: number, n: number, off = -Math.PI / 2) => {
  const a = off + (i / Math.max(n, 1)) * Math.PI * 2;
  return [CX + r * Math.cos(a), CY + r * Math.sin(a)] as const;
};

export default function Stage({ chain, state, running }: { chain?: Chain; state: EpochState; running: boolean }) {
  const { miners, validators, tags, last, idx } = state;
  const kind = last?.kind;
  const lastTag = last?.tag ? tags.find((t) => t.tag === last.tag) : undefined;
  const nM = Math.max(miners.length, 1), nV = Math.max(validators.length, 1);
  const tagPos = useMemo(() => {
    const m = new Map<string, readonly [number, number]>();
    tags.forEach((t, i) => m.set(t.tag, tags.length === 1 ? [CX, CY - RT] : polar(RT, i, tags.length, -Math.PI / 2 + 0.4)));
    return m;
  }, [tags]);
  const maxSum = Math.max(100, ...tags.map((t) => t.sum));
  const lit = kind === 'submit' ? 'm' : kind === 'score' ? 'v' : kind === 'claim' ? 'm' : '';
  const payee = [W - 44, 44] as const;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="anonymous epoch ring stage">
      <defs>
        <radialGradient id="core" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="rgb(var(--c-rgb))" stopOpacity="0.55" />
          <stop offset="100%" stopColor="rgb(var(--c-rgb))" stopOpacity="0" />
        </radialGradient>
        <filter id="glow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="4" result="b" />
          <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
      </defs>

      {/* orbits */}
      <g className={running ? 'spinfast' : 'spin'} opacity={0.5}>
        <circle cx={CX} cy={CY} r={RM + 18} fill="none" stroke="rgba(255,255,255,0.06)" strokeDasharray="2 8" />
        <circle cx={CX} cy={CY} r={RV - 18} fill="none" stroke="rgba(255,255,255,0.05)" strokeDasharray="1 6" />
      </g>
      <circle cx={CX} cy={CY} r={RM} fill="none" stroke="rgba(255,255,255,0.08)" />
      <circle cx={CX} cy={CY} r={RV} fill="none" stroke="rgba(255,255,255,0.07)" />
      <circle cx={CX} cy={CY} r={RT} fill="none" stroke="rgba(255,255,255,0.05)" strokeDasharray="3 5" />
      <text x={CX} y={CY - RM - 26} textAnchor="middle" fill="var(--ink3)" fontSize="10" fontWeight={700} letterSpacing="0.16em">
        MINERS · {miners.length}
      </text>
      <text x={CX} y={CY - RV + 28} textAnchor="middle" fill="var(--ink3)" fontSize="9" fontWeight={700} letterSpacing="0.16em">
        VALIDATORS · {validators.length}
      </text>

      {/* whole-set flash: anonymity made visible */}
      {lit === 'm' && <circle key={`fm${idx}`} className="flash" cx={CX} cy={CY} r={RM} fill="none" stroke="rgb(var(--c-rgb))" />}
      {lit === 'v' && <circle key={`fv${idx}`} className="flash" cx={CX} cy={CY} r={RV} fill="none" stroke="rgb(var(--c-rgb))" />}

      {/* beams: every member of the set -> the tag, because any of them could have */}
      {lastTag && (kind === 'submit' || kind === 'score') && (() => {
        const [tx, ty] = tagPos.get(lastTag.tag) || [CX, CY];
        const set = kind === 'submit' ? miners : validators;
        const r = kind === 'submit' ? RM : RV;
        return (
          <g key={`b${idx}`}>
            {set.map((_, i) => {
              const [x, y] = polar(r, i, set.length);
              return <line key={i} className="beam" pathLength={1} x1={x} y1={y} x2={tx} y2={ty}
                stroke="rgb(var(--c-rgb))" strokeOpacity={0.55} strokeWidth={1.2} />;
            })}
          </g>
        );
      })()}

      {/* miner + validator nodes */}
      {miners.map((m, i) => {
        const [x, y] = polar(RM, i, nM);
        return (
          <g key={`m${i}`} className="popin">
            <circle cx={x} cy={y} r={lit === 'm' ? 11 : 8} className="ringnode" fill="var(--bg2)"
              stroke="rgb(var(--c-rgb))" strokeWidth={2} filter={lit === 'm' ? 'url(#glow)' : undefined} />
            <circle cx={x} cy={y} r={3} fill="rgb(var(--c-rgb))" />
          </g>
        );
      })}
      {validators.map((v, i) => {
        const [x, y] = polar(RV, i, nV);
        return (
          <g key={`v${i}`} className="popin">
            <rect x={x - 7} y={y - 7} width={14} height={14} rx={4} fill="var(--bg2)"
              stroke={lit === 'v' ? '#fff' : 'rgba(255,255,255,0.55)'} strokeWidth={1.6}
              filter={lit === 'v' ? 'url(#glow)' : undefined} transform={`rotate(45 ${x} ${y})`} />
          </g>
        );
      })}

      {/* core */}
      <circle cx={CX} cy={CY} r={58} fill="url(#core)" className={running ? 'breathe' : undefined} />
      <circle cx={CX} cy={CY} r={30} fill="var(--bg2)" stroke="rgba(var(--c-rgb),0.7)" strokeWidth={1.5} />
      <text x={CX} y={CY - 2} textAnchor="middle" fill="var(--ink)" fontSize="15" fontWeight={800}>
        {state.epoch ?? '—'}
      </text>
      <text x={CX} y={CY + 12} textAnchor="middle" fill="var(--ink3)" fontSize="7.5" fontWeight={700} letterSpacing="0.14em">
        {state.closed ? 'CLOSED' : 'EPOCH'}
      </text>

      {/* tags */}
      {tags.map((t) => {
        const [x, y] = tagPos.get(t.tag) || [CX, CY];
        const r = 9 + 12 * Math.sqrt(t.sum / maxSum);
        const hot = lastTag?.tag === t.tag;
        return (
          <g key={t.tag} className="popin">
            <circle cx={x} cy={y} r={r + 5} fill="rgba(var(--c-rgb),0.12)" />
            <circle cx={x} cy={y} r={r} fill={t.consensus !== undefined ? 'rgb(var(--c-rgb))' : 'var(--bg2)'}
              stroke="rgb(var(--c-rgb))" strokeWidth={hot ? 3 : 1.6} filter={hot ? 'url(#glow)' : undefined}
              style={{ transition: 'r 400ms ease, fill 400ms' }} />
            <text x={x} y={y + 4} textAnchor="middle" fontSize="11" fontWeight={800}
              fill={t.consensus !== undefined ? '#06070a' : 'var(--ink)'}>{t.label}</text>
            {t.consensus !== undefined && (
              <text x={x} y={y + r + 15} textAnchor="middle" fontSize="9.5" fontWeight={700} fill="var(--ink2)" fontFamily="var(--mono)">
                c={t.consensus}{t.share !== undefined ? ` · ${t.share}` : ''}
              </text>
            )}
          </g>
        );
      })}

      {/* claim: tag -> a fresh address nobody can link back */}
      {kind === 'claim' && lastTag && (() => {
        const [x, y] = tagPos.get(lastTag.tag) || [CX, CY];
        return (
          <g key={`c${idx}`}>
            <line className="beam" pathLength={1} x1={x} y1={y} x2={payee[0]} y2={payee[1]} stroke="var(--good)" strokeWidth={2} />
            <circle cx={payee[0]} cy={payee[1]} r={10} fill="rgba(61,220,151,0.15)" stroke="var(--good)" className="popin" />
            <text x={payee[0]} y={payee[1] + 26} textAnchor="middle" fontSize="11" fontWeight={800} fill="var(--good)">
              +{fmt(last?.earned)}
            </text>
            <text x={payee[0]} y={payee[1] + 39} textAnchor="middle" fontSize="8.5" fill="var(--ink3)" fontFamily="var(--mono)">
              {short(last?.recipient, 5, 3)}
            </text>
          </g>
        );
      })()}

      {!miners.length && !validators.length && (
        <text x={CX} y={CY + 72} textAnchor="middle" fill="var(--ink3)" fontSize="12">
          {chain ? `no subnet on ${chain.title} yet — run an epoch` : 'loading…'}
        </text>
      )}
    </svg>
  );
}
