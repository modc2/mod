'use client';
/* PAPER: two papers, one reader. SIMPLE = PAPER_SIMPLE.md (plain language),
   TECHNICAL = WHITEPAPER.md (LaTeX math via KaTeX). Both come from
   GET whitepaper?kind= and go through the same small markdown renderer:
   $..$ / $$..$$ math, `::: fig <name> | caption` figures (PaperFigs.ts),
   tables, lists, callouts. Contents is a dropdown everywhere; on wide screens
   the reader can pin it as a sticky sidebar (scroll-spy over the h2s). */
import { useEffect, useMemo, useRef, useState } from 'react';
import katex from 'katex';
import 'katex/dist/katex.min.css';
import { get, BASE } from '@/lib/api';
import { figure } from '@/components/PaperFigs';

type Kind = 'simple' | 'technical';
const KINDS: { k: Kind; label: string; sub: string }[] = [
  { k: 'simple', label: 'Simple', sub: 'plain language, no maths' },
  { k: 'technical', label: 'Technical', sub: 'full scheme, LaTeX' },
];

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const DOLLAR = '\u0000';
const tex = (s: string, display: boolean) => {
  try { return katex.renderToString(s.replace(new RegExp(DOLLAR, 'g'), '\\$'), { displayMode: display, throwOnError: false, output: 'html', strict: false }); }
  catch { return `<code>${esc(s)}</code>`; }
};
const text = (s: string) => esc(s)
  .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
  .replace(/(^|[^*\w])\*([^*\s][^*]*)\*/g, '$1<i>$2</i>')
  .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
// code spans and $math$ first, so their contents never see the markdown pass
const inline = (s: string) => s.replace(/\\\$/g, DOLLAR)
  .split(/(`[^`]+`|\$[^$\n]+?\$)/g)
  .map((p, i) => i % 2 === 0 ? text(p).replace(new RegExp(DOLLAR, 'g'), '$')
    : p[0] === '`' ? `<code>${esc(p.slice(1, -1))}</code>` : tex(p.slice(1, -1), false))
  .join('');

const slug = (s: string) => 'p-' + s.toLowerCase().replace(/[`*$\\]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
const plain = (s: string) => s.replace(/[`*]/g, '').replace(/\$([^$]+)\$/g, '$1');

export function md(src: string): string {
  const out: string[] = [];
  const L = src.replace(/\r/g, '').split('\n');
  let nfig = 0;
  // table cells split on |, but not inside $..$ or `..`
  const cells = (row: string) => {
    const r: string[] = []; let cur = '', m = false, c = false;
    for (const ch of row.trim().replace(/^\||\|$/g, '')) {
      if (ch === '$') m = !m; else if (ch === '`') c = !c;
      if (ch === '|' && !m && !c) { r.push(cur.trim()); cur = ''; } else cur += ch;
    }
    return [...r, cur.trim()];
  };
  for (let i = 0; i < L.length; i++) {
    const l = L[i];
    if (l.startsWith('```')) {
      const buf: string[] = [];
      while (++i < L.length && !L[i].startsWith('```')) buf.push(L[i]);
      out.push(`<pre><code>${esc(buf.join('\n'))}</code></pre>`);
    } else if (l.trim().startsWith('$$')) {
      let body = l.trim().slice(2);
      if (!/\$\$\s*$/.test(body)) { while (++i < L.length && !/\$\$\s*$/.test(L[i])) body += '\n' + L[i]; body += '\n' + (L[i] ?? ''); }
      out.push(`<div class="pmath">${tex(body.replace(/\$\$\s*$/, '').trim(), true)}</div>`);
    } else if (/^::: fig /.test(l)) {
      const [name, cap = ''] = l.slice(8).split('|').map((x) => x.trim());
      out.push(figure(name, cap, ++nfig, inline));
    } else if (/^#{1,4} /.test(l)) {
      const n = l.match(/^#+/)![0].length;
      const t = l.slice(n + 1);
      const num = n === 2 ? t.match(/^(\d+)\.\s+/) : null;
      const body = num ? `<span class="hn">${num[1]}</span>${inline(t.slice(num[0].length))}` : inline(t);
      out.push(`<h${n} id="${slug(t)}">${body}</h${n}>`);
    } else if (/^\s*\|/.test(l)) {
      const rows: string[][] = [];
      for (; i < L.length && /^\s*\|/.test(L[i]); i++)
        if (!/^\s*\|[\s:|-]+\|\s*$/.test(L[i])) rows.push(cells(L[i]));
      i--;
      const [h, ...b] = rows;
      out.push(`<div class="tblwrap"><table><thead><tr>${h.map((c) => `<th>${inline(c)}</th>`).join('')}</tr></thead><tbody>${b.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);
    } else if (/^\s*([-*]|\d+\.) /.test(l)) {
      const ol = /^\s*\d+\./.test(l);
      const items: string[] = [];
      for (; i < L.length && /^\s*([-*]|\d+\.) /.test(L[i]); i++) {
        let it = L[i].replace(/^\s*([-*]|\d+\.) /, '');
        while (i + 1 < L.length && /^\s{2,}\S/.test(L[i + 1]) && !/^\s*([-*]|\d+\.) /.test(L[i + 1])) it += ' ' + L[++i].trim();
        items.push(`<li>${inline(it)}</li>`);
      }
      i--;
      out.push(ol ? `<ol>${items.join('')}</ol>` : `<ul>${items.join('')}</ul>`);
    } else if (l.startsWith('>')) {
      const buf: string[] = [];
      for (; i < L.length && L[i].startsWith('>'); i++) buf.push(L[i].replace(/^>\s?/, ''));
      i--;
      out.push(`<blockquote>${inline(buf.join(' '))}</blockquote>`);
    } else if (l.trim()) {
      const buf = [l];
      while (i + 1 < L.length && L[i + 1].trim() && !/^(#|```|\$\$|::: |\s*\||\s*([-*]|\d+\.) |>)/.test(L[i + 1])) buf.push(L[++i]);
      const p = buf.join(' ');
      // the paper's subtitle line (*v0.4 · ...*) and **Proposition.** style run-ins
      if (/^\*[^*].*\*$/.test(p.trim())) out.push(`<p class="pmeta">${inline(p.trim().slice(1, -1))}</p>`);
      else if (/^\*Proof\.\*/.test(p)) out.push(`<p class="pproof">${inline(p)}</p>`);
      else if (/^\*\*(Proposition|Lemma|Theorem)/.test(p)) out.push(`<p class="pthm">${inline(p)}</p>`);
      else out.push(`<p>${inline(p)}</p>`);
    }
  }
  return out.join('\n');
}

type Sec = { id: string; title: string };

export default function Paper() {
  const [kind, setKind] = useState<Kind>('simple');
  const [srcs, setSrcs] = useState<Partial<Record<Kind, string>>>({});
  const [err, setErr] = useState('');
  const [on, setOn] = useState('');
  const [rail, setRail] = useState(true);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLElement>(null);
  const drop = useRef<HTMLDivElement>(null);
  useEffect(() => {
    setRail(localStorage.getItem('ztensor.toc') !== 'off');
    const k = localStorage.getItem('ztensor.paper');
    if (k === 'technical' || k === 'simple') setKind(k);
  }, []);
  const pin = (v: boolean) => { setRail(v); setOpen(false); localStorage.setItem('ztensor.toc', v ? 'on' : 'off'); };
  const pick = (k: Kind) => {
    if (k === kind) return;
    setKind(k); setOn(''); localStorage.setItem('ztensor.paper', k);
    ref.current?.closest('.paperwrap')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };
  useEffect(() => {
    if (!open) return;
    const off = (e: Event) => { if (!drop.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('pointerdown', off);
    document.addEventListener('keydown', esc);
    return () => { document.removeEventListener('pointerdown', off); document.removeEventListener('keydown', esc); };
  }, [open]);
  useEffect(() => {
    if (srcs[kind] !== undefined) return;
    setErr('');
    get('whitepaper', { kind }).then((d) => setSrcs((s) => ({ ...s, [kind]: typeof d === 'string' ? d : d?.text ?? d?.markdown ?? '' })))
      .catch((e) => setErr(e.message));
  }, [kind]); // eslint-disable-line react-hooks/exhaustive-deps
  const src = srcs[kind] ?? '';
  const html = useMemo(() => (src ? md(src) : ''), [src]);
  const secs: Sec[] = useMemo(() => src.split('\n').filter((l) => /^## /.test(l))
    .map((l) => ({ id: slug(l.slice(3)), title: plain(l.slice(3)) })), [src]);
  const mins = (k: Kind) => {
    const s = srcs[k];
    return s ? Math.max(1, Math.round(s.replace(/\$\$[\s\S]*?\$\$/g, ' ').split(/\s+/).length / 230)) : null;
  };
  useEffect(() => {
    const root = ref.current;
    if (!root || !secs.length) return;
    const hs = Array.from(root.querySelectorAll('h2[id]'));
    const io = new IntersectionObserver((es) => {
      const vis = es.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
      if (vis[0]) setOn(vis[0].target.id);
    }, { rootMargin: '-10% 0px -70% 0px' });
    hs.forEach((h) => io.observe(h));
    return () => io.disconnect();
  }, [html]); // eslint-disable-line react-hooks/exhaustive-deps
  const go = (id: string) => { setOpen(false); document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' }); };
  const cur = secs.find((s) => s.id === on);
  const m = mins(kind);
  return (
    <div className={`paperwrap fade${rail ? ' rail' : ''}`}>
      <div className="pkinds" role="tablist" aria-label="Which paper">
        {KINDS.map(({ k, label, sub }) => {
          const mk = mins(k);
          return (
            <button key={k} role="tab" aria-selected={kind === k} className={`pkind${kind === k ? ' on' : ''}`} onClick={() => pick(k)}>
              <span className="pk-l">{label}</span>
              <span className="pk-s">{sub}{mk ? ` · ${mk} min` : ''}</span>
            </button>
          );
        })}
        <div className="pk-act">
          <a className="pk-btn" href={`${BASE}/_api/whitepaper?kind=${kind}`} target="_blank" rel="noreferrer" title="the markdown source">source</a>
          <button className="pk-btn" onClick={() => window.print()} title="print, or save as PDF">print / PDF</button>
        </div>
      </div>
      {secs.length > 0 && (
        <nav className="toc" aria-label="Contents">
          <div className="toc-h">Contents <span>{m} min read</span>
            <button className="toc-x" title="fold the sidebar into a dropdown" onClick={() => pin(false)}>hide</button></div>
          {secs.map((s) => (
            <button key={s.id} className={on === s.id ? 'on' : ''} onClick={() => go(s.id)}>{s.title}</button>
          ))}
        </nav>
      )}
      {secs.length > 0 && (
        <div className="tocdrop" ref={drop}>
          <button className={`tocsel${open ? ' open' : ''}`} aria-expanded={open} onClick={() => setOpen((o) => !o)}>
            <span className="k">Contents</span><span className="cur">{cur?.title ?? `${secs.length} sections · ${m} min read`}</span><i />
          </button>
          <button className="tocpin" title="pin contents as a sidebar" onClick={() => pin(true)}>sidebar</button>
          {open && (
            <div className="tocmenu" role="menu">
              {secs.map((s) => (
                <button key={s.id} role="menuitem" className={on === s.id ? 'on' : ''} onClick={() => go(s.id)}>{s.title}</button>
              ))}
            </div>
          )}
        </div>
      )}
      <div className="card paperc" style={{ padding: 0, minWidth: 0 }}>
        {err ? <div className="err" style={{ margin: 24 }}>{err}</div>
          : <article ref={ref} key={kind} className={`paper fade k-${kind}`} dangerouslySetInnerHTML={{ __html: html || '<p class="muted">loading…</p>' }} />}
      </div>
    </div>
  );
}
