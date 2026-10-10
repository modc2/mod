'use client';

/**
 * ztensor console. Everything secret happens HERE, in the browser:
 * keygen and LSAG signing run client-side (lib/lsag.mjs) and only the
 * public key, the signature and the choice ever reach the API.
 * Private ballots, public books: the payout table is transparent on purpose.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { keygen, sign, fromHex, toHex, pubOf } from '@/lib/lsag.mjs';

const BASE = process.env.NEXT_PUBLIC_BASE ?? '/ztensor';
const API = `${BASE}/_api`;

const SK_KEY = 'ztensor.secret';

type SetData = { size: number; participants: string[] };
type TallyData = { topic: string; counts: Record<string, number>; total: number };
type PayoutRow = { recipient: string; votes: number; share: number; amount: number };
type TopicRow = { topic: string; ring_size: number; votes: number };
type Phase = 'idle' | 'signing' | 'accepted' | 'rejected';

async function getJSON(path: string) {
  const r = await fetch(`${API}${path}`);
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}

async function postJSON(path: string, body: unknown) {
  const r = await fetch(`${API}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return r.json();
}

const short = (h: string) => (h.length > 20 ? `${h.slice(0, 10)}…${h.slice(-8)}` : h);
const fmtAmount = (n: number) =>
  n.toLocaleString('en-US', { maximumFractionDigits: 2, minimumFractionDigits: 0 });

/** The eligible set drawn as the ring a signature hides inside. While your
 *  browser signs, an arc sweeps the ring; when the vote lands, EVERY node
 *  flashes identically — any of them could have been the signer. */
function RingViz({ members, you, phase }: { members: string[]; you: string | null; phase: Phase }) {
  const S = 220;
  const C = S / 2;
  const R = 86;
  const n = members.length;
  const nodes = members.map((p, i) => {
    const a = (i / n) * Math.PI * 2 - Math.PI / 2;
    return { p, x: C + R * Math.cos(a), y: C + R * Math.sin(a), isYou: p === you };
  });
  const sweepLen = 2 * Math.PI * R;
  return (
    <svg
      className={`ringviz ${phase === 'signing' ? 'signing' : ''} ${phase === 'accepted' ? 'accepted' : ''}`}
      width={S}
      height={S}
      viewBox={`0 0 ${S} ${S}`}
      role="img"
      aria-label={`ring of ${n} member${n === 1 ? '' : 's'}`}
    >
      <defs>
        <linearGradient id="youGrad" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#5b7cfa" />
          <stop offset="100%" stopColor="#8a63f4" />
        </linearGradient>
        <linearGradient id="sweepGrad" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="#5b7cfa" stopOpacity="0" />
          <stop offset="100%" stopColor="#8a63f4" stopOpacity="1" />
        </linearGradient>
      </defs>
      <circle className="track" cx={C} cy={C} r={R} strokeWidth="1.5" strokeDasharray={n === 0 ? '3 6' : undefined} />
      <circle
        className="sweep"
        cx={C}
        cy={C}
        r={R}
        strokeWidth="3"
        strokeDasharray={`${sweepLen * 0.22} ${sweepLen * 0.78}`}
      />
      {nodes.map((nd) => (
        <g key={nd.p}>
          {nd.isYou && <circle className="halo" cx={nd.x} cy={nd.y} r="11" strokeWidth="1.5" />}
          <circle className={`node ${nd.isYou ? 'you' : ''}`} cx={nd.x} cy={nd.y} r={nd.isYou ? 6.5 : 5} />
        </g>
      ))}
      <text className="center-n" x={C} y={C - 2} textAnchor="middle">{n}</text>
      <text className="center-k" x={C} y={C + 18} textAnchor="middle">
        {n === 0 ? 'empty ring' : n === 1 ? 'member' : 'member ring'}
      </text>
    </svg>
  );
}

export default function Console() {
  const [sk, setSk] = useState<string | null>(null); // hex, localStorage only
  const [pk, setPk] = useState<string | null>(null);
  const [eligible, setEligible] = useState<SetData>({ size: 0, participants: [] });
  const [topics, setTopics] = useState<TopicRow[]>([]);
  const [topic, setTopic] = useState('reward-epoch-1');
  const [choice, setChoice] = useState('');
  const [pool, setPool] = useState('1000');
  const [tally, setTally] = useState<TallyData | null>(null);
  const [payout, setPayout] = useState<{ pool: number; total_votes: number; instructions: PayoutRow[] } | null>(null);
  const [voteMsg, setVoteMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [regMsg, setRegMsg] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [phase, setPhase] = useState<Phase>('idle');
  const phaseTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const didAutoload = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const [s, t] = await Promise.all([getJSON('/set'), getJSON('/topics')]);
      setEligible(s);
      setTopics(t.topics ?? []);
      setErr('');
    } catch (e) {
      setErr(String(e));
    }
  }, []);

  useEffect(() => {
    const stored = localStorage.getItem(SK_KEY);
    if (stored) {
      setSk(stored);
      setPk(toHex(pubOf(fromHex(stored))));
    }
    refresh();
  }, [refresh]);

  const loadResults = useCallback(
    async (t: string) => {
      try {
        const [ta, pa] = await Promise.all([
          getJSON(`/tally?topic=${encodeURIComponent(t)}`),
          getJSON(`/payout?topic=${encodeURIComponent(t)}&pool=${encodeURIComponent(pool || '0')}`),
        ]);
        setTally(ta);
        setPayout(pa);
      } catch (e) {
        setErr(String(e));
      }
    },
    [pool]
  );

  // land on live results: first topic with votes loads itself once
  useEffect(() => {
    if (didAutoload.current || topics.length === 0) return;
    didAutoload.current = true;
    setTopic(topics[0].topic);
    loadResults(topics[0].topic);
  }, [topics, loadResults]);

  const settlePhase = (p: Phase) => {
    setPhase(p);
    if (phaseTimer.current) clearTimeout(phaseTimer.current);
    phaseTimer.current = setTimeout(() => setPhase('idle'), 2600);
  };

  const generate = () => {
    const { secret, pub } = keygen();
    const hex = toHex(secret);
    localStorage.setItem(SK_KEY, hex);
    setSk(hex);
    setPk(toHex(pub));
    setRegMsg('');
  };

  const register = async () => {
    if (!pk) return;
    setBusy(true);
    try {
      const r = await postJSON('/register', { pub: pk });
      setRegMsg(`registered — member ${r.index + 1} of ${r.size}`);
      await refresh();
    } catch (e) {
      setRegMsg(String(e));
    } finally {
      setBusy(false);
    }
  };

  const castVote = async () => {
    if (!sk || !topic || !choice) return;
    setBusy(true);
    setVoteMsg(null);
    setPhase('signing');
    try {
      // sign against the topic's ring (frozen at first vote), never /set
      const rd = await getJSON(`/ring?topic=${encodeURIComponent(topic)}`);
      const ring: bigint[] = rd.ring.map(fromHex);
      if (ring.length === 0) throw new Error('eligible set is empty — register first');
      const sig = await sign(fromHex(sk), ring, topic, `${topic}|${choice}`);
      const r = await postJSON('/vote', { topic, choice, sig });
      if (r.accepted) {
        settlePhase('accepted');
        setVoteMsg({ ok: true, text: `vote accepted on "${topic}" — the tally cannot tell it was you` });
      } else {
        settlePhase('rejected');
        setVoteMsg({ ok: false, text: `rejected: ${r.reason}` });
      }
      await Promise.all([refresh(), loadResults(topic)]);
    } catch (e) {
      settlePhase('rejected');
      const m = String(e instanceof Error ? e.message : e);
      setVoteMsg({
        ok: false,
        text: m.includes('not in the ring')
          ? 'your key is not in this topic\'s ring (it was frozen before you registered)'
          : m,
      });
    } finally {
      setBusy(false);
    }
  };

  const counts = tally ? Object.entries(tally.counts).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])) : [];
  const maxCount = counts.length ? counts[0][1] : 0;
  const totalVotes = topics.reduce((a, t) => a + t.votes, 0);

  return (
    <main>
      <div className="wordmark"><span className="dot" />ztensor</div>
      <h1 className="display">
        <span className="ghostline">Private ballots.</span>
        <br />
        <span className="gradline">Public books.</span>
      </h1>
      <p className="lede">
        Anonymous consensus for miner and validator networks. A ring signature proves{' '}
        <i>a member</i> of the eligible set voted — never <i>which one</i>. Payouts stay
        transparent and auditable, on purpose.
      </p>
      <div className="stats">
        <div className="stat"><div className="v">{eligible.size}</div><div className="k">members</div></div>
        <div className="stat"><div className="v">{topics.length}</div><div className="k">topics</div></div>
        <div className="stat"><div className="v">{totalVotes}</div><div className="k">anonymous votes</div></div>
      </div>
      {err && <p className="err">{err}</p>}

      <div className="grid">
        <div className="card">
          <div className="card-head"><span className="step">01</span><h2>Your identity</h2></div>
          {pk ? (
            <>
              <div className="keyplate">
                <span className="mono pk" title={pk}>{short(pk)}</span>
                {eligible.participants.includes(pk) && <span className="tag">in the ring</span>}
              </div>
              <p className="note">
                The secret half lives in this browser only — it is never sent anywhere.
                {eligible.participants.includes(pk) ? '' : ' Register to join the eligible set.'}
              </p>
              <div className="row" style={{ marginTop: 12 }}>
                <button onClick={register} disabled={busy || eligible.participants.includes(pk)}>
                  Register public key
                </button>
                <button className="ghost" onClick={generate} disabled={busy}>
                  New key
                </button>
              </div>
              {regMsg && <p className="note ok">{regMsg}</p>}
            </>
          ) : (
            <>
              <p className="note" style={{ marginTop: 0 }}>
                Generate a keypair in your browser. Only the public half is ever shared;
                voting proves set membership without revealing which member you are.
              </p>
              <button onClick={generate} style={{ marginTop: 8 }}>Generate keypair</button>
            </>
          )}
        </div>

        <div className="card">
          <div className="card-head"><span className="step">02</span><h2>The ring</h2></div>
          <div className="ringwrap">
            <RingViz members={eligible.participants} you={pk} phase={phase} />
            <div className="ringside">
              {eligible.participants.length === 0 && (
                <p className="note" style={{ marginTop: 0 }}>
                  No members yet. Every key registered here becomes a node your
                  signature can hide behind.
                </p>
              )}
              <div className="memberlist">
                {eligible.participants.map((p, i) => (
                  <div className="member" key={p}>
                    <span className="mono" title={p}><span className="idx">{String(i + 1).padStart(2, '0')}</span>{short(p)}</span>
                    {p === pk && <span className="tag">you</span>}
                  </div>
                ))}
              </div>
              <div className="row" style={{ marginTop: 10 }}>
                <button className="ghost" onClick={refresh}>Refresh</button>
              </div>
            </div>
          </div>
        </div>

        <div className="card span2">
          <div className="card-head"><span className="step">03</span><h2>Cast an anonymous vote</h2></div>
          <div className="grid">
            <div>
              <label htmlFor="topic">Topic</label>
              <input id="topic" value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="reward-epoch-1" />
            </div>
            <div>
              <label htmlFor="choice">Choice (e.g. a miner to reward)</label>
              <input id="choice" value={choice} onChange={(e) => setChoice(e.target.value)} placeholder="minerA" />
            </div>
          </div>
          <div className="row">
            <button onClick={castVote} disabled={busy || !sk || !topic || !choice}>
              {busy ? 'Signing in your browser…' : 'Sign with ring signature & cast'}
            </button>
            {!sk && <span className="note" style={{ margin: 0 }}>generate a key first</span>}
          </div>
          {voteMsg && <p className={`note ${voteMsg.ok ? 'ok' : 'err'}`}>{voteMsg.text}</p>}
          {topics.length > 0 && (
            <p className="note" style={{ marginTop: 16 }}>
              Topics with votes:{' '}
              {topics.map((t) => (
                <button
                  key={t.topic}
                  className={`topic-chip ${t.topic === topic ? 'active' : ''}`}
                  onClick={() => {
                    setTopic(t.topic);
                    loadResults(t.topic);
                  }}
                >
                  {t.topic} ({t.votes})
                </button>
              ))}
            </p>
          )}
        </div>

        <div className="card">
          <div className="card-head"><span className="step">04</span><h2>Tally{tally ? ` — ${tally.topic}` : ''}</h2></div>
          {!tally && <p className="note" style={{ marginTop: 0 }}>Pick a topic and load its tally.</p>}
          {tally && tally.total === 0 && <p className="note" style={{ marginTop: 0 }}>No votes on &quot;{tally.topic}&quot; yet.</p>}
          {counts.map(([name, n]) => (
            <div className="bar-row" key={name} title={`${name}: ${n} of ${tally!.total} votes`}>
              <div className="bar-head">
                <span className="name">{name}</span>
                <span className="n">
                  {n} vote{n === 1 ? '' : 's'} · {((n / tally!.total) * 100).toFixed(1)}%
                </span>
              </div>
              <div className="bar-track">
                <div className="bar-fill" style={{ width: `${(n / maxCount) * 100}%` }} />
              </div>
            </div>
          ))}
          <div className="row" style={{ marginTop: 14 }}>
            <button className="ghost" onClick={() => loadResults(topic)} disabled={!topic}>
              Load tally
            </button>
          </div>
        </div>

        <div className="card">
          <div className="card-head"><span className="step">05</span><h2>Payout — public books</h2></div>
          <label htmlFor="pool">Reward pool to split by vote share</label>
          <input id="pool" value={pool} onChange={(e) => setPool(e.target.value)} inputMode="decimal" />
          <div className="row">
            <button className="ghost" onClick={() => loadResults(topic)} disabled={!topic}>
              Compute payout
            </button>
          </div>
          {payout && payout.instructions.length > 0 && (
            <table style={{ marginTop: 14 }}>
              <thead>
                <tr>
                  <th>Recipient</th>
                  <th className="num">Votes</th>
                  <th className="num">Share</th>
                  <th className="num">Amount</th>
                </tr>
              </thead>
              <tbody>
                {payout.instructions.map((row) => (
                  <tr key={row.recipient}>
                    <td>{row.recipient}</td>
                    <td className="num">{row.votes}</td>
                    <td className="num">{(row.share * 100).toFixed(2)}%</td>
                    <td className="num">{fmtAmount(row.amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="note">
            Rewards are deliberately transparent and auditable — anyone can check the
            split against the tally. Only the ballots are anonymous.
          </p>
        </div>
      </div>

      <div className="specs">
        <span className="spec">LSAG · Liu–Wei–Wong</span>
        <span className="spec">RFC 3526 MODP-2048</span>
        <span className="spec">SHA-512 transcript</span>
        <span className="spec">per-topic key image</span>
        <span className="spec">keys never leave this browser</span>
      </div>
      <p className="howit">
        Members register a <b>public</b> key. To vote, your browser produces an LSAG ring
        signature: the service verifies that <i>some</i> member of the eligible set signed,
        but cannot tell which. A per-topic key image blocks double-voting within a topic
        while staying unlinkable across topics. Payouts, by contrast, are fully public:
        the pool splits by vote share into named, auditable instructions.
      </p>
    </main>
  );
}
