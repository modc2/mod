"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  buildModToken,
  connectWallet,
  hasWallet,
  shortAddress,
} from "@/lib/wallet";
import { api, MediaOut, MeResponse, VeniceModel, mediaUrl } from "@/lib/api";
import { makePaidFetch } from "@/lib/x402";

const TOKEN_KEY = "venice:token";
const ADDR_KEY = "venice:addr";

type Mode = "byok" | "paid";
type ChatMsg = { role: "user" | "assistant"; text: string; media: MediaOut[] };

export default function Page() {
  const [wallet, setWallet] = useState(false);
  const [token, setToken] = useState<string | null>(null);
  const [address, setAddress] = useState<string | null>(null);
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
  const [thread, setThread] = useState<ChatMsg[]>([]);
  const [status, setStatus] = useState<string | null>(null);
  const threadRef = useRef<HTMLDivElement>(null);

  // tool-capable text models drive the agent (they call the media tools)
  const agentModels = models.filter(
    (m) => (m.type ?? "text") === "text" && m.model_spec?.capabilities?.supportsFunctionCalling
  );

  useEffect(() => {
    setWallet(hasWallet());
    const t = localStorage.getItem(TOKEN_KEY);
    const a = localStorage.getItem(ADDR_KEY);
    if (t && a) {
      api
        .me(t)
        .then((r) => {
          setToken(t);
          setAddress(r.address);
          setMe(r);
          setMode(r.has_key ? "byok" : r.paid_available ? "paid" : "byok");
        })
        .catch(() => {
          localStorage.removeItem(TOKEN_KEY);
          localStorage.removeItem(ADDR_KEY);
        });
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

  useEffect(() => {
    threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight });
  }, [thread, status]);

  const refreshMe = useCallback(async (t: string) => {
    try {
      const r = await api.me(t);
      setMe(r);
      return r;
    } catch {
      return null;
    }
  }, []);

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
      localStorage.setItem(TOKEN_KEY, t);
      localStorage.setItem(ADDR_KEY, r.address);
      setToken(t);
      setAddress(r.address);
      setMe(r);
      setMode(r.has_key ? "byok" : r.paid_available ? "paid" : "byok");
      setOk(`signed in as ${shortAddress(r.address)}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const signOut = () => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(ADDR_KEY);
    setToken(null);
    setAddress(null);
    setMe(null);
    setThread([]);
    setAttachments([]);
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

  // update the last (assistant) message immutably
  const patchAssistant = (fn: (m: ChatMsg) => ChatMsg) =>
    setThread((t) => {
      const copy = t.slice();
      const i = copy.length - 1;
      if (i >= 0 && copy[i].role === "assistant") copy[i] = fn(copy[i]);
      return copy;
    });

  const send = async () => {
    if (!token || !prompt.trim() || !model) return;
    setError(null);
    setOk(null);

    const userMsg: ChatMsg = { role: "user", text: prompt, media: attachments };
    const history = thread.map((m) => ({ role: m.role, content: m.text }));
    const apiMessages = [...history, { role: "user", content: prompt }];
    const attachIds = attachments.map((a) => a.media_id);

    setThread((t) => [...t, userMsg, { role: "assistant", text: "", media: [] }]);
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
          onMedia: (m) => patchAssistant((a) => ({ ...a, media: [...a.media, m] })),
          onMessage: (text) => patchAssistant((a) => ({ ...a, text })),
          onError: (err) => {
            setError(err);
            patchAssistant((a) => ({ ...a, text: a.text || `⚠ ${err}` }));
          },
          onDone: () => setStatus(null),
        },
        fetchImpl
      );
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

  return (
    <div className="stage">
    <div className="wrap">
      <header className="header">
        <div>
          <span className="brand">venice</span>
          <span className="brand-sub">a generative atelier — text, image &amp; video conjured in a single thread</span>
        </div>
        <div className="row">
          {!wallet && <span className="muted">no wallet detected</span>}
          {wallet && !token && (
            <button className="primary" onClick={signIn} disabled={!!busy}>Sign in with wallet</button>
          )}
          {token && address && (
            <>
              <span className="pill brand">mod-auth</span>
              <span className="mono">{shortAddress(address)}</span>
              <button className="ghost" onClick={signOut}>Sign out</button>
            </>
          )}
        </div>
      </header>

      {error && <div className="banner err">✕ {error}</div>}
      {ok && !busy && <div className="banner ok">✓ {ok}</div>}

      {!token && (
        <div className="panel">
          <p className="lead">
            Sign in with your wallet, then summon anything in one conversation —
            <span className="accent"> words, images, moving pictures</span>, all in the same breath.
            Bring your own Venice key and pay Venice directly, or pay per turn in USDC and we cover the call.
          </p>
        </div>
      )}

      {token && (
        <>
          <div className="panel">
            <h2 className="panel-title">Access</h2>
            <div className="row">
              <span className={`pill ${me?.has_key ? "ok" : ""}`}>
                {me?.has_key ? "✓ your key on file (BYOK)" : "no key on file"}
              </span>
              <span className={`pill ${me?.paid_available ? "ok" : ""}`}>
                {me?.paid_available
                  ? `pay-per-turn: ${me?.price} ${me?.currency} on ${me?.network}`
                  : "pay-per-turn: unavailable"}
              </span>
              <div className="spacer" />
              {me?.has_key ? (
                <button onClick={removeKey} disabled={!!busy}>Remove key</button>
              ) : (
                <>
                  <input
                    type="password"
                    placeholder="Venice API key (vk-…)"
                    value={keyInput}
                    onChange={(e) => setKeyInput(e.target.value)}
                    disabled={!!busy}
                    style={{ maxWidth: 260 }}
                  />
                  <button className="primary" onClick={saveKey} disabled={!keyInput.trim() || !!busy}>
                    Save key
                  </button>
                </>
              )}
            </div>
          </div>

          <div className="panel">
            <div className="row" style={{ marginBottom: 10 }}>
              <h2 className="panel-title" style={{ margin: 0 }}>Chat</h2>
              <div className="spacer" />
              <div className="seg">
                <button className={mode === "byok" ? "active" : ""} onClick={() => setMode("byok")} disabled={!me?.has_key} title={me?.has_key ? "" : "add a key first"}>
                  My key
                </button>
                <button className={mode === "paid" ? "active" : ""} onClick={() => setMode("paid")} disabled={!me?.paid_available} title={me?.paid_available ? "" : "paid path unavailable"}>
                  Pay per turn
                </button>
              </div>
              <select value={model} onChange={(e) => setModel(e.target.value)} title="orchestrator model (calls the image/video tools)">
                {agentModels.length === 0 && <option value="">loading…</option>}
                {agentModels.map((m) => (
                  <option key={m.id} value={m.id}>{m.id}</option>
                ))}
              </select>
            </div>

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
                  {m.role === "assistant" && !m.text && m.media.length === 0 && status && (
                    <div className="thinking"><span className="orb" />{status}</div>
                  )}
                </div>
              ))}
            </div>

            {attachments.length > 0 && (
              <div className="row" style={{ margin: "8px 0" }}>
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
            {status && <div className="thinking fineprint" style={{ marginTop: 10 }}><span className="orb" />{status}</div>}
          </div>
        </>
      )}
    </div>
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
