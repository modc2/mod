"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  buildModToken,
  buildLocalModToken,
  clearLocalIdentity,
  connectWallet,
  getOrCreateLocalIdentity,
  hasLocalIdentity,
  hasWallet,
  safeSetItem,
  shortAddress,
} from "@/lib/wallet";
import { api, MediaOut, MeResponse, VeniceModel, mediaUrl } from "@/lib/api";
import { makePaidFetch } from "@/lib/x402";

const TOKEN_KEY = "venice:token";
const ADDR_KEY = "venice:addr";
const IDKIND_KEY = "venice:idkind";
const SIDE_KEY = "venice:sidebar";

type Mode = "byok" | "paid";
type IdKind = "wallet" | "local";
type ChatMsg = { role: "user" | "assistant"; text: string; media: MediaOut[] };
type Convo = { id: string; title: string; thread: ChatMsg[]; updated: number };

// Conversations persist per identity (one shared origin across all modc2.com
// modules — keep blobs small and every write quota-safe).
const convosKey = (addr: string) => `venice:convos:${addr.toLowerCase()}`;
const MAX_CONVOS = 30;

function newId(): string {
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
}

function titleFrom(text: string): string {
  const t = text.trim().replace(/\s+/g, " ");
  return t.length > 42 ? `${t.slice(0, 42)}…` : t || "new conversation";
}

// Defensive load: anything malformed is dropped, never thrown.
function sanitizeConvos(raw: string | null): Convo[] {
  try {
    const arr = JSON.parse(raw || "[]");
    if (!Array.isArray(arr)) return [];
    return arr
      .filter((c) => c && typeof c.id === "string" && Array.isArray(c.thread))
      .map((c) => ({
        id: c.id,
        title: typeof c.title === "string" ? c.title : "conversation",
        updated: typeof c.updated === "number" ? c.updated : 0,
        thread: (c.thread as ChatMsg[]).filter(
          (m) => m && (m.role === "user" || m.role === "assistant")
        ).map((m) => ({ role: m.role, text: m.text || "", media: Array.isArray(m.media) ? m.media : [] })),
      }))
      .slice(0, MAX_CONVOS);
  } catch {
    return [];
  }
}

export default function Page() {
  const [wallet, setWallet] = useState(false);
  const [hasLocal, setHasLocal] = useState(false);
  const [token, setToken] = useState<string | null>(null);
  const [address, setAddress] = useState<string | null>(null);
  const [idKind, setIdKind] = useState<IdKind | null>(null);
  const [me, setMe] = useState<MeResponse | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  const [keyInput, setKeyInput] = useState("");
  const [models, setModels] = useState<VeniceModel[]>([]);
  const [model, setModel] = useState("");
  const [mode, setMode] = useState<Mode>("byok");

  const [prompt, setPrompt] = useState("");
  const [attachments, setAttachments] = useState<MediaOut[]>([]);
  const [status, setStatus] = useState<string | null>(null);
  const threadRef = useRef<HTMLDivElement>(null);

  // ── conversations ──────────────────────────────────────────────
  const [convos, setConvos] = useState<Convo[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  // Persist only after the stored set has been loaded for this identity —
  // otherwise the empty initial state clobbers what's on disk (see the
  // shared-localStorage clobber race that bit the module rail).
  const [convosLoaded, setConvosLoaded] = useState(false);
  const [sideOpen, setSideOpen] = useState(true);

  const active = convos.find((c) => c.id === activeId) ?? null;
  const thread = active?.thread ?? [];

  // tool-capable text models drive the agent (they call the media tools)
  const agentModels = models.filter(
    (m) => (m.type ?? "text") === "text" && m.model_spec?.capabilities?.supportsFunctionCalling
  );

  useEffect(() => {
    setWallet(hasWallet());
    setHasLocal(hasLocalIdentity());
    setSideOpen(localStorage.getItem(SIDE_KEY) !== "closed");
    const savedKind = localStorage.getItem(IDKIND_KEY) as IdKind | null;
    const t = localStorage.getItem(TOKEN_KEY);
    const a = localStorage.getItem(ADDR_KEY);
    if (t && a) {
      api
        .me(t)
        .then((r) => {
          setToken(t);
          setAddress(r.address);
          setIdKind(savedKind);
          setMe(r);
          setMode(r.has_key ? "byok" : r.paid_available ? "paid" : "byok");
        })
        .catch(() => {
          localStorage.removeItem(TOKEN_KEY);
          localStorage.removeItem(ADDR_KEY);
          // A local identity holds its own key, so it can silently re-auth
          // (mint a fresh token) without prompting — unlike a wallet.
          if (savedKind === "local" && hasLocalIdentity()) signInLocal(true);
        });
    } else if (savedKind === "local" && hasLocalIdentity()) {
      signInLocal(true);
    }
    api
      .models()
      .then((m) => {
        setModels(m);
        const firstAgent = m.find(
          (x) => (x.type ?? "text") === "text" && x.model_spec?.capabilities?.supportsFunctionCalling
        );
        if (firstAgent) setModel(firstAgent.id);
      })
      .catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Load this identity's conversations; most recent one becomes active.
  useEffect(() => {
    setConvosLoaded(false);
    if (!address) {
      setConvos([]);
      setActiveId(null);
      return;
    }
    const loaded = sanitizeConvos(localStorage.getItem(convosKey(address)));
    loaded.sort((a, b) => b.updated - a.updated);
    setConvos(loaded);
    setActiveId(loaded[0]?.id ?? null);
    setConvosLoaded(true);
  }, [address]);

  // Persist (quota-safe). If the full set won't fit in the shared origin,
  // retry with just the most recent few rather than losing everything.
  useEffect(() => {
    if (!convosLoaded || !address) return;
    const key = convosKey(address);
    if (!safeSetItem(key, JSON.stringify(convos.slice(0, MAX_CONVOS)))) {
      safeSetItem(key, JSON.stringify(convos.slice(0, 5)));
    }
  }, [convos, convosLoaded, address]);

  useEffect(() => {
    threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight });
  }, [thread, status]);

  const toggleSide = () => {
    setSideOpen((o) => {
      safeSetItem(SIDE_KEY, o ? "closed" : "open");
      return !o;
    });
  };

  const newConvo = () => {
    const c: Convo = { id: newId(), title: "new conversation", thread: [], updated: Date.now() };
    setConvos((cs) => [c, ...cs]);
    setActiveId(c.id);
    setAttachments([]);
    setError(null);
    setOk(null);
  };

  const deleteConvo = (id: string) => {
    setConvos((cs) => cs.filter((c) => c.id !== id));
    if (activeId === id) {
      const rest = convos.filter((c) => c.id !== id);
      setActiveId(rest[0]?.id ?? null);
    }
  };

  const patchConvo = (id: string, fn: (c: Convo) => Convo) =>
    setConvos((cs) => cs.map((c) => (c.id === id ? fn(c) : c)));

  // update the last (assistant) message of a conversation immutably
  const patchAssistant = (id: string, fn: (m: ChatMsg) => ChatMsg) =>
    patchConvo(id, (c) => {
      const copy = c.thread.slice();
      const i = copy.length - 1;
      if (i >= 0 && copy[i].role === "assistant") copy[i] = fn(copy[i]);
      return { ...c, thread: copy };
    });

  const refreshMe = useCallback(async (t: string) => {
    try {
      const r = await api.me(t);
      setMe(r);
      return r;
    } catch {
      return null;
    }
  }, []);

  // Apply a verified session. State first (so sign-in always succeeds), then
  // persist best-effort — a full origin (shared across all modc2.com modules)
  // must not break auth. Returns true iff the session was persisted for reload.
  const applySession = (t: string, r: MeResponse, kind: IdKind): boolean => {
    setToken(t);
    setAddress(r.address);
    setIdKind(kind);
    setMe(r);
    setMode(r.has_key ? "byok" : r.paid_available ? "paid" : "byok");
    return (
      safeSetItem(TOKEN_KEY, t) &&
      safeSetItem(ADDR_KEY, r.address) &&
      safeSetItem(IDKIND_KEY, kind)
    );
  };

  // Sign in with a real wallet (MetaMask) — links this session to your address.
  const signIn = async () => {
    setError(null);
    setOk(null);
    setBusy("connecting wallet…");
    try {
      const { address: addr } = await connectWallet();
      setBusy("waiting for signature…");
      const t = await buildModToken(addr);
      setBusy("verifying…");
      const r = await api.me(t);
      const persisted = applySession(t, r, "wallet");
      setOk(
        persisted
          ? `signed in as ${shortAddress(r.address)}`
          : `signed in as ${shortAddress(r.address)} — browser storage is full, so you'll need to sign in again after a reload`
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  // Sign in with a browser-local keypair — an anonymous pseudonym with no link
  // to any wallet. `silent` re-auths an existing identity without UI noise.
  async function signInLocal(silent = false) {
    if (!silent) {
      setError(null);
      setOk(null);
      setBusy("creating anonymous identity…");
    }
    try {
      const id = getOrCreateLocalIdentity();
      setHasLocal(hasLocalIdentity());
      const t = await buildLocalModToken(id);
      const r = await api.me(t);
      const persisted = applySession(t, r, "local");
      if (!silent)
        setOk(
          persisted
            ? `anonymous — ${shortAddress(r.address)} (key never leaves this browser)`
            : `anonymous — ${shortAddress(r.address)}, but browser storage is full: this identity is in-memory only and will be lost on reload`
        );
    } catch (e) {
      if (!silent) setError((e as Error).message);
    } finally {
      if (!silent) setBusy(null);
    }
  }

  const signOut = () => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(ADDR_KEY);
    localStorage.removeItem(IDKIND_KEY);
    setToken(null);
    setAddress(null);
    setIdKind(null);
    setMe(null);
    setConvos([]);
    setActiveId(null);
    setConvosLoaded(false);
    setAttachments([]);
  };

  // Destroy the anonymous identity itself (not just the session): wipes the
  // local private key, orphaning whatever Venice key was stored under it.
  // Its conversations go with it — they belong to the destroyed pseudonym.
  const forgetIdentity = () => {
    if (address) localStorage.removeItem(convosKey(address));
    clearLocalIdentity();
    setHasLocal(false);
    signOut();
    setOk("anonymous identity erased from this browser");
  };

  const saveKey = async () => {
    if (!token || !keyInput.trim()) return;
    setError(null);
    setOk(null);
    setBusy("saving key…");
    try {
      await api.setKey(token, keyInput.trim());
      setKeyInput("");
      await refreshMe(token);
      setMode("byok");
      setOk("your Venice key is saved (encrypted at rest)");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const removeKey = async () => {
    if (!token) return;
    setBusy("removing key…");
    try {
      await api.rmKey(token);
      await refreshMe(token);
      setOk("key removed");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const attachImage = async (file: File) => {
    if (!token) return;
    setError(null);
    setBusy("uploading image…");
    try {
      const dataUrl = await fileToDataUrl(file);
      const m = await api.uploadMedia(token, dataUrl);
      setAttachments((a) => [...a, m]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const send = async () => {
    if (!token || !prompt.trim() || !model) return;
    setError(null);
    setOk(null);

    // Sending into empty space starts a conversation.
    let cid = activeId;
    if (!cid || !convos.some((c) => c.id === cid)) {
      cid = newId();
      const c: Convo = { id: cid, title: "new conversation", thread: [], updated: Date.now() };
      setConvos((cs) => [c, ...cs]);
      setActiveId(cid);
    }

    const text = prompt;
    const userMsg: ChatMsg = { role: "user", text, media: attachments };
    const history = thread.map((m) => ({ role: m.role, content: m.text }));
    const apiMessages = [...history, { role: "user", content: text }];
    const attachIds = attachments.map((a) => a.media_id);

    patchConvo(cid, (c) => ({
      ...c,
      title: c.thread.length === 0 ? titleFrom(text) : c.title,
      updated: Date.now(),
      thread: [...c.thread, userMsg, { role: "assistant", text: "", media: [] }],
    }));
    setPrompt("");
    setAttachments([]);
    setStatus("starting…");
    setBusy("working…");

    try {
      let fetchImpl: typeof fetch = fetch;
      if (mode === "paid") {
        if (!me?.paid_available) throw new Error("paid path is not available");
        if (!address) throw new Error("connect a wallet first");
        setStatus("preparing payment…");
        fetchImpl = await makePaidFetch(address, me.network || "base");
      }
      await api.agent(
        token,
        { model, messages: apiMessages, attachments: attachIds },
        {
          onStatus: (s) => setStatus(s),
          onMedia: (m) => patchAssistant(cid, (a) => ({ ...a, media: [...a.media, m] })),
          onMessage: (t) => patchAssistant(cid, (a) => ({ ...a, text: t })),
          onError: (err) => {
            setError(err);
            patchAssistant(cid, (a) => ({ ...a, text: a.text || `⚠ ${err}` }));
          },
          onDone: () => setStatus(null),
        },
        fetchImpl
      );
      patchConvo(cid, (c) => ({ ...c, updated: Date.now() }));
      await refreshMe(token);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setStatus(null);
      setBusy(null);
    }
  };

  const canSend =
    !!token && !!prompt.trim() && !!model && (mode === "byok" ? me?.has_key : me?.paid_available);

  // ── signed-out hero ────────────────────────────────────────────
  if (!token) {
    return (
      <div className="stage hero">
        <div className="hero-card panel">
          <span className="brand">venice</span>
          <span className="brand-sub">a generative atelier — text, image &amp; video conjured in a single thread</span>
          {error && <div className="banner err">✕ {error}</div>}
          {ok && !busy && <div className="banner ok">✓ {ok}</div>}
          <p className="lead">
            Edit your photos and summon images &amp; video in one conversation —
            <span className="accent"> attach a picture, describe the change</span>, Venice does the rest.
            Bring your own Venice key (encrypted at rest, used only for your calls), or pay per turn in USDC.
          </p>
          <p className="lead" style={{ marginTop: 10 }}>
            Two ways to sign in. <strong>Wallet</strong> ties the session to your address. Or go
            <span className="accent"> anonymous</span>: a keypair is minted right here in your browser —
            no wallet, no email, no identity revealed. Each anonymous identity carries its own Venice key;
            the private key never leaves this device, and <em>Forget identity</em> erases it.
          </p>
          <div className="row" style={{ marginTop: 22 }}>
            {wallet && (
              <button className="primary" onClick={signIn} disabled={!!busy}>
                Sign in with wallet
              </button>
            )}
            <button
              className={wallet ? "ghost" : "primary"}
              onClick={() => signInLocal()}
              disabled={!!busy}
              title="Generate a keypair in this browser — no wallet, no identity revealed"
            >
              {hasLocal ? "Resume anonymous" : "Use anonymously"}
            </button>
            {busy && <span className="thinking"><span className="orb" />{busy}</span>}
          </div>
        </div>
      </div>
    );
  }

  // ── signed-in: full-screen chat + sidebar ──────────────────────
  return (
    <div className="stage shell">
      <aside className={`side ${sideOpen ? "" : "closed"}`}>
        <div className="side-head">
          <span className="side-brand">venice</span>
          <button className="ghost icon" onClick={toggleSide} title="hide sidebar" aria-label="hide sidebar">«</button>
        </div>

        <button className="primary new-convo" onClick={newConvo} disabled={!!busy}>
          + New conversation
        </button>

        <div className="convo-list">
          {convos.length === 0 && <div className="side-empty">nothing yet — say something</div>}
          {convos.map((c) => (
            <div
              key={c.id}
              className={`convo-item ${c.id === activeId ? "active" : ""}`}
              onClick={() => { setActiveId(c.id); setAttachments([]); }}
              title={c.title}
            >
              <span className="convo-title">{c.title}</span>
              <button
                className="convo-x"
                title="delete conversation"
                onClick={(e) => { e.stopPropagation(); deleteConvo(c.id); }}
              >
                ×
              </button>
            </div>
          ))}
        </div>

        <div className="side-sec">
          <div className="side-sec-title">Identity</div>
          <div className="row" style={{ gap: 8 }}>
            <span className={`pill ${idKind === "local" ? "ok" : "brand"}`}>
              {idKind === "local" ? "anonymous" : "wallet"}
            </span>
            <span className="mono" title={idKind === "local" ? "browser-local pseudonym" : "wallet address"}>
              {shortAddress(address || "")}
            </span>
          </div>
          <div className="row" style={{ gap: 8, marginTop: 8 }}>
            <button className="ghost" onClick={signOut}>Sign out</button>
            {idKind === "local" && (
              <button className="ghost" onClick={forgetIdentity} disabled={!!busy} title="erase the browser-local private key">
                Forget identity
              </button>
            )}
          </div>
        </div>

        <div className="side-sec">
          <div className="side-sec-title">Access</div>
          {me?.has_key ? (
            <div className="row" style={{ gap: 8 }}>
              <span className="pill ok">✓ your key (BYOK)</span>
              <button className="ghost" onClick={removeKey} disabled={!!busy}>Remove</button>
            </div>
          ) : (
            <div className="key-form">
              <input
                type="password"
                placeholder="Venice API key (vk-…)"
                value={keyInput}
                onChange={(e) => setKeyInput(e.target.value)}
                disabled={!!busy}
              />
              <button className="primary" onClick={saveKey} disabled={!keyInput.trim() || !!busy}>
                Save
              </button>
            </div>
          )}
          <div style={{ marginTop: 8 }}>
            <span className={`pill ${me?.paid_available ? "ok" : ""}`}>
              {me?.paid_available
                ? `pay-per-turn: ${me?.price} ${me?.currency} on ${me?.network}`
                : "pay-per-turn: unavailable"}
            </span>
          </div>
          <div className="seg" style={{ marginTop: 10 }}>
            <button className={mode === "byok" ? "active" : ""} onClick={() => setMode("byok")} disabled={!me?.has_key} title={me?.has_key ? "" : "add a key first"}>
              My key
            </button>
            <button className={mode === "paid" ? "active" : ""} onClick={() => setMode("paid")} disabled={!me?.paid_available} title={me?.paid_available ? "" : "paid path unavailable"}>
              Pay per turn
            </button>
          </div>
        </div>

        <div className="side-sec">
          <div className="side-sec-title">Model</div>
          <select value={model} onChange={(e) => setModel(e.target.value)} title="orchestrator model (calls the image/video tools)">
            {agentModels.length === 0 && <option value="">loading…</option>}
            {agentModels.map((m) => (
              <option key={m.id} value={m.id}>{m.id}</option>
            ))}
          </select>
        </div>
      </aside>

      <main className="main">
        <div className="topbar">
          {!sideOpen && (
            <button className="ghost icon" onClick={toggleSide} title="show sidebar" aria-label="show sidebar">☰</button>
          )}
          <span className="topbar-title">{active?.title ?? "venice"}</span>
          <div className="spacer" />
          {status && <span className="thinking"><span className="orb" />{status}</span>}
        </div>

        {error && <div className="banner err">✕ {error}</div>}
        {ok && !busy && <div className="banner ok">✓ {ok}</div>}

        <div className="thread" ref={threadRef}>
          {thread.length === 0 && (
            <div className="empty">
              <div className="empty-title">what shall we dream up?</div>
              <div className="prompts">
                {[
                  "draw a neon cyberpunk fox",
                  "a Murano-glass koi, then upscale it 2×",
                  "a melting clock over the Venetian lagoon, Dalí style",
                  "animate a paper crane unfolding into flight, 5s",
                ].map((p) => (
                  <button key={p} className="prompt-chip" onClick={() => setPrompt(p)} disabled={!!busy}>
                    {p}
                  </button>
                ))}
              </div>
            </div>
          )}
          {thread.map((m, i) => (
            <div key={i} className={`msg ${m.role}`}>
              <div className="msg-role">{m.role === "user" ? "you" : "venice"}</div>
              {m.text && <div className="msg-text">{m.text}</div>}
              {m.media.length > 0 && (
                <div className="media-grid">
                  {m.media.map((md) =>
                    md.kind === "video" ? (
                      <video key={md.media_id} className="media" src={mediaUrl(md.url)} controls loop />
                    ) : (
                      <a key={md.media_id} href={mediaUrl(md.url)} target="_blank" rel="noreferrer">
                        <img className="media" src={mediaUrl(md.url)} alt={md.prompt || "image"} />
                      </a>
                    )
                  )}
                </div>
              )}
              {m.role === "assistant" && !m.text && m.media.length === 0 && status && i === thread.length - 1 && (
                <div className="thinking"><span className="orb" />{status}</div>
              )}
            </div>
          ))}
        </div>

        {attachments.length > 0 && (
          <div className="row" style={{ margin: "8px 0 0" }}>
            {attachments.map((a) => (
              <span key={a.media_id} className="chip">
                <img src={mediaUrl(a.url)} alt="attachment" />
                attached
                <button className="chip-x" onClick={() => setAttachments((s) => s.filter((x) => x.media_id !== a.media_id))}>×</button>
              </span>
            ))}
          </div>
        )}

        <div className="composer">
          <label className="attach" title="attach an image">
            <input type="file" accept="image/*" hidden disabled={!!busy} onChange={(e) => { const f = e.target.files?.[0]; if (f) attachImage(f); e.target.value = ""; }} />
            📎
          </label>
          <textarea
            placeholder={me?.has_key || me?.paid_available ? "Message venice…  (text, images, video)" : "add a key or enable paid to chat"}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send(); }}
            rows={2}
          />
          <button className="primary send" onClick={send} disabled={!canSend || !!busy}>
            {mode === "paid" ? `Pay & send` : "Send"}
          </button>
        </div>
      </main>
    </div>
  );
}

function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result as string);
    r.onerror = () => reject(new Error("could not read file"));
    r.readAsDataURL(file);
  });
}
