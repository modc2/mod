'use client';

/**
 * ztensor console. Everything secret happens HERE, in the browser:
 * keygen and LSAG signing run client-side (lib/lsag.mjs) and only the
 * public key, the signature and the choice ever reach the API.
 * Private ballots, public books: the payout table is transparent on purpose.
 */

import { useCallback, useEffect, useState } from 'react';
import { keygen, sign, fromHex, toHex, pubOf } from '@/lib/lsag.mjs';

const BASE = process.env.NEXT_PUBLIC_BASE ?? '/ztensor';
const API = `${BASE}/_api`;

const SK_KEY = 'ztensor.secret';

type SetData = { size: number; participants: string[] };
type TallyData = { topic: string; counts: Record<string, number>; total: number };
type PayoutRow = { recipient: string; votes: number; share: number; amount: number };
type TopicRow = { topic: string; ring_size: number; votes: number };

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

  const castVote = async () => {
    if (!sk || !topic || !choice) return;
    setBusy(true);
    setVoteMsg(null);
    try {
      // sign against the topic's ring (frozen at first vote), never /set
      const rd = await getJSON(`/ring?topic=${encodeURIComponent(topic)}`);
      const ring: bigint[] = rd.ring.map(fromHex);
      if (ring.length === 0) throw new Error('eligible set is empty — register first');
      const sig = await sign(fromHex(sk), ring, topic, `${topic}|${choice}`);
      const r = await postJSON('/vote', { topic, choice, sig });
      if (r.accepted) {
        setVoteMsg({ ok: true, text: `vote accepted on "${topic}" — the tally cannot tell it was you` });
      } else {
        setVoteMsg({ ok: false, text: `rejected: ${r.reason}` });
      }
      await Promise.all([refresh(), loadResults(topic)]);
    } catch (e) {
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

  return (
    <main>
      <h1>ztensor</h1>
      <p className="sub">
        Anonymous voting &amp; consensus for miner/validator networks.{' '}
        <span className="tag">private ballots</span>
        <span className="tag">public books</span>
      </p>
      {err && <p className="err">{err}</p>}

      <div className="grid">
        <div className="card">
          <h2>Your key</h2>
          {pk ? (
            <>
              <div className="mono" title={pk}>pub {short(pk)}</div>
              <p className="note">
                Secret key lives in this browser only — it is never sent anywhere.
                {eligible.participants.includes(pk)
                  ? ' This key is in the eligible set.'
                  : ' Not yet in the eligible set.'}
              </p>
              <div className="row" style={{ marginTop: 10 }}>
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
              <p className="note">
                Generate a keypair in your browser. Only the public half is ever shared;
                voting proves set membership without revealing which member you are.
              </p>
              <button onClick={generate}>Generate keypair</button>
            </>
          )}
        </div>

        <div className="card">
          <h2>Eligible set — {eligible.size} member{eligible.size === 1 ? '' : 's'}</h2>
          {eligible.participants.length === 0 && <p className="note">No members yet.</p>}
          <div style={{ maxHeight: 180, overflowY: 'auto' }}>
            {eligible.participants.map((p, i) => (
              <div className="member" key={p}>
                <span className="mono" title={p}>{i + 1}. {short(p)}</span>
                {p === pk && <span className="tag">you</span>}
              </div>
            ))}
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <button className="ghost" onClick={refresh}>Refresh</button>
          </div>
        </div>

        <div className="card span2">
          <h2>Cast an anonymous vote</h2>
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
              {busy ? 'Signing…' : 'Sign with ring signature and submit'}
            </button>
            {!sk && <span className="note">generate a key first</span>}
          </div>
          {voteMsg && <p className={`note ${voteMsg.ok ? 'ok' : 'err'}`}>{voteMsg.text}</p>}
          {topics.length > 0 && (
            <p className="note" style={{ marginTop: 14 }}>
              Topics with votes:{' '}
              {topics.map((t) => (
                <button
                  key={t.topic}
                  className="topic-chip"
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
          <h2>Tally{tally ? ` — ${tally.topic}` : ''}</h2>
          {!tally && <p className="note">Pick a topic and load its tally.</p>}
          {tally && tally.total === 0 && <p className="note">No votes on &quot;{tally.topic}&quot; yet.</p>}
          {counts.map(([name, n]) => (
            <div className="bar-row" key={name} title={`${name}: ${n} of ${tally!.total} votes`}>
              <div className="bar-head">
                <span>{name}</span>
                <span className="n">
                  {n} vote{n === 1 ? '' : 's'} · {((n / tally!.total) * 100).toFixed(1)}%
                </span>
              </div>
              <div className="bar-track">
                <div className="bar-fill" style={{ width: `${(n / maxCount) * 100}%` }} />
              </div>
            </div>
          ))}
          <div className="row" style={{ marginTop: 12 }}>
            <button className="ghost" onClick={() => loadResults(topic)} disabled={!topic}>
              Load tally
            </button>
          </div>
        </div>

        <div className="card">
          <h2>Payout — public books</h2>
          <label htmlFor="pool">Reward pool to split by vote share</label>
          <input id="pool" value={pool} onChange={(e) => setPool(e.target.value)} inputMode="decimal" />
          <div className="row">
            <button className="ghost" onClick={() => loadResults(topic)} disabled={!topic}>
              Compute payout
            </button>
          </div>
          {payout && payout.instructions.length > 0 && (
            <table style={{ marginTop: 12 }}>
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
                    <td className="num">{row.amount}</td>
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

        <div className="card span2">
          <h2>How it works</h2>
          <p style={{ margin: 0 }}>
            Members register a <b>public</b> key. To vote, your browser produces an LSAG ring
            signature (Liu-Wei-Wong over RFC 3526 MODP-2048): the service verifies that{' '}
            <i>some</i> member of the eligible set signed, but cannot tell which. A per-topic
            key image blocks double-voting within a topic while staying unlinkable across
            topics. Payouts, by contrast, are fully public: the pool splits by vote share
            into named, auditable instructions.
          </p>
        </div>
      </div>
    </main>
  );
}
