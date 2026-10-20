'use client';
/* ztensor console. The chain list, its colors, nets, ring caps and cost
 * units all come from GET networks — drop a new adapter under
 * src/networks/<chain>/ and it gets a button, a theme and every tab. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Board as B, Chain } from '@/lib/api';
import { get, usd } from '@/lib/api';
import Live from '@/components/Live';
import Board from '@/components/Board';
import Costs from '@/components/Costs';
import Code from '@/components/Code';
import Tests from '@/components/Tests';
import Paper from '@/components/Paper';

const TABS = ['live', 'subnet', 'costs', 'code', 'tests', 'paper'] as const;
type Tab = typeof TABS[number];
const LABEL: Record<Tab, string> = { live: 'Live', subnet: 'Subnet', costs: 'Costs', code: 'Code', tests: 'Tests', paper: 'Paper' };

const rgb = (hex?: string) => {
  const h = (hex || '#14b8a6').replace('#', '');
  const f = h.length === 3 ? h.split('').map((c) => c + c).join('') : h;
  const n = parseInt(f.slice(0, 6), 16);
  return `${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}`;
};

export default function Page() {
  const [chains, setChains] = useState<Chain[]>([]);
  const [sel, setSel] = useState<string>('');
  const [net, setNet] = useState<string>('');
  const [tab, setTab] = useState<Tab>('live');
  const [health, setHealth] = useState<any>(null);
  const [board, setBoard] = useState<B | null>(null);
  const [boardErr, setBoardErr] = useState('');
  const [eco, setEco] = useState<any>(null);
  const [costs, setCosts] = useState<any>(null);
  const [tests, setTests] = useState<any>(null);
  const [info, setInfo] = useState<any>(null);
  const [local, setLocal] = useState(false);
  const [fatal, setFatal] = useState('');
  const [netOpen, setNetOpen] = useState(false);
  const netRef = useRef<HTMLDivElement>(null);

  // url: #<tab>/<chain>
  useEffect(() => {
    const read = () => {
      const [t, c] = window.location.hash.replace('#', '').split('/');
      if ((TABS as readonly string[]).includes(t)) setTab(t as Tab);
      if (c) setSel(c);
    };
    read();
    window.addEventListener('hashchange', read);
    return () => window.removeEventListener('hashchange', read);
  }, []);

  useEffect(() => {
    get<Chain[]>('networks').then((n) => {
      setChains(n);
      setSel((s) => (n.some((c) => c.chain === s) ? s
        : n.find((c) => c.chain === localStorage.getItem('ztensor.chain'))?.chain || n[0]?.chain || ''));
    }).catch((e) => setFatal(e.message));
    get('whoami').then((w) => setLocal(!!w.local)).catch(() => {});
    get('info').then(setInfo).catch(() => {});
  }, []);

  const chain = useMemo(() => chains.find((c) => c.chain === sel), [chains, sel]);

  useEffect(() => {
    if (!chain) return;
    localStorage.setItem('ztensor.chain', chain.chain);
    setNet(chain.net);
    const want = `#${tab}/${chain.chain}`;
    if (window.location.hash !== want) history.replaceState(null, '', want);
  }, [chain]); // eslint-disable-line react-hooks/exhaustive-deps

  const go = (t: Tab) => { setTab(t); if (chain) history.replaceState(null, '', `#${t}/${chain.chain}`); };

  const refreshSlow = useCallback(() => {
    get('economics').then(setEco).catch(() => {});
    get('costs').then(setCosts).catch(() => {});
    get('tests').then(setTests).catch(() => {});
  }, []);
  useEffect(() => { refreshSlow(); }, [refreshSlow]);
  // phone: the tab strip scrolls sideways, keep the open tab in view
  useEffect(() => {
    const nav = document.querySelector<HTMLElement>('.tabs');
    const on = nav?.querySelector<HTMLElement>('button.on');
    if (nav && on && nav.scrollWidth > nav.clientWidth) nav.scrollTo({ left: on.offsetLeft - 24, behavior: 'smooth' });
  }, [tab]);

  useEffect(() => {
    if (!netOpen) return;
    const close = (e: MouseEvent) => { if (!netRef.current?.contains(e.target as Node)) setNetOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setNetOpen(false); };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', esc);
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', esc); };
  }, [netOpen]);

  // sticky things under the header (paper contents) sit at --toph
  useEffect(() => {
    const top = document.querySelector<HTMLElement>('header.top');
    if (!top) return;
    const ro = new ResizeObserver(() => document.documentElement.style.setProperty('--toph', `${top.offsetHeight}px`));
    ro.observe(top);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const h = () => get('health').then(setHealth).catch(() => {});
    h();
    const id = window.setInterval(h, 15000);
    return () => clearInterval(id);
  }, []);

  const loadBoard = useCallback(() => {
    if (!net) return;
    get<B>('board', { network: net }).then((b) => { setBoard(b); setBoardErr(''); })
      .catch((e) => { setBoard(null); setBoardErr(e.message); });
  }, [net]);
  useEffect(() => {
    setBoard(null);
    loadBoard();
    const id = window.setInterval(loadBoard, 8000);
    return () => clearInterval(id);
  }, [loadBoard]);

  // node asleep: the hero still shows the epoch the last recorded run left behind
  const [lastEpoch, setLastEpoch] = useState<number | undefined>();
  const asleep = !!board && !board.devnet_up && board.epoch === undefined;
  useEffect(() => {
    setLastEpoch(undefined);
    if (!asleep || !chain) return;
    let live = true;
    get('last_sim', { chain: chain.chain }).then((d) => { if (live) setLastEpoch(d?.result?.epoch); }).catch(() => {});
    return () => { live = false; };
  }, [asleep, chain]);

  const onSimDone = useCallback(() => { loadBoard(); refreshSlow(); }, [loadBoard, refreshSlow]);

  const style = { '--c': chain?.color || '#14b8a6', '--c-rgb': rgb(chain?.color) } as React.CSSProperties;
  useEffect(() => {
    document.documentElement.style.setProperty('--c', chain?.color || '#14b8a6');
    document.documentElement.style.setProperty('--c-rgb', rgb(chain?.color));
  }, [chain]);

  const up = (c: string) => health?.[c]?.up ?? (c === 'starknet' ? health?.devnet_up : undefined);
  // adapter order (GET networks), never re-sorted by node status: a tile must
  // not jump when its node sleeps or wakes, the dot already says which is up
  const ordered = chains;
  const suite = chain ? tests?.[chain.chain] : null;
  const passed = Object.values(tests || {}).reduce((a: number, s: any) => a + (s?.passed || 0), 0);
  const scoreUsd = chain ? eco?.chains?.[chain.chain]?.actions?.score?.usd : undefined;

  return (
    <div style={style}>
      <div className="backdrop"><div className="glow g1" /><div className="glow g2" /><div className="grid" /></div>
      <div className="shell">
        <header className="top">
          <a className="brand" href="#live" onClick={(e) => { e.preventDefault(); go('live'); }}>
            <span className="mark">Z</span><span className="word">ztensor</span>
            {info?.version && <span className="ver">v{info.version}</span>}
          </a>
          <nav className="tabs">
            {TABS.map((t) => (
              <button key={t} className={tab === t ? 'on' : ''} onClick={() => go(t)}>
                {LABEL[t]}{t === 'tests' && passed ? <span className="cnt">{passed}</span> : null}
              </button>
            ))}
          </nav>
          <div className="netdd" ref={netRef}>
            <button className="netbtn" aria-haspopup="listbox" aria-expanded={netOpen}
              style={chain ? { '--cc': rgb(chain.color) } as React.CSSProperties : undefined}
              onClick={() => setNetOpen((o) => !o)}>
              <span className={`dot${up(sel) === true ? ' up' : up(sel) === false ? ' down' : ''}`} />
              <span className="lbl">Network</span>
              <b>{chain?.title ?? '—'}</b>
              {chain && <span className="info">{chain.local ?? chain.net} · ring ≤ {chain.max_ring}</span>}
              <span className="chev">▾</span>
            </button>
            {netOpen && (
              <div className="netmenu" role="listbox" aria-label="Network">
                {ordered.map((c) => {
                  const u = up(c.chain);
                  const on = c.chain === sel;
                  return (
                    <button key={c.chain} role="option" aria-selected={on} className={on ? 'on' : ''}
                      style={{ '--cc': rgb(c.color) } as React.CSSProperties}
                      onClick={() => { setSel(c.chain); setNetOpen(false); history.replaceState(null, '', `#${tab}/${c.chain}`); }}>
                      <span className={`dot${u === true ? ' up' : u === false ? ' down' : ''}`} />
                      <span className="nm">{c.title}</span>
                      <span className="sub">{c.local ?? c.net} · ring ≤ {c.max_ring}</span>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
          <div className="spacer" />
          <span className={`who${local ? ' local' : ''}`} title={local ? 'on the anchor host: key-touching fns allowed' : 'public: reads + sandbox runs only'}>
            {local ? 'anchor host' : 'public · read-only'}
          </span>
        </header>

        {fatal && <div className="err">API unreachable: {fatal}</div>}

        {/* costs leads: the $ per action is the point of the tab, hero + chains follow */}
        {tab === 'costs' && <Costs chains={chains} chain={chain} eco={eco} costs={costs}
          onPick={(c) => { setSel(c); history.replaceState(null, '', `#${tab}/${c}`); }} />}

        <section className={`hero${tab === 'live' ? '' : ' compact'}`}>
          <div>
            <h1>Anonymous subnets<br /> <span className="grad">on {chain?.title ?? 'any chain'}.</span></h1>
            <p>
              Miners and validators are public <b>sets</b>, never public identities. Every action is a ring signature
              made once by a Cairo prover and verified {chain?.verifier ?? 'on chain'}. Zuma consensus clips the outliers;
              the books stay public.
            </p>
          </div>
          <div className="stats">
            <div className="stat"><div className="k">Epoch</div><div className="v" style={board?.epoch === undefined && lastEpoch !== undefined ? { opacity: 0.55 } : undefined}>{board?.epoch ?? lastEpoch ?? '—'}</div><div className="s">{board?.blocks_left !== undefined ? `${board.blocks_left} blocks left` : board && !board.devnet_up ? (lastEpoch !== undefined ? 'last run · asleep' : `${net} · asleep`) : net}</div></div>
            <div className="stat"><div className="k">Max ring</div><div className="v">{chain?.max_ring ?? '—'}</div><div className="s">anonymity set</div></div>
            <div className="stat"><div className="k">$ / score</div><div className="v">{usd(scoreUsd)}</div><div className="s">mainnet est.</div></div>
            <div className="stat"><div className="k">Suite</div><div className="v" style={{ color: suite ? (suite.ok ? 'var(--good)' : 'var(--bad)') : undefined }}>{suite ? `${suite.passed}/${(suite.passed || 0) + (suite.failed || 0)}` : '—'}</div><div className="s">{chain?.title ?? ''} native</div></div>
          </div>
        </section>

        {tab === 'live' && <Live chain={chain} board={board} local={local} onDone={onSimDone} />}
        {tab === 'subnet' && <Board chain={chain} board={board} net={net} setNet={setNet} err={boardErr} onRun={() => go('live')} />}
        {tab === 'code' && <Code chain={chain} />}
        {tab === 'tests' && <Tests chains={chains} chain={chain} tests={tests} />}
        {tab === 'paper' && <Paper />}

        <footer className="foot">
          <span>private ballots · public books · secrets never leave the member&apos;s box</span>
          <span>{chains.length} chain adapters · {info?.protocol?.scheme ? info.protocol.scheme.split(',')[0] : 'LSAG'}</span>
        </footer>
      </div>
    </div>
  );
}
