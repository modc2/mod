"use client";
import { useEffect, useState } from "react";
import { api, Model } from "../lib/api";
import { Card, Field, cpuBadge } from "./ui";
import { useAuth } from "../context/AuthContext";

export default function TrainPanel({ onStarted }: { onStarted: (id: string) => void }) {
  const { auth } = useAuth();
  // Server is the authority on who's allowed (owner OR a BlocTime holder) —
  // only block the UI when nobody has signed in at all.
  const locked = auth.gated && !auth.connected;
  const [models, setModels] = useState<Model[]>([]);
  const [model, setModel] = useState("");
  const [src, setSrc] = useState("");
  const [name, setName] = useState("");
  const [epochs, setEpochs] = useState(1);
  const [loraR, setLoraR] = useState(8);
  const [blockSize, setBlockSize] = useState(512);
  const [threads, setThreads] = useState(4);
  const [maxBlocks, setMaxBlocks] = useState(0);
  const [preview, setPreview] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.models().then((r) => {
      setModels(r.models);
      setModel(r.default);
    });
  }, []);

  const sel = models.find((m) => m.id === model);

  async function doPreview() {
    setErr("");
    setPreview(null);
    try {
      setPreview(await api.datasetPreview(src));
    } catch (e: any) {
      setErr(e.message);
    }
  }

  async function start() {
    setErr("");
    setBusy(true);
    try {
      const { id } = await api.train({
        src,
        model,
        name: name || undefined,
        epochs: Number(epochs),
        lora_r: Number(loraR),
        block_size: Number(blockSize),
        threads: Number(threads),
        max_blocks: Number(maxBlocks),
      });
      onStarted(id);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Card title="1 · Code corpus">
        <Field label="Directory of code to finetune over">
          <input
            placeholder="/path/to/your/codebase"
            value={src}
            onChange={(e) => setSrc(e.target.value)}
          />
        </Field>
        <div className="flex gap-2 mt-3">
          <button className="btn" onClick={doPreview} disabled={!src}>
            scan corpus
          </button>
        </div>
        {preview && !preview.error && (
          <div className="mt-3 text-[12px] text-white/70 space-y-1">
            <div>
              <span className="text-white/40">files</span>{" "}
              <b className="mono-num">{preview.files}</b>{" · "}
              <span className="text-white/40">~tokens</span>{" "}
              <b className="mono-num">{preview.approx_tokens?.toLocaleString()}</b>{" · "}
              <span className="text-white/40">skipped</span>{" "}
              <span className="mono-num">{preview.skipped}</span>
            </div>
            <div className="flex flex-wrap gap-1">
              {Object.entries(preview.by_lang || {}).map(([k, v]: any) => (
                <span key={k} className="pill text-white/60">
                  {k} {v}
                </span>
              ))}
            </div>
          </div>
        )}
      </Card>

      <Card title="2 · Base model">
        <Field label="Model (CPU-friendly Qwen variants)">
          <select value={model} onChange={(e) => setModel(e.target.value)}>
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label} · {m.params}
              </option>
            ))}
          </select>
        </Field>
        {sel && (
          <div className="mt-2 flex items-center gap-2">
            {cpuBadge(sel.cpu)}
            <span className="text-[11px] text-white/45">{sel.note}</span>
          </div>
        )}
        <Field label="Run name (optional)">
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="auto" />
        </Field>
      </Card>

      <Card title="3 · LoRA hyperparameters" className="md:col-span-2">
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <Field label="Epochs">
            <input type="number" step="0.5" value={epochs} onChange={(e) => setEpochs(+e.target.value)} />
          </Field>
          <Field label="LoRA rank">
            <input type="number" value={loraR} onChange={(e) => setLoraR(+e.target.value)} />
          </Field>
          <Field label="Block size (tokens)">
            <input type="number" value={blockSize} onChange={(e) => setBlockSize(+e.target.value)} />
          </Field>
          <Field label="CPU threads">
            <input type="number" value={threads} onChange={(e) => setThreads(+e.target.value)} />
          </Field>
          <Field label="Max blocks (0 = all)">
            <input type="number" value={maxBlocks} onChange={(e) => setMaxBlocks(+e.target.value)} />
          </Field>
        </div>
        <div className="flex items-center gap-3 mt-4">
          <button
            className="btn-primary"
            onClick={start}
            disabled={!src || !model || busy || locked}
            title={locked ? "connect an authorized wallet (owner or BlocTime holder) to start a training run" : undefined}
          >
            {busy ? "starting…" : locked ? "🔒 connect wallet" : "▶ start finetune"}
          </button>
          <span className="text-[11px] text-white/40">
            CPU training is slow — start with a small corpus + max blocks to validate.
          </span>
        </div>
        {err && <div className="mt-3 text-[12px] text-red-400">⚠ {err}</div>}
      </Card>
    </div>
  );
}
