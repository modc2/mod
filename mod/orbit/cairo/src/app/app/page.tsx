'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Abi, AbiContract, Diag, Diagnostics, Editor, EditorHandle, TestTable } from './views';

const BASE = process.env.NEXT_PUBLIC_BASE ?? '/cairo';
// Self-calls go to /cairo/_api/* — the gateway KEEPS this prefix (routed to the
// app), and src/api/server.py maps _api onto the same handlers as /cairo/api.
const API = `${BASE}/_api`;

type BuildState = 'fresh' | 'stale' | 'unbuilt';
type Contract = { name: string; built: boolean; state?: BuildState; template?: string | null; description?: string | null };
type Template = { id: string; title: string; blurb: string };
type Job = {
  id: string;
  fn: string;
  name: string | null;
  state: 'queued' | 'running' | 'done' | 'error';
  created: number;
  started: number | null;
  finished: number | null;
  result: any;
  error: string | null;
};

// Starter prompts per template: the fastest way to see vibecoding work is a
// change that is obviously the next step for the contract in front of you.
const IDEAS: Record<string, string[]> = {
  counter: [
    'make it an owner-only u256 counter with increment, decrement and reset',
    'add a max value set in the constructor and reject anything above it',
  ],
  token: [
    'add an owner-only mint and a public burn, with tests',
    'add a pause switch the owner controls that blocks transfers',
  ],
  commit_reveal: [
    'turn it into a sealed-bid auction: commit deadline, reveal deadline, highest revealed bid wins',
    'build rock-paper-scissors for two players on top of the commit-reveal',
  ],
  merkle_allowlist: [
    'let an owner rotate the root and pause claims',
    'pay out the claimed amount from an ERC20 set in the constructor',
  ],
};
const GENERIC_IDEAS = ['add tests for every revert path', 'add events for every state change'];

// Writes are owner-only through the gateway (src/api/server.py). One
// personal_sign makes a mod-protocol token; starknet's studio token is the
// same scheme on the same origin, so it works here too.
const TOKEN_KEY = 'cairo.token';
const token = () => {
  try {
    return localStorage.getItem(TOKEN_KEY) || localStorage.getItem('starknet.studio.token');
  } catch {
    return null;
  }
};

function b64url(s: string): string {
  let bin = '';
  new TextEncoder().encode(s).forEach((b) => { bin += String.fromCharCode(b); });
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

async function signIn(): Promise<void> {
  const eth = (window as any).ethereum;
  if (!eth) throw new Error('No browser wallet found. Install MetaMask or Rabby to sign in.');
  const [address] = await eth.request({ method: 'eth_requestAccounts' });
  const data = 'cairo'; // a string: verifies in Python and Rust alike
  const time = Math.floor(Date.now() / 1000).toString();
  const signature = await eth.request({ method: 'personal_sign', params: [JSON.stringify({ data, time }), address] });
  const t = b64url(JSON.stringify({ data, time, key: String(address).toLowerCase(), signature }));
  try {
    localStorage.setItem(TOKEN_KEY, t);
  } catch {
    // shared-origin quota full: the token just won't survive a reload
  }
}

async function call(fn: string, body?: Record<string, unknown>, query?: Record<string, string>): Promise<any> {
  const qs = query ? `?${new URLSearchParams(query)}` : '';
  const t = token();
  const res = await fetch(`${API}/${fn}${qs}`, {
    method: body ? 'POST' : 'GET',
    headers: { ...(body ? { 'Content-Type': 'application/json' } : {}), ...(t ? { token: t } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  return res.json();
}

const live = (j: Job) => j.state === 'queued' || j.state === 'running';

function jobOk(j: Job): boolean | null {
  if (j.state !== 'done') return j.state === 'error' ? false : null;
  const r = j.result || {};
  if (r.error) return false;
  if (j.fn === 'vibe') return !!r.ok && (r.build?.ok ?? true) && (r.test?.ok ?? true);
  return r.ok ?? true;
}

function secs(j: Job, now: number): string {
  if (!j.started) return '';
  return `${Math.round((j.finished ?? now) - j.started)}s`;
}

const STATE_TIP: Record<BuildState, string> = {
  fresh: 'built, up to date',
  stale: 'sources changed since the last build',
  unbuilt: 'not built yet',
};

export default function Page() {
  const [tools, setTools] = useState<Record<string, any>>({});
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [shown, setShown] = useState<string | null>(null);
  const [err, setErr] = useState('');
  const [now, setNow] = useState(Date.now() / 1000);
  const [who, setWho] = useState<{ writable: boolean; address?: string | null; why?: string } | null>(null);
  const editor = useRef<{ jump: (d: Diag) => void } | null>(null);

  const refresh = useCallback(async () => {
    try {
      const r = await call('list');
      setContracts(r.contracts || []);
      setSel((s) => s ?? r.contracts?.[0]?.name ?? null);
      setErr('');
    } catch {
      setErr('API unreachable');
    }
  }, []);

  const loadJobs = useCallback(async () => {
    try {
      const r = await call('jobs');
      setJobs(r.jobs || []);
      return (r.jobs || []) as Job[];
    } catch {
      return [];
    }
  }, []);

  useEffect(() => {
    refresh();
    loadJobs();
    call('toolchain').then(setTools).catch(() => {});
    call('whoami').then(setWho).catch(() => {});
    call('templates').then((r) => setTemplates(r.templates || [])).catch(() => {});
  }, [refresh, loadJobs]);

  // Poll while anything is in flight; refresh the catalog when a job lands
  // (build state, vibe-written files). The 1s tick keeps elapsed times live.
  const active = jobs.some(live);
  useEffect(() => {
    if (!active) return;
    const tick = setInterval(() => setNow(Date.now() / 1000), 1000);
    const poll = setInterval(async () => {
      const js = await loadJobs();
      if (!js.some(live)) refresh();
    }, 2000);
    return () => {
      clearInterval(tick);
      clearInterval(poll);
    };
  }, [active, loadJobs, refresh]);

  const start = async (fn: string, body: Record<string, unknown>) => {
    setErr('');
    const r = await call(fn, { ...body, async: true });
    if (r.error) return setErr(r.error);
    setShown(r.job.id);
    setNow(Date.now() / 1000);
    loadJobs();
  };

  const busy = (name: string | null) => jobs.some((j) => j.name === name && live(j));
  const shownJob = jobs.find((j) => j.id === shown) || null;
  const selContract = contracts.find((c) => c.name === sel) || null;
  // the newest finished job on the open contract: Workbench reloads on it
  const landed = jobs.find((j) => j.name === sel && j.finished)?.id ?? null;

  const jump = (d: Diag) => editor.current?.jump(d);

  return (
    <div className="wrap">
      <header className="top">
        <div className="mark">C</div>
        <div>
          <h1>cairo</h1>
          <p className="sub">make, vibecode and test Starknet contracts. Each one is its own mod.</p>
        </div>
        <div className="tools">
          {['scarb', 'snforge', 'claude'].map((t) => (
            <span key={t} className={`pill ${tools[t]?.path ? 'ok' : tools[t] ? 'bad' : ''}`}>
              {t} {tools[t]?.version?.split(' ')[1] ?? (tools[t]?.path ? 'ok' : tools[t] ? 'missing' : '…')}
            </span>
          ))}
          {who && !who.writable && (
            <button
              className="pill"
              title={who.why || 'sign in with the owner wallet to build, test and vibecode'}
              onClick={() => signIn().then(() => call('whoami')).then(setWho).catch((e) => setErr(String(e?.message || e)))}
            >
              sign in to edit
            </button>
          )}
          {who?.writable && who.address && (
            <span className="pill ok" title="owner">{who.address.slice(0, 6)}…{who.address.slice(-4)}</span>
          )}
        </div>
      </header>

      {err && <p className="err">{err}</p>}

      <div className="grid">
        <aside>
          <section className="card">
            <h2>
              Contracts <span className="spacer" />
              <span className="count">{contracts.length}</span>
            </h2>
            <div className="list">
              {contracts.length === 0 && <div className="muted">None yet. Make one below.</div>}
              {contracts.map((c) => (
                <button key={c.name} className={`item ${sel === c.name ? 'on' : ''}`} onClick={() => setSel(c.name)}>
                  <span className={`dot ${busy(c.name) ? 'run' : c.state ?? (c.built ? 'fresh' : 'unbuilt')}`} title={busy(c.name) ? 'running' : STATE_TIP[c.state ?? 'unbuilt']} />
                  <span className="nm">{c.name}</span>
                  {c.template && c.template !== 'counter' && <span className="tag">{c.template.replace('_', ' ')}</span>}
                </button>
              ))}
            </div>
          </section>
          <NewContract
            templates={templates}
            taken={contracts.map((c) => c.name)}
            onDone={(name) => {
              refresh();
              setSel(name);
            }}
          />
        </aside>

        <main style={{ minWidth: 0 }}>
          {sel ? (
            <Workbench
              key={sel}
              name={sel}
              contract={selContract}
              busy={busy(sel)}
              start={start}
              landed={landed}
              lastJob={jobs.find((j) => j.name === sel && j.state === 'done') || null}
              editorRef={editor}
              onRemoved={() => {
                setSel(null);
                refresh();
              }}
            />
          ) : (
            <section className="card muted">Pick a contract, or make a new one.</section>
          )}

          <section className="card">
            <h2>
              Output
              <span className="spacer" />
              {shownJob && (
                <span className={`pill ${jobOk(shownJob) === true ? 'ok' : jobOk(shownJob) === false ? 'bad' : ''}`}>
                  {shownJob.fn} {shownJob.name} · {shownJob.state} {secs(shownJob, now)}
                </span>
              )}
            </h2>
            {shownJob ? (
              <JobView job={shownJob} now={now} onJump={jump} />
            ) : (
              <div className="muted">Build, test or vibe a contract to see its output here.</div>
            )}
            {jobs.length > 0 && (
              <div className="jobs">
                {jobs.slice(0, 8).map((j) => (
                  <button className={`job ${shown === j.id ? 'on' : ''}`} key={j.id} onClick={() => setShown(j.id)}>
                    <span className={`state ${live(j) ? j.state : jobOk(j) === false ? 'error' : 'done'}`}>
                      {live(j) ? j.state : jobOk(j) === false ? 'failed' : 'ok'}
                    </span>
                    <span>
                      {j.fn} {j.name}
                    </span>
                    <span className="muted">{secs(j, now)}</span>
                  </button>
                ))}
              </div>
            )}
          </section>
        </main>
      </div>
    </div>
  );
}

// What a finished (or running) job means, in order of what you want first:
// the verdict, then where it broke, then the raw log behind a fold.
function JobView({ job, now, onJump }: { job: Job; now: number; onJump: (d: Diag) => void }) {
  if (live(job)) {
    const hint =
      job.fn === 'vibe'
        ? 'The agent is editing, building and testing. This usually takes a few minutes.'
        : job.fn === 'test'
          ? 'Compiling the test crate (about 30s), then running snforge.'
          : 'Compiling to Sierra and CASM.';
    return (
      <div className="running">
        <div className="bar">
          <i />
        </div>
        <div className="muted">
          {job.state === 'queued' ? 'Waiting for another run on this contract…' : `${job.fn} ${job.name} · ${secs(job, now)}`} · {hint}
        </div>
      </div>
    );
  }
  if (job.error) return <pre className="log">{job.error}</pre>;
  const r = job.result || {};
  if (r.error) return <pre className="log">{r.error}</pre>;

  if (job.fn === 'vibe') {
    return (
      <div className="col">
        <div className="agent">
          <div className="who">
            agent
            {r.meta?.num_turns != null && <span className="muted"> · {r.meta.num_turns} turns</span>}
            {r.meta?.total_cost_usd != null && <span className="muted"> · ${Number(r.meta.total_cost_usd).toFixed(2)}</span>}
          </div>
          <pre className="say">{r.result || '(no reply)'}</pre>
        </div>
        {r.build && <StepView fn="build" r={r.build} onJump={onJump} />}
        {r.test && <StepView fn="test" r={r.test} onJump={onJump} />}
      </div>
    );
  }
  return <StepView fn={job.fn} r={r} onJump={onJump} />;
}

function StepView({ fn, r, onJump }: { fn: string; r: any; onJump: (d: Diag) => void }) {
  const diags: Diag[] = r.diagnostics || [];
  return (
    <div className="step">
      {fn === 'build' && (
        <div className="totals">
          <span className={`big ${r.ok ? 'ok' : 'bad'}`}>{r.ok ? 'compiled' : 'build failed'}</span>
          {r.ok &&
            (r.artifacts || []).map((a: AbiContract) => (
              <span className="pill" key={a.contract}>
                {a.contract} · {a.functions.length} entrypoints · {a.sierra_felts.toLocaleString()} felts
              </span>
            ))}
          {r.seconds != null && <span className="muted">{r.seconds}s</span>}
        </div>
      )}
      {fn === 'test' && r.tests && (r.tests.length > 0 || r.totals) && <TestTable tests={r.tests} totals={r.totals} seconds={r.seconds} />}
      {fn === 'test' && !r.ok && !r.totals && <div className="big bad">{r.stage === 'build' ? 'contract did not compile' : 'tests did not compile'}</div>}
      <Diagnostics diags={diags} onJump={onJump} />
      {typeof r.log === 'string' && (
        <details open={!r.ok && diags.length === 0 && !(r.tests || []).length}>
          <summary>{fn} log</summary>
          <Log text={r.log} />
        </details>
      )}
      {typeof r.log !== 'string' && fn !== 'build' && fn !== 'test' && <Log text={JSON.stringify(r, null, 2)} />}
    </div>
  );
}

// scarb/snforge put the verdict at the end of the log — open scrolled there.
function Log({ text }: { text: string }) {
  const ref = useRef<HTMLPreElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [text]);
  return (
    <pre className="log" ref={ref}>
      {text}
    </pre>
  );
}

function NewContract({ templates, taken, onDone }: { templates: Template[]; taken: string[]; onDone: (name: string) => void }) {
  const [name, setName] = useState('');
  const [desc, setDesc] = useState('');
  const [tpl, setTpl] = useState('counter');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const pkg = name
    .trim()
    .replace(/[^0-9a-zA-Z]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .toLowerCase();
  const clash = taken.includes(pkg);

  const make = async () => {
    setBusy(true);
    setErr('');
    try {
      const r = await call('new', { name, desc: desc || undefined, template: tpl });
      if (r.error) return setErr(r.error);
      setName('');
      setDesc('');
      onDone(r.name);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card">
      <h2>New contract</h2>
      <div className="col">
        <div className="tpls">
          {templates.map((t) => (
            <button key={t.id} className={`tpl ${tpl === t.id ? 'on' : ''}`} onClick={() => setTpl(t.id)} title={t.blurb}>
              <b>{t.title}</b>
              <span>{t.blurb}</span>
            </button>
          ))}
        </div>
        <input
          placeholder="name, e.g. vault"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && pkg && !clash && make()}
        />
        <input placeholder="what it does (optional)" value={desc} onChange={(e) => setDesc(e.target.value)} />
        <button className="btn primary" disabled={!pkg || clash || busy} onClick={make}>
          {busy ? 'Making…' : clash ? `${pkg} exists` : `Make${pkg ? ` ${pkg}` : ''}`}
        </button>
        {err && <div className="err">{err}</div>}
      </div>
    </section>
  );
}

function Workbench({
  name,
  contract,
  busy,
  start,
  landed,
  lastJob,
  editorRef,
  onRemoved,
}: {
  name: string;
  contract: Contract | null;
  busy: boolean;
  start: (fn: string, body: Record<string, unknown>) => Promise<void>;
  landed: string | null;
  lastJob: Job | null;
  editorRef: React.MutableRefObject<{ jump: (d: Diag) => void } | null>;
  onRemoved: () => void;
}) {
  const [files, setFiles] = useState<string[]>([]);
  const [path, setPath] = useState<string | null>(null);
  const [content, setContent] = useState('');
  const [saved, setSaved] = useState('');
  const [prompt, setPrompt] = useState('');
  const [armed, setArmed] = useState(false);
  const [msg, setMsg] = useState('');
  const [abi, setAbi] = useState<AbiContract[] | null>(null);
  const ed = useRef<EditorHandle>(null);
  const pending = useRef<Diag | null>(null);

  const loadTree = useCallback(async () => {
    const r = await call('tree', undefined, { name });
    const fs: string[] = (r.files || []).filter((f: string) => f !== 'mod.py');
    // contract source first, then tests, then manifests
    const rank = (f: string) => (f.startsWith('src/') ? 0 : f.startsWith('tests/') ? 1 : 2);
    fs.sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
    setFiles(fs);
    setPath((p) => (p && fs.includes(p) ? p : fs.find((f) => f === 'src/lib.cairo') ?? fs[0] ?? null));
  }, [name]);

  const loadFile = useCallback(async () => {
    if (!path) return;
    const r = await call('read', undefined, { name, path });
    setContent(r.content ?? '');
    setSaved(r.content ?? '');
  }, [name, path]);

  const loadAbi = useCallback(async () => {
    const r = await call('abi', undefined, { name });
    setAbi(r.contracts || []);
  }, [name]);

  useEffect(() => {
    loadTree();
    loadAbi();
  }, [loadTree, loadAbi]);
  useEffect(() => {
    loadFile();
  }, [loadFile]);

  // a jump requested before its file was open lands once the file loads
  useEffect(() => {
    const d = pending.current;
    if (d && d.path === path && content === saved) {
      pending.current = null;
      requestAnimationFrame(() => ed.current?.jump(d.line, d.col));
    }
  }, [path, content, saved]);

  editorRef.current = {
    jump(d: Diag) {
      if (d.path === path) return ed.current?.jump(d.line, d.col);
      pending.current = d;
      setPath(d.path);
    },
  };

  // A job on this contract landed: vibe may have rewritten files, build has a
  // new ABI. Reload, keeping unsaved edits.
  const seen = useRef(landed);
  useEffect(() => {
    if (landed && landed !== seen.current) {
      seen.current = landed;
      loadTree();
      loadAbi();
      if (content === saved) loadFile();
    }
  }, [landed, loadTree, loadAbi, loadFile, content, saved]);

  const save = async () => {
    if (!path) return;
    const r = await call('write', { name, path, content });
    if (r.error) return setMsg(r.error);
    setSaved(content);
    setMsg(`saved ${path}`);
  };

  const remove = async () => {
    if (!armed) return setArmed(true);
    const r = await call('remove', { name });
    if (r.error) return setMsg(r.error);
    onRemoved();
  };

  const dirty = content !== saved;
  // red-wash lines from the newest build/test of this contract
  const res = lastJob?.result || {};
  const marks: Diag[] = [
    ...(res.diagnostics || []),
    ...(res.build?.diagnostics || []),
    ...(res.test?.diagnostics || []),
  ].filter((d: Diag) => d.path === path);
  const state = contract?.state ?? 'unbuilt';
  const ideas = IDEAS[contract?.template ?? 'counter'] ?? GENERIC_IDEAS;

  const run = async (fn: string) => {
    if (dirty) await save();
    start(fn, { name });
  };

  return (
    <>
      <section className="card">
        <h2>
          <span className="title">{name}</span>
          <span className={`badge ${state}`} title={STATE_TIP[state]}>
            {state === 'fresh' ? 'built' : state}
          </span>
          {contract?.description && <span className="desc">{contract.description}</span>}
          <span className="spacer" />
          <button className="btn" disabled={busy} onClick={() => run('build')} title={dirty ? 'saves first' : ''}>
            Build
          </button>
          <button className="btn primary" disabled={busy} onClick={() => run('test')} title={dirty ? 'saves first' : ''}>
            Test
          </button>
          <button className={`btn ${armed ? 'danger' : ''}`} disabled={busy} onClick={remove} onMouseLeave={() => setArmed(false)}>
            {armed ? 'Really delete?' : 'Delete'}
          </button>
        </h2>
        <div className="split">
          <div className="list">
            {files.map((f) => (
              <button key={f} className={`item file ${path === f ? 'on' : ''}`} onClick={() => setPath(f)}>
                {f}
              </button>
            ))}
          </div>
          <div className="col" style={{ minWidth: 0 }}>
            <Editor ref={ed} value={content} path={path} marks={marks} onChange={setContent} onSave={save} />
            <div className="row">
              <button className="btn" disabled={!dirty || busy} onClick={save}>
                Save
              </button>
              {dirty && (
                <button className="btn" onClick={() => setContent(saved)}>
                  Discard
                </button>
              )}
              <span className="muted">{dirty ? 'unsaved · Ctrl+S' : msg}</span>
            </div>
          </div>
        </div>
      </section>

      <section className="card">
        <h2>
          Interface
          <span className="spacer" />
          {state === 'stale' && <span className="muted">from the last build · rebuild to refresh</span>}
        </h2>
        {abi && abi.length > 0 ? (
          <Abi contracts={abi} />
        ) : (
          <div className="muted">Build to see the entrypoints, events and size of the compiled class.</div>
        )}
      </section>

      <section className="card">
        <h2>Vibecode</h2>
        <div className="col">
          <textarea
            rows={3}
            placeholder={`Tell the agent what to change in ${name}`}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={async (e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && prompt.trim() && !busy && !dirty) {
                await start('vibe', { name, prompt });
                setPrompt('');
              }
            }}
          />
          <div className="ideas">
            {ideas.map((i) => (
              <button key={i} className="idea" onClick={() => setPrompt(i)}>
                {i}
              </button>
            ))}
          </div>
          <div className="row">
            <button
              className="btn primary"
              disabled={!prompt.trim() || busy || dirty}
              onClick={async () => {
                await start('vibe', { name, prompt });
                setPrompt('');
              }}
            >
              Vibe
            </button>
            <span className="muted">
              {dirty
                ? 'Save or discard your edits first.'
                : 'Claude edits this contract only, then builds and tests it. Uses the host Claude account.'}
            </span>
          </div>
        </div>
      </section>
    </>
  );
}
