'use client';
/* Run any protocol tool directly. Forms come from the same JSON schemas the
 * MCP server publishes, so a new tool in bt/tools.py shows up here for free. */
import { useEffect, useMemo, useState } from 'react';
import { call, getJSON, ToolSchema, WRITE_TOOLS } from '@/lib/api';
import { Section, Spinner } from '@/components/ui';

interface Group { group: string; tools: { name: string; mutates?: boolean }[] }

function coerce(v: string, type: string): unknown {
  if (type === 'integer') return parseInt(v, 10);
  if (type === 'number') return parseFloat(v);
  if (type === 'boolean') return v === 'true';
  if (type === 'array' || type === 'object') { try { return JSON.parse(v); } catch { return v; } }
  return v;
}

export default function Console() {
  const [tools, setTools] = useState<ToolSchema[]>([]);
  const [groups, setGroups] = useState<Group[]>([]);
  const [name, setName] = useState('');
  const [vals, setVals] = useState<Record<string, string>>({});
  const [out, setOut] = useState<string | null>(null);
  const [meta, setMeta] = useState('');
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState('');

  useEffect(() => {
    getJSON<{ tools: ToolSchema[] }>('tools').then(j => {
      setTools(j.tools || []);
      const q = new URLSearchParams(window.location.search).get('tool');
      setName(q && j.tools.some(t => t.name === q) ? q : j.tools[0]?.name || '');
    }).catch(() => {});
    getJSON<{ groups: Group[] }>('docs').then(j => setGroups(j.groups || [])).catch(() => {});
  }, []);

  const tool = useMemo(() => tools.find(t => t.name === name), [tools, name]);
  const pick = (n: string) => {
    setName(n); setVals({}); setOut(null); setMeta('');
    window.history.replaceState(window.history.state, '', `${window.location.pathname}?tool=${n}`);
  };

  const run = async () => {
    if (!tool) return;
    const props = tool.inputSchema.properties || {};
    const args: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(vals)) if (v.trim()) args[k] = coerce(v.trim(), props[k]?.type || 'string');
    if (WRITE_TOOLS.test(name) && !confirm(`Run ${name} ${JSON.stringify(args)}?\n\nThis is a real on-chain / key-material action.`)) return;
    setBusy(true); setOut(''); setMeta('');
    try { const j = await call(name, args); setOut(JSON.stringify(j.result, null, 2)); setMeta(`${j.ms} ms`); }
    catch (e) { setOut((e as Error).message); }
    setBusy(false);
  };

  const q = filter.trim().toLowerCase();
  const listed: Group[] = groups.length ? groups : [{ group: 'Tools', tools: tools.map(t => ({ name: t.name })) }];

  return (
    <Section id="console" title="Console."
      lead="Run any protocol tool directly. Forms are generated from the same schemas the MCP server publishes.">
      <div className="card">
        <div className="tool-grid">
          <div>
            <input value={filter} onChange={e => setFilter(e.target.value)} placeholder="Filter tools" spellCheck={false} />
            <div className="tool-list" style={{ marginTop: 8 }}>
              {listed.map(g => {
                const ts = g.tools.filter(t => !q || t.name.includes(q));
                if (!ts.length) return null;
                return (
                  <div key={g.group} style={{ display: 'contents' }}>
                    <span className="grp">{g.group}</span>
                    {ts.map(t => (
                      <button key={t.name} className={t.name === name ? 'sel' : ''} onClick={() => pick(t.name)}>
                        {t.name}{(t.mutates || WRITE_TOOLS.test(t.name)) && <span className="w">●</span>}
                      </button>
                    ))}
                  </div>
                );
              })}
            </div>
          </div>
          <div>
            {tool ? <>
              <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                <code className="inline" style={{ fontSize: 14 }}>{tool.name}</code>
                {WRITE_TOOLS.test(tool.name) && <span className="tag warn">on-chain write</span>}
              </div>
              <p className="muted" style={{ marginTop: 8 }}>{tool.description}</p>
              <div className="row" style={{ marginTop: 6 }}>
                {Object.entries(tool.inputSchema.properties || {}).map(([k, p]) => (
                  <div key={k}>
                    <label>{k}{(tool.inputSchema.required || []).includes(k) ? ' *' : ''} <span style={{ opacity: .6 }}>({p.type})</span></label>
                    <input value={vals[k] || ''} onChange={e => setVals({ ...vals, [k]: e.target.value })}
                           onKeyDown={e => e.key === 'Enter' && run()}
                           placeholder={p.default !== undefined ? String(p.default) : p.description || ''} title={p.description} />
                  </div>
                ))}
                {!Object.keys(tool.inputSchema.properties || {}).length && <span className="muted">No parameters.</span>}
              </div>
              <div style={{ marginTop: 16, display: 'flex', gap: 12, alignItems: 'center' }}>
                <button className="pill primary" onClick={run} disabled={busy}>Run</button>
                {busy && <Spinner />}
                <span className="muted">{meta}</span>
              </div>
              {out != null && <pre className="out" style={{ marginTop: 16 }}>{out}</pre>}
            </> : <Spinner />}
          </div>
        </div>
      </div>
    </Section>
  );
}
