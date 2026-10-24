'use client';

import { ReactNode, useEffect, useMemo, useState } from 'react';
import { call } from './lib';
import './fleet.css';

// A small markdown reader for WHITEPAPER.md: headings, paragraphs, lists,
// tables, fenced code, rules, and inline `code` / **bold** / *em* / [links].
// Builds React elements (no innerHTML), so the paper can't inject markup.

function inline(s: string, key = 0): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*\s][^*]*\*|\[[^\]]+\]\([^)]+\))/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(s))) {
    if (m.index > last) out.push(s.slice(last, m.index));
    const t = m[0];
    const k = `${key}.${i++}`;
    if (t[0] === '`') out.push(<code key={k}>{t.slice(1, -1)}</code>);
    else if (t.startsWith('**')) out.push(<b key={k}>{inline(t.slice(2, -2), i)}</b>);
    else if (t[0] === '*') out.push(<em key={k}>{inline(t.slice(1, -1), i)}</em>);
    else {
      const l = /\[([^\]]+)\]\(([^)]+)\)/.exec(t)!;
      const safe = /^(https?:|\/|#)/.test(l[2]) ? l[2] : '#';
      out.push(
        <a key={k} href={safe} target={safe.startsWith('http') ? '_blank' : undefined} rel="noreferrer">
          {l[1]}
        </a>,
      );
    }
    last = m.index + t.length;
  }
  if (last < s.length) out.push(s.slice(last));
  return out;
}

type Block =
  | { t: 'h'; level: number; text: string; id: string }
  | { t: 'p'; text: string }
  | { t: 'ul' | 'ol'; items: string[] }
  | { t: 'code'; text: string }
  | { t: 'table'; head: string[]; rows: string[][] }
  | { t: 'hr' };

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
const cells = (l: string) => l.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());

function parse(md: string): Block[] {
  const lines = md.replace(/\r/g, '').split('\n');
  const out: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (!l.trim()) { i++; continue; }
    if (l.startsWith('```')) {
      const buf: string[] = [];
      for (i++; i < lines.length && !lines[i].startsWith('```'); i++) buf.push(lines[i]);
      out.push({ t: 'code', text: buf.join('\n') });
      i++;
      continue;
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(l);
    if (h) { out.push({ t: 'h', level: h[1].length, text: h[2], id: slug(h[2]) }); i++; continue; }
    if (/^-{3,}$/.test(l.trim())) { out.push({ t: 'hr' }); i++; continue; }
    if (l.trim().startsWith('|') && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      const head = cells(l);
      const rows: string[][] = [];
      for (i += 2; i < lines.length && lines[i].trim().startsWith('|'); i++) rows.push(cells(lines[i]));
      out.push({ t: 'table', head, rows });
      continue;
    }
    const li = /^\s*([-*]|\d+\.)\s+/;
    if (li.test(l)) {
      const ordered = /^\s*\d+\./.test(l);
      const items: string[] = [];
      while (i < lines.length && (li.test(lines[i]) || (/^\s{2,}\S/.test(lines[i]) && items.length))) {
        if (li.test(lines[i])) items.push(lines[i].replace(li, ''));
        else items[items.length - 1] += ' ' + lines[i].trim();
        i++;
      }
      out.push({ t: ordered ? 'ol' : 'ul', items });
      continue;
    }
    const buf: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|```|\s*[-*]\s|\s*\d+\.\s|\s*\||-{3,}$)/.test(lines[i])) buf.push(lines[i++]);
    if (!buf.length) buf.push(lines[i++]);
    out.push({ t: 'p', text: buf.join(' ') });
  }
  return out;
}

// Markdown as React elements: what Paper renders, reusable by any view that
// shows a markdown file (the Agents view shows each mod's skill.md with it).
export function Markdown({ md, prefix = 'p' }: { md: string; prefix?: string }) {
  const blocks = useMemo(() => parse(md), [md]);
  return <>{renderBlocks(blocks, prefix)}</>;
}

function renderBlocks(blocks: Block[], prefix: string): ReactNode[] {
  return blocks.map((b, i) => {
    switch (b.t) {
      case 'h': {
        const Tag = (`h${Math.min(b.level, 3)}` as 'h1' | 'h2' | 'h3');
        return <Tag key={i} id={`${prefix}-${b.id}`}>{inline(b.text, i)}</Tag>;
      }
      case 'p':
        return <p key={i}>{inline(b.text, i)}</p>;
      case 'ul':
        return <ul key={i}>{b.items.map((x, j) => <li key={j}>{inline(x, i * 100 + j)}</li>)}</ul>;
      case 'ol':
        return <ol key={i}>{b.items.map((x, j) => <li key={j}>{inline(x, i * 100 + j)}</li>)}</ol>;
      case 'code':
        return <pre key={i} className="dcode">{b.text}</pre>;
      case 'hr':
        return <hr key={i} />;
      case 'table':
        return (
          <div key={i} className="ptable">
            <table>
              <thead><tr>{b.head.map((c, j) => <th key={j}>{inline(c, j)}</th>)}</tr></thead>
              <tbody>
                {b.rows.map((r, j) => (
                  <tr key={j}>{r.map((c, k) => <td key={k}>{inline(c, j * 100 + k)}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
        );
    }
  });
}

// PAPER: the whitepaper (WHITEPAPER.md at the module root, via whitepaper()).
export function Paper() {
  const [md, setMd] = useState<string | null>(null);
  const [err, setErr] = useState('');
  const [at, setAt] = useState('');

  useEffect(() => {
    call('whitepaper')
      .then((r) => (r.markdown ? setMd(r.markdown) : setErr(r.error || 'No WHITEPAPER.md yet.')))
      .catch(() => setErr('API unreachable'));
  }, []);

  const blocks = useMemo(() => (md ? parse(md) : []), [md]);
  const toc = blocks.filter((b): b is Extract<Block, { t: 'h' }> => b.t === 'h' && b.level === 2);

  useEffect(() => {
    if (!toc.length) return;
    const io = new IntersectionObserver(
      (es) => {
        const v = es.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (v) setAt(v.target.id.slice(2));
      },
      { rootMargin: '-80px 0px -60% 0px' },
    );
    toc.forEach((h) => {
      const el = document.getElementById(`p-${h.id}`);
      if (el) io.observe(el);
    });
    return () => io.disconnect();
  }, [toc]);

  const go = (id: string) => document.getElementById(`p-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });

  return (
    <div className="docs paper">
      <nav className="toc">
        {toc.map((h) => (
          <button key={h.id} className={at === h.id ? 'on' : ''} onClick={() => go(h.id)}>
            {h.text.replace(/^\d+\.\s*/, '')}
          </button>
        ))}
        {md && (
          <button className="toc-raw" onClick={() => navigator.clipboard?.writeText(md)} title="copy WHITEPAPER.md">
            copy markdown
          </button>
        )}
      </nav>
      <article className="dbody">
        {err && <p className="err">{err}</p>}
        {md === null && !err && <p className="muted">Loading the paper…</p>}
        {renderBlocks(blocks, 'p')}
      </article>
    </div>
  );
}
