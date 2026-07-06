"use client";
import { useEffect, useState } from "react";
import { api, Model } from "../lib/api";
import { Card, Stat, Spark } from "./ui";
import { useAuth } from "../context/AuthContext";

export default function EfficiencyPanel() {
  const { auth } = useAuth();
  // Server is the authority on who's allowed (owner OR a BlocTime holder) —
  // only block the UI when nobody has signed in at all.
  const locked = auth.gated && !auth.connected;
  const [metrics, setMetrics] = useState<any>(null);
  const [models, setModels] = useState<Model[]>([]);
  const [runs, setRuns] = useState<any[]>([]);
  const [target, setTarget] = useState("");
  const [bench, setBench] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.models().then((r) => {
      setModels(r.models);
      setTarget((t) => t || `model:${r.default}`);
    });
    api.runs().then((r) => setRuns(r.runs.filter((x: any) => x.has_adapter)));
    const pull = () => api.metrics().then(setMetrics).catch(() => {});
    pull();
    const t = setInterval(pull, 2000);
    return () => clearInterval(t);
  }, []);

  const latest = metrics?.latest || {};
  const hist = metrics?.history || [];
  const cpuSeries = hist.map((h: any) => h.cpu_percent);
  const memSeries = hist.map((h: any) => h.mem_percent);
  const workers = metrics?.workers?.workers || [];

  async function runBench() {
    setErr("");
    setBusy(true);
    setBench(null);
    try {
      const [kind, id] = target.split(/:(.+)/);
      const req: any = { max_new_tokens: 120 };
      if (kind === "run") req.run_id = id;
      else req.model = id;
      setBench(await api.bench(req));
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat
          label="CPU"
          value={`${latest.cpu_percent ?? 0}%`}
          sub={`${latest.cpu_cores ?? "?"} cores`}
          accent="#38bdf8"
        />
        <Stat
          label="RAM"
          value={`${latest.mem_percent ?? 0}%`}
          sub={`${((latest.mem_used_mb ?? 0) / 1000).toFixed(1)} / ${((latest.mem_total_mb ?? 0) / 1000).toFixed(0)} GB`}
          accent="#6ee7b7"
        />
        <Stat
          label="Warm workers"
          value={workers.length}
          sub={`${metrics?.workers?.total_requests ?? 0} requests served`}
          accent="#fcd34d"
        />
        <Stat
          label="Worker RAM"
          value={`${workers.reduce((a: number, w: any) => a + (w.rss_mb || 0), 0)}MB`}
          sub="resident across workers"
        />
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <Card title="CPU utilisation">
          <Spark data={cpuSeries} color="#38bdf8" max={100} />
        </Card>
        <Card title="Memory utilisation">
          <Spark data={memSeries} color="#6ee7b7" max={100} />
        </Card>
      </div>

      {workers.length > 0 && (
        <Card title="Warm inference workers">
          <table className="w-full text-[12px]">
            <thead className="text-white/40 text-left">
              <tr>
                <th className="font-normal pb-1">model</th>
                <th className="font-normal">adapter</th>
                <th className="font-normal text-right">load</th>
                <th className="font-normal text-right">served</th>
                <th className="font-normal text-right">rss</th>
              </tr>
            </thead>
            <tbody className="mono-num">
              {workers.map((w: any, i: number) => (
                <tr key={i} className="border-t border-white/5">
                  <td className="py-1 pr-2">{w.model}</td>
                  <td>{w.adapter ? "✓" : "—"}</td>
                  <td className="text-right">{w.load_s}s</td>
                  <td className="text-right">{w.served}</td>
                  <td className="text-right">{w.rss_mb}MB</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      <Card
        title="Benchmark — model efficiency over example tasks"
        right={
          <div className="flex gap-2 items-center">
            <select value={target} onChange={(e) => setTarget(e.target.value)} style={{ width: 240 }}>
              <optgroup label="Finetuned">
                {runs.map((r) => (
                  <option key={r.id} value={`run:${r.id}`}>
                    ★ {r.name}
                  </option>
                ))}
              </optgroup>
              <optgroup label="Base models">
                {models.map((m) => (
                  <option key={m.id} value={`model:${m.id}`}>
                    {m.label}
                  </option>
                ))}
              </optgroup>
            </select>
            <button
              className="btn-primary"
              onClick={runBench}
              disabled={busy || locked}
              title={locked ? "connect an authorized wallet (owner or BlocTime holder) to run a benchmark" : undefined}
            >
              {busy ? "running…" : locked ? "🔒 connect wallet" : "run benchmark"}
            </button>
          </div>
        }
      >
        {err && <div className="text-[12px] text-red-400 mb-2">⚠ {err}</div>}
        {!bench && !busy && (
          <div className="text-[12px] text-white/40">
            Runs 5 example coding tasks through the model and measures tokens, latency, throughput,
            and peak memory — the efficiency profile of this module on this CPU.
          </div>
        )}
        {busy && <div className="text-[12px] text-sky-300/70">loading model + running tasks (CPU — be patient)…</div>}
        {bench && (
          <>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
              <Stat label="Load time" value={`${bench.load_s}s`} />
              <Stat label="Avg latency" value={`${bench.avg_latency_s}s`} accent="#38bdf8" />
              <Stat label="Throughput" value={`${bench.avg_tokens_per_s}`} sub="tok/s avg" accent="#6ee7b7" />
              <Stat label="Peak RSS" value={`${bench.peak_rss_mb}MB`} />
              <Stat label="Total tokens" value={bench.total_completion_tokens} sub={`${bench.wall_s}s wall`} />
            </div>
            <table className="w-full text-[12px]">
              <thead className="text-white/40 text-left">
                <tr>
                  <th className="font-normal pb-1">task</th>
                  <th className="font-normal text-right">tok</th>
                  <th className="font-normal text-right">latency</th>
                  <th className="font-normal text-right">tok/s</th>
                </tr>
              </thead>
              <tbody className="mono-num">
                {bench.results.map((r: any, i: number) => (
                  <tr key={i} className="border-t border-white/5 align-top">
                    <td className="py-1.5 pr-3 font-sans text-white/75 max-w-[420px]">{r.task}</td>
                    <td className="text-right">{r.completion_tokens}</td>
                    <td className="text-right">{r.latency_s}s</td>
                    <td className="text-right">{r.tokens_per_s}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </Card>
    </div>
  );
}
