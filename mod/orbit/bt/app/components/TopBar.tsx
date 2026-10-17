'use client';
/* The top bar: wallet chips + the connect popover + the theme pipe. */
import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useOverlay } from '@/lib/overlay';
import { ExtAccount, kindGlyph, useWallet } from '@/lib/wallet';
import { useData } from '@/lib/data';
import { compact, fmt, fmtPrice, looksSS58, short } from '@/lib/format';
import { Ident, Pct, SubnetLogo } from './ui';
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
  return (
    <button id="themeBtn" onClick={toggle} title={dark ? 'Light mode' : 'Dark mode'} aria-label="Toggle theme">
      {dark
        ? <svg viewBox="0 0 16 16" aria-hidden><circle cx="8" cy="8" r="3" /><path d="M8 1.5v1.6M8 12.9v1.6M1.5 8h1.6M12.9 8h1.6M3.4 3.4l1.1 1.1M11.5 11.5l1.1 1.1M3.4 12.6l1.1-1.1M11.5 4.5l1.1-1.1" /></svg>
        : <svg viewBox="0 0 16 16" aria-hidden><path d="M13.2 9.6A5.6 5.6 0 0 1 6.4 2.8a5.6 5.6 0 1 0 6.8 6.8Z" /></svg>}
    </button>
  );
}

/* skins are optional stylesheets in public/skins layered over the core look;
   the layout's first-paint script loads the saved one, this flips it live */
const SKINS = ['', 'mario'];
function SkinButton() {
  const [skin, setSkin] = useState('');
  useEffect(() => { setSkin(document.documentElement.dataset.skin || ''); }, []);
  const next = () => {
    const k = SKINS[(SKINS.indexOf(skin) + 1) % SKINS.length];
    const root = document.documentElement;
    document.getElementById('bt-skin')?.remove();
    if (k) {
      const l = document.createElement('link');
      l.rel = 'stylesheet'; l.id = 'bt-skin'; l.href = `${BASE}/skins/${k}.css`;
      document.head.appendChild(l);
      root.dataset.skin = k;
    } else delete root.dataset.skin;
    try { k ? localStorage.setItem('bt.skin', k) : localStorage.removeItem('bt.skin'); } catch { /* */ }
    setSkin(k);
  };
  return <button id="skinBtn" onClick={next} title={`Skin: ${skin || 'sleek'} — click to switch`}>{skin || 'sleek'}</button>;
}

/* flips every TAO amount in the console between τ and $, off the free-ticker
   rate — tao.app charges for the dashboard that does this */
function CcyButton() {
  const { ccy, setCcy, rate, usd } = useData();
  const tip = rate != null ? `τ = $${rate.toFixed(2)}${usd?.stale ? ' (stale)' : ''}` : 'USD rate loading…';
  return (
    <div id="ccyBtn" className="ccyseg" role="group" aria-label="Currency" title={tip}>
      <button className={ccy !== 'usd' ? 'on' : ''} onClick={() => setCcy('tao')}>τ</button>
      <button className={ccy === 'usd' ? 'on' : ''} onClick={() => setCcy('usd')}>$</button>
    </div>
  );
}

function WalletPopover() {
  const w = useWallet();
  const [msg, setMsg] = useState('');
  const [accts, setAccts] = useState<ExtAccount[]>([]);
  const [addr, setAddr] = useState(w.wallet?.addr || '');
  const [, bump] = useState(0);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const t = setTimeout(() => bump(n => n + 1), 1500);   /* extensions inject late */
    const out = (e: MouseEvent) => {
      const el = e.target as HTMLElement;
      if (ref.current && !ref.current.contains(el) && !el.closest('.wbtn')) w.setPopOpen(false);
    };
    addEventListener('click', out);
    return () => { clearTimeout(t); removeEventListener('click', out); };
  }, [w]);

  const ext = async (id: string, name: string) => {
    if (w.extPresent(id)) setMsg(`Approve ${name} in the extension…`);
    setAccts([]);
    const r = await w.extConnect(id);
    setMsg(r.msg || ''); setAccts(r.accounts || []);
  };
  const watch = () => { if (addr.trim()) w.connect(addr.trim()); };
  const router = useRouter();
  const [copied, setCopied] = useState(false);
  const wal = w.wallet;
  const copy = () => wal && navigator.clipboard?.writeText(wal.addr).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1000); });

  return (
    <div className="wpop" ref={ref}>
      {wal && (
        <div className="wme">
          <Ident addr={wal.addr} size={40} />
          <div className="wme-t">
            <b>{wal.local ? wal.name : wal.ext ? (wal.name || wal.ext) : 'Watch-only'}</b>
            <button className="wme-a" onClick={copy} title="Copy address">{copied ? 'Copied ✓' : short(wal.addr) + '  · copy'}</button>
          </div>
          <div className="wme-v">
            <b>{wal.tao == null ? 'τ —' : 'τ ' + compact(wal.tao)}</b>
            {wal.free != null && <span>{compact(wal.free)} free · {compact(wal.staked)} staked</span>}
          </div>
        </div>
      )}
      {wal && (
        <div className="wme-act">
          <button className="pill primary" onClick={() => { w.setPopOpen(false); router.push('/wallet'); }}>Open wallet</button>
          <button className={'pill' + (w.refreshing ? ' busy' : '')} onClick={() => w.refresh()}>{w.refreshing ? 'Refreshing…' : 'Refresh'}</button>
          <button className="pill ghost" onClick={() => { w.disconnect(); w.setPopOpen(false); }}>Disconnect</button>
        </div>
      )}
      <label style={{ marginTop: wal ? 16 : 0 }}>{wal ? 'Switch wallet' : 'Connect a browser wallet'}</label>
      <div className="wext">
        {w.extList().map(e => {
          const on = e.installed;
          return (
            <button key={e.id} className={on ? 'on' : 'off'} onClick={() => ext(e.id, e.name)}
                    title={on ? `Connect ${e.name}` : `${e.name} not detected — install it`}>
              <span className="dot" />{e.name}{on ? '' : ' ↗'}
            </button>
          );
        })}
      </div>
      {msg && <div className="wmsg">{msg}</div>}
      {!msg && w.note && <div className="wmsg">{w.note}</div>}
      {accts.length > 0 && (
        <div className="wacct">
          {accts.map(a => (
            <button key={a.addr} onClick={() => w.connect(a.addr, a.name, false, a.ext, a.extId)}>
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
        <button className="iconbtn" onClick={() => w.setPopOpen(false)}>Close</button>
      </div>
    </div>
  );
}

/* jump anywhere: subnets by name / symbol / netuid, any ss58 → its trader
   view. ⌘K / Ctrl+K focuses it from every page. */
function Jump() {
  const { screener } = useData();
  const { openSubnet, openTrader } = useOverlay();
  const [q, setQ] = useState('');
  const [open, setOpen] = useState(false);
  const [sel, setSel] = useState(0);
  const box = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); box.current?.focus(); box.current?.select(); }
    };
    addEventListener('keydown', k);
    return () => removeEventListener('keydown', k);
  }, []);

  const t = q.trim().toLowerCase();
  const hits = !t ? [] : (screener?.rows || []).filter(r =>
    String(r.netuid) === t.replace(/^sn/, '') || (r.name || '').toLowerCase().includes(t)
    || String(r.symbol || '').toLowerCase() === t).slice(0, 7);
  const addr = looksSS58(q) ? q.trim() : '';
  const n = hits.length + (addr ? 1 : 0);

  const go = (i: number) => {
    if (addr && i === hits.length) openTrader(addr);
    else if (hits[i]) openSubnet(hits[i].netuid);
    else return;
    setQ(''); setOpen(false); box.current?.blur();
  };

  return (
    <div className="jump">
      <svg className="jump-ic" viewBox="0 0 16 16" aria-hidden><circle cx="7" cy="7" r="4.6" /><path d="M10.4 10.4 14 14" /></svg>
      <input ref={box} value={q} placeholder="Search subnets or paste an address" spellCheck={false}
             onChange={e => { setQ(e.target.value); setSel(0); setOpen(true); }}
             onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 120)}
             onKeyDown={e => {
               if (e.key === 'ArrowDown') { e.preventDefault(); setSel(s => Math.min(n - 1, s + 1)); }
               else if (e.key === 'ArrowUp') { e.preventDefault(); setSel(s => Math.max(0, s - 1)); }
               else if (e.key === 'Enter') go(sel);
               else if (e.key === 'Escape') { setQ(''); box.current?.blur(); }
             }} />
      {!q && <kbd className="jump-k">⌘K</kbd>}
      {open && t && (
        <div className="jump-menu">
          {hits.map((r, i) => (
            <button key={r.netuid} className={i === sel ? 'on' : ''} onMouseDown={() => go(i)} onMouseEnter={() => setSel(i)}>
              <SubnetLogo logo={r.logo} symbol={r.symbol} />
              <span className="jm-n">{r.name || 'Subnet ' + r.netuid}<em>SN{r.netuid}</em></span>
              <span className="jm-p">τ {fmtPrice(r.price)}</span>
              <Pct v={r.change_24h} />
            </button>
          ))}
          {addr && (
            <button className={sel === hits.length ? 'on' : ''} onMouseDown={() => go(hits.length)} onMouseEnter={() => setSel(hits.length)}>
              <Ident addr={addr} size={22} /><span className="jm-n">Open trader<em>{short(addr)}</em></span>
            </button>
          )}
          {!n && <div className="jm-none">No subnet matches “{q.trim()}”</div>}
        </div>
      )}
    </div>
  );
}

function WalletButton() {
  const w = useWallet();
  const wal = w.wallet;
  if (!wal) return (
    <button className="wbtn connect" onClick={() => w.setPopOpen(!w.popOpen)}>Connect wallet</button>
  );
  const tag = wal.local ? wal.name : wal.ext ? (wal.name || wal.ext) : 'watching';
  return (
    <button className={'wbtn' + (w.popOpen ? ' on' : '')} onClick={() => w.setPopOpen(!w.popOpen)}
            title={wal.free != null ? `free τ ${fmt(wal.free, 4)} · staked τ ${fmt(wal.staked, 4)}` : wal.addr}>
      <Ident addr={wal.addr} size={22} />
      <span className="wb-t"><b>{tag}</b><i>{short(wal.addr)}</i></span>
      <span className={'wb-v' + (wal.tao ? '' : ' zero')}>{wal.tao == null ? 'τ —' : `τ ${fmt(wal.tao, 3)}`}</span>
      <svg className="wb-c" viewBox="0 0 10 10" aria-hidden><path d="M2.5 4 5 6.5 7.5 4" /></svg>
    </button>
  );
}

export default function TopBar() {
  const w = useWallet();
  return (
    <nav>
      <div className="nav-inner">
        <a className="nav-logo" href={BASE || '/'}><span className="rb-mark">τ</span></a>
        <Jump />
        <span className="grow" />
        <CcyButton />
        <ThemeButton />
        <SkinButton />
        <WalletButton />
      </div>
      {w.popOpen && <WalletPopover />}
    </nav>
  );
}
