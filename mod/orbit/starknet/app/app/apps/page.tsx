'use client';
import Link from 'next/link';
import { useMemo, useState } from 'react';
import { App, AppCard, HowTo, NewApp, useWho } from '@/components/Apps';
import { FundCard } from '@/components/Fund';
import { ErrorBox, Loading } from '@/components/ui';
import { useApi } from '@/lib/hooks';

export default function Apps() {
  const f = useApi<any>('apps', 20000);
  const d = f.data;
  const { canWrite, reloadWho } = useWho();
  const [tag, setTag] = useState('all');
  const list: App[] = useMemo(() => (d?.apps || []).filter((a: App) => tag === 'all' || a.tag === tag), [d, tag]);

  return (
    <div className="stack">
      <section className="fam-hero">
        <div className="crumb">Apps</div>
        <h1>Every <span>app on Starknet</span> here</h1>
        <p>
          Money, privacy, contracts, staking, anonymous votes — each one is its own app and its own submod of
          starknet, with an API you can call. The numbers below are read live. Make your own with <b>+ New app</b>.
        </p>
        <div className="fam-meter">
          <span className="pill"><span className={`dot ${d && d.up === d.count ? 'ok' : ''}`} /><b>{d ? `${d.up}/${d.count}` : '…'}</b> live</span>
          <a className="pill" href="#how">How to make one ›</a>
          <Link className="pill" href={`/ask?q=${encodeURIComponent('What apps are on Starknet here and how do I make one?')}`}>Ask the guide ›</Link>
        </div>
      </section>

      {d && (
        <div className="quick app-tags">
          {['all', ...d.tags].map((t: string) => (
            <button key={t} className={`chip ${tag === t ? 'on' : ''}`} onClick={() => setTag(t)}>{t}</button>
          ))}
        </div>
      )}

      {f.error ? <ErrorBox error={f.error} onRetry={f.reload} /> : !d ? <Loading what="Opening every app" /> : (
        <div className="fam-grid">
          {list.map(a => <AppCard key={a.name} a={a} canWrite={canWrite} onRemoved={f.reload} />)}
          {(tag === 'all' || tag === 'yours') && <NewApp canWrite={canWrite} onWho={reloadWho} onMade={() => f.reload()} />}
        </div>
      )}

      <div id="how"><HowTo how={d?.how} /></div>

      <FundCard />
    </div>
  );
}
