'use client';
import { useCallback, useEffect, useState } from 'react';
import { get, post } from '@/lib/api';
import { num } from '@/lib/format';
import { Addr, Card, ErrorBox, Loading } from '@/components/ui';

/** Devnet ETH + STRK of every account and contract the Starknet mods own,
 *  with one button that tops them all up (free devnet mint, owner only). */
export function FundCard() {
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [can, setCan] = useState(false);
  const [msg, setMsg] = useState('');

  const load = useCallback(() => {
    setErr(null);
    get('fund').then(setD).catch(setErr);
  }, []);
  useEffect(() => {
    load();
    get('studio/whoami').then((w: any) => setCan(!!w?.writable)).catch(() => setCan(false));
  }, [load]);

  async function fundAll() {
    setBusy(true); setMsg('');
    try {
      const r: any = await post('fund', { target: 1000 });
      setD(r);
      setMsg(r.funded ? `Topped up ${r.funded} of ${r.addresses} addresses.` : 'Everything already had 1,000 ETH and STRK.');
    } catch (e: any) { setMsg(e.message || String(e)); }
    setBusy(false);
  }

  const right = can
    ? <button className="btn sm" disabled={busy || !d} onClick={fundAll}>{busy ? 'Funding…' : 'Fund everything'}</button>
    : <span className="dim">owner only</span>;

  return (
    <Card title="Devnet funds" right={right}>
      <p className="dim" style={{ marginTop: 0 }}>
        Every account and contract the Starknet mods have on their local devnets. Funding mints free test ETH and STRK
        so each one holds 1,000 of both. Mainnet is never touched.
      </p>
      {msg && <p>{msg}</p>}
      {err ? <ErrorBox error={err} onRetry={load} /> : !d ? <Loading what="Reading balances" /> : (
        d.chains.map((c: any) => (
          <div key={c.mod} style={{ marginTop: 14 }}>
            <div className="crumb">{c.mod} · devnet :{c.port} {c.up ? '' : '· down'}</div>
            {c.note && <p className="dim">{c.note}</p>}
            {c.targets.length > 0 && (
              <table className="table"><thead><tr><th>Name</th><th>Kind</th><th>Address</th><th>ETH</th><th>STRK</th></tr></thead>
                <tbody>{c.targets.map((t: any) => (
                  <tr key={c.mod + t.address}>
                    <td>{t.name}</td><td className="dim">{t.kind}</td><td><Addr a={t.address} link={false} /></td>
                    <td>{t.ETH == null ? '—' : num(t.ETH, 2)}</td><td>{t.STRK == null ? '—' : num(t.STRK, 2)}</td>
                  </tr>))}
                </tbody></table>
            )}
          </div>
        ))
      )}
    </Card>
  );
}
