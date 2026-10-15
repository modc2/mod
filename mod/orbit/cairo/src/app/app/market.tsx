'use client';

import { useEffect, useMemo, useState } from 'react';
import { BASE, BuildState, Template, call } from './lib';

export type Listing = {
  name: string;
  mod: string;
  title: string;
  description: string | null;
  template: string | null;
  forked_from: string | null;
  state: BuildState;
  lines: number;
  tests: number;
  touched: number;
  reads: string[];
  writes: string[];
  events: string[];
  sierra_felts: number;
  custom_app: boolean;
  deployed: { address: string; by: string; at: number } | null;
  url: string;
};

type Sort = 'recent' | 'live' | 'size' | 'name';
type Filter = 'all' | 'live' | 'built' | 'unbuilt';

function ago(t: number): string {
  if (!t) return '';
  const s = Date.now() / 1000 - t;
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

// Deterministic per-name hue so every contract gets its own glyph colour.
function hue(name: string): number {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return h;
}

// MARKET: the bazaar. Every contract on this box as a listing you can open,
// run in its app, or fork into a new mod; starter templates along the bottom.
export function Market({
  refreshKey,
  writable,
  onOpen,
  onNew,
}: {
  refreshKey: string | null;
  writable: boolean;
  onOpen: (name: string) => void;
  onNew: (opts: { template?: string; fork?: string }) => void;
}) {
  const [items, setItems] = useState<Listing[] | null>(null);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [q, setQ] = useState('');
  const [sort, setSort] = useState<Sort>('recent');
  const [filter, setFilter] = useState<Filter>('all');
  const [err, setErr] = useState('');

  useEffect(() => {
    call('market')
      .then((r) => {
        if (r.error) return setErr(r.error);
        setItems(r.contracts || []);
        setTemplates(r.templates || []);
      })
      .catch(() => setErr('API unreachable'));
  }, [refreshKey]);

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    let xs = (items || []).filter((c) => {
      if (filter === 'live' && !c.deployed) return false;
      if (filter === 'built' && c.state === 'unbuilt') return false;
      if (filter === 'unbuilt' && c.state !== 'unbuilt') return false;
      if (!needle) return true;
      return [c.name, c.description || '', c.template || '', ...c.reads, ...c.writes, ...c.events]
        .join(' ')
        .toLowerCase()
        .includes(needle);
    });
    const by: Record<Sort, (a: Listing, b: Listing) => number> = {
      recent: (a, b) => b.touched - a.touched,
      live: (a, b) => Number(!!b.deployed) - Number(!!a.deployed) || b.touched - a.touched,
      size: (a, b) => b.sierra_felts - a.sierra_felts,
      name: (a, b) => a.name.localeCompare(b.name),
    };
    return [...xs].sort(by[sort]);
  }, [items, q, sort, filter]);

  const all = items || [];

  return (
    <div className="market">
      <div className="mbar">
        <input className="search" placeholder="search name, entrypoint, event…" value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="seg">
          {(['all', 'live', 'built', 'unbuilt'] as Filter[]).map((f) => (
            <button key={f} className={filter === f ? 'on' : ''} onClick={() => setFilter(f)}>
              {f}
            </button>
          ))}
        </div>
        <div className="seg">
          {(['recent', 'live', 'size', 'name'] as Sort[]).map((s) => (
            <button key={s} className={sort === s ? 'on' : ''} onClick={() => setSort(s)} title={`sort by ${s}`}>
              {s}
            </button>
          ))}
        </div>
      </div>

      {err && <p className="err">{err}</p>}

      <div className="cards">
        {items === null && [0, 1, 2].map((i) => <div className="mcard ghost" key={i} />)}
        {shown.map((c) => (
          <article
            className="mcard click"
            key={c.name}
            style={{ ['--h' as any]: hue(c.name) }}
            title={`open ${c.name}: its Cairo source, tests, API and app`}
            onClick={(e) => {
              // buttons and links inside the card do their own thing
              if ((e.target as HTMLElement).closest('a,button')) return;
              onOpen(c.name);
            }}
          >
            <header>
              <div className="glyph">{c.title.slice(0, 1)}</div>
              <div className="who">
                <button className="mname" onClick={() => onOpen(c.name)}>
                  {c.name}
                </button>
                <span className="mmod">{c.mod}</span>
              </div>
              {c.deployed ? (
                <span className="live" title={`${c.deployed.address} · by ${c.deployed.by}`}>
                  <i /> live
                </span>
              ) : (
                <span className={`badge ${c.state}`}>{c.state === 'fresh' ? 'built' : c.state}</span>
              )}
            </header>
            <p className="mdesc">{c.description || 'No description.'}</p>
            <div className="fnchips">
              {c.reads.slice(0, 3).map((f) => (
                <span className="fchip r" key={`r${f}`}>
                  {f}
                </span>
              ))}
              {c.writes.slice(0, 3).map((f) => (
                <span className="fchip w" key={`w${f}`}>
                  {f}
                </span>
              ))}
              {c.events.slice(0, 1).map((f) => (
                <span className="fchip e" key={`e${f}`}>
                  {f}
                </span>
              ))}
              {(() => {
                const more = Math.max(0, c.reads.length - 3) + Math.max(0, c.writes.length - 3) + Math.max(0, c.events.length - 1);
                return more > 0 ? <span className="fchip">+{more} more</span> : null;
              })()}
              {c.state === 'unbuilt' && <span className="muted small">build to see its interface</span>}
            </div>
            <div className="mmeta">
              {c.template && <span>{c.template.replace('_', ' ')}</span>}
              {c.forked_from && <span>fork of {c.forked_from}</span>}
              <span>{c.lines} lines</span>
              {c.sierra_felts > 0 && <span>{c.sierra_felts.toLocaleString()} felts</span>}
              <span>{c.tests} test file{c.tests === 1 ? '' : 's'}</span>
              <span className="grow" />
              <span>{ago(c.touched)}</span>
            </div>
            <footer>
              <button className="btn primary" onClick={() => onOpen(c.name)}>
                Edit
              </button>
              {c.state !== 'unbuilt' && (
                <a className="btn" href={`${BASE}/${c.name}/`} target="_blank" rel="noreferrer">
                  Open app
                </a>
              )}
              <span className="grow" />
              <button
                className="btn ghostbtn"
                disabled={!writable}
                title={writable ? 'copy into a new contract mod' : 'sign in as owner to fork'}
                onClick={() => onNew({ fork: c.name })}
              >
                Fork
              </button>
            </footer>
          </article>
        ))}
        {items !== null && shown.length === 0 && (
          <div className="muted empty">{all.length ? 'Nothing matches.' : 'No contracts yet.'}</div>
        )}
      </div>

      {templates.length > 0 && (
        <section className="starters">
          <h3>Starters</h3>
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
