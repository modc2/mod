'use client';
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import Editor, { EditorHandle } from '@/components/Editor';
import { Badge, ErrorBox, Loading } from '@/components/ui';
import { get, post } from '@/lib/api';
import { useParam } from '@/lib/hooks';
import { hasWallet, signIn, signOut } from '@/lib/wallet';
import { short } from '@/lib/format';

// ---- shapes from /studio/* (the cairo mod, proxied) -------------------------

type Who = { writable: boolean; via: string | null; address: string | null; owners: string[]; why?: string };
type Contract = { name: string; state: 'fresh' | 'stale' | 'unbuilt'; template: string | null; description: string | null };
type Template = { id: string; title: string; blurb: string };
type Diag = { level: string; message: string; path: string; line: number; col: number };
type TestRow = { status: string; name: string; gas: number | null; reason?: string };
type RunResult = {
  ok?: boolean; log?: string; seconds?: number; error?: string; stage?: string;
  diagnostics?: Diag[]; tests?: TestRow[]; totals?: { passed: number; failed: number } | null;
};
type AbiFn = { name: string; interface: string | null; mutability: string; inputs: { name: string; type: string }[]; outputs: string[] };
type Abi = { contracts: { contract: string; constructor: { name: string; type: string }[] | null; functions: AbiFn[];
  events: { name: string; fields: { name: string; type: string; key: boolean }[] }[]; sierra_felts: number; casm_felts: number | null }[]; error?: string };
type Job = { id: string; fn: string; state: 'queued' | 'running' | 'done' | 'error'; started: number | null; created: number; result: any; error: string | null };
type Msg = { role: 'you' | 'agent'; text: string; at: number; ok?: boolean; cost?: number; build?: RunResult; test?: RunResult };

const SUGGEST = [
  'Explain what this contract does, function by function',
  'Add an owner who alone can call the write functions',
  'Write tests for every edge case and make them pass',
  'Add a pause switch the owner can flip',
];

const EDITABLE = /\.(cairo|toml|md)$/;
const msgKey = (c: string) => `starknet.studio.chat.${c}`;
const loadMsgs = (c: string): Msg[] => { try { return JSON.parse(localStorage.getItem(msgKey(c)) || '[]'); } catch { return []; } };
const saveMsgs = (c: string, m: Msg[]) => localStorage.setItem(msgKey(c), JSON.stringify(m.slice(-60)));

async function waitJob(id: string, tick?: (j: Job) => void): Promise<Job> {
  for (;;) {
    const j = await get<Job>(`studio/job?id=${id}`);
    tick?.(j);
    if (j.state === 'done' || j.state === 'error') return j;
    await new Promise(r => setTimeout(r, 1500));
  }
}

const ver = (t: any) => (String(t?.version || '').match(/\d+\.\d+\.\d+/) || ['?'])[0];

function since(t: number | null) {
  if (!t) return '';
  const s = Math.max(0, Math.floor(Date.now() / 1000 - t));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

export default function BuildPage() {
  return <Suspense fallback={<Loading />}><Studio /></Suspense>;
}

function Studio() {
  const router = useRouter();
  const sel = useParam('c');
  const [who, setWho] = useState<Who | null>(null);
  const [status, setStatus] = useState<any>(null);
  const [list, setList] = useState<Contract[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const loadWho = useCallback(() => get<Who>('studio/whoami').then(setWho).catch(() => setWho(null)), []);
  const loadList = useCallback(() => get<{ contracts: Contract[] }>('studio/contracts')
    .then(r => { setList(r.contracts); setErr(null); }).catch(e => setErr(e.message)), []);

  useEffect(() => {
    loadWho();
    get('studio/status').then(setStatus).catch(() => {});
    loadList();
  }, [loadWho, loadList]);

  // open the first contract when none is picked
  useEffect(() => {
    if (!sel && list?.length) router.replace(`/studio?c=${list[0].name}`);
  }, [sel, list, router]);

  const pick = (name: string) => router.push(`/studio?c=${name}`);
  const tc = status?.toolchain;

  return (
    <div className="studio-page">
      <div className="page-head studio-head">
        <div>
          <div className="crumb">Build</div>
          <h1>Contract studio</h1>
          <div className="studio-sub">
            Write a Cairo contract, compile it, test it — or tell the agent what you want and let it do the typing.
          </div>
        </div>
      </div>

      {err && <ErrorBox error={err} onRetry={loadList} />}

      <div className="studio">
        <aside className="studio-rail">
          <div className="rail-head">
            <span>Contracts</span>
            {who?.writable && <button className="btn sm" onClick={() => setCreating(true)}>+ New</button>}
          </div>
          {!list && !err && <Loading />}
          {list?.length === 0 && <div className="rail-empty">No contracts yet.</div>}
          <div className="rail-list">
            {list?.map(c => (
              <button key={c.name} className={`rail-item ${c.name === sel ? 'on' : ''}`} onClick={() => pick(c.name)}>
                <span className={`state-dot ${c.state}`} title={c.state} />
                <span className="rail-name">{c.name}</span>
                {c.template && <span className="rail-tpl">{c.template.replace('_', ' ')}</span>}
              </button>
            ))}
          </div>
          <div className="rail-foot">
            {who && !who.writable && (
              <div className="rail-note">Browsing read-only. Sign in with the owner wallet to create, edit and build.</div>
            )}
            <Account who={who} onChange={loadWho} />
            {status && (status.ok
              ? <div className="rail-tool" title={status.cairo}>
                  <span className="dot ok" />scarb {ver(tc?.scarb)} · snforge {ver(tc?.snforge)}{tc?.claude?.creds ? ' · agent ready' : ''}
                </div>
              : <div className="rail-tool bad" title={status.error}><span className="dot" />toolchain offline</div>)}
          </div>
        </aside>

        {sel ? <Workspace key={sel} name={sel} who={who} onChanged={loadList} />
          : <div className="card studio-blank">{list?.length === 0 ? 'Create your first contract to start.' : <Loading />}</div>}
      </div>

      {creating && <NewContract onClose={() => setCreating(false)} onCreated={n => { setCreating(false); loadList(); pick(n); }} />}
    </div>
  );
}

// ---- account chip -------------------------------------------------------------

function Account({ who, onChange }: { who: Who | null; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (!who) return null;
  if (who.writable) {
    return (
      <div className="acct on" title={who.via === 'local' ? 'Running on this machine' : who.address || ''}>
        <span className="dot ok" />
        <span className="acct-id">{who.via === 'local' ? 'local owner' : short(who.address || '', 4)}</span>
        {who.via === 'token' && <button className="acct-x" onClick={() => { signOut(); onChange(); }}>sign out</button>}
      </div>
    );
  }
  return (
    <div className="acct-wrap">
      <button className="btn" disabled={busy || !hasWallet()} title={hasWallet() ? '' : 'Install a browser wallet to sign in'}
        onClick={async () => {
          setBusy(true); setErr(null);
          try { await signIn(); onChange(); } catch (e: any) { setErr(e.message || String(e)); }
          setBusy(false);
        }}>{busy ? 'Waiting for wallet…' : 'Sign in to build'}</button>
      {(err || (who.address && who.why)) && <span className="acct-err">{err || who.why}</span>}
    </div>
  );
}

// ---- new contract dialog --------------------------------------------------

function NewContract({ onClose, onCreated }: { onClose: () => void; onCreated: (name: string) => void }) {
  const [tpls, setTpls] = useState<Template[] | null>(null);
  const [tpl, setTpl] = useState('counter');
  const [name, setName] = useState('');
  const [desc, setDesc] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { get<{ templates: Template[] }>('studio/templates').then(r => setTpls(r.templates)).catch(e => setErr(e.message)); }, []);
  const clean = name.trim().toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '');

  return (
    <div className="modal-bg" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal">
        <div className="modal-head">
          <h2>New contract</h2>
          <button className="btn ghost sm" onClick={onClose}>close</button>
        </div>
        <label className="lbl">Start from</label>
        <div className="tpl-grid">
          {tpls ? tpls.map(t => (
            <button key={t.id} className={`tpl ${t.id === tpl ? 'on' : ''}`} onClick={() => setTpl(t.id)}>
              <b>{t.title}</b><span>{t.blurb}</span>
            </button>
          )) : <Loading what="Loading templates" />}
        </div>
        <label className="lbl">Name</label>
        <input className="input" autoFocus placeholder="my_token" value={name} onChange={e => setName(e.target.value)} />
        {name && clean !== name && <div className="hint">will be saved as <code>{clean}</code></div>}
        <label className="lbl">What is it for? <span className="dim">(optional)</span></label>
        <input className="input" placeholder="A token for my game's in-world currency" value={desc} onChange={e => setDesc(e.target.value)} />
        {err && <div className="modal-err">{err}</div>}
        <div className="modal-foot">
          <button className="btn" disabled={!clean || busy} onClick={async () => {
            setBusy(true); setErr(null);
            try { await post('studio/new', { name: clean, template: tpl, desc }); onCreated(clean); }
            catch (e: any) { setErr(e.message); setBusy(false); }
          }}>{busy ? 'Creating…' : 'Create contract'}</button>
        </div>
      </div>
    </div>
  );
}

// ---- the workspace: files · editor · output · agent ------------------------

function Workspace({ name, who, onChanged }: { name: string; who: Who | null; onChanged: () => void }) {
  const canWrite = !!who?.writable;
  const [meta, setMeta] = useState<{ files: string[]; state: string; description: string | null } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [path, setPath] = useState<string | null>(null);
  const [docs, setDocs] = useState<Record<string, { orig: string; text: string }>>({});
  const [run, setRun] = useState<{ kind: string; job: Job | null } | null>(null);
  const [out, setOut] = useState<{ kind: string; r: RunResult } | null>(null);
  const [outTab, setOutTab] = useState<'problems' | 'tests' | 'log'>('problems');
  const [side, setSide] = useState<'agent' | 'iface'>('agent');
  const [abi, setAbi] = useState<Abi | null>(null);
  const [, tick] = useState(0);
  const ed = useRef<EditorHandle>(null);

  const files = useMemo(() => (meta?.files || []).filter(f => EDITABLE.test(f) && f !== 'README.md'), [meta]);

  const loadMeta = useCallback(async () => {
    try {
      const m = await get<any>(`studio/get?name=${name}`);
      setMeta(m); setErr(null);
      return m;
    } catch (e: any) { setErr(e.message); return null; }
  }, [name]);

  const loadFile = useCallback(async (p: string, force = false) => {
    if (!force && docs[p]) return;
    const r = await get<{ content: string }>(`studio/read?name=${name}&path=${encodeURIComponent(p)}`);
    setDocs(d => ({ ...d, [p]: { orig: r.content ?? '', text: r.content ?? '' } }));
  }, [name, docs]);

  const loadAbi = useCallback(() => get<Abi>(`studio/abi?name=${name}`).then(setAbi).catch(() => setAbi(null)), [name]);

  useEffect(() => {
    loadMeta().then(m => {
      const fs: string[] = (m?.files || []).filter((f: string) => EDITABLE.test(f));
      const first = fs.find(f => f === 'src/lib.cairo') || fs[0];
      if (first) { setPath(first); loadFile(first, true); }
    });
    loadAbi();
    // pick up a run that is still going (e.g. the agent, after a reload)
    get<{ jobs: Job[] }>(`studio/jobs?name=${name}`).then(r => {
      const live = r.jobs.find(j => j.state === 'running' || j.state === 'queued');
      if (live) follow(live.fn, live.id);
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name]);

  // a ticking clock while something runs
  useEffect(() => {
    if (!run) return;
    const t = setInterval(() => tick(x => x + 1), 1000);
    return () => clearInterval(t);
  }, [run]);

  const doc = path ? docs[path] : undefined;
  const dirty = Object.entries(docs).filter(([, d]) => d.text !== d.orig).map(([p]) => p);

  async function saveAll() {
    for (const p of dirty) {
      await post('studio/write', { name, path: p, content: docs[p].text });
      setDocs(d => ({ ...d, [p]: { ...d[p], orig: d[p].text } }));
    }
  }

  async function refreshAfterRun() {
    const m = await loadMeta();
    // reload open files that have no local edits — the agent may have changed them
    for (const p of Object.keys(docs)) if (docs[p].text === docs[p].orig) await loadFile(p, true);
    if (path && m && !m.files.includes(path)) setPath(m.files.find((f: string) => EDITABLE.test(f)) || null);
    loadAbi(); onChanged();
  }

  async function follow(kind: string, id: string): Promise<Job> {
    setRun({ kind, job: null });
    const j = await waitJob(id, jj => setRun({ kind, job: jj }));
    setRun(null);
    return j;
  }

  async function start(kind: 'build' | 'test') {
    if (run) return;
    try {
      await saveAll();
      const { job } = await post<{ job: Job }>(`studio/${kind}`, { name });
      const j = await follow(kind, job.id);
      const r: RunResult = j.state === 'done' ? j.result : { ok: false, error: j.error || 'failed' };
      setOut({ kind, r });
      setOutTab(r.diagnostics?.length ? 'problems' : kind === 'test' && r.tests?.length ? 'tests' : (r.ok ? 'problems' : 'log'));
      await refreshAfterRun();
    } catch (e: any) { setRun(null); setOut({ kind, r: { ok: false, error: e.message } }); setOutTab('log'); }
  }

  const diags = out?.r.diagnostics || [];
  const marks = diags.filter(d => d.path === path).map(d => ({ line: d.line, level: d.level }));
  const running = run ? `${run.kind === 'vibe' ? 'Agent working' : run.kind === 'build' ? 'Building' : 'Testing'}… ${since(run.job?.started || run.job?.created || null)}` : null;

  if (err && !meta) return <div className="studio-blank"><ErrorBox error={err} onRetry={loadMeta} /></div>;

  return (
    <>
      <section className="studio-main">
        <div className="ws-head">
          <div className="ws-title">
            <h2>{name}</h2>
            {meta && <Badge tone={meta.state === 'fresh' ? 'ok' : meta.state === 'stale' ? 'warn' : 'dim'}>
              {meta.state === 'fresh' ? 'built' : meta.state === 'stale' ? 'changed since build' : 'not built'}
            </Badge>}
          </div>
          <div className="ws-actions">
            {running && <span className="running"><span className="spin" />{running}</span>}
            {canWrite && <>
              <button className="btn ghost sm" disabled={!dirty.length || !!run} onClick={() => saveAll().catch(e => setErr(e.message))}>
                {dirty.length ? `Save (${dirty.length})` : 'Saved'}
              </button>
              <button className="btn ghost" disabled={!!run} onClick={() => start('build')}>Build</button>
              <button className="btn" disabled={!!run} onClick={() => start('test')}>Build &amp; test</button>
            </>}
          </div>
        </div>
        {meta?.description && <div className="ws-desc">{meta.description}</div>}

        <div className="file-tabs">
          {files.map(f => (
            <button key={f} className={`ftab ${f === path ? 'on' : ''}`} onClick={() => { setPath(f); loadFile(f); }}>
              {f.split('/').pop()}
              <span className="ftab-dir">{f.includes('/') ? f.slice(0, f.lastIndexOf('/')) : ''}</span>
              {docs[f] && docs[f].text !== docs[f].orig && <span className="ftab-dirty" />}
            </button>
          ))}
        </div>

        {doc ? (
          <Editor ref={ed} path={path} value={doc.text} readOnly={!canWrite || run?.kind === 'vibe'} marks={marks}
            onChange={v => path && setDocs(d => ({ ...d, [path]: { ...d[path], text: v } }))}
            onSave={() => canWrite && saveAll().catch(e => setErr(e.message))} />
        ) : <div className="editor editor-empty"><Loading what="Opening" /></div>}

        <Output out={out} tab={outTab} setTab={setOutTab} onJump={d => {
          if (d.path !== path && files.includes(d.path)) { setPath(d.path); loadFile(d.path).then(() => setTimeout(() => ed.current?.jump(d.line, d.col), 50)); }
          else ed.current?.jump(d.line, d.col);
        }} />
      </section>

      <aside className="studio-side">
        <div className="side-tabs">
          <button className={side === 'agent' ? 'on' : ''} onClick={() => setSide('agent')}>Agent</button>
          <button className={side === 'iface' ? 'on' : ''} onClick={() => { setSide('iface'); loadAbi(); }}>Interface</button>
        </div>
        {side === 'agent'
          ? <Agent name={name} canWrite={canWrite} busy={run} beforeSend={saveAll}
              send={async prompt => {
                const { job } = await post<{ job: Job }>('studio/vibe', { name, prompt });
                const j = await follow('vibe', job.id);
                await refreshAfterRun();
                if (j.state === 'done' && j.result?.test) {
                  setOut({ kind: 'test', r: j.result.test });
                  setOutTab(j.result.test.diagnostics?.length ? 'problems' : 'tests');
                }
                return j;
              }} />
          : <Iface abi={abi} />}
      </aside>
    </>
  );
}

// ---- output panel -----------------------------------------------------------

function Output({ out, tab, setTab, onJump }: {
  out: { kind: string; r: RunResult } | null; tab: string; setTab: (t: any) => void; onJump: (d: Diag) => void;
}) {
  if (!out) return <div className="out out-idle">Build or test to see compiler output here. <span className="dim">Cmd/Ctrl+S saves.</span></div>;
  const { r, kind } = out;
  const diags = r.diagnostics || [];
  const errs = diags.filter(d => d.level === 'error').length;
  const tests = r.tests || [];
  const verdict = r.error && !r.log ? r.error
    : r.ok ? (kind === 'test' && r.totals ? `${r.totals.passed} passed${r.totals.failed ? `, ${r.totals.failed} failed` : ''}` : 'Compiled')
      : r.stage === 'build' || kind === 'build' ? `Build failed${errs ? ` — ${errs} error${errs > 1 ? 's' : ''}` : ''}`
        : r.totals ? `${r.totals.failed} test${r.totals.failed === 1 ? '' : 's'} failed` : 'Failed';
  return (
    <div className="out">
      <div className="out-head">
        <span className={`verdict ${r.ok ? 'ok' : 'err'}`}>{r.ok ? '●' : '●'} {verdict}</span>
        {r.seconds != null && <span className="dim">{r.seconds}s</span>}
        <div className="out-tabs">
          <button className={tab === 'problems' ? 'on' : ''} onClick={() => setTab('problems')}>Problems{diags.length ? ` ${diags.length}` : ''}</button>
          <button className={tab === 'tests' ? 'on' : ''} onClick={() => setTab('tests')}>Tests{tests.length ? ` ${tests.length}` : ''}</button>
          <button className={tab === 'log' ? 'on' : ''} onClick={() => setTab('log')}>Log</button>
        </div>
      </div>
      <div className="out-body">
        {tab === 'problems' && (diags.length ? diags.map((d, i) => (
          <button key={i} className={`diag ${d.level}`} onClick={() => onJump(d)}>
            <span className="diag-lvl">{d.level}</span>
            <span className="diag-msg">{d.message}</span>
            <span className="diag-at">{d.path}:{d.line}</span>
          </button>
        )) : <div className="out-none">{r.ok ? 'No problems.' : r.error || 'No file diagnostics — see the log.'}</div>)}
        {tab === 'tests' && (tests.length ? tests.map((t, i) => (
          <div key={i} className={`trow ${t.status}`}>
            <span className="tmark">{t.status === 'pass' ? 'PASS' : t.status === 'fail' ? 'FAIL' : t.status.toUpperCase()}</span>
            <span className="tname">{t.name}</span>
            {t.reason && <span className="treason">{t.reason}</span>}
            {t.gas != null && <span className="tgas">{t.gas.toLocaleString()} gas</span>}
          </div>
        )) : <div className="out-none">{kind === 'test' ? 'No tests ran.' : 'Run “Build & test” to run the snforge suite.'}</div>)}
        {tab === 'log' && <pre className="out-log">{r.log || r.error || '(empty)'}</pre>}
      </div>
    </div>
  );
}

// ---- the agent --------------------------------------------------------------

function Agent({ name, canWrite, busy, send, beforeSend }: {
  name: string; canWrite: boolean; busy: { kind: string; job: Job | null } | null;
  send: (prompt: string) => Promise<Job>; beforeSend: () => Promise<void>;
}) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState('');
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { setMsgs(loadMsgs(name)); }, [name]);
  useEffect(() => { end.current?.scrollIntoView({ block: 'end' }); }, [msgs, busy]);

  const push = (m: Msg) => setMsgs(prev => { const next = [...prev, m]; saveMsgs(name, next); return next; });

  async function go(prompt: string) {
    prompt = prompt.trim();
    if (!prompt || busy) return;
    setText('');
    push({ role: 'you', text: prompt, at: Date.now() });
    try {
      await beforeSend();
      const j = await send(prompt);
      const r = j.result || {};
      push({
        role: 'agent', at: Date.now(),
        text: j.state === 'done' ? (r.error || r.result || 'Done.') : (j.error || 'The agent failed.'),
        ok: j.state === 'done' && r.ok !== false && !r.error,
        cost: r.meta?.total_cost_usd, build: r.build, test: r.test,
      });
    } catch (e: any) {
      push({ role: 'agent', at: Date.now(), text: e.message || String(e), ok: false });
    }
  }

  const agentBusy = busy?.kind === 'vibe';
  return (
    <div className="agent">
      <div className="agent-log">
        {msgs.length === 0 && (
          <div className="agent-intro">
            <div className="agent-orb" />
            <b>Cairo agent</b>
            <p>Describe a change in plain English. The agent edits <code>{name}</code>, then builds and runs its tests, and tells you what it did.</p>
            {canWrite && <div className="suggest">
              {SUGGEST.map(s => <button key={s} className="chip" onClick={() => go(s)}>{s}</button>)}
            </div>}
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            <div className="msg-who">{m.role === 'you' ? 'You' : 'Agent'}</div>
            <div className="msg-text">{m.text}</div>
            {m.role === 'agent' && (m.build || m.test || m.cost != null) && (
              <div className="msg-meta">
                {m.build && <Badge tone={m.build.ok ? 'ok' : 'err'}>{m.build.ok ? 'builds' : 'build fails'}</Badge>}
                {m.test?.totals && <Badge tone={m.test.totals.failed ? 'err' : 'ok'}>
                  {m.test.totals.passed}/{m.test.totals.passed + m.test.totals.failed} tests pass</Badge>}
                {m.cost != null && <span className="dim">${m.cost.toFixed(3)}</span>}
              </div>
            )}
          </div>
        ))}
        {agentBusy && (
          <div className="msg agent pending">
            <div className="msg-who">Agent</div>
            <div className="msg-text"><span className="dots"><i /><i /><i /></span> editing, building and testing — {since(busy?.job?.started || busy?.job?.created || null)}</div>
          </div>
        )}
        <div ref={end} />
      </div>
      {canWrite ? (
        <div className="agent-input">
          <textarea className="input" rows={3} placeholder={busy ? 'Wait for the current run…' : 'e.g. make set() only callable by the deployer'}
            value={text} disabled={!!busy} onChange={e => setText(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); go(text); } }} />
          <div className="agent-foot">
            <span className="dim">Enter to send · runs on this server's Claude account</span>
            {msgs.length > 0 && !busy && <button className="btn ghost sm" onClick={() => { setMsgs([]); saveMsgs(name, []); }}>Clear</button>}
            <button className="btn sm" disabled={!text.trim() || !!busy} onClick={() => go(text)}>Send</button>
          </div>
        </div>
      ) : <div className="agent-locked">Sign in with the owner wallet to talk to the agent.</div>}
    </div>
  );
}

// ---- compiled interface -------------------------------------------------------

function Iface({ abi }: { abi: Abi | null }) {
  if (!abi) return <div className="iface"><Loading /></div>;
  if (!abi.contracts?.length) return <div className="iface"><div className="out-none">Not built yet — build to see the interface.</div></div>;
  return (
    <div className="iface">
      {abi.contracts.map(c => (
        <div key={c.contract} className="iface-c">
          <div className="iface-name">{c.contract}
            <span className="dim"> · {c.sierra_felts.toLocaleString()} sierra{c.casm_felts ? ` · ${c.casm_felts.toLocaleString()} casm` : ''} felts</span>
          </div>
          {c.constructor && <div className="ifn"><Badge>constructor</Badge><code>({c.constructor.map(a => `${a.name}: ${a.type}`).join(', ')})</code></div>}
          {c.functions.map(f => (
            <div key={f.name} className="ifn">
              <Badge tone={f.mutability === 'view' ? 'ok' : 'accent'}>{f.mutability === 'view' ? 'read' : 'write'}</Badge>
              <code><b>{f.name}</b>({f.inputs.map(a => `${a.name}: ${a.type}`).join(', ')}){f.outputs.length ? ` → ${f.outputs.join(', ')}` : ''}</code>
            </div>
          ))}
          {c.events.length > 0 && <div className="iface-sub">Events</div>}
          {c.events.map(e => (
            <div key={e.name} className="ifn"><Badge>event</Badge><code><b>{e.name}</b>({e.fields.map(f => `${f.key ? '#' : ''}${f.name}: ${f.type}`).join(', ')})</code></div>
          ))}
        </div>
      ))}
    </div>
  );
}
