"use client";

// BACKEND — the three tabs above this one are about models. This one is about
// the machinery: who actually serves a run, whether each of them is up, and
// every call the module has answered since it started.
//
// Three boards, because there are three questions:
//   PROVIDERS  where can work go, what does each place cost, who is carrying it
//   CALLS      the ledger, newest first, with the numbers each run produced
//   MCP        the same API as tools, for the agents that don't speak REST
//
// It polls while you're looking at it and stops when you leave — a console
// left open on a laptop lid should not be a load generator.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "../context/AuthContext";
import {
  clearCalls, fetchCallStats, fetchCalls, fetchMcp, fetchProviders,
} from "../lib/api";
import type {
  Call, CallPage, CallStats, McpDescriptor, Provider, ProvidersTable, Traffic,
} from "../lib/types";

type Board = "providers" | "calls" | "mcp";

const BOARDS: { id: Board; label: string }[] = [
  { id: "providers", label: "PROVIDERS" },
  { id: "calls", label: "CALLS" },
  { id: "mcp", label: "MCP" },
];

const WINDOWS = [
  { hours: 1, label: "1H" },
  { hours: 24, label: "24H" },
  { hours: 168, label: "7D" },
];

// The same colours the rest of the console uses for a runtime, extended to the
// two providers that aren't one: HuggingFace (where every weight comes from)
// and the module itself (calls it answers out of its own memory).
const TONE: Record<string, string> = {
  browser: "text-cyan-400 border-cyan-400",
  server: "text-purple-400 border-purple-400",
  cloud: "text-amber-400 border-amber-400",
  huggingface: "text-green-400 border-green-400",
  liquidai: "text-pixel-gray-light border-pixel-border",
};

const BAR: Record<string, string> = {
  browser: "var(--neon-cyan)",
  server: "var(--neon-magenta, #c084fc)",
  cloud: "var(--neon-amber)",
  huggingface: "var(--neon-lime)",
  liquidai: "var(--border-strong)",
};

const VIA_NOTE: Record<string, string> = {
  console: "this console",
  mcp: "an MCP client",
  openai: "the OpenAI-compatible face",
  cli: "a shell on this box",
  api: "a direct API call",
};

function ago(at: number | null | undefined): string {
  if (!at) return "never";
  const sec = Math.max(0, Date.now() / 1000 - at);
  if (sec < 60) return `${Math.round(sec)}s ago`;
  if (sec < 3600) return `${Math.round(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.round(sec / 3600)}h ago`;
  return `${Math.round(sec / 86400)}d ago`;
}

function clock(at: number): string {
  return new Date(at * 1000).toLocaleTimeString([], {
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

function dur(ms: number | null | undefined): string {
  if (ms == null) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}

function bytes(n: number): string {
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`;
  if (n >= 1e6) return `${Math.round(n / 1e6)} MB`;
  return `${Math.round(n / 1e3)} KB`;
}

function count(n: number): string {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return String(n);
}

export default function BackendPage() {
  const { session } = useAuth();
  const [board, setBoard] = useState<Board>("providers");
  const [hours, setHours] = useState(24);
  const [live, setLive] = useState(true);

  const [table, setTable] = useState<ProvidersTable | null>(null);
  const [stats, setStats] = useState<CallStats | null>(null);
  const [page, setPage] = useState<CallPage | null>(null);
  const [mcp, setMcp] = useState<McpDescriptor | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // Ledger filters. They run on the API rather than in the browser: the point
  // of "show me every cloud call that failed" is the ones that fell off the
  // end of the page, and a client-side filter can only hide what it was sent.
  const [provider, setProvider] = useState("");
  const [via, setVia] = useState("");
  const [model, setModel] = useState("");
  const [failed, setFailed] = useState(false);
  const [runsOnly, setRunsOnly] = useState(false);

  const loading = useRef(false);

  const pull = useCallback(async () => {
    if (loading.current) return;
    loading.current = true;
    try {
      const params: Record<string, string> = {
        since_minutes: String(hours * 60), limit: "250",
      };
      if (provider) params.provider = provider;
      if (via) params.via = via;
      if (model.trim()) params.model = model.trim();
      if (failed) params.failed_only = "true";
      if (runsOnly) params.inference_only = "true";
      const [t, s, p] = await Promise.all([
        fetchProviders(hours), fetchCallStats(hours), fetchCalls(params),
      ]);
      setTable(t); setStats(s); setPage(p); setErr(null);
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    } finally {
      loading.current = false;
    }
  }, [hours, provider, via, model, failed, runsOnly]);

  useEffect(() => { pull(); }, [pull]);

  useEffect(() => {
    if (!live) return;
    const id = setInterval(pull, 5000);
    // A hidden tab polling every five seconds is a background job nobody asked
    // for; the next visible tick catches up anyway.
    const wake = () => { if (!document.hidden) pull(); };
    document.addEventListener("visibilitychange", wake);
    return () => { clearInterval(id); document.removeEventListener("visibilitychange", wake); };
  }, [live, pull]);

  useEffect(() => {
    if (board === "mcp" && !mcp) fetchMcp().then(setMcp).catch(() => {});
  }, [board, mcp]);

  const total = stats?.total;
  const busiest = useMemo(() => {
    if (!stats) return null;
    const rows = Object.entries(stats.providers) as [string, Traffic][];
    return rows.sort((a, b) => b[1].calls - a[1].calls)[0] ?? null;
  }, [stats]);

  return (
    <div className="flex flex-col gap-2 min-h-0">
      <div className="page-head">
        <div className="page-head-band !py-2 !px-3">
          <h1 className="font-display text-sm sm:text-base whitespace-nowrap">BACKEND</h1>
          <span className="font-mono text-sm text-pixel-gray-light">
            {total ? `${count(total.calls)} calls · ${count(total.tokens_out)} tokens out` : "…"}
            {" · "}{table?.providers.filter((p) => p.ok).length ?? "—"}/{table?.providers.length ?? "—"} providers up
          </span>

          <div className="flex flex-wrap items-center gap-1.5 ml-auto">
            {BOARDS.map((b) => (
              <button
                key={b.id}
                onClick={() => setBoard(b.id)}
                aria-pressed={board === b.id}
                className={`pixel-btn topbar-ctl px-2.5 ${board === b.id ? "nav-active" : ""}`}
              >
                {b.label}
              </button>
            ))}
            <span className="w-px h-5 bg-pixel-border mx-1" aria-hidden />
            {WINDOWS.map((w) => (
              <button
                key={w.hours}
                onClick={() => setHours(w.hours)}
                aria-pressed={hours === w.hours}
                className={`pixel-btn topbar-ctl px-2 ${hours === w.hours ? "nav-active" : ""}`}
                title={`traffic over the last ${w.label}`}
              >
                {w.label}
              </button>
            ))}
            <button
              onClick={() => setLive((v) => !v)}
              aria-pressed={live}
              className={`pixel-btn topbar-ctl px-2.5 ${live ? "nav-active" : ""}`}
              title="poll every five seconds"
            >
              {live ? "● LIVE" : "○ PAUSED"}
            </button>
          </div>
        </div>
      </div>

      {err && (
        <div className="pixel-panel pixel-panel-red p-3 font-mono text-sm text-red-400">
          backend unreachable — {err}
        </div>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
        <div className="stat-tile stat-tile-accent">
          <span className="stat-tile-label">CALLS · {hours}H</span>
          <div className="stat-tile-value">{total ? count(total.calls) : "—"}</div>
          <span className="stat-tile-sub">
            {total ? `${total.inference} of them ran a model` : "…"}
          </span>
        </div>
        <div className={`stat-tile ${total?.errors ? "stat-tile-down" : "stat-tile-up"}`}>
          <span className="stat-tile-label">FAILED</span>
          <div className="stat-tile-value">{total?.errors ?? "—"}</div>
          <span className="stat-tile-sub">
            {total ? `${total.error_rate}% of everything answered` : "…"}
          </span>
        </div>
        <div className="stat-tile">
          <span className="stat-tile-label">BUSIEST PROVIDER</span>
          <div className="stat-tile-value !text-[22px]">
            {busiest ? busiest[0].toUpperCase() : "—"}
          </div>
          <span className="stat-tile-sub">
            {busiest ? `${busiest[1].calls} calls · ${count(busiest[1].tokens_out)} tokens` : "…"}
          </span>
        </div>
        <div className="stat-tile">
          <span className="stat-tile-label">LATENCY p50 / p95</span>
          <div className="stat-tile-value !text-[26px]">
            {dur(total?.p50_ms)} <span className="text-pixel-gray">/</span> {dur(total?.p95_ms)}
          </div>
          <span className="stat-tile-sub">every route, not just generations</span>
        </div>
      </div>

      {board === "providers" && <Providers table={table} stats={stats} hours={hours} />}
      {board === "calls" && (
        <Calls
          page={page}
          stats={stats}
          owner={!!session?.owner}
          filters={{ provider, via, model, failed, runsOnly }}
          set={{ setProvider, setVia, setModel, setFailed, setRunsOnly }}
          onClear={async () => {
            if (!confirm("Drop every recorded call? This can't be undone.")) return;
            await clearCalls().catch((e) => setErr(String(e)));
            pull();
          }}
        />
      )}
      {board === "mcp" && <Mcp mcp={mcp} stats={stats} />}
    </div>
  );
}

// ── PROVIDERS ────────────────────────────────────────────────────────

function Providers({ table, stats, hours }: {
  table: ProvidersTable | null; stats: CallStats | null; hours: number;
}) {
  if (!table) {
    return <div className="pixel-panel p-6 text-center font-mono text-pixel-gray">
      asking every provider whether it works…
    </div>;
  }
  const busiest = Math.max(
    1, ...table.providers.map((p) => p.traffic?.calls ?? 0),
    table.self.traffic?.calls ?? 0);

  return (
    <div className="flex flex-col gap-2">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-2">
        {table.providers.map((p) => (
          <ProviderCard key={p.id} p={p} busiest={busiest} hours={hours} />
        ))}
      </div>

      <div className="pixel-panel p-3 flex flex-col gap-2">
        <div className="flex items-baseline gap-2 flex-wrap">
          <h2 className="font-display text-xs">WHERE THE CALLS COME FROM</h2>
          <span className="font-mono text-sm text-pixel-gray-light">
            one module, four front doors — the same handlers behind each
          </span>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2">
          {Object.entries(stats?.via ?? {}).map(([name, seen]) => (
            <div key={name} className="pixel-panel p-2">
              <div className="font-display text-[11px] text-pixel-gray-light">
                {name.toUpperCase()}
              </div>
              <div className="font-mono text-xl">{seen.calls}</div>
              <div className="font-mono text-xs text-pixel-gray">
                {VIA_NOTE[name] ?? "—"}
              </div>
            </div>
          ))}
          {!Object.keys(stats?.via ?? {}).length && (
            <div className="font-mono text-sm text-pixel-gray">
              nothing has called this module yet
            </div>
          )}
        </div>
      </div>

      <div className="pixel-panel overflow-x-auto">
        <table className="pixel-table pixel-table-auto w-full">
          <thead>
            <tr>
              <th className="text-left">MODEL</th>
              <th className="text-left">RAN ON</th>
              <th className="text-right">RUNS</th>
              <th className="text-right hidden sm:table-cell">TOKENS IN</th>
              <th className="text-right">TOKENS OUT</th>
              <th className="text-right hidden md:table-cell">p50</th>
              <th className="text-right hidden md:table-cell">ERRORS</th>
              <th className="text-right">LAST</th>
            </tr>
          </thead>
          <tbody>
            {(stats?.models ?? []).map((m) => (
              <tr key={m.model}>
                <td className="font-mono">{m.model}</td>
                <td>
                  <span className={`pixel-badge ${TONE[m.provider] ?? TONE.liquidai}`}>
                    {m.provider}
                  </span>
                </td>
                <td className="text-right font-mono">{m.calls}</td>
                <td className="text-right font-mono hidden sm:table-cell">{count(m.tokens_in)}</td>
                <td className="text-right font-mono">{count(m.tokens_out)}</td>
                <td className="text-right font-mono hidden md:table-cell">{dur(m.p50_ms)}</td>
                <td className="text-right font-mono hidden md:table-cell">
                  {m.errors ? <span className="text-red-400">{m.errors}</span> : "—"}
                </td>
                <td className="text-right font-mono text-pixel-gray-light">{ago(m.last_at)}</td>
              </tr>
            ))}
            {!(stats?.models ?? []).length && (
              <tr>
                <td colSpan={8} className="text-center text-pixel-gray py-6 font-mono">
                  no model has been run in the last {hours}h
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ProviderCard({ p, busiest, hours }: {
  p: Provider; busiest: number; hours: number;
}) {
  const t = p.traffic ?? ({} as Traffic);
  const share = Math.round(100 * (t.calls ?? 0) / busiest);
  return (
    <div className={`pixel-panel p-3 flex flex-col gap-2 ${p.ok ? "" : "pixel-panel-red"}`}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className={`pixel-badge ${TONE[p.id] ?? TONE.liquidai}`}>{p.label}</span>
        <span className={`pixel-badge ${p.ok ? "text-green-400 border-green-400"
          : "text-red-400 border-red-400"}`}>
          {p.ok ? "● " : "○ "}{p.state}
        </span>
        {!p.measured_here && (
          <span className="pixel-badge text-pixel-gray-light border-pixel-border"
                title="this server never sees these runs — the tab reports them afterwards">
            REPORTED
          </span>
        )}
        <span className="font-mono text-sm text-pixel-gray-light ml-auto">{p.where}</span>
      </div>

      <div className="font-mono text-sm text-pixel-gray-light">{p.engine}</div>
      <div className="font-mono text-sm">{p.detail}</div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 font-mono text-sm">
        <Fact label="CALLS" value={String(t.calls ?? 0)} sub={`last ${hours}h`} />
        <Fact label="TOKENS" value={count(t.tokens_out ?? 0)} sub="out" />
        <Fact label="p95" value={dur(t.p95_ms)} sub={`p50 ${dur(t.p50_ms)}`} />
        <Fact label="LAST CALL" value={ago(t.last_at)}
              sub={t.errors ? `${t.errors} failed` : "no failures"} />
      </div>

      <div className="pixel-bar" title={`${share}% of the busiest provider's traffic`}>
        <div className="pixel-bar-fill"
             style={{ width: `${share}%`, background: BAR[p.id] ?? "var(--border-strong)" }} />
      </div>

      <div className="flex flex-wrap items-center gap-2 font-mono text-xs text-pixel-gray-light">
        <span title="who pays for a run here">💲{p.cost}</span>
        {p.base && <span className="text-pixel-gray">{p.base}</span>}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {p.auth.needed ? (
          <span className={`pixel-badge ${p.auth.set
            ? "text-green-400 border-green-400" : "text-amber-400 border-amber-400"}`}>
            {p.auth.set ? `KEY SET${p.auth.masked ? ` · ${p.auth.masked}` : ""}` : "NO KEY"}
          </span>
        ) : (
          <span className="pixel-badge text-pixel-gray-light border-pixel-border">NO KEY NEEDED</span>
        )}
        {p.auth.kind && (
          <span className="font-mono text-xs text-pixel-gray">
            {p.auth.kind}{p.auth.source ? ` · from ${p.auth.source}` : ""}
          </span>
        )}
        {typeof p.models === "number" && (
          <span className="font-mono text-xs text-pixel-gray-light ml-auto">
            {p.models} models reachable
          </span>
        )}
      </div>

      {(p.resident || p.disk) && (
        <div className="font-mono text-xs text-pixel-gray-light">
          {p.resident ? `resident: ${p.resident.repo}` : "nothing resident"}
          {p.disk ? ` · ${p.disk.repos} repos on disk · ${bytes(p.disk.bytes)}` : ""}
        </div>
      )}
      {p.auth.hint && (
        <div className="font-mono text-xs text-amber-400">{p.auth.hint}</div>
      )}
    </div>
  );
}

function Fact({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div>
      <div className="font-display text-[10px] text-pixel-gray tracking-widest">{label}</div>
      <div className="text-lg">{value}</div>
      {sub && <div className="text-xs text-pixel-gray">{sub}</div>}
    </div>
  );
}

// ── CALLS ────────────────────────────────────────────────────────────

function Calls({ page, stats, owner, filters, set, onClear }: {
  page: CallPage | null;
  stats: CallStats | null;
  owner: boolean;
  filters: { provider: string; via: string; model: string; failed: boolean; runsOnly: boolean };
  set: {
    setProvider: (v: string) => void; setVia: (v: string) => void;
    setModel: (v: string) => void; setFailed: (v: boolean) => void;
    setRunsOnly: (v: boolean) => void;
  };
  onClear: () => void;
}) {
  const peak = Math.max(1, ...(stats?.series ?? []).map((s) => s.calls));
  return (
    <div className="flex flex-col gap-2">
      {/* Calls per hour. A bar chart of one number, because the only question
          this strip answers is "was that spike a moment ago or an hour ago". */}
      {!!stats?.series.length && (
        <div className="pixel-panel p-3">
          <div className="flex items-baseline gap-2">
            <h2 className="font-display text-xs">CALLS PER HOUR</h2>
            <span className="font-mono text-sm text-pixel-gray-light">
              peak {peak} · newest on the right
            </span>
          </div>
          <div className="flex items-end gap-[2px] h-16 mt-2">
            {stats.series.map((s) => (
              <div
                key={s.hour}
                className="flex-1 min-w-[3px]"
                style={{
                  height: `${Math.max(2, (100 * s.calls) / peak)}%`,
                  background: s.errors ? "var(--neon-red)" : "var(--neon-cyan)",
                }}
                title={`${new Date(s.hour * 1000).toLocaleString()} — ${s.calls} calls, ${s.errors} failed, ${s.tokens} tokens`}
              />
            ))}
          </div>
        </div>
      )}

      <div className="pixel-panel p-2 flex flex-wrap items-center gap-1.5">
        <select value={filters.provider} onChange={(e) => set.setProvider(e.target.value)}
                className="pixel-input-sm topbar-ctl" aria-label="provider">
          <option value="">PROVIDER</option>
          {["browser", "server", "cloud", "huggingface", "liquidai"].map((p) => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>
        <select value={filters.via} onChange={(e) => set.setVia(e.target.value)}
                className="pixel-input-sm topbar-ctl" aria-label="came through">
          <option value="">VIA</option>
          {["console", "mcp", "openai", "cli", "api"].map((v) => (
            <option key={v} value={v}>{v}</option>
          ))}
        </select>
        <input value={filters.model} onChange={(e) => set.setModel(e.target.value)}
               placeholder="model…" aria-label="model"
               className="pixel-input-sm topbar-ctl font-mono w-[140px]" />
        <button onClick={() => set.setRunsOnly(!filters.runsOnly)}
                aria-pressed={filters.runsOnly}
                className={`pixel-btn topbar-ctl px-2.5 ${filters.runsOnly ? "nav-active" : ""}`}
                title="hide reads — show only calls that ran a model">
          RUNS ONLY
        </button>
        <button onClick={() => set.setFailed(!filters.failed)}
                aria-pressed={filters.failed}
                className={`pixel-btn topbar-ctl px-2.5 ${filters.failed ? "nav-active" : ""}`}>
          FAILED ONLY
        </button>
        <span className="font-mono text-sm text-pixel-gray-light ml-auto">
          {page ? `${page.count} matching · ${page.held} held · ${page.path}` : "…"}
        </span>
        {owner && (
          <button onClick={onClear} className="pixel-btn topbar-ctl px-2.5 text-red-400"
                  title="drop the ledger — owner only">
            CLEAR
          </button>
        )}
      </div>

      <div className="pixel-panel overflow-x-auto">
        <table className="pixel-table pixel-table-auto w-full">
          <thead className="sticky">
            <tr>
              <th className="text-left">TIME</th>
              <th className="text-left">CALL</th>
              <th className="text-left">VIA</th>
              <th className="text-left">PROVIDER</th>
              <th className="text-left">MODEL</th>
              <th className="text-left hidden lg:table-cell">CALLER</th>
              <th className="text-right hidden sm:table-cell">TOKENS</th>
              <th className="text-right">TOOK</th>
              <th className="text-right">RESULT</th>
            </tr>
          </thead>
          <tbody>
            {(page?.calls ?? []).map((c) => <CallRow key={c.id} c={c} />)}
            {!(page?.calls ?? []).length && (
              <tr>
                <td colSpan={9} className="text-center text-pixel-gray py-6 font-mono">
                  {page ? "no call matches those switches" : "reading the ledger…"}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-2">
        <div className="pixel-panel p-3">
          <h2 className="font-display text-xs mb-2">WHO IS CALLING</h2>
          <table className="pixel-table pixel-table-auto w-full">
            <thead>
              <tr>
                <th className="text-left">ACCOUNT</th>
                <th className="text-left">KIND</th>
                <th className="text-right">CALLS</th>
                <th className="text-right">TOKENS</th>
                <th className="text-right">LAST</th>
              </tr>
            </thead>
            <tbody>
              {(stats?.callers ?? []).map((c) => (
                <tr key={c.caller}>
                  <td className="font-mono text-sm">
                    {c.caller.length > 22 ? `${c.caller.slice(0, 12)}…${c.caller.slice(-6)}` : c.caller}
                  </td>
                  <td><span className="pixel-badge text-pixel-gray-light border-pixel-border">
                    {c.kind}
                  </span></td>
                  <td className="text-right font-mono">{c.calls}</td>
                  <td className="text-right font-mono">{count(c.tokens_out)}</td>
                  <td className="text-right font-mono text-pixel-gray-light">{ago(c.last_at)}</td>
                </tr>
              ))}
              {!(stats?.callers ?? []).length && (
                <tr><td colSpan={5} className="text-center text-pixel-gray py-4 font-mono">
                  nobody yet
                </td></tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="pixel-panel p-3 font-mono text-sm text-pixel-gray-light flex flex-col gap-2">
          <h2 className="font-display text-xs text-pixel-white">WHAT IS AND ISN&apos;T IN HERE</h2>
          <p>
            Every request this API answers is written to
            <span className="text-pixel-white"> ~/.mod/liquidai/calls.jsonl</span> — the route,
            which provider did the work, the model, who asked, how long it took and how many
            tokens came back.
          </p>
          <p>
            <span className="text-pixel-white">No prompt text, ever.</span> The ledger holds
            shapes, not contents: turn counts and token counts, nothing that says what anyone
            asked.
          </p>
          <p>
            Browser runs never touch this server, so the tab reports its own —
            those rows are marked <span className="text-pixel-white">REPORTED</span> rather
            than pretending this box measured them.
          </p>
          <p>
            A generation&apos;s line is written when the stream <em>ends</em>, so its tokens and
            speed are real. <span className="text-pixel-white">TOOK</span> is the whole request,
            loading the weights included; tok/s is over the generation alone.
          </p>
        </div>
      </div>
    </div>
  );
}

function CallRow({ c }: { c: Call }) {
  const tokens = c.completion_tokens ?? c.chunks;
  return (
    <tr>
      <td className="font-mono text-pixel-gray-light" title={new Date(c.at * 1000).toLocaleString()}>
        {clock(c.at)}
      </td>
      <td className="font-mono">
        {c.tool ? <span className="text-cyan-400">{c.tool}</span> : c.route}
        {c.cache && <span className="pixel-badge ml-2 text-pixel-gray-light border-pixel-border">CACHE</span>}
      </td>
      <td>
        <span className="pixel-badge text-pixel-gray-light border-pixel-border">{c.via}</span>
      </td>
      <td>
        <span className={`pixel-badge ${TONE[c.provider] ?? TONE.liquidai}`}>{c.provider}</span>
        {c.reported && <span className="pixel-badge ml-1 text-pixel-gray border-pixel-border"
                              title="reported by the tab that ran it">RPT</span>}
      </td>
      <td className="font-mono text-sm">{c.model ?? "—"}</td>
      <td className="font-mono text-sm hidden lg:table-cell text-pixel-gray-light">
        {c.caller === "anon" ? "anon"
          : c.caller.length > 18 ? `${c.caller.slice(0, 10)}…${c.caller.slice(-4)}` : c.caller}
      </td>
      <td className="text-right font-mono hidden sm:table-cell">
        {tokens ? (
          <span title={`${c.prompt_tokens ?? "?"} in · ${tokens} out${
            c.tok_per_sec ? ` · ${c.tok_per_sec} tok/s` : ""}`}>
            {c.prompt_tokens ? `${c.prompt_tokens}→` : ""}{tokens}
          </span>
        ) : "—"}
      </td>
      <td className="text-right font-mono"
          title={c.setup_sec ? `${c.setup_sec}s of it was loading the model` : undefined}>
        {dur(c.ms)}
      </td>
      <td className="text-right">
        {c.ok ? (
          <span className="pixel-badge text-green-400 border-green-400">{c.status}</span>
        ) : (
          <span className="pixel-badge text-red-400 border-red-400" title={c.error}>
            {c.status}
          </span>
        )}
      </td>
    </tr>
  );
}

// ── MCP ──────────────────────────────────────────────────────────────

function Mcp({ mcp, stats }: { mcp: McpDescriptor | null; stats: CallStats | null }) {
  const [copied, setCopied] = useState(false);
  // The endpoint the *browser* would use is the one worth showing: it's the
  // URL this page was loaded from, which is also the one a client on this
  // network can reach. The API's own view of itself is localhost, which is
  // useless to paste anywhere else.
  const endpoint = typeof window !== "undefined"
    ? `${window.location.origin}/api/liquidai/mcp`
    : mcp?.endpoint ?? "";
  const config = JSON.stringify({
    mcpServers: {
      liquidai: { type: "http", url: endpoint,
                  headers: { Authorization: "Bearer <your liquidai session token>" } },
    },
  }, null, 2);

  if (!mcp) {
    return <div className="pixel-panel p-6 text-center font-mono text-pixel-gray">
      asking the MCP server what it can do…
    </div>;
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="pixel-panel p-3 flex flex-col gap-2">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="pixel-badge text-cyan-400 border-cyan-400">MCP</span>
          <h2 className="font-display text-xs">{mcp.server.title}</h2>
          <span className="font-mono text-sm text-pixel-gray-light">
            protocol {mcp.protocol} · {mcp.transport} · {mcp.tools.length} tools
          </span>
          <span className="font-mono text-sm text-pixel-gray-light ml-auto">
            {stats?.via?.mcp ? `${stats.via.mcp.calls} calls from MCP clients` : "no MCP client yet"}
          </span>
        </div>
        <p className="font-mono text-sm text-pixel-gray-light">
          Every tool below is the same handler the REST route uses — one gate, one
          ledger line, no second implementation to drift. Reads are open; running a
          model needs a session token; weights and the key vault need the owner.
        </p>
        <div className="flex items-center gap-2 flex-wrap">
          <code className="font-mono text-sm text-cyan-400 break-all">POST {endpoint}</code>
          <button
            onClick={() => {
              navigator.clipboard?.writeText(config).then(() => {
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              }).catch(() => {});
            }}
            className="pixel-btn topbar-ctl px-2.5 ml-auto"
          >
            {copied ? "COPIED" : "COPY CLIENT CONFIG"}
          </button>
        </div>
        <pre className="pixel-panel p-2 font-mono text-xs overflow-x-auto text-pixel-gray-light">
{config}
        </pre>
      </div>

      <div className="pixel-panel overflow-x-auto">
        <table className="pixel-table pixel-table-auto w-full">
          <thead>
            <tr>
              <th className="text-left">TOOL</th>
              <th className="text-left">NEEDS</th>
              <th className="text-left">DOES</th>
            </tr>
          </thead>
          <tbody>
            {mcp.tools.map((t) => (
              <tr key={t.name}>
                <td className="font-mono text-cyan-400">{t.name}</td>
                <td>
                  <span className={`pixel-badge ${
                    t.need === "owner" ? "text-amber-400 border-amber-400"
                      : t.need === "session" ? "text-purple-400 border-purple-400"
                      : "text-green-400 border-green-400"}`}>
                    {t.need === "open" ? "anyone" : t.need}
                  </span>
                </td>
                <td className="stack font-mono text-sm text-pixel-gray-light">{t.description}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
