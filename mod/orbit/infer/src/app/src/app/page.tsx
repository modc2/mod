'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { API, api, fmtB, num } from '@/lib/api';
import { makeTensor, ort } from '@/lib/ort';

type Tab = 'models' | 'optimize' | 'browser' | 'passes' | 'about';

const TABS: Tab[] = ['models', 'optimize', 'browser', 'passes', 'about'];
const TAB_LABEL: Record<Tab, string> = {
  models: 'models', optimize: 'optimize', browser: 'run in browser',
  passes: 'passes', about: 'about',
};

type OptOut =
  | { kind: 'idle' }
  | { kind: 'note'; text: string }
  | { kind: 'plan'; plan: any }
  | { kind: 'error'; msg: string }
  | { kind: 'result'; r: any };

type WebOut =
  | { kind: 'idle' }
  | { kind: 'note'; text: string }
  | { kind: 'error'; msg: string }
  | { kind: 'result'; server: any; browser: any };

export default function Console() {
  const [tab, setTab] = useState<Tab>('models');
  const [head, setHead] = useState('reading the store…');
  const [headErr, setHeadErr] = useState('');
  const [models, setModels] = useState<any[]>([]);
  const [passes, setPasses] = useState<Record<string, any>>({});
  const [passOrder, setPassOrder] = useState('');
  const [chosen, setChosen] = useState<string[]>(['slim', 'extended']);
  const [health, setHealth] = useState<any>(null);

  const [optModel, setOptModel] = useState('');
  const [optBatch, setOptBatch] = useState(1);
  const [optRuns, setOptRuns] = useState(30);
  const [optBusy, setOptBusy] = useState(false);
  const [optOut, setOptOut] = useState<OptOut>({ kind: 'idle' });

  const [webModel, setWebModel] = useState('');
  const [webBatch, setWebBatch] = useState(1);
  const [webRuns, setWebRuns] = useState(30);
  const [webBusy, setWebBusy] = useState(false);
  const [webOut, setWebOut] = useState<WebOut>({ kind: 'idle' });

  const [dropText, setDropText] = useState('choose an .onnx file — or drop one here');
  const [dropErr, setDropErr] = useState('');
  const [dropHot, setDropHot] = useState(false);
  const [planting, setPlanting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const loadModels = useCallback(async () => {
    const d = await api('/models');
    const ms = d.models || [];
    setModels(ms);
    setHead(`${d.count} model${d.count === 1 ? '' : 's'} · ${d.dir}`);
    setOptModel(cur => cur && ms.some((m: any) => m.id === cur) ? cur : (ms[0]?.id || ''));
    setWebModel(cur => cur && ms.some((m: any) => m.id === cur) ? cur : (ms[0]?.id || ''));
  }, []);

  useEffect(() => {
    (async () => {
      try {
        await loadModels();
        const d = await api('/passes');
        setPasses(d.passes);
        setPassOrder(d.order || '');
        setHealth(await api('/health'));
      } catch (e: any) {
        setHeadErr(e.message);
      }
    })();
  }, [loadModels]);

  async function upload(f?: File | null) {
    if (!f) return;
    setDropErr('');
    setDropText(`reading ${f.name}…`);
    try {
      const buf = new Uint8Array(await f.arrayBuffer());
      let bin = ''; const CH = 0x8000;
      for (let i = 0; i < buf.length; i += CH)
        bin += String.fromCharCode.apply(null, buf.subarray(i, i + CH) as any);
      await api('/models', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ data: btoa(bin), name: f.name.replace(/\.onnx$/, '') }),
      });
      setDropText('choose an .onnx file — or drop one here');
      loadModels();
    } catch (e: any) {
      setDropText('');
      setDropErr(e.message);
    }
  }

  async function plant() {
    setPlanting(true);
    try { await api('/examples', { method: 'POST' }); await loadModels(); }
    catch (e: any) { alert(e.message); }
    setPlanting(false);
  }

  async function rm(id: string) {
    await api('/models/' + id, { method: 'DELETE' });
    loadModels();
  }

  async function retarget(target: 'local' | 'web') {
    if (!optModel) return;
    const p = await api(`/plan?model=${optModel}&target=${target}`);
    setChosen(p.plan);
    setOptOut({ kind: 'plan', plan: p });
  }

  async function runOpt() {
    if (!optModel) return;
    setOptBusy(true);
    setOptOut({ kind: 'note', text: 'optimizing, benchmarking both, comparing outputs…' });
    try {
      const r = await api('/optimize', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ model: optModel, passes: chosen.join(','),
          batch: optBatch, runs: optRuns }),
      });
      setOptOut({ kind: 'result', r });
      await loadModels();
    } catch (e: any) {
      setOptOut({ kind: 'error', msg: e.message });
    }
    setOptBusy(false);
  }

  async function runWeb() {
    if (!webModel) return;
    setWebBusy(true);
    const say = (text: string) => setWebOut({ kind: 'note', text });
    try {
      say('benchmarking on the server first, so both sides use the same input shapes…');
      const server = await api(`/bench?model=${webModel}&runs=${webRuns}&batch=${webBatch}`);
      say('loading onnxruntime-web…');
      const o = await ort();
      say('fetching the blob — the same bytes the server just ran…');
      const bytes = new Uint8Array(await (await fetch(`${API}/blob/${webModel}`)).arrayBuffer());
      const t0 = performance.now();
      const sess = await o.InferenceSession.create(bytes, {
        executionProviders: ['wasm'],
        // Disabled on both sides: otherwise this measures what the runtime
        // would have done to the graph at load time, not the graph shipped.
        graphOptimizationLevel: 'disabled',
      });
      const load = performance.now() - t0;
      const feeds: any = {};
      server.inputs.forEach((spec: any, i: number) => { feeds[spec.name] = makeTensor(o, spec, 7 + i); });
      for (let i = 0; i < 5; i++) await sess.run(feeds);
      const times: number[] = [];
      for (let i = 0; i < webRuns; i++) {
        const a = performance.now();
        await sess.run(feeds);
        times.push(performance.now() - a);
      }
      times.sort((x, y) => x - y);
      const p = (q: number) => times[Math.min(times.length - 1, Math.floor(times.length * q))];
      const browser = {
        model: webModel, name: server.name, ms: {
          p50: +p(.5).toFixed(4), p90: +p(.9).toFixed(4), p99: +p(.99).toFixed(4),
          min: +times[0].toFixed(4), max: +times[times.length - 1].toFixed(4) },
        runs: webRuns, batch: webBatch, load_ms: +load.toFixed(1), bytes: bytes.length,
        backend: 'onnxruntime-web/wasm', ua: navigator.userAgent,
        threads: o.env.wasm.numThreads, simd: o.env.wasm.simd !== false,
        cross_origin_isolated: !!self.crossOriginIsolated, at: new Date().toISOString(),
      };
      setWebOut({ kind: 'result', server, browser });
      await api('/report', { method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify(browser) }).catch(() => {});
    } catch (e: any) {
      setWebOut({ kind: 'error', msg: e.message });
    }
    setWebBusy(false);
  }

  const modelOptions = models.map(m => (
    <option key={m.id} value={m.id}>{m.name} · {fmtB(m.bytes)}</option>
  ));

  return (
    <>
      <header>
        <h1>INFER</h1>
        <span className="sub">
          {headErr ? <span className="red">{headErr}</span> : head}
        </span>
        <span style={{ flex: 1 }} />
        <span className="sub">one ONNX binary · <span className="mint">onnxruntime</span> here ·{' '}
          <span className="mint">onnxruntime-web</span> in this tab</span>
      </header>

      <nav>
        {TABS.map(t => (
          <button key={t} className={tab === t ? 'on' : ''} onClick={() => setTab(t)}>
            {TAB_LABEL[t]}
          </button>
        ))}
      </nav>

      <main>
        <section className={tab === 'models' ? 'on' : ''}>
          <div className="panel">
            <h2>add a model</h2>
            <p className="note">Any <code>.onnx</code>. It is parsed before it is stored — ops,
              parameters and shapes are read off the graph, so nothing is taken on the
              filename&apos;s word. Stored under the SHA-256 of its bytes, which is what lets a
              benchmark be tied to the exact model it measured.</p>
            <div className={'drop' + (dropHot ? ' hot' : '')}
              onClick={() => fileRef.current?.click()}
              onDragOver={e => { e.preventDefault(); setDropHot(true); }}
              onDragLeave={() => setDropHot(false)}
              onDrop={e => { e.preventDefault(); setDropHot(false); upload(e.dataTransfer.files[0]); }}>
              {dropErr ? <span className="red">{dropErr}</span> : dropText}
              <input type="file" accept=".onnx" hidden ref={fileRef}
                onChange={e => upload(e.target.files?.[0])} />
            </div>
            <div className="row" style={{ marginTop: 12 }}>
              <button className="ghost" disabled={planting} onClick={plant}>
                {planting ? 'exporting from torch…' : 'plant three examples'}
              </button>
              <span className="dim">an MLP with BatchNorm to fuse, a small CNN, and a
                transformer block</span>
            </div>
          </div>
          <div className="panel">
            <h2>stored</h2>
            {models.length ? (
              <table><tbody>
                <tr><th>name</th><th>arch</th><th>size</th><th>nodes</th><th>params</th>
                  <th>from</th><th>id</th><th></th></tr>
                {models.map(m => (
                  <tr key={m.id}>
                    <td><b>{m.name}</b>{m.passes ? <> <span className="tag">{m.passes.join('+')}</span></> : null}</td>
                    <td className="dim">{m.arch || '?'}</td>
                    <td className="num">{fmtB(m.bytes)}</td>
                    <td className="num">{num(m.nodes)}</td>
                    <td className="num">{num(m.params)}</td>
                    <td className="dim">{m.parent || m.source || ''}</td>
                    <td className="dim">{m.id}</td>
                    <td><button className="ghost" onClick={() => rm(m.id)}>×</button></td>
                  </tr>
                ))}
              </tbody></table>
            ) : (
              <p className="note">nothing stored yet — drop an .onnx above, or plant the examples.</p>
            )}
          </div>
        </section>

        <section className={tab === 'optimize' ? 'on' : ''}>
          <div className="panel">
            <h2>optimize</h2>
            <p className="note">Every pass is measured against the model it replaced: same
              process, same inputs, same seed. The result is benchmarked, its outputs are
              compared against the original&apos;s, and its portability is re-checked — because
              the fastest local graph is often the one that stopped running anywhere else.</p>
            <div className="row">
              <select value={optModel} onChange={e => setOptModel(e.target.value)}>{modelOptions}</select>
              <button className="ghost" onClick={() => retarget('local')}>target: local</button>
              <button className="ghost" onClick={() => retarget('web')}>target: web</button>
              <label className="dim">batch <input type="number" min={1} value={optBatch}
                style={{ width: 64 }} onChange={e => setOptBatch(+e.target.value)} /></label>
              <label className="dim">runs <input type="number" min={1} value={optRuns}
                style={{ width: 70 }} onChange={e => setOptRuns(+e.target.value)} /></label>
              <button className="act" disabled={optBusy} onClick={runOpt}>
                {optBusy ? 'running…' : 'optimize'}
              </button>
            </div>
            <div className="row" style={{ marginTop: 12 }}>
              {Object.keys(passes).map(k => (
                <button key={k} className={'ghost' + (chosen.includes(k) ? ' on' : '')}
                  onClick={() => setChosen(c => c.includes(k) ? c.filter(x => x !== k) : [...c, k])}>
                  {k}
                </button>
              ))}
              <span className="dim">click to toggle · order is left to right</span>
            </div>
            <OptResult out={optOut} />
          </div>
        </section>

        <section className={tab === 'browser' ? 'on' : ''}>
          <div className="panel">
            <h2>the same bytes, in this tab</h2>
            <p className="note">This fetches <code>/blob/&lt;id&gt;</code> — the exact bytes the
              server just benchmarked — into <b>onnxruntime-web</b> and runs it here, with the
              input shapes the server used and graph optimization disabled on both sides. No
              re-export, no second conversion. If a pass fused the model into onnxruntime&apos;s
              private operator domain, this is where it fails to load, which is the point.</p>
            <div className="row">
              <select value={webModel} onChange={e => setWebModel(e.target.value)}>{modelOptions}</select>
              <label className="dim">batch <input type="number" min={1} value={webBatch}
                style={{ width: 64 }} onChange={e => setWebBatch(+e.target.value)} /></label>
              <label className="dim">runs <input type="number" min={1} value={webRuns}
                style={{ width: 70 }} onChange={e => setWebRuns(+e.target.value)} /></label>
              <button className="act" disabled={webBusy} onClick={runWeb}>run here and on the server</button>
            </div>
            <WebResult out={webOut} />
          </div>
        </section>

        <section className={tab === 'passes' ? 'on' : ''}>
          <div className="panel">
            <h2>the passes</h2>
            {Object.keys(passes).length ? (
              <>
                <table><tbody>
                  <tr><th>pass</th><th>what it does</th><th>changes the numbers</th>
                    <th>browser-safe</th><th>here</th></tr>
                  {Object.entries(passes).map(([k, v]: [string, any]) => (
                    <tr key={k}>
                      <td><b className="mint">{k}</b></td><td>{v.what}</td>
                      <td>{v.lossy ? <span className="tag warn">lossy</span>
                        : <span className="tag ok">lossless</span>}</td>
                      <td>{v.portable ? <span className="tag ok">yes</span>
                        : <span className="tag bad">local only</span>}</td>
                      <td>{v.available ? <span className="tag ok">available</span>
                        : <span className="tag bad">{v.reason || 'missing'}</span>}</td>
                    </tr>
                  ))}
                </tbody></table>
                <p className="note" style={{ marginTop: 12 }}>{passOrder}</p>
              </>
            ) : 'loading…'}
          </div>
        </section>

        <section className={tab === 'about' ? 'on' : ''}>
          <div className="panel">
            <h2>why one binary</h2>
            <p className="note">A model that has to be converted again on the way to where it runs
              is a model nobody measured. ONNX is the format both runtimes here execute
              directly — <code>onnxruntime</code> on the server, <code>onnxruntime-web</code>{' '}
              (wasm, SIMD, threads when the page is cross-origin isolated, WebGPU where it
              exists) in this tab — so the artifact that was optimized and the artifact that
              ships are the same file, and the architecture it started as stops mattering:
              by the time it is here, a ResNet and a transformer are both just graphs.</p>
            <p className="note">Two results this module will keep telling you, because they are
              true and inconvenient: <b>quantization often makes small graphs slower</b> — the
              dequantize nodes cost more than the narrower weights save — and <b>the{' '}
              <code>all</code> pass produces a binary that cannot leave the machine that made
              it</b>, because it rewrites the graph into <code>com.microsoft.nchwc.*</code>{' '}
              layout operators for that CPU. Both show up in the report rather than in a
              footnote.</p>
            <p className="note">The neighbouring claim — that <i>every</i> operator onnxruntime
              invented breaks a browser — is false, and this tab is how that was found out.{' '}
              <code>extended</code> fuses into <code>FusedConv</code> and{' '}
              <code>BiasGelu</code>, which look just as vendor-specific; both load and run
              here, because the wasm backend registers the contrib domain. So{' '}
              <code>portable</code> gives three answers rather than two, and the static
              check is only ever a prediction. This is the proof.</p>
            {health && (
              <table><tbody>
                <tr><th>onnx</th><td>{health.onnx}</td></tr>
                <tr><th>onnxruntime</th><td>{health.onnxruntime}</td></tr>
                <tr><th>providers</th><td>{(health.providers || []).join(', ')}</td></tr>
                <tr><th>passes here</th><td>{(health.passes || []).join(', ')}</td></tr>
                <tr><th>store</th><td>{health.store}</td></tr>
              </tbody></table>
            )}
          </div>
        </section>
      </main>
    </>
  );
}

function OptResult({ out }: { out: OptOut }) {
  if (out.kind === 'idle') return null;
  if (out.kind === 'note') return <p className="note">{out.text}</p>;
  if (out.kind === 'error') return <div className="banner">{out.msg}</div>;
  if (out.kind === 'plan') {
    const p = out.plan;
    return (
      <div className="panel" style={{ marginTop: 14 }}>
        <h2>plan · {p.arch} · target {p.target}</h2>
        <ul className="note" style={{ maxWidth: '104ch' }}>
          {p.why.map((w: string, i: number) => <li key={i}>{w}</li>)}
        </ul>
      </div>
    );
  }
  const r = out.r;
  const lost = r.portability_lost;
  const sp = r.speed || {}, sz = r.size || {}, pa = r.parity || {};
  return (
    <>
      <div className={'verdict' + (lost || (sp.speedup && sp.speedup < 1) ? ' warn' : '')}>{r.verdict}</div>
      {lost && (
        <div className="banner"><b>portability lost.</b> {lost.note}<br />
          ops: {Object.entries(lost.ops).map(([k, v]) => `${k}×${v}`).join(', ')}<br />
          <code>{lost.instead}</code></div>
      )}
      <div className="cols" style={{ marginTop: 14 }}>
        <div className="panel"><h2>size</h2>
          <div className="big">{sz.ratio ? sz.ratio.toFixed(2) + '×' : '—'}{' '}
            <span className="lab">smaller</span></div>
          <div className="dim">{fmtB(sz.before)} → {fmtB(sz.after)} ({sz.percent}% off)</div>
        </div>
        <div className="panel"><h2>latency p50</h2>
          <div className={'big ' + (sp.speedup >= 1 ? 'mint' : 'amber')}>
            {sp.speedup ? sp.speedup.toFixed(2) + '×' : '—'}{' '}
            <span className="lab">faster</span></div>
          <div className="dim">{sp.before_ms_p50 ?? '—'} ms → {sp.after_ms_p50 ?? '—'} ms
            {' '}· {sp.runs || 0} runs · {sp.provider || ''}</div>
        </div>
      </div>
      <div className="panel"><h2>did the answers survive</h2>
        <div className="row">
          <div><span className="lab">max abs error</span><br />
            <span className="big">{pa.max_abs_err !== undefined
              ? pa.max_abs_err.toExponential(2) : '—'}</span></div>
          <div style={{ marginLeft: 26 }}><span className="lab">argmax agreement</span><br />
            <span className="big">{pa.argmax_agreement !== null && pa.argmax_agreement !== undefined
              ? (pa.argmax_agreement * 100).toFixed(0) + '%' : '—'}</span></div>
          <div style={{ marginLeft: 26 }}><span className="lab">cosine</span><br />
            <span className="big">{pa.cosine ?? '—'}</span></div>
          <div style={{ marginLeft: 26 }}><span className="lab">verdict</span><br />
            <span className={'big ' + (pa.ok ? 'mint' : 'amber')}>{pa.verdict || '—'}</span></div>
        </div>
      </div>
      <div className="panel"><h2>what each pass did</h2>
        <table><tbody>
          <tr><th>pass</th><th>nodes</th><th>size</th><th>ops removed</th><th>note</th></tr>
          {(r.passes || []).map((s: any, i: number) => (
            <tr key={i}>
              <td><b className={s.ok ? 'mint' : 'red'}>{s.pass}</b></td>
              <td>{s.ok ? <>{s.nodes.before} → <b>{s.nodes.after}</b></> : '—'}</td>
              <td>{s.ok ? <>{fmtB(s.bytes.before)} → <b>{fmtB(s.bytes.after)}</b></> : '—'}</td>
              <td className="dim">{s.ok ? Object.entries(s.removed || {}).slice(0, 6)
                .map(([k, v]) => `${k}×${v}`).join(', ') || '—' : ''}</td>
              <td className="dim">{s.note || s.error || ''}</td>
            </tr>
          ))}
        </tbody></table>
      </div>
    </>
  );
}

function WebResult({ out }: { out: WebOut }) {
  if (out.kind === 'idle') return null;
  if (out.kind === 'note') return <p className="note">{out.text}</p>;
  if (out.kind === 'error') return (
    <div className="banner"><b>it did not run here.</b> {out.msg}
      <br /><span className="dim">If this mentions an unknown operator, check{' '}
        <code>portable</code>. Almost always it is the <code>all</code> pass:
        it rewrites the graph into <code>com.microsoft.nchwc.*</code> layout
        operators for the CPU that ran it. <code>slim,extended</code> keeps nearly
        all of the speed and loads here.</span></div>
  );
  const { server, browser } = out;
  return (
    <>
      <div className="verdict">the same binary ran in both runtimes ·
        server p50 <b>{server.ms.p50} ms</b> · this tab p50{' '}
        <b>{browser.ms.p50} ms</b> ·{' '}
        {(browser.ms.p50 / server.ms.p50).toFixed(2)}× the server&apos;s time</div>
      <div className="cols">
        <div className="panel"><h2>onnxruntime · server</h2>
          <div className="big">{server.ms.p50} <span className="lab">ms p50</span></div>
          <div className="dim">p90 {server.ms.p90} · p99 {server.ms.p99} ·{' '}
            {server.provider} · {server.runs} runs</div></div>
        <div className="panel"><h2>onnxruntime-web · this tab</h2>
          <div className="big">{browser.ms.p50} <span className="lab">ms p50</span></div>
          <div className="dim">p90 {browser.ms.p90} · p99 {browser.ms.p99} ·{' '}
            wasm{browser.simd ? '+simd' : ''} ·{' '}
            {browser.cross_origin_isolated ? browser.threads + ' threads'
              : 'single-threaded (page is not cross-origin isolated)'} ·{' '}
            session built in {browser.load_ms} ms</div></div>
      </div>
      <p className="note">{fmtB(browser.bytes)} downloaded and executed unchanged.
        The gap is the runtime, not the model — wasm has no AVX-512 and, without
        cross-origin isolation, no threads either.</p>
    </>
  );
}
