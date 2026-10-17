'use client';

import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef } from 'react';
import { highlightLine, isCairoish } from './highlight';

export type Diag = { level: 'error' | 'warning'; message: string; path: string; line: number; col: number };
export type TestRow = { status: 'pass' | 'fail' | 'ignore' | 'skip'; name: string; gas: number | null; reason?: string };
export type AbiFn = {
  name: string;
  interface: string | null;
  mutability: 'view' | 'external';
  inputs: { name: string; type: string }[];
  outputs: string[];
};
export type AbiContract = {
  contract: string;
  constructor: { name: string; type: string }[] | null;
  functions: AbiFn[];
  events: { name: string; fields: { name: string; type: string; key: boolean }[] }[];
  sierra_felts: number;
  casm_felts: number | null;
};

// ---------------------------------------------------------------------------
// Editor: a transparent textarea over a highlighted <pre>, sharing one box
// model so the caret lines up. Lines with compiler errors get a red wash.
// ---------------------------------------------------------------------------

export type EditorHandle = { jump: (line: number, col: number) => void };

export const Editor = forwardRef<
  EditorHandle,
  { value: string; path: string | null; marks: Diag[]; onChange: (v: string) => void; onSave: () => void }
>(function Editor({ value, path, marks, onChange, onSave }, ref) {
  const ta = useRef<HTMLTextAreaElement>(null);
  const pre = useRef<HTMLPreElement>(null);

  const html = useMemo(() => {
    const bad = new Map<number, Diag>();
    for (const d of marks) if (!bad.has(d.line) || d.level === 'error') bad.set(d.line, d);
    const color = isCairoish(path);
    return value
      .split('\n')
      .map((l, i) => {
        const d = bad.get(i + 1);
        const cls = d ? ` ${d.level === 'error' ? 'lerr' : 'lwarn'}` : '';
        const tip = d ? ` title="${d.message.replace(/"/g, '&quot;')}"` : '';
        const body = color ? highlightLine(l) : l.replace(/&/g, '&amp;').replace(/</g, '&lt;');
        return `<div class="l${cls}" data-n="${i + 1}"${tip}>${body || ' '}</div>`;
      })
      .join('');
  }, [value, path, marks]);

  const sync = () => {
    if (pre.current && ta.current) {
      pre.current.scrollTop = ta.current.scrollTop;
      pre.current.scrollLeft = ta.current.scrollLeft;
    }
  };
  useEffect(sync, [html]);

  useImperativeHandle(ref, () => ({
    jump(line, col) {
      const t = ta.current;
      if (!t) return;
      const lines = t.value.split('\n');
      let at = 0;
      for (let i = 0; i < Math.min(line - 1, lines.length); i++) at += lines[i].length + 1;
      at += Math.max(0, col - 1);
      t.focus();
      t.setSelectionRange(at, at);
      const lh = parseFloat(getComputedStyle(t).lineHeight) || 19;
      t.scrollTop = Math.max(0, (line - 6) * lh);
      sync();
    },
  }));

  return (
    <div className="editor">
      <pre className="hl" ref={pre} aria-hidden dangerouslySetInnerHTML={{ __html: html }} />
      <textarea
        ref={ta}
        className="code"
        spellCheck={false}
        value={value}
        onScroll={sync}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === 's') {
            e.preventDefault();
            onSave();
          } else if (e.key === 'Tab' && !e.shiftKey) {
            // indent with spaces instead of leaving the editor
            e.preventDefault();
            const t = e.currentTarget;
            const { selectionStart: a, selectionEnd: b } = t;
            onChange(t.value.slice(0, a) + '    ' + t.value.slice(b));
            requestAnimationFrame(() => t.setSelectionRange(a + 4, a + 4));
          }
        }}
      />
    </div>
  );
});

// ---------------------------------------------------------------------------
// Results
// ---------------------------------------------------------------------------

export function Diagnostics({ diags, onJump }: { diags: Diag[]; onJump: (d: Diag) => void }) {
  if (!diags.length) return null;
  return (
    <div className="diags">
      {diags.map((d, i) => (
        <button key={i} className={`diag ${d.level}`} onClick={() => onJump(d)}>
          <span className="where">
            {d.path}:{d.line}
          </span>
          <span>{d.message}</span>
        </button>
      ))}
    </div>
  );
}

export function TestTable({ tests, totals, seconds }: { tests: TestRow[]; totals: any; seconds?: number }) {
  const max = Math.max(1, ...tests.map((t) => t.gas ?? 0));
  const sorted = [...tests].sort((a, b) => Number(a.status === 'pass') - Number(b.status === 'pass') || a.name.localeCompare(b.name));
  return (
    <div className="tests">
      {totals && (
        <div className="totals">
          <span className={`big ${totals.failed ? 'bad' : 'ok'}`}>
            {totals.failed ? `${totals.failed} failing` : 'all green'}
          </span>
          <span className="pill ok">{totals.passed} passed</span>
          {totals.failed > 0 && <span className="pill bad">{totals.failed} failed</span>}
          {totals.ignored + totals.skipped > 0 && <span className="pill">{totals.ignored + totals.skipped} skipped</span>}
          {seconds != null && <span className="muted">{seconds}s</span>}
        </div>
      )}
      {sorted.map((t) => (
        <div key={t.name} className={`trow ${t.status}`}>
          <span className="tstat">{t.status}</span>
          <span className="tname">{t.name.split('::').pop()}</span>
          <span className="gas" title={t.gas != null ? `~${t.gas} L2 gas` : ''}>
            {t.gas != null && (
              <>
                <span className="track">
                  <i style={{ width: `${Math.max(2, (100 * t.gas) / max)}%` }} />
                </span>
                <b>{t.gas}</b>
              </>
            )}
          </span>
          {t.reason && <span className="reason">{t.reason}</span>}
        </div>
      ))}
    </div>
  );
}

function sig(f: AbiFn): string {
  const args = f.inputs.map((i) => `${i.name}: ${i.type}`).join(', ');
  return `${f.name}(${args})${f.outputs.length ? ` -> ${f.outputs.join(', ')}` : ''}`;
}

export function Abi({ contracts }: { contracts: AbiContract[] }) {
  return (
    <>
      {contracts.map((c) => (
        <div key={c.contract} className="abi">
          <div className="abihead">
            <b>{c.contract}</b>
            <span className="muted">
              Sierra {c.sierra_felts.toLocaleString()} felts{c.casm_felts != null && ` · CASM ${c.casm_felts.toLocaleString()} felts`}
            </span>
          </div>
          {c.constructor && (
            <div className="fn">
              <span className="mut ctor">init</span>
              <code>constructor({c.constructor.map((i) => `${i.name}: ${i.type}`).join(', ')})</code>
            </div>
          )}
          {c.functions.map((f) => (
            <div className="fn" key={f.name}>
              <span className={`mut ${f.mutability}`}>{f.mutability === 'view' ? 'read' : 'write'}</span>
              <code>{sig(f)}</code>
            </div>
          ))}
          {c.events.map((e) => (
            <div className="fn" key={e.name}>
              <span className="mut event">event</span>
              <code>
                {e.name}
                {'{ '}
                {e.fields.map((f) => `${f.key ? '#key ' : ''}${f.name}: ${f.type}`).join(', ')}
                {' }'}
              </code>
            </div>
          ))}
        </div>
      ))}
    </>
  );
}
