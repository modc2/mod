'use client';
/* The trade desk. The node signs with its own local wallet — so every write
 * is previewed against live pool prices and confirmed in the browser first. */
import { useState } from 'react';
import { call } from '@/lib/api';
import { useData } from '@/lib/data';
import { useOverlay } from '@/lib/overlay';
import { useWallet } from '@/lib/wallet';
import { fmt, fmtPrice } from '@/lib/format';
import { Section, Spinner, SubnetLogo } from '@/components/ui';

function Portfolio() {
  const { localWallets } = useWallet();
  const { names } = useData();
  const { openSubnet } = useOverlay();
  const [wallet, setWallet] = useState('default');
  const [out, setOut] = useState<React.ReactNode>(<span className="muted">—</span>);
  const load = async () => {
    setOut(<Spinner />);
    try {
      const ps = ((await call('bt_portfolio', { wallet: wallet || 'default' })).result || []) as any[];
      setOut(ps.length ? (
        <table><thead><tr><th>Subnet</th><th className="num">Alpha</th><th className="num">Value τ</th></tr></thead>
          <tbody>{ps.map((p, i) => (
            <tr key={i} className="click" onClick={() => p.netuid != null && openSubnet(p.netuid)}>
              <td>{names[p.netuid] || ''} <span className="sn-sym">#{p.netuid ?? '—'}</span></td>
              <td className="num">{fmt(p.alpha ?? p.stake, 4)}</td>
              <td className="num">{fmt(p.tao_value ?? p.value, 4)}</td></tr>
          ))}</tbody></table>
      ) : <span className="muted">No open positions.</span>);
    } catch (e) { setOut(<span className="muted">{(e as Error).message}</span>); }
  };
  return (
    <div className="card">
      <h3 className="t">Portfolio</h3>
      <div className="row">
        <div><label>Local wallet</label>
          {localWallets && localWallets.length ? (
            <select value={wallet} onChange={e => setWallet(e.target.value)}>
              {localWallets.map(w => <option key={w.name} value={w.name}>{w.name}</option>)}
            </select>
          ) : <input value={wallet} onChange={e => setWallet(e.target.value)} />}
        </div>
        <div style={{ flex: 0 }}><button className="pill primary" onClick={load}>Load</button></div>
      </div>
      <div style={{ marginTop: 14 }}>{out}</div>
    </div>
  );
}

function Ticket() {
  const { bySubnet } = useData();
  const { openSubnet } = useOverlay();
  const [side, setSide] = useState<'bt_buy' | 'bt_sell'>('bt_buy');
  const [netuid, setNetuid] = useState('');
  const [amt, setAmt] = useState('');
  const [out, setOut] = useState<React.ReactNode>('');
  const r = netuid !== '' ? bySubnet[+netuid] : undefined;
  const a = +amt;
  /* spot estimate only — the pool's slippage is applied on chain */
  const est = r?.price && a > 0 ? (side === 'bt_buy' ? a / r.price : a) : null;

  const place = async () => {
    const n = +netuid;
    if (!netuid || !a) { setOut('Enter netuid and amount.'); return; }
    const verb = side === 'bt_buy' ? 'BUY into' : 'SELL out of';
    if (!confirm(`${verb} subnet ${n}${r?.name ? ' (' + r.name + ')' : ''} for ${a} TAO?\n\nThis signs a real on-chain transaction.`)) return;
    setOut(<Spinner />);
    try {
      const j = await call(side, { netuid: n, amount_tao: a });
      setOut(j.result?.ok ? `Done in ${j.ms} ms ✓` : 'Extrinsic not confirmed');
    } catch (e) { setOut((e as Error).message); }
  };

  return (
    <div className="card">
      <h3 className="t">Place a trade</h3>
      <div className="row">
        <div><label>Action</label>
          <select value={side} onChange={e => setSide(e.target.value as any)}>
            <option value="bt_buy">Buy (stake)</option><option value="bt_sell">Sell (unstake)</option>
          </select></div>
        <div><label>Netuid</label><input type="number" value={netuid} onChange={e => setNetuid(e.target.value)} placeholder="1" /></div>
        <div><label>Amount τ</label><input type="number" step="0.01" value={amt} onChange={e => setAmt(e.target.value)} placeholder="0.5" /></div>
      </div>
      {r && (
        <div className="mrow" style={{ marginTop: 12 }} onClick={() => openSubnet(r.netuid)}>
          <SubnetLogo logo={r.logo} symbol={r.symbol} />
          <span className="nm">{r.name} <span className="sn-sym">#{r.netuid} · τ {fmtPrice(r.price)}</span></span>
          {est != null && <span className="v">{side === 'bt_buy' ? `≈ ${fmt(est, 3)} α` : `τ ${fmt(est, 3)} of α`}</span>}
        </div>
      )}
      <div style={{ marginTop: 14, display: 'flex', gap: 10, alignItems: 'center' }}>
        <button className="pill primary" onClick={place}>Review &amp; sign</button>
        <span className="muted">{out}</span>
      </div>
      <p className="muted" style={{ marginTop: 12 }}><span className="tag warn">on-chain</span> Real TAO moves, signed by this node&apos;s local wallet. You&apos;ll be asked to confirm.</p>
    </div>
  );
}

export default function Trade() {
  return (
    <Section id="trade" title="Trade." lead="Stake TAO into subnet alpha. Portfolio, buys, sells, swaps.">
      <div className="grid cols2"><Portfolio /><Ticket /></div>
    </Section>
  );
}
