'use client';

import { useEffect, useMemo, useState } from 'react';
import { call } from './lib';
import { Markdown } from './paper';
import './agents.css';

// AGENTS: every agent on this box (mod.py agents()). A mod is an agent when it
// speaks the fleet agent contract — GET /agents + POST /run/stream (SSE). For
// each one this view shows the request it takes (SCHEMA), its skill.md (SKILL)
// and the tools its MCP server lists (TOOLS). Nothing here runs an agent; the
// cairo helper's own orb does that, and `Ask` opens it.

type Level = 'agent' | 'offline' | 'skill';
type Agent = {
  id: string;
  name: string;
  description: string | null;
  writes: boolean;
  access: string;
  icon: string | null;
  brains: string[] | null;
  params: Record<string, string>;
  examples: string[];
  run: string;
  stream: string;
  available: boolean;
  harness: boolean;
  model: string | null;
  provider: string | null;
  tools: string[] | null;
  extra: Record<string, unknown>;
};
type Tool = { name: string; description: string; input: any; access: string | null };
type AgentMod = {
  name: string;
  ref: string;
  title: string;
  description: string | null;
  level: Level;
  here: boolean;
  port: number | null;
  base: string | null;
  base_path: string | null;
  url: string | null;
  private: boolean;
  agents: Agent[];
  card: Record<string, unknown> | null;
  mcp: { url: string; tools: Tool[] | null } | null;
  skill: string | null;
  skill_file: string | null;
  harness: Record<string, unknown> | null;
  declared: string[];
  roster: { value: string; cid: string | null; builtin: boolean; base_url: string | null } | null;
};
type Scan = { count: number; mods: AgentMod[]; scanned: number; levels: Record<Level, number>; roster: boolean; took_ms: number };

const LEVEL: Record<Level, { label: string; tip: string }> = {
  agent: { label: 'live', tip: 'answers GET /agents right now' },
  offline: { label: 'offline', tip: 'declares the agent contract in its config.json but is not up' },
  skill: { label: 'skill', tip: 'no agent contract; it ships a skill.md that tells an agent how to use it' },
};

function hue(name: string): number {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return h;
}

const PAGE = 24; // agents shown per mod before "show all" (arena/judge list hundreds)

// One selectable row: a live agent, or a mod that only has a skill / is offline.
type Pick = { mod: string; agent: string | null };
const key = (p: Pick) => `${p.mod}::${p.agent ?? ''}`;

function exampleBody(m: AgentMod, a: Agent | null): Record<string, unknown> {
  const body: Record<string, unknown> = { query: a?.examples[0] || 'what can you do?' };
  if (a && m.agents.length > 1) body[m.ref === 'orbit/agent' ? 'agent_type' : 'agent'] = a.id;
  for (const k of Object.keys(a?.params || {})) if (k !== 'query') body[k] = `<${k}>`;
  if (m.here && a?.id === 'helper') body.context = { contract: '<name>', view: 'studio' };
  return body;
}

function curlFor(m: AgentMod, a: Agent | null): string {
  const base = m.base || `http://127.0.0.1:${m.port ?? '<port>'}${m.base_path ?? ''}`;
  const path = a?.stream ?? `${m.base_path ?? ''}/run/stream`;
  // declared paths already carry the base_path; bare ones go straight on the host
  const url = path.startsWith('http') ? path : `${base.replace(/\/[^/]*$/, (s) => (path.startsWith(s) ? '' : s))}${path}`;
  return `curl -N ${url} \\\n  -H 'Content-Type: application/json' \\\n  -d '${JSON.stringify(exampleBody(m, a))}'`;
}

// skill.md may open with YAML front matter (name:, description:) — show it as
// a muted line instead of letting it render as a paragraph.
function splitFront(md: string): { front: [string, string][]; body: string } {
  const m = /^---\n([\s\S]*?)\n---\n?/.exec(md);
  if (!m) return { front: [], body: md };
  const front = m[1]
    .split('\n')
    .map((l) => /^([\w-]+):\s*(.*)$/.exec(l))
    .filter((x): x is RegExpExecArray => !!x)
    .map((x) => [x[1], x[2].replace(/^["']|["']$/g, '')] as [string, string]);
  return { front, body: md.slice(m[0].length) };
}

function Copy({ text, label = 'copy' }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      className="btn ghostbtn small"
      onClick={() => {
        navigator.clipboard?.writeText(text);
        setDone(true);
        setTimeout(() => setDone(false), 1200);
      }}
    >
      {done ? 'copied' : label}
    </button>
  );
}

function Schema({ m, a, onAsk }: { m: AgentMod; a: Agent | null; onAsk: () => void }) {
  const body = exampleBody(m, a);
  const base = m.base || (m.port ? `http://127.0.0.1:${m.port}${m.base_path ?? ''}` : null);
  const extra = Object.entries(a?.extra || {}).filter(([, v]) => v !== null && v !== undefined && v !== '');
  return (
    <div className="aschema">
      <div className="arow">
        <h4>Endpoints</h4>
        <table className="atable">
          <tbody>
            <tr><th>list</th><td><code>GET {m.base_path ?? ''}/agents</code></td></tr>
            <tr><th>run</th><td><code>POST {a?.run ?? `${m.base_path ?? ''}/run`}</code> <span className="muted">one JSON answer</span></td></tr>
            <tr><th>stream</th><td><code>POST {a?.stream ?? `${m.base_path ?? ''}/run/stream`}</code> <span className="muted">SSE: step… then done | error</span></td></tr>
            {m.mcp && <tr><th>mcp</th><td><code>POST {m.mcp.url.replace(/^https?:\/\/[^/]+/, '')}</code> <span className="muted">JSON-RPC 2.0, tools/list + tools/call</span></td></tr>}
            {m.card && <tr><th>card</th><td><code>GET {m.base_path ?? ''}/.well-known/agent.json</code></td></tr>}
            {base && <tr><th>host</th><td><code>{base}</code>{m.url && <> · <a href={m.url} target="_blank" rel="noreferrer">{m.url}</a></>}</td></tr>}
          </tbody>
        </table>
      </div>

      <div className="arow">
        <h4>
          Request
          <span className="muted"> · what to POST to the stream endpoint</span>
        </h4>
        <pre className="dcode">{JSON.stringify(body, null, 2)}</pre>
        <table className="atable">
          <tbody>
            <tr><th>query</th><td>what you want, in plain words <b className="req">required</b></td></tr>
            {a && m.agents.length > 1 && (
              <tr><th>{m.ref === 'orbit/agent' ? 'agent_type' : 'agent'}</th><td>which of this mod&apos;s agents: <code>{a.id}</code></td></tr>
            )}
            {Object.entries(a?.params || {}).filter(([k]) => k !== 'query').map(([k, v]) => (
              <tr key={k}><th>{k}</th><td>{v}</td></tr>
            ))}
            {a?.brains && <tr><th>brain</th><td>{a.brains.map((b) => <code key={b}>{b}</code>)}</td></tr>}
            {m.here && <tr><th>context</th><td><code>{'{contract, view, lesson, account}'}</code> — what the console knows, all optional</td></tr>}
            {m.ref === 'orbit/agent' && <tr><th>…</th><td>provider, model, toolbox, prompt, memory, steps, session, key — see the agent docs</td></tr>}
            <tr><th>auth</th><td>{a?.access === 'owner' || a?.writes ? <><code>token</code> header: a mod-protocol token signed by the owner (it writes files or spends)</> : 'none: open to anyone who can reach the host'}</td></tr>
          </tbody>
        </table>
      </div>

      <div className="arow">
        <h4>Events <span className="muted">· the SSE stream</span></h4>
        <table className="atable">
          <tbody>
            <tr><th>step</th><td><code>{'{tool, params|what, result|error}'}</code> — one tool call as it happens</td></tr>
            <tr><th>done</th><td><code>{'{answer|result, task_id?, actions?}'}</code> — the run finished</td></tr>
            <tr><th>error</th><td><code>{'{error, code?}'}</code> — 403 means standing (owner-only), not breakage</td></tr>
          </tbody>
        </table>
      </div>

      {(a?.examples.length ?? 0) > 0 && (
        <div className="arow">
          <h4>Examples <span className="muted">· click to copy</span></h4>
          <div className="achips">
            {a!.examples.map((e) => (
              <button key={e} className="achip" onClick={() => navigator.clipboard?.writeText(e)} title="copy">
                {e}
              </button>
            ))}
          </div>
        </div>
      )}

      {extra.length > 0 && (
        <div className="arow">
          <h4>Also declared</h4>
          <table className="atable">
            <tbody>
              {extra.map(([k, v]) => (
                <tr key={k}><th>{k}</th><td><code>{typeof v === 'string' ? v : JSON.stringify(v)}</code></td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="arow">
        <h4>Try it</h4>
        <pre className="dcode">{curlFor(m, a)}</pre>
        <div className="abtns">
          <Copy text={curlFor(m, a)} label="copy curl" />
          {m.here && (
            <button className="btn primary small" onClick={onAsk}>
              Ask it here
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function Tools({ m }: { m: AgentMod }) {
  if (!m.mcp) return <p className="muted">This mod has no MCP server, so there is no tool list to show. The agent uses its own tools.</p>;
  if (!m.mcp.tools) return <p className="muted">MCP at <code>{m.mcp.url}</code> did not answer <code>tools/list</code> (not up, or not speaking JSON-RPC).</p>;
  return (
    <div className="atools">
      <p className="muted">
        {m.mcp.tools.length} tool{m.mcp.tools.length === 1 ? '' : 's'} at <code>{m.mcp.url}</code>. Add it with <code>claude mcp add --transport http {m.name} {m.mcp.url}</code>.
      </p>
      {m.mcp.tools.map((t) => {
        const props = (t.input && t.input.properties) || {};
        const req = new Set<string>((t.input && t.input.required) || []);
        return (
          <div className="atool" key={t.name}>
            <div className="aline">
              <b>{t.name}</b>
              {t.access && <span className={`fchip ${t.access === 'owner' ? 'w' : 'r'}`}>{t.access}</span>}
            </div>
            {t.description && <p>{t.description}</p>}
            {Object.keys(props).length > 0 && (
              <div className="achips">
                {Object.entries(props).map(([k, v]: [string, any]) => (
                  <span className="aparam" key={k} title={v?.description || ''}>
                    {k}
                    <i>{v?.type || 'any'}</i>
                    {(req.has(k) || v?._req) && <em>*</em>}
                  </span>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

export function Agents({ onAsk }: { onAsk: () => void }) {
  const [scan, setScan] = useState<Scan | null>(null);
  const [level, setLevel] = useState<Level | 'all'>('agent');
  const [q, setQ] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [pick, setPick] = useState<Pick | null>(null);
  const [tab, setTab] = useState<'schema' | 'skill' | 'tools'>('schema');
  const [open, setOpen] = useState<Record<string, boolean>>({});

  const load = (refresh = false) => {
    setBusy(true);
    call('agents', undefined, refresh ? { refresh: '1' } : undefined)
      .then((r) => (r.error ? setErr(r.error) : (setErr(''), setScan(r))))
      .catch(() => setErr('API unreachable'))
      .finally(() => setBusy(false));
  };
  useEffect(() => load(), []);

  const needle = q.trim().toLowerCase();
  const shown = useMemo(() => {
    return (scan?.mods || [])
      .filter((m) => level === 'all' || m.level === level)
      .map((m) => {
        if (!needle) return { m, agents: m.agents };
        const modHit = [m.name, m.ref, m.description || ''].join(' ').toLowerCase().includes(needle);
        const agents = m.agents.filter((a) => [a.id, a.name, a.description || '', ...a.examples].join(' ').toLowerCase().includes(needle));
        return modHit || agents.length ? { m, agents: modHit && !agents.length ? m.agents : agents } : null;
      })
      .filter((x): x is { m: AgentMod; agents: Agent[] } => !!x);
  }, [scan, level, needle]);

  // first row is picked by default, and the pick survives a rescan
  useEffect(() => {
    if (!scan) return;
    const ok = pick && scan.mods.some((m) => m.name === pick.mod && (!pick.agent || m.agents.some((a) => a.id === pick.agent)));
    if (ok) return;
    const first = shown[0];
    if (first) setPick({ mod: first.m.name, agent: first.agents[0]?.id ?? null });
  }, [scan, shown, pick]);

  const cur = pick ? scan?.mods.find((m) => m.name === pick.mod) ?? null : null;
  const curAgent = cur && pick?.agent ? cur.agents.find((a) => a.id === pick.agent) ?? null : null;
  useEffect(() => {
    // land on the tab that has something: offline/skill-only mods open on SKILL
    if (cur && cur.level !== 'agent' && cur.skill) setTab('skill');
    else if (cur && cur.level === 'agent') setTab('schema');
  }, [cur?.name]); // eslint-disable-line react-hooks/exhaustive-deps

  const total = scan?.mods.reduce((n, m) => n + (m.level === 'agent' ? 1 : 0), 0) ?? 0;

  return (
    <div className="agents">
      <div className="fhead">
        <div>
          <h2>Agents on this box</h2>
          <p className="muted">
            Every mod that speaks the agent contract (<code>GET /agents</code> + <code>POST /run/stream</code>), found by asking each running mod.
            Pick one to see the request it takes, its skill and its tools.
            {scan && ` ${scan.count} agents in ${total} mods · ${scan.scanned} mods asked in ${scan.took_ms} ms.`}
          </p>
        </div>
        <button className="btn" disabled={busy} onClick={() => load(true)} title="ask every mod again, skipping the 60s cache">
          {busy ? 'Scanning…' : 'Rescan'}
        </button>
      </div>

      <div className="mbar">
        <input className="search" placeholder="search agents, mods, examples…" value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="seg">
          {(['agent', 'offline', 'skill', 'all'] as const).map((l) => (
            <button key={l} className={level === l ? 'on' : ''} title={l === 'all' ? 'everything found' : LEVEL[l].tip} onClick={() => setLevel(l)}>
              {l === 'all' ? 'all' : LEVEL[l].label}
              {scan && <span className="n">{l === 'all' ? scan.mods.length : scan.levels[l]}</span>}
            </button>
          ))}
        </div>
      </div>

      {err && <p className="err">{err}</p>}

      <div className="agrid">
        <div className="alist">
          {scan === null && !err && [0, 1, 2, 3].map((i) => <div className="arowghost" key={i} />)}
          {shown.map(({ m, agents }) => {
            const all = open[m.name] || !!needle;
            const rows = all ? agents : agents.slice(0, PAGE);
            return (
              <section className="amod" key={m.name} style={{ ['--h' as any]: hue(m.name) }}>
                <button
                  className={`amodhead ${pick?.mod === m.name && !pick.agent ? 'on' : ''}`}
                  onClick={() => setPick({ mod: m.name, agent: null })}
                  title={m.ref}
                >
                  <span className="glyph">{(m.title || m.name).slice(0, 1).toUpperCase()}</span>
                  <span className="amodname">
                    <b>{m.name}</b>
                    <span className={`lvl ${m.level}`} title={LEVEL[m.level].tip}>{LEVEL[m.level].label}</span>
                    {m.here && <span className="tag">here</span>}
                    {m.roster && <span className="tag" title={`mounted in orbit/build as "${m.roster.value}"${m.roster.cid ? ` · ${m.roster.cid}` : ''}`}>build: {m.roster.value}</span>}
                  </span>
                  <span className="amodn">{m.agents.length > 0 ? m.agents.length : m.skill ? 'skill' : '—'}</span>
                </button>
                {rows.map((a) => {
                  const on = pick?.mod === m.name && pick.agent === a.id;
                  return (
                    <button className={`arowbtn ${on ? 'on' : ''} ${a.available ? '' : 'off'}`} key={a.id} onClick={() => setPick({ mod: m.name, agent: a.id })}>
                      <span className="aicon">{a.icon || '◇'}</span>
                      <span className="aname">
                        <b>{a.name}</b>
                        {a.id !== a.name && <i>{a.id}</i>}
                      </span>
                      {a.writes && <span className="fchip w" title="writes files or spends: owner-only">writes</span>}
                      {a.harness && <span className="fchip e" title="runs a real CLI on the host">cli</span>}
                      {!a.available && <span className="fchip" title="not available right now">off</span>}
                    </button>
                  );
                })}
                {agents.length > PAGE && !all && (
                  <button className="amore" onClick={() => setOpen({ ...open, [m.name]: true })}>
                    show all {agents.length}
                  </button>
                )}
              </section>
            );
          })}
          {scan !== null && shown.length === 0 && <div className="muted empty">Nothing matches.</div>}
        </div>

        <div className="adetail">
          {!cur && scan !== null && <p className="muted">Pick an agent on the left.</p>}
          {cur && (
            <>
              <div className="ahead">
                <div className="aglyph" style={{ ['--h' as any]: hue(cur.name) }}>{curAgent?.icon || (cur.title || cur.name).slice(0, 1).toUpperCase()}</div>
                <div className="atitle">
                  <h3>
                    {curAgent ? curAgent.name : cur.title}
                    {curAgent && curAgent.id !== curAgent.name && <code>{curAgent.id}</code>}
                  </h3>
                  <div className="aline">
                    <span className="fchip">{cur.ref}</span>
                    <span className={`lvl ${cur.level}`}>{LEVEL[cur.level].label}</span>
                    {curAgent && <span className={`fchip ${curAgent.access === 'owner' || curAgent.writes ? 'w' : 'r'}`}>{curAgent.access === 'owner' || curAgent.writes ? 'owner' : 'open'}</span>}
                    {curAgent?.model && <span className="fchip e">{curAgent.provider ? `${curAgent.provider}/` : ''}{curAgent.model}</span>}
                    {cur.port && <span className="fchip">:{cur.port}</span>}
                    {cur.private && <span className="tag">private</span>}
                  </div>
                  <p className="adesc">{curAgent?.description || cur.description || 'No description.'}</p>
                </div>
              </div>

              <nav className="atabs">
                {([['schema', 'Schema'], ['skill', 'Skill'], ['tools', 'Tools']] as const).map(([id, label]) => (
                  <button key={id} className={`tab ${tab === id ? 'on' : ''}`} onClick={() => setTab(id)}>
                    {label}
                    {id === 'skill' && cur.skill && <i className="tdot ok" />}
                    {id === 'tools' && cur.mcp?.tools && <span className="n">{cur.mcp.tools.length}</span>}
                  </button>
                ))}
              </nav>

              {tab === 'schema' && (cur.level === 'agent' || cur.declared.length > 0 ? (
                <>
                  {cur.level === 'offline' && <p className="muted">Not up right now, so the shape below comes from its config.json: {cur.declared.map((d) => <code key={d}>{d}</code>)}</p>}
                  <Schema m={cur} a={curAgent ?? cur.agents[0] ?? null} onAsk={onAsk} />
                </>
              ) : (
                <p className="muted">This mod does not speak the agent contract; it only ships a skill. See the Skill tab for how an agent is meant to use it.</p>
              ))}

              {tab === 'skill' && (cur.skill ? (
                <article className="dbody askill">
                  <div className="aline">
                    <span className="fchip">{cur.ref}/{cur.skill_file}</span>
                    <Copy text={cur.skill} label="copy markdown" />
                  </div>
                  {(() => {
                    const { front, body } = splitFront(cur.skill);
                    return (
                      <>
                        {front.length > 0 && (
                          <p className="afront">
                            {front.map(([k, v]) => (
                              <span key={k}><b>{k}</b> {v}</span>
                            ))}
                          </p>
                        )}
                        <Markdown md={body} prefix={`sk-${cur.name}`} />
                      </>
                    );
                  })()}
                </article>
              ) : (
                <p className="muted">
                  No <code>skill.md</code> in {cur.ref}. {curAgent?.description ? 'What it says about itself: ' : ''}
                  {curAgent?.description && <em>{curAgent.description}</em>}
                </p>
              ))}

              {tab === 'tools' && <Tools m={cur} />}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
