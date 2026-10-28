"use client";

import AskConsole from "../components/AskConsole";

// The same agent the desk dock runs — this page is just the roomy view of it.
export default function AskPage() {
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-gradient text-[24px] font-bold tracking-tight leading-tight">Ask</h1>
        <p className="text-xs text-muted mt-1 max-w-2xl">
          An agent whose only toolbox is this module&apos;s own MCP server. It
          answers from live tool calls — never from memory — and every call
          goes back through the same API gate your wallet does, so it can
          never see or do more than you can.
        </p>
      </div>

      <AskConsole />

      <p className="text-[11px] text-dim">
        Same tools over MCP: <span className="font-mono">claude mcp add hyperliquid -- hyperliquid-api --stdio</span>
      </p>
    </div>
  );
}
