'use client';
import Link from 'next/link';
import { Suspense } from 'react';
import { Addr, Badge, Card, Copy, Empty, ErrorBox, Loading, Raw, Rows, Stat, TxLink } from '@/components/ui';
import { ago, hexInt, num, units, when } from '@/lib/format';
import { useApi, useParam } from '@/lib/hooks';

export default function Page() {
  return <Suspense fallback={<Loading />}><BlockPage /></Suspense>;
}

function BlockPage() {
  const n = useParam('n') || 'latest';
  const b = useApi<any>(`block?id=${encodeURIComponent(n)}&full=1`);
  const d = b.data;

  if (b.error) return <ErrorBox error={b.error} onRetry={b.reload} />;
  if (!d) return <Loading what={`Loading block ${n}`} />;

  const num_ = d.block_number as number | undefined;
  const txs: any[] = d.transactions || [];
  const fri = (p: any) => (p ? `${num(units(p.price_in_fri, 9), 3)} gFRI` : '—');

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <div className="crumb">Block</div>
          <h1>{num_ !== undefined ? `#${num_.toLocaleString()}` : 'Pending block'}
            <Badge tone={d.status === 'ACCEPTED_ON_L1' ? 'ok' : d.status === 'REJECTED' ? 'err' : 'accent'}>{statusText(d.status)}</Badge>
          </h1>
          {d.block_hash && <div className="sub">{d.block_hash}<Copy text={d.block_hash} /></div>}
        </div>
        {num_ !== undefined && (
          <div style={{ display: 'flex', gap: 8 }}>
            {num_ > 0 && <Link className="btn ghost sm" href={`/block?n=${num_ - 1}`}>Previous</Link>}
            <Link className="btn ghost sm" href={`/block?n=${num_ + 1}`}>Next</Link>
          </div>
        )}
      </div>

      <div className="stats">
        <Stat label="Time" value={ago(d.timestamp)} sub={when(d.timestamp)} />
        <Stat label="Transactions" value={txs.length} />
        <Stat label="Events" value={d.event_count ?? '—'} />
        <Stat label="Version" value={d.starknet_version || '—'} sub={d.l1_da_mode ? `data on L1 as ${d.l1_da_mode.toLowerCase()}` : ''} />
      </div>

      <Card title="Details">
        <Rows rows={[
          ['Parent block', d.parent_hash ? <Link className="hash mono" href={`/block?n=${d.parent_hash}`}>{d.parent_hash}</Link> : '—'],
          ['Sequencer', <Addr a={d.sequencer_address} full />],
          ['State root', <span className="mono">{d.new_root || '—'}</span>],
          ['L2 gas price', fri(d.l2_gas_price)],
          ['L1 gas price', fri(d.l1_gas_price)],
          ['L1 data gas price', fri(d.l1_data_gas_price)],
          ['State changes', d.state_diff_length ?? '—'],
        ]} />
      </Card>

      <Card title="Transactions" right={`${txs.length}`}>
        {!txs.length ? <Empty>This block has no transactions.</Empty> : (
          <div className="scroll">
            <table className="table">
              <thead><tr><th>Hash</th><th>Type</th><th>From</th><th className="r">Nonce</th></tr></thead>
              <tbody>
                {txs.map((t: any) => (
                  <tr key={t.transaction_hash}>
                    <td><TxLink h={t.transaction_hash} /></td>
                    <td><Badge>{t.type}</Badge></td>
                    <td>{t.sender_address ? <Addr a={t.sender_address} /> : t.contract_address ? <Addr a={t.contract_address} /> : <span className="dim">—</span>}</td>
                    <td className="r mono dim">{t.nonce !== undefined ? hexInt(t.nonce) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <Raw data={d} />
    </div>
  );
}

function statusText(s?: string) {
  return ({ ACCEPTED_ON_L2: 'on L2', ACCEPTED_ON_L1: 'final on L1', PRE_CONFIRMED: 'pre-confirmed', PENDING: 'pending', REJECTED: 'rejected' } as any)[s || ''] || (s || '').toLowerCase();
}
