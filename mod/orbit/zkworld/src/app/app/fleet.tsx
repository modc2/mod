'use client';

import { useEffect, useMemo, useState } from 'react';
import { call } from './lib';
import './fleet.css';

type Level = 'contract' | 'project' | 'mentions';
type Project = { path: string; framework: string; package: string | null; version: string | null; contract: boolean; tested: boolean };
type FleetMod = {
  name: string;
  path: string;
  title: string;
  description: string | null;
  level: Level;
  here: boolean;
  frameworks: string[];
  projects: Project[];
  files: Record<string, number>;
  tested: boolean;
  nested_projects: number;
  keywords: string[];
  config_keys: string[];
  port: number | null;
  url: string | null;
  private: boolean;
};
type Scan = { count: number; scanned: number; levels: Record<Level, number>; frameworks: Record<string, number>; mods: FleetMod[]; took_ms: number };

const MANIFEST: Record<string, string> = { cairo: 'Scarb.toml', noir: 'Nargo.toml', circom: '*.circom' };
const EXT: Record<string, string> = { cairo: '.cairo', circom: '.circom', noir: '.nr' };

const LEVEL: Record<Level, { label: string; tip: string }> = {
  contract: { label: 'contract', tip: 'has a Scarb project that builds a Starknet contract' },
  project: { label: 'project', tip: 'has a ZK project: Scarb.toml (Cairo), Nargo.toml (Noir) or *.circom (Circom)' },
  mentions: { label: 'mentions', tip: 'no ZK project; its config.json talks about a ZK toolchain' },
};

function hue(name: string): number {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return h;
}

// FLEET: every mod on this box with a ZK setup in any framework, inferred
// from its config.json and any project inside it (mod.py fleet()).
export function Fleet({ onOpen }: { onOpen: (name: string) => void }) {
  const [scan, setScan] = useState<Scan | null>(null);
  const [level, setLevel] = useState<Level | 'all'>('all');
  const [fw, setFw] = useState<string>('all');
  const [q, setQ] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const load = (refresh = false) => {
    setBusy(true);
    call('fleet', undefined, refresh ? { refresh: '1' } : undefined)
      .then((r) => (r.error ? setErr(r.error) : (setErr(''), setScan(r))))
      .catch(() => setErr('API unreachable'))
      .finally(() => setBusy(false));
  };
  useEffect(() => load(), []);

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (scan?.mods || []).filter((m) => {
      if (level !== 'all' && m.level !== level) return false;
      if (fw !== 'all' && !m.frameworks.includes(fw)) return false;
      if (!needle) return true;
      return [m.name, m.path, m.description || '', ...m.projects.map((p) => p.package || '')].join(' ').toLowerCase().includes(needle);
    });
  }, [scan, level, fw, q]);

  return (
    <div className="fleet">
      <div className="fhead">
        <div>
          <h2>Zero-knowledge across the fleet</h2>
          <p className="muted">
            Every mod on this box with a ZK setup in any framework, read from its <code>config.json</code> and any <code>Scarb.toml</code>,{' '}
            <code>Nargo.toml</code> or <code>.circom</code> inside it.
            {scan && ` ${scan.count} of ${scan.scanned} mods · scanned in ${scan.took_ms} ms.`}
          </p>
        </div>
        <button className="btn" disabled={busy} onClick={() => load(true)} title="scan again, skipping the 60s cache">
          {busy ? 'Scanning…' : 'Rescan'}
        </button>
      </div>

      <div className="mbar">
        <input className="search" placeholder="search mod, path, package…" value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="seg">
          {(['all', 'contract', 'project', 'mentions'] as const).map((l) => (
            <button key={l} className={level === l ? 'on' : ''} title={l === 'all' ? '' : LEVEL[l].tip} onClick={() => setLevel(l)}>
              {l}
              {scan && <span className="n">{l === 'all' ? scan.count : scan.levels[l]}</span>}
            </button>
          ))}
        </div>
        {scan && (
          <div className="seg">
            {['all', ...Object.keys(scan.frameworks)].map((f) => (
              <button key={f} className={fw === f ? 'on' : ''} onClick={() => setFw(f)}>
                {f}
                <span className="n">{f === 'all' ? scan.count : scan.frameworks[f]}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {err && <p className="err">{err}</p>}

      <div className="frows">
        {scan === null && !err && [0, 1, 2].map((i) => <div className="frow ghost" key={i} />)}
        {shown.map((m) => {
          const local = m.here && m.name.startsWith('zkworld.') ? m.name.slice('zkworld.'.length) : null;
          return (
            <article className={`frow ${m.level}`} key={m.path} style={{ ['--h' as any]: hue(m.name) }}>
              <div className="glyph">{(m.title || m.name).slice(0, 1).toUpperCase()}</div>
              <div className="fmain">
                <div className="fline">
                  <b>{m.name}</b>
                  <span className={`lvl ${m.level}`} title={LEVEL[m.level].tip}>
                    {LEVEL[m.level].label}
                  </span>
                  {m.here && <span className="tag">here</span>}
                  {m.private && <span className="tag">private</span>}
                  <span className="fpath">{m.path}</span>
                </div>
                {m.description && <p className="fdesc">{m.description}</p>}
                <div className="fnchips">
                  {m.projects.map((p) => (
                    <span className="proj" key={`${p.framework}:${p.path}`} title={`${m.path}/${p.path}/${MANIFEST[p.framework] || ''}`}>
                      <b>{p.package || p.path}</b>
                      <i>{p.framework}{p.version ? ` ${p.version}` : ''}</i>
                      {p.path !== '.' && <i>{p.path}</i>}
                      {p.contract && <i className="c">starknet</i>}
                      {p.tested ? <i className="t">tests</i> : <i className="u">no tests</i>}
                    </span>
                  ))}
                  {m.nested_projects > 0 && <span className="fchip e">{m.nested_projects} project{m.nested_projects === 1 ? '' : 's'} in nested mods</span>}
                  {Object.entries(m.files || {}).map(([f, n]) => (
                    <span className="fchip r" key={f}>
                      {n} {EXT[f] || f}
                    </span>
                  ))}
                  {m.projects.length === 0 &&
                    m.keywords.map((k) => (
                      <span className="fchip" key={k}>
                        {k}
                      </span>
                    ))}
                </div>
              </div>
              <div className="fact">
                {local && (
                  <button className="btn primary" onClick={() => onOpen(local)}>
                    Open
                  </button>
                )}
                {m.url && !local && (
                  <a className="btn" href={m.url} target="_blank" rel="noreferrer">
                    App
                  </a>
                )}
              </div>
            </article>
          );
        })}
        {scan !== null && shown.length === 0 && <div className="muted empty">Nothing matches.</div>}
      </div>
    </div>
  );
}
