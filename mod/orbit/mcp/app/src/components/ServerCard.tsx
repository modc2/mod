"use client";

import { Server } from "@/lib/api";

/** Compact numbers: a directory card has room for "12.4k", not "12,431". */
export function compact(n: number | null | undefined): string {
  if (n === null || n === undefined) return "";
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`;
  return `${(n / 1_000_000).toFixed(1)}m`;
}

export function SourceBadge({ id }: { id: string }) {
  return (
    <span className="badge" style={{ ["--hue" as string]: `var(--src-${id}, var(--accent))` }}>
      {id}
    </span>
  );
}

export default function ServerCard({ server, onOpen }: { server: Server; onOpen: () => void }) {
  const sources = server.sources ?? [server.source];
  const remote = server.transports.some((t) => t !== "stdio");
  return (
    <article className="card" onClick={onOpen}>
      <div className="row" style={{ gap: 6 }}>
        {sources.slice(0, 3).map((s) => (
          <SourceBadge key={s} id={s} />
        ))}
        {sources.length > 3 && <span className="tag">+{sources.length - 3}</span>}
        {server.cid && <span className="badge good">pinned</span>}
      </div>

      <h3>{server.name || server.title}</h3>
      <p className="desc">{server.description || "no description published"}</p>

      <div className="meta">
        {server.stars !== null && <span>★ {compact(server.stars)}</span>}
        {server.downloads !== null && <span>↓ {compact(server.downloads)}/mo</span>}
        {server.license && <span>{server.license}</span>}
        {server.tools ? <span>{server.tools} tools</span> : null}
        {remote && <span className="muted">remote</span>}
      </div>

      <div className="foot">
        {server.open_source ? (
          <span className="badge good">open source</span>
        ) : (
          <span className="badge plain">no public source</span>
        )}
        {server.categories.slice(0, 2).map((c) => (
          <span key={c} className="tag">
            {c}
          </span>
        ))}
      </div>
    </article>
  );
}
