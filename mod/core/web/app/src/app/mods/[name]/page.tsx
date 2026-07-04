"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { api, gatewayUrl, recents, type Module } from "@/lib/api";
import { Nav, Footer } from "../../components/Chrome";
import Explorer from "../../components/Explorer";
import RegisterPanel from "../../components/RegisterPanel";

function hueFromName(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 360;
  return `hsl(${h} 70% 64%)`;
}

type Tab = "overview" | "code" | "app";

export default function ModuleDetail({ params }: { params: { name: string } }) {
  const [m, setM] = useState<Module | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("overview");

  const reload = () =>
    api
      .mod(params.name)
      .then((mod) => setM(mod))
      .catch((e) => setErr(String(e)));

  useEffect(() => {
    let alive = true;
    api
      .mod(params.name)
      .then((mod) => {
        if (!alive) return;
        setM(mod);
        recents.add(mod.name); // feeds the workspace "previously visited" rail
      })
      .catch((e) => alive && setErr(String(e)));
    return () => {
      alive = false;
    };
  }, [params.name]);

  const glow = m?.color || hueFromName(params.name);
  const appUrl = useMemo(() => gatewayUrl(params.name), [params.name]);

  return (
    <>
      <Nav />
      <main className="wrap">
        <Link href="/" className="back">
          ← all modules
        </Link>

        {err && <div className="empty">module not found · {params.name}</div>}
        {!m && !err && <div className="skel" style={{ height: 280 }} />}

        {m && (
          <>
            <div className="detail-head">
              <span className="avatar" style={{ background: glow }}>
                {(m.icon || m.name[0]).slice(0, 1)}
              </span>
              <div>
                <h1>{m.name}</h1>
                <div className="ver">
                  v{m.version}
                  {m.registered && (
                    <span className="onchain" style={{ marginLeft: 10 }}>
                      ⛓ on-chain
                    </span>
                  )}
                </div>
              </div>
              {m.has_app && (
                <a
                  href={appUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="open-live"
                >
                  open live ↗
                </a>
              )}
            </div>

            <p className="detail-desc">
              {m.description || "No description provided."}
            </p>

            <div className="tabs">
              <button
                className={`tab${tab === "overview" ? " active" : ""}`}
                onClick={() => setTab("overview")}
              >
                Overview
              </button>
              <button
                className={`tab${tab === "code" ? " active" : ""}`}
                onClick={() => setTab("code")}
              >
                Code
              </button>
              {m.has_app && (
                <button
                  className={`tab${tab === "app" ? " active" : ""}`}
                  onClick={() => setTab("app")}
                >
                  App
                </button>
              )}
            </div>

            {tab === "overview" && (
              <>
                <div className="kv-grid">
                  <div className="kv">
                    <div className="k">Gateway mount</div>
                    <div className="v">modc2.com{m.mount}</div>
                  </div>
                  {m.port != null && (
                    <div className="kv">
                      <div className="k">API port</div>
                      <div className="v">{m.port}</div>
                    </div>
                  )}
                  {m.app_port != null && (
                    <div className="kv">
                      <div className="k">App port</div>
                      <div className="v">{m.app_port}</div>
                    </div>
                  )}
                  <div className="kv">
                    <div className="k">Stack</div>
                    <div className="v">
                      {[m.has_rust && "rust", m.has_app && "app"]
                        .filter(Boolean)
                        .join(" · ") || "python"}
                    </div>
                  </div>
                  {m.schema && (
                    <div className="kv">
                      <div className="k">Schema CID</div>
                      <div className="v">{m.schema}</div>
                    </div>
                  )}
                  <div className="kv">
                    <div className="k">On-chain</div>
                    <div className="v">
                      {m.registered ? "registered" : "not registered"}
                    </div>
                  </div>
                </div>

                {m.deps && m.deps.length > 0 && (
                  <div className="panel">
                    <h3>depends on {m.deps.length} module(s)</h3>
                    <div className="fn-list">
                      {m.deps.map((d) => (
                        <Link className="fn-chip" href={`/mods/${d}`} key={d}>
                          ↳ {d}
                        </Link>
                      ))}
                    </div>
                  </div>
                )}

                {m.fns.length > 0 && (
                  <div className="panel">
                    <h3>{m.fns.length} exposed functions</h3>
                    <div className="fn-list">
                      {m.fns.map((f) => (
                        <span className="fn-chip" key={f}>
                          {f}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {!m.registered && (
                  <RegisterPanel
                    name={m.name}
                    schema={m.schema}
                    onRegistered={reload}
                  />
                )}

                <div className="panel">
                  <h3>config.json</h3>
                  <pre className="code">
                    {JSON.stringify(m.config, null, 2)}
                  </pre>
                </div>
              </>
            )}

            {tab === "code" && <Explorer name={m.name} />}

            {tab === "app" && m.has_app && (
              <div className="app-embed">
                <div className="app-bar">
                  <span className="app-url">{appUrl}</span>
                  <a
                    href={appUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="app-open"
                  >
                    open in new tab ↗
                  </a>
                </div>
                <iframe
                  src={appUrl}
                  className="app-frame"
                  title={`${m.name} app`}
                />
              </div>
            )}
          </>
        )}
      </main>
      <Footer version={undefined} />
    </>
  );
}
