"use client";

// ── The mark in the corner ──────────────────────────────────────────────
// The console's brand sits at the FAR top-LEFT of the header — ahead of the
// /{mod} name pill, the first thing on the row — and it is the mod protocol's
// cube, not this console's own device. A module in the protocol should look
// like one of the protocol's. (It closed the row on the right until the
// panel it opens started fighting the account chip for the same corner; the
// dropdown hangs left-aligned under it now, so it opens INTO the header
// instead of off the edge of the viewport.)
//
// Clicking the mark GOES HOME — to this module itself, the build mod, the one
// thing every other address in this console is a detour from. That is what a
// mark in the top-left corner means everywhere else on the web, so it means
// it here too; it used to lead to the protocol's front page, which is one
// level up and not what anybody clicking it was reaching for.
//
// The owner can still replace the mark: the caret beside it opens a panel to
// set a glyph, an image URL, or upload a file. That choice is NOT this
// console's state — it lives in the `logo` module, which keeps every module's
// mark and only accepts a change signed by the address in that module's own
// config.json.
//
// So a save here costs one wallet signature, and it is the wallet's signature
// that authorizes it, not this session. The console mints a mod-protocol
// token (`mintToken`), /api/logo forwards it, and the logo module decides.
// Build never holds the authority to repaint its own header, which is exactly
// the property that makes it safe for this panel to exist at all.

import { useCallback, useEffect, useRef, useState } from "react";
import { ModCube } from "./Icons";

export type PublicLogo =
  | { kind: "cube"; updated?: number }
  | { kind: "glyph"; glyph: string; updated?: number }
  | { kind: "url" | "image"; src: string; updated?: number };

/** 512KB — matches the server's ceiling, refused here so the browser doesn't
 *  base64 half a megabyte just to be told no. */
const MAX_UPLOAD_BYTES = 512 * 1024;

export function BrandMark({
  basePath,
  homeHref,
  onHome,
  token,
  isOwner,
  mintToken,
  height = 30,
}: {
  basePath: string;
  /** Where the mark leads: this module's own address. A real href, so
   *  middle-click and cmd-click open the build mod in a new tab like any
   *  other link. */
  homeHref: string;
  /** Same trip without the page load — the console is one long-lived mount,
   *  so a plain navigation would throw away every open pane to arrive
   *  somewhere it can reach by moving its own address. Modifier-clicks skip
   *  this and let the browser have the link. */
  onHome?: () => void;
  /** The console's build-API bearer — proves you are signed in HERE. */
  token: string | null;
  isOwner: boolean;
  /** Mint (or reuse) the owner's mod-protocol token — one wallet signature.
   *  Absent means this session has no wallet that can sign, and the panel says
   *  so instead of offering a form that can only fail. */
  mintToken?: () => Promise<string | null>;
  height?: number;
}) {
  const [logo, setLogo] = useState<PublicLogo>({ kind: "cube" });
  const [open, setOpen] = useState(false);
  const [glyph, setGlyph] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  /** What the logo module says about this mark: where it came from, who owns
   *  it, and the host command that sets it without a browser. */
  const [meta, setMeta] = useState<{ source?: string; owner?: string | null; cli?: string }>({});
  const boxRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${basePath}/api/logo`, { cache: "no-store" });
      const d = await r.json();
      if (d?.ok && d.logo) setLogo(d.logo as PublicLogo);
      if (d?.ok) setMeta({ source: d.source, owner: d.owner, cli: d.cli });
    } catch {
      /* the cube is the fallback, and it's already on screen */
    }
  }, [basePath]);

  useEffect(() => { load(); }, [load]);

  // Close on click-outside / Escape, same as the theme and account menus.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const save = useCallback(
    async (body: Record<string, unknown>) => {
      setBusy(true);
      setError(null);
      try {
        // The signature that actually authorizes this. Asked for first, so a
        // rejected or impossible signature costs nothing else.
        let modToken: string | null = null;
        try {
          modToken = mintToken ? await mintToken() : null;
        } catch (e: any) {
          setError(e?.message || "could not sign with the owner wallet");
          return;
        }
        if (!modToken) {
          setError(
            meta.cli
              ? `connect the owner wallet to sign this, or run \`${meta.cli}\` on the host`
              : "connect the owner wallet — the logo module verifies the owner's signature, not this session"
          );
          return;
        }
        const r = await fetch(`${basePath}/api/logo`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            // Two proofs, two headers. The bearer says you are signed into
            // this console; x-mod-token is the owner signature the logo
            // module checks. Separate, because they are separate claims.
            "x-mod-token": modToken,
            ...(token && token !== "local" ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: JSON.stringify(body),
        });
        const d = await r.json();
        if (!r.ok || !d?.ok) {
          setError(d?.error || `save failed (${r.status})`);
          return;
        }
        setLogo(d.logo as PublicLogo);
        setSaved(true);
        setTimeout(() => setSaved(false), 1600);
        setGlyph("");
        setUrl("");
        load();
      } catch (e: any) {
        setError(e?.message || "save failed");
      } finally {
        setBusy(false);
      }
    },
    [basePath, token, mintToken, meta.cli, load]
  );

  const onFile = useCallback(
    (file: File | null) => {
      if (!file) return;
      if (file.size > MAX_UPLOAD_BYTES) {
        setError(`${Math.round(file.size / 1024)}KB — the limit is ${MAX_UPLOAD_BYTES / 1024}KB. Host it and paste the URL instead.`);
        return;
      }
      const reader = new FileReader();
      reader.onerror = () => setError("could not read that file");
      reader.onload = () => save({ dataUrl: String(reader.result || "") });
      reader.readAsDataURL(file);
    },
    [save]
  );

  // What the corner draws: the owner's mark, or the protocol's cube.
  const mark =
    logo.kind === "glyph" ? (
      <span
        className="leading-none"
        style={{ fontSize: Math.round(height * 0.6), color: "var(--crt-green)" }}
      >
        {logo.glyph}
      </span>
    ) : logo.kind === "url" || logo.kind === "image" ? (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={logo.src}
        alt="Module logo"
        style={{ width: height - 10, height: height - 10, objectFit: "contain", display: "block" }}
        onError={() => setLogo({ kind: "cube" })}
      />
    ) : (
      <ModCube size={height - 10} strokeWidth={1.6} />
    );

  // The module this console IS — the name the mark leads back to.
  const homeName = basePath.replace(/^\/+/, "") || "build-fork";

  const markStyle: React.CSSProperties = {
    height,
    minWidth: height + 4,
    padding: isOwner ? "0 3px 0 6px" : "0 6px",
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: isOwner ? 1 : 0,
    borderRadius: 8,
    color: "var(--crt-green)",
    border: `1px solid color-mix(in srgb, var(--crt-green) ${open ? 45 : 20}%, transparent)`,
    background: open
      ? "color-mix(in srgb, var(--crt-green) 12%, transparent)"
      : "color-mix(in srgb, var(--crt-green) 4%, transparent)",
  };

  // The mark itself: a link home. onHome moves the console's address in
  // place; a modifier-click (or no onHome at all) is left to the browser.
  const markLink = (
    <a
      href={homeHref}
      onClick={(e) => {
        if (!onHome) return;
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
        e.preventDefault();
        setOpen(false);
        onHome();
      }}
      className="inline-flex items-center justify-center transition-all focus-ring"
      style={{ height, color: "var(--crt-green)", cursor: "pointer" }}
      data-tip={`/${homeName} — this module`}
      title={`/${homeName} — this module`}
      aria-label={`Go to the ${homeName} module`}
    >
      {mark}
    </a>
  );

  // Not the owner: the mark is the whole control, and all it does is go home.
  if (!isOwner) {
    return (
      <div className="shrink-0 self-center" style={markStyle}>
        {markLink}
      </div>
    );
  }

  return (
    <div ref={boxRef} className="relative shrink-0 self-center inline-flex" style={markStyle}>
      {markLink}
      {/* Owner only: the mark is the owner's to change, but changing it is the
          rarer errand — it gets the caret, the link gets the click. */}
      <button
        onClick={() => setOpen((v) => !v)}
        className="transition-all focus-ring"
        style={{
          height,
          width: 11,
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 8,
          lineHeight: 1,
          color: "var(--crt-green)",
          opacity: open ? 1 : 0.5,
          cursor: "pointer",
        }}
        data-tip="LOGO — the module's mark, yours to change"
        title="LOGO — the module's mark, yours to change"
        aria-label="Module logo — change it"
        aria-expanded={open}
      >
        ▾
      </button>
      {open && (
        <div
          className="absolute top-full left-0 mt-2 z-[140] p-3 rounded-xl"
          style={{
            width: 268,
            background: "color-mix(in srgb, var(--bg-secondary) 96%, transparent)",
            border: "1px solid var(--border-color)",
            boxShadow: "0 12px 40px rgba(0,0,0,0.5)",
            backdropFilter: "blur(14px) saturate(140%)",
            WebkitBackdropFilter: "blur(14px) saturate(140%)",
          }}
        >
          <div
            className="text-[10px] font-bold uppercase tracking-wider font-code mb-1"
            style={{ color: "var(--text-tertiary)" }}
          >
            Logo — the mark in this corner
          </div>
          {/* Where it lives, and whether we are looking at a live answer. The
              cache exists so a sleeping logo module can't blank the header,
              but a stale mark should say it is stale. */}
          <div className="text-[9px] font-code leading-snug mb-2" style={{ color: "var(--text-tertiary)" }}>
            {meta.source === "logo" ? (
              <>kept in the <span style={{ color: "var(--crt-green)" }}>logo</span> module · one owner signature per change</>
            ) : meta.source === "cache" ? (
              <span style={{ color: "var(--crt-amber)" }}>
                the logo module isn&apos;t answering — showing the last mark it gave us
              </span>
            ) : (
              <>kept in the <span style={{ color: "var(--crt-green)" }}>logo</span> module</>
            )}
          </div>

          {/* GLYPH — one character, the cheapest possible logo. */}
          <label className="block text-[9px] uppercase tracking-wider font-code mb-1" style={{ color: "var(--text-tertiary)" }}>
            glyph
          </label>
          <div className="flex gap-1 mb-2">
            <input
              value={glyph}
              onChange={(e) => setGlyph(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && glyph.trim()) save({ glyph }); }}
              placeholder="◆"
              maxLength={8}
              className="flex-1 min-w-0 px-2 py-1 rounded font-code text-[13px] bg-transparent outline-none"
              style={{ color: "var(--text-primary)", border: "1px solid var(--border-color)" }}
            />
            <button
              disabled={busy || !glyph.trim()}
              onClick={() => save({ glyph })}
              className="px-2 rounded text-[10px] font-bold uppercase tracking-wider font-code transition-all"
              style={{
                color: "var(--crt-green)",
                border: "1px solid color-mix(in srgb, var(--crt-green) 35%, transparent)",
                background: "color-mix(in srgb, var(--crt-green) 8%, transparent)",
                opacity: busy || !glyph.trim() ? 0.4 : 1,
                cursor: busy || !glyph.trim() ? "default" : "pointer",
              }}
            >
              set
            </button>
          </div>

          {/* IMAGE URL — a mark you already host somewhere. */}
          <label className="block text-[9px] uppercase tracking-wider font-code mb-1" style={{ color: "var(--text-tertiary)" }}>
            image url
          </label>
          <div className="flex gap-1 mb-2">
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && url.trim()) save({ url }); }}
              placeholder="https://…/logo.png"
              className="flex-1 min-w-0 px-2 py-1 rounded font-code text-[11px] bg-transparent outline-none"
              style={{ color: "var(--text-primary)", border: "1px solid var(--border-color)" }}
            />
            <button
              disabled={busy || !url.trim()}
              onClick={() => save({ url })}
              className="px-2 rounded text-[10px] font-bold uppercase tracking-wider font-code transition-all"
              style={{
                color: "var(--crt-green)",
                border: "1px solid color-mix(in srgb, var(--crt-green) 35%, transparent)",
                background: "color-mix(in srgb, var(--crt-green) 8%, transparent)",
                opacity: busy || !url.trim() ? 0.4 : 1,
                cursor: busy || !url.trim() ? "default" : "pointer",
              }}
            >
              set
            </button>
          </div>

          {/* UPLOAD — the file lands in ~/.mod/build-fork and is served back from
              this module, so the mark survives whatever host it came from. */}
          <input
            ref={fileRef}
            type="file"
            accept="image/png,image/jpeg,image/webp,image/gif,image/svg+xml"
            className="hidden"
            onChange={(e) => { onFile(e.target.files?.[0] || null); e.target.value = ""; }}
          />
          <div className="flex gap-1">
            <button
              disabled={busy}
              onClick={() => fileRef.current?.click()}
              className="flex-1 px-2 py-1 rounded text-[10px] font-bold uppercase tracking-wider font-code transition-all"
              style={{
                color: "var(--crt-blue)",
                border: "1px solid color-mix(in srgb, var(--crt-blue) 35%, transparent)",
                background: "color-mix(in srgb, var(--crt-blue) 8%, transparent)",
                opacity: busy ? 0.5 : 1,
                cursor: busy ? "default" : "pointer",
              }}
            >
              upload image
            </button>
            <button
              disabled={busy || logo.kind === "cube"}
              onClick={() => save({ reset: true })}
              className="px-2 py-1 rounded text-[10px] font-bold uppercase tracking-wider font-code transition-all"
              style={{
                color: "var(--crt-amber)",
                border: "1px solid color-mix(in srgb, var(--crt-amber) 35%, transparent)",
                background: "color-mix(in srgb, var(--crt-amber) 8%, transparent)",
                opacity: busy || logo.kind === "cube" ? 0.35 : 1,
                cursor: busy || logo.kind === "cube" ? "default" : "pointer",
              }}
              title="Back to the mod protocol cube"
            >
              cube
            </button>
          </div>

          {error && (
            <div className="mt-2 text-[10px] font-code leading-snug" style={{ color: "var(--crt-red, #f87171)" }}>
              {error}
            </div>
          )}
          {saved && !error && (
            <div className="mt-2 text-[10px] font-code" style={{ color: "var(--crt-green)" }}>
              ✓ saved — everyone sees this mark
            </div>
          )}
          {!error && !saved && (
            <div className="mt-2 text-[10px] font-code leading-snug" style={{ color: "var(--text-tertiary)" }}>
              PNG · JPEG · WEBP · GIF · SVG, up to {MAX_UPLOAD_BYTES / 1024}KB.
              {mintToken ? (
                <> Saving asks your wallet for one signature.</>
              ) : meta.cli ? (
                <> No signing wallet in this session — set it on the host with{" "}
                  <span style={{ color: "var(--crt-green)" }}>{meta.cli}</span>.</>
              ) : null}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
