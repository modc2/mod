"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { api, gatewayUrl, recents, type Module } from "@/lib/api";
import { Nav, Footer } from "../../components/Chrome";
import { Icon } from "../../components/Icon";
import Explorer from "../../components/Explorer";
import RegisterPanel from "../../components/RegisterPanel";
import StakePanel from "../../components/StakePanel";

function hueFromName(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 360;
  return `hsl(${h} 70% 64%)`;
}

type Tab = "app" | "about" | "code";

export default function ModuleDetail({ params }: { params: { name: string } }) {
  const [m, setM] = useState<Module | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("about");
  const [frameKey, setFrameKey] = useState(0);

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
        // Land on the running app when there is one — the module's app is the
        // thing worth seeing first; everything else lives one tab over.
        setTab(mod.has_app ? "app" : "about");
        recents.add(mod.name); // feeds the workspace "previously visited" rail
      })
      .catch((e) => alive && setErr(String(e)));
    return () => {
      alive = false;
    };
  }, [params.name]);

  const glow = m?.color || hueFromName(params.name);
  const appUrl = useMemo(() => gatewayUrl(params.name), [params.name]);
  const stack =
    m &&
    ([m.has_rust && "Rust", m.has_app && "web app"].filter(Boolean).join(" + ") ||
      "Python");

  return (
    <>
      <Nav />
      <main className="wrap detail">
        <Link href="/" className="back">
          <Icon name="arrow-left" /> All modules
        </Link>

        {err && (
          <div className="empty">
            There is no module called “{params.name}”.{" "}
            <Link href="/" className="link">
              Back to the catalog
            </Link>
          </div>
        )}
        {!m && !err && <div className="skel" style={{ height: 280 }} />}

        {m && (
          <>
            <div className="detail-head">
              <span className="avatar" style={{ background: glow }}>
                {(m.icon || m.name[0]).slice(0, 1)}
              </span>
              <div className="detail-title">
                <h1>{m.name}</h1>
                <div className="detail-badges">
                  <span className="ver">v{m.version}</span>
                  {m.on_web && (
                    <span className="onweb">
                      <i className="live-dot" />
                      Live
                    </span>
                  )}
                  {m.registered && (
                    <span className="onchain">
                      <Icon name="chain" />
                      On-chain
                    </span>
                  )}
                </div>
              </div>
              <div className="detail-actions">
                {m.has_app && (
                  <a
                    href={appUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="btn btn-primary"
                  >
                    Open app
                    <Icon name="external" />
                  </a>
                )}
                <button
                  className="btn btn-ghost"
                  onClick={() => setTab("code")}
                  type="button"
                >
                  <Icon name="code" />
                  View code
                </button>
              </div>
            </div>

            <p className="detail-desc">
              {m.description || "No description yet."}
            </p>

            <div className="tabs" role="tablist">
              {m.has_app && (
                <button
                  role="tab"
                  aria-selected={tab === "app"}
                  className={`tab${tab === "app" ? " active" : ""}`}
                  onClick={() => setTab("app")}
                >
                  <Icon name="app" />
                  App
                </button>
              )}
              <button
                role="tab"
                aria-selected={tab === "about"}
                className={`tab${tab === "about" ? " active" : ""}`}
                onClick={() => setTab("about")}
              >
                <Icon name="info" />
                About
              </button>
              <button
                role="tab"
                aria-selected={tab === "code"}
                className={`tab${tab === "code" ? " active" : ""}`}
                onClick={() => setTab("code")}
              >
                <Icon name="code" />
                Code
              </button>
            </div>

            {tab === "app" && m.has_app && (
              <div className="app-embed">
                <div className="app-bar">
                  <span className="app-url" title={appUrl}>
                    {appUrl.replace(/^https?:\/\//, "")}
                  </span>
                  <span className="app-bar-actions">
                    <button
                      type="button"
                      className="app-open"
                      onClick={() => setFrameKey((k) => k + 1)}
                      title="Reload the app"
                    >
                      <Icon name="reload" />
                      Reload
                    </button>
                    <a
                      href={appUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="app-open"
                    >
                      Open in new tab
                      <Icon name="external" />
                    </a>
                  </span>
                </div>
                <iframe
                  key={frameKey}
                  src={appUrl}
                  className="app-frame"
                  title={`${m.name} app`}
                />
                <p className="app-hint">
                  Blank screen? Some apps take a few seconds to wake up. Press
                  Reload, or open it in a new tab.
                </p>
              </div>
            )}

            {tab === "about" && (
              <>
                <div className="kv-grid">
                  <div className="kv">
                    <div className="k">Address</div>
                    <div className="v">
                      {m.on_web ? (
                        <a href={appUrl} target="_blank" rel="noreferrer" className="link">
                          modc2.com{m.mount}
                        </a>
                      ) : (
                        <>modc2.com{m.mount}</>
                      )}
                    </div>
                  </div>
                  <div className="kv">
                    <div className="k">Built with</div>
                    <div className="v">{stack}</div>
                  </div>
                  <div className="kv">
                    <div className="k">Functions</div>
                    <div className="v">
                      {m.fns.length === 0
                        ? "none exposed"
                        : `${m.fns.length} you can call`}
                    </div>
                  </div>
                  <div className="kv">
                    <div className="k">On-chain</div>
                    <div className="v">
                      {m.registered ? "registered" : "not registered yet"}
                    </div>
                  </div>
                </div>

                {m.deps && m.deps.length > 0 && (
                  <div className="panel">
                    <h3>Uses {m.deps.length} other module{m.deps.length === 1 ? "" : "s"}</h3>
                    <p className="panel-sub">
                      This module calls these ones to do its job. Click any to open it.
                    </p>
                    <div className="fn-list">
                      {m.deps.map((d) => (
                        <Link className="fn-chip link-chip" href={`/mods/${d}`} key={d}>
                          {d}
                          <Icon name="arrow-right" />
                        </Link>
                      ))}
                    </div>
                  </div>
                )}

                {m.fns.length > 0 && (
                  <div className="panel">
                    <h3>Functions</h3>
                    <p className="panel-sub">
                      Each one is an API call at <code>modc2.com/api/{m.name}/&lt;function&gt;</code>.
                    </p>
                    <div className="fn-list">
                      {m.fns.map((f) => (
                        <span className="fn-chip" key={f}>
                          {f}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                <div className="chain-sec-head">
                  <h3>
                    <Icon name="chain" /> Back this module
                  </h3>
                  <span className="chain-sec-sub">
                    Register it on-chain, or stake BLOC behind it to signal it
                    matters. Both need a connected wallet.
                  </span>
                </div>
                <div className="chain-grid">
                  <RegisterPanel
                    name={m.name}
                    schema={m.schema}
                    registered={!!m.registered}
                    onRegistered={reload}
                  />
                  <StakePanel name={m.name} />
                </div>

                <details className="tech">
                  <summary>
                    <Icon name="chevron-right" className="tech-caret" />
                    Technical details
                    <span className="tech-sub">ports, schema CID, raw config</span>
                  </summary>
                  <div className="kv-grid tech-grid">
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
                      <div className="k">Gateway mount</div>
                      <div className="v">{m.mount}</div>
                    </div>
                    {m.schema && (
                      <div className="kv wide">
                        <div className="k">Schema CID</div>
                        <div className="v">{m.schema}</div>
                      </div>
                    )}
                  </div>
                  <div className="panel">
                    <h3>config.json</h3>
                    <pre className="code">{JSON.stringify(m.config, null, 2)}</pre>
                  </div>
                </details>
              </>
            )}

            {tab === "code" && <Explorer name={m.name} />}
          </>
        )}
      </main>
      <Footer version={undefined} />
    </>
  );
}
