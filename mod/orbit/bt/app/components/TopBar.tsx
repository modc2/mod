'use client';
/* The top bar: wallet chips + the connect popover + the theme pipe. */
import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { EXTS, kindGlyph, useWallet } from '@/lib/wallet';
import { fmt, short } from '@/lib/format';
import { Ident } from './ui';
import { BASE } from '@/lib/api';

function ThemeButton() {
  const [dark, setDark] = useState(false);
  useEffect(() => { setDark(document.documentElement.dataset.theme === 'dark'); }, []);
  const toggle = () => {
    const t = dark ? 'light' : 'dark';
    document.documentElement.dataset.theme = t;
    try { localStorage.setItem('bt.theme', t); } catch { /* */ }
    setDark(!dark);
  };
  /* light = overworld, dark = underground; the glyph shows where the pipe goes */
  return <button id="themeBtn" onClick={toggle} title="Toggle theme" aria-label="Toggle theme">{dark ? '☀' : '☾'}</button>;
}

function WalletPopover() {
  const w = useWallet();
  const [msg, setMsg] = useState('');
  const [accts, setAccts] = useState<{ addr: string; name: string; ext: string }[]>([]);
  const [addr, setAddr] = useState(w.wallet?.addr || '');
  const [, bump] = useState(0);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const t = setTimeout(() => bump(n => n + 1), 1200);   /* extensions inject late */
    const out = (e: MouseEvent) => {
      const el = e.target as HTMLElement;
      if (ref.current && !ref.current.contains(el) && !el.closest('.wchip')) w.setPopOpen(false);
    };
    addEventListener('click', out);
    return () => { clearTimeout(t); removeEventListener('click', out); };
  }, [w]);

  const ext = async (id: string) => {
    const meta = EXTS.find(e => e.id === id)!;
    if (w.extPresent(id)) setMsg(`Approve ${meta.name} in the extension…`);
    setAccts([]);
    const r = await w.extConnect(id);
    setMsg(r.msg || ''); setAccts(r.accounts || []);
  };
  const watch = () => { if (addr.trim()) w.connect(addr.trim()); };

  return (
    <div className="wpop" ref={ref}>
      <label style={{ marginTop: 0 }}>Connect a browser wallet</label>
      <div className="wext">
        {EXTS.map(e => {
          const on = w.extPresent(e.id);
          return (
            <button key={e.id} className={on ? 'on' : 'off'} onClick={() => ext(e.id)}
                    title={on ? `Connect ${e.name}` : `${e.name} not detected — install it`}>
              <span className="dot" />{e.name}{on ? '' : ' ↗'}
            </button>
          );
        })}
      </div>
      {msg && <div className="wmsg">{msg}</div>}
      {accts.length > 0 && (
        <div className="wacct">
          {accts.map(a => (
            <button key={a.addr} onClick={() => w.connect(a.addr, a.name, false, a.ext)}>
              {a.name}<span className="a">{short(a.addr)}</span>
            </button>
          ))}
        </div>
      )}
      <div className="wlocal">
        {(w.localWallets || []).filter(l => l.coldkey).map(l => (
          <button key={l.name} onClick={() => w.connect(l.coldkey!, l.name, true)}>▣ {l.name}</button>
        ))}
        {w.recent.map(a => (
          <button key={a} onClick={() => w.connect(a)} title={a}><Ident addr={a} size={14} /> {short(a)}</button>
        ))}
      </div>
      <div className="wsep">or watch any ss58 address</div>
      <input value={addr} onChange={e => setAddr(e.target.value)} placeholder="5Grw…" spellCheck={false}
             onKeyDown={e => { if (e.key === 'Enter') watch(); }} />
      <div style={{ marginTop: 12, display: 'flex', gap: 10, alignItems: 'center' }}>
        <button className="pill primary" onClick={watch}>Watch</button>
        {w.wallet && <button className="iconbtn" onClick={() => { w.disconnect(); w.setPopOpen(false); }}>Disconnect</button>}
        <button className="iconbtn" onClick={() => w.setPopOpen(false)}>Cancel</button>
      </div>
    </div>
  );
}

export default function TopBar() {
  const w = useWallet();
  const router = useRouter();
  const [copied, setCopied] = useState(false);
  const wal = w.wallet;

  const onAddr = () => {
    if (!wal) return w.setPopOpen(!w.popOpen);
    navigator.clipboard?.writeText(wal.addr).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1000); });
  };
  const tag = wal ? (wal.local ? wal.name : wal.ext ? (wal.name || wal.ext) : '') : '';

  return (
    <nav>
      <div className="nav-inner">
        <a className="nav-logo" href={BASE || '/'}>τ bt</a>
        {!w.barHidden && (
          <div className="wbar">
            <button className="wchip badge" onClick={() => w.setPopOpen(!w.popOpen)}
                    title={wal ? (wal.local ? `Local wallet · ${wal.name}` : wal.ext ? `${wal.ext} · ${wal.name || 'connected'}` : 'Watch-only') : 'Pick a wallet'}>
              {wal ? <Ident addr={wal.addr} size={20} /> : 'τ'}
            </button>
            <button className="wchip" onClick={onAddr} title={wal ? `${wal.addr} — click to copy` : ''}>
              {!wal ? 'Connect a wallet' : copied ? 'Copied ✓' : <>
                <span className="sub">{kindGlyph(wal)}{tag ? ' ' + tag : ''}</span>{short(wal.addr)} <span className="cp">⧉</span>
              </>}
            </button>
            <button className={'wchip val' + (wal?.tao ? '' : ' zero')}
                    onClick={() => (wal ? router.push('/wallet') : w.setPopOpen(true))}
                    title={wal?.free != null ? `free τ ${fmt(wal.free, 4)} · staked τ ${fmt(wal.staked, 4)}` : ''}>
              {wal?.tao == null ? 'τ —' : `τ ${fmt(wal.tao, 3)}`}
            </button>
            <div className="wbar-actions">
              <button className={'iconbtn' + (w.refreshing ? ' spin' : '')} title="Refresh balance"
                      aria-label="Refresh balance" onClick={() => (wal ? w.refresh() : w.setPopOpen(true))}>↺</button>
              <button className="iconbtn" title="Hide wallet bar" aria-label="Hide wallet bar"
                      onClick={() => w.setBarHidden(true)}>✕</button>
            </div>
          </div>
        )}
        <ThemeButton />
      </div>
      {w.popOpen && <WalletPopover />}
    </nav>
  );
}
