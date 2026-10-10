'use client';
import Link from 'next/link';
import { Suspense, useEffect, useMemo, useState } from 'react';
import { Badge, Card, Copy, Empty, ErrorBox, Loading, Raw, Rows, Stat, TxLink, Addr, BlockLink } from '@/components/ui';
import { post } from '@/lib/api';
import { nameOf, norm, num, POOL, short, TOKENS, units } from '@/lib/format';
import { useApi, useParam } from '@/lib/hooks';
import { useNet } from '@/lib/net';

type Fn = { name: string; mutability: string; signature: string; inputs: { name: string; type: string }[]; outputs: string[]; interface?: string };

export default function Page() {
  return <Suspense fallback={<Loading />}><AddressPage /></Suspense>;
}

function AddressPage() {
  const raw = useParam('a');
  const a = raw ? norm(raw) : null;
  const acct = useApi<any>(a ? `account?address=${a}` : null);
  const usdc = useApi<any>(a ? `balance?address=${a}&token=usdc` : null);
  const iface = useApi<any>(a ? `contract?address=${a}` : null);
  const [tab, setTab] = useState<'read' | 'write' | 'events'>('read');

  if (!a) return <Empty>No address given.</Empty>;
  if (acct.error) return <ErrorBox error={acct.error} onRetry={acct.reload} />;

  const d = acct.data;
  const fns: Fn[] = iface.data?.functions || [];
  const views = fns.filter(f => f.mutability === 'view');
  const writes = fns.filter(f => f.mutability !== 'view');
  const isAccount = fns.some(f => f.name === '__execute__');
  const label = nameOf(a) || (isAccount ? 'Account' : iface.data ? 'Contract' : d && !d.deployed ? 'Address' : 'Address');

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <div className="crumb">{isAccount ? 'Wallet account' : iface.data ? 'Contract' : 'Address'}</div>
          <h1>{label}
            {d && !d.deployed && <Badge tone="warn">not deployed</Badge>}
            {iface.data?.privacy_invoke && <Badge tone="accent">privacy helper</Badge>}
            {iface.data && <Badge>Cairo {iface.data.cairo === 0 ? '0' : '1'}</Badge>}
          </h1>
          <div className="sub">{a}<Copy text={a} /></div>
        </div>
        {a === POOL && <Link className="btn" href="/privacy">Open privacy pool view</Link>}
      </div>

      {!d ? <Loading what="Reading balances" /> : (
        <>
          <div className="tokens">
            {(['strk', 'eth'] as const).map(k => (
              <div className="token" key={k}><div className="sym">{k.toUpperCase()}</div>
                <div className="amt">{d.balances?.[k]?.error ? '—' : num(d.balances?.[k]?.amount, 6)}</div></div>
            ))}
            <div className="token"><div className="sym">USDC</div><div className="amt">{usdc.data ? num(usdc.data.amount, 2) : usdc.error ? '—' : '…'}</div></div>
          </div>
          <Card title="Details">
            <Rows rows={[
              ['Class hash', d.class_hash ? <span className="mono">{d.class_hash}</span> : <span className="dim">none — nothing deployed here yet</span>],
              ['Nonce', d.nonce !== undefined ? `${d.nonce} ${isAccount ? 'transactions sent' : ''}` : '—'],
              ['Functions', iface.data ? `${views.length} read · ${writes.length} write` : iface.loading ? '…' : '—'],
              ['Events', iface.data ? (iface.data.events || []).length : '—'],
            ]} />
          </Card>
        </>
      )}

      {iface.data && <TokenInfo a={a} views={views} />}

      {iface.error ? (d?.deployed ? <ErrorBox error={`Could not read this contract's ABI: ${iface.error}`} /> : null)
        : !iface.data ? (d?.deployed !== false && <Loading what="Reading the contract's ABI" />) : (
          <Card>
            <div className="tabs">
              <button className={tab === 'read' ? 'on' : ''} onClick={() => setTab('read')}>Read<span className="n">{views.length}</span></button>
              <button className={tab === 'write' ? 'on' : ''} onClick={() => setTab('write')}>Write<span className="n">{writes.length}</span></button>
              <button className={tab === 'events' ? 'on' : ''} onClick={() => setTab('events')}>Events</button>
            </div>
            {tab === 'read' && <FnList a={a} fns={views} readable />}
            {tab === 'write' && (
              <>
                <div className="note" style={{ marginBottom: 12 }}>
                  These functions change state, so they need a signed transaction from a wallet. This app is
                  read-only and never asks for keys — use the calldata encoder in <Link className="hash" href="/developers">API &amp; MCP</Link> to
                  prepare a call for your wallet.
                </div>
                <FnList a={a} fns={writes} />
              </>
            )}
            {tab === 'events' && <Events a={a} names={iface.data.events || []} />}
          </Card>
        )}
      {d && <Raw data={{ account: d, contract: iface.data }} />}
    </div>
  );
}

/** Token contracts: show name / symbol / supply up front. */
function TokenInfo({ a, views }: { a: string; views: Fn[] }) {
  const { net } = useNet();
  const has = (n: string) => views.find(f => f.name === n && f.inputs.length === 0);
  const supplyFn = has('total_supply') || has('totalSupply');
  const isToken = has('symbol') && has('decimals') && supplyFn;
  const [info, setInfo] = useState<any>(null);
  useEffect(() => {
    if (!isToken) return;
    let live = true;
    const r = (fn: string) => post('read', { contract: a, function: fn, args: {} }, net).then(x => x.result).catch(() => null);
    Promise.all([r('name'), r('symbol'), r('decimals'), r(supplyFn!.name)]).then(([name, symbol, decimals, supply]) => {
      if (live) setInfo({ name, symbol, decimals, supply });
    });
    return () => { live = false; };
  }, [a, net, isToken]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!isToken) return null;
  const dec = Number(info?.decimals ?? TOKENS[a]?.decimals ?? 18);
  return (
    <div className="stats">
      <Stat label="Token" value={info ? String(info.symbol ?? '—') : '…'} sub={info ? String(info.name ?? '') : ''} />
      <Stat label="Total supply" value={info?.supply !== undefined && info?.supply !== null ? num(units(info.supply, dec), 2) : '…'} />
      <Stat label="Decimals" value={info ? dec : '…'} />
      <Stat label="Standard" value="ERC-20" sub="has symbol, decimals, supply" />
    </div>
  );
}

function FnList({ a, fns, readable = false }: { a: string; fns: Fn[]; readable?: boolean }) {
  const [q, setQ] = useState('');
  const list = useMemo(() => fns.filter(f => f.name.toLowerCase().includes(q.toLowerCase())), [fns, q]);
  if (!fns.length) return <Empty>None.</Empty>;
  return (
    <>
      {fns.length > 8 && <input className="input filter" placeholder={`Filter ${fns.length} functions`} value={q} onChange={e => setQ(e.target.value)} />}
      {list.map(f => <FnRow key={f.name} a={a} f={f} readable={readable} />)}
    </>
  );
}

function FnRow({ a, f, readable }: { a: string; f: Fn; readable: boolean }) {
  const { net } = useNet();
  const [open, setOpen] = useState(false);
  const [vals, setVals] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState<{ ok: boolean; v: any } | null>(null);

  async function run(mode: 'read' | 'encode') {
    const args: Record<string, any> = {};
    for (const i of f.inputs) {
      const s = (vals[i.name] || '').trim();
      // structs, arrays and enums go in as JSON; plain values as strings
      if (/^[[{"]/.test(s)) { try { args[i.name] = JSON.parse(s); continue; } catch { /* keep as string */ } }
      args[i.name] = s;
    }
    setBusy(true); setOut(null);
    try {
      const r = await post(mode, { contract: a, function: f.name, args }, net);
      setOut({ ok: true, v: mode === 'read' ? r.result : r });
    } catch (e: any) {
      setOut({ ok: false, v: e.message });
    } finally { setBusy(false); }
  }

  const onOpen = () => {
    const next = !open;
    setOpen(next);
    if (next && readable && f.inputs.length === 0 && !out) run('read');
  };

  return (
    <div className="fn">
      <div className="fn-head" onClick={onOpen}>
        <span className="fn-name">{f.name}</span>
        <span className="fn-sig">{f.inputs.map(i => i.name).join(', ')}{f.outputs.length ? ` → ${f.outputs.join(', ')}` : ''}</span>
      </div>
      {open && (
        <div className="fn-body">
          {f.inputs.map(i => (
            <div key={i.name}>
              <label>{i.name}<span className="t">{i.type}</span></label>
              <input className="input" value={vals[i.name] || ''} spellCheck={false}
                placeholder={hint(i.type)}
                onChange={e => setVals({ ...vals, [i.name]: e.target.value })}
                onKeyDown={e => { if (e.key === 'Enter') run(readable ? 'read' : 'encode'); }} />
            </div>
          ))}
          <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
            {readable
              ? <button className="btn sm" disabled={busy} onClick={() => run('read')}>{busy ? 'Reading…' : 'Read'}</button>
              : <button className="btn sm" disabled={busy} onClick={() => run('encode')}>{busy ? 'Encoding…' : 'Encode calldata'}</button>}
          </div>
          {out && (
            <div className="fn-out">
              {out.ok ? <Result v={out.v} /> : <div className="error"><span>{out.v}</span></div>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Result({ v }: { v: any }) {
  if (v !== null && typeof v !== 'object') return <pre>{String(v)}</pre>;
  return <pre>{JSON.stringify(v, null, 2)}</pre>;
}

function hint(t: string) {
  if (t === 'ContractAddress' || t === 'ClassHash') return '0x…';
  if (/^u(8|16|32|64|128|256)$/.test(t) || t === 'felt252') return 'number or 0x hex';
  if (t === 'bool') return 'true / false';
  if (t === 'ByteArray') return 'text';
  if (/^(Array|Span)</.test(t)) return 'JSON list, e.g. ["0x1", "0x2"]';
  return 'JSON for structs / enums, e.g. {"field": 1}';
}

function Events({ a, names }: { a: string; names: string[] }) {
  const [name, setName] = useState<string>('');
  const ev = useApi<any>(`events?address=${a}&limit=25${name ? `&name=${encodeURIComponent(name)}` : ''}`);
  const tok = TOKENS[norm(a)];
  return (
    <>
      {names.length > 0 && (
        <div className="quick" style={{ justifyContent: 'flex-start', margin: '0 0 12px' }}>
          <button className={`chip ${!name ? 'on' : ''}`} onClick={() => setName('')}>All</button>
          {names.slice(0, 14).map(n => <button key={n} className={`chip ${name === n ? 'on' : ''}`} onClick={() => setName(n)}>{n}</button>)}
        </div>
      )}
      {ev.error ? <ErrorBox error={ev.error} onRetry={ev.reload} /> : !ev.data ? <Loading what="Scanning recent blocks for events" />
        : !ev.data.events.length ? <Empty>No events in the last {((ev.data.to_block - ev.data.from_block) || 0).toLocaleString()} blocks.</Empty> : (
          <div className="scroll">
            <table className="table">
              <thead><tr><th>Event</th><th>Fields</th><th>Block</th><th className="r">Tx</th></tr></thead>
              <tbody>
                {ev.data.events.map((e: any, i: number) => (
                  <tr key={i}>
                    <td><Badge>{e.event}</Badge></td>
                    <td><Fields f={e.fields} tok={tok} /></td>
                    <td><BlockLink n={e.block_number} /></td>
                    <td className="r"><TxLink h={e.tx} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
    </>
  );
}

function Fields({ f, tok }: { f: Record<string, any>; tok?: { symbol: string; decimals: number } }) {
  if (!f) return null;
  return (
    <div className="fields">
      {Object.entries(f).slice(0, 6).map(([k, v]) => (
        <span key={k}><b>{k}</b>{fieldVal(k, v, tok)}</span>
      ))}
    </div>
  );
}

function fieldVal(k: string, v: any, tok?: { symbol: string; decimals: number }): React.ReactNode {
  if (typeof v === 'string' && /^0x[0-9a-f]{40,}$/i.test(v)) return <Addr a={v} />;
  if (tok && typeof v === 'number' && /value|amount/i.test(k)) return <span className="mono">{num(units(v, tok.decimals), 4)} {tok.symbol}</span>;
  if (typeof v === 'object') return <span className="mono dim">{short(JSON.stringify(v), 16)}</span>;
  return <span className="mono">{String(v)}</span>;
}
