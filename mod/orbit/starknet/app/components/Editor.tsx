'use client';
import { forwardRef, useImperativeHandle, useMemo, useRef } from 'react';
import { highlightLine, isCairoish } from '@/lib/highlight';

export type EditorHandle = { jump: (line: number, col?: number) => void };

/** A code editor without a dependency: a transparent <textarea> over a
 *  highlighted <pre> sharing one box model, plus a line-number gutter.
 *  Tab indents, Cmd/Ctrl+S calls onSave. */
const Editor = forwardRef<EditorHandle, {
  path: string | null; value: string; onChange: (v: string) => void;
  onSave?: () => void; readOnly?: boolean; marks?: { line: number; level: string }[];
}>(function Editor({ path, value, onChange, onSave, readOnly, marks = [] }, ref) {
  const ta = useRef<HTMLTextAreaElement>(null);
  const pre = useRef<HTMLPreElement>(null);
  const gut = useRef<HTMLDivElement>(null);
  const lines = useMemo(() => value.split('\n'), [value]);
  const color = isCairoish(path);
  const marked = useMemo(() => {
    const m: Record<number, string> = {};
    marks.forEach(x => { if (m[x.line] !== 'error') m[x.line] = x.level; });
    return m;
  }, [marks]);

  const html = useMemo(() => lines.map(l => {
    const body = color ? highlightLine(l) : l.replace(/&/g, '&amp;').replace(/</g, '&lt;');
    return body || ' ';
  }).join('\n') + '\n', [lines, color]);

  useImperativeHandle(ref, () => ({
    jump(line, col = 1) {
      const t = ta.current;
      if (!t) return;
      let pos = 0;
      for (let i = 0; i < line - 1 && i < lines.length; i++) pos += lines[i].length + 1;
      pos += Math.max(0, col - 1);
      t.focus();
      t.setSelectionRange(pos, pos);
      t.scrollTop = Math.max(0, (line - 6) * 20);
      sync();
    },
  }), [lines]);

  function sync() {
    const t = ta.current;
    if (!t) return;
    if (pre.current) { pre.current.scrollTop = t.scrollTop; pre.current.scrollLeft = t.scrollLeft; }
    if (gut.current) gut.current.scrollTop = t.scrollTop;
  }

  function onKey(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if ((e.metaKey || e.ctrlKey) && e.key === 's') { e.preventDefault(); onSave?.(); return; }
    if (e.key === 'Tab' && !readOnly) {
      e.preventDefault();
      const t = e.currentTarget;
      const { selectionStart: a, selectionEnd: b } = t;
      const next = value.slice(0, a) + '    ' + value.slice(b);
      onChange(next);
      requestAnimationFrame(() => t.setSelectionRange(a + 4, a + 4));
    }
  }

  return (
    <div className="editor">
      <div className="gutter" ref={gut} aria-hidden>
        {lines.map((_, i) => (
          <div key={i} className={marked[i + 1] ? `mk ${marked[i + 1]}` : ''}>{i + 1}</div>
        ))}
        <div>&nbsp;</div>
      </div>
      <div className="code-box">
        <pre className="hl" ref={pre} aria-hidden dangerouslySetInnerHTML={{ __html: html }} />
        <textarea
          ref={ta} className="code" value={value} spellCheck={false} readOnly={readOnly}
          autoCapitalize="off" autoComplete="off" autoCorrect="off"
          onChange={e => onChange(e.target.value)} onScroll={sync} onKeyDown={onKey}
        />
      </div>
    </div>
  );
});

export default Editor;
