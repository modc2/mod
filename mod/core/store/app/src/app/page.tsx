"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { QRCodeSVG } from "qrcode.react";
import { ThemeToggle } from "@/components/ThemeToggle";
import {
  buildModToken,
  connectMetaMask,
  hasMetaMask,
  shortAddress,
} from "@/lib/wallet";
import {
  createLocalKey,
  exportLocalKey,
  forgetLocalKey,
  hasLocalKey,
  importLocalKey,
  loadLocalKey,
  localSign,
} from "@/lib/localKey";
import {
  api,
  ApiError,
  BackendStatus,
  CidGraphData,
  Grant,
  GraphEdge,
  GraphNode,
  MarketBrowse,
  MarketListing,
  MeResponse,
  ObjectInfo,
  PinInfo,
  PoolDetail,
  PoolSummary,
  Quota,
  StoredObject,
  TermsResponse,
} from "@/lib/api";
import { storageGet, storageRemove, storageSet } from "@/lib/safeStorage";
import {
  Ico,
  MarketIcon,
  StackIcon,
  PlusIcon,
  ShareIcon,
  PinIcon,
  PoolIcon,
  GraphIcon,
  ServerIcon,
  TagIcon,
  FreeIcon,
  LockIcon,
  UnlockIcon,
  GlobeIcon,
  ClockIcon,
  CopyIcon,
  BoltIcon,
  TrashIcon,
  GavelIcon,
  SemIcon,
  FileIcon,
  TextIcon,
  JsonIcon,
  ImageIcon,
} from "@/components/icons";

const TOKEN_KEY = "store:token";
const ADDR_KEY = "store:addr";
const MODE_KEY = "store:mode";
const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "";

type View = "market" | "files" | "add" | "shared" | "pins" | "pools" | "graph" | "backends" | "status";

/** How the session was signed: an injected wallet, or a key this browser holds. */
type SignInMode = "wallet" | "local";

/* storage backends surfaced in the UI — 'both' fans out to filecoin+hippius */
const UPLOAD_BACKENDS = ["localfs", "filecoin", "hippius", "lighthouse", "both"] as const;
type Backend = (typeof UPLOAD_BACKENDS)[number];

const BACKEND_BLURB: Record<string, string> = {
  localfs: "Content-addressed files on this server's disk — IPFS-compatible CIDs, zero dependencies.",
  filecoin: "Filecoin via a Lotus daemon + gateway (orbit/filecoin module).",
  hippius: "Hippius — Bittensor substrate storage network with an S3-compatible gateway (orbit/hippius module).",
  lighthouse: "Lighthouse Labs (lighthouse.storage) — perpetual IPFS/Filecoin pinning behind an API key (orbit/lighthouse module).",
};

const DURATIONS: { label: string; ttl: number | null }[] = [
  { label: "15 minutes", ttl: 15 * 60 },
  { label: "1 hour", ttl: 60 * 60 },
  { label: "1 day", ttl: 24 * 60 * 60 },
  { label: "7 days", ttl: 7 * 24 * 60 * 60 },
  { label: "30 days", ttl: 30 * 24 * 60 * 60 },
  { label: "Never (until revoked)", ttl: null },
];

const TICKET_TTLS = [10, 30, 60, 300];

/**
 * One place that turns anything thrown by a fetch/wallet call into a sentence a
 * user can act on. ApiError already carries a clean message; wallet rejections
 * and network drops arrive as raw provider noise, so they get translated here.
 */
function errorText(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  const err = e as { code?: number | string; message?: string };
  if (err?.code === 4001) return "signature declined in your wallet";
  const msg = (err?.message || String(e)).trim();
  if (/user (rejected|denied)/i.test(msg)) return "signature declined in your wallet";
  if (/failed to fetch|networkerror|load failed/i.test(msg)) return "can't reach the store API — check your connection";
  return msg || "something went wrong";
}

function fmtBytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return "∞";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(1)} ${units[i]}`;
}

/* ── screenshots ──────────────────────────────────────────────────────────── */

const IMAGE_EXT: Record<string, string> = {
  "image/png": "png",
  "image/jpeg": "jpg",
  "image/gif": "gif",
  "image/webp": "webp",
  "image/bmp": "bmp",
  "image/svg+xml": "svg",
};

/** Extensions the object page knows how to render inline. */
const IMAGE_KEY_RE = /\.(png|jpe?g|gif|webp|svg|bmp)$/i;

/** screenshot-2026-08-27-143012.png — sortable, unique, and it keeps the
 *  extension the viewer needs to show the thing as an image later. */
function screenshotName(type: string): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  const stamp =
    `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}-` +
    `${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
  return `screenshot-${stamp}.${IMAGE_EXT[type] || "png"}`;
}

/** Clipboard images arrive as "image.png" (or nameless) whatever they are, so a
 *  pasted screenshot gets a timestamped name; real files keep theirs. */
function namedImage(f: File): File {
  const generic = !f.name || /^image\.(png|jpe?g|gif|webp)$/i.test(f.name);
  if (!generic) return f;
  return new File([f], screenshotName(f.type), { type: f.type });
}

function fmtDuration(secs: number | null): string {
  if (secs === null) return "no expiry";
  if (secs <= 0) return "expired";
  const d = Math.floor(secs / 86400);
  const h = Math.floor((secs % 86400) / 3600);
  const m = Math.floor((secs % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m`;
  return `${secs}s`;
}

/** Compact upload stamp for list rows: "3 Aug, 14:37" this year, "3 Aug 2025"
 *  before that — the year only earns its space once it stops being obvious. */
function fmtStamp(secs: number | null | undefined): string {
  if (!secs) return "—";
  const d = new Date(secs * 1000);
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return sameYear
    ? d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function fmtDate(secs: number | null | undefined): string {
  if (!secs) return "—";
  return new Date(secs * 1000).toLocaleString();
}

/* deterministic gradient avatar for a 0x address */
function identiconStyle(addr: string): React.CSSProperties {
  let h = 0;
  for (let i = 2; i < addr.length; i++) h = (h * 31 + addr.charCodeAt(i)) >>> 0;
  const h1 = h % 360;
  const h2 = (h1 + 80 + ((h >> 8) % 140)) % 360;
  const ang = (h >> 16) % 360;
  return {
    background: `linear-gradient(${ang}deg, hsl(${h1} 72% 58%), hsl(${h2} 68% 42%))`,
  };
}

const LogoMark = () => (
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden>
    <defs>
      {/* the brand gradient follows the active theme, not a fixed pair */}
      <linearGradient id="lg-brand" x1="0" y1="0" x2="24" y2="24">
        <stop offset="0" stopColor="var(--grad-a)" />
        <stop offset="1" stopColor="var(--grad-b)" />
      </linearGradient>
    </defs>
    <path d="M12 2.6 20.6 7.4v9.2L12 21.4 3.4 16.6V7.4L12 2.6Z" stroke="url(#lg-brand)" strokeWidth="1.5" strokeLinejoin="round" />
    <path d="M3.6 7.6 12 12.3l8.4-4.7M12 12.4v8.8" stroke="url(#lg-brand)" strokeWidth="1.5" strokeLinejoin="round" />
  </svg>
);

const PhoneIcon = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden>
    <rect x="7" y="2.5" width="10" height="19" rx="2.5" />
    <path d="M10.5 18.5h3" />
  </svg>
);

const SearchIcon = () => (
  <svg className="search-ico" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" aria-hidden>
    <circle cx="10.5" cy="10.5" r="6.5" />
    <path d="M15.5 15.5 21 21" />
  </svg>
);

const PowerIcon = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden>
    <path d="M12 3v8" />
    <path d="M6.6 6.6a7.5 7.5 0 1 0 10.8 0" />
  </svg>
);

const BookIcon = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M4 4.5A2.5 2.5 0 0 1 6.5 2H20v17.5H6.5A2.5 2.5 0 0 0 4 22V4.5Z" />
    <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
  </svg>
);

const WalletIcon = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M20 7H5a2 2 0 0 1 0-4h13v4" />
    <path d="M4 6.5V18a2 2 0 0 0 2 2h14a1 1 0 0 0 1-1V8a1 1 0 0 0-1-1" />
    <circle cx="16.5" cy="13.5" r="1" fill="currentColor" stroke="none" />
  </svg>
);

const KeyIcon = () => (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <circle cx="8" cy="15" r="4" />
    <path d="M10.9 12.1 20 3" />
    <path d="M17 6l2.5 2.5" />
  </svg>
);

export default function Page() {
  const router = useRouter();
  const [hasWallet, setHasWallet] = useState(false);
  const [hasLocal, setHasLocal] = useState(false);
  const [mode, setMode] = useState<SignInMode>("wallet");
  const [address, setAddress] = useState<string | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [me, setMe] = useState<MeResponse | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const [view, setView] = useState<View>("market");
  const [objects, setObjects] = useState<StoredObject[]>([]);
  const [sharedObjects, setSharedObjects] = useState<StoredObject[]>([]);
  const [pools, setPools] = useState<PoolSummary[]>([]);
  const [pins, setPins] = useState<PinInfo[]>([]);

  // search
  const [searchText, setSearchText] = useState("");
  const [semResults, setSemResults] = useState<StoredObject[] | null>(null);

  // upload form
  const [uploadKind, setUploadKind] = useState<"file" | "text" | "json" | "image">("file");
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [textName, setTextName] = useState("");
  const [jsonText, setJsonText] = useState("");
  const [jsonName, setJsonName] = useState("");
  const [jsonErr, setJsonErr] = useState<string | null>(null);
  const [imgName, setImgName] = useState("");
  const [imgUrl, setImgUrl] = useState<string | null>(null);
  const [imgDims, setImgDims] = useState<{ w: number; h: number } | null>(null);
  const [dropHot, setDropHot] = useState(false);
  const [backend, setBackend] = useState<Backend>("localfs");
  const [bkStatus, setBkStatus] = useState<Record<string, BackendStatus> | null>(null);
  const [makePublic, setMakePublic] = useState(false);
  const [uploadPool, setUploadPool] = useState("");

  const [copied, setCopied] = useState<string | null>(null);
  const [serviceStatus, setServiceStatus] = useState<Record<string, unknown> | null>(null);
  const [svcDown, setSvcDown] = useState(false);

  // modals
  const [shareFor, setShareFor] = useState<StoredObject | null>(null);
  const [ticketFor, setTicketFor] = useState<StoredObject | null>(null);
  const [infoFor, setInfoFor] = useState<string | null>(null);
  const [linkPhone, setLinkPhone] = useState<{ code: string; expires: number } | null>(null);
  const [showLocalKey, setShowLocalKey] = useState(false);
  const [openPool, setOpenPool] = useState<PoolDetail | null>(null);
  const [termsDoc, setTermsDoc] = useState<TermsResponse | null>(null);

  // marketplace: "pick" opens the sell modal with an object dropdown;
  // a StoredObject preselects it. bump forces MarketView to refetch.
  const [sellFor, setSellFor] = useState<StoredObject | "pick" | null>(null);
  const [marketBump, setMarketBump] = useState(0);

  const quota: Quota | null = me?.quota ?? null;
  const canStore = !!me?.authorized;
  // Keyed backends the currently selected target depends on that still lack credentials.
  const needsKey = useMemo(() => {
    if (!bkStatus) return [];
    const targets = backend === "both" ? ["filecoin", "hippius"] : [backend];
    return targets.filter((b) => bkStatus[b]?.needs_key);
  }, [backend, bkStatus]);
  // Storing requires a wallet-signed acceptance of the current terms of service.
  const termsSigned = me?.terms ? me.terms.accepted : true;

  const applySession = useCallback((t: string, r: MeResponse, how: SignInMode = "wallet") => {
    storageSet(TOKEN_KEY, t);
    storageSet(ADDR_KEY, r.address);
    storageSet(MODE_KEY, how);
    setToken(t);
    setAddress(r.address);
    setMe(r);
    setMode(how);
  }, []);

  /** Sign a fresh session with the browser-held key. No prompt, no wallet. */
  const localSession = useCallback(async () => {
    const w = loadLocalKey();
    if (!w) return false;
    const t = await buildModToken(w.address, { domain: window.location.host, scope: "store" }, localSign);
    applySession(t, await api.me(t), "local");
    setHasLocal(true);
    return true;
  }, [applySession]);

  // claim a QR handoff (?claim=code)
  useEffect(() => {
    setHasWallet(hasMetaMask());
    setHasLocal(hasLocalKey());
    api.status().then(setServiceStatus).catch(() => setSvcDown(true));

    const url = new URL(window.location.href);
    const code = url.searchParams.get("claim");
    if (code) {
      setBusy("linking this device…");
      api
        .claimHandoff(code)
        .then(async (r) => {
          const me2 = await api.me(r.token);
          applySession(r.token, me2);
          setSuccess(`linked as ${shortAddress(me2.address)} — no wallet needed`);
        })
        .catch((e) => setError(`link failed: ${errorText(e)}`))
        .finally(() => {
          url.searchParams.delete("claim");
          window.history.replaceState({}, "", url.toString());
          setBusy(null);
        });
      return;
    }
    const t = storageGet(TOKEN_KEY);
    const savedMode = (storageGet(MODE_KEY) as SignInMode) || "wallet";
    if (t) {
      api
        .me(t)
        .then((r) => applySession(t, r, savedMode))
        .catch(async (e) => {
          // Only forget the session when the API actually rejected it — an
          // outage (404/5xx) must not silently sign the user out.
          const st = (e as ApiError).status;
          if (st !== 401 && st !== 403) {
            setError(errorText(e));
            return;
          }
          // A local key can re-sign without a prompt, so an expired session
          // renews itself instead of bouncing the user to the sign-in screen.
          if (savedMode === "local" && (await localSession().catch(() => false))) return;
          storageRemove(TOKEN_KEY);
          storageRemove(ADDR_KEY);
          storageRemove(MODE_KEY);
        });
    }
  }, [applySession, localSession]);

  const refreshFiles = useCallback(async (t: string) => {
    try {
      setObjects((await api.list(t)).objects);
    } catch (e) {
      setError(`list failed: ${errorText(e)}`);
    }
  }, []);
  const refreshShared = useCallback(async (t: string) => {
    try {
      setSharedObjects((await api.shared(t)).objects);
    } catch {
      /* ignore */
    }
  }, []);
  const refreshPools = useCallback(async (t: string) => {
    try {
      setPools((await api.pools(t)).pools);
    } catch {
      /* ignore */
    }
  }, []);
  const refreshPins = useCallback(async (t: string) => {
    try {
      setPins((await api.pins(t)).pins);
    } catch {
      /* ignore */
    }
  }, []);
  const refreshMe = useCallback(async (t: string) => {
    try {
      setMe(await api.me(t));
    } catch {
      /* ignore */
    }
  }, []);
  const refreshBackends = useCallback(async (t: string) => {
    try {
      setBkStatus((await api.backendsStatus(t)).backends);
    } catch {
      /* ignore */
    }
  }, []);

  const refreshAll = useCallback(
    (t: string) => {
      refreshFiles(t);
      refreshShared(t);
      refreshPools(t);
      refreshPins(t);
      refreshBackends(t);
    },
    [refreshFiles, refreshShared, refreshPools, refreshPins, refreshBackends]
  );

  useEffect(() => {
    if (token) refreshAll(token);
  }, [token, refreshAll]);

  const signIn = async () => {
    setError(null);
    setBusy("connecting wallet…");
    try {
      const { address: addr } = await connectMetaMask();
      setBusy("waiting for signature…");
      const t = await buildModToken(addr, { domain: window.location.host, scope: "store" });
      setBusy("verifying…");
      const r = await api.me(t);
      applySession(t, r, "wallet");
      setSuccess(r.authorized ? `signed in${r.admin ? " as admin" : ""}` : "signed in — not whitelisted to store");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  /** Sign in with a browser-held keypair — minted on first use. */
  const signInLocal = async () => {
    setError(null);
    const fresh = !hasLocalKey();
    setBusy(fresh ? "creating a local key…" : "signing in…");
    try {
      if (fresh) {
        const { persisted } = createLocalKey();
        if (!persisted) {
          setError(
            "this browser wouldn't save the key (private mode or full storage) — back it up before you store anything, or it's gone when the tab closes"
          );
        }
      }
      await localSession();
      setSuccess(
        fresh
          ? "local account created — back up the key before you store anything"
          : "signed in with this browser's key"
      );
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  // Ends the session but keeps the local key — signing back in is one click.
  // Destroying the key is a separate, spelled-out action in the key modal.
  const signOut = () => {
    storageRemove(TOKEN_KEY);
    storageRemove(ADDR_KEY);
    storageRemove(MODE_KEY);
    setToken(null);
    setMe(null);
    setObjects([]);
    setSharedObjects([]);
    setPools([]);
    setPins([]);
    setShowLocalKey(false);
    setHasLocal(hasLocalKey());
  };

  /** Take an image from anywhere — clipboard, drag & drop, file picker — and
   *  park it in the Add-data form, ready to store. */
  const acceptImage = useCallback(
    (f: File | null | undefined) => {
      if (!f || !f.type.startsWith("image/")) return false;
      const named = namedImage(f);
      setError(null);
      setFile(named);
      setImgName(named.name);
      setUploadKind("image");
      setView("add");
      return true;
    },
    []
  );

  /* live preview of whatever image is staged — object URL dies with the file */
  useEffect(() => {
    if (!file || !file.type.startsWith("image/")) {
      setImgUrl(null);
      setImgDims(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setImgUrl(url);
    setImgDims(null);
    const probe = new window.Image();
    probe.onload = () => setImgDims({ w: probe.naturalWidth, h: probe.naturalHeight });
    probe.src = url;
    return () => URL.revokeObjectURL(url);
  }, [file]);

  /* ⌘V / Ctrl+V anywhere in the app drops a screenshot into the upload form */
  useEffect(() => {
    if (!token || !canStore || !termsSigned) return;
    const onPaste = (e: ClipboardEvent) => {
      const cd = e.clipboardData;
      if (!cd) return;
      const el = e.target as HTMLElement | null;
      const typing = !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);
      // pasting text into a field always wins — only hijack a pure image paste
      if (typing && Array.from(cd.types || []).some((t) => t === "text/plain")) return;
      const item = Array.from(cd.items || []).find(
        (it) => it.kind === "file" && it.type.startsWith("image/")
      );
      const f = item?.getAsFile();
      if (!f) return;
      e.preventDefault();
      if (acceptImage(f)) setSuccess(`screenshot pasted (${fmtBytes(f.size)}) — hit Store to keep it`);
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, [token, canStore, termsSigned, acceptImage]);

  const upload = async () => {
    if (!token) return;
    let payload: File | null = file;
    if (uploadKind === "text") {
      if (!text.trim()) return;
      const name = (textName.trim() || `note-${Date.now()}`).replace(/[^\w.\-]/g, "_");
      payload = new File([text], name.endsWith(".txt") ? name : `${name}.txt`, { type: "text/plain" });
    }
    if (uploadKind === "json") {
      if (!jsonText.trim()) return;
      try {
        JSON.parse(jsonText);
      } catch (e) {
        setJsonErr(errorText(e));
        return;
      }
      setJsonErr(null);
      const name = (jsonName.trim() || `data-${Date.now()}`).replace(/[^\w.\-]/g, "_");
      payload = new File([jsonText], name.endsWith(".json") ? name : `${name}.json`, { type: "application/json" });
    }
    if (uploadKind === "image" && payload) {
      // a renamed screenshot keeps its extension — that's what makes it render
      // as an image on its object page instead of "binary content".
      const want = imgName.trim().replace(/[^\w.\-]/g, "_");
      if (want && want !== payload.name) {
        const ext = IMAGE_EXT[payload.type] || "png";
        payload = new File([payload], IMAGE_KEY_RE.test(want) ? want : `${want}.${ext}`, { type: payload.type });
      }
    }
    if (!payload) return;
    setError(null);
    setSuccess(null);
    setBusy(`storing to ${backend}…`);
    try {
      const r = await api.put(token, payload, backend, {
        public: makePublic,
        pool: uploadPool || undefined,
        // screenshots name themselves — keep that name as the object key instead
        // of the upload-cache one (which carries an epoch-ms prefix).
        key: uploadKind === "image" ? payload.name : undefined,
      });
      const cids = Object.entries(r.results)
        .map(([b, v]) => (v.cid ? `${b}: ${v.cid.slice(0, 16)}…` : `${b}: error — ${v.error}`))
        .join("  •  ");
      const mapped = r.refs && r.refs.length > 0 ? ` • mapped from ${r.refs.length} existing object${r.refs.length > 1 ? "s" : ""}` : "";
      setSuccess(`stored (${makePublic ? "public" : "private"})${uploadPool ? " → pool" : ""}: ${cids}${mapped}`);
      setFile(null);
      setText("");
      setTextName("");
      setJsonText("");
      setJsonName("");
      setImgName("");
      await refreshFiles(token);
      await refreshMe(token);
      if (uploadPool) await refreshPools(token);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const togglePublish = async (o: StoredObject) => {
    if (!token) return;
    const next = o.visibility !== "public";
    setBusy(`${next ? "publishing" : "making private"}…`);
    try {
      await api.publish(token, o.cid, next);
      await refreshFiles(token);
      setSuccess(`${o.cid.slice(0, 12)}… is now ${next ? "public" : "private"}`);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const doPin = async (cid: string, b: string) => {
    if (!token) return;
    setBusy(`pinning ${cid.slice(0, 12)}…`);
    try {
      await api.pin(token, cid, b);
      await refreshPins(token);
      setSuccess(`pinned ${cid.slice(0, 12)}…`);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const openTerms = async () => {
    try {
      setTermsDoc(await api.terms(token));
    } catch (e) {
      setError(errorText(e));
    }
  };

  const signTerms = async () => {
    if (!token) return;
    setBusy("recording signed acceptance…");
    try {
      await api.acceptTerms(token);
      await refreshMe(token);
      setTermsDoc(null);
      setSuccess("terms accepted — you can now store data");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const doRemove = async (o: StoredObject, takedown = false) => {
    if (!token) return;
    let reason: string | undefined;
    if (takedown) {
      const r = prompt("Takedown reason (recorded in the moderation audit log):", "illegal content");
      if (r === null) return;
      reason = r || "admin takedown";
    } else if (!confirm(`Remove ${o.cid.slice(0, 16)}…? This deletes the stored object.`)) {
      return;
    }
    setBusy(`removing ${o.cid.slice(0, 12)}…`);
    try {
      await api.rm(token, o.cid, reason);
      refreshFiles(token);
      refreshShared(token);
      setSuccess(takedown ? `taken down ${o.cid.slice(0, 12)}… (logged)` : `removed ${o.cid.slice(0, 12)}…`);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const copyText = async (s: string, tag: string) => {
    try {
      await navigator.clipboard.writeText(s);
      setCopied(tag);
      setTimeout(() => setCopied((c) => (c === tag ? null : c)), 1500);
    } catch {
      /* ignore */
    }
  };

  const startLinkPhone = async () => {
    if (!token) return;
    setBusy("minting link code…");
    try {
      const h = await api.createHandoff(token, 180);
      setLinkPhone({ code: h.code, expires: h.expires });
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const runSemanticSearch = async () => {
    if (!token || !searchText.trim()) return;
    setBusy("semantic search…");
    try {
      const r = await api.search(token, { semantic_q: searchText.trim(), scope: "all" });
      setSemResults(r.objects);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  // client-side substring filter for the instant text search
  const visibleObjects = useMemo(() => {
    if (semResults) return semResults;
    const q = searchText.trim().toLowerCase();
    if (!q) return objects;
    return objects.filter(
      (o) => o.cid.toLowerCase().includes(q) || (o.key || "").toLowerCase().includes(q)
    );
  }, [objects, searchText, semResults]);

  const usedPct = quota && quota.limit_bytes ? Math.min(100, (quota.used_bytes / quota.limit_bytes) * 100) : 0;

  return (
    <div className="wrap">
      <header className="topbar">
        <div className="topbar-row">
          <div className="brand-block">
            <div className="brand-row">
              <span className="brand-mark"><LogoMark /></span>
              <span className="brand">store</span>
              <span
                className={`svc-dot ${serviceStatus ? "up" : svcDown ? "down" : ""}`}
                title={serviceStatus ? "service online" : svcDown ? "service unreachable" : "connecting…"}
              />
            </div>
            {/* the tagline is for visitors; once signed in that space becomes the search */}
            {!token && (
              <span className="brand-sub">
                marketplace <i>·</i> private storage <i>·</i> timed sharing <i>·</i> pools <i>·</i> cid-agnostic
              </span>
            )}
          </div>
          {token && (
            <CidSearch
              token={token}
              text={searchText}
              setText={(s) => {
                setSearchText(s);
                setSemResults(null);
              }}
              objects={objects}
              shared={sharedObjects}
              onOpen={(o) => router.push(`/o/${encodeURIComponent(o.cid)}`)}
              onInfo={setInfoFor}
              onSemantic={() => {
                setView("files");
                runSemanticSearch();
              }}
              onSeeAll={() => setView("files")}
            />
          )}
          <div className="identity">
            <Link href="/docs" className="ghost icon" title="Documentation">
              <BookIcon />
            </Link>
            <ThemeToggle />
            {!token && hasWallet && (
              <button className="primary" onClick={signIn} disabled={!!busy}>
                <span className="btn-ico"><WalletIcon /></span> Sign in with wallet
              </button>
            )}
            {!token && (
              <button
                className={hasWallet ? "ghost" : "primary"}
                onClick={signInLocal}
                disabled={!!busy}
                title={
                  hasLocal
                    ? "Sign in with the key already stored in this browser"
                    : "Create a keypair in this browser — no wallet extension needed"
                }
              >
                <span className="btn-ico"><KeyIcon /></span>
                {hasLocal ? "Sign in with local key" : hasWallet ? "Use a local key" : "Continue without a wallet"}
              </button>
            )}
            {token && address && (
              <div className="id-chip">
                <span className="id-avatar" style={identiconStyle(address)}>
                  <span className={`dot ${me?.authorized ? "ok" : "warn"}`} />
                </span>
                {me?.admin && <span className="pill admin">owner</span>}
                {!me?.admin && me?.via === "bloctime" && <span className="pill bloctime"><ClockIcon /> bloctime</span>}
                {!me?.admin && me?.via !== "bloctime" && me?.authorized && <span className="pill">member</span>}
                {me && !me.authorized && <span className="pill private">view-only</span>}
                {mode === "local" && <span className="pill">local key</span>}
                <button
                  className="id-addr"
                  title={`${address} — click to copy`}
                  onClick={() => copyText(address, "hdr-addr")}
                >
                  {copied === "hdr-addr" ? "✓ copied" : shortAddress(address)}
                </button>
                <span className="id-sep" />
                {mode === "local" && (
                  <button
                    className="ghost icon"
                    onClick={() => setShowLocalKey(true)}
                    title="Local key — back it up, import another, or forget it"
                  >
                    <KeyIcon />
                  </button>
                )}
                <button className="ghost icon" onClick={startLinkPhone} disabled={!!busy} title="Link a phone — QR sign-in, no wallet needed">
                  <PhoneIcon />
                </button>
                <button className="ghost icon" onClick={signOut} title="Sign out">
                  <PowerIcon />
                </button>
              </div>
            )}
          </div>
        </div>

        {/* tabs live in the bar — they carry their own counts; storage sits on the right */}
        {token && (
          <nav className="navbar">
            {(
              [
                ["market", "Market", <MarketIcon key="i" />, null],
                ["files", "Your objects", <StackIcon key="i" />, objects.length],
                ["add", "Add data", <PlusIcon key="i" />, null],
                ["shared", "Shared", <ShareIcon key="i" />, sharedObjects.length],
                ["pins", "Pins", <PinIcon key="i" />, pins.length],
                ["pools", "Pools", <PoolIcon key="i" />, pools.length],
                ["graph", "Graph", <GraphIcon key="i" />, null],
                ["backends", "Backends", <ServerIcon key="i" />, bkStatus ? Object.values(bkStatus).filter((s) => s.needs_key).length || null : null],
              ] as [View, string, JSX.Element, number | null][]
            ).map(([v, label, icon, n]) => (
              <button key={v} className={`navtab ${view === v ? "active" : ""}`} onClick={() => setView(v)}>
                {icon}
                {label}
                {n !== null && n > 0 && <span className="navtab-n">{n}</span>}
              </button>
            ))}
            <span className="nav-gap" />
            <button
              className={`navtab storage ${view === "status" ? "active" : ""}`}
              onClick={() => setView("status")}
              title={
                quota
                  ? quota.unlimited
                    ? `${fmtBytes(quota.used_bytes)} stored · unlimited (admin)`
                    : `${fmtBytes(quota.used_bytes)} of ${fmtBytes(quota.limit_bytes)} used · ${fmtBytes(quota.remaining_bytes)} left`
                  : "service status"
              }
            >
              <span className={`nav-storage-n ${usedPct > 85 ? "hot" : ""}`}>
                {quota ? fmtBytes(quota.used_bytes) : "Status"}
              </span>
              {quota && !quota.unlimited && (
                <span className="nav-fuel">
                  <span className={`nav-fuel-fill ${usedPct > 85 ? "hot" : ""}`} style={{ width: `${usedPct}%` }} />
                </span>
              )}
            </button>
          </nav>
        )}
      </header>

      {busy && <div className="success-box">⟳ {busy}</div>}
      {error && (
        <div className="error-box">
          <span className="box-msg">✕ {error}</span>
          <button className="box-close" onClick={() => setError(null)} title="Dismiss" aria-label="Dismiss">
            ✕
          </button>
        </div>
      )}
      {success && !busy && <div className="success-box">✓ {success}</div>}

      {!token && (
        <div className="gate-card">
          <div className="gate-glow" />
          <h2>Gated storage</h2>
          <p className="muted">
            Access is restricted to the <strong>mod owner</strong> and on-chain <strong>BlocTime</strong> holders.
            Sign in with your wallet to check your access — holding BlocTime grants entry automatically, no
            whitelisting needed.
          </p>
          <p className="muted" style={{ marginBottom: 0 }}>
            No wallet extension? <strong>{hasLocal ? "Sign in with local key" : "Continue without a wallet"}</strong>{" "}
            {hasLocal ? "uses the keypair already in this browser" : "mints a keypair right here in your browser"} — it
            signs the same way a wallet does, so you can browse the market and hold an address immediately. Back the key
            up from the key button once you&apos;re in; clearing site data destroys it.
          </p>
        </div>
      )}

      {token && !canStore && (
        <div className="error-box">
          Signed in as <code>{address}</code> but not authorized — this store is gated to the mod owner and
          on-chain BlocTime holders. Hold BlocTime to gain access, or ask the owner to whitelist you.
        </div>
      )}

      {/* terms of service gate */}
      {token && canStore && !termsSigned && (
        <div className="gate-card">
          <div className="gate-glow" />
          <h2>One signature to start storing</h2>
          <p className="muted">
            Before your first upload, sign the <strong>terms of service</strong>{" "}
            <span className="pill">v{me?.terms?.version}</span> — you own what you store and are responsible
            for it, and the operator may remove illegal content. Recorded once per version against your address.
          </p>
          <div className="row" style={{ marginTop: 16 }}>
            <button className="primary" onClick={openTerms} disabled={!!busy}>Read &amp; sign terms</button>
          </div>
        </div>
      )}

      {/* upload */}
      {token && canStore && termsSigned && view === "add" && (
        <div className="panel">
          <h2 className="panel-title">Add data</h2>
          <div className="tabs">
            {(["file", "text", "json", "image"] as const).map((k) => (
              <button key={k} className={`tab ${uploadKind === k ? "active" : ""}`} onClick={() => setUploadKind(k)}>
                {k === "file" ? <><FileIcon /> File</> : k === "text" ? <><TextIcon /> Text</> : k === "json" ? <><JsonIcon /> JSON</> : <><ImageIcon /> Screenshot</>}
              </button>
            ))}
          </div>
          {uploadKind === "file" && (
            <input type="file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} disabled={!!busy} />
          )}
          {uploadKind === "image" && (
            <div className="col">
              <div
                className={`shot-zone ${dropHot ? "hot" : ""} ${imgUrl ? "filled" : ""}`}
                onDragOver={(e) => { e.preventDefault(); setDropHot(true); }}
                onDragLeave={() => setDropHot(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDropHot(false);
                  if (!acceptImage(e.dataTransfer.files?.[0])) setError("that drop wasn't an image");
                }}
              >
                {imgUrl ? (
                  <>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img className="shot-preview" src={imgUrl} alt={imgName || "screenshot"} />
                    <div className="row shot-facts">
                      <span className="muted">
                        {imgDims ? `${imgDims.w}×${imgDims.h}` : "…"} · {fmtBytes(file?.size)} · {file?.type || "image"}
                      </span>
                      <button className="ghost" onClick={() => { setFile(null); setImgName(""); }} disabled={!!busy}>
                        clear
                      </button>
                    </div>
                  </>
                ) : (
                  <div className="shot-empty">
                    <span className="shot-key">⌘V</span>
                    <p style={{ margin: 0 }}>
                      Paste a screenshot — anywhere in the app. Or drop an image here.
                    </p>
                    <label className="btn-link" style={{ cursor: "pointer" }}>
                      choose an image…
                      <input
                        type="file"
                        accept="image/*"
                        style={{ display: "none" }}
                        disabled={!!busy}
                        onChange={(e) => { acceptImage(e.target.files?.[0]); e.target.value = ""; }}
                      />
                    </label>
                  </div>
                )}
              </div>
              {imgUrl && (
                <input
                  type="text"
                  placeholder="name (keeps the extension, e.g. bug-report.png)"
                  value={imgName}
                  onChange={(e) => setImgName(e.target.value)}
                  disabled={!!busy}
                />
              )}
            </div>
          )}
          {uploadKind === "text" && (
            <div className="col">
              <input type="text" placeholder="name (optional, e.g. recipe.txt)" value={textName} onChange={(e) => setTextName(e.target.value)} disabled={!!busy} />
              <textarea placeholder="Paste or type any text — stored content-addressed like a file." value={text} onChange={(e) => setText(e.target.value)} rows={6} disabled={!!busy} />
            </div>
          )}
          {uploadKind === "json" && (
            <div className="col">
              <div className="row" style={{ justifyContent: "space-between" }}>
                <input type="text" placeholder="name (optional, e.g. bundle.json)" value={jsonName} onChange={(e) => setJsonName(e.target.value)} disabled={!!busy} />
                <label className="btn-link" style={{ cursor: "pointer" }}>
                  Load .json file…
                  <input
                    type="file"
                    accept=".json,application/json"
                    style={{ display: "none" }}
                    disabled={!!busy}
                    onChange={async (e) => {
                      const f = e.target.files?.[0];
                      if (!f) return;
                      setJsonText(await f.text());
                      setJsonName(f.name);
                      e.target.value = "";
                    }}
                  />
                </label>
              </div>
              <textarea
                placeholder="Paste or type JSON. Any CID string it embeds that matches an existing stored object is auto-linked — visible as a graph in that object's 'graph' tab."
                value={jsonText}
                onChange={(e) => { setJsonText(e.target.value); setJsonErr(null); }}
                rows={8}
                disabled={!!busy}
              />
              {jsonErr && <p className="error-box" style={{ margin: 0 }}>invalid JSON: {jsonErr}</p>}
            </div>
          )}
          <div className="row" style={{ marginTop: 12 }}>
            <select value={backend} onChange={(e) => setBackend(e.target.value as Backend)} disabled={!!busy}>
              {UPLOAD_BACKENDS.map((b) => (
                <option key={b} value={b}>
                  {b}
                  {bkStatus?.[b]?.needs_key ? " — needs API key" : ""}
                </option>
              ))}
            </select>
            <select value={uploadPool} onChange={(e) => setUploadPool(e.target.value)} disabled={!!busy}>
              <option value="">no pool</option>
              {pools.filter((p) => p.role === "owner" || p.role === "editor").map((p) => (
                <option key={p.id} value={p.id}>→ pool: {p.name}</option>
              ))}
            </select>
            <label className="check">
              <input type="checkbox" checked={makePublic} onChange={(e) => setMakePublic(e.target.checked)} disabled={!!busy} />
              public
            </label>
            <button
              className="primary"
              onClick={upload}
              disabled={!!busy || needsKey.length > 0 || (uploadKind === "text" ? !text.trim() : uploadKind === "json" ? !jsonText.trim() : !file)}
            >
              Store
            </button>
          </div>
          {needsKey.map((b) => (
            <BackendKeyPrompt
              key={b}
              backend={b}
              token={token}
              admin={!!me?.admin}
              onSaved={() => refreshBackends(token)}
              setError={setError}
              setSuccess={setSuccess}
            />
          ))}
          <p className="muted hint">
            {makePublic ? "Public: anyone with the link/CID can read it." : "Private: only you — until you share it or add it to a pool."}
          </p>
        </div>
      )}

      {/* your objects — filtered live by the top-bar search */}
      {token && view === "files" && (
        <div className="panel">
          <div className="row" style={{ justifyContent: "space-between", marginBottom: 12 }}>
            <h2 className="panel-title" style={{ margin: 0 }}>Your objects</h2>
            {(searchText.trim() || semResults) && (
              <div className="row search-row">
                <span className="muted" style={{ fontSize: 12 }}>
                  {semResults ? "ranked by meaning for" : "filtered by"} “<span className="mono">{searchText.trim()}</span>”
                </span>
                <button className="ghost" onClick={() => { setSearchText(""); setSemResults(null); }}>clear</button>
              </div>
            )}
          </div>
          {semResults && <p className="muted hint" style={{ marginTop: 0 }}>Ranked by semantic similarity to “{searchText}”. Nearest first.</p>}
          {visibleObjects.length === 0 && <p className="muted">No matching objects.</p>}
          <ul className="objects">
            {visibleObjects.map((o) => (
              <ObjectRow
                key={`${o.cid}-${o.backend}-${o.timestamp ?? 0}`}
                o={o}
                token={token}
                busy={!!busy}
                copied={copied}
                onCopy={copyText}
                onTicket={() => setTicketFor(o)}
                onShare={() => setShareFor(o)}
                onInfo={() => setInfoFor(o.cid)}
                onPublish={() => togglePublish(o)}
                onPin={() => doPin(o.cid, o.backend)}
                onSell={() => setSellFor(o)}
                onRemove={() => doRemove(o)}
              />
            ))}
          </ul>
        </div>
      )}

      {/* the whole CID graph — what references what */}
      {token && view === "graph" && (
        <GraphPanel token={token} onInfo={(c) => setInfoFor(c)} setError={setError} setSuccess={setSuccess} />
      )}

      {/* shared with you */}
      {token && view === "shared" && (
        <div className="panel">
          <h2 className="panel-title">Shared with you</h2>
          <p className="muted hint">Objects others granted you (timed) or shared via a pool.</p>
          {sharedObjects.length === 0 && <p className="muted">Nothing shared with you yet.</p>}
          <ul className="objects">
            {sharedObjects.map((o) => (
              <ObjectRow
                key={`${o.cid}-shared`}
                o={o}
                token={token}
                busy={!!busy}
                copied={copied}
                shared
                onCopy={copyText}
                onTicket={() => setTicketFor(o)}
                onInfo={() => setInfoFor(o.cid)}
                onRemove={me?.admin ? () => doRemove(o, true) : undefined}
                removeLabel={<><GavelIcon /> take down</>}
              />
            ))}
          </ul>
        </div>
      )}

      {/* pins */}
      {token && view === "pins" && (
        <div className="panel">
          <h2 className="panel-title">Pinned objects</h2>
          <p className="muted hint">Pins you’ve placed. Unpin to stop tracking it here.</p>
          {pins.length === 0 && <p className="muted">No pins yet — pin an object from “Your objects”.</p>}
          <ul className="objects">
            {pins.map((p) => (
              <li key={`${p.cid}-${p.backend}`} className="object-card compact">
                <div className="object-meta">
                  <div className="row">
                    <span className={`pill ${p.backend}`}>{p.backend}</span>
                    {p.visibility && <span className={`pill ${p.visibility}`}>{p.visibility === "private" ? <LockIcon /> : <GlobeIcon />} {p.visibility}</span>}
                    {p.key && <span className="muted">{p.key}</span>}
                    {p.size != null && <span className="muted">{fmtBytes(p.size)}</span>}
                  </div>
                  <button className="cid-btn" onClick={() => copyText(p.cid, `pin-${p.cid}`)}>
                    <span className="cid">{p.cid}</span>
                    <span className="muted"> {copied === `pin-${p.cid}` ? "✓ copied" : <CopyIcon />}</span>
                  </button>
                  <div className="row">
                    <Link href={`/o/${encodeURIComponent(p.cid)}`} className="btn-link">view</Link>
                    <button onClick={() => setInfoFor(p.cid)} disabled={!!busy}>info</button>
                    <button onClick={async () => { await api.unpin(token, p.cid, p.backend); refreshPins(token); }} disabled={!!busy}>unpin</button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* pools */}
      {token && view === "pools" && (
        <PoolsView token={token} pools={pools} onChanged={() => token && refreshPools(token)} onOpen={async (id) => setOpenPool(await api.pool(token, id))} setError={setError} />
      )}

      {token && view === "add" && (
        <RegisterExternal token={token} onDone={() => token && refreshFiles(token)} setError={setError} setSuccess={setSuccess} />
      )}

      {/* marketplace — the public storefront; browsable even signed out */}
      {(!token || view === "market") && (
        <MarketView
          token={token}
          admin={!!me?.admin}
          canSell={!!token && canStore && termsSigned}
          bump={marketBump}
          onSell={() => setSellFor("pick")}
          onInfo={(cid) => token && setInfoFor(cid)}
          onAcquired={() => token && refreshShared(token)}
          setError={setError}
          setSuccess={setSuccess}
        />
      )}

      {token && view === "backends" && (
        <BackendsView
          token={token}
          admin={!!me?.admin}
          bkStatus={bkStatus}
          serviceStatus={serviceStatus}
          onRefresh={() => refreshBackends(token)}
          setError={setError}
          setSuccess={setSuccess}
        />
      )}

      {token && view === "status" && (
        <div className="panel">
          <h2 className="panel-title">Service status</h2>
          <pre className="status">{serviceStatus ? JSON.stringify(serviceStatus, null, 2) : "loading…"}</pre>
        </div>
      )}

      {/* modals */}
      {sellFor && token && (
        <SellModal
          token={token}
          objects={objects}
          preselect={sellFor === "pick" ? null : sellFor}
          onClose={() => setSellFor(null)}
          onListed={(title) => {
            setSellFor(null);
            setView("market");
            setMarketBump((n) => n + 1);
            setSuccess(`“${title}” is live on the market`);
          }}
          setError={setError}
        />
      )}
      {shareFor && token && <ShareModal token={token} object={shareFor} onClose={() => setShareFor(null)} setError={setError} setSuccess={setSuccess} />}
      {ticketFor && token && <TicketModal token={token} object={ticketFor} onClose={() => setTicketFor(null)} setError={setError} />}
      {infoFor && token && <InfoModal token={token} cid={infoFor} onClose={() => setInfoFor(null)} onNavigate={setInfoFor} />}
      {linkPhone && <LinkPhoneModal data={linkPhone} onClose={() => setLinkPhone(null)} />}
      {showLocalKey && address && (
        <LocalKeyModal
          address={address}
          copied={copied}
          onCopy={copyText}
          onClose={() => setShowLocalKey(false)}
          onImported={async () => {
            setShowLocalKey(false);
            setBusy("switching key…");
            try {
              await localSession();
              setSuccess("signed in with the imported key");
            } catch (e) {
              setError(errorText(e));
            } finally {
              setBusy(null);
            }
          }}
          onForget={() => {
            forgetLocalKey();
            signOut();
            setSuccess("local key erased from this browser");
          }}
          setError={setError}
        />
      )}
      {termsDoc && token && (
        <Modal title={`Terms of service — v${termsDoc.version}`} onClose={() => setTermsDoc(null)}>
          <div className="terms-text">
            <TermsBody text={termsDoc.text} />
          </div>
          <p className="terms-sign-note">
            Accepting records v{termsDoc.version} against {shortAddress(address ?? "")} together with your
            wallet-signed session proof.
          </p>
          <div className="row">
            <button className="primary" onClick={signTerms} disabled={!!busy}>Sign &amp; accept</button>
            <button className="ghost" onClick={() => setTermsDoc(null)} disabled={!!busy}>not now</button>
          </div>
        </Modal>
      )}
      {openPool && token && (
        <PoolModal
          token={token}
          pool={openPool}
          me={address}
          onClose={() => setOpenPool(null)}
          onDeleted={() => { setOpenPool(null); refreshPools(token); }}
          onChanged={async () => { setOpenPool(await api.pool(token, openPool.id)); refreshPools(token); refreshFiles(token); }}
          setError={setError}
        />
      )}
    </div>
  );
}

/* ─────────────────────── storage backends ─────────────────────── */

const KEYED_BACKENDS = ["hippius", "lighthouse"];

/** Inline credential prompt for a keyed backend (hippius S3 pair, lighthouse API key). */
function BackendKeyPrompt({
  backend, token, admin, onSaved, setError, setSuccess,
}: {
  backend: string;
  token: string;
  admin: boolean;
  onSaved: () => void;
  setError: (s: string | null) => void;
  setSuccess: (s: string | null) => void;
}) {
  const [apiKey, setApiKey] = useState("");
  const [s3Key, setS3Key] = useState("");
  const [s3Secret, setS3Secret] = useState("");
  const [saving, setSaving] = useState(false);

  if (!KEYED_BACKENDS.includes(backend)) {
    return (
      <p className="muted hint">
        <strong>{backend}</strong> is not reachable — check the service status tab.
      </p>
    );
  }
  if (!admin) {
    return (
      <p className="muted hint">
        <KeyIcon /> <strong>{backend}</strong> needs an API key before uploads work — ask the store owner to add one
        in the Backends tab.
      </p>
    );
  }

  const ready = backend === "lighthouse" ? !!apiKey.trim() : !!s3Key.trim() && !!s3Secret.trim();
  const save = async () => {
    setError(null);
    setSaving(true);
    try {
      const body =
        backend === "lighthouse"
          ? { backend, api_key: apiKey.trim() }
          : { backend, s3_key: s3Key.trim(), s3_secret: s3Secret.trim() };
      const r = await api.setBackendKey(token, body);
      setSuccess(
        r.valid === false
          ? `${backend} key saved, but the service rejected it: ${r.error ?? "check the key"}`
          : `${backend} key saved${r.valid ? " and verified ✓" : ""}`
      );
      setApiKey("");
      setS3Key("");
      setS3Secret("");
      onSaved();
    } catch (e) {
      setError(`saving ${backend} key failed: ${errorText(e)}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="key-prompt">
      <p className="muted hint" style={{ margin: "0 0 8px" }}>
        <KeyIcon /> <strong>{backend}</strong> needs credentials.{" "}
        {backend === "lighthouse" ? (
          <>Create an API key at <a href="https://files.lighthouse.storage" target="_blank" rel="noreferrer">files.lighthouse.storage</a> and paste it here — it is stored off-chain on the server (<code>~/.mod/lighthouse/</code>), never in the repo.</>
        ) : (
          <>Paste your Hippius S3 key + secret (console.hippius.com) — stored off-chain on the server (<code>~/.mod/hippius/</code>), never in the repo.</>
        )}
      </p>
      <div className="row">
        {backend === "lighthouse" ? (
          <input
            type="password"
            placeholder="Lighthouse API key"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            disabled={saving}
            style={{ flex: 1 }}
          />
        ) : (
          <>
            <input
              type="password"
              placeholder="S3 access key"
              value={s3Key}
              onChange={(e) => setS3Key(e.target.value)}
              disabled={saving}
              style={{ flex: 1 }}
            />
            <input
              type="password"
              placeholder="S3 secret"
              value={s3Secret}
              onChange={(e) => setS3Secret(e.target.value)}
              disabled={saving}
              style={{ flex: 1 }}
            />
          </>
        )}
        <button className="primary" onClick={save} disabled={saving || !ready}>
          {saving ? "saving…" : "Save key"}
        </button>
      </div>
    </div>
  );
}

/** BACKENDS tab — one sub-tab per storage backend with status + key management. */
function BackendsView({
  token, admin, bkStatus, serviceStatus, onRefresh, setError, setSuccess,
}: {
  token: string;
  admin: boolean;
  bkStatus: Record<string, BackendStatus> | null;
  serviceStatus: Record<string, unknown> | null;
  onRefresh: () => void;
  setError: (s: string | null) => void;
  setSuccess: (s: string | null) => void;
}) {
  const order = ["localfs", "filecoin", "hippius", "lighthouse"];
  const names = bkStatus ? order.filter((b) => b in bkStatus) : order;
  const [tab, setTab] = useState(names[0] ?? "localfs");
  const [probed, setProbed] = useState<Record<string, BackendStatus> | null>(null);
  const [probing, setProbing] = useState(false);

  const st = probed?.[tab] ?? bkStatus?.[tab];
  const detail = (serviceStatus as { backends?: Record<string, unknown> } | null)?.backends?.[tab];

  const probe = async () => {
    setProbing(true);
    setError(null);
    try {
      setProbed((await api.backendsStatus(token, true)).backends);
      setSuccess("credentials validated against the remote services");
    } catch (e) {
      setError(`probe failed: ${errorText(e)}`);
    } finally {
      setProbing(false);
    }
  };

  return (
    <div className="panel">
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 12 }}>
        <h2 className="panel-title" style={{ margin: 0 }}>Storage backends</h2>
        {admin && (
          <button className="ghost" onClick={probe} disabled={probing}>
            {probing ? "validating…" : "⟳ Validate keys"}
          </button>
        )}
      </div>
      <div className="tabs">
        {names.map((b) => (
          <button key={b} className={`tab ${tab === b ? "active" : ""}`} onClick={() => setTab(b)}>
            {b}
            {(probed?.[b] ?? bkStatus?.[b])?.needs_key && <span className="tab-warn"> <KeyIcon /></span>}
          </button>
        ))}
      </div>

      <p className="muted" style={{ marginTop: 4 }}>{BACKEND_BLURB[tab] ?? tab}</p>

      <div className="row" style={{ margin: "8px 0" }}>
        <span className={`pill ${tab}`}>{tab}</span>
        {st?.needs_key ? (
          <span className="pill error"><LockIcon /> needs API key</span>
        ) : (
          <span className="pill public">ready</span>
        )}
        {st?.valid === true && <span className="pill public">key verified ✓</span>}
        {st?.valid === false && <span className="pill error">key rejected</span>}
        {st?.source && <span className="pill">key via {st.source}</span>}
      </div>

      {KEYED_BACKENDS.includes(tab) && admin && (
        <BackendKeyPrompt
          backend={tab}
          token={token}
          admin={admin}
          onSaved={onRefresh}
          setError={setError}
          setSuccess={setSuccess}
        />
      )}
      {KEYED_BACKENDS.includes(tab) && !admin && st?.needs_key && (
        <p className="muted hint">Ask the store owner to configure this backend's API key.</p>
      )}

      {st?.error && <p className="error-box" style={{ marginTop: 8 }}>{st.error}</p>}

      {detail !== undefined && (
        <>
          <h3 className="panel-title" style={{ marginTop: 16 }}>Live status</h3>
          <pre className="status">{JSON.stringify(detail, null, 2)}</pre>
        </>
      )}
    </div>
  );
}

/* ──────────────────────────── object row ──────────────────────────── */

function ObjectRow({
  o, token, busy, copied, shared, onCopy, onTicket, onShare, onInfo, onPublish, onPin, onSell, onRemove, removeLabel,
}: {
  o: StoredObject;
  token: string;
  busy: boolean;
  copied: string | null;
  shared?: boolean;
  onCopy: (s: string, tag: string) => void;
  onTicket: () => void;
  onShare?: () => void;
  onInfo: () => void;
  onPublish?: () => void;
  onPin?: () => void;
  onSell?: () => void;
  onRemove?: () => void;
  removeLabel?: React.ReactNode;
}) {
  const priv = o.visibility === "private";
  const thumb = IMAGE_KEY_RE.test(o.key || "") ? api.getUrl(o.cid, o.backend, priv ? token : null) : null;
  return (
    <li className="object-card compact">
      {thumb && (
        <Link href={`/o/${encodeURIComponent(o.cid)}`} className="obj-thumb" title={o.key || "image"}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={thumb} alt={o.key || o.cid} loading="lazy" />
        </Link>
      )}
      <div className="object-meta">
        <div className="row">
          {o.backend && <span className={`pill ${o.backend}`}>{o.backend}</span>}
          {o.scheme && o.scheme !== "ipfs" && <span className="pill">{o.scheme}</span>}
          {o.visibility && <span className={`pill ${priv ? "private" : "public"}`}>{priv ? <><LockIcon /> private</> : <><GlobeIcon /> public</>}</span>}
          {o.shared_via && <span className="pill">via {o.shared_via}</span>}
          {o.similarity != null && <span className="pill sim">{Math.round(o.similarity * 100)}% match</span>}
          {o.key && <span className="muted">{o.key}</span>}
          {o.size != null && <span className="muted">{fmtBytes(o.size)}</span>}
          {!!o.timestamp && (
            <span className="muted" title={`uploaded ${fmtDate(o.timestamp)} · ${fmtAgo(o.timestamp)}`}>
              <ClockIcon /> {fmtStamp(o.timestamp)}
            </span>
          )}
        </div>
        <button className="cid-btn" title="Copy CID" onClick={() => onCopy(o.cid, o.cid)}>
          <span className="cid">{o.cid}</span>
          <span className="muted"> {copied === o.cid ? "✓ copied" : <CopyIcon />}</span>
        </button>
        {o.semhash && (
          <button className="sem-chip" title="1-bit semantic hash (click to copy)" onClick={() => onCopy(o.semhash!, `sem-${o.cid}`)}>
            <SemIcon /> <span className="mono">{o.semhash}</span>
            <span className="muted"> {copied === `sem-${o.cid}` ? "✓" : ""}</span>
          </button>
        )}
        <div className="row actions">
          <Link href={`/o/${encodeURIComponent(o.cid)}`} className="btn-link" title="Open this object's page">view</Link>
          <button onClick={onTicket} disabled={busy} title="One-time QR / link for your phone"><PhoneIcon /> QR</button>
          <a href={api.getUrl(o.cid, o.backend, priv ? token : null)} target="_blank" rel="noreferrer">download</a>
          <button onClick={onInfo} disabled={busy}>info</button>
          {!shared && onShare && <button onClick={onShare} disabled={busy}>share</button>}
          {!shared && onPublish && <button onClick={onPublish} disabled={busy}>{priv ? "make public" : "make private"}</button>}
          {!shared && onPin && <button onClick={onPin} disabled={busy}>pin</button>}
          {!shared && onSell && <button onClick={onSell} disabled={busy} title="List it on the market — free or priced in BlocTime">🏷 sell</button>}
          {onRemove && <button className="danger" onClick={onRemove} disabled={busy} title="Delete the stored object">{removeLabel ?? <><TrashIcon /> remove</>}</button>}
        </div>
      </div>
    </li>
  );
}

/* ──────────────────────────── top-bar CID search ──────────────────────────── */

/**
 * One search for everything addressable: it filters your own + shared objects as
 * you type, and any string long enough to be a CID can be fetched straight from
 * the network — the store is cid-agnostic, so an unknown CID is still openable.
 * The text doubles as the "Your objects" filter, so the list follows along.
 */
function CidSearch({
  token, text, setText, objects, shared, onOpen, onInfo, onSemantic, onSeeAll,
}: {
  token: string;
  text: string;
  setText: (s: string) => void;
  objects: StoredObject[];
  shared: StoredObject[];
  onOpen: (o: StoredObject) => void;
  onInfo: (cid: string) => void;
  onSemantic: () => void;
  onSeeAll: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(0);
  const raw = text.trim();
  const q = raw.toLowerCase();

  const hits = useMemo(() => {
    if (!q) return [];
    const seen = new Set<string>();
    return [...objects, ...shared].filter((o) => {
      if (seen.has(o.cid)) return false;
      if (!o.cid.toLowerCase().includes(q) && !(o.key || "").toLowerCase().includes(q)) return false;
      seen.add(o.cid);
      return true;
    });
  }, [objects, shared, q]);

  const top = hits.slice(0, 6);
  // Anything CID-shaped and not already an exact local hit can still be fetched.
  const fetchable = raw.length >= 8 && !hits.some((o) => o.cid === raw);
  const rows = top.length + (fetchable ? 1 : 0);

  const openRow = (i: number) => {
    if (i < top.length) onOpen(top[i]);
    else if (fetchable) onOpen({ cid: raw, backend: "" } as StoredObject);
    setOpen(false);
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") { setOpen(false); (e.target as HTMLInputElement).blur(); return; }
    if (!rows) return;
    if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setHi((h) => (h + 1) % rows); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setOpen(true); setHi((h) => (h - 1 + rows) % rows); }
    else if (e.key === "Enter") { e.preventDefault(); openRow(Math.min(hi, rows - 1)); }
  };

  return (
    <div className="search-wrap">
      <div className="search-field">
        <SearchIcon />
        <input
          type="text"
          placeholder="search cid or name…"
          value={text}
          onChange={(e) => { setText(e.target.value); setHi(0); setOpen(true); }}
          onFocus={() => setOpen(true)}
          onBlur={() => setOpen(false)}
          onKeyDown={onKey}
          spellCheck={false}
        />
        {raw && (
          <button className="search-x" onMouseDown={(e) => e.preventDefault()} onClick={() => setText("")} title="Clear">✕</button>
        )}
      </div>

      {open && raw && (
        /* keep focus on the input so blur doesn't close the menu before the click lands */
        <div className="search-menu" onMouseDown={(e) => e.preventDefault()}>
          {top.map((o, i) => (
            <button key={`${o.cid}-${o.backend}`} className={`search-hit ${i === hi ? "on" : ""}`} onClick={() => openRow(i)}>
              <span className="search-hit-main">
                <span className="search-hit-name">{o.key || "(unnamed)"}</span>
                <span className="search-hit-cid mono">{o.cid}</span>
              </span>
              <span className="search-hit-meta">
                {o.shared_via && <span className="pill">shared</span>}
                {o.backend && <span className="muted">{o.backend}</span>}
                <span className="muted">{fmtBytes(o.size)}</span>
              </span>
            </button>
          ))}
          {fetchable && (
            <button className={`search-hit ${hi === top.length ? "on" : ""}`} onClick={() => openRow(top.length)}>
              <span className="search-hit-main">
                <span className="search-hit-name">Fetch this CID from the network</span>
                <span className="search-hit-cid mono">{raw}</span>
              </span>
              <span className="search-hit-meta"><span className="muted">any backend</span></span>
            </button>
          )}
          {!top.length && !fetchable && <div className="search-empty muted">Nothing matches “{raw}”.</div>}
          <div className="search-foot">
            {hits.length > 0 && (
              <button className="ghost" onClick={() => { onSeeAll(); setOpen(false); }}>
                Show {hits.length} in Your objects
              </button>
            )}
            <button className="ghost" onClick={() => { onSemantic(); setOpen(false); }} title="Rank your objects by semantic similarity (1-bit hash)">
              <SemIcon /> semantic
            </button>
            {fetchable && (
              <>
                <button className="ghost" onClick={() => { onInfo(raw); setOpen(false); }}>info</button>
                <a className="btn-link" href={api.getUrl(raw, undefined, token)} target="_blank" rel="noreferrer">download</a>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/* ──────────────────────── one-time ticket modal ───────────────────────── */

function TicketModal({
  token, object, onClose, setError,
}: {
  token: string;
  object: StoredObject;
  onClose: () => void;
  setError: (s: string) => void;
}) {
  const [ttl, setTtl] = useState(10);
  const [ticket, setTicket] = useState<{ code: string; expires: number } | null>(null);
  const [left, setLeft] = useState(0);

  const mint = useCallback(
    async (seconds: number) => {
      try {
        const t = await api.createTicket(token, object.cid, seconds, object.backend || undefined);
        setTicket({ code: t.code, expires: t.expires });
      } catch (e) {
        setError(errorText(e));
      }
    },
    [token, object.cid, object.backend, setError]
  );

  useEffect(() => {
    mint(ttl);
  }, [mint, ttl]);

  useEffect(() => {
    if (!ticket) return;
    const i = setInterval(() => setLeft(Math.max(0, ticket.expires - Math.floor(Date.now() / 1000))), 250);
    return () => clearInterval(i);
  }, [ticket]);

  const url = ticket ? api.ticketUrl(ticket.code) : "";

  return (
    <Modal title="One-time QR / link" onClose={onClose}>
      <p className="muted cid" style={{ marginTop: 0 }}>{object.cid}</p>
      <p className="muted hint" style={{ marginTop: 0 }}>
        Single-use, expiring link — scan it from your phone or copy it. It works <strong>once</strong> and only
        within the window, so it can’t be replayed.
      </p>
      <div className="row" style={{ marginBottom: 12 }}>
        <span className="muted">expires in</span>
        <select value={ttl} onChange={(e) => setTtl(Number(e.target.value))}>
          {TICKET_TTLS.map((s) => (
            <option key={s} value={s}>{s < 60 ? `${s}s` : `${s / 60}m`}</option>
          ))}
        </select>
        <button onClick={() => mint(ttl)}>↻ new code</button>
      </div>
      {url && (
        <div className="center">
          <div className="qr big center">
            <QRCodeSVG value={url} size={220} level="M" />
          </div>
          <p className={`hint center ${left <= 0 ? "expired-txt" : "muted"}`}>
            {left > 0 ? `valid for ${left}s` : "expired — generate a new code"}
          </p>
          <button onClick={() => navigator.clipboard.writeText(url)}>copy link</button>
        </div>
      )}
    </Modal>
  );
}

/* ──────────────────────────── object info modal ──────────────────────────── */

function shortCid(cid: string): string {
  return cid.length > 18 ? `${cid.slice(0, 8)}…${cid.slice(-6)}` : cid;
}

function InfoModal({
  token,
  cid,
  onClose,
  onNavigate,
}: {
  token: string;
  cid: string;
  onClose: () => void;
  onNavigate?: (cid: string) => void;
}) {
  const [info, setInfo] = useState<ObjectInfo | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState<"info" | "graph">("info");
  useEffect(() => {
    let live = true;
    setInfo(null);
    api.objectInfo(token, cid).then((r) => live && setInfo(r)).catch((e) => live && setErr(errorText(e)));
    return () => { live = false; };
  }, [token, cid]);

  const hasGraph = !!(info && (info.links.out.length > 0 || info.links.in.length > 0));

  return (
    <Modal title="Object info" onClose={onClose}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <p className="muted cid" style={{ margin: 0, flex: 1 }}>{cid}</p>
        <Link href={`/o/${encodeURIComponent(cid)}`} className="btn-link">full page ↗</Link>
      </div>
      {err && <p className="error-box">{err}</p>}
      {!info && !err && <p className="muted">loading…</p>}
      {info && (
        <>
          <div className="tabs">
            <button className={`tab ${tab === "info" ? "active" : ""}`} onClick={() => setTab("info")}>info</button>
            <button className={`tab ${tab === "graph" ? "active" : ""}`} onClick={() => setTab("graph")}>
              graph{hasGraph ? ` (${info.links.out.length + info.links.in.length})` : ""}
            </button>
          </div>

          {tab === "info" && (
            <>
              <div className="info-grid">
                <span className="muted">Owner</span><span className="mono">{info.owner ? shortAddress(info.owner) : "—"} {info.is_owner && <span className="pill admin">you</span>}</span>
                <span className="muted">Stored</span><span>{fmtDate(info.stored_at)}</span>
                <span className="muted">Name</span><span>{info.key || "—"}</span>
                <span className="muted">Size</span><span>{fmtBytes(info.size)}</span>
                <span className="muted">Backends</span><span>{info.backends.join(", ") || "—"}</span>
                <span className="muted">Scheme</span><span>{info.scheme}</span>
                <span className="muted">Visibility</span><span>{info.visibility === "private" ? <><LockIcon /> private</> : <><GlobeIcon /> public</>}</span>
                <span className="muted">Pinned</span><span>{info.pinned ? "yes" : "no"}</span>
                <span className="muted">Semantic hash</span><span className="mono" style={{ wordBreak: "break-all" }}>{info.semhash || "—"}</span>
                {info.external_url && (<><span className="muted">External</span><span><a href={info.external_url} target="_blank" rel="noreferrer">{info.external_url}</a></span></>)}
              </div>

              {info.is_owner && (
                <>
                  <h3 className="panel-title" style={{ marginTop: 18 }}>Who has access</h3>
                  {info.grants.length === 0 && info.pools.length === 0 && (
                    <p className="muted">{info.visibility === "public" ? "Public — anyone can read." : "Only you. Share it or add it to a pool to grant access."}</p>
                  )}
                  {info.grants.map((g) => (
                    <div key={g.id} className="row grant-row">
                      <span className="pill">grant</span>
                      <span className="muted">{shortAddress(g.grantee)}</span>
                      <span className="pill">{g.scope}</span>
                      <span className="muted">{g.expired ? "expired" : fmtDuration(g.expires_in)}</span>
                      {g.cid === "*" && <span className="pill">all objects</span>}
                    </div>
                  ))}
                  {info.pools.map((p) => (
                    <div key={p.id} className="row grant-row">
                      <span className="pill">pool</span>
                      <strong>{p.name}</strong>
                      <span className="muted">{p.members.filter((m) => !m.expired).length} members</span>
                    </div>
                  ))}
                </>
              )}
              {!info.is_owner && (
                <p className="muted" style={{ marginTop: 14 }}>{info.you_can_read ? "You have access to this object." : "You do not have access."}</p>
              )}
            </>
          )}

          {tab === "graph" && (
            hasGraph
              ? <CidGraph cid={cid} links={info.links} onNavigate={onNavigate} />
              : <p className="muted" style={{ marginTop: 14 }}>Not mapped from (or referenced by) any other object in the store.</p>
          )}
        </>
      )}
    </Modal>
  );
}

/* ──────────────────────────── CID graph ──────────────────────────── */

function CidGraph({
  cid,
  links,
  onNavigate,
}: {
  cid: string;
  links: { out: { cid: string; key: string | null }[]; in: { cid: string; key: string | null }[] };
  onNavigate?: (cid: string) => void;
}) {
  const rowH = 46;
  const colW = 240;
  const midY = Math.max(links.out.length, links.in.length, 1) * rowH / 2;
  const height = Math.max(links.out.length, links.in.length, 1) * rowH + 20;

  const node = (n: { cid: string; key: string | null }, i: number, col: "out" | "in") => {
    const x = col === "out" ? 20 : colW * 2 - 20;
    const y = 10 + i * rowH + rowH / 2;
    return (
      <g key={n.cid} transform={`translate(${x},${y})`}>
        <line className="graph-edge" x1={col === "out" ? 100 : -100} y1="0" x2={col === "out" ? 0 : 0} y2="0" />
        <foreignObject x={col === "out" ? -10 : -190} y={-16} width="200" height="32">
          <button
            className="graph-node"
            title={n.cid}
            onClick={() => onNavigate?.(n.cid)}
            disabled={!onNavigate}
          >
            {n.key || shortCid(n.cid)}
          </button>
        </foreignObject>
      </g>
    );
  };

  return (
    <div className="graph-wrap">
      <svg width="100%" viewBox={`0 0 ${colW * 2} ${height}`} height={height}>
        {links.out.map((n, i) => node(n, i, "out"))}
        {links.in.map((n, i) => node(n, i, "in"))}
        <line className="graph-edge" x1="100" y1={midY + 10} x2={colW * 2 - 100} y2={midY + 10} strokeDasharray="3,3" />
        <foreignObject x={colW - 90} y={midY - 6} width="180" height="32">
          <div className="graph-node self" title={cid}>{shortCid(cid)}</div>
        </foreignObject>
      </svg>
      <div className="graph-legend">
        <span className="muted">← mapped from (this object's content references these)</span>
        <span className="muted">mapped into (these were built from this) →</span>
      </div>
    </div>
  );
}

/* ─────────────────────── store-wide CID graph ─────────────────────── */

const GW = 640;   // layout canvas — the SVG scales to fit its container
const GH = 430;
const GPAD = 34;        // keeps node circles + their labels inside the frame
const LOOSE_STEP = 16;  // grid pitch for the unlinked band under the graph
const LOOSE_R = 5;      // …drawn as uniform dots: their size says nothing here

type Pt = { x: number; y: number; dx: number; dy: number };

/**
 * Fruchterman-Reingold on the *linked* subgraph, seeded from each CID so the
 * same store always draws the same picture (no jitter between renders). Loose
 * objects — nothing references them, they reference nothing — are parked in a
 * grid underneath instead of being simulated: with a thousand of them the
 * O(n²) pass would stall the tab and the cloud would say nothing anyway.
 */
function layoutGraph(nodes: GraphNode[], edges: GraphEdge[]): { pos: Map<string, Pt>; loose: Set<string>; height: number } {
  const linkedIds = new Set<string>();
  edges.forEach((e) => { linkedIds.add(e.from); linkedIds.add(e.to); });
  const linked = nodes.filter((n) => linkedIds.has(n.cid));
  const loose = nodes.filter((n) => !linkedIds.has(n.cid)).map((n) => n.cid);

  const pos = new Map<string, Pt>();
  linked.forEach((n) => {
    let s = 0;
    for (let i = 0; i < n.cid.length; i++) s = (s * 31 + n.cid.charCodeAt(i)) >>> 0;
    const a = ((s % 997) / 997) * Math.PI * 2;
    const r = 40 + ((s >> 7) % 100) / 100 * (Math.min(GW, GH) / 2 - 60);
    pos.set(n.cid, { x: GW / 2 + Math.cos(a) * r, y: GH / 2 + Math.sin(a) * r, dx: 0, dy: 0 });
  });

  const k = Math.sqrt((GW * GH) / Math.max(linked.length, 1)) * 0.55;
  const iters = 320;
  for (let it = 0; it < iters; it++) {
    const temp = (1 - it / iters) * (GW / 12);
    for (let i = 0; i < linked.length; i++) {
      const a = pos.get(linked[i].cid)!;
      a.dx = 0; a.dy = 0;
      for (let j = 0; j < linked.length; j++) {
        if (i === j) continue;
        const b = pos.get(linked[j].cid)!;
        let ex = a.x - b.x, ey = a.y - b.y;
        let d = Math.hypot(ex, ey);
        if (d < 0.01) { ex = (i - j) * 0.1; ey = 0.1; d = 0.14; }
        const f = (k * k) / d;
        a.dx += (ex / d) * f; a.dy += (ey / d) * f;
      }
      // pull toward the middle so disconnected clusters don't drift off-canvas
      a.dx += (GW / 2 - a.x) * 0.03;
      a.dy += (GH / 2 - a.y) * 0.03;
    }
    for (const e of edges) {
      const a = pos.get(e.from), b = pos.get(e.to);
      if (!a || !b) continue;
      const ex = a.x - b.x, ey = a.y - b.y;
      const d = Math.max(Math.hypot(ex, ey), 0.01);
      const f = (d * d) / k;
      const fx = (ex / d) * f, fy = (ey / d) * f;
      a.dx -= fx; a.dy -= fy;
      b.dx += fx; b.dy += fy;
    }
    for (const n of linked) {
      const p = pos.get(n.cid)!;
      const d = Math.max(Math.hypot(p.dx, p.dy), 0.01);
      const step = Math.min(d, temp);
      p.x = Math.max(GPAD, Math.min(GW - GPAD, p.x + (p.dx / d) * step));
      p.y = Math.max(GPAD, Math.min(GH - GPAD, p.y + (p.dy / d) * step));
    }
  }

  // Zoom the settled cloud to fill the frame — a two-node graph shouldn't be
  // two dots in the middle of an empty canvas. Aspect ratio is preserved so
  // the springs still look like equal lengths.
  if (linked.length > 1) {
    const xs = linked.map((n) => pos.get(n.cid)!.x);
    const ys = linked.map((n) => pos.get(n.cid)!.y);
    const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
    const [y0, y1] = [Math.min(...ys), Math.max(...ys)];
    const s = Math.min((GW - 2 * GPAD) / Math.max(x1 - x0, 1),
                       (GH - 2 * GPAD) / Math.max(y1 - y0, 1), 3);
    const ox = (GW - (x1 - x0) * s) / 2, oy = (GH - (y1 - y0) * s) / 2;
    for (const n of linked) {
      const p = pos.get(n.cid)!;
      p.x = ox + (p.x - x0) * s;
      p.y = oy + (p.y - y0) * s;
    }
  }

  const perRow = Math.floor((GW - 40) / LOOSE_STEP);
  const rows = Math.ceil(loose.length / perRow);
  loose.forEach((cid, i) => {
    pos.set(cid, {
      x: 30 + (i % perRow) * LOOSE_STEP,
      y: GH + 46 + Math.floor(i / perRow) * LOOSE_STEP,
      dx: 0, dy: 0,
    });
  });
  return { pos, loose: new Set(loose), height: loose.length ? GH + 60 + rows * LOOSE_STEP : GH };
}

/** Uploads without an explicit key land as `<epoch-ms>-<filename>`; the graph
 *  shows the human half, short enough not to smear across its neighbours. */
function nodeLabel(n: GraphNode): string {
  const base = (n.key || "").replace(/^\d{10,}-/, "");
  if (!base) return shortCid(n.cid);
  return base.length > 26 ? `${base.slice(0, 24)}…` : base;
}

function nodeRadius(n: GraphNode): number {
  if (n.external) return 6;
  const kb = (n.size || 0) / 1024;
  return 7 + Math.min(11, Math.log10(kb + 1) * 5);
}

function GraphPanel({
  token, onInfo, setError, setSuccess,
}: {
  token: string;
  onInfo: (cid: string) => void;
  setError: (s: string | null) => void;
  setSuccess: (s: string | null) => void;
}) {
  const [data, setData] = useState<CidGraphData | null>(null);
  const [scope, setScope] = useState<"mine" | "all">("mine");
  const [showLoose, setShowLoose] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [hover, setHover] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await api.graph(token, { scope, isolated: showLoose }));
    } catch (e) {
      setError(errorText(e));
    }
  }, [token, scope, showLoose, setError]);

  useEffect(() => { load(); }, [load]);

  const rescan = async () => {
    setScanning(true);
    try {
      const r = await api.graphScan(token);
      setError(null);
      await load();
      setSuccess(
        r.edges
          ? `scanned ${r.scanned} objects — ${r.edges} link${r.edges === 1 ? "" : "s"} across ${r.with_refs} of them`
          : `scanned ${r.scanned} objects — none of them mention another CID`
      );
    } catch (e) {
      setError(errorText(e));
    } finally {
      setScanning(false);
    }
  };

  const nodes = useMemo(() => data?.nodes ?? [], [data]);
  const edges = useMemo(() => data?.edges ?? [], [data]);
  const byCid = useMemo(() => new Map(nodes.map((n) => [n.cid, n])), [nodes]);
  const { pos, loose, height } = useMemo(() => layoutGraph(nodes, edges), [nodes, edges]);
  const degree = useMemo(() => {
    const d = new Map<string, { in: number; out: number }>();
    const bump = (cid: string, side: "in" | "out") => {
      const e = d.get(cid) || { in: 0, out: 0 };
      e[side]++; d.set(cid, e);
    };
    edges.forEach((e) => { bump(e.from, "out"); bump(e.to, "in"); });
    return d;
  }, [edges]);

  const focus = hover ? byCid.get(hover) : null;
  const externals = nodes.filter((n) => n.external).length;

  return (
    <div className="panel">
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 10 }}>
        <h2 className="panel-title" style={{ margin: 0 }}>CID graph</h2>
        <div className="row">
          <div className="sort-seg">
            <button className={`seg ${scope === "mine" ? "active" : ""}`} onClick={() => setScope("mine")}>mine</button>
            <button className={`seg ${scope === "all" ? "active" : ""}`} onClick={() => setScope("all")} title="Include objects shared with you">+ shared</button>
            <button className={`seg ${showLoose ? "active" : ""}`} onClick={() => setShowLoose((v) => !v)} title="Also show objects with no links">
              unlinked
            </button>
          </div>
          <button onClick={rescan} disabled={scanning} title="Re-read your objects and re-derive their links">
            {scanning ? "scanning…" : "↻ rescan"}
          </button>
        </div>
      </div>
      <p className="muted hint" style={{ marginTop: 0 }}>
        An edge means one object’s content contains another CID — a manifest pointing at what it was built from.
        Dashed nodes are CIDs referenced but not stored here.
      </p>

      {!data && <p className="muted">loading…</p>}
      {data && (
        <>
          <div className="row cg-stats">
            <span className="pill">{data.total_objects} objects</span>
            <span className="pill">{edges.length} links</span>
            <span className="pill">{data.linked} linked</span>
            {externals > 0 && <span className="pill">{externals} external</span>}
          </div>

          {edges.length === 0 && (
            <p className="muted" style={{ marginTop: 14 }}>
              Nothing references anything yet. Links are detected when you upload — hit <strong>rescan</strong> to
              re-read objects stored before that, or ones whose targets arrived later.
            </p>
          )}

          {(edges.length > 0 || showLoose) && (
            <div className="cg-wrap">
              <svg className="cg-svg" viewBox={`0 0 ${GW} ${height}`} width="100%">
                <defs>
                  <marker id="cg-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                    <path d="M0,0 L8,4 L0,8 z" className="cg-arrow-head" />
                  </marker>
                </defs>
                {edges.map((e) => {
                  const a = pos.get(e.from), b = pos.get(e.to);
                  if (!a || !b) return null;
                  const lit = hover === e.from || hover === e.to;
                  const bn = byCid.get(e.to);
                  const r = bn ? nodeRadius(bn) + 5 : 10;
                  const d = Math.max(Math.hypot(b.x - a.x, b.y - a.y), 0.01);
                  return (
                    <line
                      key={`${e.from}-${e.to}`}
                      className={`cg-edge ${lit ? "lit" : ""}`}
                      x1={a.x} y1={a.y}
                      x2={b.x - ((b.x - a.x) / d) * r} y2={b.y - ((b.y - a.y) / d) * r}
                      markerEnd="url(#cg-arrow)"
                    />
                  );
                })}
                {nodes.map((n) => {
                  const p = pos.get(n.cid);
                  if (!p) return null;
                  const deg = degree.get(n.cid);
                  const r = loose.has(n.cid) ? LOOSE_R : nodeRadius(n);
                  return (
                    <g
                      key={n.cid}
                      className={`cg-node ${n.external ? "external" : n.visibility === "private" ? "private" : "public"} ${hover === n.cid ? "lit" : ""}`}
                      transform={`translate(${p.x},${p.y})`}
                      onMouseEnter={() => setHover(n.cid)}
                      onMouseLeave={() => setHover((h) => (h === n.cid ? null : h))}
                      onClick={() => onInfo(n.cid)}
                    >
                      <circle r={r} />
                      <title>{`${n.key || n.cid}\n${n.external ? "not stored here" : `${fmtBytes(n.size)} · uploaded ${fmtDate(n.timestamp)}`}\n${deg ? `${deg.out} out · ${deg.in} in` : ""}`}</title>
                      {(hover === n.cid || (deg && deg.out + deg.in > 1)) && (
                        <text className="cg-label" y={-r - 6}>{nodeLabel(n)}</text>
                      )}
                    </g>
                  );
                })}
                {showLoose && loose.size > 0 && (
                  <text className="cg-band" x={GW / 2} y={GH + 26}>
                    {loose.size} unlinked object{loose.size === 1 ? "" : "s"}
                  </text>
                )}
              </svg>

              <div className={`cg-card ${focus ? "" : "empty"}`}>
                {focus ? (
                  <>
                    <div className="row">
                      {focus.external
                        ? <span className="pill">external</span>
                        : <span className={`pill ${focus.visibility === "private" ? "private" : "public"}`}>{focus.visibility === "private" ? "🔒 private" : "🌐 public"}</span>}
                      {focus.backend && <span className={`pill ${focus.backend}`}>{focus.backend}</span>}
                    </div>
                    <strong className="cg-card-name" title={focus.key || focus.cid}>{nodeLabel(focus)}</strong>
                    <span className="cid">{focus.cid}</span>
                    <div className="info-grid" style={{ marginTop: 8 }}>
                      <span className="muted">Uploaded</span>
                      <span>{focus.external ? "— (not stored here)" : `${fmtDate(focus.timestamp)} · ${fmtAgo(focus.timestamp)}`}</span>
                      <span className="muted">Size</span><span>{focus.external ? "—" : fmtBytes(focus.size)}</span>
                      <span className="muted">Links</span>
                      <span>{(degree.get(focus.cid)?.out ?? 0)} out · {(degree.get(focus.cid)?.in ?? 0)} in</span>
                    </div>
                    <button className="ghost" style={{ marginTop: 10 }} onClick={() => onInfo(focus.cid)}>open info</button>
                  </>
                ) : (
                  <p className="muted">Hover a node for its details — click to open it.</p>
                )}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

/* ──────────────────────────── share modal ──────────────────────────── */

function ShareModal({
  token, object, onClose, setError, setSuccess,
}: {
  token: string;
  object: StoredObject;
  onClose: () => void;
  setError: (s: string) => void;
  setSuccess: (s: string) => void;
}) {
  const [grantee, setGrantee] = useState("");
  const [scope, setScope] = useState<"read" | "write">("read");
  const [ttlIdx, setTtlIdx] = useState(2);
  const [grants, setGrants] = useState<Grant[]>([]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const g = await api.grants(token);
      setGrants(g.granted.filter((x) => x.cid === object.cid || x.cid === "*"));
    } catch {
      /* ignore */
    }
  }, [token, object.cid]);
  useEffect(() => { load(); }, [load]);

  const create = async () => {
    const addr = grantee.trim().toLowerCase();
    if (!addr.startsWith("0x") || addr.length !== 42) {
      setError("enter a valid 0x address");
      return;
    }
    setBusy(true);
    try {
      await api.createGrant(token, { grantee: addr, cid: object.cid, scope, ttl_seconds: DURATIONS[ttlIdx].ttl ?? undefined });
      setSuccess(`shared with ${shortAddress(addr)} for ${DURATIONS[ttlIdx].label}`);
      setGrantee("");
      await load();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (id: string) => {
    setBusy(true);
    try {
      await api.revokeGrant(token, id);
      await load();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title="Share object" onClose={onClose}>
      <p className="muted cid" style={{ marginTop: 0 }}>{object.cid}</p>
      <h3 className="panel-title">Grant timed access to an address</h3>
      <div className="col">
        <input type="text" placeholder="grantee 0x address" value={grantee} onChange={(e) => setGrantee(e.target.value)} />
        <div className="row">
          <select value={scope} onChange={(e) => setScope(e.target.value as never)}>
            <option value="read">can read</option>
            <option value="write">can read + write</option>
          </select>
          <select value={ttlIdx} onChange={(e) => setTtlIdx(Number(e.target.value))}>
            {DURATIONS.map((d, i) => (<option key={i} value={i}>{d.label}</option>))}
          </select>
          <button className="primary" onClick={create} disabled={busy}>Grant access</button>
        </div>
      </div>
      <div style={{ marginTop: 16 }}>
        <h3 className="panel-title">Active grants</h3>
        {grants.length === 0 && <p className="muted">None yet.</p>}
        {grants.map((g) => (
          <div key={g.id} className="row grant-row">
            <span className="muted">{shortAddress(g.grantee)}</span>
            <span className="pill">{g.scope}</span>
            <span className="muted">{fmtDuration(g.expires_in)}</span>
            {g.cid === "*" && <span className="pill">all objects</span>}
            <button onClick={() => revoke(g.id)} disabled={busy}>revoke</button>
          </div>
        ))}
      </div>
    </Modal>
  );
}

/* ──────────────────────────── local key modal ─────────────────────────── */

function LocalKeyModal({
  address, copied, onCopy, onClose, onImported, onForget, setError,
}: {
  address: string;
  copied: string | null;
  onCopy: (s: string, tag: string) => void;
  onClose: () => void;
  onImported: () => void;
  onForget: () => void;
  setError: (s: string) => void;
}) {
  const [revealed, setRevealed] = useState(false);
  const [importPk, setImportPk] = useState("");
  const [confirmForget, setConfirmForget] = useState(false);
  const pk = revealed ? exportLocalKey() : null;

  const doImport = () => {
    try {
      importLocalKey(importPk);
      onImported();
    } catch {
      setError("that doesn't look like a private key — expect 64 hex characters");
    }
  };

  return (
    <Modal title="Local key" onClose={onClose}>
      <p className="muted" style={{ marginTop: 0 }}>
        This account lives in <strong>this browser</strong> — no extension, no server copy. It signs every request the
        same way a wallet would, but nothing else protects it: anyone using this browser profile can act as{" "}
        <code>{shortAddress(address)}</code>, and clearing site data erases it for good.
      </p>

      <h3 className="panel-title">Back it up</h3>
      <div className="col">
        {!revealed ? (
          <button onClick={() => setRevealed(true)}>Reveal private key</button>
        ) : (
          <>
            <input type="text" readOnly value={pk ?? ""} onFocus={(e) => e.currentTarget.select()} />
            <div className="row">
              <button className="primary" onClick={() => pk && onCopy(pk, "localpk")}>
                {copied === "localpk" ? "✓ copied" : "Copy key"}
              </button>
              <button onClick={() => setRevealed(false)}>Hide</button>
            </div>
            <p className="muted hint">
              Store it somewhere only you can read. Whoever holds this key owns everything stored under the address.
            </p>
          </>
        )}
      </div>

      <div style={{ marginTop: 16 }}>
        <h3 className="panel-title">Use a different key</h3>
        <div className="col">
          <input
            type="password"
            placeholder="paste a private key to restore"
            value={importPk}
            onChange={(e) => setImportPk(e.target.value)}
          />
          <div className="row">
            <button className="primary" onClick={doImport} disabled={!importPk.trim()}>Import &amp; sign in</button>
          </div>
          <p className="muted hint">Replaces the key in this browser — back up the current one first.</p>
        </div>
      </div>

      <div style={{ marginTop: 16 }}>
        <h3 className="panel-title">Forget this key</h3>
        {!confirmForget ? (
          <button onClick={() => setConfirmForget(true)}>Erase from this browser</button>
        ) : (
          <div className="row">
            <span className="muted">Without a backup this is permanent.</span>
            <button className="primary" onClick={onForget}>Erase it</button>
            <button onClick={() => setConfirmForget(false)}>Cancel</button>
          </div>
        )}
      </div>
    </Modal>
  );
}

/* ──────────────────────── link-phone (handoff) modal ─────────────────── */

function LinkPhoneModal({ data, onClose }: { data: { code: string; expires: number }; onClose: () => void }) {
  const [left, setLeft] = useState(Math.max(0, data.expires - Math.floor(Date.now() / 1000)));
  useEffect(() => {
    const t = setInterval(() => setLeft(Math.max(0, data.expires - Math.floor(Date.now() / 1000))), 1000);
    return () => clearInterval(t);
  }, [data.expires]);
  const claimUrl = useMemo(() => `${window.location.origin}${BASE_PATH}/?claim=${data.code}`, [data.code]);
  return (
    <Modal title="Link a phone" onClose={onClose}>
      <p className="muted" style={{ marginTop: 0 }}>
        Scan with your phone to sign in there — no MetaMask needed. Single-use, expires in <strong>{left}s</strong>.
      </p>
      <div className="qr big center">
        <QRCodeSVG value={claimUrl} size={220} level="M" />
      </div>
      <p className="muted hint center">{left > 0 ? "waiting for scan…" : "expired — close and try again"}</p>
    </Modal>
  );
}

/* ──────────────────────────── pools view ──────────────────────────── */

function PoolsView({
  token, pools, onChanged, onOpen, setError,
}: {
  token: string;
  pools: PoolSummary[];
  onChanged: () => void;
  onOpen: (id: string) => void;
  setError: (s: string) => void;
}) {
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [busy, setBusy] = useState(false);

  const create = async () => {
    if (!name.trim()) return;
    setBusy(true);
    try {
      await api.createPool(token, { name: name.trim(), description: desc.trim() || undefined });
      setName("");
      setDesc("");
      onChanged();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel">
      <h2 className="panel-title">Data pools (buckets)</h2>
      <p className="muted hint">
        A pool is a permissioned bucket: every member gets mutual read access to objects pooled into it. Add
        members with roles (owner/editor/viewer) and optional time limits.
      </p>
      <div className="row">
        <input type="text" placeholder="pool name" value={name} onChange={(e) => setName(e.target.value)} />
        <input type="text" placeholder="description (optional)" value={desc} onChange={(e) => setDesc(e.target.value)} style={{ flex: 1 }} />
        <button className="primary" onClick={create} disabled={busy || !name.trim()}>Create pool</button>
      </div>
      {pools.length === 0 && <p className="muted" style={{ marginTop: 16 }}>No pools yet.</p>}
      <div className="pool-grid">
        {pools.map((p) => (
          <button key={p.id} className="pool-card" onClick={() => onOpen(p.id)}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <strong>{p.name}</strong>
              <span className={`pill ${p.role === "owner" ? "admin" : ""}`}>{p.role}</span>
            </div>
            {p.description && <p className="muted" style={{ margin: "6px 0" }}>{p.description}</p>}
            <div className="row muted" style={{ fontSize: 12 }}>
              <span>{p.member_count} members</span><span>·</span><span>{p.object_count} objects</span>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

/* ──────────────────────────── pool modal ──────────────────────────── */

function PoolModal({
  token, pool, me, onClose, onChanged, onDeleted, setError,
}: {
  token: string;
  pool: PoolDetail;
  me: string | null;
  onClose: () => void;
  onChanged: () => void;
  onDeleted: () => void;
  setError: (s: string) => void;
}) {
  const [addr, setAddr] = useState("");
  const [role, setRole] = useState<"viewer" | "editor">("viewer");
  const [ttlIdx, setTtlIdx] = useState(5);
  const [cid, setCid] = useState("");
  const [busy, setBusy] = useState(false);
  const canManage = pool.role === "owner" || pool.role === "editor";
  const isOwner = !!me && pool.owner === me.toLowerCase();

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
      onChanged();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={`Pool — ${pool.name}`} onClose={onClose}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <p className="muted" style={{ marginTop: 0 }}>{pool.description || "No description."} · you are <strong>{pool.role}</strong></p>
        {isOwner && (
          <button onClick={() => { if (confirm(`Delete pool “${pool.name}”? This removes all members + pooled objects.`)) run(async () => { await api.deletePool(token, pool.id); onDeleted(); }); }} disabled={busy}>
            delete pool
          </button>
        )}
      </div>
      <div className="modal-grid">
        <div>
          <h3 className="panel-title">Members ({pool.members.length})</h3>
          {pool.members.map((m) => (
            <div key={m.address} className="row grant-row">
              <span className="muted">{shortAddress(m.address)}</span>
              <span className={`pill ${m.role === "owner" ? "admin" : ""}`}>{m.role}</span>
              <span className="muted">{m.expired ? "expired" : fmtDuration(m.expires_in)}</span>
              {canManage && m.role !== "owner" && (
                <button onClick={() => run(() => api.removeMember(token, pool.id, m.address))} disabled={busy}>remove</button>
              )}
            </div>
          ))}
          {canManage && (
            <div className="col" style={{ marginTop: 12 }}>
              <input type="text" placeholder="member 0x address" value={addr} onChange={(e) => setAddr(e.target.value)} />
              <div className="row">
                {isOwner && (
                  <select value={role} onChange={(e) => setRole(e.target.value as never)}>
                    <option value="viewer">viewer</option>
                    <option value="editor">editor</option>
                  </select>
                )}
                <select value={ttlIdx} onChange={(e) => setTtlIdx(Number(e.target.value))}>
                  {DURATIONS.map((d, i) => (<option key={i} value={i}>{d.label}</option>))}
                </select>
                <button className="primary" disabled={busy || !addr.trim()} onClick={() => run(async () => { await api.addMember(token, pool.id, { address: addr.trim(), role, ttl_seconds: DURATIONS[ttlIdx].ttl ?? undefined }); setAddr(""); })}>add</button>
              </div>
            </div>
          )}
        </div>
        <div>
          <h3 className="panel-title">Objects ({pool.objects.length})</h3>
          {pool.objects.map((o) => (
            <div key={o.cid} className="row grant-row">
              <span className="cid" style={{ fontSize: 11 }}>{o.cid.slice(0, 20)}…</span>
              {o.backend && <span className="pill">{o.backend}</span>}
              <a href={api.getUrl(o.cid, o.backend || undefined, token)} target="_blank" rel="noreferrer">open</a>
              {canManage && (<button onClick={() => run(() => api.removePoolObject(token, pool.id, o.cid))} disabled={busy}>remove</button>)}
            </div>
          ))}
          {canManage && (
            <div className="col" style={{ marginTop: 12 }}>
              <input type="text" placeholder="CID to pool (must be readable by you)" value={cid} onChange={(e) => setCid(e.target.value)} />
              <button className="primary" disabled={busy || !cid.trim()} onClick={() => run(async () => { await api.addPoolObject(token, pool.id, { cid: cid.trim() }); setCid(""); })}>add object</button>
            </div>
          )}
          {me && !isOwner && (
            <button style={{ marginTop: 16 }} onClick={() => run(async () => { await api.removeMember(token, pool.id, me); onClose(); })} disabled={busy}>Leave pool</button>
          )}
        </div>
      </div>
    </Modal>
  );
}

/* ─────────────────────── register external CID ─────────────────────── */

function RegisterExternal({
  token, onDone, setError, setSuccess,
}: {
  token: string;
  onDone: () => void;
  setError: (s: string) => void;
  setSuccess: (s: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [cid, setCid] = useState("");
  const [url, setUrl] = useState("");
  const [scheme, setScheme] = useState("");
  const [pub, setPub] = useState(false);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!cid.trim()) return;
    setBusy(true);
    try {
      await api.registerExternal(token, { cid: cid.trim(), url: url.trim() || undefined, scheme: scheme.trim() || undefined, public: pub });
      setSuccess(`registered ${cid.slice(0, 16)}… — now shareable & poolable`);
      setCid("");
      setUrl("");
      setScheme("");
      onDone();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel">
      <button className="link-toggle" onClick={() => setOpen((o) => !o)}>
        {open ? "▾" : "▸"} Register an external CID (arweave, ipfs elsewhere, s3, any system)
      </button>
      {open && (
        <div className="col" style={{ marginTop: 12 }}>
          <p className="muted hint" style={{ margin: 0 }}>
            The store is CID-agnostic: reference data living in another system so it becomes a first-class object
            you can share and pool. Provide a gateway URL to make it retrievable.
          </p>
          <input type="text" placeholder="cid / id (e.g. ar://… , bafy… , s3://…)" value={cid} onChange={(e) => setCid(e.target.value)} />
          <input type="text" placeholder="gateway url (optional, e.g. https://arweave.net/<tx>)" value={url} onChange={(e) => setUrl(e.target.value)} />
          <div className="row">
            <input type="text" placeholder="scheme (auto-detected if blank)" value={scheme} onChange={(e) => setScheme(e.target.value)} />
            <label className="check"><input type="checkbox" checked={pub} onChange={(e) => setPub(e.target.checked)} /> public</label>
            <button className="primary" onClick={submit} disabled={busy || !cid.trim()}>Register</button>
          </div>
        </div>
      )}
    </div>
  );
}

/* ──────────────────────────── marketplace ──────────────────────────── */

function fmtAgo(secs: number): string {
  const d = Math.floor(Date.now() / 1000) - secs;
  if (d < 60) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  if (d < 86400 * 30) return `${Math.floor(d / 86400)}d ago`;
  return `${Math.floor(d / (86400 * 30))}mo ago`;
}

const SORTS: { key: "hot" | "new" | "top"; label: string; icon: JSX.Element }[] = [
  {
    key: "hot",
    label: "hot",
    icon: <Ico><path d="M12 3.2c2.6 3 3.6 5 3 6.3 1.2-.4 2-1.4 2.3-2.9 1.6 1.9 2.5 4 2.5 5.9a7.8 7.8 0 1 1-15.6 0c0-3 2.2-6.3 7.8-9.3Z" /></Ico>,
  },
  {
    key: "new",
    label: "new",
    icon: <Ico><path d="M12 3.5 13.7 9l5.8.3-4.5 3.6 1.5 5.6L12 15.4 7.5 18.5 9 12.9 4.5 9.3 10.3 9 12 3.5Z" /></Ico>,
  },
  {
    key: "top",
    label: "top",
    icon: <Ico><path d="M3.5 7.5 7 11l5-6.5 5 6.5 3.5-3.5-1.6 10H5.1L3.5 7.5Z" /><path d="M5 20.5h14" /></Ico>,
  },
];

function MarketView({
  token, admin, canSell, bump, onSell, onInfo, onAcquired, setError, setSuccess,
}: {
  token: string | null;
  admin: boolean;
  canSell: boolean;
  bump: number;
  onSell: () => void;
  onInfo: (cid: string) => void;
  onAcquired: () => void;
  setError: (s: string) => void;
  setSuccess: (s: string) => void;
}) {
  const [data, setData] = useState<MarketBrowse | null>(null);
  // a scanned drop QR (?drop=<cid>) lands straight on that listing
  const [q, setQ] = useState(() =>
    typeof window === "undefined" ? "" : new URL(window.location.href).searchParams.get("drop") ?? ""
  );
  const [tag, setTag] = useState("");
  const [seller, setSeller] = useState("");
  const [sort, setSort] = useState<"hot" | "new" | "top">("hot");
  const [freeOnly, setFreeOnly] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(
        await api.market(
          { q: q.trim() || undefined, tag: tag || undefined, seller: seller || undefined, sort, free: freeOnly || undefined },
          token
        )
      );
    } catch (e) {
      setError(`market: ${errorText(e)}`);
    }
  }, [q, tag, seller, sort, freeOnly, token, setError]);

  // Debounced refetch — search-as-you-type without hammering the API.
  useEffect(() => {
    const t = setTimeout(load, 250);
    return () => clearTimeout(t);
  }, [load, bump]);

  useEffect(() => {
    const url = new URL(window.location.href);
    if (url.searchParams.has("drop")) {
      url.searchParams.delete("drop");
      window.history.replaceState({}, "", url.toString());
    }
  }, []);

  const acquire = async (l: MarketListing) => {
    if (!token) return;
    setBusy(l.cid);
    try {
      await api.marketAcquire(token, l.cid);
      setSuccess(l.price_bloc > 0 ? `unlocked “${l.title}” — your BlocTime is the ticket` : `“${l.title}” is yours`);
      onAcquired();
      await load();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const like = async (l: MarketListing) => {
    if (!token) return;
    setBusy(`like-${l.cid}`);
    try {
      const r = await api.marketLike(token, l.cid);
      setData((d) =>
        d ? { ...d, listings: d.listings.map((x) => (x.cid === l.cid ? { ...x, liked: r.liked, likes: r.likes } : x)) } : d
      );
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const delist = async (l: MarketListing) => {
    if (!token) return;
    let reason: string | undefined;
    if (!l.owned && admin) {
      const r = prompt("Takedown reason (recorded in the moderation audit log):", "policy violation");
      if (r === null) return;
      reason = r || undefined;
    } else if (!confirm(`Delist “${l.title}”? The object stays stored — it just leaves the market.`)) {
      return;
    }
    setBusy(l.cid);
    try {
      await api.marketDelist(token, l.cid, reason);
      setSuccess(`delisted “${l.title}”`);
      await load();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const tags = data ? Object.entries(data.tags) : [];
  // free listings in the current result set — the hero's third number
  const freeCount = data ? data.listings.filter((l) => l.price_bloc <= 0).length : null;

  return (
    <>
      <div className="market-hero">
        <div className="market-hero-glow" />
        <div className="market-hero-inner">
          <h1 className="market-title">THE MARKET</h1>
          <p className="market-tag">
            content-addressed drops <i>·</i> own it by CID <i>·</i> free or unlocked by <strong>BlocTime</strong> you hold on-chain
          </p>
          {!token && <p className="muted hint">sign in to cop, like &amp; sell</p>}
        </div>
        {/* the numbers and the CTA claim the right half, so the band is never
            a title floating in empty space on a wide screen */}
        <div className="market-hero-side">
          <div className="market-hero-stats">
            <span className="mk-stat"><strong>{data?.count ?? "…"}</strong><i>live drops</i></span>
            <span className="mk-stat"><strong>{freeCount ?? "…"}</strong><i>free</i></span>
            <span className="mk-stat"><strong>{tags.length}</strong><i>tags</i></span>
          </div>
          {canSell && (
            <button className="primary sell-cta" onClick={onSell}>
              <span className="btn-ico"><TagIcon /></span> Drop something
            </button>
          )}
        </div>
      </div>

      <div className="panel market-panel">
        <div className="market-controls">
          <input
            type="text"
            className="market-search"
            placeholder="search drops — title, tag, cid…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <div className="sort-seg">
            {SORTS.map((s) => (
              <button key={s.key} className={`seg ${sort === s.key ? "active" : ""}`} onClick={() => setSort(s.key)}>
                {s.icon} {s.label}
              </button>
            ))}
            <button className={`seg ${freeOnly ? "active" : ""}`} onClick={() => setFreeOnly((f) => !f)} title="Free drops only">
              <FreeIcon /> free
            </button>
          </div>
        </div>

        {(tags.length > 0 || seller) && (
          <div className="tag-row">
            {seller && (
              <button className="tag-chip active" onClick={() => setSeller("")}>
                seller {shortAddress(seller)} ✕
              </button>
            )}
            {tags.slice(0, 14).map(([t, n]) => (
              <button key={t} className={`tag-chip ${tag === t ? "active" : ""}`} onClick={() => setTag(tag === t ? "" : t)}>
                #{t} <span className="tag-n">{n}</span>
              </button>
            ))}
          </div>
        )}

        {data && data.listings.length === 0 && (
          <div className="market-empty">
            <p className="muted">
              {q || tag || seller || freeOnly ? "Nothing matches — loosen the filters." : "No drops yet. Be the first — list an object and set the tone."}
            </p>
            {canSell && !q && !tag && (
              <button className="primary" onClick={onSell}>
                <span className="btn-ico"><TagIcon /></span> List the first drop
              </button>
            )}
          </div>
        )}

        <div className="market-grid">
          {data?.listings.map((l) => (
            <MarketCard
              key={l.cid}
              l={l}
              token={token}
              admin={admin}
              busy={busy}
              onAcquire={() => acquire(l)}
              onLike={() => like(l)}
              onDelist={() => delist(l)}
              onInfo={() => onInfo(l.cid)}
              onSeller={() => setSeller(seller === l.seller ? "" : l.seller)}
            />
          ))}
        </div>
      </div>
    </>
  );
}

function MarketCard({
  l, token, admin, busy, onAcquire, onLike, onDelist, onInfo, onSeller,
}: {
  l: MarketListing;
  token: string | null;
  admin: boolean;
  busy: string | null;
  onAcquire: () => void;
  onLike: () => void;
  onDelist: () => void;
  onInfo: () => void;
  onSeller: () => void;
}) {
  const free = l.price_bloc <= 0;
  const mine = !!l.owned;
  const unlocked = !!l.can_read;
  const working = busy === l.cid;
  const [qrOpen, setQrOpen] = useState(false);
  const [linkCopied, setLinkCopied] = useState(false);
  const dropUrl =
    typeof window === "undefined" ? "" : `${window.location.origin}${window.location.pathname}?drop=${l.cid}`;

  return (
    <div className={`market-card ${mine ? "mine" : ""}`}>
      <div className="mk-ribbon" style={identiconStyle(l.seller)} />
      <div className="mk-body">
        <div className="mk-top">
          <span className={`price-badge ${free ? "free" : "bloc"}`}>
            {free ? "FREE" : <><ClockIcon /> {l.price_bloc} BLOC</>}
          </span>
          {l.visibility === "private" && !unlocked && <span className="pill private"><LockIcon /> locked</span>}
          {l.visibility === "private" && unlocked && !mine && <span className="pill public"><UnlockIcon /> unlocked</span>}
          {mine && <span className="pill admin">your drop</span>}
          <button
            className={`like-btn ${l.liked ? "liked" : ""}`}
            onClick={onLike}
            disabled={!token || busy === `like-${l.cid}`}
            title={token ? (l.liked ? "unlike" : "like") : "sign in to like"}
          >
            {l.liked ? "♥" : "♡"} {l.likes}
          </button>
          <button className="mk-qr" onClick={() => setQrOpen(true)} title="Scan to open this drop on your phone">
            <QRCodeSVG value={dropUrl} size={30} level="L" />
          </button>
        </div>
        <h3 className="mk-title" title={l.title}>
          <Link href={`/o/${encodeURIComponent(l.cid)}`} className="mk-title-link">{l.title}</Link>
        </h3>
        {l.description && <p className="mk-desc">{l.description}</p>}
        {l.tags.length > 0 && (
          <div className="mk-tags">
            {l.tags.map((t) => <span key={t} className="mk-tag">#{t}</span>)}
          </div>
        )}
        <div className="mk-meta">
          <button className="mk-seller" onClick={onSeller} title={`${l.seller} — click to see their storefront`}>
            <span className="mk-avatar" style={identiconStyle(l.seller)} />
            {shortAddress(l.seller)}
          </button>
          <span className="muted">{l.size != null ? fmtBytes(l.size) : ""}</span>
          <span className="muted">⇣ {l.downloads}</span>
          <span className="muted">{fmtAgo(l.created)}</span>
        </div>
        <div className="mk-actions">
          {!token && free && l.visibility !== "private" ? (
            <a className="btn-link" href={api.getUrl(l.cid)} target="_blank" rel="noreferrer">open ↗</a>
          ) : !token ? (
            <span className="muted hint">sign in to unlock</span>
          ) : unlocked ? (
            <>
              <a className="btn-link" href={api.getUrl(l.cid, undefined, l.visibility === "private" ? token : null)} target="_blank" rel="noreferrer">
                ⇣ download
              </a>
              <Link href={`/o/${encodeURIComponent(l.cid)}`} className="btn-link">view</Link>
            </>
          ) : (
            <button className={`primary ${free ? "" : "unlock"}`} onClick={onAcquire} disabled={working}>
              {working ? "…" : free ? <><BoltIcon /> Get it</> : <><UnlockIcon s={13} /> Unlock · {l.price_bloc} BLOC</>}
            </button>
          )}
          {token && <button onClick={onInfo} disabled={working}>info</button>}
          {token && (mine || admin) && (
            <button className="danger" onClick={onDelist} disabled={working}>
              {mine ? "delist" : <><GavelIcon /> take down</>}
            </button>
          )}
        </div>
      </div>
      {qrOpen && (
        <Modal title={`Scan — ${l.title}`} onClose={() => setQrOpen(false)}>
          <div className="qr-share">
            <div className="qr big center">
              <QRCodeSVG value={dropUrl} size={220} level="M" />
            </div>
            <p className="muted">
              Scanning lands right on this drop — {free ? "free to grab" : `unlocks with ${l.price_bloc} BLOC held on-chain`}.
            </p>
            <div className="row" style={{ justifyContent: "center" }}>
              <button
                onClick={async () => {
                  await navigator.clipboard.writeText(dropUrl);
                  setLinkCopied(true);
                }}
              >
                {linkCopied ? "✓ copied" : "copy link"}
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

/* ──────────────────────────── sell modal ──────────────────────────── */

function SellModal({
  token, objects, preselect, onClose, onListed, setError,
}: {
  token: string;
  objects: StoredObject[];
  preselect: StoredObject | null;
  onClose: () => void;
  onListed: (title: string) => void;
  setError: (s: string) => void;
}) {
  const [cid, setCid] = useState(preselect?.cid ?? objects[0]?.cid ?? "");
  const [title, setTitle] = useState(preselect?.key?.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ") ?? "");
  const [desc, setDesc] = useState("");
  const [tags, setTags] = useState("");
  const [price, setPrice] = useState("0");
  const [busy, setBusy] = useState(false);

  const chosen = objects.find((o) => o.cid === cid);
  const priceN = Number(price) || 0;

  const submit = async () => {
    if (!cid || !title.trim()) return;
    setBusy(true);
    try {
      await api.marketList(token, {
        cid,
        title: title.trim(),
        description: desc.trim() || undefined,
        tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
        price_bloc: priceN,
      });
      onListed(title.trim());
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title="Drop it on the market" onClose={onClose}>
      <div className="col">
        <select value={cid} onChange={(e) => setCid(e.target.value)} disabled={busy || !!preselect}>
          {objects.length === 0 && <option value="">— no objects yet: add data first —</option>}
          {objects.map((o) => (
            <option key={`${o.cid}-${o.backend}`} value={o.cid}>
              {o.key || shortCid(o.cid)} · {o.visibility} · {fmtBytes(o.size)}
            </option>
          ))}
        </select>
        <input type="text" placeholder="title — make it slap" value={title} onChange={(e) => setTitle(e.target.value)} disabled={busy} />
        <textarea placeholder="description (what is it, why it's dope)" value={desc} onChange={(e) => setDesc(e.target.value)} rows={3} disabled={busy} />
        <input type="text" placeholder="tags, comma-separated (art, dataset, model…)" value={tags} onChange={(e) => setTags(e.target.value)} disabled={busy} />
        <div className="row">
          <label className="muted" htmlFor="mk-price">price</label>
          <input id="mk-price" type="text" inputMode="decimal" style={{ width: 110 }} value={price} onChange={(e) => setPrice(e.target.value)} disabled={busy} />
          <span className="muted">BLOC · 0 = free</span>
        </div>
        <p className="muted hint" style={{ margin: 0 }}>
          {priceN > 0
            ? `Buyers must HOLD ≥ ${priceN} BlocTime on-chain — their stake is the ticket; no payment moves.`
            : chosen?.visibility === "private"
              ? "Free drop of a private object: anyone signed in can claim access (a permanent read grant)."
              : "Free public drop: instantly readable by anyone — the listing makes it discoverable."}
        </p>
        <div className="row" style={{ marginTop: 6 }}>
          <button className="primary" onClick={submit} disabled={busy || !cid || !title.trim()}>
            <TagIcon /> List it
          </button>
          <button className="ghost" onClick={onClose} disabled={busy}>cancel</button>
        </div>
      </div>
    </Modal>
  );
}

/* ──────────────────────────── modal shell ──────────────────────────── */

/* ──────────────────────────── terms body (mini markdown) ──────────────────────────── */

function boldSpans(line: string): React.ReactNode {
  // **bold** only — all the terms document needs.
  return line.split("**").map((seg, i) => (i % 2 ? <strong key={i}>{seg}</strong> : seg));
}

function TermsBody({ text }: { text: string }) {
  const blocks: React.ReactNode[] = [];
  let list: string[] = [];
  let para: string[] = [];
  const flush = () => {
    if (list.length) {
      blocks.push(<ul key={blocks.length}>{list.map((li, i) => <li key={i}>{boldSpans(li)}</li>)}</ul>);
      list = [];
    }
    if (para.length) {
      blocks.push(<p key={blocks.length}>{boldSpans(para.join(" "))}</p>);
      para = [];
    }
  };
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line) { flush(); continue; }
    if (line.startsWith("## ")) { flush(); blocks.push(<h2 key={blocks.length}>{line.slice(3)}</h2>); continue; }
    if (line.startsWith("# ")) { flush(); blocks.push(<h1 key={blocks.length}>{line.slice(2)}</h1>); continue; }
    if (line.startsWith("- ")) { if (para.length) flush(); list.push(line.slice(2)); continue; }
    if (list.length) { list[list.length - 1] += ` ${line}`; continue; } // wrapped bullet
    para.push(line);
  }
  flush();
  return <>{blocks}</>;
}

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <strong>{title}</strong>
          <button onClick={onClose}>✕</button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}
