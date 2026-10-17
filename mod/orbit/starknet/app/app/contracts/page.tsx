'use client';
import Link from 'next/link';
import { Badge, Card, Copy, ErrorBox, Loading } from '@/components/ui';
import { useApi } from '@/lib/hooks';
import { short } from '@/lib/format';

const STATE: Record<string, 'ok' | 'warn' | 'dim'> = { fresh: 'ok', stale: 'warn', unbuilt: 'dim' };
const KIND: Record<string, 'ok' | 'accent' | 'warn' | 'dim'> = { token: 'accent', stablecoin: 'ok', privacy: 'warn', oracle: 'dim' };

export default function Contracts() {
  const c = useApi<any>('contracts', 30000);
  const d = c.data;
  const deployed = d?.yours.filter((x: any) => x.devnet).length ?? 0;

  return (
    <div className="stack">
      <section className="fam-hero">
        <div className="crumb">Contracts</div>
        <h1>Every <span>contract</span> in one place</h1>
        <p>
          Yours first — the Cairo contracts written on this node, with their address on the local devnet once
          deployed. Below them, the well-known Starknet contracts: open any one to read it through its own ABI.
        </p>
        <div className="fam-meter">
          <span className="pill"><b>{d ? d.yours.length : '…'}</b> yours</span>
          <span className="pill"><b>{d ? deployed : '…'}</b> on devnet</span>
          <Link className="pill" href="/studio">Write a new one ›</Link>
        </div>
      </section>

      {c.error ? <ErrorBox error={c.error} onRetry={c.reload} /> : !d ? <Loading what="Listing contracts" /> : <>
        <Card title="Your contracts" right={!d.workbench.ok && <Badge tone="err">workbench down</Badge>}>
          {d.yours.length === 0 ? (
            <p className="dim">None yet. <Link href="/studio">Open the studio</Link> to write your first.</p>
          ) : (
            <div className="fam-grid">
              {d.yours.map((x: any) => (
                <div key={x.name} className="mod up">
                  <div className="mod-head">
                    <div className="mod-icon">{x.name[0].toUpperCase()}</div>
                    <div>
                      <div className="mod-title">{x.name}</div>
                      <div className="mod-sub">{x.mod}{x.template ? ` · ${x.template}` : ''}</div>
                    </div>
                    {x.state && <Badge tone={STATE[x.state] || 'dim'}>{x.state}</Badge>}
                  </div>
                  {x.description && <p className="mod-pitch">{x.description}</p>}
                  <div className="mod-item">
                    <span className="n">devnet</span>
                    {x.devnet
                      ? <><span className="d mono" title={x.devnet.address}>{short(x.devnet.address)}</span><Copy text={x.devnet.address} /></>
                      : <span className="d">not deployed</span>}
                  </div>
                  <div className="mod-foot">
                    {x.mod.startsWith('cairo.') && <Link className="btn sm" href={`/studio?c=${encodeURIComponent(x.name)}`}>Edit ›</Link>}
                    {x.app && <a className="btn sm ghost" href={x.app} target="_blank" rel="noreferrer">Open app ›</a>}
                  </div>
                </div>
              ))}
            </div>
          )}
          {!d.workbench.ok && <p className="mod-err">{d.workbench.error}</p>}
        </Card>

        <Card title={`Well-known on ${d.network}`}>
          {d.known.length === 0 ? <p className="dim">None listed for this network.</p> : (
            <table className="table">
              <thead><tr><th>Contract</th><th>Kind</th><th>Address</th><th></th></tr></thead>
              <tbody>
                {d.known.map((k: any) => (
                  <tr key={k.address + k.name}>
                    <td><b>{k.name}</b>{k.note && <div className="dim">{k.note}</div>}</td>
                    <td><Badge tone={KIND[k.kind] || 'dim'}>{k.kind}</Badge></td>
                    <td><Link href={`/address?a=${k.address}`} className="hash"><span className="mono" title={k.address}>{short(k.address)}</span></Link> <Copy text={k.address} /></td>
                    <td><Link className="btn sm ghost" href={`/address?a=${k.address}`}>Read ›</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </>}
    </div>
  );
}
