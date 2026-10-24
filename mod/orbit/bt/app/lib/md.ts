/* A small markdown renderer — enough for the agent's numbers, tables and
 * links. Everything is escaped first, so the output is safe to set as HTML. */

export const esc = (s: unknown) => String(s ?? '').replace(/[&<>"]/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c] as string));

function inline(t: string): string {
  return esc(t)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1<i>$2</i>')
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/(^|\s)(https?:\/\/[^\s<]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
}

const isSep = (l: string) => /^\s*\|?[\s:-]*-[-\s|:]*\|?\s*$/.test(l) && l.includes('-');
const cells = (l: string) => l.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
const NUMERIC = /^[-+τ$]?[\d.,%\s]+[%τ]?$/;

export function md(src: string): string {
  const lines = String(src || '').split('\n');
  let out = '';
  let para: string[] = [];
  let list: 'ul' | 'ol' | null = null;
  const flushPara = () => { if (para.length) { out += `<p>${inline(para.join(' '))}</p>`; para = []; } };
  const flushList = () => { if (list) { out += `</${list}>`; list = null; } };
  const flush = () => { flushPara(); flushList(); };

  for (let i = 0; i < lines.length; i++) {
    const t = lines[i].trim();
    if (t.startsWith('```')) {
      flush();
      const code: string[] = [];
      while (++i < lines.length && !lines[i].trim().startsWith('```')) code.push(lines[i]);
      out += `<pre><code>${esc(code.join('\n'))}</code></pre>`;
      continue;
    }
    if (t.includes('|') && lines[i + 1] !== undefined && isSep(lines[i + 1])) {
      flush();
      const head = cells(t); i++;
      const body: string[][] = [];
      while (i + 1 < lines.length && lines[i + 1].includes('|') && lines[i + 1].trim()) body.push(cells(lines[++i]));
      const num = head.map((_, c) => body.length > 0 &&
        body.every(r => r[c] === undefined || r[c] === '' || NUMERIC.test(r[c])));
      out += '<table><thead><tr>' + head.map((h, c) => `<th class="${num[c] ? 'num' : ''}">${inline(h)}</th>`).join('')
        + '</tr></thead><tbody>' + body.map(r => '<tr>' + head.map((_, c) =>
          `<td class="${num[c] ? 'num' : ''}">${inline(r[c] || '')}</td>`).join('') + '</tr>').join('')
        + '</tbody></table>';
      continue;
    }
    if (!t) { flush(); continue; }
    if (/^(-{3,}|\*{3,})$/.test(t)) { flush(); out += '<hr>'; continue; }
    let m: RegExpMatchArray | null;
    if ((m = t.match(/^(#{1,6})\s+(.*)$/))) {
      flush();
      const h = Math.min(m[1].length + 2, 4);
      out += `<h${h}>${inline(m[2])}</h${h}>`;
      continue;
    }
    if ((m = t.match(/^[-*•]\s+(.*)$/))) {
      flushPara(); if (list !== 'ul') { flushList(); out += '<ul>'; list = 'ul'; }
      out += `<li>${inline(m[1])}</li>`; continue;
    }
    if ((m = t.match(/^\d+[.)]\s+(.*)$/))) {
      flushPara(); if (list !== 'ol') { flushList(); out += '<ol>'; list = 'ol'; }
      out += `<li>${inline(m[1])}</li>`; continue;
    }
    flushList(); para.push(t);
  }
  flush();
  return out;
}
