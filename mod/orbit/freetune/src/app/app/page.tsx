"use client";
import { useState } from "react";
import TrainPanel from "./components/TrainPanel";
import RunsPanel from "./components/RunsPanel";
import ChatPanel from "./components/ChatPanel";
import EfficiencyPanel from "./components/EfficiencyPanel";
import WalletChip from "./components/WalletChip";
import { AuthProvider } from "./context/AuthContext";

const TABS = ["train", "runs", "chat", "efficiency"] as const;
type Tab = (typeof TABS)[number];

function PageInner() {
  const [tab, setTab] = useState<Tab>("train");
  const [focusRun, setFocusRun] = useState<string | undefined>();

  return (
    <main className="min-h-screen">
      <header className="border-b border-border px-5 py-3 flex items-center justify-between sticky top-0 bg-bg/90 backdrop-blur z-10">
        <div className="flex items-baseline gap-3">
          <h1 className="text-[18px] font-bold tracking-tight">
            free<span className="text-accent">tune</span>
          </h1>
          <span className="text-[11px] text-white/40 hidden sm:block">
            CPU LoRA finetuning of Qwen over a directory of code
          </span>
        </div>
        <div className="flex items-center gap-4">
          <nav className="flex gap-1">
            {TABS.map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className="px-3 py-1.5 rounded-lg text-[12px] uppercase tracking-wide"
                style={{
                  background: tab === t ? "#1b2027" : "transparent",
                  color: tab === t ? "#6ee7b7" : "#7c8493",
                  border: tab === t ? "1px solid #2a2f3a" : "1px solid transparent",
                }}
              >
                {t}
              </button>
            ))}
          </nav>
          <WalletChip />
        </div>
      </header>

      <div className="max-w-6xl mx-auto p-5">
        {tab === "train" && (
          <TrainPanel
            onStarted={(id) => {
              setFocusRun(id);
              setTab("runs");
            }}
          />
        )}
        {tab === "runs" && <RunsPanel focusId={focusRun} />}
        {tab === "chat" && <ChatPanel />}
        {tab === "efficiency" && <EfficiencyPanel />}
      </div>
    </main>
  );
}

export default function Page() {
  return (
    <AuthProvider>
      <PageInner />
    </AuthProvider>
  );
}
