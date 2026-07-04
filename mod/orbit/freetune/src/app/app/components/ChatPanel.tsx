"use client";
import { useEffect, useRef, useState } from "react";
import { api, Model } from "../lib/api";
import { Card } from "./ui";
import { useAuth } from "../context/AuthContext";

type Msg = { role: "user" | "assistant"; text: string; usage?: any };

export default function ChatPanel() {
  const { auth } = useAuth();
  // Server is the authority on who's allowed (owner OR a BlocTime holder) —
  // only block the UI when nobody has signed in at all.
  const locked = auth.gated && !auth.connected;
  const [models, setModels] = useState<Model[]>([]);
  const [runs, setRuns] = useState<any[]>([]);
  const [target, setTarget] = useState(""); // "model:<id>" or "run:<id>"
  const [maxTok, setMaxTok] = useState(200);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.models().then((r) => {
      setModels(r.models);
      setTarget((t) => t || `model:${r.default}`);
    });
    api.runs().then((r) => setRuns(r.runs.filter((x: any) => x.has_adapter)));
  }, []);

  useEffect(() => endRef.current?.scrollIntoView({ behavior: "smooth" }), [msgs]);

  async function send() {
    if (!input.trim() || busy) return;
    const prompt = input.trim();
    setInput("");
    setMsgs((m) => [...m, { role: "user", text: prompt }]);
    setBusy(true);
    try {
      const [kind, id] = target.split(/:(.+)/);
      const req: any = { prompt, max_new_tokens: Number(maxTok) };
      if (kind === "run") req.run_id = id;
      else req.model = id;
      const r = await api.infer(req);
      setMsgs((m) => [...m, { role: "assistant", text: r.text, usage: r }]);
    } catch (e: any) {
      setMsgs((m) => [...m, { role: "assistant", text: "⚠ " + e.message }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="Chat / inference"
      right={
        <div className="flex items-center gap-2">
          <select
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            style={{ width: 260 }}
          >
            <optgroup label="Finetuned (run adapter)">
              {runs.map((r) => (
                <option key={r.id} value={`run:${r.id}`}>
                  ★ {r.name} ({r.id})
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
          <input
            type="number"
            value={maxTok}
            onChange={(e) => setMaxTok(+e.target.value)}
            style={{ width: 80 }}
            title="max new tokens"
          />
        </div>
      }
    >
      <div className="space-y-3 max-h-[58vh] overflow-auto pr-1">
        {locked && (
          <div className="text-[12px] text-amber-400">
            🔒 connect an authorized wallet (owner or BlocTime holder, top right) to chat with a model.
          </div>
        )}
        {!locked && msgs.length === 0 && (
          <div className="text-[12px] text-white/40">
            First message loads the model (cold start — a few seconds on CPU). After that the
            worker stays warm. Each reply shows its token usage + latency.
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i}>
            <div className="text-[10px] uppercase tracking-wide text-white/35 mb-1">{m.role}</div>
            <div
              className="text-[13px] whitespace-pre-wrap leading-[1.55] p-3 rounded-lg"
              style={{
                background: m.role === "user" ? "#101722" : "#0f1410",
                border: "1px solid #1e2530",
              }}
            >
              {m.text}
            </div>
            {m.usage && (
              <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-white/45 mt-1 mono-num">
                <span>{m.usage.completion_tokens} tok</span>
                <span>{m.usage.latency_s}s</span>
                <span>{m.usage.tokens_per_s} tok/s</span>
                <span>RSS {m.usage.peak_rss_mb}MB</span>
                {m.usage.adapter && <span className="text-emerald-300">finetuned</span>}
                {m.usage.worker_served === 1 && <span>load {m.usage.worker_load_s}s</span>}
              </div>
            )}
          </div>
        ))}
        {busy && <div className="text-[12px] text-sky-300/70">generating…</div>}
        <div ref={endRef} />
      </div>
      <div className="flex gap-2 mt-3">
        <textarea
          rows={2}
          value={input}
          placeholder="Ask the model something about your code…"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send();
          }}
        />
        <button className="btn-primary" onClick={send} disabled={busy || !input.trim() || locked}>
          {locked ? "🔒 locked" : "send"}
        </button>
      </div>
      <div className="text-[10px] text-white/30 mt-1">⌘/Ctrl + Enter to send</div>
    </Card>
  );
}
