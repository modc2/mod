'use client';
/* The connected wallet's home. Paints from the local trader index (ms) when
 * the address is tracked; only waits on the chain when it is not — with a
 * one-click Track that makes this page instant forever after. */
import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { call, Position } from '@/lib/api';
import { useData } from '@/lib/data';
import { kindGlyph, kindText, useWallet } from '@/lib/wallet';
import { useCopy } from '@/lib/hooks';
import { fmt, RANGES, short } from '@/lib/format';
import { Ident, Pct, Section, Spinner } from '@/components/ui';
import LineChart from '@/components/LineChart';
import Positions, { BalanceStats } from '@/components/Positions';

interface Tracked { tracked?: boolean; warming?: boolean; total_tao?: number; free_tao?: number;
  staked_tao?: number; positions: Position[]; snapshots?: number; snapshot_ts?: number;
  series?: { t: number; total_tao: number; staked_tao?: number }[] }
interface Account { free_tao?: number; staked_value_tao?: number; total_value_tao?: number; positions: Position[] }

function Hero() {
  const w = useWallet();
  const { reloadTraders } = useData();
  const [copied, copy] = useCopy();
  const [t, setT] = useState<Tracked | null>(null);
  const [acct, setAcct] = useState<Account | null>(null);
  const [phase, setPhase] = useState<'idle' | 'index' | 'chain' | 'done'>('idle');
  const [err, setErr] = useState('');
  const forRef = useRef<string | null>(null);
  const addr = w.wallet?.addr || null;

  const load = useCallback(async (a: string) => {
    forRef.current = a;
    setErr(''); setPhase('index');
    let tr: Tracked | null = null;
    try { tr = (await call<Tracked>('bt_trader', { address: a, hours: RANGES[2].hours })).result; } catch { /* */ }
    if (forRef.current !== a) return;   /* switched wallets mid-flight */
    if (tr && tr.tracked && !tr.warming) {
      setT(tr); setAcct(null); setPhase('done');
      w.setBalance(tr.total_tao ?? null, tr.free_tao, tr.staked_tao);
      return;
    }
    setT(null); setPhase('chain');
    try {
      const ac = (await call<Account>('bt_account', { address: a })).result;
      if (forRef.current !== a) return;
      setAcct(ac); setPhase('done');
      w.setBalance(ac.total_value_tao ?? ac.free_tao ?? null, ac.free_tao, ac.staked_value_tao);
    } catch (e) { if (forRef.current === a) { setErr((e as Error).message); setPhase('done'); } }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { if (addr) load(addr); else { forRef.current = null; setPhase('idle'); } }, [addr, load]);

  const track = async () => {
    if (!w.wallet) return;
    try {
      await call('bt_track', w.wallet.name ? { address: w.wallet.addr, label: w.wallet.name } : { address: w.wallet.addr });
      reloadTraders(); load(w.wallet.addr);
    } catch (e) { setErr((e as Error).message); }
  };

  if (!w.wallet) return (
    <div className="card whero">
      <div style={{ display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ fontFamily: 'var(--pixel)', fontSize: 13 }}>No wallet yet.</span>
        <button className="pill primary" onClick={() => w.setPopOpen(true)}>Connect a wallet</button>
      </div>
      <p className="muted" style={{ marginTop: 12 }}>Connect SubWallet or Talisman, pick a local coldkey, or just watch any ss58 address — the whole console then follows it: balance in the bar, this page, and the trader index.</p>
    </div>
  );

  const wal = w.wallet;
  const series = (t?.series || []).filter(p => p.total_tao != null).map(p => ({ t: p.t, v: p.total_tao, sub: `${fmt(p.staked_tao, 2)} staked` }));
  const d0 = series[0]?.v, d1 = series[series.length - 1]?.v;
  const pct = series.length > 1 && d0 ? ((d1 - d0) / d0) * 100 : null;
  const age = t?.snapshot_ts ? Math.max(0, Math.round((Date.now() / 1000 - t.snapshot_ts) / 60)) : null;

  return (
    <div className="card whero">
      <div className="whead">
        <Ident addr={wal.addr} size={42} />
        <div style={{ minWidth: 0 }}>
          <b className="num" style={{ fontSize: 13, wordBreak: 'break-all' }}>{wal.addr}</b>
          <div className="muted" style={{ marginTop: 4 }}>{kindGlyph(wal)} {kindText(wal)}
            <button className="iconbtn" style={{ fontSize: 12, padding: '2px 6px' }} title="Copy address"
                    onClick={() => copy(wal.addr)}>{copied ? '✓' : '⧉'}</button>
          </div>
        </div>
      </div>
      {phase === 'index' && <p className="muted" style={{ marginTop: 14 }}><Spinner /> checking the local index…</p>}
      {phase === 'chain' && <p className="muted" style={{ marginTop: 14 }}><Spinner /> reading the chain — can take a minute while the indexer is busy…</p>}
      {err && <p className="muted" style={{ marginTop: 14 }}>{err}</p>}
      {t && <>
        <BalanceStats free={t.free_tao} staked={t.staked_tao} total={t.total_tao} n={t.positions.length} />
        <h3 style={{ fontSize: 14, margin: '18px 0 6px' }}>Equity — tracked by the local index</h3>
        <LineChart series={series} hours={RANGES[2].hours} height={180}
                   color={pct == null || pct >= 0 ? 'var(--good)' : 'var(--bad)'} fmtY={v => `τ ${fmt(v, 3)}`}
                   empty="Tracked — first snapshots are landing, the equity curve appears shortly." />
        {series.length > 1 && <p className="muted" style={{ marginTop: 6 }}><Pct v={pct} /> over 30d window · {t.snapshots || series.length} snapshots</p>}
        <Positions rows={t.positions} />
        {age != null && <p className="muted" style={{ marginTop: 10 }}>from the local index, snapshot {age}m ago — <button className="iconbtn" style={{ fontSize: 12, textDecoration: 'underline', padding: 0 }} onClick={track}>snapshot now</button></p>}
      </>}
      {acct && <>
        <BalanceStats free={acct.free_tao} staked={acct.staked_value_tao} total={acct.total_value_tao} n={acct.positions.length} />
        <div style={{ marginTop: 16, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <button className="pill ghost" onClick={track}>Track this wallet</button>
          <span className="muted">The local indexer will snapshot it every 15 min: equity curve, PnL, inferred trade tape — and this page turns instant.</span>
        </div>
        <Positions rows={acct.positions} />
      </>}
    </div>
  );
}

function LocalWallets() {
  const w = useWallet();
  const ws = w.localWallets;
  return (
    <div className="card">
      <h3 className="t">Local wallets</h3>
      {ws == null ? <Spinner /> : ws.length ? (
        <table><thead><tr><th>Name</th><th>Coldkey</th><th>Hotkeys</th><th /></tr></thead>
          <tbody>{ws.map(x => (
            <tr key={x.name}><td>{x.name}</td><td className="num" title={x.coldkey}>{x.coldkey ? short(x.coldkey) : '—'}</td>
              <td>{(x.hotkeys || []).join(', ') || '—'}</td>
              <td>{x.coldkey && <button className="copybtn" style={{ position: 'static' }}
                onClick={() => w.connect(x.coldkey!, x.name, true)}>Use</button>}</td></tr>
          ))}</tbody></table>
      ) : <span className="muted">No local wallets yet — create one from the Console (<code className="inline">bt_create_wallet</code>).</span>}
    </div>
  );
}

function Balance() {
  const [addr, setAddr] = useState('');
  const [out, setOut] = useState<React.ReactNode>(null);
  const check = async () => {
    if (!addr.trim()) return;
    setOut(<Spinner />);
    try { setOut(`τ ${fmt((await call('bt_balance', { address: addr.trim() })).result.tao, 6)}`); }
    catch (e) { setOut((e as Error).message); }
  };
  return (
    <div className="card">
      <h3 className="t">Check any balance</h3>
      <label>ss58 address</label>
      <input value={addr} onChange={e => setAddr(e.target.value)} placeholder="5Grw…" spellCheck={false}
             onKeyDown={e => e.key === 'Enter' && check()} />
      <div style={{ marginTop: 12, display: 'flex', gap: 10, alignItems: 'center' }}>
        <button className="pill primary" onClick={check}>Balance</button>
        <span className="num" style={{ fontSize: 17 }}>{out}</span>
      </div>
      <p className="muted" style={{ marginTop: 16 }}>Transfers, wallet creation, and every other write happen in the <Link href="/console">Console</Link> — each on-chain action asks before it signs.</p>
    </div>
  );
}

export default function WalletPage() {
  return (
    <Section id="wallet" title="Wallet."
      lead="Your account, live from the chain — connect a browser wallet, pick a local one, or just watch an address.">
      <Hero />
      <div className="grid cols2">
        <LocalWallets />
        <Balance />
      </div>
    </Section>
  );
}
