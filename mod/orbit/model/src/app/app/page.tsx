"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  GATE_ADDRESS,
  clearSession,
  connectWallet,
  fetchChallenge,
  hasWallet,
  loadSession,
  readGate,
  saveSession,
  signChallenge,
  type GateSession,
} from "./lib/gate";

const API = process.env.NEXT_PUBLIC_API_URL || "/api/model";
const KEY_PREFIX = "modelgw_key_";
const LAST_PROVIDER = "modelgw_last_provider";
const LAST_MODEL = "modelgw_last_model_";

interface ProviderInfo {
  id: string; label: string; url: string; default_model: string;
  key_hint: string; docs: string;
}
interface ChatMsg { role: "user" | "assistant"; content: string; error?: boolean; }

/// Curated fallback lists per provider, shown before the user enters a key
/// so the dropdown isn't a single grok entry. The live /models list (fetched
/// after key entry) replaces this and is always authoritative.
const FALLBACK_MODELS: Record<string, string[]> = {
  "openrouter": [
    "anthropic/claude-opus-4",
    "anthropic/claude-sonnet-4",
    "anthropic/claude-3.7-sonnet",
    "openai/gpt-4o",
    "openai/o1",
    "google/gemini-2.0-flash-001",
    "google/gemini-pro-1.5",
    "meta-llama/llama-3.3-70b-instruct",
    "x-ai/grok-2-1212",
    "deepseek/deepseek-chat",
    "deepseek/deepseek-r1",
    "mistralai/mistral-large",
    "qwen/qwen-2.5-72b-instruct",
  ],
  "chutes": [
    "deepseek-ai/DeepSeek-V3",
    "deepseek-ai/DeepSeek-R1",
    "meta-llama/Llama-3.3-70B-Instruct",
    "Qwen/Qwen2.5-72B-Instruct",
    "Qwen/QwQ-32B",
    "mistralai/Mistral-Large-Instruct-2411",
    "nvidia/Llama-3.1-Nemotron-70B-Instruct-HF",
    "xai/grok-2-1212",
  ],
  "targon": [
    "deepseek-ai/DeepSeek-V3",
    "meta-llama/Llama-3.3-70B-Instruct",
    "Qwen/Qwen2.5-72B-Instruct",
    "x-ai/grok-2",
    "x-ai/grok-2-mini",
  ],
  "venice": [
    "llama-3.3-70b",
    "qwen-2.5-coder-32b",
    "deepseek-r1-llama-70b",
    "mistral-31-24b",
    "grok-3",
    "grok-3-medium",
  ],
};

const shorten = (a?: string) => (a ? `${a.slice(0, 6)}…${a.slice(-4)}` : "");

export default function Page() {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [provider, setProvider] = useState<string>("");
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState<string>("");
  const [keyDialogOpen, setKeyDialogOpen] = useState(false);
  const [keyInput, setKeyInput] = useState("");
  const [hasKey, setHasKey] = useState(false);

  const [wallet, setWallet] = useState<string>("");
  const [session, setSession] = useState<GateSession | null>(null);
  const [gateAllowed, setGateAllowed] = useState<boolean | null>(null);
  const [gateBusy, setGateBusy] = useState(false);
  const [gateError, setGateError] = useState<string>("");

  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const getKey = (pid: string) =>
    typeof window === "undefined" ? "" : localStorage.getItem(KEY_PREFIX + pid) || "";
  const setKey = (pid: string, k: string) => localStorage.setItem(KEY_PREFIX + pid, k);
  const clearKey = (pid: string) => localStorage.removeItem(KEY_PREFIX + pid);

  // Load providers + session on mount
  useEffect(() => {
    fetch(`${API}/providers`)
      .then((r) => r.json())
      .then((p: ProviderInfo[]) => {
        setProviders(p);
        const last = localStorage.getItem(LAST_PROVIDER);
        const pick = last && p.find((x) => x.id === last) ? last : p[0]?.id;
        if (pick) setProvider(pick);
      })
      .catch(() => {});

    const s = loadSession();
    if (s) { setSession(s); setWallet(s.address); }
  }, []);

  useEffect(() => { setHasKey(!!getKey(provider)); }, [provider]);

  const loadModels = useCallback(async () => {
    if (!provider) return;
    const cfg = providers.find((p) => p.id === provider);
    const fallback = FALLBACK_MODELS[provider] || (cfg ? [cfg.default_model] : []);
    const last = (typeof window !== "undefined" && localStorage.getItem(LAST_MODEL + provider)) || "";
    const pickInitial = (list: string[]) =>
      list.includes(last) ? last :
      cfg && list.includes(cfg.default_model) ? cfg.default_model :
      (list[0] || cfg?.default_model || "");

    const key = getKey(provider);
    if (!key) {
      setModels(fallback);
      setModel(last || pickInitial(fallback));
      return;
    }
    try {
      const r = await fetch(`${API}/models?provider=${encodeURIComponent(provider)}`, {
        headers: { "X-API-Key": key },
      });
      if (!r.ok) throw new Error(await r.text());
      const data = await r.json();
      const ids: string[] = (data.models || []).map((m: any) => m.id).filter(Boolean);
      // Merge live list with fallback so the user always sees something
      // useful — and de-duplicate while preserving live ordering.
      const seen = new Set<string>();
      const merged = [...ids, ...fallback].filter((id) => {
        if (seen.has(id)) return false;
        seen.add(id);
        return true;
      });
      setModels(merged);
      setModel(pickInitial(merged));
    } catch {
      setModels(fallback);
      setModel(last || pickInitial(fallback));
    }
  }, [provider, providers]);
  useEffect(() => { loadModels(); }, [loadModels]);

  // Refresh gate when wallet/session changes
  useEffect(() => {
    if (!session) { setGateAllowed(null); return; }
    if (!GATE_ADDRESS) { setGateAllowed(true); return; }
    readGate(session.address).then((s) => setGateAllowed(s.allowed)).catch(() => setGateAllowed(false));
  }, [session]);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages]);

  const onConnect = async () => {
    setGateError(""); setGateBusy(true);
    try {
      const addr = await connectWallet();
      setWallet(addr);
      const issuedAt = Math.floor(Date.now() / 1000);
      const challenge = await fetchChallenge(addr, issuedAt);
      const signature = await signChallenge(challenge);
      const s: GateSession = { address: addr, challenge, signature, issuedAt };
      saveSession(s); setSession(s);
      const r = await fetch(`${API}/gate/verify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ address: addr, challenge, signature }),
      });
      if (!r.ok) throw new Error(`verify failed: ${r.status}`);
      const v = await r.json();
      if (!v.signature_valid) throw new Error(`signature mismatch (recovered ${v.recovered})`);
    } catch (e: any) {
      setGateError(e?.message || String(e));
      clearSession(); setSession(null);
    } finally { setGateBusy(false); }
  };

  const onDisconnect = () => {
    clearSession(); setSession(null); setWallet(""); setGateAllowed(null);
  };

  const onSend = async () => {
    if (streaming) return;
    const text = input.trim();
    if (!text) return;

    if (!getKey(provider)) {
      setKeyInput(getKey(provider) || "");
      setKeyDialogOpen(true);
      return;
    }

    const userMsg: ChatMsg = { role: "user", content: text };
    const asstMsg: ChatMsg = { role: "assistant", content: "" };
    setMessages((m) => [...m, userMsg, asstMsg]);
    setInput("");
    setStreaming(true);

    localStorage.setItem(LAST_PROVIDER, provider);
    if (model) localStorage.setItem(LAST_MODEL + provider, model);

    try {
      const r = await fetch(`${API}/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-API-Key": getKey(provider),
          ...(session ? { "X-Gate-Address": session.address, "X-Gate-Signature": session.signature } : {}),
        },
        body: JSON.stringify({
          provider, model: model || undefined,
          messages: [...messages, userMsg].map(({ role, content }) => ({ role, content })),
          stream: true,
        }),
      });
      if (!r.ok || !r.body) throw new Error(`${r.status} ${(await r.text()).slice(0, 200)}`);

      const reader = r.body.getReader();
      const dec = new TextDecoder();
      let buf = "", acc = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const frame = buf.slice(0, idx); buf = buf.slice(idx + 2);
          if (!frame.startsWith("data:")) continue;
          try {
            const obj = JSON.parse(frame.slice(5).trim());
            if (obj.delta) {
              acc += obj.delta;
              setMessages((m) => {
                const copy = [...m];
                copy[copy.length - 1] = { role: "assistant", content: acc };
                return copy;
              });
            } else if (obj.error) {
              setMessages((m) => {
                const copy = [...m];
                copy[copy.length - 1] = {
                  role: "assistant",
                  content: acc + "\n[stream error] " + obj.error,
                  error: true,
                };
                return copy;
              });
            }
          } catch { /* ignore */ }
        }
      }
    } catch (e: any) {
      setMessages((m) => {
        const copy = [...m];
        copy[copy.length - 1] = {
          role: "assistant",
          content: "[error] " + (e?.message || String(e)),
          error: true,
        };
        return copy;
      });
    } finally { setStreaming(false); }
  };

  const onSaveKey = () => {
    const k = keyInput.trim();
    if (k) { setKey(provider, k); setHasKey(true); loadModels(); }
    setKeyDialogOpen(false);
  };
  const onClearKey = () => { clearKey(provider); setHasKey(false); loadModels(); };

  const cfg = providers.find((p) => p.id === provider);
  const gateBlocked = !!GATE_ADDRESS && gateAllowed === false;
  const gateNeeded = !!GATE_ADDRESS && !session;
  const canSend = !streaming && !gateNeeded && !gateBlocked && !!provider;

  // Last assistant message is the one being streamed
  const lastIdx = messages.length - 1;

  return (
    <div className="app">
      {/* ── Brand row ── */}
      <div className="hdr">
        <div className="brand">
          <span className="dot" />
          <span>MODEL · GATEWAY</span>
        </div>
        <span className="tag">BYOK · KEYS STAY IN YOUR BROWSER</span>

        <div className="spacer" />

        {!session ? (
          <button
            className="ghost"
            onClick={onConnect}
            disabled={gateBusy || !hasWallet()}
            title={hasWallet() ? "Connect a wallet to pass the on-chain gate" : "No wallet detected"}
          >
            {gateBusy ? "SIGNING…" : hasWallet() ? "CONNECT WALLET" : "NO WALLET"}
          </button>
        ) : (
          <>
            <span className="pill mute" title={session.address}>{shorten(session.address)}</span>
            {GATE_ADDRESS ? (
              <span className={"pill " + (gateAllowed === null ? "mute" : gateAllowed ? "ok" : "bad")}>
                <span className="swatch" />
                {gateAllowed === null ? "GATE…" : gateAllowed ? "GATE OK" : "DENIED"}
              </span>
            ) : (
              <span className="pill mute">NO GATE · DEV</span>
            )}
            <button className="ghost" onClick={onDisconnect}>DISCONNECT</button>
          </>
        )}

        <button className="ghost" onClick={() => setMessages([])} disabled={streaming}>NEW CHAT</button>
      </div>

      {/* ── Toolbar ── */}
      <div className="toolbar">
        <span className="lbl">Provider</span>
        <select value={provider} onChange={(e) => setProvider(e.target.value)}>
          {providers.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
        </select>

        <span className="sep" />

        <span className="lbl">Model</span>
        <input
          className="grow"
          list="model-options"
          value={model}
          onChange={(e) => setModel(e.target.value)}
          placeholder={cfg?.default_model || "model id"}
          spellCheck={false}
          autoComplete="off"
        />
        <datalist id="model-options">
          {models.map((m) => <option key={m} value={m} />)}
        </datalist>
        <button className="ghost" onClick={loadModels} title={hasKey ? "Reload model list" : "Set a key to fetch the live model list"}>↻</button>
        <span className="tag" style={{ whiteSpace: "nowrap" }}>
          {hasKey ? `${models.length} live` : `${models.length} suggested`}
        </span>

        <span className="sep" />

        <span className="lbl">Key</span>
        <span className={"pill " + (hasKey ? "ok" : "warn")}>
          <span className="swatch" />
          {hasKey ? "KEY SET" : "NO KEY"}
        </span>
        <button onClick={() => { setKeyDialogOpen(true); setKeyInput(getKey(provider) || ""); }}>SET</button>
        <button className="ghost" onClick={onClearKey} disabled={!hasKey}>CLEAR</button>
      </div>

      {/* ── Chat ── */}
      <div className="chat" ref={scrollRef}>
        {gateError && <div className="notice error">gate: {gateError}</div>}
        {gateNeeded && (
          <div className="notice warn">
            Connect a wallet and sign the ModelGate challenge to begin a session. (Base Sepolia · chain {process.env.NEXT_PUBLIC_MODEL_GATE_CHAIN_ID})
          </div>
        )}
        {gateBlocked && (
          <div className="notice error">
            {shorten(session?.address)} is not on the ModelGate allowlist. Ask the gate owner to call <code>setAllowed</code>.
          </div>
        )}

        {messages.length === 0 && !gateNeeded && !gateBlocked && (
          <div className="empty">
            <div className="title">Pick a provider · set a key · chat</div>
            <div className="sub">
              Every request carries your key only for that round-trip. The server never stores it. Switch providers freely — each one has its own key slot in your browser.
            </div>
            <div className="hints">
              <span className="tag"><span className="kbd">⌘</span> + <span className="kbd">⏎</span>  to send</span>
              <span className="tag">↻ refresh models</span>
              {GATE_ADDRESS && <span className="tag">wallet-gated</span>}
            </div>
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} className={"msg " + (m.error ? "error" : m.role)}>
            <div className="who">
              <span className="badge">{m.role.toUpperCase()}</span>
              {m.role === "assistant" && cfg && <span style={{ opacity: 0.6 }}>· {cfg.label} · {model}</span>}
            </div>
            <div>
              {m.content}
              {streaming && i === lastIdx && m.role === "assistant" && <span className="cursor" />}
            </div>
          </div>
        ))}
      </div>

      {/* ── Composer ── */}
      <div className="composer">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") onSend(); }}
          placeholder={
            gateNeeded ? "Connect wallet to enable chat" :
            gateBlocked ? "Address not on ModelGate allowlist" :
            !hasKey ? "Type a message — you'll be prompted for your key on send" :
            "Type a message — ⌘/Ctrl + Enter to send"
          }
          disabled={gateNeeded || gateBlocked}
        />
        <button className="primary" onClick={onSend} disabled={!canSend}>
          {streaming ? "STREAMING…" : "SEND"}
        </button>
      </div>

      {/* ── Key dialog ── */}
      {keyDialogOpen && cfg && (
        <div className="scrim" onClick={() => setKeyDialogOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>{cfg.label} · API key</h3>
            <span className="hint">
              Format hint: <code>{cfg.key_hint}</code> ·{" "}
              <a href={cfg.docs} target="_blank" rel="noreferrer">{cfg.docs}</a>
            </span>
            <input
              type="password" autoFocus autoComplete="off"
              value={keyInput} onChange={(e) => setKeyInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") onSaveKey(); }}
              placeholder={cfg.key_hint}
            />
            <span className="hint">
              Stored only in this browser's localStorage. The server sees it just in transit, then forgets it.
            </span>
            <div className="row">
              <button className="ghost" onClick={() => setKeyDialogOpen(false)}>CANCEL</button>
              <button className="primary" onClick={onSaveKey}>SAVE</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
