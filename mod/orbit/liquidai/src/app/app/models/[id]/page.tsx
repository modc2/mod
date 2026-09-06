"use client";

// One model, every way it ships. The formats table is the useful half: it's
// the same weights four times over, and which row you take decides which
// machine gets to run it.

import Link from "next/link";
import { useEffect, useState } from "react";
import { KindBadge, RoleBadge, RuntimeBadges, fmtCount, params } from "../../components/badges";
import { fetchModel, pullRepo } from "../../lib/api";
import type { Model } from "../../lib/types";

const FORMAT_NOTE: Record<string, string> = {
  torch: "safetensors — transformers, vLLM, this box's SERVER runtime",
  onnx: "ONNX — transformers.js in the browser, onnxruntime anywhere",
  gguf: "GGUF — llama.cpp, Ollama, LM Studio, LEAP bundles on a phone",
  mlx: "MLX — Apple silicon, native",
};

export default function ModelPage({ params: route }: { params: { id: string } }) {
  const [m, setM] = useState<Model | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    fetchModel(decodeURIComponent(route.id)).then(setM).catch((e) => setErr(String(e)));
  }, [route.id]);

  if (err) {
    return (
      <div className="pixel-panel pixel-panel-red p-3 font-mono text-red-400">
        {err} — <Link href="/" className="text-cyan-400">back to catalog</Link>
      </div>
    );
  }
  if (!m) return <div className="font-mono text-pixel-gray p-3">loading…</div>;

  const runnable = m.kind === "text" || m.kind === "vision";

  return (
    <div className="flex flex-col gap-2">
      <div className="page-head">
        <div className="page-head-band !py-2 !px-3">
          <h1 className="font-display text-sm sm:text-base truncate">{m.id}</h1>
          <span className="inline-flex flex-wrap gap-1">
            <KindBadge kind={m.kind} />
            <RoleBadge role={m.role} />
            <RuntimeBadges runtimes={m.runtimes} />
          </span>
          <div className="flex gap-1.5 ml-auto">
            {runnable && m.onnx_repo && (
              <Link href={`/chat?model=${m.id}&runtime=browser`} className="pixel-btn topbar-ctl px-3 no-underline">
                ▶ IN BROWSER
              </Link>
            )}
            {runnable && m.torch_repo && (
              <Link href={`/chat?model=${m.id}&runtime=server`} className="pixel-btn topbar-ctl px-3 no-underline">
                ▶ ON SERVER
              </Link>
            )}
          </div>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-px bg-pixel-border">
          <Fact k="PARAMS" v={params(m)} />
          <Fact k="GENERATION" v={m.family} />
          <Fact k="FORMATS" v={m.formats.join(" ")} />
          <Fact k="PULLS" v={fmtCount(m.downloads)} />
          <Fact k="LICENSE" v={m.license ?? "—"} />
          <Fact k="UPDATED" v={m.updated ? m.updated.slice(0, 10) : "—"} />
        </div>
      </div>

      <div className="pixel-panel overflow-x-auto">
        <table className="pixel-table pixel-table-auto w-full">
          <thead>
            <tr>
              <th className="text-left">FORMAT</th>
              <th className="text-left">REPO</th>
              <th className="text-left">QUANT</th>
              <th className="text-right hidden sm:table-cell">PULLS</th>
              <th className="text-left">WHERE IT RUNS</th>
              <th className="text-right">ON DISK</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(m.variants).flatMap(([fmt, v]) =>
              v.repos.map((r) => (
                <tr key={r.repo}>
                  <td className="font-mono">{fmt}</td>
                  <td className="font-mono">
                    <a
                      href={`https://huggingface.co/${r.repo}`}
                      target="_blank" rel="noreferrer"
                      className="text-pixel-white hover:text-cyan-400 no-underline"
                    >
                      {r.repo}
                    </a>
                  </td>
                  <td className="font-mono">{r.quant ?? (fmt === "torch" ? "bf16" : "—")}</td>
                  <td className="text-right font-mono hidden sm:table-cell">{fmtCount(r.downloads)}</td>
                  <td className="font-mono text-xs text-pixel-gray-light">{FORMAT_NOTE[fmt]}</td>
                  <td className="text-right">
                    {fmt === "torch" ? (
                      r.local
                        ? <span className="pixel-badge text-green-400 border-green-400">pulled</span>
                        : <button
                            className="pixel-btn topbar-ctl px-2.5"
                            onClick={() => pullRepo(r.repo)
                              .then(() => setNote(`pulling ${r.repo} — watch it in the CHAT rail on SERVER`))
                              .catch((e) => setNote(String(e)))}
                          >
                            PULL
                          </button>
                    ) : fmt === "onnx" ? (
                      <span className="font-mono text-xs text-pixel-gray" title="the browser caches these itself">
                        browser-cached
                      </span>
                    ) : (
                      <span className="font-mono text-xs text-pixel-gray">—</span>
                    )}
                  </td>
                </tr>
              )),
            )}
          </tbody>
        </table>
      </div>

      {note && <p className="font-mono text-sm text-cyan-400 px-1">{note}</p>}

      {!!m.languages.length && (
        <p className="font-mono text-xs text-pixel-gray-light px-1">
          languages: {m.languages.join(" · ")}
        </p>
      )}

      {/* This board is a front door, not a lock-in: the same model runs on
          four other people's runtimes and the commands for that are short
          enough to print. */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
        {m.torch_repo && (
          <Snippet
            title="TRANSFORMERS (PYTHON)"
            code={`from transformers import pipeline\npipe = pipeline("text-generation", "${m.torch_repo}")\nprint(pipe("hello")[0]["generated_text"])`}
          />
        )}
        {m.onnx_repo && (
          <Snippet
            title="TRANSFORMERS.JS (BROWSER)"
            code={`import { pipeline } from "@huggingface/transformers";\nconst pipe = await pipeline("text-generation",\n  "${m.onnx_repo}", { device: "webgpu", dtype: "q4f16" });`}
          />
        )}
        {m.gguf_repo && (
          <Snippet
            title="LLAMA.CPP (ANY BOX)"
            code={`llama-cli -hf ${m.gguf_repo} -p "hello"`}
          />
        )}
      </div>
    </div>
  );
}

function Snippet({ title, code }: { title: string; code: string }) {
  return (
    <div className="pixel-panel p-2 flex flex-col gap-1 min-w-0">
      <span className="stat-tile-label">{title}</span>
      <pre className="font-mono text-xs text-pixel-gray-light whitespace-pre-wrap break-all m-0">
        {code}
      </pre>
    </div>
  );
}

function Fact({ k, v }: { k: string; v: string }) {
  return (
    <div className="bg-pixel-panel px-3 py-1.5">
      <div className="stat-tile-label">{k}</div>
      <div className="font-mono text-sm truncate" title={v}>{v}</div>
    </div>
  );
}
