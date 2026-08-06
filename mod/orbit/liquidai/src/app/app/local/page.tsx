"use client";

// LOCAL — what this box holds and what it can reach: weights on disk, the one
// model resident in RAM, and the cloud key. Three panels because they're the
// three answers to "why can't I run that yet".

import { useCallback, useEffect, useState } from "react";
import { KindBadge, params } from "../components/badges";
import { fmtBytes } from "../lib/browser";
import {
  fetchCatalog, fetchKeys, fetchLocal, fetchPulls, fetchRuntimes,
  loadRepo, pullRepo, setKey, unloadRepo,
} from "../lib/api";
import type { Catalog, KeyStatus, LocalModel, Pull, Runtimes } from "../lib/types";

export default function LocalPage() {
  const [cat, setCat] = useState<Catalog | null>(null);
  const [local, setLocal] = useState<LocalModel[]>([]);
  const [cache, setCache] = useState("");
  const [pulls, setPulls] = useState<Pull[]>([]);
  const [rt, setRt] = useState<Runtimes | null>(null);
  const [keys, setKeys] = useState<Record<string, KeyStatus> | null>(null);
  const [keyDraft, setKeyDraft] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const refresh = useCallback(() => {
    fetchLocal().then((d) => { setLocal(d.models); setCache(d.cache); }).catch(() => {});
    fetchPulls().then((d) => setPulls(d.pulls)).catch(() => {});
    fetchRuntimes().then(setRt).catch(() => {});
  }, []);

  useEffect(() => {
    refresh();
    fetchCatalog({ runtime: "server", limit: "500" }).then(setCat).catch(() => {});
    fetchKeys().then(setKeys).catch(() => {});
    // A pull is minutes of downloading; the panel has to move on its own.
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, [refresh]);

  const act = async (fn: () => Promise<any>, ok: string) => {
    setErr(null); setMsg(null);
    try { await fn(); setMsg(ok); refresh(); }
    catch (e) { setErr(String(e)); }
  };

  const resident = rt?.server.loaded ?? null;

  // One row per server-runnable model, pulled or not — a dropdown of things
  // to pull and a table of things pulled were the same list twice, and the
  // half of the board under them was empty.
  // Text and vision only: the SERVER runtime loads causal LMs, so listing an
  // embedding model here would offer a LOAD button that can only fail.
  const rows = (cat?.models ?? [])
    .filter((m) => m.torch_repo && (m.kind === "text" || m.kind === "vision"))
    .map((m) => {
      const repo = m.torch_repo as string;
      const disk = local.find((l) => l.repo === repo);
      const pull = pulls.find((p) => p.repo === repo && p.state === "running");
      return { model: m, repo, disk, pull };
    })
    .sort((a, b) => Number(!!b.disk) - Number(!!a.disk) || b.model.downloads - a.model.downloads);

  return (
    <div className="flex flex-col gap-2">
      <div className="page-head">
        <div className="page-head-band !py-2 !px-3">
          <h1 className="font-display text-sm sm:text-base">LOCAL</h1>
          <span className="font-mono text-sm text-pixel-gray-light truncate">
            {local.length} on disk · {fmtBytes(local.reduce((a, l) => a + l.bytes, 0))} · {cache || "…"}
          </span>
          <span className="font-mono text-sm ml-auto">
            {rt?.server.ok
              ? <span className="text-green-400">{rt.server.device}{rt.server.gpu ? ` · ${rt.server.gpu}` : ""} · {rt.server.threads} threads</span>
              : <span className="text-red-400">{rt?.server.error ?? "…"}</span>}
          </span>
        </div>
      </div>

      {(msg || err) && (
        <div className={`pixel-panel ${err ? "pixel-panel-red" : "pixel-panel-cyan"} px-3 py-2 font-mono text-sm ${err ? "text-red-400" : "text-cyan-400"}`}>
          {err || msg}
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-2 items-start">
        {/* ── every model this box could run, and whether it holds it ── */}
        <section className="pixel-panel xl:col-span-2 p-2 flex flex-col gap-2">
          <div className="flex flex-wrap items-baseline gap-2">
            <span className="stat-tile-label">SERVER-RUNNABLE MODELS</span>
            <span className="font-mono text-xs text-pixel-gray-light">
              PULL fetches safetensors into the HuggingFace cache · LOAD makes it resident
            </span>
            {resident && (
              <span className="font-mono text-xs text-green-400 ml-auto">
                resident: {resident.repo.replace("LiquidAI/", "")} · {resident.load_sec}s to load
                · one at a time
              </span>
            )}
          </div>

          <table className="pixel-table pixel-table-auto w-full">
            <thead>
              <tr>
                <th className="text-left">MODEL</th>
                <th className="text-right">PARAMS</th>
                <th className="text-left">TASK</th>
                <th className="text-right">ON DISK</th>
                <th className="text-left">STATE</th>
                <th className="text-right">ACTION</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ model, repo: r, disk, pull }) => (
                <tr key={r}>
                  <td className="font-mono">{model.id}</td>
                  <td className="text-right font-mono">{params(model)}</td>
                  <td><KindBadge kind={model.kind} /></td>
                  <td className="text-right font-mono">
                    {pull
                      ? `${fmtBytes(pull.bytes ?? 0)}${pull.total ? ` / ${fmtBytes(pull.total)}` : ""}`
                      : disk ? fmtBytes(disk.bytes) : "—"}
                  </td>
                  <td>
                    {pull ? (
                      <div className="pixel-bar w-[90px]">
                        <div className="pixel-bar-fill" style={{ width: `${pull.pct ?? 5}%` }} />
                      </div>
                    ) : (
                      <span className={`pixel-badge ${
                        disk?.resident ? "text-green-400 border-green-400"
                          : disk ? "text-pixel-gray-light border-pixel-border"
                          : "text-pixel-gray border-pixel-border"}`}>
                        {disk?.resident ? "resident" : disk ? "on disk" : "not pulled"}
                      </span>
                    )}
                  </td>
                  <td className="text-right">
                    <button
                      className="pixel-btn topbar-ctl px-2.5"
                      disabled={!!pull}
                      onClick={() => act(
                        () => disk?.resident ? unloadRepo() : disk ? loadRepo(r) : pullRepo(r),
                        disk?.resident ? "unloaded" : disk ? `${model.id} resident` : `pulling ${model.id}`,
                      )}
                    >
                      {pull ? "…" : disk?.resident ? "FREE" : disk ? "LOAD" : "PULL"}
                    </button>
                  </td>
                </tr>
              ))}
              {!rows.length && (
                <tr><td colSpan={6} className="text-center py-5 font-mono text-pixel-gray">
                  catalog unreachable — the browser runtime needs none of this anyway
                </td></tr>
              )}
            </tbody>
          </table>
        </section>

        {/* ── the other two runtimes, from this box's point of view ── */}
        <div className="flex flex-col gap-2">
        <section className="pixel-panel p-2 flex flex-col gap-2">
          <span className="stat-tile-label">CLOUD KEY (BYOK)</span>
          <p className="font-mono text-xs text-pixel-gray-light leading-snug">
            Cloud runs bill to the key you put here, and it never leaves this box — it&apos;s
            written to ~/.mod/liquidai/keys.json at 0600, never to config.json.
          </p>
          <div className="font-mono text-sm">
            {keys?.cloud?.set
              ? <span className="text-green-400">● {keys.cloud.masked} ({keys.cloud.source})</span>
              : <span className="text-red-400">○ no key</span>}
          </div>
          <form
            className="flex gap-1.5"
            onSubmit={(e) => {
              e.preventDefault();
              act(async () => { await setKey(keyDraft.trim()); setKeyDraft(""); setKeys(await fetchKeys()); },
                  keyDraft.trim() ? "key stored" : "key cleared");
            }}
          >
            <input
              type="password"
              value={keyDraft}
              onChange={(e) => setKeyDraft(e.target.value)}
              placeholder="paste key…"
              className="pixel-input-sm font-mono flex-1 min-w-0"
              aria-label="cloud key"
            />
            <button className="pixel-btn topbar-ctl px-3">SAVE</button>
          </form>
          {rt?.cloud.ok && (
            <p className="font-mono text-xs text-pixel-gray-light">
              {rt.cloud.count} models reachable at {rt.cloud.base}
            </p>
          )}
          {!rt?.cloud.ok && rt?.cloud.error && (
            <p className="font-mono text-xs text-red-400">{rt.cloud.error}</p>
          )}
        </section>

        <section className="pixel-panel p-2 flex flex-col gap-1">
          <span className="stat-tile-label">THIS BOX</span>
          <dl className="grid grid-cols-2 gap-x-2 gap-y-0.5 font-mono text-xs">
            <Row k="device" v={rt?.server.device ?? "—"} />
            <Row k="gpu" v={rt?.server.gpu ?? "none"} />
            <Row k="threads" v={String(rt?.server.threads ?? "—")} />
            <Row k="torch" v={rt?.server.torch ?? "—"} />
            <Row k="transformers" v={rt?.server.transformers ?? "—"} />
            <Row k="resident" v={resident ? resident.repo.replace("LiquidAI/", "") : "none"} />
          </dl>
          <p className="font-mono text-xs text-pixel-gray-light leading-snug mt-1">
            {rt?.server.note ?? ""} The BROWSER runtime needs none of this: it pulls ONNX weights
            straight into the visitor&apos;s tab, so nothing here is on its path.
          </p>
        </section>
        </div>
      </div>
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <>
      <dt className="text-pixel-gray">{k}</dt>
      <dd className="truncate text-right" title={v}>{v}</dd>
    </>
  );
}
