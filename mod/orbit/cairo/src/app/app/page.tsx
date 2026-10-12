'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

const BASE = process.env.NEXT_PUBLIC_BASE ?? '/cairo';
// Self-calls go to /cairo/_api/* — the gateway KEEPS this prefix (routed to the
// app), and src/api/server.py maps _api onto the same handlers as /cairo/api.
const API = `${BASE}/_api`;

type Contract = { name: string; built: boolean };
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

async function call(fn: string, body?: Record<string, unknown>, query?: Record<string, string>): Promise<any> {
  const qs = query ? `?${new URLSearchParams(query)}` : '';
  const res = await fetch(`${API}/${fn}${qs}`, {
    method: body ? 'POST' : 'GET',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  return res.json();
}

// Human-readable output for a finished job.
function jobText(j: Job): string {
  if (j.error) return j.error;
  const r = j.result;
  if (!r) return '';
  if (r.error) return r.error;
  if (j.fn === 'vibe') {
    const parts = [r.result || ''];
    if (r.build) parts.push(`\n── scarb build (${r.build.ok ? 'ok' : 'FAILED'}) ──\n${r.build.log || r.build.error || ''}`);
    if (r.test) parts.push(`\n── snforge test (${r.test.ok ? 'ok' : 'FAILED'}) ──\n${r.test.log || r.test.error || ''}`);
    return parts.join('\n');
  }
  if (typeof r.log === 'string') return r.log;
  return JSON.stringify(r, null, 2);
}

function jobOk(j: Job): boolean | null {
  if (j.state !== 'done') return j.state === 'error' ? false : null;
  const r = j.result || {};
  if (r.error) return false;
  if (j.fn === 'vibe') return !!r.ok && (r.build?.ok ?? true) && (r.test?.ok ?? true);
  return r.ok ?? true;
}

function secs(j: Job): string {
  if (!j.started) return '';
  return `${Math.round((j.finished ?? Date.now() / 1000) - j.started)}s`;
}

export default function Page() {
  const [tools, setTools] = useState<Record<string, any>>({});
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [shown, setShown] = useState<string | null>(null);
  const [err, setErr] = useState('');

  const refresh = useCallback(async () => {
    try {
      const r = await call('list');
      setContracts(r.contracts || []);
      setSel((s) => s ?? r.contracts?.[0]?.name ?? null);
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
  }, [refresh, loadJobs]);

  // Poll while anything is in flight; refresh the catalog when a job lands
  // (build state, vibe-written files).
  const active = jobs.some((j) => j.state === 'queued' || j.state === 'running');
  useEffect(() => {
    if (!active) return;
    const t = setInterval(async () => {
      const js = await loadJobs();
      if (!js.some((j) => j.state === 'queued' || j.state === 'running')) refresh();
    }, 2000);
    return () => clearInterval(t);
  }, [active, loadJobs, refresh]);

  const start = async (fn: string, body: Record<string, unknown>) => {
    setErr('');
    const r = await call(fn, { ...body, async: true });
    if (r.error) return setErr(r.error);
    setShown(r.job.id);
    loadJobs();
  };

  const busy = (name: string | null) =>
    jobs.some((j) => j.name === name && (j.state === 'queued' || j.state === 'running'));

  const shownJob = jobs.find((j) => j.id === shown) || null;

  return (
    <div className="wrap">
      <header className="top">
        <div className="mark">C</div>
        <div>
          <h1>cairo</h1>
          <p className="sub">make, vibecode and test Cairo contracts — each one its own submod</p>
        </div>
        <div className="tools">
          {['scarb', 'snforge', 'claude'].map((t) => (
            <span key={t} className={`pill ${tools[t]?.path ? 'ok' : tools[t] ? 'bad' : ''}`}>
              {t} {tools[t]?.version?.split(' ')[1] ?? (tools[t]?.path ? 'ok' : tools[t] ? 'missing' : '…')}
            </span>
          ))}
        </div>
      </header>

      {err && <p className="err">{err}</p>}

      <div className="grid">
        <aside>
          <section className="card">
            <h2>Contracts</h2>
            <div className="list">
              {contracts.length === 0 && <div className="muted">None yet.</div>}
              {contracts.map((c) => (
                <button key={c.name} className={`item ${sel === c.name ? 'on' : ''}`} onClick={() => setSel(c.name)}>
                  <span className={`dot ${c.built ? 'ok' : ''}`} title={c.built ? 'built' : 'not built'} />
                  {c.name}
                  {busy(c.name) && <span className="muted" style={{ marginLeft: 'auto' }}>…</span>}
                </button>
              ))}
            </div>
          </section>
          <NewContract
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
              busy={busy(sel)}
              start={start}
              jobs={jobs}
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
                  {shownJob.fn} {shownJob.name} · {shownJob.state} {secs(shownJob)}
                </span>
              )}
            </h2>
            {shownJob ? (
              shownJob.state === 'done' || shownJob.state === 'error' ? (
                <Log text={jobText(shownJob) || '(no output)'} />
              ) : (
                <div className="muted">
                  {shownJob.state === 'queued' ? 'Waiting for another run on this contract…' : `Running ${shownJob.fn}…`}
                  {shownJob.fn === 'vibe' && ' The agent edits, builds and tests; this takes a few minutes.'}
                </div>
              )
            ) : (
              <div className="muted">Build, test or vibe a contract to see its output here.</div>
            )}
            {jobs.length > 0 && (
              <div style={{ marginTop: 10 }}>
                {jobs.slice(0, 8).map((j) => (
                  <div className="job" key={j.id}>
                    <span className={`state ${j.state}`}>{j.state}</span>
                    <span>
                      {j.fn} {j.name}
                    </span>
                    <span className="muted">{secs(j)}</span>
                    <button className="btn" onClick={() => setShown(j.id)} disabled={shown === j.id}>
                      view
                    </button>
                  </div>
                ))}
              </div>
            )}
          </section>
        </main>
      </div>
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

function NewContract({ onDone }: { onDone: (name: string) => void }) {
  const [name, setName] = useState('');
  const [desc, setDesc] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const make = async () => {
    setBusy(true);
    setErr('');
    try {
      const r = await call('new', { name, desc: desc || undefined });
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
        <input placeholder="name, e.g. vault" value={name} onChange={(e) => setName(e.target.value)} />
        <input placeholder="what it does (optional)" value={desc} onChange={(e) => setDesc(e.target.value)} />
        <button className="btn primary" disabled={!name.trim() || busy} onClick={make}>
          {busy ? 'Making…' : 'Make'}
        </button>
        {err && <div className="err">{err}</div>}
      </div>
    </section>
  );
}

function Workbench({
  name,
  busy,
  start,
  jobs,
  onRemoved,
}: {
  name: string;
  busy: boolean;
  start: (fn: string, body: Record<string, unknown>) => Promise<void>;
  jobs: Job[];
  onRemoved: () => void;
}) {
  const [files, setFiles] = useState<string[]>([]);
  const [path, setPath] = useState<string | null>(null);
  const [content, setContent] = useState('');
  const [saved, setSaved] = useState('');
  const [prompt, setPrompt] = useState('');
  const [armed, setArmed] = useState(false);
  const [msg, setMsg] = useState('');

  const loadTree = useCallback(async () => {
    const r = await call('tree', undefined, { name });
    const fs: string[] = r.files || [];
    setFiles(fs);
    setPath((p) => (p && fs.includes(p) ? p : fs.find((f) => f === 'src/lib.cairo') ?? fs[0] ?? null));
  }, [name]);

  const loadFile = useCallback(async () => {
    if (!path) return;
    const r = await call('read', undefined, { name, path });
    setContent(r.content ?? '');
    setSaved(r.content ?? '');
  }, [name, path]);

  useEffect(() => {
    loadTree();
  }, [loadTree]);
  useEffect(() => {
    loadFile();
  }, [loadFile]);

  // A vibe run rewrites files on disk: reload the tree and the open file when
  // one for this contract lands (unless there are unsaved edits).
  const lastDone = jobs.find((j) => j.name === name && j.fn === 'vibe' && j.state === 'done')?.id;
  const seen = useRef(lastDone);
  useEffect(() => {
    if (lastDone && lastDone !== seen.current) {
      seen.current = lastDone;
      loadTree();
      if (content === saved) loadFile();
    }
  }, [lastDone, loadTree, loadFile, content, saved]);

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

  return (
    <>
      <section className="card">
        <h2>
          {name}
          <span className="spacer" />
          <button className="btn" disabled={busy} onClick={() => start('build', { name })}>
            Build
          </button>
          <button className="btn" disabled={busy} onClick={() => start('test', { name })}>
            Test
          </button>
          <button className="btn" disabled={busy} onClick={remove} onMouseLeave={() => setArmed(false)}>
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
            <textarea
              className="code"
              spellCheck={false}
              value={content}
              onChange={(e) => setContent(e.target.value)}
              onKeyDown={(e) => {
                if ((e.metaKey || e.ctrlKey) && e.key === 's') {
                  e.preventDefault();
                  save();
                }
              }}
            />
            <div className="row">
              <button className="btn primary" disabled={!dirty || busy} onClick={save}>
                Save
              </button>
              {dirty && (
                <button className="btn" onClick={() => setContent(saved)}>
                  Discard
                </button>
              )}
              <span className="muted">{dirty ? 'unsaved changes' : msg}</span>
            </div>
          </div>
        </div>
      </section>

      <section className="card">
        <h2>Vibecode</h2>
        <div className="col">
          <textarea
            rows={3}
            placeholder={`Tell the agent what to change in ${name}, e.g. "make it an owner-only u256 counter with increment and reset"`}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
          />
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
                : 'Runs Claude on this contract only, then builds and tests it. Uses the host Claude account.'}
            </span>
          </div>
        </div>
      </section>
    </>
  );
}
