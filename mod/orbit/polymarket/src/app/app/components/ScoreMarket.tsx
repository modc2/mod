"use client";

// The ▦ SCORE MARKET — a searchable shelf of score functions. Its ONE home
// is the sidebar's STRATS tab (SCORE FUNCTIONS section); the board's ƒ SCORE
// panel only links here. Type "consistent roi" and every listing (curated +
// community) mentioning both words surfaces; USE hands the source to the
// host's setFormula — StratsTab broadcasts it over the formula bus into the
// board's score box, so a listing still compiles through the normal editor
// path and can never rank the board without that editor vetting it.
//
// Publishing goes the other way: whatever is in the score box right now can
// be named, described, and pushed to the community shelf — and shared
// cross-deploy by CID through the same content-addressable store strats use.

import { useCallback, useEffect, useMemo, useState } from "react";

import { getOwnerAddress } from "../lib/access";
import { shortAddress } from "../lib/auth";
import { detectScoreLang } from "../lib/scoreFormula";
import {
  SCORE_FN_LIBRARY, type ScoreFnListing,
  deleteScoreFn, fetchCommunityScoreFns, importScoreFn, publishScoreFn,
  searchScoreFns, shareScoreFn,
} from "../lib/scoreMarket";

interface Props {
  formula: string;
  setFormula: (f: string) => void;
}

const LANG_BADGE: Record<string, string> = { expr: "ƒ", js: "JS ƒ", py: "PY ƒ" };

/** The listing's headline equation — the last `return` expression in its
    source. Gates (`return null` guards) live in the full code view; this is
    the number the survivors actually rank by. */
function equationOf(source: string): string {
  const lines = source
    .split("\n")
    .map((s) => s.trim().replace(/\s*(?:\/\/|#).*$/, "")) // drop trailing comments
    .filter(Boolean);
  for (let i = lines.length - 1; i >= 0; i--) {
    const m = lines[i].match(/^return\s+(.+?);?\s*$/);
    if (m) return m[1];
  }
  return lines[lines.length - 1] ?? "";
}

export default function ScoreMarket({ formula, setFormula }: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [community, setCommunity] = useState<ScoreFnListing[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // The PUBLISH flow: a button that becomes a name+description form. EDIT on
  // one of your own listings prefills it — republishing under the same name
  // updates the listing in place (the API keys on the name's slug).
  const [publishing, setPublishing] = useState(false);
  const [pubName, setPubName] = useState("");
  const [pubDesc, setPubDesc] = useState("");
  const [pubTags, setPubTags] = useState("");
  const [editingName, setEditingName] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Cards show their one-line equation always; the full source expands per
  // card (or all at once), because a score you can't read is a score you
  // shouldn't rank on.
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [allCode, setAllCode] = useState(false);
  const [mineOnly, setMineOnly] = useState(false);

  const [importCid, setImportCid] = useState("");

  const owner = getOwnerAddress();

  const refresh = useCallback(async () => {
    setCommunity(await fetchCommunityScoreFns(owner));
  }, [owner]);

  useEffect(() => {
    if (open) void refresh();
  }, [open, refresh]);

  const mineCount = useMemo(() => community.filter((l) => l.mine).length, [community]);

  const listings = useMemo(() => {
    const pool = mineOnly ? community.filter((l) => l.mine) : [...SCORE_FN_LIBRARY, ...community];
    return searchScoreFns(query, pool);
  }, [query, community, mineOnly]);

  const act = useCallback(async (fn: () => Promise<string | null>) => {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      setNote(await fn());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  if (!open) {
    return (
      <div className="flex items-center gap-2">
        <span className="text-[11px] text-pixel-gray tracking-wider shrink-0 min-w-[72px]" title="A searchable shelf of score functions — curated consistency hunters plus anything published from this deploy.">
          &#9638; MARKET
        </span>
        <button
          className="pixel-btn text-[11px] px-2.5 py-1 border-pixel-border text-pixel-gray hover:text-pixel-green hover:border-pixel-green/60"
          onClick={() => setOpen(true)}
          title='Browse score functions — try searching "consistent roi"'
        >
          BROWSE {SCORE_FN_LIBRARY.length}+ SCORE FUNCTIONS
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-1.5 border border-pixel-border/70 rounded p-2">
      <div className="flex items-center gap-2">
        <span className="text-[11px] text-pixel-gray tracking-wider shrink-0" title="Every listing is just source for the score box — USE drops it in, nothing runs until it compiles there.">
          &#9638; SCORE MARKET
        </span>
        <input
          autoFocus
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          spellCheck={false}
          placeholder='search — "consistent roi", "winrate", "python"…'
          className="pixel-input-sm flex-1 min-w-[140px]"
        />
        <span className="text-[10px] text-pixel-gray shrink-0">{listings.length} fn{listings.length === 1 ? "" : "s"}</span>
        {mineCount > 0 && (
          <button
            className={`pixel-btn text-[10px] px-1.5 py-0.5 shrink-0 ${mineOnly ? "border-pixel-green text-pixel-green" : "border-pixel-border text-pixel-gray hover:text-pixel-green"}`}
            onClick={() => setMineOnly((v) => !v)}
            title="Just your own published functions — the ones you can EDIT and unpublish"
          >
            YOURS {mineCount}
          </button>
        )}
        <button
          className={`pixel-btn text-[10px] px-1.5 py-0.5 shrink-0 ${allCode ? "border-amber-400/70 text-amber-400" : "border-pixel-border text-pixel-gray hover:text-amber-400"}`}
          onClick={() => setAllCode((v) => !v)}
          title="Open every listing's full source at once — the equation line is always visible either way"
        >
          ƒ CODE
        </button>
        <button
          className="pixel-btn text-[11px] px-1.5 py-0.5 text-pixel-gray hover:text-red-400"
          onClick={() => setOpen(false)}
          title="Close the market"
        >
          ✕
        </button>
      </div>

      <div className="max-h-64 overflow-y-auto space-y-1.5 pr-0.5">
        {listings.length === 0 && (
          <div className="text-[11px] text-pixel-gray py-2">
            {mineOnly
              ? "Nothing of yours here yet — write a score and + PUBLISH it below, and it'll live under YOURS."
              : "Nothing matches — fewer words, or write it yourself and PUBLISH it below."}
          </div>
        )}
        {listings.map((l) => {
          const active = formula.trim() === l.source.trim();
          const lang = LANG_BADGE[detectScoreLang(l.source)];
          const key = `${l.builtin ? "b" : "c"}:${l.id}`;
          const showCode = allCode || expanded.has(key);
          return (
            <div
              key={key}
              className={`border rounded p-1.5 ${active ? "border-pixel-green/70" : "border-pixel-border/60"}`}
            >
              <div className="flex items-center gap-2 flex-wrap">
                <span className={`text-[11px] tracking-wider ${active ? "text-pixel-green" : "text-pixel-gray-light"}`}>{l.name}</span>
                <span className="text-[10px] text-amber-400/80" title={l.builtin ? "Runs in your browser like any score" : "Community source — read it before you rank on it; it still only runs in your own browser"}>{lang}</span>
                {l.builtin ? (
                  <span className="text-[10px] text-pixel-gray">CURATED</span>
                ) : (
                  <span className="text-[10px] text-pixel-gray" title={l.author ? `published by ${l.author}` : "community"}>
                    {l.mine ? "YOURS" : l.author ? `@${shortAddress(l.author)}` : "COMMUNITY"}
                  </span>
                )}
                <span className="flex-1" />
                <button
                  className={`pixel-btn text-[10px] px-2 py-0.5 ${active ? "border-pixel-green text-pixel-green" : "border-pixel-border text-pixel-gray hover:text-pixel-green hover:border-pixel-green/60"}`}
                  onClick={() => setFormula(l.source)}
                  title="Drop this function into the score box — the board re-ranks once it compiles"
                >
                  {active ? "IN USE" : "USE"}
                </button>
                {!l.builtin && (
                  <button
                    className="pixel-btn text-[10px] px-2 py-0.5 border-pixel-border text-pixel-gray hover:text-pixel-green"
                    disabled={busy}
                    onClick={() => void act(async () => {
                      const cid = await shareScoreFn(l.id);
                      try { await navigator.clipboard.writeText(cid); } catch {}
                      return `CID ${cid} (copied) — anyone can IMPORT it on their own deploy`;
                    })}
                    title="Get a portable CID for this listing — same content-addressable store shared strats use"
                  >
                    CID
                  </button>
                )}
                {!l.builtin && l.mine && (
                  <button
                    className="pixel-btn text-[10px] px-2 py-0.5 border-pixel-border text-pixel-gray hover:text-pixel-green"
                    disabled={busy}
                    onClick={() => {
                      setFormula(l.source);
                      setPubName(l.name);
                      setPubDesc(l.description);
                      setPubTags(l.tags.join(" "));
                      setEditingName(l.name);
                      setPublishing(true);
                    }}
                    title="Edit — drops the source into the score box and prefills the form below; SAVE under the same name updates this listing in place"
                  >
                    EDIT
                  </button>
                )}
                {!l.builtin && l.mine && (
                  <button
                    className="pixel-btn text-[10px] px-1.5 py-0.5 border-pixel-border text-red-400/80 hover:text-red-400"
                    disabled={busy}
                    onClick={() => void act(async () => {
                      await deleteScoreFn(l.id, owner ?? "");
                      await refresh();
                      return `“${l.name}” unpublished`;
                    })}
                    title="Unpublish — removes it from this deploy's shelf (shared CIDs keep working)"
                  >
                    ✕
                  </button>
                )}
              </div>
              <div className="text-[10px] text-pixel-gray leading-snug mt-0.5">{l.description}</div>
              {l.tags.length > 0 && (
                <div className="text-[10px] text-pixel-gray/70 mt-0.5">{l.tags.map((t) => `#${t}`).join(" ")}</div>
              )}
              {/* The equation — always on the card; click for the full source
                  with its gates. What you read here is exactly what USE runs. */}
              <button
                className="mt-1 w-full text-left font-mono text-[10px] text-pixel-gray-light hover:text-amber-300 flex items-baseline gap-1.5"
                onClick={() => setExpanded((prev) => {
                  const next = new Set(prev);
                  if (next.has(key)) next.delete(key); else next.add(key);
                  return next;
                })}
                title={showCode ? "Hide the full source" : "Show the full source — every gate above this equation"}
              >
                <span className="text-amber-400/80 shrink-0">ƒ =</span>
                <span className="truncate">{equationOf(l.source)}</span>
                <span className="text-pixel-gray/70 shrink-0 ml-auto">{showCode ? "▴" : "▾ code"}</span>
              </button>
              {showCode && (
                <pre className="mt-1 p-1.5 border border-pixel-border/50 rounded bg-black/30 font-mono text-[10px] text-pixel-gray-light leading-snug whitespace-pre-wrap overflow-x-auto">
                  {l.source}
                </pre>
              )}
            </div>
          );
        })}
      </div>

      {/* PUBLISH the score currently in the editor + IMPORT by CID. */}
      <div className="flex items-center gap-2 flex-wrap pt-0.5 border-t border-pixel-border/40">
        {publishing ? (
          <span className="inline-flex items-center gap-1 flex-wrap">
            {editingName && (
              <span className="text-[10px] text-amber-400/80 tracking-wider" title="Saving under this exact name updates the listing in place; a new name publishes a separate copy.">
                EDITING “{editingName}”
              </span>
            )}
            <input
              autoFocus
              className="pixel-input-sm w-32 text-[11px]"
              value={pubName}
              spellCheck={false}
              placeholder="name"
              onChange={(e) => setPubName(e.target.value)}
            />
            <input
              className="pixel-input-sm w-56 text-[11px]"
              value={pubDesc}
              spellCheck={false}
              placeholder="what does it look for?"
              onChange={(e) => setPubDesc(e.target.value)}
            />
            <input
              className="pixel-input-sm w-32 text-[11px]"
              value={pubTags}
              spellCheck={false}
              placeholder="tags (roi steady…)"
              onChange={(e) => setPubTags(e.target.value)}
              title="Space- or comma-separated — tags are searchable, like #consistent #roi on the curated shelf"
            />
            <button
              className="pixel-btn text-[10px] px-2 py-0.5 border-green-400/70 text-green-400 disabled:opacity-50"
              disabled={busy || !pubName.trim() || !owner}
              onClick={() => void act(async () => {
                const renamed = editingName !== null && pubName.trim() !== editingName;
                await publishScoreFn({
                  owner: owner ?? "",
                  name: pubName.trim(),
                  description: pubDesc.trim(),
                  source: formula,
                  tags: pubTags.split(/[\s,]+/).filter(Boolean),
                });
                setPublishing(false);
                setEditingName(null);
                setPubName("");
                setPubDesc("");
                setPubTags("");
                await refresh();
                return renamed
                  ? "Published under the new name — the old listing is still there; ✕ it if you meant a rename."
                  : editingName
                    ? "Saved — the listing updated in place."
                    : "Published — it's on the shelf, and CID gives you a portable link.";
              })}
            >
              &#10003; {editingName ? "SAVE" : "PUBLISH"}
            </button>
            <button
              className="pixel-btn text-[10px] px-1.5 py-0.5 text-pixel-gray"
              onClick={() => { setPublishing(false); setEditingName(null); }}
            >
              ✕
            </button>
          </span>
        ) : (
          <button
            className="pixel-btn text-[10px] px-2 py-0.5 border-dashed border-pixel-border text-pixel-gray hover:text-pixel-green disabled:opacity-50"
            disabled={!formula.trim() || !owner}
            onClick={() => setPublishing(true)}
            title={owner ? "Publish the score that's in the box right now to this shelf" : "Sign in first — publishing needs the connected wallet"}
          >
            + PUBLISH CURRENT SCORE
          </button>
        )}
        <span className="flex-1" />
        <input
          className="pixel-input-sm w-44 text-[11px] font-mono"
          value={importCid}
          spellCheck={false}
          placeholder="import by CID…"
          onChange={(e) => setImportCid(e.target.value)}
          title="Paste a CID someone shared — the function lands on your shelf as your own copy"
        />
        <button
          className="pixel-btn text-[10px] px-2 py-0.5 border-pixel-border text-pixel-gray hover:text-pixel-green disabled:opacity-50"
          disabled={busy || !importCid.trim() || !owner}
          onClick={() => void act(async () => {
            await importScoreFn(importCid.trim(), owner ?? "");
            setImportCid("");
            await refresh();
            return "Imported — it's on the shelf under YOURS.";
          })}
        >
          IMPORT
        </button>
      </div>

      {error && <div className="text-[11px] text-red-400 leading-snug">{error}</div>}
      {note && !error && (
        <div className="text-[11px] text-pixel-gray-light leading-snug break-all">
          <span className="text-green-400/80">&#9638;</span> {note}
        </div>
      )}
    </div>
  );
}
