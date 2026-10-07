"use client";

import AskConsole from "../components/AskConsole";

// The general chatbot: the same Claude agent the Ask desk runs, in its open
// mode — any question is fair game, answers may come from the model's own
// knowledge, the Hyperliquid read tools stay on hand for live facts, and the
// conversation keeps context turn to turn. Model auth rides the host's
// claude mod credential keeper, so it stays signed in as long as the claude
// console is.
export default function ChatPage() {
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-gradient text-[24px] font-bold tracking-tight leading-tight">Chat</h1>
        <p className="text-xs text-muted mt-1 max-w-2xl">
          Ask anything — markets, perps 101, or nothing to do with trading at
          all. This is the claude mod&apos;s agent in conversation mode: it
          remembers the thread, can pull live Hyperliquid data when the
          question needs it, and its toolbox is strictly read-only — it can
          never place an order or move funds from here.
        </p>
      </div>

      <AskConsole mode="chat" />

      <p className="text-[11px] text-dim">
        Need the agent to act on your wallet? That lives on the Ask page&apos;s
        action mode.
      </p>
    </div>
  );
}
