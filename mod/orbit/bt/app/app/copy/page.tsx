'use client';
/* Copy trading, full page. The same flow lives in the copy sidebar
 * (components/CopyDock, toggled from the top bar / rail) — both render
 * components/CopyPanel against the copytensor submod at {BASE}/ct/*. */
import { Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import { Section } from '@/components/ui';
import CopyPanel from '@/components/CopyPanel';
import Strats from '@/components/Strats';

function CopyInner() {
  const target = useSearchParams().get('target') || '';
  return (
    <Section id="copy" title="Copy trading"
      lead={<>Pick a trader and an amount — your stake follows their subnet mix. Powered by <b>copytensor</b>, which runs inside bt.{' '}
        <a href="/copytensor/" target="_blank" rel="noreferrer">Full copy studio ↗</a></>}>
      <CopyPanel target={target} />
      <div className="card" style={{ marginTop: 14 }}>
        <h3>Strats</h3>
        <Strats />
      </div>
    </Section>
  );
}

export default function CopyPage() {
  return <Suspense fallback={null}><CopyInner /></Suspense>;
}
