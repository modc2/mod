"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  type ChatMessage,
  type MeResponse,
  type Provider,
} from "@/lib/api";
import { buildModToken, connectWallet, hasWallet, shortAddress } from "@/lib/wallet";

export default function Page() {
  // ── auth ──────────────────────────────────────────────────
  const [address, setAddress] = useState<string | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [me, setMe] = useState<MeResponse | null>(null);

  // ── providers / models ────────────────────────────────────
  const [providers, setProviders] = useState<Provider[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState<string>("");

  // ── chat ──────────────────────────────────────────────────
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // ── modals ────────────────────────────────────────────────
  const [keyModal, setKeyModal] = useState(false);
  const [addModal, setAddModal] = useState(false);

  const threadRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);

  const activeProvider = useMemo(
    () => providers.find((p) => p.name === active) || null,
    [providers, active]
  );
  const meProvider = useMemo(
    () => me?.providers.find((p) => p.name === active) || null,
    [me, active]
  );
  const canChat = !!meProvider && (meProvider.has_key || meProvider.has_backend);

  // ── effects ───────────────────────────────────────────────
  const loadProviders = useCallback(async () => {
    try {
      const list = await api.providers();
      setProviders(list);
      setActive((cur) => cur || list[0]?.name || null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    loadProviders();
  }, [loadProviders]);

  const refreshMe = useCallback(async (t: string) => {
    try {
      setMe(await api.me(t));
    } catch {
      /* token may be stale; ignore */
    }
  }, []);

  // Load models whenever the active provider (or auth) changes.
  useEffect(() => {
    if (!active) return;
    const fallback = activeProvider?.default_model || "";
    setModel(fallback);
    setModels(fallback ? [fallback] : []);
    if (!token) return;
    let cancelled = false;
    api
      .models(token, active)
      .then((list) => {
        if (cancelled) return;
        const ids = list.map((m) => m.id).filter(Boolean).sort();
        if (ids.length) {
          setModels(ids);
          setModel(fallback && ids.includes(fallback) ? fallback : ids[0]);
        }
      })
      .catch(() => {/* keep fallback */});
    return () => {
      cancelled = true;
    };
  }, [active, token, activeProvider]);

  useEffect(() => {
    threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  // ── actions ───────────────────────────────────────────────
  async function connect() {
    setError(null);
    if (!hasWallet()) {
      setError("No Ethereum wallet detected. Install MetaMask to authenticate.");
      return;
    }
    try {
      const { address: addr } = await connectWallet();
      const t = await buildModToken(addr);
      setAddress(addr);
      setToken(t);
      await refreshMe(t);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  function autosize() {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 200) + "px";
  }

  async function send() {
    if (!token || !active || !input.trim() || streaming) return;
    const userMsg: ChatMessage = { role: "user", content: input.trim() };
    const history = [...messages, userMsg];
    setMessages([...history, { role: "assistant", content: "" }]);
    setInput("");
    setStreaming(true);
    setError(null);
    requestAnimationFrame(autosize);

    await api.chat(
      token,
      active,
      { model: model || undefined, messages: history },
      {
        onToken: (delta) => {
          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            next[next.length - 1] = { ...last, content: last.content + delta };
            return next;
          });
        },
        onError: (err) => {
          setError(err);
          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last && last.role === "assistant" && !last.content) next.pop();
            return next;
          });
        },
        onDone: () => setStreaming(false),
      }
    );
    setStreaming(false);
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  // ── render ─────────────────────────────────────────────────
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="logo">D</div>
          <div>
            <div className="title">dev</div>
            <div className="subtitle">LLM gateway</div>
          </div>
        </div>

        <div className="rail-label">
          <span>Providers</span>
          {me?.is_owner && (
            <button title="Add provider" onClick={() => setAddModal(true)}>
              ＋
            </button>
          )}
        </div>

        <div className="provider-list">
          {providers.map((p) => {
            const mp = me?.providers.find((x) => x.name === p.name);
            const state = mp?.has_key ? "key" : mp?.has_backend || p.has_backend ? "backend" : "none";
            return (
              <button
                key={p.name}
                className={`provider${p.name === active ? " active" : ""}`}
                onClick={() => {
                  setActive(p.name);
                  setMessages([]);
                  setError(null);
                }}
              >
                <div className="chip" style={{ background: p.color || "var(--accent)" }}>
                  {p.icon || p.label[0]?.toUpperCase() || "?"}
                </div>
                <div className="meta">
                  <div className="name">{p.label}</div>
                  <div className="sub">{p.default_model || hostOf(p.base_url)}</div>
                </div>
                <div className={`dot ${state}`} title={dotTitle(state)} />
              </button>
            );
          })}
          {providers.length === 0 && <div className="muted" style={{ padding: 14 }}>No providers configured.</div>}
        </div>

        <div className="sidebar-footer">
          {address ? (
            <div className="wallet">
              <div className="avatar" />
              <span className="addr mono">{shortAddress(address)}</span>
              {me?.is_owner && <span className="role">owner</span>}
            </div>
          ) : (
            <button className="wallet connect" onClick={connect}>
              Connect wallet
            </button>
          )}
        </div>
      </aside>

      <main className="main">
        <div className="topbar">
          <div className="provider-name">
            {activeProvider && (
              <span
                className="chip"
                style={{
                  background: activeProvider.color,
                  width: 26,
                  height: 26,
                  borderRadius: 8,
                  display: "grid",
                  placeItems: "center",
                  fontSize: 13,
                  fontWeight: 700,
                  color: "#fff",
                }}
              >
                {activeProvider.icon || activeProvider.label[0]}
              </span>
            )}
            {activeProvider?.label || "dev"}
          </div>
          <div className="spacer" />
          {models.length > 0 && (
            <select className="model" value={model} onChange={(e) => setModel(e.target.value)}>
              {models.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          )}
          {token && active && (
            <button className="icon-btn" title="Manage API key" onClick={() => setKeyModal(true)}>
              <KeyIcon />
            </button>
          )}
          {me?.is_owner && activeProvider && !activeProvider.builtin && (
            <button
              className="icon-btn"
              title="Remove provider"
              onClick={async () => {
                if (!token) return;
                if (!confirm(`Remove provider "${activeProvider.label}"?`)) return;
                try {
                  await api.removeProvider(token, activeProvider.name);
                  setActive(null);
                  await loadProviders();
                } catch (e) {
                  setError(e instanceof Error ? e.message : String(e));
                }
              }}
            >
              <TrashIcon />
            </button>
          )}
        </div>

        <div className="thread" ref={threadRef}>
          {messages.length === 0 ? (
            <div className="hero">
              <div className="glyph">✶</div>
              <h1>One console. Every model.</h1>
              <p>
                OpenAI-compatible providers behind a single Rust gateway — streamed token by token.
                Pick a provider, drop in your key (or use the backend key), and start.
              </p>
              <div className="chips">
                {providers.slice(0, 6).map((p) => (
                  <span key={p.name}>{p.label}</span>
                ))}
                {me?.is_owner && <span>＋ add your own</span>}
              </div>
            </div>
          ) : (
            <div className="thread-inner">
              {messages.map((m, i) => (
                <div key={i} className={`msg ${m.role}`}>
                  <div className="role-badge">{m.role === "user" ? "you" : "ai"}</div>
                  <div className="bubble">
                    {m.content}
                    {streaming && i === messages.length - 1 && m.role === "assistant" && (
                      <span className="cursor" />
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {error && (
          <div style={{ maxWidth: 820, margin: "0 auto", padding: "0 26px", width: "100%" }}>
            <div className="banner error">{error}</div>
          </div>
        )}

        <div className="composer">
          <div className="composer-inner">
            <textarea
              ref={taRef}
              rows={1}
              placeholder={
                !token
                  ? "Connect your wallet to begin…"
                  : !canChat
                  ? `Add a key for ${activeProvider?.label ?? "this provider"} →`
                  : `Message ${activeProvider?.label ?? ""}…`
              }
              value={input}
              disabled={!token || !canChat || streaming}
              onChange={(e) => {
                setInput(e.target.value);
                autosize();
              }}
              onKeyDown={onKeyDown}
            />
            <button
              className="send-btn"
              disabled={!token || !canChat || streaming || !input.trim()}
              onClick={send}
              title="Send (Enter)"
            >
              <SendIcon />
            </button>
          </div>
          <div className="composer-hint">
            <span>
              {activeProvider
                ? `${activeProvider.label} · ${model || activeProvider.default_model || "default model"}`
                : ""}
            </span>
            <span>Enter to send · Shift+Enter for newline</span>
          </div>
        </div>
      </main>

      {keyModal && activeProvider && token && (
        <KeyModal
          provider={activeProvider}
          hasKey={!!meProvider?.has_key}
          token={token}
          onClose={() => setKeyModal(false)}
          onChanged={() => refreshMe(token)}
        />
      )}

      {addModal && token && (
        <AddProviderModal
          token={token}
          onClose={() => setAddModal(false)}
          onAdded={async (name) => {
            setAddModal(false);
            await loadProviders();
            await refreshMe(token);
            setActive(name);
          }}
        />
      )}
    </div>
  );
}

// ── BYOK key modal ─────────────────────────────────────────
function KeyModal({
  provider,
  hasKey,
  token,
  onClose,
  onChanged,
}: {
  provider: Provider;
  hasKey: boolean;
  token: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function save() {
    if (!value.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      await api.setKey(token, provider.name, value.trim());
      onChanged();
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setErr(null);
    try {
      await api.rmKey(token, provider.name);
      onChanged();
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>{provider.label} API key</h2>
        <div className="modal-sub">
          Stored encrypted (AES-256-GCM), keyed to your wallet. It never leaves the gateway in plaintext.
        </div>
        {err && <div className="banner error">{err}</div>}
        <div className="key-status">
          <span className={`dot ${hasKey ? "key" : provider.has_backend ? "backend" : "none"}`} />
          {hasKey
            ? "Your key is on file."
            : provider.has_backend
            ? "No personal key — falling back to the backend key."
            : "No key on file and no backend key configured."}
        </div>
        <div className="field">
          <label>Key</label>
          <input
            type="password"
            autoFocus
            placeholder={`Paste your ${provider.label} API key`}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && save()}
          />
        </div>
        <div className="modal-actions">
          {hasKey && (
            <button className="btn danger" disabled={busy} onClick={remove}>
              Remove
            </button>
          )}
          <button className="btn ghost" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          <button className="btn primary" disabled={busy || !value.trim()} onClick={save}>
            {busy ? "Saving…" : "Save key"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── add-provider modal (owner only) ────────────────────────
function AddProviderModal({
  token,
  onClose,
  onAdded,
}: {
  token: string;
  onClose: () => void;
  onAdded: (name: string) => void;
}) {
  const [f, setF] = useState({
    name: "",
    label: "",
    base_url: "",
    backend_env: "",
    default_model: "",
    icon: "",
    color: "#6366f1",
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setF({ ...f, [k]: e.target.value });

  async function submit() {
    if (!f.name.trim() || !f.base_url.trim()) {
      setErr("name and base_url are required");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const res = await api.addProvider(token, {
        ...f,
        name: f.name.trim().toLowerCase(),
        icon: f.icon || f.name[0]?.toUpperCase() || "?",
        byok: true,
      });
      onAdded(res.provider.name);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>Add a provider</h2>
        <div className="modal-sub">
          Any OpenAI-compatible API works — give it a base URL exposing <code>/models</code> and{" "}
          <code>/chat/completions</code>.
        </div>
        {err && <div className="banner error">{err}</div>}
        <div className="field">
          <label>Name (slug)</label>
          <input placeholder="groq" value={f.name} onChange={set("name")} />
        </div>
        <div className="field">
          <label>Base URL</label>
          <input placeholder="https://api.groq.com/openai/v1" value={f.base_url} onChange={set("base_url")} />
        </div>
        <div className="field row">
          <div style={{ flex: 1 }}>
            <label>Backend key env var</label>
            <input placeholder="GROQ_API_KEY" value={f.backend_env} onChange={set("backend_env")} />
          </div>
          <div style={{ flex: 1 }}>
            <label>Default model</label>
            <input placeholder="llama-3.3-70b" value={f.default_model} onChange={set("default_model")} />
          </div>
        </div>
        <div className="field row">
          <div style={{ width: 90 }}>
            <label>Label</label>
            <input placeholder="Groq" value={f.label} onChange={set("label")} />
          </div>
          <div style={{ width: 64 }}>
            <label>Icon</label>
            <input placeholder="G" maxLength={2} value={f.icon} onChange={set("icon")} />
          </div>
          <div style={{ flex: 1 }}>
            <label>Color</label>
            <input type="color" value={f.color} onChange={set("color")} style={{ padding: 4, height: 40 }} />
          </div>
        </div>
        <div className="modal-actions">
          <button className="btn ghost" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          <button className="btn primary" disabled={busy} onClick={submit}>
            {busy ? "Adding…" : "Add provider"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── helpers + icons ────────────────────────────────────────
function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}
function dotTitle(state: string): string {
  return state === "key" ? "your key on file" : state === "backend" ? "backend key available" : "no key";
}

function SendIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 2 11 13" />
      <path d="M22 2 15 22l-4-9-9-4 20-7Z" />
    </svg>
  );
}
function KeyIcon() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="7.5" cy="15.5" r="5.5" />
      <path d="m21 2-9.6 9.6" />
      <path d="m15.5 7.5 3 3L22 7l-3-3" />
    </svg>
  );
}
function TrashIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 6h18" />
      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
    </svg>
  );
}
