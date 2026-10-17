'use client';

import { useEffect, useState } from 'react';
import { BASE, Template, call } from './lib';
import type { Listing } from './market';

// HOME: the front door. One screen that says what this is and what to do:
// make a contract, play with it, learn how it works. Your contracts as plain
// cards (one action each), starters underneath. The dense bazaar with search,
// filters and sort lives on in MARKET under the More menu.

function hue(name: string): number {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return h;
}

function ago(t: number): string {
  if (!t) return '';
  const s = Date.now() / 1000 - t;
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

const STEPS = [
  { n: 1, title: 'Start it', say: 'Pick a starter (a coin, a vault, an airdrop…) or describe what you want in one sentence.' },
  { n: 2, title: 'The agent builds it', say: 'It writes the Cairo, compiles it and runs the tests. Red lines show you exactly where anything broke.' },
  { n: 3, title: 'Play with it', say: 'Every built contract gets its own page on a practice chain. Press the buttons, watch what happens. Nothing is real money.' },
];

export function Home({
  refreshKey,
  writable,
  onOpen,
  onNew,
  onLearn,
  onMarket,
}: {
  refreshKey: string | null;
  writable: boolean;
  onOpen: (name: string) => void;
  onNew: (opts: { template?: string; fork?: string }) => void;
  onLearn: () => void;
  onMarket: () => void;
}) {
  const [items, setItems] = useState<Listing[] | null>(null);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [err, setErr] = useState('');

  useEffect(() => {
    call('market')
      .then((r) => {
        if (r.error) return setErr(r.error);
        setItems([...(r.contracts || [])].sort((a: Listing, b: Listing) => b.touched - a.touched));
        setTemplates(r.templates || []);
      })
      .catch(() => setErr('API unreachable'));
  }, [refreshKey]);

  const live = (items || []).filter((c) => c.deployed).length;

  return (
    <div className="home">
      <section className="hhero">
        <div className="hcopy">
          <div className="eyebrow">zero-knowledge contracts, made simple</div>
          <h1>
            Make a contract.
            <br />
            Play with it. Learn how it works.
          </h1>
          <p>
            Say what you want, or pick a starter. The agent writes the Cairo, builds it and tests it, then hands you a page to try it on a
            practice chain. No wallet, no real money, nothing to install.
          </p>
          <div className="hcta">
            <button className="btn primary big" onClick={() => onNew({})}>
              {writable ? 'New contract' : 'Try it: new contract'}
            </button>
            <button className="btn big ghostbtn" onClick={onLearn}>
              Learn the basics
            </button>
          </div>
        </div>
        <ol className="how">
          {STEPS.map((s) => (
            <li key={s.n}>
              <span className="hnum">{s.n}</span>
              <span>
                <b>{s.title}</b>
                <span>{s.say}</span>
              </span>
            </li>
          ))}
        </ol>
      </section>

      {err && <p className="err">{err}</p>}

      <section className="hsec">
        <div className="hsechead">
          <h2>
            Your contracts
            {items && (
              <span className="hcount">
                {items.length}
                {live > 0 && ` · ${live} live`}
              </span>
            )}
          </h2>
          <span className="spacer" />
          <button className="btn ghostbtn sm" onClick={onMarket} title="search, filter and sort every contract">
            Browse all
          </button>
        </div>
        <div className="hgrid">
          {items === null && [0, 1, 2].map((i) => <div className="hcard ghost" key={i} />)}
          {items && items.length === 0 && (
            <button className="hcard hempty" onClick={() => onNew({})}>
              <b>Nothing here yet.</b>
              <span>Make your first contract: it takes about a minute.</span>
            </button>
          )}
          {(items || []).map((c) => {
            const built = c.state !== 'unbuilt';
            return (
              <article
                key={c.name}
                className="hcard"
                style={{ ['--h' as any]: hue(c.name) }}
                onClick={(e) => {
                  if ((e.target as HTMLElement).closest('a,button')) return;
                  onOpen(c.name);
                }}
              >
                <header>
                  <span className="glyph">{c.title.slice(0, 1)}</span>
                  <span className="hwho">
                    <b>{c.name}</b>
                    <small>{c.template ? c.template.replace('_', ' ') : 'contract'}{c.forked_from ? ` · fork of ${c.forked_from}` : ''}</small>
                  </span>
                  {c.deployed ? (
                    <span className="live" title={`${c.deployed.address} on the practice chain`}>
                      <i /> live
                    </span>
                  ) : (
                    <span className={`badge ${c.state}`}>{c.state === 'fresh' ? 'built' : c.state === 'stale' ? 'edited' : 'not built'}</span>
                  )}
                </header>
                <p>{c.description || 'No description yet.'}</p>
                <footer>
                  {built ? (
                    <a className="btn primary" href={c.url || `${BASE}/${c.name}/`} target="_blank" rel="noreferrer">
                      Open app
                    </a>
                  ) : (
                    <button className="btn primary" onClick={() => onOpen(c.name)}>
                      Build it
                    </button>
                  )}
                  <button className="btn ghostbtn" onClick={() => onOpen(c.name)}>
                    Edit
                  </button>
                  <span className="spacer" />
                  <span className="hago">{ago(c.touched)}</span>
                </footer>
              </article>
            );
          })}
        </div>
      </section>

      {templates.length > 0 && (
        <section className="hsec">
          <div className="hsechead">
            <h2>Start from a starter</h2>
          </div>
          <div className="srow">
            {templates.map((t) => (
              <button key={t.id} className="starter" onClick={() => onNew({ template: t.id })}>
                <b>{t.title}</b>
                <span>{t.blurb}</span>
              </button>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
