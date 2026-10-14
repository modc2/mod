'use client';
import Link from 'next/link';
import { Badge } from './ui';

/** One colour per mod so a card, its chip on the home page and its
 *  buttons read as the same thing. */
export const TONE: Record<string, string> = {
  cairo: '#f97316', starktime: '#facc15', zquorum: '#8b7cf6', zuma: '#22d3ee', zkwasm: '#34d399',
};
const TAG: Record<string, 'accent' | 'ok' | 'warn' | 'dim'> = { build: 'accent', stake: 'warn', privacy: 'ok', research: 'dim' };

const tone = (name: string) => ({ ['--tone' as any]: TONE[name] || 'var(--accent)' });

export function ModCard({ m }: { m: any }) {
  return (
    <article className={`mod ${m.up ? 'up' : 'down'}`} style={tone(m.name)}>
      <div className="mod-head">
        <span className="mod-icon">{m.icon}</span>
        <div>
          <div className="mod-title">{m.title}</div>
          <div className="mod-sub">{m.name}{m.version ? ` · v${m.version}` : ''} · :{m.port}</div>
        </div>
        <Badge tone={m.up ? 'ok' : 'err'}>{m.up ? 'live' : 'down'}</Badge>
      </div>
      <p className="mod-pitch">{m.pitch}</p>
      {m.up && m.facts.length > 0 && (
        <div className="mod-facts">
          {m.facts.map(([l, v]: [string, any]) => (
            <div className="mod-fact" key={l}><div className="l">{l}</div><div className="v" title={String(v)}>{String(v)}</div></div>
          ))}
        </div>
      )}
      {m.items?.length > 0 && (
        <div className="mod-items">
          {m.items.map((it: any) => {
            const body = <>
              <span className={`state-dot ${it.state === 'fresh' || it.state === 'reached' ? 'fresh' : 'stale'}`} />
              <span className="n">{it.name}</span><span className="d">{it.desc}</span>
              {it.href && <span className="dim">open ›</span>}
            </>;
            return it.href && m.public
              ? <a key={it.name} className="mod-item" href={it.href} target="_blank" rel="noreferrer">{body}</a>
              : <div key={it.name} className="mod-item">{body}</div>;
          })}
        </div>
      )}
      {!m.up && m.error && <div className="mod-err">{m.error}</div>}
      <div className="mod-foot">
        <Badge tone={TAG[m.tag] || 'dim'}>{m.tag}</Badge>
        {m.app && m.public && <a className="btn sm" href={m.app} target="_blank" rel="noreferrer">Open app ›</a>}
        {m.app && !m.public && <span className="dim" title="Private on this node: only the owner's console can open it">private app · owner only</span>}
        {!m.app && <span className="dim">API only</span>}
        {!m.up && <code className="dim">m serve {m.name}</code>}
      </div>
    </article>
  );
}

/** The compact row on the home page: one chip per mod, links to /mods. */
export function FamilyStrip({ mods }: { mods: any[] }) {
  return (
    <div className="fam-strip">
      {mods.map(m => (
        <Link key={m.name} href={`/mods#${m.name}`} className="fam-chip" style={tone(m.name)}>
          <span className="mod-icon">{m.icon}</span>
          <div><div className="t">{m.title}</div><div className="s">{m.tag}</div></div>
          <span className={`dot ${m.up ? 'ok' : ''}`} style={m.up ? undefined : { background: 'var(--err)' }} />
        </Link>
      ))}
    </div>
  );
}
