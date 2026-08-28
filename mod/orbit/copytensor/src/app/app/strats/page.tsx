"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import type { CopyConfig, ServerStrat } from "../lib/types";
import {
  fetchCopies, pauseCopy, resumeCopy, deleteCopy, syncCopy, shortSs58,
  fetchHubStrats, cloneStrat,
} from "../lib/api";
import CopyForm from "../components/CopyForm";
import PageHeader from "../components/PageHeader";
import { useSidebar } from "../context/SidebarContext";

type Mode = "index" | "single" | "hub";

function StratsBody() {
  const params = useSearchParams();
  const target = params.get("target") || "";
  const { openStrat } = useSidebar();
  const [mode, setMode] = useState<Mode>("index");
  const [copies, setCopies] = useState<CopyConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    fetchCopies()
      .then(setCopies)
      .catch(() => {})
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  // Old ?target= links (and anything else that deep-links a trader) now open
  // the drawer's builder with that address already in the basket.
  useEffect(() => {
    if (target) openStrat(target);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target]);

  const runOp = async (id: string, op: () => Promise<unknown>) => {
    setBusyId(id);
    try { await op(); } finally { setBusyId(null); load(); }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="STRATS"
        right={
          <>
            <button
              className={`pixel-btn text-[11px] px-3 py-1 flex-1 md:flex-none ${
                mode === "index" ? "border-green-400 text-green-400" : ""
              }`}
              onClick={() => setMode("index")}
            >
              {/* nbsp, not a space: `.pixel-btn` is a flex box, and flex
                  strips whitespace between items — a plain space here
                  rendered as "INDEXOF TRADERS". */}
              INDEX<span className="hidden sm:inline">&nbsp;OF TRADERS</span>
            </button>
            <button
              className={`pixel-btn text-[11px] px-3 py-1 flex-1 md:flex-none ${
                mode === "single" ? "border-green-400 text-green-400" : ""
              }`}
              onClick={() => setMode("single")}
            >
              SINGLE<span className="hidden sm:inline">&nbsp;TARGET</span>
            </button>
            <button
              className={`pixel-btn text-[11px] px-3 py-1 flex-1 md:flex-none ${
                mode === "hub" ? "border-green-400 text-green-400" : ""
              }`}
              onClick={() => setMode("hub")}
              title="Strats other people published"
            >
              HUB
            </button>
          </>
        }
      >
        Mirror a weighted basket of traders, or run a single-target copy —
        with safety limits, pause/resume and force-sync. Don&rsquo;t know who
        to pick?{" "}
        <Link href="/agent" className="text-cyan-400">
          ask the agent
        </Link>{" "}
        to build one.
      </PageHeader>

      {mode === "index" && (
        // The builder lives in the drawer so it stays open next to the board
        // you're picking from — this is the way in from the menu. The blurb
        // stacks above the button under md: beside a 280px cap on a phone it
        // came out one word per line.
        <section className="pixel-panel p-4 flex flex-col md:flex-row md:flex-wrap md:items-center gap-3">
          <div className="min-w-0 md:flex-1">
            <h2 className="font-display text-base font-bold mb-1">
              Strat maker
            </h2>
            <p className="arcade-prose arcade-prose-sm">
              Tick any set of traders — the whole board, your watchlist, a
              subnet’s validators or a list of pasted addresses — weight them,
              and start one copy per trader.
            </p>
          </div>
          <button
            onClick={() => openStrat()}
            className="pixel-btn border-green-400 text-green-400 shrink-0 w-full md:w-auto"
          >
            OPEN STRAT MAKER →
          </button>
        </section>
      )}

      {mode === "hub" && <HubStrats />}

      <section>
        <h2 className="font-display text-lg font-bold mb-3">Active copies</h2>
        {loading ? (
          <p className="text-pixel-gray text-sm">loading…</p>
        ) : copies.length === 0 ? (
          <div className="pixel-panel p-6">
            <p className="arcade-prose">No copies yet. Start one below.</p>
          </div>
        ) : (
          <>
          {/* On a phone a copy is a card: who it mirrors, whether it's live,
              and the three things you can do to it — with the limits it runs
              under as a footnote. */}
          <div className="lg:hidden space-y-2">
            {copies.map((c) => (
              <div key={c.id} className="row-card">
                <div className="flex items-center gap-2">
                  <Link
                    href={`/traders/${c.target_ss58}`}
                    className="font-mono text-pixel-white hover:text-green-400 no-underline truncate min-w-0"
                  >
                    {c.label || shortSs58(c.target_ss58)}
                  </Link>
                  <span
                    className={`pixel-badge shrink-0 ml-auto ${
                      c.status === "active"
                        ? "border-green-400/40 text-green-400"
                        : c.status === "paused"
                          ? "border-amber-400/40 text-amber-400"
                          : "border-red-400/40 text-red-400"
                    }`}
                  >
                    {c.status}
                  </span>
                </div>
                <p className="text-[11px] text-pixel-gray font-mono mt-1">
                  {c.id.slice(0, 8)}… · max {c.config?.max_tao_per_tx}τ/tx ·{" "}
                  {c.config?.daily_limit_tao}τ daily ·{" "}
                  {c.last_sync_block ? `synced #${c.last_sync_block}` : "never synced"}
                </p>
                <div className="flex gap-2 mt-2.5 pt-2.5 border-t border-pixel-white/10">
                  {c.status === "active" ? (
                    <button
                      className="pixel-btn text-[11px] px-3 py-1 flex-1"
                      onClick={() => runOp(c.id, () => pauseCopy(c.id))}
                      disabled={busyId === c.id}
                    >
                      PAUSE
                    </button>
                  ) : c.status === "paused" ? (
                    <button
                      className="pixel-btn text-[11px] px-3 py-1 flex-1 border-green-400 text-green-400"
                      onClick={() => runOp(c.id, () => resumeCopy(c.id))}
                      disabled={busyId === c.id}
                    >
                      RESUME
                    </button>
                  ) : null}
                  <button
                    className="pixel-btn text-[11px] px-3 py-1 flex-1"
                    onClick={() => runOp(c.id, () => syncCopy(c.id))}
                    disabled={busyId === c.id}
                  >
                    SYNC
                  </button>
                  <button
                    className="pixel-btn text-[11px] px-3 py-1 flex-1 border-red-400/50 text-red-400"
                    onClick={() => {
                      if (confirm("Delete this copy?")) runOp(c.id, () => deleteCopy(c.id));
                    }}
                    disabled={busyId === c.id}
                  >
                    DEL
                  </button>
                </div>
              </div>
            ))}
          </div>

          <div className="pixel-panel overflow-hidden hidden lg:block">
            <table className="pixel-table">
              <thead className="sticky">
                <tr>
                  <th>ID</th>
                  <th>Target</th>
                  <th>Status</th>
                  <th>Last sync</th>
                  <th className="num">Max/tx</th>
                  <th className="num">Daily</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {copies.map((c) => (
                  <tr key={c.id}>
                    <td className="font-mono text-[11px] text-pixel-gray">
                      {c.id.slice(0, 8)}…
                    </td>
                    <td>
                      <Link
                        href={`/traders/${c.target_ss58}`}
                        className="font-mono text-pixel-white hover:text-green-400 no-underline"
                      >
                        {c.label || shortSs58(c.target_ss58)}
                      </Link>
                    </td>
                    <td>
                      <span
                        className={`pixel-badge ${
                          c.status === "active"
                            ? "border-green-400/40 text-green-400"
                            : c.status === "paused"
                              ? "border-amber-400/40 text-amber-400"
                              : "border-red-400/40 text-red-400"
                        }`}
                      >
                        {c.status}
                      </span>
                    </td>
                    <td className="text-pixel-gray text-xs font-mono">
                      {c.last_sync_block ? `#${c.last_sync_block}` : "—"}
                    </td>
                    <td className="num font-mono">{c.config?.max_tao_per_tx}τ</td>
                    <td className="num font-mono">{c.config?.daily_limit_tao}τ</td>
                    <td>
                      <div className="flex flex-wrap gap-1">
                        {c.status === "active" ? (
                          <button
                            className="pixel-btn text-[10px] px-2 py-0.5"
                            onClick={() => runOp(c.id, () => pauseCopy(c.id))}
                            disabled={busyId === c.id}
                          >
                            PAUSE
                          </button>
                        ) : c.status === "paused" ? (
                          <button
                            className="pixel-btn text-[10px] px-2 py-0.5 border-green-400 text-green-400"
                            onClick={() => runOp(c.id, () => resumeCopy(c.id))}
                            disabled={busyId === c.id}
                          >
                            RESUME
                          </button>
                        ) : null}
                        <button
                          className="pixel-btn text-[10px] px-2 py-0.5"
                          onClick={() => runOp(c.id, () => syncCopy(c.id))}
                          disabled={busyId === c.id}
                        >
                          SYNC
                        </button>
                        <button
                          className="pixel-btn text-[10px] px-2 py-0.5 border-red-400/50 text-red-400"
                          onClick={() => {
                            if (confirm("Delete this copy?"))
                              runOp(c.id, () => deleteCopy(c.id));
                          }}
                          disabled={busyId === c.id}
                        >
                          DEL
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          </>
        )}
      </section>

      {mode === "single" && (
        <section>
          <h2 className="font-display text-lg font-bold mb-3">New single-target copy</h2>
          <CopyForm defaultTarget={target} />
        </section>
      )}
    </div>
  );
}

/**
 * HubStrats — the public shelf. A strat is private until its owner
 * publishes it; these are the ones they did. CLONE drops a private copy
 * onto your own key, flat (never live), so you can edit and backtest it
 * without touching theirs.
 */
function HubStrats() {
  const { openIndex } = useSidebar();
  const [rows, setRows] = useState<ServerStrat[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [note, setNote] = useState("");

  const load = () => {
    setLoading(true);
    fetchHubStrats()
      .then((r) => setRows(r.strats))
      .catch(() => {})
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  return (
    <section className="pixel-panel p-4 space-y-3">
      <h2 className="font-display text-base font-bold">Published strats</h2>
      {loading ? (
        <p className="text-pixel-gray text-sm">loading…</p>
      ) : rows.length === 0 ? (
        <p className="arcade-prose arcade-prose-sm">
          Nothing published yet. Build a basket in the strat maker, set it to
          PUBLIC, and it shows up here for everyone.
        </p>
      ) : (
        <ul className="space-y-2">
          {rows.map((s) => (
            <li key={s.id} className="border-t-2 border-pixel-border pt-2 first:border-t-0 first:pt-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-[13px] text-pixel-white truncate min-w-0 flex-1">
                  {s.name}
                  <span className="text-pixel-gray"> · {(s.traders || []).length} traders</span>
                </span>
                {s.mine && <span className="pixel-badge text-pixel-gray">yours</span>}
                <span className="pixel-badge text-pixel-gray" title="owner id">
                  {s.owner_fingerprint?.slice(0, 8)}
                </span>
                <button
                  className="pixel-btn text-[10px] px-2 py-0.5 border-green-400 text-green-400"
                  disabled={busyId === s.id}
                  onClick={async () => {
                    setBusyId(s.id);
                    try {
                      const copy = await cloneStrat(s.id);
                      setNote(`cloned as "${copy.name}" — private, on your key`);
                      openIndex(copy.id);
                    } catch (e) {
                      setNote(e instanceof Error ? e.message : String(e));
                    } finally {
                      setBusyId(null);
                    }
                  }}
                >
                  CLONE
                </button>
              </div>
              {s.thesis && (
                <p className="arcade-prose arcade-prose-sm mt-1">{s.thesis}</p>
              )}
              <p className="text-[10px] font-mono text-pixel-gray mt-1">
                {(s.traders || []).slice(0, 5).map((t) => t.label || shortSs58(t.ss58)).join(", ")}
                {(s.traders || []).length > 5 ? " …" : ""}
              </p>
            </li>
          ))}
        </ul>
      )}
      {note && <p className="text-[11px] font-mono text-green-400">{note}</p>}
    </section>
  );
}

export default function StratsPage() {
  return (
    <Suspense fallback={<p className="text-pixel-gray">loading…</p>}>
      <StratsBody />
    </Suspense>
  );
}
