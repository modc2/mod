"use client";

import { useEffect, useMemo, useState } from "react";
import { apiUrl, fetchMcpSchema, McpSchema, McpTool } from "../lib/api";
import { useWallet } from "../lib/wallet";

// How to point each kind of client at this server. The URLs are read off the
// browser's own origin, so the page shows the address the visitor can actually
// reach — localhost during development, the gateway in production.
type ClientId = "claude-code" | "json" | "sse" | "stdio" | "curl";

const CLIENTS: { id: ClientId; label: string; blurb: string }[] = [
  { id: "claude-code", label: "Claude Code", blurb: "one command, then /mcp to see the tools" },
  { id: "json", label: "config file", blurb: "Claude Desktop, editors — anything that reads mcpServers JSON" },
  { id: "sse", label: "SSE clients", blurb: "the older HTTP+SSE transport, still what many agent frameworks ship" },
  { id: "stdio", label: "stdio", blurb: "run the binary as a child process — no network hop" },
  { id: "curl", label: "raw JSON-RPC", blurb: "what any of the above is doing underneath" },
];

function Copy({ text, label = "copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      className="btn shrink-0"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setDone(true);
          setTimeout(() => setDone(false), 1400);
        } catch { /* clipboard blocked (insecure origin) — the text is selectable anyway */ }
      }}
    >
      {done ? "copied" : label}
    </button>
  );
}

function Snippet({ title, code, note }: { title: string; code: string; note?: string }) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <span className="stat">{title}</span>
        <div className="ml-auto"><Copy text={code} /></div>
      </div>
      <pre className="panel p-3 overflow-x-auto text-[11.5px] leading-relaxed font-mono text-ink whitespace-pre">
        {code}
      </pre>
      {note && <p className="text-[11px] text-dim">{note}</p>}
    </div>
  );
}

function ToolRow({ t }: { t: McpTool }) {
  const [open, setOpen] = useState(false);
  const props = Object.entries(t.inputSchema?.properties ?? {});
  const required = new Set(t.inputSchema?.required ?? []);
  return (
    <div className="border-b border-white/[0.05] last:border-0">
      <button className="w-full text-left px-3 py-2.5 hover:bg-white/[0.02] transition-colors"
        onClick={() => setOpen(!open)}>
        <div className="flex items-baseline gap-2 flex-wrap">
          <span className="font-mono text-[12.5px] text-accent">{t.name}</span>
          <span className={`pill ${t.public ? "" : "border-warn/30 text-warn"}`}>
            {t.public ? "public" : "token"}
          </span>
          <span className="ml-auto font-mono text-[10.5px] text-dim">
            {t.method} {t.path}
          </span>
        </div>
        <p className="text-[11.5px] text-muted mt-1 pr-6">{t.description}</p>
      </button>
      {open && (
        <div className="px-3 pb-3 space-y-1">
          <div className="text-[10px] uppercase tracking-wider text-dim">
            arguments · mod fn <span className="font-mono text-muted normal-case tracking-normal">{t.fn}</span>
          </div>
          {props.length === 0 && <div className="text-[11.5px] text-dim">none</div>}
          {props.map(([k, v]: [string, any]) => (
            <div key={k} className="text-[11.5px] flex gap-2">
              <span className="font-mono text-ink shrink-0">{k}</span>
              <span className="text-dim shrink-0">{v.type}{required.has(k) ? " · required" : ""}</span>
              <span className="text-muted">{v.description ?? ""}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function McpPage() {
  const { token } = useWallet();
  const [schema, setSchema] = useState<McpSchema | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [client, setClient] = useState<ClientId>("claude-code");
  const [withToken, setWithToken] = useState(false);
  const [q, setQ] = useState("");
  const [scope, setScope] = useState<"all" | "public" | "token">("all");
  const [urls, setUrls] = useState({ mcp: "/mcp", sse: "/sse" });

  useEffect(() => {
    fetchMcpSchema().then(setSchema).catch((e) => setErr(e.message ?? String(e)));
    setUrls({ mcp: apiUrl("/mcp"), sse: apiUrl("/sse") });
  }, []);
  useEffect(() => { if (!token) setWithToken(false); }, [token]);

  // Signed-in visitors can paste a config that already carries their token;
  // otherwise the snippets keep the placeholder so nothing leaks into a copy.
  const bearer = withToken && token ? token : "<mod protocol token>";
  const authHeader = `Authorization: Bearer ${bearer}`;

  const snippets: Record<ClientId, { title: string; code: string; note?: string }[]> = {
    "claude-code": [{
      title: "add the server",
      code: `claude mcp add --transport http hyperliquid ${urls.mcp}` +
        (withToken || token ? ` \\\n  --header "${authHeader}"` : ""),
      note: "then `claude mcp list` to check the connection, or /mcp inside a session.",
    }],
    json: [{
      title: "mcpServers entry",
      code: JSON.stringify({
        mcpServers: {
          hyperliquid: {
            type: "http",
            url: urls.mcp,
            headers: { Authorization: `Bearer ${bearer}` },
          },
        },
      }, null, 2),
      note: "drop the headers block to connect signed-out — public tools still work.",
    }],
    sse: [{
      title: "HTTP+SSE transport",
      code: `claude mcp add --transport sse hyperliquid ${urls.sse}` +
        (withToken || token ? ` \\\n  --header "${authHeader}"` : ""),
      note: "GET /sse opens the stream and names the /messages endpoint to POST to. " +
        "Use this for clients that predate streamable HTTP.",
    }],
    stdio: [{
      title: "child process",
      code: JSON.stringify({
        mcpServers: {
          hyperliquid: {
            command: "hyperliquid-api",
            args: ["--stdio"],
            env: { HL_API_URL: apiUrl(""), HYPERLIQUID_TOKEN: bearer },
          },
        },
      }, null, 2),
      note: "the binary proxies to the API at HL_API_URL — it holds no keys of its own.",
    }],
    curl: [
      {
        title: "handshake",
        code: `curl -s ${urls.mcp} \\\n  -H 'Content-Type: application/json' \\\n` +
          `  -d '{"jsonrpc":"2.0","id":1,"method":"initialize",` +
          `"params":{"protocolVersion":"2025-06-18","capabilities":{},` +
          `"clientInfo":{"name":"you","version":"1"}}}'`,
      },
      {
        title: "call a tool",
        code: `curl -s ${urls.mcp} \\\n  -H 'Content-Type: application/json' \\\n` +
          (withToken || token ? `  -H '${authHeader}' \\\n` : "") +
          `  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call",` +
          `"params":{"name":"hl_top_traders","arguments":{"days":7,"pool":5}}}'`,
      },
    ],
  };

  const tools = schema?.tools ?? [];
  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return tools.filter((t) => {
      if (scope === "public" && !t.public) return false;
      if (scope === "token" && t.public) return false;
      if (!needle) return true;
      return (t.name + t.description + t.fn + t.path).toLowerCase().includes(needle);
    });
  }, [tools, q, scope]);

  const publicCount = tools.filter((t) => t.public).length;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-display text-2xl font-bold tracking-tight text-gradient">MCP server</h1>
        <p className="text-sm text-muted mt-1 max-w-2xl">
          Every function this module exposes is also an MCP tool, so an agent can read the
          markets, analyse traders and — with your token — trade, without a bespoke integration.
          Connect over streamable HTTP, HTTP+SSE, or stdio.
        </p>
      </div>

      {err && <div className="panel p-3 text-sm text-loss">could not load the tool schema — {err}</div>}

      {/* endpoints + protocol facts, read from the server itself */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {[
          { k: "tools", v: schema ? String(tools.length) : "—", sub: schema ? `${publicCount} public · ${tools.length - publicCount} need a token` : "" },
          { k: "protocol", v: schema?.mcp.protocolVersion ?? "—", sub: schema?.mcp.supportedVersions?.join(" · ") ?? "" },
          { k: "transports", v: schema ? String(schema.mcp.transports?.length ?? 1) : "—", sub: schema?.mcp.transports?.map((t) => t.type).join(" · ") ?? "" },
          { k: "network", v: schema ? (schema.testnet ? "testnet" : "mainnet") : "—", sub: schema ? `${schema.name} v${schema.version}` : "" },
        ].map((s) => (
          <div key={s.k} className="panel p-4">
            <div className="stat">{s.k}</div>
            <div className="text-xl font-display font-bold text-ink mt-1">{s.v}</div>
            <div className="text-[10.5px] text-dim mt-1 truncate" title={s.sub}>{s.sub}</div>
          </div>
        ))}
      </div>

      <div className="panel p-4 space-y-4">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="stat">connect</span>
          <div className="flex gap-1 flex-wrap">
            {CLIENTS.map((c) => (
              <button key={c.id} onClick={() => setClient(c.id)}
                className={`text-[11px] font-medium uppercase tracking-wider px-2.5 py-1 rounded-md transition-all
                  ${client === c.id
                    ? "text-accent bg-accent/10 shadow-[inset_0_0_0_1px_rgb(var(--c-accent)/0.3)]"
                    : "text-muted hover:text-ink hover:bg-white/[0.04]"}`}>
                {c.label}
              </button>
            ))}
          </div>
          {token && (
            <label className="ml-auto flex items-center gap-1.5 text-[11px] text-muted cursor-pointer">
              <input type="checkbox" checked={withToken} onChange={(e) => setWithToken(e.target.checked)} />
              include my token
            </label>
          )}
        </div>
        <p className="text-[11.5px] text-dim">{CLIENTS.find((c) => c.id === client)?.blurb}</p>
        <div className="space-y-4">
          {snippets[client].map((s) => <Snippet key={s.title} {...s} />)}
        </div>
      </div>

      {/* what the token does — the same gate the browser hits */}
      <div className="panel p-4 space-y-2">
        <div className="stat">authorization</div>
        <p className="text-[12.5px] text-muted leading-relaxed max-w-3xl">
          Market data, leaderboards, trader analysis, vaults and strat reads are public — an agent
          connects signed-out and gets {publicCount || "the read"} tools. Anything wallet-scoped
          needs the same <span className="font-mono text-ink">Authorization: Bearer</span> token the
          browser uses, and any <span className="font-mono text-ink">eoa</span> argument must be that
          token&apos;s own address. Tool calls re-enter this API through its normal routes, so MCP
          grants nothing the signed-in wallet does not already have.
        </p>
        {token ? (
          <div className="flex items-center gap-2 flex-wrap">
            <span className="pill border-accent/30 text-accent">signed in</span>
            <span className="text-[11px] text-dim font-mono truncate max-w-[42ch]">{token.slice(0, 28)}…</span>
            <Copy text={token} label="copy token" />
          </div>
        ) : (
          <p className="text-[11.5px] text-warn">
            Not signed in — connect your wallet in the header to mint a token for the trading tools.
          </p>
        )}
      </div>

      {/* the tool surface itself, straight from /mcp/schema */}
      <div className="panel overflow-hidden">
        <div className="flex items-center gap-2 p-3 border-b border-white/[0.06] flex-wrap">
          <span className="stat">tools</span>
          <div className="flex gap-1">
            {(["all", "public", "token"] as const).map((s) => (
              <button key={s} onClick={() => setScope(s)}
                className={`text-[10px] font-medium uppercase tracking-wider px-2 py-1 rounded-md transition-all
                  ${scope === s ? "text-accent bg-accent/10" : "text-muted hover:text-ink hover:bg-white/[0.04]"}`}>
                {s}
              </button>
            ))}
          </div>
          <input className="input ml-auto w-[24ch] text-xs" placeholder="filter tools…"
            value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        {!schema && !err && <div className="p-4 text-sm text-dim">loading the tool schema…</div>}
        {shown.map((t) => <ToolRow key={t.name} t={t} />)}
        {schema && shown.length === 0 && (
          <div className="p-4 text-sm text-dim">no tool matches “{q}”</div>
        )}
      </div>
    </div>
  );
}
