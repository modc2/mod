'use client';
/* CODE: every chain's verifier and adapter, straight from GET files/source.
 * Groups carry a `chain`, so the list follows the chain switch. */
import { useEffect, useMemo, useRef, useState } from 'react';
import type { Chain } from '@/lib/api';
import { get } from '@/lib/api';

type Group = { group: string; chain: string; files: { path: string; note?: string; lines?: number }[] };

const KW = new Set(('fn let mut pub use mod struct impl trait enum match if else for while loop return self Self const static ' +
  'def class import from as with try except raise yield lambda None True False and or not in is pass async await ' +
  'function contract mapping uint256 bytes32 address public private external internal view pure returns require emit event ' +
  'memory storage calldata assembly felt252 u256 u128 u64 u32 u8 bool ref nopanic of where type interface export default var').split(' '));

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

function hl(line: string, hash: boolean): string {
  const re = hash
    ? /(#.*$)|("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|(\b0x[0-9a-fA-F]+\b|\b\d+\b)|([A-Za-z_][A-Za-z0-9_]*)(?=\s*\()|([A-Za-z_][A-Za-z0-9_]*)/g
    : /(\/\/.*$)|("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)')|(\b0x[0-9a-fA-F]+\b|\b\d+\b)|([A-Za-z_][A-Za-z0-9_]*)(?=\s*\()|([A-Za-z_][A-Za-z0-9_]*)/g;
  let out = '', last = 0, m: RegExpExecArray | null;
  while ((m = re.exec(line))) {
    out += esc(line.slice(last, m.index));
    const [tok, c, s, n, f, w] = m;
    out += c ? `<span class="tk-c">${esc(c)}</span>`
      : s ? `<span class="tk-s">${esc(s)}</span>`
      : n ? `<span class="tk-n">${n}</span>`
      : f ? (KW.has(f) ? `<span class="tk-k">${f}</span>` : `<span class="tk-f">${f}</span>`)
      : w && KW.has(w) ? `<span class="tk-k">${w}</span>` : esc(tok);
    last = m.index + tok.length;
  }
  return out + esc(line.slice(last));
}

const LANG: Record<string, string> = { cairo: 'Cairo', rs: 'Rust', sol: 'Solidity', py: 'Python', toml: 'TOML', ts: 'TS', sh: 'Shell', yul: 'Yul', json: 'JSON', md: 'Markdown' };

/* top-level definitions worth jumping to, per language */
const SYM = /^\s*(?:pub(?:\([a-z]+\))?\s+)?(?:async\s+)?(?:fn|def|class|struct|trait|impl|mod|contract|library|interface|enum)\s+([A-Za-z_][A-Za-z0-9_]*)|^\s*function\s+([A-Za-z_][A-Za-z0-9_]*)/;

export default function Code({ chain }: { chain?: Chain }) {
  const [groups, setGroups] = useState<Group[]>([]);
  const [all, setAll] = useState(false);
  const [path, setPath] = useState('');
  const [text, setText] = useState('');
  const [err, setErr] = useState('');
  const [copied, setCopied] = useState(false);
  const [q, setQ] = useState('');
  const [mark, setMark] = useState(0);
  const pre = useRef<HTMLPreElement>(null);
  const copy = () => navigator.clipboard?.writeText(text).then(() => { setCopied(true); window.setTimeout(() => setCopied(false), 1400); }).catch(() => {});

  useEffect(() => { get<Group[]>('files').then(setGroups).catch((e) => setErr(e.message)); }, []);
  const shown = useMemo(() => groups.filter((g) => all || g.chain === 'shared' || g.chain === chain?.chain), [groups, all, chain]);
  const needle = q.trim().toLowerCase();
  const listed = useMemo(() => !needle ? shown : shown
    .map((g) => ({ ...g, files: g.files.filter((f) => (f.path + ' ' + (f.note ?? '')).toLowerCase().includes(needle)) }))
    .filter((g) => g.files.length), [shown, needle]);
  useEffect(() => {
    const first = shown.find((g) => g.chain === chain?.chain)?.files[0] || shown[0]?.files[0];
    if (first && !shown.some((g) => g.files.some((f) => f.path === path))) setPath(first.path);
  }, [shown, chain]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!path) return;
    setText(''); setMark(0);
    get('source', { path }).then((d) => setText(d.text ?? '')).catch((e) => setErr(e.message));
  }, [path]);

  const hash = /\.(py|toml|sh|ya?ml)$/.test(path);
  const lines = useMemo(() => text.split('\n').map((l) => hl(l, hash)), [text, hash]);
  const syms = useMemo(() => text.split('\n').flatMap((l, i) => { const m = SYM.exec(l); return m ? [{ name: m[1] || m[2], line: i + 1 }] : []; }), [text]);
  const ext = path.split('.').pop() ?? '';
  const jump = (n: number) => {
    setMark(n);
    const el = pre.current?.children[n - 1] as HTMLElement | undefined;
    if (el && pre.current) pre.current.scrollTo({ top: el.offsetTop - pre.current.clientHeight / 3, behavior: 'smooth' });
  };
  const note = groups.flatMap((g) => g.files).find((f) => f.path === path)?.note;

  return (
    <div className="code fade">
      <div className="card files">
        <div className="row" style={{ padding: '4px 6px 0' }}>
          <div className="seg">
            <button className={!all ? 'on' : ''} onClick={() => setAll(false)}>{chain?.title ?? 'chain'}</button>
            <button className={all ? 'on' : ''} onClick={() => setAll(true)}>all chains</button>
          </div>
        </div>
        <input className="ffind" placeholder="filter files…" value={q} onChange={(e) => setQ(e.target.value)} spellCheck={false} />
        {listed.length === 0 && <div className="muted" style={{ padding: '10px', fontSize: 12 }}>no file matches “{q}”</div>}
        {listed.map((g) => (
          <div key={g.group}>
            <div className="grp">{g.group}</div>
            {g.files.map((f) => (
              <button key={f.path} className={f.path === path ? 'on' : ''} onClick={() => setPath(f.path)}>
                <div className="p">{f.path.split('/').slice(-2).join('/')}</div>
                {f.note && <div className="nt">{f.note}{f.lines ? ` · ${f.lines} lines` : ''}</div>}
              </button>
            ))}
          </div>
        ))}
      </div>
      <div className="card src">
        <div className="hd">
          <span className="crumb">{path.split('/').map((seg, i, a) => <span key={i} className={i === a.length - 1 ? 'leaf' : ''}>{seg}{i < a.length - 1 && <i>/</i>}</span>)}</span>
          <div className="spacer" />
          {text && <span className="lang">{LANG[ext] ?? ext}<em>{lines.length} lines</em></span>}
          {syms.length > 1 && (
            <select className="jump" value="" onChange={(e) => e.target.value && jump(+e.target.value)} aria-label="jump to definition">
              <option value="">jump to… ({syms.length})</option>
              {syms.map((sy) => <option key={sy.line} value={sy.line}>{sy.name} · {sy.line}</option>)}
            </select>
          )}
          {text && <button className={`copy${copied ? ' ok' : ''}`} onClick={copy}>{copied ? 'copied' : 'copy'}</button>}
        </div>
        {note && <div className="srcnote">{note}</div>}
        {err && <div style={{ padding: 14 }}><div className="err">{err}</div></div>}
        <pre ref={pre}>{text ? lines.map((l, i) => <span className={`l${mark === i + 1 ? ' mk' : ''}`} key={i} onClick={(e) => { if ((e.target as HTMLElement) === e.currentTarget && e.nativeEvent.offsetX < 48) setMark(mark === i + 1 ? 0 : i + 1); }} dangerouslySetInnerHTML={{ __html: l || ' ' }} />) : <span className="l muted">loading…</span>}</pre>
      </div>
    </div>
  );
}
