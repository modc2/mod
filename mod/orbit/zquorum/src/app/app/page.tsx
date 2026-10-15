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
  chain?: { network: string; address: string; txs: number; last_tx?: string } | null;
  error?: string;
};

type Health = {
  starknet?: { devnet_up: boolean; deployments: Record<string, string>; anchored: number };
};

const short = (h?: string | null) => (h ? `${h.slice(0, 8)}…${h.slice(-4)}` : '');

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
  const [health, setHealth] = useState<Health>({});

  const refresh = useCallback(async () => {
    try {
      const [r, h] = await Promise.all([call('list'), call('health')]);
      setQuorums(r.quorums || []);
      setHealth(h || {});
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
          <p className="sub">anonymous quorums, Cairo-verified, on Starknet</p>
        </div>
        <ChainChip h={health} />
      </header>

      <p className="tagline">
        A group reaches a quorum <strong>without revealing who voted</strong>. Members register a
        Pedersen <code>commitment</code> derived from a private secret; a vote reveals only a{' '}
        <code>nullifier</code> — unlinkable to any member, and a repeated nullifier is a rejected
        double vote. Every hash runs in one Cairo program (<code>circuit/</code>), and the books
        (members, nullifiers, tally, frozen root) can live on the <code>ZQuorum</code> Starknet
        contract.
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
        persists only <code>(nullifier, choice)</code>. No identities are ever stored. On Starknet the
        tallier&apos;s account posts every transaction, so the sender never identifies a voter; the
        contract itself enforces the freeze, one vote per nullifier and the threshold. v0.2 replaces
        execution with a STARK proof so no party sees a secret at all. API: <code>{BASE}/api/&lt;fn&gt;</code>.
      </p>
    </div>
  );
}

function Create({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState('');
  const [threshold, setThreshold] = useState('2');
  const [desc, setDesc] = useState('');
  const [onchain, setOnchain] = useState(true);
  const [out, setOut] = useState<string>('');
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!name.trim()) return;
    setBusy(true);
    setOut('');
    try {
      const r = await call('create', { name, threshold, desc, network: onchain ? 'devnet' : null });
      const chainErr = r.chain?.error;
      setOut(
        r.error
          ? `error: ${r.error}`
          : `opened "${r.created}" · threshold ${r.threshold}` +
              (r.chain?.address ? ` · on Starknet at ${short(r.chain.address)}` : '') +
              (chainErr ? ` · error: Starknet: ${chainErr}` : ''),
      );
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
      <label className="check">
        <input type="checkbox" checked={onchain} onChange={(e) => setOnchain(e.target.checked)} />
        Keep the books on Starknet (local devnet)
      </label>
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

      <ChainRow q={q} onChange={onChange} />

      {msg && <div className={`out ${msg.startsWith('error') ? 'err' : ''}`}>{msg}</div>}
    </div>
  );
}

function ChainChip({ h }: { h: Health }) {
  const sn = h.starknet;
  if (!sn) return null;
  const dep = sn.deployments?.devnet;
  const live = sn.devnet_up && dep;
  return (
    <span className={`pill chip ${live ? 'reached' : 'open'}`} title={dep || 'not deployed yet'}>
      STARKNET {live ? `devnet · ${short(dep)}` : dep ? 'devnet asleep' : 'not deployed'}
    </span>
  );
}

function ChainRow({ q, onChange }: { q: Tally; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');

  async function verify() {
    setBusy(true);
    setMsg('');
    try {
      const r = await call(`onchain?name=${encodeURIComponent(q.quorum)}`);
      if (r.error) setMsg(`error: ${r.error}`);
      else if (!r.onchain) setMsg('error: not found on chain');
      else if (r.match)
        setMsg(`chain agrees · yes ${r.chain.yes} · no ${r.chain.no} · ${r.chain.members} member(s)` +
          (r.chain.root ? ` · root ${short(r.chain.root)}` : ''));
      else setMsg(`error: chain differs on ${r.diff.join(', ')}`);
    } finally {
      setBusy(false);
    }
  }

  async function anchor() {
    setBusy(true);
    setMsg('');
    try {
      const r = await call('anchor', { name: q.quorum, network: 'devnet' });
      setMsg(r.error ? `error: ${r.error}` : `on Starknet · ${r.txs.length} tx(s) replayed`);
      onChange();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="chain">
      {q.chain ? (
        <>
          <span className="pill reached">STARKNET</span>
          <span>{q.chain.network}</span>
          <code title={q.chain.address}>{short(q.chain.address)}</code>
          <span>{q.chain.txs} tx</span>
          <button className="ghost small" disabled={busy} onClick={verify}>
            {busy ? '…' : 'Check on-chain'}
          </button>
        </>
      ) : (
        <>
          <span className="pill frozen">OFF-CHAIN</span>
          <span>local ledger only</span>
          <button className="ghost small" disabled={busy} onClick={anchor}>
            {busy ? '…' : 'Put on Starknet'}
          </button>
        </>
      )}
      {msg && <span className={msg.startsWith('error') ? 'err' : 'ok'}>{msg.replace(/^error: /, '')}</span>}
    </div>
  );
}
