"use client";

// THE STRATS TAB — where strats are BUILT, MANAGED and SHARED.
//
// The side panel's rail used to be INDEX · BACKTEST · LIVE, with the strat
// list squeezed into INDEX between the money and the copy book. That put
// "which strat is my capital on" and "let me author a new strategy" in the
// same 340px block, so building got two buttons and sharing got none. The
// split now:
//
//   INDEX   →  allocation. Whose money, how it's split across strats,
//              deposit/withdraw. (StratBlock, MoneyBlock.)
//   STRATS  →  this tab. Create, fork, rename, delete, chat with, and
//              SHARE strats. Every strat is PRIVATE BY DEFAULT; a per-row
//              toggle publishes it to the community gallery, and the
//              gallery below lets you fork anyone else's back in.
//
// Four sections, top to bottom in the order a user grows into them:
//
//   MY STRATS   the saved list with full management — the one place a strat
//               is renamed, forked, deleted or published. Selecting still
//               sets the ACTIVE strat (BACKTEST/LIVE read it).
//   BUILD       the machines that write strats for you: the AUTO STRAT
//               factory (one agent run invents + benches a recipe) and the
//               STRAT LAB (an agent that iterates until the data clears the
//               confidence bar). Both register their output in MY STRATS.
//   COMMUNITY   every published recipe strat on this deploy — fork one into
//               a private copy you own. Your own published cards show here
//               too so you can see exactly what's public and pull it back.
//   CODE        user-written Strat classes (mod.py) — upload, publish, and
//               the CID share/import path that works across deploys.
//
// Publishing a RECIPE strat goes through useStratManager.setVisibility → the
// plaintext /strats/public gallery (stratSync.ts); the local token that
// published is the only credential that can unpublish. CODE strats have their
// own owner-keyed public flag + CID store (UserStratsPanel). Nothing here
// touches allocation: funding lives on INDEX, running lives on LIVE.

import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { useAuth } from "../context/AuthContext";
import { DEFAULT_STRATS, orderedTemplates, traderIndexTemplate } from "../lib/defaultStrats";
import { useStratManager } from "../lib/stratManager";
import { useStratStats } from "../lib/stratStats";
import { fetchPublicStrats, type PublicStratEntry } from "../lib/stratSync";
import { isTraderIndex } from "../lib/traderIndex";
import { describeTraderFilter } from "../lib/strats/strat";
import { shortAddress } from "../lib/auth";
import AutoStratPanel from "./AutoStratPanel";
import ConfirmDeleteStrat from "./ConfirmDeleteStrat";
import StratChat from "./StratChat";
import StratLab from "./StratLab";
import UserStratsPanel from "./UserStratsPanel";

function SectionHeader({ label, hint }: { label: string; hint: string }) {
  return (
    <div className="flex items-baseline gap-2 px-1 pt-1">
      <span className="text-[11px] font-semibold tracking-[0.2em] text-pixel-white">{label}</span>
      <span className="text-[9.5px] font-mono text-pixel-gray truncate">{hint}</span>
    </div>
  );
}

export default function StratsTab() {
  const { auth } = useAuth();
  const {
    indexes, activeId, select, fork, forkDefault, rename,
    requestDelete, pendingDelete, confirmDelete, cancelDelete,
    stopStrat, setVisibility, importPublic, broadcast,
  } = useStratManager();
  // Only the running set — the money columns live on INDEX.
  const { running: liveStratIds } = useStratStats();

  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  // Held by ID, not by object: the list re-reads every couple of seconds and
  // an open chat must follow the strat's edits rather than pin a stale copy.
  const [chatId, setChatId] = useState<string | null>(null);
  const [shelfOpen, setShelfOpen] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  // Which strat's publish/unpublish call is in flight (per-row spinner-ish).
  const [visBusy, setVisBusy] = useState<string | null>(null);
  const [gallery, setGallery] = useState<PublicStratEntry[]>([]);
  const chatStrat = chatId === null ? null : indexes.find((i) => i.id === chatId) ?? null;

  const commitRename = () => {
    if (renamingId && renameValue.trim()) rename(renamingId, renameValue.trim());
    setRenamingId(null);
  };

  const refreshGallery = useCallback(async () => {
    setGallery(await fetchPublicStrats());
  }, []);

  useEffect(() => {
    void refreshGallery();
  }, [refreshGallery]);

  const toggleVisibility = useCallback(async (id: string, name: string, pub: boolean) => {
    setStatus(null);
    setVisBusy(id);
    const ok = await setVisibility(id, pub);
    setVisBusy(null);
    if (!ok) {
      setStatus(pub
        ? `Couldn't publish "${name}" — sign in first (publishing is keyed to your account so only you can unpublish).`
        : `Couldn't unpublish "${name}".`);
    } else {
      setStatus(pub
        ? `"${name}" is PUBLIC — it's on the community gallery below, anyone can view and fork it.`
        : `"${name}" is private again.`);
    }
    void refreshGallery();
  }, [setVisibility, refreshGallery]);

  const myAddr = auth.address?.toLowerCase() ?? "";

  return (
    <div className="p-2 space-y-3">
      <div className="px-1 text-[9.5px] font-mono leading-snug text-pixel-gray">
        Build, manage and share your strats here. Every strat is{" "}
        <span className="text-pixel-white">private by default</span> — publish one to the
        community gallery when it's ready. Money lives on the{" "}
        <span className="text-pixel-white">INDEX</span> tab: allocate there, run on LIVE.
      </div>

      {/* ── MY STRATS — the management list ── */}
      <section className="space-y-1">
        <SectionHeader label="MY STRATS" hint="click = active strat · double-click name to rename" />
        <div className="flex flex-col gap-0.5">
          {indexes.length === 0 && (
            <div className="px-1.5 py-2 text-[10.5px] font-mono text-pixel-gray">
              No strats yet — <span className="text-pixel-gray-light">+ NEW STRAT</span> makes the default one.
            </div>
          )}

          {indexes.map((idx) => {
            const isActive = idx.id === activeId;
            const isRunning = liveStratIds.has(idx.id);
            const isPublic = idx.visibility === "public";
            const indexed = isTraderIndex(idx);
            return (
              <div
                key={idx.id}
                onClick={() => select(idx.id)}
                className={`relative flex items-center gap-2 rounded-[var(--radius-sm)] px-2.5 py-1.5 text-left cursor-pointer transition-colors ${
                  isActive
                    ? "text-green-400 bg-green-400/10"
                    : "text-pixel-gray hover:text-pixel-white hover:bg-pixel-white/[0.06]"
                }`}
              >
                <span
                  className={`absolute left-0 top-1/2 -translate-y-1/2 w-[3px] rounded-full bg-green-400 transition-all duration-200 ${
                    isActive ? "h-5 opacity-100 shadow-[0_0_10px_rgba(74,222,128,0.7)]" : "h-0 opacity-0"
                  }`}
                />
                {isRunning && (
                  <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse shrink-0" title="Engine running for this strat" />
                )}
                {renamingId === idx.id ? (
                  <input
                    value={renameValue}
                    onChange={(e) => setRenameValue(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") commitRename(); if (e.key === "Escape") setRenamingId(null); }}
                    onBlur={commitRename}
                    onClick={(e) => e.stopPropagation()}
                    autoFocus
                    className="flex-1 min-w-0 bg-transparent border-b border-green-400 text-green-400 font-mono text-[12px] outline-none"
                  />
                ) : (
                  <span
                    onDoubleClick={(e) => { e.stopPropagation(); setRenamingId(idx.id); setRenameValue(idx.name); }}
                    className="flex-1 min-w-0"
                    title="Double-click to rename"
                  >
                    <span className="block truncate text-[12px] font-mono font-semibold">
                      {idx.name}
                      {idx.identity && (
                        <span className="ml-1.5 text-[9px] tracking-[0.1em] text-cyan-400" title={`IDENTITY strat — copies exactly one trader: ${idx.identity}`}>
                          ID
                        </span>
                      )}
                    </span>
                    <span className="block truncate text-[10px] font-mono text-pixel-gray">
                      {indexed ? "1:1 · your $ ÷ their $" : "conviction · flow-sized"}
                      {" "}· {idx.traders.length}T
                      {idx.marketQuery?.trim() && (
                        <span className="text-amber-300/70" title={`Copying only markets matching: ${idx.marketQuery.trim()}`}>
                          {" "}· ⌕ {idx.marketQuery.trim()}
                        </span>
                      )}
                      {idx.filter && (
                        <span className="text-cyan-300/70">
                          {" "}· ▼ {describeTraderFilter(idx.filter)}
                        </span>
                      )}
                    </span>
                  </span>
                )}

                {/* Visibility, on the row — the whole point of this tab.
                    Private is the default; the toggle is the publish. */}
                <button
                  onClick={(e) => { e.stopPropagation(); void toggleVisibility(idx.id, idx.name, !isPublic); }}
                  disabled={visBusy === idx.id}
                  className={`shrink-0 px-1.5 py-0.5 rounded border text-[9px] font-mono font-semibold tracking-[0.1em] transition-colors ${
                    isPublic
                      ? "border-green-400/60 text-green-400 hover:border-red-400/60 hover:text-red-400"
                      : "border-pixel-border text-pixel-gray hover:border-green-400/60 hover:text-green-400"
                  } ${visBusy === idx.id ? "opacity-40" : ""}`}
                  title={
                    isPublic
                      ? "PUBLIC — on the community gallery, anyone can view and fork it. Click to make it private again."
                      : "PRIVATE (the default) — only you can see it. Click to publish it to the community gallery, plaintext, forkable by anyone."
                  }
                >
                  {isPublic ? "PUBLIC" : "PRIVATE"}
                </button>

                <button
                  onClick={(e) => { e.stopPropagation(); setChatId(idx.id); }}
                  className="text-[9.5px] font-mono font-semibold tracking-[0.08em] text-pixel-gray hover:text-green-400 shrink-0"
                  title={`Chat about "${idx.name}" — ask for a change in words and apply the patch it proposes`}
                >
                  ASK
                </button>
                {/* Text, not a glyph: U+2442 has no coverage in the console's
                    font on this host and rendered as tofu. */}
                <button
                  onClick={(e) => { e.stopPropagation(); fork(idx.id); }}
                  className="text-[9.5px] font-mono font-semibold tracking-[0.08em] text-pixel-gray hover:text-green-400 shrink-0"
                  title={`Fork "${idx.name}" — an independent copy, stopped, un-funded and private`}
                >
                  FORK
                </button>
                {isRunning ? (
                  <button
                    onClick={(e) => { e.stopPropagation(); void stopStrat(idx.id); }}
                    className="text-[10px] font-mono text-pixel-gray hover:text-red-400 shrink-0"
                    title="Stop this strat's engine — the wallet's other funded strats keep running"
                  >
                    ■
                  </button>
                ) : (
                  <button
                    onClick={(e) => { e.stopPropagation(); setRenamingId(idx.id); setRenameValue(idx.name); }}
                    className="text-[11px] text-pixel-gray hover:text-green-400 shrink-0"
                    title="Rename"
                  >
                    ✎
                  </button>
                )}
                <button
                  onClick={(e) => { e.stopPropagation(); requestDelete(idx.id); }}
                  className="text-[13px] text-pixel-gray hover:text-red-400 shrink-0"
                  title="Delete (a published strat comes off the gallery too)"
                >
                  ×
                </button>
              </div>
            );
          })}

          <button
            onClick={() => forkDefault(traderIndexTemplate())}
            title="New strat — a TRADER INDEX: copies the bench trade for trade, each one scaled by your capital against that trader's book. Seeded with this week's best traders. Private until you publish it."
            className="rounded-[var(--radius-sm)] border border-dashed border-pixel-border px-2 py-1.5 text-left text-[10.5px] font-mono font-semibold tracking-[0.08em] text-pixel-gray hover:text-green-400 hover:border-green-400/60 transition-colors"
          >
            + NEW STRAT
          </button>
        </div>

        {status && (
          <div className="px-1 text-[9.5px] font-mono leading-snug text-pixel-gray-light break-words">{status}</div>
        )}

        {/* Curated starting points, folded — a first-time user meeting eleven
            recipes has not been helped. */}
        <div className="px-1">
          <button
            onClick={() => setShelfOpen((v) => !v)}
            className="text-[9.5px] font-mono tracking-[0.12em] text-pixel-gray hover:text-pixel-white transition-colors"
          >
            {shelfOpen ? "⌃" : "⌄"} MORE RECIPES ({DEFAULT_STRATS.length})
          </button>
        </div>
        {shelfOpen && (
          <div className="flex flex-col gap-0.5">
            {orderedTemplates().map((t) => (
              <button
                key={t.slug}
                onClick={() => forkDefault(t)}
                title={`Fork "${t.name}" into your strats`}
                className="group w-full flex items-start gap-2 rounded-[var(--radius-sm)] px-2.5 py-1.5 text-left text-pixel-gray hover:text-pixel-white hover:bg-pixel-white/[0.06] transition-colors"
              >
                <span className="flex-1 min-w-0">
                  <span className="block truncate text-[11px] font-mono font-semibold group-hover:text-green-400">
                    {t.name}
                    {t.isDefault && t.slug === traderIndexTemplate().slug && (
                      <span className="ml-1.5 text-[9px] tracking-[0.1em] text-green-400/80">DEFAULT</span>
                    )}
                  </span>
                  <span className="block text-[9.5px] leading-snug text-pixel-gray/80 line-clamp-3">{t.description}</span>
                </span>
                <span className="text-[9.5px] font-mono font-semibold tracking-[0.08em] shrink-0 mt-0.5 opacity-60 group-hover:opacity-100 group-hover:text-green-400">
                  FORK
                </span>
              </button>
            ))}
          </div>
        )}
      </section>

      {/* ── BUILD — the machines that write strats for you ── */}
      <section className="space-y-1" style={{ borderTop: "1px solid var(--border)" }}>
        <SectionHeader label="BUILD" hint="agents that invent + bench strats, registered above when they land" />
        {/* The factory: one agent run invents a strat off the live board and
            benches it over 1/3/7 days — on demand or on a loop. */}
        <AutoStratPanel />
        {/* The lab: an agent that finds, backtests and refines a strat until
            the data clears the confidence bar; ADOPT lands it above, paused. */}
        <StratLab />
      </section>

      {/* ── COMMUNITY — the public recipe gallery on this deploy ── */}
      <section className="space-y-1" style={{ borderTop: "1px solid var(--border)" }}>
        <div className="flex items-center gap-2 px-1 pt-1">
          <SectionHeader label="COMMUNITY" hint="published recipe strats — fork one into a private copy you own" />
          <button
            onClick={() => void refreshGallery()}
            className="ml-auto text-[10px] px-1.5 py-0.5 rounded border border-pixel-border text-pixel-gray hover:text-pixel-white transition-colors"
            title="Refresh the gallery"
          >
            ↻
          </button>
        </div>
        {gallery.length === 0 ? (
          <div className="px-1.5 py-1 text-[10px] font-mono text-pixel-gray">
            Nothing published yet. Flip one of your strats to PUBLIC above and it lists here for everyone.
          </div>
        ) : (
          <div className="flex flex-col gap-0.5">
            {gallery.map((entry) => {
              const mine = myAddr !== "" && entry.owner.toLowerCase() === myAddr;
              const localCopy = indexes.find((i) => i.id === entry.strat.id);
              return (
                <div
                  key={entry.id}
                  className="flex items-center gap-2 rounded-[var(--radius-sm)] px-2.5 py-1.5 text-pixel-gray hover:text-pixel-white hover:bg-pixel-white/[0.06] transition-colors"
                >
                  <span className="flex-1 min-w-0">
                    <span className="block truncate text-[11.5px] font-mono font-semibold">
                      {entry.strat.name}
                      {mine && (
                        <span className="ml-1.5 text-[9px] tracking-[0.1em] text-green-400/90">YOURS</span>
                      )}
                    </span>
                    <span className="block truncate text-[9.5px] font-mono text-pixel-gray">
                      by {mine ? "you" : shortAddress(entry.owner)} · {entry.strat.traders?.length ?? 0}T
                      {entry.strat.marketQuery?.trim() ? ` · ⌕ ${entry.strat.marketQuery.trim()}` : ""}
                    </span>
                  </span>
                  {mine && localCopy ? (
                    <button
                      onClick={() => void toggleVisibility(localCopy.id, localCopy.name, false)}
                      className="shrink-0 px-1.5 py-0.5 rounded border border-pixel-border text-[9px] font-mono font-semibold tracking-[0.1em] text-pixel-gray hover:text-red-400 hover:border-red-400/60 transition-colors"
                      title="Take this strat off the gallery"
                    >
                      MAKE PRIVATE
                    </button>
                  ) : (
                    <button
                      onClick={() => importPublic(entry)}
                      className="shrink-0 px-1.5 py-0.5 rounded border border-pixel-border text-[9px] font-mono font-semibold tracking-[0.1em] text-pixel-gray hover:text-green-400 hover:border-green-400/60 transition-colors"
                      title={`Fork "${entry.strat.name}" into your strats — the copy is private, stopped and yours`}
                    >
                      FORK
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* ── CODE — user-written Strat classes, with the CID share path ── */}
      <section className="space-y-1" style={{ borderTop: "1px solid var(--border)" }}>
        <SectionHeader label="CODE STRATS" hint="Python Strat classes — upload, publish, share by CID across deploys" />
        <UserStratsPanel eoa={auth.address ?? undefined} />
      </section>

      <ConfirmDeleteStrat
        name={pendingDelete === null ? null : indexes.find((i) => i.id === pendingDelete)?.name ?? pendingDelete}
        onConfirm={confirmDelete}
        onCancel={cancelDelete}
      />

      {/* Portaled: the TopBar's backdrop-blur makes the header a containing
          block for fixed children, which would clip a modal rendered in place. */}
      {chatStrat && createPortal(
        <StratChat
          strat={chatStrat}
          eoa={auth.address}
          onClose={() => setChatId(null)}
          onApplied={() => broadcast()}
        />,
        document.body,
      )}
    </div>
  );
}
