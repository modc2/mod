'use client';
/* TESTS: each chain's native suite as last recorded (GET tests). */
import type { Chain } from '@/lib/api';

type Suite = { at?: number; ok?: boolean; passed?: number; failed?: number; tests?: [string, string][] };

export default function Tests({ chains, chain, tests }: { chains: Chain[]; chain?: Chain; tests: Record<string, Suite> | null }) {
  const ordered = [...chains].sort((a, b) => (a.chain === chain?.chain ? -1 : b.chain === chain?.chain ? 1 : 0));
  const extra = Object.keys(tests || {}).filter((k) => !chains.some((c) => c.chain === k));
  const rows = [...ordered.map((c) => ({ key: c.chain, title: c.title, color: c.color })), ...extra.map((k) => ({ key: k, title: k, color: undefined }))];
  const tot = rows.reduce((a, { key }) => a + (tests?.[key]?.passed || 0), 0);
  const bad = rows.reduce((a, { key }) => a + (tests?.[key]?.failed || 0), 0);
  const last = Math.max(0, ...rows.map(({ key }) => tests?.[key]?.at || 0));
  return (
    <div className="stack fade">
      {rows.length > 0 && (
        <div className="card tsum">
          <div>
            <div className="big" style={{ color: bad ? 'var(--bad)' : 'var(--good)' }}>{tot}<small>/ {tot + bad} passing</small></div>
            <div className="cap">{rows.length} native suites{last ? ` · last ${new Date(last * 1000).toLocaleDateString()}` : ''}</div>
          </div>
          <div className="tbars">
            {rows.map(({ key, title, color }) => {
              const s = tests?.[key];
              const n = (s?.passed || 0) + (s?.failed || 0);
              return (
                <button key={key} className="tbar" onClick={() => document.getElementById(`suite-${key}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>
                  <div className="nm"><span style={{ color }}>{title}</span><span>{s ? `${s.passed ?? 0}/${n}` : '—'}</span></div>
                  <div className="meter"><b style={{ width: n ? `${(100 * (s?.passed || 0)) / n}%` : 0, background: s?.ok ? 'var(--good)' : s ? 'var(--bad)' : 'transparent' }} /></div>
                </button>
              );
            })}
          </div>
        </div>
      )}
      {rows.map(({ key, title, color }) => {
        const s = tests?.[key];
        return (
          <div className={`card${key === chain?.chain ? ' hi' : ''}`} key={key} id={`suite-${key}`} style={{ scrollMarginTop: 80 }}>
            <div className="row" style={{ marginBottom: 12 }}>
              <h3 style={{ margin: 0, color }}>{title}</h3>
              {s ? <>
                <span className={`pill ${s.ok ? 'good' : 'bad'}`}>{s.ok ? 'passing' : 'failing'}</span>
                <span className="pill">{s.passed ?? 0} passed</span>
                {!!s.failed && <span className="pill bad">{s.failed} failed</span>}
              </> : <span className="pill">no run recorded</span>}
              <div className="spacer" />
              {s?.at && <span className="muted tiny mono">{new Date(s.at * 1000).toLocaleString()}</span>}
            </div>
            {s?.tests?.length ? (
              <div className="tgrid">
                {s.tests.map(([r, n]) => (
                  <div key={n} className={`tcase${r === 'PASS' ? '' : ' fail'}`}><i />{n.replace(/_/g, ' ').replace(/^test /, '').replace(/\byuma\b/g, 'zuma')}</div>
                ))}
              </div>
            ) : <div className="muted tiny">run it on the anchor host: <span className="mono">m ztensor/test network={key}</span></div>}
          </div>
        );
      })}
    </div>
  );
}
