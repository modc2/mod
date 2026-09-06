"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  getToken,
  setToken,
  ApiKey,
  Candidate,
  Hub,
  HubsView,
  Listing,
  Me,
  PageText,
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

function ToolRow({ tool }: { tool: Tool }) {
  const [open, setOpen] = useState(false);
  const [args, setArgs] = useState("{}");
  const [out, setOut] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async () => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(args || "{}");
    } catch {
      setOut("args must be valid JSON");
      return;
    }
    setBusy(true);
    setOut(null);
    try {
      const r = await api.call(tool.name, parsed);
      setOut(JSON.stringify(r.result, null, 2));
    } catch (e) {
      setOut(String((e as Error).message || e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="tool">
      <div
        className="row"
        style={{ cursor: "pointer", justifyContent: "space-between" }}
        onClick={() => setOpen(!open)}
      >
        <span className="name">{tool.name}</span>
        <span className="muted small">{open ? "▾" : "▸"}</span>
      </div>
      {tool.description && <div className="d">{tool.description}</div>}
      {open && (
        <div className="col" style={{ marginTop: 8 }}>
          {tool.inputSchema && (
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
            <button className="primary sm" disabled={busy} onClick={run}>
              {busy ? "running…" : "run"}
            </button>
          </div>
          {out !== null && (
            <pre className="code" style={{ maxHeight: 280 }}>
              {out}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

/* ── server detail panel ──────────────────────────────────────────── */

function ServerPanel({
  server,
  onClose,
  onChanged,
}: {
  server: Server;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [tools, setTools] = useState<Tool[]>([]);
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
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
          <span className={`badge ${server.source === "fleet" ? "" : "plain"}`} style={{ ["--hue" as string]: server.source === "fleet" ? "var(--src-fleet)" : "var(--accent)" }}>
            {server.source}
          </span>
          <div className="spacer" />
          <button className="sm ghost" onClick={onClose}>
            esc
          </button>
        </div>
        {server.note && <p className="muted small">{server.note}</p>}

        <section>
          <h4>Server</h4>
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
          <button
            className="sm danger"
            disabled={!!busy}
            onClick={() => act("remove", () => api.removeServer(server.id))}
          >
            {server.source === "user" ? "delete" : "disable (fleet)"}
          </button>
        </section>
        {error && <div className="note bad" style={{ marginTop: 10 }}>{error}</div>}

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
  const [url, setUrl] = useState(seed?.url || "");
  const [id, setId] = useState(seed?.id || "");
  const [name, setName] = useState(seed?.name || "");
  const [note, setNote] = useState(seed?.description || "");
  const [headers, setHeaders] = useState("");
  const [token, setTok] = useState(getToken());
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<string>("");
  const [paste, setPaste] = useState("");
  const [found, setFound] = useState<Candidate[] | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);

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

  const doProbe = async () => {
    const h = parseHeaders();
    if (h === null) return setError("headers must be a JSON object");
    setBusy("probe");
    setError("");
    setPreview("");
    try {
      const r = await api.probe(url.trim(), h);
      setPreview(
        r.probe.ok
          ? `✓ ${r.probe.serverInfo?.name || "server"} — ${r.probe.toolCount} tools, ${r.probe.latency_ms}ms`
          : `✗ ${r.probe.error}`
      );
    } catch (e) {
      setError(String((e as Error).message || e));
    } finally {
      setBusy("");
    }
  };

  const doAdd = async () => {
    const h = parseHeaders();
    if (h === null) return setError("headers must be a JSON object");
    setToken(token.trim());
    setBusy("add");
    setError("");
    try {
      await api.addServer({
        url: url.trim(),
        id: id.trim() || undefined,
        name: name.trim() || undefined,
        note: note.trim() || undefined,
        headers: h,
      });
      onAdded();
      onClose();
    } catch (e) {
      setError(String((e as Error).message || e));
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="sheet" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="sheet-inner">
        <h2>Add an MCP server</h2>
        <p className="muted small" style={{ margin: 0 }}>
          Any Streamable HTTP endpoint. It's probed first — a server that won't shake hands isn't registered.
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
          )}
          {warnings.map((w) => (
            <div key={w} className="note warn small">
              {w}
            </div>
          ))}
        </div>

        <div className="form">
          <label className="field wide">
            endpoint url
            <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://host/mcp" />
          </label>
          <label className="field">
            id <span className="hint">tool prefix; defaults to hostname</span>
            <input value={id} onChange={(e) => setId(e.target.value)} placeholder="myserver" />
          </label>
          <label className="field">
            display name
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="My Server" />
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
        {error && <div className="note bad" style={{ marginTop: 12 }}>{error}</div>}
        <div className="row" style={{ marginTop: 14, justifyContent: "flex-end" }}>
          <button disabled={!url.trim() || !!busy} onClick={doProbe}>
            {busy === "probe" ? "probing…" : "probe"}
          </button>
          <button className="primary" disabled={!url.trim() || !!busy} onClick={doAdd}>
            {busy === "add" ? "adding…" : "add to hub"}
          </button>
        </div>
      </div>
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
                      <span className={`badge ${l.status === "live" ? "good" : l.status === "auth" ? "warn" : "bad"}`}>
                        {l.status}
                      </span>
                    )}
                    {l.tools ? <span className="muted small">{l.tools} tools</span> : null}
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
            Another mod hub or index, by its API base — e.g. <code>https://host/api/mcp</code>. It has to answer{" "}
            <code>/hub</code> (or a hub-shaped <code>/stats</code>); a bare MCP endpoint is a server, not a hub —
            use <em>add server</em> for that.
          </div>
          <div className="row">
            <input
              style={{ flex: 1 }}
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && add()}
              placeholder="https://other-host/api/mcp"
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

function ConnectCard({ url, me }: { url: string; me: Me | null }) {
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [minted, setMinted] = useState<string>("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    if (!me?.is_owner) return;
    api
      .keys()
      .then((r) => setKeys(r.keys))
      .catch(() => setKeys(null));
  }, [me?.is_owner]);

  useEffect(load, [load]);

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

  const header = minted ? ` --header "Authorization: Bearer ${minted}"` : "";
  const cli = `claude mcp add hub --transport http ${url}${header}`;
  const json = JSON.stringify(
    {
      mcpServers: {
        hub: {
          transport: "http",
          url,
          ...(minted ? { headers: { Authorization: `Bearer ${minted}` } } : {}),
        },
      },
    },
    null,
    2
  );

  return (
    <div className="note" style={{ marginTop: 14 }}>
      <strong>Point any MCP client at the hub</strong>
      <div className="row" style={{ marginTop: 8 }}>
        <pre className="code" style={{ flex: 1 }}>{cli}</pre>
        <Copy text={cli} />
      </div>
      <div className="row" style={{ marginTop: 8 }}>
        <pre className="code" style={{ flex: 1 }}>{json}</pre>
        <Copy text={json} />
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
  const [loadError, setLoadError] = useState("");

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
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    api.clientConfig("json").then((r) => setEndpoint(r.url)).catch(() => {});
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

  const [hubsOpen, setHubsOpen] = useState(false);
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
          <button className="sm" onClick={() => setSearching(true)}>
            search web
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

        <div className="grid">
          {servers === null && [0, 1, 2, 3, 4, 5].map((i) => <div key={i} className="skel" />)}
          {servers?.map((s) => (
            <div key={s.id} className="card" style={{ opacity: s.enabled ? 1 : 0.45 }} onClick={() => setSel(s.id)}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <h3>
                  <span style={{ color: s.probe.ok ? "var(--good)" : "var(--bad)", marginRight: 6 }}>●</span>
                  {s.name}
                </h3>
                <span
                  className="badge"
                  style={{ ["--hue" as string]: s.source === "fleet" ? "var(--src-fleet)" : "var(--accent)" }}
                >
                  {s.source}
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
                {!s.enabled && <span className="tag">disabled</span>}
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

      {selected && <ServerPanel server={selected} onClose={() => setSel(null)} onChanged={load} />}
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
    </>
  );
}
