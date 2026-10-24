'use client';
/* Review -> sign in the wallet -> included. One component for every
 * browser-wallet action (stake, unstake, transfer): the node prepares, the
 * wallet (SubWallet / Talisman / polkadot{.js}) shows and signs, the node
 * verifies the signature against what it built and broadcasts. */
import { useState } from 'react';
import { useWallet } from '@/lib/wallet';
import { metaFor, rejectText } from '@/lib/injected';
import { prepare, Prepared, Sent, signAndSubmit, TxKind } from '@/lib/signer';
import { useData } from '@/lib/data';
import { fmt, fmtPrice, short } from '@/lib/format';
import { useCopy } from '@/lib/hooks';
import { Spinner } from './ui';

type Phase = 'idle' | 'preparing' | 'review' | 'sign' | 'send' | 'done' | 'error';

export function useTx() {
  const w = useWallet();
  const [phase, setPhase] = useState<Phase>('idle');
  const [prep, setPrep] = useState<Prepared | null>(null);
  const [sent, setSent] = useState<Sent | null>(null);
  const [err, setErr] = useState('');
  const extId = w.wallet?.extId || null;
  const name = metaFor(extId || w.wallet?.ext)?.name || 'your wallet';

  const review = async (kind: TxKind, args: Record<string, unknown>) => {
    if (!w.wallet || !extId) { setErr('Connect SubWallet (or another browser wallet) first.'); setPhase('error'); return; }
    setErr(''); setSent(null); setPhase('preparing');
    try { setPrep(await prepare(kind, w.wallet.addr, args)); setPhase('review'); }
    catch (e) { setErr((e as Error).message); setPhase('error'); }
  };
  const sign = async () => {
    if (!prep || !extId) return;
    setErr('');
    try {
      const r = await signAndSubmit(extId, prep, s => setPhase(s));
      setSent(r); setPhase(r.ok ? 'done' : 'error');
      if (!r.ok) setErr(r.error || 'The chain rejected the transaction.');
      if (r.ok) setTimeout(() => w.refresh(), 1500);
    } catch (e) {
      const m = (e as Error).message || '';
      setErr(/cancel|reject|denied|declined/i.test(m) ? rejectText(e, name) : m);
      setPhase('error');
    }
  };
  const reset = () => { setPhase('idle'); setPrep(null); setSent(null); setErr(''); };
  return { phase, prep, sent, err, review, sign, reset, walletName: name };
}

function Line({ k, v, title }: { k: string; v: React.ReactNode; title?: string }) {
  return <div className="txl"><span className="muted">{k}</span><b className="num" title={title}>{v}</b></div>;
}

export default function TxReview({ tx, onClose }: { tx: ReturnType<typeof useTx>; onClose?: () => void }) {
  const { names } = useData();
  const [copied, copy] = useCopy();
  const { phase, prep, sent, err } = tx;
  if (phase === 'idle') return null;
  if (phase === 'preparing') return <div className="txbox"><Spinner /> composing the transaction on the node…</div>;
  const p = prep?.preview || {};
  const close = () => { tx.reset(); onClose?.(); };
  /* TAO this needs from the free balance: the amount (stake/send) + the fee */
  const need = prep ? (p.amount_tao != null && prep.kind !== 'unstake' ? p.amount_tao : 0) + (prep.fee_tao || 0) : 0;
  const short_ = prep && prep.free_tao != null && need > prep.free_tao;

  return (
    <div className={'txbox' + (phase === 'done' ? ' ok' : phase === 'error' ? ' bad' : '')}>
      {prep && (
        <>
          <div className="txh">{p.action}</div>
          {p.netuid != null && <Line k="Subnet" v={`${names[p.netuid] || ''} #${p.netuid}`} />}
          {p.hotkey && <Line k="Validator" v={short(p.hotkey)} title={p.hotkey} />}
          {p.to && <Line k="To" v={short(p.to)} title={p.to} />}
          {p.amount_tao != null && <Line k="Amount" v={`τ ${fmt(p.amount_tao, 4)}`} />}
          {p.amount_alpha != null && <Line k="Amount" v={`${fmt(p.amount_alpha, 4)} α`} />}
          {p.price != null && p.netuid != null && <Line k="Price now" v={`τ ${fmtPrice(p.price)}`} />}
          {p.est_alpha != null && <Line k="You get ≈" v={`${fmt(p.est_alpha, 4)} α`} />}
          {p.est_tao != null && <Line k="You get ≈" v={`τ ${fmt(p.est_tao, 4)}`} />}
          {p.limit_price != null && <Line k={`Limit (±${p.slippage_pct}%)`} v={`τ ${fmtPrice(p.limit_price)}`} />}
          <Line k="Network fee" v={prep.fee_tao != null ? `τ ${fmt(prep.fee_tao, 6)}` : '—'} />
          {prep.free_tao != null && <Line k="Free balance" v={`τ ${fmt(prep.free_tao, 4)}`} />}
          <p className="muted" style={{ margin: '8px 0 0', fontSize: 12 }}>
            {prep.call} · nonce {prep.nonce} · valid ~{Math.round(prep.valid_for_blocks * 12 / 60)} min.
            {p.limit_price != null && ' A limit order: if the price moves past the limit, nothing executes.'}
          </p>
        </>
      )}
      {phase === 'review' && short_ && (
        <p className="txs" style={{ color: 'var(--bad)' }}>
          Not enough free TAO: this needs τ {fmt(need, 6)} (amount + fee), the account has τ {fmt(prep!.free_tao, 6)}.
        </p>
      )}
      {phase === 'review' && (
        <div className="txa">
          <button className="pill primary" disabled={!!short_} onClick={tx.sign}>Sign in {tx.walletName}</button>
          <button className="iconbtn" onClick={close}>Cancel</button>
        </div>
      )}
      {phase === 'sign' && <p className="txs"><Spinner /> Approve it in {tx.walletName}…</p>}
      {phase === 'send' && <p className="txs"><Spinner /> Broadcast — waiting for a block (~12 s)…</p>}
      {phase === 'done' && sent && (
        <div className="txs">
          <b>Included in block #{sent.block ?? '…'} ✓</b>
          {sent.extrinsic_hash && (
            <button className="iconbtn" style={{ fontSize: 12 }} onClick={() => copy(sent.extrinsic_hash!)}
                    title={sent.extrinsic_hash}>{copied ? 'copied ✓' : `tx ${short(sent.extrinsic_hash)} ⧉`}</button>
          )}
          <button className="iconbtn" onClick={close}>Done</button>
        </div>
      )}
      {phase === 'error' && (
        <div className="txs">
          <span>{err}</span>
          <button className="iconbtn" onClick={close}>Close</button>
        </div>
      )}
    </div>
  );
}
