"use client";

import { useState, useEffect, useCallback } from "react";
import { api, shortenKey } from "@/lib/api";

interface Neuron {
  uid: number;
  hotkey: string;
  role: string;
  stake: number;
  url: string | null;
  incentive?: number;
}

interface SnStatus {
  network: string;
  netuid: number;
  near_network: string;
  task: string;
  epoch_seconds: number;
  metagraph?: { n: number; block: number; epochs?: number; incentive?: Record<string, number>; error?: string };
  validator?: { hotkey: string; scores: Record<string, number>; last_epoch_at?: number };
}

export default function BittensorPanel() {
  const [status, setStatus] = useState<SnStatus | null>(null);
  const [neurons, setNeurons] = useState<Neuron[]>([]);
  const [running, setRunning] = useState(false);
  const [epochNote, setEpochNote] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const [st, mg] = await Promise.all([
      api("neartensor/sn_status"),
      api("neartensor/sn_metagraph"),
    ]);
    if (st && !st.error) setStatus(st);
    if (mg && !mg.error) setNeurons(Array.isArray(mg.neurons) ? mg.neurons : []);
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const runEpoch = async () => {
    setRunning(true);
    setEpochNote(null);
    const rep = await api("neartensor/sn_epoch");
    if (rep && !rep.error) {
      const w = rep.weights || {};
      setEpochNote(
        `Epoch complete: ${rep.miners} miner(s) queried, weights ${
          Object.keys(w).length ? JSON.stringify(w) : "unchanged (no scoring miners)"
        }`
      );
    } else {
      setEpochNote(`Epoch failed: ${rep?.error || "unknown error"}`);
    }
    setRunning(false);
    refresh();
  };

  const incentive = status?.metagraph?.incentive || {};
  const scores = status?.validator?.scores || {};

  const stats = [
    { label: "Chain", value: status ? (status.network === "local" ? "local (file-backed)" : `subtensor ${status.network}`) : "…" },
    { label: "Netuid", value: status ? String(status.netuid) : "…" },
    { label: "Dedicated Task", value: status?.task || "NEAR chain attestation" },
    { label: "Attests To", value: status ? `NEAR ${status.near_network}` : "…" },
    { label: "Neurons", value: String(status?.metagraph?.n ?? neurons.length) },
    { label: "Epochs", value: String(status?.metagraph?.epochs ?? "—") },
  ];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-nt-text">Bittensor Subnet</h2>
          <p className="text-[11px] text-nt-muted mt-1">
            Miners attest to NEAR chain state; validators re-verify every answer at the
            anchored block hash and set weights. Runs local-first, or on subtensor with a wallet.
          </p>
        </div>
        <button
          onClick={runEpoch}
          disabled={running}
          className="px-3 py-1.5 text-xs rounded border border-nt-accent/30 bg-nt-accent/10 text-nt-accent hover:bg-nt-accent/20 disabled:opacity-50"
        >
          {running ? "Running epoch…" : "Run validation epoch"}
        </button>
      </div>

      {epochNote && (
        <div className="p-3 rounded border border-nt-border bg-nt-panel text-xs text-nt-text">
          {epochNote}
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
        {stats.map((s) => (
          <div key={s.label} className="p-3 rounded border border-nt-border bg-nt-panel">
            <div className="text-[10px] text-nt-muted mb-1">{s.label}</div>
            <div className="text-sm font-medium truncate">{s.value}</div>
          </div>
        ))}
      </div>

      <div>
        <h3 className="text-xs font-semibold text-nt-text mb-2">Metagraph</h3>
        {neurons.length === 0 ? (
          <div className="text-center py-6 text-nt-muted text-xs">
            No neurons registered yet — start a miner with <code>m neartensor sn_serve</code>
          </div>
        ) : (
          <div className="overflow-x-auto rounded border border-nt-border">
            <table className="w-full text-xs">
              <thead className="bg-nt-panel text-nt-muted">
                <tr>
                  <th className="text-left px-3 py-2 font-medium">UID</th>
                  <th className="text-left px-3 py-2 font-medium">Hotkey</th>
                  <th className="text-left px-3 py-2 font-medium">Role</th>
                  <th className="text-left px-3 py-2 font-medium">Endpoint</th>
                  <th className="text-right px-3 py-2 font-medium">Score</th>
                  <th className="text-right px-3 py-2 font-medium">Incentive</th>
                </tr>
              </thead>
              <tbody>
                {neurons.map((n) => (
                  <tr key={n.uid} className="border-t border-nt-border">
                    <td className="px-3 py-2">{n.uid}</td>
                    <td className="px-3 py-2 font-mono">{shortenKey(n.hotkey, 6)}</td>
                    <td className="px-3 py-2">
                      <span className={n.role === "validator" ? "text-nt-accent" : ""}>{n.role}</span>
                    </td>
                    <td className="px-3 py-2 text-nt-muted">{n.url || "not serving"}</td>
                    <td className="px-3 py-2 text-right">
                      {scores[String(n.uid)] !== undefined ? scores[String(n.uid)].toFixed(3) : "—"}
                    </td>
                    <td className="px-3 py-2 text-right">
                      {incentive[String(n.uid)] !== undefined ? incentive[String(n.uid)].toFixed(3) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
