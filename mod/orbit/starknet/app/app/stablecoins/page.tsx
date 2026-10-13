'use client';
import Link from 'next/link';
import { Addr, Badge, Card, Empty, ErrorBox, Loading, Raw, Stat } from '@/components/ui';
import { useApi } from '@/lib/hooks';
import { useNet } from '@/lib/net';

const MODEL_LABEL: Record<string, string> = { fiat: 'Native, fiat-backed', bridged: 'Bridged', cdp: 'Crypto-backed (CDP)' };
const MODEL_TONE: Record<string, 'ok' | 'accent' | 'warn'> = { fiat: 'ok', bridged: 'accent', cdp: 'warn' };

function usd(v: number | null | undefined) {
  if (v === null || v === undefined) return '—';
  if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(1)}k`;
  return `$${v.toFixed(0)}`;
}

function Peg({ t }: { t: any }) {
  if (t.price === undefined || t.price === null) return <span className="dim">no oracle</span>;
  const d = t.deviation_bps as number;
  const tone = Math.abs(d) < 25 ? 'ok' : Math.abs(d) < 100 ? 'warn' : 'err';
  return (
    <span className="nowrap">
      <span className="mono">${t.price.toFixed(4)}</span>{' '}
      <Badge tone={tone}>{d > 0 ? '+' : ''}{d.toFixed(1)} bps</Badge>
    </span>
  );
}

function ago(s: number | undefined) {
  if (s === undefined) return '';
  return s < 90 ? `${s}s ago` : s < 5400 ? `${Math.round(s / 60)}m ago` : `${Math.round(s / 3600)}h ago`;
}

export default function Stablecoins() {
  const { net } = useNet();
  const s = useApi<any>('stablecoins', 60000);
  const d = s.data;

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <div className="crumb">Tokens</div>
          <h1>Stablecoins on Starknet</h1>
          <div className="sub">Supply read from each token contract · prices from the Pragma oracle on chain</div>
        </div>
        <Link className="btn sm" href={`/ask?q=${encodeURIComponent('How do stablecoins work on Starknet?')}`}>Ask the agent to explain</Link>
      </div>

      <div className="note">
        <b>What a stablecoin is:</b> an ordinary token whose price stays near $1 because something outside the token
        stands behind it. On Starknet that is one of three things — an <b>issuer</b> holding dollars (native USDC),
        the same thing <b>bridged</b> over from Ethereum (USDC.e, USDT, DAI), or a <b>vault of crypto collateral</b> on
        Starknet itself (Opus CASH). The kind decides who you are trusting.
      </div>

      {s.error ? <ErrorBox error={s.error} onRetry={s.reload} /> : !d ? <Loading what="Reading token contracts" /> : !d.tokens.length
        ? <Empty>No stablecoins listed for {net}.</Empty> : (
        <>
          <div className="stats">
            <Stat label="Total supply" value={usd(d.total_supply_usd)} sub={`${d.tokens.length} tokens on ${net}`} />
            {['fiat', 'bridged', 'cdp'].map(m => (
              <Stat key={m} label={MODEL_LABEL[m]} value={usd(d.by_model[m] || 0)}
                sub={d.total_supply_usd ? `${(((d.by_model[m] || 0) / d.total_supply_usd) * 100).toFixed(1)}% of all` : ''} />
            ))}
          </div>

          <Card title="Every stablecoin" right={d.oracle ? <>oracle <Addr a={d.oracle} /></> : 'no oracle on this network'}>
            <div className="scroll">
              <table className="table">
                <thead><tr><th>Token</th><th>Backing</th><th className="r">Supply</th><th>Share</th><th>Price</th><th>Oracle</th></tr></thead>
                <tbody>
                  {d.tokens.map((t: any) => {
                    const share = d.total_supply_usd ? (t.supply || 0) / d.total_supply_usd : 0;
                    return (
                      <tr key={t.id}>
                        <td>
                          <Link href={`/address?a=${t.address}`} className="hash"><b>{t.symbol}</b></Link>
                          <div className="dim" style={{ fontSize: 12 }}>{t.name} · {t.issuer}</div>
                        </td>
                        <td><Badge tone={MODEL_TONE[t.model]}>{MODEL_LABEL[t.model]}</Badge></td>
                        <td className="r mono">{t.supply === null ? <span className="dim" title={t.supply_error}>?</span> : usd(t.supply)}</td>
                        <td style={{ minWidth: 110 }}>
                          <div className="bar"><i style={{ width: `${Math.max(share * 100, 0.5)}%` }} /></div>
                          <span className="dim" style={{ fontSize: 11.5 }}>{(share * 100).toFixed(1)}%</span>
                        </td>
                        <td><Peg t={t} /></td>
                        <td className="dim nowrap" style={{ fontSize: 12 }}>
                          {t.pair ? <>{t.pair} · {ago(t.oracle_age_s)}{t.oracle_sources ? ` · ${t.oracle_sources} src` : ''}</> : '—'}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <Raw data={d} />
          </Card>

          <div className="grid g3">
            {Object.entries(d.models as Record<string, string>).map(([m, text]) => (
              <Card key={m} title={<><Badge tone={MODEL_TONE[m]}>{MODEL_LABEL[m]}</Badge></>}>
                <div className="muted" style={{ fontSize: 13 }}>{text}</div>
              </Card>
            ))}
          </div>

          <Card title="How to get stablecoins on Starknet">
            <ol className="steps">
              <li><b>Bring them in.</b> Send USDC from another chain with Circle&apos;s CCTP (arrives as native USDC), or bridge from Ethereum through StarkGate.</li>
              <li><b>Swap for them.</b> Trade ETH or STRK for USDC on a Starknet DEX such as Ekubo or AVNU.</li>
              <li><b>Borrow them.</b> Lock ETH or STRK in an Opus vault and mint CASH against it — repay to unlock.</li>
              <li><b>Issue your own.</b> Write the token in the <Link href="/studio" className="named">Build</Link> studio; the hard part is the rule behind <code>mint</code>. <Link href={`/ask?q=${encodeURIComponent('How could I launch my own stablecoin?')}`} className="named">Ask how</Link>.</li>
            </ol>
          </Card>
        </>
      )}
    </div>
  );
}
