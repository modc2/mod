"use client";

import { useState } from "react";
import { startScan } from "../lib/api";

export function ScanForm({ onStarted }: { onStarted: (id: string) => void }) {
  const [repo, setRepo] = useState("");
  const [branch, setBranch] = useState("");
  const [subdir, setSubdir] = useState("");
  const [steps, setSteps] = useState(15);
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!repo.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const r = await startScan({
        repo: repo.trim(),
        branch: branch.trim() || undefined,
        subdir: subdir.trim() || undefined,
        steps,
        provider: provider.trim() || undefined,
        model: model.trim() || undefined,
      });
      onStarted(r.scan_id);
      setRepo("");
      setBranch("");
      setSubdir("");
    } catch (err: any) {
      setError(err?.message || "failed to start scan");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      className="bg-panel border border-border rounded-lg p-5 space-y-3"
    >
      <div>
        <label className="block text-xs uppercase tracking-wider text-muted mb-1">
          GitHub repository
        </label>
        <input
          type="text"
          value={repo}
          onChange={(e) => setRepo(e.target.value)}
          placeholder="owner/name  or  https://github.com/owner/name"
          className="w-full bg-panel2 border border-border rounded px-3 py-2 font-mono text-sm focus:outline-none focus:border-accent"
          spellCheck={false}
          autoFocus
        />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs uppercase tracking-wider text-muted mb-1">
            Branch (optional)
          </label>
          <input
            type="text"
            value={branch}
            onChange={(e) => setBranch(e.target.value)}
            placeholder="main"
            className="w-full bg-panel2 border border-border rounded px-3 py-2 font-mono text-sm focus:outline-none focus:border-accent"
            spellCheck={false}
          />
        </div>
        <div>
          <label className="block text-xs uppercase tracking-wider text-muted mb-1">
            Agent steps
          </label>
          <input
            type="number"
            min={3}
            max={60}
            value={steps}
            onChange={(e) => setSteps(parseInt(e.target.value) || 15)}
            className="w-full bg-panel2 border border-border rounded px-3 py-2 font-mono text-sm focus:outline-none focus:border-accent"
          />
        </div>
      </div>
      <div>
        <label className="block text-xs uppercase tracking-wider text-muted mb-1">
          Subdirectory (optional)
        </label>
        <input
          type="text"
          value={subdir}
          onChange={(e) => setSubdir(e.target.value)}
          placeholder="mod/orbit/bridge"
          className="w-full bg-panel2 border border-border rounded px-3 py-2 font-mono text-sm focus:outline-none focus:border-accent"
          spellCheck={false}
        />
      </div>
      <div>
        <button
          type="button"
          onClick={() => setShowAdvanced((v) => !v)}
          className="text-xs text-muted hover:text-fg transition"
        >
          {showAdvanced ? "▾ advanced" : "▸ advanced"}
        </button>
        {showAdvanced && (
          <div className="grid grid-cols-2 gap-3 mt-2">
            <div>
              <label className="block text-xs uppercase tracking-wider text-muted mb-1">
                Provider
              </label>
              <input
                type="text"
                value={provider}
                onChange={(e) => setProvider(e.target.value)}
                placeholder="openrouter"
                className="w-full bg-panel2 border border-border rounded px-3 py-2 font-mono text-sm focus:outline-none focus:border-accent"
                spellCheck={false}
              />
            </div>
            <div>
              <label className="block text-xs uppercase tracking-wider text-muted mb-1">
                Model
              </label>
              <input
                type="text"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder="default"
                className="w-full bg-panel2 border border-border rounded px-3 py-2 font-mono text-sm focus:outline-none focus:border-accent"
                spellCheck={false}
              />
            </div>
          </div>
        )}
      </div>
      <div className="flex items-center justify-between pt-1">
        <span className="text-xs text-muted">
          {busy ? "starting…" : "scans run in the background"}
        </span>
        <button
          type="submit"
          disabled={busy || !repo.trim()}
          className="px-4 py-2 bg-accent text-bg font-medium rounded hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed transition"
        >
          {busy ? "scanning…" : "scan"}
        </button>
      </div>
      {error && (
        <div className="text-sm text-critical border border-critical/40 rounded px-3 py-2 bg-critical/10">
          {error}
        </div>
      )}
    </form>
  );
}
