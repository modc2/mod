'use client';
import { Suspense, useEffect, useRef, useState } from 'react';
import { Badge } from '@/components/ui';
import { API, TOKEN_KEY } from '@/lib/api';
import { useApi, useParam } from '@/lib/hooks';
import { Md } from '@/lib/md';
import { useNet } from '@/lib/net';
import { hasWallet, signIn } from '@/lib/wallet';

type Step = { tool: string; params?: any; result?: any; error?: string };
type Msg = { role: 'user' | 'assistant'; content: string; steps?: Step[]; brain?: string; note?: string | null; ms?: number; pending?: boolean; error?: string };

const EXAMPLES = [
  'What is Starknet and how does it work?',
  'How do stablecoins work on Starknet?',
  'Is USDC on its peg right now?',
  'How does the STRK20 privacy pool hide payments?',
  'What do I pay fees in?',
  'How could I launch my own stablecoin?',
];

const STEP_LABEL: Record<string, string> = {
  read_network: 'read the network', read_stables: 'read every stablecoin', read_pool: 'read the privacy pool',
  bash: 'ran', fetch: 'fetched', search: 'searched', read: 'read a file', response: 'note',
};

function stepText(s: Step) {
  const r = s.error || s.result;
  if (s.tool === 'bash') return String(s.params?.command || '').slice(0, 140);
  if (typeof r === 'string') return r.slice(0, 160);
  return '';
}

function Ask() {
  const { net } = useNet();
  const q0 = useParam('q');
  const who = useApi<any>('studio/whoami');
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState('');
  const [brain, setBrain] = useState<'facts' | 'claude'>('facts');
  const [busy, setBusy] = useState(false);
  const [signErr, setSignErr] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const log = useRef<HTMLDivElement>(null);
  const started = useRef(false);
  const owner = !!who.data?.writable;

  useEffect(() => { if (owner) setBrain('claude'); }, [owner]);
  useEffect(() => { log.current?.scrollTo({ top: log.current.scrollHeight, behavior: 'smooth' }); }, [msgs]);
  useEffect(() => {
    if (q0 && !started.current) { started.current = true; send(q0); }
  }, [q0]); // eslint-disable-line react-hooks/exhaustive-deps

  function patchLast(f: (m: Msg) => Msg) {
    setMsgs(ms => ms.map((m, i) => (i === ms.length - 1 ? f(m) : m)));
  }

  async function send(query: string) {
    query = query.trim();
    if (!query || busy) return;
    const history = msgs.filter(m => !m.pending && !m.error).map(m => ({ role: m.role, content: m.content }));
    setMsgs(ms => [...ms, { role: 'user', content: query }, { role: 'assistant', content: '', steps: [], pending: true }]);
    setText('');
    setBusy(true);
    const ctl = new AbortController();
    abort.current = ctl;
    try {
      const tok = window.localStorage.getItem(TOKEN_KEY);
      const r = await fetch(`${API}/run/stream`, {
        method: 'POST', signal: ctl.signal,
        headers: { 'Content-Type': 'application/json', ...(tok ? { token: tok } : {}) },
        body: JSON.stringify({ query, brain, network: net, history }),
      });
      if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`);
      const reader = r.body.getReader();
      const dec = new TextDecoder();
      let buf = '', finished = false;
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
          const line = chunk.split('\n').find(l => l.startsWith('data:'));
          if (!line) continue;
          const ev = JSON.parse(line.slice(5));
          if (ev.type === 'step' && ev.step.tool !== 'finish') patchLast(m => ({ ...m, steps: [...(m.steps || []), ev.step] }));
          else if (ev.type === 'done') { finished = true; patchLast(m => ({ ...m, pending: false, content: ev.answer, brain: ev.brain, note: ev.note, ms: ev.ms })); }
          else if (ev.type === 'error') { finished = true; patchLast(m => ({ ...m, pending: false, error: ev.error })); }
        }
      }
      if (!finished) patchLast(m => ({ ...m, pending: false, error: 'the stream ended before an answer' }));
    } catch (e: any) {
      patchLast(m => ({ ...m, pending: false, error: e?.name === 'AbortError' ? 'stopped' : (e?.message || String(e)) }));
    } finally {
      setBusy(false);
      abort.current = null;
    }
  }

  async function doSignIn() {
    setSignErr(null);
    try { await signIn(); who.reload(); } catch (e: any) { setSignErr(e?.message || String(e)); }
  }

  return (
    <div className="ask">
      <div className="page-head">
        <div>
          <div className="crumb">Agent</div>
          <h1>Ask Starknet</h1>
          <div className="sub">Plain-English answers with live numbers from the chain · agent protocol at /run/stream</div>
        </div>
        <div className="seg" role="group" aria-label="brain">
          <button className={brain === 'facts' ? 'on' : ''} onClick={() => setBrain('facts')} title="No model, no cost: answers from live chain readings">Live facts</button>
          <button className={brain === 'claude' ? 'on' : ''} disabled={!owner} onClick={() => setBrain('claude')}
            title={owner ? 'Claude Code, grounded in the same readings' : 'Claude spends the host account — owner only'}>Claude</button>
        </div>
      </div>

      <div className="ask-box">
        <div className="agent-log" ref={log}>
          {!msgs.length && (
            <div className="agent-intro">
              <div className="agent-orb" />
              <b>What do you want to understand?</b>
              <p>Ask anything about how Starknet works. Each answer shows what it read from the chain.</p>
              <div className="quick">
                {EXAMPLES.map(e => <button key={e} className="chip" onClick={() => send(e)}>{e}</button>)}
              </div>
            </div>
          )}
          {msgs.map((m, i) => m.role === 'user' ? (
            <div key={i} className="msg you"><div className="msg-text">{m.content}</div></div>
          ) : (
            <div key={i} className={`msg agent ${m.pending ? 'pending' : ''}`}>
              <div className="msg-who">guide{m.brain ? ` · ${m.brain === 'claude' ? 'claude' : 'live facts'}` : ''}</div>
              {!!m.steps?.length && (
                <div className="trace">
                  {m.steps.map((s, j) => (
                    <div key={j} className={`trace-row ${s.error ? 'bad' : ''}`}>
                      <span className="trace-tool">{STEP_LABEL[s.tool] || s.tool}</span>
                      <span className="trace-res">{stepText(s)}</span>
                    </div>
                  ))}
                </div>
              )}
              {m.pending && <div className="msg-text"><span className="dots"><i /><i /><i /></span>{brain === 'claude' ? 'Claude is thinking' : 'reading the chain'}…</div>}
              {m.error && <div className="error"><span>{m.error}</span></div>}
              {m.content && <Md text={m.content} />}
              {!m.pending && !m.error && (
                <div className="msg-meta">
                  {m.note && <Badge tone="warn">{m.note}</Badge>}
                  {m.ms !== undefined && <span className="dim">{(m.ms / 1000).toFixed(1)}s</span>}
                </div>
              )}
            </div>
          ))}
        </div>
        <div className="agent-input">
          <textarea className="input" rows={2} value={text} placeholder="Ask about Starknet, stablecoins, the privacy pool…"
            onChange={e => setText(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(text); } }} />
          <div className="agent-foot">
            <span className="dim">
              {owner ? 'Signed in as owner — Claude available.' : <>Live facts are free for everyone.{' '}
                {hasWallet() && <button className="linkish" onClick={doSignIn}>Sign in as owner</button>} to use Claude.</>}
              {signErr && <span className="acct-err"> {signErr}</span>}
            </span>
            {busy ? <button className="btn ghost sm" onClick={() => abort.current?.abort()}>Stop</button>
              : <button className="btn sm" disabled={!text.trim()} onClick={() => send(text)}>Ask</button>}
          </div>
        </div>
      </div>
    </div>
  );
}

export default function Page() {
  return <Suspense fallback={null}><Ask /></Suspense>;
}
