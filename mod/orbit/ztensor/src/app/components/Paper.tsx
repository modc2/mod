'use client';
/* WHITEPAPER: GET whitepaper, rendered by a small markdown subset,
   Contents is a dropdown everywhere; on wide screens the reader can pin it
   as a sticky sidebar instead (scroll-spy over the h2s, choice remembered). */
import { useEffect, useRef, useState } from 'react';
import { get } from '@/lib/api';

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const inline = (s: string) => esc(s)
  .replace(/`([^`]+)`/g, '<code>$1</code>')
  .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
  .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<i>$2</i>')
  .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');

const slug = (s: string) => 'p-' + s.toLowerCase().replace(/[`*]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

export function md(src: string): string {
  const out: string[] = [];
  const L = src.replace(/\r/g, '').split('\n');
  for (let i = 0; i < L.length; i++) {
    const l = L[i];
    if (l.startsWith('```')) {
      const buf: string[] = [];
      while (++i < L.length && !L[i].startsWith('```')) buf.push(L[i]);
      out.push(`<pre><code>${esc(buf.join('\n'))}</code></pre>`);
    } else if (/^#{1,4} /.test(l)) {
      const n = l.match(/^#+/)![0].length;
      const t = l.slice(n + 1);
      out.push(`<h${n} id="${slug(t)}">${inline(t)}</h${n}>`);
    } else if (/^\s*\|/.test(l)) {
      const rows: string[][] = [];
      for (; i < L.length && /^\s*\|/.test(L[i]); i++)
        if (!/^\s*\|[\s:|-]+\|\s*$/.test(L[i])) rows.push(L[i].trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim()));
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
    } else if (l.startsWith('> ')) {
      out.push(`<blockquote>${inline(l.slice(2))}</blockquote>`);
    } else if (l.trim()) {
      const buf = [l];
      while (i + 1 < L.length && L[i + 1].trim() && !/^(#|```|\s*\||\s*([-*]|\d+\.) |> )/.test(L[i + 1])) buf.push(L[++i]);
      out.push(`<p>${inline(buf.join(' '))}</p>`);
    }
  }
  return out.join('\n');
}

type Sec = { id: string; title: string };

export default function Paper() {
  const [src, setSrc] = useState('');
  const [err, setErr] = useState('');
  const [on, setOn] = useState('');
  const [rail, setRail] = useState(true);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLElement>(null);
  const drop = useRef<HTMLDivElement>(null);
  useEffect(() => { setRail(localStorage.getItem('ztensor.toc') !== 'off'); }, []);
  const pin = (v: boolean) => { setRail(v); setOpen(false); localStorage.setItem('ztensor.toc', v ? 'on' : 'off'); };
  useEffect(() => {
    if (!open) return;
    const off = (e: Event) => { if (!drop.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('pointerdown', off);
    document.addEventListener('keydown', esc);
    return () => { document.removeEventListener('pointerdown', off); document.removeEventListener('keydown', esc); };
  }, [open]);
  useEffect(() => {
    get('whitepaper').then((d) => setSrc(typeof d === 'string' ? d : d?.text ?? d?.markdown ?? '')).catch((e) => setErr(e.message));
  }, []);
  const html = src ? md(src) : '';
  const secs: Sec[] = src.split('\n').filter((l) => /^## /.test(l)).map((l) => ({ id: slug(l.slice(3)), title: l.slice(3).replace(/[`*]/g, '') }));
  const mins = Math.max(1, Math.round(src.split(/\s+/).length / 230));
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
  if (err) return <div className="card fade"><div className="err">{err}</div></div>;
  return (
    <div className={`paperwrap fade${rail ? ' rail' : ''}`}>
      {secs.length > 0 && (
        <nav className="toc" aria-label="Contents">
          <div className="toc-h">Contents <span>{mins} min read</span>
            <button className="toc-x" title="fold the sidebar into a dropdown" onClick={() => pin(false)}>hide</button></div>
          {secs.map((s) => (
            <button key={s.id} className={on === s.id ? 'on' : ''} onClick={() => go(s.id)}>{s.title}</button>
          ))}
        </nav>
      )}
      {secs.length > 0 && (
        <div className="tocdrop" ref={drop}>
          <button className={`tocsel${open ? ' open' : ''}`} aria-expanded={open} onClick={() => setOpen((o) => !o)}>
            <span className="k">Contents</span><span className="cur">{cur?.title ?? `${secs.length} sections · ${mins} min read`}</span><i />
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
      <div className="card" style={{ padding: 0, minWidth: 0 }}>
        <article ref={ref} className="paper" dangerouslySetInnerHTML={{ __html: html || '<p class="muted">loading…</p>' }} />
      </div>
    </div>
  );
}
