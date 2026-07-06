"use client";

import { useState } from "react";
import { ScanForm } from "./components/ScanForm";
import { ScanList } from "./components/ScanList";
import { ScanReport } from "./components/ScanReport";

export default function Home() {
  const [selected, setSelected] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  return (
    <main className="min-h-screen bg-grid">
      <div className="max-w-6xl mx-auto px-5 py-8">
        <header className="mb-6 flex items-baseline justify-between flex-wrap gap-2">
          <div>
            <h1 className="text-2xl font-mono">
              <span className="text-accent">/</span>securescan
            </h1>
            <p className="text-sm text-muted mt-1">
              agent-powered security scanner for any GitHub repository
            </p>
          </div>
          <a
            href="https://github.com"
            target="_blank"
            rel="noreferrer"
            className="text-xs text-muted hover:text-accent"
          >
            scans run in the background · poll for results
          </a>
        </header>

        <div className="grid lg:grid-cols-[1fr_2fr] gap-6">
          <aside className="space-y-4">
            <ScanForm
              onStarted={(id) => {
                setSelected(id);
                setRefreshKey((k) => k + 1);
              }}
            />
            <div className="bg-panel border border-border rounded-lg overflow-hidden">
              <div className="px-3 py-2 border-b border-border text-xs uppercase tracking-wider text-muted">
                recent scans
              </div>
              <ScanList
                selectedId={selected}
                onSelect={setSelected}
                refreshKey={refreshKey}
              />
            </div>
          </aside>

          <section>
            {selected ? (
              <ScanReport id={selected} />
            ) : (
              <div className="bg-panel border border-border rounded-lg p-12 text-center">
                <div className="text-2xl font-mono text-muted mb-2">
                  no scan selected
                </div>
                <p className="text-sm text-muted">
                  Enter a GitHub repo on the left to start a vulnerability scan,
                  or pick an existing one from the list.
                </p>
              </div>
            )}
          </section>
        </div>

        <footer className="mt-12 text-xs text-muted text-center">
          findings are written to{" "}
          <code className="text-accent">~/.securescan/scans/&lt;id&gt;/</code>
        </footer>
      </div>
    </main>
  );
}
