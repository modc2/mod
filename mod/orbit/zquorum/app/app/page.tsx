'use client';

import { useCallback, useEffect, useState } from 'react';

const BASE = process.env.NEXT_PUBLIC_BASE ?? '/zquorum';
// The app self-calls the API under /zquorum/_api/* — the gateway KEEPS this
// prefix (routed to the app), and server.py maps _api onto the same handlers.
const API = `${BASE}/_api`;

type Tally = {
  quorum: string;
  desc?: string;
  yes: number;
  no: number;
  threshold: number;
  reached: boolean;
  members: number;
  frozen: boolean;
  root: string | null;
  error?: string;
};

async function call(fn: string, body?: Record<string, unknown>): Promise<any> {
  const res = await fetch(`${API}/${fn}`, {
    method: body ? 'POST' : 'GET',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  return res.json();
}

export default function Page() {
  const [quorums, setQuorums] = useState<Tally[]>([]);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const r = await call('list');
      setQuorums(r.quorums || []);
    } catch {
      /* panels fail soft */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return (
    <div className="wrap">
      <header className="top">
        <div className="mark">Q</div>
        <div>
          <h1>zQuorum</h1>
          <p className="sub">anonymous quorums, Cairo-verified</p>
        </div>
      </header>

      <p className="tagline">
        A group reaches a quorum <strong>without revealing who voted</strong>. Members register a
        Pedersen <code>commitment</code> derived from a private secret; a vote reveals only a{' '}
        <code>nullifier</code> — unlinkable to any member, and a repeated nullifier is a rejected
        double vote. Every hash runs in one Cairo program (<code>circuit/</code>).
      </p>

      <Create onDone={refresh} />
      <Commit />

      <section className="card">
        <h2>Quorums</h2>
        {loading ? (
          <div className="empty">Loading…</div>
        ) : quorums.length === 0 ? (
          <div className="empty">No quorums yet. Open one above.</div>
        ) : (
          quorums.map((q) => <Quorum key={q.quorum} q={q} onChange={refresh} />)
        )}
      </section>

      <p className="footer">
        Trust model v0.1: this local anchor is a <strong>trusted tallier</strong> — it sees a secret
        transiently while the Cairo circuit verifies membership and derives the nullifier, then
        persists only <code>(nullifier, choice)</code>. No identities are ever stored. v0.2 replaces
        execution with a STARK proof so no party sees a secret at all. API: <code>{BASE}/api/&lt;fn&gt;</code>.
      </p>
    </div>
  );
}

function Create({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState('');
  const [threshold, setThreshold] = useState('2');
  const [desc, setDesc] = useState('');
  const [out, setOut] = useState<string>('');
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!name.trim()) return;
    setBusy(true);
    setOut('');
    try {
      const r = await call('create', { name, threshold, desc });
      setOut(r.error ? `error: ${r.error}` : `opened "${r.created}" · threshold ${r.threshold}`);
      if (!r.error) {
        setName('');
        setDesc('');
        onDone();
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h2>Open a quorum</h2>
      <div className="row">
        <div className="field" style={{ flex: '2 1 200px' }}>
          <label>Name</label>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="launch" />
        </div>
        <div className="field" style={{ flex: '0 1 120px' }}>
          <label>Threshold (yes votes)</label>
          <input
            type="number"
            min={1}
            value={threshold}
            onChange={(e) => setThreshold(e.target.value)}
          />
        </div>
        <div className="field" style={{ flex: '3 1 240px' }}>
          <label>Description</label>
          <input value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="ship it?" />
        </div>
        <button onClick={submit} disabled={busy || !name.trim()}>
          {busy ? '…' : 'Open'}
        </button>
      </div>
      {out && <div className={`out ${out.startsWith('error') ? 'err' : ''}`}>{out}</div>}
    </section>
  );
}

function Commit() {
  const [secret, setSecret] = useState('');
  const [commitment, setCommitment] = useState('');
  const [busy, setBusy] = useState(false);

  async function derive() {
    if (!secret) return;
    setBusy(true);
    setCommitment('');
    try {
      const r = await call('commit', { secret });
      setCommitment(r.error ? `error: ${r.error}` : r.commitment);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h2>Derive a commitment</h2>
      <div className="row">
        <div className="field" style={{ flex: '2 1 240px' }}>
          <label>Secret (passphrase or felt — stays with you)</label>
          <input
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            placeholder="my passphrase"
          />
        </div>
        <button onClick={derive} disabled={busy || !secret}>
          {busy ? '…' : 'Derive C'}
        </button>
      </div>
      {commitment && <div className={`out ${commitment.startsWith('error') ? 'err' : ''}`}>{commitment}</div>}
      <p className="note">
        Share the <strong>commitment</strong>, never the secret — paste it into a quorum&apos;s Join
        field. (In v0.1 the secret reaches the anchor to run the circuit; v0.2 moves this
        derivation client-side.)
      </p>
    </section>
  );
}

function Quorum({ q, onChange }: { q: Tally; onChange: () => void }) {
  const total = q.yes + q.no;
  const yPct = total ? (q.yes / Math.max(total, q.threshold)) * 100 : 0;
  const nPct = total ? (q.no / Math.max(total, q.threshold)) * 100 : 0;

  const [commitment, setCommitment] = useState('');
  const [secret, setSecret] = useState('');
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);

  async function act(fn: string, body: Record<string, unknown>) {
    setBusy(true);
    setMsg('');
    try {
      const r = await call(fn, body);
      if (r.error) setMsg(`error: ${r.error}`);
      else if (fn === 'join') {
        setMsg(`joined · ${r.members} member(s)`);
        setCommitment('');
      } else if (fn === 'vote') {
        setMsg(`vote accepted · nullifier ${String(r.nullifier).slice(0, 14)}…`);
        setSecret('');
      }
      onChange();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="quorum">
      <div className="head">
        <div>
          <span className="name">{q.quorum}</span>
          {q.desc ? <p className="desc">{q.desc}</p> : null}
        </div>
        <span className={`pill ${q.reached ? 'reached' : 'open'}`}>
          {q.reached ? 'REACHED' : `${q.yes}/${q.threshold}`}
        </span>
      </div>

      <div className="bar">
        <div className="y" style={{ width: `${yPct}%` }} />
        <div className="n" style={{ width: `${nPct}%` }} />
      </div>
      <div className="meta">
        <span>yes {q.yes}</span>
        <span>no {q.no}</span>
        <span>threshold {q.threshold}</span>
        <span>{q.members} member(s)</span>
        <span className={`pill ${q.frozen ? 'frozen' : 'open'}`}>
          {q.frozen ? 'FROZEN' : 'OPEN'}
        </span>
      </div>

      {!q.frozen && (
        <div className="actions">
          <input
            style={{ flex: '2 1 220px' }}
            value={commitment}
            onChange={(e) => setCommitment(e.target.value)}
            placeholder="member commitment (C)"
          />
          <button
            className="ghost"
            disabled={busy || !commitment.trim()}
            onClick={() => act('join', { name: q.quorum, commitment })}
          >
            Join
          </button>
        </div>
      )}

      <div className="actions">
        <input
          style={{ flex: '2 1 220px' }}
          value={secret}
          onChange={(e) => setSecret(e.target.value)}
          placeholder="your secret"
        />
        <button
          className="yes"
          disabled={busy || !secret.trim() || q.members === 0}
          onClick={() => act('vote', { name: q.quorum, secret, choice: 'yes' })}
        >
          Vote yes
        </button>
        <button
          className="no"
          disabled={busy || !secret.trim() || q.members === 0}
          onClick={() => act('vote', { name: q.quorum, secret, choice: 'no' })}
        >
          Vote no
        </button>
      </div>

      {msg && <div className={`out ${msg.startsWith('error') ? 'err' : ''}`}>{msg}</div>}
    </div>
  );
}
