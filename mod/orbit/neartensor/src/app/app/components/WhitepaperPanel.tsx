"use client";

import { useEffect, useState, type ReactNode } from "react";
import { api } from "@/lib/api";

// Tiny markdown renderer: headings, paragraphs, lists, tables, code blocks,
// `code` and **bold**. Enough for src/whitepaper.md without a dependency.
function inline(text: string): ReactNode[] {
  return text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).map((part, i) => {
    if (part.startsWith("`")) return <code key={i} className="text-nt-accent">{part.slice(1, -1)}</code>;
    if (part.startsWith("**")) return <strong key={i} className="text-nt-text">{part.slice(2, -2)}</strong>;
    return part;
  });
}

function render(md: string): ReactNode[] {
  const lines = md.split("\n");
  const out: ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith("```")) {
      const code: string[] = [];
      for (i++; i < lines.length && !lines[i].startsWith("```"); i++) code.push(lines[i]);
      out.push(<pre key={i} className="p-3 rounded border border-nt-border bg-nt-bg text-xs overflow-x-auto">{code.join("\n")}</pre>);
      i++;
    } else if (line.startsWith("#")) {
      const level = line.match(/^#+/)![0].length;
      const cls = level === 1 ? "text-xl font-bold text-nt-accent" : "text-sm font-semibold text-nt-text pt-4";
      out.push(<div key={i} className={cls}>{inline(line.replace(/^#+\s*/, ""))}</div>);
      i++;
    } else if (line.startsWith("|")) {
      const rows: string[][] = [];
      for (; i < lines.length && lines[i].startsWith("|"); i++) {
        if (!/^\|[\s\-|]+\|$/.test(lines[i])) rows.push(lines[i].slice(1, -1).split("|").map((c) => c.trim()));
      }
      out.push(
        <table key={i} className="w-full text-xs border border-nt-border">
          <tbody>
            {rows.map((r, ri) => (
              <tr key={ri} className={ri === 0 ? "bg-nt-panel text-nt-muted" : "border-t border-nt-border"}>
                {r.map((c, ci) => <td key={ci} className="px-3 py-1.5 align-top">{inline(c)}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      );
    } else if (/^\d+\.\s|^- /.test(line)) {
      const ordered = /^\d+\./.test(line);
      const items: string[] = [];
      for (; i < lines.length && /^\d+\.\s|^- |^\s{2,}\S/.test(lines[i]); i++) {
        if (/^\s{2,}/.test(lines[i]) && items.length) items[items.length - 1] += " " + lines[i].trim();
        else items.push(lines[i].replace(/^\d+\.\s|^- /, ""));
      }
      const Tag = ordered ? "ol" : "ul";
      out.push(
        <Tag key={i} className={`${ordered ? "list-decimal" : "list-disc"} pl-5 space-y-1 text-xs text-nt-muted`}>
          {items.map((it, k) => <li key={k}>{inline(it)}</li>)}
        </Tag>
      );
    } else if (line.trim()) {
      const para: string[] = [];
      for (; i < lines.length && lines[i].trim() && !/^(#|\||```|- |\d+\.\s)/.test(lines[i]); i++) para.push(lines[i]);
      out.push(<p key={i} className="text-xs leading-relaxed text-nt-muted">{inline(para.join(" "))}</p>);
    } else {
      i++;
    }
  }
  return out;
}

export default function WhitepaperPanel() {
  const [md, setMd] = useState<string | null>(null);
  useEffect(() => {
    api("neartensor/whitepaper").then((r) => setMd(typeof r === "string" ? r : `Could not load: ${r?.error}`));
  }, []);
  return (
    <article className="max-w-3xl mx-auto space-y-3">
      {md === null ? <div className="text-xs text-nt-muted">Loading…</div> : render(md)}
    </article>
  );
}
