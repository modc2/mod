"use client";

// CHAT — the same conversation, against whichever machine you point it at.
//
// The runtime switch is one half of the page: BROWSER loads ONNX weights into
// a worker in this tab, SERVER streams SSE off the box the API runs on, CLOUD
// calls inference.liquid.ai on the operator's key. The other half is the
// model's own task, which decides what the board even is — a transcript for
// text and vision, an upload for speech, a similarity grid for embeddings.
// Liquid ships all four kinds, so all four have a surface here.
//
// The rail also carries what used to be a LOCAL tab. Pulling weights and
// pasting a cloud key are only ever done because a runtime you just picked
// can't run the model you just picked — so they belong under that switch, not
// behind a fourth tab you'd have to know to go looking for.

import Link from "next/link";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  embedTexts, fetchCatalog, fetchKeys, fetchLocal, fetchPulls, fetchRuntimes,
  loadRepo, pullRepo, reportBrowserRun, setKey, streamChat, transcribe,
  unloadRepo,
} from "../lib/api";
import { fmtBytes, useBrowserRuntime } from "../lib/browser";
import { params as fmtParams } from "../components/badges";
import { useAuth } from "../context/AuthContext";
import SignIn from "../components/SignIn";
import {
  messageImages, messageText,
  type Catalog, type ChatMessage, type ContentPart, type Embedding,
  type KeyStatus, type LocalModel, type Model, type Pull,
  type RunStats, type Runtime, type Runtimes,
} from "../lib/types";

const RUNTIMES: { id: Runtime; label: string; blurb: string }[] = [
  { id: "browser", label: "BROWSER", blurb: "this tab · WebGPU · nothing leaves the machine" },
  { id: "server", label: "SERVER", blurb: "the box running this module · transformers" },
  { id: "cloud", label: "CLOUD", blurb: "inference.liquid.ai · your key, your bill" },
];

// What each task needs from you before it can run.
const TASK_BLURB: Record<string, string> = {
  text: "a prompt",
  vision: "a prompt, and an image if you want it looked at",
  audio: "an audio file — speech comes back as text",
  embed: "two or more lines, compared against each other",
};

export default function RunPage() {
  return (
    <Suspense fallback={<div className="font-mono text-pixel-gray p-3">loading…</div>}>
      <Run />
    </Suspense>
  );
}

function Run() {
  const qs = useSearchParams();
  const { session } = useAuth();
  const [cat, setCat] = useState<Catalog | null>(null);
  const [rt, setRt] = useState<Runtimes | null>(null);
  const [runtime, setRuntime] = useState<Runtime>((qs.get("runtime") as Runtime) || "browser");
  const [modelId, setModelId] = useState<string>(qs.get("model") || "");
  const [maxTokens, setMaxTokens] = useState(256);
  const [temperature, setTemperature] = useState(0.3);
  const [system, setSystem] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [shots, setShots] = useState<string[]>([]);       // images pinned to the next turn
  const [live, setLive] = useState("");                   // the reply being streamed
  const [busy, setBusy] = useState(false);
  const [stats, setStats] = useState<RunStats | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [rail, setRail] = useState(true);                 // the control column
  const [gate, setGate] = useState(false);                // sign-in sheet
  const abort = useRef<AbortController | null>(null);
  const tail = useRef<HTMLDivElement>(null);
  const picker = useRef<HTMLInputElement>(null);

  const browser = useBrowserRuntime();

  useEffect(() => {
    fetchCatalog({ limit: "500" }).then(setCat).catch((e) => setErr(String(e)));
    fetchRuntimes().then(setRt).catch(() => {});
  }, []);

  useEffect(() => {
    tail.current?.scrollIntoView({ block: "end" });
  }, [messages, live]);

  // Only models that can actually run where you're pointing. A picker that
  // offers a GGUF-only model to the browser is a picker that lies — and the
  // browser can't do speech at all, so those drop out over there too.
  const options = useMemo(() => {
    if (runtime === "cloud") {
      return (rt?.cloud.models ?? []).map((id) => ({ id, repo: id, label: id, kind: "text" }));
    }
    const field = runtime === "browser" ? "onnx_repo" : "torch_repo";
    return (cat?.models ?? [])
      .filter((m) => m[field as keyof Model])
      .filter((m) => !(runtime === "browser" && m.kind === "audio"))
      .map((m) => ({
        id: m.id,
        repo: m[field as keyof Model] as string,
        kind: m.kind,
        label: `${m.id} · ${fmtParams(m)} · ${m.kind}${m.role ? ` · ${m.role}` : ""}`,
      }));
  }, [cat, rt, runtime]);

  // Keep the selection valid across a runtime switch: same model if it exists
  // over there, otherwise the smallest thing that does.
  useEffect(() => {
    if (!options.length) return;
    if (options.some((o) => o.id === modelId)) return;
    const fallback = runtime === "browser"
      ? options.find((o) => /230M|350M/.test(o.id)) ?? options[0]
      : options[0];
    setModelId(fallback.id);
  }, [options, modelId, runtime]);

  const selected = options.find((o) => o.id === modelId) ?? null;
  // Cloud model ids aren't catalog ids, so this is legitimately null there.
  const meta = cat?.models.find((m) => m.id === modelId) ?? null;
  const task = (selected?.kind ?? "text") as "text" | "vision" | "audio" | "embed";

  const runtimeReady =
    runtime === "browser" ? browser.status.supported !== false
      : runtime === "server" ? !!rt?.server.ok
      : !!rt?.cloud.ok;
  // Browser runs cost this box nothing, so they never ask who you are.
  const needsAccount = runtime !== "browser" && !session;

  const reset = useCallback(() => {
    abort.current?.abort();
    setMessages([]); setLive(""); setStats(null); setErr(null);
    setDraft(""); setShots([]); setBusy(false);
  }, []);

  // ── attachments ───────────────────────────────────────────────────

  const attach = useCallback((files: FileList | null) => {
    if (!files?.length) return;
    for (const file of Array.from(files).slice(0, 4)) {
      const reader = new FileReader();
      reader.onload = () => setShots((s) => [...s, String(reader.result)]);
      reader.readAsDataURL(file);
    }
  }, []);

  // Pasting a screenshot straight into the composer is the fastest path to a
  // vision run, and the one people try first.
  const onPaste = useCallback((e: React.ClipboardEvent) => {
    if (task !== "vision") return;
    const images = Array.from(e.clipboardData.files).filter((f) => f.type.startsWith("image/"));
    if (images.length) {
      e.preventDefault();
      attach(e.clipboardData.files);
    }
  }, [task, attach]);

  // ── send ──────────────────────────────────────────────────────────

  const send = useCallback(async (override?: ChatMessage[]) => {
    const text = draft.trim();
    if (busy || !selected) return;
    if (!override && !text && !shots.length) return;
    if (needsAccount) { setGate(true); return; }

    const turn: ChatMessage = shots.length
      ? { role: "user", content: [
          ...(text ? [{ type: "text", text } as ContentPart] : []),
          ...shots.map((image) => ({ type: "image", image } as ContentPart)),
        ] }
      : { role: "user", content: text };

    const base = override ?? [...messages, turn];
    const history: ChatMessage[] = [
      ...(system.trim() ? [{ role: "system" as const, content: system.trim() }] : []),
      ...base,
    ];
    setMessages(base);
    setDraft("");
    setShots([]);
    setLive("");
    setStats(null);
    setErr(null);
    setBusy(true);

    const finish = (s: RunStats | null, reply: string) => {
      setMessages((m) => [...m, { role: "assistant", content: reply }]);
      setLive("");
      setStats(s);
      setBusy(false);
    };
    // A run that dies mid-stream still generated something; dropping it would
    // lose the half-answer that's already on screen.
    const fail = (e: string, partial: string) => {
      if (partial) finish(null, partial);
      setErr(e);
      setBusy(false);
    };

    if (runtime === "browser") {
      let acc = "";
      try {
        // Loading is part of sending: you asked a question, not to download
        // 300 MB. The worker no-ops a load it already holds, so this is free
        // on every send after the first.
        await browser.load(selected.repo, task);
      } catch (e) {
        setErr(String(e instanceof Error ? e.message : e));
        setBusy(false);
        return;
      }
      browser.generate(history, { max_tokens: maxTokens, temperature }, {
        token: (t) => { acc += t; setLive(acc); },
        done: (s) => {
          finish(s, acc);
          // This run happened here, in this tab — the API never saw a token of
          // it and never will. Reporting it is the only way a browser run
          // reaches the ledger at all, and it's fire-and-forget: a backend
          // that's down must not cost you an answer you already have.
          reportBrowserRun({
            model: selected.repo, task, ok: true, turns: history.length,
            elapsed_sec: s.elapsed_sec, ttft_sec: s.ttft_sec ?? undefined,
            tokens: s.chunks, engine: browser.status.device ?? undefined,
          });
        },
        error: (e) => {
          fail(e, acc);
          reportBrowserRun({
            model: selected.repo, task, ok: false, error: e,
            turns: history.length, engine: browser.status.device ?? undefined,
          });
        },
      });
      return;
    }

    let acc = "";
    try {
      abort.current = new AbortController();
      let closed = false;
      for await (const ev of streamChat(
        { messages: history, model: selected.repo, runtime, max_tokens: maxTokens, temperature },
        abort.current.signal,
      )) {
        if (ev.type === "token") { acc += ev.text; setLive(acc); }
        else if (ev.type === "done") { closed = true; finish(ev as RunStats, acc); }
        else if (ev.type === "error") { closed = true; fail(ev.error, acc); }
      }
      // A stream that ends without a done frame (proxy timeout, killed
      // worker) still has to hand back whatever arrived.
      if (!closed) finish(null, acc);
    } catch (e) {
      fail(String(e instanceof Error ? e.message : e), acc);
    }
  }, [draft, shots, busy, selected, system, messages, runtime, maxTokens,
      temperature, browser, task, needsAccount]);

  // ── edit / fork ───────────────────────────────────────────────────

  // 🔧 lifts a turn back into the composer and drops everything after it: the
  // point of editing a prompt is to ask a different question from that point,
  // not to leave a reply that answered the old one.
  const editFrom = useCallback((index: number) => {
    const turn = messages[index];
    setDraft(messageText(turn));
    setShots(messageImages(turn));
    setMessages(messages.slice(0, index));
    setLive(""); setStats(null); setErr(null);
  }, [messages]);

  // ⑂ keeps the turn and everything before it and clears the rest — same
  // history, a fresh branch to carry on down.
  const forkAt = useCallback((index: number) => {
    setMessages(messages.slice(0, index + 1));
    setLive(""); setStats(null); setErr(null);
  }, [messages]);

  // The bar tracks the biggest file, not the sum: the tokenizer's 4 MB lands
  // first and a summed bar reads "4.5 / 4.5 MB — done" while the 200 MB weight
  // blob hasn't started. One dominant file is the honest denominator.
  const files = Object.values(browser.status.files);
  const lead = files.reduce((a, f) => (f.total > (a?.total ?? 0) ? f : a), files[0]);
  const dl = lead?.loaded ?? 0;
  const dlTotal = lead?.total ?? 0;
  const loading = runtime === "browser" && browser.status.state === "loading";

  return (
    <div className="flex flex-col lg:flex-row gap-2 flex-1 min-h-0">
      {/* ── control column ── */}
      {rail && (
        <aside className="lg:w-[300px] shrink-0 flex flex-col gap-2">
          <div className="pixel-panel p-2 flex flex-col gap-2">
            <div className="grid grid-cols-3 gap-1.5">
              {RUNTIMES.map((r) => (
                <button
                  key={r.id}
                  onClick={() => setRuntime(r.id)}
                  aria-pressed={runtime === r.id}
                  className={`pixel-btn topbar-ctl px-1 ${runtime === r.id ? "nav-active" : ""}`}
                  title={r.blurb}
                >
                  {r.label}
                </button>
              ))}
            </div>
            <p className="font-mono text-xs text-pixel-gray-light leading-snug">
              {RUNTIMES.find((r) => r.id === runtime)!.blurb}
            </p>

            {!runtimeReady && (
              <p className="font-mono text-xs text-red-400 leading-snug">
                {runtime === "cloud"
                  ? <>no cloud key — paste one below</>
                  : runtime === "server"
                    ? rt?.server.error ?? "server runtime unavailable"
                    : "this browser can't run a module worker"}
              </p>
            )}

            <select
              value={modelId}
              onChange={(e) => { setModelId(e.target.value); setStats(null); }}
              className="pixel-input-sm w-full font-mono"
              aria-label="model"
            >
              {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
              {!options.length && <option value="">no models for this runtime</option>}
            </select>
            {selected && (
              <p className="font-mono text-xs text-pixel-gray break-all">{selected.repo}</p>
            )}

            {task !== "embed" && (
              <div className="grid grid-cols-2 gap-1.5">
                <label className="flex flex-col gap-1">
                  <span className="stat-tile-label">TOKENS</span>
                  <input
                    type="number" min={16} max={4096} step={16} value={maxTokens}
                    onChange={(e) => setMaxTokens(Number(e.target.value))}
                    className="pixel-input-sm font-mono w-full"
                  />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="stat-tile-label">TEMP</span>
                  <input
                    type="number" min={0} max={2} step={0.1} value={temperature}
                    onChange={(e) => setTemperature(Number(e.target.value))}
                    className="pixel-input-sm font-mono w-full"
                  />
                </label>
              </div>
            )}

            {(task === "text" || task === "vision") && (
              <label className="flex flex-col gap-1">
                <span className="stat-tile-label">SYSTEM</span>
                <textarea
                  value={system}
                  onChange={(e) => setSystem(e.target.value)}
                  rows={2}
                  placeholder="optional"
                  className="pixel-input-sm font-mono w-full resize-none"
                />
              </label>
            )}
          </div>

          {/* The two things that used to be a LOCAL tab, each shown under the
              switch that makes it matter. */}
          {runtime === "server" && (
            <ServerWeights
              repo={selected?.repo ?? null}
              modelId={selected?.id ?? null}
              rt={rt}
              onChange={() => fetchRuntimes().then(setRt).catch(() => {})}
            />
          )}
          {runtime === "cloud" && (
            <CloudKey rt={rt} onChange={() => fetchRuntimes().then(setRt).catch(() => {})} />
          )}

          {/* Download meter — only while there's something to meter. */}
          {loading && (
            <div className="pixel-panel p-2 flex flex-col gap-1">
              <span className="stat-tile-label">PULLING WEIGHTS → THIS TAB</span>
              <div className="pixel-bar">
                <div
                  className="pixel-bar-fill"
                  style={{ width: `${dlTotal ? Math.round((100 * dl) / dlTotal) : 3}%` }}
                />
              </div>
              <span className="font-mono text-xs text-pixel-gray-light">
                {fmtBytes(dl)}{dlTotal ? ` / ${fmtBytes(dlTotal)}` : ""} · cached by the browser after the first run
              </span>
            </div>
          )}

          {browser.status.supported && runtime === "browser" && !loading && (
            <p className="font-mono text-xs text-pixel-gray px-1">
              device: {browser.status.device ?? (browser.status.webgpu ? "webgpu" : "wasm")}
              {browser.status.dtype ? ` · ${browser.status.dtype}` : ""}
              {browser.status.loadMs ? ` · loaded in ${(browser.status.loadMs / 1000).toFixed(1)}s` : ""}
            </p>
          )}

          {/* The rail would otherwise stop a third of the way down the board.
              The facts about what you just selected are what belongs in the
              rest of it. */}
          {meta && (
            <div className="pixel-panel p-2 flex flex-col gap-1">
              <span className="stat-tile-label">{meta.id}</span>
              <dl className="grid grid-cols-2 gap-x-2 gap-y-0.5 font-mono text-xs">
                <Fact k="params" v={fmtParams(meta)} />
                <Fact k="task" v={meta.kind + (meta.role ? ` · ${meta.role}` : "")} />
                <Fact k="formats" v={meta.formats.join(" ")} />
                <Fact k="pulls" v={meta.downloads.toLocaleString()} />
                <Fact k="license" v={meta.license ?? "—"} />
                <Fact k="updated" v={meta.updated?.slice(0, 10) ?? "—"} />
              </dl>
              {!!meta.languages.length && (
                <p className="font-mono text-xs text-pixel-gray-light">
                  {meta.languages.join(" · ")}
                </p>
              )}
              <Link href={`/models/${meta.id}`} className="pixel-btn topbar-ctl w-full no-underline">
                EVERY FORMAT →
              </Link>
            </div>
          )}
        </aside>
      )}

      {/* ── the board ── */}
      <section className="pixel-panel flex-1 min-h-0 flex flex-col">
        <div className="border-b-2 border-pixel-border px-2 py-1.5 flex items-center gap-1.5 flex-wrap">
          <button
            onClick={() => setRail((r) => !r)}
            className="pixel-btn topbar-ctl px-2.5"
            aria-pressed={rail}
            title={rail ? "hide the controls" : "show the controls"}
          >
            ▤<span className="hidden sm:inline ml-1.5">{rail ? "HIDE" : "SHOW"}</span>
          </button>
          <span className="font-mono text-sm text-pixel-gray-light truncate">
            {selected?.id ?? "—"} · <b className="text-cyan-400">{task}</b> · {TASK_BLURB[task]}
          </span>
          <div className="flex items-center gap-1.5 ml-auto">
            {busy && (
              <button
                onClick={() => { abort.current?.abort(); setBusy(false); }}
                className="pixel-btn topbar-ctl px-2.5 text-red-400"
                title="stop this run"
              >
                ■
              </button>
            )}
            <button
              onClick={reset}
              className="pixel-btn topbar-ctl px-2.5"
              title="start over — a new, empty run"
            >
              <span className="lq-ico" aria-hidden>✚</span>
            <span className="hidden sm:inline ml-1.5">NEW</span>
            </button>
          </div>
        </div>

        {task === "embed" ? (
          <EmbedBoard model={selected} runtime={runtime} browser={browser}
                      onGate={() => setGate(true)} needsAccount={needsAccount} />
        ) : task === "audio" ? (
          <AudioBoard model={selected} onGate={() => setGate(true)} needsAccount={needsAccount} />
        ) : (
          <>
            <div className="flex-1 min-h-0 overflow-y-auto p-2 sm:p-3 flex flex-col gap-2">
              {!messages.length && !live && (
                <StartCard
                  model={selected?.id ?? "a model"}
                  rt={rt}
                  browserDevice={browser.status.webgpu ? "webgpu" : "wasm"}
                  onPick={(p) => setDraft(p)}
                />
              )}
              {messages.map((m, i) => (
                <Bubble
                  key={i} message={m}
                  onEdit={m.role === "user" && !busy ? () => editFrom(i) : undefined}
                  onFork={!busy && i < messages.length - 1 ? () => forkAt(i) : undefined}
                />
              ))}
              {live && <Bubble message={{ role: "assistant", content: live }} live />}
              {busy && !live && (
                <p className="font-mono text-sm text-pixel-gray-light">
                  {loading ? "loading weights…" : "thinking…"}<span className="pixel-cursor" />
                </p>
              )}
              {err && (
                <p className="font-mono text-sm text-red-400 break-words">error — {err}</p>
              )}
              <div ref={tail} />
            </div>

            {stats && <StatsStrip stats={stats} />}

            <form
              onSubmit={(e) => { e.preventDefault(); send(); }}
              className="border-t-2 border-pixel-border p-2 flex flex-col gap-2"
            >
              {!!shots.length && (
                <div className="flex gap-1.5 flex-wrap">
                  {shots.map((src, i) => (
                    <button
                      key={i} type="button"
                      onClick={() => setShots((s) => s.filter((_, j) => j !== i))}
                      title="remove"
                      className="relative"
                    >
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={src} alt={`attachment ${i + 1}`} className="lq-thumb" />
                    </button>
                  ))}
                </div>
              )}
              <div className="flex gap-2">
                {task === "vision" && (
                  <>
                    <input
                      ref={picker} type="file" accept="image/*" multiple hidden
                      onChange={(e) => { attach(e.target.files); e.target.value = ""; }}
                    />
                    <button
                      type="button"
                      onClick={() => picker.current?.click()}
                      className="pixel-btn topbar-ctl px-3"
                      title="attach an image — or just paste one"
                    >
                      <span className="lq-ico" aria-hidden>✚</span>
                    </button>
                  </>
                )}
                <input
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onPaste={onPaste}
                  placeholder={
                    !runtimeReady ? "runtime unavailable"
                      : needsAccount ? "sign in to spend this box's compute…"
                      : task === "vision" ? "ask about the image…" : "say something…"
                  }
                  disabled={!runtimeReady}
                  className="pixel-input-sm font-mono flex-1 min-w-0"
                  aria-label="prompt"
                />
                <button
                  className="pixel-btn topbar-ctl px-4"
                  disabled={busy || !runtimeReady || (!draft.trim() && !shots.length)}
                >
                  {busy ? "…" : needsAccount ? "SIGN IN" : "SEND"}
                </button>
              </div>
            </form>
          </>
        )}
      </section>

      {gate && <SignIn onClose={() => setGate(false)} />}
    </div>
  );
}

// ── the box, from the rail ───────────────────────────────────────────

// SERVER can only run weights this box already holds, so the panel is about
// exactly one repo — the one selected above. Pull, load, free: the same three
// buttons the LOCAL table carried, minus fifty rows you weren't asking about.
function ServerWeights({ repo, modelId, rt, onChange }: {
  repo: string | null;
  modelId: string | null;
  rt: Runtimes | null;
  onChange: () => void;
}) {
  const [local, setLocal] = useState<LocalModel[]>([]);
  const [pulls, setPulls] = useState<Pull[]>([]);
  const [err, setErr] = useState<string | null>(null);

  const refresh = useCallback(() => {
    fetchLocal().then((d) => setLocal(d.models)).catch(() => {});
    fetchPulls().then((d) => setPulls(d.pulls)).catch(() => {});
  }, []);

  const pull = pulls.find((p) => p.repo === repo && p.state === "running");

  // Idle it polls slowly enough to stay out of the way; mid-download the bar
  // has to move or a five-minute pull reads as a hang.
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, pull ? 1500 : 6000);
    return () => clearInterval(t);
  }, [refresh, pull]);

  const disk = local.find((l) => l.repo === repo);
  const resident = rt?.server.loaded ?? null;

  const act = (fn: () => Promise<any>) => {
    setErr(null);
    fn().then(() => { refresh(); onChange(); }).catch((e) => setErr(String(e)));
  };

  return (
    <div className="pixel-panel p-2 flex flex-col gap-1.5">
      <span className="stat-tile-label">WEIGHTS ON THIS BOX</span>
      {!repo ? (
        <p className="font-mono text-xs text-pixel-gray">nothing selected</p>
      ) : (
        <>
          <p className="font-mono text-xs text-pixel-gray break-all">{repo}</p>
          {pull ? (
            <>
              <div className="pixel-bar">
                <div className="pixel-bar-fill" style={{ width: `${pull.pct ?? 5}%` }} />
              </div>
              <span className="font-mono text-xs text-pixel-gray-light">
                {fmtBytes(pull.bytes ?? 0)}{pull.total ? ` / ${fmtBytes(pull.total)}` : ""} · downloading
              </span>
            </>
          ) : (
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className={`pixel-badge ${
                disk?.resident ? "text-green-400 border-green-400"
                  : disk ? "text-pixel-gray-light border-pixel-border"
                  : "text-pixel-gray border-pixel-border"}`}>
                {disk?.resident ? "resident" : disk ? `on disk · ${fmtBytes(disk.bytes)}` : "not pulled"}
              </span>
              <button
                className="pixel-btn topbar-ctl px-2.5 ml-auto"
                onClick={() => act(() => disk?.resident ? unloadRepo() : disk ? loadRepo(repo) : pullRepo(repo))}
                title={disk?.resident ? "free the RAM" : disk ? "make it resident" : "download the safetensors"}
              >
                {disk?.resident ? "FREE" : disk ? "LOAD" : "PULL"}
              </button>
            </div>
          )}
        </>
      )}
      {/* One model at a time, so say which one is holding the slot — otherwise
          LOAD on a second model looks like it silently did nothing. */}
      <p className="font-mono text-xs text-pixel-gray-light leading-snug">
        {resident
          ? <>resident: <span className="text-green-400">{resident.repo.replace("LiquidAI/", "")}</span> · one at a time{
              modelId && resident.repo !== repo ? " — loading this evicts it" : ""}</>
          : "nothing resident — the first send loads the model"}
      </p>
      <dl className="grid grid-cols-2 gap-x-2 gap-y-0.5 font-mono text-xs">
        <Fact k="device" v={rt?.server.device ?? "—"} />
        <Fact k="gpu" v={rt?.server.gpu ?? "none"} />
        <Fact k="threads" v={String(rt?.server.threads ?? "—")} />
        <Fact k="torch" v={rt?.server.torch ?? "—"} />
      </dl>
      {err && <p className="font-mono text-xs text-red-400 break-words">{err}</p>}
    </div>
  );
}

// BYOK. The key is written to ~/.mod/liquidai/keys.json at 0600 and never to
// config.json, which is the whole reason this is a field and not an env var.
function CloudKey({ rt, onChange }: { rt: Runtimes | null; onChange: () => void }) {
  const [keys, setKeys] = useState<Record<string, KeyStatus> | null>(null);
  const [draft, setDraft] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { fetchKeys().then(setKeys).catch(() => {}); }, []);

  return (
    <div className="pixel-panel p-2 flex flex-col gap-1.5">
      <span className="stat-tile-label">CLOUD KEY (BYOK)</span>
      <div className="font-mono text-xs">
        {keys?.cloud?.set
          ? <span className="text-green-400">● {keys.cloud.masked} ({keys.cloud.source})</span>
          : <span className="text-red-400">○ no key</span>}
      </div>
      <form
        className="flex gap-1.5"
        onSubmit={(e) => {
          e.preventDefault();
          setErr(null); setMsg(null);
          const wanted = draft.trim();
          setKey(wanted)
            .then(async () => {
              setDraft("");
              setMsg(wanted ? "key stored" : "key cleared");
              setKeys(await fetchKeys());
              onChange();
            })
            .catch((e2) => setErr(String(e2)));
        }}
      >
        <input
          type="password"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="paste key…"
          className="pixel-input-sm font-mono flex-1 min-w-0"
          aria-label="cloud key"
        />
        <button className="pixel-btn topbar-ctl px-3">SAVE</button>
      </form>
      <p className="font-mono text-xs text-pixel-gray-light leading-snug">
        {rt?.cloud.ok
          ? `${rt.cloud.count} models reachable at ${rt.cloud.base}`
          : "runs bill to this key, never to a house key — stored at ~/.mod/liquidai/keys.json, 0600"}
      </p>
      {err && <p className="font-mono text-xs text-red-400 break-words">{err}</p>}
      {msg && <p className="font-mono text-xs text-cyan-400">{msg}</p>}
    </div>
  );
}

// ── embeddings ──────────────────────────────────────────────────────

const SAMPLE_LINES = [
  "the smallest model that solves the problem is the right model",
  "a tiny model is the correct choice when it can do the job",
  "the train leaves from platform nine at a quarter past four",
];

function EmbedBoard({ model, runtime, browser, needsAccount, onGate }: {
  model: { id: string; repo: string } | null;
  runtime: Runtime;
  browser: ReturnType<typeof useBrowserRuntime>;
  needsAccount: boolean;
  onGate: () => void;
}) {
  const [text, setText] = useState(SAMPLE_LINES.join("\n"));
  const [out, setOut] = useState<Embedding | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);

  const run = useCallback(async () => {
    if (!model || lines.length < 1) return;
    if (needsAccount) return onGate();
    setBusy(true); setErr(null); setOut(null);
    try {
      if (runtime === "browser") {
        await browser.load(model.repo, "embed");
        browser.embed(lines, {
          embedding: (e) => { setOut(e); setBusy(false); },
          error: (e) => { setErr(e); setBusy(false); },
        });
        return;
      }
      setOut(await embedTexts(model.repo, lines));
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    } finally {
      if (runtime !== "browser") setBusy(false);
    }
  }, [model, lines, runtime, browser, needsAccount, onGate]);

  return (
    <div className="flex-1 min-h-0 overflow-y-auto p-2 sm:p-3 flex flex-col gap-2">
      <p className="font-mono text-sm text-pixel-gray-light leading-snug">
        An embedding model turns each line into a vector. On its own that&apos;s a
        list of numbers; against another line it&apos;s an answer. One line per row —
        the grid below is every pair, scored 0 to 1.
      </p>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={6}
        className="pixel-input-sm font-mono w-full resize-y"
        aria-label="lines to embed"
      />
      <div className="flex items-center gap-2 flex-wrap">
        <button onClick={run} disabled={busy || lines.length < 1}
                className="pixel-btn topbar-ctl px-4">
          {busy ? "…" : needsAccount ? "SIGN IN" : "EMBED"}
        </button>
        <span className="font-mono text-sm text-pixel-gray-light">
          {lines.length} line{lines.length === 1 ? "" : "s"}
          {out ? ` · ${out.dim} dimensions · ${out.elapsed_sec}s` : ""}
        </span>
      </div>
      {err && <p className="font-mono text-sm text-red-400 break-words">error — {err}</p>}

      {out && (
        <div className="flex flex-col gap-1">
          <span className="stat-tile-label">COSINE SIMILARITY</span>
          <div className="overflow-x-auto">
            <div
              className="lq-sim min-w-[320px]"
              style={{ gridTemplateColumns: `minmax(120px, 2fr) repeat(${lines.length}, minmax(52px, 1fr))` }}
            >
              <div />
              {lines.map((_, i) => (
                <div key={`h${i}`} className="lq-sim-cell !border-transparent text-pixel-gray">
                  {i + 1}
                </div>
              ))}
              {out.similarity.map((row, i) => (
                <div key={`r${i}`} className="contents">
                  <div className="lq-sim-cell !border-transparent !text-left truncate" title={lines[i]}>
                    {i + 1}. {lines[i]}
                  </div>
                  {row.map((v, jx) => (
                    <div
                      key={jx}
                      className="lq-sim-cell"
                      // Below 0.5 every pair looks "a bit related" if you map
                      // the raw score; the floor is where the signal starts.
                      style={{ ["--heat" as any]: Math.max(0, (v - 0.45) / 0.55).toFixed(2) }}
                      title={`${lines[i]} ↔ ${lines[jx]}`}
                    >
                      {v.toFixed(2)}
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── speech ──────────────────────────────────────────────────────────

function AudioBoard({ model, needsAccount, onGate }: {
  model: { id: string; repo: string } | null;
  needsAccount: boolean;
  onGate: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [out, setOut] = useState<string | null>(null);
  const [meta, setMeta] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const run = useCallback(async () => {
    if (!model || !file) return;
    if (needsAccount) return onGate();
    setBusy(true); setErr(null); setOut(null);
    try {
      const res = await transcribe(model.repo, file);
      setOut(res.text);
      setMeta(`${res.seconds}s of audio in ${res.elapsed_sec}s`);
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  }, [model, file, needsAccount, onGate]);

  return (
    <div className="flex-1 min-h-0 overflow-y-auto p-2 sm:p-3 flex flex-col gap-2">
      <p className="font-mono text-sm text-pixel-gray-light leading-snug">
        Speech runs on the box, never in the tab. Drop a file in; whatever the
        box can decode (wav, mp3, m4a, webm) comes back as text.
      </p>
      {/* Said up front rather than as a 500 two minutes into a 3 GB download:
          LFM2-Audio is not a transformers architecture, it wants Liquid's own
          audio stack, which this box doesn't carry. */}
      <p className="font-mono text-xs text-amber-400 leading-snug">
        ⚑ Liquid&apos;s own audio models need Liquid&apos;s <span className="text-pixel-white">liquid-audio</span> runtime —
        stock transformers has no Lfm2Audio class and the repos ship no remote
        code. This surface serves speech-to-text repos this box can load; an
        LFM2-Audio pick will say so rather than half-run.
      </p>
      <input
        type="file"
        accept="audio/*"
        onChange={(e) => { setFile(e.target.files?.[0] ?? null); setOut(null); }}
        className="pixel-input-sm font-mono w-full"
        aria-label="audio file"
      />
      <div className="flex items-center gap-2 flex-wrap">
        <button onClick={run} disabled={busy || !file} className="pixel-btn topbar-ctl px-4">
          {busy ? "…" : needsAccount ? "SIGN IN" : "TRANSCRIBE"}
        </button>
        <span className="font-mono text-sm text-pixel-gray-light">
          {file ? `${file.name} · ${fmtBytes(file.size)}` : "no file"}
          {meta ? ` · ${meta}` : ""}
        </span>
      </div>
      {err && <p className="font-mono text-sm text-red-400 break-words">error — {err}</p>}
      {out !== null && (
        <div className="pixel-panel pixel-panel-cyan p-2">
          <span className="stat-tile-label text-cyan-400">TRANSCRIPT</span>
          <p className="arcade-prose-sm whitespace-pre-wrap break-words mt-1">
            {out || "(silence)"}
          </p>
        </div>
      )}
    </div>
  );
}

// ── shared furniture ────────────────────────────────────────────────

function StatsStrip({ stats }: { stats: RunStats }) {
  return (
    <div className="border-t-2 border-pixel-border px-2 sm:px-3 py-1.5 flex flex-wrap gap-x-4 gap-y-1 font-mono text-xs text-pixel-gray-light">
      <span>ran on <b className="text-cyan-400">{stats.runtime}</b>
        {stats.device ? ` (${stats.device})` : ""}</span>
      {stats.ttft_sec != null && <span>first token {stats.ttft_sec}s</span>}
      {stats.chunks_per_sec != null && <span>{stats.chunks_per_sec} chunks/s</span>}
      {stats.elapsed_sec != null && <span>total {stats.elapsed_sec}s</span>}
      {stats.prompt_tokens != null && <span>prompt {stats.prompt_tokens} tok</span>}
      {stats.usage?.total_tokens != null && <span>{stats.usage.total_tokens} tok billed</span>}
    </div>
  );
}

// The empty transcript is the first thing anyone sees, so it carries the two
// things worth knowing before the first prompt: what each switch would do
// right now, and something to type. Left blank it was half a screen of void.
const STARTERS = [
  "Explain what a Liquid Foundation Model is, in two sentences.",
  "Write a haiku about a model running inside a browser tab.",
  "Extract the dates and amounts: 'invoice 4471, $2,300 due 14 March, paid $800 on 2 March'.",
  "Translate to Japanese: the smallest model that solves the problem is the right model.",
  "Summarise this in one line: an edge model trades a little accuracy for never leaving the device.",
  "Name three tasks a 350M model does well and one it doesn't.",
];

function StartCard({ model, rt, browserDevice, onPick }: {
  model: string;
  rt: Runtimes | null;
  browserDevice: string;
  onPick: (p: string) => void;
}) {
  const rows = [
    { id: "BROWSER", ok: true, where: `this tab · ${browserDevice}`,
      note: "ONNX weights from HuggingFace, cached by the browser. Nothing reaches the server." },
    { id: "SERVER", ok: !!rt?.server.ok,
      where: rt?.server.ok ? `${rt.server.device}${rt.server.gpu ? ` · ${rt.server.gpu}` : ""} · ${rt.server.threads} threads` : "unavailable",
      note: rt?.server.ok ? (rt.server.note ?? "transformers on the box running this module") : (rt?.server.error ?? "—") },
    { id: "CLOUD", ok: !!rt?.cloud.ok,
      where: rt?.cloud.ok ? `${rt.cloud.count} models` : "no key",
      note: rt?.cloud.ok ? rt.cloud.base : "paste a key in the rail under CLOUD — runs bill to it, never to a house key" },
  ];

  return (
    <div className="flex flex-col gap-2 flex-1 min-h-0">
      <p className="font-mono text-sm text-pixel-gray-light">
        Ask <b className="text-pixel-white">{model}</b> something. The same prompt runs on any of
        the three switches; the strip under the transcript says where it actually ran and how fast.
      </p>

      <table className="pixel-table pixel-table-auto w-full">
        <thead>
          <tr>
            <th className="text-left">RUNTIME</th>
            <th className="text-left">RIGHT NOW</th>
            <th className="text-left">WHAT IT MEANS</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td className={`font-mono ${r.ok ? "text-green-400" : "text-red-400"}`}>
                {r.ok ? "●" : "○"} {r.id}
              </td>
              <td className="font-mono whitespace-nowrap">{r.where}</td>
              <td className="font-mono text-xs text-pixel-gray-light">{r.note}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <span className="stat-tile-label">TRY ONE</span>
      {/* auto-rows-fr + flex-1: the starters stretch to fill whatever the
          transcript pane isn't using, so an empty console reads as a menu
          rather than as a mistake. */}
      <div className="grid sm:grid-cols-2 gap-1.5 flex-1 auto-rows-fr">
        {STARTERS.map((s) => (
          <button
            key={s}
            onClick={() => onPick(s)}
            className="pixel-btn px-3 py-2 !justify-start !items-start text-left font-mono text-xs leading-snug normal-case"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}

function Fact({ k, v }: { k: string; v: string }) {
  return (
    <>
      <dt className="text-pixel-gray">{k}</dt>
      <dd className="truncate text-right" title={v}>{v}</dd>
    </>
  );
}

function Bubble({ message, live, onEdit, onFork }: {
  message: ChatMessage;
  live?: boolean;
  onEdit?: () => void;
  onFork?: () => void;
}) {
  const mine = message.role === "user";
  const images = messageImages(message);
  return (
    <div className={`pixel-panel ${mine ? "pixel-panel-cyan" : ""} p-2 group`}>
      <div className="flex items-center gap-2">
        <span className={`stat-tile-label ${mine ? "text-cyan-400" : "text-purple-400"}`}>
          {mine ? "YOU" : "LFM"}
        </span>
        {/* Actions live on the turn rather than in a menu: the thing you want
            to edit or branch from is the thing you're pointing at. */}
        <span className="ml-auto flex gap-1 opacity-40 group-hover:opacity-100 transition-opacity">
          {onEdit && (
            <button onClick={onEdit} className="pixel-btn topbar-ctl !px-2"
                    title="edit this turn and re-ask from here">
              <span className="lq-ico" aria-hidden>⚒</span>
            </button>
          )}
          {onFork && (
            <button onClick={onFork} className="pixel-btn topbar-ctl !px-2"
                    title="fork — keep the run up to here and carry on differently">
              <span className="lq-ico" aria-hidden>⋔</span>
            </button>
          )}
        </span>
      </div>
      {!!images.length && (
        <div className="flex gap-1.5 flex-wrap mt-1.5">
          {images.map((src, i) => (
            // eslint-disable-next-line @next/next/no-img-element
            <img key={i} src={src} alt={`attachment ${i + 1}`} className="lq-thumb" />
          ))}
        </div>
      )}
      <p className="arcade-prose-sm whitespace-pre-wrap break-words mt-1">
        {messageText(message)}{live && <span className="pixel-cursor" />}
      </p>
    </div>
  );
}
