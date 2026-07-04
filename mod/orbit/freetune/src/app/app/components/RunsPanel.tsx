"use client";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Card } from "./ui";
import { useAuth } from "../context/AuthContext";

const STATUS_COLOR: Record<string, string> = {
  done: "#6ee7b7",
  training: "#38bdf8",
  error: "#f87171",
  cancelled: "#9ca3af",
  queued: "#fcd34d",
};

function Bar({ p }: { p: number }) {
  return (
    <div className="h-1.5 w-full rounded-full bg-white/10 overflow-hidden">
      <div className="h-full rounded-full" style={{ width: `${p}%`, background: "#38bdf8" }} />
    </div>
  );
}

export default function RunsPanel({ focusId }: { focusId?: string }) {
  const { auth } = useAuth();
  // Server is the authority on who's allowed (owner OR a BlocTime holder) —
  // only block the UI when nobody has signed in at all.
  const locked = auth.gated && !auth.connected;
  const [runs, setRuns] = useState<any[]>([]);
  const [open, setOpen] = useState<string | null>(focusId || null);
  const [logs, setLogs] = useState("");

  async function refresh() {
    try {
      const r = await api.runs();
      setRuns(r.runs);
    } catch {}
  }

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 2500);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (focusId) setOpen(focusId);
  }, [focusId]);

  useEffect(() => {
    if (!open) return;
    let live = true;
    const pull = () => api.runLogs(open!, 300).then((r) => live && setLogs(r.logs)).catch(() => {});
    pull();
    const t = setInterval(pull, 2000);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, [open]);

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Runs" right={<button className="btn" onClick={refresh}>refresh</button>}>
        {runs.length === 0 && <div className="text-[12px] text-white/40">No runs yet.</div>}
        <div className="space-y-2">
          {runs.map((r) => {
            const p = r.progress || {};
            const pct = p.total_steps ? Math.round((100 * (p.step || 0)) / p.total_steps) : 0;
            const c = STATUS_COLOR[r.status] || "#9ca3af";
            return (
              <div
                key={r.id}
                onClick={() => setOpen(r.id)}
                className="card p-3 cursor-pointer"
                style={{ borderColor: open === r.id ? "#38bdf8" : undefined }}
              >
                <div className="flex items-center justify-between">
                  <div className="font-bold text-[13px]">{r.name}</div>
                  <span className="pill" style={{ color: c, borderColor: c + "55" }}>
                    {r.running ? "● " : ""}
                    {r.status}
                  </span>
                </div>
                <div className="text-[11px] text-white/40 mt-0.5">{r.model}</div>
                {p.total_steps > 0 && (
                  <div className="mt-2">
                    <Bar p={pct} />
                    <div className="flex justify-between text-[10px] text-white/40 mt-1 mono-num">
                      <span>
                        step {p.step || 0}/{p.total_steps}
                      </span>
                      {p.loss != null && <span>loss {p.loss}</span>}
                      {p.eta_seconds != null && r.running && (
                        <span>eta {Math.round(p.eta_seconds)}s</span>
                      )}
                    </div>
                  </div>
                )}
                {p.final_loss != null && (
                  <div className="text-[11px] text-emerald-300 mt-1 mono-num">
                    final loss {p.final_loss}
                  </div>
                )}
                {r.has_adapter && <span className="pill text-emerald-300 mt-2 inline-block">adapter ✓</span>}
                <div className="flex gap-2 mt-2">
                  {r.running && (
                    <button
                      className="btn-danger"
                      disabled={locked}
                      title={locked ? "connect an authorized wallet (owner or BlocTime holder) to stop a run" : undefined}
                      onClick={(e) => {
                        e.stopPropagation();
                        api.stop(r.id).then(refresh);
                      }}
                    >
                      stop
                    </button>
                  )}
                  <button
                    className="btn"
                    disabled={locked}
                    title={locked ? "connect an authorized wallet (owner or BlocTime holder) to delete a run" : undefined}
                    onClick={(e) => {
                      e.stopPropagation();
                      api.del(r.id).then(refresh);
                    }}
                  >
                    delete
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </Card>

      <Card title={open ? `Logs · ${open}` : "Logs"}>
        {!open && <div className="text-[12px] text-white/40">Select a run.</div>}
        {open && (
          <pre className="text-[11px] leading-[1.5] text-white/70 whitespace-pre-wrap max-h-[60vh] overflow-auto">
            {logs || "…"}
          </pre>
        )}
      </Card>
    </div>
  );
}
