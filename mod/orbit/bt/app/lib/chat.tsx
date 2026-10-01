'use client';
/* Chat with the network — the agent protocol client.
 *
 * Held in the root layout, not the /chat page: when the agent calls bt_view
 * the console navigates (markets, an account, …) while the answer is still
 * streaming, and the run must keep going underneath. Messages are plain data
 * here; components/chat renders them. Server side is bt/agent.py over SSE:
 * start · status · text_delta · text · tool · tool_done · view · done · error. */
import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { api, getJSON, postJSON, ViewAction } from './api';
import { useData } from './data';
import { useOverlay } from './overlay';
import { short } from './format';

export interface ToolChip {
  id: string; name: string; args: Record<string, unknown>; builtin?: boolean;
  state: 'pending' | 'done' | 'err'; ms?: number | null; preview?: string;
}
export interface Msg {
  role: 'user' | 'bot'; text: string;
  tools: ToolChip[]; views: ViewAction[];
  status?: string; error?: string;
  done?: { turns?: number; ms?: number; cost_usd?: number };
}
export interface ChatSummary { id: string; title: string; turns?: number; updated_at?: number }
export interface AgentStatus {
  ready: boolean; model?: string; tools?: number; denied?: number; method?: string;
  hint?: string; starters?: string[];
}

const CHAT_KEY = 'bt.chat.id';

export const viewLabel = (a: ViewAction) => {
  if (a.view === 'subnet') return `open subnet ${a.netuid}`;
  if (a.view === 'trader') return `open trader ${short(a.address)}`;
  if (a.view === 'account') return `open account ${short(a.address)}`;
  if (a.view === 'markets') return 'open markets' + (a.sort_by ? ' by ' + a.sort_by : '')
    + (a.search ? ` "${a.search}"` : '');
  return 'open ' + a.view;
};

/* bt_view names → console routes */
const VIEW_ROUTES: Record<string, string> = {
  markets: '/markets', traders: '/traders', account: '/account', wallet: '/wallet',
  trade: '/trade', docs: '/docs', mcp: '/mcp', chat: '/chat', console: '/console', open: '/open',
};

interface ChatCtx {
  id: string | null; msgs: Msg[]; running: boolean; chats: ChatSummary[];
  agent: AgentStatus | null; showBack: boolean;
  send: (text: string) => Promise<void>; stop: () => void;
  newChat: () => void; openChat: (id: string, nav?: boolean) => Promise<void>;
  deleteChat: (id: string) => Promise<void>; applyView: (a: ViewAction) => void;
}

const Ctx = createContext<ChatCtx | null>(null);

function fromStored(m: any): Msg {
  if (m.role === 'user') return { role: 'user', text: m.text || '', tools: [], views: [] };
  const meta = m.meta || {};
  return {
    role: 'bot', text: m.text || '',
    tools: (m.tools || []).map((t: any, i: number) => ({
      id: `s${i}`, name: t.name, args: t.args || {}, builtin: t.builtin,
      state: t.ok === false ? 'err' : 'done', ms: t.ms })),
    views: meta.views || [],
    done: meta.turns ? { turns: meta.turns, ms: meta.ms, cost_usd: meta.cost_usd } : undefined,
  };
}

export function ChatProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { setSearch, setSort } = useData();
  const overlay = useOverlay();

  const [id, setId] = useState<string | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [running, setRunning] = useState(false);
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [agent, setAgent] = useState<AgentStatus | null>(null);
  const [showBack, setShowBack] = useState(false);
  const idRef = useRef<string | null>(null);
  const ctlRef = useRef<AbortController | null>(null);
  const touched = useRef(false);
  const runningRef = useRef(false);
  const ctxRef = useRef({ pathname, subnet: overlay.subnet, trader: overlay.trader });
  ctxRef.current = { pathname, subnet: overlay.subnet, trader: overlay.trader };

  const remember = (cid: string | null) => {
    idRef.current = cid; setId(cid);
    try { cid ? localStorage.setItem(CHAT_KEY, cid) : localStorage.removeItem(CHAT_KEY); } catch { /* */ }
  };

  const loadChats = useCallback(async () => {
    try { setChats((await getJSON<{ chats: ChatSummary[] }>('agent/chats?limit=40')).chats || []); } catch { /* */ }
  }, []);

  const openChat = useCallback(async (cid: string, nav = true) => {
    if (runningRef.current) return;
    if (nav) touched.current = true;
    try {
      const c = await getJSON<any>('agent/chats/' + cid);
      if (!c || !c.id) return;
      remember(c.id);
      setMsgs((c.messages || []).map(fromStored));
      loadChats();
      if (nav) router.push('/chat');
    } catch { /* */ }
  }, [loadChats, router]);

  /* boot: agent status, then the last conversation unless one was started */
  useEffect(() => {
    getJSON<AgentStatus>('agent/status').then(setAgent).catch(() => setAgent({ ready: false }));
    let last: string | null = null;
    try { last = localStorage.getItem(CHAT_KEY); } catch { /* */ }
    (async () => {
      if (last && !touched.current) await openChat(last, false);
      loadChats();
    })();
  }, [openChat, loadChats]);

  useEffect(() => { if (pathname === '/chat') setShowBack(false); }, [pathname]);

  const applyView = useCallback((a: ViewAction) => {
    if (!a || !a.view) return;
    if (a.view === 'subnet' && a.netuid != null) { overlay.openSubnet(+a.netuid, a.tab); return; }
    if (a.view === 'trader' && a.address) { overlay.openTrader(a.address); return; }
    overlay.close();
    if (a.view === 'markets') {
      if (a.search !== undefined) setSearch(a.search || '');
      if (a.sort_by) setSort({ key: a.sort_by, dir: -1 });
    }
    const to = a.view === 'account' && a.address
      ? `/account?addr=${encodeURIComponent(a.address)}` : VIEW_ROUTES[a.view];
    if (!to) return;          /* never a hard reload mid-answer — it would kill the stream */
    router.push(to);
    if (idRef.current) setShowBack(true);
  }, [overlay, router, setSearch, setSort]);

  /* patch the newest bot message */
  const patchBot = (fn: (m: Msg) => Msg) => setMsgs(ms => {
    const i = ms.length - 1;
    if (i < 0 || ms[i].role !== 'bot') return ms;
    const copy = ms.slice(); copy[i] = fn(ms[i]); return copy;
  });

  const send = useCallback(async (text: string) => {
    const q = text.trim();
    if (!q || runningRef.current) return;
    touched.current = true;
    runningRef.current = true; setRunning(true);
    setMsgs(ms => [...ms, { role: 'user', text: q, tools: [], views: [] },
                         { role: 'bot', text: '', tools: [], views: [], status: 'thinking' }]);
    const ctl = new AbortController(); ctlRef.current = ctl;
    const c = ctxRef.current;
    const context: Record<string, unknown> = { view: c.pathname.replace(/^\//, '') || 'top' };
    if (c.subnet != null) context.netuid = c.subnet;
    if (c.trader) context.address = c.trader;

    const handle = (ev: any) => {
      if (ev.chat && ev.chat !== idRef.current) remember(ev.chat);
      switch (ev.type) {
        case 'start': patchBot(m => ({ ...m, status: `${ev.model || ''} · ${ev.tools || 0} tools` })); break;
        case 'status': patchBot(m => ({ ...m, status: ev.status })); break;
        case 'text_delta': patchBot(m => ({ ...m, text: m.text + (ev.delta || ''), status: '' })); break;
        case 'text': patchBot(m => ({ ...m, text: m.text + (ev.text || ''), status: '' })); break;
        case 'tool': patchBot(m => ({ ...m, status: 'reading the chain', tools: [...m.tools, {
          id: ev.id || `t${m.tools.length}`, name: ev.name, args: ev.args || {}, builtin: ev.builtin,
          state: 'pending' }] })); break;
        case 'tool_done': patchBot(m => {
          let i = m.tools.findIndex(t => ev.id && t.id === ev.id);
          if (i < 0) i = m.tools.findIndex(t => t.state === 'pending');
          if (i < 0) return m;
          const tools = m.tools.slice();
          tools[i] = { ...tools[i], state: ev.error ? 'err' : 'done', ms: ev.ms, preview: ev.preview || '' };
          return { ...m, tools };
        }); break;
        case 'view': patchBot(m => ({ ...m, views: [...m.views, ev.action] })); applyView(ev.action); break;
        case 'done': patchBot(m => ({
          ...m, status: '', text: m.text.trim() ? m.text : (ev.answer || ''),
          tools: m.tools.map(t => (t.state === 'pending' ? { ...t, state: 'done' } : t)),
          done: { turns: ev.turns, ms: ev.ms, cost_usd: ev.cost_usd } })); break;
        case 'error': patchBot(m => ({
          ...m, status: '', error: ev.error,
          tools: m.tools.map(t => (t.state === 'pending' ? { ...t, state: 'done' } : t)) })); break;
      }
    };

    try {
      const r = await fetch(api('agent/chat'), {
        method: 'POST', headers: { 'content-type': 'application/json' }, signal: ctl.signal,
        body: JSON.stringify({ message: q, chat: idRef.current, context }),
      });
      if (!r.ok || !r.body) {
        handle({ type: 'error', error: (await r.json().catch(() => ({}))).error || `HTTP ${r.status}` });
      } else {
        const reader = r.body.getReader(), dec = new TextDecoder();
        let buf = '';
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          let i;
          while ((i = buf.indexOf('\n\n')) >= 0) {
            const chunk = buf.slice(0, i).trim(); buf = buf.slice(i + 2);
            if (chunk.startsWith('data: ')) { try { handle(JSON.parse(chunk.slice(6))); } catch { /* */ } }
          }
        }
      }
    } catch (e) {
      handle({ type: 'error', error: (e as Error).name === 'AbortError' ? 'stopped' : (e as Error).message });
    }
    runningRef.current = false; setRunning(false); ctlRef.current = null;
    loadChats();
  }, [applyView, loadChats]);

  const stop = useCallback(() => {
    if (!runningRef.current) return;
    if (idRef.current) postJSON('agent/stop', { chat: idRef.current }).catch(() => {});
    ctlRef.current?.abort();
  }, []);

  const newChat = useCallback(() => {
    if (runningRef.current) return;
    touched.current = true;
    remember(null); setMsgs([]); setShowBack(false);
    loadChats();
  }, [loadChats]);

  const deleteChat = useCallback(async (cid: string) => {
    await fetch(api('agent/chats/' + cid), { method: 'DELETE' }).catch(() => {});
    if (idRef.current === cid) newChat(); else loadChats();
  }, [newChat, loadChats]);

  return (
    <Ctx.Provider value={{ id, msgs, running, chats, agent, showBack, send, stop, newChat,
                           openChat, deleteChat, applyView }}>
      {children}
    </Ctx.Provider>
  );
}

export function useChat() {
  const c = useContext(Ctx);
  if (!c) throw new Error('useChat outside ChatProvider');
  return c;
}
