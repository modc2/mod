'use client';

import { useEffect, useState } from 'react';
import { Framework, Template, call } from './lib';

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

const CIRCUIT_SPARKS = [
  'prove I know a password whose hash is public, without revealing it',
  'prove my balance is above a threshold without showing the balance',
  'prove two private numbers sum to a public total',
  'a private vote: prove my choice is 0 or 1 and output a nullifier',
];

// NEW: pick a framework, then where the project starts (a starter or a fork of one already
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
  const [from, setFrom] = useState<{ kind: 'template' | 'fork' | 'blank'; id: string }>({ kind: 'template', id: 'counter' });
  const [fws, setFws] = useState<Framework[]>([]);
  // catalog frameworks with no adapter (risc0, sp1, arkworks, ...): a blank
  // runner-protocol project the agent fills in
  const [more, setMore] = useState<Framework[]>([]);
  const [fw, setFw] = useState('cairo');
  const [name, setName] = useState('');
  const [desc, setDesc] = useState('');
  const [prompt, setPrompt] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    call('frameworks')
      .then((r) => {
        setFws((r.frameworks || []).filter((f: Framework) => f.id !== 'custom'));
        setMore((r.catalog || []).filter((c: Framework) => !c.driven));
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (seed.fork) setFrom({ kind: 'fork', id: seed.fork });
    else if (seed.template) {
      setFrom({ kind: 'template', id: seed.template });
      const t = templates.find((x) => x.id === seed.template);
      if (t?.framework) setFw(t.framework);
    }
  }, [seed, templates]);

  const pickFw = (id: string) => {
    setFw(id);
    const first = templates.find((t) => t.framework === id);
    setFrom(first ? { kind: 'template', id: first.id } : { kind: 'blank', id });
  };
  const shownTemplates = templates.filter((t) => !t.framework || t.framework === fw);
  const fwInfo = fws.find((f) => f.id === fw);

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
          : await call('new', { name, desc: desc || undefined, template: from.kind === 'blank' ? undefined : from.id, framework: fw });
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
          <div className="eyebrow">new</div>
          <h2>Start a ZK project.</h2>
          <p>Pick a framework and a starting point, name it, and optionally tell the agent what to turn it into. It becomes its own mod.</p>
        </div>
      </section>

      <div className="steps">
        <section className="card step1">
          <h2>
            <span className="num">1</span> Start from
          </h2>
          {fws.length > 0 && (
            <div className="fwtabs">
              {fws.map((f) => (
                <button key={f.id} className={`fwtab ${fw === f.id ? 'on' : ''}`} onClick={() => pickFw(f.id)} title={f.blurb}>
                  <b>{f.title}</b>
                  <small>
                    {f.sandbox ? 'contracts + live app' : 'circuits'}
                    {!f.installed && <span className="miss"> · toolchain not installed</span>}
                  </small>
                </button>
              ))}
            </div>
          )}
          {more.length > 0 && (
            <div className="fwmore">
              <span className="muted small">or another framework</span>
              <select value={more.some((m) => m.id === fw) ? fw : ''} onChange={(e) => e.target.value && pickFw(e.target.value)}>
                <option value="">zkVMs and libraries…</option>
                {more.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.title} ({m.id})
                  </option>
                ))}
              </select>
            </div>
          )}
          {from.kind === 'blank' && (
            <p className="muted small">
              {fw} has no starter here. You get an empty project wired to its toolchain through the runner protocol. Describe what it should prove in
              step 3 and the agent writes the code, the runner and the test cases.
            </p>
          )}
          {fwInfo && fwInfo.installed === false && (
            <p className="muted small">
              {fwInfo.title} isn't installed on this box, so build and test will fail until it is: <code>{fwInfo.install}</code>
            </p>
          )}
          <div className="pickgrid">
            {shownTemplates.map((t) => (
              <button
                key={t.id}
                className={`pick ${from.kind === 'template' && from.id === t.id ? 'on' : ''}`}
                onClick={() => setFrom({ kind: 'template', id: t.id })}
              >
                <span className="ptag">starter</span>
                <b>{t.title}</b>
                <span>{t.blurb}</span>
              </button>
            ))}
            {contracts.map((c) => (
              <button
                key={`fork-${c}`}
                className={`pick fork ${from.kind === 'fork' && from.id === c ? 'on' : ''}`}
                onClick={() => setFrom({ kind: 'fork', id: c })}
              >
                <span className="ptag">fork</span>
                <b>{c}</b>
                <span>Copy this project, its tests and its app, renamed.</span>
              </button>
            ))}
          </div>
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
                  package <code>{pkg}</code> · mod <code>zkworld.{pkg}</code> · app <code>/zkworld/{pkg}/</code>
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
              {(fw === 'cairo' ? SPARKS : CIRCUIT_SPARKS).map((s) => (
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
