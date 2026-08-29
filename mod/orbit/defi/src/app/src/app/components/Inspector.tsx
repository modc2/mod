"use client";

import type { BlockSpec, Catalog, Graph, GraphNode, Report } from "../lib/types";

type Props = {
  catalog: Catalog;
  graph: Graph;
  node: GraphNode | null;
  report: Report | null;
  onChange: (graph: Graph) => void;
  onViewSource: (block: BlockSpec) => void;
};

export default function Inspector({
  catalog,
  graph,
  node,
  report,
  onChange,
  onViewSource,
}: Props) {
  if (!node) {
    return (
      <div className="scroll" style={{ padding: 12, height: "100%" }}>
        <div className="label" style={{ marginBottom: 10 }}>
          Issues
        </div>
        {report && report.issues.length === 0 && (
          <div className="pill ok">
            <span className="dot" /> this composition type-checks
          </div>
        )}
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {(report?.issues ?? []).map((issue, i) => (
            <div key={i} className={`issue ${issue.level}`}>
              <span>{issue.level === "error" ? "✕" : "!"}</span>
              <span>
                {issue.message}
                {issue.node && (
                  <span className="mono-small" style={{ display: "block", marginTop: 3 }}>
                    {issue.node}
                    {issue.port ? `.${issue.port}` : ""}
                  </span>
                )}
              </span>
            </div>
          ))}
        </div>
        {report?.ok && report.order.length > 0 && (
          <>
            <div className="label" style={{ margin: "18px 0 8px" }}>
              Deployment order
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              {report.order.map((id, i) => {
                const n = graph.nodes.find((x) => x.id === id);
                const spec = catalog.blocks.find((b) => b.id === n?.block);
                return (
                  <div key={id} style={{ fontSize: 11, color: "var(--muted)" }}>
                    <span style={{ color: "var(--dim)" }}>{i + 1}.</span> {spec?.icon}{" "}
                    {n?.label || spec?.name || id}
                  </div>
                );
              })}
            </div>
          </>
        )}
        <div style={{ marginTop: 20, fontSize: 11, color: "var(--dim)", lineHeight: 1.6 }}>
          Select a block to edit its parameters. Drag from a block&apos;s right-hand dot into
          another block&apos;s port to wire them; click a wire to disconnect it.
        </div>
      </div>
    );
  }

  const spec = catalog.blocks.find((b) => b.id === node.block);
  if (!spec) return <div style={{ padding: 12 }}>unknown block “{node.block}”</div>;

  const setParam = (name: string, value: any) =>
    onChange({
      ...graph,
      nodes: graph.nodes.map((n) =>
        n.id === node.id ? { ...n, params: { ...n.params, [name]: value } } : n
      ),
    });

  const nodeIssues = (report?.issues ?? []).filter((i) => i.node === node.id);

  return (
    <div className="scroll" style={{ padding: 12, height: "100%" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
        <span style={{ fontSize: 15 }}>{spec.icon}</span>
        <span style={{ fontSize: 13, fontWeight: 600 }}>{spec.name}</span>
      </div>
      <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.55, marginBottom: 12 }}>
        {spec.docs || spec.summary}
      </div>

      <button className="ghost" style={{ width: "100%" }} onClick={() => onViewSource(spec)}>
        read {spec.contract}.sol
      </button>

      {nodeIssues.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, margin: "12px 0" }}>
          {nodeIssues.map((issue, i) => (
            <div key={i} className={`issue ${issue.level}`}>
              <span>{issue.level === "error" ? "✕" : "!"}</span>
              <span>{issue.message}</span>
            </div>
          ))}
        </div>
      )}

      <div className="label" style={{ margin: "18px 0 8px" }}>
        Label
      </div>
      <input
        value={node.label ?? ""}
        placeholder={spec.name}
        onChange={(e) =>
          onChange({
            ...graph,
            nodes: graph.nodes.map((n) =>
              n.id === node.id ? { ...n, label: e.target.value || undefined } : n
            ),
          })
        }
      />

      <div className="label" style={{ margin: "18px 0 8px" }}>
        Wiring
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {spec.inputs.length === 0 && (
          <div style={{ fontSize: 11, color: "var(--dim)" }}>this block takes no inputs</div>
        )}
        {spec.inputs.map((port) => {
          const edge = graph.edges.find((e) => e.to === node.id && e.port === port.id);
          const source = edge && graph.nodes.find((n) => n.id === edge.from);
          const sourceSpec = source && catalog.blocks.find((b) => b.id === source.block);
          return (
            <div
              key={port.id}
              style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11 }}
            >
              <span
                className="dot"
                style={{ color: catalog.portTypes[port.type]?.color, flexShrink: 0 }}
              />
              <span style={{ color: "var(--muted)", width: 92 }}>{port.label}</span>
              <span style={{ flex: 1, color: edge ? "var(--text)" : "var(--dim)" }}>
                {source ? source.label || sourceSpec?.name : port.required ? "— required" : "— none"}
              </span>
              {edge && (
                <button
                  className="ghost"
                  style={{ padding: "1px 6px", fontSize: 10 }}
                  onClick={() =>
                    onChange({
                      ...graph,
                      edges: graph.edges.filter(
                        (e) => !(e.to === node.id && e.port === port.id)
                      ),
                    })
                  }
                >
                  unwire
                </button>
              )}
            </div>
          );
        })}
      </div>

      {spec.params.length > 0 && (
        <>
          <div className="label" style={{ margin: "18px 0 8px" }}>
            Parameters
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 11 }}>
            {spec.params.map((param) => {
              const value = node.params[param.name] ?? param.default;
              return (
                <label key={param.name} style={{ display: "block" }}>
                  <div
                    style={{
                      fontSize: 11,
                      color: "var(--muted)",
                      marginBottom: 4,
                      display: "flex",
                      justifyContent: "space-between",
                    }}
                  >
                    <span>{param.label}</span>
                    <span className="mono-small">{param.type}</span>
                  </div>
                  {param.type === "bool" ? (
                    <button
                      className="ghost"
                      style={{ width: "100%", color: value ? "var(--accent)" : "var(--dim)" }}
                      onClick={() => setParam(param.name, !value)}
                    >
                      {value ? "on" : "off"}
                    </button>
                  ) : (
                    <input
                      value={value === "$owner" ? "" : String(value ?? "")}
                      placeholder={value === "$owner" ? "your connected wallet" : ""}
                      onChange={(e) => {
                        const raw = e.target.value;
                        if (param.type === "address" && raw.trim() === "") {
                          setParam(param.name, "$owner");
                        } else {
                          setParam(param.name, raw);
                        }
                      }}
                    />
                  )}
                  {param.help && (
                    <div className="mono-small" style={{ marginTop: 4 }}>
                      {param.help}
                    </div>
                  )}
                  {param.scale !== undefined && (
                    <div className="mono-small" style={{ marginTop: 4 }}>
                      whole units — scaled by{" "}
                      {typeof param.scale === "number" ? `1e${param.scale}` : `the ${param.scale} param`}
                    </div>
                  )}
                </label>
              );
            })}
          </div>
        </>
      )}

      {spec.wires && spec.wires.length > 0 && (
        <div style={{ marginTop: 18, fontSize: 11, color: "var(--dim)", lineHeight: 1.6 }}>
          Wiring {spec.wires.map((w) => w.when).join(", ")} adds a post-deployment call
          ({spec.wires.map((w) => w.method).join(", ")}) rather than a constructor argument — that
          is what lets two blocks point at each other.
        </div>
      )}
    </div>
  );
}
