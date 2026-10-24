'use client';

import { useEffect, useState } from 'react';
import { Template, call } from './lib';

export type NewSeed = { template?: string; fork?: string };

// One prompt per starter that turns it into something worth deploying — shown
// as chips under the agent box.
const SPARKS = [
  'a sealed-bid auction with commit and reveal deadlines',
  'a tip jar that splits every deposit between three addresses',
  'an on-chain guestbook: anyone can sign once, newest first',
  'a 2-of-3 multisig that approves arbitrary calls',
  'a raffle: buy tickets, owner draws with a committed seed',
];

// NEW: pick where the contract starts (a starter or a fork of one already
// here), name it, and optionally hand the agent a sentence to vibe it from
// there. One button does all three.
export function Create({
  templates,
  contracts,
  seed,
  writable,
  onSignIn,
  start,
  onDone,
}: {
  templates: Template[];
  contracts: string[];
  seed: NewSeed;
  writable: boolean;
  onSignIn: () => void;
  start: (fn: string, body: Record<string, unknown>) => Promise<void>;
  onDone: (name: string) => void;
}) {
  const [from, setFrom] = useState<{ kind: 'template' | 'fork'; id: string }>({ kind: 'template', id: 'counter' });
  const [name, setName] = useState('');
  const [desc, setDesc] = useState('');
  const [prompt, setPrompt] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (seed.fork) setFrom({ kind: 'fork', id: seed.fork });
    else if (seed.template) setFrom({ kind: 'template', id: seed.template });
  }, [seed]);

  const pkg = name
    .trim()
    .replace(/[^0-9a-zA-Z]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .toLowerCase();
  const clash = contracts.includes(pkg);
  const tpl = templates.find((t) => t.id === from.id);

  const make = async () => {
    setBusy(true);
    setErr('');
    try {
      const r =
        from.kind === 'fork'
          ? await call('fork', { src: from.id, name, desc: desc || undefined })
          : await call('new', { name, desc: desc || undefined, template: from.id });
      if (r.error) return setErr(r.error);
      if (prompt.trim()) await start('vibe', { name: r.name, prompt });
      setName('');
      setDesc('');
      setPrompt('');
      onDone(r.name);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="create">
      <section className="hero slim">
        <div className="hero-copy">
          <div className="eyebrow">new contract</div>
          <h2>Start a contract.</h2>
          <p>Pick a starting point, give it a name, and if you like, tell the agent what to turn it into. One button does the rest.</p>
        </div>
      </section>

      <div className="steps">
        <section className="card step1">
          <h2>
            <span className="num">1</span> Start from
          </h2>
          <div className="pickgrid">
            {templates.map((t) => (
              <button
                key={t.id}
                className={`pick ${from.kind === 'template' && from.id === t.id ? 'on' : ''}`}
                onClick={() => setFrom({ kind: 'template', id: t.id })}
              >
                <b>{t.title}</b>
                <span>{t.blurb}</span>
              </button>
            ))}
          </div>
          {contracts.length > 0 && (
            <details className="forkfold" open={from.kind === 'fork'}>
              <summary>
                Or copy one of your contracts
                {from.kind === 'fork' && <b> · {from.id}</b>}
              </summary>
              <div className="forkrow">
                {contracts.map((c) => (
                  <button
                    key={`fork-${c}`}
                    className={`pick fork ${from.kind === 'fork' && from.id === c ? 'on' : ''}`}
                    onClick={() => setFrom({ kind: 'fork', id: c })}
                    title="copy this contract, its tests and its app under a new name"
                  >
                    <b>{c}</b>
                  </button>
                ))}
              </div>
            </details>
          )}
        </section>

        <section className="card">
          <h2>
            <span className="num">2</span> Name it
          </h2>
          <div className="col">
            <input
              className="bigin"
              placeholder="vault"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && pkg && !clash && writable && make()}
            />
            <div className="muted small">
              {pkg ? (
                <>
                  package <code>{pkg}</code> · mod <code>cairo.{pkg}</code> · app <code>/cairo/{pkg}/</code>
                </>
              ) : (
                'Letters, digits and underscores. It becomes the package, the mod and the app path.'
              )}
            </div>
            <input placeholder="what it does (optional)" value={desc} onChange={(e) => setDesc(e.target.value)} />
          </div>
        </section>

        <section className="card">
          <h2>
            <span className="num">3</span> Vibe it <span className="muted small">optional</span>
          </h2>
          <div className="col">
            <textarea
              rows={3}
              placeholder={`Describe what ${pkg || 'it'} should become. The agent edits, builds and tests it.`}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
            />
            <div className="ideas">
              {SPARKS.map((s) => (
                <button key={s} className="idea" onClick={() => setPrompt(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        </section>

        <div className="makebar">
          <div className="summary">
            {from.kind === 'fork' ? `fork of ${from.id}` : `${tpl?.title ?? from.id} starter`}
            {pkg && <> → <b>{pkg}</b></>}
            {prompt.trim() && <> → agent</>}
          </div>
          {writable ? (
            <button className="btn primary big" disabled={!pkg || clash || busy} onClick={make}>
              {busy ? 'Making…' : clash ? `${pkg} exists` : prompt.trim() ? 'Make and vibe' : 'Make'}
            </button>
          ) : (
            <button className="btn primary big" onClick={onSignIn}>
              Sign in to make
            </button>
          )}
        </div>
        {err && <div className="err">{err}</div>}
      </div>
    </div>
  );
}
