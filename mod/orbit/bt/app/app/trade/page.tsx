'use client';
/* The trade desk — two ways to sign.
 *
 *  ◈ Browser wallet (SubWallet, Talisman, polkadot{.js}): the node composes
 *    the extrinsic, the wallet shows it and signs, the node broadcasts. Your
 *    keys never leave the extension. Buy/sell are limit orders by default.
 *  ▣ Local wallet: this node signs with its own coldkey (~/.bittensor). */
import { useCallback, useEffect, useState } from 'react';
import { call, getJSON, Position } from '@/lib/api';
import { useData } from '@/lib/data';
import { useOverlay } from '@/lib/overlay';
import { kindGlyph, kindText, useWallet } from '@/lib/wallet';
import { fmt, fmtPrice, looksSS58, short, when } from '@/lib/format';
import { Pct, Section, Spinner, SubnetLogo, Tabs } from '@/components/ui';
import TxReview, { useTx } from '@/components/TxReview';
import Ticket, { useAccount } from '@/components/Ticket';
interface TxRow { id: string; ts: number; kind: string; call: string; status: string; preview?: any;
                  extrinsic_hash?: string; block?: number; error?: string }

/* ------------------------------------------------- browser-wallet desk */

/* pick a market by name, symbol or netuid — the subnets ARE the markets */
function MarketPick({ value, onPick }: { value: number | null; onPick: (n: number) => void }) {
  const { screener, bySubnet } = useData();
  const [q, setQ] = useState('');
  const [open, setOpen] = useState(false);
  const r = value != null ? bySubnet[value] : undefined;
  const t = q.trim().toLowerCase();
  const rows = (screener?.rows || []).filter(x => x.netuid !== 0 && (!t || String(x.netuid) === t
    || String(x.name || '').toLowerCase().includes(t) || String(x.symbol || '').toLowerCase().includes(t)))
    .sort((a, b) => (b.market_cap || 0) - (a.market_cap || 0)).slice(0, 40);
  const pick = (n: number) => { onPick(n); setQ(''); setOpen(false); };
  return (
    <div className="mpick">
      <input value={open ? q : r ? `${r.name || 'subnet'} · SN${r.netuid}` : q}
             onFocus={() => { setOpen(true); setQ(''); }} onBlur={() => setTimeout(() => setOpen(false), 150)}
             onChange={e => setQ(e.target.value)} placeholder="Search a market — name, symbol or netuid" spellCheck={false}
             onKeyDown={e => { if (e.key === 'Enter' && rows[0]) pick(rows[0].netuid); }} />
      {open && (
        <div className="mpick-list">{rows.length ? rows.map(x => (
          <button key={x.netuid} onMouseDown={e => e.preventDefault()} onClick={() => pick(x.netuid)}>
            <SubnetLogo logo={x.logo} symbol={x.symbol} />
            <span className="nm">{x.name || 'subnet ' + x.netuid} <span className="sn-sym">SN{x.netuid}</span></span>
            <span className="num">τ {fmtPrice(x.price)}</span>
            <Pct v={x.change_24h} />
          </button>
        )) : <span className="muted">No market matches.</span>}</div>
      )}
    </div>
  );
}

function WalletDesk() {
  const w = useWallet();
  const { names } = useData();
  const { openSubnet } = useOverlay();
  const addr = w.wallet!.addr;
  const acct = useAccount(addr);
  const tx = useTx();
  const [mode, setMode] = useState<'market' | 'transfer'>('market');
  const [netuid, setNetuid] = useState<number | null>(null);
  const [side, setSide] = useState<'stake' | 'unstake'>('stake');
  const [amt, setAmt] = useState('');
  const [dest, setDest] = useState('');
  const [hist, setHist] = useState<TxRow[]>([]);

  const loadHist = useCallback(() => {
    getJSON<{ txs: TxRow[] }>(`tx?address=${encodeURIComponent(addr)}&limit=8`)
      .then(j => setHist(j.txs || [])).catch(() => {});
  }, [addr]);
  useEffect(loadHist, [loadHist]);
  useEffect(() => { if (tx.phase === 'done') { acct.reload(); loadHist(); } },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [tx.phase]);

  const sell = (p: Position) => { setMode('market'); setNetuid(p.netuid); setSide('unstake'); };
  const send = () => { const a = +amt; if (a > 0) tx.review('transfer', { dest: dest.trim(), amount_tao: a }); };

  return (
    <div className="grid cols2">
      <div className="card">
        <h3 className="t">Your positions</h3>
        <p className="muted" style={{ marginTop: 0 }}>{kindGlyph(w.wallet!)} {kindText(w.wallet!)} · <span className="num" title={addr}>{short(addr)}</span></p>
        {acct.rows == null ? <p className="muted"><Spinner /> reading the chain…</p> : acct.rows.length ? (
          <div className="scroll-x"><table><thead><tr><th>Market</th><th>Validator</th><th className="num">Alpha</th><th className="num">Value τ</th><th /></tr></thead>
            <tbody>{acct.rows.map((p, i) => (
              <tr key={i}>
                <td className="click" onClick={() => openSubnet(p.netuid)}>{names[p.netuid] || ''} <span className="sn-sym">SN{p.netuid}</span></td>
                <td className="num" title={p.hotkey}>{short(p.hotkey)}</td>
                <td className="num">{fmt(p.alpha, 4)}</td>
                <td className="num">{p.value_tao != null ? fmt(p.value_tao, 4) : '—'}</td>
                <td><button className="copybtn" style={{ position: 'static' }} onClick={() => sell(p)}>Sell</button></td>
              </tr>))}</tbody></table></div>
        ) : <p className="muted">{acct.err || 'No alpha positions yet — free TAO only.'}</p>}
        {hist.length > 0 && <>
          <h3 style={{ fontSize: 13, margin: '18px 0 6px' }}>Signed from this console</h3>
          <table><tbody>{hist.map(h => (
            <tr key={h.id} title={h.error || h.extrinsic_hash || ''}>
              <td className="muted">{when(h.ts)}</td>
              <td>{h.preview?.action || h.kind}{h.preview?.netuid != null ? ` SN${h.preview.netuid}` : ''}</td>
              <td className="num">{h.preview?.amount_tao != null ? `τ ${fmt(h.preview.amount_tao, 3)}` : h.preview?.amount_alpha != null ? `${fmt(h.preview.amount_alpha, 3)} α` : ''}</td>
              <td><span className={'tag' + (h.status === 'ok' ? '' : ' warn')}>{h.status === 'ok' ? `#${h.block ?? ''}` : h.status}</span></td>
            </tr>))}</tbody></table>
        </>}
      </div>

      <div className="card">
        <h3 className="t">Trade with {tx.walletName}</h3>
        <Tabs value={mode} onChange={m => { setMode(m); tx.reset(); }}
              options={[['market', 'Markets'], ['transfer', 'Send τ']]} />
        {mode === 'transfer' ? <>
          <div className="row" style={{ marginTop: 12 }}>
            <div style={{ flex: 2 }}><label>To (ss58)</label>
              <input value={dest} onChange={e => setDest(e.target.value)} placeholder="5Grw…" spellCheck={false} /></div>
            <div><label>Amount τ</label><input type="number" step="0.01" min="0" value={amt} onChange={e => setAmt(e.target.value)} placeholder="0.5" /></div>
          </div>
          {['idle', 'done', 'error'].includes(tx.phase) && (
            <div style={{ marginTop: 14 }}>
              <button className="pill primary" disabled={!(+amt > 0) || !looksSS58(dest)} onClick={send}>Review</button>
            </div>
          )}
          <TxReview tx={tx} />
        </> : <>
          <div style={{ marginTop: 12 }}><MarketPick value={netuid} onPick={setNetuid} /></div>
          {netuid != null ? <>
            <button className="linkish" style={{ marginTop: 10 }} onClick={() => openSubnet(netuid)}>
              open the SN{netuid} market — chart, trades, validators →</button>
            <div style={{ marginTop: 12 }}><Ticket key={netuid + side} netuid={netuid} initial={side} /></div>
          </> : <p className="muted" style={{ marginTop: 12 }}>Every subnet is a market. Pick one to buy or sell its alpha.</p>}
        </>}
      </div>
    </div>
  );
}

/* ------------------------------------------------- node-signed (local) desk */

function LocalDesk() {
  const { localWallets } = useWallet();
  const { bySubnet, names } = useData();
  const { openSubnet } = useOverlay();
  const [wallet, setWallet] = useState('default');
  const [ps, setPs] = useState<any[] | null>(null);
  const [msg, setMsg] = useState<React.ReactNode>('');
  const [side, setSide] = useState<'bt_buy' | 'bt_sell'>('bt_buy');
  const [netuid, setNetuid] = useState('');
  const [amt, setAmt] = useState('');
  const [confirming, setConfirming] = useState(false);
  const r = netuid !== '' ? bySubnet[+netuid] : undefined;
  const a = +amt;

  const load = async () => {
    setPs(null); setMsg(<Spinner />);
    try { setPs(((await call('bt_portfolio', { wallet: wallet || 'default' })).result || []) as any[]); setMsg(''); }
    catch (e) { setPs([]); setMsg((e as Error).message); }
  };
  const place = async () => {
    setConfirming(false); setMsg(<Spinner />);
    try {
      const j = await call(side, { netuid: +netuid, amount_tao: a, wallet });
      setMsg(j.result?.ok ? `Done in ${j.ms} ms ✓` : 'Extrinsic not confirmed');
    } catch (e) { setMsg((e as Error).message); }
  };

  return (
    <div className="card">
      <h3 className="t">Node wallet</h3>
      <p className="muted" style={{ marginTop: 0 }}>Signed by a coldkey stored on this node (~/.bittensor/wallets).</p>
      <div className="row">
        <div><label>Local wallet</label>
          {localWallets && localWallets.length ? (
            <select value={wallet} onChange={e => setWallet(e.target.value)}>
              {localWallets.map(w => <option key={w.name} value={w.name}>{w.name}</option>)}
            </select>
          ) : <input value={wallet} onChange={e => setWallet(e.target.value)} />}
        </div>
        <div style={{ flex: 0 }}><button className="pill ghost" onClick={load}>Portfolio</button></div>
      </div>
      {ps && ps.length > 0 && (
        <table style={{ marginTop: 12 }}><thead><tr><th>Subnet</th><th className="num">Alpha</th><th className="num">Value τ</th></tr></thead>
          <tbody>{ps.map((p, i) => (
            <tr key={i} className="click" onClick={() => p.netuid != null && openSubnet(p.netuid)}>
              <td>{names[p.netuid] || ''} <span className="sn-sym">#{p.netuid ?? '—'}</span></td>
              <td className="num">{fmt(p.alpha ?? p.stake, 4)}</td>
              <td className="num">{fmt(p.tao_value ?? p.value, 4)}</td></tr>))}</tbody></table>
      )}
      <div className="row" style={{ marginTop: 14 }}>
        <div><label>Action</label>
          <select value={side} onChange={e => setSide(e.target.value as any)}>
            <option value="bt_buy">Buy (stake)</option><option value="bt_sell">Sell (unstake)</option>
          </select></div>
        <div><label>Subnet</label><input type="number" value={netuid} onChange={e => setNetuid(e.target.value)} placeholder="1" /></div>
        <div><label>Amount τ</label><input type="number" step="0.01" value={amt} onChange={e => setAmt(e.target.value)} placeholder="0.5" /></div>
      </div>
      {confirming ? (
        <div className="txbox">
          <div className="txh">{side === 'bt_buy' ? 'Buy into' : 'Sell out of'} subnet {netuid}{r?.name ? ` (${r.name})` : ''} for τ {a}?</div>
          <p className="muted" style={{ margin: '6px 0' }}>Signed by the node wallet “{wallet}”. Real TAO moves.</p>
          <div className="txa"><button className="pill primary" onClick={place}>Sign &amp; send</button>
            <button className="iconbtn" onClick={() => setConfirming(false)}>Cancel</button></div>
        </div>
      ) : (
        <div style={{ marginTop: 14, display: 'flex', gap: 10, alignItems: 'center' }}>
          <button className="pill ghost" disabled={!netuid || !(a > 0)} onClick={() => setConfirming(true)}>Review</button>
          <span className="muted">{msg}</span>
        </div>
      )}
    </div>
  );
}

function ConnectNudge() {
  const w = useWallet();
  const installed = w.extList().filter(e => e.installed);
  return (
    <div className="card">
      <h3 className="t">Trade from your own wallet</h3>
      <p className="muted" style={{ marginTop: 0 }}>
        {w.wallet ? <>Watching <span className="num">{short(w.wallet.addr)}</span> read-only. </> : null}
        Connect SubWallet (or Talisman / polkadot{'{.js}'}) to buy, sell and send from your own account — the wallet signs, this node only builds and broadcasts.
      </p>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        <button className="pill primary" onClick={() => w.setPopOpen(true)}>Connect {installed[0]?.name || 'SubWallet'}</button>
        {!installed.length && <a className="pill ghost" href="https://www.subwallet.app/download.html" target="_blank" rel="noopener noreferrer">Get SubWallet ↗</a>}
      </div>
    </div>
  );
}

export default function Trade() {
  const w = useWallet();
  return (
    <Section id="trade" title="Trade." lead="Every subnet is a market. Buy and sell its alpha, send TAO — signed in SubWallet, or by this node's own wallet.">
      {w.canSign ? <WalletDesk /> : <ConnectNudge />}
      {w.note && <p className="muted">{w.note}</p>}
      <div className="grid cols2" style={{ marginTop: 18 }}><LocalDesk /></div>
    </Section>
  );
}
