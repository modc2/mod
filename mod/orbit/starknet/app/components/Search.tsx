'use client';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { get } from '@/lib/api';
import { useNet } from '@/lib/net';

export function routeFor(r: { kind: string; id: string | number }) {
  if (r.kind === 'tx') return `/tx?h=${r.id}`;
  if (r.kind === 'block') return `/block?n=${r.id}`;
  return `/address?a=${r.id}`;
}

export default function Search({ big = false }: { big?: boolean }) {
  const router = useRouter();
  const { net } = useNet();
  const [q, setQ] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function go(e?: React.FormEvent) {
    e?.preventDefault();
    const s = q.trim();
    if (!s || busy) return;
    setBusy(true); setErr(null);
    try {
      const r = await get(`search?q=${encodeURIComponent(s)}`, net);
      setQ('');
      router.push(routeFor(r));
    } catch (e: any) {
      setErr(e.message || 'not found');
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className={`search ${big ? 'big' : ''}`} onSubmit={go}>
      <input
        value={q}
        onChange={e => { setQ(e.target.value); setErr(null); }}
        placeholder={big ? 'Search a block number, transaction, address or token (strk, eth, usdc)' : 'Search block / tx / address'}
        spellCheck={false}
        autoComplete="off"
      />
      <button className="btn" disabled={busy}>{busy ? '…' : 'Search'}</button>
      {err && <div className="search-err">{err}</div>}
    </form>
  );
}
