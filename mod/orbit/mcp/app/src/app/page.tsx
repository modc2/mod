"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  getToken,
  setToken,
  ActivityLog,
  ApiKey,
  CallEntry,
  Candidate,
  Finding,
  Hub,
  HubsView,
  Listing,
  Me,
  PageText,
  Review,
  SearchResult,
  Server,
  Stats,
  Tool,
  WebProvider,
} from "../lib/api";

/// Where a server row came from, in one line, for the stats badges.
const SOURCE_HINT: Record<string, string> = {
  fleet: "a mod on this host that declares an MCP endpoint in its config.json",
  sweep: "a mod caught serving MCP by the port scan, without declaring it",
  user: "registered by hand or connected from a public directory",
};

/// Badge text for a server: anything running on this box reads "local", the
/// tooltip keeps how it was found (declared, swept, or user-registered).
function sourceLabel(s: { source: string; local?: boolean }): { text: string; hint: string } {
  const hint = SOURCE_HINT[s.source] || "";
  return s.local ? { text: "local", hint: hint ? `local — ${hint}` : "runs on this host" } : { text: s.source, hint };
}

function ago(unix: number): string {
  if (!unix) return "never";
  const s = Math.max(0, Math.floor(Date.now() / 1000) - unix);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function Copy({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      className="sm ghost"
      onClick={() => {
        navigator.clipboard?.writeText(text).then(() => {
          setDone(true);
          setTimeout(() => setDone(false), 1200);
        });
      }}
    >
      {done ? "copied" : "copy"}
    </button>
  );
}

/* ── tool runner ──────────────────────────────────────────────────── */

type PropSchema = { type?: string; description?: string };

function SchemaForm({
  properties,
  required,
  values,
  onChange,
}: {
  properties: Record<string, PropSchema>;
  required: string[];
  values: Record<string, unknown>;
  onChange: (key: string, val: unknown) => void;
}) {
  return (
    <div className="col" style={{ gap: 6 }}>
      {Object.entries(properties).map(([key, prop]) => {
        const isRequired = required.includes(key);
        const type = prop.type || "string";
        const label = (
          <span className="small">
            {isRequired && <span style={{ color: "var(--bad-ink)" }}>* </span>}
            <span className="mono">{key}</span>
            {prop.description && <span className="muted"> — {prop.description}</span>}
          </span>
        );

        if (type === "boolean") {
          return (
            <label key={key} className="row" style={{ gap: 6, alignItems: "center" }}>
              <input
                type="checkbox"
                checked={!!values[key]}
                onChange={(e) => onChange(key, e.target.checked)}
              />
              {label}
            </label>
          );
        }

        if (type === "number" || type === "integer") {
          return (
            <label key={key} className="col" style={{ gap: 2 }}>
              {label}
              <input
                type="number"
                value={values[key] == null ? "" : String(values[key])}
                onChange={(e) => {
                  if (e.target.value === "") { onChange(key, undefined); return; }
                  onChange(key, type === "integer" ? parseInt(e.target.value, 10) : parseFloat(e.target.value));
                }}
              />
            </label>
          );
        }

        if (type === "array" || type === "object") {
          const seed = type === "array" ? "[]" : "{}";
          return (
            <label key={key} className="col" style={{ gap: 2 }}>
              {label}
              <textarea
                style={{ minHeight: 40, fontFamily: "monospace", fontSize: 12 }}
                value={typeof values[key] === "string" ? (values[key] as string) : seed}
                onChange={(e) => onChange(key, e.target.value)}
              />
            </label>
          );
        }

        return (
          <label key={key} className="col" style={{ gap: 2 }}>
            {label}
            <input
              type="text"
              value={values[key] == null ? "" : String(values[key])}
              onChange={(e) => onChange(key, e.target.value)}
            />
          </label>
        );
      })}
    </div>
  );
}

function ToolRow({ tool, onSelectServer }: { tool: Tool; onSelectServer?: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const [raw, setRaw] = useState(false);
  const [args, setArgs] = useState("{}");
  const [formValues, setFormValues] = useState<Record<string, unknown>>({});
  const [out, setOut] = useState<{ display: string; raw: string } | null>(null);
  const [showRaw, setShowRaw] = useState(false);
  const [busy, setBusy] = useState(false);
  const [curlCopied, setCurlCopied] = useState(false);

  const schemaProps = tool.inputSchema?.properties as Record<string, PropSchema> | undefined;
  const schemaRequired = (tool.inputSchema?.required as string[] | undefined) || [];
  const hasSchema = !!schemaProps && Object.keys(schemaProps).length > 0;

  const buildFromForm = (): { ok: true; val: Record<string, unknown> } | { ok: false; err: string } => {
    const obj: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(formValues)) {
      if (v === undefined) continue;
      const t = schemaProps?.[k]?.type;
      if ((t === "array" || t === "object") && typeof v === "string") {
        try {
          obj[k] = JSON.parse(v);
        } catch {
          return { ok: false, err: `field "${k}" must be valid JSON` };
        }
      } else {
        obj[k] = v;
      }
    }
    return { ok: true, val: obj };
  };

  const run = async () => {
    let parsed: unknown;
    if (hasSchema && !raw) {
      const r = buildFromForm();
      if (!r.ok) { setOut({ display: r.err, raw: r.err }); return; }
      parsed = r.val;
    } else {
      try {
        parsed = JSON.parse(args || "{}");
      } catch {
        setOut({ display: "args must be valid JSON", raw: "args must be valid JSON" });
        return;
      }
    }
    setBusy(true);
    setOut(null);
    setShowRaw(false);
    try {
      const r = await api.call(tool.name, parsed);
      const rawStr = JSON.stringify(r.result, null, 2);
      const res = r.result as Record<string, unknown> | null | undefined;
      let displayStr = rawStr;
      if (res?.structuredContent != null) {
        displayStr = JSON.stringify(res.structuredContent, null, 2);
      } else if (
        Array.isArray(res?.content) &&
        (res!.content as unknown[]).every((item) => (item as { type?: string }).type === "text")
      ) {
        displayStr = (res!.content as { text: string }[]).map((item) => item.text).join("\n");
      }
      setOut({ display: displayStr, raw: rawStr });
    } catch (e) {
      const msg = String((e as Error).message || e);
      setOut({ display: msg, raw: msg });
    } finally {
      setBusy(false);
    }
  };

  const copyCurl = () => {
    let parsedArgs: unknown;
    if (hasSchema && !raw) {
      const r = buildFromForm();
      parsedArgs = r.ok ? r.val : {};
    } else {
      try { parsedArgs = JSON.parse(args || "{}"); } catch { parsedArgs = {}; }
    }
    const body = JSON.stringify({ tool: tool.name, args: parsedArgs });
    const token = getToken();
    const authHeader = token ? ` -H "Authorization: Bearer ${token}"` : "";
    const cmd = `curl -s -X POST "${window.location.origin}/mcp/api/call" -H "Content-Type: application/json"${authHeader} -d '${body}'`;
    navigator.clipboard?.writeText(cmd).then(() => {
      setCurlCopied(true);
      setTimeout(() => setCurlCopied(false), 1200);
    });
  };

  const switchToRaw = () => {
    const r = buildFromForm();
    setArgs(r.ok ? JSON.stringify(r.val, null, 2) : "{}");
    setRaw(true);
  };

  return (
    <div className="tool">
      <div
        className="row"
        style={{ cursor: "pointer", justifyContent: "space-between" }}
        onClick={() => setOpen(!open)}
      >
        <span className="row" style={{ gap: 6, alignItems: "center" }}>
          {onSelectServer && tool.name.includes("__") && (() => {
            const prefix = tool.name.split("__")[0];
            return (
              <button
                className="sm ghost"
                style={{ fontSize: 11, padding: "1px 6px" }}
                onClick={(e) => { e.stopPropagation(); onSelectServer(prefix); }}
              >
                {prefix}
              </button>
            );
          })()}
          <span className="name">{tool.name}</span>
        </span>
        <span className="muted small">{open ? "▾" : "▸"}</span>
      </div>
      {tool.description && <div className="d">{tool.description}</div>}
      {open && (
        <div className="col" style={{ marginTop: 8 }}>
          {hasSchema && !raw ? (
            <>
              <SchemaForm
                properties={schemaProps!}
                required={schemaRequired}
                values={formValues}
                onChange={(k, v) => setFormValues((prev) => ({ ...prev, [k]: v }))}
              />
              <div className="row" style={{ marginTop: 6 }}>
                <button className="sm ghost" style={{ fontSize: 11 }} onClick={switchToRaw}>
                  raw JSON
                </button>
                <div style={{ flex: 1 }} />
                <button className="sm ghost" disabled={busy} onClick={copyCurl}>
                  {curlCopied ? "copied!" : "curl"}
                </button>
                <button className="primary sm" disabled={busy} onClick={run}>
                  {busy ? "running…" : "run"}
                </button>
              </div>
            </>
          ) : (
            <>
              {tool.inputSchema && !hasSchema && (
                <pre className="code" style={{ maxHeight: 180 }}>
                  {JSON.stringify(tool.inputSchema, null, 2)}
                </pre>
              )}
              <div className="row">
                <textarea
                  style={{ flex: 1, minHeight: 56 }}
                  value={args}
                  onChange={(e) => setArgs(e.target.value)}
                  placeholder='{"arg": "value"}'
                />
                <button className="sm ghost" disabled={busy} onClick={copyCurl}>
                  {curlCopied ? "copied!" : "curl"}
                </button>
                <button className="primary sm" disabled={busy} onClick={run}>
                  {busy ? "running…" : "run"}
                </button>
              </div>
              {hasSchema && (
                <button className="sm ghost" style={{ alignSelf: "flex-start", fontSize: 11, marginTop: 4 }} onClick={() => setRaw(false)}>
                  form
                </button>
              )}
            </>
          )}
          {out !== null && (
            <div className="col" style={{ position: "relative" }}>
              <pre className="code" style={{ maxHeight: 280 }}>
                {showRaw ? out.raw : out.display}
              </pre>
              <div style={{ position: "absolute", top: 4, right: 4, display: "flex", gap: 4 }}>
                {out.display !== out.raw && (
                  <button className="sm ghost" onClick={() => setShowRaw(!showRaw)}>
                    {showRaw ? "text" : "raw"}
                  </button>
                )}
                <Copy text={showRaw ? out.raw : out.display} />
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ── security review section ──────────────────────────────────────── */

function riskColor(risk: string): string {
  if (risk === "low") return "var(--good)";
  if (risk === "medium") return "#f59e0b";
  if (risk === "high" || risk === "critical") return "var(--bad)";
  return "var(--ink-mute)";
}

function SecuritySection({ serverId, probeToolCount }: { serverId: string; probeToolCount: number }) {
  const [review, setReview] = useState<Review | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api.getReview(serverId).then(setReview).catch(() => {});
  }, [serverId]);

  const runReview = async () => {
    setBusy(true);
    setError("");
    try {
      setReview(await api.review(serverId));
    } catch (e) {
      setError(String((e as Error).message || e));
    } finally {
      setBusy(false);
    }
  };

  const stale = review != null && review.tool_count !== probeToolCount;

  return (
    <section>
      <h4>Security</h4>
      {review ? (
        <div>
          <div className="row" style={{ alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{ color: riskColor(review.risk), fontWeight: 700 }}>●</span>
            <span style={{ fontWeight: 600, textTransform: "uppercase", fontSize: 12, color: riskColor(review.risk) }}>
              {review.risk}
            </span>
            <span className="badge">{review.verdict}</span>
            <span className="muted small" style={{ flex: 1 }}>{review.summary}</span>
          </div>
          {stale && (
            <div className="note warn small" style={{ marginTop: 6 }}>
              tool count changed since this review — re-run to refresh
            </div>
          )}
          {review.findings.length > 0 && (
            <div className="col" style={{ marginTop: 8, gap: 4 }}>
              {review.findings.map((f: Finding, i: number) => (
                <div key={i} className="tool">
                  <div className="row" style={{ gap: 6, alignItems: "center" }}>
                    <span style={{ color: riskColor(f.severity), fontWeight: 600, textTransform: "uppercase", fontSize: 11, minWidth: 52 }}>
                      {f.severity}
                    </span>
                    <span>{f.title}</span>
                    {f.tool && <span className="mono small muted">{f.tool}</span>}
                  </div>
                  {f.detail && <div className="d muted small" style={{ marginTop: 2 }}>{f.detail}</div>}
                </div>
              ))}
            </div>
          )}
        </div>
      ) : (
        <div className="muted small">No review yet.</div>
      )}
      {error && <div className="note bad small" style={{ marginTop: 6 }}>{error}</div>}
      <div style={{ marginTop: 8 }}>
        <button className="sm" disabled={busy} onClick={runReview}>
          {busy ? "reviewing…" : review ? "re-run review" : "run review"}
        </button>
      </div>
    </section>
  );
}

/* ── per-server recent calls ──────────────────────────────────────── */

function RecentCallsSection({ serverId }: { serverId: string }) {
  const [entries, setEntries] = useState<CallEntry[]>([]);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    api.activityForServer(serverId)
      .then((r) => setEntries(r.recent.slice(0, 20)))
      .catch(() => {});
  }, [serverId]);

  useEffect(() => {
    const stop = api.streamActivity((entry) => {
      if (entry.server !== serverId) return;
      setEntries((prev) => [entry, ...prev].slice(0, 20));
    });
    return stop;
  }, [serverId]);

  return (
    <section>
      <h4 style={{ cursor: "pointer", userSelect: "none" }} onClick={() => setOpen(!open)}>
        calls {open ? "▾" : "▸"} {entries.length}
      </h4>
      {open && (
        <div className="toollist">
          {entries.length === 0 ? (
            <div className="muted small">no calls recorded yet</div>
          ) : (
            entries.map((e, i) => {
              const sep = e.tool.indexOf("__");
              const tname = sep > -1 ? e.tool.slice(sep + 2) : e.tool;
              return (
                <div key={i} className="tool" title={e.error || undefined}>
                  <div className="row" style={{ gap: 8, flexWrap: "nowrap", alignItems: "center" }}>
                    <span className="muted small mono" style={{ minWidth: 52, flexShrink: 0 }}>{ago(e.ts)}</span>
                    <span className="mono small" style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {tname}
                    </span>
                    <span style={{ color: e.ok ? "var(--good)" : "var(--bad)", flexShrink: 0, fontWeight: 600 }}>
                      {e.ok ? "✓" : "✗"}
                    </span>
                    <span className="muted small" style={{ minWidth: 48, textAlign: "right", flexShrink: 0 }}>
                      {e.latency_ms}ms
                    </span>
                  </div>
                </div>
              );
            })
          )}
        </div>
      )}
    </section>
  );
}

/* ── server detail panel ──────────────────────────────────────────── */

function ServerPanel({
  server,
  onClose,
  onChanged,
  onCompare,
}: {
  server: Server;
  onClose: () => void;
  onChanged: () => void;
  onCompare?: () => void;
}) {
  const [tools, setTools] = useState<Tool[]>([]);
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState(server.name);
  const [editNote, setEditNote] = useState(server.note);
  const [editHeaders, setEditHeaders] = useState("");
  const p = server.probe;

  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  useEffect(() => {
    api
      .tools(server.id)
      .then((r) => setTools(r.tools))
      .catch(() => setTools([]));
  }, [server.id, p.checked_at]);

  const act = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label);
    setError("");
    try {
      await fn();
      onChanged();
    } catch (e) {
      setError(String((e as Error).message || e));
    } finally {
      setBusy("");
    }
  };

  const saveEdit = async () => {
    let parsedHeaders: Record<string, string> | null = {};
    if (editHeaders.trim()) {
      try {
        const o = JSON.parse(editHeaders);
        parsedHeaders = typeof o === "object" && o ? (o as Record<string, string>) : null;
      } catch {
        parsedHeaders = null;
      }
    }
    if (parsedHeaders === null) {
      setError("headers must be a JSON object");
      return;
    }
    await act("save", () =>
      api.updateServer(server.id, {
        name: editName.trim() || undefined,
        note: editNote,
        headers: parsedHeaders!,
      })
    );
    setEditing(false);
  };

  const shown = filter
    ? tools.filter((t) => (t.name + " " + (t.description || "")).toLowerCase().includes(filter.toLowerCase()))
    : tools;

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <div className="panel">
        <div className="panel-head">
          <h2>
            <span style={{ color: p.ok ? "var(--good)" : "var(--bad)" }}>●</span> {server.name}
          </h2>
          <span
            className={`badge ${server.local || server.source === "fleet" ? "" : "plain"}`}
            style={{ ["--hue" as string]: server.local || server.source === "fleet" ? "var(--src-fleet)" : "var(--accent)" }}
            title={sourceLabel(server).hint}
          >
            {sourceLabel(server).text}
          </span>
          <div className="spacer" />
          {server.source === "user" && !editing && (
            <button
              className="sm ghost"
              onClick={() => {
                setEditName(server.name);
                setEditNote(server.note);
                setEditHeaders(server.auth_headers.length ? "" : "");
                setEditing(true);
              }}
            >
              edit
            </button>
          )}
          <button className="sm ghost" onClick={onClose}>
            esc
          </button>
        </div>
        {!editing && server.note && <p className="muted small">{server.note}</p>}

        <section>
          <h4>Server</h4>
          {editing ? (
            <div className="col" style={{ gap: 8 }}>
              <label className="col" style={{ gap: 2 }}>
                <span className="small">display name</span>
                <input value={editName} onChange={(e) => setEditName(e.target.value)} />
              </label>
              <label className="col" style={{ gap: 2 }}>
                <span className="small">note</span>
                <input value={editNote} onChange={(e) => setEditNote(e.target.value)} placeholder="what this server is for" />
              </label>
              <label className="col" style={{ gap: 2 }}>
                <span className="small">
                  headers <span className="muted">JSON — sent on every request (e.g. Authorization)</span>
                </span>
                <textarea
                  value={editHeaders}
                  onChange={(e) => setEditHeaders(e.target.value)}
                  placeholder='{"Authorization": "Bearer …"}'
                  style={{ minHeight: 52, fontFamily: "monospace", fontSize: 12 }}
                />
              </label>
              <div className="row" style={{ gap: 6 }}>
                <button className="primary sm" disabled={busy === "save"} onClick={saveEdit}>
                  {busy === "save" ? "saving…" : "save"}
                </button>
                <button className="sm ghost" onClick={() => setEditing(false)}>
                  cancel
                </button>
              </div>
            </div>
          ) : (
            <dl className="kv">
              <dt>endpoint</dt>
              <dd className="mono small">{server.url}</dd>
              <dt>status</dt>
              <dd>{p.ok ? `up · ${p.toolCount} tools · ${p.latency_ms}ms` : `down — ${p.error || "unprobed"}`}</dd>
              <dt>server info</dt>
              <dd className="mono small">
                {p.serverInfo?.name || "?"} {p.serverInfo?.version || ""} · MCP {p.protocolVersion || "?"}
              </dd>
              <dt>checked</dt>
              <dd>{ago(p.checked_at)}</dd>
              {server.auth_headers.length > 0 && (
                <>
                  <dt>auth headers</dt>
                  <dd className="mono small">{server.auth_headers.join(", ")}</dd>
                </>
              )}
            </dl>
          )}
        </section>

        <section className="row">
          <button className="sm" disabled={!!busy} onClick={() => act("refresh", () => api.refresh(server.id))}>
            {busy === "refresh" ? "probing…" : "re-probe"}
          </button>
          <button
            className="sm"
            disabled={!!busy}
            onClick={() => act("toggle", () => api.toggle(server.id, !server.enabled))}
          >
            {server.enabled ? "disable" : "enable"}
          </button>
          {onCompare && (
            <button className="sm" disabled={!!busy} onClick={onCompare}>
              compare
            </button>
          )}
          <button
            className="sm danger"
            disabled={!!busy}
            onClick={() => act("remove", () => api.removeServer(server.id))}
          >
            {server.source === "user" ? "delete" : "disable (fleet)"}
          </button>
        </section>
        {error && <div className="note bad" style={{ marginTop: 10 }}>{error}</div>}

        <SecuritySection serverId={server.id} probeToolCount={p.toolCount} />

        <RecentCallsSection serverId={server.id} />

        <section>
          <h4>
            Tools · {tools.length} <span className="muted">(callable as {server.id}__*)</span>
          </h4>
          {tools.length > 8 && (
            <input
              type="search"
              placeholder="filter tools…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              style={{ width: "100%", marginBottom: 8 }}
            />
          )}
          <div className="toollist">
            {shown.map((t) => (
              <ToolRow key={t.name} tool={t} />
            ))}
            {tools.length === 0 && <div className="muted small">no tools probed{p.error ? ` — ${p.error}` : ""}</div>}
          </div>
        </section>
      </div>
    </>
  );
}

/* ── compare sheet ────────────────────────────────────────────────── */

function toolBaseName(t: Tool): string {
  const sep = t.name.indexOf("__");
  return sep > -1 ? t.name.slice(sep + 2) : t.name;
}

function CompareSheet({
  serverA,
  servers,
  onClose,
}: {
  serverA: Server;
  servers: Server[];
  onClose: () => void;
}) {
  const [serverBId, setServerBId] = useState("");
  const [toolsA, setToolsA] = useState<Tool[] | null>(null);
  const [toolsB, setToolsB] = useState<Tool[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  const serverB = useMemo(
    () => servers.find((s) => s.id === serverBId) || null,
    [servers, serverBId]
  );

  useEffect(() => {
    if (!serverBId) {
      setToolsA(null);
      setToolsB(null);
      return;
    }
    setBusy(true);
    setError("");
    Promise.all([api.tools(serverA.id), api.tools(serverBId)])
      .then(([ra, rb]) => {
        setToolsA(ra.tools);
        setToolsB(rb.tools);
      })
      .catch((e) => setError(String((e as Error).message || e)))
      .finally(() => setBusy(false));
  }, [serverA.id, serverBId]);

  const sharedNames = useMemo(() => {
    if (!toolsA || !toolsB) return new Set<string>();
    const setA = new Set(toolsA.map(toolBaseName));
    const setB = new Set(toolsB.map(toolBaseName));
    const shared = new Set<string>();
    for (const n of setA) {
      if (setB.has(n)) shared.add(n);
    }
    return shared;
  }, [toolsA, toolsB]);

  const uniqueA = toolsA ? toolsA.filter((t) => !sharedNames.has(toolBaseName(t))).length : 0;
  const uniqueB = toolsB ? toolsB.filter((t) => !sharedNames.has(toolBaseName(t))).length : 0;
  const sharedCount = sharedNames.size;

  const others = servers.filter((s) => s.id !== serverA.id);

  return (
    <div className="sheet" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="sheet-inner" style={{ width: "min(900px, 94vw)", maxWidth: "none" }}>
        <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <h2 style={{ margin: 0 }}>Compare tools</h2>
          <button className="sm ghost" onClick={onClose}>close</button>
        </div>
        <p className="muted small" style={{ margin: "6px 0 0" }}>
          Pick a second server to compare its tools with <strong>{serverA.name}</strong> side by side. Shared base names are highlighted.
        </p>

        <div className="row" style={{ marginTop: 12 }}>
          <select
            style={{ flex: 1 }}
            value={serverBId}
            onChange={(e) => setServerBId(e.target.value)}
          >
            <option value="">pick a server to compare…</option>
            {others.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name} — {s.probe.toolCount} tools
              </option>
            ))}
          </select>
        </div>

        {error && <div className="note bad" style={{ marginTop: 12 }}>{error}</div>}
        {busy && <div className="muted small" style={{ marginTop: 12 }}>loading tools…</div>}

        {toolsA && toolsB && serverB && (
          <>
            <div className="row" style={{ marginTop: 12, padding: "6px 10px", background: "var(--bg-card)", borderRadius: 6, fontSize: 13, gap: 0 }}>
              <span><strong>{uniqueA}</strong> unique to {serverA.name}</span>
              <span className="muted" style={{ margin: "0 8px" }}>·</span>
              <span><strong>{sharedCount}</strong> shared by base name</span>
              <span className="muted" style={{ margin: "0 8px" }}>·</span>
              <span><strong>{uniqueB}</strong> unique to {serverB.name}</span>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginTop: 12 }}>
              <div>
                <div className="row" style={{ gap: 6, alignItems: "center", marginBottom: 6 }}>
                  <span style={{ fontWeight: 600 }}>{serverA.name}</span>
                  <span
                    className="badge"
                    style={{ ["--hue" as string]: serverA.local || serverA.source === "fleet" ? "var(--src-fleet)" : "var(--accent)" }}
                  >
                    {sourceLabel(serverA).text}
                  </span>
                  <span className="badge plain">{toolsA.length} tools</span>
                </div>
                <div className="toollist">
                  {toolsA.map((t) => {
                    const base = toolBaseName(t);
                    const shared = sharedNames.has(base);
                    return (
                      <div key={t.name} className="tool" style={shared ? { background: "var(--line-soft)" } : {}}>
                        <div className="row" style={{ gap: 6, alignItems: "center", justifyContent: "space-between" }}>
                          <span className="name">{base}</span>
                          {shared && <span className="badge plain" style={{ fontSize: 10 }}>shared</span>}
                        </div>
                        {t.description && <div className="d">{t.description}</div>}
                      </div>
                    );
                  })}
                  {toolsA.length === 0 && <div className="muted small">no tools</div>}
                </div>
              </div>
              <div>
                <div className="row" style={{ gap: 6, alignItems: "center", marginBottom: 6 }}>
                  <span style={{ fontWeight: 600 }}>{serverB.name}</span>
                  <span
                    className="badge"
                    style={{ ["--hue" as string]: serverB.local || serverB.source === "fleet" ? "var(--src-fleet)" : "var(--accent)" }}
                  >
                    {sourceLabel(serverB).text}
                  </span>
                  <span className="badge plain">{toolsB.length} tools</span>
                </div>
                <div className="toollist">
                  {toolsB.map((t) => {
                    const base = toolBaseName(t);
                    const shared = sharedNames.has(base);
                    return (
                      <div key={t.name} className="tool" style={shared ? { background: "var(--line-soft)" } : {}}>
                        <div className="row" style={{ gap: 6, alignItems: "center", justifyContent: "space-between" }}>
                          <span className="name">{base}</span>
                          {shared && <span className="badge plain" style={{ fontSize: 10 }}>shared</span>}
                        </div>
                        {t.description && <div className="d">{t.description}</div>}
                      </div>
                    );
                  })}
                  {toolsB.length === 0 && <div className="muted small">no tools</div>}
                </div>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/* ── add-server sheet ─────────────────────────────────────────────── */

function AddSheet({
  onClose,
  onAdded,
  seed,
}: {
  onClose: () => void;
  onAdded: () => void;
  seed?: Listing | null;
}) {
  const [transport, setTransport] = useState<"url" | "stdio">("url");
  const [url, setUrl] = useState(seed?.url || "");
  const [command, setCommand] = useState("");
  const [stdioArgs, setStdioArgs] = useState("");
  const [envJson, setEnvJson] = useState("");
  const [id, setId] = useState(seed?.id || "");
  const [name, setName] = useState(seed?.name || "");
  const [note, setNote] = useState(seed?.description || "");
  const [headers, setHeaders] = useState("");
  const [token, setTok] = useState(getToken());
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<string>("");
  const [probeTools, setProbeTools] = useState<Tool[]>([]);
  const [paste, setPaste] = useState("");
  const [found, setFound] = useState<Candidate[] | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkProgress, setBulkProgress] = useState("");
  const [bulkResults, setBulkResults] = useState<{ id: string; ok: boolean; error?: string }[]>([]);

  const doParse = async () => {
    setBusy("parse");
    setError("");
    setFound(null);
    try {
      const r = await api.intake(paste);
      setFound(r.candidates);
      setWarnings(r.warnings || []);
      // One unambiguous answer needs no picking.
      if (r.candidates.length === 1) useCandidate(r.candidates[0]);
    } catch (e) {
      setError(String((e as Error).message || e));
    } finally {
      setBusy("");
    }
  };

  const useCandidate = (c: Candidate) => {
    setUrl(c.url);
    setId(c.id);
    setName(c.name);
    if (c.note) setNote(c.note);
    if (c.headers && Object.keys(c.headers).length) setHeaders(JSON.stringify(c.headers, null, 2));
  };

  const doAddAll = async () => {
    if (!found || found.length < 2) return;
    setBulkBusy(true);
    setBulkProgress("");
    setBulkResults([]);
    const results: { id: string; ok: boolean; error?: string }[] = [];
    for (let i = 0; i < found.length; i++) {
      const c = found[i];
      setBulkProgress(`adding ${i + 1} of ${found.length}: ${c.name || c.id}…`);
      try {
        await api.addServer({ url: c.url, id: c.id, name: c.name, headers: c.headers });
        results.push({ id: c.id, ok: true });
      } catch (e) {
        results.push({ id: c.id, ok: false, error: String((e as Error).message || e) });
      }
    }
    setBulkResults(results);
    const okCount = results.filter((r) => r.ok).length;
    const failed = results.filter((r) => !r.ok);
    setBulkProgress(
      failed.length
        ? `${okCount} of ${found.length} registered, ${failed.length} failed: ${failed.map((r) => r.id).join(", ")}`
        : `${okCount} of ${found.length} registered`
    );
    setBulkBusy(false);
    onAdded();
  };

  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  const parseHeaders = (): Record<string, string> | null => {
    if (!headers.trim()) return {};
    try {
      const o = JSON.parse(headers);
      return typeof o === "object" && o ? (o as Record<string, string>) : null;
    } catch {
      return null;
    }
  };

  const parseEnv = (): Record<string, string> | null => {
    if (!envJson.trim()) return {};
    try {
      const o = JSON.parse(envJson);
      return typeof o === "object" && o ? (o as Record<string, string>) : null;
    } catch {
      return null;
    }
  };

  const doProbe = async () => {
    setBusy("probe");
    setError("");
    setPreview("");
    setProbeTools([]);
    try {
      if (transport === "stdio") {
        const env = parseEnv();
        if (env === null) return setError("env must be a JSON object");
        const args = stdioArgs.trim() ? stdioArgs.trim().split(/\s+/) : [];
        const r = await api.probe({ command: command.trim(), args, env });
        setPreview(
          r.probe.ok
            ? `✓ ${r.probe.serverInfo?.name || "server"} — ${r.probe.toolCount} tools, ${r.probe.latency_ms}ms`
            : `✗ ${r.probe.error}`
        );
        if (r.probe.ok) setProbeTools(r.tools || []);
      } else {
        const h = parseHeaders();
        if (h === null) return setError("headers must be a JSON object");
        const r = await api.probe({ url: url.trim(), headers: h });
        setPreview(
          r.probe.ok
            ? `✓ ${r.probe.serverInfo?.name || "server"} — ${r.probe.toolCount} tools, ${r.probe.latency_ms}ms`
            : `✗ ${r.probe.error}`
        );
        if (r.probe.ok) setProbeTools(r.tools || []);
      }
    } catch (e) {
      setError(String((e as Error).message || e));
    } finally {
      setBusy("");
    }
  };

  const doAdd = async () => {
    setToken(token.trim());
    setBusy("add");
    setError("");
    try {
      if (transport === "stdio") {
        const env = parseEnv();
        if (env === null) return setError("env must be a JSON object");
        const args = stdioArgs.trim() ? stdioArgs.trim().split(/\s+/) : [];
        await api.addServer({
          command: command.trim(),
          args,
          env,
          id: id.trim() || undefined,
          name: name.trim() || undefined,
          note: note.trim() || undefined,
        });
      } else {
        const h = parseHeaders();
        if (h === null) return setError("headers must be a JSON object");
        await api.addServer({
          url: url.trim(),
          id: id.trim() || undefined,
          name: name.trim() || undefined,
          note: note.trim() || undefined,
          headers: h,
          pay: seed?.pay || undefined,
        });
      }
      onAdded();
      onClose();
    } catch (e) {
      setError(String((e as Error).message || e));
    } finally {
      setBusy("");
    }
  };

  const canSubmit = transport === "stdio" ? !!command.trim() : !!url.trim();

  return (
    <div className="sheet" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="sheet-inner">
        <h2>Add an MCP server</h2>
        <p className="muted small" style={{ margin: 0 }}>
          Any Streamable HTTP endpoint or stdio command. It's probed first — a server that won't shake hands isn't registered.
        </p>

        <div className="col" style={{ marginTop: 12 }}>
          <label className="field wide">
            paste anything <span className="hint">a URL, a client config, a `claude mcp add` line, a store CID</span>
            <textarea
              value={paste}
              onChange={(e) => setPaste(e.target.value)}
              placeholder={'{"mcpServers": {"github": {"url": "https://…/mcp"}}}'}
              style={{ minHeight: 52 }}
            />
          </label>
          <div className="row">
            <button className="sm" disabled={!paste.trim() || !!busy} onClick={doParse}>
              {busy === "parse" ? "reading…" : "read it"}
            </button>
            {found?.length === 0 && <span className="muted small">nothing registrable in there</span>}
          </div>
          {found && found.length > 1 && (
            <div className="col" style={{ gap: 6 }}>
              <div className="toollist">
                {found.map((c) => (
                  <div key={c.url} className="tool" style={{ cursor: "pointer" }} onClick={() => useCandidate(c)}>
                    <div className="row" style={{ justifyContent: "space-between" }}>
                      <span className="name">{c.id}</span>
                      <span className="badge plain">{c.kind}</span>
                    </div>
                    <div className="d">{c.url}</div>
                  </div>
                ))}
              </div>
              <div className="row" style={{ gap: 8, alignItems: "center" }}>
                <button className="sm primary" disabled={bulkBusy || !!busy} onClick={doAddAll}>
                  {bulkBusy ? bulkProgress : `register all ${found.length} servers`}
                </button>
                {!bulkBusy && bulkProgress && (
                  <span className={`small ${bulkResults.some((r) => !r.ok) ? "warn" : "muted"}`}>
                    {bulkProgress}
                  </span>
                )}
              </div>
            </div>
          )}
          {warnings.map((w) => (
            <div key={w} className="note warn small">
              {w}
            </div>
          ))}
        </div>

        <div className="row" style={{ gap: 6, marginTop: 8 }}>
          <button
            className={`sm${transport === "url" ? " primary" : " ghost"}`}
            onClick={() => setTransport("url")}
          >
            URL endpoint
          </button>
          <button
            className={`sm${transport === "stdio" ? " primary" : " ghost"}`}
            onClick={() => setTransport("stdio")}
          >
            stdio command
          </button>
        </div>

        <div className="form">
          {transport === "url" ? (
            <>
              <label className="field wide">
                endpoint url
                <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://host/mcp" />
              </label>
              <label className="field wide">
                headers <span className="hint">JSON, sent on every upstream request (e.g. Authorization)</span>
                <textarea
                  value={headers}
                  onChange={(e) => setHeaders(e.target.value)}
                  placeholder='{"Authorization": "Bearer …"}'
                  style={{ minHeight: 52 }}
                />
              </label>
            </>
          ) : (
            <>
              <label className="field wide">
                command <span className="hint">executable to launch, e.g. npx</span>
                <input
                  value={command}
                  onChange={(e) => setCommand(e.target.value)}
                  placeholder="npx"
                />
              </label>
              <label className="field wide">
                args <span className="hint">space-separated arguments</span>
                <input
                  value={stdioArgs}
                  onChange={(e) => setStdioArgs(e.target.value)}
                  placeholder="-y @modelcontextprotocol/server-filesystem /path"
                />
              </label>
              <label className="field wide">
                env <span className="hint">optional JSON object of environment variables</span>
                <textarea
                  value={envJson}
                  onChange={(e) => setEnvJson(e.target.value)}
                  placeholder='{"API_KEY": "…"}'
                  style={{ minHeight: 52 }}
                />
              </label>
            </>
          )}
          <label className="field">
            id <span className="hint">tool prefix; defaults to hostname</span>
            <input value={id} onChange={(e) => setId(e.target.value)} placeholder="myserver" />
          </label>
          <label className="field">
            display name
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="My Server" />
          </label>
          <label className="field wide">
            note
            <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="what this server is for" />
          </label>
          <label className="field wide">
            hub access token <span className="hint">only needed when the write gate is armed</span>
            <input
              type="password"
              value={token}
              onChange={(e) => setTok(e.target.value)}
              placeholder="~/.mod/mcp/server.secret"
            />
          </label>
        </div>
        {preview && <div className={`note ${preview.startsWith("✓") ? "good" : "warn"}`} style={{ marginTop: 12 }}>{preview}</div>}
        {probeTools.length > 0 && (
          <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", gap: 4 }}>
            {probeTools.slice(0, 8).map((t) => (
              <span key={t.name} className="mono" style={{ fontSize: 11, padding: "1px 5px", background: "var(--bg2, #f0f0f0)", borderRadius: 3 }}>{t.name}</span>
            ))}
            {probeTools.length > 8 && (
              <span style={{ fontSize: 11, opacity: 0.6 }}>… and {probeTools.length - 8} more</span>
            )}
          </div>
        )}
        {error && <div className="note bad" style={{ marginTop: 12 }}>{error}</div>}
        <div className="row" style={{ marginTop: 14, justifyContent: "flex-end" }}>
          <button disabled={!canSubmit || !!busy} onClick={doProbe}>
            {busy === "probe" ? "probing…" : "probe"}
          </button>
          <button className="primary" disabled={!canSubmit || !!busy} onClick={doAdd}>
            {busy === "add" ? "adding…" : "add to hub"}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ── tools search sheet ──────────────────────────────────────────── */

function ToolsSheet({ onClose, initialQ, onSelectServer }: { onClose: () => void; initialQ?: string; onSelectServer?: (id: string) => void }) {
  const [tools, setTools] = useState<Tool[]>([]);
  const [q, setQ] = useState(initialQ || "");
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  useEffect(() => {
    api
      .tools()
      .then((r) => {
        setTools(r.tools);
        setBusy(false);
      })
      .catch((e) => {
        setError(String((e as Error).message || e));
        setBusy(false);
      });
  }, []);

  const shown = q
    ? tools.filter((t) =>
        (t.name + " " + (t.description || "")).toLowerCase().includes(q.toLowerCase())
      )
    : tools;

  return (
    <div className="sheet" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="sheet-inner">
        <h2>Search tools</h2>
        <p className="muted small" style={{ margin: 0 }}>
          Every tool callable through this hub — across all connected servers. Names are namespaced as{" "}
          <code>server__tool</code>.
        </p>
        <div className="row" style={{ marginTop: 14 }}>
          <input
            autoFocus
            style={{ flex: 1 }}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="filter by name or description…"
          />
          {!busy && (
            <span className="muted small">
              {shown.length} / {tools.length}
            </span>
          )}
        </div>

        {error && <div className="note bad" style={{ marginTop: 12 }}>{error}</div>}
        {busy && <div className="muted small" style={{ marginTop: 12 }}>loading tools…</div>}

        {!busy && !error && (
          <div className="toollist" style={{ marginTop: 12 }}>
            {shown.map((t) => (
              <ToolRow key={t.name} tool={t} onSelectServer={onSelectServer} />
            ))}
            {shown.length === 0 && tools.length > 0 && (
              <div className="muted small">no tools matched that filter</div>
            )}
            {tools.length === 0 && (
              <div className="muted small">no tools found — are any servers connected and up?</div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/* ── activity feed ────────────────────────────────────────────────── */

function ActivitySheet({ onClose }: { onClose: () => void }) {
  const [log, setLog] = useState<ActivityLog | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  const refresh = useCallback(async () => {
    try {
      setLog(await api.activity());
      setError("");
    } catch (e) {
      setError(String((e as Error).message || e));
    }
  }, []);

  useEffect(() => {
    refresh();
    const stop = api.streamActivity((entry) => {
      setLog((prev) =>
        prev
          ? { total: prev.total + 1, recent: [entry, ...prev.recent].slice(0, 100) }
          : null
      );
    });
    return stop;
  }, [refresh]);

  return (
    <div className="sheet" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="sheet-inner">
        <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <h2>Call activity</h2>
          {log && <span className="muted small">{log.total.toLocaleString()} total calls</span>}
        </div>
        <p className="muted small" style={{ margin: "4px 0 0" }}>
          Every tool call through this hub — most recent first. Updates in real time.
        </p>

        {error && <div className="note bad" style={{ marginTop: 12 }}>{error}</div>}
        {!log && !error && <div className="muted small" style={{ marginTop: 12 }}>loading…</div>}

        {log && (
          <div className="toollist" style={{ marginTop: 12 }}>
            {log.recent.length === 0 && (
              <div className="muted small">no calls recorded yet — make a tool call to see it here</div>
            )}
            {log.recent.map((e: CallEntry, i: number) => {
              const sep = e.tool.indexOf("__");
              const srv = sep > -1 ? e.tool.slice(0, sep) : "hub";
              const tname = sep > -1 ? e.tool.slice(sep + 2) : e.tool;
              return (
                <div key={i} className="tool" title={e.error || undefined}>
                  <div className="row" style={{ gap: 8, flexWrap: "nowrap", alignItems: "center" }}>
                    <span className="muted small mono" style={{ minWidth: 52, flexShrink: 0 }}>{ago(e.ts)}</span>
                    <span className="mono small" style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      <span className="muted">{srv}</span>
                      {sep > -1 && <span className="muted">__</span>}
                      {tname}
                    </span>
                    <span style={{ color: e.ok ? "var(--good)" : "var(--bad)", flexShrink: 0, fontWeight: 600 }}>
                      {e.ok ? "✓" : "✗"}
                    </span>
                    <span className="muted small" style={{ minWidth: 48, textAlign: "right", flexShrink: 0 }}>
                      {e.latency_ms}ms
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

/* ── top tools leaderboard ────────────────────────────────────────── */

function TopToolsPanel({
  recent,
  open,
  onToggle,
  onOpenTools,
  onOpenServer,
}: {
  recent: CallEntry[];
  open: boolean;
  onToggle: () => void;
  onOpenTools: (q: string) => void;
  onOpenServer: (id: string) => void;
}) {
  const top = useMemo(() => {
    const byTool = new Map<string, { count: number; errors: number; latencySum: number }>();
    for (const e of recent) {
      const prev = byTool.get(e.tool) || { count: 0, errors: 0, latencySum: 0 };
      byTool.set(e.tool, {
        count: prev.count + 1,
        errors: prev.errors + (e.ok ? 0 : 1),
        latencySum: prev.latencySum + e.latency_ms,
      });
    }
    return [...byTool.entries()]
      .sort((a, b) => b[1].count - a[1].count)
      .slice(0, 10)
      .map(([tool, s]) => {
        const sep = tool.indexOf("__");
        const server = sep > -1 ? tool.slice(0, sep) : "";
        const name = sep > -1 ? tool.slice(sep + 2) : tool;
        return { tool, server, name, count: s.count, errors: s.errors, avgMs: Math.round(s.latencySum / s.count) };
      });
  }, [recent]);

  if (top.length === 0) return null;

  return (
    <div style={{ marginTop: 14, border: "1px solid var(--line)", borderRadius: 8, overflow: "hidden" }}>
      <button
        className="ghost"
        style={{
          width: "100%", display: "flex", alignItems: "center", gap: 8,
          padding: "7px 12px", fontSize: 12.5, fontWeight: 550,
          borderRadius: 0, background: "var(--bg-card)",
        }}
        onClick={onToggle}
      >
        <span style={{ flex: 1, textAlign: "left", color: "var(--ink-dim)" }}>top tools</span>
        <span style={{ color: "var(--ink-faint)", fontSize: 11 }}>{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div style={{ borderTop: "1px solid var(--line-soft)" }}>
          {top.map((row) => (
            <div
              key={row.tool}
              className="row"
              style={{
                padding: "5px 12px", gap: 8, cursor: "pointer", flexWrap: "nowrap",
                borderBottom: "1px solid var(--line-soft)",
              }}
              onClick={() => onOpenTools(row.name)}
            >
              <span className="mono small" style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {row.name}
              </span>
              {row.server && (
                <button
                  className="badge plain"
                  style={{ fontSize: 10.5, padding: "1px 5px", cursor: "pointer", flexShrink: 0 }}
                  onClick={(e) => { e.stopPropagation(); onOpenServer(row.server); }}
                >
                  {row.server}
                </button>
              )}
              <span className="badge" style={{ fontSize: 10.5, padding: "1px 5px", flexShrink: 0 }}>
                {row.count}
              </span>
              <span style={{ color: row.errors === 0 ? "var(--good)" : "var(--bad)", fontSize: 12, flexShrink: 0 }}>●</span>
              <span className="muted small" style={{ minWidth: 46, textAlign: "right", flexShrink: 0 }}>
                {row.avgMs}ms
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ── the web ──────────────────────────────────────────────────────── */

function WebSheet({ onClose, providers }: { onClose: () => void; providers: WebProvider[] }) {
  const [q, setQ] = useState("");
  const [provider, setProvider] = useState("");
  const [res, setRes] = useState<SearchResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [reading, setReading] = useState<string>("");
  const [page, setPage] = useState<PageText | null>(null);

  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  const run = async () => {
    if (!q.trim()) return;
    setBusy(true);
    setError("");
    setPage(null);
    try {
      setRes(await api.search(q.trim(), 8, provider || undefined));
    } catch (e) {
      setError(String((e as Error).message || e));
      setRes(null);
    } finally {
      setBusy(false);
    }
  };

  /// What the pinned provider is, in its own words — duckduckgo answers
  /// "what is X" and nothing else, which is worth knowing before you pin it.
  const pinned = providers.find((p) => p.name === provider);

  const read = async (url: string) => {
    setReading(url);
    setPage(null);
    try {
      setPage(await api.readPage(url));
    } catch (e) {
      setError(String((e as Error).message || e));
    } finally {
      setReading("");
    }
  };

  return (
    <div className="sheet" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="sheet-inner">
        <h2>Search the web</h2>
        <p className="muted small" style={{ margin: 0 }}>
          The same <code>web_search</code> and <code>web_fetch</code> your MCP client gets. No key needed — a
          Brave, Tavily, Exa or Serper key in <code>~/.mod/mcp/web.json</code> is used first when there is one.
        </p>
        <div className="row" style={{ marginTop: 14 }}>
          <input
            autoFocus
            style={{ flex: 1 }}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && run()}
            placeholder="what do you want to know?"
          />
          <select value={provider} onChange={(e) => setProvider(e.target.value)}>
            <option value="">best available</option>
            {providers.map((p) => (
              <option key={p.name} value={p.name} disabled={!p.ready}>
                {p.name}
                {p.ready ? "" : " (no key)"}
              </option>
            ))}
          </select>
          <button className="primary" disabled={busy || !q.trim()} onClick={run}>
            {busy ? "searching…" : "search"}
          </button>
        </div>

        {pinned?.note && (
          <div className="muted small" style={{ marginTop: 6 }}>
            {pinned.name} — {pinned.note}
          </div>
        )}

        {error && <div className="note bad" style={{ marginTop: 12 }}>{error}</div>}

        {res?.error && (
          <div className="note warn" style={{ marginTop: 12 }}>
            <strong>{res.error}</strong>
            {/* One pinned provider already said its piece in the headline. */}
            {(res.tried?.length ?? 0) > 1 && (
              <div className="col" style={{ gap: 2, marginTop: 6 }}>
                {res.tried?.map((t) => (
                  <div key={t} className="muted small">
                    {t}
                  </div>
                ))}
              </div>
            )}
            {provider && (
              <div className="muted small" style={{ marginTop: 6 }}>
                Pinned to {provider} — clear the pin to let the whole chain try.
              </div>
            )}
          </div>
        )}

        {res && res.results.length > 0 && (
          <div className="col" style={{ marginTop: 14 }}>
            <div className="row">
              <span className="badge">{res.provider}</span>
              <span className="muted small">{res.results.length} results</span>
            </div>
            {res.results.map((h) => (
              <div key={h.url} className="tool">
                <div className="row" style={{ justifyContent: "space-between", flexWrap: "nowrap" }}>
                  <a href={h.url} target="_blank" rel="noreferrer" className="t">
                    {h.title || h.url}
                  </a>
                  <button className="sm ghost" disabled={!!reading} onClick={() => read(h.url)}>
                    {reading === h.url ? "reading…" : "read"}
                  </button>
                </div>
                <div className="d">{h.snippet}</div>
                <div className="src" style={{ marginTop: 4 }}>
                  {h.url}
                  {h.published ? ` · ${h.published}` : ""}
                </div>
              </div>
            ))}
          </div>
        )}

        {page && (
          <div className="col" style={{ marginTop: 14 }}>
            <h4>
              {page.title || page.url}{" "}
              <span className="muted small">
                · {page.chars.toLocaleString()} chars via {page.via}
              </span>
            </h4>
            <pre className="code" style={{ maxHeight: 320, whiteSpace: "pre-wrap" }}>
              {page.text}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}

/* ── the public directories ───────────────────────────────────────── */

function CatalogSheet({
  onClose,
  onConnect,
  known,
  initialRegistry = "all",
}: {
  onClose: () => void;
  onConnect: (l: Listing) => void;
  known: Set<string>;
  initialRegistry?: string;
}) {
  const [q, setQ] = useState("");
  const [registry, setRegistry] = useState(initialRegistry);
  const [hubs, setHubs] = useState<Hub[]>([]);

  useEffect(() => {
    api.hubs().then((v) => setHubs(v.hubs)).catch(() => {});
  }, []);
  const [rows, setRows] = useState<Listing[] | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const run = useCallback(
    async (query: string, reg: string) => {
      setBusy(true);
      setError("");
      try {
        const r = await api.catalog(query, reg, 24);
        setRows(r.listings);
        setErrors(r.errors || []);
      } catch (e) {
        setError(String((e as Error).message || e));
        setRows([]);
      } finally {
        setBusy(false);
      }
    },
    []
  );

  useEffect(() => {
    run("", initialRegistry);
  }, [run, initialRegistry]);

  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  /// Every hub that can be searched, as a select option — the index and any
  /// peer hub by id, the directories by name; keyed ones only when ready.
  const searchable = hubs.filter((h) => !h.self && h.registry && h.registry !== "hub");

  return (
    <div className="sheet" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="sheet-inner">
        <h2>Find a server across every hub</h2>
        <p className="muted small" style={{ margin: 0 }}>
          One search over every hub type this one can see: a keyless <strong>featured</strong> shortlist, the
          fleet's <strong>index</strong> (every row probed — live, auth, down), every <strong>peer hub</strong>{" "}
          added by URL, and the public <strong>directories</strong>. Connecting a row probes it first, like any
          other server; a row marked <em>via</em> is behind a hub — connect that hub and its tools arrive
          nested.
        </p>
        <div className="row" style={{ marginTop: 14 }}>
          <input
            autoFocus
            style={{ flex: 1 }}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && run(q, registry)}
            placeholder="what should it do? e.g. github, filesystem, weather"
          />
          <select
            value={registry}
            onChange={(e) => {
              setRegistry(e.target.value);
              run(q, e.target.value);
            }}
          >
            <option value="all">every hub</option>
            <option value="featured">featured (no key)</option>
            {searchable.map((h) => (
              <option key={h.id} value={h.registry} disabled={!h.ready}>
                {h.name} · {h.kind}
                {h.ready ? "" : h.needs_key ? " (no key)" : " (down)"}
              </option>
            ))}
          </select>
          <button className="primary" disabled={busy} onClick={() => run(q, registry)}>
            {busy ? "searching…" : "search"}
          </button>
        </div>

        {error && <div className="note bad" style={{ marginTop: 12 }}>{error}</div>}
        {errors.map((e) => (
          <div key={e} className="note warn small" style={{ marginTop: 10 }}>
            {e}
          </div>
        ))}

        <div className="col" style={{ marginTop: 14 }}>
          {rows === null && <div className="muted small">loading the directories…</div>}
          {rows?.map((l) => {
            const already = known.has(l.url.replace(/\/$/, ""));
            return (
              <div key={l.url} className="tool">
                <div className="row" style={{ justifyContent: "space-between" }}>
                  {l.homepage ? (
                    <a href={l.homepage} target="_blank" rel="noreferrer" className="t">
                      {l.name}
                    </a>
                  ) : (
                    <span className="t">{l.name}</span>
                  )}
                  <div className="row">
                    <span className="badge plain">{l.registry}</span>
                    {l.status && (
                      <span className={`badge ${l.status === "live" ? "good" : l.status === "auth" || l.status === "paid" ? "warn" : "bad"}`}>
                        {l.status}
                      </span>
                    )}
                    {l.tools ? <span className="muted small">{l.tools} tools</span> : null}
                    {l.pay && (
                      <span className="badge warn" title="paid per request over x402 — the hub pays through its x402 module">
                        x402{l.price_usd != null ? ` from $${l.price_usd}` : ""}
                      </span>
                    )}
                    {l.needs_key && <span className="badge warn">needs key</span>}
                    {l.uses ? <span className="muted small">{l.uses.toLocaleString()} uses</span> : null}
                    <button className="sm" disabled={already} onClick={() => onConnect(l)}>
                      {already ? "connected" : l.via ? `connect ${l.via} hub` : "connect"}
                    </button>
                  </div>
                </div>
                {l.description && <div className="d">{l.description}</div>}
                <div className="src" style={{ marginTop: 4 }}>
                  {l.url}
                </div>
                {l.note && <div className="muted small">{l.note}</div>}
              </div>
            );
          })}
          {rows?.length === 0 && <div className="muted small">no server in the directories matched that</div>}
        </div>
      </div>
    </div>
  );
}

/* ── hub types ────────────────────────────────────────────────────── */

const KIND_HUE: Record<string, string> = {
  mod: "var(--accent)",
  index: "var(--src-fleet)",
  directory: "var(--ink-mute)",
};

/// Every hub type this one can see: itself (a mod hub), peers added by URL,
/// the fleet's index, and the public directories — with what each holds and
/// what you can do with it: browse it, or connect the whole thing.
function HubsSheet({
  onClose,
  onBrowse,
  onChanged,
  me,
}: {
  onClose: () => void;
  onBrowse: (registry: string) => void;
  onChanged: () => void;
  me: Me | null;
}) {
  const [view, setView] = useState<HubsView | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string>("");
  const [note, setNote] = useState("");
  const [url, setUrl] = useState("");
  const [manifest, setManifest] = useState<string>("");

  const load = useCallback(async (refresh = false) => {
    setBusy(refresh ? "refresh" : "");
    try {
      setView(await api.hubs(refresh));
      setError("");
    } catch (e) {
      setError(String((e as Error).message || e));
    } finally {
      setBusy("");
    }
  }, []);

  useEffect(() => {
    load();
    api.manifest().then((m) => setManifest(JSON.stringify(m, null, 2))).catch(() => {});
  }, [load]);

  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  const canEdit = !!(me?.can_write || me?.local);

  const connect = async (h: Hub) => {
    setBusy(h.id);
    setNote("");
    try {
      const r = await api.connectHub(h.id);
      setNote(`connected ${h.id} — ${r.probe.toolCount} tools now callable as ${h.id}__server__tool`);
      onChanged();
    } catch (e) {
      setNote(String((e as Error).message || e));
    } finally {
      setBusy("");
    }
  };

  const remove = async (h: Hub) => {
    setBusy(h.id);
    try {
      await api.removeHub(h.id);
      setNote(`forgot ${h.id}`);
      load();
    } catch (e) {
      setNote(String((e as Error).message || e));
    } finally {
      setBusy("");
    }
  };

  const add = async () => {
    if (!url.trim()) return;
    setBusy("add");
    setNote("");
    try {
      const r = await api.addHub({ url: url.trim() });
      setNote(`added ${r.added.id} — a ${r.kind} hub with ${r.added.servers ?? "?"} servers`);
      setUrl("");
      load(true);
    } catch (e) {
      setNote(String((e as Error).message || e));
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="sheet" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="sheet-inner" style={{ width: "min(960px, 94vw)", maxWidth: "none" }}>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2 style={{ margin: 0 }}>Hub types</h2>
          <div className="row">
            <button className="sm ghost" disabled={busy === "refresh"} onClick={() => load(true)}>
              {busy === "refresh" ? "probing…" : "re-probe hubs"}
            </button>
            <button className="sm ghost" onClick={onClose}>
              close
            </button>
          </div>
        </div>
        <p className="muted small" style={{ margin: "6px 0 0" }}>
          Anything that lists MCP servers is a hub. <strong>mod</strong> is this software — this deployment is
          one, and any other mod hub is a peer you can browse or connect whole (its tools arrive as{" "}
          <code>peer__server__tool</code>). An <strong>index</strong> is an internet-wide crawl with a probe
          status per server. A <strong>directory</strong> is a public registry you connect rows from.
        </p>

        {view && (
          <div className="row" style={{ gap: 8, marginTop: 12 }}>
            <span className="badge plain">{view.count} hubs</span>
            <span className="badge good">{view.ready} ready</span>
            {Object.entries(view.kinds).map(([k, n]) => (
              <span key={k} className="badge" style={{ ["--hue" as string]: KIND_HUE[k] }} title={view.kind_docs[k]}>
                {n} {k}
              </span>
            ))}
            <span className="muted small">probed {ago(view.checked_at)}</span>
          </div>
        )}

        {error && <div className="note bad" style={{ marginTop: 12 }}>{error}</div>}
        {note && <div className={`note ${/^(connected|added|forgot)/.test(note) ? "good" : "warn"} small`} style={{ marginTop: 12 }}>{note}</div>}

        <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(290px, 1fr))" }}>
          {view === null && [0, 1, 2, 3].map((i) => <div key={i} className="skel" />)}
          {view?.hubs.map((h) => (
            <div key={h.id} className="card" style={{ opacity: h.ready ? 1 : 0.6, cursor: "default" }}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <h3>
                  <span style={{ color: h.ready ? "var(--good)" : "var(--bad)", marginRight: 6 }}>●</span>
                  {h.name}
                </h3>
                <div className="row" style={{ gap: 6 }}>
                  {h.self && <span className="tag">this hub</span>}
                  <span className="badge" style={{ ["--hue" as string]: KIND_HUE[h.kind] }} title={view.kind_docs[h.kind]}>
                    {h.kind}
                  </span>
                </div>
              </div>
              <p className="desc">{h.description}</p>
              <div className="meta">
                {h.servers != null && <span>{h.servers.toLocaleString()} servers</span>}
                {h.live != null && <span style={{ color: "var(--good)" }}>{h.live.toLocaleString()} live</span>}
                {h.tools != null && <span>{h.tools.toLocaleString()} tools</span>}
                {h.version && <span className="mono">v{h.version}</span>}
                {h.needs_key && !h.ready && <span className="tag">needs {h.key_name || "a key"}</span>}
                {h.note && <span className="muted small">{h.note}</span>}
              </div>
              {h.error && (
                <div className="small" style={{ color: "var(--bad-ink)" }}>
                  {h.error.slice(0, 120)}
                </div>
              )}
              <div className="src" style={{ marginTop: 2 }}>
                {h.mcp || h.url}
              </div>
              <div className="foot">
                {!h.self && h.registry && h.registry !== "hub" && (
                  <button className="sm" disabled={!h.ready} onClick={() => onBrowse(h.registry!)}>
                    browse
                  </button>
                )}
                {!h.self && h.mcp && (
                  <button
                    className="sm primary"
                    disabled={!h.ready || !canEdit || busy === h.id}
                    title={canEdit ? `register ${h.mcp} as one upstream` : "editing needs the owner wallet"}
                    onClick={() => connect(h)}
                  >
                    {busy === h.id ? "connecting…" : "connect whole hub"}
                  </button>
                )}
                {h.homepage && (
                  <a className="muted small" href={h.homepage} target="_blank" rel="noreferrer">
                    site ↗
                  </a>
                )}
                {h.source === "user" && !h.self && (
                  <button className="sm ghost" disabled={!canEdit || busy === h.id} onClick={() => remove(h)}>
                    forget
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>

        <div className="form" style={{ marginTop: 16 }}>
          <h4 style={{ margin: "0 0 6px" }}>Add a peer hub</h4>
          <div className="muted small" style={{ marginBottom: 8 }}>
            Another mod hub or index, by its API base — e.g. <code>https://host/mcp/api</code>. It has to answer{" "}
            <code>/hub</code> (or a hub-shaped <code>/stats</code>); a bare MCP endpoint is a server, not a hub —
            use <em>add server</em> for that.
          </div>
          <div className="row">
            <input
              style={{ flex: 1 }}
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && add()}
              placeholder="https://other-host/mcp/api"
              disabled={!canEdit}
            />
            <button className="primary" disabled={!canEdit || !url.trim() || busy === "add"} onClick={add}>
              {busy === "add" ? "identifying…" : "add hub"}
            </button>
          </div>
          {!canEdit && (
            <div className="muted small" style={{ marginTop: 6 }}>
              adding or connecting a hub edits the registry — sign in with the owner wallet
            </div>
          )}
        </div>

        {manifest && (
          <details style={{ marginTop: 14 }}>
            <summary className="muted small" style={{ cursor: "pointer" }}>
              this hub's manifest — what a peer reads at <code>GET /hub</code>
            </summary>
            <pre className="code" style={{ maxHeight: 260, marginTop: 8 }}>{manifest}</pre>
          </details>
        )}
      </div>
    </div>
  );
}

/* ── connecting a client ──────────────────────────────────────────── */

const CLIENT_TABS = ["claude", "cursor", "vscode", "json", "export"] as const;
type ClientTab = (typeof CLIENT_TABS)[number];

function ConnectCard({ url, me }: { url: string; me: Me | null }) {
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [minted, setMinted] = useState<string>("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [client, setClient] = useState<ClientTab>("json");
  const [configData, setConfigData] = useState<Record<string, unknown> | null>(null);

  const load = useCallback(() => {
    if (!me?.is_owner) return;
    api
      .keys()
      .then((r) => setKeys(r.keys))
      .catch(() => setKeys(null));
  }, [me?.is_owner]);

  useEffect(load, [load]);

  useEffect(() => {
    if (client === "export") {
      api.export().then(setConfigData).catch(() => {});
    } else {
      api
        .clientConfig(client)
        .then((r) => setConfigData(r.config as Record<string, unknown>))
        .catch(() => {});
    }
  }, [client]);

  const mint = async () => {
    setBusy(true);
    setError("");
    try {
      const r = await api.createKey(name.trim() || "mcp client");
      setMinted(r.secret);
      setName("");
      load();
    } catch (e) {
      setError(String((e as Error).message || e));
    } finally {
      setBusy(false);
    }
  };

  const configText = (() => {
    if (!configData) return "";
    if (client === "export") return JSON.stringify(configData, null, 2);
    if (client === "claude") {
      const cmd = (configData as { command?: string }).command ?? "";
      return minted ? `${cmd} --header "Authorization: Bearer ${minted}"` : cmd;
    }
    if (minted && configData.mcpServers) {
      const cfg = JSON.parse(JSON.stringify(configData)) as {
        mcpServers: { hub: Record<string, unknown> };
      };
      cfg.mcpServers.hub.headers = { Authorization: `Bearer ${minted}` };
      return JSON.stringify(cfg, null, 2);
    }
    return JSON.stringify(configData, null, 2);
  })();

  return (
    <div className="note" style={{ marginTop: 14 }}>
      <strong>Point any MCP client at the hub</strong>
      <div className="row" style={{ marginTop: 8, gap: 4 }}>
        {CLIENT_TABS.map((c) => (
          <button
            key={c}
            className={`sm${client === c ? " primary" : " ghost"}`}
            onClick={() => setClient(c)}
          >
            {c}
          </button>
        ))}
      </div>
      <div className="row" style={{ marginTop: 8 }}>
        <pre className="code" style={{ flex: 1 }}>{configText}</pre>
        <Copy text={configText} />
      </div>
      <p className="muted small">
        Calls from this host need no credential. Through the public gateway they do — mint a key below and the
        snippets above will carry it.
      </p>

      {me?.is_owner ? (
        <div className="col" style={{ marginTop: 10 }}>
          <div className="row">
            <input
              style={{ flex: 1 }}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="what is this key for? e.g. laptop claude code"
            />
            <button className="sm" disabled={busy} onClick={mint}>
              {busy ? "minting…" : "mint api key"}
            </button>
          </div>
          {minted && (
            <div className="note good">
              <div className="row">
                <pre className="code" style={{ flex: 1 }}>{minted}</pre>
                <Copy text={minted} />
              </div>
              <span className="small">Copy it now — the hub keeps only its hash.</span>
            </div>
          )}
          {error && <div className="note bad small">{error}</div>}
          {keys && keys.length > 0 && (
            <div className="toollist">
              {keys.map((k) => (
                <div key={k.id} className="tool">
                  <div className="row" style={{ justifyContent: "space-between" }}>
                    <span className="name">{k.name}</span>
                    <div className="row">
                      <span className="muted small mono">{k.hint}…</span>
                      <span className="muted small">{k.calls} calls</span>
                      <button
                        className="sm danger"
                        onClick={() => api.revokeKey(k.id).then(load).catch(() => {})}
                      >
                        revoke
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      ) : (
        <p className="muted small">Sign in at /build as the owner to mint an API key for remote clients.</p>
      )}
    </div>
  );
}

/* ── command palette ──────────────────────────────────────────────── */

function CmdPalette({
  servers,
  toolsCache,
  onClose,
  onSelectServer,
  onSearchTools,
  onAdd,
  onBrowse,
  onHubs,
  onActivity,
  onSearchWeb,
}: {
  servers: Server[];
  toolsCache: React.MutableRefObject<Tool[] | null>;
  onClose: () => void;
  onSelectServer: (id: string) => void;
  onSearchTools: (q: string) => void;
  onAdd: () => void;
  onBrowse: () => void;
  onHubs: () => void;
  onActivity: () => void;
  onSearchWeb: () => void;
}) {
  const [q, setQ] = useState("");
  const [activeIdx, setActiveIdx] = useState(0);
  const [tools, setTools] = useState<Tool[]>(() => toolsCache.current || []);
  const inputRef = useRef<HTMLInputElement>(null);
  const rowRefs = useRef<(HTMLButtonElement | null)[]>([]);

  useEffect(() => {
    inputRef.current?.focus();
    if (!toolsCache.current) {
      api.tools().then((r) => {
        toolsCache.current = r.tools;
        setTools(r.tools);
      }).catch(() => {});
    }
  }, [toolsCache]);

  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  useEffect(() => {
    rowRefs.current[activeIdx]?.scrollIntoView({ block: "nearest" });
  }, [activeIdx]);

  const lq = q.toLowerCase();

  const filteredServers = (q
    ? servers.filter((s) =>
        s.name.toLowerCase().includes(lq) ||
        s.id.toLowerCase().includes(lq) ||
        s.url.toLowerCase().includes(lq)
      )
    : servers
  ).slice(0, 6);

  const filteredTools = (q
    ? tools.filter((t) =>
        t.name.toLowerCase().includes(lq) ||
        (t.description || "").toLowerCase().includes(lq)
      )
    : tools
  ).slice(0, 8);

  const STATIC = [
    { label: "add server",   fn: () => { onAdd();       onClose(); } },
    { label: "find servers", fn: () => { onBrowse();    onClose(); } },
    { label: "hubs",         fn: () => { onHubs();      onClose(); } },
    { label: "activity",     fn: () => { onActivity();  onClose(); } },
    { label: "search web",   fn: () => { onSearchWeb(); onClose(); } },
  ];

  const filteredStatic = q ? STATIC.filter((c) => c.label.includes(lq)) : STATIC;

  const items = [
    ...filteredServers.map((s) => ({ fn: () => { onSelectServer(s.id); onClose(); } })),
    ...filteredTools.map((t) => ({ fn: () => { onSearchTools(t.name); onClose(); } })),
    ...filteredStatic.map((c) => ({ fn: c.fn })),
  ];

  const rowStyle: React.CSSProperties = {
    width: "100%", textAlign: "left", border: "none", borderRadius: 0,
    borderBottom: "1px solid var(--line-soft)", background: "transparent",
    padding: "9px 14px", display: "flex", gap: 10, alignItems: "center",
    cursor: "pointer",
  };
  const sectionLabel: React.CSSProperties = {
    padding: "8px 14px 2px", fontSize: 11, color: "var(--ink-faint)",
    fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em",
    userSelect: "none",
  };

  return (
    <div
      style={{
        position: "fixed", inset: 0, zIndex: 100, display: "flex",
        alignItems: "flex-start", justifyContent: "center", paddingTop: "15vh",
        background: "color-mix(in srgb, var(--bg) 50%, transparent)",
        backdropFilter: "blur(4px)",
      }}
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div style={{
        background: "var(--surface-2)", border: "1px solid var(--line-strong)",
        borderRadius: "var(--radius)", boxShadow: "var(--shadow-2)",
        width: "min(540px, 90vw)", overflow: "hidden", maxHeight: "70vh",
        display: "flex", flexDirection: "column",
      }}>
        <div style={{ padding: "12px 14px", borderBottom: "1px solid var(--line-soft)" }}>
          <input
            ref={inputRef}
            style={{
              width: "100%", background: "transparent", border: "none",
              borderRadius: 0, padding: 0, fontSize: 15, outline: "none",
            }}
            value={q}
            onChange={(e) => { setQ(e.target.value); setActiveIdx(0); }}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setActiveIdx((i) => Math.min(i + 1, items.length - 1));
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setActiveIdx((i) => Math.max(i - 1, 0));
              } else if (e.key === "Enter") {
                e.preventDefault();
                items[activeIdx]?.fn();
              }
            }}
            placeholder="search servers, tools, commands…"
          />
        </div>

        <div style={{ overflowY: "auto", flex: 1 }}>
          {filteredServers.length > 0 && (
            <div>
              <div style={sectionLabel}>Servers</div>
              {filteredServers.map((s, i) => (
                <button key={s.id} ref={(el) => { rowRefs.current[i] = el; }} style={{ ...rowStyle, background: activeIdx === i ? "var(--hover)" : "transparent" }} onClick={() => { onSelectServer(s.id); onClose(); }}>
                  <span style={{ color: s.probe.ok ? "var(--good)" : "var(--bad)", fontSize: 10 }}>●</span>
                  <span style={{ flex: 1 }}>{s.name}</span>
                  <span className="mono" style={{ fontSize: 11, color: "var(--ink-mute)" }}>{s.id}</span>
                  <span className="badge plain" style={{ fontSize: 10 }}>{s.probe.toolCount} tools</span>
                </button>
              ))}
            </div>
          )}
          {filteredTools.length > 0 && (
            <div>
              <div style={sectionLabel}>Tools</div>
              {filteredTools.map((t, i) => {
                const fi = filteredServers.length + i;
                const sep = t.name.indexOf("__");
                const srv = sep > -1 ? t.name.slice(0, sep) : null;
                const tname = sep > -1 ? t.name.slice(sep + 2) : t.name;
                return (
                  <button key={t.name} ref={(el) => { rowRefs.current[fi] = el; }} style={{ ...rowStyle, background: activeIdx === fi ? "var(--hover)" : "transparent" }} onClick={() => { onSearchTools(t.name); onClose(); }}>
                    <span className="mono" style={{ flex: 1, fontSize: 13 }}>{tname}</span>
                    {srv && <span className="mono" style={{ fontSize: 11, color: "var(--ink-mute)" }}>{srv}</span>}
                    {t.description && (
                      <span style={{ fontSize: 12, color: "var(--ink-mute)", maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {t.description}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          )}
          {filteredStatic.length > 0 && (
            <div>
              <div style={sectionLabel}>Actions</div>
              {filteredStatic.map((c, i) => {
                const fi = filteredServers.length + filteredTools.length + i;
                return (
                  <button key={c.label} ref={(el) => { rowRefs.current[fi] = el; }} style={{ ...rowStyle, background: activeIdx === fi ? "var(--hover)" : "transparent" }} onClick={c.fn}>
                    {c.label}
                  </button>
                );
              })}
            </div>
          )}
          {filteredServers.length === 0 && filteredTools.length === 0 && filteredStatic.length === 0 && q && (
            <div className="muted small" style={{ padding: 14, textAlign: "center" }}>
              no results for &ldquo;{q}&rdquo;
            </div>
          )}
        </div>

        <div style={{
          padding: "5px 14px", borderTop: "1px solid var(--line-soft)",
          fontSize: 11, color: "var(--ink-faint)", display: "flex", gap: 14,
        }}>
          <span>&#8984;K toggle</span>
          <span>esc close</span>
          <span>&#8593;&#8595; navigate · &#8629; select</span>
        </div>
      </div>
    </div>
  );
}

/* ── page ─────────────────────────────────────────────────────────── */

export default function Page() {
  const [servers, setServers] = useState<Server[] | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [seed, setSeed] = useState<Listing | null>(null);
  const [browsing, setBrowsing] = useState(false);
  const [searching, setSearching] = useState(false);
  const [endpoint, setEndpoint] = useState("");
  const [showConnect, setShowConnect] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [sweeping, setSweeping] = useState(false);
  const [sweepNote, setSweepNote] = useState("");
  const [paying, setPaying] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [gridFilter, setGridFilter] = useState("");
  const [gridSort, setGridSort] = useState("name");
  const [recentlyActive, setRecentlyActive] = useState<Set<string>>(new Set());

  const load = useCallback(() => {
    api
      .servers()
      .then((s) => {
        setServers(s);
        setLoadError("");
      })
      .catch((e) => setLoadError(String((e as Error).message || e)));
    api.stats().then(setStats).catch(() => {});
    api.me().then((r) => setMe(r.me)).catch(() => setMe(null));
    api.activity().then((r) => {
      setActivityTotal(r.total);
      setActivityBaseline((prev) => (prev !== null ? prev : r.total));
      setActivityRecent(r.recent.slice(0, 200));
    }).catch(() => {});
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    api.clientConfig("json").then((r) => setEndpoint(r.url)).catch(() => {});
  }, []);

  useEffect(() => {
    const stop = api.streamActivity((entry) => {
      setActivityTotal((prev) => prev + 1);
      setActivityRecent((prev) => [entry, ...prev].slice(0, 200));
      setServers((prev) =>
        prev
          ? prev.map((s) =>
              s.id === entry.server
                ? { ...s, calls: (s.calls ?? 0) + 1, last_call: entry.ts }
                : s
            )
          : prev
      );
      setRecentlyActive((prev) => {
        const next = new Set(prev);
        next.add(entry.server);
        return next;
      });
      setTimeout(() => {
        setRecentlyActive((prev) => {
          const next = new Set(prev);
          next.delete(entry.server);
          return next;
        });
      }, 1500);
    });
    return stop;
  }, []);

  const refreshAll = async () => {
    setRefreshing(true);
    try {
      await api.refresh();
      load();
    } finally {
      setRefreshing(false);
    }
  };

  /// Scan the x402 index for paid MCP servers and register every one that answers.
  const enableX402 = async () => {
    setPaying(true);
    setSweepNote("scanning the x402 index and probing every paid MCP server…");
    try {
      await api.x402Enable();
      for (let i = 0; i < 90; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        const st = await api.x402Status();
        if (!st.running) {
          const l = st.last;
          setSweepNote(
            l?.error
              ? `x402: ${l.error}`
              : `x402: ${l?.scanned ?? 0} paid MCP servers found — ${l?.enabled ?? 0} enabled, ${l?.gated ?? 0} pay-to-connect, ${l?.needs_key ?? 0} need a key, ${l?.down ?? 0} not answering`
          );
          break;
        }
      }
      load();
    } catch (e) {
      setSweepNote(String((e as Error).message || e));
    } finally {
      setPaying(false);
    }
  };

  /// The other half of discovery: mods that serve MCP without declaring it.
  const sweepFleet = async () => {
    setSweeping(true);
    setSweepNote("");
    try {
      const r = await api.discover();
      setSweepNote(
        r.swept
          ? `${r.swept} undeclared MCP mod${r.swept === 1 ? "" : "s"} — ${r.servers.join(", ")}`
          : "no undeclared MCP endpoints on this host"
      );
      load();
    } catch (e) {
      setSweepNote(String((e as Error).message || e));
    } finally {
      setSweeping(false);
    }
  };

  const [compareA, setCompareA] = useState<Server | null>(null);
  const [hubsOpen, setHubsOpen] = useState(false);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [toolsInitQ, setToolsInitQ] = useState<string | undefined>();
  const [activityOpen, setActivityOpen] = useState(false);
  const [cmdOpen, setCmdOpen] = useState(false);
  const cmdToolsCache = useRef<Tool[] | null>(null);

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCmdOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);
  const [activityBaseline, setActivityBaseline] = useState<number | null>(null);
  const [activityTotal, setActivityTotal] = useState(0);
  const [activityRecent, setActivityRecent] = useState<CallEntry[]>([]);
  const [topToolsOpen, setTopToolsOpen] = useState(true);
  const [catalogRegistry, setCatalogRegistry] = useState("all");

  const connectListing = (l: Listing) => {
    // A row behind a peer hub connects the hub itself, once, under its id.
    setSeed(
      l.via
        ? { ...l, id: l.via, name: `${l.via} hub`, description: `${l.via} hub — tools arrive as ${l.via}__server__tool` }
        : l
    );
    setBrowsing(false);
    setAdding(true);
  };

  const browseHub = (registry: string) => {
    setCatalogRegistry(registry);
    setHubsOpen(false);
    setBrowsing(true);
  };

  const knownUrls = useMemo(
    () => new Set((servers || []).map((s) => s.url.replace(/\/$/, ""))),
    [servers]
  );

  const toggleTheme = () => {
    const cur = document.documentElement.getAttribute("data-theme") === "light" ? "" : "light";
    if (cur) document.documentElement.setAttribute("data-theme", cur);
    else document.documentElement.removeAttribute("data-theme");
    try {
      cur ? localStorage.setItem("mcp:theme", cur) : localStorage.removeItem("mcp:theme");
    } catch {}
  };

  const selected = useMemo(() => servers?.find((s) => s.id === sel) || null, [servers, sel]);

  const shown = useMemo(() => {
    if (!servers) return null;
    let list = servers;
    if (gridFilter) {
      const q = gridFilter.toLowerCase();
      list = list.filter((s) => s.name.toLowerCase().includes(q) || s.id.toLowerCase().includes(q) || s.url.toLowerCase().includes(q));
    }
    list = [...list].sort((a, b) => {
      switch (gridSort) {
        case "calls":    return (b.calls ?? 0) - (a.calls ?? 0);
        case "tools":    return (b.probe.toolCount ?? 0) - (a.probe.toolCount ?? 0);
        case "latency":  return (a.probe.ok ? (a.probe.latency_ms ?? Infinity) : Infinity) - (b.probe.ok ? (b.probe.latency_ms ?? Infinity) : Infinity);
        case "status":   return (b.probe.ok ? 1 : 0) - (a.probe.ok ? 1 : 0);
        default:         return a.name.localeCompare(b.name);
      }
    });
    return list;
  }, [servers, gridFilter, gridSort]);

  return (
    <>
      <div className="topbar">
        <div className="topbar-inner">
          <div className="brand">
            <span className="glyph">◈</span> MCP HUB
            <span className="sub">every server, one endpoint</span>
          </div>
          <div className="spacer" />
          <button className="sm ghost" onClick={toggleTheme}>
            ◐
          </button>
          <button className="sm" disabled={refreshing} onClick={refreshAll}>
            {refreshing ? "probing fleet…" : "re-probe all"}
          </button>
          <button className="sm" disabled={sweeping} onClick={sweepFleet}>
            {sweeping ? "scanning ports…" : "scan fleet"}
          </button>
          <button className="sm" disabled={paying} onClick={enableX402} title="find every MCP server that is paid over x402 and connect the ones that answer">
            {paying ? "scanning x402…" : "enable x402"}
          </button>
          <button className="sm" onClick={() => setSearching(true)}>
            search web
          </button>
          <button className="sm" onClick={() => setToolsOpen(true)}>
            search tools
          </button>
          <button className="sm" onClick={() => setActivityOpen(true)}>
            activity{activityTotal > activityBaseline! && activityBaseline !== null ? ` · ${activityTotal - activityBaseline!}` : ""}
          </button>
          <button className="sm" onClick={() => setHubsOpen(true)}>
            hubs
          </button>
          <button
            className="sm"
            onClick={() => {
              setCatalogRegistry("all");
              setBrowsing(true);
            }}
          >
            find servers
          </button>
          <button className="sm" onClick={() => setShowConnect(!showConnect)}>
            connect
          </button>
          <button
            className="primary sm"
            onClick={() => {
              setSeed(null);
              setAdding(true);
            }}
          >
            + add server
          </button>
        </div>
      </div>

      <div className="page">
        <div className="hero">
          <h1>One hub for every MCP server</h1>
          <p>
            The local fleet is found two ways — what a mod declares, and what the port scan catches it serving —
            remote servers register by URL or from any other hub, and the union is one MCP endpoint where every
            tool is callable as <code>server__tool</code>. This is a <strong>mod</strong> hub — one hub type among
            the <button className="sm ghost" style={{ padding: "0 4px" }} onClick={() => setHubsOpen(true)}>hubs</button>{" "}
            it can see and connect to: peer mod hubs, the fleet's internet-wide index, and the public
            directories. The web comes along for the ride: <code>web_search</code> and <code>web_fetch</code>{" "}
            work with no API key.
          </p>
        </div>

        {stats && (
          <div className="row" style={{ gap: 10 }}>
            <span className="badge plain">{stats.servers} servers</span>
            <span className="badge good">{stats.up} up</span>
            {stats.down > 0 && <span className="badge bad">{stats.down} down</span>}
            <span className="badge">{stats.tools} tools aggregated</span>
            {Object.entries(stats.by_source).map(([k, v]) => (
              <span key={k} className="badge plain" title={SOURCE_HINT[k] || ""}>
                {v} {k}
              </span>
            ))}
            {servers && servers.some((s) => s.pay) && (
              <span className="badge warn" title="servers whose 402 is paid through the x402 module">
                {servers.filter((s) => s.pay).length} x402
              </span>
            )}
            {stats.hubs && (
              <button
                className="badge"
                style={{ cursor: "pointer", ["--hue" as string]: "var(--accent)" }}
                onClick={() => setHubsOpen(true)}
                title="every hub type this one can see — mod peers, the index, the directories"
              >
                {stats.hubs.count} hubs{stats.hubs.ready != null ? ` · ${stats.hubs.ready} ready` : ""}
              </button>
            )}
            {stats.web.provider && <span className="badge">web: {stats.web.provider}</span>}
            {me?.authenticated ? (
              <span className="badge plain" title={me.address || ""}>
                {me.role}
              </span>
            ) : me?.local ? (
              <span className="badge plain" title="calls from this host need no credential">
                local caller
              </span>
            ) : (
              stats.auth.gates.writes && (
                <a className="badge warn" href={`/${stats.auth.issuer}`}>
                  not signed in — editing needs the owner wallet
                </a>
              )
            )}
          </div>
        )}

        {sweepNote && (
          <div className="note small" style={{ marginTop: 12 }}>
            {sweepNote}
          </div>
        )}

        {showConnect && endpoint && <ConnectCard url={endpoint} me={me} />}

        {loadError && (
          <div className="note bad" style={{ marginTop: 14 }}>
            hub api unreachable — {loadError}
          </div>
        )}

        {activityTotal > 0 && (
          <TopToolsPanel
            recent={activityRecent}
            open={topToolsOpen}
            onToggle={() => setTopToolsOpen((v) => !v)}
            onOpenTools={(q) => { setToolsInitQ(q); setToolsOpen(true); }}
            onOpenServer={(id) => setSel(id)}
          />
        )}

        {servers !== null && servers.length > 6 && (
          <div className="searchbar" style={{ marginTop: 8 }}>
            <input
              type="search"
              placeholder="filter by name, id, or URL…"
              value={gridFilter}
              onChange={(e) => setGridFilter(e.target.value)}
            />
            <select value={gridSort} onChange={(e) => setGridSort(e.target.value)}>
              <option value="name">sort: name</option>
              <option value="calls">sort: calls</option>
              <option value="tools">sort: tools</option>
              <option value="latency">sort: latency</option>
              <option value="status">sort: status</option>
            </select>
          </div>
        )}

        <div className="grid">
          {servers === null && [0, 1, 2, 3, 4, 5].map((i) => <div key={i} className="skel" />)}
          {shown?.map((s) => (
            <div key={s.id} className={`card${recentlyActive.has(s.id) ? " card-active" : ""}`} style={{ opacity: s.enabled ? 1 : 0.45 }} onClick={() => setSel(s.id)}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <h3>
                  <span style={{ color: s.probe.ok ? "var(--good)" : "var(--bad)", marginRight: 6 }}>●</span>
                  {s.name}
                </h3>
                <span
                  className="badge"
                  style={{ ["--hue" as string]: s.local || s.source === "fleet" ? "var(--src-fleet)" : "var(--accent)" }}
                  title={sourceLabel(s).hint}
                >
                  {sourceLabel(s).text}
                </span>
              </div>
              <p className="desc">{s.note || s.url}</p>
              <div className="meta">
                <span className="mono">{s.id}__*</span>
                {s.probe.ok ? (
                  <>
                    <span>{s.probe.toolCount} tools</span>
                    <span>{s.probe.latency_ms}ms</span>
                  </>
                ) : (
                  <span style={{ color: "var(--bad-ink)" }}>{s.probe.error?.slice(0, 60) || "unprobed"}</span>
                )}
                {(s.calls ?? 0) > 0 && (
                  <>
                    <span>{s.calls!.toLocaleString()} calls</span>
                    {s.last_call && <span>last {ago(s.last_call)}</span>}
                  </>
                )}
                {s.review_risk && (
                  <span style={{ color: riskColor(s.review_risk), fontWeight: 600, textTransform: "uppercase", fontSize: 11 }}>
                    {s.review_risk}
                  </span>
                )}
                {s.review_capabilities && s.review_capabilities.length > 0 && s.review_capabilities.map((cap) => {
                  const label =
                    cap === "filesystem-write" ? "fs-write" :
                    cap === "filesystem-read" ? "fs-read" :
                    cap === "prompt-injection" ? "⚠inject" :
                    cap === "database" ? "db" :
                    cap === "network" ? "net" :
                    cap === "exec" ? "exec" : cap;
                  const bg =
                    cap === "prompt-injection" || cap === "exec" ? "var(--bad)" :
                    cap === "filesystem-write" || cap === "database" ? "#f59e0b" :
                    "var(--ink-mute)";
                  return (
                    <span key={cap} style={{ fontSize: 10, padding: "1px 5px", borderRadius: 3, background: bg, color: "#fff", fontWeight: 600, letterSpacing: "0.02em" }}>
                      {label}
                    </span>
                  );
                })}
                {s.pay && <span className="tag" title="a 402 from this server is paid through the x402 module">x402</span>}
                {!s.enabled && <span className="tag">disabled</span>}
                {s.uptime && s.uptime.length > 0 && (
                  <span style={{ display: "flex", alignItems: "flex-end", gap: 1, marginLeft: 2 }} title={`${s.uptime.filter(d => d.ok).length}/${s.uptime.length} checks up`}>
                    {(() => {
                      const maxLat = Math.max(...s.uptime!.filter(d => d.latency_ms != null).map(d => d.latency_ms as number), 1);
                      return s.uptime!.map((dot, i) => {
                        const h = dot.ok && dot.latency_ms != null
                          ? Math.max(3, Math.min(14, Math.round((dot.latency_ms / maxLat) * 14)))
                          : 3;
                        const bg = !dot.ok
                          ? "var(--line-strong)"
                          : dot.latency_ms == null || dot.latency_ms < 300
                          ? "var(--good)"
                          : dot.latency_ms < 1500
                          ? "var(--warn)"
                          : "var(--bad)";
                        return <span key={i} style={{ display: "inline-block", width: 4, height: h, borderRadius: 1, background: bg, opacity: 0.8 }} />;
                      });
                    })()}
                  </span>
                )}
              </div>
            </div>
          ))}
          {servers?.length === 0 && (
            <div className="empty">
              No servers yet. Fleet mods with an MCP endpoint appear automatically — or add a remote one.
            </div>
          )}
        </div>
      </div>

      {selected && (
        <ServerPanel
          server={selected}
          onClose={() => setSel(null)}
          onChanged={load}
          onCompare={() => { setCompareA(selected); setSel(null); }}
        />
      )}
      {compareA && (
        <CompareSheet
          serverA={compareA}
          servers={servers || []}
          onClose={() => setCompareA(null)}
        />
      )}
      {adding && (
        <AddSheet
          seed={seed}
          onClose={() => {
            setAdding(false);
            setSeed(null);
          }}
          onAdded={load}
        />
      )}
      {browsing && (
        <CatalogSheet
          onClose={() => setBrowsing(false)}
          onConnect={connectListing}
          known={knownUrls}
          initialRegistry={catalogRegistry}
        />
      )}
      {hubsOpen && <HubsSheet onClose={() => setHubsOpen(false)} onBrowse={browseHub} onChanged={load} me={me} />}
      {searching && (
        <WebSheet onClose={() => setSearching(false)} providers={stats?.web.providers || []} />
      )}
      {toolsOpen && (
        <ToolsSheet
          initialQ={toolsInitQ}
          onClose={() => { setToolsOpen(false); setToolsInitQ(undefined); }}
          onSelectServer={(id) => { setToolsOpen(false); setSel(id); }}
        />
      )}
      {activityOpen && <ActivitySheet onClose={() => setActivityOpen(false)} />}
      {cmdOpen && servers !== null && (
        <CmdPalette
          servers={servers}
          toolsCache={cmdToolsCache}
          onClose={() => setCmdOpen(false)}
          onSelectServer={(id) => setSel(id)}
          onSearchTools={(q) => { setToolsInitQ(q); setToolsOpen(true); }}
          onAdd={() => { setSeed(null); setAdding(true); }}
          onBrowse={() => { setCatalogRegistry("all"); setBrowsing(true); }}
          onHubs={() => setHubsOpen(true)}
          onActivity={() => setActivityOpen(true)}
          onSearchWeb={() => setSearching(true)}
        />
      )}
    </>
  );
}
