'use client';
import Link from 'next/link';
import { useEffect, useState } from 'react';
import { BASE, get, post } from '@/lib/api';
import { hasWallet, signIn } from '@/lib/wallet';
import { Badge } from './ui';

/** Every app is a submod of starknet at mods/<name>. Its config.json "app"
 *  block says what kind it is:  page (a page of this app), mod (a sibling
 *  mod's own app) or html (mods/<name>/app/index.html, made with + New app). */
export type App = {
  name: string; mod: string; kind: 'page' | 'mod' | 'html'; title: string; icon: string; tone: string;
  tag: string; pitch: string; fns: string[]; address: string; href: string | null; page: string | null;
  public: boolean; linked: string | null; port: number | null; version?: string; created?: string;
  up: boolean; slow?: boolean; error?: string; facts: [string, any][]; items: any[];
};

const TAG: Record<string, 'accent' | 'ok' | 'warn' | 'dim'> = { money: 'ok', build: 'accent', stake: 'warn', privacy: 'ok', research: 'dim' };
export const tone = (a: { tone?: string }) => ({ ['--tone' as any]: a.tone || 'var(--accent)' });

/** The one link that opens an app, whatever its kind. */
export function OpenApp({ a, className = 'btn sm', children }: { a: App; className?: string; children?: React.ReactNode }) {
  const label = children ?? 'Open ›';
  if (a.kind === 'page' && a.page) return <Link className={className} href={a.page}>{label}</Link>;
  if (a.kind === 'html' && a.href) return <a className={className} href={a.href}>{label}</a>;
  if (a.href && a.public) return <a className={className} href={a.href} target="_blank" rel="noreferrer">{label}</a>;
  return null;
}

export function AppCard({ a, canWrite, onRemoved }: { a: App; canWrite?: boolean; onRemoved?: () => void }) {
  const [arm, setArm] = useState(false);
  const [err, setErr] = useState('');
  const remove = async () => {
    if (!arm) { setArm(true); setTimeout(() => setArm(false), 3000); return; }
    try { await post('apps/remove', { name: a.name }); onRemoved?.(); } catch (e: any) { setErr(e.message); }
  };
  return (
    <article id={a.name} className={`mod app-card ${a.up ? 'up' : 'down'}`} style={tone(a)}>
      <div className="mod-head">
        <span className="mod-icon">{a.icon}</span>
        <div>
          <div className="mod-title">{a.title}</div>
          <div className="mod-sub">{a.mod}{a.version ? ` · v${a.version}` : ''}{a.port ? ` · :${a.port}` : ''}</div>
        </div>
        <Badge tone={a.up ? 'ok' : 'err'}>{a.up ? (a.slow ? 'slow' : 'live') : 'down'}</Badge>
      </div>
      <p className="mod-pitch">{a.pitch}</p>
      {a.facts.length > 0 && (
        <div className="mod-facts">
          {a.facts.map(([l, v]) => (
            <div className="mod-fact" key={l}><div className="l">{l}</div><div className="v" title={String(v)}>{String(v)}</div></div>
          ))}
        </div>
      )}
      {a.items?.length > 0 && (
        <div className="mod-items">
          {a.items.slice(0, 6).map((it: any) => {
            const body = <>
              <span className={`state-dot ${it.state === 'fresh' || it.state === 'reached' ? 'fresh' : 'stale'}`} />
              <span className="n">{it.name}</span><span className="d">{it.desc}</span>
              {it.href && a.public && <span className="dim">open ›</span>}
            </>;
            return it.href && a.public
              ? <a key={it.name} className="mod-item" href={it.href} target="_blank" rel="noreferrer">{body}</a>
              : <div key={it.name} className="mod-item">{body}</div>;
          })}
          {a.items.length > 6 && <div className="dim">+{a.items.length - 6} more</div>}
        </div>
      )}
      {a.error && <div className="mod-err">{a.error}</div>}
      {err && <div className="mod-err">{err}</div>}
      <div className="mod-foot">
        <Badge tone={TAG[a.tag] || 'dim'}>{a.tag}</Badge>
        <OpenApp a={a} />
        {a.kind === 'mod' && a.href && !a.public && <span className="dim" title="Private on this node: only the owner's console can open it">private app · owner only</span>}
        {a.kind === 'mod' && !a.href && <span className="dim">API only</span>}
        {a.fns.length > 0 && <span className="app-fns" title={`GET ${BASE}/_api/apps/${a.name}/<fn>`}>{a.fns.length} fn{a.fns.length > 1 ? 's' : ''}</span>}
        {!a.up && a.linked && <code className="dim">m serve {a.linked}</code>}
        {canWrite && a.created && (
          <button className={`btn sm ghost app-x ${arm ? 'armed' : ''}`} onClick={remove} title="Delete this app (mods/<name>)">
            {arm ? 'sure?' : '×'}
          </button>
        )}
      </div>
    </article>
  );
}

/** The compact row on the home page: one chip per app. */
export function AppStrip({ apps }: { apps: App[] }) {
  return (
    <div className="fam-strip">
      {apps.map(a => {
        const body = <>
          <span className="mod-icon">{a.icon}</span>
          <div><div className="t">{a.title}</div><div className="s">{a.tag}</div></div>
          <span className={`dot ${a.up ? 'ok' : ''}`} style={a.up ? undefined : { background: 'var(--err)' }} />
        </>;
        return a.kind === 'page' && a.page
          ? <Link key={a.name} href={a.page} className="fam-chip" style={tone(a)}>{body}</Link>
          : <Link key={a.name} href={`/apps#${a.name}`} className="fam-chip" style={tone(a)}>{body}</Link>;
      })}
    </div>
  );
}

const slug = (s: string) => s.toLowerCase().trim().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').replace(/^(\d)/, 'a$1').slice(0, 31);

/** + New app: scaffolds mods/<name>/ (config.json, mod.py, app/index.html). */
export function NewApp({ canWrite, onWho, onMade }: { canWrite: boolean; onWho: () => void; onMade: (name: string) => void }) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState('');
  const [pitch, setPitch] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [made, setMade] = useState<App | null>(null);
  const name = slug(title);

  const make = async () => {
    setBusy(true); setErr('');
    try {
      const a = await post<App>('apps/new', { name, title, pitch });
      setMade(a); setTitle(''); setPitch(''); onMade(a.name);
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  const login = async () => {
    setErr('');
    try { await signIn(); onWho(); } catch (e: any) { setErr(e.message || String(e)); }
  };

  if (!open) {
    return (
      <button className="mod app-new" onClick={() => setOpen(true)}>
        <span className="plus">+</span>
        <span className="t">New app</span>
        <span className="s">A page, an API and a card here, in one step.</span>
      </button>
    );
  }
  return (
    <article className="mod app-new open">
      <div className="mod-head">
        <span className="mod-icon">+</span>
        <div><div className="mod-title">New app</div><div className="mod-sub">{name ? `starknet.${name}` : 'starknet.<name>'}</div></div>
        <button className="btn sm ghost" style={{ marginLeft: 'auto' }} onClick={() => { setOpen(false); setMade(null); }}>close</button>
      </div>
      {made ? (
        <div className="stack-sm">
          <p className="mod-pitch"><b>{made.title}</b> is live. Its files are in <code>mods/{made.name}/</code>.</p>
          <div className="mod-foot"><OpenApp a={made}>Open it ›</OpenApp><button className="btn sm ghost" onClick={() => setMade(null)}>Make another</button></div>
        </div>
      ) : canWrite ? (
        <form className="app-form" onSubmit={e => { e.preventDefault(); if (name.length > 1) make(); }}>
          <label>Name<input value={title} onChange={e => setTitle(e.target.value)} placeholder="Gas meter" maxLength={40} autoFocus /></label>
          <label>What it does<input value={pitch} onChange={e => setPitch(e.target.value)} placeholder="Shows what a transfer costs right now." maxLength={200} /></label>
          <button className="btn" disabled={busy || name.length < 2}>{busy ? 'Making…' : 'Make app'}</button>
        </form>
      ) : (
        <div className="stack-sm">
          <p className="mod-pitch">Making an app writes files on this box, so it needs the owner. Sign in with the owner wallet, or run it from a terminal:</p>
          <pre className="app-code">m starknet/new_app name=gas_meter title="Gas meter"</pre>
          {hasWallet() && <div className="mod-foot"><button className="btn sm" onClick={login}>Sign in</button></div>}
        </div>
      )}
      {err && <div className="mod-err">{err}</div>}
    </article>
  );
}

/** How an app is put together, with the exact files and commands. */
export function HowTo({ how }: { how?: { steps: string[]; tree: string[] } }) {
  const [origin, setOrigin] = useState('');
  useEffect(() => setOrigin(window.location.origin), []);
  return (
    <section className="app-how">
      <div>
        <div className="crumb">Make your own</div>
        <h2>How an app works</h2>
        <p className="dim">
          Every app here is a submod of starknet — a folder at <code>mods/&lt;name&gt;</code> that the core names
          {' '}<code>starknet.&lt;name&gt;</code>. Three files, no build step, no restart.
        </p>
        <ol className="app-steps">
          <li><b>Make it.</b> Press <b>+ New app</b>, or <code>m starknet/new_app name=gas_meter</code>.</li>
          <li><b>Add a function</b> to <code>mod.py</code> and list it in <code>config.json</code> <code>&quot;fns&quot;</code>. It is live at once.</li>
          <li><b>Build the page</b> in <code>app/index.html</code>, served at <code>{BASE}/apps/&lt;name&gt;/</code>. Call your functions with <code>api(&apos;fn&apos;, {'{…}'})</code>.</li>
        </ol>
      </div>
      <div className="app-how-code">
        <pre className="app-code">{(how?.tree || []).join('\n')}</pre>
        <pre className="app-code">{`# mods/gas_meter/mod.py
class Mod:
    def facts(self):            # the numbers on its card
        return [['block', chain.block_number()]]

    def head(self, network=None): # GET ${BASE}/_api/apps/gas_meter/head
        return chain.block('latest', network=network)`}</pre>
        <pre className="app-code">{`curl ${origin}${BASE}/_api/apps/stablecoins/facts
m starknet.privacy/activity limit=5`}</pre>
      </div>
    </section>
  );
}

/** Who may write: the studio's owner check (loopback or owner-signed token). */
export function useWho() {
  const [can, setCan] = useState(false);
  const load = () => get<any>('studio/whoami').then(w => setCan(!!w.writable)).catch(() => setCan(false));
  useEffect(() => { load(); }, []);
  return { canWrite: can, reloadWho: load };
}
