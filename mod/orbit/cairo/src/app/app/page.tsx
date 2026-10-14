'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Abi, AbiContract, Diag, Diagnostics, Editor, EditorHandle, TestTable } from './views';
import { AgentStep, BASE, BuildState, Contract, Job, STATE_TIP, Template, call, jobOk, live, secs, signIn } from './lib';
import { Market } from './market';
import { Create, NewSeed } from './create';
import { Docs } from './docs';
import { Learn } from './learn';
import { AgentOrb, AgentAction } from './agent';

// Top-level views. The hash (#market, #new, #docs) makes each one linkable.
type View = 'studio' | 'market' | 'new' | 'docs' | 'learn';
const VIEWS: { id: View; label: string; tip: string }[] = [
  { id: 'studio', label: 'Studio', tip: 'edit, build, test and vibecode a contract' },
  { id: 'market', label: 'Market', tip: 'the bazaar: every contract here, open or fork one' },
  { id: 'learn', label: 'Learn', tip: 'stablecoins from zero: short plain-English lessons' },
  { id: 'docs', label: 'Docs', tip: 'how it works, Cairo basics, the HTTP API' },
];
const isView = (v: string): v is View => v === 'new' || VIEWS.some((x) => x.id === v);

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

// The mark: a pyramid, lit face + shade face over a base line.
function Pyramid({ size = 28 }: { size?: number }) {
  return (
    <svg className="pyramid" width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <defs>
        <linearGradient id="pyr-lit" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#ffb199" />
          <stop offset="1" stopColor="#ff6b4a" />
        </linearGradient>
        <linearGradient id="pyr-shade" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#c4472a" />
          <stop offset="1" stopColor="#7a2a17" />
        </linearGradient>
      </defs>
      <path d="M16 3 L3 27 L19 27 Z" fill="url(#pyr-lit)" />
      <path d="M16 3 L19 27 L29 27 Z" fill="url(#pyr-shade)" />
      <path d="M16 3 L19 27" stroke="rgba(255,255,255,0.35)" strokeWidth="0.8" />
    </svg>
  );
}

// Sidebar toggle icon: a panel with its left column filled when open.
function SideIcon({ open }: { open: boolean }) {
  return (
    <svg width="18" height="18" viewBox="0 0 20 20" aria-hidden="true">
      <rect x="2.5" y="3.5" width="15" height="13" rx="2.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <rect x="11.75" y="4.25" width="5" height="11.5" rx="1.5" fill={open ? 'currentColor' : 'none'} opacity={open ? 0.9 : 0} />
      <path d="M11.5 3.5 V16.5" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  );
}


export default function Page() {
  const [tools, setTools] = useState<Record<string, any>>({});
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [shown, setShown] = useState<string | null>(null);
  const [err, setErr] = useState('');
  const [now, setNow] = useState(Date.now() / 1000);
  const [who, setWho] = useState<{ writable: boolean; via?: string | null; address?: string | null; why?: string } | null>(null);
  const editor = useRef<{ jump: (d: Diag) => void } | null>(null);
  const [view, setViewState] = useState<View>('studio');
  const [seed, setSeed] = useState<NewSeed>({});
  // the open lesson in LEARN (null = the lesson list); remembered
  const [lesson, setLessonState] = useState<string | null>(null);
  useEffect(() => setLessonState(localStorage.getItem('cairo.lesson') || null), []);
  const setLesson = (id: string | null) => {
    setLessonState(id);
    if (id) localStorage.setItem('cairo.lesson', id);
    else localStorage.removeItem('cairo.lesson');
  };
  // the right drawer (My contracts), toggled from the top-right; remembered
  const [drawer, setDrawerState] = useState(false);
  useEffect(() => {
    const saved = localStorage.getItem('cairo.drawer');
    setDrawerState(saved === null ? window.innerWidth >= 1100 : saved === '1');
  }, []);
  const setDrawer = (open: boolean) => {
    setDrawerState(open);
    localStorage.setItem('cairo.drawer', open ? '1' : '0');
  };
  const narrow = () => typeof window !== 'undefined' && window.innerWidth < 1100;

  const setView = (v: View) => {
    setViewState(v);
    if (typeof window !== 'undefined') history.replaceState(null, '', v === 'studio' ? window.location.pathname : `#${v}`);
  };
  useEffect(() => {
    const read = () => {
      const h = window.location.hash.slice(1);
      setViewState(isView(h) ? h : 'studio');
    };
    read();
    window.addEventListener('hashchange', read);
    return () => window.removeEventListener('hashchange', read);
  }, []);

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
  const doSignIn = () =>
    signIn()
      .then(() => call('whoami'))
      .then(setWho)
      .catch((e) => setErr(String(e?.message || e)));
  const openContract = (name: string) => {
    setSel(name);
    setView('studio');
    if (narrow()) setDrawerState(false);
  };
  const newContract = (sd: NewSeed = {}) => {
    setSeed(sd);
    setView('new');
  };
  const removeContract = async (name: string) => {
    const r = await call('remove', { name });
    if (r.error) return setErr(r.error);
    if (sel === name) setSel(null);
    refresh();
  };
  // newest finished job anywhere: the market re-reads when something lands
  const landedAny = jobs.find((j) => j.finished)?.id ?? null;

  return (
    <div className={`wrap ${drawer ? 'shift' : ''}`}>
      <header className="top">
        <button className="brand" onClick={() => setView('studio')} title="cairo · zero-knowledge contracts, one mod each">
          <span className="mark"><Pyramid /></span>
          <span className="name">cairo</span>
          <span className="sub">zk contracts</span>
        </button>
        <nav className="views">
          {VIEWS.map((v) => (
            <button key={v.id} className={`view ${view === v.id ? 'on' : ''}`} title={v.tip} onClick={() => setView(v.id)}>
              {v.label}
              {v.id === 'studio' && active && <i className="vdot" />}
            </button>
          ))}
        </nav>
        <button className={`plusbtn ${view === 'new' ? 'on' : ''}`} title="new contract: from a starter, a fork or a sentence" onClick={() => newContract()}>
          +
        </button>
        <div className="tools">
          <span className="chain" title={['scarb', 'snforge', 'claude'].map((t) => `${t}: ${tools[t]?.version ?? (tools[t]?.path ? 'ok' : tools[t] ? 'missing' : '…')}`).join('\n')}>
            {['scarb', 'snforge', 'claude'].map((t) => (
              <span key={t} className={`tool ${tools[t]?.path ? 'ok' : tools[t] ? 'bad' : ''}`}>
                <i />
                <span className="tname">{t}</span>
              </span>
            ))}
          </span>
          {who && !who.address && who.via !== 'local' && (
            <button className="signin" title={who.why || 'sign in with the owner wallet to build, test and vibecode'} onClick={doSignIn}>
              Sign in
            </button>
          )}
          {who?.via === 'local' && !who.address && (
            <span className="me" title="local owner: this machine can build, test and vibecode">
              <i />
              local
            </span>
          )}
          {who?.address && (
            <button
              className={`me ${who.writable ? '' : 'guest'}`}
              title={`${who.writable ? 'owner' : 'signed in, read-only (not an owner)'} · ${who.address}\nclick to sign in with another wallet`}
              onClick={doSignIn}
            >
              <i />
              {who.address.slice(0, 6)}…{who.address.slice(-4)}
            </button>
          )}
          <button
            className={`sidebtn ${drawer ? 'on' : ''}`}
            title={drawer ? 'close the sidebar' : 'open the sidebar: my contracts'}
            aria-expanded={drawer}
            onClick={() => setDrawer(!drawer)}
          >
            <SideIcon open={drawer} />
          </button>
        </div>
      </header>

      <aside className={`drawer ${drawer ? 'open' : ''}`} aria-hidden={!drawer}>
        <h2>
          My contracts <span className="count">{contracts.length}</span>
          <span className="spacer" />
          <button className="plusbtn sm" title="new contract" onClick={() => newContract()}>
            +
          </button>
          <button className="xbtn" title="close" onClick={() => setDrawer(false)}>
            ×
          </button>
        </h2>
        <div className="list">
          {contracts.length === 0 && (
            <button className="item ghostitem" onClick={() => newContract()}>
              None yet. Press + to make one.
            </button>
          )}
          {contracts.map((c) => (
            <MyContract
              key={c.name}
              c={c}
              on={view === 'studio' && sel === c.name}
              busy={busy(c.name)}
              writable={!!who?.writable}
              onOpen={() => openContract(c.name)}
              onFork={() => newContract({ fork: c.name })}
              onRemove={() => removeContract(c.name)}
            />
          ))}
        </div>
      </aside>
      {drawer && <div className="scrim" onClick={() => setDrawer(false)} />}

      {err && <p className="err">{err}</p>}

      {view === 'docs' && <Docs />}
      {view === 'learn' && (
        <Learn
          lesson={lesson}
          setLesson={setLesson}
          onNew={(template) => newContract({ template })}
          onAsk={() => window.dispatchEvent(new Event('cairo:agent-open'))}
        />
      )}

      {view !== 'docs' && view !== 'learn' && (
      <div>
        <main style={{ minWidth: 0 }}>
        {view === 'market' && (
          <Market refreshKey={landedAny} writable={!!who?.writable} onOpen={openContract} onNew={newContract} />
        )}

        {view === 'new' && (
          <Create
            templates={templates}
            contracts={contracts.map((c) => c.name)}
            seed={seed}
            writable={!!who?.writable}
            onSignIn={doSignIn}
            start={start}
            onDone={(name) => {
              refresh();
              openContract(name);
            }}
          />
        )}

          {view === 'studio' && (
            <>
              {sel ? (
                <Workbench
                  key={sel}
                  name={sel}
                  contract={selContract}
                  busy={busy(sel)}
                  start={start}
                  landed={landed}
                  lastJob={jobs.find((j) => j.name === sel && j.state === 'done') || null}
                  lastTest={jobs.find((j) => j.name === sel && j.state !== 'queued' && j.state !== 'running' && (j.fn === 'test' || (j.fn === 'vibe' && j.result?.test))) || null}
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
            </>
          )}
        </main>
      </div>
      )}
      <AgentOrb
        ctx={{ contract: view === 'studio' ? sel : null, view, lesson: view === 'learn' ? lesson : null }}
        writable={!!who?.writable}
        onAction={(a: AgentAction) => {
          if (a.lesson) {
            setLesson(a.lesson);
            setView('learn');
          } else if (a.new) newContract({ template: a.new });
          else if (a.open) openContract(a.open);
        }}
      />
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
        {job.steps && job.steps.length > 0 && <Trail steps={job.steps} live />}
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
            {r.meta?.agent && <span className="muted"> · via {r.meta.agent}</span>}
          </div>
          <pre className="say">{r.result || '(no reply)'}</pre>
        </div>
        {(r.steps || []).length > 0 && (
          <details>
            <summary>{r.steps.length} agent steps</summary>
            <Trail steps={r.steps} />
          </details>
        )}
        {r.build && <StepView fn="build" r={r.build} onJump={onJump} />}
        {r.test && <StepView fn="test" r={r.test} onJump={onJump} />}
      </div>
    );
  }
  return <StepView fn={job.fn} r={r} onJump={onJump} />;
}

// What the agent did, one line per tool call. Live: only the newest few.
function Trail({ steps, live }: { steps: AgentStep[]; live?: boolean }) {
  const rows = live ? steps.slice(-6) : steps;
  return (
    <ol className="trail">
      {rows.map((s, i) => (
        <li key={i} className={s.error ? 'bad' : ''}>
          <b>{s.tool}</b> <span className="muted">{s.what}</span>
        </li>
      ))}
    </ol>
  );
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

// One contract, four views of it: the APP it ships, the API it exposes, the
// CODE behind it, and the TESTS that hold it. Header actions work from any tab.
type Tab = 'app' | 'api' | 'code' | 'test';
const TABS: { id: Tab; label: string; tip: string }[] = [
  { id: 'app', label: 'App', tip: 'the contract as a page on the local devnet' },
  { id: 'api', label: 'API', tip: 'entrypoints, events and how to call them over HTTP' },
  { id: 'code', label: 'Code', tip: 'edit the sources or vibecode them' },
  { id: 'test', label: 'Test', tip: 'snforge results and the test files' },
];
const TAB_KEY = 'cairo.tab';

// One row in the sidebar: click to open; hover shows app / fork / delete.
// Delete is two clicks (arm, then confirm) like the Workbench button.
function MyContract({
  c,
  on,
  busy,
  writable,
  onOpen,
  onFork,
  onRemove,
}: {
  c: Contract;
  on: boolean;
  busy: boolean;
  writable: boolean;
  onOpen: () => void;
  onFork: () => void;
  onRemove: () => void;
}) {
  const [armed, setArmed] = useState(false);
  const state = c.state ?? (c.built ? 'fresh' : 'unbuilt');
  const stop = (f: () => void) => (e: React.MouseEvent) => {
    e.stopPropagation();
    f();
  };
  return (
    <div className={`item mine ${on ? 'on' : ''}`} onClick={onOpen} onMouseLeave={() => setArmed(false)}>
      <span className={`dot ${busy ? 'run' : state}`} title={busy ? 'running' : STATE_TIP[state]} />
      <span className="nm">{c.name}</span>
      {c.template && c.template !== 'counter' && <span className="tag">{c.template.replace('_', ' ')}</span>}
      <span className="acts">
        {state !== 'unbuilt' && (
          <a className="act" href={`${BASE}/${c.name}/`} target="_blank" rel="noreferrer" title="open its app" onClick={(e) => e.stopPropagation()}>
            app
          </a>
        )}
        {writable && (
          <button className="act" title="copy into a new contract" onClick={stop(onFork)}>
            fork
          </button>
        )}
        {writable && (
          <button
            className={`act ${armed ? 'danger' : ''}`}
            disabled={busy}
            title="delete this contract"
            onClick={stop(() => (armed ? onRemove() : setArmed(true)))}
          >
            {armed ? 'sure?' : '×'}
          </button>
        )}
      </span>
    </div>
  );
}

function Workbench({
  name,
  contract,
  busy,
  start,
  landed,
  lastJob,
  lastTest,
  editorRef,
  onRemoved,
}: {
  name: string;
  contract: Contract | null;
  busy: boolean;
  start: (fn: string, body: Record<string, unknown>) => Promise<void>;
  landed: string | null;
  lastJob: Job | null;
  lastTest: Job | null;
  editorRef: React.MutableRefObject<{ jump: (d: Diag) => void } | null>;
  onRemoved: () => void;
}) {
  const [tab, setTabState] = useState<Tab>('app');
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

  const setTab = (t: Tab) => {
    setTabState(t);
    try {
      localStorage.setItem(TAB_KEY, t);
    } catch {
      // not remembered, that's all
    }
  };
  useEffect(() => {
    try {
      const t = localStorage.getItem(TAB_KEY) as Tab | null;
      if (t && TABS.some((x) => x.id === t)) setTabState(t);
    } catch {
      // default tab
    }
  }, []);

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

  // a jump requested before its file (or the CODE tab) was open lands once
  // the file loads and the editor is mounted
  useEffect(() => {
    const d = pending.current;
    if (d && tab === 'code' && d.path === path && content === saved) {
      pending.current = null;
      requestAnimationFrame(() => ed.current?.jump(d.line, d.col));
    }
  }, [tab, path, content, saved]);

  const open = (d: Diag) => {
    if (tab === 'code' && d.path === path) return ed.current?.jump(d.line, d.col);
    pending.current = d;
    setTab('code');
    setPath(d.path);
  };
  editorRef.current = { jump: open };

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
        <nav className="tabs">
          {TABS.map((t) => (
            <button key={t.id} className={`tab ${tab === t.id ? 'on' : ''}`} title={t.tip} onClick={() => setTab(t.id)}>
              {t.label}
              {t.id === 'code' && dirty && <i className="tdot" title="unsaved" />}
              {t.id === 'test' && lastTest && <i className={`tdot ${jobOk(lastTest) === false ? 'bad' : 'ok'}`} />}
            </button>
          ))}
        </nav>

        {tab === 'app' && <AppTab name={name} state={state} landed={landed} />}

        {tab === 'api' && <ApiTab name={name} state={state} abi={abi} />}

        {tab === 'code' && (
          <div className="codepane">
            <div className="filetabs" role="tablist">
              {files.map((f) => {
                const cut = f.lastIndexOf('/') + 1;
                return (
                  <button key={f} title={f} className={`ftab ${path === f ? 'on' : ''}`} onClick={() => setPath(f)}>
                    {cut > 0 && <span className="dir">{f.slice(0, cut)}</span>}
                    {f.slice(cut)}
                    {path === f && dirty && <i className="tdot" title="unsaved" />}
                  </button>
                );
              })}
            </div>
            <Editor ref={ed} value={content} path={path} marks={marks} onChange={setContent} onSave={save} />
            <div className="codebar">
              <span className="muted">{dirty ? 'unsaved · Ctrl+S' : msg}</span>
              {dirty && (
                <button className="btn" onClick={() => setContent(saved)}>
                  Discard
                </button>
              )}
              <button className="btn" disabled={!dirty || busy} onClick={save}>
                Save
              </button>
            </div>
          </div>
        )}

        {tab === 'test' && (
          <TestTab
            files={files.filter((f) => f.startsWith('tests/'))}
            job={lastTest}
            busy={busy}
            onRun={() => run('test')}
            onOpen={(f) => {
              setPath(f);
              setTab('code');
            }}
            onJump={open}
          />
        )}
      </section>

      {tab === 'code' && (
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
      )}
    </>
  );
}

// APP: the contract's own page (src/mods/<name>/app/index.html or the shared
// ABI-driven default), framed. Reloads when a job lands so a rebuild shows.
function AppTab({ name, state, landed }: { name: string; state: BuildState; landed: string | null }) {
  const url = `${BASE}/${name}/`;
  if (state === 'unbuilt') return <div className="muted">Build the contract first. Its app is generated from the compiled ABI.</div>;
  return (
    <div className="col">
      <div className="framebar">
        <span className="url">
          <i />
          {url}
        </span>
        <span className="muted small">live on the local devnet</span>
        <span className="spacer" />
        <a className="btn ghostbtn" href={url} target="_blank" rel="noreferrer">
          Open ↗
        </a>
      </div>
      <iframe key={landed ?? 'first'} className="appframe" src={url} title={`${name} app`} />
    </div>
  );
}

// Placeholder for one typed arg, in the shape the server's ABI codec accepts.
function sample(type: string): unknown {
  const t = type.split('::').pop() || type;
  if (/^(u|i)(8|16|32|64|128|256)$|^usize$/.test(t)) return 1;
  if (t === 'bool') return true;
  if (t === 'ContractAddress') return 'bob';
  if (t === 'ByteArray') return 'hello';
  if (t.startsWith('Span') || t.startsWith('Array')) return '1,2';
  return '0x1';
}

// API: the compiled interface, the deployment, and copy-paste HTTP calls for
// every entrypoint. Calls go through /api/call (open), signed by a devnet account.
function ApiTab({ name, state, abi }: { name: string; state: BuildState; abi: AbiContract[] | null }) {
  const [dep, setDep] = useState<any>(null);
  const [copied, setCopied] = useState('');
  const origin = typeof window !== 'undefined' ? window.location.origin : '';
  const api = `${origin}${BASE}/api`;

  useEffect(() => {
    if (state === 'unbuilt') return;
    call('app', undefined, { name }).then((r) => setDep(r.deployment || null)).catch(() => {});
  }, [name, state]);

  const copy = (s: string) => {
    navigator.clipboard?.writeText(s).then(() => setCopied(s)).catch(() => {});
    setTimeout(() => setCopied(''), 1200);
  };
  const post = (fn: string, body: object) =>
    `curl -s ${api}/${fn} -H 'Content-Type: application/json' -d '${JSON.stringify(body)}'`;

  const fns = (abi || []).flatMap((c) => c.functions);
  const lines: { label: string; cmd: string }[] = [
    { label: 'abi', cmd: `curl -s '${api}/abi?name=${name}'` },
    ...fns.map((f) => ({
      label: `${f.mutability === 'view' ? 'read' : 'write'} ${f.name}`,
      cmd: post('call', {
        name,
        fn: f.name,
        ...(f.inputs.length ? { args: Object.fromEntries(f.inputs.map((i) => [i.name, sample(i.type)])) } : {}),
        ...(f.mutability === 'view' ? {} : { account: 'alice' }),
      }),
    })),
    { label: 'events', cmd: `curl -s '${api}/events?name=${name}'` },
  ];

  return (
    <div className="col">
      <div className="row apimeta">
        <span className="pill">mod cairo.{name}</span>
        {dep?.address ? (
          <span className="pill ok" title={`class ${dep.class_hash}`}>
            {dep.contract} @ {dep.address.slice(0, 10)}…{dep.address.slice(-6)}
          </span>
        ) : (
          state !== 'unbuilt' && <span className="pill">not deployed · use the App tab</span>
        )}
        {state === 'stale' && <span className="muted">from the last build · rebuild to refresh</span>}
      </div>
      {abi && abi.length > 0 ? (
        <>
          <Abi contracts={abi} />
          <details open>
            <summary>HTTP</summary>
            <div className="curls">
              {lines.map((l) => (
                <div className="curl" key={l.label}>
                  <span className="lbl">{l.label}</span>
                  <code>{l.cmd}</code>
                  <button className="idea" onClick={() => copy(l.cmd)}>
                    {copied === l.cmd ? 'copied' : 'copy'}
                  </button>
                </div>
              ))}
            </div>
            <div className="muted">
              Args are typed values by name (u256 as one number, ByteArray as text, addresses or alice/bob/carol/dave). Writes
              are signed by the given sandbox account.
            </div>
          </details>
        </>
      ) : (
        <div className="muted">Build to see the entrypoints, events and size of the compiled class.</div>
      )}
    </div>
  );
}

// TEST: the newest snforge run on this contract (a vibe's test counts too),
// and the files that define the suite.
function TestTab({
  files,
  job,
  busy,
  onRun,
  onOpen,
  onJump,
}: {
  files: string[];
  job: Job | null;
  busy: boolean;
  onRun: () => void;
  onOpen: (f: string) => void;
  onJump: (d: Diag) => void;
}) {
  const r = job?.result ? (job.fn === 'vibe' ? job.result.test : job.result) : null;
  return (
    <div className="split">
      <div className="list">
        {files.length === 0 && <div className="muted">No test files.</div>}
        {files.map((f) => (
          <button key={f} className="item file" onClick={() => onOpen(f)} title="open in Code">
            {f}
          </button>
        ))}
      </div>
      <div className="col" style={{ minWidth: 0 }}>
        <div className="row">
          <button className="btn primary" disabled={busy} onClick={onRun}>
            {busy ? 'Running…' : 'Run tests'}
          </button>
          {job?.finished && (
            <span className="muted">
              last run {new Date(job.finished * 1000).toLocaleTimeString()}
              {job.fn === 'vibe' && ' · after a vibe'}
            </span>
          )}
        </div>
        {r ? (
          <StepView fn="test" r={r} onJump={onJump} />
        ) : job?.error ? (
          <pre className="log">{job.error}</pre>
        ) : (
          <div className="muted">Not tested yet this session. Run the suite to see each test, its gas and any revert reason.</div>
        )}
      </div>
    </div>
  );
}
