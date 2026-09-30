'use client';
import Link from 'next/link';
import { useEffect, useState } from 'react';
import { BASE, call } from '@/lib/api';
import { useData } from '@/lib/data';
import { compact } from '@/lib/format';
import { CopyBlock, Section } from '@/components/ui';

export default function Open() {
  const { info } = useData();
  const [origin, setOrigin] = useState('');
  const [snaps, setSnaps] = useState<number | null>(null);
  useEffect(() => {
    setOrigin(window.location.origin);
    call('bt_history', { netuid: 0, hours: 24 * 30, points: 5000 })
      .then(j => setSnaps((j.result.series || []).length)).catch(() => {});
  }, []);
  const base = origin + BASE;
  return (
    <Section id="open" title="Open. As in actually open."
      lead="Taostats and tao.app sit on closed indexers you can't run, read, or fork. This explorer's entire stack — the indexer, the API, the MCP server, this page — is one open-source module you can run on a laptop.">
      <div className="open-grid">
        <div className="card">
          <h3>The code</h3>
          <p>Everything lives in <code className="inline">orbit/bt</code> of the mod repo — a Python indexer on SQLite, one FastAPI server, and this Next.js console compiled to static files the same server hands out. No node process at runtime.</p>
          <p style={{ marginTop: 10 }}><a href="https://github.com/modc2/mod/tree/main/mod/orbit/bt">github.com/modc2/mod → orbit/bt</a></p>
        </div>
        <div className="card">
          <h3>The data</h3>
          <p>Every number on this page comes from a public endpoint — same table, no key, no rate-limit tiers:</p>
          <CopyBlock text={`curl -s ${base}/api/call -H 'content-type: application/json' -d '{"tool":"bt_screener","args":{"limit":5}}'`} />
        </div>
        <div className="card">
          <h3>The indexer</h3>
          <p>A background thread snapshots every subnet pool on an interval into local SQLite — that&apos;s the whole trick. Prices, changes, volume and charts are served from your own disk in milliseconds.</p>
          <p style={{ marginTop: 10, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            <span className="tag">{snaps == null ? 'indexer: —' : snaps ? `root: ${snaps} snapshots / 30d` : 'indexer: warming up'}</span>
            {info?.traders?.snapshots != null && <span className="tag">traders: {compact(info.traders.tracked)} · {compact(info.traders.snapshots)} snaps</span>}
            {info?.block && <span className="tag">block #{info.block.toLocaleString()}</span>}
          </p>
        </div>
        <div className="card">
          <h3>The agent surface</h3>
          <p>The same registry drives an MCP server, so Claude — or any agent — gets the whole explorer <em>and</em> trading as tools. Closed explorers stop at a dashboard; this one is programmable.</p>
          <p style={{ marginTop: 10 }}><Link href="/mcp">Connect via MCP →</Link></p>
        </div>
      </div>
    </Section>
  );
}
