'use client';
/* ChatDock — the chat on every page that is not /chat.
 *
 *   closed  → a launcher button in the bottom-right corner
 *   float   → a window that grows out of that corner
 *   side    → a full-height sidebar on the right; the page makes room for it
 *             (html[data-dock=side] + --dock), drag its left edge to resize
 *
 * Mode, open state and width persist in localStorage `bt.dock`. Ctrl/⌘+J
 * toggles it. Starting a new chat anywhere (chat.newChat → lib/dock signal)
 * opens it as the sidebar. When the agent navigates away mid-answer (bt_view) the dock
 * opens itself so the stream stays in view — this replaced "← back to chat". */
import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useChat } from '@/lib/chat';
import ChatThread from './ChatThread';
import { DOCK_EVENT, DockMode, DockRequest } from '@/lib/dock';

type Mode = DockMode;
interface DockState { open: boolean; mode: Mode; w: number }

const KEY = 'bt.dock';
const MIN_W = 320, MAX_W = 760;
const DEFAULT: DockState = { open: false, mode: 'side', w: 400 };
const clampW = (w: number) => Math.max(MIN_W, Math.min(MAX_W, Math.round(w) || DEFAULT.w));

function load(): DockState {
  try {
    const s = JSON.parse(localStorage.getItem(KEY) || '{}');
    return { open: !!s.open, mode: s.mode === 'float' ? 'float' : 'side', w: clampW(s.w ?? DEFAULT.w) };
  } catch { return DEFAULT; }
}

export default function ChatDock() {
  const c = useChat();
  const path = usePathname();
  const [st, setSt] = useState<DockState>(DEFAULT);
  const [ready, setReady] = useState(false);       /* localStorage read after mount — no hydration drift */
  const [hist, setHist] = useState(false);
  const wasBack = useRef(false);

  useEffect(() => { setSt(load()); setReady(true); }, []);
  useEffect(() => {
    if (ready) try { localStorage.setItem(KEY, JSON.stringify(st)); } catch { /* */ }
  }, [st, ready]);

  const onChatPage = path === '/chat';
  const open = ready && st.open && !onChatPage;
  const side = open && st.mode === 'side';

  /* the page makes room for the sidebar */
  useEffect(() => {
    const root = document.documentElement;
    if (side) { root.dataset.dock = 'side'; root.style.setProperty('--dock', st.w + 'px'); }
    else { delete root.dataset.dock; root.style.removeProperty('--dock'); }
  }, [side, st.w]);

  const set = useCallback((p: Partial<DockState>) => setSt(s => ({ ...s, ...p })), []);

  /* someone asked for the dock (a new chat was started) */
  useEffect(() => {
    const on = (e: Event) => set((e as CustomEvent<DockRequest>).detail || { open: true });
    addEventListener(DOCK_EVENT, on);
    return () => removeEventListener(DOCK_EVENT, on);
  }, [set]);

  /* agent took us somewhere else mid-answer → keep the conversation on screen */
  useEffect(() => {
    if (c.showBack && !wasBack.current) set({ open: true });
    wasBack.current = c.showBack;
  }, [c.showBack, set]);

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'j') { e.preventDefault(); setSt(s => ({ ...s, open: !s.open })); }
      else if (e.key === 'Escape' && st.open && st.mode === 'float' && !document.querySelector('.ovl.open')) set({ open: false });
    };
    addEventListener('keydown', key);
    return () => removeEventListener('keydown', key);
  }, [st.open, st.mode, set]);

  /* drag the sidebar's left edge */
  const drag = (e: React.PointerEvent) => {
    e.preventDefault();
    const move = (ev: PointerEvent) => set({ w: clampW(innerWidth - ev.clientX) });
    const up = () => { removeEventListener('pointermove', move); removeEventListener('pointerup', up); document.body.style.userSelect = ''; };
    document.body.style.userSelect = 'none';
    addEventListener('pointermove', move); addEventListener('pointerup', up);
  };

  if (!ready || onChatPage) return null;

  if (!open) {
    return (
      <button className={'dock-fab' + (c.running ? ' live' : '')} onClick={() => set({ open: true })}
              title="Chat with the network (Ctrl+J)" aria-label="Open chat">
        <span className="dock-fab-dot" />
        {c.running ? 'answering…' : 'Ask'}
      </button>
    );
  }

  const title = (c.id && c.chats.find(x => x.id === c.id)?.title) || 'New chat';
  return (
    <aside className={'dock ' + st.mode} style={st.mode === 'side' ? { width: st.w } : undefined} aria-label="Chat">
      {st.mode === 'side' && <div className="dock-grip" onPointerDown={drag} title="Drag to resize" />}
      <header className="dock-head">
        <button className="dock-title" onClick={() => setHist(h => !h)} title="Conversations">
          <span className={'dock-fab-dot' + (c.running ? ' live' : '')} />
          <span className="t">{title}</span>
          <span className="caret">{hist ? '▴' : '▾'}</span>
        </button>
        <div className="dock-acts">
          <button onClick={() => { c.newChat(); setHist(false); }} disabled={c.running} title="New chat">+</button>
          <button onClick={() => set({ mode: st.mode === 'side' ? 'float' : 'side' })}
                  title={st.mode === 'side' ? 'Pop out to the corner' : 'Dock as a sidebar'}>
            {st.mode === 'side' ? '◲' : '◨'}
          </button>
          <Link href="/chat" title="Full page" onClick={() => set({ open: false })}>⤢</Link>
          <button onClick={() => set({ open: false })} title="Close (Ctrl+J)">✕</button>
        </div>
      </header>
      {hist && (
        <div className="dock-hist">
          {c.chats.length ? c.chats.map(ch => (
            <div key={ch.id} className={'chat-item' + (ch.id === c.id ? ' sel' : '')}
                 onClick={() => { c.openChat(ch.id, false); setHist(false); }}>
              <span className="t" title={ch.title}>{ch.title}</span>
              <span className="x" title="Delete" onClick={e => { e.stopPropagation(); c.deleteChat(ch.id); }}>✕</span>
            </div>
          )) : <span className="muted">No conversations yet.</span>}
        </div>
      )}
      <ChatThread compact focusKey={st.mode} />
    </aside>
  );
}
