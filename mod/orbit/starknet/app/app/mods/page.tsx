'use client';
import Link from 'next/link';
import { ModCard } from '@/components/Family';
import { FundCard } from '@/components/Fund';
import { ErrorBox, Loading } from '@/components/ui';
import { useApi } from '@/lib/hooks';

export default function Mods() {
  const f = useApi<any>('mods', 20000);
  const d = f.data;
  const contracts = d?.mods.find((m: any) => m.name === 'cairo')?.items?.length ?? 0;

  return (
    <div className="stack">
      <section className="fam-hero">
        <div className="crumb">The family</div>
        <h1>Every <span>Starknet mod</span> on this node</h1>
        <p>
          This explorer is the front door. The rest of the Starknet work lives in its own mods — a contract workbench,
          time-weighted staking, anonymous voting, an anonymous subnet and a zero-knowledge WASM lab. Each one runs
          on its own port; the numbers below are read from them live.
        </p>
        <div className="fam-meter">
          <span className="pill"><span className={`dot ${d && d.up === d.count ? 'ok' : ''}`} /><b>{d ? `${d.up}/${d.count}` : '…'}</b> live</span>
          <span className="pill"><b>{contracts}</b> Cairo contracts</span>
          <Link className="pill" href="/studio">Write a contract ›</Link>
          <Link className="pill" href={`/ask?q=${encodeURIComponent('What Starknet mods run on this node and what does each do?')}`}>Ask the guide ›</Link>
        </div>
      </section>

      {f.error ? <ErrorBox error={f.error} onRetry={f.reload} /> : !d ? <Loading what="Checking each mod" /> : (
        <div className="fam-grid">
          {d.mods.map((m: any) => <div id={m.name} key={m.name} style={{ display: 'contents' }}><ModCard m={m} /></div>)}
        </div>
      )}

      <FundCard />
    </div>
  );
}
