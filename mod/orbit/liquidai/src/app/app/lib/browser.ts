"use client";

// The main-thread half of the browser runtime: owns the worker, turns its
// messages into React state, and never blocks the UI thread — a 300 MB model
// load and a token loop both live over there.

import { useCallback, useEffect, useRef, useState } from "react";
import type { ChatMessage, Embedding, RunStats } from "./types";

export interface LoadProgress {
  file: string;
  loaded: number;
  total: number;
  pct: number;
}

export interface BrowserState {
  supported: boolean | null;   // null until probed
  webgpu: boolean;
  state: "idle" | "loading" | "ready" | "generating" | "error";
  repo: string | null;
  device: string | null;
  dtype: string | null;
  loadMs: number | null;
  files: Record<string, LoadProgress>;
  error: string | null;
}

const INITIAL: BrowserState = {
  supported: null, webgpu: false, state: "idle", repo: null, device: null,
  dtype: null, loadMs: null, files: {}, error: null,
};

export function useBrowserRuntime() {
  const workerRef = useRef<Worker | null>(null);
  const [status, setStatus] = useState<BrowserState>(INITIAL);
  // Token and done handlers are swapped per run; a ref keeps the worker's
  // single onmessage pointed at the live one without re-creating the worker.
  const sink = useRef<{ token?: (t: string) => void; done?: (s: RunStats) => void;
                        error?: (e: string) => void;
                        embedding?: (e: Embedding) => void }>({});
  // An in-flight load, so callers can await weights instead of polling state.
  const pendingLoad = useRef<{ resolve: () => void; reject: (e: Error) => void } | null>(null);

  const ensure = useCallback(() => {
    if (workerRef.current) return workerRef.current;
    const base = process.env.NEXT_PUBLIC_BASE_PATH ?? "";
    const worker = new Worker(`${base}/lfm-worker.js`, { type: "module" });
    worker.onmessage = (e: MessageEvent) => {
      const msg = e.data || {};
      switch (msg.type) {
        case "probe":
          setStatus((s) => ({ ...s, supported: true, webgpu: !!msg.webgpu }));
          break;
        case "status":
          setStatus((s) => ({ ...s, state: "loading", error: null }));
          break;
        case "progress":
          setStatus((s) => ({
            ...s,
            files: { ...s.files, [msg.file]: msg as LoadProgress },
          }));
          break;
        case "ready":
          setStatus((s) => ({
            ...s, state: "ready", repo: msg.repo, device: msg.device,
            dtype: msg.dtype, loadMs: msg.ms, error: null,
          }));
          pendingLoad.current?.resolve();
          pendingLoad.current = null;
          break;
        case "token":
          sink.current.token?.(msg.text);
          break;
        case "done":
          setStatus((s) => ({ ...s, state: "ready" }));
          sink.current.done?.(msg as RunStats);
          break;
        case "embedding":
          setStatus((s) => ({ ...s, state: "ready" }));
          sink.current.embedding?.(msg as Embedding);
          break;
        case "error":
          setStatus((s) => ({ ...s, state: "error", error: msg.error }));
          // A load that fails has to reject its awaiter, not just paint red.
          pendingLoad.current?.reject(new Error(msg.error));
          pendingLoad.current = null;
          sink.current.error?.(msg.error);
          break;
      }
    };
    // A worker that dies outright (bad CDN, blocked import) never posts, so
    // an awaited load would hang on nothing.
    worker.onerror = (e) => {
      const error = (e as ErrorEvent).message || "worker failed to start";
      setStatus((s) => ({ ...s, state: "error", error }));
      pendingLoad.current?.reject(new Error(error));
      pendingLoad.current = null;
      sink.current.error?.(error);
    };
    workerRef.current = worker;
    return worker;
  }, []);

  useEffect(() => {
    // A module worker is the floor: no module workers, no browser runtime.
    if (typeof Worker === "undefined") {
      setStatus((s) => ({ ...s, supported: false }));
      return;
    }
    try {
      ensure().postMessage({ type: "probe" });
    } catch (e) {
      setStatus((s) => ({ ...s, supported: false, error: String(e) }));
    }
    return () => { workerRef.current?.terminate(); workerRef.current = null; };
  }, [ensure]);

  // Resolves when the weights are resident — the worker answers `ready` for a
  // repo it already holds too, so calling this on every send is free.
  const load = useCallback((repo: string, modality = "text", dtype?: string, device?: string) => {
    setStatus((s) => ({ ...s, state: "loading", files: {}, error: null, repo }));
    return new Promise<void>((resolve, reject) => {
      pendingLoad.current = { resolve, reject };
      ensure().postMessage({ type: "load", repo, modality, dtype, device });
    });
  }, [ensure]);

  const generate = useCallback((
    messages: ChatMessage[],
    opts: { max_tokens?: number; temperature?: number },
    handlers: { token: (t: string) => void; done: (s: RunStats) => void; error: (e: string) => void },
  ) => {
    sink.current = handlers;
    setStatus((s) => ({ ...s, state: "generating" }));
    ensure().postMessage({ type: "generate", messages, ...opts });
  }, [ensure]);

  const embed = useCallback((
    texts: string[],
    handlers: { embedding: (e: Embedding) => void; error: (e: string) => void },
  ) => {
    sink.current = handlers;
    setStatus((s) => ({ ...s, state: "generating" }));
    ensure().postMessage({ type: "embed", texts });
  }, [ensure]);

  return { status, load, generate, embed };
}

export function fmtBytes(n: number): string {
  if (!n) return "0";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)}${units[i]}`;
}
