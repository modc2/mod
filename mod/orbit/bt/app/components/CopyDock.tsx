'use client';
/* CopyDock — copy trading + strats as a right-hand sidebar, toggled by the
 * user (top-bar "Copy" button, the rail's Copy entry, or any openCopy() call
 * such as a Traders row's Copy button). Two tabs: Copy (components/CopyPanel,
 * compact) and Strats (components/Strats). Open state, tab and width persist
 * in localStorage `bt.copydock`. When the chat sidebar is also open the two
 * sit side by side: this one docks left of it (--dock) and the page pads for
 * both (html[data-cdock] + --cdock). Hidden on /copy, which is the full page. */
import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { COPYDOCK_EVENT, CopyDockRequest, CopyTab } from '@/lib/ct';
import CopyPanel from './CopyPanel';
import Strats from './Strats';
import { Tabs } from './ui';

interface St { open: boolean; tab: CopyTab; w: number }
const KEY = 'bt.copydock';
const MIN_W = 340, MAX_W = 720;
const DEFAULT: St = { open: false, tab: 'copy', w: 420 };
const clampW = (w: number) => Math.max(MIN_W, Math.min(MAX_W, Math.round(w) || DEFAULT.w));

function load(): St {
  try {
    const s = JSON.parse(localStorage.getItem(KEY) || '{}');
    return { open: !!s.open, tab: s.tab === 'strats' ? 'strats' : 'copy', w: clampW(s.w ?? DEFAULT.w) };
  } catch { return DEFAULT; }
}

export default function CopyDock() {
  const path = usePathname();
  const [st, setSt] = useState<St>(DEFAULT);
  const [ready, setReady] = useState(false);
  const [target, setTarget] = useState('');

  useEffect(() => { setSt(load()); setReady(true); }, []);
  useEffect(() => {
    if (ready) try { localStorage.setItem(KEY, JSON.stringify(st)); } catch { /* */ }
  }, [st, ready]);

  const set = useCallback((p: Partial<St>) => setSt(s => ({ ...s, ...p })), []);
  const open = ready && st.open && path !== '/copy';

  useEffect(() => {
    const root = document.documentElement;
    if (open) { root.dataset.cdock = '1'; root.style.setProperty('--cdock', st.w + 'px'); }
    else { delete root.dataset.cdock; root.style.removeProperty('--cdock'); }
    /* the rail/top-bar buttons light up off this */
    dispatchEvent(new CustomEvent('bt:copydock-state', { detail: open }));
  }, [open, st.w]);

  useEffect(() => {
    const on = (e: Event) => {
      const d = (e as CustomEvent<CopyDockRequest>).detail || {};
      if (d.target) setTarget(d.target);
      setSt(s => ({ ...s, open: d.open === 'toggle' ? !s.open : d.open ?? s.open, tab: d.tab || (d.target ? 'copy' : s.tab) }));
    };
    addEventListener(COPYDOCK_EVENT, on);
    return () => removeEventListener(COPYDOCK_EVENT, on);
  }, []);

  const drag = (e: React.PointerEvent) => {
    e.preventDefault();
    const right = (e.currentTarget.parentElement as HTMLElement).getBoundingClientRect().right;
    const move = (ev: PointerEvent) => set({ w: clampW(right - ev.clientX) });
    const up = () => { removeEventListener('pointermove', move); removeEventListener('pointerup', up); document.body.style.userSelect = ''; };
    document.body.style.userSelect = 'none';
    addEventListener('pointermove', move); addEventListener('pointerup', up);
  };

  if (!open) return null;
  return (
    <aside className="cdock" style={{ width: st.w }} aria-label="Copy trading">
      <div className="dock-grip" onPointerDown={drag} title="Drag to resize" />
      <header className="dock-head">
        <Tabs value={st.tab} options={[['copy', 'Copy'], ['strats', 'Strats']]} onChange={tab => set({ tab })} />
        <span className="grow" />
        <div className="dock-acts">
          <Link href={target ? `/copy?target=${target}` : '/copy'} title="Full page">⤢</Link>
          <button onClick={() => set({ open: false })} title="Close">✕</button>
        </div>
      </header>
      <div className="cdock-body">
        {st.tab === 'copy'
          ? <CopyPanel compact target={target} />
          : <Strats onCopy={ss58 => { setTarget(ss58); set({ tab: 'copy' }); }} />}
      </div>
    </aside>
  );
}
