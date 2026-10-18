'use client';
/* Copy trading — the copytensor submod (bt/copytensor), driven from bt.
 *
 * Every call goes to {BASE}/ct/*, bt.server's proxy onto the submod's API
 * (:50150). Unlike /trade, nothing is signed in the browser: copytensor holds
 * its own server-side wallet and mirrors each copied trader's subnet book into
 * it, so every write here is operator-only. From this machine on localhost
 * that is automatic; anywhere else paste the operator key (the server's
 * ~/.mod/bt/write_token) once and it rides along as a bearer token. */
import { Suspense, useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { BASE } from '@/lib/api';
import { usePoll, useStored } from '@/lib/hooks';
import { fmt, looksSS58, short } from '@/lib/format';
import { Ident, Section, Spinner, Stat } from '@/components/ui';

interface CtStatus { running: boolean; reads: string; wallet_set: boolean; active_copies: number;
  allocated_tao: number; tracked_accounts: number; block_height?: number }
interface Copy { id: string; target_ss58: string; label?: string | null; status: string;
  alloc_tao: number; last_sync_block?: number | null;
  target_info?: { total_stake_tao: number; num_subnets: number; pnl_pct: number } | null }
interface PlanRow { netuid: number; subnet_name: string; action: string; desired_tao: number;
  current_tao: number; amount_tao: number; reason: string }
interface Plan { our_ss58?: string | null; staked_tao: number; free_tao: number; requested_tao: number;
  deployable_tao: number; scale: number; rows: PlanRow[]; trades: number; blocked?: string | null;
  notes: string[]; executed: boolean; results: { action: string; netuid: number; amount_tao: number; status: string; error?: string }[] }
interface Trade { id: string; timestamp: string; action: string; netuid: number; amount_tao: number;
  status: string; error?: string | null; tx_hash?: string | null }

const TOKEN_KEY = 'bt.ct.token';
const CT = (p: string) => `${BASE}/ct/${p.replace(/^\//, '')}`;

/* one fetch for the whole page — attaches the operator key when there is one */
async function ct<T = any>(path: string, method = 'GET', body?: unknown): Promise<T> {
  let tok = '';
  try { tok = JSON.parse(localStorage.getItem(TOKEN_KEY) || '""'); } catch { /* */ }
  const r = await fetch(CT(path), {
    method, cache: 'no-store',
    headers: { 'content-type': 'application/json', ...(tok ? { authorization: `Bearer ${tok}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let j: any = null;
  try { j = await r.json(); } catch { /* */ }
  if (r.status === 403) throw new Error('403 operator only');
  if (!r.ok) throw new Error(j?.detail || j?.error || `HTTP ${r.status}`);
  if (j && j.ok === false) throw new Error(j.error || 'copytensor unreachable');
  return j as T;
}

const denied = (m: string) => m.startsWith('403');

function OperatorKey({ need }: { need: boolean }) {
  const [tok, setTok] = useStored<string>(TOKEN_KEY, '');
  const [draft, setDraft] = useState('');
  if (tok && !need) return (
    <p className="muted" style={{ marginTop: 10 }}>Operator key saved in this browser.{' '}
      <button className="copybtn" style={{ position: 'static' }} onClick={() => setTok('')}>Forget</button></p>
  );
  return (
    <div className="card" style={{ marginTop: 14 }}>
      <h3>Operator key</h3>
      <p className="muted">{need ? 'That change was refused — copying moves this server\'s TAO, so only its operator can do it. '
        : 'Starting, stopping or syncing a copy needs the operator key. '}
        On the server, run <code className="inline">cat ~/.mod/bt/write_token</code> and paste it here. It stays in this browser.</p>
      <div className="row">
        <div style={{ flex: 3 }}><input type="password" value={draft} onChange={e => setDraft(e.target.value)}
          placeholder="operator key" spellCheck={false} autoComplete="off"
          onKeyDown={e => e.key === 'Enter' && draft.trim() && (setTok(draft.trim()), setDraft(''))} /></div>
        <div style={{ flex: 0 }}><button className="pill primary" disabled={!draft.trim()}
          onClick={() => { setTok(draft.trim()); setDraft(''); }}>Save</button></div>
      </div>
    </div>
  );
}

function WalletStep({ onDone, onDenied }: { onDone: () => void; onDenied: () => void }) {
  const [mn, setMn] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const load = async () => {
    setBusy(true); setMsg('');
    try {
      const r = await ct<{ ss58: string }>('wallet/set', 'POST', mn.trim() ? { mnemonic: mn.trim() } : {});
      setMsg(`Wallet ${short(r.ss58)} loaded.`); setMn(''); onDone();
    } catch (e) {
      const m = (e as Error).message;
      if (denied(m)) onDenied();
      setMsg(m);
    }
    setBusy(false);
  };
  return (
    <div className="card" style={{ marginTop: 14 }}>
      <h3>1 · Pick the wallet that copies</h3>
      <p className="muted">Copies trade from a wallet on this server. Leave the box empty to use the
        server&apos;s existing <code className="inline">copytensor</code> wallet, or paste its 12-word phrase to restore it.</p>
      <div className="row">
        <div style={{ flex: 3 }}><input type="password" value={mn} onChange={e => setMn(e.target.value)}
          placeholder="recovery phrase (optional)" spellCheck={false} autoComplete="off" /></div>
        <div style={{ flex: 0 }}><button className="pill primary" onClick={load} disabled={busy}>
          {busy ? <Spinner /> : 'Load wallet'}</button></div>
      </div>
      {msg && <p className="muted" style={{ marginTop: 8 }}>{msg}</p>}
    </div>
  );
}

function StartCopy({ target, wallet, onDone, onDenied }:
  { target: string; wallet: string | null; onDone: () => void; onDenied: () => void }) {
  const [addr, setAddr] = useState(target);
  const [amt, setAmt] = useState('');
  const [label, setLabel] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  useEffect(() => { if (target) setAddr(target); }, [target]);
  const ok = looksSS58(addr) && +amt > 0 && !!wallet;
  const start = async () => {
    if (!ok) return;
    setBusy(true); setMsg('');
    try {
      await ct('copy', 'POST', { target_ss58: addr.trim(), our_hotkey: wallet, alloc_tao: +amt,
                                 ...(label.trim() ? { label: label.trim() } : {}) });
      setMsg(`Copying ${label.trim() || short(addr)} with τ ${fmt(+amt, 3)} — the next pass mirrors their book.`);
      setAddr(''); setAmt(''); setLabel(''); onDone();
    } catch (e) {
      const m = (e as Error).message;
      if (denied(m)) onDenied();
      setMsg(m);
    }
    setBusy(false);
  };
  return (
    <div className="card" style={{ marginTop: 14 }}>
      <h3>{wallet ? 'Copy a trader' : '2 · Copy a trader'}</h3>
      <div className="row">
        <div style={{ flex: 3 }}><label>Trader coldkey</label>
          <input value={addr} onChange={e => setAddr(e.target.value)} placeholder="5Grw…" spellCheck={false} /></div>
        <div style={{ flex: 1 }}><label>Amount τ</label>
          <input value={amt} onChange={e => setAmt(e.target.value.replace(/[^0-9.]/g, ''))} placeholder="5" inputMode="decimal" /></div>
        <div style={{ flex: 1 }}><label>Label (optional)</label>
          <input value={label} onChange={e => setLabel(e.target.value)} placeholder="whale #1" spellCheck={false} /></div>
        <div style={{ flex: 0 }}><button className="pill primary" onClick={start} disabled={busy || !ok}>
          {busy ? <Spinner /> : 'Start copy'}</button></div>
      </div>
      <p className="muted" style={{ marginTop: 8 }}>
        {!wallet ? 'Load a wallet first.'
          : addr && !looksSS58(addr) ? 'That is not an ss58 address.'
          : 'Your amount follows their subnet mix — when they move, your stake moves the same way. Pick traders on the Traders board.'}
      </p>
      {msg && <p className="muted" style={{ marginTop: 6 }}>{msg}</p>}
    </div>
  );
}

function Copies({ copies, onChange, onDenied }: { copies: Copy[]; onChange: () => void; onDenied: () => void }) {
  const [sure, setSure] = useState<string | null>(null);
  const [err, setErr] = useState('');
  const act = async (c: Copy, what: 'pause' | 'resume' | 'stop') => {
    if (what === 'stop' && sure !== c.id) {
      setSure(c.id); setTimeout(() => setSure(s => (s === c.id ? null : s)), 3000); return;
    }
    setSure(null); setErr('');
    try {
      if (what === 'stop') await ct(`copy/${c.id}`, 'DELETE');
      else await ct(`copy/${c.id}/${what}`, 'POST');
      onChange();
    } catch (e) {
      const m = (e as Error).message;
      if (denied(m)) onDenied();
      setErr(m);
    }
  };
  if (!copies.length) return <p className="muted" style={{ marginTop: 10 }}>You are not copying anyone yet.</p>;
  return (
    <>
      <div className="scroll-x" style={{ marginTop: 10 }}>
        <table><thead><tr><th>Trader</th><th className="num">Amount τ</th><th className="num">Their book τ</th>
          <th className="num">Subnets</th><th>Status</th><th /></tr></thead>
          <tbody>{copies.map(c => (
            <tr key={c.id}>
              <td><div className="sn-cell"><Ident addr={c.target_ss58} size={26} />
                <div><div className="sn-name">{c.label || short(c.target_ss58)}</div>
                  <div className="sn-sym" title={c.target_ss58}>{c.label ? short(c.target_ss58) : `synced @ ${c.last_sync_block ?? '—'}`}</div></div></div></td>
              <td className="num">{fmt(c.alloc_tao, 3)}</td>
              <td className="num">{c.target_info ? fmt(c.target_info.total_stake_tao, 2) : '—'}</td>
              <td className="num">{c.target_info?.num_subnets ?? '—'}</td>
              <td><span className={'tag' + (c.status === 'active' ? '' : ' warn')}>{c.status}</span></td>
              <td className="num" style={{ whiteSpace: 'nowrap' }}>
                {c.status === 'active'
                  ? <button className="copybtn" style={{ position: 'static' }} onClick={() => act(c, 'pause')}>Pause</button>
                  : <button className="copybtn" style={{ position: 'static' }} onClick={() => act(c, 'resume')}>Resume</button>}{' '}
                <button className={'copybtn' + (sure === c.id ? ' sure' : '')} style={{ position: 'static' }}
                  title="Stop copying — your stake stays where it is" onClick={() => act(c, 'stop')}>
                  {sure === c.id ? 'Sure?' : 'Stop'}</button></td>
            </tr>
          ))}</tbody></table>
      </div>
      {err && <p className="muted" style={{ marginTop: 8 }}>{err}</p>}
    </>
  );
}

function PlanCard({ plan, onSynced, onDenied }: { plan: Plan | null; onSynced: () => void; onDenied: () => void }) {
  const [arm, setArm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<Plan | null>(null);
  const [err, setErr] = useState('');
  if (!plan) return <p className="muted" style={{ marginTop: 10 }}><Spinner /> working out the plan…</p>;
  const moves = plan.rows.filter(r => r.action !== 'hold');
  const sync = async () => {
    if (!arm) { setArm(true); setTimeout(() => setArm(false), 3000); return; }
    setArm(false); setBusy(true); setErr(''); setRes(null);
    try { setRes(await ct<Plan>('portfolio/sync', 'POST')); onSynced(); }
    catch (e) { const m = (e as Error).message; if (denied(m)) onDenied(); setErr(m); }
    setBusy(false);
  };
  return (
    <>
      <div className="stats" style={{ marginTop: 10 }}>
        <Stat label="asked for" value={`τ ${fmt(plan.requested_tao, 2)}`} />
        <Stat label="can back" value={`τ ${fmt(plan.deployable_tao, 2)}`} />
        <Stat label="staked now" value={`τ ${fmt(plan.staked_tao, 2)}`} />
        <Stat label="trades pending" value={plan.trades} />
      </div>
      {plan.notes.map(n => <p key={n} className="muted" style={{ marginTop: 6 }}>{n}</p>)}
      {moves.length > 0 && (
        <div className="scroll-x" style={{ marginTop: 10 }}>
          <table><thead><tr><th>Subnet</th><th>Move</th><th className="num">Now τ</th><th className="num">Target τ</th><th className="num">Trade τ</th></tr></thead>
            <tbody>{moves.map(r => (
              <tr key={r.netuid}><td>{r.subnet_name || 'SN'} <span className="sn-sym">#{r.netuid}</span></td>
                <td><span className={'tag' + (r.action === 'unstake' || r.action === 'sell' ? ' warn' : '')}>{r.action}</span></td>
                <td className="num">{fmt(r.current_tao, 3)}</td><td className="num">{fmt(r.desired_tao, 3)}</td>
                <td className="num">{fmt(r.amount_tao, 3)}</td></tr>
            ))}</tbody></table>
        </div>
      )}
      <div className="row" style={{ marginTop: 12 }}>
        <button className={'pill' + (arm ? ' primary' : '')} onClick={sync}
          disabled={busy || !!plan.blocked || !plan.trades || !plan.our_ss58}>
          {busy ? <Spinner /> : arm ? `Send ${plan.trades} trade${plan.trades === 1 ? '' : 's'} — sure?` : 'Sync now'}</button>
        <span className="muted" style={{ alignSelf: 'center' }}>The engine also syncs on its own every few minutes.</span>
      </div>
      {res && <p className="muted" style={{ marginTop: 8 }}>
        {res.results.length ? res.results.map(t => `${t.action} SN${t.netuid} τ${fmt(t.amount_tao, 3)} ${t.status}${t.error ? ` (${t.error})` : ''}`).join(' · ')
          : 'Nothing needed to move.'}</p>}
      {err && <p className="muted" style={{ marginTop: 8 }}>{err}</p>}
    </>
  );
}

function CopyInner() {
  const params = useSearchParams();
  const target = params.get('target') || '';
  const [needKey, setNeedKey] = useState(false);
  const st = usePoll(() => ct<CtStatus>('status'), 20000);
  const wal = usePoll(() => ct<{ ss58: string; balance_tao: number }>('wallet/balance').catch(() => null), 30000);
  const copies = usePoll(() => ct<Copy[]>('copies'), 20000);
  const plan = usePoll(() => ct<Plan>('portfolio'), 30000);
  const trades = usePoll(() => ct<Trade[]>('trades?limit=15'), 30000);
  const reload = () => { st.refresh(); wal.refresh(); copies.refresh(); plan.refresh(); trades.refresh(); };
  const s = st.data;
  const wallet = wal.data?.ss58 || null;
  const onDenied = () => setNeedKey(true);

  return (
    <Section id="copy" title="Copy trading"
      lead={<>Pick a trader and an amount — your stake follows their subnet mix. Powered by <b>copytensor</b>, which runs inside bt.{' '}
        <a href="/copytensor/" target="_blank" rel="noreferrer">Full copy studio ↗</a></>}>
      {st.error && !s ? <div className="card"><p className="muted">Copy engine is not answering ({st.error}). It runs as
        pm2 <code className="inline">copytensor-api</code> out of <code className="inline">bt/copytensor</code>.</p></div>
        : !s ? <p className="muted"><Spinner /> connecting to the copy engine…</p>
        : <div className="stats">
            <Stat label="wallet" value={wallet ? short(wallet) : 'not set'} />
            <Stat label="balance" value={wal.data ? `τ ${fmt(wal.data.balance_tao, 3)}` : '—'} />
            <Stat label="copies" value={s.active_copies} />
            <Stat label="allocated" value={`τ ${fmt(s.allocated_tao, 2)}`} />
            <Stat label="data" value={s.reads === 'bt' ? 'bt index' : 'public RPC'} />
          </div>}

      <OperatorKey need={needKey} />
      {s && !wallet && <WalletStep onDone={reload} onDenied={onDenied} />}
      {s && <StartCopy target={target} wallet={wallet} onDone={reload} onDenied={onDenied} />}

      <div className="card" style={{ marginTop: 14 }}>
        <h3>Who you copy</h3>
        {copies.data ? <Copies copies={copies.data} onChange={reload} onDenied={onDenied} />
          : <p className="muted">{copies.error || <Spinner />}</p>}
      </div>

      <div className="card" style={{ marginTop: 14 }}>
        <h3>Next pass</h3>
        <PlanCard plan={plan.data} onSynced={reload} onDenied={onDenied} />
      </div>

      {!!trades.data?.length && (
        <div className="card" style={{ marginTop: 14 }}>
          <h3>Recent copy trades</h3>
          <div className="scroll-x"><table><thead><tr><th>When</th><th>Move</th><th>Subnet</th><th className="num">τ</th><th>Status</th></tr></thead>
            <tbody>{trades.data.map(t => (
              <tr key={t.id}><td className="muted">{new Date(t.timestamp).toLocaleString()}</td>
                <td>{t.action}</td><td>#{t.netuid}</td><td className="num">{fmt(t.amount_tao, 3)}</td>
                <td title={t.error || t.tx_hash || ''}><span className={'tag' + (t.status === 'failed' ? ' warn' : '')}>{t.status}</span></td></tr>
            ))}</tbody></table></div>
        </div>
      )}
    </Section>
  );
}

export default function CopyPage() {
  return <Suspense fallback={null}><CopyInner /></Suspense>;
}
