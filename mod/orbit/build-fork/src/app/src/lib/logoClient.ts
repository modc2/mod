// The console's mark — read from, and written to, the `logo` module.
//
// This file used to BE the store: it read and wrote `~/.mod/build-fork/logo.json`
// and spliced the mark into this module's config.json. That state now lives in
// orbit/logo, which owns every module's mark and gates each write on the
// signature of that module's own configured owner.
//
// What build keeps is a *pass-through*, and the shape of it is the point:
//
//   read    anyone. Proxied, then cached on disk — the console is what people
//           open to fix a broken fleet, so it must render its own header when
//           the logo module is down or asleep.
//
//   write   the owner's mod-protocol token, minted in the browser by the
//           owner's wallet and forwarded upstream verbatim. Build cannot mint
//           one. It runs as root on this host and could edit any file it
//           liked, but nothing it can reach forges that signature, so
//           "the console shows the editor" and "the console can change the
//           mark" stay separate powers.
import fs from "fs";
import os from "os";
import path from "path";

/** The logo module's API. Loopback by default: the mark is fleet state, and
 *  the two processes live on the same host. */
export const LOGO_API = (process.env.LOGO_API || "http://127.0.0.1:50760").replace(/\/$/, "");

/** Which module's mark this console is showing. Qualified, because a bare name
 *  would resolve `core/` first if one ever appeared there. */
export const LOGO_MODULE = process.env.LOGO_MODULE || "orbit/build-fork";

/** The activator sleeps idle modules and wakes them on access — but only
 *  through its own port. A direct call to :50760 never wakes anything, so a
 *  failed read knocks here once before giving up. */
const ACTIVATOR = (process.env.LOGO_ACTIVATOR_URL ?? "http://127.0.0.1:9000").replace(/\/$/, "");

const TIMEOUT_MS = Number(process.env.LOGO_TIMEOUT_MS || 2500);
const CACHE_PATH = path.join(os.homedir(), ".mod", "build-fork", "logo-cache.json");

export type PublicLogo =
  | { kind: "cube"; updated?: number }
  | { kind: "glyph"; glyph: string; updated?: number }
  | { kind: "url" | "image"; src: string; updated?: number };

export const CUBE: PublicLogo = { kind: "cube" };

async function call(pathname: string, init: RequestInit = {}, timeout = TIMEOUT_MS) {
  const abort = AbortSignal.timeout(timeout);
  return fetch(`${LOGO_API}${pathname}`, { ...init, signal: abort, cache: "no-store" });
}

/** One knock on the activator, then one retry. Best effort throughout: if the
 *  activator isn't there either, the caller falls back to the cache. */
async function wake(): Promise<void> {
  if (!ACTIVATOR) return;
  try {
    await fetch(`${ACTIVATOR}/api/logo/health`, {
      signal: AbortSignal.timeout(TIMEOUT_MS),
      cache: "no-store",
    });
  } catch {
    /* nothing to wake, or no activator here */
  }
}

// ── read ────────────────────────────────────────────────────────────

function readCache(): PublicLogo | null {
  try {
    const parsed = JSON.parse(fs.readFileSync(CACHE_PATH, "utf8"));
    return parsed && typeof parsed.kind === "string" ? (parsed as PublicLogo) : null;
  } catch {
    return null;
  }
}

function writeCache(logo: PublicLogo): void {
  try {
    fs.mkdirSync(path.dirname(CACHE_PATH), { recursive: true });
    fs.writeFileSync(CACHE_PATH, JSON.stringify(logo, null, 2));
  } catch {
    /* the cache is an optimisation, not a requirement */
  }
}

/** Point an uploaded mark at OUR origin. The logo module serves the bytes, but
 *  routing them through this console means the header keeps working on a
 *  deployment where only build is exposed — and keeps the `<img>` same-origin. */
function localize(logo: PublicLogo, basePath: string): PublicLogo {
  if (logo.kind !== "image") return logo;
  return { kind: "image", src: `${basePath}/api/logo/image?v=${logo.updated || 0}`, updated: logo.updated };
}

/** The mark to draw, and whether it came from the module or from the cache. */
export async function fetchLogo(basePath: string): Promise<{ logo: PublicLogo; source: string }> {
  for (const attempt of [0, 1]) {
    try {
      const r = await call(`/logo/${LOGO_MODULE}`);
      if (r.ok) {
        const data = await r.json();
        const logo = (data?.logo || CUBE) as PublicLogo;
        writeCache(logo);
        return { logo: localize(logo, basePath), source: "logo" };
      }
    } catch {
      if (attempt === 0) await wake();
    }
  }
  const cached = readCache();
  return cached
    ? { logo: localize(cached, basePath), source: "cache" }
    : { logo: CUBE, source: "default" };
}

/** The uploaded bytes, straight from the logo module. */
export async function fetchImage(): Promise<{ bytes: ArrayBuffer; mime: string } | null> {
  try {
    const r = await call(`/logo/${LOGO_MODULE}/image`, {}, TIMEOUT_MS * 2);
    if (!r.ok) return null;
    return {
      bytes: await r.arrayBuffer(),
      mime: r.headers.get("content-type") || "application/octet-stream",
    };
  } catch {
    return null;
  }
}

/** Who the logo module says may change this console's mark. */
export async function fetchOwner(): Promise<any | null> {
  try {
    const r = await call(`/logo/${LOGO_MODULE}/owner`);
    return r.ok ? await r.json() : null;
  } catch {
    return null;
  }
}

// ── write ───────────────────────────────────────────────────────────

export type SaveResult =
  | { ok: true; logo: PublicLogo; by?: string }
  | { ok: false; error: string; status: number };

/** Forward a save. `modToken` is the OWNER's mod-protocol token — this process
 *  never mints it, only carries it, and the logo module is what decides
 *  whether the signature belongs to this module's owner. */
export async function saveLogo(
  body: Record<string, unknown>,
  modToken: string,
  basePath: string
): Promise<SaveResult> {
  let r: Response;
  try {
    r = await call(`/logo/${LOGO_MODULE}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${modToken}` },
      body: JSON.stringify(body),
    }, TIMEOUT_MS * 4);
  } catch {
    await wake();
    try {
      r = await call(`/logo/${LOGO_MODULE}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${modToken}` },
        body: JSON.stringify(body),
      }, TIMEOUT_MS * 4);
    } catch (e: any) {
      return {
        ok: false,
        status: 503,
        error: `the logo module (${LOGO_API}) did not answer — start it with \`m logo/serve\``,
      };
    }
  }
  const data = await r.json().catch(() => null);
  if (!r.ok || !data?.ok) {
    return { ok: false, status: r.status, error: data?.error || `save failed (${r.status})` };
  }
  const logo = data.logo as PublicLogo;
  writeCache(logo);
  return { ok: true, logo: localize(logo, basePath), by: data.by };
}
