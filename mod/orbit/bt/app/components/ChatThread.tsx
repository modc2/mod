'use client';
/* The conversation itself — thread, starters, composer. One component, two
 * hosts: the full /chat page and the ChatDock (corner window or right
 * sidebar). State is lib/chat's, so both show the same live run. */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Msg, ToolChip, useChat, viewLabel } from '@/lib/chat';
import { md } from '@/lib/md';

function Chip({ t, onShow }: { t: ToolChip; onShow: (s: string) => void }) {
  const args = t.builtin ? '' : Object.entries(t.args || {}).map(([k, v]) => `${k}:${v}`).join(' ');
  const glyph = t.state === 'pending' ? '?' : t.state === 'err' ? '✗' : '●';
  return (
    <span className={`chip ${t.state === 'pending' ? 'pending' : t.state === 'err' ? 'err' : 'done'}${t.builtin ? ' plumb' : ''}`}
          onClick={() => onShow(`${t.name}(${JSON.stringify(t.args || {})})` + (t.preview ? '\n→ ' + t.preview : ''))}>
      {glyph} {t.name}{args ? ' · ' + args : ''} <span className="ms">{t.ms != null ? (t.ms / 1000).toFixed(1) + 's' : ''}</span>
    </span>
  );
}

/* The way out of a dead login: one click to sign in, one to look again. */
function SignIn({ href }: { href: string }) {
  const { recheck } = useChat();
  return (
    <span className="chat-signin">
      <a className="pill primary" href={href} target="_blank" rel="noopener noreferrer">Sign in with Claude ↗</a>
      <button className="pill" onClick={recheck}>I signed in — check again</button>
    </span>
  );
}

function Bot({ m }: { m: Msg }) {
  const { applyView } = useChat();
  const [detail, setDetail] = useState<string | null>(null);
  const html = useMemo(() => md(m.text), [m.text]);
  const show = (s: string) => setDetail(d => (d === s ? null : s));
  return (
    <div className="msg bot">
      {(m.tools.length > 0 || m.views.length > 0) && (
        <div className="chat-tools">
          {m.tools.map((t, i) => <Chip key={t.id + i} t={t} onShow={show} />)}
          {m.views.map((a, i) => (
            <span key={'v' + i} className="chip view" title="Show me this again" onClick={() => applyView(a)}>▶ {viewLabel(a)}</span>
          ))}
        </div>
      )}
      {detail && <div className="chip-detail">{detail}</div>}
      {m.text && <div className="bub md" dangerouslySetInnerHTML={{ __html: html }} />}
      {m.status && <div className="chat-status">● {m.status}</div>}
      {m.done && (
        <div className="chat-foot">⚑ COURSE CLEAR · {m.done.turns ?? '?'} turns
          {m.done.ms != null ? ` · ${(m.done.ms / 1000).toFixed(1)}s` : ''}
          {m.done.cost_usd != null ? ` · $${(+m.done.cost_usd).toFixed(4)}` : ''}</div>
      )}
      {m.error && <div className="chat-err">{m.error === 'stopped' ? '■ stopped' : '✗ ' + m.error}</div>}
      {m.error && m.login && <SignIn href={m.login} />}
    </div>
  );
}

/* compact = the dock: shorter copy, no model line, thread fills the host */
export default function ChatThread({ compact = false, focusKey }: { compact?: boolean; focusKey?: unknown }) {
  const c = useChat();
  const [text, setText] = useState('');
  const thread = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    const t = thread.current;
    if (t) t.scrollTop = t.scrollHeight;
  }, [c.msgs, focusKey]);

  useEffect(() => { input.current?.focus(); }, [c.id, focusKey]);

  const grow = () => {
    const t = input.current;
    if (!t) return;
    t.style.height = 'auto'; t.style.height = Math.min(t.scrollHeight, 200) + 'px';
  };
  const submit = (q?: string) => {
    const s = (q ?? text).trim();
    if (!s || c.running) return;
    setText(''); requestAnimationFrame(grow);
    c.send(s);
  };

  const a = c.agent;
  return (
    <div className={'chat-body' + (compact ? ' compact' : '')}>
      {a && !a.ready && (
        <div style={{ marginBottom: 12 }}>
          <p className="chat-err">⚠ {a.hint || 'Agent auth not configured on this host.'}</p>
          {a.login && <SignIn href={a.login} />}
        </div>
      )}
      <div className="chat-thread" ref={thread}>
        {c.msgs.map((m, i) => m.role === 'user'
          ? <div key={i} className="msg you"><div className="bub">{m.text}</div></div>
          : <Bot key={i} m={m} />)}
      </div>
      {!c.msgs.length && (a?.starters?.length ?? 0) > 0 && (
        <div className="chat-sugg">
          <p className="chat-empty" style={{ width: '100%' }}>
            {compact ? 'Ask about a subnet, a trader, or the market. Try:'
              : 'Ask about a subnet, a trader, or the whole market — the answer opens on your screen. Try:'}
          </p>
          {a!.starters!.slice(0, compact ? 3 : undefined).map(s => <button key={s} onClick={() => submit(s)}>{s}</button>)}
        </div>
      )}
      <div className="chat-form">
        <textarea ref={input} rows={1} spellCheck={false} value={text}
          placeholder={compact ? 'Ask the network…' : 'Which subnet pumped hardest today, and who runs it?'}
          onChange={e => { setText(e.target.value); grow(); }}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); } }} />
        {c.running
          ? <button className="pill primary" onClick={c.stop}>Stop ■</button>
          : <button className="pill primary" onClick={() => submit()}>{compact ? '▶' : 'Send ▶'}</button>}
      </div>
      {!compact && a?.ready && <p className="muted" style={{ marginTop: 12 }}>{a.model} · {a.tools} tools · {a.denied} on-chain tools denied · auth: {a.method}</p>}
    </div>
  );
}
