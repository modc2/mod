import Link from "next/link";
import type { Module } from "@/lib/api";

// Deterministic accent for modules that don't declare a color — hash the name
// into a pleasant hue so the grid stays colorful without being random per-render.
function hueFromName(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 360;
  return `hsl(${h} 70% 64%)`;
}

export function ModuleCard({ m, index }: { m: Module; index: number }) {
  const glow = m.color || hueFromName(m.name);
  const label = (m.icon || m.name[0] || "m").slice(0, 1);
  const depCount = m.deps?.length ?? 0;
  return (
    <Link
      href={`/mods/${m.name}`}
      className="card"
      style={
        {
          "--glow": glow,
          animationDelay: `${Math.min(index, 24) * 28}ms`,
        } as React.CSSProperties
      }
    >
      <div className="card-top">
        <span className="avatar" style={{ background: glow }}>
          {label}
        </span>
        <div>
          <div className="title">{m.name}</div>
          <div className="ver">v{m.version}</div>
        </div>
        {m.registered && (
          <span className="onchain" title="Registered in the on-chain Registry">
            ⛓ on-chain
          </span>
        )}
      </div>
      <div className="desc">{m.description || "No description provided."}</div>
      <div className="card-foot">
        {m.has_rust && <span className="tag rust">rust</span>}
        {m.has_app && <span className="tag app">app</span>}
        {m.fn_count > 0 && <span className="tag fns">{m.fn_count} fns</span>}
        {depCount > 0 && (
          <span className="tag deps" title={`Depends on: ${m.deps.join(", ")}`}>
            ↳ {depCount} dep{depCount > 1 ? "s" : ""}
          </span>
        )}
        {m.port && <span className="tag">:{m.port}</span>}
      </div>
    </Link>
  );
}
