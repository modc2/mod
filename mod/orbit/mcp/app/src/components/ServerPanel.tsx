"use client";

import { useEffect, useState } from "react";
import { api, ClientConfig, ProbeResult, Server } from "@/lib/api";
import { compact, SourceBadge } from "./ServerCard";

const CLIENTS = ["claude", "cursor", "vscode"];

function Copy({ text, label = "copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      className="ghost sm"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setDone(true);
          setTimeout(() => setDone(false), 1400);
        } catch {
          /* clipboard blocked — the text is on screen anyway */
        }
      }}
    >
      {done ? "copied" : label}
    </button>
  );
}

/**
 * The detail drawer: everything the directories know about one server, plus the
 * two things only this hub can tell you — what its tools are *right now*, and
 * the exact config line to install it.
 */
export default function ServerPanel({ id, onClose }: { id: string; onClose: () => void }) {
  const [server, setServer] = useState<Server | null>(null);
  const [error, setError] = useState("");
  const [probe, setProbe] = useState<ProbeResult | null>(null);
  const [probing, setProbing] = useState(false);
  const [client, setClient] = useState("claude");
  const [config, setConfig] = useState<ClientConfig | null>(null);

  useEffect(() => {
    let live = true;
    setServer(null);
    setProbe(null);
    setError("");
    api
      .server(id)
      .then((s) => {
        if (!live) return;
        setServer(s);
        if (s.probe) setProbe(s.probe);
      })
      .catch((e) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [id]);

  useEffect(() => {
    let live = true;
    api
      .clientConfig(id, client)
      .then((c) => live && setConfig(c))
      .catch(() => live && setConfig(null));
    return () => {
      live = false;
    };
  }, [id, client]);

  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);

  async function runProbe(refresh: boolean) {
    setProbing(true);
    try {
      setProbe(await api.probe({ id, refresh }));
    } catch (e) {
      setProbe({ url: "", ok: false, tools: [], error: e instanceof Error ? e.message : String(e) });
    } finally {
      setProbing(false);
    }
  }

  const sources = server?.sources ?? (server ? [server.source] : []);

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="panel">
        <div className="panel-head">
          <div style={{ flex: 1 }}>
            <div className="row" style={{ gap: 6, marginBottom: 6 }}>
              {sources.map((s) => (
                <SourceBadge key={s} id={s} />
              ))}
            </div>
            <h2>{server?.name || server?.title || id}</h2>
            <div className="mono small muted" style={{ marginTop: 4 }}>
              {id}
            </div>
          </div>
          <button className="ghost sm" onClick={onClose}>
            close
          </button>
        </div>

        {error && <div className="note bad">{error}</div>}
        {!server && !error && <div className="skel" style={{ marginTop: 20 }} />}

        {server && (
          <>
            <p style={{ color: "var(--ink-dim)" }}>{server.description || "no description published"}</p>

            <section>
              <h4>facts</h4>
              <dl className="kv">
                {server.repo && (
                  <>
                    <dt>source</dt>
                    <dd>
                      <a href={server.repo} target="_blank" rel="noreferrer">
                        {server.repo.replace(/^https?:\/\//, "")}
                      </a>
                    </dd>
                  </>
                )}
                {server.homepage && (
                  <>
                    <dt>homepage</dt>
                    <dd>
                      {server.homepage.startsWith("http") ? (
                        <a href={server.homepage} target="_blank" rel="noreferrer">
                          {server.homepage.replace(/^https?:\/\//, "")}
                        </a>
                      ) : (
                        server.homepage
                      )}
                    </dd>
                  </>
                )}
                <dt>license</dt>
                <dd>{server.license || <span className="muted">not published</span>}</dd>
                <dt>transports</dt>
                <dd>{server.transports.join(", ") || <span className="muted">unknown</span>}</dd>
                {server.stars !== null && (
                  <>
                    <dt>stars</dt>
                    <dd>{compact(server.stars)}</dd>
                  </>
                )}
                {server.downloads !== null && (
                  <>
                    <dt>downloads</dt>
                    <dd>{compact(server.downloads)} / month</dd>
                  </>
                )}
                {server.version && (
                  <>
                    <dt>version</dt>
                    <dd>{server.version}</dd>
                  </>
                )}
                {server.author && (
                  <>
                    <dt>author</dt>
                    <dd className="mono small">{server.author}</dd>
                  </>
                )}
                {server.updated && (
                  <>
                    <dt>updated</dt>
                    <dd>{String(server.updated).slice(0, 10)}</dd>
                  </>
                )}
                {server.cid && (
                  <>
                    <dt>manifest</dt>
                    <dd>
                      <a href={api.storeObjectUrl(server.cid)} target="_blank" rel="noreferrer">
                        <span className="mono small">{server.cid}</span>
                      </a>
                    </dd>
                  </>
                )}
              </dl>
              {server.tags.length > 0 && (
                <div className="row" style={{ marginTop: 10 }}>
                  {server.tags.map((t) => (
                    <span key={t} className="tag">
                      {t}
                    </span>
                  ))}
                </div>
              )}
            </section>

            <section>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <h4 style={{ margin: 0 }}>live tools</h4>
                <div className="row">
                  <button className="sm" disabled={probing} onClick={() => runProbe(true)}>
                    {probing ? "probing…" : probe ? "re-probe" : "probe"}
                  </button>
                </div>
              </div>
              {!probe && !probing && (
                <p className="muted small" style={{ marginTop: 8 }}>
                  Ask the server itself what it exposes — the hub runs an MCP handshake and lists the
                  tools it actually answers with today. Remote endpoints only; stdio servers run on
                  your machine, not ours.
                </p>
              )}
              {probe && !probe.ok && (
                <div className="note warn" style={{ marginTop: 8 }}>
                  {probe.stdio_only
                    ? "stdio-only server — install it in your own client to see its tools"
                    : probe.error || "probe failed"}
                </div>
              )}
              {probe && probe.ok && (
                <>
                  <div className="meta" style={{ marginTop: 8, marginBottom: 8 }}>
                    <span className="badge good">up</span>
                    {probe.server_info?.name && (
                      <span>
                        {probe.server_info.name} {probe.server_info.version}
                      </span>
                    )}
                    {probe.protocol_version && <span>protocol {probe.protocol_version}</span>}
                    {probe.latency_ms !== undefined && <span>{probe.latency_ms} ms</span>}
                    {probe.cached && <span className="muted">cached</span>}
                  </div>
                  {probe.tools_error && <div className="note warn">{probe.tools_error}</div>}
                  <div className="toollist">
                    {probe.tools.map((t) => (
                      <div className="tool" key={t.name}>
                        <div className="name">{t.name}</div>
                        {t.description && <div className="d">{t.description}</div>}
                      </div>
                    ))}
                  </div>
                </>
              )}
            </section>

            <section>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <h4 style={{ margin: 0 }}>install</h4>
                <div className="chips">
                  {CLIENTS.map((c) => (
                    <span key={c} className="chip" data-on={client === c} onClick={() => setClient(c)}>
                      {c}
                    </span>
                  ))}
                </div>
              </div>
              {config?.command && (
                <div style={{ marginTop: 8 }}>
                  <div className="row" style={{ justifyContent: "space-between", marginBottom: 4 }}>
                    <span className="muted small">command line</span>
                    <Copy text={config.command} />
                  </div>
                  <pre className="code">{config.command}</pre>
                </div>
              )}
              {config?.config && (
                <div style={{ marginTop: 10 }}>
                  <div className="row" style={{ justifyContent: "space-between", marginBottom: 4 }}>
                    <span className="muted small">{config.file}</span>
                    <Copy text={JSON.stringify(config.config, null, 2)} />
                  </div>
                  <pre className="code">{JSON.stringify(config.config, null, 2)}</pre>
                </div>
              )}
              {config && !config.config && (
                <div className="note warn" style={{ marginTop: 8 }}>
                  {config.note || "no install recipe published"}
                </div>
              )}
            </section>
          </>
        )}
      </aside>
    </>
  );
}
