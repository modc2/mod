"use client";

import { useEffect, useState } from "react";
import { api, ApiError, Submission, SubmitBody, Terms } from "@/lib/api";
import { Session } from "@/lib/session";
import { shortAddress } from "@/lib/wallet";

const EMPTY: SubmitBody = {
  name: "",
  description: "",
  repo: "",
  license: "",
  remote_url: "",
  npm: "",
  pypi: "",
  tags: [],
};

/**
 * Publish sheet: terms gate → form → CID.
 *
 * The manifest is pinned to the store mod with the publisher's own token, and
 * store requires a signed terms acceptance first — so the gate is shown here
 * rather than letting a submit fail with a 451 the user can't act on.
 */
export default function Publish({
  session,
  onClose,
  onPublished,
}: {
  session: Session;
  onClose: () => void;
  onPublished: () => void;
}) {
  const [terms, setTerms] = useState<Terms | null>(null);
  const [accepting, setAccepting] = useState(false);
  const [form, setForm] = useState<SubmitBody>(EMPTY);
  const [tags, setTags] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<Submission | null>(null);
  const [mine, setMine] = useState<Submission[]>([]);

  const reload = () =>
    api
      .submissions(session.token, true)
      .then((r) => setMine(r.servers))
      .catch(() => setMine([]));

  useEffect(() => {
    api.terms(session.token).then(setTerms).catch(() => setTerms(null));
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.token]);

  const gated = terms?.required && !terms?.accepted;

  async function accept() {
    setAccepting(true);
    setError("");
    try {
      await api.acceptTerms(session.token);
      setTerms(await api.terms(session.token));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setAccepting(false);
    }
  }

  async function submit() {
    setBusy(true);
    setError("");
    try {
      const body: SubmitBody = {
        ...form,
        tags: tags
          .split(",")
          .map((t) => t.trim())
          .filter(Boolean),
      };
      const s = await api.submit(session.token, body);
      setResult(s);
      setForm(EMPTY);
      setTags("");
      await reload();
      onPublished();
    } catch (e) {
      if (e instanceof ApiError && e.status === 502 && /terms/i.test(e.detail))
        setTerms(await api.terms(session.token).catch(() => terms));
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const set = (k: keyof SubmitBody) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  return (
    <div className="sheet" onClick={onClose}>
      <div className="sheet-inner" onClick={(e) => e.stopPropagation()}>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2>Publish an MCP server</h2>
          <button className="ghost sm" onClick={onClose}>
            close
          </button>
        </div>
        <p className="muted small" style={{ marginTop: 0 }}>
          Signed as <span className="mono">{shortAddress(session.address)}</span> (
          {session.mode === "local" ? "browser key" : "wallet"}). Your manifest is pinned to the store
          mod as your own object — the hub keeps the CID, you keep the bytes.
        </p>

        {error && <div className="note bad">{error}</div>}

        {gated && (
          <section style={{ marginTop: 14 }}>
            <div className="note warn">
              <strong>Publisher terms v{terms?.version}</strong> — pinning a manifest stores bytes in
              the fleet, so store asks you to sign-accept these once.
            </div>
            <div className="terms" style={{ marginTop: 10 }}>
              {terms?.text}
            </div>
            <div className="row" style={{ marginTop: 10 }}>
              <button className="primary" disabled={accepting} onClick={accept}>
                {accepting ? "signing…" : "Accept and continue"}
              </button>
            </div>
          </section>
        )}

        {!gated && (
          <>
            {result && (
              <div className="note good" style={{ marginTop: 14 }}>
                <strong>{result.name}</strong> published as{" "}
                <span className="mono small">{result.id}</span>
                {result.cid ? (
                  <div className="small" style={{ marginTop: 4 }}>
                    manifest CID{" "}
                    <a href={api.storeObjectUrl(result.cid)} target="_blank" rel="noreferrer">
                      <span className="mono">{result.cid}</span>
                    </a>
                  </div>
                ) : (
                  <div className="small" style={{ marginTop: 4 }}>
                    not pinned yet: {result.pin_error} — use re-pin below once store is reachable
                  </div>
                )}
              </div>
            )}

            <div className="form">
              <label className="field wide">
                name
                <input value={form.name} onChange={set("name")} placeholder="Postgres MCP" />
              </label>
              <label className="field wide">
                description
                <textarea
                  value={form.description}
                  onChange={set("description")}
                  placeholder="What the server does, and what its tools let a model do."
                />
              </label>
              <label className="field">
                repository
                <input
                  value={form.repo}
                  onChange={set("repo")}
                  placeholder="https://github.com/you/server"
                />
                <span className="hint">public source is what makes it open source here</span>
              </label>
              <label className="field">
                license
                <input value={form.license} onChange={set("license")} placeholder="MIT" />
              </label>
              <label className="field">
                remote endpoint
                <input
                  value={form.remote_url}
                  onChange={set("remote_url")}
                  placeholder="https://host/mcp"
                />
                <span className="hint">hosted Streamable HTTP — probeable by the hub</span>
              </label>
              <label className="field">
                npm package
                <input value={form.npm} onChange={set("npm")} placeholder="@you/server-mcp" />
                <span className="hint">becomes an npx install line</span>
              </label>
              <label className="field">
                PyPI package
                <input value={form.pypi} onChange={set("pypi")} placeholder="server-mcp" />
                <span className="hint">becomes a uvx install line</span>
              </label>
              <label className="field">
                tags
                <input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="sql, data" />
              </label>
            </div>

            <div className="row" style={{ marginTop: 16 }}>
              <button
                className="primary"
                disabled={busy || !form.name.trim() || !form.description.trim()}
                onClick={submit}
              >
                {busy ? "pinning…" : "Publish"}
              </button>
              <span className="muted small">
                needs a repo, a remote endpoint, or a package — something to install from
              </span>
            </div>
          </>
        )}

        {mine.length > 0 && (
          <section style={{ marginTop: 24 }}>
            <h4 style={{ color: "var(--ink-mute)", fontSize: 11.5, letterSpacing: "0.09em" }}>
              PUBLISHED BY YOU
            </h4>
            <div className="col">
              {mine.map((s) => (
                <div className="note" key={s.id}>
                  <div className="row" style={{ justifyContent: "space-between" }}>
                    <div>
                      <strong>{s.name}</strong>{" "}
                      <span className="mono small muted">{s.id}</span>
                      <div className="small muted mono" style={{ marginTop: 3 }}>
                        {s.cid ? s.cid : `unpinned — ${s.pin_error ?? "no CID"}`}
                      </div>
                    </div>
                    <div className="row">
                      {!s.pinned && (
                        <button
                          className="sm"
                          onClick={async () => {
                            try {
                              await api.repin(session.token, s.id);
                              await reload();
                            } catch (e) {
                              setError(e instanceof Error ? e.message : String(e));
                            }
                          }}
                        >
                          re-pin
                        </button>
                      )}
                      <button
                        className="sm danger"
                        onClick={async () => {
                          if (!confirm(`Delist ${s.name}? Your pinned manifest stays in store.`))
                            return;
                          try {
                            await api.delist(session.token, s.id);
                            await reload();
                            onPublished();
                          } catch (e) {
                            setError(e instanceof Error ? e.message : String(e));
                          }
                        }}
                      >
                        delist
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
