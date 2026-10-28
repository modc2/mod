"use client";

import { useEffect, useMemo, useState } from "react";
import { getAgentStatus, getPrompts, importPrompt } from "../lib/api";
import type { Prompt } from "../lib/types";

type Props = {
  onClose: () => void;
  onCompose: (prompt: string, promptId?: string) => Promise<void>;
  composing: boolean;
};

/// The agent protocol's prompt library, browsed in place.
///
/// These prompts are not ours — they live in the agent mod, CID-pinned and
/// shared across every console on the fleet. Retrieving one here and using it to
/// frame a composition request is the whole point of speaking the protocol
/// instead of keeping a private copy.
export default function PromptDrawer({ onClose, onCompose, composing }: Props) {
  const [prompts, setPrompts] = useState<Prompt[]>([]);
  const [status, setStatus] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [selected, setSelected] = useState<Prompt | null>(null);
  const [request, setRequest] = useState("");
  const [cid, setCid] = useState("");
  const [note, setNote] = useState<string | null>(null);

  const load = async () => {
    setError(null);
    try {
      const [s, p] = await Promise.all([getAgentStatus(), getPrompts()]);
      setStatus(s);
      setPrompts(p.prompts ?? []);
    } catch (e: any) {
      setError(e.message);
      try {
        setStatus(await getAgentStatus());
      } catch {
        /* the status call is best-effort too */
      }
    }
  };

  useEffect(() => {
    load();
  }, []);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return prompts;
    return prompts.filter(
      (p) =>
        p.name?.toLowerCase().includes(needle) ||
        p.description?.toLowerCase().includes(needle) ||
        p.text?.toLowerCase().includes(needle) ||
        (p.tags ?? []).some((t) => t.toLowerCase().includes(needle))
    );
  }, [prompts, query]);

  const doImport = async () => {
    if (!cid.trim()) return;
    setNote(null);
    try {
      await importPrompt(cid.trim());
      setCid("");
      setNote("imported — refreshing the library");
      await load();
    } catch (e: any) {
      setNote(e.message);
    }
  };

  return (
    <div className="drawer" style={{ width: 420 }}>
      <div
        style={{
          padding: "11px 12px",
          borderBottom: "1px solid var(--line)",
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        <span style={{ fontSize: 12, fontWeight: 600, flex: 1 }}>Agent protocol</span>
        <span className={`pill ${status?.reachable ? "ok" : "bad"}`}>
          <span className="dot" />
          {status?.reachable ? `agent ${status?.agent?.version ?? "live"}` : "agent offline"}
        </span>
        <button className="ghost" onClick={onClose} style={{ padding: "2px 8px" }}>
          ×
        </button>
      </div>

      <div className="scroll" style={{ flex: 1, padding: 12 }}>
        <div className="label" style={{ marginBottom: 8 }}>
          Compose from a description
        </div>
        <textarea
          rows={3}
          value={request}
          onChange={(e) => setRequest(e.target.value)}
          placeholder="a stablecoin vault that farms a fixed-yield strategy, governed by a 48h timelock"
        />
        {selected && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              marginTop: 8,
              fontSize: 11,
              color: "var(--muted)",
            }}
          >
            <span className="pill ok">
              <span className="dot" /> framed by “{selected.name}”
            </span>
            <button
              className="ghost"
              style={{ padding: "1px 7px", fontSize: 10 }}
              onClick={() => setSelected(null)}
            >
              clear
            </button>
          </div>
        )}
        <button
          className="primary"
          style={{ width: "100%", marginTop: 9 }}
          disabled={composing || !request.trim() || !status?.reachable}
          onClick={() => onCompose(request.trim(), selected?.id)}
        >
          {composing ? "composing…" : "compose protocol"}
        </button>
        {!status?.reachable && (
          <div className="mono-small" style={{ marginTop: 7, lineHeight: 1.6 }}>
            The agent module is not answering at {status?.url ?? "its URL"}. Everything else in
            this console — designing, compiling, deploying — still works.
          </div>
        )}

        <div
          className="label"
          style={{ margin: "22px 0 8px", display: "flex", justifyContent: "space-between" }}
        >
          <span>Prompt library ({prompts.length})</span>
          <button
            className="ghost"
            style={{ padding: "0 6px", fontSize: 10 }}
            onClick={load}
          >
            refresh
          </button>
        </div>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="search prompts…"
        />

        {error && (
          <div className="issue" style={{ marginTop: 9 }}>
            <span>✕</span>
            <span>{error}</span>
          </div>
        )}

        <div style={{ display: "flex", flexDirection: "column", gap: 7, marginTop: 10 }}>
          {filtered.map((prompt) => (
            <div key={prompt.id} className="card">
              <div
                style={{ display: "flex", alignItems: "center", gap: 7, cursor: "pointer" }}
                onClick={() => setOpen(open === prompt.id ? null : prompt.id)}
              >
                <span style={{ fontSize: 12, fontWeight: 600, flex: 1 }}>{prompt.name}</span>
                {prompt.builtin && <span className="pill">builtin</span>}
              </div>
              <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4, lineHeight: 1.45 }}>
                {prompt.description}
              </div>
              {open === prompt.id && (
                <>
                  <div
                    className="scroll"
                    style={{
                      marginTop: 8,
                      padding: 8,
                      maxHeight: 190,
                      background: "#0a1017",
                      border: "1px solid var(--line)",
                      borderRadius: 6,
                      fontSize: 11,
                      lineHeight: 1.55,
                      whiteSpace: "pre-wrap",
                      color: "var(--muted)",
                    }}
                  >
                    {prompt.text}
                  </div>
                  <div style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap" }}>
                    <button onClick={() => setSelected(prompt)}>use to frame</button>
                    <button
                      className="ghost"
                      onClick={() => navigator.clipboard.writeText(prompt.text)}
                    >
                      copy text
                    </button>
                    {prompt.cid && (
                      <button
                        className="ghost"
                        onClick={() => navigator.clipboard.writeText(prompt.cid!)}
                      >
                        copy CID
                      </button>
                    )}
                  </div>
                </>
              )}
              <div style={{ display: "flex", gap: 4, marginTop: 7, flexWrap: "wrap" }}>
                {(prompt.tags ?? []).map((tag) => (
                  <span key={tag} className="pill">
                    {tag}
                  </span>
                ))}
              </div>
              {prompt.cid && (
                <div className="mono-small" style={{ marginTop: 6 }}>
                  {prompt.cid}
                </div>
              )}
            </div>
          ))}
          {filtered.length === 0 && !error && (
            <div style={{ fontSize: 11, color: "var(--dim)", padding: 10, textAlign: "center" }}>
              no prompts match
            </div>
          )}
        </div>

        <div className="label" style={{ margin: "22px 0 8px" }}>
          Import a shared prompt
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          <input value={cid} onChange={(e) => setCid(e.target.value)} placeholder="Qm… / bafy…" />
          <button onClick={doImport} disabled={!cid.trim()}>
            import
          </button>
        </div>
        {note && (
          <div className="mono-small" style={{ marginTop: 7 }}>
            {note}
          </div>
        )}
        <div className="mono-small" style={{ marginTop: 12, lineHeight: 1.6 }}>
          Prompts live in the agent module, not here — one library, every console.
          {status?.url ? ` Source: ${status.url}` : ""}
        </div>
      </div>
    </div>
  );
}
