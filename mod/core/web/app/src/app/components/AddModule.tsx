"use client";

// Owner-only "Add module from GitHub" affordance for the front door.
//
// The catalog is read-only for the public; the owner can grow it by importing a
// GitHub repo. Auth is a single bearer token (resolved server-side from env or
// ~/.mod/web/owner.token). We remember a token that validates in localStorage so
// the owner unlocks once, then `+ Add module` is a two-field form: repo + name.

import { useEffect, useState } from "react";
import { api, type Module } from "@/lib/api";

const TOKEN_KEY = "mod.web.owner";

function readToken(): string {
  if (typeof window === "undefined") return "";
  try {
    return window.localStorage.getItem(TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

export default function AddModule({ onAdded }: { onAdded?: (m: Module) => void }) {
  const [open, setOpen] = useState(false);
  const [token, setToken] = useState("");
  const [unlocked, setUnlocked] = useState(false);

  const [repo, setRepo] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<Module | null>(null);

  // Validate any remembered token once on mount so returning owners skip the gate.
  useEffect(() => {
    const t = readToken();
    if (!t) return;
    setToken(t);
    api.verifyOwner(t).then((ok) => setUnlocked(ok));
  }, []);

  function close() {
    setOpen(false);
    setErr(null);
    setDone(null);
  }

  async function unlock() {
    setBusy(true);
    setErr(null);
    try {
      const ok = await api.verifyOwner(token.trim());
      if (!ok) {
        setErr("that token isn’t the owner token");
        return;
      }
      try {
        window.localStorage.setItem(TOKEN_KEY, token.trim());
      } catch {
        /* storage blocked — keep it in memory for this session */
      }
      setUnlocked(true);
    } finally {
      setBusy(false);
    }
  }

  async function submit() {
    if (!repo.trim()) return;
    setBusy(true);
    setErr(null);
    setDone(null);
    try {
      const m = await api.addModule(token.trim(), repo.trim(), name.trim() || undefined);
      setDone(m);
      setRepo("");
      setName("");
      onAdded?.(m);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        className="chip-toggle add-mod-btn"
        onClick={() => setOpen(true)}
        title="Owner: import a GitHub repo as a new module"
      >
        ＋ Add module
      </button>

      {open && (
        <div className="add-mod-backdrop" onClick={close}>
          <div className="add-mod-modal" onClick={(e) => e.stopPropagation()}>
            <div className="add-mod-head">
              <div>
                <h3>Add a module</h3>
                <p className="add-mod-sub">
                  Import a GitHub repo into the catalog. Owner only.
                </p>
              </div>
              <button className="add-mod-x" onClick={close} aria-label="close">
                ✕
              </button>
            </div>

            {!unlocked ? (
              <div className="add-mod-body">
                <label className="add-mod-label">owner token</label>
                <input
                  className="add-mod-input"
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && !busy && unlock()}
                  placeholder="paste your mod-web owner token"
                  type="password"
                  autoFocus
                  spellCheck={false}
                />
                <p className="add-mod-hint">
                  Find it on the API host at <code>~/.mod/web/owner.token</code>{" "}
                  (printed in the mod-web-api logs the first time it ran).
                </p>
                {err && <div className="add-mod-err">{err}</div>}
                <button
                  className="btn btn-primary add-mod-go"
                  disabled={busy || !token.trim()}
                  onClick={unlock}
                >
                  {busy ? "checking…" : "Unlock"}
                </button>
              </div>
            ) : done ? (
              <div className="add-mod-body">
                <div className="add-mod-ok">
                  imported <code>{done.name}</code> ✓
                </div>
                <p className="add-mod-hint">
                  It’s in the catalog now. Open its page to browse the source.
                </p>
                <div className="add-mod-row">
                  <button
                    className="chip-toggle"
                    onClick={() => {
                      setDone(null);
                    }}
                  >
                    add another
                  </button>
                  <a className="btn btn-primary add-mod-go" href={`/mods/${done.name}`}>
                    open {done.name} →
                  </a>
                </div>
              </div>
            ) : (
              <div className="add-mod-body">
                <label className="add-mod-label">github repo</label>
                <input
                  className="add-mod-input"
                  value={repo}
                  onChange={(e) => setRepo(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && !busy && submit()}
                  placeholder="owner/repo  ·  or  https://github.com/owner/repo"
                  autoFocus
                  spellCheck={false}
                />
                <label className="add-mod-label">module name (optional)</label>
                <input
                  className="add-mod-input"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && !busy && submit()}
                  placeholder="defaults to the repo name"
                  spellCheck={false}
                />
                {err && <div className="add-mod-err">{err}</div>}
                <button
                  className="btn btn-primary add-mod-go"
                  disabled={busy || !repo.trim()}
                  onClick={submit}
                >
                  {busy ? "cloning…" : "Import module"}
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}
