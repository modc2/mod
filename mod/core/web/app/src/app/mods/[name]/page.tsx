"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, type Module } from "@/lib/api";
import { Nav, Footer } from "../../components/Chrome";

function hueFromName(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 360;
  return `hsl(${h} 70% 64%)`;
}

export default function ModuleDetail({ params }: { params: { name: string } }) {
  const [m, setM] = useState<Module | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .mod(params.name)
      .then((mod) => alive && setM(mod))
      .catch((e) => alive && setErr(String(e)));
    return () => {
      alive = false;
    };
  }, [params.name]);

  const glow = m?.color || hueFromName(params.name);

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
                <div className="ver">v{m.version}</div>
              </div>
            </div>

            <p className="detail-desc">
              {m.description || "No description provided."}
            </p>

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
            </div>

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

            <div className="panel">
              <h3>config.json</h3>
              <pre className="code">
                {JSON.stringify(m.config, null, 2)}
              </pre>
            </div>
          </>
        )}
      </main>
      <Footer version={undefined} />
    </>
  );
}
