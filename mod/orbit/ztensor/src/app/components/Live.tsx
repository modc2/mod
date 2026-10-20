'use client';
/* LIVE: run an anonymous epoch on the selected chain's local net (POST
 * simulate, then poll GET sim) or replay the last one (GET last_sim). Both
 * feed the same queue; a timer walks a cursor through it so a live run and a
 * replay animate identically. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Board, Chain, SimEvent } from '@/lib/api';
import { costOf, fmt, get, post, short } from '@/lib/api';
import Stage, { derive } from './Stage';

const PACE: Record<string, number> = {
  devnet: 250, deploying: 350, deploy: 450, register: 60, submit: 450,
  score: 120, close: 400, settle: 350, claim: 550, replay: 500, done: 200,
};

function caption(e: SimEvent | undefined, chain: Chain | undefined, s: ReturnType<typeof derive>) {
  if (!e) return ['Ready', `Run a full anonymous epoch on ${chain?.title ?? 'the chain'}: deploy, register, submit, score, Zuma settle, claim.`];
  const T = s.tags.find((t) => t.tag === e.tag)?.label;
  switch (e.kind) {
    case 'devnet': return ['Local chain up', `${chain?.local ?? 'devnet'} at block ${e.block ?? '—'}.`];
    case 'deploying': return ['Deploying', chain?.deploying ?? 'deploying the verifier'];
    case 'deploy': return ['Verifier live', `Every signature is checked ${chain?.verifier ?? 'on chain'}.`];
    case 'register': return ['Registering the sets', `${s.miners.length} miners · ${s.validators.length} validators — public keys only, no names.`];
    case 'submit': return [`Tag ${T} submitted`, `Signed by one of ${s.miners.length} miners. Nobody, the contract included, knows which. The tag is a key image: one per miner per epoch.`];
    case 'score': return [`Tag ${T} scored ${e.score}`, `By one of ${s.validators.length} validators. Its nullifier stops a second vote on the same tag.`];
    case 'close': return ['Epoch closed', 'No more submissions or scores. Anyone can now settle.'];
    case 'settle': return [`Zuma settled ${T}`, `Consensus ${e.consensus}: the highest score at least half the validators gave. Raw ${e.raw} clipped to ${e.rank} → share ${e.share}.`];
    case 'claim': return [`${T} claimed ${fmt(e.earned)}`, `The miner re-signed in the epoch scope and paid a fresh address${e.expected !== undefined ? ` (Zuma said ${e.expected})` : ''}.`];
    case 'replay': return [String(e.result || '').startsWith('rejected') ? 'Replay rejected' : 'Replay', `Same signature, second claim: ${e.result}.`];
    case 'done': return ['Epoch complete', `${e.seconds}s end to end on ${chain?.title}.`];
    case 'error': return ['Run failed', e.error];
    default: return [e.kind, ''];
  }
}

const KCOL: Record<string, string> = {
  submit: 'rgb(var(--c-rgb))', score: '#fff', settle: 'var(--warn)', claim: 'var(--good)',
  replay: 'var(--bad)', error: 'var(--bad)', deploy: 'rgb(var(--c-rgb))', done: 'var(--good)',
};

function detail(e: SimEvent) {
  switch (e.kind) {
    case 'register': return `${e.role} ${short(e.x, 8, 4)}`;
    case 'submit': return `tag ${short(e.tag, 8, 4)} · ring ${e.ring}${e.ms ? ` · signed ${e.ms}ms` : ''}`;
    case 'score': return `${short(e.tag, 6, 3)} ← ${e.score} · null ${short(e.nullifier, 6, 3)}`;
    case 'settle': return `${short(e.tag, 6, 3)} c=${e.consensus} rank=${e.rank} share=${e.share}`;
    case 'claim': return `${fmt(e.earned)} → ${short(e.recipient, 6, 4)}`;
    case 'deploy': return short(e.address, 10, 6);
    case 'replay': return String(e.result);
    case 'devnet': return `block ${e.block}`;
    case 'close': return `epoch ${e.epoch} @ block ${e.block}`;
    case 'done': return `${e.seconds}s`;
    case 'error': return String(e.error);
    default: return '';
  }
}

export default function Live({ chain, board, local, onDone }: {
  chain?: Chain; board?: Board | null; local: boolean; onDone: () => void;
}) {
  const [events, setEvents] = useState<SimEvent[]>([]);
  const [cursor, setCursor] = useState(0);
  const [running, setRunning] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [miners, setMiners] = useState(4);
  const [validators, setValidators] = useState(4);
  const [err, setErr] = useState('');
  const [lastAt, setLastAt] = useState<number | null>(null);
  const termRef = useRef<HTMLDivElement>(null);
  const pollRef = useRef<number | null>(null);
  const cap = Math.min(chain?.max_ring ?? 8, local ? 32 : 8);

  // load the last run of this chain, parked at its end state
  const loadLast = useCallback(async (play = false) => {
    if (!chain) return;
    try {
      const d = await get('last_sim', { chain: chain.chain });
      const ev: SimEvent[] = d?.events || [];
      setEvents(ev);
      setLastAt(d?.at ?? null);
      setCursor(play ? 0 : ev.length);
    } catch { setEvents([]); setCursor(0); }
  }, [chain]);

  useEffect(() => {
    setErr(''); setRunning(false);
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    loadLast(false);
  }, [chain?.chain, loadLast]);

  useEffect(() => { setMiners((m) => Math.min(m, cap)); setValidators((v) => Math.min(v, cap)); }, [cap]);

  // the playhead
  useEffect(() => {
    if (cursor >= events.length) return;
    const k = events[cursor]?.kind;
    const behind = events.length - cursor;
    const ms = (PACE[k] ?? 400) / speed / (behind > 40 ? 3 : 1);
    const id = window.setTimeout(() => setCursor((c) => c + 1), ms);
    return () => clearTimeout(id);
  }, [cursor, events, speed]);

  useEffect(() => { termRef.current?.scrollTo({ top: termRef.current.scrollHeight, behavior: 'smooth' }); }, [cursor]);
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  const poll = useCallback((since0: number) => {
    let since = since0;
    pollRef.current = window.setInterval(async () => {
      try {
        const s = await get('sim', { since });
        if (s.events?.length) { setEvents((ev) => [...ev, ...s.events]); since = s.next; }
        if (!s.running) {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          setRunning(false);
          if (s.error) setErr(s.error);
          setLastAt(Date.now() / 1000);
          onDone();
        }
      } catch { /* server restart mid-run: keep polling */ }
    }, 700);
  }, [onDone]);

  const run = async () => {
    if (!chain) return;
    setErr('');
    try {
      const r = await post('simulate', { chain: chain.chain, miners, validators });
      if (r.cooldown) { setErr(`cooldown — try again in ${r.cooldown}s (public runs are rate limited)`); return; }
      if (r.error) { setErr(r.error); return; }
      if (!r.started && !r.running) { setErr('could not start'); return; }
      setEvents([]); setCursor(0); setRunning(true); setLastAt(null);
      poll(0);
    } catch (e: any) { setErr(e.message); }
  };

  const state = useMemo(() => derive(events, cursor, board), [events, cursor, board]);
  const [title, sub] = caption(state.last, chain, state);
  const pct = events.length ? (cursor / events.length) * 100 : 0;
  const settled = state.tags.filter((t) => t.consensus !== undefined);
  const totalShare = settled.reduce((a, t) => a + (t.share || 0), 0);
  const fields = chain?.cost_fields || [];
  const spent = useMemo(() => {
    const out: Record<string, number> = {};
    for (const e of events.slice(0, cursor)) {
      const c = costOf(e, fields);
      if (c) out[c.field] = (out[c.field] || 0) + c.v;
    }
    return out;
  }, [events, cursor, fields]);

  return (
    <div className="live fade">
      <div className="card stage hi">
        <div className="bar">
          <div className="field">
            <label>Miners <b>{miners}</b></label>
            <input type="range" min={3} max={cap} value={miners} disabled={running} onChange={(e) => setMiners(+e.target.value)} />
          </div>
          <div className="field">
            <label>Validators <b>{validators}</b></label>
            <input type="range" min={3} max={cap} value={validators} disabled={running} onChange={(e) => setValidators(+e.target.value)} />
          </div>
          <div className="spacer" />
          <div className="seg" title="playback speed">
            {[0.5, 1, 2, 4].map((x) => (
              <button key={x} className={speed === x ? 'on' : ''} onClick={() => setSpeed(x)}>{x}x</button>
            ))}
          </div>
          <button className="btn sm ghost" disabled={running || !events.length} onClick={() => setCursor(0)}>Replay</button>
          <button className="btn go" disabled={running || !chain} onClick={run}>
            {running ? 'Running…' : `Run epoch on ${chain?.title ?? '…'}`}
          </button>
        </div>
        <div className="progress"><i style={{ width: `${pct}%` }} /></div>
        <div className="canvas">
          <Stage chain={chain} state={state} running={running || cursor < events.length} />
          <div className="caption">
            <div className="phase" key={state.idx}>
              <span className="fade" style={{ display: 'block' }}>{title}</span>
              <small>{sub}</small>
            </div>
            <div className="row" style={{ justifyContent: 'flex-end' }}>
              {state.rejected && (
                <span key={`rj${state.idx}`} className={`pill ${state.rejected.startsWith('rejected') ? 'bad' : 'warn'}${state.last?.kind === 'replay' ? ' shake' : ''}`}
                  style={{ fontSize: 11.5, padding: '6px 11px' }}>
                  double claim {state.rejected.startsWith('rejected') ? 'rejected' : state.rejected}
                </span>
              )}
              {Object.entries(spent).map(([k, v]) => <span key={k} className="pill">{k} {fmt(v)}</span>)}
            </div>
          </div>
        </div>
        {err && <div style={{ padding: '0 18px 16px' }}><div className="err">{err}</div></div>}
      </div>

      <div className="stack">
        <div className="card">
          <h3><span className="n">ZUMA</span> Consensus</h3>
          <p className="lede">Each tag&apos;s raw score sum, clipped to its consensus (hatched = clipped away). Emission splits by clipped rank.</p>
          {!state.tags.length && <div className="empty sm">no tags yet — run an epoch</div>}
          {state.tags.map((t) => {
            const raw = t.raw ?? t.sum;
            const rank = t.rank ?? raw;
            const max = Math.max(1, ...state.tags.map((x) => x.raw ?? x.sum));
            return (
              <div className="ybar" key={t.tag}>
                <div className="lab">
                  <span className="sw" style={{ background: 'rgb(var(--c-rgb))' }} />
                  {t.label}<span className="muted">{t.consensus !== undefined ? ` c${t.consensus}` : ` ${t.n}v`}</span>
                </div>
                <div className="track" title={`raw ${raw} · rank ${rank}${t.share !== undefined ? ` · share ${t.share}` : ''}`}>
                  <div className="fill" style={{ width: `${(rank / max) * 100}%` }} />
                  {raw > rank && <div className="clip" style={{ left: `${(rank / max) * 100}%`, width: `${((raw - rank) / max) * 100}%` }} />}
                </div>
                <div className="val">
                  {t.share !== undefined ? fmt(t.share) : fmt(raw)}
                  {t.claimed && <span style={{ color: 'var(--good)', marginLeft: 4 }}>✓</span>}
                </div>
              </div>
            );
          })}
          {settled.length > 0 && (
            <div className="row tiny muted" style={{ marginTop: 10, justifyContent: 'space-between' }}>
              <span>{settled.length}/{state.tags.length} settled · emission paid by share</span>
              <span className="mono">Σ {fmt(totalShare)}</span>
            </div>
          )}
        </div>

        <div className="card logcard" style={{ padding: 0 }}>
          <div className="row" style={{ padding: '14px 18px 8px' }}>
            <h3 style={{ margin: 0 }}><span className="n">LOG</span> Chain events</h3>
            <div className="spacer" />
            <span className="muted tiny mono">
              {cursor}/{events.length}{lastAt ? ` · ${new Date(lastAt * 1000).toLocaleTimeString()}` : ''}
            </span>
          </div>
          <div className="term" ref={termRef}>
            {!events.length && <div className="empty">no run recorded on this chain yet</div>}
            {events.slice(0, cursor).map((e, i) => {
              const c = costOf(e, fields);
              return (
                <div className={`ln${i === cursor - 1 ? ' new' : ''}`} key={i}>
                  <span className="t">{e.t?.toFixed(1)}s</span>
                  <span className="k" style={{ color: KCOL[e.kind] || 'var(--ink2)' }}>{e.kind}</span>
                  <span className="d">{detail(e)}</span>
                  <span className="c">{c ? `${fmt(c.v)} ${c.field}` : ''}</span>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
