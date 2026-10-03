'use client';
import { useEffect, useState } from 'react';
import { BASE } from '@/lib/api';
import { CopyBlock, Section } from '@/components/ui';

export default function Mcp() {
  const [origin, setOrigin] = useState('');
  useEffect(() => setOrigin(window.location.origin), []);
  const url = `${origin}${BASE}/mcp`;
  return (
    <Section id="mcp" narrow title="MCP." lead="Wire the whole protocol into Claude — or any MCP client — in one line.">
      <div className="grid cols2">
        <div className="card">
          <h3 className="t">stdio <span className="tag">recommended</span></h3>
          <p className="muted">Local, no server needed. From Claude Code:</p>
          <CopyBlock text="claude mcp add bittensor -- python3 -m bt.mcp_server" />
          <p className="muted">Or in any <code className="inline">mcpServers</code> config:</p>
          <CopyBlock text={`{
  "bittensor": {
    "command": "python3",
    "args": ["-m", "bt.mcp_server"],
    "cwd": "/root/mod/mod/orbit/bt"
  }
}`} />
        </div>
        <div className="card">
          <h3 className="t">Streamable HTTP</h3>
          <p className="muted">This server also speaks MCP over HTTP — point a remote client at:</p>
          <CopyBlock text={url} />
          <p className="muted">Same tools, same schemas. Try it:</p>
          <CopyBlock text={`curl -s ${url} -H 'content-type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'`} />
          <p className="muted">Agent card (agent/1.0): <code className="inline">{origin}{BASE}/.well-known/agent.json</code></p>
        </div>
      </div>
    </Section>
  );
}
