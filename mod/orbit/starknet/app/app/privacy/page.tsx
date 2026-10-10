'use client';
import { useState } from 'react';
import { Addr, Badge, BlockLink, Card, Empty, ErrorBox, Loading, Raw, Rows, Stat, TxLink } from '@/components/ui';
import { EV_LABEL, evAmount, evTone } from '@/lib/events';
import { num, short } from '@/lib/format';
import { get } from '@/lib/api';
import { useApi } from '@/lib/hooks';
import { useNet } from '@/lib/net';

const FILTERS = ['', 'Deposit', 'Withdrawal', 'OpenNoteDeposited', 'EncNoteCreated', 'NoteUsed', 'ExternalContractInvoked'];

export default function Privacy() {
  const { net } = useNet();
  const main = net === 'mainnet';
  const pool = useApi<any>(main ? 'strk20/pool' : null, 60000);
  const [ev, setEv] = useState('');
  const tape = useApi<any>(main ? `strk20/activity?limit=40${ev ? `&event=${ev}` : ''}` : null, 30000);
  const helpers = useApi<any>(main ? 'strk20/helpers' : null);

  if (!main) return <Empty>The STRK20 privacy pool is deployed on mainnet only — switch network at the top right.</Empty>;
  const p = pool.data;

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <div className="crumb">STRK20</div>
          <h1>Privacy pool {p && <Badge tone={p.paused ? 'warn' : 'ok'}>{p.paused ? 'paused' : 'live'}</Badge>}</h1>
          {p && <div className="sub"><Addr a={p.pool} full /></div>}
        </div>
        <a className="btn ghost sm" href="https://strk20.starknet.io/docs" target="_blank" rel="noopener noreferrer">Official docs</a>
      </div>

      <div className="note">
        <b>How it works, in short:</b> you deposit tokens into the pool and get back encrypted notes. Moving notes around
        inside the pool hides who paid whom and how much. Only the edges are public — money going in (deposits), money
        coming out (withdrawals), and the helper contracts the pool calls to trade or lend privately. That public part is
        what you see below.
      </div>

      {pool.error ? <ErrorBox error={pool.error} onRetry={pool.reload} /> : !p ? <Loading what="Reading the pool" /> : (
        <>
          <div className="stats">
            {Object.entries(p.public_holdings || {}).map(([k, v]) => (
              <Stat key={k} label={`${k.toUpperCase()} held`} value={num(v as number, 2)} />
            ))}
            <Stat label="Fee per action" value={`${num(p.fee_amount_1e18, 4)} STRK`} />
          </div>
          <Card title="Pool settings">
            <Rows rows={[
              ['Version', p.version],
              ['Proof valid for', `${p.proof_validity_blocks} blocks`],
              ['Fee collector', <Addr a={p.fee_collector} full />],
              ['Upgrade delay', p.upgrade_delay_s ? `${p.upgrade_delay_s}s` : <Badge tone="warn">none — upgrades are instant</Badge>],
              ['Auditor key', <span className="mono">{short(p.auditor_public_key, 10)}</span>],
              ['Screener key', <span className="mono">{short(p.screener_public_key, 10)}</span>],
            ]} />
          </Card>
        </>
      )}

      <div className="grid g2">
        <Card title="Public activity" right={tape.data ? `blocks ${tape.data.from_block?.toLocaleString()}–${tape.data.to_block?.toLocaleString()}` : ''}>
          <div className="quick" style={{ justifyContent: 'flex-start', margin: '0 0 12px' }}>
            {FILTERS.map(f => <button key={f || 'all'} className={`chip ${ev === f ? 'on' : ''}`} onClick={() => setEv(f)}>{f ? EV_LABEL[f] : 'All'}</button>)}
          </div>
          {tape.error ? <ErrorBox error={tape.error} onRetry={tape.reload} /> : !tape.data ? <Loading what="Reading events" />
            : !tape.data.events.length ? <Empty>No matching events recently.</Empty> : (
              <div className="scroll">
                <table className="table">
                  <thead><tr><th>What</th><th>Amount / who</th><th>Block</th><th className="r">Tx</th></tr></thead>
                  <tbody>
                    {tape.data.events.map((e: any, i: number) => (
                      <tr key={i}>
                        <td><Badge tone={evTone(e.event)}>{EV_LABEL[e.event] || e.event}</Badge></td>
                        <td>{evAmount(e)}</td>
                        <td><BlockLink n={e.block_number} /></td>
                        <td className="r"><TxLink h={e.tx} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
        </Card>

        <div className="stack">
          <CheckAddress />
          <Card title="Helper contracts" right={helpers.data ? `${helpers.data.helpers.length}` : ''}>
            <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>Contracts the pool has called to do DeFi privately (swaps, lending).</p>
            {helpers.error ? <ErrorBox error={helpers.error} /> : !helpers.data ? <Loading what="Finding helpers" />
              : !helpers.data.helpers.length ? <Empty>None seen recently.</Empty> : (
                <table className="table"><tbody>
                  {helpers.data.helpers.map((h: any) => (
                    <tr key={h.address}>
                      <td><Addr a={h.address} /></td>
                      <td><Badge tone={h.kind === 'invoke' ? 'accent' : 'dim'}>{h.kind === 'invoke' ? 'invoke' : 'compute'}</Badge></td>
                      <td className="r mono dim">{h.calls} call{h.calls === 1 ? '' : 's'}</td>
                    </tr>
                  ))}
                </tbody></table>
              )}
          </Card>
        </div>
      </div>
      {p && <Raw data={p} label="Show raw pool state" />}
    </div>
  );
}

function CheckAddress() {
  const { net } = useNet();
  const [a, setA] = useState('');
  const [busy, setBusy] = useState(false);
  const [r, setR] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  async function go(e: React.FormEvent) {
    e.preventDefault();
    if (!a.trim()) return;
    setBusy(true); setErr(null); setR(null);
    try { setR(await get(`strk20/user?address=${encodeURIComponent(a.trim())}`, net)); }
    catch (e: any) { setErr(e.message); }
    finally { setBusy(false); }
  }
  const deps: any[] = r?.deposits || [];
  const wds: any[] = r?.withdrawals_to || [];
  return (
    <Card title="Check an address">
      <form className="search" onSubmit={go}>
        <input value={a} onChange={e => setA(e.target.value)} placeholder="0x… wallet address" spellCheck={false} />
        <button className="btn" disabled={busy}>{busy ? '…' : 'Check'}</button>
      </form>
      {err && <div className="error" style={{ marginTop: 10 }}><span>{err}</span></div>}
      {r && (
        <div style={{ marginTop: 12 }}>
          <Rows rows={[
            ['Registered', r.registered ? <Badge tone="ok">yes</Badge> : <Badge>no</Badge>],
            ['Incoming channels', r.registered ? String(r.incoming_channels ?? '—') : '—'],
            ['Registered at', r.registered_at ? <BlockLink n={r.registered_at} /> : '—'],
            ['Public deposits', deps.length ? deps.map((d, i) => <div key={i}><TxLink h={d.tx} /> {evAmount(d)}</div>) : 'none found'],
            ['Withdrawals to it', wds.length ? wds.map((d, i) => <div key={i}><TxLink h={d.tx} /> {evAmount(d)}</div>) : 'none found'],
          ]} />
          <Raw data={r} />
        </div>
      )}
    </Card>
  );
}
