"use client";

import { useCallback, useEffect, useState } from "react";
import {
  api, shortCid, relTime, fmtBytes, BACKEND_COLORS,
  type BackendsResp, type Status, type ObjectRow, type ListResp,
} from "@/lib/api";

type Toast = { kind: "success" | "error" | "info"; text: string } | null;

export default function Page() {
  const [backends, setBackends] = useState<BackendsResp | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [objects, setObjects] = useState<ObjectRow[]>([]);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState("");
  const [ownerFilter, setOwnerFilter] = useState("");
  const [page, setPage] = useState(0);
  const PAGE_SIZE = 20;
  const [toast, setToast] = useState<Toast>(null);
  const [busy, setBusy] = useState<string | null>(null);

  // put form
  const [putPath, setPutPath] = useState("");
  const [putBackend, setPutBackend] = useState("all");
  const [putOwner, setPutOwner] = useState("");

  // replicate form
  const [repCid, setRepCid] = useState("");
  const [repFrom, setRepFrom] = useState("ipfs");
  const [repTo, setRepTo] = useState("filecoin");

  const flash = (kind: Toast extends infer T ? T extends { kind: infer K } ? K : never : never, text: string) => {
    setToast({ kind, text });
    setTimeout(() => setToast(null), 4500);
  };

  const loadAll = useCallback(async () => {
    try {
      const [b, s, l] = await Promise.all([
        api.backends(),
        api.status(),
        api.list({
          limit: PAGE_SIZE,
          offset: page * PAGE_SIZE,
          search: search || undefined,
          owner: ownerFilter || undefined,
        }),
      ]);
      setBackends(b);
      setStatus(s);
      setObjects(l.objects);
      setTotal(l.total);
    } catch (e) {
      flash("error", (e as Error).message);
    }
  }, [page, search, ownerFilter]);

  useEffect(() => { void loadAll(); }, [loadAll]);

  useEffect(() => {
    const t = setInterval(() => { void loadAll(); }, 10000);
    return () => clearInterval(t);
  }, [loadAll]);

  const onPut = async () => {
    if (!putPath) { flash("error", "enter a file path on the server"); return; }
    setBusy("uploading…");
    try {
      const r = await api.put(putPath, putBackend, putOwner || undefined);
      const ok = Object.entries(r.results).filter(([, v]) => v.ok);
      const failed = Object.entries(r.results).filter(([, v]) => !v.ok);
      flash(
        ok.length ? "success" : "error",
        `${ok.length}/${ok.length + failed.length} backends took it: ${ok.map(([k]) => k).join(", ") || "none"}`,
      );
      setPutPath("");
      await loadAll();
    } catch (e) {
      flash("error", (e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const onReplicate = async () => {
    if (!repCid) return;
    setBusy(`replicating ${shortCid(repCid)}…`);
    try {
      const r = await api.replicate(repCid, repFrom, repTo);
      flash(r.ok ? "success" : "error", JSON.stringify(r).slice(0, 200));
      await loadAll();
    } catch (e) {
      flash("error", (e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const onRm = async (cid: string, backend: string) => {
    setBusy(`removing…`);
    try {
      await api.rm(cid, backend);
      flash("success", `removed ${shortCid(cid)} from ${backend}`);
      await loadAll();
    } catch (e) {
      flash("error", (e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const aliveCount = backends ? Object.values(backends).filter((b) => b.available).length : 0;
  const totalBackends = backends ? Object.keys(backends).length : 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="wrap">
      {/* Header */}
      <div className="brand-bar">
        <div className="brand-logo">
          <span className="ms-mark" />
          <span>multistore</span>
        </div>
        <div className="summary">
          <div className="stat">
            <div className="v">{aliveCount}/{totalBackends || "—"}</div>
            <div className="k">backends alive</div>
          </div>
          <div className="stat">
            <div className="v">{status?.total_objects ?? "—"}</div>
            <div className="k">objects indexed</div>
          </div>
        </div>
      </div>

      <h1 className="title">Unified Storage</h1>
      <div className="subtitle">
        IPFS · Filecoin · Hippius · Arweave · local FS — one panel, one index.
      </div>

      {toast && <div className={`toast ${toast.kind}`}>{toast.text}</div>}
      {busy && <div className="toast info">⟳ {busy}</div>}

      {/* Backends grid */}
      <div className="panel" style={{ marginTop: 22 }}>
        <div className="row" style={{ marginBottom: 14 }}>
          <span className="section-label" style={{ marginBottom: 0 }}>Backends</span>
          <span className="spacer" />
          <span className="dim mono" style={{ fontSize: 12 }}>auto-refreshing every 10s</span>
        </div>
        <div className="backend-grid">
          {backends && Object.entries(backends).map(([name, info]) => (
            <div key={name} className={`backend-card ${info.available ? "alive" : "dead"}`}>
              <div className="backend-name">
                <span className={`dot ${info.available ? "green" : "red"}`} />
                <span>{name}</span>
                <span className="spacer" />
                <span className={`pill ${BACKEND_COLORS[name] || "dim"}`}>{info.available ? "alive" : "down"}</span>
              </div>
              <div className="backend-count">{status?.by_backend?.[name] ?? "—"}</div>
              <div className="backend-meta">{info.module}</div>
            </div>
          ))}
        </div>
      </div>

      {/* PUT */}
      <div className="panel">
        <div className="section-label">Upload</div>
        <div className="row">
          <input
            type="text"
            placeholder="/path/to/file (on the server)"
            value={putPath}
            onChange={(e) => setPutPath(e.target.value)}
            style={{ flex: 1, minWidth: 280 }}
          />
          <select
            value={putBackend}
            onChange={(e) => setPutBackend(e.target.value)}
            style={{ width: 160 }}
          >
            <option value="all">all (fan-out)</option>
            {backends && Object.entries(backends).filter(([, v]) => v.available).map(([name]) => (
              <option key={name} value={name}>{name}</option>
            ))}
          </select>
          <input
            type="text"
            placeholder="owner 0x… (optional)"
            value={putOwner}
            onChange={(e) => setPutOwner(e.target.value)}
            style={{ width: 220 }}
          />
          <button className="primary" onClick={onPut} disabled={!putPath || !!busy}>
            Put
          </button>
        </div>
        <div className="subtitle" style={{ marginTop: 10, fontSize: 13 }}>
          Choose <span className="pill green">all</span> to fan-out for max durability, or target a single backend.
        </div>
      </div>

      {/* REPLICATE */}
      <div className="panel">
        <div className="section-label">Replicate</div>
        <div className="row">
          <input
            type="text"
            placeholder="CID to replicate"
            value={repCid}
            onChange={(e) => setRepCid(e.target.value)}
            style={{ flex: 1, minWidth: 280 }}
          />
          <select value={repFrom} onChange={(e) => setRepFrom(e.target.value)} style={{ width: 140 }}>
            {backends && Object.keys(backends).map((b) => <option key={b} value={b}>from: {b}</option>)}
          </select>
          <span className="dim">→</span>
          <select value={repTo} onChange={(e) => setRepTo(e.target.value)} style={{ width: 140 }}>
            {backends && Object.keys(backends).map((b) => <option key={b} value={b}>to: {b}</option>)}
          </select>
          <button onClick={onReplicate} disabled={!repCid || !!busy}>
            Replicate
          </button>
        </div>
      </div>

      {/* OBJECTS */}
      <div className="panel">
        <div className="row" style={{ marginBottom: 14 }}>
          <span className="section-label" style={{ marginBottom: 0 }}>Objects</span>
          <span className="spacer" />
          <input
            type="search"
            placeholder="search by CID or key…"
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(0); }}
            style={{ width: 240 }}
          />
          <input
            type="text"
            placeholder="owner 0x…"
            value={ownerFilter}
            onChange={(e) => { setOwnerFilter(e.target.value); setPage(0); }}
            style={{ width: 200 }}
          />
          <span className="muted-num">{total} total · page {page + 1}/{totalPages}</span>
        </div>

        {objects.length === 0 ? (
          <div className="dim" style={{ padding: 24, textAlign: "center", fontSize: 14 }}>
            no objects {search || ownerFilter ? "matching that filter" : "yet — upload one above"}
          </div>
        ) : (
          <ul className="objects">
            {objects.map((o) => (
              <li key={`${o.cid}-${o.backend}-${o.timestamp}`}>
                <div style={{ minWidth: 0 }}>
                  <div className="cid" title={o.cid}>{shortCid(o.cid)}</div>
                  {o.key && <div className="dim" style={{ fontSize: 12, marginTop: 4 }}>{o.key}</div>}
                </div>
                <span className={`pill ${BACKEND_COLORS[o.backend] || "dim"}`}>{o.backend}</span>
                <span className="muted-num">{o.size ? fmtBytes(o.size) : ""} · {relTime(o.timestamp)}</span>
                <button className="danger" onClick={() => onRm(o.cid, o.backend)} disabled={!!busy}>rm</button>
              </li>
            ))}
          </ul>
        )}

        {totalPages > 1 && (
          <div className="row" style={{ marginTop: 14, justifyContent: "center" }}>
            <button onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0}>prev</button>
            <span className="muted-num">{page + 1} / {totalPages}</span>
            <button onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))} disabled={page >= totalPages - 1}>next</button>
          </div>
        )}
      </div>
    </div>
  );
}
