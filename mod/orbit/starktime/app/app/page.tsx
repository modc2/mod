'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import Chart, { Pt } from './Chart';
import {
  DAY, connectWallet, fmt, get, injectedWallets, lockLabel, pct, post, randomPaperAddress, short, span,
} from '../lib/api';

type Stats = {
  now: number; epoch: number; next_epoch_at: number; emission_epoch: number; rate_per_second: number;
  total_staked: number; total_weight: number; avg_multiplier: number; total_emitted: number;
  total_claimed: number; positions: number; stakers: number; apr_1x: number;
};
type Position = {
  id: number; owner: string; validator: string | null; amount: number; start: number; lock_seconds: number;
  end: number; remaining: number; multiplier: number; weight: number; share: number; pending: number;
  lapsed: boolean; unlocked: boolean;
};
type Account = {
  owner: string; wallet: number; staked: number; weight: number; share: number; pending: number;
  earning_per_second: number; positions: Position[]; now: number;
};
type Quote = {
  multiplier: number; weight: number; share: number; daily_reward: number; apr_while_locked: number; unlock_at: number;
};

const TABS = ['STAKE', 'POSITIONS', 'BOARD', 'NETWORK', 'PROTOCOL'] as const;
type Tab = (typeof TABS)[number];
const PRESETS = [0, 7, 30, 90, 180, 365, 730, 1460];
const ADDR_KEY = 'starktime_address';

export default function Page() {
  const [tab, setTab] = useState<Tab>('STAKE');
  const [stats, setStats] = useState<Stats | null>(null);
  const [curve, setCurve] = useState<Pt[]>([]);
  const [maxLockDays, setMaxLockDays] = useState(1460);
  const [network, setNetwork] = useState<any>(null);
  const [addr, setAddr] = useState('');
  const [walletName, setWalletName] = useState<string | null>(null);
  const [account, setAccount] = useState<Account | null>(null);
  const [fetchedAt, setFetchedAt] = useState(Date.now());
  const [tick, setTick] = useState(Date.now());
  const [toast, setToast] = useState<{ msg: string; bad?: boolean } | null>(null);
  const [menu, setMenu] = useState(false);

  const say = (msg: string, bad = false) => {
    setToast({ msg, bad });
    setTimeout(() => setToast(null), 4200);
  };

  // restore address (wallet or paper) from last visit
  useEffect(() => {
    const a = localStorage.getItem(ADDR_KEY);
    if (a) setAddr(a);
    get('/curve?samples=96').then((c) => {
      setCurve(c.samples.map((s: any) => ({ x: s.lock_seconds / DAY, y: s.multiplier })));
      setMaxLockDays(Math.round(c.max_lock_seconds / DAY));
    }).catch(() => {});
  }, []);

  const refresh = useCallback(async () => {
    try {
      setStats(await get('/stats'));
      if (addr) setAccount(await get(`/account?owner=${addr}`));
      setFetchedAt(Date.now());
    } catch (e: any) {
      say(e.message, true);
    }
  }, [addr]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 15000);
    return () => clearInterval(t);
  }, [refresh]);

  useEffect(() => {
    get('/network').then(setNetwork).catch(() => {});
    const t = setInterval(() => get('/network').then(setNetwork).catch(() => {}), 30000);
    return () => clearInterval(t);
  }, []);

  // 1s clock for live-ticking rewards and countdowns
  useEffect(() => {
    const t = setInterval(() => setTick(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const elapsed = Math.max(0, (tick - fetchedAt) / 1000);

  const useAddress = (a: string, name: string | null) => {
    setAddr(a);
    setWalletName(name);
    localStorage.setItem(ADDR_KEY, a);
  };

  const connect = async () => {
    const ws = injectedWallets();
    if (!ws.length) {
      say('No Starknet wallet found. Install Argent X or Braavos, or use a paper address.', true);
      return;
    }
    try {
      const a = await connectWallet(ws[0].w);
      useAddress(a, ws[0].name);
      say(`Connected ${ws[0].name}`);
    } catch (e: any) {
      say(e.message || 'connect rejected', true);
    }
  };

  const disconnect = () => {
    setAddr('');
    setWalletName(null);
    setAccount(null);
    localStorage.removeItem(ADDR_KEY);
    setMenu(false);
  };

  return (
    <main>
      <header className="top">
        <div className="wordmark">
          <span className="logo" aria-hidden />
          STARKTIME
          <span className="chip paper">PAPER</span>
        </div>
        <nav className="tabs">
          {TABS.map((t) => (
            <button key={t} className={`tab ${tab === t ? 'on' : ''}`} onClick={() => setTab(t)}>
              {t}
            </button>
          ))}
        </nav>
        <div className="who">
          {network?.ok && (
            <span className="chip live" title="Starknet mainnet head">
              <i className="pulse" /> {network.block.toLocaleString()}
            </span>
          )}
          {addr ? (
            <button className="btn ghost" onClick={() => setMenu(!menu)}>
              <i className="dot" /> {short(addr)}
            </button>
          ) : (
            <button className="btn primary" onClick={connect}>Connect</button>
          )}
          {menu && addr && (
            <div className="menu">
              <div className="lbl">{walletName ? walletName : 'Paper address'}</div>
              <code className="addr">{addr}</code>
              <button className="btn ghost sm" onClick={() => { navigator.clipboard?.writeText(addr); say('Copied'); }}>Copy</button>
              <button className="btn ghost sm" onClick={disconnect}>Disconnect</button>
            </div>
          )}
        </div>
      </header>

      <section className="hero">
        <h1 className="display">
          Stake <span className="grad">time</span>, not just tokens.
        </h1>
        <p className="sub">
          Lock STRK for a chosen time. Longer locks earn a bigger multiplier on your weight, and rewards
          stream every second, split by weight. When a lock ends it drops back to 1x.
        </p>
        <div className="stats">
          <Stat k="Total staked" v={`${fmt(stats?.total_staked)} STRK`} />
          <Stat k="Total weight" v={fmt(stats?.total_weight)} />
          <Stat k="Avg multiplier" v={stats ? `${stats.avg_multiplier.toFixed(2)}x` : '—'} />
          <Stat k="Emission today" v={`${fmt(stats?.emission_epoch)} STRK`} />
          <Stat k="Base APR (1x)" v={apr(stats?.apr_1x)} />
          <Stat k="Stakers" v={fmt(stats?.stakers, 0)} />
        </div>
      </section>

      {!addr && tab !== 'NETWORK' && tab !== 'PROTOCOL' && (
        <div className="card gate">
          <div>
            <b>Pick an address to start.</b>
            <p>Connect a Starknet wallet, or try it instantly with a paper address. Each new address gets 10,000 paper STRK.</p>
          </div>
          <div className="row">
            <button className="btn primary" onClick={connect}>Connect wallet</button>
            <button className="btn ghost" onClick={() => { useAddress(randomPaperAddress(), null); say('Paper address created'); }}>
              Use a paper address
            </button>
          </div>
        </div>
      )}

      {tab === 'STAKE' && (
        <StakeTab addr={addr} account={account} curve={curve} maxLockDays={maxLockDays}
          onDone={(m) => { say(m); refresh(); setTab('POSITIONS'); }} onErr={(m) => say(m, true)} />
      )}
      {tab === 'POSITIONS' && (
        <PositionsTab addr={addr} account={account} elapsed={elapsed} maxLockDays={maxLockDays}
          onDone={(m) => { say(m); refresh(); }} onErr={(m) => say(m, true)} />
      )}
      {tab === 'BOARD' && <BoardTab me={addr} />}
      {tab === 'NETWORK' && <NetworkTab network={network} addr={addr} />}
      {tab === 'PROTOCOL' && <ProtocolTab />}

      {toast && <div className={`toast ${toast.bad ? 'bad' : ''}`}>{toast.msg}</div>}
      <footer className="foot">
        starktime · Rust API + Cairo contract · paper ledger mirrors <code>contracts/src/lib.cairo</code>
      </footer>
    </main>
  );
}

// early paper pools are tiny next to the emission, so APR can be absurd
function apr(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return v > 10 ? '>1,000%' : pct(v, 1);
}

function Stat({ k, v }: { k: string; v: string }) {
  return (
    <div className="stat">
      <div className="k">{k}</div>
      <div className="v">{v}</div>
    </div>
  );
}

// ── STAKE ──

function StakeTab({ addr, account, curve, maxLockDays, onDone, onErr }: {
  addr: string; account: Account | null; curve: Pt[]; maxLockDays: number;
  onDone: (m: string) => void; onErr: (m: string) => void;
}) {
  const [amount, setAmount] = useState('1000');
  const [days, setDays] = useState(365);
  const [validator, setValidator] = useState('');
  const [quote, setQuote] = useState<Quote | null>(null);
  const [busy, setBusy] = useState(false);
  const amt = Number(amount);

  useEffect(() => {
    if (!(amt > 0)) { setQuote(null); return; }
    const t = setTimeout(() => {
      get(`/quote?amount=${amt}&lock_seconds=${Math.round(days * DAY)}`).then(setQuote).catch(() => setQuote(null));
    }, 150);
    return () => clearTimeout(t);
  }, [amt, days]);

  const stake = async () => {
    setBusy(true);
    try {
      const r = await post('/stake', { owner: addr, amount: amt, lock_seconds: Math.round(days * DAY), validator: validator || null });
      onDone(`Staked ${fmt(amt)} STRK for ${lockLabel(r.position.lock_seconds)} at ${r.position.multiplier.toFixed(2)}x`);
    } catch (e: any) {
      onErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  const marker = quote ? { x: days, y: quote.multiplier } : null;

  return (
    <div className="grid2">
      <div className="card">
        <div className="card-h"><span className="n">01</span> Lock</div>
        <label className="lbl">Amount</label>
        <div className="field">
          <input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^0-9.]/g, ''))} />
          <span className="unit">STRK</span>
          {account && <button className="btn ghost sm" onClick={() => setAmount(String(Math.floor(account.wallet)))}>MAX</button>}
        </div>
        {account && <div className="hint">Paper wallet: {fmt(account.wallet)} STRK</div>}

        <label className="lbl">Lock time · <b>{lockLabel(days * DAY)}</b></label>
        <input type="range" min={0} max={maxLockDays} value={days} onChange={(e) => setDays(Number(e.target.value))} className="slider" />
        <div className="presets">
          {PRESETS.filter((p) => p <= maxLockDays).map((p) => (
            <button key={p} className={`chip sel ${days === p ? 'on' : ''}`} onClick={() => setDays(p)}>
              {lockLabel(p * DAY)}
            </button>
          ))}
        </div>

        <details className="adv">
          <summary>Validator (optional)</summary>
          <input className="input" placeholder="0x… Starknet staker address" value={validator} onChange={(e) => setValidator(e.target.value.trim())} />
          <div className="hint">Tags your weight to a validator. Shown on the BOARD tab.</div>
        </details>

        <button className="btn primary big" disabled={!addr || busy || !(amt > 0)} onClick={stake}>
          {busy ? 'Staking…' : addr ? 'Stake' : 'Pick an address first'}
        </button>
      </div>

      <div className="card">
        <div className="card-h"><span className="n">02</span> You get</div>
        <div className="big-mult">{quote ? `${quote.multiplier.toFixed(2)}x` : '—'}</div>
        <div className="kv">
          <span>Weight</span><b>{fmt(quote?.weight)}</b>
          <span>Share of pool</span><b>{pct(quote?.share)}</b>
          <span>Rewards / day (now)</span><b>{fmt(quote?.daily_reward)} STRK</b>
          <span>APR while locked</span><b>{apr(quote?.apr_while_locked)}</b>
          <span>Unlocks</span><b>{quote ? (days === 0 ? 'anytime' : new Date(quote.unlock_at * 1000).toLocaleDateString()) : '—'}</b>
        </div>
        <div className="card-h sub2">Lock multiplier</div>
        <Chart data={curve} marker={marker} label="Multiplier by lock length"
          fmtX={(x) => lockLabel(Math.round(x) * DAY)} fmtY={(y) => `${y.toFixed(1)}x`} height={200} />
      </div>
    </div>
  );
}

// ── POSITIONS ──

function PositionsTab({ addr, account, elapsed, maxLockDays, onDone, onErr }: {
  addr: string; account: Account | null; elapsed: number; maxLockDays: number;
  onDone: (m: string) => void; onErr: (m: string) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [extendId, setExtendId] = useState<number | null>(null);
  const [extendDays, setExtendDays] = useState(365);
  if (!addr) return null;
  if (!account) return <div className="card empty">Loading…</div>;

  const live = account.pending + account.earning_per_second * elapsed;

  const act = async (key: string, path: string, body: any, ok: (r: any) => string) => {
    setBusy(key);
    try {
      onDone(ok(await post(path, body)));
    } catch (e: any) {
      onErr(e.message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <div className="card acct">
        <Stat k="Paper wallet" v={`${fmt(account.wallet)} STRK`} />
        <Stat k="Staked" v={`${fmt(account.staked)} STRK`} />
        <Stat k="Your weight" v={`${fmt(account.weight)} · ${pct(account.share)}`} />
        <div className="stat">
          <div className="k">Rewards (live)</div>
          <div className="v up">{live.toFixed(4)}</div>
        </div>
        <button className="btn primary" disabled={busy === 'claim' || live <= 0}
          onClick={() => act('claim', '/claim', { owner: addr }, (r) => `Claimed ${fmt(r.claimed, 4)} STRK`)}>
          Claim
        </button>
      </div>

      <div className="card">
        <div className="card-h"><span className="n">03</span> Your positions</div>
        {account.positions.length === 0 ? (
          <div className="empty">No positions yet. Stake on the STAKE tab.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>#</th><th>Amount</th><th>Lock</th><th>Mult</th><th>Weight</th><th>Rewards</th><th>Unlocks in</th><th /></tr>
              </thead>
              <tbody>
                {account.positions.map((p) => {
                  const remaining = Math.max(0, p.remaining - Math.floor(elapsed));
                  return (
                    <tr key={p.id}>
                      <td className="mono">{p.id}</td>
                      <td>{fmt(p.amount)}</td>
                      <td>{lockLabel(p.lock_seconds)}</td>
                      <td>{p.multiplier.toFixed(2)}x {p.lapsed && <span className="chip warn">lapsed</span>}</td>
                      <td>{fmt(p.weight)}</td>
                      <td className="up">{(p.pending + account.earning_per_second * elapsed * (account.weight ? p.weight / account.weight : 0)).toFixed(4)}</td>
                      <td>{remaining > 0 ? span(remaining) : <span className="chip good">unlocked</span>}</td>
                      <td className="acts">
                        <button className="btn ghost sm" onClick={() => setExtendId(extendId === p.id ? null : p.id)}>Extend</button>
                        <button className="btn ghost sm" disabled={remaining > 0 || busy === `u${p.id}`}
                          title={remaining > 0 ? 'Still locked' : 'Withdraw principal + rewards'}
                          onClick={() => act(`u${p.id}`, '/unstake', { owner: addr, id: p.id },
                            (r) => `Unstaked ${fmt(r.principal)} STRK + ${fmt(r.rewards, 4)} rewards`)}>
                          Unstake
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {extendId != null && (
          <div className="extend">
            <span>Re-lock #{extendId} from now for <b>{lockLabel(extendDays * DAY)}</b></span>
            <input type="range" min={0} max={maxLockDays} value={extendDays} onChange={(e) => setExtendDays(Number(e.target.value))} className="slider" />
            <button className="btn primary sm" disabled={busy === 'x'}
              onClick={() => act('x', '/extend', { owner: addr, id: extendId, lock_seconds: extendDays * DAY },
                (r) => { setExtendId(null); return `#${r.position.id} now ${r.position.multiplier.toFixed(2)}x`; })}>
              Confirm
            </button>
          </div>
        )}
      </div>
    </>
  );
}

// ── BOARD ──

function BoardTab({ me }: { me: string }) {
  const [board, setBoard] = useState<any[]>([]);
  const [vals, setVals] = useState<any[]>([]);
  const [events, setEvents] = useState<any[]>([]);
  useEffect(() => {
    get('/leaderboard').then((r) => setBoard(r.stakers)).catch(() => {});
    get('/validators').then((r) => setVals(r.validators)).catch(() => {});
    get('/history').then((r) => setEvents(r.events)).catch(() => {});
  }, []);
  const meNorm = me ? '0x' + me.replace(/^0x0*/, '') : '';
  return (
    <div className="grid2">
      <div className="card">
        <div className="card-h">Top stakers by weight</div>
        {board.length === 0 ? <div className="empty">Nobody has staked yet.</div> : (
          <table>
            <thead><tr><th>#</th><th>Address</th><th>Staked</th><th>Weight</th><th>Share</th></tr></thead>
            <tbody>
              {board.map((r, i) => (
                <tr key={r.owner} className={r.owner === meNorm ? 'me' : ''}>
                  <td>{i + 1}</td><td className="mono">{short(r.owner)}</td><td>{fmt(r.staked)}</td><td>{fmt(r.weight)}</td>
                  <td><Meter v={r.share} /> {pct(r.share, 1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <div className="card">
        <div className="card-h">Validators</div>
        {vals.length === 0 ? <div className="empty">No weight yet.</div> : (
          <table>
            <thead><tr><th>Validator</th><th>Staked</th><th>Weight</th><th>Stakers</th></tr></thead>
            <tbody>
              {vals.map((v) => (
                <tr key={v.validator}><td className="mono">{v.validator === 'pool' ? 'pool (none)' : short(v.validator)}</td>
                  <td>{fmt(v.staked)}</td><td>{fmt(v.weight)}</td><td>{v.delegators}</td></tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="card-h sub2">Recent activity</div>
        <ul className="events">
          {events.slice(0, 12).map((e, i) => (
            <li key={i}><span className={`chip ev-${e.kind}`}>{e.kind}</span> <span className="mono">{short(e.owner)}</span>
              <b>{fmt(e.amount, 4)}</b> <span className="mute">{new Date(e.ts * 1000).toLocaleString()}</span></li>
          ))}
          {events.length === 0 && <li className="mute">Nothing yet.</li>}
        </ul>
      </div>
    </div>
  );
}

function Meter({ v }: { v: number }) {
  return <span className="meter"><i style={{ width: `${Math.max(2, Math.min(100, v * 100))}%` }} /></span>;
}

// ── NETWORK ──

function NetworkTab({ network, addr }: { network: any; addr: string }) {
  const [bal, setBal] = useState<any>(null);
  const [q, setQ] = useState(addr);
  const look = (a: string) => a && get(`/balance?address=${a}`).then(setBal).catch((e) => setBal({ error: e.message }));
  useEffect(() => { if (addr) { setQ(addr); look(addr); } }, [addr]);
  const s = network?.staking;
  return (
    <div className="grid2">
      <div className="card">
        <div className="card-h"><i className="pulse" /> Starknet mainnet (live)</div>
        {!network ? <div className="empty">Reaching Starknet…</div> : !network.ok ? (
          <div className="empty bad">RPC unreachable: {network.error}</div>
        ) : (
          <div className="kv">
            <span>Chain</span><b>{network.chain_id}</b>
            <span>Block</span><b>{network.block.toLocaleString()}</b>
            <span>Native staking: total stake</span><b>{fmt(s?.total_stake_strk)} STRK</b>
            <span>Staking contract balance</span><b>{fmt(s?.strk_held)} STRK</b>
            <span>Staking epoch</span><b>{s?.epoch ?? '—'}</b>
            <span>Min validator stake</span><b>{fmt(s?.min_validator_stake_strk)} STRK</b>
            <span>Staking contract</span><b className="mono">{short(s?.contract)}</b>
          </div>
        )}
        <p className="hint">Native Starknet staking delegates STRK to validators at a flat rate. StarkTime adds the time dimension: the longer you commit, the bigger your share.</p>
      </div>
      <div className="card">
        <div className="card-h">Balance lookup</div>
        <div className="field">
          <input placeholder="0x… Starknet address" value={q} onChange={(e) => setQ(e.target.value.trim())} />
          <button className="btn ghost sm" onClick={() => look(q)}>Look up</button>
        </div>
        {bal && (bal.error ? <div className="empty bad">{bal.error}</div> : (
          <div className="kv">
            <span>Address</span><b className="mono">{short(bal.address)}</b>
            <span>STRK</span><b>{fmt(bal.strk, 4)}</b>
            <span>ETH</span><b>{fmt(bal.eth, 6)}</b>
          </div>
        ))}
        <p className="hint">Real on-chain balances. The STAKE tab uses paper STRK until the Cairo contract is deployed.</p>
      </div>
    </div>
  );
}

// ── PROTOCOL ──

function ProtocolTab() {
  const [params, setParams] = useState<any>(null);
  const [emis, setEmis] = useState<Pt[]>([]);
  const [cur, setCur] = useState(0);
  const [contract, setContract] = useState<any>(null);
  const [paper, setPaper] = useState('');
  const [showSrc, setShowSrc] = useState(false);
  useEffect(() => {
    get('/params').then((r) => setParams(r.params)).catch(() => {});
    get('/emissions?epochs=730').then((r) => {
      setCur(r.current_epoch);
      setEmis(r.schedule.map((s: any) => ({ x: s.epoch, y: s.emission })));
    }).catch(() => {});
    get('/contract').then(setContract).catch(() => {});
    get('/whitepaper').then((r) => setPaper(r.markdown)).catch(() => {});
  }, []);
  const marker = useMemo(() => emis.find((e) => e.x === cur) ?? null, [emis, cur]);
  return (
    <>
      <div className="grid2">
        <div className="card">
          <div className="card-h">Emission per epoch (day)</div>
          <Chart data={emis} area marker={marker} label="Daily emission schedule"
            fmtX={(x) => `day ${Math.round(x)}`} fmtY={(y) => fmt(y, 0)} height={200} />
          {params && (
            <div className="kv">
              <span>Day 0 emission</span><b>{fmt(params.emission_per_epoch)} STRK</b>
              <span>Decay per day</span><b>{(params.decay_bps / 100).toFixed(2)}%</b>
              <span>Floor</span><b>{fmt(params.emission_floor)} STRK / day</b>
              <span>Max lock</span><b>{lockLabel(params.max_lock_seconds)}</b>
              <span>Min stake</span><b>{params.min_stake} STRK</b>
            </div>
          )}
        </div>
        <div className="card">
          <div className="card-h">Cairo contract</div>
          {contract && (
            <div className="kv">
              <span>Network</span><b>{contract.network}</b>
              <span>Status</span><b>{contract.deployed ? <span className="chip good">deployed</span> : <span className="chip warn">not deployed</span>}</b>
              {contract.address && (<><span>Address</span><b className="mono">{short(contract.address)}</b></>)}
            </div>
          )}
          <p className="hint">Same math as the paper ledger: weight = amount × multiplier, an acc-per-weight reward index, lapse-to-1x via a permissionless <code>poke</code>.</p>
          <div className="pills">{contract?.entrypoints?.map((e: string) => <span key={e} className="chip">{e}</span>)}</div>
          <button className="btn ghost sm" onClick={() => setShowSrc(!showSrc)}>{showSrc ? 'Hide' : 'View'} source</button>
          {showSrc && <pre className="src">{contract?.source}</pre>}
        </div>
      </div>
      {paper && (
        <div className="card paperdoc">
          <div className="card-h">Whitepaper</div>
          <Markdown md={paper} />
        </div>
      )}
    </>
  );
}

// tiny markdown: headings, paragraphs, bullet lists, code fences, inline code
function Markdown({ md }: { md: string }) {
  const blocks = md.split(/\n*```[a-z]*\n([\s\S]*?)\n```\n*|\n{2,}/);
  const inline = (s: string) =>
    s.split(/(`[^`]+`|\*\*[^*]+\*\*)/).map((p, i) =>
      p.startsWith('`') ? <code key={i}>{p.slice(1, -1)}</code> : p.startsWith('**') ? <b key={i}>{p.slice(2, -2)}</b> : p);
  return (
    <div className="md">
      {blocks.map((b, i) => {
        if (b == null || !b.trim()) return null;
        if (i % 2 === 1) return <pre key={i} className="src">{b}</pre>;
        const t = b.trim();
        if (t.startsWith('### ')) return <h4 key={i}>{inline(t.slice(4))}</h4>;
        if (t.startsWith('## ')) return <h3 key={i}>{inline(t.slice(3))}</h3>;
        if (t.startsWith('# ')) return <h2 key={i}>{inline(t.slice(2))}</h2>;
        if (/^[-*] /.test(t)) return <ul key={i}>{t.split('\n').map((l, j) => <li key={j}>{inline(l.replace(/^[-*] /, ''))}</li>)}</ul>;
        return <p key={i}>{inline(t)}</p>;
      })}
    </div>
  );
}
