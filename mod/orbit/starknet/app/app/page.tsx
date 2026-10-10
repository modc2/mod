'use client';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';
import Search from '@/components/Search';
import { BlockLink, Card, Empty, ErrorBox, Loading, Stat, TxLink, Badge } from '@/components/ui';
import { ago, num } from '@/lib/format';
import { EV_LABEL, evAmount, evTone } from '@/lib/events';
import { useApi } from '@/lib/hooks';
import { useNet } from '@/lib/net';

type B = { block_number: number; block_hash?: string; timestamp?: number; transaction_count?: number; event_count?: number; error?: string };

export default function Home() {
  const router = useRouter();
  const { net } = useNet();
  const status = useApi<any>('status', 15000);
  const blocks = useApi<{ head: number; blocks: B[] }>('blocks?limit=12', 8000);
  const pool = useApi<any>(net === 'mainnet' ? 'strk20/pool' : null, 60000);
  const tape = useApi<any>(net === 'mainnet' ? 'strk20/activity?limit=6' : null, 30000);
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => { const t = setInterval(() => setNow(Date.now() / 1000), 1000); return () => clearInterval(t); }, []);

  // which block rows are new since the last poll, for a brief highlight
  const seen = useRef<number>(0);
  const head = blocks.data?.head || 0;
  const prevHead = useRef(0);
  useEffect(() => { if (head) { seen.current = prevHead.current; prevHead.current = head; } }, [head]);

  const bs = (blocks.data?.blocks || []).filter(b => !b.error);
  const times = bs.map(b => b.timestamp || 0).filter(Boolean);
  const blockTime = times.length > 1 ? (times[0] - times[times.length - 1]) / (times.length - 1) : null;
  const txs = bs.reduce((s, b) => s + (b.transaction_count || 0), 0);

  return (
    <>
      <div className="hero">
        <h1>Explore <span>Starknet</span></h1>
        <p>Blocks, transactions, wallets and any contract — read straight from the chain.</p>
        <Search big />
        <div className="quick">
          {['strk', 'eth', 'usdc'].map(t => (
            <button key={t} className="chip" onClick={() => router.push(`/address?a=${t}`)}>{t.toUpperCase()} token</button>
          ))}
          <Link className="chip" href="/privacy">STRK20 privacy pool</Link>
        </div>
      </div>

      <div className="stats">
        <Stat label="Latest block" value={status.data ? status.data.block_number.toLocaleString() : '—'}
          sub={bs[0]?.timestamp ? ago(bs[0].timestamp, now) : ' '} />
        <Stat label="Block time" value={blockTime ? `${blockTime.toFixed(1)}s` : '—'} sub="average, last 12 blocks" />
        <Stat label="Transactions" value={bs.length ? txs.toLocaleString() : '—'} sub={`in the last ${bs.length || 12} blocks`} />
        <Stat label="Network" value={status.data?.chain || '—'} sub={status.data ? `RPC spec ${status.data.spec_version}` : ' '} />
      </div>

      <div className="grid g32">
        <Card title="Latest blocks" right={<><span className="spin" style={{ width: 10, height: 10, opacity: blocks.loading ? 1 : 0 }} /> live</>}>
          {blocks.error && !blocks.data ? <ErrorBox error={blocks.error} onRetry={blocks.reload} />
            : !blocks.data ? <Loading what="Loading blocks" /> : (
              <div className="scroll">
                <table className="table">
                  <thead><tr><th>Block</th><th>Age</th><th className="r">Txs</th><th className="r">Events</th><th>Hash</th></tr></thead>
                  <tbody>
                    {blocks.data.blocks.map(b => (
                      <tr key={b.block_number} className={seen.current && b.block_number > seen.current ? 'fresh' : ''}>
                        <td><BlockLink n={b.block_number} /></td>
                        <td className="muted">{b.error ? <span className="dim">unavailable</span> : ago(b.timestamp, now)}</td>
                        <td className="r mono">{b.transaction_count ?? '—'}</td>
                        <td className="r mono dim">{b.event_count ?? '—'}</td>
                        <td className="mono dim">{b.block_hash ? `${b.block_hash.slice(0, 12)}…` : ''}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
        </Card>

        <div className="stack">
          <Card title="STRK20 privacy pool" right={pool.data && <Badge tone={pool.data.paused ? 'warn' : 'ok'}>{pool.data.paused ? 'paused' : 'live'}</Badge>}>
            {net !== 'mainnet' ? <Empty>The privacy pool is on mainnet only.</Empty>
              : pool.error ? <ErrorBox error={pool.error} onRetry={pool.reload} />
              : !pool.data ? <Loading what="Reading the pool" /> : (
                <>
                  <div className="tokens">
                    {Object.entries(pool.data.public_holdings || {}).map(([k, v]) => (
                      <div className="token" key={k}><div className="sym">{k.toUpperCase()}</div><div className="amt">{num(v as number, 2)}</div></div>
                    ))}
                  </div>
                  <p className="muted" style={{ fontSize: 13, margin: '12px 0 0' }}>
                    Held by the pool contract. Inside it, who sent what to whom is encrypted.{' '}
                    <Link href="/privacy" className="hash">Open the pool</Link>
                  </p>
                </>
              )}
          </Card>
          {net === 'mainnet' && (
            <Card title="Pool activity" right={<Link href="/privacy" className="hash">all</Link>}>
              {tape.error ? <ErrorBox error={tape.error} /> : !tape.data ? <Loading what="Reading events" />
                : !tape.data.events.length ? <Empty>No recent pool events.</Empty> : (
                  <table className="table"><tbody>
                    {tape.data.events.map((e: any, i: number) => (
                      <tr key={i}>
                        <td><Badge tone={evTone(e.event)}>{EV_LABEL[e.event] || e.event}</Badge></td>
                        <td className="mono">{evAmount(e)}</td>
                        <td className="r"><TxLink h={e.tx} /></td>
                      </tr>
                    ))}
                  </tbody></table>
                )}
            </Card>
          )}
        </div>
      </div>
    </>
  );
}
