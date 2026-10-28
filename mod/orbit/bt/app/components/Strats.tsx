'use client';
/* Strats — copytensor's saved trader baskets: yours (by copytensor's owner
 * key), every public one, and any that whitelist you. Open one to see its
 * traders, a 7-day replay, and a Copy button per trader that hands the
 * address to the copy flow. Building/editing strats stays in the studio. */
import { useState } from 'react';
import { usePoll } from '@/lib/hooks';
import { fmt, short } from '@/lib/format';
import { Backtest, ct, openCopy, Strat } from '@/lib/ct';
import { Ident, Pct, Spinner, Tabs } from './ui';

function Replay({ s }: { s: Strat }) {
  const bt = usePoll(() => ct<Backtest>('strats/backtest', 'POST', { traders: s.traders, days: 7 }), 0, [s.id]);
  if (!bt.data) return <span className="muted">{bt.error || <><Spinner /> replaying 7d…</>}</span>;
  const r = bt.data.stats?.total_return_pct;
  if (!bt.data.points || r == null) return <span className="muted">{bt.data.note || 'not enough history to replay'}</span>;
  return (
    <span className="muted">7d replay <Pct v={r} />
      {bt.data.stats.max_drawdown_pct != null && <> · max drawdown {fmt(bt.data.stats.max_drawdown_pct, 1)}%</>}
      {bt.data.thin && ' · thin history'}</span>
  );
}

function StratRow({ s, onCopy }: { s: Strat; onCopy: (ss58: string) => void }) {
  const [open, setOpen] = useState(false);
  const on = s.traders.filter(t => t.enabled !== false);
  const sum = on.reduce((a, t) => a + (t.alloc_tao || t.weight || 0), 0) || 1;
  return (
    <div className={'strat' + (open ? ' open' : '')}>
      <button className="strat-head" onClick={() => setOpen(o => !o)}>
        <span className="strat-n">{s.name}</span>
        <span className="strat-m">{on.length} trader{on.length === 1 ? '' : 's'}</span>
        {s.mine && <span className="tag">yours</span>}
        {!!s.live_copy_ids?.length && <span className="tag">live</span>}
        {s.visibility !== 'private' && !s.mine && <span className="tag warn">{s.visibility}</span>}
        <span className="caret">{open ? '▴' : '▾'}</span>
      </button>
      {open && (
        <div className="strat-body">
          {s.thesis && <p className="muted">{s.thesis}</p>}
          <Replay s={s} />
          {on.map(t => (
            <div className="strat-tr" key={t.ss58}>
              <Ident addr={t.ss58} size={22} />
              <span className="strat-tn" title={t.ss58}>{t.label || short(t.ss58)}</span>
              <span className="strat-w">{fmt(100 * (t.alloc_tao || t.weight || 0) / sum, 0)}%</span>
              <button className="copybtn" style={{ position: 'static' }} title="Copy this trader"
                      onClick={() => onCopy(t.ss58)}>Copy</button>
            </div>
          ))}
          {!on.length && <span className="muted">No enabled traders in this strat.</span>}
        </div>
      )}
    </div>
  );
}

export default function Strats({ onCopy }: { onCopy?: (ss58: string) => void }) {
  const [scope, setScope] = useState<'all' | 'mine'>('all');
  const list = usePoll(() => ct<{ strats: Strat[] }>('strats'), 60000);
  const copy = onCopy || ((ss58: string) => openCopy({ open: true, tab: 'copy', target: ss58 }));
  const rows = (list.data?.strats || []).filter(s => scope === 'all' || s.mine);
  return (
    <div className="strats">
      <div className="strats-bar">
        <Tabs value={scope} options={[['all', 'All'], ['mine', 'Yours']]} onChange={setScope} />
        <a className="muted" href="/copytensor/" target="_blank" rel="noreferrer">Build one in the studio ↗</a>
      </div>
      {!list.data ? <p className="muted">{list.error || <Spinner />}</p>
        : rows.length ? rows.map(s => <StratRow key={s.id} s={s} onCopy={copy} />)
        : <p className="muted">{scope === 'mine' ? 'You have not saved a strat yet.' : 'No strats shared yet.'}</p>}
    </div>
  );
}
