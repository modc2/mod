'use client';
import { useEffect, useState } from 'react';
import { Badge, Card, Copy, ErrorBox, Loading } from '@/components/ui';
import { API, BASE, get } from '@/lib/api';
import { useApi } from '@/lib/hooks';
import { useNet } from '@/lib/net';

const EXAMPLES = ['status', 'blocks?limit=5', 'balance?address=0x040337b1af3c663e86e333bab5a4b28da8d4652a15a69beee2b677776ffe812a&token=strk',
  'contract?address=strk', 'events?address=strk&name=Transfer&limit=5', 'strk20/pool', 'selector?name=balanceOf'];

export default function Developers() {
  const { net } = useNet();
  const info = useApi<any>('');
  const tools = useApi<any>('tools');
  const [origin, setOrigin] = useState('');
  useEffect(() => setOrigin(window.location.origin), []);
  const [path, setPath] = useState('status');
  const [out, setOut] = useState<{ ok: boolean; text: string; ms: number } | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(p = path) {
    setPath(p); setBusy(true);
    const t0 = performance.now();
    try { const r = await get(p, net); setOut({ ok: true, text: JSON.stringify(r, null, 2), ms: performance.now() - t0 }); }
    catch (e: any) { setOut({ ok: false, text: JSON.stringify(e.body || e.message, null, 2), ms: performance.now() - t0 }); }
    finally { setBusy(false); }
  }

  const apiBase = `${origin}${API}`;
  const mcpUrl = `${origin}${BASE}/mcp`;
  const mcpCfg = JSON.stringify({ mcpServers: { starknet: { type: 'http', url: mcpUrl } } }, null, 2);
  const curl = `curl '${apiBase}/balance?address=0x…&token=strk'`;

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <div className="crumb">Build on it</div>
          <h1>API &amp; MCP <Badge tone="ok">read-only</Badge></h1>
          <div className="sub">Everything this app shows comes from the same JSON API — use it from code or from an AI agent.</div>
        </div>
      </div>

      <div className="grid g2">
        <Card title="REST API">
          <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>Plain GET/POST, JSON out. Add <code>network=sepolia</code> to any call.</p>
          <div className="snip"><Copy text={apiBase} /><pre>{apiBase}</pre></div>
          <div className="snip" style={{ marginTop: 8 }}><Copy text={curl} /><pre>{curl}</pre></div>
        </Card>
        <Card title="MCP server" right={tools.data && <Badge tone="accent">{tools.data.count} tools</Badge>}>
          <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>Add this to Claude, Cursor or any MCP client. Locally: <code>python3 api/mcp.py</code> (stdio).</p>
          <div className="snip"><Copy text={mcpCfg} /><pre>{mcpCfg}</pre></div>
        </Card>
      </div>

      <Card title="Try it" right={out && <span className={out.ok ? '' : 'dim'}>{out.ms.toFixed(0)} ms</span>}>
        <form className="search" onSubmit={e => { e.preventDefault(); run(); }}>
          <input value={path} onChange={e => setPath(e.target.value)} spellCheck={false} />
          <button className="btn" disabled={busy}>{busy ? '…' : 'GET'}</button>
        </form>
        <div className="quick" style={{ justifyContent: 'flex-start' }}>
          {EXAMPLES.map(x => <button key={x} className="chip" onClick={() => run(x)}>{x.split('?')[0]}</button>)}
        </div>
        {out && <div className="raw-box" style={{ marginTop: 12 }}><Copy text={out.text} /><pre className={out.ok ? '' : 'err'}>{out.text}</pre></div>}
      </Card>

      <div className="grid g2">
        <Card title="Endpoints">
          {info.error ? <ErrorBox error={info.error} /> : !info.data ? <Loading /> :
            Object.entries(info.data.endpoints || {}).map(([k, v]) => (
              <div className="endpoint" key={k}><span><span className="muted">{k}</span> &nbsp;{String(v)}</span></div>
            ))}
        </Card>
        <Card title="MCP tools">
          {tools.error ? <ErrorBox error={tools.error} /> : !tools.data ? <Loading /> :
            tools.data.tools.map((t: any) => (
              <div className="endpoint" key={t.name} title={t.description}>
                <span>{t.name} <span className="dim" style={{ fontFamily: 'inherit' }}>— {t.description.split('. ')[0].slice(0, 90)}</span></span>
              </div>
            ))}
        </Card>
      </div>
    </div>
  );
}
