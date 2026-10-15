'use client';

import { useEffect, useMemo, useState } from 'react';
import { BASE, call } from './lib';

type Entry = {
  id: string;
  kind: 'dsl' | 'zkvm' | 'library';
  lang: string;
  by: string;
  proof: string;
  homepage: string;
  title: string;
  driven: boolean;
  via: string;
  installed: boolean | null;
  install?: string;
  projects: number;
  templates: string[];
};
type Port = { name: string; framework: string; state: string };
type Statement = { statement: string; frameworks: string[]; projects: Port[] };
type Row = { name: string; framework: string; ok: boolean | null; outputs?: Record<string, unknown> | null; error?: string | null; ms?: number };
type Cmp = { rows: Row[]; agree: boolean; verdict: boolean | null };

const KINDS: { id: Entry['kind']; label: string; tip: string }[] = [
  { id: 'dsl', label: 'Circuit languages', tip: 'a language made for writing circuits or provable programs' },
  { id: 'zkvm', label: 'zkVMs', tip: 'prove an ordinary program (Rust, RISC-V, WASM) ran correctly' },
  { id: 'library', label: 'Libraries', tip: 'write circuits as code in a host language (Go, Rust, JS)' },
];

// FRAMEWORKS: every ZK framework zkworld knows, and the statements that tie
// projects in different frameworks together. One statement (say "factors")
// has a port per framework; the same tests/*.json cases are the spec of all
// of them, and compare runs one input through every port side by side.
export function Frameworks({
  writable,
  onOpen,
  start,
  onNew,
}: {
  writable: boolean;
  onOpen: (name: string) => void;
  start: (fn: string, body: Record<string, unknown>) => Promise<void>;
  onNew: () => void;
}) {
  const [cat, setCat] = useState<Entry[] | null>(null);
  const [sts, setSts] = useState<Statement[]>([]);
  const [err, setErr] = useState('');
  // each driven framework is a running submod (zkworld.<id>) with its own page
  const [subs, setSubs] = useState<Record<string, { port: number; running: boolean }>>({});

  useEffect(() => {
    call('submods')
      .then((r) => setSubs(Object.fromEntries((r.frameworks || []).map((f: any) => [f.framework, f]))))
      .catch(() => {});
    call('frameworks')
      .then((r) => (r.error ? setErr(r.error) : setCat(r.catalog || [])))
      .catch(() => setErr('API unreachable'));
    call('statements')
      .then((r) => setSts(r.statements || []))
      .catch(() => {});
  }, []);

  const driven = (cat || []).filter((c) => c.driven);

  return (
    <div className="fleet fws">
      <div className="fhead">
        <div>
          <h2>Every ZK framework, one workbench</h2>
          <p className="muted">
            {cat ? `${cat.length} frameworks. ${driven.length} are driven directly (build, test, run). ` : ''}
            The rest plug in through the runner protocol: the project's <code>config.json</code> names its build and run commands, and its runner
            answers <code>--abi</code> and <code>input.json</code>. Every project shares one test format, <code>tests/*.json</code>, so the same
            statement can be checked in every framework.
          </p>
        </div>
        <button className="btn" onClick={onNew} title="start a project in any framework">
          New project
        </button>
      </div>

      {err && <p className="err">{err}</p>}

      {sts.length > 0 && (
        <section className="fwsec">
          <h3>Statements across frameworks</h3>
          {sts.map((s) => (
            <StatementCard key={s.statement} s={s} cat={cat || []} writable={writable} onOpen={onOpen} start={start} />
          ))}
        </section>
      )}

      {KINDS.map((k) => (
        <section className="fwsec" key={k.id}>
          <h3 title={k.tip}>
            {k.label} <span className="muted small">{k.tip}</span>
          </h3>
          <div className="fwgrid">
            {cat === null && [0, 1, 2].map((i) => <div className="frow ghost" key={i} />)}
            {(cat || [])
              .filter((c) => c.kind === k.id)
              .map((c) => (
                <a
                  className={`fwcard ${c.driven ? 'driven' : ''}`}
                  key={c.id}
                  href={subs[c.id] ? `${BASE}/${c.id}/` : c.homepage}
                  target="_blank"
                  rel="noreferrer"
                  title={subs[c.id] ? `zkworld.${c.id}: its page, toolchain and projects` : c.install || c.homepage}
                >
                  <div className="fline">
                    <b>{c.title}</b>
                    <span className={`fwchip ${c.id}`}>{c.id}</span>
                  </div>
                  <div className="muted small">
                    {c.lang} · {c.by}
                  </div>
                  <div className="small">{c.proof}</div>
                  <div className="fnchips">
                    {c.driven ? (
                      c.installed ? <i className="t">installed</i> : <i className="u">not installed</i>
                    ) : (
                      <i>runner protocol</i>
                    )}
                    {c.projects > 0 && <i>{c.projects} here</i>}
                    {subs[c.id] && (
                      <i className={subs[c.id].running ? 't' : 'u'}>
                        zkworld.{c.id} {subs[c.id].running ? `:${subs[c.id].port}` : 'down'}
                      </i>
                    )}
                  </div>
                </a>
              ))}
          </div>
        </section>
      ))}
    </div>
  );
}

function StatementCard({
  s,
  cat,
  writable,
  onOpen,
  start,
}: {
  s: Statement;
  cat: Entry[];
  writable: boolean;
  onOpen: (name: string) => void;
  start: (fn: string, body: Record<string, unknown>) => Promise<void>;
}) {
  // input names: the union over every port's ABI (circom computes c, the
  // others take it in, so a full claim is {a, b, c})
  const [names, setNames] = useState<string[]>([]);
  const [vals, setVals] = useState<Record<string, string>>({});
  const [cmp, setCmp] = useState<Cmp | null>(null);
  const [busy, setBusy] = useState(false);
  const [target, setTarget] = useState('');

  useEffect(() => {
    Promise.all(s.projects.map((p) => call('abi', undefined, { name: p.name }).catch(() => ({}))))
      .then((abis) => {
        const seen: string[] = [];
        abis.forEach((a: any) =>
          (a.circuits || []).slice(0, 1).forEach((c: any) =>
            [...(c.inputs || []), ...(c.outputs || [])].forEach((x: any) => {
              if (x.name && x.name !== 'return' && !seen.includes(x.name)) seen.push(x.name);
            }),
          ),
        );
        setNames(seen);
      });
  }, [s]);

  const missing = useMemo(() => cat.filter((c) => !s.frameworks.includes(c.id)), [cat, s.frameworks]);

  const run = async () => {
    setBusy(true);
    try {
      const input = Object.fromEntries(Object.entries(vals).filter(([, v]) => v.trim() !== ''));
      setCmp(await call('compare', { statement: s.statement, input }));
    } finally {
      setBusy(false);
    }
  };

  const src = s.projects[0]?.name;

  return (
    <article className="frow stcard">
      <div className="glyph">{s.statement.slice(0, 1).toUpperCase()}</div>
      <div className="fmain">
        <div className="fline">
          <b>{s.statement}</b>
          <span className="muted small">
            {s.projects.length} port{s.projects.length === 1 ? '' : 's'}, one set of test cases
          </span>
        </div>
        <div className="fnchips">
          {s.projects.map((p) => (
            <button className="proj" key={p.name} onClick={() => onOpen(p.name)} title={`open ${p.name} (${p.state})`}>
              <b>{p.name}</b>
              <i>{p.framework}</i>
            </button>
          ))}
        </div>
        <div className="stform">
          {names.map((n) => (
            <label key={n}>
              <span>{n}</span>
              <input value={vals[n] || ''} placeholder="number" onChange={(e) => setVals({ ...vals, [n]: e.target.value })} onKeyDown={(e) => e.key === 'Enter' && run()} />
            </label>
          ))}
          <button className="btn primary" disabled={busy} onClick={run} title="run these inputs through every port">
            {busy ? 'Running…' : 'Check in every framework'}
          </button>
        </div>
        {cmp && (
          <div className="cmp">
            <div className={`verdict ${cmp.agree ? (cmp.verdict ? 'ok' : 'bad') : 'warn'}`}>
              {cmp.agree
                ? cmp.verdict
                  ? 'Every framework accepts this claim.'
                  : 'Every framework rejects this claim.'
                : 'The frameworks disagree. One of the ports does not prove the same thing.'}
            </div>
            <table className="cmptab">
              <tbody>
                {cmp.rows.map((r) => (
                  <tr key={r.name}>
                    <td>
                      <span className={`fwchip ${r.framework}`}>{r.framework}</span>
                    </td>
                    <td className={r.ok ? 'ok' : r.ok === false ? 'bad' : 'muted'}>{r.ok ? 'holds' : r.ok === false ? 'rejected' : 'skipped'}</td>
                    <td className="muted small">
                      {r.ok && r.outputs && Object.keys(r.outputs).length
                        ? Object.entries(r.outputs)
                            .map(([k, v]) => `${k} = ${v}`)
                            .join(', ')
                        : r.error || ''}
                    </td>
                    <td className="muted small">{r.ms != null ? `${r.ms} ms` : ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {writable && src && missing.length > 0 && (
          <div className="stform">
            <select value={target} onChange={(e) => setTarget(e.target.value)}>
              <option value="">port it to another framework…</option>
              {missing.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title} ({c.id}){c.driven ? '' : ' · runner protocol'}
                </option>
              ))}
            </select>
            <button
              className="btn"
              disabled={!target}
              onClick={() => start('port_to', { src, framework: target }).then(() => setTarget(''))}
              title="the agent rewrites it in that framework, held to the same test cases"
            >
              Port
            </button>
          </div>
        )}
      </div>
    </article>
  );
}
