'use client';
import { Suspense, useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { call, Position } from '@/lib/api';
import { useData } from '@/lib/data';
import { useOverlay } from '@/lib/overlay';
import { Ident, Section, Spinner } from '@/components/ui';
import Positions, { BalanceStats } from '@/components/Positions';

interface Account { free_tao?: number; staked_value_tao?: number; total_value_tao?: number;
                    positions: Position[]; stake_error?: string }

function AccountInner() {
  const params = useSearchParams();
  const qAddr = params.get('addr');
  const { reloadTraders } = useData();
  const { openTrader } = useOverlay();
  const [addr, setAddr] = useState('');
  const [shown, setShown] = useState('');
  const [acct, setAcct] = useState<Account | null>(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [tracked, setTracked] = useState('');

  const load = useCallback(async (a: string) => {
    a = a.trim();
    if (!a) return;
    setShown(a); setAcct(null); setErr(''); setBusy(true); setTracked('');
    /* the address is the page — make it a link */
    window.history.replaceState(window.history.state, '', `${window.location.pathname}?addr=${encodeURIComponent(a)}`);
    try { setAcct((await call<Account>('bt_account', { address: a })).result); }
    catch (e) { setErr((e as Error).message); }
    setBusy(false);
  }, []);

  /* deep links: /account?addr=5… (also what the chat agent opens) */
  useEffect(() => {
    if (qAddr && qAddr !== shown) { setAddr(qAddr); load(qAddr); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qAddr, load]);

  const track = async () => {
    try {
      const r = (await call('bt_track', { address: shown })).result;
      setTracked(r.snapshot ? 'Tracked — opening its profile.' : 'Tracked — first snapshot pending.');
      reloadTraders(); openTrader(shown);
    } catch (e) { setTracked((e as Error).message); }
  };

  return (
    <Section id="account" title="Account."
      lead="Explore any address on the chain — free TAO plus every alpha position, valued live. Not just yours: anyone's.">
      <div className="card">
        <div className="row">
          <div style={{ flex: 3 }}><label>ss58 coldkey address</label>
            <input value={addr} onChange={e => setAddr(e.target.value)} placeholder="5Grw…" spellCheck={false}
                   onKeyDown={e => e.key === 'Enter' && load(addr)} /></div>
          <div style={{ flex: 0 }}><button className="pill primary" onClick={() => load(addr)} disabled={busy}>
            {busy ? <Spinner /> : 'Explore'}</button></div>
        </div>
        {shown && (
          <div className="whead" style={{ marginTop: 18 }}>
            <Ident addr={shown} size={36} />
            <b className="num" style={{ fontSize: 13, wordBreak: 'break-all' }}>{shown}</b>
          </div>
        )}
        {busy && <p className="muted" style={{ marginTop: 14 }}><Spinner /> reading the chain — can take a moment while the indexer is busy…</p>}
        {err && <p className="muted" style={{ marginTop: 14 }}>{err}</p>}
        {acct && <>
          <BalanceStats free={acct.free_tao} staked={acct.staked_value_tao} total={acct.total_value_tao} n={acct.positions.length} />
          <Positions rows={acct.positions} hotkeys />
          <div style={{ marginTop: 16, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <button className="pill ghost" onClick={track}>Track this trader</button>
            <span className="muted">{tracked || 'Keeps its history so you get an equity curve, PnL and a trade tape.'}</span>
          </div>
          {acct.stake_error && <p className="muted" style={{ marginTop: 10 }}>stake lookup: {acct.stake_error}</p>}
        </>}
        {!shown && <p className="muted" style={{ marginTop: 14 }}>Tip: every account page is a link — <code className="inline">/bt/account?addr=5…</code> — so you can share exactly what you are looking at.</p>}
      </div>
    </Section>
  );
}

export default function AccountPage() {
  return <Suspense fallback={null}><AccountInner /></Suspense>;
}
