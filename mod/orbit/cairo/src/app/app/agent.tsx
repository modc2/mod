'use client';

import { useEffect, useRef, useState } from 'react';
import { BASE, authHeaders } from './lib';
import './learn.css';

// The helper in the bottom-right corner. Speaks the fleet agent contract
// (POST /cairo/run/stream, SSE step ... done) and every fact it reads comes
// through the cairo MCP tools, shown as a fold-out trail under each answer.
// Two brains: "Quick" (free, instant, anyone) and "Smart" (Claude via
// orbit/claudecode; owner only, since it bills the host account), plus
// "Build" = the builder agent (owner only): makes or edits a contract
// ("make a tip jar called tips", "add a pause switch" on the open one).

export type AgentAction = { label: string; lesson?: string; new?: string; open?: string; ask?: string };
export type AgentContext = { contract?: string | null; view?: string; lesson?: string | null };
type Step = { tool: string; params?: any; result?: any };
type Msg = { role: 'me' | 'bot'; text: string; steps?: Step[]; actions?: AgentAction[]; brain?: string; note?: string; busy?: boolean };

type Brain = 'helper' | 'claude' | 'builder';

const STARTERS = [
  'What is a stablecoin, like I\'m 10?',
  'How do I make my own stablecoin?',
  'USDC vs DAI: what\'s the difference?',
  'What can this contract do?',
];

export function AgentOrb({ ctx, writable, onAction }: { ctx: AgentContext; writable: boolean; onAction: (a: AgentAction) => void }) {
  const [open, setOpenState] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [q, setQ] = useState('');
  const [brain, setBrainState] = useState<Brain>('helper');
  const [running, setRunning] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    setOpenState(localStorage.getItem('cairo.agent.open') === '1');
    const saved = localStorage.getItem('cairo.agent.brain');
    if (saved === 'claude' || saved === 'builder') setBrainState(saved);
  }, []);
  const setOpen = (v: boolean) => {
    setOpenState(v);
    localStorage.setItem('cairo.agent.open', v ? '1' : '0');
  };
  const setBrain = (b: Brain) => {
    setBrainState(b);
    localStorage.setItem('cairo.agent.brain', b);
  };
  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: 'smooth' });
  }, [msgs, open]);
  useEffect(() => {
    if (open) input.current?.focus();
  }, [open]);
  // anything on the page can pop the helper open (LEARN's "Stuck?" button)
  useEffect(() => {
    const pop = () => setOpen(true);
    window.addEventListener('cairo:agent-open', pop);
    return () => window.removeEventListener('cairo:agent-open', pop);
  }, []);

  const ask = async (text: string) => {
    const query = text.trim();
    if (!query || running) return;
    setQ('');
    setRunning(true);
    const useBrain = writable ? brain : 'helper';
    setMsgs((m) => [...m, { role: 'me', text: query }, { role: 'bot', text: '', steps: [], busy: true }]);
    const patch = (f: (b: Msg) => Msg) => setMsgs((m) => [...m.slice(0, -1), f(m[m.length - 1])]);
    try {
      const res = await fetch(`${BASE}/run/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ query, brain: useBrain, context: ctx }),
      });
      if (!res.body) throw new Error(`HTTP ${res.status}`);
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const line = buf.slice(0, i).trim();
          buf = buf.slice(i + 2);
          if (!line.startsWith('data:')) continue;
          let ev: any;
          try {
            ev = JSON.parse(line.slice(5));
          } catch {
            continue;
          }
          // the builder made/started something: the page re-reads its list + jobs
          if ((ev.type === 'step' && /^(job|mcp:cairo_(new|fork|write|vibe))$/.test(ev.step?.tool || '')) || (ev.type === 'done' && ev.contract))
            window.dispatchEvent(new Event('cairo:changed'));
          if (ev.type === 'step') patch((b) => ({ ...b, steps: [...(b.steps || []), ev.step] }));
          else if (ev.type === 'done')
            patch((b) => ({ ...b, busy: false, text: ev.answer || '(no answer)', actions: ev.actions, brain: ev.brain, note: ev.note }));
          else if (ev.type === 'error') patch((b) => ({ ...b, busy: false, text: `Something went wrong: ${ev.error}` }));
        }
      }
      patch((b) => (b.busy ? { ...b, busy: false, text: b.text || 'The answer got cut off. Try again?' } : b));
    } catch (e: any) {
      patch((b) => ({ ...b, busy: false, text: `I couldn't reach the helper (${e?.message || e}).` }));
    }
    setRunning(false);
  };

  const act = (a: AgentAction) => {
    if (a.ask) return ask(a.ask);
    onAction(a);
  };

  return (
    <>
      {open && (
        <div className="agentpanel" role="dialog" aria-label="helper">
          <div className="agenthead">
            <span className="agentdot" />
            <b>Helper</b>
            <span className="agentsub">{ctx.contract ? `looking at ${ctx.contract}` : 'ask me anything'}</span>
            <span className="spacer" />
            {writable && (
              <span className="brainpick" title="Quick: instant and free. Smart: Claude reads your contracts through the cairo MCP server and can answer anything (slower, uses your Claude account). Build: make or change a contract.">
                <button className={brain === 'helper' ? 'on' : ''} onClick={() => setBrain('helper')}>Quick</button>
                <button className={brain === 'claude' ? 'on' : ''} onClick={() => setBrain('claude')}>Smart</button>
                <button className={brain === 'builder' ? 'on' : ''} onClick={() => setBrain('builder')} title="Build: make a new contract or change the open one; the agent writes the Cairo and tests, then builds and tests it (minutes, uses your Claude account).">Build</button>
              </span>
            )}
            {msgs.length > 0 && (
              <button className="agentx" title="clear the chat" onClick={() => setMsgs([])}>
                ↺
              </button>
            )}
            <button className="agentx" title="close" onClick={() => setOpen(false)}>
              ×
            </button>
          </div>
          <div className="agentmsgs" ref={scroller}>
            {msgs.length === 0 && (
              <div className="agenthello">
                <p>
                  Hi! I explain <b>stablecoins</b> and <b>smart contracts</b> in plain words, and I can look at your contracts for you.
                </p>
                <div className="agentchips">
                  {STARTERS.map((s) => (
                    <button key={s} onClick={() => ask(s)}>
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {msgs.map((m, i) =>
              m.role === 'me' ? (
                <div key={i} className="amsg me">
                  {m.text}
                </div>
              ) : (
                <div key={i} className="amsg bot">
                  {m.busy && (
                    <div className="athinking">
                      <i />
                      <i />
                      <i />
                      <span>{m.steps?.length ? stepLabel(m.steps[m.steps.length - 1]) : brain === 'builder' && writable ? 'building (a few minutes)…' : brain === 'claude' && writable ? 'thinking (this can take a minute)…' : 'thinking…'}</span>
                    </div>
                  )}
                  {m.text && <Md text={m.text} />}
                  {m.note && <p className="anote">{m.note}</p>}
                  {!!m.actions?.length && (
                    <div className="aactions">
                      {m.actions.map((a, j) => (
                        <button key={j} className={a.new ? 'primary' : ''} onClick={() => act(a)}>
                          {a.label}
                        </button>
                      ))}
                    </div>
                  )}
                  {!m.busy && !!m.steps?.length && (
                    <details className="atrail">
                      <summary>
                        {m.steps.length} step{m.steps.length === 1 ? '' : 's'} · {m.brain === 'claude' ? 'Claude' : 'helper'} via cairo MCP
                      </summary>
                      <ol>
                        {m.steps.map((s, j) => (
                          <li key={j}>
                            <b>{s.tool}</b> {brief(s.params)}
                          </li>
                        ))}
                      </ol>
                    </details>
                  )}
                </div>
              ),
            )}
          </div>
          <form
            className="agentin"
            onSubmit={(e) => {
              e.preventDefault();
              ask(q);
            }}
          >
            <textarea
              ref={input}
              rows={1}
              value={q}
              placeholder={writable && brain === 'builder'
                ? (ctx.contract ? `Change ${ctx.contract}, or "make a … called …"` : 'e.g. make a tip jar called tips')
                : ctx.contract ? `Ask about ${ctx.contract}, or anything…` : 'Ask anything, e.g. what is collateral?'}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  ask(q);
                }
              }}
            />
            <button type="submit" disabled={running || !q.trim()} title="send (Enter)">
              ↑
            </button>
          </form>
        </div>
      )}
      <button className={`agentorb ${open ? 'on' : ''} ${running ? 'busy' : ''}`} title={open ? 'hide the helper' : 'ask the helper'} onClick={() => setOpen(!open)}>
        {open ? '×' : '?'}
      </button>
    </>
  );
}

function stepLabel(s: Step): string {
  if (s.tool?.startsWith('mcp:')) return `reading ${s.tool.slice(8).replace(/_/g, ' ')}…`;
  if (s.tool === 'bash') return 'checking with the cairo MCP server…';
  return `${s.tool}…`;
}

function brief(p: any): string {
  if (!p || typeof p !== 'object') return '';
  const v = p.command || p.name || p.id || p.file_path || p.path || Object.values(p)[0];
  return v ? String(v).slice(0, 90) : '';
}

// Just enough markdown for answers and lessons: paragraphs, - and 1. lists,
// **bold** and `code`.
export function Md({ text }: { text: string }) {
  const blocks = text.split(/\n\s*\n/);
  return (
    <div className="md">
      {blocks.map((b, i) => {
        const lines = b.split('\n');
        if (lines.every((l) => /^\s*[-*] /.test(l)))
          return (
            <ul key={i}>
              {lines.map((l, j) => (
                <li key={j}>{inline(l.replace(/^\s*[-*] /, ''))}</li>
              ))}
            </ul>
          );
        if (lines.every((l) => /^\s*\d+\. /.test(l)))
          return (
            <ol key={i}>
              {lines.map((l, j) => (
                <li key={j}>{inline(l.replace(/^\s*\d+\. /, ''))}</li>
              ))}
            </ol>
          );
        return <p key={i}>{lines.map((l, j) => (j ? [<br key={j} />, inline(l)] : inline(l)))}</p>;
      })}
    </div>
  );
}

export function inline(s: string): React.ReactNode[] {
  return s.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, i) =>
    part.startsWith('**') && part.endsWith('**') ? (
      <b key={i}>{part.slice(2, -2)}</b>
    ) : part.startsWith('`') && part.endsWith('`') && part.length > 1 ? (
      <code key={i}>{part.slice(1, -1)}</code>
    ) : (
      part
    ),
  );
}
