"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Canvas from "./components/Canvas";
import DexDesk from "./components/DexDesk";
import Inspector from "./components/Inspector";
import Palette from "./components/Palette";
import PromptDrawer from "./components/PromptDrawer";
import YieldDesk from "./components/YieldDesk";
import Hub, { type Prefill } from "./components/Hub";
import Modules from "./components/Modules";
import Book from "./components/Book";
import AuditView, { RiskPill } from "./components/AuditView";
import * as api from "./lib/api";
import { runPlan, type StepState } from "./lib/deploy";
import { emptyGraph, type Audit, type BlockSpec, type Catalog, type Graph, type Plan, type Protocol, type Report } from "./lib/types";

type Drawer = "none" | "prompts" | "library" | "source" | "deploy";
/// The console's six rooms. HUB is home: the curated front door for USD —
/// MODULES is the full registry behind it.
type View = "hub" | "modules" | "book" | "treasury" | "trade" | "compose";
const VIEWS: { id: View; label: string }[] = [
  { id: "hub", label: "HUB" },
  { id: "modules", label: "MODULES" },
  { id: "book", label: "BOOK" },
  { id: "treasury", label: "TREASURY" },
  { id: "trade", label: "TRADE" },
  { id: "compose", label: "COMPOSE" },
];

export default function Page() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [boot, setBoot] = useState<string | null>(null);
  const [graph, setGraph] = useState<Graph>(emptyGraph());
  const [report, setReport] = useState<Report | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [drawer, setDrawer] = useState<Drawer>("none");
  const [view, setView] = useState<View>("hub");
  const [prefill, setPrefill] = useState<Prefill | null>(null);
  const [sourceOf, setSourceOf] = useState<{ block: BlockSpec; artifact?: any; audit?: Audit | null } | null>(null);
  const [sourceTab, setSourceTab] = useState<"source" | "audit">("source");

  const [address, setAddress] = useState<string | null>(null);
  const [protocolId, setProtocolId] = useState<string | null>(null);
  const [protocols, setProtocols] = useState<Protocol[]>([]);
  const [compileInfo, setCompileInfo] = useState<any>(null);

  const [plan, setPlan] = useState<Plan | null>(null);
  const [steps, setSteps] = useState<StepState[]>([]);
  const [deployed, setDeployed] = useState<Record<string, string>>({});
  const [deploying, setDeploying] = useState(false);
  const [composing, setComposing] = useState(false);
  const [toast, setToast] = useState<{ text: string; bad?: boolean } | null>(null);

  const say = useCallback((text: string, bad = false) => {
    setToast({ text, bad });
    setTimeout(() => setToast(null), 4200);
  }, []);

  // ── boot ────────────────────────────────────────────────────────────────
  useEffect(() => {
    (async () => {
      try {
        const cat = await api.getCatalog();
        setCatalog(cat);
        setCompileInfo(await api.getCompileStatus().catch(() => null));
        setAddress(api.getAddress());
        api.getProtocols().then((p) => setProtocols(p.protocols)).catch(() => {});
        // ?import=<cid> deep link — the share URL publish() hands back.
        const cid = new URLSearchParams(window.location.search).get("import");
        if (cid) {
          try {
            const { protocol } = await api.importProtocol(cid);
            setGraph(protocol.graph);
            setProtocolId(protocol.id);
            setView("compose");
            say(`imported “${protocol.name}”`);
          } catch (e: any) {
            say(e.message, true);
          }
        }
        const room = new URLSearchParams(window.location.search).get("view");
        if (room && VIEWS.some((v) => v.id === room)) setView(room as View);
      } catch (e: any) {
        setBoot(e.message);
      }
    })();
  }, [say]);

  // The catalog can be compiling when the page loads; poll until it is ready so
  // the DEPLOY button does not sit disabled with no explanation.
  useEffect(() => {
    if (compileInfo?.ready || compileInfo?.error) return;
    const timer = setInterval(async () => {
      try {
        const next = await api.getCompileStatus();
        setCompileInfo(next);
        if (next.ready || next.error) clearInterval(timer);
      } catch {
        /* keep polling */
      }
    }, 2500);
    return () => clearInterval(timer);
  }, [compileInfo]);

  // ── validation, debounced ───────────────────────────────────────────────
  const validateTimer = useRef<any>(null);
  useEffect(() => {
    if (!catalog) return;
    clearTimeout(validateTimer.current);
    validateTimer.current = setTimeout(() => {
      api.validateGraph(graph).then(setReport).catch(() => {});
    }, 180);
    return () => clearTimeout(validateTimer.current);
  }, [graph, catalog]);

  const selectedNode = useMemo(
    () => graph.nodes.find((n) => n.id === selected) ?? null,
    [graph, selected]
  );

  // ── actions ─────────────────────────────────────────────────────────────

  const connect = async () => {
    try {
      const { address } = await api.signIn();
      setAddress(address);
      say(`signed in as ${address.slice(0, 8)}…`);
      api.getProtocols().then((p) => setProtocols(p.protocols)).catch(() => {});
    } catch (e: any) {
      say(e.message, true);
    }
  };

  const save = async () => {
    if (!address) return say("sign in to save", true);
    try {
      const { protocol } = await api.saveProtocol(graph, graph.name, protocolId ?? undefined);
      setProtocolId(protocol.id);
      setProtocols(await api.getProtocols().then((p) => p.protocols));
      say(`saved “${protocol.name}”`);
    } catch (e: any) {
      say(e.message, true);
    }
  };

  const publish = async () => {
    if (!address) return say("sign in to publish", true);
    try {
      let id = protocolId;
      if (!id) {
        const { protocol } = await api.saveProtocol(graph, graph.name);
        id = protocol.id;
        setProtocolId(id);
      }
      const { cid } = await api.publishProtocol(id!);
      await navigator.clipboard.writeText(cid).catch(() => {});
      say(`published ${cid.slice(0, 18)}… (copied)`);
      setProtocols(await api.getProtocols().then((p) => p.protocols));
    } catch (e: any) {
      say(e.message, true);
    }
  };

  const buildPlan = async () => {
    try {
      const result = await api.planGraph(graph);
      if (!result.ok || !result.plan) {
        setReport(result.report);
        return say(result.error ?? "fix the wiring first", true);
      }
      setPlan(result.plan);
      setSteps([]);
      setDrawer("deploy");
    } catch (e: any) {
      say(e.message, true);
    }
  };

  const deploy = async () => {
    if (!plan) return;
    setDeploying(true);
    try {
      const result = await runPlan(plan, setSteps);
      setDeployed(result.addresses);
      if (result.failed) {
        say("deployment stopped — see the plan panel", true);
      } else {
        say(`deployed ${Object.keys(result.addresses).length} contracts`);
        if (protocolId && address) {
          const chainId = Number(
            await (window as any).ethereum.request({ method: "eth_chainId" })
          );
          await api
            .recordDeployment(protocolId, {
              chainId,
              network: String(chainId),
              addresses: result.addresses,
              txs: result.txs,
            })
            .catch(() => {});
        }
      }
    } catch (e: any) {
      say(e.message, true);
    } finally {
      setDeploying(false);
    }
  };

  const compose = async (prompt: string, promptId?: string) => {
    setComposing(true);
    try {
      const result = await api.compose(prompt, promptId, graph.nodes.length ? graph : undefined);
      setGraph({ ...result.graph, name: result.graph.name || graph.name });
      setReport(result.report);
      setDrawer("none");
      say(
        result.report.ok
          ? "composed — review it before deploying"
          : "composed with issues — check the inspector",
        !result.report.ok
      );
    } catch (e: any) {
      say(e.message, true);
    } finally {
      setComposing(false);
    }
  };

  const openTemplate = (id: string) => {
    const template = catalog?.templates.find((t) => t.id === id);
    if (!template || !catalog) return;
    setGraph({
      name: template.name,
      description: template.summary,
      nodes: template.nodes.map((n) => {
        const spec = catalog.blocks.find((b) => b.id === n.block);
        const params: Record<string, any> = {};
        for (const p of spec?.params ?? []) params[p.name] = p.default;
        return { ...n, params: { ...params, ...n.params } };
      }),
      edges: template.edges,
    });
    setProtocolId(null);
    setDeployed({});
    setPlan(null);
    setDrawer("none");
  };

  const viewSource = async (block: BlockSpec, tab: "source" | "audit" = "source") => {
    setSourceOf({ block });
    setSourceTab(tab);
    setDrawer("source");
    try {
      const full = await api.getBlock(block.id);
      setSourceOf({ block: full.block, artifact: full.artifact, audit: full.audit ?? null });
    } catch (e: any) {
      say(e.message, true);
    }
  };

  const addToMiddle = (block: BlockSpec) => {
    const params: Record<string, any> = {};
    for (const p of block.params) params[p.name] = p.default;
    const id = `n${Date.now().toString(36)}`;
    setGraph((g) => ({
      ...g,
      nodes: [...g.nodes, { id, block: block.id, x: 320, y: 160 + g.nodes.length * 40, params }],
    }));
    setSelected(id);
  };

  // ── render ──────────────────────────────────────────────────────────────

  if (boot) {
    return (
      <main style={{ display: "grid", placeItems: "center", height: "100vh", padding: 24 }}>
        <div style={{ maxWidth: 460, textAlign: "center", lineHeight: 1.7 }}>
          <div style={{ fontSize: 24, marginBottom: 12 }}>✦</div>
          <div style={{ color: "var(--danger)", marginBottom: 10 }}>{boot}</div>
          <div style={{ fontSize: 11, color: "var(--dim)" }}>
            The composer needs its API. Start it with <code>m defi/serve</code>, or point this
            page at a different one:
          </div>
          <div style={{ display: "flex", gap: 6, marginTop: 12 }}>
            <input id="api-url" placeholder="http://localhost:50500" />
            <button
              onClick={() => {
                const el = document.getElementById("api-url") as HTMLInputElement;
                if (el?.value) {
                  api.setApiOverride(el.value);
                  window.location.reload();
                }
              }}
            >
              use
            </button>
          </div>
        </div>
      </main>
    );
  }

  if (!catalog) {
    return (
      <main style={{ display: "grid", placeItems: "center", height: "100vh", color: "var(--dim)" }}>
        loading catalog…
      </main>
    );
  }

  return (
    <main style={{ height: "100vh", display: "flex", flexDirection: "column" }}>
      {/* top bar */}
      <header
        style={{
          display: "flex",
          alignItems: "center",
          gap: 9,
          padding: "9px 12px",
          borderBottom: "1px solid var(--line)",
          background: "var(--panel)",
        }}
      >
        <span style={{ color: "var(--accent)", fontSize: 15 }}>✦</span>
        <span style={{ fontWeight: 700, letterSpacing: "0.06em", fontSize: 12 }}>DEFI</span>
        <nav className="nav">
          {VIEWS.map((v) => (
            <button
              key={v.id}
              className={view === v.id ? "active" : ""}
              onClick={() => {
                // A nav click is a fresh visit — only EXPLORE carries a prefill.
                if (v.id === "modules") setPrefill(null);
                setView(v.id);
              }}
            >
              {v.label}
            </button>
          ))}
        </nav>
        {view !== "compose" && <div style={{ flex: 1 }} />}
        {view === "compose" && (<>
        <input
          value={graph.name}
          onChange={(e) => setGraph({ ...graph, name: e.target.value })}
          style={{ width: 220, marginLeft: 6 }}
        />

        <span className={`pill ${report ? (report.ok ? "ok" : "bad") : ""}`}>
          <span className="dot" />
          {report ? (report.ok ? "type-checks" : `${report.issues.filter((i) => i.level === "error").length} issues`) : "…"}
        </span>
        <span
          className={`pill ${compileInfo?.ready ? "ok" : compileInfo?.error ? "bad" : "warn"}`}
          title={compileInfo?.error || compileInfo?.version || ""}
        >
          <span className="dot" />
          {compileInfo?.ready
            ? `solc ${String(compileInfo.version ?? "").split("+")[0] || "ready"}`
            : compileInfo?.error
              ? "solc missing"
              : "compiling…"}
        </span>

        <div style={{ flex: 1 }} />

        <select
          value=""
          onChange={(e) => e.target.value && openTemplate(e.target.value)}
          style={{ width: 150 }}
        >
          <option value="">templates…</option>
          {catalog.templates.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
        <button onClick={() => setDrawer(drawer === "library" ? "none" : "library")}>
          saved ({protocols.length})
        </button>
        <button onClick={() => setDrawer(drawer === "prompts" ? "none" : "prompts")}>
          ✦ AI compose
        </button>
        <button onClick={save} disabled={!address}>
          save
        </button>
        <button onClick={publish} disabled={!address}>
          publish
        </button>
        <button className="primary" onClick={buildPlan} disabled={!report?.ok || !compileInfo?.ready}>
          deploy…
        </button>
        </>)}
        <button className="ghost" onClick={address ? () => { api.clearSession(); setAddress(null); } : connect}>
          {address ? `${address.slice(0, 6)}…${address.slice(-4)}` : "connect wallet"}
        </button>
      </header>

      {view === "hub" && (
        <Hub
          say={say}
          onExplore={(pf) => {
            setPrefill(pf);
            setView("modules");
          }}
        />
      )}
      {view === "modules" && (
        <Modules say={say} address={address} prefill={prefill} onOpenTreasury={() => setView("treasury")} onOpenBook={() => setView("book")} />
      )}
      {view === "book" && <Book say={say} onOpenModules={() => setView("modules")} />}
      {view === "trade" && (
        <div style={{ flex: 1, position: "relative", minHeight: 0 }}>
          <div className="rail-empty" style={{ padding: "40px 48px", maxWidth: 520 }}>
            <div style={{ fontSize: 22, color: "var(--accent)" }}>✦</div>
            <div style={{ marginTop: 10, lineHeight: 1.7 }}>
              The trading desk. Quote and swap on Ethereum and Base (Uniswap V3), Solana (Jupiter) and
              Bittensor (dTAO pools) — each trade signed by the module that owns that chain. Buying a
              module&apos;s receipt token from MODULES goes through this same desk.
            </div>
          </div>
          <DexDesk onClose={() => setView("modules")} say={say} />
        </div>
      )}
      {view === "compose" && (
      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
        <aside
          style={{
            width: 254,
            borderRight: "1px solid var(--line)",
            background: "var(--panel)",
            flexShrink: 0,
          }}
        >
          <Palette catalog={catalog} onInspect={viewSource} onAdd={addToMiddle} />
        </aside>

        <section style={{ flex: 1, position: "relative", minWidth: 0 }}>
          <Canvas
            catalog={catalog}
            graph={graph}
            report={report}
            selected={selected}
            deployed={deployed}
            onSelect={setSelected}
            onChange={setGraph}
          />

          {drawer === "prompts" && (
            <PromptDrawer
              onClose={() => setDrawer("none")}
              onCompose={compose}
              composing={composing}
            />
          )}


          {drawer === "library" && (
            <div className="drawer">
              <div style={{ padding: "11px 12px", borderBottom: "1px solid var(--line)", display: "flex" }}>
                <span style={{ fontSize: 12, fontWeight: 600, flex: 1 }}>Saved protocols</span>
                <button className="ghost" onClick={() => setDrawer("none")} style={{ padding: "2px 8px" }}>
                  ×
                </button>
              </div>
              <div className="scroll" style={{ flex: 1, padding: 12, display: "flex", flexDirection: "column", gap: 7 }}>
                {protocols.length === 0 && (
                  <div style={{ fontSize: 11, color: "var(--dim)", textAlign: "center", padding: 14 }}>
                    nothing saved yet
                  </div>
                )}
                {protocols.map((p) => (
                  <div key={p.id} className="card click" onClick={() => {
                    setGraph(p.graph);
                    setProtocolId(p.id);
                    setDeployed(p.deployments.at(-1)?.addresses ?? {});
                    setDrawer("none");
                  }}>
                    <div style={{ display: "flex", gap: 7 }}>
                      <span style={{ fontSize: 12, fontWeight: 600, flex: 1 }}>{p.name}</span>
                      <span className="pill">{p.graph.nodes.length} blocks</span>
                    </div>
                    <div className="mono-small" style={{ marginTop: 5 }}>
                      {p.owner.slice(0, 10)}… · {new Date(p.updated * 1000).toLocaleDateString()}
                      {p.deployments.length > 0 && ` · ${p.deployments.length} deployment(s)`}
                    </div>
                    {p.cid && <div className="mono-small" style={{ marginTop: 4 }}>{p.cid}</div>}
                  </div>
                ))}
                <div className="label" style={{ marginTop: 10 }}>Import by CID</div>
                <div style={{ display: "flex", gap: 6 }}>
                  <input id="import-cid" placeholder="bafk…" />
                  <button
                    onClick={async () => {
                      const el = document.getElementById("import-cid") as HTMLInputElement;
                      if (!el?.value.trim()) return;
                      try {
                        const { protocol } = await api.importProtocol(el.value.trim());
                        setGraph(protocol.graph);
                        setProtocolId(protocol.id);
                        setDrawer("none");
                        say(`imported “${protocol.name}”`);
                      } catch (e: any) {
                        say(e.message, true);
                      }
                    }}
                  >
                    import
                  </button>
                </div>
              </div>
            </div>
          )}

          {drawer === "source" && sourceOf && (
            <div className="drawer" style={{ width: 560 }}>
              <div style={{ padding: "11px 12px", borderBottom: "1px solid var(--line)", display: "flex", gap: 8, alignItems: "center" }}>
                <span style={{ fontSize: 12, fontWeight: 600, flex: 1 }}>
                  {sourceOf.block.icon} {sourceOf.block.contract}.sol
                </span>
                <button
                  className={sourceTab === "source" ? "" : "ghost"}
                  style={{ padding: "2px 8px" }}
                  onClick={() => setSourceTab("source")}
                >
                  source
                </button>
                <button
                  className={sourceTab === "audit" ? "" : "ghost"}
                  style={{ padding: "2px 8px", display: "flex", gap: 6, alignItems: "center" }}
                  onClick={() => setSourceTab("audit")}
                >
                  audit <RiskPill summary={sourceOf.block.audit} compact />
                </button>
                {sourceOf.artifact && sourceTab === "source" && (
                  <span className="pill">{sourceOf.artifact.deployedSize} bytes</span>
                )}
                <button className="ghost" onClick={() => setDrawer("none")} style={{ padding: "2px 8px" }}>
                  ×
                </button>
              </div>
              <div className="scroll" style={{ flex: 1, padding: 12 }}>
                {sourceTab === "audit" && (
                  <AuditView audit={sourceOf.audit} loading={sourceOf.audit === undefined} />
                )}
                {sourceTab === "source" && (
                <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.6, marginBottom: 12 }}>
                  {sourceOf.block.docs || sourceOf.block.summary}
                </div>
                )}
                {sourceTab === "source" && (
                <pre
                  style={{
                    margin: 0,
                    fontSize: 11,
                    lineHeight: 1.55,
                    whiteSpace: "pre-wrap",
                    color: "var(--muted)",
                  }}
                >
                  {sourceOf.block.source ?? "loading…"}
                </pre>
                )}
              </div>
            </div>
          )}

          {drawer === "deploy" && plan && (
            <div className="drawer" style={{ width: 440 }}>
              <div style={{ padding: "11px 12px", borderBottom: "1px solid var(--line)", display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ fontSize: 12, fontWeight: 600, flex: 1 }}>Deployment plan</span>
                <span className="pill">{plan.steps.length} txs</span>
                <button className="ghost" onClick={() => setDrawer("none")} style={{ padding: "2px 8px" }}>
                  ×
                </button>
              </div>
              <div className="scroll" style={{ flex: 1, padding: 12 }}>
                <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.6, marginBottom: 12 }}>
                  Your wallet signs every one of these. Addresses from earlier steps are
                  substituted into later ones as they confirm — so keep the tab open.
                </div>
                {plan.warnings.map((w, i) => (
                  <div key={i} className="issue warning" style={{ marginBottom: 8 }}>
                    <span>!</span>
                    <span>{w}</span>
                  </div>
                ))}
                <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                  {plan.steps.map((step, i) => {
                    const state = steps[i];
                    return (
                      <div key={i} className={`step ${state?.status ?? ""}`}>
                        <span style={{ color: "var(--dim)", width: 18 }}>{i + 1}</span>
                        <div style={{ flex: 1 }}>
                          <div>
                            {step.kind === "deploy"
                              ? `deploy ${step.label}`
                              : `wire ${step.note}`}
                          </div>
                          {step.kind === "deploy" && (
                            <div className="mono-small">{step.contract}</div>
                          )}
                          {state?.address && (
                            <div className="mono-small" style={{ color: "var(--accent)" }}>
                              {state.address}
                            </div>
                          )}
                          {state?.error && (
                            <div className="mono-small" style={{ color: "var(--danger)" }}>
                              {state.error}
                            </div>
                          )}
                        </div>
                        <span style={{ color: "var(--dim)", fontSize: 10 }}>
                          {state?.status === "done"
                            ? "✓"
                            : state?.status === "running"
                              ? "…"
                              : state?.status === "failed"
                                ? "✕"
                                : ""}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
              <div style={{ padding: 12, borderTop: "1px solid var(--line)" }}>
                <button className="primary" style={{ width: "100%" }} onClick={deploy} disabled={deploying}>
                  {deploying ? "deploying…" : `sign ${plan.steps.length} transactions`}
                </button>
              </div>
            </div>
          )}
        </section>

        <aside
          style={{
            width: 300,
            borderLeft: "1px solid var(--line)",
            background: "var(--panel)",
            flexShrink: 0,
          }}
        >
          <Inspector
            catalog={catalog}
            graph={graph}
            node={selectedNode}
            report={report}
            onChange={setGraph}
            onViewSource={viewSource}
          />
        </aside>
      </div>
      )}

      {/* TREASURY keeps its full-window desk: the yields table beside the
          BlocTime treasury, because they are one decision. */}
      {view === "treasury" && (
        <YieldDesk onClose={() => setView("modules")} say={say} address={address} />
      )}

      {toast && (
        <div
          style={{
            position: "fixed",
            bottom: 18,
            left: "50%",
            transform: "translateX(-50%)",
            background: "var(--panel-2)",
            border: `1px solid ${toast.bad ? "#3a2126" : "var(--accent-dim)"}`,
            color: toast.bad ? "var(--danger)" : "var(--text)",
            padding: "9px 16px",
            borderRadius: 8,
            fontSize: 12,
            zIndex: 100,
            maxWidth: "70vw",
          }}
        >
          {toast.text}
        </div>
      )}
    </main>
  );
}
