'use client';

import { useEffect, useState } from 'react';
import { call } from './lib';
import './privacy.css';

type Who = 'anyone' | 'owner' | 'nobody';
type Fact = { what: string; state: 'yes' | 'no'; plain: string };
type Rule = { do: string; who: Who; why: string };
type CPriv = {
  name: string;
  mod: string;
  title: string;
  description: string | null;
  private: boolean;
  site: string;
  everyone_sees: string[];
  kept_secret: string[];
  summary: string;
};
type Report = {
  module: { summary: string; listed: boolean; linked: boolean; locked: boolean; facts: Fact[] };
  rules: Rule[];
  chain_note: string;
  contracts: CPriv[];
  legend: Record<string, string>;
  hidden_count?: number;
};

const WHO: Record<Who, { label: string; head: string }> = {
  anyone: { label: 'anyone', head: 'Anyone can' },
  owner: { label: 'owner only', head: 'Only the owner can' },
  nobody: { label: 'nobody', head: 'Nobody can' },
};

// PRIVACY view: what is private and what isn't, in words anyone can follow.
// Everything comes from mod.py privacy(); the server enforces the same rules.
export function Privacy({ writable, onOpen, onChanged }: { writable: boolean; onOpen: (name: string) => void; onChanged: () => void }) {
  const [r, setR] = useState<Report | null>(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState<string | null>(null);

  const load = () =>
    call('privacy')
      .then((x) => (x.error ? setErr(x.error) : (setErr(''), setR(x))))
      .catch(() => setErr('API unreachable'));
  useEffect(() => {
    load();
  }, [writable]);

  const flip = async (c: CPriv) => {
    setBusy(c.name);
    try {
      const x = await call('visibility', { name: c.name, private: !c.private });
      if (x.error) setErr(x.error);
      else {
        await load();
        onChanged();
      }
    } finally {
      setBusy(null);
    }
  };

  if (!r) return <div className="priv">{err ? <p className="err">{err}</p> : <p className="muted">Loading…</p>}</div>;
  const m = r.module;
  const tone = m.listed && m.linked ? 'pub' : m.linked ? 'unl' : 'prv';
  const word = tone === 'pub' ? 'public' : tone === 'unl' ? 'unlisted' : 'private';

  return (
    <div className="priv">
      <div className="phead">
        <h2>Who can see what</h2>
        <p className="muted">What is open to everyone, what only the owner can do, and what stays secret.</p>
      </div>
      {err && <p className="err">{err}</p>}

      <section className={`pbanner ${tone}`}>
        <span className={`pchip ${tone}`}>{word}</span>
        <p>{m.summary}</p>
        <ul className="pfacts">
          {m.facts.map((f) => (
            <li key={f.what}>
              <span className={`yn ${f.state}`}>{f.state}</span>
              <b>{f.what}</b>
              <span className="muted">{f.plain}</span>
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h3>Who can do what</h3>
        <div className="prules">
          {(['anyone', 'owner', 'nobody'] as Who[]).map((w) => (
            <div key={w} className={`pcol ${w}`}>
              <div className="pcolhead">
                <span className={`pchip ${w}`}>{WHO[w].label}</span>
                <span className="muted">{r.legend[w]}</span>
              </div>
              <h4>{WHO[w].head}</h4>
              <ul>
                {r.rules
                  .filter((x) => x.who === w)
                  .map((x) => (
                    <li key={x.do}>
                      <b>{x.do}</b>
                      <span className="muted">{x.why}</span>
                    </li>
                  ))}
              </ul>
            </div>
          ))}
        </div>
      </section>

      <section>
        <h3>Two kinds of private</h3>
        <div className="ptwo">
          <div>
            <span className="pchip private">private</span>
            <b>On this site</b>
            <p className="muted">
              A switch on each contract. Private means only the owner can see it here: it is not listed, its code is closed and its app is closed.
              Everyone else is told it does not exist.
            </p>
          </div>
          <div>
            <span className="pchip secret">secret</span>
            <b>On the chain</b>
            <p className="muted">
              A blockchain is a public notebook: whatever a contract stores, everyone can read, whatever the switch says. A contract only keeps
              something secret if it is built to: sealed votes, fingerprints (hashes), proofs, ring signatures.
            </p>
          </div>
        </div>
      </section>

      <section>
        <h3>
          Every contract <span className="muted">({r.contracts.length})</span>
        </h3>
        {!!r.hidden_count && (
          <p className="pnote">
            {r.hidden_count} more {r.hidden_count === 1 ? 'contract is' : 'contracts are'} private. Only the owner can see {r.hidden_count === 1 ? 'it' : 'them'}.
          </p>
        )}
        <div className="pcards">
          {r.contracts.map((c) => (
            <article key={c.name} className={`pcard ${c.private ? 'isprv' : ''}`}>
              <header>
                <button className="ptitle" onClick={() => onOpen(c.name)} title="open in the studio">
                  <b>{c.title}</b>
                  <span className="mmod">{c.mod}</span>
                </button>
                <span className={`pchip ${c.private ? 'private' : 'anyone'}`} title={c.private ? r.legend.private : 'anyone can see it here'}>
                  {c.private ? 'private' : 'public'}
                </span>
              </header>
              <p className="psite">{c.site}</p>
              <p className="psum">{c.summary}</p>
              <div className="plists">
                <div>
                  <h5>Everyone can see on the chain</h5>
                  <ul>
                    {c.everyone_sees.map((x) => (
                      <li key={x}>{x}</li>
                    ))}
                  </ul>
                </div>
                <div>
                  <h5>Kept secret</h5>
                  {c.kept_secret.length ? (
                    <ul className="sec">
                      {c.kept_secret.map((x) => (
                        <li key={x}>{x}</li>
                      ))}
                    </ul>
                  ) : (
                    <p className="muted pnone">Nothing. It is fully transparent.</p>
                  )}
                </div>
              </div>
              {writable && (
                <footer>
                  <button className="btn" disabled={busy === c.name} onClick={() => flip(c)}>
                    {busy === c.name ? 'Saving…' : c.private ? 'Make public' : 'Make private'}
                  </button>
                  <span className="muted">{c.private ? 'Anyone will be able to see its code and use its app.' : 'Only you will be able to see it here.'}</span>
                </footer>
              )}
            </article>
          ))}
        </div>
        {!writable && <p className="muted pfoot">Only the owner can flip a contract between public and private. Sign in with the owner wallet to do it.</p>}
      </section>
    </div>
  );
}
