'use client';
import { Suspense, useState } from 'react';
import { Addr, Badge, BlockLink, Card, Copy, Empty, ErrorBox, Loading, Raw, Rows, Stat } from '@/components/ui';
import { fee, hexInt, short } from '@/lib/format';
import { useApi, useParam } from '@/lib/hooks';

export default function Page() {
  return <Suspense fallback={<Loading />}><TxPage /></Suspense>;
}

function TxPage() {
  const h = useParam('h');
  const tx = useApi<any>(h ? `tx?hash=${h}` : null);
  const rc = useApi<any>(h ? `receipt?hash=${h}` : null);
  const [showData, setShowData] = useState(false);

  if (!h) return <Empty>No transaction hash given.</Empty>;
  if (tx.error) return <ErrorBox error={tx.error} onRetry={tx.reload} />;
  if (!tx.data) return <Loading what="Loading transaction" />;

  const t = tx.data, r = rc.data;
  const ok = r?.execution_status === 'SUCCEEDED';
  const events: any[] = r?.events || [];
  const calls = decodeCalls(t.calldata || []);

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <div className="crumb">Transaction</div>
          <h1>{short(h, 8)}
            {r && <Badge tone={ok ? 'ok' : 'err'}>{ok ? 'succeeded' : 'reverted'}</Badge>}
            <Badge>{t.type}</Badge>
          </h1>
          <div className="sub">{t.transaction_hash}<Copy text={t.transaction_hash} /></div>
        </div>
      </div>

      <div className="stats">
        <Stat label="Block" value={r?.block_number !== undefined ? <BlockLink n={r.block_number} /> : rc.loading ? '…' : 'pending'} />
        <Stat label="Fee" value={r ? fee(r.actual_fee) : '…'} />
        <Stat label="Calls" value={calls ? calls.length : '—'} sub="contracts touched" />
        <Stat label="Events" value={r ? events.length : '…'} />
      </div>

      {r?.revert_reason && <div className="error"><span>{r.revert_reason}</span></div>}

      <Card title="Details">
        <Rows rows={[
          ['From', t.sender_address ? <Addr a={t.sender_address} full /> : t.contract_address ? <Addr a={t.contract_address} full /> : '—'],
          ['Status', r ? `${r.execution_status?.toLowerCase()} · ${r.finality_status?.replaceAll('_', ' ').toLowerCase()}` : '…'],
          ['Nonce', t.nonce !== undefined ? hexInt(t.nonce) : '—'],
          ['Version', t.version],
          ['L2 gas used', r?.execution_resources ? r.execution_resources.l2_gas?.toLocaleString() : '—'],
          ['Tip', t.tip !== undefined ? hexInt(t.tip) : '—'],
        ]} />
      </Card>

      {calls && calls.length > 0 && (
        <Card title="Calls" right={`${calls.length}`}>
          <table className="table">
            <thead><tr><th>Contract</th><th>Function selector</th><th className="r">Args</th></tr></thead>
            <tbody>
              {calls.map((c, i) => (
                <tr key={i}><td><Addr a={c.to} /></td><td className="mono dim">{short(c.selector, 8)}</td><td className="r mono">{c.args}</td></tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      <Card title="Events" right={`${events.length}`}>
        {!r ? <Loading what="Loading receipt" /> : !events.length ? <Empty>No events.</Empty> : (
          <div className="scroll">
            <table className="table">
              <thead><tr><th>#</th><th>Emitted by</th><th>Event key</th><th className="r">Data</th></tr></thead>
              <tbody>
                {events.map((e, i) => (
                  <tr key={i}>
                    <td className="dim mono">{i}</td>
                    <td><Addr a={e.from_address} /></td>
                    <td className="mono dim">{knownEvent(e.keys?.[0]) || short(e.keys?.[0], 8)}</td>
                    <td className="r mono dim">{e.data?.length || 0} felts</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Calldata" right={<button className="btn ghost sm" onClick={() => setShowData(!showData)}>{showData ? 'hide' : `show ${t.calldata?.length || 0} felts`}</button>}>
        {showData ? <pre>{(t.calldata || []).join('\n')}</pre> : <span className="dim" style={{ fontSize: 13 }}>Raw input to the account contract.</span>}
      </Card>
      <Raw data={{ transaction: t, receipt: r }} />
    </div>
  );
}

/** Account-contract calldata (Cairo 1 multicall): [n, (to, selector, len, ...args)*n]. */
function decodeCalls(cd: string[]): { to: string; selector: string; args: number }[] | null {
  try {
    const n = hexInt(cd[0]);
    if (!n || n > 64) return null;
    const out = [];
    let i = 1;
    for (let k = 0; k < n; k++) {
      const to = cd[i], selector = cd[i + 1], len = hexInt(cd[i + 2]);
      if (!to || !selector || len > cd.length) return null;
      out.push({ to, selector, args: len });
      i += 3 + len;
    }
    return i === cd.length ? out : null;
  } catch { return null; }
}

const EVENTS: Record<string, string> = {
  '0x99cd8bde557814842a3121e8ddfd433a539b8c9f14bf31ebf108d12e6196e9': 'Transfer',
  '0x134692b230b9e1ffa39098904722134159652b09c5bc41d88d6698779d228ff': 'Approval',
  '0x1dcde06aabdbca2f80aa51392b345d7549d7757aa855f7e37f5d335ac8243b1': 'TransactionExecuted',
};
function knownEvent(k?: string) { return k ? EVENTS[k] : undefined; }
