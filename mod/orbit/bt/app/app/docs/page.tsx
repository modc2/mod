'use client';
/* The complete protocol surface, generated from the live registry. */
import Link from 'next/link';
import { useEffect, useState } from 'react';
import { getJSON } from '@/lib/api';
import { Section, Spinner } from '@/components/ui';

interface Param { name: string; type: string; required?: boolean; description?: string; default?: unknown }
interface Group { group: string; tools: { name: string; description: string; mutates?: boolean; params: Param[] }[] }

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-');

export default function Docs() {
  const [groups, setGroups] = useState<Group[] | null>(null);
  const [err, setErr] = useState('');
  useEffect(() => { getJSON<{ groups: Group[] }>('docs').then(j => setGroups(j.groups)).catch(e => setErr(e.message)); }, []);
  const total = groups?.reduce((n, g) => n + g.tools.length, 0) || 0;
  return (
    <Section id="docs" narrow title="Docs."
      lead="The complete protocol surface — every tool, every parameter. This is generated from the live registry, so it can't drift.">
      <div className="card">
        {err ? <span className="muted">{err}</span> : !groups ? <Spinner /> : <>
          <div className="doc-toc">
            {groups.map(g => <a key={g.group} href={'#' + slug(g.group)}>{g.group} · {g.tools.length}</a>)}
            <span className="muted" style={{ alignSelf: 'center', marginLeft: 6 }}>{total} tools</span>
          </div>
          {groups.map(g => (
            <div className="doc-group" key={g.group} id={slug(g.group)}>
              <h3>{g.group}</h3>
              {g.tools.map(t => (
                <div className="doc-tool" key={t.name} id={t.name}>
                  <span className="tname">{t.name}</span>
                  {t.mutates && <> <span className="tag warn">on-chain write</span></>}
                  {' '}<Link href={`/console?tool=${t.name}`} className="muted" style={{ fontSize: 12 }}>run ▸</Link>
                  <p>{t.description}</p>
                  <div className="doc-params">
                    {t.params.length ? t.params.map(p => (
                      <div key={p.name}><b>{p.name}</b>{p.required ? '*' : ''} <span style={{ opacity: .7 }}>{p.type}</span> — {p.description || ''}
                        {p.default !== undefined ? ` (default ${String(p.default)})` : ''}</div>
                    )) : <span style={{ opacity: .6 }}>no parameters</span>}
                  </div>
                </div>
              ))}
            </div>
          ))}
        </>}
      </div>
    </Section>
  );
}
