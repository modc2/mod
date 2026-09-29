'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { api, fmtB, num } from '@/lib/api';

// The zoo: every ONNX model the server can find, across every source. The
// server does the crawling and keeps the catalog; this only searches it and
// asks for one model to be planted into the store.

const post = (path: string, body: any) => api(path, {
  method: 'POST', headers: { 'content-type': 'application/json' },
  body: JSON.stringify(body),
});

type Plant = { state: 'busy' | 'ok' | 'err'; msg?: string };

function usePlanter(onPlanted: () => void) {
  const [plants, setPlants] = useState<Record<string, Plant>>({});
  const plant = useCallback(async (key: string, file?: string) => {
    const id = file ? `${key}#${file}` : key;
    setPlants(p => ({ ...p, [id]: { state: 'busy' } }));
    try {
      const r = await post('/zoo/plant', { key, file });
      setPlants(p => ({ ...p, [id]: { state: 'ok', msg: `${r.id} · ${fmtB(r.bytes)}` } }));
      onPlanted();
    } catch (e: any) {
      setPlants(p => ({ ...p, [id]: { state: 'err', msg: e.message } }));
    }
  }, [onPlanted]);
  return { plants, plant };
}

function PlantButton({ p, onClick, label = 'plant' }: { p?: Plant; onClick: () => void; label?: string }) {
  if (p?.state === 'ok') return <span className="tag ok" title={p.msg}>planted</span>;
  return (
    <>
      <button className="ghost" disabled={p?.state === 'busy'} onClick={onClick}>
        {p?.state === 'busy' ? 'fetching…' : label}
      </button>
      {p?.state === 'err' && <div className="red" style={{ fontSize: 11, maxWidth: 320 }}>{p.msg}</div>}
    </>
  );
}

// ── the examples row on the models tab ─────────────────────────

export function Examples({ onPlanted }: { onPlanted: () => void }) {
  const [archs, setArchs] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState('');
  const { plants, plant } = usePlanter(onPlanted);

  useEffect(() => {
    api('/zoo/models?source=builtin&sort=source&limit=200')
      .then(d => setArchs(d.models || [])).catch(() => {});
  }, []);

  async function all() {
    setBusy(true); setDone('');
    try {
      const r = await post('/examples', {});
      const bad = Object.keys(r.failed || {});
      setDone(`${r.count} planted` + (bad.length ? ` · ${bad.length} failed: ${bad.join(', ')}` : ''));
      onPlanted();
    } catch (e: any) { setDone(e.message); }
    setBusy(false);
  }

  return (
    <>
      <div className="row" style={{ marginTop: 12 }}>
        <button className="ghost" disabled={busy} onClick={all}>
          {busy ? 'building every architecture…' : `plant all ${archs.length || ''} examples`}
        </button>
        <span className="dim">one small model per architecture family, built here — no download</span>
        {done && <span className="mint">{done}</span>}
      </div>
      {archs.length > 0 && (
        <div className="row" style={{ marginTop: 10, gap: 6 }}>
          {archs.map(a => {
            const p = plants[a.key];
            return (
              <button key={a.key} title={`${a.task} — ${a.about}`}
                className={'ghost' + (p?.state === 'ok' ? ' on' : '')}
                style={{ padding: '3px 9px', fontSize: 12 }}
                disabled={p?.state === 'busy'} onClick={() => plant(a.key)}>
                {p?.state === 'busy' ? '…' : a.name}
              </button>
            );
          })}
        </div>
      )}
    </>
  );
}

// ── the zoo tab ─────────────────────────────────────────────────

const DOMAINS = ['vision', 'text', 'audio', 'multimodal', 'tabular', 'graph',
  'generative', 'operator', 'other'];
const SORTS = ['downloads', 'likes', 'recent', 'name', 'size', 'source'];
const PAGE = 50;

export function Zoo({ onPlanted }: { onPlanted: () => void }) {
  const [status, setStatus] = useState<any>(null);
  const [q, setQ] = useState('');
  const [source, setSource] = useState('');
  const [domain, setDomain] = useState('');
  const [sort, setSort] = useState('downloads');
  const [local, setLocal] = useState(false);
  const [res, setRes] = useState<any>(null);
  const [rows, setRows] = useState<any[]>([]);
  const [err, setErr] = useState('');
  const [open, setOpen] = useState<Record<string, any>>({});
  const [crawl, setCrawl] = useState('');
  const { plants, plant } = usePlanter(onPlanted);
  const seq = useRef(0);

  const loadStatus = useCallback(async () => {
    try { setStatus(await api('/zoo')); } catch (e: any) { setErr(e.message); }
  }, []);

  const search = useCallback(async (offset = 0) => {
    const my = ++seq.current;
    const p = new URLSearchParams({ sort, limit: String(PAGE), offset: String(offset) });
    if (q.trim()) p.set('q', q.trim());
    if (source) p.set('source', source);
    if (domain) p.set('domain', domain);
    if (local) p.set('local', 'true');
    try {
      const d = await api('/zoo/models?' + p.toString());
      if (my !== seq.current) return;
      setErr(''); setRes(d);
      setRows(r => offset ? [...r, ...d.models] : d.models);
    } catch (e: any) { if (my === seq.current) setErr(e.message); }
  }, [q, source, domain, sort, local]);

  useEffect(() => { loadStatus(); }, [loadStatus]);
  useEffect(() => {
    const t = setTimeout(() => search(0), 250);
    return () => clearTimeout(t);
  }, [search]);

  // While any source is crawling, keep the counts moving.
  const running = (status?.running || []).length > 0;
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => { loadStatus(); }, 3000);
    return () => clearInterval(t);
  }, [running, loadStatus]);
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !running) search(0);
    wasRunning.current = running;
  }, [running, search]);

  async function scrape(fresh: boolean) {
    setCrawl(fresh ? 'starting a fresh crawl of every provider…' : 'starting…');
    try {
      const r = await post('/zoo/scrape', { fresh });
      setStatus(r);
      setCrawl(r.started.length ? `crawling ${r.started.join(', ')}` : 'already crawling');
    } catch (e: any) { setCrawl(e.message); }
  }

  async function toggle(key: string) {
    if (open[key]) { setOpen(o => { const n = { ...o }; delete n[key]; return n; }); return; }
    setOpen(o => ({ ...o, [key]: 'loading' }));
    try {
      const d = await api('/zoo/model?key=' + encodeURIComponent(key));
      setOpen(o => ({ ...o, [key]: d }));
    } catch (e: any) { setOpen(o => ({ ...o, [key]: { error: e.message } })); }
  }

  const sources: any[] = status?.sources || [];
  const facetDom = res?.facets?.domain || {};

  return (
    <>
      <div className="panel">
        <h2>the zoo</h2>
        <p className="note">Every ONNX model this box can find, in one list. Three sources
          need no network — every architecture built here, the onnx package&apos;s own
          conformance models, and torchvision&apos;s registry. Four are crawled: the GitHub
          ONNX Model Zoo, every HuggingFace repo tagged onnx, ModelScope and Kaggle.
          Planting downloads (or builds) one file into the store, ready to optimize.</p>
        <div className="row" style={{ gap: 6 }}>
          <button className={'ghost' + (source === '' ? ' on' : '')} onClick={() => setSource('')}>
            all <span className="dim">{num(status?.total)}</span>
          </button>
          {sources.map(s => (
            <button key={s.name} title={`${s.title} — ${s.note}`}
              className={'ghost' + (source === s.name ? ' on' : '')}
              onClick={() => setSource(source === s.name ? '' : s.name)}>
              {s.name} <span className="dim">{num(s.count)}</span>
              {s.running ? <span className="amber"> · crawling</span>
                : s.error ? <span className="red"> · !</span>
                  : !s.complete && s.kind === 'remote' ? <span className="dim"> · not crawled</span> : null}
            </button>
          ))}
        </div>
        <div className="row" style={{ marginTop: 12 }}>
          <button className="act" disabled={running} onClick={() => scrape(false)}>
            {running ? 'crawling…' : 'scrape every provider'}
          </button>
          <button className="ghost" disabled={running} onClick={() => scrape(true)}>re-crawl from scratch</button>
          <span className="dim">{crawl}</span>
        </div>
        {sources.filter(s => s.running || s.error).map(s => (
          <div key={s.name} className="dim" style={{ fontSize: 12, marginTop: 6 }}>
            <b className={s.error && !s.running ? 'red' : 'amber'}>{s.name}</b>{' '}
            {s.running ? (s.log || []).slice(-1)[0] : s.error}
          </div>
        ))}
      </div>

      <div className="panel">
        <div className="row">
          <input placeholder="search — whisper, yolo, bert, resnet, gelu…" value={q}
            style={{ flex: 1, minWidth: 220 }} onChange={e => setQ(e.target.value)} />
          <select value={domain} onChange={e => setDomain(e.target.value)}>
            <option value="">every domain</option>
            {DOMAINS.map(d => <option key={d} value={d}>{d}{facetDom[d] !== undefined ? ` · ${num(facetDom[d])}` : ''}</option>)}
          </select>
          <select value={sort} onChange={e => setSort(e.target.value)}>
            {SORTS.map(s => <option key={s} value={s}>sort: {s}</option>)}
          </select>
          <label className="dim"><input type="checkbox" checked={local}
            onChange={e => setLocal(e.target.checked)} /> no download</label>
        </div>
        {err && <div className="banner">{err}</div>}
        {res && <p className="note" style={{ margin: '10px 0 0' }}>{num(res.count)} models
          {res.facets?.source ? ' · ' + Object.entries(res.facets.source)
            .map(([k, v]) => `${k} ${num(v)}`).join(' · ') : ''}</p>}
        <table><tbody>
          <tr><th>model</th><th>source</th><th>task</th><th>downloads</th><th>size</th><th></th></tr>
          {rows.map(m => {
            const o = open[m.key];
            return [
              <tr key={m.key}>
                <td style={{ maxWidth: 420, wordBreak: 'break-word' }}>
                  <a href="#" onClick={e => { e.preventDefault(); toggle(m.key); }}>
                    <b>{m.name}</b></a>
                  {m.author && m.author !== m.name && <span className="dim"> · {m.author}</span>}
                  {m.n_files > 1 && <span className="tag" style={{ marginLeft: 6 }}>{m.n_files} files</span>}
                  {m.gated && <span className="tag warn" style={{ marginLeft: 6 }}>gated</span>}
                  {m.about && <div className="dim" title={m.about} style={{ fontSize: 11, display: '-webkit-box',
                    WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{m.about}</div>}
                </td>
                <td><span className={'tag' + (m.local ? ' ok' : '')}>{m.source}</span></td>
                <td className="dim">{m.task || m.domain}</td>
                <td className="num">{m.downloads ? num(m.downloads) : m.likes ? `♥ ${num(m.likes)}` : '—'}</td>
                <td className="num">{m.bytes ? fmtB(m.bytes) : <span className="dim">?</span>}</td>
                <td><PlantButton p={plants[m.key]} onClick={() => plant(m.key)} /></td>
              </tr>,
              o && (
                <tr key={m.key + '#open'}>
                  <td colSpan={6} style={{ background: '#0a1017' }}>
                    {o === 'loading' ? <span className="dim">reading the file list…</span>
                      : o.error ? <span className="red">{o.error}</span> : (
                        <>
                          <div className="dim" style={{ fontSize: 12 }}>
                            {o.url && <a href={o.url} target="_blank" rel="noreferrer">{o.url}</a>}
                            {o.license && <> · {o.license}</>}
                            {o.files_error && <span className="red"> · {o.files_error}</span>}
                          </div>
                          <table><tbody>
                            {(o.files || []).filter((f: any) => f.onnx).map((f: any) => (
                              <tr key={f.path}>
                                <td>{f.path}{f.path === o.default_file && <span className="tag ok" style={{ marginLeft: 6 }}>default</span>}</td>
                                <td className="num">{f.bytes ? fmtB(f.bytes) : '?'}</td>
                                <td><PlantButton p={plants[`${m.key}#${f.path}`]}
                                  onClick={() => plant(m.key, f.path)} /></td>
                              </tr>
                            ))}
                          </tbody></table>
                        </>
                      )}
                  </td>
                </tr>
              ),
            ];
          })}
        </tbody></table>
        {res && rows.length < res.count && (
          <div className="row" style={{ marginTop: 12 }}>
            <button className="ghost" onClick={() => search(rows.length)}>
              more ({num(res.count - rows.length)} left)</button>
          </div>
        )}
      </div>
    </>
  );
}
