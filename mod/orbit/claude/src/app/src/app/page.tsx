"use client";

import { useState, useEffect, useRef, useCallback, useMemo, Fragment } from "react";
import dynamic from "next/dynamic";
import { VersionsPanel } from "../components/VersionsPanel";
import AuthBadge from "../components/AuthBadge";
import {
  ApiIcon,
  AppIcon,
  CodeIcon,
  OverviewIcon,
  FilesIcon,
  VersionsIcon,
  HubIcon,
  TasksIcon,
  ClaudeMark,
  prettyModName,
} from "../components/Icons";
import { qrSvg } from "./lib/qr";

const WalletModal = dynamic(() => import("../components/WalletModal"), { ssr: false });
const SudoModal = dynamic(() => import("../components/SudoModal"), { ssr: false });

import {
  getNetworkName,
  NETWORK_LOGOS,
  EVM_NETWORKS,
  switchNetwork,
  isExtensionBignumberBug,
  friendlyWalletError,
} from "../utils/wallet";

const DEFAULT_BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "/claude";
// The browser must reach the API through the relative gateway path
// (`/api/claude`, served by the host Caddy and the next.config rewrite). A
// localhost/127.0.0.1 value of NEXT_PUBLIC_API_URL points the fetch at the
// *visitor's* own machine — so sign-in/job submits fail for everyone except a
// browser running on the host. Ignore such values and fall back to the
// relative path; a genuine external API host is still honored.
const _ENV_API_URL = process.env.NEXT_PUBLIC_API_URL;
const DEFAULT_API_URL =
  _ENV_API_URL && !/localhost|127\.0\.0\.1|0\.0\.0\.0/.test(_ENV_API_URL)
    ? _ENV_API_URL
    : `/api${DEFAULT_BASE_PATH}`;
const API_PORT = parseInt(process.env.NEXT_PUBLIC_API_PORT || "8820", 10);

// ── Types ────────────────────────────────────────────────────────────

interface Job {
  id: string;
  prompt: string;
  model: string;
  work_dir: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  output: string;
  error: string | null;
  pid: number | null;
  created_at: number;
  updated_at: number;
  user_address?: string;
  asks?: Array<{ question: string; answer?: string; timestamp?: number }>;
}

interface TokenStats {
  balance: string;
  symbol: string;
  decimals: number;
  address: string;
  network: string;
}

interface SavedPrompt {
  id: string;
  title: string;
  body: string;
  pinned: boolean;
  created_at: number;
  updated_at: number;
  model?: string;
  tags?: string[];
  agent_type?: string;
}

interface Personality {
  id: string;
  name: string;
  icon: string;
  prompt: string;
  builtin?: boolean;
}

// A named system-prompt block. Blocks toggled `on` are chained (concatenated
// in list order) into the single system prompt sent with every task. Content
// is edited in the SYSTEM PROMPTS manager modal — never shown inline.
interface SysPromptBlock {
  id: string;
  name: string;
  text: string;
  on: boolean;
}

// ── Helpers ──────────────────────────────────────────────────────────

// While dragging/resizing a floating panel, embedded APP iframes must not
// swallow mousemove events (they go to the iframe's document, freezing the
// gesture) — toggle their pointer events off for the gesture's duration.
function setIframesInert(inert: boolean) {
  document.querySelectorAll("iframe").forEach((f) => {
    (f as HTMLElement).style.pointerEvents = inert ? "none" : "";
  });
}

// Safe localStorage write — never throws. When the browser quota is
// exhausted (claude_personalities, claude_saved_prompts, the auto-saved
// jobs cache, etc. can balloon), we evict the biggest claude_* keys
// except the ones that hold the auth session, then retry the write.
function safeSetItem(key: string, value: string): boolean {
  if (typeof window === "undefined") return false;
  try {
    window.localStorage.setItem(key, value);
    return true;
  } catch (e) {
    const isQuota = e instanceof Error && /quota/i.test(e.name + e.message);
    if (!isQuota) return false;
    const PRESERVE = new Set([
      "claude_jobs_token", "claude_jobs_address", "claude_jobs_wallet_type",
      "claude_jobs_seed", "claude_jobs_theme",
    ]);
    try {
      const sizes: Array<[string, number]> = [];
      for (let i = 0; i < window.localStorage.length; i++) {
        const k = window.localStorage.key(i);
        if (!k || PRESERVE.has(k) || k === key) continue;
        if (!k.startsWith("claude_")) continue;
        sizes.push([k, (window.localStorage.getItem(k) || "").length]);
      }
      sizes.sort((a, b) => b[1] - a[1]);
      for (const [k] of sizes.slice(0, 3)) window.localStorage.removeItem(k);
      window.localStorage.setItem(key, value);
      return true;
    } catch {
      return false;
    }
  }
}

// Build a submit-failure message that carries the API's reason (401 invalid
// token, 403 not permitted, …) instead of a bare "SUBMIT FAILED".
async function submitFailReason(res: Response): Promise<string> {
  const detail = await res.json().catch(() => null);
  return detail?.error ? `SUBMIT FAILED — ${detail.error}` : `SUBMIT FAILED (${res.status})`;
}

// Default gateway origin for this deployment. Two cases:
//  • Public deploy (behind Cloudflare/caddy on 80/443, i.e. https or no
//    explicit port): use the page origin verbatim — e.g. https://modc2.com.
//    The :3000 gateway port is internal and NOT publicly exposed, so
//    appending it would produce a dead "modc2.com:3000" link.
//  • LAN/dev (served on a non-standard port like :8823): the local gateway
//    listens on :3000, so point there — e.g. http://192.168.x.y:3000 — so a
//    phone on the same wifi works.
// `mounted` guards against SSR hydration mismatch: during the server render and
// the first client paint (mounted === false) we MUST return the same value the
// server produced ("http://localhost:3000"), or React errors on the http/https
// text divergence. After mount the effect flips it and we re-render with the
// real page origin. Handlers (which only run post-mount) can omit the arg.
function defaultGatewayOrigin(mounted = true): string {
  if (typeof window === "undefined" || !mounted) return "http://localhost:3000";
  const loc = window.location;
  const behindPublicProxy =
    loc.protocol === "https:" || loc.port === "" || loc.port === "80" || loc.port === "443";
  return behindPublicProxy ? loc.origin : `http://${loc.hostname}:3000`;
}

// Parse omnibox text into {origin, mod}. Accepts a full URL
// ("https://host/mod"), a schemeless one ("host/mod"), a bare path
// ("/mod") or a bare module name ("mod"). origin is null when the text
// carries no host of its own.
function parseOmnibox(text: string): { origin: string | null; mod: string } {
  const t = text.trim();
  if (!t) return { origin: null, mod: "" };
  if (t.startsWith("/")) return { origin: null, mod: t.split("/").filter(Boolean)[0] || "" };
  if (!/^[a-z]+:\/\//i.test(t) && !/[/.]/.test(t)) return { origin: null, mod: t };
  try {
    const u = new URL(/^[a-z]+:\/\//i.test(t) ? t : `https://${t}`);
    return { origin: u.origin, mod: u.pathname.split("/").filter(Boolean)[0] || "" };
  } catch {
    return { origin: null, mod: t.split("/").filter(Boolean).pop() || "" };
  }
}

// ── API tab: schema → playground form parsing ───────────────────────
// The /schema catalog is terse ({method, path, auth, desc}), so the merged
// docs/playground derives its input forms from it: ":id" path segments and
// "?address=0x.." query hints become labelled inputs, and a "{prompt,
// model?}" fragment in the description becomes one input per body field.

function parseEndpointInputs(path: string): {
  basePath: string;
  pathParams: string[];
  queryParams: Array<{ name: string; hint: string }>;
} {
  const [basePath, query] = path.split("?");
  const pathParams = (basePath.match(/:(\w+)/g) || []).map((s) => s.slice(1));
  const queryParams = (query || "")
    .split("&")
    .filter(Boolean)
    .map((pair) => {
      const [name, hint] = pair.split("=");
      return { name, hint: hint || "" };
    });
  return { basePath, pathParams, queryParams };
}

function parseBodyFields(desc: string): Array<{ name: string; optional: boolean; hint: string }> {
  const m = desc.match(/\{([^}]+)\}/);
  if (!m) return [];
  return m[1]
    .split(",")
    .map((tok) => {
      const [rawName, hint] = tok.split(":").map((s) => s.trim());
      return {
        name: (rawName || "").replace(/\?$/, ""),
        optional: (rawName || "").endsWith("?"),
        hint: hint || "",
      };
    })
    .filter((f) => /^\w+$/.test(f.name));
}

// Filled-in body fields → JSON string. Values that parse as JSON (42, true,
// ["a"]) keep their type; everything else is sent as a plain string, so
// non-technical users never have to quote anything.
function buildBodyJson(fields: Record<string, string>): string {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(fields)) {
    const t = v.trim();
    if (!t) continue;
    try {
      out[k] = JSON.parse(t);
    } catch {
      out[k] = t;
    }
  }
  return Object.keys(out).length ? JSON.stringify(out) : "";
}

// Plain-language read on an HTTP status, shown next to the numeric code.
function statusExplainer(code: number): string {
  if (code >= 200 && code < 300) return "it worked";
  if (code === 401) return "sign in first";
  if (code === 403) return "your account isn't allowed to do this";
  if (code === 404) return "nothing found at this address";
  if (code === 400 || code === 422) return "a required field is missing or malformed";
  if (code >= 500) return "the server hit an error";
  return "";
}

// Bucket endpoints into human sections by path prefix, in a fixed order.
const API_SECTION_ORDER = ["Basics", "Sign in", "Tasks", "Modules", "Files", "Access & sharing", "Credits", "Sudo"];
function endpointSection(path: string): string {
  const p = path.split("?")[0];
  if (p.startsWith("/auth")) return "Sign in";
  if (p.startsWith("/jobs")) return "Tasks";
  if (p.startsWith("/modules")) return "Modules";
  if (p.startsWith("/files")) return "Files";
  if (p.startsWith("/whitelist") || p.startsWith("/grants")) return "Access & sharing";
  if (p.startsWith("/credits")) return "Credits";
  if (p.startsWith("/sudo")) return "Sudo";
  return "Basics";
}

function timeSince(ts: number): string {
  const s = Math.floor(Date.now() / 1000 - ts);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function formatDate(ts: number): string {
  return new Date(ts * 1000).toLocaleString();
}

// Compact run-time label for a finished job (updated_at − created_at).
function formatDuration(secs: number): string {
  const s = Math.max(0, Math.floor(secs));
  if (s < 60) return `${s}s`;
  if (s < 3600) return s % 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${Math.floor(s / 60)}m`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

function shortCaller(addr?: string): string {
  if (!addr) return "local";
  const a = addr.trim();
  if (!a) return "local";
  if (a.length <= 12) return a;
  return `${a.slice(0, 6)}…${a.slice(-4)}`;
}

function shortHost(p?: string): string {
  if (!p) return "—";
  const m = p.match(/\/orbit\/([^/]+)(\/.*)?$/);
  if (m) {
    const rest = m[2] ? m[2].replace(/\/$/, "") : "";
    return rest ? `${m[1]}${rest}` : m[1];
  }
  const parts = p.split("/").filter(Boolean);
  if (parts.length <= 2) return p;
  return `…/${parts.slice(-2).join("/")}`;
}

// HUB card accents — each module gets a stable neon hue hashed from its
// name, so the card wall reads as a constellation instead of a spreadsheet.
const HUB_HUES = [
  "#a78bfa", "#38bdf8", "#34d399", "#fbbf24", "#fb7185",
  "#22d3ee", "#a3e635", "#e879f9", "#f472b6", "#818cf8",
];
function hubHue(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return HUB_HUES[h % HUB_HUES.length];
}

const STATUS_ICON: Record<string, string> = {
  pending: "○",
  running: "●",
  completed: "✓",
  failed: "✕",
  cancelled: "◼",
};

const STATUS_LABEL: Record<string, string> = {
  pending: "Queued",
  running: "Running",
  completed: "Complete",
  failed: "Failed",
  cancelled: "Cancelled",
};

const STATUS_COLOR_DARK: Record<string, string> = {
  pending: "#fbbf24",
  running: "#60a5fa",
  completed: "#34d399",
  failed: "#f87171",
  cancelled: "#64748b",
};

const STATUS_COLOR_LIGHT: Record<string, string> = {
  pending: "#f59e0b",
  running: "#3b82f6",
  completed: "#10b981",
  failed: "#ef4444",
  cancelled: "#94a3b8",
};

// ── Models ──────────────────────────────────────────────────────────
// Specific model IDs the user can pick. Value is what we hand to the CLI's
// --model flag (accepts both family aliases and full names like
// claude-opus-4-7). Keep first item = current default (Fable 5).
const MODEL_OPTIONS = [
  { value: "claude-fable-5",    label: "Fable 5",    family: "fable",  color: "#f472b6" },
  { value: "claude-opus-4-8",   label: "Opus 4.8",   family: "opus",   color: "#c4b5fd" },
  { value: "claude-opus-4-7",   label: "Opus 4.7",   family: "opus",   color: "#a78bfa" },
  { value: "claude-opus-4-6",   label: "Opus 4.6",   family: "opus",   color: "#9f7aea" },
  { value: "claude-sonnet-4-6", label: "Sonnet 4.6", family: "sonnet", color: "#60a5fa" },
  { value: "claude-sonnet-4-5", label: "Sonnet 4.5", family: "sonnet", color: "#3b82f6" },
  { value: "claude-haiku-4-5",  label: "Haiku 4.5",  family: "haiku",  color: "#34d399" },
];

// Legacy aliases (saved by older builds / older jobs) → upgraded value.
// Family aliases resolve to the newest of that family. `fable` now resolves to
// the live Claude Fable 5 model (Mythos access is generally available).
const MODEL_ALIAS_UPGRADE: Record<string, string> = {
  opus: "claude-opus-4-8",
  sonnet: "claude-sonnet-4-6",
  haiku: "claude-haiku-4-5",
  fable: "claude-fable-5",
};

// Map any stored/legacy model string to a currently-selectable model value,
// falling back to the default when it's unknown or gated. Mirrors the saved-
// model restore guard so replays/edits never resurrect an unavailable model.
function normalizeModelValue(m: string): string {
  if (!m) return MODEL_OPTIONS[0].value;
  const upgraded = MODEL_ALIAS_UPGRADE[m] || m;
  return MODEL_OPTIONS.some(o => o.value === upgraded) ? upgraded : MODEL_OPTIONS[0].value;
}

// Pretty label for any model string (handles legacy aliases too).
function modelLabel(m: string): string {
  if (!m) return "—";
  const chip = MODEL_OPTIONS.find(o => o.value === m);
  if (chip) return chip.label;
  if (m === "opus") return "Opus 4.6";       // historical default
  if (m === "sonnet") return "Sonnet 4.5";   // historical default
  if (m === "haiku") return "Haiku 4.5";
  return m;
}

// ── Agents ───────────────────────────────────────────────────────────
// Which agent runs the task — the selector at the top of PARAMS. Each agent
// owns its own parameter set (model, system prompts, …). "mod" is this
// module's claude-code harness and the only one wired today, which is why it
// is the default; sibling agents (codex, agent) plug in here later with
// their own params.
const AGENT_MODS = [
  { value: "mod",   label: "mod",   icon: "⬡", color: "#a78bfa", available: true,  hint: "this module's claude-code harness" },
  { value: "codex", label: "codex", icon: "◇", color: "#fbbf24", available: false, hint: "orbit/codex — a sibling module, coming soon" },
  { value: "agent", label: "agent", icon: "✦", color: "#60a5fa", available: false, hint: "orbit/agent — a sibling module, coming soon" },
];

// ── Default system prompt ─────────────────────────────────────────────
// Seeded as the first block in the SYSTEM PROMPTS chain on a fresh install,
// so there's always a nice, readable default the user can see, fork, and
// share — not a hidden empty string. Kept in sync with the backend
// DEFAULT_SYSTEM_PROMPT in src/mod.py.
const DEFAULT_SYSTEM_PROMPT = `You are a builder in the Orbit — a fleet of small, composable modules that each own one job and talk to each other over the mod protocol. You write code that ships.

HOW YOU WORK
- Read before you write. Match the file you're in: its naming, its idioms, its comment density. New code should look like it was always there.
- Prefer the smallest change that fully solves the problem. Delete more than you add when you can.
- Verify. Run the tests, drive the flow, read the output — don't claim it works because it should.
- Be honest about state. If something is a stub, say stub. If a test failed, show the failure. No cheerful hand-waving.

WHAT GOOD LOOKS LIKE
- Simple beats clever. The best design is the one the next person understands without you.
- One module, one responsibility. Reach for an existing module before inventing a new one.
- Secrets and per-user state live off-tree (~/.mod/<module>/), never in committed config.
- Leave the codebase better lit than you found it: clear names, a why-comment where the reason isn't obvious.

Ship it clean. Make Steve proud.`;

// ── Personalities ────────────────────────────────────────────────────

const DEFAULT_PERSONALITIES: Personality[] = [
  { id: "default", name: "Default", icon: ">_", prompt: "", builtin: true },
  { id: "architect", name: "Architect", icon: "△", prompt: "You are a senior software architect. You design systems, plan implementations, and reason about tradeoffs.\n\nCORE PRINCIPLES:\n- Think in systems. Consider how components interact.\n- Favor simplicity. The best architecture is the simplest one that works.\n- Plan before building. Use think to reason through designs.\n- Document decisions. Explain WHY, not just WHAT.\n\nWORKFLOW:\n1. UNDERSTAND: Read existing code, understand the codebase structure\n2. ANALYZE: Identify patterns, dependencies, and constraints\n3. DESIGN: Propose architecture with clear reasoning\n4. VALIDATE: Check feasibility against existing code\n5. FINISH: Deliver a clear implementation plan", builtin: true },
  { id: "reviewer", name: "Reviewer", icon: "◉", prompt: "You are an expert code reviewer. You find bugs, suggest improvements, and ensure code quality.\n\nCORE PRINCIPLES:\n- Be thorough. Check logic, edge cases, error handling.\n- Be constructive. Suggest fixes, not just problems.\n- Prioritize. Focus on correctness > security > performance > style.\n- Verify claims. Read the actual code, don't guess.\n\nWORKFLOW:\n1. READ: Examine the code under review\n2. ANALYZE: Check for bugs, security issues, and anti-patterns\n3. TEST: Run existing tests to verify current behavior\n4. REPORT: Provide structured feedback with severity levels\n5. FINISH: Summary of findings and recommendations", builtin: true },
  { id: "debugger", name: "Debugger", icon: "⬡", prompt: "You are an expert debugger. You find root causes, not symptoms.\n\nCORE PRINCIPLES:\n- Reproduce first. Understand the bug before fixing it.\n- Trace the data. Follow the flow from input to output.\n- Question assumptions. The bug is often where you least expect it.\n- Fix the root cause. Band-aids create more bugs.\n\nWORKFLOW:\n1. REPRODUCE: Understand the symptoms and reproduce the issue\n2. TRACE: Follow code paths, read logs, check state\n3. ISOLATE: Narrow down to the exact location and cause\n4. FIX: Apply a surgical fix to the root cause\n5. VERIFY: Run tests to confirm the fix works", builtin: true },
  { id: "builder", name: "Builder", icon: "◆", prompt: "You are a rapid builder. You ship features fast with production quality.\n\nCORE PRINCIPLES:\n- Ship it. Working code beats perfect plans.\n- Read first. Understand patterns before writing.\n- Test it. Verify your changes work.\n- Keep it clean. Simple, readable, maintainable.\n\nWORKFLOW:\n1. CONTEXT: Understand the codebase and requirements\n2. PLAN: Quick plan, then execute\n3. BUILD: Write the code, following existing patterns\n4. TEST: Verify it works\n5. FINISH: Commit-ready code", builtin: true },
  { id: "refactorer", name: "Refactorer", icon: "⟳", prompt: "You are a refactoring specialist. You improve code structure without changing behavior.\n\nCORE PRINCIPLES:\n- Preserve behavior. Refactoring must not change what the code does.\n- Test first. Ensure tests pass before AND after changes.\n- Small steps. Make incremental improvements.\n- Follow patterns. Match the codebase's existing conventions.\n\nWORKFLOW:\n1. UNDERSTAND: Read the code and its tests thoroughly\n2. TEST: Run tests to establish baseline\n3. REFACTOR: Make targeted improvements\n4. VERIFY: Run tests again to confirm behavior preserved\n5. FINISH: Clean, improved code with passing tests", builtin: true },
];

const PERSONALITY_ICONS = [">_", "△", "◉", "⬡", "◆", "⟳", "☆", "⚡", "♦", "◎", "⊕", "⊗", "♠", "♣", "✦", "⬢", "◇", "▣", "◈", "⊛"];

// ── File Type Colors ─────────────────────────────────────────────────

const FILE_TYPE_COLORS: Record<string, string> = {
  ".py": "#3572A5", ".js": "#f1e05a", ".ts": "#2b7489", ".tsx": "#2b7489",
  ".jsx": "#f1e05a", ".rs": "#dea584", ".go": "#00ADD8", ".java": "#b07219",
  ".cpp": "#f34b7d", ".c": "#555555", ".sh": "#89e051", ".json": "#ffb000",
  ".md": "#519aba", ".yaml": "#cb171e", ".yml": "#cb171e", ".toml": "#9c4221",
  ".xml": "#0060ac", ".html": "#e34c26", ".css": "#563d7c", ".sol": "#AA6746",
  ".txt": "#cccccc", ".log": "#888888", ".ini": "#d1dbe0", ".lock": "#555555",
  ".svg": "#ff9900", ".png": "#a074c4", ".jpg": "#a074c4", ".gif": "#a074c4",
  ".sql": "#e38c00", ".rb": "#701516", ".php": "#4F5D95", ".swift": "#F05138",
};

function getFileTypeColor(filename: string): string {
  const dot = filename.lastIndexOf(".");
  if (dot === -1) return "#cccccc";
  const ext = filename.substring(dot).toLowerCase();
  return FILE_TYPE_COLORS[ext] || "#cccccc";
}

// ── ASCII Art ────────────────────────────────────────────────────────

const BOOT_ART = `
  ┌──────────────────────────────────────┐
  │                                      │
  │      C L A U D E  ✦  O R B I T        │
  │                                      │
  │     Programmable AI dev console      │
  │                                      │
  └──────────────────────────────────────┘`;

// ── Module avatar ────────────────────────────────────────────────────
// HUB profile pic: a live screenshot of the module's app, captured and
// cached by the API (GET /modules/:name/screenshot — headless chromium
// through the local gateway). Modules without an app, or whose capture
// failed, fall back to a letter tile so the grid never shows broken images.
function ModuleAvatar({ apiUrl, name, hasApp }: { apiUrl: string; name: string; hasApp: boolean }) {
  const [failed, setFailed] = useState(false);
  const showShot = hasApp && !failed;
  return (
    <span
      className="shrink-0 w-10 h-10 rounded-md overflow-hidden flex items-center justify-center"
      style={{
        border: "1px solid var(--border-color)",
        background: "color-mix(in srgb, var(--accent-color) 6%, transparent)",
      }}
    >
      {showShot ? (
        <img
          src={`${apiUrl}/modules/${encodeURIComponent(name)}/screenshot`}
          alt=""
          loading="lazy"
          className="w-full h-full object-cover"
          style={{ objectPosition: "top" }}
          onError={() => setFailed(true)}
        />
      ) : (
        <span className="font-code font-bold text-[16px]" style={{ color: "var(--accent-color)", opacity: 0.75 }}>
          {name.slice(0, 1).toUpperCase()}
        </span>
      )}
    </span>
  );
}

// ── Main Component ───────────────────────────────────────────────────

export default function Home() {
  const [address, setAddress] = useState<string | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [authLoading, setAuthLoading] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);
  const [bootPhase, setBootPhase] = useState(0);
  const [walletType, setWalletType] = useState<"metamask" | "subwallet" | "local" | "password" | null>(null);
  const [isOwner, setIsOwner] = useState<boolean>(false);
  const [ownerAddress, setOwnerAddress] = useState<string | null>(null);
  // Terms-of-Use agreement modal shown to non-owner wallets before their
  // first sign-in signature; the resolver unblocks signChallenge().
  const [termsPrompt, setTermsPrompt] = useState<{ title: string; text: string } | null>(null);
  const termsResolveRef = useRef<((agreed: boolean) => void) | null>(null);

  const [jobs, setJobs] = useState<Job[]>([]);
  const [draggedJobId, setDraggedJobId] = useState<string | null>(null);
  const [dragOverJobId, setDragOverJobId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState("claude-fable-5");
  const [agentType, setAgentType] = useState("default");
  // Chainable system prompts: named blocks, each toggleable on/off. All
  // active blocks are concatenated in order and sent as the system prompt
  // with every task (winning over a personality). PARAMS shows only name
  // chips — the content lives in a separate manager modal.
  const [sysPrompts, setSysPrompts] = useState<SysPromptBlock[]>([]);
  const [systemPromptOpen, setSystemPromptOpen] = useState(false);
  // MOD picker inside PARAMS — a searchable inline list instead of a
  // native <select>, so a 60+ module registry stays findable. Opens in
  // place (the panel scrolls) to avoid overflow clipping on phones.
  const [modPickerOpen, setModPickerOpen] = useState(false);
  const [modPickerQuery, setModPickerQuery] = useState("");
  const modPickerInputRef = useRef<HTMLInputElement | null>(null);
  const [showSysPromptManager, setShowSysPromptManager] = useState(false);
  const [editingSysPrompt, setEditingSysPrompt] = useState<SysPromptBlock | null>(null);
  const [creatingSysPrompt, setCreatingSysPrompt] = useState(false);
  const [sysPromptDraft, setSysPromptDraft] = useState({ name: "", text: "" });
  // Which agent runs the task. Defaults to "mod" (this module's claude-code
  // harness — the only one wired today); the selector also surfaces sibling
  // agents (codex, agent) that will bring their own parameter sets.
  const [agentMod, setAgentMod] = useState("mod");

  const [workDir, setWorkDir] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [selectedJob, setSelectedJob] = useState<string | null>(null);
  const [streamOutput, setStreamOutput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [showSubmit, setShowSubmit] = useState(true);
  const [repos, setRepos] = useState<{ name: string; path: string; display: string }[]>([]);
  const [showRepos, setShowRepos] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<string | null>(null);
  const [showModuleOptions, setShowModuleOptions] = useState(false);
  const [moduleName, setModuleName] = useState("");
  const [creationMode, setCreationMode] = useState<"edit" | "fork" | "new">("edit");
  const [selectedModule, setSelectedModule] = useState("claude");
  // Most-recently-opened modules (names, newest first) — powers the left nav
  // rail's quick-switch list. Persisted so the rail is useful on reload.
  const [recentModules, setRecentModules] = useState<string[]>([]);
  // Recents rail — a slim vertical tab on the far left that expands into a
  // drag-resizable list of recently-opened modules. Collapsed by default;
  // both the open flag and the width persist across reloads.
  const [recentsRailOpen, setRecentsRailOpen] = useState(false);
  const [recentsRailWidth, setRecentsRailWidth] = useState(208);
  const [isRecentsRailDragging, setIsRecentsRailDragging] = useState(false);
  // Expand the embedded module APP to a full-viewport overlay (hides the
  // console chrome so you get the module's app edge-to-edge).
  const [appExpanded, setAppExpanded] = useState(false);
  // Phone-view: render the embedded APP iframe inside a 390×844 device
  // frame so mobile layout breakage is visible without leaving the console.
  // Media queries inside the iframe respond to the iframe's own size, so
  // shrinking the frame is a faithful phone-viewport preview.
  const [appPhoneView, setAppPhoneView] = useState(false);
  // ── Omnibox (APP tab) ─────────────────────────────────────────────
  // The URL strip and the module search merged into one browser-style
  // address bar: the /{mod} path segment autocompletes against the module
  // registry, ‹ › step through it, and the host is editable too — each
  // host runs its own registrar, so swapping the host re-roots the same
  // /{mod} namespace onto that site's registry.
  const [omniEditing, setOmniEditing] = useState(false);
  const [omniValue, setOmniValue] = useState("");
  const [omniIdx, setOmniIdx] = useState(0);
  // False until after the first client render, so URL strings derived from
  // window.location (see defaultGatewayOrigin) hydrate identically to the server.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  // Custom gateway origin ("https://otherhost.xyz") — null = this site.
  const [gatewayHostOverride, setGatewayHostOverride] = useState<string | null>(null);
  // A /{mod} committed while on a foreign host that this site's registrar
  // doesn't know — kept verbatim (the foreign registrar may resolve it).
  const [omniForeignMod, setOmniForeignMod] = useState<string | null>(null);
  const [githubUrl, setGithubUrl] = useState("");
  const [anchorDir, setAnchorDir] = useState("~/mod");
  const [modules, setModules] = useState<string[]>([]);
  const [moduleList, setModuleList] = useState<Array<{
    name: string; path: string; display: string; category: string; has_config: boolean;
    app_url: string | null; api_url: string | null; description: string | null;
    fns: string[]; has_app_dir: boolean; has_server_dir: boolean; has_api_dir: boolean;
    owner: string | null; version: string | null; cid: string | null; created_at: number | null;
    updated_at?: number | null;
    deps?: string[] | null;
  }>>([]);
  const [moduleSearch, setModuleSearch] = useState("");
  const [showModuleDropdown, setShowModuleDropdown] = useState(false);
  const [selectedModuleInfo, setSelectedModuleInfo] = useState<typeof moduleList[0] | null>(null);
  const [moduleConfig, setModuleConfig] = useState<any>(null);
  const [loadingConfig, setLoadingConfig] = useState(false);
  const [moduleRunning, setModuleRunning] = useState<boolean | null>(null);
  const [appRunning, setAppRunning] = useState<boolean | null>(null);
  const [togglingModule, setTogglingModule] = useState(false);
  const [togglingApi, setTogglingApi] = useState(false);
  const [togglingApp, setTogglingApp] = useState(false);
  const [moduleLogs, setModuleLogs] = useState<Record<string, string>>({});
  const [moduleLogsOpen, setModuleLogsOpen] = useState<"api" | "app" | null>(null);
  const [moduleLogsLoading, setModuleLogsLoading] = useState(false);
  const [moduleLogsAutoRefresh, setModuleLogsAutoRefresh] = useState(false);
  // ── Module hub ────────────────────────────────────────────────────
  // Landing grid of every module: pick one to start editing it, with a live
  // online/offline dot per module. Statuses are probed in the background while
  // the hub is open (app port via the same-origin /api/service route, API via
  // its /health). `autoRestartAfterEdit` restarts a module through pm2 as soon
  // as an edit job targeting it completes, so changes actually take effect.
  const [moduleStatuses, setModuleStatuses] = useState<Record<string, { app: boolean | null; api: boolean | null }>>({});
  const [hubSearch, setHubSearch] = useState("");
  // Hub layout: "grid" is the card wall; "graph" lays modules out by their
  // declared `deps` (config.json) as a dependency graph, leaving modules with
  // no edges as isolated nodes.
  const [hubGraphMode, setHubGraphMode] = useState(false);
  // Hub status filter: every module, only ones with a live app/api probe, or
  // only ones without. "stopped" includes not-yet-probed modules — anything
  // not confirmed live — so the split always covers the whole grid.
  const [hubRunFilter, setHubRunFilter] = useState<"all" | "running" | "stopped">("all");
  // Same module name published by DIFFERENT owners collapses into one hub
  // card with an owner picker. Maps name → path of the copy being shown;
  // not persisted, so every load defaults back to YOUR copy when you own one.
  const [hubOwnerPick, setHubOwnerPick] = useState<Record<string, string>>({});
  // Anchor for the owner-picker popover (fixed-position — .hub-card clips
  // overflow, so the menu can't live inside the card).
  const [hubOwnerMenu, setHubOwnerMenu] = useState<{ name: string; x: number; y: number } | null>(null);
  const [autoRestartAfterEdit, setAutoRestartAfterEdit] = useState(true);
  // ── Add-Module modal: import a fresh module from a GitHub repo or a
  //    snapshot CID (POST /modules/import). ───────────────────────────
  const [addOpen, setAddOpen] = useState(false);
  const [addSource, setAddSource] = useState<"github" | "cid">("github");
  const [addUrl, setAddUrl] = useState("");
  const [addCid, setAddCid] = useState("");
  const [addName, setAddName] = useState("");
  const [addBusy, setAddBusy] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  const [restartNotice, setRestartNotice] = useState<string | null>(null);
  const prevJobStatusRef = useRef<Record<string, string>>({});
  const [expandedAsks, setExpandedAsks] = useState<Set<string>>(new Set());
  const [expandedPrompts, setExpandedPrompts] = useState<Set<string>>(new Set());
  const [expandedJobImage, setExpandedJobImage] = useState<string | null>(null);
  const [images, setImages] = useState<Array<{ name: string; data: string }>>(
    []
  );
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [showUserDetails, setShowUserDetails] = useState(false);
  const [showVersions, setShowVersions] = useState(false);

  // Snapshot chain of the currently-selected module — polls every 30s and
  // drives the versions count badge; the full VersionsPanel handles
  // fork/restore actions.
  type VersionChainEntry = {
    cid: string;
    message: string;
    author: string;
    timestamp: number;
    parent: string | null;
    action?: string;
  };
  const [agentVersions, setAgentVersions] = useState<VersionChainEntry[]>([]);

  // Owner-managed whitelist of EOAs that may sign in to this module.
  // Backed by GET/POST/DELETE /whitelist on the Rust API. Non-owners
  // see the list read-only; owner gets a delete X per row + an add box.
  const [whitelist, setWhitelist] = useState<string[]>([]);
  const [whitelistInput, setWhitelistInput] = useState("");
  const [whitelistBusy, setWhitelistBusy] = useState(false);
  const [whitelistError, setWhitelistError] = useState<string | null>(null);

  // ── Time-boxed edit grants (QR hand-off) ──────────────────────────
  // The owner mints a grant → a QR-shareable invite that confers temporary
  // edit access (default 1h), optionally gated by a locally-generated key the
  // owner shares out of band. Backed by GET/POST/DELETE /grants on the API.
  type GrantRow = { id: string; exp: number; ttl: number; label?: string | null; created: number; key_required: boolean };
  type RedemptionRow = { address: string; exp: number; grant: string; redeemed: number };
  const [grants, setGrants] = useState<GrantRow[]>([]);
  const [grantRedemptions, setGrantRedemptions] = useState<RedemptionRow[]>([]);
  const [grantTtl, setGrantTtl] = useState(3600); // seconds; default 1h
  const [grantKey, setGrantKey] = useState("");
  const [grantLabel, setGrantLabel] = useState("");
  const [grantBusy, setGrantBusy] = useState(false);
  const [grantError, setGrantError] = useState<string | null>(null);
  // The just-minted grant we're showing a QR for (id + the key to share OOB).
  const [activeGrant, setActiveGrant] = useState<{ id: string; exp: number; key?: string; key_required: boolean } | null>(null);
  const [grantCopied, setGrantCopied] = useState<string | null>(null);
  // Now (seconds) ticking for live countdowns on grants.
  const [nowSec, setNowSec] = useState(() => Math.floor(Date.now() / 1000));

  // Redemption side: an invite link (`?grant=<id>`) lands a non-owner here.
  // We capture it on mount and offer a "redeem" banner that threads the grant
  // through sign-in. The key (if required) is typed by the visitor.
  const [pendingGrant, setPendingGrant] = useState<string | null>(null);
  const [redeemKey, setRedeemKey] = useState("");
  const pendingGrantRef = useRef<{ id: string; key: string } | null>(null);
  // Walletless guest sessions (POST /grants/:id/redeem): unix expiry of the
  // guest access, driving the countdown pill + auto sign-out at grant end.
  const [guestExp, setGuestExp] = useState<number | null>(null);

  // ── Phone sign-in (session handoff QR) ─────────────────────────────
  // A signed-in browser mints a single-use, 5-min code bound to its OWN
  // identity (POST /auth/handoff); scanning the QR opens `?handoff=<code>`,
  // which the mount hook below trades for a bearer token as the SAME address
  // — so your phone signs in without a wallet or any signing. Distinct from
  // grants, which invite someone ELSE in.
  const [handoff, setHandoff] = useState<{ code: string; exp: number } | null>(null);
  const [handoffBusy, setHandoffBusy] = useState(false);
  const [handoffError, setHandoffError] = useState<string | null>(null);

  // ── Share-anything QR ───────────────────────────────────────────────
  // One overlay that turns any shareable string — a snapshot CID/hash, a
  // module's app link, an import deep-link — into a locally-rendered QR
  // (qrSvg; the payload never leaves the browser). `options` are alternate
  // payload forms the user flips between (APP / IMPORT / CID / …).
  type QrShareOption = { label: string; value: string; hint?: string };
  const [qrShare, setQrShare] = useState<{ title: string; options: QrShareOption[] } | null>(null);
  const [qrShareIdx, setQrShareIdx] = useState(0);
  const [qrShareCopied, setQrShareCopied] = useState(false);

  // File viewer state — a splittable grid of panes: columns sit side-by-side
  // (split →) and each column stacks panes (split ↓). A pane collapses to a
  // slim bar; a column whose panes are all collapsed folds into a vertical
  // rail. An empty grid hands the tree the full width (old no-file state).
  type FilePane = { id: number; file: string; content: string; loading: boolean; collapsed: boolean };
  type FilePaneColumn = { id: number; panes: FilePane[] };
  const paneSeq = useRef(1);
  const [paneCols, setPaneCols] = useState<FilePaneColumn[]>([]);
  const [activePaneId, setActivePaneId] = useState<number | null>(null);
  const [editingPaneId, setEditingPaneId] = useState<number | null>(null);
  const [editBuffer, setEditBuffer] = useState("");
  const [savingFile, setSavingFile] = useState(false);
  // Inline search state
  const [inlineSearchMode, setInlineSearchMode] = useState<"off" | "files" | "grep">("files");
  const [inlineSearchQuery, setInlineSearchQuery] = useState("");
  const [inlineSearchResults, setInlineSearchResults] = useState<any[]>([]);
  const [inlineSearchLoading, setInlineSearchLoading] = useState(false);
  const [inlineSelectedIndex, setInlineSelectedIndex] = useState(0);
  const inlineSearchRef = useRef<HTMLInputElement>(null);

  // Token stats modal
  const [showTokenStats, setShowTokenStats] = useState(false);
  const [inputHeight, setInputHeight] = useState(160);
  const isDragging = useRef(false);
  const [rightPanelWidth, setRightPanelWidth] = useState(480);
  const isRightDragging = useRef(false);
  const [tokenStats, setTokenStats] = useState<TokenStats | null>(null);
  const [loadingTokenStats, setLoadingTokenStats] = useState(false);

  // Sudo authorization modal (privileged cross-module ops)
  const [sudoReq, setSudoReq] = useState<{ action: string; target: string } | null>(null);
  const [sudoStatus, setSudoStatus] = useState<"review" | "signing" | "success" | "error">("review");
  const [sudoError, setSudoError] = useState<string | null>(null);
  const sudoResolver = useRef<{ resolve: (t: string) => void; reject: (e: any) => void } | null>(null);
  // Sudo session + owner policy — one signature unlocks privileged ops for a
  // window (default 1h); the owner tailors duration / always-ask in ACCOUNT.
  const [sudoInfo, setSudoInfo] = useState<{
    active: boolean;
    expires: number | null;
    sessionSecs: number;
    alwaysAsk: string[];
  } | null>(null);
  const [sudoPolicyBusy, setSudoPolicyBusy] = useState(false);
  const [sudoPolicyErr, setSudoPolicyErr] = useState<string | null>(null);

  // Wallet modal
  const [copiedAddress, setCopiedAddress] = useState(false);
  const [profileMenuOpen, setProfileMenuOpen] = useState(false);
  const profileMenuRef = useRef<HTMLDivElement>(null);

  // Sign-in drawer — shown as a right-side panel inside the hub when there's no
  // session (replaces the old full-screen sign-in takeover). Closed by default:
  // signed-out visitors land on the hub as read-only guests and opt into
  // signing in via the edge tab. Re-opens only when an existing
  // session drops (sign-out / expiry); dismissible so the hub stays browsable behind it.
  const [signInOpen, setSignInOpen] = useState(false);

  // Account sidebar (persistent right panel). One function per tab —
  // WALLET / ACCESS / SUDO / MODULE — instead of one long merged scroll;
  // only the tabs the current session can use render.
  const [showOwnerSidebar, setShowOwnerSidebar] = useState(false);
  const [ownerSidebarWidth, setOwnerSidebarWidth] = useState(440);
  const [isOwnerSidebarDragging, setIsOwnerSidebarDragging] = useState(false);
  const [accountTab, setAccountTab] = useState<"wallet" | "access" | "sudo" | "module">("access");
  const [copiedWlAddr, setCopiedWlAddr] = useState<string | null>(null);

  // Network switcher (header)
  const [currentChainId, setCurrentChainId] = useState<number>(1);
  const [showHeaderNetworkDropdown, setShowHeaderNetworkDropdown] = useState(false);
  const [headerSwitchingNetwork, setHeaderSwitchingNetwork] = useState(false);

  const [showPasswordInput, setShowPasswordInput] = useState(false);
  const [passwordInput, setPasswordInput] = useState("");
  const [hasMetaMask, setHasMetaMask] = useState(false);
  const [hasSubWallet, setHasSubWallet] = useState(false);

  // Backend URL state
  const [apiUrl, setApiUrl] = useState(DEFAULT_API_URL);
  const [showBackendEditor, setShowBackendEditor] = useState(false);
  const [backendInput, setBackendInput] = useState("");

  // Dynamic API lifecycle
  const [apiStatus, setApiStatus] = useState<"on" | "off" | "starting">("off");
  const apiIdleTimeout = useRef(300); // seconds before auto-shutdown
  const apiLastActivity = useRef(0);
  const apiIdleTimer = useRef<NodeJS.Timeout | null>(null);

  // API explorer state
  const [apiSelectedEndpoint, setApiSelectedEndpoint] = useState<string | null>(null);
  const [apiParams, setApiParams] = useState<Record<string, string>>({});
  const [apiResponse, setApiResponse] = useState<string | null>(null);
  const [apiResponseStatus, setApiResponseStatus] = useState<number | null>(null);
  const [apiLoading, setApiLoading] = useState(false);
  const [apiMethod, setApiMethod] = useState<string>("GET");

  // Direct config from /config endpoint (fallback)
  const [directConfig, setDirectConfig] = useState<any>(null);

  // Changelog state
  const [changelogEntries, setChangelogEntries] = useState<Array<{
    version: string; cid: string; date: string; description: string;
    timestamp: number; file_count?: number;
  }>>([]);
  const [changelogLoading, setChangelogLoading] = useState(false);
  const [selectedVersion, setSelectedVersion] = useState<string | null>(null);
  const [versionDetail, setVersionDetail] = useState<any>(null);
  const [versionDetailLoading, setVersionDetailLoading] = useState(false);

  // Config tab state
  const [configSubTab, setConfigSubTab] = useState<"functions" | "endpoints" | "settings">("functions");
  const [configSelectedFn, setConfigSelectedFn] = useState<string | null>(null);
  const [configFnParams, setConfigFnParams] = useState<Record<string, string>>({});
  const [configFnResponse, setConfigFnResponse] = useState<string | null>(null);
  const [configFnLoading, setConfigFnLoading] = useState(false);

  // JSON tree viewer state
  const [collapsedPaths, setCollapsedPaths] = useState<Set<string>>(new Set());
  const [copiedPath, setCopiedPath] = useState<string | null>(null);

  // New UI state
  const [moduleTab, setModuleTab] = useState<"app" | "api" | "changelog">("app");
  const [taskSubTab, setTaskSubTab] = useState<"tasks" | "input" | "output" | "deltas">("input");
  // Sub-tab inside an open task: raw OUTPUT, the file EDITS it made, or an
  // AUDIT trail of every tool action (bash / reads / searches / subtasks).
  const [taskDetailTab, setTaskDetailTab] = useState<"output" | "edits" | "audit">("output");
  // Whether the open task's prompt shows in full (clamped to 3 lines otherwise).
  const [promptExpanded, setPromptExpanded] = useState(false);
  const [viewMode, setViewMode] = useState<"output" | "code">("output");
  const [directoryTree, setDirectoryTree] = useState<any[]>([]);
  const [directoryTreeError, setDirectoryTreeError] = useState<string | null>(null);
  // Snapshot-scheme tree CID of the whole browsed dir (sha256 of the sorted
  // manifest) — matches what a VERSIONS snapshot of this dir would produce.
  const [treeRootHash, setTreeRootHash] = useState<string | null>(null);
  // Which CID chip was just click-copied (path or "root"), for ✓ feedback.
  const [copiedCid, setCopiedCid] = useState<string | null>(null);
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(new Set());

  // Agent sidebar state (persistent right panel)
  const [tasksSidebarOpen, setTasksSidebarOpen] = useState(true);
  const [sidebarWidth, setSidebarWidth] = useState(480);
  const [isSidebarDragging, setIsSidebarDragging] = useState(false);
  const [isLeftDragging, setIsLeftDragging] = useState(false);
  const [sidebarView, setSidebarView] = useState<"hub" | "tasks" | "app" | "api" | "apitab" | "overview" | "files" | "logs" | "terminal" | "versions">("overview");

  // ── API tab: endpoint catalog with an inline playground (FastAPI-docs
  // style, but each row expands in place instead of jumping to a second
  // view), plus logs. One endpoint is expanded at a time so the shared
  // try* request/response state is unambiguous. ──
  const [apiTabView, setApiTabView] = useState<"schema" | "logs">("schema");
  const [apiSchema, setApiSchema] = useState<{
    auth_note?: string;
    base?: string;
    endpoints: Array<{ method: string; path: string; auth: string; desc: string }>;
  } | null>(null);
  const [apiFilter, setApiFilter] = useState("");
  const [apiExpanded, setApiExpanded] = useState<number | null>(null);
  const [tryParams, setTryParams] = useState<Record<string, string>>({});
  const [tryBodyFields, setTryBodyFields] = useState<Record<string, string>>({});
  const [tryBodyRaw, setTryBodyRaw] = useState(false);
  const [tryBody, setTryBody] = useState<string>("");
  const [tryStatus, setTryStatus] = useState<string | null>(null);
  const [tryOk, setTryOk] = useState<boolean | null>(null);
  const [tryResp, setTryResp] = useState<string | null>(null);
  const [tryRespKind, setTryRespKind] = useState<"text" | "image">("text");
  const [trySending, setTrySending] = useState(false);
  // Inner tab inside the OVERVIEW view — keeps the identity hero always
  // visible while INFO / ACCESS / LOGS / CONFIG swap below it instead of
  // stacking into one long scroll.
  const [overviewTab, setOverviewTab] = useState<"info" | "access" | "logs" | "config">("info");
  // Sub-view inside the merged CODE tab: the file tree vs. the snapshot
  // version history. Versions used to be its own top-level mod tab; it's
  // now folded into CODE alongside the files browser.
  const [codeView, setCodeView] = useState<"files" | "versions">("files");

  // ── Terminal tab state (owner-only shell access) ────────────────────
  // Each history entry is one executed command plus its captured output.
  // The terminal runs commands inside the selected module's working dir
  // via /api/terminal — no PTY, just one-shot `bash -c` exec, which keeps
  // the surface small and matches the existing /api/service pattern.
  type TerminalEntry = {
    id: string;
    cmd: string;
    cwd: string;
    stdout: string;
    stderr: string;
    code: number | null;
    durationMs: number;
    pending: boolean;
    // Which nix env the command ran inside ("flake" | "shell"), or null/undefined
    // for the bare host shell. Set from the /api/terminal response.
    nix?: string | null;
  };
  const [terminalHistory, setTerminalHistory] = useState<TerminalEntry[]>([]);
  const [terminalInput, setTerminalInput] = useState("");
  const [terminalRunning, setTerminalRunning] = useState(false);
  const [terminalRecallIdx, setTerminalRecallIdx] = useState<number | null>(null);
  // Owner session token for the host shell — minted by /api/terminal/auth after
  // the owner signs with their wallet. Without it the terminal route 401s.
  const [terminalToken, setTerminalToken] = useState<string | null>(null);
  const [terminalTokenExp, setTerminalTokenExp] = useState<number>(0);
  const [terminalAuthing, setTerminalAuthing] = useState(false);
  const [terminalAuthError, setTerminalAuthError] = useState<string | null>(null);

  // Left sidebar (agent) and right sidebar (wallet)
  const [leftSidebarOpen, setLeftSidebarOpen] = useState(true);
  const [leftSidebarWidth, setLeftSidebarWidth] = useState(420);

  // Mobile viewport detector — drives the AGENT-panel overlay vs. side-
  // by-side layout, single vs. two-col bento, etc. Matches Tailwind's
  // `md` breakpoint (768px) so the JS-driven inline styles align with
  // any future `md:` class additions.
  const [isMobile, setIsMobile] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined") return;
    const mq = window.matchMedia("(max-width: 767px)");
    const update = () => setIsMobile(mq.matches);
    update();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, []);
  const [sidebarSide, setSidebarSide] = useState<"left" | "right">("right");

  const moduleDropdownRef = useRef<HTMLDivElement>(null);
  const inlineModuleRef = useRef<HTMLDivElement>(null);
  const headerModuleRef = useRef<HTMLDivElement>(null);
  const [showInlineModuleDropdown, setShowInlineModuleDropdown] = useState(false);
  const [showHeaderModuleDropdown, setShowHeaderModuleDropdown] = useState(false);
  const [headerModuleSearch, setHeaderModuleSearch] = useState("");
  const [ownerFilter, setOwnerFilter] = useState<string | null>(null);
  // Owner bubble in the tabs row — brief "copied" flash after click-to-copy.
  const [ownerBubbleCopied, setOwnerBubbleCopied] = useState(false);
  const [folderSuggestions, setFolderSuggestions] = useState<Array<{
    name: string; path: string; display: string; score: number; preview: string;
    has_config: boolean; has_mod: boolean;
  }>>([]);
  const [folderList, setFolderList] = useState<Array<{
    name: string; path: string; display: string; has_config: boolean; has_mod: boolean;
  }>>([]);
  const [selectorMode, setSelectorMode] = useState<"modules" | "folders">("modules");
  const [showHeaderCreateForm, setShowHeaderCreateForm] = useState<"create" | "fork" | "edit" | "import" | null>(null);
  const [headerNewName, setHeaderNewName] = useState("");
  const [headerGithubUrl, setHeaderGithubUrl] = useState("");
  const [headerEditPrompt, setHeaderEditPrompt] = useState("");
  // IMPORT tab — deterministic add from an existing GitHub repo or snapshot
  // CID (same POST /modules/import path the HUB "Add a module" modal uses).
  const [headerImportSource, setHeaderImportSource] = useState<"github" | "cid">("github");
  const [headerCid, setHeaderCid] = useState("");
  // Where the BUILD/FORK/EDIT/IMPORT form renders: the header "+" popover
  // (mobile / collapsed rail), compressed inline at the bottom of the left
  // nav rail (desktop default), or popping up from the composer dock's "+"
  // button. Same state + submit path in all three.
  const [createAnchor, setCreateAnchor] = useState<"header" | "composer">("header");

  // Prompt management state
  const [savedPrompts, setSavedPrompts] = useState<SavedPrompt[]>([]);
  const [showPromptManager, setShowPromptManager] = useState(false);
  const [showPromptList, setShowPromptList] = useState(false);
  const [editingPrompt, setEditingPrompt] = useState<SavedPrompt | null>(null);
  const [promptDraft, setPromptDraft] = useState({ title: "", body: "", tags: "", agent_type: "default" });
  const [promptSearchQuery, setPromptSearchQuery] = useState("");

  // Personality management state
  const [personalities, setPersonalities] = useState<Personality[]>(DEFAULT_PERSONALITIES);
  const [showPersonalityManager, setShowPersonalityManager] = useState(false);
  const [editingPersonality, setEditingPersonality] = useState<Personality | null>(null);
  const [creatingPersonality, setCreatingPersonality] = useState(false);
  const [personalityDraft, setPersonalityDraft] = useState({ name: "", icon: ">_", prompt: "" });

  // Floating FILES panel state
  const [filesPanelFloating, setFilesPanelFloating] = useState(false);
  const [filesPanelPos, setFilesPanelPos] = useState({ x: 200, y: 100 });
  const [filesPanelSize, setFilesPanelSize] = useState({ w: 600, h: 500 });
  const filesPanelDrag = useRef<{ startX: number; startY: number; origX: number; origY: number } | null>(null);
  const filesPanelResize = useRef<{ startX: number; startY: number; origW: number; origH: number; edge: string } | null>(null);

  // Floating composer dock — the bottom ask bar can pop out to a movable,
  // width-resizable panel, or collapse to a small pill tool (bottom-right).
  const [composerFloating, setComposerFloating] = useState(false);
  const [composerMinimized, setComposerMinimized] = useState(false);
  const [composerPos, setComposerPos] = useState({ x: 120, y: 120 });
  const [composerW, setComposerW] = useState(720);
  const composerDrag = useRef<{ startX: number; startY: number; origX: number; origY: number } | null>(null);
  const composerResize = useRef<{ startX: number; origW: number; origX: number; edge: string } | null>(null);

  // Kill process dialog state (host key: Cmd+K)
  const [showKillDialog, setShowKillDialog] = useState(false);
  const [killInput, setKillInput] = useState("");
  const [killMode, setKillMode] = useState<"pid" | "port">("port");
  const [killSignal, setKillSignal] = useState<"SIGKILL" | "SIGTERM">("SIGKILL");
  const [killResult, setKillResult] = useState<any>(null);
  const [killLoading, setKillLoading] = useState(false);
  const killInputRef = useRef<HTMLInputElement>(null);
  const composerInputRef = useRef<HTMLInputElement>(null);
  // Composer dock — publish its live height as a CSS var so fixed
  // full-height neighbors (the left nav rail, the mobile fullscreen task
  // overlay) can stop right above it; the dock itself spans the full
  // viewport width.
  const composerDockRO = useRef<ResizeObserver | null>(null);
  const composerDockRef = useCallback((el: HTMLDivElement | null) => {
    composerDockRO.current?.disconnect();
    composerDockRO.current = null;
    if (!el) {
      document.documentElement.style.setProperty("--composer-dock-h", "0px");
      return;
    }
    const apply = () =>
      document.documentElement.style.setProperty("--composer-dock-h", `${el.offsetHeight}px`);
    apply();
    if (typeof ResizeObserver !== "undefined") {
      const ro = new ResizeObserver(apply);
      ro.observe(el);
      composerDockRO.current = ro;
    }
  }, []);

  // Measure the sticky header's height into --header-h so right-side drawers
  // (the sign-in panel) can start BELOW the header instead of overlapping it.
  const headerRO = useRef<ResizeObserver | null>(null);
  const headerRef = useCallback((el: HTMLDivElement | null) => {
    headerRO.current?.disconnect();
    headerRO.current = null;
    if (!el) {
      document.documentElement.style.setProperty("--header-h", "0px");
      return;
    }
    const apply = () =>
      document.documentElement.style.setProperty("--header-h", `${el.offsetHeight}px`);
    apply();
    if (typeof ResizeObserver !== "undefined") {
      const ro = new ResizeObserver(apply);
      ro.observe(el);
      headerRO.current = ro;
    }
  }, []);

  const headerCreateRef = useRef<HTMLDivElement>(null);
  const composerCreateRef = useRef<HTMLDivElement>(null);
  const repoRef = useRef<HTMLDivElement>(null);
  const outputRef = useRef<HTMLDivElement>(null);
  const esRef = useRef<EventSource | null>(null);
  const userDetailsRef = useRef<HTMLDivElement>(null);
  const tokenStatsRef = useRef<HTMLDivElement>(null);

  // Theme-aware helpers
  const isLight = theme === "light";
  const STATUS_COLOR = isLight ? STATUS_COLOR_LIGHT : STATUS_COLOR_DARK;
  const tintBg = isLight ? "rgba(0,0,0,0.02)" : "rgba(255,255,255,0.02)";
  const tintBgStrong = isLight ? "rgba(0,0,0,0.04)" : "rgba(255,255,255,0.04)";
  const subtleBorder = isLight ? "rgba(0,0,0,0.08)" : "var(--border-color)";
  const subtleBorderStrong = isLight ? "rgba(0,0,0,0.12)" : "rgba(255,255,255,0.12)";
  const faintGreen = isLight ? "rgba(16,185,129,0.06)" : "rgba(52,211,153,0.08)";
  const faintGreenText = isLight ? "rgba(16,185,129,0.25)" : "rgba(52,211,153,0.25)";
  const walletGreen = isLight ? "rgba(16,185,129," : "rgba(52,211,153,";
  const walletAmber = isLight ? "rgba(245,158,11," : "rgba(251,191,36,";
  const apiGreenBorder = isLight ? "rgba(16,185,129,0.25)" : "rgba(52,211,153,0.3)";
  const apiGreenBg = isLight ? "rgba(16,185,129,0.05)" : "rgba(52,211,153,0.06)";
  const apiBlueBorder = isLight ? "rgba(59,130,246,0.25)" : "rgba(96,165,250,0.3)";
  const apiBlueBg = isLight ? "rgba(59,130,246,0.05)" : "rgba(96,165,250,0.06)";
  const apiRedBorder = isLight ? "rgba(239,68,68,0.25)" : "rgba(248,113,113,0.3)";
  const apiRedBg = isLight ? "rgba(239,68,68,0.05)" : "rgba(248,113,113,0.06)";
  const darkOverlay = isLight ? "rgba(0,0,0,0.02)" : "rgba(0,0,0,0.3)";
  const darkOverlayStrong = isLight ? "rgba(0,0,0,0.04)" : "rgba(0,0,0,0.4)";
  const cardHoverBg = isLight ? "rgba(0,0,0,0.02)" : "rgba(255,255,255,0.02)";
  const networkBtnBg = isLight ? "rgba(0,0,0,0.03)" : "rgba(0,0,0,0.15)";
  const selectedHighlight = isLight ? "rgba(255,255,255,0.05)" : "rgba(255,255,255,0.05)";
  const copyBtnBg = isLight ? "rgba(0,0,0,0.03)" : "rgba(255,255,255,0.04)";
  // JSON viewer colors (light-aware)
  const jsonKeyColor = isLight ? "#0369a1" : "#8be9fd";
  const jsonNullColor = isLight ? "#6b7280" : "#6272a4";
  const jsonBoolColor = isLight ? "#be185d" : "#ff79c6";
  const jsonNumColor = isLight ? "#7c3aed" : "#bd93f9";
  const jsonAddrColor = isLight ? "#059669" : "#50fa7b";
  const jsonUrlColor = isLight ? "#0284c7" : "#8be9fd";
  const jsonStrColor = isLight ? "#b45309" : "#f1fa8c";
  const jsonRowHover = isLight ? "rgba(0,0,0,0.02)" : "rgba(139,233,253,0.03)";
  const jsonCopiedColor = isLight ? "#059669" : "#50fa7b";
  const jsonCopiedBg = isLight ? "rgba(5,150,105,0.1)" : "rgba(80,250,123,0.1)";

  // Pick the best default tab for a module based on its capabilities.
  // We land on APP (the live module interface) whenever the module has one —
  // the real thing first, OVERVIEW chrome only when there's no app to show.
  // claude's APP embeds the real claude app (see appModName in
  // renderAppApiTab); an embedded copy falls back to the web front-door so it
  // doesn't iframe itself forever.
  const getBestTab = useCallback((info: typeof moduleList[0] | null): "hub" | "overview" | "app" | "api" | "files" => {
    if (info?.app_url || info?.has_app_dir) return "app";
    return "overview";
  }, []);

  // Reset all module-specific state when switching modules
  const resetModuleState = useCallback((newModuleInfo?: typeof moduleList[0] | null) => {
    // Omnibox: picking a real module supersedes any foreign /{mod}
    setOmniForeignMod(null);
    // API explorer
    setApiSelectedEndpoint(null);
    setApiParams({});
    setApiResponse(null);
    setApiResponseStatus(null);
    setApiMethod("GET");
    setApiLoading(false);
    // Config tab
    setConfigSubTab("functions");
    setConfigSelectedFn(null);
    setConfigFnParams({});
    setConfigFnResponse(null);
    setConfigFnLoading(false);
    // Module health
    setModuleRunning(null);
    setTogglingModule(false);
    // Changelog
    setChangelogEntries([]);
    setSelectedVersion(null);
    setVersionDetail(null);
    // File viewer
    setPaneCols([]);
    setActivePaneId(null);
    setEditingPaneId(null);
    // Directory tree
    setDirectoryTree([]);
    setExpandedDirs(new Set());
    // JSON tree
    setCollapsedPaths(new Set());
    // A freshly opened module starts un-expanded.
    setAppExpanded(false);
    // Auto-select the best tab for the new module
    setSidebarView(getBestTab(newModuleInfo || null));
  }, [getBestTab]);

  // Track recently-opened modules (newest first, deduped, capped) whenever the
  // selection changes, and persist so the rail survives reloads.
  useEffect(() => {
    try {
      const saved = localStorage.getItem("claude_recent_modules");
      if (saved) {
        // Dedupe + sanitize on load: an earlier build could persist the same
        // name over and over (a rail full of "registry"), and stored dupes
        // otherwise survive forever because updates only filter the newly
        // selected name. A polluted cache heals itself here.
        const parsed = JSON.parse(saved);
        if (Array.isArray(parsed)) {
          setRecentModules(
            [...new Set(parsed.filter((n): n is string => typeof n === "string" && n.length > 0))].slice(0, 15),
          );
        }
      }
    } catch { /* ignore corrupt cache */ }
  }, []);
  // Esc exits the full-viewport app overlay.
  useEffect(() => {
    if (!appExpanded) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setAppExpanded(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [appExpanded]);
  useEffect(() => {
    if (!selectedModule) return;
    setRecentModules((prev) => {
      const next = [selectedModule, ...prev.filter((n) => n !== selectedModule)].slice(0, 15);
      return next;
    });
  }, [selectedModule]);
  useEffect(() => {
    // Never persist the initial empty state: this effect fires on mount
    // before the loaded list lands, and writing "[]" here clobbers the
    // stored recents (StrictMode's second mount then re-reads the empty
    // value and the rail forgets everything). Recents only ever grow or
    // reorder, so an empty list is always "not loaded yet", never data.
    if (recentModules.length === 0) return;
    safeSetItem("claude_recent_modules", JSON.stringify(recentModules));
  }, [recentModules]);

  // Recents rail open/width: load once, then persist on change. The loaded
  // ref gates the persist effects — they also fire on mount with the default
  // values, which would otherwise clobber the stored ones (localStorage is
  // shared across every modc2 module, so writes must be deliberate).
  const recentsRailLoaded = useRef(false);
  useEffect(() => {
    try {
      const open = localStorage.getItem("claude_recents_rail_open");
      if (open !== null) setRecentsRailOpen(open === "true");
      const w = parseInt(localStorage.getItem("claude_recents_rail_width") || "", 10);
      if (Number.isFinite(w)) setRecentsRailWidth(Math.max(160, Math.min(420, w)));
    } catch { /* ignore */ }
    recentsRailLoaded.current = true;
  }, []);
  useEffect(() => {
    if (recentsRailLoaded.current) safeSetItem("claude_recents_rail_open", String(recentsRailOpen));
  }, [recentsRailOpen]);
  useEffect(() => {
    if (recentsRailLoaded.current) safeSetItem("claude_recents_rail_width", String(recentsRailWidth));
  }, [recentsRailWidth]);
  // Drag the rail/content divider to resize — the rail hugs the viewport's
  // left edge, so the pointer's x IS the new width.
  useEffect(() => {
    if (!isRecentsRailDragging) return;
    const onMove = (e: MouseEvent) => setRecentsRailWidth(Math.max(160, Math.min(420, e.clientX)));
    const onUp = () => setIsRecentsRailDragging(false);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [isRecentsRailDragging]);

  // Restore the omnibox's custom gateway host (points /{mod} at another
  // deployment's registrar) once on mount.
  useEffect(() => {
    try {
      const h = window.localStorage.getItem("claude_gateway_host");
      if (h && /^[a-z]+:\/\/[^/]+$/i.test(h)) setGatewayHostOverride(h);
    } catch { /* ignore */ }
  }, []);

  // Detect wallet extensions client-side only (avoids hydration mismatch)
  useEffect(() => {
    setHasMetaMask(!!(window as any).ethereum?.isMetaMask);
    setHasSubWallet(!!(window as any).ethereum?.isSubWallet);
  }, []);

  // Detect current chain and listen for changes
  useEffect(() => {
    const ethereum = (window as any).ethereum;
    if (!ethereum) return;
    const fetchChain = async () => {
      try {
        const cid = await ethereum.request({ method: "eth_chainId" });
        setCurrentChainId(parseInt(cid, 16));
      } catch {}
    };
    fetchChain();
    const handler = (cid: string) => setCurrentChainId(parseInt(cid, 16));
    ethereum.on?.("chainChanged", handler);
    return () => ethereum.removeListener?.("chainChanged", handler);
  }, [address]);

  // Boot animation
  useEffect(() => {
    const timers = [
      setTimeout(() => setBootPhase(1), 300),
      setTimeout(() => setBootPhase(2), 800),
      setTimeout(() => setBootPhase(3), 1400),
    ];
    return () => timers.forEach(clearTimeout);
  }, []);

  // Apply theme to document root
  useEffect(() => {
    const savedTheme = localStorage.getItem("claude_jobs_theme");
    if (savedTheme === "light" || savedTheme === "dark") {
      setTheme(savedTheme);
    }
  }, []);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    safeSetItem("claude_jobs_theme", theme);
  }, [theme]);

  // Keyboard shortcuts for inline file search
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "p") {
        e.preventDefault();
        setInlineSearchMode((prev) => prev === "files" ? "off" : "files");
        setInlineSearchQuery("");
        setInlineSearchResults([]);
        setInlineSelectedIndex(0);
        setTimeout(() => inlineSearchRef.current?.focus(), 50);
      }
      if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key === "f") {
        e.preventDefault();
        setInlineSearchMode((prev) => prev === "grep" ? "off" : "grep");
        setInlineSearchQuery("");
        setInlineSearchResults([]);
        setInlineSelectedIndex(0);
        setTimeout(() => inlineSearchRef.current?.focus(), 50);
      }
      // Host key: Cmd/Ctrl+K — kill process dialog (owner-only)
      if ((e.metaKey || e.ctrlKey) && e.key === "k" && !e.shiftKey) {
        e.preventDefault();
        setShowKillDialog((prev) => !prev);
        setKillInput("");
        setKillResult(null);
        setTimeout(() => killInputRef.current?.focus(), 50);
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, []);

  // The read-only /files/* endpoints default-deny unauthenticated callers, so
  // every browse/search fetch must carry the bearer token (local mode excepted).
  const fileAuthHeaders = useCallback(
    (): Record<string, string> =>
      token && token !== "local" ? { Authorization: `Bearer ${token}` } : {},
    [token]
  );

  // Inline search debounce effect
  useEffect(() => {
    if (inlineSearchMode === "off" || !inlineSearchQuery.trim()) {
      setInlineSearchResults([]);
      return;
    }
    const tid = setTimeout(async () => {
      setInlineSearchLoading(true);
      const sd = workDir
        || (selectedJob ? jobs.find((j) => j.id === selectedJob)?.work_dir : "")
        || "~/mod";
      try {
        if (inlineSearchMode === "files") {
          const r = await fetch(
            `${apiUrl}/files/search?path=${encodeURIComponent(sd)}&query=${encodeURIComponent(inlineSearchQuery)}`,
            { headers: fileAuthHeaders() }
          );
          if (r.ok) { const d = await r.json(); setInlineSearchResults(d.results || []); }
        } else {
          const p = new URLSearchParams({ path: sd, query: inlineSearchQuery });
          const r = await fetch(`${apiUrl}/files/grep?${p}`, { headers: fileAuthHeaders() });
          if (r.ok) { const d = await r.json(); setInlineSearchResults(d.matches || []); }
        }
      } catch { /* ignore */ }
      setInlineSearchLoading(false);
      setInlineSelectedIndex(0);
    }, 300);
    return () => clearTimeout(tid);
  }, [inlineSearchQuery, inlineSearchMode, workDir, selectedJob, jobs, apiUrl, fileAuthHeaders]);

  // Close menus when clicking outside
  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (userDetailsRef.current && !userDetailsRef.current.contains(e.target as Node)) {
        setShowUserDetails(false);
      }
      if (tokenStatsRef.current && !tokenStatsRef.current.contains(e.target as Node)) {
        setShowTokenStats(false);
      }
      if (headerModuleRef.current && !headerModuleRef.current.contains(e.target as Node)) {
        setShowHeaderModuleDropdown(false);
        setHeaderModuleSearch("");
      }
      if (
        (!headerCreateRef.current || !headerCreateRef.current.contains(e.target as Node)) &&
        (!composerCreateRef.current || !composerCreateRef.current.contains(e.target as Node))
      ) {
        setShowHeaderCreateForm(null);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  // Load saved model and backend URL
  useEffect(() => {
    const savedModel = localStorage.getItem("claude_jobs_model");
    if (savedModel) {
      // Upgrade old family aliases (opus/sonnet/haiku) to the full model ID
      // so users don't get silently bumped to a different version on update.
      let upgraded = MODEL_ALIAS_UPGRADE[savedModel] || savedModel;
      // Drop any saved value that's no longer a selectable model (e.g. a model
      // pulled for being gated/unavailable) so jobs never run with one.
      if (!MODEL_OPTIONS.some(o => o.value === upgraded)) upgraded = MODEL_OPTIONS[0].value;
      setModel(upgraded);
      if (upgraded !== savedModel) safeSetItem("claude_jobs_model", upgraded);
    }

    const savedAgent = localStorage.getItem("claude_jobs_agent");
    if (savedAgent) setAgentType(savedAgent);

    // Load the system-prompt chain; migrate the legacy single saved prompt
    // into it as the first block (already on) so it keeps applying.
    let loadedChain: SysPromptBlock[] = [];
    try {
      const rawChain = localStorage.getItem("claude_system_prompts");
      if (rawChain) loadedChain = JSON.parse(rawChain);
    } catch {}
    if (!loadedChain.length) {
      const legacy = localStorage.getItem("claude_system_prompt");
      if (legacy && legacy.trim()) {
        loadedChain = [{ id: "migrated", name: "SAVED", text: legacy, on: true }];
        safeSetItem("claude_system_prompts", JSON.stringify(loadedChain));
        try { localStorage.removeItem("claude_system_prompt"); } catch {}
      } else if (localStorage.getItem("claude_system_prompts_seeded") !== "1") {
        // Fresh install: seed the nice default system prompt so the chain is
        // never empty. Only seeded once — if the user later clears it, we
        // don't keep re-adding it.
        loadedChain = [{ id: "default", name: "DEFAULT", text: DEFAULT_SYSTEM_PROMPT, on: true }];
        safeSetItem("claude_system_prompts", JSON.stringify(loadedChain));
        safeSetItem("claude_system_prompts_seeded", "1");
      }
    }
    if (loadedChain.length) setSysPrompts(loadedChain);
    // Restore the PARAMS panel's open/closed preference. With no saved
    // preference, default to open only when a chain is active (so it stays
    // discoverable the first time).
    const savedSystemPromptOpen = localStorage.getItem("claude_system_prompt_open");
    if (savedSystemPromptOpen !== null) {
      setSystemPromptOpen(savedSystemPromptOpen === "1");
    } else if (loadedChain.some(p => p.on)) {
      setSystemPromptOpen(true);
    }

    const savedTermTok = localStorage.getItem("claude_terminal_token");
    const savedTermExp = Number(localStorage.getItem("claude_terminal_token_exp") || "0");
    if (savedTermTok && savedTermExp > Date.now()) {
      setTerminalToken(savedTermTok);
      setTerminalTokenExp(savedTermExp);
    } else {
      localStorage.removeItem("claude_terminal_token");
      localStorage.removeItem("claude_terminal_token_exp");
    }

    // Restore the selected agent; the legacy "claude_jobs_engine" key stored
    // "claude-code", which is what "mod" is — anything unavailable falls back
    // to mod, so tasks always run on a real agent.
    const savedAgentMod = localStorage.getItem("claude_jobs_agent_mod");
    if (savedAgentMod && AGENT_MODS.some(a => a.value === savedAgentMod && a.available)) {
      setAgentMod(savedAgentMod);
    }

    const savedUrl = localStorage.getItem("claude_backend_url");
    if (savedUrl) setApiUrl(savedUrl);

    // Load saved prompts
    try {
      const raw = localStorage.getItem("claude_saved_prompts");
      if (raw) setSavedPrompts(JSON.parse(raw));
    } catch {}

    // Load saved personalities (merge with builtins)
    try {
      const raw = localStorage.getItem("claude_personalities");
      if (raw) {
        const custom: Personality[] = JSON.parse(raw);
        // Merge: builtins + custom, custom overrides builtin prompts if same id
        const merged = DEFAULT_PERSONALITIES.map(bp => {
          const override = custom.find(c => c.id === bp.id);
          return override ? { ...bp, prompt: override.prompt, icon: override.icon, name: override.name } : bp;
        });
        const customOnly = custom.filter(c => !DEFAULT_PERSONALITIES.some(bp => bp.id === c.id));
        setPersonalities([...merged, ...customOnly]);
      }
    } catch {}
  }, []);

  // Load saved sidebar state
  useEffect(() => {
    const savedSidebar = localStorage.getItem("claude_tasks_sidebar_open");
    if (savedSidebar !== null) setTasksSidebarOpen(savedSidebar === "true");
    const savedWidth = localStorage.getItem("claude_tasks_sidebar_width");
    if (savedWidth) setSidebarWidth(parseInt(savedWidth, 10));
    const savedLeft = localStorage.getItem("claude_left_sidebar_open");
    if (savedLeft !== null) setLeftSidebarOpen(savedLeft === "true");
    const savedLeftW = localStorage.getItem("claude_left_sidebar_width");
    if (savedLeftW) setLeftSidebarWidth(parseInt(savedLeftW, 10));
    const savedSide = localStorage.getItem("claude_sidebar_side");
    if (savedSide === "left" || savedSide === "right") setSidebarSide(savedSide);
    const savedAccountTab = localStorage.getItem("claude_account_tab");
    if (savedAccountTab === "owner") setAccountTab("access"); // legacy pre-tabs value
    else if (savedAccountTab === "wallet" || savedAccountTab === "access" || savedAccountTab === "sudo" || savedAccountTab === "module")
      setAccountTab(savedAccountTab);
    const savedOwnerOpen = localStorage.getItem("claude_owner_sidebar_open");
    if (savedOwnerOpen !== null) setShowOwnerSidebar(savedOwnerOpen === "true");
    const savedOwnerWidth = localStorage.getItem("claude_owner_sidebar_width");
    if (savedOwnerWidth) setOwnerSidebarWidth(parseInt(savedOwnerWidth, 10));
  }, []);

  useEffect(() => {
    safeSetItem("claude_tasks_sidebar_open", String(tasksSidebarOpen));
  }, [tasksSidebarOpen]);

  useEffect(() => {
    safeSetItem("claude_tasks_sidebar_width", String(sidebarWidth));
  }, [sidebarWidth]);

  useEffect(() => {
    safeSetItem("claude_left_sidebar_open", String(leftSidebarOpen));
  }, [leftSidebarOpen]);

  useEffect(() => {
    safeSetItem("claude_left_sidebar_width", String(leftSidebarWidth));
  }, [leftSidebarWidth]);

  useEffect(() => {
    safeSetItem("claude_sidebar_side", sidebarSide);
  }, [sidebarSide]);

  useEffect(() => {
    safeSetItem("claude_account_tab", accountTab);
  }, [accountTab]);

  useEffect(() => {
    safeSetItem("claude_owner_sidebar_open", String(showOwnerSidebar));
  }, [showOwnerSidebar]);

  useEffect(() => {
    safeSetItem("claude_owner_sidebar_width", String(ownerSidebarWidth));
  }, [ownerSidebarWidth]);

  // Surface the sign-in drawer when an EXISTING session drops (sign-out or
  // expiry). Initial load with no token stays on the hub — signed-out visitors
  // browse as read-only guests and opt into signing in via the edge tab.
  const hadTokenRef = useRef(false);
  useEffect(() => {
    if (token) {
      hadTokenRef.current = true;
    } else if (hadTokenRef.current) {
      hadTokenRef.current = false;
      setSignInOpen(true);
    }
  }, [token]);

  // Check saved token or detect local mode
  useEffect(() => {
    const saved = localStorage.getItem("claude_jobs_token");
    const savedAddr = localStorage.getItem("claude_jobs_address");
    const savedWalletType = localStorage.getItem("claude_jobs_wallet_type") as typeof walletType;
    const dropLocalSession = () => {
      localStorage.removeItem("claude_jobs_token");
      localStorage.removeItem("claude_jobs_address");
      localStorage.removeItem("claude_jobs_wallet_type");
    };
    if (saved && savedAddr) {
      // A persisted "local" session is only valid while the server really runs
      // with auth disabled — an old probe bug used to persist it against an
      // authed server, leaving every authed call to 401. Re-verify, self-heal.
      if (saved === "local") {
        fetch(`${apiUrl}/health`)
          .then((r) => r.json())
          .then((d) => {
            if (d && d.local === true) {
              setToken(saved);
              setAddress(savedAddr);
              setWalletType(savedWalletType);
            } else {
              dropLocalSession();
            }
          })
          .catch(() => { /* server unreachable — keep the auth screen */ });
        return;
      }
      setToken(saved);
      setAddress(savedAddr);
      setWalletType(savedWalletType);
      return;
    }
    // The user explicitly signed out — stay on the auth screen
    if (localStorage.getItem("claude_jobs_signed_out") === "1") return;
    // Probe server — only /health's explicit `local` flag means auth is off.
    // Never infer local mode from an unauthed read succeeding: /jobs is a
    // public ledger, so that heuristic false-positives on every real server.
    fetch(`${apiUrl}/health`)
      .then((r) => r.json())
      .then((d) => {
        if (d && d.local === true) {
          setToken("local");
          setAddress("local");
          setWalletType("local");
          safeSetItem("claude_jobs_token", "local");
          safeSetItem("claude_jobs_address", "local");
          safeSetItem("claude_jobs_wallet_type", "local");
        }
      })
      .catch(() => { /* server not reachable, show auth screen */ });
  }, []);

  // Load token stats when address changes
  useEffect(() => {
    if (address && address !== "local") {
      loadTokenStats();
    }
  }, [address]);

  // Poll the snapshot chain for the currently-selected module so the
  // versions count badge stays fresh without opening the full VersionsPanel.
  useEffect(() => {
    const mod = selectedModule || "claude";
    let cancelled = false;
    const load = async () => {
      try {
        const r = await fetch(`${apiUrl}/modules/${encodeURIComponent(mod)}/versions`);
        const d = await r.json();
        if (cancelled) return;
        setAgentVersions(Array.isArray(d.versions) ? d.versions.slice().reverse() : []);
      } catch {
        if (!cancelled) setAgentVersions([]);
      }
    };
    load();
    const t = setInterval(load, 30_000);
    return () => { cancelled = true; clearInterval(t); };
  }, [apiUrl, selectedModule]);

  // Load the whitelist on mount + after any owner-driven mutation.
  const loadWhitelist = useCallback(async () => {
    try {
      const r = await fetch(`${apiUrl}/whitelist`);
      const d = await r.json();
      setWhitelist(Array.isArray(d.whitelist) ? d.whitelist : []);
    } catch {
      setWhitelist([]);
    }
  }, [apiUrl]);
  useEffect(() => { loadWhitelist(); }, [loadWhitelist]);

  const addToWhitelist = useCallback(async (addr: string) => {
    const clean = addr.trim().toLowerCase();
    if (!/^0x[0-9a-f]{40}$/.test(clean)) {
      setWhitelistError("address must be 0x + 40 hex chars");
      return;
    }
    setWhitelistBusy(true);
    setWhitelistError(null);
    try {
      const r = await fetch(`${apiUrl}/whitelist`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify({ address: clean }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
      setWhitelist(Array.isArray(d.whitelist) ? d.whitelist : []);
      setWhitelistInput("");
    } catch (e) {
      setWhitelistError((e as Error).message);
    } finally {
      setWhitelistBusy(false);
    }
  }, [apiUrl, token]);

  const removeFromWhitelist = useCallback(async (addr: string) => {
    setWhitelistBusy(true);
    setWhitelistError(null);
    try {
      const r = await fetch(`${apiUrl}/whitelist/${encodeURIComponent(addr)}`, {
        method: "DELETE",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
      setWhitelist(Array.isArray(d.whitelist) ? d.whitelist : []);
    } catch (e) {
      setWhitelistError((e as Error).message);
    } finally {
      setWhitelistBusy(false);
    }
  }, [apiUrl, token]);

  // ── Grants: owner mints / lists / revokes time-boxed edit invites ──
  const loadGrants = useCallback(async () => {
    if (!token || !isOwner) { setGrants([]); setGrantRedemptions([]); return; }
    try {
      const r = await fetch(`${apiUrl}/grants`, { headers: { Authorization: `Bearer ${token}` } });
      if (!r.ok) return;
      const d = await r.json();
      setGrants(Array.isArray(d.grants) ? d.grants : []);
      setGrantRedemptions(Array.isArray(d.redemptions) ? d.redemptions : []);
    } catch { /* transient */ }
  }, [apiUrl, token, isOwner]);
  useEffect(() => { loadGrants(); }, [loadGrants]);

  // Generate a short, URL-safe key locally (the "further restriction").
  const genKey = useCallback(() => {
    const bytes = new Uint8Array(8);
    (globalThis.crypto || (window as any).crypto).getRandomValues(bytes);
    const k = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
    setGrantKey(k);
  }, []);

  const createGrant = useCallback(async () => {
    setGrantBusy(true);
    setGrantError(null);
    try {
      const key = grantKey.trim();
      const r = await fetch(`${apiUrl}/grants`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify({ ttl: grantTtl, key: key || undefined, label: grantLabel.trim() || undefined }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
      setActiveGrant({ id: d.id, exp: d.exp, key: key || undefined, key_required: !!d.key_required });
      await loadGrants();
    } catch (e) {
      setGrantError((e as Error).message);
    } finally {
      setGrantBusy(false);
    }
  }, [apiUrl, token, grantTtl, grantKey, grantLabel, loadGrants]);

  const revokeGrant = useCallback(async (id: string) => {
    setGrantBusy(true);
    setGrantError(null);
    try {
      const r = await fetch(`${apiUrl}/grants/${encodeURIComponent(id)}`, {
        method: "DELETE",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!r.ok) { const d = await safeJson(r); throw new Error(d.error || `HTTP ${r.status}`); }
      if (activeGrant?.id === id) setActiveGrant(null);
      await loadGrants();
    } catch (e) {
      setGrantError((e as Error).message);
    } finally {
      setGrantBusy(false);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiUrl, token, activeGrant, loadGrants]);

  // Build the invite URL a grantee opens. The id (a capability) travels in the
  // QR; the key never does — it's shared out of band, so a leaked QR is inert.
  const grantInviteUrl = useCallback((id: string) => {
    const origin = typeof window !== "undefined" ? window.location.origin : "";
    return `${origin}${DEFAULT_BASE_PATH}?grant=${encodeURIComponent(id)}`;
  }, []);

  const copyGrantBit = useCallback((label: string, value: string) => {
    navigator.clipboard?.writeText(value).catch(() => {});
    setGrantCopied(label);
    setTimeout(() => setGrantCopied((c) => (c === label ? null : c)), 1400);
  }, []);

  // Tick a clock once a second so grant countdowns stay live.
  useEffect(() => {
    const t = setInterval(() => setNowSec(Math.floor(Date.now() / 1000)), 1000);
    return () => clearInterval(t);
  }, []);

  // Capture an inbound `?grant=<id>` invite once on mount.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const p = params.get("grant");
    if (p) setPendingGrant(p);
    // `?import=<cid>` — a scanned share-QR lands here: open the Add-module
    // form prefilled with the snapshot CID (+ optional suggested name), so
    // scanning a code on another machine is all it takes to pull a module in.
    const imp = params.get("import");
    if (imp) {
      setAddSource("cid");
      setAddCid(imp);
      const n = params.get("name");
      if (n) setAddName(n);
      setAddOpen(true);
    }
  }, []);

  // ── Share-anything QR: open the overlay with non-empty payloads ─────
  const openQrShare = useCallback(
    (title: string, options: Array<{ label: string; value?: string | null; hint?: string }>) => {
      const opts = options.filter((o): o is { label: string; value: string; hint?: string } => !!o.value);
      if (!opts.length) return;
      setQrShare({ title, options: opts });
      setQrShareIdx(0);
      setQrShareCopied(false);
    },
    []
  );

  // Share a module: its public app link, an import deep-link (opens this
  // console with the Add-module form prefilled), and the raw snapshot CID.
  const shareModuleQr = useCallback(
    (name: string, cid?: string | null) => {
      const origin = typeof window !== "undefined" ? window.location.origin : "";
      openQrShare(`Share · ${name}`, [
        { label: "App", value: `${origin}/${name}`, hint: "Public app link — scan to open this module" },
        {
          label: "Import",
          value: cid
            ? `${origin}${DEFAULT_BASE_PATH}?import=${encodeURIComponent(cid)}&name=${encodeURIComponent(name)}`
            : null,
          hint: "Scan to open this console with the import form prefilled",
        },
        { label: "CID", value: cid, hint: "Raw snapshot CID" },
      ]);
    },
    [openQrShare]
  );

  // Share a hash/CID: import deep-link first (most useful on a phone), then
  // the raw hash, then an IPFS gateway link when the caller has one.
  const shareCidQr = useCallback(
    (cid: string, name?: string | null, gateway?: string | null) => {
      const origin = typeof window !== "undefined" ? window.location.origin : "";
      const nameQ = name ? `&name=${encodeURIComponent(name)}` : "";
      openQrShare(name ? `Share · ${name} snapshot` : "Share · snapshot", [
        {
          label: "Import",
          value: `${origin}${DEFAULT_BASE_PATH}?import=${encodeURIComponent(cid)}${nameQ}`,
          hint: "Scan to open this console with the import form prefilled",
        },
        { label: "CID", value: cid, hint: "Raw snapshot CID / hash" },
        { label: "Gateway", value: gateway, hint: "IPFS gateway link" },
      ]);
    },
    [openQrShare]
  );

  // Human countdown for grant/redemption expiries (shared by the whitelist
  // timed rows and the share-access card).
  const fmtTimeLeft = useCallback((exp: number) => {
    let s = Math.max(0, exp - nowSec);
    if (s >= 86400) return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
    if (s >= 3600) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
    if (s >= 60) return `${Math.floor(s / 60)}m ${s % 60}s`;
    return `${s}s`;
  }, [nowSec]);

  // ── Phone sign-in (session handoff) ────────────────────────────────

  // Mint a single-use handoff code for THIS session's identity. The code is
  // a capability for the whole identity, so it's rendered as a QR locally
  // (qrSvg) and never touches any third-party service.
  const createHandoff = useCallback(async () => {
    if (!token) return;
    setHandoffBusy(true);
    setHandoffError(null);
    try {
      const r = await fetch(`${apiUrl}/auth/handoff`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || "HANDOFF MINT FAILED");
      if (!d.code || typeof d.exp !== "number") throw new Error("INVALID HANDOFF RESPONSE");
      setHandoff({ code: d.code, exp: d.exp });
    } catch (e) {
      setHandoffError((e as Error).message);
    } finally {
      setHandoffBusy(false);
    }
  }, [apiUrl, token]);

  const handoffUrl = useCallback((code: string) => {
    const origin = typeof window !== "undefined" ? window.location.origin : "";
    return `${origin}${DEFAULT_BASE_PATH}?handoff=${encodeURIComponent(code)}`;
  }, []);

  // Redeem an inbound `?handoff=<code>` once on mount: trade the code for a
  // bearer token as the minting session's address — the phone signs in with
  // no wallet and no signature. The param is stripped from the URL/history
  // immediately (the code is single-use, but don't leave it lying around).
  useEffect(() => {
    if (typeof window === "undefined") return;
    const code = new URLSearchParams(window.location.search).get("handoff");
    if (!code) return;
    const url = new URL(window.location.href);
    url.searchParams.delete("handoff");
    window.history.replaceState({}, "", url.toString());
    (async () => {
      setAuthLoading(true);
      setAuthError(null);
      try {
        const r = await fetch(`${apiUrl}/auth/handoff/redeem`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ code }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.error || "SIGN-IN CODE REJECTED");
        if (!d.token || !d.address) throw new Error("INVALID HANDOFF RESPONSE");
        setToken(d.token);
        setAddress(d.address);
        setWalletType(null);
        localStorage.removeItem("claude_jobs_signed_out");
        localStorage.removeItem("claude_jobs_wallet_type");
        safeSetItem("claude_jobs_token", d.token);
        safeSetItem("claude_jobs_address", d.address);
      } catch (e) {
        const msg = (e as Error).message;
        setAuthError(msg === "Load failed" || msg === "Failed to fetch" ? "API OFFLINE — start the backend first" : msg);
      } finally {
        setAuthLoading(false);
      }
    })();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Reflect the configured owner (config.json, exposed via the API /owner
  // endpoint) and treat the connected wallet as owner only when it matches.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${apiUrl}/owner`);
        if (!res.ok) return;
        const data = await res.json();
        const owner = data?.owner ? String(data.owner).toLowerCase() : null;
        if (cancelled) return;
        setOwnerAddress(owner);
        setIsOwner(!!(owner && address && address.toLowerCase() === owner));
      } catch { /* keep previous owner state on transient errors */ }
    })();
    return () => { cancelled = true; };
  }, [address, apiUrl]);

  // ── Auth ──────────────────────────────────────────────────────────

  const safeJson = async (res: Response) => {
    const text = await res.text();
    if (!text) return {};
    try { return JSON.parse(text); } catch { return {}; }
  };

  const signChallenge = async (addr: string, signFn: (msg: string) => Promise<string>) => {
    // Check owner status before authentication
    let wasOwnerSet = false;
    try {
      const ownerRes = await fetch(`${apiUrl}/owner`);
      const ownerData = await safeJson(ownerRes);
      wasOwnerSet = !!ownerData.has_owner;
    } catch { /* owner endpoint not available, skip */ }

    // Non-owner wallets must accept the Terms of Use on first sign-in. The
    // server embeds the terms in the challenge so the signature covers them;
    // here we show them and get explicit agreement before asking the wallet.
    let termsInfo: { title?: string; text: string } | null = null;
    try {
      const tr = await fetch(`${apiUrl}/auth/terms?address=${addr}`);
      const td = await safeJson(tr);
      if (tr.ok && td.required && td.text) termsInfo = td;
    } catch { /* endpoint unavailable — the server still enforces on verify */ }
    if (termsInfo) {
      const agreed = await new Promise<boolean>((resolve) => {
        termsResolveRef.current = resolve;
        setTermsPrompt({ title: termsInfo!.title || "Terms of Use", text: termsInfo!.text });
      });
      setTermsPrompt(null);
      termsResolveRef.current = null;
      if (!agreed) throw new Error("TERMS DECLINED — SIGN-IN CANCELLED");
    }

    const challengeRes = await fetch(
      `${apiUrl}/auth/challenge?address=${addr}`
    );
    if (!challengeRes.ok) throw new Error("CHALLENGE REQUEST FAILED");
    const { message } = await safeJson(challengeRes);
    if (!message) throw new Error("INVALID CHALLENGE RESPONSE");

    const signature = await signFn(message);

    // If the visitor arrived via a QR edit-invite, thread its id + key so the
    // API redeems the grant during verify and lets a non-whitelisted address in.
    const grant = pendingGrantRef.current;
    const verifyRes = await fetch(`${apiUrl}/auth/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        address: addr,
        signature,
        message,
        ...(grant ? { grant: grant.id, grant_key: grant.key || undefined } : {}),
      }),
    });

    const verifyData = await safeJson(verifyRes);
    if (!verifyRes.ok) {
      throw new Error(verifyData.error || "SIGNATURE VERIFICATION FAILED");
    }
    // Invite consumed — clear it so a later re-sign doesn't re-thread it.
    if (grant) { pendingGrantRef.current = null; setPendingGrant(null); }

    const { token: newToken, address: verifiedAddr } = verifyData;
    if (!newToken || !verifiedAddr) throw new Error("INVALID VERIFY RESPONSE");
    setToken(newToken);
    setAddress(verifiedAddr);
    localStorage.removeItem("claude_jobs_signed_out");
    safeSetItem("claude_jobs_token", newToken);
    safeSetItem("claude_jobs_address", verifiedAddr);

    // Check if this user became the owner
    if (!wasOwnerSet) {
      try {
        const newOwnerRes = await fetch(`${apiUrl}/owner`);
        const newOwnerData = await safeJson(newOwnerRes);
        if (newOwnerData.has_owner && newOwnerData.owner === verifiedAddr) {
          console.log("✓ You are now the owner of this Claude instance");
        }
      } catch { /* ignore */ }
    }
  };

  const connectWallet = async (type: "metamask" | "subwallet") => {
    setAuthLoading(true);
    setAuthError(null);
    try {
      const ethereum = (window as any).ethereum;

      // For SubWallet, request specific provider
      if (type === "subwallet" && ethereum.providers) {
        const subwalletProvider = ethereum.providers.find((p: any) => p.isSubWallet);
        if (!subwalletProvider) throw new Error("SUBWALLET NOT FOUND");
        const accounts: string[] = await subwalletProvider.request({
          method: "eth_requestAccounts",
        });
        if (!accounts.length) throw new Error("NO ACCOUNTS FOUND");
        const addr = accounts[0].toLowerCase();

        await signChallenge(addr, async (msg) => {
          return await subwalletProvider.request({
            method: "personal_sign",
            params: [msg, addr],
          });
        });
        setWalletType("subwallet");
        safeSetItem("claude_jobs_wallet_type", "subwallet");
      } else {
        // MetaMask or default provider
        const accounts: string[] = await ethereum.request({
          method: "eth_requestAccounts",
        });
        if (!accounts.length) throw new Error("NO ACCOUNTS FOUND");
        const addr = accounts[0].toLowerCase();

        await signChallenge(addr, async (msg) => {
          return await ethereum.request({
            method: "personal_sign",
            params: [msg, addr],
          });
        });
        setWalletType("metamask");
        safeSetItem("claude_jobs_wallet_type", "metamask");
      }
    } catch (e: any) {
      const msg = e.message || "";
      setAuthError(msg === "Load failed" || msg === "Failed to fetch" ? "API OFFLINE — start the backend first" : friendlyWalletError(e) || "AUTHENTICATION FAILED");
    } finally {
      setAuthLoading(false);
    }
  };

  // Redeem an inbound QR edit-invite: stash {id,key} where signChallenge reads
  // it, then run the normal wallet sign-in (which threads the grant to verify).
  const redeemInvite = useCallback(async (walletKind: "metamask" | "subwallet" = "metamask") => {
    if (!pendingGrant) return;
    pendingGrantRef.current = { id: pendingGrant, key: redeemKey.trim() };
    await connectWallet(walletKind);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingGrant, redeemKey]);

  // Walletless entry: trade the grant id (+ optional key) for a guest bearer
  // token via the public redeem endpoint. No wallet, no signature — possession
  // of the QR is the capability; access ends when the grant expires.
  const enterAsGuest = useCallback(async () => {
    if (!pendingGrant) return;
    setAuthLoading(true);
    setAuthError(null);
    try {
      const r = await fetch(`${apiUrl}/grants/${encodeURIComponent(pendingGrant)}/redeem`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: redeemKey.trim() || undefined }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || "INVITE REDEMPTION FAILED");
      if (!d.token || !d.address) throw new Error("INVALID REDEEM RESPONSE");
      setToken(d.token);
      setAddress(d.address);
      setWalletType(null);
      setGuestExp(typeof d.exp === "number" ? d.exp : null);
      localStorage.removeItem("claude_jobs_signed_out");
      safeSetItem("claude_jobs_token", d.token);
      safeSetItem("claude_jobs_address", d.address);
      if (typeof d.exp === "number") safeSetItem("claude_jobs_guest_exp", String(d.exp));
      setPendingGrant(null);
      pendingGrantRef.current = null;
    } catch (e) {
      const msg = (e as Error).message;
      setAuthError(msg === "Load failed" || msg === "Failed to fetch" ? "API OFFLINE — start the backend first" : msg);
    } finally {
      setAuthLoading(false);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiUrl, pendingGrant, redeemKey]);

  // Restore a guest session's expiry across reloads; drop it for wallet users.
  useEffect(() => {
    if (address && address.startsWith("guest_")) {
      const saved = Number(localStorage.getItem("claude_jobs_guest_exp") || "");
      setGuestExp(Number.isFinite(saved) && saved > 0 ? saved : null);
    } else {
      setGuestExp(null);
    }
  }, [address]);

  // Guest access is time-boxed server-side; mirror it client-side so the UI
  // signs out the moment the grant window closes instead of on the next 401.
  useEffect(() => {
    if (!address || !address.startsWith("guest_") || !guestExp) return;
    if (nowSec >= guestExp) disconnect();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nowSec, address, guestExp]);

  const connectWithPassword = async (password: string) => {
    setAuthLoading(true);
    setAuthError(null);
    try {
      const { ethers } = await import("ethers");
      // Derive a deterministic private key from the password
      const hash = ethers.id(password); // keccak256
      const wallet = new ethers.Wallet(hash);
      const addr = wallet.address.toLowerCase();

      await signChallenge(addr, async (msg) => {
        return await wallet.signMessage(msg);
      });
      setWalletType("password");
      safeSetItem("claude_jobs_wallet_type", "password");
      // These are throwaway wallets — keep the password around so the ACCOUNT
      // panel can reveal/copy it (and the derived private key) at any time.
      safeSetItem("claude_jobs_password", password);
    } catch (e: any) {
      const msg = e.message || "";
      setAuthError(msg === "Load failed" || msg === "Failed to fetch" ? "API OFFLINE — start the backend first" : msg || "PASSWORD KEY DERIVATION FAILED");
    } finally {
      setAuthLoading(false);
    }
  };

  const connectLocal = async () => {
    setAuthLoading(true);
    setAuthError(null);
    try {
      const { ethers } = await import("ethers");

      let mnemonic = localStorage.getItem("claude_jobs_seed");
      let isNew = false;
      if (!mnemonic) {
        const wallet = ethers.Wallet.createRandom();
        mnemonic = wallet.mnemonic!.phrase;
        safeSetItem("claude_jobs_seed", mnemonic);
        isNew = true;
      }

      const wallet = ethers.Wallet.fromPhrase(mnemonic);
      const addr = wallet.address.toLowerCase();

      await signChallenge(addr, async (msg) => {
        return await wallet.signMessage(msg);
      });

      setWalletType("local");
      safeSetItem("claude_jobs_wallet_type", "local");

      if (isNew) {
        console.log(
          "%c[MOD AI] New local wallet created. Back up your seed phrase from localStorage key 'claude_jobs_seed'",
          "color: #ffb000"
        );
      }
    } catch (e: any) {
      const msg = e.message || "";
      setAuthError(msg === "Load failed" || msg === "Failed to fetch" ? "API OFFLINE — start the backend first" : msg || "LOCAL KEY GENERATION FAILED");
    } finally {
      setAuthLoading(false);
    }
  };

  const disconnect = () => {
    setToken(null);
    setAddress(null);
    setWalletType(null);
    setJobs([]);
    setSelectedJob(null);
    setStreamOutput("");
    setTokenStats(null);
    setGuestExp(null);
    localStorage.removeItem("claude_jobs_token");
    localStorage.removeItem("claude_jobs_address");
    localStorage.removeItem("claude_jobs_wallet_type");
    localStorage.removeItem("claude_jobs_guest_exp");
    // Persist the sign-out so the local-mode probe on next load
    // doesn't silently reconnect us.
    safeSetItem("claude_jobs_signed_out", "1");
    if (esRef.current) esRef.current.close();
  };

  // ── Token Stats ───────────────────────────────────────────────────

  const loadTokenStats = async () => {
    if (!address || address === "local") return;

    setLoadingTokenStats(true);
    try {
      const { ethers } = await import("ethers");
      const ethereum = (window as any).ethereum;

      if (!ethereum) {
        setTokenStats({
          balance: "0.00",
          symbol: "ETH",
          decimals: 18,
          address: address,
          network: "Unknown",
        });
        return;
      }

      const provider = new ethers.BrowserProvider(ethereum);
      const balance = await provider.getBalance(address);
      const network = await provider.getNetwork();

      setTokenStats({
        balance: ethers.formatEther(balance),
        symbol: "ETH",
        decimals: 18,
        address: address,
        network: network.name || `Chain ${network.chainId}`,
      });
    } catch (e) {
      console.error("Failed to load token stats:", e);
      setTokenStats({
        balance: "0.00",
        symbol: "ETH",
        decimals: 18,
        address: address || "",
        network: "Unknown",
      });
    } finally {
      setLoadingTokenStats(false);
    }
  };

  // ── Authed Fetch ──────────────────────────────────────────────────

  const authFetch = useCallback(
    async (path: string, opts: RequestInit = {}, timeoutMs: number = 60000) => {
      if (!token) throw new Error("NOT AUTHENTICATED");
      const headers: Record<string, string> = {
        ...((opts.headers as Record<string, string>) || {}),
        "Content-Type": "application/json",
      };
      // In local mode, no bearer token needed
      if (token !== "local") {
        headers["Authorization"] = `Bearer ${token}`;
      }

      // Add abort signal with custom timeout (default 60 seconds)
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

      try {
        const response = await fetch(`${apiUrl}${path}`, {
          ...opts,
          headers,
          signal: controller.signal
        });
        clearTimeout(timeoutId);
        // A 401 with a token means the server no longer honors it (expired or
        // the API restarted with a new signing secret) — drop the session so
        // the sign-in screen comes back instead of every action failing.
        if (response.status === 401 && token !== "local") {
          disconnect();
          setAuthError("SESSION EXPIRED — SIGN IN AGAIN");
        }
        return response;
      } catch (error) {
        clearTimeout(timeoutId);
        throw error;
      }
    },
    [token, apiUrl]
  );

  // Pull the sudo session + policy so the ACCOUNT card and the Sudo sheet can
  // show what one signature actually buys. Owner-only server-side; anyone else
  // (or local mode) just clears the card.
  const refreshSudoStatus = useCallback(async () => {
    try {
      const r = await authFetch("/sudo/status");
      if (!r.ok) { setSudoInfo(null); return; }
      const d = await r.json();
      if (d?.local) { setSudoInfo(null); return; }
      setSudoInfo({
        active: !!d.session_active,
        expires: d.expires ?? null,
        sessionSecs: d.policy?.session_secs ?? 3600,
        alwaysAsk: Array.isArray(d.policy?.always_ask) ? d.policy.always_ask : [],
      });
    } catch {
      /* transient — keep whatever we last knew */
    }
  }, [authFetch]);

  // Keep the sudo card honest: fetch when the owner opens ACCOUNT (and once on
  // owner sign-in so the Sudo sheet can announce the session it will open).
  useEffect(() => {
    if (token && token !== "local" && isOwner) refreshSudoStatus();
  }, [token, isOwner, showOwnerSidebar, refreshSudoStatus]);

  // ── Dynamic API Lifecycle ───────────────────────────────────────────

  const touchApiActivity = useCallback(() => {
    apiLastActivity.current = Date.now();
  }, []);

  const checkApiHealth = useCallback(async (): Promise<boolean> => {
    try {
      const res = await fetch(`${apiUrl}/health`, { signal: AbortSignal.timeout(2000) });
      return res.ok;
    } catch {
      return false;
    }
  }, [apiUrl]);

  const startApiServer = useCallback(async (): Promise<boolean> => {
    try {
      const res = await fetch(`${DEFAULT_BASE_PATH}/api/service`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "start",
          type: "api",
          port: API_PORT,
          workDir: `${anchorDir.replace("~", process.env.HOME || "/Users/broski")}/mod/orbit/claude/src/api`,
        }),
      });
      const data = await res.json();
      return data.ok && data.running;
    } catch {
      return false;
    }
  }, [apiUrl, anchorDir]);

  const stopApiServer = useCallback(async () => {
    try {
      const port = API_PORT;
      await fetch(`${DEFAULT_BASE_PATH}/api/service`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "stop", port }),
      });
      setApiStatus("off");
    } catch { /* ignore */ }
  }, [apiUrl]);

  const ensureApi = useCallback(async (): Promise<boolean> => {
    touchApiActivity();
    if (await checkApiHealth()) {
      setApiStatus("on");
      return true;
    }
    setApiStatus("starting");
    // Try starting via start.sh (the Rust binary)
    const apiDir = `${anchorDir.replace("~", process.env.HOME || "/Users/broski")}/mod/orbit/claude/src/api`;
    try {
      const res = await fetch(`${DEFAULT_BASE_PATH}/api/service`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "start",
          type: "api",
          port: API_PORT,
          workDir: apiDir,
        }),
      });
      const data = await res.json();
      if (data.ok && data.running) {
        // Wait a bit more for the Rust server to be fully ready
        for (let i = 0; i < 10; i++) {
          await new Promise(r => setTimeout(r, 500));
          if (await checkApiHealth()) {
            setApiStatus("on");
            return true;
          }
        }
      }
    } catch { /* fall through */ }
    setApiStatus("off");
    return false;
  }, [apiUrl, anchorDir, checkApiHealth, touchApiActivity]);

  // Idle monitor: check every 30s, shut down if no activity for idleTimeout
  useEffect(() => {
    if (apiStatus !== "on") return;
    const iv = setInterval(async () => {
      const idle = (Date.now() - apiLastActivity.current) / 1000;
      if (idle >= apiIdleTimeout.current && apiLastActivity.current > 0) {
        console.log(`[mod] API idle for ${Math.floor(idle)}s — shutting down`);
        await stopApiServer();
      }
    }, 30000);
    return () => clearInterval(iv);
  }, [apiStatus, stopApiServer]);

  // Check API status on mount
  useEffect(() => {
    checkApiHealth().then(ok => {
      setApiStatus(ok ? "on" : "off");
      if (ok) touchApiActivity();
    });
  }, []);

  // ── Directory Tree ────────────────────────────────────────────────

  const collectDirPaths = (tree: any[]): string[] => {
    const paths: string[] = [];
    for (const item of tree) {
      if (item.type === "directory") {
        paths.push(item.path);
        if (item.children) paths.push(...collectDirPaths(item.children));
      }
    }
    return paths;
  };

  // Patch one pane in the split grid
  const patchPane = useCallback((paneId: number, patch: Partial<FilePane>) => {
    setPaneCols(cols => cols.map(c => ({ ...c, panes: c.panes.map(p => p.id === paneId ? { ...p, ...patch } : p) })));
  }, []);

  // Ref mirrors so loadFileContent stays identity-stable — effects depend on
  // it, and a paneCols dep would loop (load → grid change → new fn → re-run).
  const paneColsRef = useRef(paneCols);
  paneColsRef.current = paneCols;
  const activePaneIdRef = useRef(activePaneId);
  activePaneIdRef.current = activePaneId;

  // Load file content into a pane — the target (or active) pane if it still
  // exists, else the first pane; an empty grid bootstraps its first pane.
  const loadFileContent = useCallback(async (filePath: string, targetPaneId?: number) => {
    const allPanes = paneColsRef.current.flatMap(c => c.panes);
    let paneId = targetPaneId ?? activePaneIdRef.current ?? -1;
    if (!allPanes.some(p => p.id === paneId)) paneId = allPanes[0]?.id ?? -1;
    if (paneId === -1) {
      paneId = paneSeq.current++;
      const colId = paneSeq.current++;
      setPaneCols([{ id: colId, panes: [{ id: paneId, file: filePath, content: "", loading: true, collapsed: false }] }]);
    } else {
      patchPane(paneId, { file: filePath, content: "", loading: true, collapsed: false });
    }
    setActivePaneId(paneId);
    setEditingPaneId(prev => (prev === paneId ? null : prev));
    let content = "";
    try {
      const res = await fetch(`${apiUrl}/files/content?path=${encodeURIComponent(filePath)}`, { headers: fileAuthHeaders() });
      if (res.ok) {
        const data = await res.json();
        content = data.error ? `// ${data.error}` : (data.content || "");
      } else {
        content = "// Failed to load file";
      }
    } catch {
      content = "// Error loading file";
    }
    patchPane(paneId, { content, loading: false });
  }, [apiUrl, fileAuthHeaders, patchPane]);

  // Split at a pane: "right" inserts a new column after the pane's column,
  // "down" stacks a new pane below it. Both clone the source pane so the file
  // carries over, and focus moves to the new pane.
  const splitPane = useCallback((paneId: number, dir: "right" | "down") => {
    const newPaneId = paneSeq.current++;
    const newColId = paneSeq.current++;
    setPaneCols(cols => {
      const ci = cols.findIndex(c => c.panes.some(p => p.id === paneId));
      if (ci === -1) return cols;
      const src = cols[ci].panes.find(p => p.id === paneId)!;
      const clone = { ...src, id: newPaneId, collapsed: false };
      if (dir === "down") {
        const panes = [...cols[ci].panes];
        panes.splice(panes.findIndex(p => p.id === paneId) + 1, 0, clone);
        return cols.map((c, i) => (i === ci ? { ...c, panes } : c));
      }
      const next = [...cols];
      next.splice(ci + 1, 0, { id: newColId, panes: [clone] });
      return next;
    });
    setActivePaneId(newPaneId);
  }, []);

  const closePane = useCallback((paneId: number) => {
    const remaining = paneCols.flatMap(c => c.panes).filter(p => p.id !== paneId);
    setPaneCols(cols => cols
      .map(c => ({ ...c, panes: c.panes.filter(p => p.id !== paneId) }))
      .filter(c => c.panes.length > 0));
    setEditingPaneId(prev => (prev === paneId ? null : prev));
    setActivePaneId(prev => (prev === paneId ? (remaining[0]?.id ?? null) : prev));
  }, [paneCols]);

  const saveFile = useCallback(async () => {
    const pane = paneCols.flatMap(c => c.panes).find(p => p.id === editingPaneId);
    if (!pane || !token) return;
    setSavingFile(true);
    try {
      // Editing a module OTHER than claude is privileged: authFetchSudo signs a
      // fresh owner authorization bound to this exact path and retries if the
      // server demands it. Saving inside claude itself needs no extra step.
      const body = JSON.stringify({ path: pane.file, content: editBuffer });
      const res = await authFetchSudo("/files/write", { method: "POST", body });
      if (res.ok) {
        // Every pane showing this file picks up the saved content
        setPaneCols(cols => cols.map(c => ({ ...c, panes: c.panes.map(p => p.file === pane.file ? { ...p, content: editBuffer } : p) })));
        setEditingPaneId(null);
      } else {
        const data = await res.json().catch(() => ({} as any));
        setError(data.error || "Failed to save file");
      }
    } catch (e: any) {
      setError(e?.message || "Error saving file");
    } finally {
      setSavingFile(false);
    }
  }, [paneCols, editingPaneId, editBuffer, token, authFetch, address, walletType]);

  // Navigate file tree to show the parent folder of a file and expand all ancestor dirs
  const navigateToFile = useCallback(async (filePath: string) => {
    // Extract parent directory
    const parentDir = filePath.substring(0, filePath.lastIndexOf("/"));
    if (!parentDir) return;

    // Fetch the tree for the parent directory so we see sibling files
    try {
      const res = await fetch(`${apiUrl}/files/tree?path=${encodeURIComponent(parentDir)}`, { headers: fileAuthHeaders() });
      if (res.ok) {
        const data = await res.json();
        const tree = data.tree || [];
        setDirectoryTree(tree);

        // Expand all ancestor directories by collecting path segments
        const newExpanded = new Set<string>();
        // Build ancestor paths from the parent dir down through the tree
        const collectDirPaths = (nodes: any[]) => {
          for (const node of nodes) {
            if (node.type === "directory") {
              // Check if the file is somewhere inside this directory
              if (filePath.startsWith(node.path + "/") || filePath.startsWith(node.path)) {
                newExpanded.add(node.path);
              }
              if (node.children) collectDirPaths(node.children);
            }
          }
        };
        collectDirPaths(tree);
        setExpandedDirs(newExpanded);
      }
    } catch (e) {
      console.error("Failed to navigate to file:", e);
    }
  }, [apiUrl, fileAuthHeaders]);

  const fetchDirectoryTree = useCallback(async (path?: string) => {
    try {
      // workDir wins over the selected job's work_dir so an explicit module
      // switch in the header always re-syncs FILES; the job dir is only a
      // fallback for the pre-selection initial state.
      const targetPath = path || workDir || (selectedJob ? jobs.find(j => j.id === selectedJob)?.work_dir : null) || "~/mod";
      const res = await fetch(`${apiUrl}/files/tree?path=${encodeURIComponent(targetPath)}`, { headers: fileAuthHeaders() });
      if (res.ok) {
        const data = await res.json();
        const tree = data.tree || [];
        setDirectoryTree(tree);
        setTreeRootHash(data.root_hash || null);
        // /files/tree reports auth/path failures as { tree: [], error } with a
        // 200 status — surface it instead of rendering a silent empty tree.
        setDirectoryTreeError(tree.length === 0 && data.error ? data.error : null);
        // Start with all folders collapsed
        setExpandedDirs(new Set());
        // Auto-select config.json — but only when no pane is open yet, so the
        // periodic re-runs of this fetch (jobs polling changes our deps) never
        // stomp files the user opened, and split panes keep their contents.
        const configFile = tree.find((n: any) => n.type === "file" && n.name === "config.json");
        if (configFile && paneColsRef.current.length === 0) {
          loadFileContent(configFile.path);
        }
      }
    } catch (e) {
      console.error("Failed to fetch directory tree:", e);
    }
  }, [selectedJob, jobs, workDir, apiUrl, loadFileContent, fileAuthHeaders]);

  // Reset the open-task sub-tab to OUTPUT (and re-collapse the prompt)
  // whenever a different task is opened.
  useEffect(() => {
    setTaskDetailTab("output");
    setPromptExpanded(false);
  }, [selectedJob]);

  // Load directory tree on mount and when relevant state changes
  useEffect(() => {
    fetchDirectoryTree();
  }, [fetchDirectoryTree]);

  // Also reload when switching to changelog tab if empty
  useEffect(() => {
    if (moduleTab === "changelog" && changelogEntries.length === 0) {
      fetchChangelog();
    }
  }, [moduleTab]);

  // Handle sidebar resize dragging
  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (isLeftDragging) {
        const newWidth = e.clientX;
        setLeftSidebarWidth(Math.max(280, Math.min(800, newWidth)));
      }
    };

    const handleMouseUp = () => {
      setIsLeftDragging(false);
    };

    if (isLeftDragging) {
      document.addEventListener('mousemove', handleMouseMove);
      document.addEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
    }

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
  }, [isLeftDragging]);

  // Handle OWNER sidebar resize dragging. Sits at the far right; drag handle
  // on the LEFT edge — dragging left grows the panel.
  useEffect(() => {
    if (!isOwnerSidebarDragging) return;
    const handleMouseMove = (e: MouseEvent) => {
      const ww = window.innerWidth;
      const newWidth = ww - e.clientX;
      const maxWidth = Math.max(380, ww * 0.7);
      setOwnerSidebarWidth(Math.max(300, Math.min(maxWidth, newWidth)));
    };
    const handleMouseUp = () => setIsOwnerSidebarDragging(false);
    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
  }, [isOwnerSidebarDragging]);

  // Handle right panel resize dragging
  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (isRightDragging.current) {
        const windowWidth = window.innerWidth;
        const newWidth = windowWidth - e.clientX;
        setRightPanelWidth(Math.max(200, Math.min(windowWidth * 0.6, newWidth)));
      }
    };

    const handleMouseUp = () => {
      if (isRightDragging.current) {
        isRightDragging.current = false;
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
      }
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, []);

  // Handle floating FILES panel drag & resize
  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (filesPanelDrag.current) {
        const d = filesPanelDrag.current;
        setFilesPanelPos({
          x: Math.max(0, Math.min(window.innerWidth - 200, d.origX + e.clientX - d.startX)),
          y: Math.max(0, Math.min(window.innerHeight - 60, d.origY + e.clientY - d.startY)),
        });
      }
      if (filesPanelResize.current) {
        const r = filesPanelResize.current;
        const dx = e.clientX - r.startX;
        const dy = e.clientY - r.startY;
        setFilesPanelSize(prev => ({
          w: r.edge.includes("e") ? Math.max(320, Math.min(window.innerWidth - 40, r.origW + dx)) : prev.w,
          h: r.edge.includes("s") ? Math.max(200, Math.min(window.innerHeight - 40, r.origH + dy)) : prev.h,
        }));
      }
    };
    const handleMouseUp = () => {
      if (filesPanelDrag.current || filesPanelResize.current) {
        filesPanelDrag.current = null;
        filesPanelResize.current = null;
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        setIframesInert(false);
      }
    };
    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, []);

  // Persist floating files panel position/size
  useEffect(() => {
    const saved = localStorage.getItem("claude_files_panel");
    if (saved) {
      try {
        const { pos, size, floating } = JSON.parse(saved);
        if (pos) setFilesPanelPos(pos);
        if (size) setFilesPanelSize(size);
        if (floating !== undefined) setFilesPanelFloating(floating);
      } catch {}
    }
  }, []);

  // Skip the save effect's first run: on mount it still closes over the
  // default state, and writing that would clobber the just-loaded values
  // (StrictMode re-runs effects, so the load effect re-reads storage).
  const filesPanelSaveReady = useRef(false);
  useEffect(() => {
    if (!filesPanelSaveReady.current) { filesPanelSaveReady.current = true; return; }
    safeSetItem("claude_files_panel", JSON.stringify({ pos: filesPanelPos, size: filesPanelSize, floating: filesPanelFloating }));
  }, [filesPanelPos, filesPanelSize, filesPanelFloating]);

  // Handle floating composer dock drag & resize (width only — the bar's
  // height follows its content: params panel, image chips, the band)
  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (composerDrag.current) {
        const d = composerDrag.current;
        setComposerPos({
          x: Math.max(0, Math.min(window.innerWidth - 240, d.origX + e.clientX - d.startX)),
          y: Math.max(0, Math.min(window.innerHeight - 60, d.origY + e.clientY - d.startY)),
        });
      }
      if (composerResize.current) {
        const r = composerResize.current;
        const dx = e.clientX - r.startX;
        if (r.edge === "e") {
          setComposerW(Math.max(360, Math.min(window.innerWidth - 40, r.origW + dx)));
        } else {
          // west edge — grow leftward, keep the right side anchored
          // (cap at the right edge's position so clamping x never lets
          // the width keep growing past it)
          const w = Math.max(360, Math.min(window.innerWidth - 40, Math.min(r.origW - dx, r.origX + r.origW)));
          setComposerW(w);
          setComposerPos((p) => ({ ...p, x: Math.max(0, r.origX + (r.origW - w)) }));
        }
      }
    };
    const handleMouseUp = () => {
      if (composerDrag.current || composerResize.current) {
        composerDrag.current = null;
        composerResize.current = null;
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        setIframesInert(false);
      }
    };
    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, []);

  // Persist floating composer position/width/mode — clamped on load so a
  // stale saved position can never strand the bar off-screen
  useEffect(() => {
    const saved = localStorage.getItem("claude_composer_dock");
    if (!saved) return;
    try {
      const { pos, w, floating, min } = JSON.parse(saved);
      if (w) setComposerW(Math.max(360, Math.min(window.innerWidth - 40, w)));
      if (pos) setComposerPos({
        x: Math.max(0, Math.min(window.innerWidth - 240, pos.x)),
        y: Math.max(0, Math.min(window.innerHeight - 60, pos.y)),
      });
      if (floating !== undefined) setComposerFloating(!!floating);
      if (min !== undefined) setComposerMinimized(!!min);
    } catch {}
  }, []);

  // First run still closes over default state — skip it so the loaded
  // values are never clobbered (see the files-panel note above).
  const composerSaveReady = useRef(false);
  useEffect(() => {
    if (!composerSaveReady.current) { composerSaveReady.current = true; return; }
    safeSetItem("claude_composer_dock", JSON.stringify({ pos: composerPos, w: composerW, floating: composerFloating, min: composerMinimized }));
  }, [composerPos, composerW, composerFloating, composerMinimized]);

  // ── Jobs ──────────────────────────────────────────────────────────

  const fetchJobs = useCallback(async () => {
    if (!token) return;
    try {
      const res = await authFetch("/jobs");
      if (res.status === 401) { disconnect(); return; }
      if (!res.ok) throw new Error("FETCH FAILED");
      const data = await res.json();
      setJobs(data.jobs || []);
      setError(null);
      setApiStatus("on");
      touchApiActivity();
    } catch {
      setApiStatus("off");
      setError("API OFFLINE — will auto-start on next job submit");
    } finally {
      setLoading(false);
    }
  }, [token, authFetch, touchApiActivity]);

  useEffect(() => {
    if (!token) return;
    fetchJobs();
    const iv = setInterval(fetchJobs, 4000);
    return () => clearInterval(iv);
  }, [token, fetchJobs]);

  // ── Personality Management ───────────────────────────────────────────
  const persistPersonalities = (ps: Personality[]) => {
    setPersonalities(ps);
    // Only persist non-default or modified builtins
    const toSave = ps.filter(p => !p.builtin || DEFAULT_PERSONALITIES.find(d => d.id === p.id)?.prompt !== p.prompt);
    safeSetItem("claude_personalities", JSON.stringify(toSave));
  };

  const savePersonality = () => {
    const name = personalityDraft.name.trim();
    if (!name) return;
    if (editingPersonality) {
      persistPersonalities(personalities.map(p =>
        p.id === editingPersonality.id ? { ...p, name, icon: personalityDraft.icon, prompt: personalityDraft.prompt } : p
      ));
    } else {
      const id = `p_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;
      persistPersonalities([...personalities, { id, name, icon: personalityDraft.icon, prompt: personalityDraft.prompt }]);
    }
    setShowPersonalityManager(false);
    setEditingPersonality(null);
    setCreatingPersonality(false);
    setPersonalityDraft({ name: "", icon: ">_", prompt: "" });
  };

  const deletePersonality = (id: string) => {
    const p = personalities.find(x => x.id === id);
    if (p?.builtin) return; // can't delete builtins
    persistPersonalities(personalities.filter(x => x.id !== id));
    if (agentType === id) {
      setAgentType("default");
      safeSetItem("claude_jobs_agent", "default");
    }
  };

  const startEditPersonality = (p: Personality) => {
    setEditingPersonality(p);
    setCreatingPersonality(false);
    setPersonalityDraft({ name: p.name, icon: p.icon, prompt: p.prompt });
    setShowPersonalityManager(true);
  };

  const startNewPersonality = () => {
    setEditingPersonality(null);
    setCreatingPersonality(true);
    setPersonalityDraft({ name: "", icon: "☆", prompt: "" });
    setShowPersonalityManager(true);
  };

  const activePersonality = personalities.find(p => p.id === agentType) || personalities[0];

  // Derive AGENT_OPTIONS from personalities for backward compat
  const AGENT_OPTIONS = personalities.map(p => ({ value: p.id, label: p.name, icon: p.icon }));

  // ── System-prompt chain ────────────────────────────────────────────
  // Every mutation persists immediately — blocks are "included already":
  // no save step, active blocks apply to every task on submit.
  const persistSysPrompts = (ps: SysPromptBlock[]) => {
    setSysPrompts(ps);
    safeSetItem("claude_system_prompts", JSON.stringify(ps));
  };

  const toggleSysPrompt = (id: string) =>
    persistSysPrompts(sysPrompts.map(p => (p.id === id ? { ...p, on: !p.on } : p)));

  const deleteSysPrompt = (id: string) =>
    persistSysPrompts(sysPrompts.filter(p => p.id !== id));

  // Reorder within the chain — order is meaning: blocks concatenate top→down.
  const moveSysPrompt = (id: string, dir: -1 | 1) => {
    const i = sysPrompts.findIndex(p => p.id === id);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= sysPrompts.length) return;
    const next = [...sysPrompts];
    [next[i], next[j]] = [next[j], next[i]];
    persistSysPrompts(next);
  };

  const saveSysPromptDraft = () => {
    const name = sysPromptDraft.name.trim();
    if (!name) return;
    if (editingSysPrompt) {
      persistSysPrompts(sysPrompts.map(p =>
        p.id === editingSysPrompt.id ? { ...p, name, text: sysPromptDraft.text } : p
      ));
    } else {
      const id = `sp-${name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}-${Date.now().toString(36)}`;
      persistSysPrompts([...sysPrompts, { id, name, text: sysPromptDraft.text, on: true }]);
    }
    setEditingSysPrompt(null);
    setCreatingSysPrompt(false);
    setSysPromptDraft({ name: "", text: "" });
  };

  const startEditSysPrompt = (p: SysPromptBlock) => {
    setEditingSysPrompt(p);
    setCreatingSysPrompt(false);
    setSysPromptDraft({ name: p.name, text: p.text });
    setShowSysPromptManager(true);
  };

  const startNewSysPrompt = () => {
    setEditingSysPrompt(null);
    setCreatingSysPrompt(true);
    setSysPromptDraft({ name: "", text: "" });
    setShowSysPromptManager(true);
  };

  // The chain: active blocks concatenated in order — this is what tasks get.
  const activeSysPrompts = sysPrompts.filter(p => p.on && p.text.trim());
  const chainedSystemPrompt = activeSysPrompts.map(p => p.text.trim()).join("\n\n");

  // ── Prompt Management ──────────────────────────────────────────────
  const persistPrompts = (prompts: SavedPrompt[]) => {
    setSavedPrompts(prompts);
    safeSetItem("claude_saved_prompts", JSON.stringify(prompts));
  };

  const savePromptFn = (title: string, body: string, tags?: string[], promptModel?: string, promptAgentType?: string) => {
    const now = Math.floor(Date.now() / 1000);
    const p: SavedPrompt = {
      id: `p_${now}_${Math.random().toString(36).slice(2, 8)}`,
      title: title.trim() || body.slice(0, 40).trim(),
      body,
      pinned: false,
      created_at: now,
      updated_at: now,
      model: promptModel,
      tags: tags?.filter(Boolean),
      agent_type: promptAgentType,
    };
    persistPrompts([p, ...savedPrompts]);
    return p;
  };

  const updatePrompt = (id: string, updates: Partial<SavedPrompt>) => {
    persistPrompts(savedPrompts.map(p =>
      p.id === id ? { ...p, ...updates, updated_at: Math.floor(Date.now() / 1000) } : p
    ));
  };

  const deletePrompt = (id: string) => {
    persistPrompts(savedPrompts.filter(p => p.id !== id));
  };

  const togglePinPrompt = (id: string) => {
    persistPrompts(savedPrompts.map(p =>
      p.id === id ? { ...p, pinned: !p.pinned } : p
    ));
  };

  const loadPromptIntoInput = (p: SavedPrompt) => {
    setPrompt(p.body);
    if (p.model) setModel(p.model);
    if (p.agent_type) setAgentType(p.agent_type);
    setShowPromptManager(false);
    setEditingPrompt(null);
  };

  const startCompose = () => {
    setEditingPrompt(null);
    setPromptDraft({ title: "", body: "", tags: "", agent_type: agentType });
    setShowPromptManager(true);
  };

  const startEditPrompt = (p: SavedPrompt) => {
    setEditingPrompt(p);
    setPromptDraft({ title: p.title, body: p.body, tags: (p.tags || []).join(", "), agent_type: p.agent_type || "default" });
    setShowPromptManager(true);
  };

  const saveDraft = () => {
    const tags = promptDraft.tags.split(",").map(t => t.trim()).filter(Boolean);
    if (editingPrompt) {
      updatePrompt(editingPrompt.id, { title: promptDraft.title, body: promptDraft.body, tags, agent_type: promptDraft.agent_type });
    } else {
      savePromptFn(promptDraft.title, promptDraft.body, tags, model, promptDraft.agent_type);
    }
    setShowPromptManager(false);
    setEditingPrompt(null);
    setPromptDraft({ title: "", body: "", tags: "", agent_type: "default" });
  };

  const saveCurrentAsPrompt = () => {
    if (!prompt.trim()) return;
    savePromptFn("", prompt.trim(), [], model, agentType);
  };

  // Sort: pinned first, then by updated_at desc
  const sortedPrompts = [...savedPrompts].sort((a, b) => {
    if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
    return b.updated_at - a.updated_at;
  });

  const filteredSavedPrompts = promptSearchQuery
    ? sortedPrompts.filter(p => {
        const q = promptSearchQuery.toLowerCase();
        return p.title.toLowerCase().includes(q) || p.body.toLowerCase().includes(q) ||
          (p.tags || []).some(t => t.toLowerCase().includes(q));
      })
    : sortedPrompts;

  useEffect(() => {
    if (outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }
  }, [streamOutput]);

  const submitJob = async () => {
    if (!prompt.trim() || !token) return;
    setSubmitting(true);
    try {
      // Ensure API is running before submitting
      const apiReady = await ensureApi();
      if (!apiReady) {
        setError("API SERVER COULD NOT BE STARTED — check api/start.sh");
        setSubmitting(false);
        return;
      }
      touchApiActivity();
      const body: any = { prompt: prompt.trim(), model };

      // The chained system prompts win; otherwise fall back to an active
      // personality.
      if (chainedSystemPrompt) {
        body.system_prompt = chainedSystemPrompt;
      } else if (activePersonality && activePersonality.prompt) {
        body.system_prompt = activePersonality.prompt;
      }
      if (agentType && agentType !== "default") body.agent_type = agentType;
      if (images.length > 0) body.images = images;

      // Edit mode - edit existing module
      if (creationMode === "edit") {
        // If a module is selected, use that as work_dir
        if (selectedModule.trim()) {
          // Enforce _outer restriction for non-owners
          if (!isOwner && !selectedModule.includes("peers/") && !selectedModule.startsWith("peers.")) {
            setError("NON-OWNERS CAN ONLY EDIT MODULES IN PEERS FOLDER");
            setSubmitting(false);
            return;
          }
          body.work_dir = `${anchorDir}/mod/orbit/${selectedModule}`;
        }
        // Otherwise use the manual work_dir input
        else if (workDir.trim()) {
          body.work_dir = workDir.trim();
        }
      }
      // Fork mode - fork existing module into new name
      else if (creationMode === "fork") {
        if (!moduleName.trim()) {
          setError("MODULE NAME REQUIRED FOR FORK");
          setSubmitting(false);
          return;
        }

        let finalModuleName = moduleName.trim();
        if (!isOwner && !finalModuleName.startsWith("peers/")) {
          finalModuleName = `peers/${finalModuleName}`;
        }

        body.prompt = `Fork the module "${selectedModule}" into a new module called "${finalModuleName}". Copy all source files, config.json, and directory structure. Update any self-references to use the new module name.\n\n${prompt.trim()}`;
        body.module_name = finalModuleName;
        body.creation_mode = "new";
        body.anchor_dir = anchorDir;
        if (selectedModule) body.fork_from = selectedModule;
      }
      // New mode - create new module
      else if (creationMode === "new") {
        if (!moduleName.trim()) {
          setError("MODULE NAME REQUIRED");
          setSubmitting(false);
          return;
        }

        // For non-owners, enforce _outer folder for new modules
        let finalModuleName = moduleName.trim();
        if (!isOwner && !finalModuleName.startsWith("peers/")) {
          finalModuleName = `peers/${finalModuleName}`;
        }

        body.module_name = finalModuleName;
        body.creation_mode = creationMode;
        body.anchor_dir = anchorDir;

        // Add GitHub URL if provided
        if (githubUrl.trim()) {
          body.github_url = githubUrl.trim();
        }
      }

      const res = await authFetch("/jobs", {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(await submitFailReason(res));
      const job = await res.json();
      setPrompt("");
      setImages([]);
      setModuleName("");
      setGithubUrl("");
      setSelectedJob(job.id);
      // Tasks run in the BACKGROUND — don't hijack the current view. The
      // composer's running-tasks strip and the header TASKS badge track
      // progress; click either to open the full-page view.
      fetchJobs();
      startStream(job.id);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSubmitting(false);
    }
  };

  // Minimal job submission used by the profile panel's NEW tab — a plain
  // prompt/model/agent-type task against the default work_dir, bypassing the
  // edit/fork/new-module branching in submitJob() above. Returns true/false
  // instead of relying on the composer's own prompt/model/agentType state.
  const quickSubmitJob = useCallback(async (promptText: string, modelValue: string, agentTypeValue: string): Promise<boolean> => {
    const text = promptText.trim();
    if (!text || !token) return false;
    setSubmitting(true);
    try {
      const apiReady = await ensureApi();
      if (!apiReady) {
        setError("API SERVER COULD NOT BE STARTED — check api/start.sh");
        return false;
      }
      touchApiActivity();
      const body: any = { prompt: text, model: modelValue };
      if (agentTypeValue && agentTypeValue !== "default") body.agent_type = agentTypeValue;
      const res = await authFetch("/jobs", { method: "POST", body: JSON.stringify(body) });
      if (!res.ok) throw new Error(await submitFailReason(res));
      const job = await res.json();
      setSelectedJob(job.id);
      fetchJobs();
      startStream(job.id);
      return true;
    } catch (e: any) {
      setError(e.message);
      return false;
    } finally {
      setSubmitting(false);
    }
  }, [token, ensureApi, touchApiActivity, authFetch, fetchJobs]);

  const startStream = (jobId: string) => {
    if (esRef.current) esRef.current.close();
    // Pre-fill with any existing output so late subscribers see accumulated logs
    const existing = jobs.find(j => j.id === jobId);
    setStreamOutput(existing?.output || "");
    const es = new EventSource(`${apiUrl}/jobs/${jobId}/stream`);
    esRef.current = es;
    es.onmessage = (event) => {
      if (event.data === "[DONE]" || event.data === "[CANCELLED]") { es.close(); fetchJobs(); return; }
      setStreamOutput((prev) => prev + event.data);
    };
    es.addEventListener("complete", (event: any) => {
      setStreamOutput(event.data);
      es.close();
    });
    es.onerror = () => { es.close(); fetchJobs(); };
  };

  const cancelJob = async (id: string) => {
    // Close the stream immediately so UI stops updating
    if (esRef.current) { esRef.current.close(); esRef.current = null; }
    await authFetch(`/jobs/${id}/cancel`, { method: "POST" });
    fetchJobs();
  };

  // Kill process by PID or port (owner-only, host key Cmd+K)
  const executeKill = async () => {
    if (!token || !killInput.trim()) return;
    setKillLoading(true);
    setKillResult(null);
    try {
      const val = parseInt(killInput.trim(), 10);
      if (isNaN(val)) { setKillResult({ error: "Enter a valid number" }); return; }
      const body = killMode === "pid"
        ? { pid: val, signal: killSignal }
        : { port: val, signal: killSignal };
      const res = await authFetch("/kill", { method: "POST", body: JSON.stringify(body) });
      const data = await res.json();
      setKillResult(data);
    } catch (e: any) {
      setKillResult({ error: e.message || "Kill failed" });
    } finally {
      setKillLoading(false);
    }
  };

  const deleteJob = async (id: string) => {
    await authFetch(`/jobs/${id}`, { method: "DELETE" });
    if (selectedJob === id) { setSelectedJob(null); setStreamOutput(""); }
    fetchJobs();
  };

  const [confirmDeleteModule, setConfirmDeleteModule] = useState<string | null>(null);

  const deleteModule = async (name: string) => {
    if (!token) return;
    try {
      const res = await authFetchSudo(`/modules/${encodeURIComponent(name)}`, { method: "DELETE" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({ error: "Delete failed" }));
        setError(data.error || "DELETE FAILED");
        return;
      }
      setConfirmDeleteModule(null);
      // Reset selection if we deleted the current module
      if (selectedModule === name) {
        setSelectedModule("claude");
        setSelectedModuleInfo(null);
        setModuleConfig(null);
      }
      fetchModules();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const viewJob = (job: Job) => {
    setSelectedJob(job.id);
    setTaskSubTab("output");
    if (job.status === "running") {
      startStream(job.id);
    } else {
      setStreamOutput(job.output);
      if (esRef.current) esRef.current.close();
    }
  };

  const toggleAsks = (jobId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setExpandedAsks((prev) => {
      const next = new Set(prev);
      if (next.has(jobId)) {
        next.delete(jobId);
      } else {
        next.add(jobId);
      }
      return next;
    });
  };

  // ── Task Actions (Copy/Edit/Fork/Create) ───────────────────────────
  const extractModuleFromWorkDir = (workDir: string): string | null => {
    // Modules live under both orbit/ and core/ — attribute jobs to either.
    const match = workDir.match(/\/(?:orbit|core)\/([^/]+)/);
    return match ? match[1] : null;
  };

  // Task tally per module — feeds the recents-rail badges so you can see each
  // mod's progress (running/queued/done/failed) without opening the TASKS
  // view. Jobs with no work_dir belong to claude itself (same attribution
  // rule as the task-list module filter).
  const railTasks = useMemo(() => {
    const byModule: Record<string, { running: number; pending: number; completed: number; failed: number; prompts: string[] }> = {};
    let running = 0;
    let pending = 0;
    let completed = 0;
    let failed = 0;
    for (const j of jobs) {
      const mod = (j.work_dir ? extractModuleFromWorkDir(j.work_dir) : null) || "claude";
      const entry = byModule[mod] || (byModule[mod] = { running: 0, pending: 0, completed: 0, failed: 0, prompts: [] });
      if (j.status === "running") { entry.running += 1; running += 1; }
      else if (j.status === "pending") { entry.pending += 1; pending += 1; }
      else if (j.status === "completed") { entry.completed += 1; completed += 1; continue; }
      else if (j.status === "failed") { entry.failed += 1; failed += 1; continue; }
      else continue; // cancelled: neither progress nor error
      if (entry.prompts.length < 4) {
        const p = j.prompt.replace(/\s+/g, " ").trim();
        entry.prompts.push(`${j.status === "running" ? "▶" : "…"} ${p.length > 72 ? `${p.slice(0, 72)}…` : p}`);
      }
    }
    return { byModule, running, pending, completed, failed };
  }, [jobs]);

  const copyTaskToInput = (job: Job, e: React.MouseEvent) => {
    e.stopPropagation();
    setPrompt(parsePromptImages(job.prompt).cleanPrompt);
    setModel(job.model);
    setCreationMode("edit");
    if (job.work_dir) {
      const mod = extractModuleFromWorkDir(job.work_dir);
      if (mod) {
        const moduleInfo = moduleList.find(m => m.name === mod);
        if (moduleInfo) {
          resetModuleState(moduleInfo);
          setSelectedModule(mod);
          setSelectedModuleInfo(moduleInfo);
          setWorkDir(moduleInfo.path);
          fetchModuleConfig(mod);
        }
      }
    }
  };

  const forkTask = (job: Job, e: React.MouseEvent) => {
    e.stopPropagation();
    setPrompt(parsePromptImages(job.prompt).cleanPrompt);
    setModel(job.model);
    setCreationMode("fork");
    if (job.work_dir) {
      const mod = extractModuleFromWorkDir(job.work_dir);
      if (mod) setModuleName(mod + "-fork");
    }
  };

  // ── Drag-and-drop reorder ──────────────────────────────────────────
  const handleDragStart = (e: React.DragEvent, jobId: string) => {
    setDraggedJobId(jobId);
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", jobId);
    if (e.currentTarget instanceof HTMLElement) {
      e.currentTarget.style.opacity = "0.4";
    }
  };

  const handleDragEnd = (e: React.DragEvent) => {
    if (e.currentTarget instanceof HTMLElement) {
      e.currentTarget.style.opacity = "1";
    }
    setDraggedJobId(null);
    setDragOverJobId(null);
  };

  const handleDragOver = (e: React.DragEvent, jobId: string) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    if (jobId !== draggedJobId) {
      setDragOverJobId(jobId);
    }
  };

  const handleDragLeave = () => {
    setDragOverJobId(null);
  };

  const handleDrop = (e: React.DragEvent, targetJobId: string) => {
    e.preventDefault();
    if (!draggedJobId || draggedJobId === targetJobId) return;
    setJobs((prev) => {
      const fromIdx = prev.findIndex((j) => j.id === draggedJobId);
      const toIdx = prev.findIndex((j) => j.id === targetJobId);
      if (fromIdx === -1 || toIdx === -1) return prev;
      const updated = [...prev];
      const [moved] = updated.splice(fromIdx, 1);
      updated.splice(toIdx, 0, moved);
      return updated;
    });
    setDraggedJobId(null);
    setDragOverJobId(null);
  };

  const headerCreateOrFork = async () => {
    if (!token) return;
    if (showHeaderCreateForm === "import") {
      // Deterministic import (no agent job) — mirrors submitAddModule but
      // reads the header panel's fields, then jumps into the new module.
      const name = (headerNewName.trim() || (headerImportSource === "github" ? deriveNameFromUrl(headerGithubUrl) : "")).trim();
      if (!name || !/^[a-zA-Z0-9_-]+$/.test(name)) { setError("ENTER A MODULE NAME (LETTERS, NUMBERS, - OR _)"); return; }
      if (headerImportSource === "github" && !/^https?:\/\//i.test(headerGithubUrl.trim())) { setError("ENTER AN HTTP(S) GIT URL"); return; }
      if (headerImportSource === "cid" && !headerCid.trim()) { setError("ENTER A SNAPSHOT CID"); return; }
      setSubmitting(true);
      try {
        const body: Record<string, string> = { source: headerImportSource, name };
        if (headerImportSource === "github") body.url = headerGithubUrl.trim();
        else body.cid = headerCid.trim();
        // git clone can be slow — give it a generous timeout.
        const res = await authFetch("/modules/import", { method: "POST", body: JSON.stringify(body) }, 200000);
        const data = await res.json().catch(() => ({} as any));
        if (!res.ok || data?.error) throw new Error(data?.error || `IMPORT FAILED (HTTP ${res.status})`);
        setHeaderNewName(""); setHeaderGithubUrl(""); setHeaderCid("");
        setShowHeaderCreateForm(null);
        await fetchModules("");
        const opened = (data.module as string) || name;
        const m = {
          name: opened,
          path: (data.path as string) || "",
          category: (data.category as string) || "orbit",
          description: "",
        } as typeof moduleList[0];
        resetModuleState(m);
        setSelectedModule(opened);
        setSelectedModuleInfo(m);
        setWorkDir(m.path);
        fetchModuleConfig(opened);
        setSidebarView("overview");
      } catch (e: any) {
        setError(e?.message === "NOT AUTHENTICATED" ? "SIGN IN TO IMPORT A MODULE" : (e?.message || "IMPORT FAILED"));
      } finally {
        setSubmitting(false);
      }
      return;
    }
    if (showHeaderCreateForm === "edit") {
      if (!headerEditPrompt.trim() || !selectedModule) return;
    } else {
      if (!headerNewName.trim()) return;
    }
    setSubmitting(true);
    try {
      const body: any = {
        model,
        anchor_dir: anchorDir,
      };

      if (showHeaderCreateForm === "edit") {
        if (!isOwner && !selectedModule.includes("peers/") && !selectedModule.startsWith("peers.")) {
          setError("NON-OWNERS CAN ONLY EDIT MODULES IN PEERS FOLDER");
          setSubmitting(false);
          return;
        }
        body.prompt = headerEditPrompt.trim();
        body.work_dir = `${anchorDir}/mod/orbit/${selectedModule}`;
        body.creation_mode = "edit";
      } else {
        let finalName = headerNewName.trim();
        if (!isOwner && !finalName.startsWith("peers/")) {
          finalName = `peers/${finalName}`;
        }

        const defaultPrompt = showHeaderCreateForm === "fork"
          ? `Fork the module "${selectedModule}" into a new module called "${finalName}". Copy all source files, config.json, and directory structure. Update any self-references to use the new module name.`
          : `Create a new module called "${finalName}". Set up the standard module structure with config.json, mod.py, and a README.md.`;

        body.prompt = defaultPrompt;
        body.module_name = finalName;
        body.creation_mode = "new";

        if (showHeaderCreateForm === "fork" && selectedModule) {
          body.fork_from = selectedModule;
        }

        if (headerGithubUrl.trim()) {
          body.github_url = headerGithubUrl.trim();
        }
      }

      const res = await authFetch("/jobs", {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(await submitFailReason(res));
      const job = await res.json();
      setHeaderNewName("");
      setHeaderGithubUrl("");
      setHeaderEditPrompt("");
      setShowHeaderCreateForm(null);
      setSelectedJob(job.id);
      fetchJobs();
      startStream(job.id);
      // Runs in the background — the composer strip / TASKS badge track it.
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSubmitting(false);
    }
  };

  const rerunTask = async (job: Job, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!token) return;
    setSubmitting(true);
    try {
      const body: any = { prompt: job.prompt, model: normalizeModelValue(job.model) };
      if (job.work_dir) body.work_dir = job.work_dir;
      // Apply whatever system prompt is currently configured (the chain
      // wins, else an active personality) so replays aren't bare.
      if (chainedSystemPrompt) body.system_prompt = chainedSystemPrompt;
      else if (activePersonality && activePersonality.prompt) body.system_prompt = activePersonality.prompt;
      const res = await authFetch("/jobs", {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(await submitFailReason(res));
      const newJob = await res.json();
      setSelectedJob(newJob.id);
      fetchJobs();
      startStream(newJob.id);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  // Load a finished/failed task back into the composer so it can be tweaked and
  // re-submitted as a NEW task (the original is left untouched). Mirrors the
  // model-normalization guard so a stale/gated model never gets reloaded.
  const editTask = (job: Job) => {
    setPrompt(job.prompt || "");
    if (job.model) setModel(normalizeModelValue(job.model));
    if (job.work_dir) {
      setWorkDir(job.work_dir);
      setCreationMode("edit");
    }
    setSelectedJob(null);
    setStreamOutput("");
    setComposerMinimized(false);
    setTimeout(() => composerInputRef.current?.focus(), 50);
  };

  const togglePromptExpand = (jobId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setExpandedPrompts((prev) => {
      const next = new Set(prev);
      if (next.has(jobId)) next.delete(jobId);
      else next.add(jobId);
      return next;
    });
  };

  // ── Repo Search ────────────────────────────────────────────────────
  const searchRepos = useCallback(async (q: string) => {
    try {
      const res = await fetch(`${apiUrl}/repos?q=${encodeURIComponent(q)}`);
      if (res.ok) {
        const data = await res.json();
        setRepos(data.repos || []);
        // Extract module names from orbit directory
        const orbitModules = data.repos
          .filter((r: any) => r.path.includes('/mod/orbit/'))
          .map((r: any) => r.name);
        setModules(orbitModules);
      }
    } catch { /* ignore */ }
  }, [apiUrl]);

  const fetchModules = useCallback(async (q: string = "", anchor?: string) => {
    try {
      const params = new URLSearchParams();
      if (q) params.set("q", q);
      if (anchor || anchorDir) params.set("anchor", anchor || anchorDir);
      const res = await fetch(`${apiUrl}/modules?${params}`);
      if (res.ok) {
        const data = await res.json();
        setModuleList(data.modules || []);
      }
    } catch { /* ignore */ }
  }, [anchorDir, apiUrl]);

  const fetchFolders = useCallback(async (q: string = "", path?: string) => {
    try {
      const params = new URLSearchParams();
      if (q) params.set("q", q);
      if (path) params.set("path", path);
      params.set("depth", "3");
      const res = await fetch(`${apiUrl}/folders?${params}`);
      if (res.ok) {
        const data = await res.json();
        setFolderList(data.folders || []);
      }
    } catch { /* ignore */ }
  }, [apiUrl]);

  const suggestFolderDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const fetchFolderSuggestions = useCallback(async (query: string, path?: string) => {
    if (!query.trim()) { setFolderSuggestions([]); return; }
    if (suggestFolderDebounceRef.current) clearTimeout(suggestFolderDebounceRef.current);
    suggestFolderDebounceRef.current = setTimeout(async () => {
      try {
        const params = new URLSearchParams();
        params.set("query", query);
        if (path) params.set("path", path);
        params.set("top_k", "8");
        const res = await fetch(`${apiUrl}/suggest_folders?${params}`);
        if (res.ok) {
          const data = await res.json();
          setFolderSuggestions(data.suggestions || []);
        }
      } catch { /* ignore */ }
    }, 300);
  }, [apiUrl]);

  const fetchModuleConfig = useCallback(async (name: string) => {
    setLoadingConfig(true);
    setModuleConfig(null);
    try {
      const params = new URLSearchParams();
      if (anchorDir) params.set("anchor", anchorDir);
      const res = await fetch(`${apiUrl}/modules/${encodeURIComponent(name)}/config?${params}`);
      if (res.ok) {
        const data = await res.json();
        setModuleConfig(data);
      }
    } catch { /* ignore */ }
    finally { setLoadingConfig(false); }
  }, [anchorDir, apiUrl]);

  // Canonical "open this module for editing" flow — mirrors the HUB card click
  // so the left nav rail (and anywhere else) selects a module identically.
  const selectModule = useCallback((m: typeof moduleList[0]) => {
    resetModuleState(m);
    setSelectedModule(m.name);
    setSelectedModuleInfo(m);
    setWorkDir(m.path);
    fetchModuleConfig(m.name);
    setSidebarView(getBestTab(m));
  }, [resetModuleState, getBestTab, fetchModuleConfig]);

  // Header name+search suggestions: substring-filter the catalog on the typed
  // query and rank most-recently-opened modules first, so an empty query
  // surfaces your recents and Enter always picks the top visible suggestion.
  const rankedHeaderModules = useCallback((q: string) => {
    const query = q.trim().toLowerCase();
    let list = ownerFilter ? moduleList.filter((m) => m.owner === ownerFilter) : moduleList;
    if (query) list = list.filter((m) => m.name.toLowerCase().includes(query));
    const recency = new Map<string, number>(recentModules.map((n, i) => [n, i]));
    return [...list].sort((a, b) => {
      const ra = recency.has(a.name) ? (recency.get(a.name) as number) : Infinity;
      const rb = recency.has(b.name) ? (recency.get(b.name) as number) : Infinity;
      if (ra !== rb) return ra - rb;
      if (query) {
        const pa = a.name.toLowerCase().startsWith(query) ? 0 : 1;
        const pb = b.name.toLowerCase().startsWith(query) ? 0 : 1;
        if (pa !== pb) return pa - pb;
      }
      return a.name.localeCompare(b.name);
    });
  }, [moduleList, recentModules, ownerFilter]);

  // Derive a sensible default module name from a git URL's last path
  // segment (".git" stripped) so the user rarely has to type one.
  const deriveNameFromUrl = (url: string): string => {
    try {
      const tail = url.trim().replace(/\/+$/, "").split("/").pop() || "";
      return tail.replace(/\.git$/i, "").toLowerCase().replace(/[^a-z0-9_-]/g, "-").replace(/^-+|-+$/g, "");
    } catch { return ""; }
  };

  // Import a brand-new module from a GitHub repo or a snapshot CID, then
  // refresh the catalog and jump straight into the new module.
  const submitAddModule = useCallback(async () => {
    setAddError(null);
    const name = (addName.trim() || (addSource === "github" ? deriveNameFromUrl(addUrl) : "")).trim();
    if (!name || !/^[a-zA-Z0-9_-]+$/.test(name)) {
      setAddError("Enter a module name (letters, numbers, - or _).");
      return;
    }
    if (addSource === "github" && !/^https?:\/\//i.test(addUrl.trim())) {
      setAddError("Enter an http(s) git URL.");
      return;
    }
    if (addSource === "cid" && !addCid.trim()) {
      setAddError("Enter a snapshot CID.");
      return;
    }
    setAddBusy(true);
    try {
      const body: Record<string, string> = { source: addSource, name };
      if (addSource === "github") body.url = addUrl.trim();
      else body.cid = addCid.trim();
      // git clone can be slow — give it a generous timeout.
      const res = await authFetch("/modules/import", { method: "POST", body: JSON.stringify(body) }, 200000);
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.error) {
        setAddError(data?.error || `Import failed (HTTP ${res.status})`);
        setAddBusy(false);
        return;
      }
      // Success — close the modal, reset its fields, refresh the catalog.
      setAddOpen(false);
      setAddUrl(""); setAddCid(""); setAddName(""); setAddError(null);
      await fetchModules("");
      // Open the freshly imported module.
      const opened = (data.module as string) || name;
      const m = {
        name: opened,
        path: (data.path as string) || "",
        category: (data.category as string) || "orbit",
        description: "",
      } as typeof moduleList[0];
      resetModuleState(m);
      setSelectedModule(opened);
      setSelectedModuleInfo(m);
      setWorkDir(m.path);
      fetchModuleConfig(opened);
      setSidebarView("overview");
    } catch (e: any) {
      setAddError(e?.message === "NOT AUTHENTICATED" ? "Sign in to import a module." : (e?.message || "Import failed"));
    } finally {
      setAddBusy(false);
    }
  }, [addName, addSource, addUrl, addCid, authFetch, fetchModules, resetModuleState, fetchModuleConfig]);

  // Fetch direct config from /config endpoint on mount
  const fetchDirectConfig = useCallback(async () => {
    try {
      const res = await fetch(`${apiUrl}/config`);
      if (res.ok) {
        const data = await res.json();
        setDirectConfig(data);
      }
    } catch { /* ignore */ }
  }, [apiUrl]);

  useEffect(() => {
    fetchDirectConfig();
  }, [fetchDirectConfig]);

  useEffect(() => {
    fetchModules();
  }, [fetchModules]);

  // The HUB grid renders from `moduleList` — the same state the header
  // module-search box narrows via fetchModules(query). Without this, a prior
  // header search (e.g. "polymarket") leaves moduleList = [that one match],
  // so opening the hub shows a single card while its own filter box is empty.
  // Reload the unfiltered list every time the hub opens so it always shows
  // every module; the hub's own `hubSearch` box does any in-view filtering.
  useEffect(() => {
    if (sidebarView === "hub") fetchModules("");
  }, [sidebarView, fetchModules]);

  // Auto-select default module and keep selectedModuleInfo in sync with moduleList
  useEffect(() => {
    if (selectedModule && moduleList.length > 0) {
      const match = moduleList.find((m) => m.name === selectedModule);
      if (match) {
        if (selectedModuleInfo?.name !== selectedModule) {
          // Module switched (or first load): point workDir at the new module's
          // path so the FILES tab tracks it, refetch its config, and pick a
          // default tab. Without this, switching modules in a code path that
          // doesn't itself call setWorkDir leaves FILES showing the prior mod.
          setWorkDir(match.path);
          fetchModuleConfig(match.name);
          setSidebarView(getBestTab(match));
        }
        // Always sync selectedModuleInfo with latest moduleList data
        setSelectedModuleInfo(match);
      }
    }
  }, [moduleList, selectedModule]);

  // ── Module health check ────────────────────────────────────────────
  const checkModuleHealth = useCallback(async () => {
    if (!selectedModuleInfo?.api_url) {
      setModuleRunning(null);
    } else {
      try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 2000);
        const res = await fetch(`${selectedModuleInfo.api_url}/health`, { signal: controller.signal });
        clearTimeout(timeout);
        setModuleRunning(res.ok);
      } catch {
        setModuleRunning(false);
      }
    }
    if (!selectedModuleInfo?.app_url) {
      setAppRunning(null);
    } else {
      // Probe via the same-origin /api/service route (uses net.createServer
      // to check port occupancy, no HTTP request to the app — avoids CORS).
      const portMatch = selectedModuleInfo.app_url.match(/:(\d+)/);
      const port = portMatch?.[1];
      if (!port) {
        setAppRunning(null);
      } else {
        try {
          const res = await fetch(`${DEFAULT_BASE_PATH}/api/service?port=${port}`, { signal: AbortSignal.timeout(2000) });
          const data = await res.json();
          setAppRunning(!!data.running);
        } catch {
          setAppRunning(false);
        }
      }
    }
  }, [selectedModuleInfo]);

  useEffect(() => {
    checkModuleHealth();
    if (!selectedModuleInfo?.api_url && !selectedModuleInfo?.app_url) return;
    const interval = setInterval(checkModuleHealth, 5000);
    return () => clearInterval(interval);
  }, [checkModuleHealth]);

  // ── Process control: start / stop / restart for API and App separately ──
  // Drive the selected module's lifecycle through pm2 (the real supervisor) via
  // the API's /modules/{name}/process endpoint. Going through pm2 makes "stop"
  // actually stay stopped and "restart" reliable — unlike a raw port kill, which
  // pm2's autorestart immediately reverses. Cross-module actions surface the
  // owner Sudo sheet automatically through authFetchSudo.
  // authFetchSudo is defined further down the component; reach it through a ref
  // (kept current each render) so this callback doesn't reference a not-yet-
  // initialized const in its dependency array (temporal dead zone at render).
  const authFetchSudoRef = useRef<
    ((path: string, opts?: RequestInit, timeoutMs?: number) => Promise<Response>) | null
  >(null);

  const moduleProcessAction = useCallback(async (
    target: "api" | "app",
    action: "stop" | "start" | "restart",
  ) => {
    if (!selectedModuleInfo || !token) return;
    const doFetch = authFetchSudoRef.current;
    if (!doFetch) return;
    const setToggling = target === "api" ? setTogglingApi : setTogglingApp;
    setToggling(true);
    try {
      // start/restart may rebuild a prod (next start) app server-side first, so
      // allow plenty of time; stop is quick.
      await doFetch(`/modules/${selectedModuleInfo.name}/process`, {
        method: "POST",
        body: JSON.stringify({ action, target }),
      }, action === "stop" ? 60000 : 300000);
      // Give pm2 a moment to settle, then re-check health.
      setTimeout(() => { checkModuleHealth(); setToggling(false); }, action === "stop" ? 1500 : 3000);
    } catch { setToggling(false); }
  }, [selectedModuleInfo, token, checkModuleHealth]);

  const stopProcess = useCallback(
    (target: "api" | "app") => moduleProcessAction(target, "stop"),
    [moduleProcessAction],
  );

  const startProcess = useCallback(
    (target: "api" | "app") => moduleProcessAction(target, "start"),
    [moduleProcessAction],
  );

  const restartProcess = useCallback(
    (target: "api" | "app") => moduleProcessAction(target, "restart"),
    [moduleProcessAction],
  );

  // ── Module hub: status probing ─────────────────────────────────────
  // "Real" modules are the ones worth showing on the hub — anything with a
  // config, a service dir, or a live url. Bare folders are skipped.
  const isRealModule = useCallback((m: typeof moduleList[0]) => (
    m.has_config || m.has_api_dir || m.has_app_dir || m.has_server_dir || !!m.app_url || !!m.api_url
  ), []);

  // Rail search results: ranked name matches (recents first) followed by
  // description matches, deduped by name. Enter in the rail search picks
  // railMatches(q)[0] — the SAME list the rail renders, so what you see on
  // top is what Enter selects.
  const railMatches = useCallback((q: string) => {
    const query = q.trim().toLowerCase();
    const seen = new Set<string>();
    const take = (list: typeof moduleList) =>
      list.filter((m) => (seen.has(m.name) ? false : (seen.add(m.name), true)));
    return [
      ...take(rankedHeaderModules(q).filter(isRealModule)),
      ...take(moduleList.filter(isRealModule).filter((m) => query && (m.description || "").toLowerCase().includes(query))),
    ];
  }, [rankedHeaderModules, moduleList, isRealModule]);

  // Probe one module's liveness without a cross-origin app request: the app is
  // checked by port occupancy through the same-origin /api/service route, the
  // API by its /health (same pattern as checkModuleHealth for the selected mod).
  const probeModuleStatus = useCallback(async (m: typeof moduleList[0]) => {
    let app: boolean | null = null;
    let api: boolean | null = null;
    const port = m.app_url?.match(/:(\d+)/)?.[1];
    if (port) {
      try {
        const r = await fetch(`${DEFAULT_BASE_PATH}/api/service?port=${port}`, { signal: AbortSignal.timeout(2500) });
        const d = await r.json();
        app = !!d.running;
      } catch { app = false; }
    }
    if (m.api_url) {
      try {
        const r = await fetch(`${m.api_url}/health`, { signal: AbortSignal.timeout(2500) });
        api = r.ok;
      } catch { api = false; }
    }
    return { app, api };
  }, []);

  // Probe a list of modules in small concurrent batches, committing results as
  // each batch lands so dots fill in progressively rather than all-at-once.
  const probeHubStatuses = useCallback(async (mods: typeof moduleList) => {
    const BATCH = 8;
    for (let i = 0; i < mods.length; i += BATCH) {
      const slice = mods.slice(i, i + BATCH);
      const results = await Promise.all(
        slice.map(async (m) => [m.name, await probeModuleStatus(m)] as const),
      );
      setModuleStatuses((prev) => {
        const next = { ...prev };
        for (const [name, st] of results) next[name] = st;
        return next;
      });
    }
  }, [probeModuleStatus]);

  // Probe while the hub or the recents rail is open; refresh on an interval
  // so dots stay live. Rail-only probing sticks to the recents list, so an
  // open rail doesn't hammer every module the way the full hub grid does.
  useEffect(() => {
    const railWantsDots = !isMobile && recentsRailOpen;
    if (sidebarView !== "hub" && !railWantsDots) return;
    const real = sidebarView === "hub"
      ? moduleList.filter(isRealModule)
      : moduleList.filter(isRealModule).filter((m) => recentModules.includes(m.name));
    if (!real.length) return;
    probeHubStatuses(real);
    const iv = setInterval(() => probeHubStatuses(real), 8000);
    return () => clearInterval(iv);
  }, [sidebarView, moduleList, isRealModule, probeHubStatuses, isMobile, recentsRailOpen, recentModules]);

  // An open rail needs the catalog for its rows' info + click targets —
  // fetch it once if the rail was restored open before anything else did.
  useEffect(() => {
    if (!isMobile && recentsRailOpen && !moduleList.length) fetchModules("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recentsRailOpen, isMobile]);

  // pm2 lifecycle for any module by name (the account panel's list isn't tied
  // to the selected module the way moduleProcessAction is). Cross-module
  // actions surface the owner Sudo sheet automatically through authFetchSudo.
  const walletModuleAction = useCallback(async (
    name: string,
    action: "start" | "stop" | "restart",
  ) => {
    const doFetch = authFetchSudoRef.current;
    if (!doFetch || !token) return;
    try {
      await doFetch(`/modules/${encodeURIComponent(name)}/process`, {
        method: "POST",
        body: JSON.stringify({ action }),
      }, action === "stop" ? 60000 : 300000);
    } catch { /* status re-probe below reflects the real outcome */ }
    const m = moduleList.find((x) => x.name === name);
    if (m) {
      setTimeout(async () => {
        const st = await probeModuleStatus(m);
        setModuleStatuses((prev) => ({ ...prev, [name]: st }));
      }, action === "stop" ? 1500 : 3000);
    }
  }, [token, moduleList, probeModuleStatus]);

  // ── Auto-restart after an edit ─────────────────────────────────────
  // Restart a module's processes through pm2 (the real supervisor) so a fresh
  // edit is actually live. Skips "claude" itself — restarting the console we're
  // driving would kill this very UI mid-action.
  const triggerModuleRestart = useCallback(async (name: string) => {
    const doFetch = authFetchSudoRef.current;
    if (!doFetch) return;
    setRestartNotice(`↻ restarting ${name} to apply edits…`);
    try {
      // A prod-built (next start) module is rebuilt server-side before the
      // restart so the edit is actually served — that can take a while, so we
      // give this a generous timeout (a dev-mode module just restarts fast).
      const res = await doFetch(`/modules/${name}/process`, {
        method: "POST",
        body: JSON.stringify({ action: "restart" }),
      }, 300000);
      const data = await res.json().catch(() => ({}));
      const built = typeof data?.output === "string" && data.output.includes("rebuilt prod app");
      setRestartNotice(
        res.ok && data?.ok !== false
          ? `✓ ${name} ${built ? "rebuilt + restarted" : "restarted"} — edits live`
          : `⚠ couldn't restart ${name}${data?.output ? `: ${String(data.output).split("\n")[0]}` : ""}`,
      );
    } catch {
      setRestartNotice(`⚠ couldn't restart ${name}`);
    }
    setTimeout(() => setRestartNotice(null), 6000);
    if (selectedModule === name) setTimeout(checkModuleHealth, 3000);
  }, [selectedModule, checkModuleHealth]);

  // Watch the jobs list: when an edit job for a module flips to "completed",
  // restart that module. A ref of prior statuses means we only fire on the
  // running→completed *transition*, not for jobs already done on first load.
  useEffect(() => {
    const prev = prevJobStatusRef.current;
    const next: Record<string, string> = {};
    const justCompleted: typeof jobs = [];
    for (const j of jobs) {
      next[j.id] = j.status;
      if (prev[j.id] && prev[j.id] !== j.status && j.status === "completed") justCompleted.push(j);
    }
    prevJobStatusRef.current = next;
    if (!autoRestartAfterEdit) return;
    const seen = new Set<string>();
    for (const j of justCompleted) {
      const mod = j.work_dir ? extractModuleFromWorkDir(j.work_dir) : null;
      if (!mod || mod === "claude" || seen.has(mod)) continue;
      seen.add(mod);
      triggerModuleRestart(mod);
    }
  }, [jobs, autoRestartAfterEdit, triggerModuleRestart]);

  // Fetch module logs (API and App)
  const fetchModuleLogs = useCallback(async () => {
    if (!selectedModule || !token) return;
    setModuleLogsLoading(true);
    try {
      const res = await authFetch(`/modules/${selectedModule}/logs`, {
        method: "POST",
        body: JSON.stringify({ lines: 200 }),
      });
      const data = await res.json().catch(() => null);
      if (res.ok && data && typeof data === "object" && !data.error) {
        setModuleLogs(typeof data === "string" ? { stdout: data } : data);
      } else {
        setModuleLogs({ error: data?.error || `Failed to load logs (HTTP ${res.status})` });
      }
    } catch (e) {
      setModuleLogs({ error: e instanceof Error ? e.message : "Failed to load logs" });
    }
    setModuleLogsLoading(false);
  }, [selectedModule, token, authFetch]);

  // Auto-refresh logs (in overview's inline logs panel, the dedicated LOGS
  // tab, or the API tab's logs pane)
  useEffect(() => {
    const inLogsTab = sidebarView === "logs" || (sidebarView === "apitab" && apiTabView === "logs");
    if (!moduleLogsAutoRefresh || (!moduleLogsOpen && !inLogsTab)) return;
    const iv = setInterval(fetchModuleLogs, 4000);
    return () => clearInterval(iv);
  }, [moduleLogsAutoRefresh, moduleLogsOpen, sidebarView, apiTabView, fetchModuleLogs]);

  // Fetch logs when opened or when entering the LOGS tab / API tab logs pane
  useEffect(() => {
    if (moduleLogsOpen || sidebarView === "logs" || (sidebarView === "apitab" && apiTabView === "logs")) fetchModuleLogs();
  }, [moduleLogsOpen, sidebarView, apiTabView]);

  // ── API tab: load the endpoint catalog once per session ──
  useEffect(() => {
    if (sidebarView !== "apitab" || apiSchema) return;
    fetch(`${apiUrl}/schema`)
      .then((r) => r.json())
      .then((d) => { if (d && Array.isArray(d.endpoints)) setApiSchema(d); })
      .catch(() => { /* schema endpoint unavailable — the tab shows a hint */ });
  }, [sidebarView, apiSchema, apiUrl]);

  // API playground: resolve the expanded endpoint's template (path params,
  // filled query params, body fields) into a real request and run it. Uses
  // the signed-in bearer token when there is one so owner/bearer endpoints
  // work. Image responses (module screenshots) render inline.
  const sendTryRequest = useCallback(async (ep: { method: string; path: string }) => {
    setTrySending(true);
    setTryStatus(null);
    setTryOk(null);
    setTryResp(null);
    try {
      const { basePath, queryParams } = parseEndpointInputs(ep.path);
      let path = basePath.replace(/:(\w+)/g, (_m, name) => encodeURIComponent((tryParams[name] || "").trim()));
      const qs = queryParams
        .filter((q) => (tryParams[q.name] || "").trim() !== "")
        .map((q) => `${q.name}=${encodeURIComponent(tryParams[q.name].trim())}`)
        .join("&");
      if (qs) path += `?${qs}`;
      const opts: RequestInit = { method: ep.method };
      if (ep.method !== "GET") {
        const body = tryBodyRaw ? tryBody.trim() : buildBodyJson(tryBodyFields);
        if (body) opts.body = body;
      }
      const res = token && token !== "local"
        ? await authFetch(path, opts)
        : await fetch(`${apiUrl}${path}`, { ...opts, headers: { "Content-Type": "application/json" } });
      setTryOk(res.ok);
      const friendly = statusExplainer(res.status);
      setTryStatus(`${res.status}${friendly ? ` — ${friendly}` : ""}`);
      const ctype = res.headers.get("content-type") || "";
      if (res.ok && ctype.startsWith("image/")) {
        const blob = await res.blob();
        setTryRespKind("image");
        setTryResp(URL.createObjectURL(blob));
      } else {
        const text = await res.text();
        setTryRespKind("text");
        try {
          setTryResp(JSON.stringify(JSON.parse(text), null, 2));
        } catch {
          setTryResp(text.slice(0, 20000) || "(empty response)");
        }
      }
    } catch (e: any) {
      setTryOk(false);
      setTryStatus("network error — is the API running?");
      setTryRespKind("text");
      setTryResp(e?.message || String(e));
    } finally {
      setTrySending(false);
    }
  }, [tryParams, tryBodyRaw, tryBody, tryBodyFields, token, authFetch, apiUrl]);

  // Reset logs when switching modules
  useEffect(() => {
    setModuleLogs({});
    setModuleLogsOpen(null);
    setModuleLogsAutoRefresh(false);
  }, [selectedModule]);

  // Legacy toggle (used by old single button, kept for compat)
  const toggleModule = useCallback(async () => {
    if (!selectedModuleInfo || !token || togglingModule) return;
    setTogglingModule(true);
    try {
      if (moduleRunning) {
        await stopProcess("api");
      } else {
        await startProcess("api");
      }
      setTimeout(() => { setTogglingModule(false); }, 3000);
    } catch { setTogglingModule(false); }
  }, [selectedModuleInfo, token, togglingModule, moduleRunning, stopProcess, startProcess]);

  useEffect(() => {
    searchRepos("");
  }, [searchRepos]);

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (repoRef.current && !repoRef.current.contains(e.target as Node)) {
        setShowRepos(false);
      }
      if (moduleDropdownRef.current && !moduleDropdownRef.current.contains(e.target as Node)) {
        setShowModuleDropdown(false);
      }
      if (inlineModuleRef.current && !inlineModuleRef.current.contains(e.target as Node)) {
        setShowInlineModuleDropdown(false);
      }
      if (profileMenuRef.current && !profileMenuRef.current.contains(e.target as Node)) {
        setProfileMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  // Filter jobs based on search query and status
  const filteredJobs = jobs.filter((job) => {
    // Status filter
    if (statusFilter && job.status !== statusFilter) return false;

    // Search query filter
    if (!searchQuery.trim()) return true;
    const query = searchQuery.toLowerCase();
    return (
      job.prompt.toLowerCase().includes(query) ||
      job.id.toLowerCase().includes(query) ||
      job.model.toLowerCase().includes(query) ||
      job.status.toLowerCase().includes(query) ||
      (job.work_dir && job.work_dir.toLowerCase().includes(query))
    );
  });

  const selectedJobData = jobs.find((j) => j.id === selectedJob);
  const runningCount = jobs.filter((j) => j.status === "running").length;

  // Parse attached images from prompt text
  const parsePromptImages = (prompt: string): { cleanPrompt: string; imagePaths: string[] } => {
    const match = prompt.match(/^\[Attached images: (.+?)\]\n\nPlease read and analyze the attached image files above\.\n\n/);
    if (!match) return { cleanPrompt: prompt, imagePaths: [] };
    const paths = match[1].split(", ").map(p => p.trim());
    const cleanPrompt = prompt.slice(match[0].length);
    return { cleanPrompt, imagePaths: paths };
  };

  // Colorize output with diff highlighting
  const renderOutput = (text: string) => {
    if (!text) return null;
    const lines = text.split("\n");
    return lines.map((line, i) => {
      // Diff removals
      if (line.startsWith("│- ")) {
        return <span key={i} style={{ color: "var(--crt-red)" }}>{line}{"\n"}</span>;
      }
      // Diff additions
      if (line.startsWith("│+ ")) {
        return <span key={i} style={{ color: "var(--accent-color)" }}>{line}{"\n"}</span>;
      }
      // Edit/Write headers
      if (line.startsWith("┌─ EDIT:") || line.startsWith("┌─ WRITE:")) {
        return <span key={i} style={{ color: "var(--crt-amber)", fontWeight: "bold" }}>{line}{"\n"}</span>;
      }
      // Diff separator
      if (line === "│───" || line === "└─") {
        return <span key={i} style={{ color: "var(--crt-amber)", opacity: 0.4 }}>{line}{"\n"}</span>;
      }
      // Bash commands
      if (line.startsWith("$ ")) {
        return <span key={i} style={{ color: "var(--crt-blue)" }}>{line}{"\n"}</span>;
      }
      // Tool use markers
      if (line.startsWith("⚡ ")) {
        return <span key={i} style={{ color: "var(--crt-amber)" }}>{line}{"\n"}</span>;
      }
      return <span key={i} style={{ color: "var(--text-primary)" }}>{line}{"\n"}</span>;
    });
  };

  // ── Task output parsers ────────────────────────────────────────────
  // The job output stream embeds structured markers the API emits per tool
  // call (see jobs.rs format_tool_input): EDIT/WRITE diff blocks, "$ " bash
  // commands, and "⚡ " tool-use lines. These two parsers re-derive the
  // EDITS and AUDIT views from that single stream — no extra backend data.

  type TaskEdit = { kind: "edit" | "write"; file: string; removed: string[]; added: string[]; lineCount: number };

  const parseTaskEdits = (text: string): TaskEdit[] => {
    if (!text) return [];
    const EDIT = "┌─ EDIT: ", WRITE = "┌─ WRITE: ";
    const edits: TaskEdit[] = [];
    let cur: TaskEdit | null = null;
    let phase: "removed" | "added" = "removed";
    for (const line of text.split("\n")) {
      if (line.startsWith(EDIT)) {
        cur = { kind: "edit", file: line.slice(EDIT.length).trim(), removed: [], added: [], lineCount: 0 };
        phase = "removed";
        continue;
      }
      if (line.startsWith(WRITE)) {
        const rest = line.slice(WRITE.length).trim();
        const m = rest.match(/^(.*) \((\d+) lines\)$/);
        edits.push({ kind: "write", file: m ? m[1] : rest, removed: [], added: [], lineCount: m ? parseInt(m[2], 10) : 0 });
        cur = null;
        continue;
      }
      if (!cur) continue;
      if (line === "│───") { phase = "added"; continue; }
      if (line === "└─") { edits.push(cur); cur = null; continue; }
      if (phase === "removed" && line.startsWith("│- ")) cur.removed.push(line.slice(3));
      else if (phase === "added" && line.startsWith("│+ ")) cur.added.push(line.slice(3));
    }
    if (cur) edits.push(cur); // block still streaming (job running) — show what we have
    return edits;
  };

  type AuditEvent = { type: "edit" | "write" | "bash" | "read" | "search" | "task"; label: string; detail?: string };

  const parseTaskAudit = (text: string): AuditEvent[] => {
    if (!text) return [];
    const EDIT = "┌─ EDIT: ", WRITE = "┌─ WRITE: ";
    const READ = "⚡ Read ", GLOB = "⚡ Glob ", GREP = "⚡ Grep ", TASK = "⚡ Task ";
    const events: AuditEvent[] = [];
    for (const line of text.split("\n")) {
      if (line.startsWith(EDIT)) events.push({ type: "edit", label: line.slice(EDIT.length).trim() });
      else if (line.startsWith(WRITE)) events.push({ type: "write", label: line.slice(WRITE.length).trim() });
      else if (line.startsWith("$ ")) {
        const body = line.slice(2);
        const sep = body.lastIndexOf(" # ");
        if (sep >= 0) events.push({ type: "bash", label: body.slice(0, sep), detail: body.slice(sep + 3) });
        else events.push({ type: "bash", label: body });
      }
      else if (line.startsWith(READ)) events.push({ type: "read", label: line.slice(READ.length).trim() });
      else if (line.startsWith(GLOB) || line.startsWith(GREP)) events.push({ type: "search", label: line.slice(GLOB.length).trim() });
      else if (line.startsWith(TASK)) events.push({ type: "task", label: line.slice(TASK.length).trim() });
    }
    return events;
  };

  // Effective config: prefer moduleConfig, fallback to directConfig (hoisted before early return)
  const effectiveConfig = moduleConfig?.config || directConfig;

  // The active module's API URL (for display, API explorer, health checks, etc.)
  // Falls back to the host apiUrl if the module has no dedicated API
  const moduleApiUrl = selectedModuleInfo?.api_url || effectiveConfig?.urls?.api || effectiveConfig?.api_url || apiUrl;

  // Auto-collapse nested objects (depth >= 2) when config loads
  useEffect(() => {
    if (!effectiveConfig) return;
    const paths = new Set<string>();
    const walk = (obj: any, p: string, depth: number) => {
      if (obj && typeof obj === "object") {
        if (depth >= 2) paths.add(p);
        if (Array.isArray(obj)) obj.forEach((v: any, i: number) => walk(v, `${p}[${i}]`, depth + 1));
        else Object.keys(obj).forEach(k => walk(obj[k], `${p}.${k}`, depth + 1));
      }
    };
    walk(effectiveConfig, "$", 0);
    setCollapsedPaths(paths);
  }, [effectiveConfig]);

  // Sync api_url/app_url from config when moduleList doesn't have them
  useEffect(() => {
    if (!selectedModuleInfo || !effectiveConfig) return;
    const cfgApiUrl = effectiveConfig.urls?.api || effectiveConfig.api_url;
    const cfgAppUrl = effectiveConfig.urls?.app || effectiveConfig.app_url;
    const needsApiUrl = !selectedModuleInfo.api_url && cfgApiUrl;
    const needsAppUrl = !selectedModuleInfo.app_url && cfgAppUrl;
    if (needsApiUrl || needsAppUrl) {
      const updated = {
        ...selectedModuleInfo,
        api_url: selectedModuleInfo.api_url || cfgApiUrl || null,
        app_url: selectedModuleInfo.app_url || cfgAppUrl || null,
      };
      setSelectedModuleInfo(updated);
      // Auto-switch to best tab if we just discovered new capabilities
      if (sidebarView === "overview") {
        setSidebarView(getBestTab(updated));
      }
    }
  }, [effectiveConfig]);

  const fireApiRequest = useCallback(async (endpoint: string, method: string, params: Record<string, string>) => {
    const baseUrl = selectedModuleInfo?.api_url || effectiveConfig?.urls?.api || effectiveConfig?.api_url || apiUrl;
    setApiLoading(true);
    setApiResponse(null);
    setApiResponseStatus(null);
    try {
      // Build URL with path params replaced
      let url = endpoint;
      const queryParams = new URLSearchParams();
      const bodyParams: Record<string, any> = {};
      const ec = moduleConfig?.config || directConfig;
      const endpointConfig = ec?.endpoints?.[endpoint];
      const inputs = endpointConfig?.input || [];

      for (const [key, value] of Object.entries(params)) {
        if (!value && value !== "0") continue;
        if (url.includes(`{${key}}`)) {
          url = url.replace(`{${key}}`, encodeURIComponent(value));
        } else if (method === "GET") {
          queryParams.set(key, value);
        } else {
          // Check schema for type coercion
          const inputDef = inputs.find((i: any) => i.name === key);
          if (inputDef?.type === "bool") {
            bodyParams[key] = value === "true";
          } else if (inputDef?.type === "int") {
            bodyParams[key] = parseInt(value, 10);
          } else if (inputDef?.type === "list") {
            try { bodyParams[key] = JSON.parse(value); } catch { bodyParams[key] = value; }
          } else {
            bodyParams[key] = value;
          }
        }
      }

      const qs = queryParams.toString();
      const fullUrl = `${baseUrl}${url}${qs ? `?${qs}` : ""}`;
      const headers: Record<string, string> = {};
      if (method !== "GET") headers["Content-Type"] = "application/json";
      if (token && token !== "local") headers["Authorization"] = `Bearer ${token}`;
      const endpointAuth = endpointConfig?.auth;

      const opts: RequestInit = { method, headers };
      if (method !== "GET" && method !== "DELETE" && Object.keys(bodyParams).length > 0) {
        opts.body = JSON.stringify(bodyParams);
      }

      const res = await fetch(fullUrl, opts);
      setApiResponseStatus(res.status);
      const text = await res.text();
      try {
        const json = JSON.parse(text);
        setApiResponse(JSON.stringify(json, null, 2));
      } catch {
        setApiResponse(text);
      }
    } catch (err: any) {
      setApiResponseStatus(0);
      setApiResponse(`Error: ${err.message}`);
    } finally {
      setApiLoading(false);
    }
  }, [selectedModuleInfo, apiUrl, moduleConfig, directConfig, token]);

  // ── Fire a function from the config schema ──
  const fireConfigFn = useCallback(async (fnName: string, params: Record<string, string>) => {
    const baseUrl = selectedModuleInfo?.api_url || effectiveConfig?.urls?.api || effectiveConfig?.api_url || apiUrl;
    setConfigFnLoading(true);
    setConfigFnResponse(null);
    try {
      const schema = effectiveConfig?.schema?.[fnName];
      const inputs = schema?.input || [];
      const bodyParams: Record<string, any> = {};
      for (const input of inputs) {
        const val = params[input.name];
        if (val !== undefined && val !== "") {
          if (input.type === "bool") bodyParams[input.name] = val === "true";
          else if (input.type === "int" || input.type === "float") bodyParams[input.name] = Number(val);
          else if (input.type === "list") { try { bodyParams[input.name] = JSON.parse(val); } catch { bodyParams[input.name] = val; } }
          else bodyParams[input.name] = val;
        }
      }
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (token && token !== "local") headers["Authorization"] = `Bearer ${token}`;
      const res = await fetch(`${baseUrl}/forward`, {
        method: "POST",
        headers,
        body: JSON.stringify({ fn: fnName, ...bodyParams }),
      });
      const text = await res.text();
      let formatted: string;
      try { formatted = JSON.stringify(JSON.parse(text), null, 2); } catch { formatted = text; }
      setConfigFnResponse(`[${res.status}] ${formatted}`);
    } catch (e: any) {
      setConfigFnResponse(`[ERROR] ${e.message}`);
    } finally {
      setConfigFnLoading(false);
    }
  }, [selectedModuleInfo, apiUrl, effectiveConfig, token]);

  // Fetch changelog from API (must be before early return to maintain hook order)
  const fetchChangelog = useCallback(async () => {
    const base = selectedModuleInfo?.api_url || effectiveConfig?.urls?.api || effectiveConfig?.api_url || apiUrl;
    setChangelogLoading(true);
    try {
      const res = await fetch(`${base}/changelog`);
      if (res.ok) {
        const data = await res.json();
        setChangelogEntries(data.changelog || []);
      }
    } catch (e) {
      console.error("Failed to fetch changelog:", e);
    } finally {
      setChangelogLoading(false);
    }
  }, [selectedModuleInfo, effectiveConfig, apiUrl]);

  // Fetch a specific version detail (must be before early return to maintain hook order)
  const fetchVersionDetail = useCallback(async (version: string) => {
    const base = selectedModuleInfo?.api_url || effectiveConfig?.urls?.api || effectiveConfig?.api_url || apiUrl;
    setVersionDetailLoading(true);
    setSelectedVersion(version);
    try {
      const res = await fetch(`${base}/versions/${encodeURIComponent(version)}`);
      if (res.ok) {
        const data = await res.json();
        setVersionDetail(data);
      }
    } catch (e) {
      console.error("Failed to fetch version detail:", e);
    } finally {
      setVersionDetailLoading(false);
    }
  }, [selectedModuleInfo, effectiveConfig, apiUrl]);

  // ── JSON Tree Helpers (must be before early return to maintain hook order)
  const toggleCollapse = useCallback((path: string) => {
    setCollapsedPaths(prev => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  const copyValue = useCallback((path: string, value: any) => {
    const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
    navigator.clipboard.writeText(text);
    setCopiedPath(path);
    setTimeout(() => setCopiedPath(null), 1500);
  }, []);

  const collapseAll = useCallback((data: any, prefix = "$") => {
    const paths = new Set<string>();
    const walk = (obj: any, p: string) => {
      if (obj && typeof obj === "object") {
        paths.add(p);
        if (Array.isArray(obj)) obj.forEach((v: any, i: number) => walk(v, `${p}[${i}]`));
        else Object.keys(obj).forEach(k => walk(obj[k], `${p}.${k}`));
      }
    };
    walk(data, prefix);
    setCollapsedPaths(paths);
  }, []);

  const expandAll = useCallback(() => {
    setCollapsedPaths(new Set());
  }, []);

  // ═══════════════════════════════════════════════════════════════════
  // AUTH SCREEN — IBM BOOT STYLE
  // ═══════════════════════════════════════════════════════════════════

  // Connect-wallet panel — rendered as a right-side sign-in drawer inside the
  // hub when there's no session (replaces the old full-screen takeover).
  const renderConnectPanel = () => (
    <div className="h-full flex flex-col overflow-y-auto" style={{ background: "var(--bg-secondary)" }}>
          <div className="flex-1 flex flex-col justify-center p-6">
            <div className="flex items-center justify-between gap-3 mb-4">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ background: "rgba(245,158,11,0.1)" }}>
                  <span className="text-crt-amber text-[16px]">🔐</span>
                </div>
                <h2
                  className="text-[16px] font-semibold"
                  style={{ color: "var(--text-primary)" }}
                >
                  Connect Wallet
                </h2>
              </div>
              <button
                onClick={() => setSignInOpen(false)}
                className="text-[18px] leading-none px-2 py-1"
                style={{ color: "var(--text-tertiary)" }}
                title="Hide — browse the hub"
              >
                ×
              </button>
            </div>

            <div className="text-[13px] mb-5 leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
              Sign a cryptographic challenge to authenticate.
              Your signature is verified server-side via ecrecover and
              becomes a 24-hour bearer token for all API requests.
            </div>

            <div
              className="text-[12px] mb-5 px-3 py-2 leading-relaxed"
              style={{
                color: "var(--text-tertiary)",
                border: "1px solid color-mix(in srgb, var(--crt-green) 15%, var(--border-color))",
                background: "color-mix(in srgb, var(--crt-green) 4%, transparent)",
                borderRadius: "10px",
              }}
            >
              No wallet? No problem — close this panel and browse the hub as a
              guest (read-only). Signing in only matters when you want to edit.
            </div>

            <div className="flex flex-col items-center gap-4">
              {(hasMetaMask || hasSubWallet) && (
                <>
                  <div className="flex gap-2 w-full">
                    {hasMetaMask && (
                      <button
                        onClick={() => connectWallet("metamask")}
                        disabled={authLoading}
                        className="pixel-btn pixel-btn-amber flex-1 text-[13px] py-3"
                        style={{ letterSpacing: "0.04em" }}
                      >
                        {authLoading ? (
                          <span className="animate-pulse">SIGNING...</span>
                        ) : (
                          "MetaMask"
                        )}
                      </button>
                    )}
                    {hasSubWallet && (
                      <button
                        onClick={() => connectWallet("subwallet")}
                        disabled={authLoading}
                        className="pixel-btn pixel-btn-blue flex-1 text-[13px] py-3"
                        style={{ letterSpacing: "0.04em" }}
                      >
                        {authLoading ? (
                          <span className="animate-pulse">SIGNING...</span>
                        ) : (
                          "SubWallet"
                        )}
                      </button>
                    )}
                  </div>

                  <div className="flex items-center gap-3 w-full">
                    <div className="flex-1 border-t border-crt-green/10" />
                    <span className="text-[13px] text-crt-green/20">OR</span>
                    <div className="flex-1 border-t border-crt-green/10" />
                  </div>
                </>
              )}

              <button
                onClick={connectLocal}
                disabled={authLoading}
                className="pixel-btn w-full text-[13px] py-3"
                style={{ letterSpacing: "0.04em" }}
              >
                {authLoading && !hasMetaMask && !hasSubWallet ? (
                  <span className="animate-pulse">GENERATING KEY...</span>
                ) : (
                  "Use Local Key"
                )}
              </button>

              <div className="flex items-center gap-3 w-full">
                <div className="flex-1 border-t border-crt-green/10" />
                <span className="text-[13px] text-crt-green/20">OR</span>
                <div className="flex-1 border-t border-crt-green/10" />
              </div>

              {!showPasswordInput ? (
                <button
                  onClick={() => setShowPasswordInput(true)}
                  className="pixel-btn w-full text-[13px] py-3"
                  style={{ letterSpacing: "0.04em" }}
                >
                  Use Password Key
                </button>
              ) : (
                <div className="w-full space-y-2">
                  <input
                    type="password"
                    value={passwordInput}
                    onChange={(e) => setPasswordInput(e.target.value)}
                    placeholder="Enter password..."
                    className="w-full px-3 py-2 text-[13px] bg-crt-dark text-crt-green border-2 border-crt-amber/40 font-pixel"
                    style={{ letterSpacing: "0.01em" }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && passwordInput.trim()) connectWithPassword(passwordInput.trim());
                    }}
                  />
                  <button
                    onClick={() => passwordInput.trim() && connectWithPassword(passwordInput.trim())}
                    disabled={authLoading || !passwordInput.trim()}
                    className="pixel-btn pixel-btn-amber w-full text-[13px] py-3"
                    style={{ letterSpacing: "0.04em" }}
                  >
                    {authLoading ? (
                      <span className="animate-pulse">DERIVING KEY...</span>
                    ) : (
                      "Connect with Password"
                    )}
                  </button>
                </div>
              )}

              <div className="text-[13px] text-crt-green/25 text-center">
                Password derives a deterministic wallet key via keccak256.
                Throwaway wallet — view / copy the password and private key
                anytime from the ACCOUNT panel.
              </div>
            </div>

            {authError && (
              <div className="mt-4 border-2 border-crt-red/60 p-3" style={{ background: "rgba(239,68,68,0.05)" }}>
                <div className="text-[14px] text-crt-red text-center">{authError}</div>
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="p-4 text-[12px] text-center" style={{ color: "var(--text-tertiary)", opacity: 0.5 }}>
            Bismillah — Mod AI v1.0 — Powered by Rust + Next.js
          </div>
    </div>
  );

  // ═══════════════════════════════════════════════════════════════════
  // RENDER FUNCTIONS
  // ═══════════════════════════════════════════════════════════════════

  const renderDirectoryTree = (tree: any[], depth: number = 0): JSX.Element[] => {
    return tree.map((item, idx) => {
      const isExpanded = expandedDirs.has(item.path);
      const isDir = item.type === "directory";

      return (
        <div key={item.path + idx} style={{ marginLeft: `${depth * 12}px` }}>
          <div
            className="flex items-center gap-1.5 py-1 px-2 hover:bg-crt-green/5 cursor-pointer transition-colors text-[14px]"
            onClick={() => {
              if (isDir) {
                const newExpanded = new Set(expandedDirs);
                if (isExpanded) {
                  newExpanded.delete(item.path);
                } else {
                  newExpanded.add(item.path);
                }
                setExpandedDirs(newExpanded);
              } else {
                loadFileContent(item.path);
              }
            }}
          >
            {isDir ? (
              <span className="text-crt-amber/70">{isExpanded ? "📂" : "📁"}</span>
            ) : (
              <span className="text-crt-blue/50">📄</span>
            )}
            <span className="truncate font-code" style={{ fontSize: "14px", color: isDir ? "var(--crt-green)" : getFileTypeColor(item.name) }}>
              {item.name}
            </span>
            {!isDir && item.cid && (
              <span
                onClick={(e) => { e.stopPropagation(); copyCid(item.path, item.cid); }}
                className="ml-auto shrink-0 font-code text-[11px] px-1 transition-opacity hover:opacity-100"
                style={{ color: "var(--crt-purple, #c084fc)", opacity: copiedCid === item.path ? 1 : 0.35 }}
                title={`CID ${item.cid} — click to copy`}
              >
                {copiedCid === item.path ? "✓" : item.cid.slice(0, 8)}
              </span>
            )}
          </div>
          {isDir && isExpanded && item.children && renderDirectoryTree(item.children, depth + 1)}
        </div>
      );
    });
  };

  const getLanguageFromPath = (filePath: string): string => {
    const ext = filePath.split(".").pop()?.toLowerCase() || "";
    const map: Record<string, string> = {
      py: "python", js: "javascript", jsx: "javascript", ts: "typescript", tsx: "typescript",
      rs: "rust", go: "go", java: "java", cpp: "cpp", c: "c", sh: "bash",
      json: "json", md: "markdown", yaml: "yaml", yml: "yaml", toml: "toml",
      xml: "xml", html: "html", css: "css", sql: "sql", rb: "ruby",
    };
    return map[ext] || "text";
  };

  const renderChangelogTab = () => {
    if (changelogLoading) {
      return (
        <div className="flex-1 flex items-center justify-center h-full">
          <span className="text-[14px] text-crt-green/30 uppercase" style={{ letterSpacing: "0.01em" }}>
            Loading changelog...
          </span>
        </div>
      );
    }

    if (changelogEntries.length === 0) {
      return (
        <div className="flex-1 flex flex-col items-center justify-center gap-4 h-full p-6">
          <span className="text-[48px] text-crt-green/10">v0</span>
          <span className="text-[14px] text-crt-green/30 uppercase" style={{ letterSpacing: "0.01em" }}>
            No versions yet
          </span>
          <p className="text-[14px] text-crt-green/20 text-center max-w-xs">
            Use <code className="text-crt-amber/40">c.snapshot(&quot;description&quot;)</code> from the Python SDK to create
            the first version. Each version is stored permanently on IPFS.
          </p>
          <button
            onClick={fetchChangelog}
            className="pixel-btn text-[14px] px-3 py-1.5 mt-2"
            style={{ background: "var(--accent-color)", color: "#fff" }}
          >
            REFRESH
          </button>
        </div>
      );
    }

    return (
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Changelog Header */}
        <div
          className="px-4 py-2 border-b flex items-center justify-between"
          style={{ borderColor: "var(--border-color)", background: "rgba(59,130,246,0.02)" }}
        >
          <div>
            <span className="text-[14px] text-crt-blue/70 uppercase" style={{ letterSpacing: "0.02em" }}>
              VERSION HISTORY
            </span>
            <div className="text-[14px] text-crt-green/40 mt-0.5">
              {changelogEntries.length} version{changelogEntries.length !== 1 ? "s" : ""} on IPFS
            </div>
          </div>
          <button
            onClick={fetchChangelog}
            className="text-[14px] text-crt-green/40 hover:text-crt-green/70 transition-colors"
          >
            REFRESH
          </button>
        </div>

        <div className="flex-1 flex flex-col overflow-hidden">
          {/* Version List */}
          <div className="overflow-y-auto" style={{ maxHeight: selectedVersion ? "40%" : "100%" }}>
            {changelogEntries.map((entry, i) => {
              const isSelected = selectedVersion === entry.version;
              const isLatest = i === 0;
              return (
                <div
                  key={entry.version}
                  onClick={() => fetchVersionDetail(entry.version)}
                  className={`px-4 py-3 cursor-pointer border-b transition-all ${
                    isSelected ? "bg-crt-blue/10" : "hover:bg-white/[0.02]"
                  }`}
                  style={{ borderColor: "var(--border-color)" }}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className={`text-[13px] font-bold ${isLatest ? "text-crt-green" : "text-crt-amber/70"}`}>
                        v{entry.version}
                      </span>
                      {isLatest && (
                        <span className="text-[13px] px-1.5 py-0.5 bg-crt-green/20 text-crt-green rounded" style={{ letterSpacing: "0.01em" }}>
                          LATEST
                        </span>
                      )}
                    </div>
                    <span className="text-[14px] text-crt-green/30">{entry.date}</span>
                  </div>
                  <div className="text-[14px] text-crt-green/50 mt-1">{entry.description}</div>
                  <div className="flex items-center gap-3 mt-1.5">
                    <span className="text-[13px] text-crt-green/25 font-mono">
                      {entry.cid?.substring(0, 20)}...
                    </span>
                    {entry.file_count && (
                      <span className="text-[13px] text-crt-green/25">
                        {entry.file_count} files
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Version Detail Panel */}
          {selectedVersion && (
            <div className="flex-1 border-t overflow-y-auto" style={{ borderColor: "var(--border-color-strong)" }}>
              <div
                className="px-4 py-2 border-b flex items-center justify-between sticky top-0 z-10"
                style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}
              >
                <span className="text-[14px] text-crt-blue uppercase" style={{ letterSpacing: "0.01em" }}>
                  v{selectedVersion}
                </span>
                <button
                  onClick={() => { setSelectedVersion(null); setVersionDetail(null); }}
                  className="text-[14px] text-crt-red/50 hover:text-crt-red/70"
                >
                  CLOSE
                </button>
              </div>

              {versionDetailLoading ? (
                <div className="p-4 text-[14px] text-crt-green/30">Loading version data...</div>
              ) : versionDetail ? (
                <div className="p-4">
                  <div className="text-[14px] text-crt-green/60 mb-3">
                    {versionDetail.version?.description || "No description"}
                  </div>

                  {/* CID with link + share QR */}
                  <div className="mb-3">
                    <div className="flex items-center justify-between mb-1">
                      <div className="text-[14px] text-crt-green/30" style={{ letterSpacing: "0.01em" }}>IPFS CID</div>
                      {versionDetail.version?.cid && (
                        <button
                          onClick={() => shareCidQr(versionDetail.version.cid, selectedModule, versionDetail.gateway)}
                          className="text-[11px] px-1.5 py-0.5 border border-crt-green/25 text-crt-green/50 hover:text-crt-green hover:border-crt-green/50 transition-all"
                          title="Share this snapshot as a QR code"
                        >
                          ⛶ QR
                        </button>
                      )}
                    </div>
                    <a
                      href={versionDetail.gateway}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-[14px] text-crt-blue/70 hover:text-crt-blue font-mono break-all transition-colors"
                    >
                      {versionDetail.version?.cid}
                    </a>
                  </div>

                  {/* Metadata */}
                  <div className="grid grid-cols-2 gap-2 text-[14px]">
                    <div>
                      <span className="text-crt-green/30">Date</span>
                      <div className="text-crt-green/60">{versionDetail.version?.date}</div>
                    </div>
                    {versionDetail.version?.file_count && (
                      <div>
                        <span className="text-crt-green/30">Files</span>
                        <div className="text-crt-green/60">{versionDetail.version.file_count}</div>
                      </div>
                    )}
                  </div>

                  {/* Restore hint */}
                  <div className="mt-4 p-2 border rounded" style={{ borderColor: "var(--border-color)", background: "var(--bg-tint)" }}>
                    <div className="text-[14px] text-crt-amber/50 mb-1">RESTORE THIS VERSION</div>
                    <code className="text-[14px] text-crt-green/40 block">
                      c.restore_version(&quot;{selectedVersion}&quot;, dry_run=False)
                    </code>
                  </div>
                </div>
              ) : (
                <div className="p-4 text-[14px] text-crt-green/30">Select a version to view details</div>
              )}
            </div>
          )}
        </div>
      </div>
    );
  };

  // Click-to-copy for CID chips, with brief ✓ feedback keyed by chip id.
  const copyCid = (key: string, value: string) => {
    navigator.clipboard?.writeText(value);
    setCopiedCid(key);
    setTimeout(() => setCopiedCid((p) => (p === key ? null : p)), 1200);
  };

  // Tree node for a path — the /files/tree walk carries per-file cid + size,
  // which the open-file header surfaces next to the line count.
  const findTreeNode = (tree: any[], path: string): any | null => {
    for (const item of tree) {
      if (item.path === path) return item;
      if (item.children) {
        const hit = findTreeNode(item.children, path);
        if (hit) return hit;
      }
    }
    return null;
  };

  // Path of the dir the FILES browser is showing, "~"-abbreviated for display.
  const filesDisplayPath = () => {
    const p = workDir || (selectedJob ? jobs.find(j => j.id === selectedJob)?.work_dir : "") || "~/mod";
    return p.replace(/^\/Users\/[^/]+/, "~").replace(/^\/home\/[^/]+/, "~").replace(/^\/root(?=\/|$)/, "~");
  };

  // Compact control cluster (root hash + search/grep/refresh/float) shared by
  // the docked CODE toolbar row and the floating FILES panel title bar.
  // `float: false` hides the float-toggle where a dock button already exists.
  const filesToolbarControls = (opts: { float?: boolean } = {}) => (
    <div className="flex items-center gap-1.5 shrink-0">
      {treeRootHash && (
        <button
          onClick={() => copyCid("root", treeRootHash)}
          className="text-[11px] px-1.5 py-0.5 border font-code transition-all"
          style={{
            borderColor: "color-mix(in srgb, var(--crt-purple, #c084fc) 35%, transparent)",
            color: "var(--crt-purple, #c084fc)",
            background: "color-mix(in srgb, var(--crt-purple, #c084fc) 6%, transparent)",
            letterSpacing: "0.02em",
          }}
          title={`Root tree hash (snapshot CID of this dir): ${treeRootHash} — click to copy`}
        >
          {copiedCid === "root" ? "✓ copied" : `⌬ ${treeRootHash.slice(0, 10)}`}
        </button>
      )}
      <button
        onClick={() => {
          setInlineSearchMode((prev) => prev === "off" ? "files" : "off");
          setInlineSearchQuery("");
          setInlineSearchResults([]);
          setTimeout(() => inlineSearchRef.current?.focus(), 50);
        }}
        className={`text-[13px] px-1.5 py-0.5 border transition-all uppercase ${
          inlineSearchMode !== "off"
            ? "border-crt-blue text-crt-blue bg-crt-blue/10"
            : "border-crt-blue/30 text-crt-blue/60 hover:text-crt-blue hover:border-crt-blue"
        }`}
        title="Search — file names (Ctrl+P) or contents (Ctrl+Shift+F)"
        style={{ letterSpacing: "0" }}
      >
        🔍
      </button>
      <button
        onClick={() => fetchDirectoryTree()}
        className="text-[13px] px-1.5 py-0.5 border border-crt-green/20 text-crt-green/40 hover:text-crt-green/70 hover:border-crt-green/40 transition-all"
        title="Refresh"
      >
        ↻
      </button>
      {opts.float !== false && (
        <button
          onClick={() => setFilesPanelFloating(f => !f)}
          className={`text-[13px] px-1.5 py-0.5 border transition-all ${
            filesPanelFloating
              ? "border-crt-amber/50 text-crt-amber/70 hover:text-crt-amber hover:border-crt-amber"
              : "border-crt-green/20 text-crt-green/40 hover:text-crt-green/70 hover:border-crt-green/40"
          }`}
          title={filesPanelFloating ? "Dock panel" : "Float panel"}
        >
          {filesPanelFloating ? "⊡" : "⊞"}
        </button>
      )}
    </div>
  );

  const renderDirectoryTab = () => {
    const filePanesAll = paneCols.flatMap(c => c.panes);
    const filePanesOpen = filePanesAll.length > 0;
    const filePanesExpanded = filePanesAll.some(p => !p.collapsed);
    // Ghost icon button shared by the pane-header split/collapse/close cluster
    const paneBtnCls = "w-5 h-5 flex items-center justify-center rounded text-[12px] leading-none transition-all hover:bg-white/10";
    return (
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Inline search (toggled from the CODE toolbar / floating title bar) */}
        {inlineSearchMode !== "off" && (
          <div className="px-3 pb-2 shrink-0" style={{ borderBottom: `1px solid ${subtleBorder}`, background: tintBg }}>
              <div className="flex items-center gap-2 px-2 py-1 mt-1 border border-crt-blue/30 bg-black/40" style={{ borderRadius: "8px" }}>
                {/* Mode toggle lives in the bar — the toolbar has a single search button */}
                {(["files", "grep"] as const).map((mode) => (
                  <button
                    key={mode}
                    onClick={() => {
                      setInlineSearchMode(mode);
                      inlineSearchRef.current?.focus();
                    }}
                    className={`text-[10px] px-1.5 py-0.5 border uppercase transition-all shrink-0 ${
                      inlineSearchMode === mode
                        ? "border-crt-blue text-crt-blue bg-crt-blue/10"
                        : "border-crt-blue/20 text-crt-blue/40 hover:text-crt-blue/70 hover:border-crt-blue/50"
                    }`}
                    title={mode === "files" ? "Search file names (Ctrl+P)" : "Search file contents (Ctrl+Shift+F)"}
                  >
                    {mode === "files" ? "NAME" : "TEXT"}
                  </button>
                ))}
                <input
                  ref={inlineSearchRef}
                  type="text"
                  value={inlineSearchQuery}
                  onChange={(e) => setInlineSearchQuery(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Escape") {
                      setInlineSearchMode("off");
                      setInlineSearchQuery("");
                      setInlineSearchResults([]);
                    } else if (e.key === "ArrowDown") {
                      e.preventDefault();
                      setInlineSelectedIndex((p) => Math.min(p + 1, inlineSearchResults.length - 1));
                    } else if (e.key === "ArrowUp") {
                      e.preventDefault();
                      setInlineSelectedIndex((p) => Math.max(p - 1, 0));
                    } else if (e.key === "Enter" && inlineSearchResults[inlineSelectedIndex]) {
                      const r = inlineSearchResults[inlineSelectedIndex];
                      loadFileContent(r.path);
                      navigateToFile(r.path);
                      setInlineSearchMode("off");
                      setInlineSearchQuery("");
                      setInlineSearchResults([]);
                    }
                  }}
                  placeholder={inlineSearchMode === "files" ? "Search files by name..." : "Search file contents..."}
                  className="flex-1 bg-transparent border-none outline-none text-[13px] text-white font-code"
                  autoFocus
                />
                {inlineSearchLoading && (
                  <span className="text-[13px] text-crt-green/40 animate-pulse">...</span>
                )}
                <span className="text-[11px] text-white/20">ESC</span>
              </div>

              {/* Search Results */}
              {inlineSearchResults.length > 0 && (
                <div className="mt-1 max-h-[240px] overflow-y-auto border border-white/5 bg-black/60" style={{ borderRadius: "8px" }}>
                  {inlineSearchResults.map((result, idx) => (
                    <div
                      key={inlineSearchMode === "files" ? result.path : `${result.path}-${result.line}-${idx}`}
                      onClick={() => {
                        loadFileContent(result.path);
                        navigateToFile(result.path);
                        setInlineSearchMode("off");
                        setInlineSearchQuery("");
                        setInlineSearchResults([]);
                      }}
                      onMouseEnter={() => setInlineSelectedIndex(idx)}
                      className="px-2 py-1.5 cursor-pointer transition-colors"
                      style={{
                        backgroundColor: idx === inlineSelectedIndex ? "rgba(59,130,246,0.15)" : "transparent",
                        borderLeft: idx === inlineSelectedIndex ? "2px solid #00aaff" : "2px solid transparent",
                      }}
                    >
                      {inlineSearchMode === "files" ? (
                        <>
                          <div className="text-[14px] font-code" style={{ color: getFileTypeColor(result.filename || result.path) }}>{result.filename}</div>
                          <div className="text-[14px] text-white/30 font-code truncate">{result.path}</div>
                        </>
                      ) : (
                        <>
                          <div className="flex items-center gap-1.5">
                            <span className="text-[14px] font-code" style={{ color: getFileTypeColor(result.filename || result.path) }}>{result.filename}</span>
                            <span className="text-[14px] text-white/30 font-code">:{result.line}</span>
                          </div>
                          <div className="text-[14px] text-white/50 font-code truncate whitespace-pre">{result.content}</div>
                        </>
                      )}
                    </div>
                  ))}
                </div>
              )}
              {inlineSearchQuery && !inlineSearchLoading && inlineSearchResults.length === 0 && (
                <div className="mt-1 text-center text-[14px] text-white/20 py-2 font-code">No results</div>
              )}
            </div>
          )}

        {/* Side-by-side: file tree + splittable pane grid */}
        <div className="flex-1 flex overflow-hidden">
          {/* Left: File tree — takes the leftover width when every pane is collapsed */}
          <div
            className={`overflow-y-auto p-2 border-r ${filePanesExpanded ? "shrink-0" : "flex-1"}`}
            style={{
              width: filePanesExpanded ? "200px" : undefined,
              borderColor: "var(--border-color)",
            }}
          >
            {directoryTree.length > 0 ? (
              renderDirectoryTree(directoryTree, 0)
            ) : (
              <div className="flex flex-col items-center justify-center h-full gap-3">
                <span className="text-[13px] text-crt-green/50">📂 No files loaded</span>
                {directoryTreeError ? (
                  <span className="text-[13px] text-crt-amber/70 text-center px-4">
                    {directoryTreeError.includes("auth token")
                      ? "⚠ Sign in to browse files — the file API requires authentication"
                      : `⚠ ${directoryTreeError}`}
                  </span>
                ) : (
                  <span className="text-[14px] text-crt-green/30">Select a module above or click refresh</span>
                )}
                <button
                  onClick={() => fetchDirectoryTree()}
                  className="text-[14px] px-3 py-1.5 border border-crt-green/30 text-crt-green/60 hover:text-crt-green hover:border-crt-green transition-all"
                >
                  ↻ LOAD FILES
                </button>
              </div>
            )}
          </div>

          {/* Right: splittable pane grid — columns side-by-side, panes stacked */}
          {filePanesOpen && (
            <div className={`flex overflow-hidden min-w-0 ${filePanesExpanded ? "flex-1" : "shrink-0"}`}>
              {paneCols.map((col, ci) => {
                // A column whose panes are all collapsed folds into a vertical rail
                if (col.panes.every(p => p.collapsed)) {
                  return (
                    <div
                      key={col.id}
                      onClick={() => setPaneCols(cols => cols.map(c => c.id === col.id ? { ...c, panes: c.panes.map(p => ({ ...p, collapsed: false })) } : c))}
                      className="shrink-0 flex flex-col items-center gap-2.5 py-2.5 cursor-pointer transition-colors hover:bg-white/[0.04] group"
                      style={{ width: "30px", borderLeft: ci > 0 ? `1px solid ${subtleBorder}` : undefined, background: "rgba(255,255,255,0.015)" }}
                      title="Expand"
                    >
                      <span className="text-[10px] opacity-40 group-hover:opacity-90 transition-opacity" style={{ color: "var(--text-secondary)" }}>◂</span>
                      {col.panes.map(p => (
                        <span
                          key={p.id}
                          className="font-code text-[11px]"
                          style={{
                            writingMode: "vertical-rl",
                            maxHeight: "150px",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                            color: getFileTypeColor(p.file.split("/").pop() || ""),
                            opacity: 0.6,
                          }}
                        >
                          {p.file.split("/").pop()}
                        </span>
                      ))}
                    </div>
                  );
                }
                return (
                  <div
                    key={col.id}
                    className="flex-1 flex flex-col overflow-hidden min-w-0"
                    style={{ borderLeft: ci > 0 ? `1px solid ${subtleBorder}` : undefined }}
                  >
                    {col.panes.map((pane, pi) => {
                      const name = pane.file.split("/").pop() || pane.file;
                      // Collapsed pane: a slim bar inside its column
                      if (pane.collapsed) {
                        return (
                          <div
                            key={pane.id}
                            onClick={() => { patchPane(pane.id, { collapsed: false }); setActivePaneId(pane.id); }}
                            className="shrink-0 flex items-center gap-2 px-2.5 cursor-pointer transition-colors hover:bg-white/[0.04] group"
                            style={{ height: "28px", borderTop: pi > 0 ? `1px solid ${subtleBorder}` : undefined, background: "rgba(255,255,255,0.015)" }}
                            title="Expand"
                          >
                            <span className="text-[10px] opacity-40 group-hover:opacity-90 transition-opacity" style={{ color: "var(--text-secondary)" }}>▸</span>
                            <span className="font-code text-[12px] truncate" style={{ color: getFileTypeColor(name), opacity: 0.7 }}>{name}</span>
                            <button
                              onClick={(e) => { e.stopPropagation(); closePane(pane.id); }}
                              className={`${paneBtnCls} opacity-0 group-hover:opacity-60 hover:!opacity-100`}
                              style={{ color: "var(--crt-red, #f87171)" }}
                              title="Close"
                            >
                              ✕
                            </button>
                          </div>
                        );
                      }
                      const active = pane.id === activePaneId;
                      const editing = editingPaneId === pane.id;
                      return (
                        <div
                          key={pane.id}
                          className="flex-1 flex flex-col overflow-hidden min-h-0 min-w-0"
                          style={{ borderTop: pi > 0 ? `1px solid ${subtleBorder}` : undefined }}
                          onMouseDown={() => setActivePaneId(pane.id)}
                        >
                          {/* Pane header — full meta cluster only on the active pane.
                              Everything sits on one line on the left: no
                              justify-between stretch, so there's no dead gap
                              pushing the controls to the far right edge. */}
                          <div
                            className="px-2.5 py-1 border-b flex items-center gap-2 shrink-0 transition-colors"
                            style={{
                              borderColor: "var(--border-color)",
                              background: active ? "rgba(59,130,246,0.05)" : "rgba(255,255,255,0.01)",
                              boxShadow: active ? "inset 0 -1px 0 rgba(96,165,250,0.35)" : undefined,
                            }}
                          >
                            <div className="flex items-center gap-2 min-w-0">
                              <span
                                className="text-[13px] font-bold shrink-0 font-code transition-colors"
                                style={{ color: active ? "var(--crt-blue, #60a5fa)" : "var(--text-secondary)" }}
                              >
                                {name}
                              </span>
                              <span className="text-[12px] text-crt-green/30 uppercase shrink-0 font-code">
                                {getLanguageFromPath(pane.file)}
                              </span>
                              {/* Toolbar already shows the browse dir — only render the part it doesn't */}
                              {active && (() => {
                                const root = filesDisplayPath();
                                const rel = pane.file.startsWith(root + "/") ? pane.file.slice(root.length + 1) : pane.file;
                                return rel === name ? null : (
                                  <span className="text-[11px] text-crt-green/20 truncate font-code" title={pane.file}>
                                    {rel}
                                  </span>
                                );
                              })()}
                            </div>
                            <div className="flex items-center gap-1.5 shrink-0">
                              {active && (() => {
                                const node = findTreeNode(directoryTree, pane.file);
                                const bytes = editing
                                  ? new TextEncoder().encode(editBuffer).length
                                  : node?.size ?? new TextEncoder().encode(pane.content).length;
                                const cidKey = `viewer:${pane.id}:${pane.file}`;
                                return (
                                  <>
                                    {node?.cid && (
                                      <span
                                        onClick={() => copyCid(cidKey, node.cid)}
                                        className="font-code text-[11px] cursor-pointer transition-opacity hover:opacity-100"
                                        style={{ color: "var(--crt-purple, #c084fc)", opacity: copiedCid === cidKey ? 1 : 0.5 }}
                                        title={`CID ${node.cid} — click to copy`}
                                      >
                                        {copiedCid === cidKey ? "✓ copied" : `⌬ ${node.cid.slice(0, 8)}`}
                                      </span>
                                    )}
                                    <span className="text-[12px] text-crt-green/20 font-code" title={`${bytes} bytes`}>
                                      {(editing ? editBuffer : pane.content).split("\n").length}L · {bytes.toLocaleString()}B
                                    </span>
                                  </>
                                );
                              })()}
                              {editing ? (
                                <>
                                  <button
                                    onClick={saveFile}
                                    disabled={savingFile}
                                    className="text-[12px] px-2 py-0.5 border border-crt-green/40 text-crt-green/70 hover:text-crt-green hover:border-crt-green transition-all disabled:opacity-40"
                                    title="Save file (⌘S)"
                                  >
                                    {savingFile ? "..." : "SAVE"}
                                  </button>
                                  <button
                                    onClick={() => setEditingPaneId(null)}
                                    className="text-[12px] px-1.5 py-0.5 border border-crt-yellow/30 text-crt-yellow/50 hover:text-crt-yellow hover:border-crt-yellow transition-all"
                                    title="Cancel editing"
                                  >
                                    ESC
                                  </button>
                                </>
                              ) : (
                                <button
                                  onClick={() => { setEditBuffer(pane.content); setEditingPaneId(pane.id); setActivePaneId(pane.id); }}
                                  className="text-[12px] px-2 py-0.5 border border-crt-blue/30 text-crt-blue/50 hover:text-crt-blue hover:border-crt-blue transition-all"
                                  title="Edit file"
                                >
                                  EDIT
                                </button>
                              )}
                              {/* Split / collapse / close — ghost cluster */}
                              <div className="flex items-center" style={{ color: "var(--text-tertiary)" }}>
                                <button onClick={() => splitPane(pane.id, "right")} className={`${paneBtnCls} opacity-40 hover:opacity-100`} title="Split right">◨</button>
                                <button onClick={() => splitPane(pane.id, "down")} className={`${paneBtnCls} opacity-40 hover:opacity-100`} title="Split down">⬓</button>
                                <button onClick={() => patchPane(pane.id, { collapsed: true })} className={`${paneBtnCls} opacity-40 hover:opacity-100`} title="Collapse">–</button>
                                <button
                                  onClick={() => closePane(pane.id)}
                                  className={`${paneBtnCls} opacity-40 hover:opacity-100`}
                                  style={{ color: "var(--crt-red, #f87171)" }}
                                  title="Close"
                                >
                                  ✕
                                </button>
                              </div>
                            </div>
                          </div>
                          {/* Pane content */}
                          <div className="flex-1 overflow-auto">
                            {pane.loading ? (
                              <div className="flex items-center justify-center h-full">
                                <span className="text-[14px] text-crt-blue animate-pulse">Loading file...</span>
                              </div>
                            ) : editing ? (
                              <textarea
                                value={editBuffer}
                                onChange={(e) => setEditBuffer(e.target.value)}
                                onKeyDown={(e) => {
                                  if (e.key === "s" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); saveFile(); }
                                  if (e.key === "Escape") { setEditingPaneId(null); }
                                  if (e.key === "Tab") {
                                    e.preventDefault();
                                    const start = e.currentTarget.selectionStart;
                                    const end = e.currentTarget.selectionEnd;
                                    setEditBuffer(editBuffer.substring(0, start) + "  " + editBuffer.substring(end));
                                    setTimeout(() => { e.currentTarget.selectionStart = e.currentTarget.selectionEnd = start + 2; }, 0);
                                  }
                                }}
                                className="w-full h-full m-0 p-3 text-[13px] leading-relaxed font-code whitespace-pre resize-none bg-transparent border-0 outline-none"
                                style={{ color: "var(--text-primary)", tabSize: 2 }}
                                spellCheck={false}
                                autoFocus
                              />
                            ) : (
                              <pre
                                className="m-0 p-3 text-[13px] leading-relaxed font-code whitespace-pre"
                                style={{ color: "var(--text-primary)" }}
                              >
                                {pane.content.split("\n").map((line, i) => (
                                  <div key={i} className="flex hover:bg-crt-green/5">
                                    <span
                                      className="select-none text-right pr-3 shrink-0"
                                      style={{ color: "var(--text-tertiary)", opacity: 0.3, minWidth: "3em" }}
                                    >
                                      {i + 1}
                                    </span>
                                    <span className="flex-1">{line || " "}</span>
                                  </div>
                                ))}
                              </pre>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    );
  };

  const renderInputTab = () => {
    return (
      <div className="flex flex-col overflow-hidden flex-1">
        {/* NEW TASK FORM - Sleek unified input with Prompt Manager */}
        <div className="flex flex-col flex-1" style={{ background: tintBg }}>

          {/* Prompt Manager Panel (compose/edit overlay) */}
          {showPromptManager && (
            <div className="absolute inset-0 z-50 flex items-center justify-center" style={{ background: "rgba(0,0,0,0.6)" }}>
              <div
                className="w-[560px] max-h-[80vh] border rounded-xl flex flex-col overflow-hidden"
                style={{ background: "var(--bg-primary)", borderColor: subtleBorderStrong }}
              >
                {/* Header */}
                <div className="flex items-center justify-between px-4 py-3 border-b" style={{ borderColor: subtleBorder, background: tintBg }}>
                  <span className="text-[13px] font-pixel uppercase" style={{ color: "var(--text-primary)" }}>
                    {editingPrompt ? "Edit Prompt" : "Compose Prompt"}
                  </span>
                  <button
                    onClick={() => { setShowPromptManager(false); setEditingPrompt(null); }}
                    className="text-[16px] transition-colors"
                    style={{ color: "var(--text-tertiary)" }}
                  >
                    ✕
                  </button>
                </div>
                {/* Form */}
                <div className="flex flex-col gap-3 p-4 overflow-y-auto flex-1">
                  <input
                    type="text"
                    value={promptDraft.title}
                    onChange={(e) => setPromptDraft(d => ({ ...d, title: e.target.value }))}
                    placeholder="Prompt title (optional)"
                    className="w-full px-3 py-2 text-[13px] border rounded bg-transparent outline-none"
                    style={{ borderColor: subtleBorder, color: "var(--text-primary)" }}
                    autoFocus
                  />
                  <textarea
                    value={promptDraft.body}
                    onChange={(e) => setPromptDraft(d => ({ ...d, body: e.target.value }))}
                    placeholder="Write your prompt here..."
                    className="w-full px-3 py-2 text-[14px] border rounded bg-transparent outline-none resize-none"
                    style={{ borderColor: subtleBorder, color: "var(--text-primary)", minHeight: "160px", lineHeight: "1.6" }}
                  />
                  <input
                    type="text"
                    value={promptDraft.tags}
                    onChange={(e) => setPromptDraft(d => ({ ...d, tags: e.target.value }))}
                    placeholder="Tags (comma separated)"
                    className="w-full px-3 py-2 text-[12px] border rounded bg-transparent outline-none"
                    style={{ borderColor: subtleBorder, color: "var(--text-tertiary)" }}
                  />
                  {/* Agent type selector */}
                  <div className="flex items-center gap-2">
                    <span className="text-[11px] shrink-0" style={{ color: "var(--text-tertiary)" }}>Agent</span>
                    <div className="flex gap-1 flex-wrap">
                      {AGENT_OPTIONS.map((a) => (
                        <button
                          key={a.value}
                          onClick={() => setPromptDraft(d => ({ ...d, agent_type: a.value }))}
                          className="px-2 py-1 text-[11px] border rounded transition-all font-pixel uppercase"
                          style={{
                            borderColor: promptDraft.agent_type === a.value ? "rgba(96,165,250,0.6)" : subtleBorder,
                            background: promptDraft.agent_type === a.value ? "rgba(96,165,250,0.1)" : "transparent",
                            color: promptDraft.agent_type === a.value ? "#60a5fa" : "var(--text-tertiary)",
                          }}
                        >
                          {a.icon} {a.label}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
                {/* Actions */}
                <div className="flex items-center justify-between px-4 py-3 border-t" style={{ borderColor: subtleBorder, background: tintBg }}>
                  <span className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                    {editingPrompt ? `Created ${timeSince(editingPrompt.created_at)}` : "New prompt"}
                  </span>
                  <div className="flex gap-2">
                    <button
                      onClick={() => { setShowPromptManager(false); setEditingPrompt(null); }}
                      className="px-3 py-1.5 text-[12px] border rounded transition-colors"
                      style={{ borderColor: subtleBorder, color: "var(--text-secondary)" }}
                    >
                      Cancel
                    </button>
                    {editingPrompt && (
                      <button
                        onClick={() => { loadPromptIntoInput(editingPrompt); }}
                        className="px-3 py-1.5 text-[12px] border rounded transition-colors"
                        style={{ borderColor: "rgba(96,165,250,0.3)", color: "#60a5fa" }}
                      >
                        Load
                      </button>
                    )}
                    <button
                      onClick={saveDraft}
                      disabled={!promptDraft.body.trim()}
                      className="pixel-btn text-[12px] py-1.5 px-4 uppercase"
                    >
                      {editingPrompt ? "Update" : "Save"}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Personality Manager Modal */}
          {showPersonalityManager && (
            <div className="absolute inset-0 z-50 flex items-center justify-center" style={{ background: "rgba(0,0,0,0.6)" }}>
              <div
                className="w-[620px] max-h-[85vh] border rounded-xl flex flex-col overflow-hidden"
                style={{ background: "var(--bg-primary)", borderColor: subtleBorderStrong }}
              >
                {/* Header */}
                <div className="flex items-center justify-between px-4 py-3 border-b" style={{ borderColor: subtleBorder, background: tintBg }}>
                  <span className="text-[13px] font-pixel uppercase" style={{ color: "var(--text-primary)" }}>
                    {editingPersonality ? "Edit Personality" : "New Personality"}
                  </span>
                  <button
                    onClick={() => { setShowPersonalityManager(false); setEditingPersonality(null); setCreatingPersonality(false); }}
                    className="text-[16px] transition-colors"
                    style={{ color: "var(--text-tertiary)" }}
                  >
                    ✕
                  </button>
                </div>

                {/* If not editing/creating, show personality list */}
                {!editingPersonality && !creatingPersonality ? (
                  <div className="flex flex-col overflow-hidden flex-1">
                    <div className="flex-1 overflow-y-auto">
                      {personalities.map(p => (
                        <div
                          key={p.id}
                          className="group flex items-center gap-3 px-4 py-3 border-b transition-colors cursor-pointer"
                          style={{ borderColor: `${subtleBorder}66` }}
                          onMouseEnter={(e) => (e.currentTarget.style.background = cardHoverBg)}
                          onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                          onClick={() => startEditPersonality(p)}
                        >
                          <span className="text-[18px] shrink-0 w-7 text-center" style={{ color: agentType === p.id ? "#60a5fa" : "var(--text-secondary)" }}>
                            {p.icon}
                          </span>
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2">
                              <span className="text-[13px] font-pixel uppercase" style={{ color: "var(--text-primary)" }}>
                                {p.name}
                              </span>
                              {p.builtin && (
                                <span className="text-[9px] px-1.5 py-0.5 rounded" style={{ background: "rgba(100,116,139,0.15)", color: "var(--text-tertiary)" }}>
                                  builtin
                                </span>
                              )}
                              {agentType === p.id && (
                                <span className="text-[9px] px-1.5 py-0.5 rounded" style={{ background: "rgba(96,165,250,0.15)", color: "#60a5fa" }}>
                                  active
                                </span>
                              )}
                            </div>
                            <div className="text-[11px] mt-0.5 truncate" style={{ color: "var(--text-tertiary)" }}>
                              {p.prompt ? p.prompt.slice(0, 100) + (p.prompt.length > 100 ? "..." : "") : "No system prompt (default behavior)"}
                            </div>
                          </div>
                          <div className="flex gap-1 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
                            <button
                              onClick={(e) => { e.stopPropagation(); setAgentType(p.id); safeSetItem("claude_jobs_agent", p.id); }}
                              className="text-[10px] px-2 py-1 border rounded transition-colors"
                              style={{ borderColor: "rgba(96,165,250,0.3)", color: "#60a5fa" }}
                              title="Set as active"
                            >
                              Use
                            </button>
                            {!p.builtin && (
                              <button
                                onClick={(e) => { e.stopPropagation(); deletePersonality(p.id); }}
                                className="text-[10px] px-2 py-1 border rounded transition-colors"
                                style={{ borderColor: "rgba(239,68,68,0.3)", color: "#ef4444" }}
                                title="Delete"
                              >
                                Del
                              </button>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                    {/* Footer with New button */}
                    <div className="flex items-center justify-between px-4 py-3 border-t" style={{ borderColor: subtleBorder, background: tintBg }}>
                      <span className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                        {personalities.length} personalit{personalities.length !== 1 ? "ies" : "y"}
                      </span>
                      <button
                        onClick={() => { setEditingPersonality(null); setCreatingPersonality(true); setPersonalityDraft({ name: "", icon: "☆", prompt: "" }); }}
                        className="pixel-btn text-[12px] py-1.5 px-4 uppercase"
                      >
                        + New
                      </button>
                    </div>
                  </div>
                ) : (
                  /* Edit/Create form */
                  <div className="flex flex-col overflow-hidden flex-1">
                    <div className="flex flex-col gap-3 p-4 overflow-y-auto flex-1">
                      {/* Name */}
                      <div className="flex gap-2">
                        <input
                          type="text"
                          value={personalityDraft.name}
                          onChange={(e) => setPersonalityDraft(d => ({ ...d, name: e.target.value }))}
                          placeholder="Personality name"
                          className="flex-1 px-3 py-2 text-[13px] border rounded bg-transparent outline-none"
                          style={{ borderColor: subtleBorder, color: "var(--text-primary)" }}
                          autoFocus
                        />
                      </div>
                      {/* Icon picker */}
                      <div className="flex items-center gap-2">
                        <span className="text-[11px] shrink-0" style={{ color: "var(--text-tertiary)" }}>Icon</span>
                        <div className="flex gap-1 flex-wrap">
                          {PERSONALITY_ICONS.map((ic) => (
                            <button
                              key={ic}
                              onClick={() => setPersonalityDraft(d => ({ ...d, icon: ic }))}
                              className="w-7 h-7 flex items-center justify-center text-[14px] border rounded transition-all"
                              style={{
                                borderColor: personalityDraft.icon === ic ? "rgba(96,165,250,0.6)" : subtleBorder,
                                background: personalityDraft.icon === ic ? "rgba(96,165,250,0.1)" : "transparent",
                                color: personalityDraft.icon === ic ? "#60a5fa" : "var(--text-tertiary)",
                              }}
                            >
                              {ic}
                            </button>
                          ))}
                        </div>
                      </div>
                      {/* System prompt */}
                      <div className="flex flex-col gap-1">
                        <span className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>System Prompt</span>
                        <textarea
                          value={personalityDraft.prompt}
                          onChange={(e) => setPersonalityDraft(d => ({ ...d, prompt: e.target.value }))}
                          placeholder="Define this personality's behavior, role, and instructions...&#10;&#10;Example: You are an expert security auditor. Focus on finding vulnerabilities, checking for injection attacks, and ensuring proper authentication patterns."
                          className="w-full px-3 py-2 text-[13px] border rounded bg-transparent outline-none resize-none font-mono"
                          style={{ borderColor: subtleBorder, color: "var(--text-primary)", minHeight: "200px", lineHeight: "1.6" }}
                        />
                        <span className="text-[10px]" style={{ color: "var(--text-tertiary)", opacity: 0.6 }}>
                          This prompt is appended to the system prompt when submitting jobs with this personality.
                        </span>
                      </div>
                    </div>
                    {/* Actions */}
                    <div className="flex items-center justify-between px-4 py-3 border-t" style={{ borderColor: subtleBorder, background: tintBg }}>
                      <button
                        onClick={() => {
                          if (editingPersonality) { setShowPersonalityManager(false); setEditingPersonality(null); }
                          else { setCreatingPersonality(false); setPersonalityDraft({ name: "", icon: ">_", prompt: "" }); }
                        }}
                        className="px-3 py-1.5 text-[12px] border rounded transition-colors"
                        style={{ borderColor: subtleBorder, color: "var(--text-secondary)" }}
                      >
                        {editingPersonality ? "Cancel" : "Back"}
                      </button>
                      <button
                        onClick={savePersonality}
                        disabled={!personalityDraft.name.trim()}
                        className="pixel-btn text-[12px] py-1.5 px-4 uppercase"
                      >
                        {editingPersonality ? "Update" : "Create"}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Main input area */}
          <div className="flex-1 flex flex-col overflow-hidden p-3 pt-2">
            <div
              className="border relative flex-1 flex flex-col overflow-hidden rounded-xl"
              style={{ background: darkOverlay, borderColor: subtleBorder }}
            >
              {/* Textarea */}
              <textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="Describe what Claude should do... (paste images here)  [Enter=submit, Shift+Enter=newline]"
                className="w-full p-4 pb-10 text-[16px] resize-none rounded-none bg-transparent border-0 outline-none flex-1"
                style={{ lineHeight: "1.6", color: "var(--text-primary)", minHeight: "60px" }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    submitJob();
                  }
                }}
                onPaste={(e) => {
                  const items = e.clipboardData?.items;
                  if (!items) return;
                  for (let i = 0; i < items.length; i++) {
                    if (items[i].type.startsWith("image/")) {
                      e.preventDefault();
                      const file = items[i].getAsFile();
                      if (!file) continue;
                      const reader = new FileReader();
                      reader.onload = () => {
                        const base64 = reader.result as string;
                        setImages((prev) => [...prev, { name: file.name || `image-${Date.now()}.png`, data: base64 }]);
                      };
                      reader.readAsDataURL(file);
                    }
                  }
                }}
              />

              {/* Bottom bar inside textarea border */}
              <div
                className="flex items-center gap-2 px-3 py-2 border-t border-crt-amber/20 shrink-0"
                style={{ background: darkOverlayStrong }}
              >
                {/* Model selector — actual versions, not just families */}
                <select
                  value={model}
                  onChange={(e) => {
                    setModel(e.target.value);
                    safeSetItem("claude_jobs_model", e.target.value);
                  }}
                  className="px-2 py-1 text-[13px] bg-transparent text-crt-green border border-crt-green/20 font-pixel uppercase cursor-pointer hover:border-crt-green/40 transition-colors"
                  style={{ maxWidth: "180px" }}
                  title={`Model: ${modelLabel(model)} (${model})`}
                >
                  {MODEL_OPTIONS.map(m => (
                    <option key={m.value} value={m.value}>{m.label}</option>
                  ))}
                </select>

                {/* Agent/Personality selector */}
                <div className="flex items-center">
                  <select
                    value={agentType}
                    onChange={(e) => {
                      setAgentType(e.target.value);
                      safeSetItem("claude_jobs_agent", e.target.value);
                    }}
                    className="px-2 py-1 text-[13px] bg-transparent text-crt-blue border border-crt-blue/20 font-pixel uppercase cursor-pointer hover:border-crt-blue/40 transition-colors rounded-l"
                    style={{ maxWidth: "160px", borderRight: "none" }}
                  >
                    {AGENT_OPTIONS.map((a) => (
                      <option key={a.value} value={a.value}>{a.icon} {a.label}</option>
                    ))}
                  </select>
                  <button
                    onClick={() => { setEditingPersonality(null); setCreatingPersonality(false); setPersonalityDraft({ name: "", icon: ">_", prompt: "" }); setShowPersonalityManager(true); }}
                    className="px-1.5 py-1 text-[11px] border border-crt-blue/20 text-crt-blue/40 hover:text-crt-blue hover:border-crt-blue/40 transition-colors rounded-r"
                    title="Manage personalities"
                  >
                    ...
                  </button>
                </div>

                {/* Active module picker — click to switch the module being edited */}
                <div ref={inlineModuleRef} className="relative">
                  <button
                    type="button"
                    onClick={() => setShowInlineModuleDropdown((v) => !v)}
                    className="px-2 py-1 text-[12px] font-pixel uppercase tracking-wide border rounded flex items-center gap-1.5 hover:bg-amber-400/10 transition-colors"
                    style={{
                      color: "#fbbf24",
                      borderColor: "rgba(251,191,36,0.35)",
                      background: "rgba(251,191,36,0.08)",
                      letterSpacing: "0.04em",
                    }}
                    title={selectedModule ? `Module: ${selectedModule} — click to switch` : "Pick a module to edit"}
                  >
                    <span>{selectedModule || "pick module"}</span>
                    <span className="text-[9px] opacity-60">▾</span>
                  </button>
                  {showInlineModuleDropdown && (
                    <div
                      className="absolute z-50 mt-1 left-0 w-[280px] border rounded shadow-xl"
                      style={{
                        background: "var(--bg-primary, #0a0a0a)",
                        borderColor: "rgba(251,191,36,0.35)",
                      }}
                    >
                      <input
                        type="text"
                        autoFocus
                        value={moduleSearch}
                        onChange={(e) => setModuleSearch(e.target.value)}
                        placeholder="search modules..."
                        className="w-full px-2 py-1.5 text-[12px] bg-transparent border-b font-mono outline-none"
                        style={{
                          borderColor: "rgba(251,191,36,0.2)",
                          color: "#fbbf24",
                        }}
                        spellCheck={false}
                      />
                      <div className="max-h-[280px] overflow-y-auto">
                        {moduleList
                          .filter((m) => !moduleSearch.trim() || m.name.toLowerCase().includes(moduleSearch.toLowerCase()))
                          .slice(0, 200)
                          .map((m) => (
                            <button
                              key={m.path}
                              type="button"
                              onClick={() => {
                                resetModuleState(m);
                                setSelectedModule(m.name);
                                setSelectedModuleInfo(m);
                                setWorkDir(m.path);
                                fetchModuleConfig(m.name);
                                setShowInlineModuleDropdown(false);
                                setModuleSearch("");
                              }}
                              className="w-full px-2 py-1.5 text-left text-[12px] font-code flex items-center gap-2 hover:bg-amber-400/10 transition-colors"
                              style={{
                                color: m.name === selectedModule ? "#fbbf24" : "var(--text-secondary)",
                                background: m.name === selectedModule ? "rgba(251,191,36,0.08)" : "transparent",
                              }}
                            >
                              <span className="truncate flex-1">{m.name}</span>
                              {m.category && (
                                <span className="text-[9px] opacity-50 shrink-0 uppercase">{m.category}</span>
                              )}
                            </button>
                          ))}
                        {moduleList.filter((m) => !moduleSearch.trim() || m.name.toLowerCase().includes(moduleSearch.toLowerCase())).length === 0 && (
                          <div className="px-2 py-3 text-[11px] text-center opacity-50 font-pixel uppercase">
                            no matches
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </div>

                {/* Mode selector: edit/ fork/ new/ */}
                <div className="flex items-center border rounded overflow-hidden" style={{ borderColor: "rgba(251,191,36,0.25)" }}>
                  {(["edit", "fork", "new"] as const).map((md) => (
                    <button
                      key={md}
                      onClick={() => setCreationMode(md)}
                      className="px-2 py-1 text-[11px] font-pixel uppercase transition-colors"
                      style={{
                        background: creationMode === md ? "rgba(251,191,36,0.15)" : "transparent",
                        color: creationMode === md ? "#fbbf24" : "var(--text-tertiary)",
                        borderRight: md !== "new" ? "1px solid rgba(251,191,36,0.15)" : "none",
                      }}
                    >
                      {md}/
                    </button>
                  ))}
                </div>

                {/* Editable directory display */}
                <input
                  type="text"
                  value={(() => {
                    const dir = creationMode === "edit" && selectedModule
                      ? `${anchorDir}/mod/orbit/${selectedModule}`
                      : workDir || anchorDir;
                    return dir.replace(/^\/Users\/[^/]+/, "~");
                  })()}
                  onChange={(e) => {
                    const val = e.target.value;
                    if (creationMode === "edit" && selectedModule) {
                      setWorkDir(val);
                      setSelectedModule("");
                    } else {
                      setWorkDir(val);
                    }
                  }}
                  className="px-2 py-1 text-[11px] bg-transparent border border-dashed font-mono transition-colors min-w-0"
                  style={{
                    borderColor: "rgba(100,116,139,0.3)",
                    color: "var(--text-secondary)",
                    maxWidth: "260px",
                  }}
                  title="Working directory"
                  spellCheck={false}
                />

                {/* Module name input (fork/new modes) */}
                {(creationMode === "fork" || creationMode === "new") && (
                  <input
                    type="text"
                    value={moduleName}
                    onChange={(e) => setModuleName(e.target.value)}
                    placeholder={creationMode === "fork" ? `${selectedModule}-fork` : "module-name"}
                    className="px-2 py-1 text-[11px] bg-transparent border font-mono transition-colors"
                    style={{
                      borderColor: "rgba(251,191,36,0.3)",
                      color: "#fbbf24",
                      maxWidth: "160px",
                    }}
                    spellCheck={false}
                  />
                )}

                {/* Image count badge */}
                {images.length > 0 && (
                  <div className="relative group flex items-center">
                    <span
                      className="text-[14px] px-2 py-1 border border-crt-blue/30 text-crt-blue/70 uppercase cursor-default"
                      style={{ letterSpacing: "0" }}
                    >
                      {images.length} IMG{images.length > 1 ? "S" : ""}
                    </span>
                    <button
                      onClick={() => setImages([])}
                      className="text-[14px] text-crt-red/60 hover:text-crt-red ml-1 transition-colors"
                      title="Clear all images"
                    >
                      ✕
                    </button>
                  </div>
                )}

                {/* Spacer */}
                <div className="flex-1" />

                {/* Submit button */}
                <button
                  onClick={submitJob}
                  disabled={submitting || !prompt.trim()}
                  className="pixel-btn text-[13px] py-1.5 px-6 uppercase"
                  style={{ letterSpacing: "0.02em" }}
                >
                  {submitting ? (
                    <span className="animate-pulse">...</span>
                  ) : (
                    "Run"
                  )}
                </button>
              </div>
            </div>
          </div>

          {/* Prompt Library Bar */}
          <div className="flex items-center gap-1 px-3 py-1.5 border-t shrink-0" style={{ borderColor: subtleBorder, background: darkOverlay }}>
            <button
              onClick={startCompose}
              className="text-[11px] px-2 py-1 border rounded transition-colors shrink-0"
              style={{ borderColor: "rgba(52,211,153,0.3)", color: "#34d399" }}
              title="Compose new prompt"
            >
              + Compose
            </button>
            {prompt.trim() && (
              <button
                onClick={saveCurrentAsPrompt}
                className="text-[11px] px-2 py-1 border rounded transition-colors shrink-0"
                style={{ borderColor: "rgba(251,191,36,0.3)", color: "#fbbf24" }}
                title="Save current prompt"
              >
                Save
              </button>
            )}

            {/* Divider */}
            <div className="w-px h-4 mx-1 shrink-0" style={{ background: subtleBorder }} />

            {/* Saved prompt chips - horizontal scroll */}
            <div className="flex-1 overflow-x-auto flex gap-1 items-center" style={{ scrollbarWidth: "none" }}>
              {savedPrompts.length === 0 ? (
                <span className="text-[10px] italic" style={{ color: "var(--text-tertiary)", opacity: 0.5 }}>
                  No saved prompts — compose or save one
                </span>
              ) : (
                sortedPrompts.slice(0, 20).map(sp => (
                  <button
                    key={sp.id}
                    onClick={() => loadPromptIntoInput(sp)}
                    className="group flex items-center gap-1 px-2 py-0.5 text-[11px] border rounded whitespace-nowrap transition-all hover:border-opacity-60 shrink-0"
                    style={{
                      borderColor: sp.pinned ? "rgba(251,191,36,0.4)" : subtleBorder,
                      color: sp.pinned ? "#fbbf24" : "var(--text-secondary)",
                      background: sp.pinned ? "rgba(251,191,36,0.05)" : "transparent",
                    }}
                    title={sp.body.slice(0, 120)}
                  >
                    {sp.pinned && <span className="text-[9px]">*</span>}
                    <span className="max-w-[120px] overflow-hidden text-ellipsis">{sp.title || sp.body.slice(0, 30)}</span>
                  </button>
                ))
              )}
            </div>

            {/* Manage button */}
            {savedPrompts.length > 0 && (
              <button
                onClick={() => setShowPromptList(prev => !prev)}
                className="text-[10px] px-1.5 py-0.5 border rounded transition-colors shrink-0"
                style={{ color: showPromptList ? "var(--text-primary)" : "var(--text-tertiary)", borderColor: showPromptList ? subtleBorderStrong : "transparent" }}
                title="Manage prompts"
              >
                {showPromptList ? "Hide" : "All"}
              </button>
            )}
          </div>

          {/* Expanded Prompt List Panel */}
          {showPromptList && savedPrompts.length > 0 && (
            <div className="border-t overflow-y-auto shrink-0" style={{ borderColor: subtleBorder, maxHeight: "200px", background: darkOverlay }}>
              {/* Search within prompts */}
              <div className="px-3 py-1.5 border-b flex gap-2 items-center" style={{ borderColor: subtleBorder }}>
                <input
                  type="text"
                  value={promptSearchQuery}
                  onChange={(e) => setPromptSearchQuery(e.target.value)}
                  placeholder="Search prompts..."
                  className="flex-1 text-[11px] bg-transparent border-none outline-none"
                  style={{ color: "var(--text-primary)" }}
                />
                <span className="text-[10px] shrink-0" style={{ color: "var(--text-tertiary)" }}>
                  {filteredSavedPrompts.length} prompt{filteredSavedPrompts.length !== 1 ? "s" : ""}
                </span>
              </div>
              {filteredSavedPrompts.map(sp => (
                <div
                  key={sp.id}
                  className="group flex items-center gap-2 px-3 py-2 border-b cursor-pointer transition-colors"
                  style={{ borderColor: `${subtleBorder}66` }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = cardHoverBg)}
                  onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                >
                  {/* Pin indicator */}
                  <button
                    onClick={(e) => { e.stopPropagation(); togglePinPrompt(sp.id); }}
                    className="text-[12px] shrink-0 transition-colors"
                    style={{ color: sp.pinned ? "#fbbf24" : "var(--text-tertiary)", opacity: sp.pinned ? 1 : 0.4 }}
                    title={sp.pinned ? "Unpin" : "Pin"}
                  >
                    {sp.pinned ? "*" : "-"}
                  </button>
                  {/* Title + preview - click to load */}
                  <div
                    className="flex-1 min-w-0 cursor-pointer"
                    onClick={() => loadPromptIntoInput(sp)}
                  >
                    <div className="text-[12px] truncate" style={{ color: "var(--text-primary)" }}>
                      {sp.title || sp.body.slice(0, 50)}
                    </div>
                    {sp.title && (
                      <div className="text-[10px] truncate mt-0.5" style={{ color: "var(--text-tertiary)" }}>
                        {sp.body.slice(0, 80)}
                      </div>
                    )}
                    <div className="flex gap-1 mt-0.5">
                      {(sp.tags || []).map(t => (
                        <span key={t} className="text-[9px] px-1 rounded" style={{ background: "rgba(96,165,250,0.1)", color: "#60a5fa" }}>
                          {t}
                        </span>
                      ))}
                      {sp.model && (
                        <span className="text-[9px] px-1 rounded" style={{ background: "rgba(52,211,153,0.1)", color: "#34d399" }}>
                          {sp.model}
                        </span>
                      )}
                      {sp.agent_type && sp.agent_type !== "default" && (
                        <span className="text-[9px] px-1 rounded" style={{ background: "rgba(96,165,250,0.1)", color: "#60a5fa" }}>
                          {AGENT_OPTIONS.find(a => a.value === sp.agent_type)?.icon} {sp.agent_type}
                        </span>
                      )}
                      <span className="text-[9px]" style={{ color: "var(--text-tertiary)", opacity: 0.5 }}>
                        {timeSince(sp.updated_at)}
                      </span>
                    </div>
                  </div>
                  {/* Actions */}
                  <div className="flex gap-1 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button
                      onClick={(e) => { e.stopPropagation(); startEditPrompt(sp); }}
                      className="text-[10px] px-1.5 py-0.5 border rounded transition-colors"
                      style={{ borderColor: subtleBorder, color: "var(--text-secondary)" }}
                      title="Edit"
                    >
                      Edit
                    </button>
                    <button
                      onClick={(e) => { e.stopPropagation(); loadPromptIntoInput(sp); }}
                      className="text-[10px] px-1.5 py-0.5 border rounded transition-colors"
                      style={{ borderColor: "rgba(96,165,250,0.3)", color: "#60a5fa" }}
                      title="Load into prompt"
                    >
                      Use
                    </button>
                    <button
                      onClick={(e) => { e.stopPropagation(); deletePrompt(sp.id); }}
                      className="text-[10px] px-1.5 py-0.5 border rounded transition-colors"
                      style={{ borderColor: "rgba(248,113,113,0.3)", color: "#f87171" }}
                      title="Delete"
                    >
                      ✕
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

        </div>
      </div>
    );
  };

  const renderTasksTab = () => {
    const filteredJobs = jobs.filter((j) => {
      const q = searchQuery.toLowerCase();
      const matchesSearch =
        !q ||
        j.prompt.toLowerCase().includes(q) ||
        j.status.toLowerCase().includes(q) ||
        j.model.toLowerCase().includes(q) ||
        j.id.toLowerCase().includes(q) ||
        (j.work_dir && j.work_dir.toLowerCase().includes(q));
      const matchesStatus = !statusFilter || j.status === statusFilter;
      const matchesModule = !selectedModule || (j.work_dir ? j.work_dir.includes(`/orbit/${selectedModule}`) : selectedModule === "claude");
      return matchesSearch && matchesStatus && matchesModule;
    });

    return (
      <div className="flex flex-col overflow-hidden flex-1">
        {/* Search & Filter Bar */}
        <div className="border-b px-3 py-1 flex items-center gap-2" style={{ borderColor: subtleBorder }}>
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Filter..."
            className="flex-1 min-w-0 px-1 py-0.5 text-[11px] border-none bg-transparent text-crt-green/50 focus:text-crt-green/80 focus:outline-none placeholder:text-crt-green/15 transition-colors"
          />
          <div className="flex gap-1.5 shrink-0 items-center">
            {["running", "pending", "completed", "failed", "cancelled"].map((status) => {
              const count = jobs.filter(j => j.status === status && (!selectedModule || (j.work_dir ? j.work_dir.includes(`/orbit/${selectedModule}`) : selectedModule === "claude"))).length;
              if (count === 0) return null;
              const isActive = statusFilter === status;
              return (
                <button
                  key={status}
                  onClick={() => setStatusFilter(isActive ? null : status)}
                  className="text-[10px] transition-all whitespace-nowrap border-none bg-transparent cursor-pointer"
                  style={{
                    color: STATUS_COLOR[status],
                    opacity: isActive ? 0.9 : 0.3,
                  }}
                  title={STATUS_LABEL[status]}
                >
                  {STATUS_ICON[status]}{count}
                </button>
              );
            })}
          </div>
        </div>

        {/* Task List */}
        <div className="flex-1 overflow-y-auto">
          {loading && !jobs.length ? (
            <div className="p-8 text-center">
              <p className="text-[11px] cursor-blink" style={{ color: "var(--text-tertiary)" }}>
                LOADING JOBS
              </p>
            </div>
          ) : filteredJobs.length === 0 ? (
            <div className="p-8 text-center">
              <p className="text-[11px]" style={{ color: "var(--text-tertiary)", opacity: 0.5 }}>
                No agent tasks found
              </p>
            </div>
          ) : (
            filteredJobs.map((job) => {
              const isSelected = selectedJob === job.id;
              const color = STATUS_COLOR[job.status];
              const isPromptExpanded = expandedPrompts.has(job.id);
              const moduleName = (job.work_dir ? extractModuleFromWorkDir(job.work_dir) : null) || "claude";
              const isDragOver = dragOverJobId === job.id && draggedJobId !== job.id;
              return (
                <div
                  key={job.id}
                  draggable
                  onDragStart={(e) => handleDragStart(e, job.id)}
                  onDragEnd={handleDragEnd}
                  onDragOver={(e) => handleDragOver(e, job.id)}
                  onDragLeave={handleDragLeave}
                  onDrop={(e) => handleDrop(e, job.id)}
                  onClick={() => viewJob(job)}
                  className="cursor-pointer transition-all duration-150"
                  style={{
                    borderBottom: `1px solid ${subtleBorder}`,
                    borderLeft: isSelected ? `3px solid ${color}` : "3px solid transparent",
                    borderTop: isDragOver ? "2px solid var(--crt-blue, #60a5fa)" : "2px solid transparent",
                    background: isSelected ? `${color}08` : isDragOver ? "rgba(96,165,250,0.05)" : "transparent",
                  }}
                >
                  <div className="px-3 py-2">
                    <div className="flex items-center justify-between mb-1.5">
                      <div className="flex items-center gap-1.5">
                        <span
                          className="text-[10px] cursor-grab active:cursor-grabbing select-none"
                          style={{ color: "var(--text-tertiary)", opacity: 0.3 }}
                          title="Drag to reorder"
                        >⠿</span>
                        <span className={`text-[11px] ${job.status === "running" ? "led-pulse" : ""}`} style={{ color }}>
                          {STATUS_ICON[job.status]}
                        </span>
                        <span className="text-[11px] font-pixel" style={{ color, letterSpacing: "0" }}>
                          {STATUS_LABEL[job.status]}
                        </span>
                        <span className="text-[10px]" style={{ color: "var(--text-tertiary)", opacity: 0.4 }}>
                          {modelLabel(job.model)}
                        </span>
                        <span className="text-[9px] uppercase tracking-wide" style={{ color: "var(--crt-amber)", opacity: 0.4 }}>
                          {moduleName}
                        </span>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-[10px]" style={{ color: faintGreenText, opacity: 0.5 }}>
                          {timeSince(job.created_at)}
                        </span>
                        {(job.status === "running" || job.status === "pending") && (
                          <button
                            onClick={(e) => { e.stopPropagation(); cancelJob(job.id); }}
                            className="text-[9px] px-1.5 py-0.5 border border-red-500/20 text-red-400/50 hover:bg-red-500/10 hover:text-red-400 hover:border-red-500/40 transition-all uppercase"
                            title="Cancel task"
                          >
                            CANCEL
                          </button>
                        )}
                        {(job.status === "completed" || job.status === "failed" || job.status === "cancelled") && (
                          <button
                            onClick={(e) => { e.stopPropagation(); deleteJob(job.id); }}
                            className="text-[9px] px-1.5 py-0.5 border border-red-500/15 text-red-400/30 hover:bg-red-500/10 hover:text-red-400 hover:border-red-500/40 transition-all uppercase"
                            title="Delete task"
                          >
                            DEL
                          </button>
                        )}
                      </div>
                    </div>

                    {/* Prompt - click to expand/collapse */}
                    {(() => {
                      const { cleanPrompt, imagePaths } = parsePromptImages(job.prompt);
                      return (
                        <>
                          <div
                            onClick={(e) => { if (cleanPrompt.length > 80) togglePromptExpand(job.id, e); }}
                            style={{ cursor: cleanPrompt.length > 80 ? "pointer" : "default" }}
                          >
                            <p className="text-[12px] leading-relaxed" style={{ color: "var(--text-secondary)", opacity: 0.85, whiteSpace: isPromptExpanded ? "pre-wrap" : "nowrap", overflow: isPromptExpanded ? "visible" : "hidden", textOverflow: isPromptExpanded ? "clip" : "ellipsis" }}>
                              {isPromptExpanded ? cleanPrompt : (cleanPrompt.length > 80 ? cleanPrompt.slice(0, 80) + "..." : cleanPrompt)}
                            </p>
                            {cleanPrompt.length > 80 && (
                              <span className="text-[10px] mt-0.5 inline-block" style={{ color: "var(--crt-blue)", opacity: 0.35 }}>
                                {isPromptExpanded ? "Show less" : "Show more"}
                              </span>
                            )}
                          </div>
                          {imagePaths.length > 0 && (
                            <div className="flex gap-1.5 mt-1.5 flex-wrap" onClick={(e) => e.stopPropagation()}>
                              {imagePaths.map((imgPath, idx) => (
                                <button
                                  key={idx}
                                  onClick={() => setExpandedJobImage(expandedJobImage === imgPath ? null : imgPath)}
                                  className="border border-crt-blue/20 hover:border-crt-blue/50 transition-all overflow-hidden rounded-sm"
                                  style={{ width: 32, height: 32, padding: 0, background: "var(--bg-primary)" }}
                                  title={imgPath.split("/").pop() || "image"}
                                >
                                  <img
                                    src={`${apiUrl}/files/raw?path=${encodeURIComponent(imgPath)}`}
                                    alt={`attachment ${idx + 1}`}
                                    style={{ width: "100%", height: "100%", objectFit: "cover" }}
                                  />
                                </button>
                              ))}
                            </div>
                          )}
                          {expandedJobImage && imagePaths.includes(expandedJobImage) && (
                            <div
                              className="mt-2 border border-crt-blue/20 overflow-hidden rounded-sm"
                              style={{ maxWidth: "100%", background: "var(--bg-primary)" }}
                              onClick={(e) => e.stopPropagation()}
                            >
                              <img
                                src={`${apiUrl}/files/raw?path=${encodeURIComponent(expandedJobImage)}`}
                                alt="expanded attachment"
                                style={{ width: "100%", height: "auto", maxHeight: 400, objectFit: "contain" }}
                              />
                            </div>
                          )}
                        </>
                      );
                    })()}

                    {/* Footer: module + copy */}
                    <div className="flex items-center gap-2 mt-1.5" onClick={(e) => e.stopPropagation()}>
                      {job.work_dir && moduleName && moduleName === "claude" && (
                        <span className="text-[9px] uppercase" style={{ color: "var(--crt-amber)", opacity: 0.3 }}>
                          ◈ {moduleName}
                        </span>
                      )}
                      <button
                        onClick={(e) => copyTaskToInput(job, e)}
                        className="text-[9px] px-1.5 py-0.5 border border-transparent text-crt-blue/30 hover:border-crt-blue/30 hover:text-crt-blue/70 transition-all uppercase"
                        title="Copy prompt & module into input"
                      >
                        ⧉ COPY
                      </button>
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>
    );
  };

  // Full-page TASKS view (like the HUB) — tasks from ALL modules, each row
  // carrying its module pill. The docked agent side panel this once powered
  // is gone; TASKS in the nav is the one home for agent jobs now.
  const renderAgentTab = () => {
    const filteredJobs = jobs.filter((j) => {
      const q = searchQuery.toLowerCase();
      const matchesSearch =
        !q ||
        j.prompt.toLowerCase().includes(q) ||
        j.status.toLowerCase().includes(q) ||
        j.model.toLowerCase().includes(q) ||
        j.id.toLowerCase().includes(q) ||
        (j.work_dir && j.work_dir.toLowerCase().includes(q));
      const matchesStatus = !statusFilter || j.status === statusFilter;
      return matchesSearch && matchesStatus;
    });
    // Active work floats to the top: running, then queued, then everything
    // else newest-first — opening TASKS answers "what's running right now?".
    const statusRank = (s: string) => (s === "running" ? 0 : s === "pending" ? 1 : 2);
    filteredJobs.sort((a, b) => statusRank(a.status) - statusRank(b.status) || b.created_at - a.created_at);

    const runningCount = filteredJobs.filter(j => j.status === "running").length;
    const isRunning = selectedJobData?.status === "running";
    const output = streamOutput || selectedJobData?.output || "";
    // Derived views for the open task's EDITS / AUDIT sub-tabs.
    const taskEdits = parseTaskEdits(output);
    const taskAudit = parseTaskAudit(output);
    const activeModelChip = MODEL_OPTIONS.find(m => m.value === model)
      || MODEL_OPTIONS.find(m => m.family === model)
      || MODEL_OPTIONS[0];

    return (
      <div className="flex-1 flex flex-col overflow-hidden" style={{ background: "var(--bg-primary)" }}>
        {/* ── Full-page TASKS toolbar — mirrors the HUB toolbar ── */}
          <div
            className="flex items-center gap-3 px-4 py-2.5 shrink-0 flex-wrap"
            style={{ borderBottom: "1px solid var(--border-color)", background: "color-mix(in srgb, var(--crt-blue) 4%, transparent)" }}
          >
            <span className="text-[14px] font-bold font-code" style={{ color: "var(--crt-blue)", letterSpacing: "0.04em" }}>
              ▤ TASKS
            </span>
            <span className="text-[11px] font-code" style={{ color: "var(--crt-blue)" }}>
              {runningCount} running
            </span>
          </div>

        {/* ── Main content area (TASKS — full-page view) ── */}
        <div ref={outputRef} className="flex-1 overflow-y-auto" style={{ scrollbarWidth: "thin" }}>
          {selectedJobData ? (
            <div className="fade-in">
              {/* ── Task header — the prompt is the hero, everything else stays quiet ── */}
              {(() => {
                const statusColor = STATUS_COLOR[selectedJobData.status];
                const moduleName = selectedJobData.work_dir
                  ? extractModuleFromWorkDir(selectedJobData.work_dir)
                  : null;
                const { cleanPrompt } = parsePromptImages(selectedJobData.prompt);
                const displayPrompt = cleanPrompt || selectedJobData.prompt;
                const finished = ["completed", "failed", "cancelled"].includes(selectedJobData.status);
                const runSecs = finished ? selectedJobData.updated_at - selectedJobData.created_at : null;
                return (
                  <div className="max-w-[920px] mx-auto px-4 pt-4 pb-4">
                    {/* Row 1 — back on the left, actions on the right */}
                    <div className="flex items-center justify-between gap-2 mb-4">
                      <button
                        onClick={() => { setSelectedJob(null); setStreamOutput(""); }}
                        className="task-action focus-ring"
                        title="Back to task list"
                      >
                        ← Back
                      </button>
                      <div className="flex items-center gap-1.5">
                        {(selectedJobData.status === "running" || selectedJobData.status === "pending") && (
                          <button
                            onClick={() => cancelJob(selectedJobData.id)}
                            className="task-action focus-ring"
                            style={{ "--action-accent": "var(--crt-red)" } as React.CSSProperties}
                            title="Cancel this task"
                          >
                            ✕ Stop
                          </button>
                        )}
                        {finished && (
                          <button
                            onClick={(e) => rerunTask(selectedJobData, e)}
                            disabled={submitting}
                            className="task-action focus-ring disabled:opacity-40"
                            style={{ "--action-accent": activeModelChip.color } as React.CSSProperties}
                            title="Run this exact task again as a new job"
                          >
                            ↻ Replay
                          </button>
                        )}
                        <button
                          onClick={() => editTask(selectedJobData)}
                          className="task-action focus-ring"
                          title="Load this task into the composer to edit and re-submit"
                        >
                          ✎ Edit
                        </button>
                        {finished && (
                          <button
                            onClick={() => deleteJob(selectedJobData.id)}
                            className="task-action focus-ring"
                            title="Delete this task"
                          >
                            Delete
                          </button>
                        )}
                      </div>
                    </div>

                    {/* The prompt — click to expand past the 3-line clamp */}
                    <p
                      onClick={() => setPromptExpanded(v => !v)}
                      className="text-[14.5px] leading-relaxed m-0 cursor-pointer"
                      style={{
                        color: "var(--text-primary)",
                        wordBreak: "break-word",
                        ...(promptExpanded ? {} : {
                          display: "-webkit-box",
                          WebkitLineClamp: 3,
                          WebkitBoxOrient: "vertical" as const,
                          overflow: "hidden",
                        }),
                      }}
                      title={promptExpanded ? "Collapse" : "Show full prompt"}
                    >
                      {displayPrompt}
                    </p>

                    {/* One quiet metadata line — status · model · id · time · caller · host */}
                    <div className="flex items-center gap-2 flex-wrap mt-3 text-[10.5px] font-mono" style={{ color: "var(--text-tertiary)" }}>
                      <span className="inline-flex items-center gap-1.5 font-bold uppercase" style={{ color: statusColor, letterSpacing: "0.08em" }}>
                        <span
                          className={`inline-block w-1.5 h-1.5 rounded-full ${isRunning ? "soft-pulse" : ""}`}
                          style={{ background: statusColor, boxShadow: isRunning ? `0 0 6px ${statusColor}` : "none" }}
                        />
                        {STATUS_LABEL[selectedJobData.status]}
                      </span>
                      <span className="opacity-30">·</span>
                      <span className="uppercase font-semibold" style={{ color: "var(--text-secondary)", letterSpacing: "0.05em" }}>
                        {modelLabel(selectedJobData.model)}
                      </span>
                      <span className="opacity-30">·</span>
                      <span title={selectedJobData.id}>#{selectedJobData.id.slice(0, 8)}</span>
                      <span className="opacity-30">·</span>
                      <span title={formatDate(selectedJobData.created_at)}>{timeSince(selectedJobData.created_at)}</span>
                      {runSecs != null && runSecs > 0 && (
                        <>
                          <span className="opacity-30">·</span>
                          <span title="Run time">⏱ {formatDuration(runSecs)}</span>
                        </>
                      )}
                      <span className="opacity-30">·</span>
                      <span title={`Caller: ${selectedJobData.user_address || "local"}`}>{shortCaller(selectedJobData.user_address)}</span>
                      {moduleName && (
                        <>
                          <span className="opacity-30">·</span>
                          <span
                            title={selectedJobData.work_dir}
                            style={moduleName !== "claude" ? { color: "var(--crt-amber)" } : undefined}
                          >
                            ◈ {moduleName}
                          </span>
                        </>
                      )}
                    </div>
                  </div>
                );
              })()}

              {/* ── Sub-tabs — OUTPUT · EDITS · AUDIT, stick while the log scrolls ── */}
              <div
                className="sticky top-0 z-10"
                style={{
                  borderBottom: `1px solid ${subtleBorder}`,
                  background: "color-mix(in srgb, var(--bg-primary) 92%, transparent)",
                  backdropFilter: "blur(8px)",
                }}
              >
                <div className="max-w-[920px] mx-auto px-4 flex items-center gap-5">
                  {([
                    ["output", "OUTPUT", null],
                    ["edits", "EDITS", taskEdits.length],
                    ["audit", "AUDIT", taskAudit.length],
                  ] as const).map(([key, label, count]) => {
                    const active = taskDetailTab === key;
                    return (
                      <button
                        key={key}
                        onClick={() => setTaskDetailTab(key)}
                        className="flex items-center gap-1.5 text-[10.5px] font-bold uppercase py-2.5 focus-ring"
                        style={{
                          color: active ? "var(--text-primary)" : "var(--text-tertiary)",
                          boxShadow: active ? "inset 0 -2px 0 var(--accent-color)" : "none",
                          letterSpacing: "0.08em",
                          transition: "color 150ms ease",
                        }}
                        onMouseEnter={e => { if (!active) e.currentTarget.style.color = "var(--text-secondary)"; }}
                        onMouseLeave={e => { if (!active) e.currentTarget.style.color = "var(--text-tertiary)"; }}
                      >
                        {label}
                        {count != null && count > 0 && (
                          <span className="font-mono text-[9.5px]" style={{ color: active ? "var(--accent-color)" : "inherit", opacity: active ? 1 : 0.7 }}>
                            {count}
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              </div>

              {/* Tab content */}
              <div className="max-w-[920px] mx-auto px-4 py-4 pb-10">
                {/* OUTPUT — the raw stream, no extra chrome */}
                {taskDetailTab === "output" && (output ? (
                  <pre
                    className="m-0 whitespace-pre-wrap text-[11.5px] px-4 py-3.5 rounded-xl"
                    style={{ color: "var(--text-primary)", fontFamily: "monospace", lineHeight: 1.7, wordBreak: "break-word", background: tintBg }}
                  >
                    {renderOutput(output)}
                    {isRunning && <span className="inline-block animate-pulse" style={{ color: STATUS_COLOR.running }}>&#9610;</span>}
                  </pre>
                ) : (
                  <div className="flex items-center justify-center h-32">
                    {isRunning ? (
                      <div className="flex items-center gap-1.5">
                        <span className="w-2 h-2 rounded-full animate-pulse" style={{ background: STATUS_COLOR.running }} />
                        <span className="w-2 h-2 rounded-full animate-pulse" style={{ background: STATUS_COLOR.running, animationDelay: "0.2s" }} />
                        <span className="w-2 h-2 rounded-full animate-pulse" style={{ background: STATUS_COLOR.running, animationDelay: "0.4s" }} />
                      </div>
                    ) : (
                      <p className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>No output</p>
                    )}
                  </div>
                ))}

                {/* EDITS — the file changes this task made, as diffs */}
                {taskDetailTab === "edits" && (taskEdits.length === 0 ? (
                  <div className="flex flex-col items-center justify-center h-32 gap-1.5">
                    <span className="text-[18px] opacity-30">✎</span>
                    <p className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                      {isRunning ? "No file edits yet…" : "This task made no file edits"}
                    </p>
                  </div>
                ) : (
                  <div className="space-y-2.5">
                    {/* Summary */}
                    <div className="text-[10px] font-mono px-0.5" style={{ color: "var(--text-tertiary)" }}>
                      {taskEdits.length} change{taskEdits.length === 1 ? "" : "s"} across {new Set(taskEdits.map(e => e.file)).size} file{new Set(taskEdits.map(e => e.file)).size === 1 ? "" : "s"}
                    </div>
                    {taskEdits.map((ed, i) => (
                      <div
                        key={i}
                        className="rounded-lg overflow-hidden"
                        style={{ border: `1px solid ${subtleBorder}`, background: "var(--bg-secondary)" }}
                      >
                        {/* Edit header */}
                        <div
                          className="flex items-center gap-2 px-2.5 py-1.5"
                          style={{ borderBottom: `1px solid ${subtleBorder}`, background: tintBg }}
                        >
                          <span
                            className="text-[8px] font-bold uppercase px-1.5 py-[1px] rounded-full shrink-0"
                            style={{
                              color: ed.kind === "write" ? "var(--accent-color)" : "var(--crt-amber)",
                              background: ed.kind === "write" ? "color-mix(in srgb, var(--accent-color) 12%, transparent)" : "color-mix(in srgb, var(--crt-amber) 12%, transparent)",
                              border: `1px solid ${ed.kind === "write" ? "color-mix(in srgb, var(--accent-color) 30%, transparent)" : "color-mix(in srgb, var(--crt-amber) 30%, transparent)"}`,
                              letterSpacing: "0.05em",
                            }}
                          >
                            {ed.kind}
                          </span>
                          <span className="text-[11px] font-mono truncate flex-1" style={{ color: "var(--text-primary)" }} title={ed.file}>
                            {ed.file}
                          </span>
                          {ed.kind === "edit" ? (
                            <span className="text-[9px] font-mono shrink-0 flex items-center gap-1.5">
                              {ed.added.length > 0 && <span style={{ color: "var(--accent-color)" }}>+{ed.added.length}</span>}
                              {ed.removed.length > 0 && <span style={{ color: "var(--crt-red)" }}>−{ed.removed.length}</span>}
                            </span>
                          ) : (
                            <span className="text-[9px] font-mono shrink-0" style={{ color: "var(--accent-color)" }}>{ed.lineCount} lines</span>
                          )}
                        </div>
                        {/* Diff body (edits only) */}
                        {ed.kind === "edit" && (ed.removed.length > 0 || ed.added.length > 0) && (
                          <pre className="m-0 px-2.5 py-1.5 text-[10.5px] leading-relaxed overflow-x-auto" style={{ fontFamily: "monospace" }}>
                            {ed.removed.map((l, j) => (
                              <span key={`r${j}`} style={{ color: "var(--crt-red)" }}>{"- " + l}{"\n"}</span>
                            ))}
                            {ed.added.map((l, j) => (
                              <span key={`a${j}`} style={{ color: "var(--accent-color)" }}>{"+ " + l}{"\n"}</span>
                            ))}
                          </pre>
                        )}
                      </div>
                    ))}
                  </div>
                ))}

                {/* AUDIT — ordered trail of every tool action the task took */}
                {taskDetailTab === "audit" && (taskAudit.length === 0 ? (
                  <div className="flex flex-col items-center justify-center h-32 gap-1.5">
                    <span className="text-[18px] opacity-30">⊙</span>
                    <p className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                      {isRunning ? "No actions recorded yet…" : "No tool actions recorded for this task"}
                    </p>
                  </div>
                ) : (() => {
                  const META: Record<AuditEvent["type"], { icon: string; color: string; verb: string }> = {
                    edit:   { icon: "✎", color: "var(--crt-amber)",   verb: "edit" },
                    write:  { icon: "✚", color: "var(--accent-color)", verb: "write" },
                    bash:   { icon: "$", color: "var(--crt-blue)",    verb: "bash" },
                    read:   { icon: "◇", color: "var(--text-tertiary)", verb: "read" },
                    search: { icon: "⌕", color: "var(--crt-amber)",   verb: "search" },
                    task:   { icon: "→", color: "var(--text-secondary)", verb: "task" },
                  };
                  const counts = taskAudit.reduce((acc, ev) => { acc[ev.type] = (acc[ev.type] || 0) + 1; return acc; }, {} as Record<string, number>);
                  return (
                    <div className="space-y-2">
                      {/* Summary chips */}
                      <div className="flex flex-wrap gap-1.5">
                        {(Object.keys(META) as AuditEvent["type"][]).filter(t => counts[t]).map(t => (
                          <span
                            key={t}
                            className="inline-flex items-center gap-1 text-[9px] font-mono px-1.5 py-[2px] rounded-full"
                            style={{ color: META[t].color, background: `color-mix(in srgb, ${META[t].color} 10%, transparent)`, border: `1px solid color-mix(in srgb, ${META[t].color} 28%, transparent)` }}
                          >
                            <span style={{ fontWeight: "bold" }}>{META[t].icon}</span>
                            {counts[t]} {META[t].verb}{counts[t] === 1 ? "" : "s"}
                          </span>
                        ))}
                      </div>
                      {/* Timeline */}
                      <div className="space-y-px font-mono">
                        {taskAudit.map((ev, i) => {
                          const m = META[ev.type];
                          return (
                            <div
                              key={i}
                              className="flex items-baseline gap-2 px-2 py-1 rounded"
                              style={{ background: i % 2 === 0 ? "transparent" : "color-mix(in srgb, var(--bg-secondary) 50%, transparent)" }}
                            >
                              <span className="text-[10px] tabular-nums shrink-0 w-6 text-right opacity-40" style={{ color: "var(--text-tertiary)" }}>{i + 1}</span>
                              <span className="text-[11px] shrink-0 w-3.5 text-center font-bold" style={{ color: m.color }} title={m.verb}>{m.icon}</span>
                              <span className="text-[10.5px] truncate flex-1" style={{ color: "var(--text-secondary)" }} title={ev.label}>{ev.label}</span>
                              {ev.detail && (
                                <span className="text-[9.5px] truncate shrink-0 max-w-[40%] italic opacity-60" style={{ color: "var(--text-tertiary)" }} title={ev.detail}>{ev.detail}</span>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                })())}

                {selectedJobData.error && (
                  <div className="mt-3 p-2.5 rounded-lg" style={{ background: "rgba(239,68,68,0.06)", border: "1px solid rgba(239,68,68,0.2)" }}>
                    <span className="text-[9px] font-bold uppercase" style={{ color: "var(--crt-red)" }}>Error</span>
                    <pre className="m-0 mt-1 whitespace-pre-wrap text-[10px]" style={{ color: "var(--crt-red)", fontFamily: "monospace" }}>
                      {selectedJobData.error}
                    </pre>
                  </div>
                )}
              </div>
            </div>
          ) : (
            /* Job list */
            <div className="fade-in">
              {/* Filter bar */}
              <div
                className="flex items-center gap-2 px-3.5 py-2 sticky top-0 z-10"
                style={{
                  borderBottom: `1px solid ${subtleBorder}`,
                  background: "color-mix(in srgb, var(--bg-primary) 92%, transparent)",
                  backdropFilter: "blur(8px)",
                }}
              >
                <span className="text-[11px]" style={{ color: "var(--text-tertiary)", opacity: 0.6 }}>⌕</span>
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="filter tasks…"
                  className="flex-1 min-w-0 px-1 py-0.5 text-[11.5px] border-none bg-transparent focus:outline-none placeholder:opacity-30"
                  style={{ color: "var(--text-primary)", boxShadow: "none" }}
                />
                <div className="flex gap-1 shrink-0 items-center">
                  {["running", "pending", "completed", "failed"].map((status) => {
                    const count = filteredJobs.filter(j => j.status === status).length;
                    if (count === 0) return null;
                    const isActive = statusFilter === status;
                    return (
                      <button
                        key={status}
                        onClick={() => setStatusFilter(isActive ? null : status)}
                        className="text-[10px] font-mono px-1.5 py-[2px] rounded-full cursor-pointer"
                        style={{
                          color: STATUS_COLOR[status],
                          opacity: isActive ? 1 : 0.45,
                          background: isActive ? `${STATUS_COLOR[status]}1f` : "transparent",
                          border: `1px solid ${isActive ? `${STATUS_COLOR[status]}50` : "transparent"}`,
                          transition: "opacity 150ms ease, background 150ms ease, border-color 150ms ease",
                        }}
                        onMouseEnter={e => { if (!isActive) e.currentTarget.style.opacity = "0.85"; }}
                        onMouseLeave={e => { if (!isActive) e.currentTarget.style.opacity = "0.45"; }}
                        title={`${STATUS_LABEL[status]}: ${count}`}
                      >
                        {STATUS_ICON[status]} {count}
                      </button>
                    );
                  })}
                </div>
              </div>

              {filteredJobs.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-16 gap-3">
                  <span
                    className="inline-flex items-center justify-center"
                    style={{
                      width: 48,
                      height: 48,
                      borderRadius: 12,
                      background: "rgba(167,139,250,0.08)",
                      border: "1px solid rgba(167,139,250,0.18)",
                      color: "#a78bfa",
                      fontSize: 22,
                      boxShadow: "0 0 24px -6px rgba(167,139,250,0.4)",
                    }}
                  >
                    ⬡
                  </span>
                  <p className="text-[11px] font-bold uppercase" style={{ color: "var(--text-secondary)", letterSpacing: "0.16em" }}>
                    No tasks yet
                  </p>
                  <p className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>
                    Submit a prompt below to get started
                  </p>
                </div>
              ) : (
                <div className="w-full max-w-[920px] mx-auto px-3 py-3 flex flex-col gap-2.5">
                  {filteredJobs.map((job, idx) => {
                    const color = STATUS_COLOR[job.status];
                    const moduleName = (job.work_dir ? extractModuleFromWorkDir(job.work_dir) : null) || "claude";
                    const { cleanPrompt, imagePaths } = parsePromptImages(job.prompt);
                    const jobModelChip = MODEL_OPTIONS.find(o => o.value === job.model || o.family === job.model);
                    const finished = ["completed", "failed", "cancelled"].includes(job.status);
                    const runSecs = finished ? job.updated_at - job.created_at : null;
                    return (
                      <button
                        key={job.id}
                        onClick={() => viewJob(job)}
                        className={`task-card w-full text-left group focus-ring ${job.status === "running" ? "task-card--running" : ""}`}
                        style={{ "--task-accent": color, animationDelay: `${Math.min(idx, 10) * 28}ms` } as React.CSSProperties}
                      >
                        <div className="pl-4 pr-3.5 py-3">
                          {/* Row 1 — status pill · model · module · attachments · time */}
                          <div className="flex items-center gap-1.5 flex-wrap mb-2">
                            <span className="status-pill" style={{ "--pill-accent": color } as React.CSSProperties}>
                              <span className={`status-pill__dot ${job.status === "running" ? "soft-pulse" : ""}`} />
                              {STATUS_LABEL[job.status]}
                            </span>
                            <span
                              className="task-chip"
                              style={{ "--chip-accent": jobModelChip?.color || "var(--text-tertiary)" } as React.CSSProperties}
                              title={job.model}
                            >
                              {modelLabel(job.model)}
                            </span>
                            <span
                              className="task-chip font-mono"
                              style={{ "--chip-accent": "var(--crt-amber)" } as React.CSSProperties}
                              title={job.work_dir || "claude"}
                            >
                              ◈ {moduleName}
                            </span>
                            {imagePaths.length > 0 && (
                              <span className="task-chip" title={`${imagePaths.length} image attachment${imagePaths.length === 1 ? "" : "s"}`}>
                                ⧉ {imagePaths.length}
                              </span>
                            )}
                            <span className="ml-auto inline-flex items-center gap-2 shrink-0 text-[10px] font-mono" style={{ color: "var(--text-tertiary)" }}>
                              {runSecs != null && runSecs > 0 && (
                                <span style={{ opacity: 0.7 }} title="Run time">⏱ {formatDuration(runSecs)}</span>
                              )}
                              <span title={formatDate(job.created_at)}>{timeSince(job.created_at)}</span>
                            </span>
                          </div>
                          {/* Prompt — 2-line clamp */}
                          <p
                            className="text-[12px] leading-relaxed m-0"
                            style={{
                              color: "var(--text-primary)",
                              opacity: 0.92,
                              display: "-webkit-box",
                              WebkitLineClamp: 2,
                              WebkitBoxOrient: "vertical",
                              overflow: "hidden",
                              wordBreak: "break-word",
                            }}
                          >
                            {cleanPrompt || job.prompt}
                          </p>
                          {/* Footer — id + hover actions */}
                          <div className="flex items-center justify-between gap-2 mt-2 min-h-[20px]">
                            <span className="text-[9.5px] font-mono" style={{ color: "var(--text-tertiary)", opacity: 0.55 }} title={job.id}>
                              #{job.id.slice(0, 8)}
                            </span>
                            <span className="inline-flex items-center gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
                              {(job.status === "running" || job.status === "pending") && (
                                <span
                                  className="task-action"
                                  style={{ "--action-accent": "var(--crt-red)" } as React.CSSProperties}
                                  onClick={(e) => { e.stopPropagation(); cancelJob(job.id); }}
                                >
                                  ✕ Cancel
                                </span>
                              )}
                              {finished && (
                                <>
                                  <span
                                    className="task-action"
                                    style={{ "--action-accent": jobModelChip?.color || "var(--accent-color)" } as React.CSSProperties}
                                    onClick={(e) => { e.stopPropagation(); rerunTask(job, e); }}
                                    title="Run this exact task again as a new job"
                                  >
                                    ↻ Replay
                                  </span>
                                  <span
                                    className="task-action"
                                    onClick={(e) => { e.stopPropagation(); deleteJob(job.id); }}
                                  >
                                    Delete
                                  </span>
                                </>
                              )}
                            </span>
                          </div>
                        </div>
                        {job.status === "running" && <span aria-hidden className="task-card__progress" />}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </div>

      </div>
    );
  };

  // ── Compact BUILD/FORK/EDIT/IMPORT form ──────────────────────────────
  // One form, two anchors: the left rail's bottom actions and the composer
  // dock's "+" popover both render this. Shares the header popover's state
  // and submit path, so behavior is identical — only the surface differs.
  const renderCompactCreateForm = () => {
                  const isEdit = showHeaderCreateForm === "edit";
                  const accent = isEdit
                    ? "var(--crt-blue)"
                    : showHeaderCreateForm === "fork"
                    ? "var(--crt-amber)"
                    : showHeaderCreateForm === "import"
                    ? "#22d3ee"
                    : "var(--crt-green)";
                  const sources = [
                    { key: "new", label: "NEW", active: showHeaderCreateForm === "create", disabled: false },
                    { key: "fork", label: "FORK", active: showHeaderCreateForm === "fork", disabled: !selectedModule },
                    { key: "git", label: "GIT", active: showHeaderCreateForm === "import" && headerImportSource === "github", disabled: false },
                    { key: "cid", label: "CID", active: showHeaderCreateForm === "import" && headerImportSource === "cid", disabled: false },
                  ];
                  const pickSource = (key: string) => {
                    if (key === "new") {
                      setHeaderNewName(""); setHeaderGithubUrl("");
                      setShowHeaderCreateForm("create");
                    } else if (key === "fork") {
                      if (!selectedModule) return;
                      setHeaderNewName(selectedModule + "-fork"); setHeaderGithubUrl("");
                      setShowHeaderCreateForm("fork");
                    } else {
                      setHeaderNewName("");
                      setHeaderImportSource(key === "git" ? "github" : "cid");
                      setShowHeaderCreateForm("import");
                    }
                  };
                  const canGo = isEdit
                    ? !!headerEditPrompt.trim() && !!selectedModule
                    : showHeaderCreateForm === "import"
                    ? (headerImportSource === "github" ? !!headerGithubUrl.trim() : !!headerCid.trim())
                    : !!headerNewName.trim();
                  const onFormKeys = (e: React.KeyboardEvent) => {
                    const inTextarea = e.target instanceof HTMLTextAreaElement;
                    if (e.key === "Enter" && (!inTextarea || e.metaKey || e.ctrlKey)) headerCreateOrFork();
                    if (e.key === "Escape") setShowHeaderCreateForm(null);
                  };
                  const inputStyle: React.CSSProperties = {
                    borderColor: `color-mix(in srgb, ${accent} 30%, transparent)`,
                    color: "var(--text-primary)",
                    background: "var(--bg-primary)",
                  };
                  return (
                    <div className="flex flex-col gap-1.5 pb-1" onKeyDown={onFormKeys}>
                      <div className="flex items-center justify-between gap-2">
                        <span
                          className="text-[10px] font-bold font-code uppercase truncate"
                          style={{ color: accent, letterSpacing: "0.08em" }}
                        >
                          {isEdit
                            ? `✎ EDIT ${selectedModule || "?"}`
                            : showHeaderCreateForm === "fork"
                            ? `⑂ FORK ${selectedModule || "?"}`
                            : showHeaderCreateForm === "import"
                            ? "⇩ IMPORT MODULE"
                            : "+ BUILD MODULE"}
                        </span>
                        <button
                          onClick={() => setShowHeaderCreateForm(null)}
                          className="text-[10px] shrink-0 hover:brightness-125"
                          style={{ color: "var(--text-tertiary)" }}
                          aria-label="Close create form"
                        >
                          ✕
                        </button>
                      </div>
                      {!isEdit && (
                        <div className="flex gap-1">
                          {sources.map((s) => (
                            <button
                              key={s.key}
                              onClick={() => pickSource(s.key)}
                              disabled={s.disabled}
                              title={s.disabled ? "Select a module to fork" : s.label}
                              className="flex-1 text-[10px] font-bold py-1 font-code border rounded-sm transition-all disabled:cursor-not-allowed"
                              style={{
                                letterSpacing: "0.04em",
                                color: s.active ? accent : "var(--text-secondary, var(--text-tertiary))",
                                opacity: s.disabled ? 0.35 : 1,
                                borderColor: s.active ? `color-mix(in srgb, ${accent} 50%, transparent)` : "var(--border-color)",
                                background: s.active ? `color-mix(in srgb, ${accent} 10%, transparent)` : "transparent",
                              }}
                            >
                              {s.label}
                            </button>
                          ))}
                        </div>
                      )}
                      {isEdit ? (
                        <textarea
                          autoFocus
                          value={headerEditPrompt}
                          onChange={(e) => setHeaderEditPrompt(e.target.value)}
                          placeholder="describe the edit… (⌘↵ runs)"
                          rows={3}
                          className="px-2 py-1.5 text-[12px] border font-code outline-none resize-none rounded-sm"
                          style={inputStyle}
                        />
                      ) : (
                        <>
                          {showHeaderCreateForm === "import" && (
                            headerImportSource === "github" ? (
                              <input
                                type="text"
                                autoFocus
                                value={headerGithubUrl}
                                onChange={(e) => setHeaderGithubUrl(e.target.value)}
                                placeholder="https://github.com/user/repo"
                                className="px-2 py-1.5 text-[12px] border font-code outline-none rounded-sm"
                                style={inputStyle}
                              />
                            ) : (
                              <input
                                type="text"
                                autoFocus
                                value={headerCid}
                                onChange={(e) => setHeaderCid(e.target.value)}
                                placeholder="snapshot cid…"
                                className="px-2 py-1.5 text-[12px] border font-code outline-none rounded-sm"
                                style={inputStyle}
                              />
                            )
                          )}
                          <input
                            type="text"
                            autoFocus={showHeaderCreateForm !== "import"}
                            value={headerNewName}
                            onChange={(e) => setHeaderNewName(e.target.value)}
                            placeholder={
                              showHeaderCreateForm === "import" && headerImportSource === "github"
                                ? (deriveNameFromUrl(headerGithubUrl) ? `name: ${deriveNameFromUrl(headerGithubUrl)} (auto)` : "name (auto from url)…")
                                : "module name…"
                            }
                            className="px-2 py-1.5 text-[12px] border font-code outline-none rounded-sm"
                            style={inputStyle}
                          />
                          {showHeaderCreateForm === "create" && (
                            <input
                              type="text"
                              value={headerGithubUrl}
                              onChange={(e) => setHeaderGithubUrl(e.target.value)}
                              placeholder="github url (optional)…"
                              className="px-2 py-1.5 text-[12px] border font-code outline-none rounded-sm"
                              style={inputStyle}
                            />
                          )}
                        </>
                      )}
                      <div className="flex items-center gap-1.5">
                        {showHeaderCreateForm !== "import" && (
                          <select
                            value={model}
                            onChange={(e) => {
                              setModel(e.target.value);
                              safeSetItem("claude_jobs_model", e.target.value);
                            }}
                            className="min-w-0 flex-1 px-1 py-1 text-[10px] bg-transparent border font-code uppercase cursor-pointer rounded-sm"
                            style={{ color: accent, borderColor: "var(--border-color)" }}
                            title={`Model: ${modelLabel(model)} (${model})`}
                          >
                            {MODEL_OPTIONS.map((m) => (
                              <option key={m.value} value={m.value} style={{ background: "var(--bg-primary)", color: "var(--text-primary)" }}>
                                {m.label}
                              </option>
                            ))}
                          </select>
                        )}
                        <button
                          onClick={headerCreateOrFork}
                          disabled={!canGo || submitting}
                          className="pixel-btn text-[11px] py-1 px-3 uppercase shrink-0 disabled:cursor-not-allowed"
                          style={{
                            opacity: canGo && !submitting ? 1 : 0.4,
                            marginLeft: showHeaderCreateForm === "import" ? "auto" : undefined,
                          }}
                        >
                          {submitting
                            ? "…"
                            : isEdit
                            ? "EDIT"
                            : showHeaderCreateForm === "fork"
                            ? "FORK"
                            : showHeaderCreateForm === "import"
                            ? "IMPORT"
                            : "BUILD"}
                        </button>
                      </div>
                    </div>
                  );
  };

  // ── Composer dock ─────────────────────────────────────────────────────
  // The ask box + savable system prompt, docked at the bottom of the whole
  // console (spans the app view and the tasks sidebar) so the prompt always
  // lives at the bottom, like a chat app — on desktop and phone alike.
  // Extracted from the agent panel: EDIT now reads app (main) + tasks
  // (sidebar) + prompt (bottom).
  const renderComposerDock = () => {
    const activeModelChip = MODEL_OPTIONS.find(m => m.value === model)
      || MODEL_OPTIONS.find(m => m.family === model)
      || MODEL_OPTIONS[0];
    // Frosted-glass capsule tokens — hairline border, translucent blur,
    // one soft floating shadow. The band reads as a single quiet object.
    const glassBorder = isLight ? "rgba(0,0,0,0.07)" : "rgba(255,255,255,0.09)";
    const glassBg = isLight ? "rgba(255,255,255,0.72)" : "rgba(16,16,18,0.66)";
    const glassShadow = "0 10px 32px -14px rgba(0,0,0,0.55), inset 0 1px 0 rgba(255,255,255,0.05)";
    // Minimized to a tool — the pill that restores it renders at the mount
    // site so it survives this early return.
    if (composerMinimized) return null;
    const inner = (
      <>
          {/* ── PARAMS — the agent selector + auth + the request params sent
              with every task (mod, model, system prompt). Expands ABOVE the
              one-line band; toggled from the PARAMS chip in it. ── */}
            {systemPromptOpen && (
              <div
                className="mb-2 rounded-2xl px-3.5 py-3 overflow-y-auto"
                style={{
                  // Never taller than what's left below the header — on a
                  // phone the open panel used to squeeze the header/content
                  // off; the calc keeps header + strip + band always visible.
                  maxHeight: "min(55vh, calc(100dvh - var(--header-h, 56px) - 150px))",
                  background: glassBg,
                  backdropFilter: "blur(24px) saturate(1.5)",
                  WebkitBackdropFilter: "blur(24px) saturate(1.5)",
                  border: `1px solid ${glassBorder}`,
                  boxShadow: glassShadow,
                }}
              >
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-[10px] font-bold uppercase" style={{ color: "var(--text-secondary)", letterSpacing: "0.08em" }}>
                    PARAMS
                  </span>
                  <div className="flex-1" />
                  <button
                    onClick={() => { setSystemPromptOpen(false); safeSetItem("claude_system_prompt_open", "0"); }}
                    className="text-[13px] px-2 py-1 rounded focus-ring"
                    style={{ color: "var(--text-tertiary)" }}
                    title="Close params"
                    aria-label="Close params"
                  >
                    ✕
                  </button>
                </div>
                {/* AGENT — which agent runs the task, at the top because every
                    param below belongs to it. Each agent owns its own parameter
                    set; "mod" (this module's claude-code harness) is the only
                    one wired today, so it's the honest default — codex et al.
                    become selectable here later. */}
                <div className="flex items-center flex-wrap gap-x-2 gap-y-1.5 mb-2">
                  <span className="text-[10px] font-bold uppercase w-[52px] shrink-0" style={{ color: "var(--text-tertiary)", letterSpacing: "0.08em" }}>
                    AGENT
                  </span>
                  {(() => {
                    const active = AGENT_MODS.find(a => a.value === agentMod) || AGENT_MODS[0];
                    return (
                      <select
                        value={agentMod}
                        onChange={(e) => {
                          const pick = AGENT_MODS.find(a => a.value === e.target.value);
                          if (!pick?.available) return;
                          setAgentMod(pick.value);
                          safeSetItem("claude_jobs_agent_mod", pick.value);
                        }}
                        className="text-[12px] font-mono px-2.5 py-1.5 rounded-full outline-none cursor-pointer"
                        style={{
                          color: active.color,
                          border: `1px solid ${active.color}55`,
                          background: `${active.color}14`,
                          letterSpacing: "0.04em",
                        }}
                        title={`Agent: ${active.label} — ${active.hint}. Each agent has its own parameter set; more agents (codex) land here soon.`}
                      >
                        {AGENT_MODS.map(a => (
                          <option key={a.value} value={a.value} disabled={!a.available}>
                            {a.label}{a.available ? "" : " — soon"}
                          </option>
                        ))}
                      </select>
                    );
                  })()}
                  <span className="text-[10px]" style={{ color: "var(--text-tertiary)", opacity: 0.7 }}>
                    params below are this agent&apos;s set
                  </span>
                </div>
                {/* AUTH — who this request is signed as */}
                <div className="flex items-center flex-wrap gap-x-2 gap-y-1.5 mb-2">
                  <span className="text-[10px] font-bold uppercase w-[52px] shrink-0" style={{ color: "var(--text-tertiary)", letterSpacing: "0.08em" }}>
                    AUTH
                  </span>
                  <span
                    className="text-[11px] font-mono px-2.5 py-1 rounded-full"
                    style={{
                      color: address ? "var(--text-secondary)" : "var(--crt-red)",
                      border: `1px solid ${subtleBorder}`,
                      background: "var(--bg-secondary)",
                      letterSpacing: "0.04em",
                    }}
                    title={address ? `Signed in as ${address}${walletType ? ` via ${walletType}` : ""}` : "Not signed in"}
                  >
                    {address ? (address === "local" ? "LOCAL" : `${address.slice(0, 6)}…${address.slice(-4)}`) : "SIGNED OUT"}
                    {walletType && <span style={{ opacity: 0.55 }}> · {walletType}</span>}
                  </span>
                  {address && (
                    <span
                      className="text-[10px] font-bold uppercase px-2.5 py-1 rounded-full"
                      style={isOwner ? {
                        color: "var(--crt-green)",
                        border: "1px solid rgba(52,211,153,0.35)",
                        background: "rgba(52,211,153,0.08)",
                        letterSpacing: "0.06em",
                      } : {
                        color: "var(--crt-amber)",
                        border: "1px solid rgba(251,191,36,0.3)",
                        background: "rgba(251,191,36,0.06)",
                        letterSpacing: "0.06em",
                      }}
                      title={isOwner ? "You are the configured owner — full edit access" : "Guest — edits limited to peers/ modules"}
                    >
                      {isOwner ? "OWNER" : "GUEST · PEERS EDIT"}
                    </span>
                  )}
                  {ownerAddress && !isOwner && (
                    <span className="text-[9px] font-mono" style={{ color: "var(--text-tertiary)", opacity: 0.7 }} title={`Configured owner: ${ownerAddress}`}>
                      owner {ownerAddress.slice(0, 6)}…{ownerAddress.slice(-4)}
                    </span>
                  )}
                  <span
                    className="inline-block w-1.5 h-1.5 rounded-full"
                    style={{
                      background: token ? "var(--crt-green)" : "var(--crt-red)",
                      boxShadow: token ? "0 0 5px var(--crt-green)" : "none",
                    }}
                    title={token ? "Auth token active" : "No auth token"}
                  />
                  {/* Claude credential status — lives here now, not on the band */}
                  <AuthBadge inline />
                </div>
                {/* Request params — MOD / MODEL / AGENT */}
                <div className="flex items-center flex-wrap gap-x-2 gap-y-1.5 mb-2">
                  <span className="text-[10px] font-bold uppercase w-[52px] shrink-0" style={{ color: "var(--text-tertiary)", letterSpacing: "0.08em" }}>
                    MOD
                  </span>
                  {!modPickerOpen ? (
                    <button
                      onClick={() => {
                        setModPickerQuery("");
                        setModPickerOpen(true);
                        if (!moduleList.length) fetchModules("");
                        setTimeout(() => modPickerInputRef.current?.focus(), 0);
                      }}
                      className="text-[12px] font-mono px-2.5 py-1.5 rounded-full focus-ring inline-flex items-center gap-1.5 max-w-[220px]"
                      style={{
                        color: "#fbbf24",
                        border: "1px solid rgba(251,191,36,0.3)",
                        background: "rgba(251,191,36,0.08)",
                        letterSpacing: "0.04em",
                      }}
                      title={`Agent works on module: ${selectedModule || "none"} — click to search & change`}
                    >
                      <span className="truncate">{selectedModule || "select mod"}</span>
                      <span style={{ opacity: 0.55, fontSize: 10 }}>▾</span>
                    </button>
                  ) : (
                    <div
                      className="flex-1 min-w-[180px] max-w-[340px] rounded-xl overflow-hidden"
                      style={{ border: "1px solid rgba(251,191,36,0.3)", background: "var(--bg-secondary)" }}
                    >
                      <input
                        ref={modPickerInputRef}
                        type="text"
                        value={modPickerQuery}
                        onChange={(e) => { setModPickerQuery(e.target.value); fetchModules(e.target.value); }}
                        onKeyDown={(e) => {
                          if (e.key === "Escape") {
                            setModPickerOpen(false);
                            if (modPickerQuery) fetchModules("");
                            return;
                          }
                          if (e.key === "Enter") {
                            const q = modPickerQuery.trim().toLowerCase();
                            const first = moduleList.find((m) => m.name.toLowerCase().includes(q));
                            if (first) {
                              resetModuleState(first);
                              setSelectedModule(first.name);
                              setSelectedModuleInfo(first);
                              setWorkDir(first.path);
                              fetchModuleConfig(first.name);
                              setModPickerOpen(false);
                              if (modPickerQuery) fetchModules("");
                            }
                          }
                        }}
                        placeholder="search modules…"
                        className="w-full text-[13px] font-mono px-3 py-2 outline-none"
                        style={{ color: "var(--text-primary)", background: "transparent", border: "none" }}
                        aria-label="Search modules"
                      />
                      <div className="max-h-[180px] overflow-y-auto" style={{ borderTop: `1px solid ${subtleBorder}` }}>
                        {moduleList
                          .filter((m) => m.name.toLowerCase().includes(modPickerQuery.trim().toLowerCase()))
                          .slice(0, 40)
                          .map((m) => (
                            <button
                              // key by path — module NAMES are not unique in the
                              // registry (two "app"s) and duplicate keys leave
                              // stale rows behind when the filter narrows
                              key={m.path || m.name}
                              onClick={() => {
                                resetModuleState(m);
                                setSelectedModule(m.name);
                                setSelectedModuleInfo(m);
                                setWorkDir(m.path);
                                fetchModuleConfig(m.name);
                                setModPickerOpen(false);
                                if (modPickerQuery) fetchModules("");
                              }}
                              className="w-full text-left text-[12px] font-mono px-3 py-1.5 focus-ring transition-colors hover:brightness-125"
                              style={{
                                color: m.name === selectedModule ? "#fbbf24" : "var(--text-secondary)",
                                background: m.name === selectedModule ? "rgba(251,191,36,0.08)" : "transparent",
                              }}
                            >
                              {m.name === selectedModule && <span style={{ opacity: 0.7 }}>› </span>}
                              {m.name}
                            </button>
                          ))}
                        {moduleList.filter((m) => m.name.toLowerCase().includes(modPickerQuery.trim().toLowerCase())).length === 0 && (
                          <div className="text-[11px] px-3 py-2" style={{ color: "var(--text-tertiary)" }}>
                            no modules match “{modPickerQuery}”
                          </div>
                        )}
                      </div>
                      <button
                        onClick={() => { setModPickerOpen(false); if (modPickerQuery) fetchModules(""); }}
                        className="w-full text-[10px] font-bold uppercase px-3 py-1.5 focus-ring"
                        style={{ color: "var(--text-tertiary)", letterSpacing: "0.06em", borderTop: `1px solid ${subtleBorder}` }}
                      >
                        close
                      </button>
                    </div>
                  )}
                  <span className="text-[10px] font-bold uppercase w-[52px] shrink-0 sm:ml-2" style={{ color: "var(--text-tertiary)", letterSpacing: "0.08em" }}>
                    MODEL
                  </span>
                  <select
                    value={model}
                    onChange={(e) => { setModel(e.target.value); safeSetItem("claude_jobs_model", e.target.value); }}
                    className="text-[12px] font-mono px-2.5 py-1.5 rounded-full outline-none cursor-pointer"
                    style={{
                      color: activeModelChip.color,
                      border: `1px solid ${activeModelChip.color}55`,
                      background: `${activeModelChip.color}14`,
                      letterSpacing: "0.04em",
                    }}
                    title={`Model: ${activeModelChip.label} (${activeModelChip.value})`}
                  >
                    {MODEL_OPTIONS.map(m => (
                      <option key={m.value} value={m.value}>{m.label}</option>
                    ))}
                  </select>
                  <span className="text-[10px] font-bold uppercase w-[52px] shrink-0 sm:ml-2" style={{ color: "var(--text-tertiary)", letterSpacing: "0.08em" }}>
                    PROMPT
                  </span>
                  {/* System-prompt selector (was "Agent" — these are prompt
                      presets, not agents). The selected prompt is shown in
                      full in the SYSTEM section below. */}
                  <select
                    value={agentType}
                    onChange={(e) => { setAgentType(e.target.value); safeSetItem("claude_jobs_agent", e.target.value); }}
                    className="text-[12px] font-mono px-2.5 py-1.5 rounded-full outline-none cursor-pointer max-w-[180px]"
                    style={{
                      color: "var(--text-secondary)",
                      border: `1px solid ${subtleBorder}`,
                      background: "var(--bg-secondary)",
                      letterSpacing: "0.04em",
                    }}
                    title="Select system prompt — sent when no chain blocks are active; shown in SYSTEM below"
                  >
                    {AGENT_OPTIONS.map(a => (
                      <option key={a.value} value={a.value}>
                        {a.value === "default" ? "select system prompt" : a.label}
                      </option>
                    ))}
                  </select>
                </div>
                {/* SYSTEM — the prompt chain as name-only chips. Click a chip
                    to toggle it in/out of the chain; content is edited in the
                    separate SYSTEM PROMPTS modal. Active chips show their
                    chain position (concatenation order). */}
                <div className="flex items-start gap-x-2">
                  <span
                    className="text-[10px] font-bold uppercase w-[52px] shrink-0 mt-[6px]"
                    style={{ color: "var(--text-tertiary)", letterSpacing: "0.08em" }}
                  >
                    SYSTEM
                  </span>
                  <div className="flex-1 flex items-center flex-wrap gap-1.5">
                    {sysPrompts.map(p => {
                      const chainIdx = activeSysPrompts.findIndex(a => a.id === p.id);
                      const active = chainIdx >= 0;
                      return (
                        <span
                          key={p.id}
                          className="inline-flex items-center rounded-full overflow-hidden"
                          style={active ? {
                            border: `1px solid ${activeModelChip.color}66`,
                            background: `${activeModelChip.color}18`,
                          } : {
                            border: `1px solid ${subtleBorder}`,
                            background: "var(--bg-secondary)",
                          }}
                        >
                          <button
                            onClick={() => toggleSysPrompt(p.id)}
                            className="text-[11px] font-mono px-2.5 py-1 focus-ring"
                            style={{
                              color: active ? activeModelChip.color : "var(--text-tertiary)",
                              letterSpacing: "0.04em",
                              opacity: active ? 1 : 0.75,
                            }}
                            title={`${active ? "Remove from" : "Add to"} the chain — ${p.text.trim().slice(0, 140) || "(empty)"}`}
                            aria-pressed={active}
                          >
                            {active && <span style={{ opacity: 0.6 }}>{chainIdx + 1}·</span>}
                            {p.name}
                          </button>
                          <button
                            onClick={() => startEditSysPrompt(p)}
                            className="text-[12px] pr-2.5 pl-1 py-1 focus-ring"
                            style={{ color: "var(--text-tertiary)", opacity: 0.7 }}
                            title={`Edit "${p.name}"`}
                            aria-label={`Edit system prompt ${p.name}`}
                          >
                            ✎
                          </button>
                        </span>
                      );
                    })}
                    <button
                      onClick={startNewSysPrompt}
                      className="text-[11px] font-bold uppercase px-2.5 py-1 rounded-full focus-ring"
                      style={{
                        color: "var(--text-secondary)",
                        border: `1px dashed ${subtleBorder}`,
                        letterSpacing: "0.06em",
                      }}
                      title="Add a system prompt to the chain"
                    >
                      + ADD
                    </button>
                    {sysPrompts.length > 1 && (
                      <button
                        onClick={() => { setEditingSysPrompt(null); setCreatingSysPrompt(false); setShowSysPromptManager(true); }}
                        className="text-[11px] font-bold uppercase px-2.5 py-1 rounded-full focus-ring"
                        style={{ color: "var(--text-tertiary)", letterSpacing: "0.06em" }}
                        title="Manage & reorder the prompt chain"
                      >
                        CHAIN…
                      </button>
                    )}
                    {sysPrompts.length === 0 && (
                      <span className="text-[10px]" style={{ color: "var(--text-tertiary)", opacity: 0.7 }}>
                        {activePersonality?.prompt
                          ? `none — "${activePersonality.name}" prompt applies`
                          : "none — no system prompt is sent"}
                      </span>
                    )}
                  </div>
                </div>
                {/* The effective system prompt, shown in full — what actually
                    ships with the task: the chain when blocks are active,
                    otherwise the prompt selected in PROMPT above. */}
                {(chainedSystemPrompt || activePersonality?.prompt) && (
                  <div className="flex items-start gap-x-2 mt-2">
                    <span className="w-[52px] shrink-0" aria-hidden="true" />
                    <div
                      className="flex-1 rounded-xl px-3 py-2 min-w-0"
                      style={{ border: `1px solid ${subtleBorder}`, background: "var(--bg-secondary)" }}
                    >
                      <div className="text-[9px] font-bold uppercase mb-1" style={{ color: "var(--text-tertiary)", letterSpacing: "0.08em" }}>
                        {chainedSystemPrompt
                          ? `sent with every task · chain × ${activeSysPrompts.length}`
                          : `sent with every task · "${activePersonality.name}"`}
                        {` · ${(chainedSystemPrompt || activePersonality.prompt).length} chars`}
                      </div>
                      <pre
                        className="text-[10px] font-mono whitespace-pre-wrap m-0 max-h-[140px] overflow-y-auto"
                        style={{ color: "var(--text-secondary)" }}
                      >
                        {chainedSystemPrompt || activePersonality.prompt}
                      </pre>
                    </div>
                  </div>
                )}
              </div>
            )}
          {/* ── Params strip — TASKS + the request params (mod / model /
              PARAMS toggle) all share ONE line above the ask band, on
              every screen size. ── */}
          <div className="mb-2 flex items-center gap-2 flex-wrap">
            {/* PARAMS toggle — leftmost, leading the mod/model chips it
                expands (auth + mod/model/agent + system prompt chain) */}
            <button
              onClick={() => setSystemPromptOpen(o => { const next = !o; safeSetItem("claude_system_prompt_open", next ? "1" : "0"); return next; })}
              className="shrink-0 text-[12px] font-bold uppercase px-3.5 py-2 rounded-full focus-ring inline-flex items-center gap-1 transition-all hover:brightness-125"
              style={activeSysPrompts.length > 0 ? {
                color: activeModelChip.color,
                letterSpacing: "0.08em",
                border: `1px solid ${activeModelChip.color}40`,
                background: glassBg,
                backdropFilter: "blur(24px) saturate(1.5)",
                WebkitBackdropFilter: "blur(24px) saturate(1.5)",
                boxShadow: glassShadow,
              } : {
                color: systemPromptOpen ? "var(--text-primary)" : "var(--text-tertiary)",
                letterSpacing: "0.08em",
                border: `1px solid ${glassBorder}`,
                background: glassBg,
                backdropFilter: "blur(24px) saturate(1.5)",
                WebkitBackdropFilter: "blur(24px) saturate(1.5)",
                boxShadow: glassShadow,
              }}
              title="Agent + auth + params sent with every task (module, model, system prompts)"
              aria-expanded={systemPromptOpen}
            >
              PARAMS
              {activeSysPrompts.length > 0 && (
                <span
                  className="inline-flex items-center justify-center text-[11px] font-mono rounded-full px-1"
                  style={{
                    minWidth: 16,
                    background: `${activeModelChip.color}30`,
                    color: activeModelChip.color,
                    boxShadow: `0 0 5px ${activeModelChip.color}50`,
                  }}
                  title={`${activeSysPrompts.length} system prompt${activeSysPrompts.length > 1 ? "s" : ""} chained`}
                >
                  {activeSysPrompts.length}
                </span>
              )}
            </button>
            {/* Module chip — the mod the agent is editing. Click jumps
                straight into the searchable mod picker in PARAMS. */}
            <button
              onClick={() => {
                setSystemPromptOpen(true);
                safeSetItem("claude_system_prompt_open", "1");
                setModPickerQuery("");
                setModPickerOpen(true);
                if (!moduleList.length) fetchModules("");
                setTimeout(() => modPickerInputRef.current?.focus(), 0);
              }}
              className="inline-flex items-center gap-1 text-[13px] font-mono px-3.5 py-2 rounded-full focus-ring transition-all hover:brightness-125 max-w-[46vw] sm:max-w-[220px]"
              style={{
                color: "#fbbf24",
                border: "1px solid rgba(251,191,36,0.25)",
                background: glassBg,
                backdropFilter: "blur(24px) saturate(1.5)",
                WebkitBackdropFilter: "blur(24px) saturate(1.5)",
                fontWeight: 600,
                letterSpacing: "0.05em",
                boxShadow: glassShadow,
              }}
              title={`Agent works on module: ${selectedModule || "none"} — click to search & change`}
            >
              <span className="truncate">{selectedModule || "select mod"}</span>
              <span style={{ opacity: 0.55, fontSize: 11 }}>▾</span>
            </button>
            {/* Model chip — which model runs the task; opens PARAMS */}
            <button
              onClick={() => { setSystemPromptOpen(true); safeSetItem("claude_system_prompt_open", "1"); }}
              className="inline-flex items-center text-[13px] font-mono px-3.5 py-2 rounded-full focus-ring transition-all hover:brightness-125 max-w-[34vw] sm:max-w-[200px]"
              style={{
                color: activeModelChip.color,
                border: `1px solid ${activeModelChip.color}40`,
                background: glassBg,
                backdropFilter: "blur(24px) saturate(1.5)",
                WebkitBackdropFilter: "blur(24px) saturate(1.5)",
                fontWeight: 600,
                letterSpacing: "0.05em",
                boxShadow: glassShadow,
              }}
              title={`Model: ${activeModelChip.label} — click to change in PARAMS`}
            >
              <span className="truncate">{activeModelChip.label}</span>
            </button>
            <div className="flex-1" />
            {/* TASKS pill — opens the full-page TASKS view; shows a count
                while jobs run. Right-anchored opposite the params chips. */}
            {(() => {
              const running = jobs.filter(j => j.status === "running" || j.status === "pending");
              if (running.length === 0) return null;
              return (
                <button
                  onClick={() => setSidebarView("tasks")}
                  className="flex items-center gap-1.5 px-3.5 py-2 rounded-full font-code text-[13px] font-bold uppercase transition-all hover:brightness-125 focus-ring"
                  style={{
                    background: glassBg,
                    backdropFilter: "blur(24px) saturate(1.5)",
                    WebkitBackdropFilter: "blur(24px) saturate(1.5)",
                    border: "1px solid color-mix(in srgb, var(--crt-blue) 35%, transparent)",
                    color: "var(--text-secondary)",
                    letterSpacing: "0.06em",
                    boxShadow: glassShadow,
                  }}
                  title="Open the full-page tasks view"
                >
                  <span
                    className="animate-spin inline-block"
                    style={{ color: "var(--crt-blue)", fontSize: 14, lineHeight: 1 }}
                  >
                    ⟳
                  </span>
                  <span>Tasks</span>
                  <span className="font-mono text-[12px] shrink-0" style={{ color: "var(--text-tertiary)" }}>
                    {running.length}
                  </span>
                </button>
              );
            })()}
          </div>
          {/* ── The band — everything lives on ONE line at the bottom:
              [+] [ask agent…] [imgs] [▶] — module/model/agent/auth
              chips live on the params strip above, not on the band. ── */}
          <div
            className="flex gap-1.5 sm:gap-2 items-stretch rounded-full"
            style={{
              background: glassBg,
              backdropFilter: "blur(24px) saturate(1.5)",
              WebkitBackdropFilter: "blur(24px) saturate(1.5)",
              border: `1px solid ${submitting ? `${activeModelChip.color}55` : glassBorder}`,
              boxShadow: submitting
                ? `0 0 0 4px ${activeModelChip.color}14, 0 10px 32px -14px ${activeModelChip.color}40`
                : glassShadow,
              transition: "border-color 200ms ease, box-shadow 200ms ease",
              padding: 8,
            }}
          >
            {/* "+" — build / fork / import a module, right where you type.
                Opens the same compact create form as the rail, popping up
                above the composer. */}
            <div className="relative flex items-center shrink-0" ref={composerCreateRef}>
              {showHeaderCreateForm && createAnchor === "composer" && (
                <div
                  className="absolute bottom-full left-0 mb-2 border rounded-lg p-2 z-[120] w-[300px] max-w-[80vw]"
                  style={{
                    background: "var(--bg-primary)",
                    borderColor: "color-mix(in srgb, var(--crt-green) 30%, transparent)",
                    boxShadow: "0 12px 40px rgba(0,0,0,0.45)",
                  }}
                >
                  {renderCompactCreateForm()}
                </div>
              )}
              <button
                onClick={() => {
                  if (showHeaderCreateForm && createAnchor === "composer") {
                    setShowHeaderCreateForm(null);
                    return;
                  }
                  setCreateAnchor("composer");
                  setHeaderNewName("");
                  setHeaderGithubUrl("");
                  setShowHeaderCreateForm("create");
                }}
                className="flex items-center justify-center rounded-full transition-all hover:brightness-125 focus-ring"
                style={{
                  // 44px = the iOS/Android minimum comfortable touch target
                  width: 44,
                  height: 44,
                  border: "none",
                  color: showHeaderCreateForm && createAnchor === "composer" ? "var(--crt-green)" : "color-mix(in srgb, var(--crt-green) 65%, var(--text-secondary))",
                  background: showHeaderCreateForm && createAnchor === "composer"
                    ? "color-mix(in srgb, var(--crt-green) 14%, transparent)"
                    : (isLight ? "rgba(0,0,0,0.045)" : "rgba(255,255,255,0.06)"),
                  fontSize: 22,
                  fontWeight: 300,
                  lineHeight: 1,
                }}
                title="Build, fork or import a module"
                aria-expanded={!!(showHeaderCreateForm && createAnchor === "composer")}
                aria-label="Build, fork or import a module"
              >
                +
              </button>
            </div>
            <input
              ref={composerInputRef}
              type="text"
              value={prompt}
              onChange={e => setPrompt(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submitJob(); } }}
              onPaste={(e) => {
                const items = e.clipboardData?.items;
                if (!items) return;
                for (let i = 0; i < items.length; i++) {
                  if (items[i].type.startsWith("image/")) {
                    e.preventDefault();
                    const file = items[i].getAsFile();
                    if (!file) continue;
                    const reader = new FileReader();
                    reader.onload = () => {
                      const base64 = reader.result as string;
                      setImages((prev) => [...prev, { name: file.name || `image-${Date.now()}.png`, data: base64 }]);
                    };
                    reader.readAsDataURL(file);
                  }
                }
              }}
              placeholder="ask agent…"
              disabled={submitting}
              className="flex-1 min-w-0 px-2.5 py-2.5 sm:px-3.5 sm:py-3 outline-none text-[16px] sm:text-[17px]"
              style={{
                color: "var(--text-primary)",
                fontFamily: "'JetBrains Mono', monospace",
                background: "transparent",
                border: "none",
                borderRadius: 9999,
                outline: "none",
                boxShadow: "none",
              }}
              onFocus={e => {
                const wrap = e.currentTarget.parentElement as HTMLElement;
                if (wrap) {
                  wrap.style.borderColor = activeModelChip.color + "66";
                  wrap.style.boxShadow = `0 0 0 4px ${activeModelChip.color}14, 0 10px 32px -14px ${activeModelChip.color}40`;
                }
              }}
              onBlur={e => {
                const wrap = e.currentTarget.parentElement as HTMLElement;
                if (wrap) {
                  wrap.style.borderColor = glassBorder;
                  wrap.style.boxShadow = glassShadow;
                }
              }}
            />
            {/* Attached images — inline chip on the band, no extra line */}
            {images.length > 0 && (
              <span
                className="self-center shrink-0 inline-flex items-center gap-1.5 text-[11px] font-mono px-3 py-2 rounded-full"
                style={{
                  border: "none",
                  background: "rgba(96,165,250,0.10)",
                  color: "rgba(147,197,253,0.9)",
                  fontWeight: 600,
                  letterSpacing: "0.05em",
                }}
                title={`${images.length} image${images.length > 1 ? "s" : ""} attached`}
              >
                {images.length} IMG{images.length > 1 ? "S" : ""}
                <button
                  onClick={() => setImages([])}
                  className="transition-opacity"
                  style={{ color: "var(--crt-red)", opacity: 0.6, lineHeight: 1 }}
                  onMouseEnter={e => (e.currentTarget.style.opacity = "1")}
                  onMouseLeave={e => (e.currentTarget.style.opacity = "0.6")}
                  title="Clear images"
                  aria-label="Clear images"
                >
                  ✕
                </button>
              </span>
            )}
            <button
              onClick={submitJob}
              disabled={!prompt.trim() || submitting}
              className="self-center rounded-full font-bold focus-ring shrink-0 flex items-center justify-center"
              style={{
                width: 44,
                height: 44,
                backgroundColor: !prompt.trim() || submitting
                  ? `${activeModelChip.color}1c`
                  : activeModelChip.color,
                border: "none",
                color: !prompt.trim() || submitting ? `${activeModelChip.color}88` : "var(--bg-primary)",
                fontSize: 20,
                lineHeight: 1,
                boxShadow: !prompt.trim() || submitting ? "none" : `0 4px 14px -4px ${activeModelChip.color}90`,
                transition: "background-color 180ms ease, box-shadow 180ms ease, transform 120ms ease",
              }}
              onMouseEnter={e => {
                if (!e.currentTarget.disabled) {
                  e.currentTarget.style.filter = "brightness(1.15)";
                  e.currentTarget.style.transform = "scale(1.05)";
                }
              }}
              onMouseLeave={e => {
                e.currentTarget.style.filter = "";
                e.currentTarget.style.transform = "";
              }}
              title={submitting ? "Submitting…" : "Send (Enter)"}
            >
              {submitting ? <span className="animate-spin inline-block">⟳</span> : "↑"}
            </button>
            {/* Float / minimize — pop the bar out to a movable panel, or
                collapse it to a pill tool (bottom-right) */}
            {!composerFloating && (
              <button
                onClick={() => {
                  const w = Math.min(composerW, window.innerWidth - 40);
                  setComposerPos({
                    x: Math.max(8, Math.round((window.innerWidth - w) / 2)),
                    y: Math.max(8, window.innerHeight - 200),
                  });
                  setComposerFloating(true);
                }}
                className="self-center shrink-0 hidden sm:inline-flex text-[14px] px-1.5 py-2 rounded focus-ring"
                style={{ color: "var(--text-tertiary)" }}
                onMouseEnter={e => (e.currentTarget.style.color = "var(--text-primary)")}
                onMouseLeave={e => (e.currentTarget.style.color = "var(--text-tertiary)")}
                title="Float the ask bar — drag it anywhere, resize from the edges"
                aria-label="Float the ask bar"
              >
                ⊞
              </button>
            )}
            <button
              onClick={() => setComposerMinimized(true)}
              className="self-center shrink-0 text-[14px] px-1.5 py-2 rounded focus-ring"
              style={{ color: "var(--text-tertiary)" }}
              onMouseEnter={e => (e.currentTarget.style.color = "var(--text-primary)")}
              onMouseLeave={e => (e.currentTarget.style.color = "var(--text-tertiary)")}
              title="Minimize the ask bar to a tool"
              aria-label="Minimize the ask bar"
            >
              ─
            </button>
          </div>
      </>
    );
    // Floating — a movable, width-resizable panel; height follows content.
    // Drag/resize are mouse-only and a floated bar can sit under a phone
    // keyboard, so on mobile the bar always renders docked instead.
    if (composerFloating && !isMobile) {
      return (
        <div
          className="fixed flex flex-col"
          style={{
            left: composerPos.x,
            top: composerPos.y,
            width: composerW,
            maxWidth: "calc(100vw - 16px)",
            zIndex: 95,
            background: glassBg,
            backdropFilter: "blur(28px) saturate(1.5)",
            WebkitBackdropFilter: "blur(28px) saturate(1.5)",
            border: `1px solid ${glassBorder}`,
            borderRadius: 20,
            boxShadow: "0 18px 48px rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.05)",
          }}
        >
          {/* Drag handle title bar */}
          <div
            className="flex items-center justify-between px-3 py-1 shrink-0 select-none"
            style={{
              background: "transparent",
              borderBottom: `1px solid ${glassBorder}`,
              borderRadius: "20px 20px 0 0",
              cursor: "grab",
            }}
            onMouseDown={(e) => {
              if ((e.target as HTMLElement).closest("button")) return;
              e.preventDefault();
              composerDrag.current = { startX: e.clientX, startY: e.clientY, origX: composerPos.x, origY: composerPos.y };
              document.body.style.cursor = 'grabbing';
              document.body.style.userSelect = 'none';
              setIframesInert(true);
            }}
          >
            <span className="text-[10px] font-code uppercase" style={{ color: "var(--text-tertiary)", letterSpacing: "0.1em" }}>
              ⠿ ASK
            </span>
            <div className="flex items-center gap-1">
              <button
                onClick={() => setComposerMinimized(true)}
                className="text-[11px] px-1.5 py-0.5 rounded focus-ring"
                style={{ color: "var(--text-tertiary)" }}
                onMouseEnter={e => (e.currentTarget.style.color = "var(--text-primary)")}
                onMouseLeave={e => (e.currentTarget.style.color = "var(--text-tertiary)")}
                title="Minimize to a tool"
                aria-label="Minimize the ask bar"
              >
                ─
              </button>
              <button
                onClick={() => setComposerFloating(false)}
                className="text-[11px] px-1.5 py-0.5 rounded focus-ring"
                style={{ color: "var(--text-tertiary)" }}
                onMouseEnter={e => (e.currentTarget.style.color = "var(--text-primary)")}
                onMouseLeave={e => (e.currentTarget.style.color = "var(--text-tertiary)")}
                title="Dock back to the bottom"
                aria-label="Dock the ask bar"
              >
                ⊡
              </button>
            </div>
          </div>
          <div className="px-2.5 py-2">{inner}</div>
          {/* Resize edges — width only */}
          <div
            className="absolute top-0 bottom-0 right-0 w-1.5"
            style={{ cursor: "e-resize" }}
            onMouseDown={(e) => { e.preventDefault(); composerResize.current = { startX: e.clientX, origW: composerW, origX: composerPos.x, edge: "e" }; document.body.style.cursor = 'e-resize'; document.body.style.userSelect = 'none'; setIframesInert(true); }}
          />
          <div
            className="absolute top-0 bottom-0 left-0 w-1.5"
            style={{ cursor: "w-resize" }}
            onMouseDown={(e) => { e.preventDefault(); composerResize.current = { startX: e.clientX, origW: composerW, origX: composerPos.x, edge: "w" }; document.body.style.cursor = 'w-resize'; document.body.style.userSelect = 'none'; setIframesInert(true); }}
          />
        </div>
      );
    }
    // Docked — the classic full-width bar at the bottom of the console.
    // Spans the ENTIRE viewport width: the nav rail (fixed, left) stops
    // above it via the --composer-dock-h var, so there's no dead margin.
    return (
      <div
        ref={composerDockRef}
        className="shrink-0 px-2.5 sm:px-3.5 pt-2 sm:pt-3"
        style={{
          position: "relative",
          zIndex: 80,
          background: `linear-gradient(0deg, var(--bg-primary), ${tintBg})`,
          // Clear the iPhone home-indicator / Android gesture bar
          paddingBottom: `calc(env(safe-area-inset-bottom, 0px) + ${isMobile ? 10 : 12}px)`,
        }}
      >
        {inner}
      </div>
    );
  };

  const renderAppTab = (gatewayUrl: string) => {
    return (
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* App Content — with a foreign host or foreign /{mod} from the
            omnibox, this site's registry can't know whether an app exists
            there, so trust the URL and render the iframe. */}
        <div className="flex-1 overflow-hidden">
          {selectedModuleInfo?.app_url || omniForeignMod || gatewayHostOverride ? (
            appPhoneView ? (
              // Device frame — the iframe is genuinely 390px wide, so the
              // embedded app's own media queries fire exactly as on a phone.
              <div
                className="w-full h-full flex flex-col items-center justify-center gap-2 overflow-auto"
                style={{ padding: "14px 12px", background: "radial-gradient(ellipse at 50% 0%, var(--bg-tint), var(--bg-primary))" }}
              >
                <span className="text-[9px] font-bold uppercase tracking-[0.14em] shrink-0" style={{ color: "var(--text-tertiary)" }}>
                  ▯ 390 × 844 · phone viewport
                </span>
                <div
                  className="flex flex-col overflow-hidden shrink"
                  style={{
                    width: 390,
                    maxWidth: "100%",
                    height: 844,
                    maxHeight: "calc(100% - 24px)",
                    borderRadius: 26,
                    border: "1px solid color-mix(in srgb, var(--crt-blue) 35%, var(--border-color))",
                    boxShadow: "0 0 0 6px color-mix(in srgb, var(--crt-dark) 70%, transparent), 0 18px 50px rgba(0,0,0,0.5)",
                    background: "var(--bg-primary)",
                  }}
                >
                  <iframe
                    src={gatewayUrl}
                    className="w-full flex-1 border-0"
                    title="Module App (phone preview)"
                  />
                </div>
              </div>
            ) : (
              <iframe
                src={gatewayUrl}
                className="w-full h-full border-0"
                title="Module App"
              />
            )
          ) : (
            <div className="flex flex-col items-center justify-center h-full gap-4 p-6">
              <span className="text-[48px] text-crt-green/10">🎨</span>
              <span className="text-[14px] text-crt-green/30 uppercase" style={{ letterSpacing: "0.01em" }}>
                No app available
              </span>
              <p className="text-[14px] text-crt-green/20 text-center max-w-xs">
                {selectedModule
                  ? `Module "${selectedModule}" does not have an app interface.`
                  : "Select a module with an app to view it here."}
              </p>
            </div>
          )}
        </div>
      </div>
    );
  };


  // Depth-based guide line colors for the JSON tree
  const DEPTH_COLORS = [
    "#ff6b6b", // red
    "#ffa502", // orange
    "#ffd43b", // yellow
    "#51cf66", // green
    "#22b8cf", // cyan
    "#748ffc", // blue
    "#cc5de8", // purple
    "#ff6b9d", // pink
  ];

  const renderJsonNode = (key: string | number | null, value: any, path: string, depth: number, isLast: boolean, isArrayItem: boolean): React.ReactNode => {
    const isCollapsed = collapsedPaths.has(path);
    const isCopied = copiedPath === path;
    const isPrimitive = value === null || typeof value !== "object";
    const guideColor = DEPTH_COLORS[depth % DEPTH_COLORS.length];

    // Depth guide lines (vertical colored bars)
    const renderGuides = (d: number) => {
      const guides = [];
      for (let i = 0; i < d; i++) {
        guides.push(
          <span
            key={i}
            className="inline-block shrink-0"
            style={{
              width: "16px",
              borderLeft: `2px solid ${DEPTH_COLORS[i % DEPTH_COLORS.length]}`,
              opacity: 0.2,
              height: "100%",
              minHeight: "18px",
            }}
          />
        );
      }
      return guides;
    };

    // Type badge for values
    const typeBadge = (val: any) => {
      if (val === null) return <span className="text-[13px] px-1 py-px rounded ml-1" style={{ color: jsonNullColor, background: `${jsonNullColor}15`, border: `1px solid ${jsonNullColor}25` }}>null</span>;
      if (typeof val === "boolean") return <span className="text-[13px] px-1 py-px rounded ml-1" style={{ color: jsonBoolColor, background: `${jsonBoolColor}15`, border: `1px solid ${jsonBoolColor}25` }}>bool</span>;
      if (typeof val === "number") return <span className="text-[13px] px-1 py-px rounded ml-1" style={{ color: jsonNumColor, background: `${jsonNumColor}15`, border: `1px solid ${jsonNumColor}25` }}>num</span>;
      if (typeof val === "string" && val.startsWith("0x")) return <span className="text-[13px] px-1 py-px rounded ml-1" style={{ color: jsonAddrColor, background: `${jsonAddrColor}15`, border: `1px solid ${jsonAddrColor}25` }}>addr</span>;
      if (typeof val === "string" && val.startsWith("http")) return <span className="text-[13px] px-1 py-px rounded ml-1" style={{ color: jsonUrlColor, background: `${jsonUrlColor}15`, border: `1px solid ${jsonUrlColor}25` }}>url</span>;
      return null;
    };

    // Render value portion with enhanced colors
    const renderVal = () => {
      if (value === null) return <span style={{ color: jsonNullColor, fontStyle: "italic" }}>null</span>;
      if (typeof value === "boolean") return <span style={{ color: jsonBoolColor, fontWeight: "bold", textShadow: "none" }}>{String(value)}</span>;
      if (typeof value === "number") return <span style={{ color: jsonNumColor, textShadow: "none" }}>{value}</span>;
      if (typeof value === "string") {
        const isUrl = value.startsWith("http");
        const isAddr = value.startsWith("0x");
        const color = isUrl ? jsonUrlColor : isAddr ? jsonAddrColor : jsonStrColor;
        return <span style={{ color, textShadow: "none" }}>&quot;{value}&quot;</span>;
      }
      return null;
    };

    // Primitive
    if (isPrimitive) {
      return (
        <div
          key={path}
          className="group/jrow flex items-stretch transition-colors"
          style={{
            background: "transparent",
          }}
          onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = jsonRowHover; }}
          onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = "transparent"; }}
        >
          {renderGuides(depth)}
          <div className="flex-1 flex items-center py-[2px] pl-1">
            {key !== null && !isArrayItem && (
              <><span style={{ color: jsonKeyColor, fontWeight: "bold" }}>&quot;{key}&quot;</span><span style={{ color: "var(--text-tertiary)", opacity: 0.5 }}>: </span></>
            )}
            {isArrayItem && key !== null && (
              <span className="text-[14px] mr-1.5 inline-flex items-center justify-center w-4 text-center" style={{ color: "var(--text-tertiary)", opacity: 0.35 }}>{key}</span>
            )}
            {renderVal()}
            {typeBadge(value)}
            {!isLast && <span style={{ color: "var(--text-tertiary)", opacity: 0.25 }}>,</span>}
          </div>
          <span
            onClick={() => copyValue(path, value)}
            className="cursor-pointer opacity-0 group-hover/jrow:opacity-60 hover:!opacity-100 text-[14px] px-2 py-0 mr-2 rounded transition-all select-none shrink-0 flex items-center"
            style={{ color: isCopied ? jsonCopiedColor : "var(--text-tertiary)", background: isCopied ? jsonCopiedBg : copyBtnBg }}
            title="Copy value"
          >
            {isCopied ? "✓ copied" : "⧉"}
          </span>
        </div>
      );
    }

    // Object / Array
    const isArray = Array.isArray(value);
    const entries = isArray ? value.map((v: any, i: number) => [i, v] as [number, any]) : Object.entries(value);
    const count = entries.length;
    const openBracket = isArray ? "[" : "{";
    const closeBracket = isArray ? "]" : "}";
    const bracketColor = DEPTH_COLORS[depth % DEPTH_COLORS.length];

    if (count === 0) {
      return (
        <div key={path} className="flex items-stretch" style={{}}>
          {renderGuides(depth)}
          <div className="flex items-center py-[2px] pl-1">
            {key !== null && !isArrayItem && (
              <><span style={{ color: jsonKeyColor, fontWeight: "bold" }}>&quot;{key}&quot;</span><span style={{ color: "var(--text-tertiary)", opacity: 0.5 }}>: </span></>
            )}
            <span style={{ color: bracketColor, opacity: 0.5 }}>{openBracket}{closeBracket}</span>
            {!isLast && <span style={{ color: "var(--text-tertiary)", opacity: 0.25 }}>,</span>}
          </div>
        </div>
      );
    }

    return (
      <div key={path}>
        {/* Header row */}
        <div
          className="group/jrow flex items-stretch cursor-pointer transition-colors"
          style={{ background: "transparent" }}
          onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = jsonRowHover; }}
          onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = "transparent"; }}
          onClick={() => toggleCollapse(path)}
        >
          {renderGuides(depth)}
          <span className="w-4 flex items-center justify-center text-[13px] shrink-0 select-none transition-transform" style={{ color: bracketColor }}>
            {isCollapsed ? "▸" : "▾"}
          </span>
          <div className="flex-1 flex items-center py-[2px]">
            {key !== null && !isArrayItem && (
              <><span style={{ color: jsonKeyColor, fontWeight: "bold" }}>&quot;{key}&quot;</span><span style={{ color: "var(--text-tertiary)", opacity: 0.5 }}>: </span></>
            )}
            {isArrayItem && key !== null && (
              <span className="text-[14px] mr-1.5 inline-flex items-center justify-center w-4 text-center" style={{ color: "var(--text-tertiary)", opacity: 0.35 }}>{key}</span>
            )}
            <span style={{ color: bracketColor, fontWeight: "bold", textShadow: "none" }}>{openBracket}</span>
            {isCollapsed && (
              <>
                <span className="text-[14px] px-1.5 mx-1 rounded-sm inline-flex items-center gap-1" style={{
                  color: bracketColor,
                  background: `${bracketColor}11`,
                  border: `1px solid ${bracketColor}22`,
                }}>
                  <span style={{ opacity: 0.7 }}>{isArray ? "▤" : "◈"}</span>
                  {count} {isArray ? (count === 1 ? "item" : "items") : (count === 1 ? "key" : "keys")}
                </span>
                <span style={{ color: bracketColor, fontWeight: "bold", textShadow: "none" }}>{closeBracket}</span>
                {!isLast && <span style={{ color: "var(--text-tertiary)", opacity: 0.25 }}>,</span>}
              </>
            )}
          </div>
          <span
            onClick={(e) => { e.stopPropagation(); copyValue(path, value); }}
            className="cursor-pointer opacity-0 group-hover/jrow:opacity-60 hover:!opacity-100 text-[14px] px-2 py-0 mr-2 rounded transition-all select-none shrink-0 flex items-center"
            style={{ color: isCopied ? jsonCopiedColor : "var(--text-tertiary)", background: isCopied ? jsonCopiedBg : copyBtnBg }}
            title="Copy object"
          >
            {isCopied ? "✓ copied" : "⧉"}
          </span>
        </div>
        {/* Children */}
        {!isCollapsed && (
          <>
            {entries.map(([k, v]: [any, any], idx: number) => {
              const childPath = isArray ? `${path}[${k}]` : `${path}.${k}`;
              return renderJsonNode(k, v, childPath, depth + 1, idx === count - 1, isArray);
            })}
            <div className="flex items-stretch">
              {renderGuides(depth)}
              <span className="w-4 inline-block shrink-0" />
              <span style={{ color: bracketColor, fontWeight: "bold", textShadow: "none" }}>{closeBracket}</span>
              {!isLast && <span style={{ color: "var(--text-tertiary)", opacity: 0.25 }}>,</span>}
            </div>
          </>
        )}
      </div>
    );
  };

  // Sign a message with the currently-connected wallet — mirrors connectWallet's
  // per-type signing so terminal auth reuses the same key the owner signed in with.
  const signTerminalMessage = async (msg: string): Promise<string> => {
    if (!address) throw new Error("connect your wallet first");
    if (walletType === "metamask" || walletType === "subwallet") {
      const ethereum = (window as any).ethereum;
      if (!ethereum) throw new Error("no wallet provider found");
      const provider =
        walletType === "subwallet" && ethereum.providers
          ? ethereum.providers.find((p: any) => p.isSubWallet) || ethereum
          : ethereum;
      try {
        return await provider.request({ method: "personal_sign", params: [msg, address] });
      } catch (e: any) {
        // Extension-side bignumber crash is transient — one silent retry.
        if (!isExtensionBignumberBug(e)) throw e;
        await new Promise((r) => setTimeout(r, 600));
        return await provider.request({ method: "personal_sign", params: [msg, address] });
      }
    }
    if (walletType === "local") {
      const { ethers } = await import("ethers");
      const seed = localStorage.getItem("claude_jobs_seed");
      if (!seed) throw new Error("no local wallet seed found — reconnect");
      return await ethers.Wallet.fromPhrase(seed).signMessage(msg);
    }
    throw new Error(`reconnect with MetaMask or a local wallet to authorize the terminal (current: ${walletType || "none"})`);
  };

  // ── Sudo authorization ────────────────────────────────────────────────
  // Privileged cross-module operations (editing/deleting modules OTHER than
  // claude) require a *fresh* owner signature bound to the exact (action, target)
  // — verified once and replay-rejected server-side (src/api/src/sudo.rs). This
  // builds the byte-for-byte message the Rust side reconstructs.
  const buildSudoMessage = (action: string, target: string, time: number, nonce: string): string =>
    [
      "MOD Claude Sudo Authorization",
      `action: ${action}`,
      `target: ${target}`,
      `time: ${time}`,
      `nonce: ${nonce}`,
      "",
      "Authorizing a privileged cross-module operation as the owner. Free signature, not a transaction.",
    ].join("\n");

  const base64UrlEncode = (s: string): string =>
    btoa(unescape(encodeURIComponent(s)))
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");

  // Sign one privileged operation and return the base64url `x-sudo` token. Throws
  // if the connected wallet can't sign (e.g. a read-only session).
  const mintSudoToken = async (action: string, target: string): Promise<string> => {
    if (!address) throw new Error("connect the owner wallet to authorize this");
    const time = Math.floor(Date.now() / 1000);
    const nonce = Array.from(crypto.getRandomValues(new Uint8Array(16)))
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");
    const signature = await signTerminalMessage(buildSudoMessage(action, target, time, nonce));
    return base64UrlEncode(JSON.stringify({ action, target, time, nonce, key: address, signature }));
  };

  // Open the Sudo Authorization sheet and resolve with a signed x-sudo token once
  // the owner approves (or reject if they cancel). The signing itself happens in
  // confirmSudo so the modal can show signing / success / error states.
  const requestSudo = (action: string, target: string): Promise<string> =>
    new Promise<string>((resolve, reject) => {
      sudoResolver.current = { resolve, reject };
      setSudoError(null);
      setSudoStatus("review");
      setSudoReq({ action, target });
    });

  const confirmSudo = async () => {
    if (!sudoReq) return;
    setSudoStatus("signing");
    setSudoError(null);
    try {
      const tok = await mintSudoToken(sudoReq.action, sudoReq.target);
      setSudoStatus("success");
      sudoResolver.current?.resolve(tok);
      sudoResolver.current = null;
      // Let the "Authorized" checkmark breathe, then close.
      setTimeout(() => setSudoReq(null), 620);
      // The server opens the sudo session when it verifies this signature —
      // give the retried request a beat to land, then reflect it in ACCOUNT.
      setTimeout(() => { refreshSudoStatus(); }, 1200);
    } catch (e: any) {
      setSudoStatus("error");
      setSudoError(friendlyWalletError(e) || "Signature was rejected");
    }
  };

  const cancelSudo = () => {
    sudoResolver.current?.reject(new Error("sudo cancelled"));
    sudoResolver.current = null;
    setSudoReq(null);
  };

  // authFetch that handles the privileged path: if the server replies
  // 401 { sudo_required }, raise the Sudo Authorization sheet, get a fresh owner
  // signature bound to the exact (action, target), and retry once. If the owner
  // cancels, the original 401 is returned so the caller surfaces the reason.
  const authFetchSudo = async (
    path: string,
    opts: RequestInit = {},
    timeoutMs?: number,
  ): Promise<Response> => {
    const res = await authFetch(path, opts, timeoutMs);
    if (res.status !== 401) return res;
    const data = await res.clone().json().catch(() => ({} as any));
    if (!data?.sudo_required) return res;
    try {
      const sudoTok = await requestSudo(data.action || "write", data.target || path);
      return await authFetch(
        path,
        { ...opts, headers: { ...(opts.headers as Record<string, string>), "x-sudo": sudoTok } },
        timeoutMs,
      );
    } catch {
      return res; // cancelled — let the caller show the server's sudo_required message
    }
  };
  // Keep the ref current so earlier-declared callbacks (e.g. module process
  // control) can invoke the latest authFetchSudo without a render-time TDZ.
  authFetchSudoRef.current = authFetchSudo;

  // Owner-tailored sudo policy. Changing it ALWAYS demands a fresh signature
  // (the server refuses to let a cached session loosen the auth rules), so this
  // goes through authFetchSudo and will raise the Sudo sheet.
  const setSudoPolicy = async (patch: { session_secs?: number; always_ask?: string[] }) => {
    setSudoPolicyBusy(true);
    setSudoPolicyErr(null);
    try {
      const res = await authFetchSudo("/sudo/policy", { method: "POST", body: JSON.stringify(patch) });
      const d = await res.json().catch(() => ({} as any));
      if (!res.ok) throw new Error(d?.error || "policy update failed");
      await refreshSudoStatus();
    } catch (e: any) {
      if (e?.message !== "sudo cancelled") setSudoPolicyErr(e?.message || "policy update failed");
    } finally {
      setSudoPolicyBusy(false);
    }
  };

  // Re-lock sudo immediately ("sudo -k") — free, no signature needed.
  const lockSudo = async () => {
    try { await authFetch("/sudo/lock", { method: "POST" }); } catch {}
    await refreshSudoStatus();
  };

  // Prove ownership by signing, then store the minted session token.
  const authorizeTerminal = async () => {
    if (!address) { setTerminalAuthError("connect your wallet first"); return; }
    setTerminalAuthing(true);
    setTerminalAuthError(null);
    try {
      const ts = Date.now();
      const msg = [
        "MOD Terminal Authorization",
        `address: ${address.toLowerCase()}`,
        `ts: ${ts}`,
        "",
        "Sign to use the owner terminal on this server. This is a free signature, not a transaction.",
      ].join("\n");
      const signature = await signTerminalMessage(msg);
      const res = await fetch(`${DEFAULT_BASE_PATH}/api/terminal/auth`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ address, ts, signature }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || `auth failed (HTTP ${res.status})`);
      setTerminalToken(data.token);
      setTerminalTokenExp(data.expiresAt);
      safeSetItem("claude_terminal_token", data.token);
      safeSetItem("claude_terminal_token_exp", String(data.expiresAt));
    } catch (e: any) {
      setTerminalAuthError(e?.message || "authorization failed");
    } finally {
      setTerminalAuthing(false);
    }
  };

  const clearTerminalAuth = () => {
    setTerminalToken(null);
    setTerminalTokenExp(0);
    localStorage.removeItem("claude_terminal_token");
    localStorage.removeItem("claude_terminal_token_exp");
  };

  const runTerminalCommand = async (rawCmd: string) => {
    const cmd = rawCmd.trim();
    if (!cmd || terminalRunning) return;
    const cwd = selectedModuleInfo?.path || workDir || "~";
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    const start = Date.now();

    // Client-side built-ins so they feel instant and don't bother the server.
    if (cmd === "clear" || cmd === "cls") {
      setTerminalHistory([]);
      setTerminalInput("");
      setTerminalRecallIdx(null);
      return;
    }

    setTerminalHistory(h => [...h, {
      id, cmd, cwd, stdout: "", stderr: "", code: null, durationMs: 0, pending: true,
    }]);
    setTerminalInput("");
    setTerminalRecallIdx(null);
    setTerminalRunning(true);
    try {
      const res = await fetch(`${DEFAULT_BASE_PATH}/api/terminal`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(terminalToken ? { "x-terminal-token": terminalToken } : {}),
        },
        body: JSON.stringify({ cmd, cwd }),
      });
      if (res.status === 401) {
        // Session missing/expired — drop it so the gate prompts a re-sign.
        clearTerminalAuth();
        setTerminalAuthError("session expired — authorize again");
        setTerminalHistory(h => h.filter(e => e.id !== id));
        setTerminalRunning(false);
        return;
      }
      const data = await res.json();
      setTerminalHistory(h => h.map(e => e.id === id ? {
        ...e,
        stdout: data.stdout || "",
        stderr: data.stderr || (res.ok ? "" : (data.error || `HTTP ${res.status}`)),
        code: typeof data.code === "number" ? data.code : (res.ok ? 0 : 1),
        durationMs: Date.now() - start,
        pending: false,
        nix: data.nix ?? null,
      } : e));
    } catch (err: any) {
      setTerminalHistory(h => h.map(e => e.id === id ? {
        ...e,
        stderr: err?.message || String(err),
        code: 1,
        durationMs: Date.now() - start,
        pending: false,
      } : e));
    } finally {
      setTerminalRunning(false);
    }
  };

  const renderTerminalTab = () => {
    const cwd = selectedModuleInfo?.path || workDir || "~";
    const prompt = `${selectedModule || "shell"} $`;
    const termAuthed = !!terminalToken && terminalTokenExp > Date.now();
    const termHoursLeft = termAuthed ? Math.max(1, Math.round((terminalTokenExp - Date.now()) / 3600000)) : 0;

    return (
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Header */}
        <div
          className="px-4 py-2 border-b flex items-center justify-between gap-2 shrink-0"
          style={{ borderColor: "var(--border-color)", background: "color-mix(in srgb, var(--crt-blue) 3%, transparent)" }}
        >
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-[12px] leading-none" style={{ color: "var(--crt-blue)" }}>▶_</span>
            <span className="text-[11px] font-bold uppercase tracking-[0.18em] shrink-0" style={{ color: "var(--crt-blue)" }}>
              {selectedModule || "module"} TERMINAL
            </span>
            <span className="text-[10px] font-mono truncate" style={{ color: "var(--text-tertiary)" }}>
              {cwd}
            </span>
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            <span
              className="text-[9px] px-1.5 py-0.5 rounded-sm uppercase font-bold"
              style={{
                color: termAuthed ? "var(--crt-green)" : "var(--crt-amber)",
                background: `color-mix(in srgb, ${termAuthed ? "var(--crt-green)" : "var(--crt-amber)"} 10%, transparent)`,
                border: `1px solid color-mix(in srgb, ${termAuthed ? "var(--crt-green)" : "var(--crt-amber)"} 30%, transparent)`,
                letterSpacing: "0.08em",
              }}
              title={termAuthed ? `Owner session active — ~${termHoursLeft}h left` : "Owner-only — authorize with your wallet to run commands"}
            >
              {termAuthed ? `♛ ${termHoursLeft}h` : "LOCKED"}
            </span>
            {termAuthed && (
              <button
                onClick={clearTerminalAuth}
                className="text-[10px] px-2 py-1 rounded-sm border uppercase font-bold transition-all"
                style={{ borderColor: "color-mix(in srgb, var(--border-color) 50%, transparent)", color: "var(--text-tertiary)" }}
                title="End the terminal session"
              >
                LOCK
              </button>
            )}
            <button
              onClick={() => { setTerminalHistory([]); setTerminalRecallIdx(null); }}
              className="text-[10px] px-2 py-1 rounded-sm border uppercase font-bold transition-all"
              style={{ borderColor: "color-mix(in srgb, var(--border-color) 50%, transparent)", color: "var(--text-tertiary)" }}
              title="Clear terminal output"
            >
              CLEAR
            </button>
          </div>
        </div>

        {/* Scrollback */}
        <div
          ref={(el) => { if (el) el.scrollTop = el.scrollHeight; }}
          className="flex-1 overflow-auto px-4 py-3 text-[12px]"
          style={{
            fontFamily: "var(--font-code, monospace)",
            lineHeight: "1.5",
            background: "var(--bg-primary)",
            color: "var(--text-secondary)",
          }}
        >
          {terminalHistory.length === 0 ? (
            <div style={{ color: "var(--text-tertiary)" }}>
              <div>{`# shell access in ${cwd}`}</div>
              <div>{`# commands run via bash -lc — owner-only, single-shot (no PTY).`}</div>
              <div>{`# if the module ships a flake.nix/shell.nix, commands auto-enter its nix env (⬢).`}</div>
              <div>{`# try: ls, cat config.json, git status, pwd`}</div>
              <div>{`# 'clear' wipes the scrollback.`}</div>
            </div>
          ) : terminalHistory.map(e => (
            <div key={e.id} className="mb-2">
              <div className="flex gap-2">
                <span style={{ color: "var(--crt-green)", flexShrink: 0 }}>{prompt}</span>
                <span style={{ color: "var(--text-primary)", whiteSpace: "pre-wrap", wordBreak: "break-all" }}>{e.cmd}</span>
              </div>
              {e.pending ? (
                <div style={{ color: "var(--text-tertiary)" }}>…running…</div>
              ) : (
                <>
                  {e.stdout && (
                    <pre className="m-0" style={{ whiteSpace: "pre-wrap", wordBreak: "break-all", color: "var(--text-secondary)" }}>{e.stdout}</pre>
                  )}
                  {e.stderr && (
                    <pre className="m-0" style={{ whiteSpace: "pre-wrap", wordBreak: "break-all", color: "var(--crt-red)" }}>{e.stderr}</pre>
                  )}
                  <div className="text-[10px] flex items-center gap-2" style={{ color: "var(--text-tertiary)" }}>
                    <span>exit {e.code} · {e.durationMs}ms</span>
                    {e.nix && (
                      <span
                        title={`ran inside the module's nix ${e.nix} environment`}
                        style={{ color: "var(--crt-blue)" }}
                      >⬢ nix:{e.nix}</span>
                    )}
                  </div>
                </>
              )}
            </div>
          ))}
        </div>

        {/* Authorize gate — shown until the owner has a valid session */}
        {!termAuthed ? (
          <div
            className="border-t flex flex-col items-center justify-center gap-3 px-4 py-6 shrink-0"
            style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary, var(--bg-primary))" }}
          >
            <div className="text-[11px] text-center max-w-sm" style={{ color: "var(--text-tertiary)", lineHeight: 1.6 }}>
              This terminal runs shell commands on the host. Sign with the owner wallet to unlock it for ~24h.
            </div>
            <button
              onClick={authorizeTerminal}
              disabled={terminalAuthing || !address}
              className="text-[11px] px-4 py-2 rounded-sm border uppercase font-bold transition-all"
              style={{
                borderColor: "color-mix(in srgb, var(--crt-blue) 55%, transparent)",
                color: "var(--crt-blue)",
                background: "color-mix(in srgb, var(--crt-blue) 10%, transparent)",
                opacity: terminalAuthing || !address ? 0.5 : 1,
                letterSpacing: "0.08em",
              }}
            >
              {terminalAuthing ? "SIGN IN WALLET…" : !address ? "CONNECT WALLET FIRST" : "▶ AUTHORIZE WITH WALLET"}
            </button>
            {terminalAuthError && (
              <div className="text-[10px] text-center" style={{ color: "var(--crt-red)" }}>{terminalAuthError}</div>
            )}
          </div>
        ) : (
        <form
          onSubmit={(ev) => { ev.preventDefault(); runTerminalCommand(terminalInput); }}
          className="border-t flex items-center gap-2 px-4 py-2 shrink-0"
          style={{ borderColor: "var(--border-color)", background: "var(--bg-secondary, var(--bg-primary))" }}
        >
          <span className="text-[12px] font-mono" style={{ color: "var(--crt-green)" }}>{prompt}</span>
          <input
            value={terminalInput}
            onChange={(ev) => { setTerminalInput(ev.target.value); setTerminalRecallIdx(null); }}
            onKeyDown={(ev) => {
              // ArrowUp / ArrowDown walk through prior commands, bash-style.
              if (ev.key === "ArrowUp") {
                if (terminalHistory.length === 0) return;
                ev.preventDefault();
                const next = terminalRecallIdx === null ? terminalHistory.length - 1 : Math.max(0, terminalRecallIdx - 1);
                setTerminalRecallIdx(next);
                setTerminalInput(terminalHistory[next].cmd);
              } else if (ev.key === "ArrowDown") {
                if (terminalRecallIdx === null) return;
                ev.preventDefault();
                const next = terminalRecallIdx + 1;
                if (next >= terminalHistory.length) {
                  setTerminalRecallIdx(null);
                  setTerminalInput("");
                } else {
                  setTerminalRecallIdx(next);
                  setTerminalInput(terminalHistory[next].cmd);
                }
              }
            }}
            disabled={terminalRunning}
            autoFocus
            spellCheck={false}
            autoCorrect="off"
            autoCapitalize="off"
            placeholder={terminalRunning ? "running…" : "type a command, ↑/↓ for history"}
            className="flex-1 bg-transparent outline-none text-[12px] font-mono"
            style={{ color: "var(--text-primary)" }}
          />
          <button
            type="submit"
            disabled={terminalRunning || !terminalInput.trim()}
            className="text-[10px] px-2.5 py-1 rounded-sm border uppercase font-bold transition-all"
            style={{
              borderColor: "color-mix(in srgb, var(--crt-blue) 50%, transparent)",
              color: "var(--crt-blue)",
              background: "color-mix(in srgb, var(--crt-blue) 8%, transparent)",
              opacity: terminalRunning || !terminalInput.trim() ? 0.4 : 1,
            }}
          >
            {terminalRunning ? "…" : "RUN"}
          </button>
        </form>
        )}
      </div>
    );
  };

  // ── API tab — the endpoint catalog IS the playground (FastAPI-docs
  // style, minus the jump to a second view): every row expands in place
  // into a plain-language form with one input per parameter, a TRY IT
  // button, and the response inline. A second sub-tab keeps process logs. ──
  const renderClaudeApiTab = () => {
    const authMeta = (auth: string) =>
      auth === "public"
        ? { c: "var(--crt-green)", label: "ANYONE", note: "Anyone can call this — no sign-in needed." }
        : auth === "bearer"
          ? { c: "var(--crt-blue)", label: "SIGNED IN", note: "You need to be signed in with your wallet to call this." }
          : { c: "var(--crt-amber)", label: "OWNER", note: "Only the owner wallet of this host can call this." };
    const authBadge = (auth: string) => {
      const m = authMeta(auth);
      return (
        <span
          className="text-[9px] px-1.5 py-0.5 rounded-sm border uppercase font-bold shrink-0"
          style={{ color: m.c, borderColor: `color-mix(in srgb, ${m.c} 45%, transparent)`, background: `color-mix(in srgb, ${m.c} 8%, transparent)` }}
        >
          {m.label}
        </span>
      );
    };
    const methodColor = (m: string) =>
      m === "GET" ? "var(--crt-green)" : m === "DELETE" ? "var(--crt-red)" : m === "POST" ? "var(--crt-blue)" : "var(--crt-amber)";
    // Strip the "{prompt, model?}" body hint out of a description — the
    // expanded form renders those as labelled inputs, so repeating the raw
    // fragment in prose would just read as noise.
    const plainDesc = (desc: string) =>
      desc.replace(/\{[^}]+\}\s*/g, "").replace(/^[:\s—-]+|[:\s—-]+$/g, "") || desc;
    const inputStyle = {
      background: "var(--bg-secondary)",
      border: "1px solid var(--border-color)",
      color: "var(--text-primary)",
    } as const;

    const expandRow = (i: number) => {
      if (apiExpanded === i) { setApiExpanded(null); return; }
      setApiExpanded(i);
      setTryParams({});
      setTryBodyFields({});
      setTryBodyRaw(false);
      setTryBody("");
      setTryStatus(null);
      setTryOk(null);
      setTryResp(null);
    };

    // Everything the form needs is derived from the schema row itself
    // (path template + desc), so there is no second source of truth.
    const renderExpanded = (ep: { method: string; path: string; auth: string; desc: string }) => {
      const { pathParams, queryParams } = parseEndpointInputs(ep.path);
      const bodyFields = ep.method === "GET" ? [] : parseBodyFields(ep.desc);
      const wantsBody = ep.method !== "GET";
      const isStream = ep.path.includes("/stream");
      const missingPathParam = pathParams.some((p) => !(tryParams[p] || "").trim());
      const signedIn = !!(token && token !== "local");
      const authWarning =
        ep.auth === "public" ? null
        : !signedIn ? "You aren't signed in, so this will be refused — sign in from the top bar first."
        : ep.auth === "owner" && !isOwner ? "This is owner-only and you aren't the owner, so it will be refused."
        : null;

      const paramInput = (name: string, hint: string, required: boolean, value: string, onChange: (v: string) => void) => (
        <label key={name} className="flex items-center gap-2">
          <span className="text-[11px] font-mono w-32 shrink-0 text-right" style={{ color: "var(--text-secondary)" }}>
            {name}{required && <span style={{ color: "var(--crt-amber)" }}> *</span>}
          </span>
          <input
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder={hint || (required ? "required" : "optional")}
            className="flex-1 text-[12px] font-mono px-2.5 py-1.5 rounded outline-none"
            style={inputStyle}
          />
        </label>
      );

      return (
        <div
          className="flex flex-col gap-2.5 px-3 py-3 rounded-b"
          style={{ background: tintBg, borderTop: `1px dashed ${subtleBorder}` }}
        >
          <div className="text-[12px]" style={{ color: "var(--text-secondary)", lineHeight: 1.6 }}>
            {plainDesc(ep.desc)}.{" "}
            <span style={{ color: "var(--text-tertiary)" }}>{authMeta(ep.auth).note}</span>
          </div>
          {authWarning && (
            <div className="text-[11px]" style={{ color: "var(--crt-amber)" }}>⚠ {authWarning}</div>
          )}

          {(pathParams.length > 0 || queryParams.length > 0 || (!tryBodyRaw && bodyFields.length > 0)) && (
            <div className="flex flex-col gap-1.5">
              {pathParams.map((p) =>
                paramInput(p, "", true, tryParams[p] || "", (v) => setTryParams((prev) => ({ ...prev, [p]: v }))))}
              {queryParams.map((q) =>
                paramInput(q.name, q.hint, false, tryParams[q.name] || "", (v) => setTryParams((prev) => ({ ...prev, [q.name]: v }))))}
              {!tryBodyRaw && bodyFields.map((f) =>
                paramInput(f.name, f.hint, !f.optional, tryBodyFields[f.name] || "", (v) => setTryBodyFields((prev) => ({ ...prev, [f.name]: v }))))}
            </div>
          )}

          {wantsBody && (tryBodyRaw || bodyFields.length === 0) && (
            <textarea
              value={tryBody}
              onChange={(e) => setTryBody(e.target.value)}
              placeholder='{"key": "value"} — JSON body (optional)'
              rows={4}
              className="text-[12px] font-mono px-2.5 py-1.5 rounded outline-none resize-y"
              style={inputStyle}
            />
          )}

          <div className="flex items-center gap-3">
            {isStream ? (
              <a
                href={`${apiUrl}${ep.path.split("?")[0].replace(/:(\w+)/g, (_m, n) => encodeURIComponent((tryParams[n] || "").trim()))}`}
                target="_blank"
                rel="noreferrer"
                className="text-[11px] px-4 py-1.5 rounded uppercase font-bold tracking-wider"
                style={{
                  color: missingPathParam ? "var(--text-tertiary)" : "var(--bg-primary)",
                  background: missingPathParam ? "var(--bg-secondary)" : "var(--crt-blue)",
                  border: missingPathParam ? "1px solid var(--border-color)" : "1px solid transparent",
                  pointerEvents: missingPathParam ? "none" : "auto",
                }}
              >
                Open live stream ↗
              </a>
            ) : (
              <button
                onClick={() => sendTryRequest(ep)}
                disabled={trySending || missingPathParam}
                className="text-[11px] px-4 py-1.5 rounded uppercase font-bold tracking-wider transition-all"
                style={{
                  color: "var(--bg-primary)",
                  background: "var(--crt-blue)",
                  opacity: trySending || missingPathParam ? 0.4 : 1,
                }}
              >
                {trySending ? "Sending…" : "▶ Try it"}
              </button>
            )}
            {missingPathParam && (
              <span className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                fill in the <span style={{ color: "var(--crt-amber)" }}>*</span> fields first
              </span>
            )}
            {wantsBody && bodyFields.length > 0 && (
              <button
                onClick={() => setTryBodyRaw((r) => !r)}
                className="text-[10px] uppercase tracking-wider ml-auto"
                style={{ color: "var(--text-tertiary)" }}
              >
                {tryBodyRaw ? "← simple form" : "edit raw JSON"}
              </button>
            )}
          </div>

          {tryStatus && (
            <div className="flex items-center gap-2 text-[11px]">
              <span
                className="px-2 py-0.5 rounded-sm border font-bold"
                style={{
                  color: tryOk ? "var(--crt-green)" : "var(--crt-red)",
                  borderColor: `color-mix(in srgb, ${tryOk ? "var(--crt-green)" : "var(--crt-red)"} 45%, transparent)`,
                  background: `color-mix(in srgb, ${tryOk ? "var(--crt-green)" : "var(--crt-red)"} 8%, transparent)`,
                }}
              >
                {tryOk ? "✓" : "✗"} {tryStatus}
              </span>
            </div>
          )}
          {tryResp !== null && (
            tryRespKind === "image" ? (
              <img
                src={tryResp}
                alt="response"
                className="rounded max-w-full"
                style={{ border: "1px solid var(--border-color)", maxHeight: "40vh", objectFit: "contain", alignSelf: "flex-start" }}
              />
            ) : (
              <pre
                className="text-[11px] rounded p-3 overflow-auto whitespace-pre max-h-[40vh]"
                style={inputStyle}
              >
                {tryResp}
              </pre>
            )
          )}
        </div>
      );
    };

    const sub = (
      <div className="flex items-center gap-1 px-3 py-1.5 shrink-0" style={{ borderBottom: `1px solid ${subtleBorder}`, background: tintBg }}>
        {([
          { k: "schema" as const, label: "Endpoints" },
          { k: "logs" as const, label: "Logs" },
        ]).map((s) => (
          <button
            key={s.k}
            onClick={() => setApiTabView(s.k)}
            className="text-[11px] px-2.5 py-1 rounded-sm uppercase font-bold tracking-wider transition-all"
            style={{
              color: apiTabView === s.k ? "var(--crt-blue)" : "var(--text-tertiary)",
              background: apiTabView === s.k ? "color-mix(in srgb, var(--crt-blue) 8%, transparent)" : "transparent",
              border: `1px solid ${apiTabView === s.k ? "color-mix(in srgb, var(--crt-blue) 45%, transparent)" : "transparent"}`,
            }}
          >
            {s.label}
          </button>
        ))}
        {apiTabView === "schema" && (
          <input
            value={apiFilter}
            onChange={(e) => setApiFilter(e.target.value)}
            placeholder="Filter… (e.g. jobs, screenshot)"
            className="ml-auto text-[11px] px-2.5 py-1 rounded outline-none w-52"
            style={{ background: "var(--bg-secondary)", border: `1px solid ${subtleBorder}`, color: "var(--text-primary)" }}
          />
        )}
      </div>
    );

    const filter = apiFilter.trim().toLowerCase();
    const visible = (apiSchema?.endpoints || [])
      .map((ep, i) => ({ ep, i }))
      .filter(({ ep }) => !filter || `${ep.method} ${ep.path} ${ep.desc}`.toLowerCase().includes(filter));
    const sections = API_SECTION_ORDER
      .map((title) => ({ title, rows: visible.filter(({ ep }) => endpointSection(ep.path) === title) }))
      .filter((s) => s.rows.length > 0);

    const schemaPane = !apiSchema ? (
      <div className="text-[12px] p-4" style={{ color: "var(--text-tertiary)" }}>
        Loading endpoint catalog… if this never resolves, the API is unreachable at {apiUrl}/schema.
      </div>
    ) : (
      <div className="flex flex-col max-w-3xl">
        <p className="text-[12px] mb-1" style={{ color: "var(--text-tertiary)", lineHeight: 1.6 }}>
          Everything this module can do, callable right here — click a row, fill in the blanks, and hit{" "}
          <span style={{ color: "var(--crt-blue)", fontWeight: 700 }}>TRY IT</span>.{" "}
          <span style={{ color: "var(--crt-green)" }}>ANYONE</span> works without signing in,{" "}
          <span style={{ color: "var(--crt-blue)" }}>SIGNED IN</span> needs your wallet session, and{" "}
          <span style={{ color: "var(--crt-amber)" }}>OWNER</span> is host-only.
          {token && token !== "local" ? " Requests use your current sign-in automatically." : ""}
        </p>
        {sections.length === 0 && (
          <div className="text-[12px] py-6 text-center" style={{ color: "var(--text-tertiary)" }}>
            Nothing matches “{apiFilter}”.
          </div>
        )}
        {sections.map((section) => (
          <div key={section.title} className="flex flex-col">
            <div
              className="text-[10px] uppercase font-bold tracking-widest mt-4 mb-1 px-1"
              style={{ color: "var(--text-tertiary)" }}
            >
              {section.title}
            </div>
            {section.rows.map(({ ep, i }) => (
              <div
                key={i}
                className="rounded"
                style={{
                  border: `1px solid ${apiExpanded === i ? "color-mix(in srgb, var(--crt-blue) 35%, transparent)" : "transparent"}`,
                  borderBottomColor: apiExpanded === i ? undefined : `${subtleBorder}44`,
                  marginBottom: apiExpanded === i ? 8 : 0,
                }}
              >
                <button
                  onClick={() => expandRow(i)}
                  className="w-full flex items-center gap-2 px-2 py-1.5 text-left transition-colors rounded"
                  style={{ background: apiExpanded === i ? tintBg : "transparent" }}
                  onMouseEnter={(e) => { if (apiExpanded !== i) e.currentTarget.style.background = cardHoverBg; }}
                  onMouseLeave={(e) => { if (apiExpanded !== i) e.currentTarget.style.background = "transparent"; }}
                >
                  <span className="text-[10px] font-bold w-12 shrink-0" style={{ color: methodColor(ep.method) }}>
                    {ep.method}
                  </span>
                  <code className="text-[11px] shrink-0" style={{ color: "var(--text-primary)" }}>{ep.path.split("?")[0]}</code>
                  {authBadge(ep.auth)}
                  <span className="text-[11px] truncate flex-1" style={{ color: "var(--text-tertiary)" }}>
                    {plainDesc(ep.desc)}
                  </span>
                  <span className="text-[10px] shrink-0" style={{ color: apiExpanded === i ? "var(--crt-blue)" : "var(--text-tertiary)" }}>
                    {apiExpanded === i ? "▾" : "▸"}
                  </span>
                </button>
                {apiExpanded === i && renderExpanded(ep)}
              </div>
            ))}
          </div>
        ))}
      </div>
    );

    return (
      <div className="flex-1 flex flex-col overflow-hidden">
        {sub}
        {apiTabView === "logs" ? (
          renderLogsTab()
        ) : (
          <div className="flex-1 overflow-y-auto p-4">
            {schemaPane}
          </div>
        )}
      </div>
    );
  };

  const renderLogsTab = () => {
    const info = selectedModuleInfo;
    const hasApi = !!(info?.api_url || info?.has_api_dir);
    const hasApp = !!(info?.app_url || info?.has_app_dir);
    // Default to whichever side exists; prefer api.
    const active = moduleLogsOpen || (hasApi ? "api" : hasApp ? "app" : "api") as "api" | "app";
    const keys = Object.keys(moduleLogs);
    const matchKey = keys.find(k => k.toLowerCase().includes(active));
    const logContent = matchKey
      ? (moduleLogs[matchKey] || "(empty)")
      : keys.length > 0
        ? (moduleLogs[keys[0]] || "(empty)")
        : moduleLogsLoading ? "Loading..." : "(no logs found — start the service to begin capturing output)";

    return (
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Header */}
        <div
          className="px-4 py-2 border-b flex items-center justify-between gap-2 shrink-0"
          style={{ borderColor: "var(--border-color)", background: "color-mix(in srgb, var(--crt-amber) 3%, transparent)" }}
        >
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-[12px] leading-none" style={{ color: "var(--crt-amber)" }}>▤</span>
            <span className="text-[11px] font-bold uppercase tracking-[0.18em] shrink-0" style={{ color: "var(--crt-amber)" }}>
              {selectedModule || "claude"} LOGS
            </span>
            <span className="text-[10px] font-mono truncate" style={{ color: "var(--text-tertiary)" }}>
              /tmp/mod-{active}-{selectedModule || "claude"}.log
            </span>
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            {/* Source tabs */}
            {hasApi && (
              <button
                onClick={() => setModuleLogsOpen("api")}
                className="text-[10px] px-2.5 py-1 rounded-sm border uppercase font-bold transition-all hover:brightness-125"
                style={{
                  borderColor: active === "api" ? "color-mix(in srgb, var(--crt-blue) 50%, transparent)" : "color-mix(in srgb, var(--border-color) 50%, transparent)",
                  color: active === "api" ? "var(--crt-blue)" : "var(--text-tertiary)",
                  background: active === "api" ? "color-mix(in srgb, var(--crt-blue) 8%, transparent)" : "transparent",
                }}
              >
                API
              </button>
            )}
            {hasApp && (
              <button
                onClick={() => setModuleLogsOpen("app")}
                className="text-[10px] px-2.5 py-1 rounded-sm border uppercase font-bold transition-all hover:brightness-125"
                style={{
                  borderColor: active === "app" ? "color-mix(in srgb, var(--crt-amber) 50%, transparent)" : "color-mix(in srgb, var(--border-color) 50%, transparent)",
                  color: active === "app" ? "var(--crt-amber)" : "var(--text-tertiary)",
                  background: active === "app" ? "color-mix(in srgb, var(--crt-amber) 8%, transparent)" : "transparent",
                }}
              >
                APP
              </button>
            )}
            <div className="w-px h-4 mx-1" style={{ background: "var(--border-color)" }} />
            <button
              onClick={() => setModuleLogsAutoRefresh(!moduleLogsAutoRefresh)}
              className="text-[10px] px-2 py-1 rounded-sm border uppercase font-bold transition-all"
              style={{
                borderColor: moduleLogsAutoRefresh ? "color-mix(in srgb, var(--crt-green) 50%, transparent)" : "color-mix(in srgb, var(--border-color) 50%, transparent)",
                color: moduleLogsAutoRefresh ? "var(--crt-green)" : "var(--text-tertiary)",
                background: moduleLogsAutoRefresh ? "color-mix(in srgb, var(--crt-green) 8%, transparent)" : "transparent",
              }}
              title="Auto-refresh every 4s"
            >
              {moduleLogsAutoRefresh ? "● LIVE" : "AUTO"}
            </button>
            <button
              onClick={fetchModuleLogs}
              className="text-[10px] px-2 py-1 rounded-sm border uppercase font-bold transition-all"
              style={{ borderColor: "color-mix(in srgb, var(--border-color) 50%, transparent)", color: "var(--text-tertiary)" }}
            >
              {moduleLogsLoading ? "..." : "REFRESH"}
            </button>
          </div>
        </div>

        {/* Log content */}
        <pre
          className="flex-1 overflow-auto px-4 py-3 text-[12px] m-0"
          style={{
            color: "var(--text-secondary)",
            fontFamily: "var(--font-code, monospace)",
            whiteSpace: "pre-wrap",
            wordBreak: "break-all",
            lineHeight: "1.5",
            background: "var(--bg-primary)",
          }}
          ref={(el) => { if (el) el.scrollTop = el.scrollHeight; }}
        >
          {logContent}
        </pre>
      </div>
    );
  };

  // ── Dependency graph layout ─────────────────────────────────────────
  // Lay modules out by the `deps` they declare in config.json. Edge A→B means
  // "A depends on B"; dependents float above their dependencies. Modules with
  // no edge in either direction are pulled out into an "isolated" strip.
  const renderHubGraph = (
    mods: typeof moduleList,
    liveOf: (name: string) => boolean | null,
    openModule: (m: typeof moduleList[0]) => void,
  ) => {
    const present = new Set(mods.map((m) => m.name));
    const byName = new Map(mods.map((m) => [m.name, m] as const));
    // Resolved dependency edges (only to modules that actually exist here).
    const depsOf = (name: string): string[] =>
      (byName.get(name)?.deps || []).filter((d) => d !== name && present.has(d));
    // Who depends on a given module (reverse edges).
    const dependents = new Map<string, string[]>();
    for (const m of mods) {
      for (const d of depsOf(m.name)) {
        dependents.set(d, [...(dependents.get(d) || []), m.name]);
      }
    }
    const hasEdge = (name: string) =>
      depsOf(name).length > 0 || (dependents.get(name)?.length || 0) > 0;
    const connected = mods.filter((m) => hasEdge(m.name));
    const isolated = mods.filter((m) => !hasEdge(m.name));

    // Layer = longest dependency chain below a node. Leaves (no deps) sit at
    // depth 0; the deeper a node's dependency chain, the higher its layer.
    // Memoized with a cycle guard so a bad config can't loop forever.
    const depthCache = new Map<string, number>();
    const depthOf = (name: string, stack: Set<string>): number => {
      if (depthCache.has(name)) return depthCache.get(name)!;
      if (stack.has(name)) return 0; // cycle — break it
      stack.add(name);
      const ds = depsOf(name);
      const d = ds.length === 0 ? 0 : 1 + Math.max(...ds.map((x) => depthOf(x, stack)));
      stack.delete(name);
      depthCache.set(name, d);
      return d;
    };

    const NODE_W = 156, NODE_H = 46, GAP_X = 30, GAP_Y = 72, PAD = 16;
    const maxDepth = connected.reduce((mx, m) => Math.max(mx, depthOf(m.name, new Set())), 0);
    // Bucket connected nodes by row (dependents on top → row 0).
    const rows: string[][] = Array.from({ length: maxDepth + 1 }, () => []);
    for (const m of connected) {
      const row = maxDepth - depthOf(m.name, new Set());
      rows[row].push(m.name);
    }
    rows.forEach((r) => r.sort((a, b) => a.localeCompare(b)));
    const widest = rows.reduce((mx, r) => Math.max(mx, r.length), 1);
    const canvasW = widest * NODE_W + (widest - 1) * GAP_X + PAD * 2;
    const canvasH = rows.length * NODE_H + (rows.length - 1) * GAP_Y + PAD * 2;
    // Node center positions.
    const pos = new Map<string, { x: number; y: number; cx: number; cy: number }>();
    rows.forEach((r, ri) => {
      const rowW = r.length * NODE_W + (r.length - 1) * GAP_X;
      const startX = (canvasW - rowW) / 2;
      r.forEach((name, i) => {
        const x = startX + i * (NODE_W + GAP_X);
        const y = PAD + ri * (NODE_H + GAP_Y);
        pos.set(name, { x, y, cx: x + NODE_W / 2, cy: y + NODE_H / 2 });
      });
    });
    const edges: Array<{ from: string; to: string }> = [];
    for (const m of connected) for (const d of depsOf(m.name)) edges.push({ from: m.name, to: d });

    const NodeCard = ({ name, mini }: { name: string; mini?: boolean }) => {
      const m = byName.get(name)!;
      const live = liveOf(name);
      const isSel = name === selectedModule;
      const dot = live === true ? "var(--crt-green)" : live === false ? "#888" : "color-mix(in srgb, var(--crt-amber) 60%, transparent)";
      return (
        <button
          onClick={() => openModule(m)}
          className={`hub-card text-left flex flex-col justify-center group ${mini ? "px-2 py-1.5" : "px-3 py-2"}`}
          data-live={live === true ? "true" : undefined}
          data-selected={isSel ? "true" : undefined}
          style={{
            width: mini ? undefined : NODE_W,
            height: mini ? undefined : NODE_H,
            borderRadius: mini ? 10 : undefined,
            ["--hub-accent" as any]: hubHue(name),
          }}
          title={[m.description || name, m.updated_at ? `updated ${timeSince(m.updated_at)}` : null, m.cid ? `cid ${m.cid}` : null].filter(Boolean).join(" · ")}
        >
          <div className="flex items-center gap-1.5 min-w-0">
            <span className={`shrink-0 ${live === true ? "led-pulse" : ""}`} style={{ color: dot, fontSize: "9px" }}>●</span>
            <span className="font-code font-bold text-[12px] truncate" style={{ color: "var(--text-primary)" }}>{name}</span>
          </div>
          {!mini && (depsOf(name).length > 0 || (dependents.get(name)?.length || 0) > 0) && (
            <span className="text-[9px] font-code mt-0.5" style={{ color: "var(--text-tertiary)", opacity: 0.6 }}>
              {depsOf(name).length > 0 ? `↑ ${depsOf(name).join(", ")}` : `${dependents.get(name)!.length} dependent${dependents.get(name)!.length === 1 ? "" : "s"}`}
            </span>
          )}
        </button>
      );
    };

    return (
      <div className="flex flex-col gap-5">
        {connected.length > 0 ? (
          <div className="relative mx-auto" style={{ width: canvasW, height: canvasH }}>
            <svg width={canvasW} height={canvasH} className="absolute inset-0 pointer-events-none" style={{ overflow: "visible" }}>
              <defs>
                <marker id="hub-arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto">
                  <path d="M0,0 L7,3.5 L0,7 Z" fill="color-mix(in srgb, var(--crt-green) 55%, transparent)" />
                </marker>
              </defs>
              {edges.map((e, i) => {
                const a = pos.get(e.from), b = pos.get(e.to);
                if (!a || !b) return null;
                // From the dependent's bottom to the dependency's top.
                const x1 = a.cx, y1 = a.y + NODE_H, x2 = b.cx, y2 = b.y;
                const my = (y1 + y2) / 2;
                return (
                  <path
                    key={i}
                    d={`M ${x1} ${y1} C ${x1} ${my}, ${x2} ${my}, ${x2} ${y2}`}
                    fill="none"
                    stroke="color-mix(in srgb, var(--crt-green) 35%, transparent)"
                    strokeWidth={1.4}
                    markerEnd="url(#hub-arrow)"
                  />
                );
              })}
            </svg>
            {connected.map((m) => {
              const p = pos.get(m.name)!;
              return (
                <div key={m.name} className="absolute" style={{ left: p.x, top: p.y }}>
                  <NodeCard name={m.name} />
                </div>
              );
            })}
          </div>
        ) : (
          <div className="text-center text-[12px] font-code py-6" style={{ color: "var(--text-tertiary)", opacity: 0.6 }}>
            No dependency edges declared yet — add a <span className="text-crt-green">&quot;deps&quot;</span> array to a module&apos;s config.json.
          </div>
        )}

        {isolated.length > 0 && (
          <div className="border-t pt-4" style={{ borderColor: "var(--border-color)" }}>
            <div className="text-[11px] font-code uppercase tracking-[0.14em] mb-2.5" style={{ color: "var(--text-tertiary)" }}>
              ○ isolated · {isolated.length}
            </div>
            <div className="flex flex-wrap gap-2">
              {isolated.map((m) => <NodeCard key={m.name} name={m.name} mini />)}
            </div>
          </div>
        )}
      </div>
    );
  };

  // ── Module hub ──────────────────────────────────────────────────────
  // Landing grid of every real module. Click a card to load it for editing
  // (same select flow as the header dropdown), with a live online/offline dot.
  const renderHubView = () => {
    const openModule = (m: typeof moduleList[0]) => {
      resetModuleState(m);
      setSelectedModule(m.name);
      setSelectedModuleInfo(m);
      setWorkDir(m.path);
      fetchModuleConfig(m.name);
      setSidebarView(getBestTab(m));
    };

    const q = hubSearch.trim().toLowerCase();
    let mods = moduleList.filter(isRealModule);
    if (q) {
      mods = mods.filter(
        (m) => m.name.toLowerCase().includes(q) || (m.description || "").toLowerCase().includes(q),
      );
    }
    // Owner filter — distinct owners across all real modules. `ownerFilter`
    // holds the selected owner address (set to the connected wallet for the
    // "mine" pill), or null for "all". Reused from the header dropdown.
    const me = address && address !== "local" ? address.toLowerCase() : null;
    const hubOwners = [...new Set(
      moduleList.filter(isRealModule).map((m) => m.owner).filter(Boolean) as string[],
    )];
    // The owner string (with config's original casing) that belongs to the
    // connected wallet, so the "mine" pill matches m.owner exactly.
    const myOwner = me ? hubOwners.find((o) => o.toLowerCase() === me) ?? null : null;
    const otherOwners = hubOwners.filter((o) => o !== myOwner);
    if (ownerFilter) {
      mods = mods.filter((m) => m.owner === ownerFilter);
    }
    const liveOf = (name: string): boolean | null => {
      const st = moduleStatuses[name];
      if (!st) return null;
      if (st.app === true || st.api === true) return true;
      if (st.app === false || st.api === false) return false;
      return null;
    };
    // Status filter — counts are taken before the split so the pills show
    // both sides regardless of which one is selected.
    const runningCount = mods.filter((m) => liveOf(m.name) === true).length;
    const stoppedCount = mods.length - runningCount;
    if (hubRunFilter !== "all") {
      mods = mods.filter((m) => (liveOf(m.name) === true) === (hubRunFilter === "running"));
    }
    // Online modules first, then alphabetical — the things you're running
    // float to the top.
    mods = [...mods].sort((a, b) => {
      const la = liveOf(a.name) === true ? 0 : 1;
      const lb = liveOf(b.name) === true ? 0 : 1;
      return la - lb || a.name.localeCompare(b.name);
    });
    const onlineCount = runningCount;

    return (
      <div className="flex-1 flex flex-col overflow-hidden relative">
        {/* Slow-drifting aurora behind the card wall */}
        <div className="hub-aurora" aria-hidden />
        {/* Hub toolbar */}
        <div
          className="flex items-center gap-3 px-4 py-2.5 shrink-0 flex-wrap relative z-[1]"
          style={{ borderBottom: "1px solid var(--border-color)", background: "color-mix(in srgb, var(--crt-green) 4%, transparent)" }}
        >
          <span className="text-[14px] font-bold font-code text-crt-green flex items-center gap-1.5" style={{ letterSpacing: "0.04em" }}>
            <HubIcon size={14} /> HUB
          </span>
          <span className="text-[11px] font-code" style={{ color: "var(--text-tertiary)" }}>
            {mods.length} modules · <span className="text-crt-green">{onlineCount} online</span>
          </span>
          <button
            onClick={() => { setAddError(null); setAddOpen(true); }}
            className="flex items-center gap-1.5 text-[11px] font-code px-3 py-1.5 rounded-md transition-all font-bold"
            style={{
              color: "#fff",
              background: "linear-gradient(135deg, var(--accent-color), var(--accent-color-2, var(--crt-blue)))",
              boxShadow: "0 2px 12px color-mix(in srgb, var(--accent-color) 35%, transparent)",
              letterSpacing: "0.03em",
            }}
            onMouseEnter={(e) => { e.currentTarget.style.filter = "brightness(1.1)"; e.currentTarget.style.transform = "translateY(-1px)"; }}
            onMouseLeave={(e) => { e.currentTarget.style.filter = "none"; e.currentTarget.style.transform = "none"; }}
            title="Import a module from a GitHub repo or a snapshot CID"
          >
            <span style={{ fontSize: "13px", lineHeight: 1 }}>＋</span> ADD MODULE
          </button>
          <input
            type="text"
            value={hubSearch}
            onChange={(e) => setHubSearch(e.target.value)}
            placeholder="filter modules…"
            className="px-2.5 py-1 bg-transparent text-crt-green border border-crt-green/30 font-code outline-none text-[12px] rounded"
            style={{ minWidth: "180px" }}
          />
          {/* Grid / dependency-graph layout toggle */}
          <div className="flex items-center border rounded overflow-hidden" style={{ borderColor: "var(--border-color)" }}>
            {([["grid", "▦ grid"], ["graph", "◆ graph"]] as const).map(([mode, label]) => {
              const on = hubGraphMode === (mode === "graph");
              return (
                <button
                  key={mode}
                  onClick={() => setHubGraphMode(mode === "graph")}
                  className="text-[11px] font-code px-2.5 py-1 transition-colors"
                  style={{
                    color: on ? "var(--crt-green)" : "var(--text-tertiary)",
                    background: on ? "color-mix(in srgb, var(--crt-green) 12%, transparent)" : "transparent",
                  }}
                  title={mode === "graph" ? "Lay modules out by their config.json deps; modules with no edges float as isolated nodes." : "Card grid of every module."}
                >
                  {label}
                </button>
              );
            })}
          </div>
          {/* Running-status filter — driven by the same liveness probes as the
              card dots, so "running" means a green app or api probe. */}
          <div className="flex items-center gap-1.5">
            <span className="text-[11px] font-code uppercase" style={{ color: "var(--text-tertiary)", opacity: 0.6 }}>status:</span>
            {([["all", "all", null], ["running", "● running", runningCount], ["stopped", "○ stopped", stoppedCount]] as const).map(([key, label, count]) => (
              <button
                key={key}
                onClick={() => setHubRunFilter(key)}
                className={`text-[11px] px-2 py-0.5 border rounded font-code transition-colors ${hubRunFilter === key ? "border-crt-green/50 text-crt-green bg-crt-green/10" : "border-crt-green/15 text-crt-green/30 hover:border-crt-green/30"}`}
                title={key === "all" ? "Show every module" : key === "running" ? "Only modules with a live app or API" : "Only modules that aren't running"}
              >
                {label}{count !== null ? ` (${count})` : ""}
              </button>
            ))}
          </div>
          {/* Owner filter — "all" / "mine" + a single dropdown for everyone
              else, so the row stays on one line instead of a pill per owner. */}
          {hubOwners.length > 1 && (
            <div className="flex items-center gap-1.5">
              <span className="text-[11px] font-code uppercase" style={{ color: "var(--text-tertiary)", opacity: 0.6 }}>owner:</span>
              <button
                onClick={() => setOwnerFilter(null)}
                className={`text-[11px] px-2 py-0.5 border rounded font-code transition-colors ${!ownerFilter ? "border-crt-green/50 text-crt-green bg-crt-green/10" : "border-crt-green/15 text-crt-green/30 hover:border-crt-green/30"}`}
              >all</button>
              {myOwner && (
                <button
                  onClick={() => setOwnerFilter(ownerFilter === myOwner ? null : myOwner)}
                  className={`text-[11px] px-2 py-0.5 border rounded font-code transition-colors ${ownerFilter === myOwner ? "border-crt-green/50 text-crt-green bg-crt-green/10" : "border-crt-green/15 text-crt-green/30 hover:border-crt-green/30"}`}
                  title={myOwner}
                >mine</button>
              )}
              {otherOwners.length > 0 && (
                <select
                  value={ownerFilter && otherOwners.includes(ownerFilter) ? ownerFilter : ""}
                  onChange={(e) => setOwnerFilter(e.target.value || null)}
                  title="Filter by another owner"
                  className={`text-[11px] px-2 py-0.5 border rounded font-mono bg-transparent outline-none transition-colors ${ownerFilter && otherOwners.includes(ownerFilter) ? "border-crt-blue/50 text-crt-blue bg-crt-blue/10" : "border-crt-green/15 text-crt-green/40 hover:border-crt-green/30"}`}
                >
                  <option value="">others…</option>
                  {otherOwners.map((o) => (
                    <option key={o} value={o}>{o.slice(0, 6)}..{o.slice(-4)}</option>
                  ))}
                </select>
              )}
            </div>
          )}
          {/* Auto-restart-after-edit toggle */}
          <button
            onClick={() => setAutoRestartAfterEdit((v) => !v)}
            className="ml-auto flex items-center gap-1.5 text-[11px] font-code px-2.5 py-1 border rounded transition-colors"
            style={{
              borderColor: autoRestartAfterEdit ? "color-mix(in srgb, var(--crt-green) 50%, transparent)" : "var(--border-color)",
              color: autoRestartAfterEdit ? "var(--crt-green)" : "var(--text-tertiary)",
              background: autoRestartAfterEdit ? "color-mix(in srgb, var(--crt-green) 8%, transparent)" : "transparent",
            }}
            title="When on, a module is restarted via pm2 as soon as an edit job targeting it completes, so changes go live."
          >
            <span>{autoRestartAfterEdit ? "●" : "○"}</span>
            auto-restart after edit
          </button>
        </div>

        {/* Module grid */}
        <div className="flex-1 overflow-y-auto p-4 relative z-[1]">
          {mods.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full gap-2 text-center">
              <span className="text-[40px]" style={{ color: "var(--crt-green)", opacity: 0.15 }}>▦</span>
              <span className="text-[13px] font-code" style={{ color: "var(--text-tertiary)" }}>
                {moduleList.length ? "No modules match your filter." : "Loading modules…"}
              </span>
            </div>
          ) : hubGraphMode ? (
            renderHubGraph(mods, liveOf, openModule)
          ) : (
            <div
              className="grid gap-3"
              style={{ gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))" }}
            >
              <button
                onClick={() => { setAddError(null); setAddOpen(true); }}
                className="hub-add-card text-left p-3 flex flex-col items-center justify-center gap-1.5 group min-h-[104px]"
                title="Import from GitHub or a snapshot CID"
              >
                <span className="hub-add-card__plus text-[26px] leading-none">＋</span>
                <span className="text-[12px] font-code font-bold" style={{ color: "var(--accent-color)" }}>Add a module</span>
                <span className="text-[10px] font-code" style={{ color: "var(--text-tertiary)" }}>GitHub · CID</span>
              </button>
              {(() => {
                // Same name published by DIFFERENT owners → one card with an
                // owner picker, your copy as the default face. Same-owner
                // twins (core/x vs orbit/x) stay separate cards — there the
                // tree prefix, not the owner, is what tells them apart.
                const groups = new Map<string, typeof mods>();
                for (const mm of mods) {
                  const g = groups.get(mm.name);
                  if (g) g.push(mm); else groups.set(mm.name, [mm]);
                }
                const chosenFor = (name: string, group: typeof mods) =>
                  group.find((v) => v.path === hubOwnerPick[name]) ??
                  (myOwner ? group.find((v) => v.owner === myOwner) : undefined) ??
                  group[0];
                const cards: Array<{ m: typeof mods[number]; variants: typeof mods }> = [];
                const collapsed = new Set<string>();
                for (const mm of mods) {
                  if (collapsed.has(mm.name)) continue;
                  const group = groups.get(mm.name)!;
                  const distinctOwners = new Set(group.map((v) => (v.owner || "").toLowerCase()));
                  if (group.length > 1 && distinctOwners.size > 1) {
                    collapsed.add(mm.name);
                    cards.push({ m: chosenFor(mm.name, group), variants: group });
                  } else {
                    cards.push({ m: mm, variants: [mm] });
                  }
                }
                return cards.map(({ m, variants }, i) => {
                const live = liveOf(m.name);
                const isSel = m.name === selectedModule;
                const dotColor = live === true ? "var(--crt-green)" : live === false ? "#888" : "color-mix(in srgb, var(--crt-amber) 60%, transparent)";
                return (
                  <button
                    key={m.path}
                    onClick={() => openModule(m)}
                    className="hub-card text-left p-3 flex flex-col gap-2 group"
                    data-live={live === true ? "true" : undefined}
                    data-selected={isSel ? "true" : undefined}
                    style={{
                      ["--hub-accent" as any]: hubHue(m.name),
                      animationDelay: `${Math.min(i * 26, 390)}ms`,
                    }}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2 min-w-0">
                        <ModuleAvatar apiUrl={apiUrl} name={m.name} hasApp={!!(m.app_url || m.has_app_dir)} />
                        <span
                          className={`shrink-0 ${live === true ? "led-pulse" : ""}`}
                          style={{ color: dotColor, fontSize: "10px" }}
                          title={live === true ? "online" : live === false ? "offline" : "checking…"}
                        >●</span>
                        <span className="font-code font-bold text-[14px] truncate" style={{ color: "var(--text-primary)" }} title={m.display}>
                          {/* Same name can exist in both scanned trees (core/config
                              vs orbit/config) — the tree prefix is what tells the
                              twins apart. */}
                          <span style={{ opacity: 0.45, fontWeight: 400 }}>{m.category}/</span>{m.name}
                        </span>
                      </div>
                      <div className="flex items-center gap-1 shrink-0">
                        {/* span, not button: the whole card is already a <button> */}
                        <span
                          role="button"
                          tabIndex={0}
                          onClick={(e) => { e.stopPropagation(); shareModuleQr(m.name, m.cid); }}
                          onKeyDown={(e) => { if (e.key === "Enter") { e.stopPropagation(); shareModuleQr(m.name, m.cid); } }}
                          className="text-[8px] px-1 py-0.5 border border-crt-green/20 text-crt-green/40 rounded-sm font-code opacity-0 group-hover:opacity-100 hover:text-crt-green hover:border-crt-green/50 transition-all cursor-pointer"
                          title={`Share ${m.name} as a QR code (app link / import / CID)`}
                        >
                          ⛶ QR
                        </span>
                        {(m.app_url || m.has_app_dir) && (
                          <span className="text-[8px] px-1 py-0.5 border border-crt-blue/30 text-crt-blue/60 rounded-sm font-code">APP</span>
                        )}
                        {(m.api_url || m.has_api_dir || m.has_server_dir) && (
                          <span className="text-[8px] px-1 py-0.5 border border-crt-amber/30 text-crt-amber/60 rounded-sm font-code">API</span>
                        )}
                      </div>
                    </div>
                    <div className="text-[11px] leading-snug font-code line-clamp-2 min-h-[28px]" style={{ color: "var(--text-tertiary)" }}>
                      {m.description || <span style={{ opacity: 0.4 }}>{m.path.replace(/^.*\/mod\/orbit\//, "orbit/").replace(/^.*\/mod\/core\//, "core/")}</span>}
                    </div>
                    {/* Meta row: last-updated (newest file mtime) */}
                    {m.updated_at ? (
                      <div className="flex items-center gap-2 min-w-0">
                        <span
                          className="text-[9px] font-code shrink-0"
                          style={{ color: "var(--text-tertiary)", opacity: 0.7 }}
                          title={`Last updated ${formatDate(m.updated_at)}`}
                        >
                          ⟳ {timeSince(m.updated_at)}
                        </span>
                      </div>
                    ) : null}
                    {/* Owner row: owner (a picker when several owners publish
                        this name — yours is the default) + copyable CID. */}
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-1.5 min-w-0">
                        {variants.length > 1 ? (
                          <span
                            role="button"
                            tabIndex={0}
                            onClick={(e) => {
                              e.stopPropagation();
                              const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
                              setHubOwnerMenu((cur) => (cur?.name === m.name ? null : { name: m.name, x: r.left, y: r.bottom + 4 }));
                            }}
                            onKeyDown={(e) => { if (e.key === "Enter") { e.stopPropagation(); (e.currentTarget as HTMLElement).click(); } }}
                            className="text-[9px] font-mono px-1 py-0.5 border rounded-sm shrink-0 cursor-pointer transition-colors hover:border-crt-green/50"
                            style={{
                              borderColor: "var(--border-color)",
                              color: myOwner && m.owner === myOwner ? "var(--crt-green)" : "var(--text-tertiary)",
                            }}
                            title={`${variants.length} owners publish “${m.name}” — pick whose copy this card shows (yours is the default)\n${m.owner || ""}`}
                          >
                            {m.owner ? `${m.owner.slice(0, 6)}..${m.owner.slice(-4)}` : "no owner"}
                            {myOwner && m.owner === myOwner ? " · you" : ""} ▾
                          </span>
                        ) : m.owner ? (
                          <span className="text-[9px] font-mono shrink-0" style={{ color: "var(--text-tertiary)", opacity: 0.5 }} title={m.owner}>
                            {m.owner.slice(0, 6)}..{m.owner.slice(-4)}
                          </span>
                        ) : null}
                        {m.cid ? (
                          <span
                            role="button"
                            tabIndex={0}
                            onClick={(e) => { e.stopPropagation(); copyCid(`hub:${m.path}`, m.cid!); }}
                            onKeyDown={(e) => { if (e.key === "Enter") { e.stopPropagation(); copyCid(`hub:${m.path}`, m.cid!); } }}
                            className="text-[9px] font-code truncate cursor-pointer transition-opacity hover:opacity-100"
                            style={{ color: "var(--crt-purple, #c084fc)", opacity: copiedCid === `hub:${m.path}` ? 1 : 0.55 }}
                            title={`CID ${m.cid} — click to copy`}
                          >
                            {copiedCid === `hub:${m.path}` ? "✓ copied" : `⌬ ${m.cid.slice(0, 12)}…`}
                          </span>
                        ) : (
                          <span
                            className="text-[9px] font-code shrink-0"
                            style={{ color: "var(--text-tertiary)", opacity: 0.35 }}
                            title="No snapshot CID in the registry yet — snapshot the module to publish one"
                          >
                            no cid
                          </span>
                        )}
                      </div>
                      <span
                        className="text-[10px] font-code font-bold opacity-0 -translate-x-1 group-hover:opacity-100 group-hover:translate-x-0 transition-all"
                        style={{ color: "color-mix(in srgb, var(--hub-accent) 75%, var(--text-primary))" }}
                      >edit →</span>
                    </div>
                  </button>
                );
                });
              })()}
            </div>
          )}
          {renderHubOwnerMenu()}
        </div>
      </div>
    );
  };

  // ── Hub owner-picker popover ─────────────────────────────────────────
  // Shown when a hub card's name is published by several owners. Rendered
  // fixed at the chip's screen position (the card clips overflow), with a
  // transparent backdrop that closes it on any outside click.
  const renderHubOwnerMenu = () => {
    if (!hubOwnerMenu) return null;
    const group = moduleList.filter(isRealModule).filter((v) => v.name === hubOwnerMenu.name);
    if (group.length === 0) return null;
    const me = address && address !== "local" ? address.toLowerCase() : null;
    const current =
      group.find((v) => v.path === hubOwnerPick[hubOwnerMenu.name]) ??
      (me ? group.find((v) => (v.owner || "").toLowerCase() === me) : undefined) ??
      group[0];
    return (
      <>
        <div className="fixed inset-0 z-[120]" onClick={() => setHubOwnerMenu(null)} />
        <div
          className="fixed z-[121] rounded-md border overflow-hidden py-0.5"
          style={{
            left: hubOwnerMenu.x,
            top: hubOwnerMenu.y,
            minWidth: 190,
            background: "var(--bg-primary)",
            borderColor: "var(--border-color-strong, var(--border-color))",
            boxShadow: "0 10px 36px rgba(0,0,0,0.45)",
          }}
        >
          {group.map((v) => {
            const you = !!(me && (v.owner || "").toLowerCase() === me);
            const on = v.path === current.path;
            return (
              <div
                key={v.path}
                onClick={() => {
                  setHubOwnerPick((p) => ({ ...p, [hubOwnerMenu.name]: v.path }));
                  setHubOwnerMenu(null);
                }}
                className="px-3 py-1.5 text-[11px] font-mono cursor-pointer transition-colors hover:bg-crt-green/10 flex items-center gap-2"
                style={{ color: on ? "var(--crt-green)" : "var(--text-secondary)" }}
                title={`${v.owner || "no owner"}\n${v.path}`}
              >
                <span className="w-2 shrink-0">{on ? "▸" : ""}</span>
                <span className="truncate">
                  {v.owner ? `${v.owner.slice(0, 6)}..${v.owner.slice(-4)}` : "no owner"}
                  {you ? " · you" : ""}
                </span>
                <span className="font-code shrink-0" style={{ opacity: 0.45 }}>{v.category}/</span>
              </div>
            );
          })}
        </div>
      </>
    );
  };

  // ── Share-QR overlay ─────────────────────────────────────────────────
  // The anti-copy-paste path: any hash / CID / module link becomes a
  // scannable code, rendered locally (qrSvg — the payload never leaves the
  // browser). Pills flip between payload forms (App / Import / CID / …);
  // the payload row is one tap to copy for when a camera isn't handy.
  const renderQrShareModal = () => {
    if (!qrShare) return null;
    const opt = qrShare.options[Math.min(qrShareIdx, qrShare.options.length - 1)];
    let svg: string | null = null;
    try {
      svg = qrSvg(opt.value, 220);
    } catch {
      svg = null; // payload too large for a QR — copy row still works
    }
    return (
      <div
        className="fixed inset-0 z-[300] flex items-center justify-center p-4"
        style={{ background: "rgba(7, 7, 13, 0.65)", backdropFilter: "blur(8px)", WebkitBackdropFilter: "blur(8px)" }}
        onClick={() => setQrShare(null)}
      >
        <div
          className="rounded-2xl w-full max-w-sm flex flex-col overflow-hidden"
          onClick={(e) => e.stopPropagation()}
          style={{
            background: "color-mix(in srgb, var(--glass-bg, var(--bg-primary)) 92%, transparent)",
            border: "1px solid var(--border-color-strong)",
            boxShadow: "0 18px 60px rgba(0,0,0,0.55)",
            backdropFilter: "blur(18px) saturate(150%)",
            WebkitBackdropFilter: "blur(18px) saturate(150%)",
          }}
        >
          <div className="flex items-center justify-between px-4 py-3" style={{ borderBottom: "1px solid var(--border-color)" }}>
            <span className="text-[12px] uppercase font-bold tracking-[0.14em] truncate" style={{ color: "var(--text-primary)" }}>
              {qrShare.title}
            </span>
            <button
              onClick={() => setQrShare(null)}
              className="text-[13px] px-1.5 transition-colors"
              style={{ color: "var(--text-tertiary)" }}
              title="Close"
            >
              ✕
            </button>
          </div>
          <div className="flex flex-col items-center gap-3 p-4">
            {qrShare.options.length > 1 && (
              <div className="flex items-center gap-1.5 flex-wrap justify-center">
                {qrShare.options.map((o, i) => {
                  const on = i === Math.min(qrShareIdx, qrShare.options.length - 1);
                  return (
                    <button
                      key={o.label}
                      onClick={() => { setQrShareIdx(i); setQrShareCopied(false); }}
                      className="text-[10px] px-2.5 py-1 rounded uppercase font-bold tracking-wider transition-all"
                      style={{
                        color: on ? "var(--bg-primary)" : "var(--text-secondary)",
                        background: on ? "var(--accent-color, #cc785c)" : "var(--bg-secondary)",
                        border: `1px solid ${on ? "var(--accent-color, #cc785c)" : "var(--border-color)"}`,
                      }}
                    >
                      {o.label}
                    </button>
                  );
                })}
              </div>
            )}
            {svg ? (
              <div className="rounded-lg p-2 bg-white" dangerouslySetInnerHTML={{ __html: svg }} />
            ) : (
              <div
                className="rounded-lg px-4 py-6 text-[11px] text-center"
                style={{ color: "var(--text-tertiary)", background: "var(--bg-secondary)", border: "1px solid var(--border-color)" }}
              >
                Too long for a QR code — use the copy button below.
              </div>
            )}
            <button
              onClick={() => {
                navigator.clipboard?.writeText(opt.value).catch(() => {});
                setQrShareCopied(true);
                setTimeout(() => setQrShareCopied(false), 1400);
              }}
              className="w-full text-[10px] px-2 py-1.5 rounded font-mono break-all text-left transition-all"
              style={{
                color: qrShareCopied ? "var(--crt-green)" : "var(--text-secondary)",
                background: "var(--bg-primary)",
                border: `1px solid ${qrShareCopied ? "color-mix(in srgb, var(--crt-green) 40%, transparent)" : "var(--border-color)"}`,
              }}
              title="Copy to clipboard"
            >
              {qrShareCopied ? "✓ copied" : opt.value}
            </button>
            {opt.hint && (
              <div className="text-[10px] text-center leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
                {opt.hint}
              </div>
            )}
          </div>
        </div>
      </div>
    );
  };

  // ── Add-Module modal ─────────────────────────────────────────────────
  // A sleek glass sheet for importing a fresh module straight from a
  // GitHub repo (shallow clone) or a snapshot CID (shared blob store).
  const renderAddModuleModal = () => {
    const sources: Array<{ k: "github" | "cid"; label: string; glyph: string; hint: string }> = [
      { k: "github", label: "GitHub", glyph: "⎇", hint: "Clone a public git repo" },
      { k: "cid", label: "CID / Snapshot", glyph: "◈", hint: "Restore from a snapshot CID" },
    ];
    const derived = addSource === "github" ? deriveNameFromUrl(addUrl) : "";
    return (
      <div
        className="fixed inset-0 z-[200] flex items-center justify-center p-4"
        style={{ background: "rgba(0,0,0,0.55)", backdropFilter: "blur(6px)", WebkitBackdropFilter: "blur(6px)" }}
        onClick={() => !addBusy && setAddOpen(false)}
      >
        <div
          onClick={(e) => e.stopPropagation()}
          className="w-full max-w-[460px] rounded-2xl overflow-hidden flex flex-col"
          style={{
            background: "var(--glass-bg-strong, var(--bg-secondary))",
            border: "1px solid var(--glass-border-strong, var(--border-color-strong))",
            boxShadow: "var(--shadow-lg)",
            backdropFilter: "blur(24px) saturate(160%)",
            WebkitBackdropFilter: "blur(24px) saturate(160%)",
          }}
        >
          {/* Header */}
          <div
            className="flex items-center justify-between px-5 py-3.5"
            style={{ borderBottom: "1px solid var(--border-color)", background: "color-mix(in srgb, var(--accent-color) 6%, transparent)" }}
          >
            <div className="flex items-center gap-2.5">
              <span className="text-[16px]" style={{ color: "var(--accent-color)" }}>＋</span>
              <span className="text-[14px] font-bold font-code" style={{ color: "var(--text-primary)", letterSpacing: "0.02em" }}>Add a module</span>
            </div>
            <button
              onClick={() => !addBusy && setAddOpen(false)}
              className="text-[18px] leading-none transition-opacity hover:opacity-100"
              style={{ color: "var(--text-tertiary)", opacity: 0.7 }}
              title="Close"
            >×</button>
          </div>

          <div className="p-5 flex flex-col gap-4">
            {/* Source selector */}
            <div className="grid grid-cols-2 gap-2">
              {sources.map((s) => {
                const on = addSource === s.k;
                return (
                  <button
                    key={s.k}
                    onClick={() => { setAddSource(s.k); setAddError(null); }}
                    className="flex flex-col items-start gap-0.5 px-3 py-2.5 rounded-lg transition-all text-left"
                    style={{
                      border: `1px solid ${on ? "var(--accent-color)" : "var(--border-color)"}`,
                      background: on ? "color-mix(in srgb, var(--accent-color) 10%, transparent)" : "transparent",
                    }}
                  >
                    <span className="text-[12px] font-bold font-code flex items-center gap-1.5" style={{ color: on ? "var(--accent-color)" : "var(--text-secondary)" }}>
                      <span style={{ fontSize: "13px" }}>{s.glyph}</span> {s.label}
                    </span>
                    <span className="text-[10px] font-code" style={{ color: "var(--text-tertiary)" }}>{s.hint}</span>
                  </button>
                );
              })}
            </div>

            {/* Source-specific input */}
            {addSource === "github" ? (
              <label className="flex flex-col gap-1.5">
                <span className="text-[10px] uppercase tracking-wider font-code" style={{ color: "var(--text-tertiary)" }}>Repository URL</span>
                <input
                  autoFocus
                  type="text"
                  value={addUrl}
                  onChange={(e) => setAddUrl(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && !addBusy) submitAddModule(); }}
                  placeholder="https://github.com/owner/repo"
                  className="px-3 py-2 rounded-lg bg-transparent font-code outline-none text-[12px]"
                  style={{ border: "1px solid var(--border-color)", color: "var(--text-primary)" }}
                />
              </label>
            ) : (
              <label className="flex flex-col gap-1.5">
                <span className="text-[10px] uppercase tracking-wider font-code" style={{ color: "var(--text-tertiary)" }}>Snapshot CID</span>
                <input
                  autoFocus
                  type="text"
                  value={addCid}
                  onChange={(e) => setAddCid(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && !addBusy) submitAddModule(); }}
                  placeholder="content id of a module snapshot"
                  className="px-3 py-2 rounded-lg bg-transparent font-mono outline-none text-[12px]"
                  style={{ border: "1px solid var(--border-color)", color: "var(--text-primary)" }}
                />
              </label>
            )}

            {/* Module name */}
            <label className="flex flex-col gap-1.5">
              <span className="text-[10px] uppercase tracking-wider font-code" style={{ color: "var(--text-tertiary)" }}>
                Module name {derived && !addName.trim() && <span style={{ opacity: 0.7 }}>· defaults to “{derived}”</span>}
              </span>
              <input
                type="text"
                value={addName}
                onChange={(e) => setAddName(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && !addBusy) submitAddModule(); }}
                placeholder={derived || "my-module"}
                className="px-3 py-2 rounded-lg bg-transparent font-code outline-none text-[12px]"
                style={{ border: "1px solid var(--border-color)", color: "var(--text-primary)" }}
              />
            </label>

            {!isOwner && (
              <div className="text-[10px] font-code leading-snug px-2.5 py-1.5 rounded" style={{ color: "var(--text-tertiary)", background: "color-mix(in srgb, var(--crt-amber) 8%, transparent)", border: "1px solid color-mix(in srgb, var(--crt-amber) 20%, transparent)" }}>
                As a non-owner your module is imported into your personal <span className="font-bold">portal/</span> namespace.
              </div>
            )}

            {addError && (
              <div className="text-[11px] font-code leading-snug px-2.5 py-2 rounded" style={{ color: "var(--crt-red)", background: "color-mix(in srgb, var(--crt-red) 8%, transparent)", border: "1px solid color-mix(in srgb, var(--crt-red) 25%, transparent)" }}>
                {addError}
              </div>
            )}

            {/* Actions */}
            <div className="flex items-center justify-end gap-2 pt-1">
              <button
                onClick={() => !addBusy && setAddOpen(false)}
                className="text-[12px] font-code px-3.5 py-2 rounded-lg transition-colors"
                style={{ color: "var(--text-secondary)", border: "1px solid var(--border-color)" }}
              >Cancel</button>
              <button
                onClick={() => submitAddModule()}
                disabled={addBusy}
                className="text-[12px] font-code font-bold px-4 py-2 rounded-lg transition-all flex items-center gap-2"
                style={{
                  color: "#fff",
                  background: addBusy ? "var(--text-tertiary)" : "linear-gradient(135deg, var(--accent-color), var(--accent-color-2, var(--crt-blue)))",
                  boxShadow: addBusy ? "none" : "0 2px 12px color-mix(in srgb, var(--accent-color) 35%, transparent)",
                  opacity: addBusy ? 0.7 : 1,
                  cursor: addBusy ? "wait" : "pointer",
                }}
              >
                {addBusy ? (<><span className="led-pulse">●</span> Importing…</>) : (<>＋ Import</>)}
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  };

  // ── Share Edit Access card — owner mints a QR invite that confers
  //    TEMPORARY edit rights (default 1h), optionally protected by a
  //    locally-generated key shared out of band. The id rides the QR;
  //    the key never does. Backed by GET/POST/DELETE /grants. Rendered
  //    in BOTH the module OVERVIEW tab and the ACCOUNT sidebar; owner-
  //    only: it renders nothing for anyone else.
  const renderShareAccessCard = () => {
    if (!isOwner) return null;
                const TTL_OPTS = [
                  { l: "15m", v: 900 },
                  { l: "1h", v: 3600 },
                  { l: "8h", v: 28800 },
                  { l: "24h", v: 86400 },
                  { l: "7d", v: 604800 },
                ];
                const fmtLeft = (exp: number) => {
                  let s = Math.max(0, exp - nowSec);
                  if (s >= 86400) return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
                  if (s >= 3600) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
                  if (s >= 60) return `${Math.floor(s / 60)}m ${s % 60}s`;
                  return `${s}s`;
                };
                const accent = "var(--accent-color, #cc785c)";
                return (
                <div className="section-card" data-accent="green" style={{ ["--card-accent" as any]: accent }}>
                  <span className="section-card__bar" style={{ background: accent }} />
                  <div className="section-card__head">
                    <div className="section-card__title min-w-0">
                      <span className="section-card__glyph" style={{ color: accent }}>⧉</span>
                      Share Edit Access
                      <span
                        className="text-[10px] font-mono px-2 py-0.5 rounded-full ml-1"
                        style={{
                          color: accent,
                          background: `color-mix(in srgb, ${accent} 12%, transparent)`,
                          border: `1px solid color-mix(in srgb, ${accent} 28%, transparent)`,
                        }}
                      >
                        {grants.length}
                      </span>
                    </div>
                    <span className="text-[9px] uppercase tracking-wider" style={{ color: "var(--text-tertiary)" }}>
                      QR invite
                    </span>
                  </div>
                  <div className="pl-5 pr-4 py-3 flex flex-col gap-3">
                    <div className="text-[10.5px] leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
                      Mint a QR that grants <span style={{ color: "var(--text-secondary)" }}>temporary access</span> to
                      anyone who scans it — no wallet needed (guests get an anonymous pass; wallet sign-in ties access to
                      their address). Access ends automatically when the timer runs out. Add a key for a second factor you
                      share separately (a leaked QR alone won&apos;t work).
                    </div>

                    {/* Duration picker */}
                    <div className="flex flex-col gap-1.5">
                      <span className="text-[9px] uppercase tracking-[0.16em]" style={{ color: "var(--text-tertiary)" }}>Duration</span>
                      <div className="flex items-center gap-1.5 flex-wrap">
                        {TTL_OPTS.map((o) => {
                          const on = grantTtl === o.v;
                          return (
                            <button
                              key={o.v}
                              onClick={() => setGrantTtl(o.v)}
                              className="text-[11px] px-2.5 py-1 rounded font-mono transition-all"
                              style={{
                                color: on ? "var(--bg-primary)" : "var(--text-secondary)",
                                background: on ? accent : "var(--bg-secondary)",
                                border: `1px solid ${on ? accent : "var(--border-color)"}`,
                              }}
                            >
                              {o.l}
                            </button>
                          );
                        })}
                      </div>
                    </div>

                    {/* Optional key (second factor) */}
                    <div className="flex flex-col gap-1.5">
                      <span className="text-[9px] uppercase tracking-[0.16em]" style={{ color: "var(--text-tertiary)" }}>
                        Key — optional second factor
                      </span>
                      <div className="flex items-center gap-1.5">
                        <input
                          type="text"
                          value={grantKey}
                          onChange={(e) => setGrantKey(e.target.value)}
                          placeholder="none — open to anyone with the QR"
                          className="flex-1 px-2 py-1.5 text-[11px] font-mono rounded outline-none"
                          style={{ color: "var(--text-primary)", background: "var(--bg-secondary)", border: "1px solid var(--border-color)" }}
                        />
                        <button
                          onClick={genKey}
                          className="text-[10px] px-2.5 py-1.5 rounded uppercase font-bold tracking-wider transition-all"
                          style={{ color: accent, background: `color-mix(in srgb, ${accent} 10%, transparent)`, border: `1px solid color-mix(in srgb, ${accent} 35%, transparent)` }}
                          title="Generate a random key locally"
                        >
                          Generate
                        </button>
                      </div>
                    </div>

                    {/* Optional label */}
                    <input
                      type="text"
                      value={grantLabel}
                      onChange={(e) => setGrantLabel(e.target.value)}
                      placeholder="label (optional) — e.g. “Sam, design review”"
                      className="px-2 py-1.5 text-[11px] rounded outline-none"
                      style={{ color: "var(--text-primary)", background: "var(--bg-secondary)", border: "1px solid var(--border-color)" }}
                    />

                    <button
                      onClick={createGrant}
                      disabled={grantBusy}
                      className="text-[11px] px-3 py-2 rounded uppercase font-bold tracking-wider transition-all disabled:opacity-40"
                      style={{ color: "var(--bg-primary)", background: accent, border: `1px solid ${accent}` }}
                    >
                      {grantBusy ? "…" : "Create Invite QR"}
                    </button>
                    {grantError && (
                      <div className="text-[10px]" style={{ color: "var(--crt-red)" }}>{grantError}</div>
                    )}

                    {/* Freshly-minted invite: QR + shareables */}
                    {activeGrant && (
                      <div
                        className="rounded-lg p-3 flex flex-col items-center gap-2.5"
                        style={{ background: "var(--bg-secondary)", border: `1px solid color-mix(in srgb, ${accent} 30%, transparent)` }}
                      >
                        <div
                          className="rounded-lg p-2 bg-white"
                          dangerouslySetInnerHTML={{ __html: qrSvg(grantInviteUrl(activeGrant.id), 200) }}
                        />
                        <div className="text-[10px] font-mono" style={{ color: "var(--text-tertiary)" }}>
                          expires in <span style={{ color: accent }}>{fmtLeft(activeGrant.exp)}</span>
                        </div>
                        <button
                          onClick={() => copyGrantBit("link", grantInviteUrl(activeGrant.id))}
                          className="w-full text-[10px] px-2 py-1.5 rounded font-mono truncate transition-all"
                          style={{ color: "var(--text-secondary)", background: "var(--bg-primary)", border: "1px solid var(--border-color)" }}
                          title="Copy invite link"
                        >
                          {grantCopied === "link" ? "✓ copied link" : grantInviteUrl(activeGrant.id)}
                        </button>
                        {activeGrant.key && (
                          <button
                            onClick={() => copyGrantBit("key", activeGrant.key!)}
                            className="w-full text-[10px] px-2 py-1.5 rounded font-mono transition-all flex items-center justify-between gap-2"
                            style={{ color: accent, background: `color-mix(in srgb, ${accent} 8%, transparent)`, border: `1px solid color-mix(in srgb, ${accent} 30%, transparent)` }}
                            title="Copy key — share this SEPARATELY from the QR"
                          >
                            <span className="uppercase tracking-wider text-[8px] font-bold">key · share separately</span>
                            <span className="truncate">{grantCopied === "key" ? "✓ copied" : activeGrant.key}</span>
                          </button>
                        )}
                        <button
                          onClick={() => setActiveGrant(null)}
                          className="text-[9px] uppercase tracking-wider"
                          style={{ color: "var(--text-tertiary)" }}
                        >
                          done
                        </button>
                      </div>
                    )}

                    {/* Active invites */}
                    {grants.length > 0 && (
                      <div className="flex flex-col gap-1.5 mt-1">
                        <span className="text-[9px] uppercase tracking-[0.16em]" style={{ color: "var(--text-tertiary)" }}>
                          Active invites
                        </span>
                        {grants.map((g) => {
                          const used = grantRedemptions.filter((r) => r.grant === g.id).length;
                          return (
                            <div
                              key={g.id}
                              className="flex items-center gap-2 px-2 py-1.5 rounded"
                              style={{ background: "var(--bg-secondary)", border: "1px solid var(--border-color)" }}
                            >
                              <span className="inline-block w-1.5 h-1.5 rounded-full shrink-0" style={{ background: accent }} />
                              <button
                                onClick={() => setActiveGrant({ id: g.id, exp: g.exp, key_required: g.key_required })}
                                className="flex-1 min-w-0 text-left"
                                title="Show QR"
                              >
                                <div className="font-mono text-[11px] truncate" style={{ color: "var(--text-secondary)" }}>
                                  {g.label || `${g.id.slice(0, 8)}…`}
                                </div>
                                <div className="text-[9px] flex items-center gap-1.5" style={{ color: "var(--text-tertiary)" }}>
                                  <span>{fmtLeft(g.exp)} left</span>
                                  {g.key_required && <span style={{ color: accent }}>· 🔑 key</span>}
                                  {used > 0 && <span>· {used} active</span>}
                                </div>
                              </button>
                              <button
                                onClick={() => revokeGrant(g.id)}
                                disabled={grantBusy}
                                className="text-[10px] px-1.5 py-0.5 rounded shrink-0 transition-all"
                                style={{ color: "var(--crt-red)", background: "color-mix(in srgb, var(--crt-red) 6%, transparent)", border: "1px solid color-mix(in srgb, var(--crt-red) 22%, transparent)" }}
                                title="Revoke this invite (cuts every session it opened)"
                              >
                                ✕
                              </button>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                </div>
    );
  };

  const renderProfileTab = () => {
    const cfg = effectiveConfig;
    const info = selectedModuleInfo;

    return (
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Profile Content */}
        <div className="flex-1 overflow-y-auto">
          {sidebarView === "overview" && (
            <div className="p-4 flex flex-col gap-4">

              {/* Module Info — definition list on top, big stats grid on
                  bottom. Section header gets an accent bar + glyph so it
                  reads at a glance instead of fading into the muted tone. */}
              <div className="section-card">
                <span className="section-card__bar" />
                <div className="section-card__head">
                  <div className="section-card__title">
                    <span className="section-card__glyph">◇</span>
                    Module
                  </div>
                </div>
                <div className="pl-5 pr-4 py-3 flex flex-col gap-2">
                  {selectedModule && (
                    <div className="flex items-center gap-3">
                      <span className="shrink-0 w-16 text-[10px] uppercase tracking-wider" style={{ color: "var(--text-tertiary)" }}>Name</span>
                      <span className="flex items-center gap-1.5 text-[13px] font-bold" style={{ color: "var(--text-primary)" }}>
                        <ClaudeMark size={14} />
                        {cfg?.title || prettyModName(selectedModule)}
                      </span>
                    </div>
                  )}
                  {info?.path && (
                    <div className="flex items-start gap-3">
                      <span className="shrink-0 w-16 text-[10px] uppercase tracking-wider" style={{ color: "var(--text-tertiary)" }}>Path</span>
                      <span className="font-mono text-[12px] break-all" style={{ color: "var(--text-secondary)" }}>{info.path.replace(/^\/Users\/[^/]+\//, "~/")}</span>
                    </div>
                  )}
                  {cfg?.owner && (
                    <div className="flex items-center gap-3">
                      <span className="shrink-0 w-16 text-[10px] uppercase tracking-wider" style={{ color: "var(--text-tertiary)" }}>Owner</span>
                      <button
                        onClick={() => navigator.clipboard?.writeText(cfg.owner).catch(() => {})}
                        title={`Click to copy\n${cfg.owner}`}
                        className="font-mono text-[12px] transition-colors cursor-pointer"
                        style={{ color: "var(--crt-green)", background: "transparent", border: "none", padding: 0 }}
                      >
                        {cfg.owner.slice(0, 6)}…{cfg.owner.slice(-4)}
                      </button>
                    </div>
                  )}
                  {info?.category && (
                    <div className="flex items-center gap-3">
                      <span className="shrink-0 w-16 text-[10px] uppercase tracking-wider" style={{ color: "var(--text-tertiary)" }}>Cat</span>
                      <span
                        className="text-[10px] px-1.5 py-0.5 rounded uppercase tracking-wider font-bold"
                        style={{
                          color: "var(--accent-color)",
                          background: "color-mix(in srgb, var(--accent-color) 10%, transparent)",
                          border: "1px solid color-mix(in srgb, var(--accent-color) 22%, transparent)",
                        }}
                      >
                        {info.category}
                      </span>
                    </div>
                  )}
                  {info?.cid && (
                    <div className="flex items-start gap-3">
                      <span className="shrink-0 w-16 text-[10px] uppercase tracking-wider" style={{ color: "var(--text-tertiary)" }}>CID</span>
                      <span className="font-mono text-[11px] break-all" style={{ color: "var(--text-tertiary)" }}>{info.cid}</span>
                    </div>
                  )}
                </div>
                {/* Stat grid — punchier than the inline row. */}
                <div
                  className="grid grid-cols-4 gap-px"
                  style={{
                    background: "var(--border-color)",
                    borderTop: "1px solid var(--border-color)",
                  }}
                >
                  {[
                    { label: "fns", value: cfg?.fns?.length ?? 0, color: "var(--crt-amber)" },
                    { label: "endpoints", value: cfg?.endpoints ? Object.keys(cfg.endpoints).length : 0, color: "var(--crt-amber)" },
                    {
                      label: "app",
                      value: info?.has_app_dir ? "yes" : "no",
                      color: info?.has_app_dir ? "var(--crt-green)" : "var(--text-tertiary)",
                    },
                    {
                      label: "api",
                      value: (info?.has_api_dir || info?.has_server_dir) ? "yes" : "no",
                      color: (info?.has_api_dir || info?.has_server_dir) ? "var(--crt-green)" : "var(--text-tertiary)",
                    },
                  ].map((s) => (
                    <div key={s.label} className="flex flex-col items-center justify-center py-2.5" style={{ background: "var(--bg-primary)" }}>
                      <span className="text-[18px] font-bold leading-none" style={{ color: s.color, letterSpacing: "-0.02em" }}>{s.value}</span>
                      <span className="text-[9px] uppercase mt-1 tracking-wider" style={{ color: "var(--text-tertiary)" }}>{s.label}</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Whitelist — owner-managed list of EOAs trusted to EDIT the
                  orbit (sign in + owner-level edit access: host files,
                  unsandboxed jobs, core/+orbit/ writes). Owner-only powers
                  (whitelist mgmt, kill, delete/rename) stay with the owner.
                  Backed by GET/POST/DELETE /whitelist on the Rust API;
                  non-owners see read-only rows + a hint.
                  Owner gets a click-to-remove X per row and a 0x… input
                  with ADD. Always-visible: a new caller checking whether
                  they were granted access shouldn't need owner perms to
                  read the list. */}
              <div className="section-card" data-accent="green">
                <span className="section-card__bar" />
                <div className="section-card__head">
                  <div className="section-card__title min-w-0">
                    <span className="section-card__glyph">◐</span>
                    Whitelist
                    <span
                      className="text-[10px] font-mono px-2 py-0.5 rounded-full ml-1"
                      style={{
                        color: "var(--crt-green)",
                        background: "color-mix(in srgb, var(--crt-green) 10%, transparent)",
                        border: "1px solid color-mix(in srgb, var(--crt-green) 25%, transparent)",
                      }}
                    >
                      {whitelist.length}
                    </span>
                  </div>
                  <span className="text-[9px] uppercase tracking-wider" style={{ color: "var(--text-tertiary)" }}>
                    {isOwner ? "owner edit" : "read-only"}
                  </span>
                </div>
                <div className="pl-5 pr-4 py-3 flex flex-col gap-2">
                  {whitelist.length === 0 ? (
                    <div className="text-[11px] py-2 text-center" style={{ color: "var(--text-tertiary)" }}>
                      No editors whitelisted yet. Whitelisted addresses get owner-level edit access to the orbit. The configured owner is always allowed.
                    </div>
                  ) : (
                    whitelist.map((addr) => {
                      const isCaller = address && address.toLowerCase() === addr.toLowerCase();
                      return (
                        <div
                          key={addr}
                          className="flex items-center gap-2 px-2 py-1.5 rounded"
                          style={{
                            background: isCaller ? "color-mix(in srgb, var(--crt-green) 6%, transparent)" : "var(--bg-secondary)",
                            border: `1px solid ${isCaller ? "color-mix(in srgb, var(--crt-green) 30%, transparent)" : "var(--border-color)"}`,
                          }}
                        >
                          <span
                            className="inline-block w-1.5 h-1.5 rounded-full shrink-0"
                            style={{ background: "var(--crt-green)", boxShadow: isCaller ? "0 0 4px var(--crt-green)" : "none" }}
                          />
                          <button
                            onClick={() => navigator.clipboard?.writeText(addr).catch(() => {})}
                            className="font-mono text-[11px] truncate flex-1 text-left transition-colors cursor-pointer"
                            style={{ color: "var(--text-secondary)", background: "transparent", border: "none", padding: 0 }}
                            title={`${addr} — click to copy`}
                          >
                            {addr}
                          </button>
                          {isCaller && (
                            <span
                              className="text-[8px] font-bold uppercase tracking-widest px-1 py-[1px] rounded shrink-0"
                              style={{ background: "var(--crt-green)", color: "var(--bg-primary)" }}
                            >
                              YOU
                            </span>
                          )}
                          {isOwner && (
                            <button
                              onClick={() => removeFromWhitelist(addr)}
                              disabled={whitelistBusy}
                              className="text-[10px] px-1.5 py-0.5 rounded shrink-0 transition-all"
                              style={{
                                color: "var(--crt-red)",
                                background: "color-mix(in srgb, var(--crt-red) 6%, transparent)",
                                border: "1px solid color-mix(in srgb, var(--crt-red) 22%, transparent)",
                              }}
                              title={`Remove ${addr.slice(0, 6)}…${addr.slice(-4)} from the whitelist`}
                            >
                              ✕
                            </button>
                          )}
                        </div>
                      );
                    })
                  )}
                  {isOwner && (
                    <div className="flex items-center gap-1.5 mt-1">
                      <input
                        type="text"
                        value={whitelistInput}
                        onChange={(e) => { setWhitelistInput(e.target.value); setWhitelistError(null); }}
                        onKeyDown={(e) => { if (e.key === "Enter" && whitelistInput.trim()) addToWhitelist(whitelistInput); }}
                        placeholder="0x… address to whitelist"
                        disabled={whitelistBusy}
                        className="flex-1 px-2 py-1.5 text-[11px] font-mono rounded outline-none"
                        style={{
                          color: "var(--text-primary)",
                          background: "var(--bg-secondary)",
                          border: `1px solid ${whitelistError ? "color-mix(in srgb, var(--crt-red) 45%, transparent)" : "var(--border-color)"}`,
                        }}
                      />
                      <button
                        onClick={() => addToWhitelist(whitelistInput)}
                        disabled={whitelistBusy || !whitelistInput.trim()}
                        className="text-[10px] px-3 py-1.5 rounded uppercase font-bold tracking-wider transition-all disabled:opacity-40"
                        style={{
                          color: "var(--crt-green)",
                          background: "color-mix(in srgb, var(--crt-green) 12%, transparent)",
                          border: "1px solid color-mix(in srgb, var(--crt-green) 40%, transparent)",
                        }}
                      >
                        {whitelistBusy ? "…" : "Add"}
                      </button>
                    </div>
                  )}
                  {whitelistError && (
                    <div className="text-[10px] mt-1" style={{ color: "var(--crt-red)" }}>
                      {whitelistError}
                    </div>
                  )}
                  {!isOwner && (
                    <div className="text-[9px] uppercase tracking-wider mt-1" style={{ color: "var(--text-tertiary)" }}>
                      Only the configured owner ({cfg?.owner ? `${cfg.owner.slice(0, 6)}…${cfg.owner.slice(-4)}` : "—"}) can edit this list.
                    </div>
                  )}
                </div>
              </div>

              {renderShareAccessCard()}

              {/* Utility toolbar — process start/stop/restart already live
                  on the API/APP tiles above, so this is just the cross-
                  cutting actions: reload config (re-pulls config.json),
                  check health (probes /health), delete (owner only). Flat
                  toolbar feels lighter than a section card and matches the
                  density a header bar should have. */}
              <div
                className="rounded-xl flex items-center gap-2 p-2.5 flex-wrap"
                style={{
                  border: "1px solid var(--border-color)",
                  background: "color-mix(in srgb, var(--glass-bg) 70%, transparent)",
                  backdropFilter: "blur(10px) saturate(140%)",
                  WebkitBackdropFilter: "blur(10px) saturate(140%)",
                }}
              >
                <span className="text-[10.5px] uppercase font-bold tracking-[0.16em] pl-2 pr-1.5" style={{ color: "var(--text-tertiary)" }}>
                  Actions
                </span>
                <button
                  onClick={fetchDirectConfig}
                  className="text-[10px] px-3 py-1.5 rounded-md uppercase font-bold tracking-wider transition-all flex items-center gap-1.5 hover:brightness-125"
                  style={{
                    border: "1px solid color-mix(in srgb, var(--crt-amber) 28%, transparent)",
                    color: "var(--crt-amber)",
                    background: "color-mix(in srgb, var(--crt-amber) 7%, transparent)",
                  }}
                  title="Re-fetch config.json from disk"
                >
                  <span style={{ opacity: 0.85 }}>↻</span> Reload Config
                </button>
                <button
                  onClick={() => checkModuleHealth()}
                  className="text-[10px] px-3 py-1.5 rounded-md uppercase font-bold tracking-wider transition-all flex items-center gap-1.5 hover:brightness-125"
                  style={{
                    border: "1px solid color-mix(in srgb, var(--crt-blue) 28%, transparent)",
                    color: "var(--crt-blue)",
                    background: "color-mix(in srgb, var(--crt-blue) 7%, transparent)",
                  }}
                  title="Probe the module's /health endpoint"
                >
                  <span style={{ opacity: 0.85 }}>♡</span> Check Health
                </button>
                <div className="flex-1" />
                {selectedModule && (isOwner || (cfg?.owner && address && cfg.owner.toLowerCase() === address.toLowerCase())) && (
                  confirmDeleteModule === selectedModule ? (
                    <div className="flex items-center gap-1.5">
                      <span className="text-[10px] uppercase font-bold tracking-wider" style={{ color: "var(--crt-red)" }}>Delete?</span>
                      <button
                        onClick={() => deleteModule(selectedModule)}
                        className="text-[10px] px-2 py-1 rounded uppercase font-bold tracking-wider transition-all"
                        style={{
                          border: "1px solid var(--crt-red)",
                          color: "#fff",
                          background: "var(--crt-red)",
                        }}
                      >
                        Confirm
                      </button>
                      <button
                        onClick={() => setConfirmDeleteModule(null)}
                        className="text-[10px] px-2 py-1 rounded uppercase font-bold tracking-wider transition-all"
                        style={{
                          border: "1px solid var(--border-color)",
                          color: "var(--text-tertiary)",
                          background: "transparent",
                        }}
                      >
                        Cancel
                      </button>
                    </div>
                  ) : (
                    <button
                      onClick={() => setConfirmDeleteModule(selectedModule)}
                      className="text-[10px] px-2.5 py-1 rounded uppercase font-bold tracking-wider transition-all"
                      style={{
                        border: "1px solid color-mix(in srgb, var(--crt-red) 22%, transparent)",
                        color: "color-mix(in srgb, var(--crt-red) 60%, var(--text-tertiary))",
                        background: "transparent",
                      }}
                      title="Permanently remove this module"
                    >
                      ✕ Delete Module
                    </button>
                  )
                )}
              </div>

              {/* Logs */}
              {(info?.api_url || info?.app_url) && (
              <div className="section-card" data-accent="amber">
                <span className="section-card__bar" />
                <div className="section-card__head">
                  <div className="section-card__title">
                    <span className="section-card__glyph">▤</span>
                    Logs
                  </div>
                  <div className="flex items-center gap-1.5">
                    <button
                      onClick={() => { setModuleLogsAutoRefresh(!moduleLogsAutoRefresh); if (!moduleLogsOpen) setModuleLogsOpen("api"); }}
                      className="text-[9px] px-1.5 py-0.5 rounded-sm border uppercase font-bold transition-all"
                      style={{
                        borderColor: moduleLogsAutoRefresh ? "color-mix(in srgb, var(--crt-green) 50%, transparent)" : "color-mix(in srgb, var(--border-color) 50%, transparent)",
                        color: moduleLogsAutoRefresh ? "var(--crt-green)" : "var(--text-tertiary)",
                        background: moduleLogsAutoRefresh ? "color-mix(in srgb, var(--crt-green) 8%, transparent)" : "transparent",
                      }}
                    >
                      {moduleLogsAutoRefresh ? "LIVE" : "AUTO"}
                    </button>
                    {moduleLogsOpen && (
                      <button
                        onClick={fetchModuleLogs}
                        className="text-[9px] px-1.5 py-0.5 rounded-sm border uppercase font-bold transition-all"
                        style={{ borderColor: "color-mix(in srgb, var(--border-color) 50%, transparent)", color: "var(--text-tertiary)" }}
                      >
                        {moduleLogsLoading ? "..." : "REFRESH"}
                      </button>
                    )}
                  </div>
                </div>
                <div className="p-3 flex flex-col gap-2">
                  {/* Source tabs */}
                  <div className="flex items-center gap-1.5">
                    {info?.api_url && (
                      <button
                        onClick={() => setModuleLogsOpen(moduleLogsOpen === "api" ? null : "api")}
                        className="text-[10px] px-2.5 py-1 rounded-sm border uppercase font-bold transition-all hover:brightness-125"
                        style={{
                          borderColor: moduleLogsOpen === "api" ? "color-mix(in srgb, var(--crt-blue) 50%, transparent)" : "color-mix(in srgb, var(--border-color) 50%, transparent)",
                          color: moduleLogsOpen === "api" ? "var(--crt-blue)" : "var(--text-tertiary)",
                          background: moduleLogsOpen === "api" ? "color-mix(in srgb, var(--crt-blue) 8%, transparent)" : "transparent",
                        }}
                      >
                        API LOGS
                      </button>
                    )}
                    {info?.app_url && (
                      <button
                        onClick={() => setModuleLogsOpen(moduleLogsOpen === "app" ? null : "app")}
                        className="text-[10px] px-2.5 py-1 rounded-sm border uppercase font-bold transition-all hover:brightness-125"
                        style={{
                          borderColor: moduleLogsOpen === "app" ? "color-mix(in srgb, var(--crt-amber) 50%, transparent)" : "color-mix(in srgb, var(--border-color) 50%, transparent)",
                          color: moduleLogsOpen === "app" ? "var(--crt-amber)" : "var(--text-tertiary)",
                          background: moduleLogsOpen === "app" ? "color-mix(in srgb, var(--crt-amber) 8%, transparent)" : "transparent",
                        }}
                      >
                        APP LOGS
                      </button>
                    )}
                  </div>
                  {/* Log output */}
                  {moduleLogsOpen && (
                    <div className="border rounded overflow-hidden" style={{ borderColor: moduleLogsOpen === "api" ? "color-mix(in srgb, var(--crt-blue) 25%, transparent)" : "color-mix(in srgb, var(--crt-amber) 25%, transparent)" }}>
                      <div className="flex items-center justify-between px-3 py-1" style={{ background: moduleLogsOpen === "api" ? "color-mix(in srgb, var(--crt-blue) 4%, transparent)" : "color-mix(in srgb, var(--crt-amber) 4%, transparent)", borderBottom: "1px solid var(--border-color)" }}>
                        <span className="text-[9px] font-bold uppercase" style={{ color: moduleLogsOpen === "api" ? "var(--crt-blue)" : "var(--crt-amber)", letterSpacing: "0.05em" }}>
                          {moduleLogsOpen.toUpperCase()} LOGS
                        </span>
                        <button onClick={() => setModuleLogsOpen(null)} className="text-[9px] font-bold uppercase" style={{ color: "var(--text-tertiary)", background: "none", border: "none", cursor: "pointer" }}>
                          CLOSE
                        </button>
                      </div>
                      <pre
                        className="px-3 py-2 text-[11px] overflow-auto"
                        style={{
                          color: "var(--text-secondary)",
                          fontFamily: "var(--font-code, monospace)",
                          whiteSpace: "pre-wrap",
                          wordBreak: "break-all",
                          lineHeight: "1.5",
                          maxHeight: "300px",
                          background: "var(--bg-primary)",
                          margin: 0,
                        }}
                        ref={(el) => { if (el) el.scrollTop = el.scrollHeight; }}
                      >
                        {(() => {
                          const keys = Object.keys(moduleLogs);
                          const matchKey = keys.find(k => k.toLowerCase().includes(moduleLogsOpen));
                          return matchKey ? moduleLogs[matchKey] || "(empty)" : keys.length > 0 ? moduleLogs[keys[0]] || "(empty)" : moduleLogsLoading ? "Loading..." : "(no logs found)";
                        })()}
                      </pre>
                    </div>
                  )}
                </div>
              </div>
              )}

              {/* Scripts & Ports */}
              <div className="section-card" data-accent="green">
                <span className="section-card__bar" />
                <div className="section-card__head">
                  <div className="section-card__title">
                    <span className="section-card__glyph">▸</span>
                    Scripts &amp; Ports
                  </div>
                </div>
                <div className="p-3 flex flex-col gap-1.5 text-[12px] font-mono">
                  {cfg?.scripts?.start && (
                    <div className="flex items-center gap-3">
                      <span className="text-crt-green/30 w-14 shrink-0">start</span>
                      <span className="text-crt-green/60">{cfg.scripts.start}</span>
                    </div>
                  )}
                  {cfg?.scripts?.stop && (
                    <div className="flex items-center gap-3">
                      <span className="text-crt-green/30 w-14 shrink-0">stop</span>
                      <span className="text-crt-green/60">{cfg.scripts.stop}</span>
                    </div>
                  )}
                  {cfg?.scripts?.docker && (
                    <div className="flex items-center gap-3">
                      <span className="text-crt-green/30 w-14 shrink-0">docker</span>
                      <span className="text-crt-green/60">{cfg.scripts.docker}</span>
                    </div>
                  )}
                  {!cfg?.scripts?.start && !cfg?.scripts?.stop && !cfg?.scripts?.docker && (
                    <>
                      {info?.has_app_dir && (
                        <div className="flex items-center gap-3">
                          <span className="text-crt-green/30 w-14 shrink-0">start</span>
                          <span className="text-crt-green/60">scripts/start.sh</span>
                        </div>
                      )}
                    </>
                  )}
                  {cfg?.port && (
                    <div className="flex items-center gap-3 pt-1" style={{ borderTop: "1px solid var(--border-color)" }}>
                      <span className="text-crt-amber/50 w-14 shrink-0 font-bold">port</span>
                      <span className="text-crt-amber font-bold">{cfg.port}</span>
                    </div>
                  )}
                </div>
              </div>

              {/* Config */}
              <div className="section-card" data-accent="amber">
                <span className="section-card__bar" />
                <div className="section-card__head">
                  <div className="section-card__title">
                    <span className="section-card__glyph">{"{}"}</span>
                    Config
                  </div>
                  <div className="flex items-center gap-1.5">
                    <button
                      onClick={() => collapseAll(cfg)}
                      className="text-[10px] px-2.5 py-1 rounded-md uppercase font-bold tracking-wider transition-all hover:brightness-125"
                      style={{ color: "var(--crt-amber)", background: "color-mix(in srgb, var(--crt-amber) 7%, transparent)", border: "1px solid color-mix(in srgb, var(--crt-amber) 26%, transparent)" }}
                    >
                      Collapse
                    </button>
                    <button
                      onClick={expandAll}
                      className="text-[10px] px-2.5 py-1 rounded-md uppercase font-bold tracking-wider transition-all hover:brightness-125"
                      style={{ color: "var(--crt-green)", background: "color-mix(in srgb, var(--crt-green) 7%, transparent)", border: "1px solid color-mix(in srgb, var(--crt-green) 26%, transparent)" }}
                    >
                      Expand
                    </button>
                    <button
                      onClick={() => copyValue("$root", cfg)}
                      className="text-[10px] px-2.5 py-1 rounded-md uppercase font-bold tracking-wider transition-all hover:brightness-125"
                      style={{
                        color: copiedPath === "$root" ? jsonCopiedColor : "var(--crt-blue)",
                        background: copiedPath === "$root" ? jsonCopiedBg : "color-mix(in srgb, var(--crt-blue) 7%, transparent)",
                        border: `1px solid ${copiedPath === "$root" ? `color-mix(in srgb, ${jsonCopiedColor} 30%, transparent)` : "color-mix(in srgb, var(--crt-blue) 26%, transparent)"}`,
                      }}
                    >
                      {copiedPath === "$root" ? "Copied" : "Copy"}
                    </button>
                  </div>
                </div>
                {cfg ? (
                  <div
                    className="overflow-y-auto overflow-x-auto px-3 py-3 text-[13px] font-mono leading-[1.55]"
                    style={{ color: "var(--crt-green)", maxHeight: "400px" }}
                  >
                    {renderJsonNode(null, cfg, "$", 0, true, false)}
                  </div>
                ) : (
                  <div className="p-4 text-center">
                    <span className="text-[13px] text-crt-green/30 uppercase">
                      {loadingConfig ? "Loading config…" : "No config loaded"}
                    </span>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    );
  };

  const renderConfigTab = () => {
    const cfg = effectiveConfig;
    if (!cfg) {
      return (
        <div className="flex-1 flex flex-col items-center justify-center gap-4 h-full p-6">
          <span className="text-[48px] opacity-10">⚙️</span>
          <span className="text-[14px] text-crt-green/30 uppercase" style={{ letterSpacing: "0.01em" }}>
            {loadingConfig ? "Loading config..." : "No config loaded"}
          </span>
          <button
            onClick={fetchDirectConfig}
            className="text-[14px] px-3 py-1 border border-crt-green/30 text-crt-green/60 hover:bg-crt-green/10 transition-all uppercase"
            style={{ letterSpacing: "0.01em" }}
          >
            Retry
          </button>
        </div>
      );
    }

    const endpointCount = cfg.endpoints ? Object.keys(cfg.endpoints).length : 0;
    const fnCount = cfg.fns ? cfg.fns.length : 0;

    return (
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Config Header - Enhanced */}
        <div
          className="px-4 py-3 border-b shrink-0"
          style={{
            borderColor: "rgba(245,158,11,0.15)",
            background: "linear-gradient(180deg, rgba(245,158,11,0.06) 0%, rgba(245,158,11,0.01) 100%)",
          }}
        >
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <span className="text-[13px] font-bold" style={{ color: "var(--crt-amber)", letterSpacing: "0.04em", textShadow: "none" }}>
                {cfg.name?.toUpperCase() || "MODULE"}
              </span>
              <span className="text-[14px] px-1.5 py-0.5 rounded-sm" style={{ color: "var(--crt-green)", background: `color-mix(in srgb, var(--crt-green) 10%, transparent)`, border: `1px solid color-mix(in srgb, var(--crt-green) 20%, transparent)` }}>
                v{cfg.version || "?"}
              </span>
              <span className="text-[14px] px-1.5 py-0.5 rounded-sm" style={{ color: "var(--crt-blue)", background: "rgba(59,130,246,0.08)", border: "1px solid rgba(59,130,246,0.15)" }}>
                :{cfg.port || "?"}
              </span>
            </div>
            <div className="flex items-center gap-1.5">
              <button
                onClick={() => collapseAll(cfg)}
                className="text-[13px] px-2.5 py-1 rounded-sm transition-all hover:brightness-125"
                style={{
                  color: "var(--crt-amber)",
                  background: "color-mix(in srgb, var(--crt-amber) 8%, transparent)",
                  border: "1px solid color-mix(in srgb, var(--crt-amber) 20%, transparent)",
                  letterSpacing: "0",
                }}
                title="Collapse all nested objects"
              >
                ◇ COLLAPSE
              </button>
              <button
                onClick={expandAll}
                className="text-[13px] px-2.5 py-1 rounded-sm transition-all hover:brightness-125"
                style={{
                  color: "var(--crt-green)",
                  background: "color-mix(in srgb, var(--crt-green) 8%, transparent)",
                  border: "1px solid color-mix(in srgb, var(--crt-green) 20%, transparent)",
                  letterSpacing: "0",
                }}
                title="Expand all nested objects"
              >
                ◆ EXPAND
              </button>
              <button
                onClick={() => copyValue("$root", cfg)}
                className="text-[13px] px-2.5 py-1 rounded-sm transition-all hover:brightness-125"
                style={{
                  color: copiedPath === "$root" ? jsonCopiedColor : "var(--crt-blue)",
                  background: copiedPath === "$root" ? jsonCopiedBg : "color-mix(in srgb, var(--crt-blue) 8%, transparent)",
                  border: `1px solid ${copiedPath === "$root" ? `color-mix(in srgb, ${jsonCopiedColor} 30%, transparent)` : "color-mix(in srgb, var(--crt-blue) 20%, transparent)"}`,
                  letterSpacing: "0",
                }}
                title="Copy entire config JSON"
              >
                {copiedPath === "$root" ? "✓ COPIED" : "⧉ COPY ALL"}
              </button>
            </div>
          </div>
          {/* Stats bar */}
          <div className="flex items-center gap-3 text-[14px]">
            {endpointCount > 0 && (
              <span style={{ color: "var(--crt-red)", opacity: 0.7 }}>
                ● {endpointCount} endpoints
              </span>
            )}
            {fnCount > 0 && (
              <span style={{ color: "var(--crt-amber)", opacity: 0.7 }}>
                ● {fnCount} functions
              </span>
            )}
            {cfg.owner && (
              <span style={{ color: "var(--crt-green)", opacity: 0.5 }}>
                ● {cfg.owner.slice(0, 6)}...{cfg.owner.slice(-4)}
              </span>
            )}
            <span style={{ color: "var(--text-tertiary)", opacity: 0.3 }}>config.json</span>
          </div>
        </div>

        {/* Collapsible JSON Tree - Enhanced */}
        <div
          className="flex-1 overflow-y-auto overflow-x-auto px-1 py-2 text-[13px] font-mono leading-[1.5]"
          style={{
            color: "var(--crt-green)",
            background: "linear-gradient(180deg, rgba(0,0,0,0.3) 0%, transparent 2%)",
          }}
        >
          {renderJsonNode(null, cfg, "$", 0, true, false)}
        </div>
      </div>
    );
  };

  const renderApiTab = () => {
    // Only show the selected module's own endpoints — never fall back to another module's config
    const ownConfig = moduleConfig?.config;
    const endpoints = ownConfig?.endpoints || {};
    const endpointKeys = Object.keys(endpoints);
    const baseUrl = selectedModuleInfo?.api_url || ownConfig?.urls?.api || ownConfig?.api_url || apiUrl;

    if (!ownConfig || endpointKeys.length === 0) {
      return (
        <div className="flex-1 flex flex-col items-center justify-center gap-4 h-full p-6">
          <span className="text-[48px] text-crt-green/10">⚙️</span>
          <span className="text-[14px] text-crt-green/30 uppercase" style={{ letterSpacing: "0.01em" }}>
            {loadingConfig ? "Loading config..." : "No API endpoints"}
          </span>
          <p className="text-[13px] text-crt-green/20 text-center max-w-xs">
            {selectedModule
              ? `Select a module with endpoints in config.json`
              : "Select a module to explore its API."}
          </p>
        </div>
      );
    }

    const currentEndpoint = apiSelectedEndpoint ? endpoints[apiSelectedEndpoint] : null;
    const currentInputs: Array<{ name: string; type: string; value: any }> = currentEndpoint?.input || [];
    // Extract path params from endpoint pattern
    const pathParams = apiSelectedEndpoint ? (apiSelectedEndpoint.match(/\{(\w+)\}/g) || []).map((p: string) => p.slice(1, -1)) : [];

    return (
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* API Header - Enhanced */}
        <div
          className="px-5 py-3 border-b"
          style={{
            borderColor: "var(--border-color)",
            background: "linear-gradient(to bottom, rgba(239,68,68,0.04), rgba(239,68,68,0.01))",
            boxShadow: "0 1px 0 rgba(239,68,68,0.1)"
          }}
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-4">
              <span className="text-[15px] font-semibold text-crt-red/80 uppercase tracking-wide">
                API EXPLORER
              </span>
              <div className="flex items-center gap-2 px-3 py-1.5 rounded-md" style={{ background: "rgba(251,191,36,0.08)", border: "1px solid rgba(251,191,36,0.15)" }}>
                <span className="text-[11px] text-crt-amber/60 uppercase tracking-wide">Base URL</span>
                <span className="text-[13px] text-crt-amber font-mono">{baseUrl}</span>
              </div>
            </div>
            <div className="flex items-center gap-2 px-3 py-1 rounded-full" style={{ background: "rgba(52,211,153,0.08)", border: "1px solid rgba(52,211,153,0.15)" }}>
              <div className="w-1.5 h-1.5 rounded-full led-pulse" style={{ background: "var(--crt-green)" }}></div>
              <span className="text-[13px] text-crt-green/80 font-medium">{endpointKeys.length} endpoints</span>
            </div>
          </div>
        </div>

        <div className="flex-1 flex flex-row overflow-hidden">
          {/* Endpoint List (left side) - Enhanced */}
          <div className="overflow-y-auto border-r" style={{ borderColor: "var(--border-color)", width: "280px", minWidth: "220px", flexShrink: 0, background: "rgba(0,0,0,0.15)" }}>
            {endpointKeys.map((ep) => {
              const info = endpoints[ep];
              const methods = Array.isArray(info.method) ? info.method : [info.method];
              const isSelected = apiSelectedEndpoint === ep;
              return (
                <div
                  key={ep}
                  onClick={() => {
                    setApiSelectedEndpoint(ep);
                    setApiMethod(methods[0]);
                    setApiParams({});
                    setApiResponse(null);
                    setApiResponseStatus(null);
                  }}
                  className="px-4 py-2.5 cursor-pointer border-b transition-all hover:bg-opacity-80"
                  style={{
                    borderColor: "rgba(255,255,255,0.04)",
                    background: isSelected ? "linear-gradient(to right, rgba(239,68,68,0.12), rgba(239,68,68,0.06))" : "transparent",
                    borderLeft: isSelected ? "3px solid var(--crt-red)" : "3px solid transparent",
                  }}
                >
                  <div className="flex items-center gap-2.5 mb-1">
                    <div className="flex gap-1.5">
                      {methods.map((m: string) => (
                        <span
                          key={m}
                          className="text-[11px] px-2 py-0.5 font-bold rounded"
                          style={{
                            color: m === "GET" ? "var(--crt-green)" : m === "POST" ? "var(--crt-blue)" : "var(--crt-red)",
                            background: m === "GET" ? "rgba(52,211,153,0.15)" : m === "POST" ? "rgba(96,165,250,0.15)" : "rgba(248,113,113,0.15)",
                            border: `1px solid ${m === "GET" ? apiGreenBorder : m === "POST" ? apiBlueBorder : apiRedBorder}`,
                            letterSpacing: "0.03em"
                          }}
                        >
                          {m}
                        </span>
                      ))}
                    </div>
                    {info.auth && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded border uppercase tracking-wide" style={{ borderColor: "rgba(251,191,36,0.3)", color: "var(--crt-amber)", background: "rgba(251,191,36,0.08)" }}>
                        Auth
                      </span>
                    )}
                  </div>
                  <div className="text-[14px] font-mono font-medium truncate mb-0.5" style={{ color: isSelected ? "var(--crt-red)" : "var(--text-primary)", opacity: isSelected ? 1 : 0.85 }}>
                    {ep}
                  </div>
                  {info.docs && (
                    <div className="text-[12px] leading-tight truncate" style={{ color: "var(--text-tertiary)", opacity: 0.5 }}>
                      {info.docs}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Selected Endpoint Detail (right side) */}
          {apiSelectedEndpoint && currentEndpoint ? (
            <div className="flex-1 flex flex-col overflow-hidden">
              {/* Method + Path + Send */}
              <div className="px-3 py-2 border-b flex items-center gap-2" style={{ borderColor: "var(--border-color)" }}>
                {Array.isArray(currentEndpoint.method) ? (
                  <select
                    value={apiMethod}
                    onChange={(e) => setApiMethod(e.target.value)}
                    className="text-[13px] font-bold px-2 py-1 border bg-transparent font-mono"
                    style={{
                      color: apiMethod === "GET" ? "var(--crt-green)" : apiMethod === "POST" ? "var(--crt-blue)" : "var(--crt-red)",
                      borderColor: "rgba(255,255,255,0.15)",
                    }}
                  >
                    {(currentEndpoint.method as string[]).map((m: string) => (
                      <option key={m} value={m} style={{ background: "var(--bg-primary)", color: "var(--text-primary)" }}>{m}</option>
                    ))}
                  </select>
                ) : (
                  <span
                    className="text-[13px] font-bold px-2 py-1 border"
                    style={{
                      color: currentEndpoint.method === "GET" ? "var(--crt-green)" : currentEndpoint.method === "POST" ? "var(--crt-blue)" : "var(--crt-red)",
                      borderColor: "rgba(255,255,255,0.15)",
                    }}
                  >
                    {currentEndpoint.method}
                  </span>
                )}
                <span className="text-[14px] font-mono flex-1" style={{ color: "var(--text-primary)", opacity: 0.8 }}>
                  {apiSelectedEndpoint}
                </span>
                <button
                  onClick={() => fireApiRequest(apiSelectedEndpoint, apiMethod, apiParams)}
                  disabled={apiLoading}
                  className="text-[13px] font-bold px-3 py-1 border transition-all"
                  style={{
                    color: apiLoading ? "var(--text-tertiary)" : "#fff",
                    background: apiLoading ? "transparent" : "var(--crt-green)",
                    borderColor: "var(--crt-green)",
                    letterSpacing: "0.01em",
                    opacity: apiLoading ? 0.5 : 1,
                  }}
                >
                  {apiLoading ? "..." : "SEND"}
                </button>
              </div>

              {/* Params */}
              {(currentInputs.length > 0 || pathParams.length > 0) && (
                <div className="px-3 py-2 border-b space-y-1.5" style={{ borderColor: "var(--border-color)" }}>
                  <span className="text-[13px] uppercase" style={{ color: "var(--text-tertiary)", opacity: 0.4, letterSpacing: "0.01em" }}>
                    Parameters
                  </span>
                  {pathParams.map((p: string) => (
                    <div key={p} className="flex items-center gap-2">
                      <span className="text-[13px] font-mono w-24 shrink-0" style={{ color: "var(--crt-amber)" }}>{`{${p}}`}</span>
                      <input
                        type="text"
                        value={apiParams[p] || ""}
                        onChange={(e) => setApiParams({ ...apiParams, [p]: e.target.value })}
                        className="flex-1 text-[13px] font-mono px-2 py-1 border bg-transparent"
                        style={{ color: "var(--text-primary)", borderColor: "var(--border-color-strong)" }}
                        placeholder={`path param: ${p}`}
                        onKeyDown={(e) => { if (e.key === "Enter") fireApiRequest(apiSelectedEndpoint!, apiMethod, apiParams); }}
                      />
                    </div>
                  ))}
                  {currentInputs.map((input: any) => (
                    <div key={input.name} className="flex items-center gap-2">
                      <span className="text-[13px] font-mono w-24 shrink-0 truncate" style={{ color: "var(--text-primary)", opacity: 0.6 }} title={`${input.name} (${input.type})`}>
                        {input.name}
                      </span>
                      {input.type === "bool" ? (
                        <select
                          value={apiParams[input.name] || ""}
                          onChange={(e) => setApiParams({ ...apiParams, [input.name]: e.target.value })}
                          className="flex-1 text-[13px] font-mono px-2 py-1 border bg-transparent"
                          style={{ color: "var(--text-primary)", borderColor: "var(--border-color-strong)" }}
                        >
                          <option value="" style={{ background: "var(--bg-primary)" }}>—</option>
                          <option value="true" style={{ background: "var(--bg-primary)" }}>true</option>
                          <option value="false" style={{ background: "var(--bg-primary)" }}>false</option>
                        </select>
                      ) : (
                        <input
                          type="text"
                          value={apiParams[input.name] || ""}
                          onChange={(e) => setApiParams({ ...apiParams, [input.name]: e.target.value })}
                          className="flex-1 text-[13px] font-mono px-2 py-1 border bg-transparent"
                          style={{ color: "var(--text-primary)", borderColor: "var(--border-color-strong)" }}
                          placeholder={input.value === "_empty" ? `required (${input.type})` : `${input.type}${input.value != null ? ` = ${input.value}` : ""}`}
                          onKeyDown={(e) => { if (e.key === "Enter") fireApiRequest(apiSelectedEndpoint!, apiMethod, apiParams); }}
                        />
                      )}
                    </div>
                  ))}
                </div>
              )}

              {/* Response */}
              <div className="flex-1 overflow-hidden flex flex-col">
                <div className="px-3 py-1 flex items-center justify-between" style={{ background: "var(--bg-tint)" }}>
                  <span className="text-[13px] uppercase" style={{ color: "var(--text-tertiary)", opacity: 0.4, letterSpacing: "0.01em" }}>
                    Response
                  </span>
                  {apiResponseStatus !== null && (
                    <span
                      className="text-[13px] font-bold px-1.5 py-0.5"
                      style={{
                        color: apiResponseStatus >= 200 && apiResponseStatus < 300 ? "var(--crt-green)" : apiResponseStatus === 0 ? "var(--crt-red)" : "var(--crt-amber)",
                      }}
                    >
                      {apiResponseStatus === 0 ? "ERR" : apiResponseStatus}
                    </span>
                  )}
                </div>
                <pre className="flex-1 overflow-y-auto px-3 py-2 m-0 text-[13px] font-mono leading-relaxed" style={{ color: "var(--text-primary)", opacity: 0.8 }}>
                  {apiLoading ? "Sending request..." : apiResponse || "Hit SEND to execute the request"}
                </pre>
              </div>
            </div>
          ) : (
            /* No endpoint selected */
            <div className="flex-1 flex flex-col items-center justify-center gap-3 p-6">
              <span className="text-[14px] text-crt-green/20 uppercase" style={{ letterSpacing: "0.01em" }}>
                Select an endpoint
              </span>
            </div>
          )}
        </div>
      </div>
    );
  };

  // Browser-style address bar (the "omnibox"), lifted out of the APP tab into
  // the header's top-left corner. It's now the single way to navigate the
  // /{mod} namespace — the old module-name search dropdown was redundant with
  // it, so it's gone. ‹ › step through modules; clicking the URL edits it (the
  // /{mod} path autocompletes against the registry, and the host is editable
  // to re-root the same names onto a different host's registrar).
  const renderOmnibox = () => {
    const modName = selectedModule || "claude";
    // Mirror renderAppApiTab: claude's own APP is the web front-door.
    const appModName = omniForeignMod || (modName === "claude" ? "web" : modName);
    const gatewayUrl = `${(gatewayHostOverride || defaultGatewayOrigin(mounted)).replace(/\/+$/, "")}/${appModName}`;
    const stepModule = (dir: 1 | -1) => {
      const list = moduleList
        .filter(isRealModule)
        .filter((m) => m.app_url || m.has_app_dir)
        .sort((a, b) => a.name.localeCompare(b.name));
      if (!list.length) { fetchModules(""); return; }
      const i = list.findIndex((m) => m.name === selectedModule);
      const next = list[i < 0 ? (dir === 1 ? 0 : list.length - 1) : (i + dir + list.length) % list.length];
      selectModule(next);
      setSidebarView("app");
    };
    const openOmnibox = () => {
      setOmniValue(gatewayUrl);
      setOmniIdx(0);
      setOmniEditing(true);
      if (!moduleList.length) fetchModules("");
    };
    const omniMatches = (q: string) => {
      const list = railMatches(q).slice(0, 12);
      const query = q.trim().toLowerCase();
      const i = list.findIndex((m) => m.name.toLowerCase() === query);
      if (i > 0) list.unshift(list.splice(i, 1)[0]);
      return list.slice(0, 8);
    };
    const commitOmnibox = (pick?: typeof moduleList[0] | null) => {
      const { origin, mod } = parseOmnibox(omniValue);
      let override = gatewayHostOverride;
      if (origin !== null) {
        override = origin === defaultGatewayOrigin() ? null : origin;
        setGatewayHostOverride(override);
        if (override) safeSetItem("claude_gateway_host", override);
        else { try { window.localStorage.removeItem("claude_gateway_host"); } catch { /* ignore */ } }
      }
      const target = pick || (mod ? omniMatches(mod)[0] : null);
      if (target) {
        if (target.name !== selectedModule) { selectModule(target); setSidebarView("app"); }
        else { setOmniForeignMod(null); }
      } else if (mod && override) {
        setOmniForeignMod(mod);
      }
      setOmniEditing(false);
    };
    return (
      <div className="flex items-center gap-1.5 flex-1 min-w-0" style={{ minWidth: 320, maxWidth: 760 }}>
        {([-1, 1] as const).map((dir) => (
          <button
            key={dir}
            onClick={() => stepModule(dir)}
            className="shrink-0 flex items-center justify-center transition-all hover:brightness-125"
            style={{
              width: 24, height: 24, borderRadius: 999,
              color: "var(--crt-green)",
              background: "color-mix(in srgb, var(--crt-green) 8%, transparent)",
              border: "1px solid color-mix(in srgb, var(--crt-green) 25%, transparent)",
              fontSize: 14, lineHeight: 1, cursor: "pointer",
            }}
            title={dir === -1 ? "Previous module" : "Next module"}
            aria-label={dir === -1 ? "Previous module" : "Next module"}
          >
            {dir === -1 ? "‹" : "›"}
          </button>
        ))}
        <div className="relative flex-1 min-w-0">
          <div
            className="flex items-center gap-1.5 min-w-0 transition-all"
            style={{
              background: "var(--bg-secondary)",
              border: `1px solid ${omniEditing ? "color-mix(in srgb, var(--crt-green) 45%, transparent)" : "var(--border-color)"}`,
              borderRadius: 999,
              padding: "3px 10px",
            }}
          >
            {omniEditing ? (
              <input
                value={omniValue}
                autoFocus
                spellCheck={false}
                onFocus={(e) => {
                  const v = e.currentTarget.value;
                  const i = v.lastIndexOf("/");
                  if (i >= 0) e.currentTarget.setSelectionRange(i + 1, v.length);
                }}
                onChange={(e) => {
                  setOmniValue(e.target.value);
                  setOmniIdx(0);
                  fetchModules(parseOmnibox(e.target.value).mod);
                }}
                onKeyDown={(e) => {
                  const matches = omniMatches(parseOmnibox(omniValue).mod);
                  if (e.key === "ArrowDown") { e.preventDefault(); setOmniIdx((i) => Math.min(i + 1, Math.max(matches.length - 1, 0))); }
                  else if (e.key === "ArrowUp") { e.preventDefault(); setOmniIdx((i) => Math.max(i - 1, 0)); }
                  else if (e.key === "Enter") { e.preventDefault(); commitOmnibox(matches[omniIdx] || null); }
                  else if (e.key === "Escape") { setOmniEditing(false); }
                }}
                onBlur={() => setOmniEditing(false)}
                className="flex-1 min-w-0 font-mono text-[11px] bg-transparent outline-none"
                style={{ color: "var(--text-primary)" }}
                aria-label="Address bar — type a module name, or a different host"
              />
            ) : (
              <div
                onMouseDown={(e) => { e.preventDefault(); openOmnibox(); }}
                className="flex-1 min-w-0 font-mono text-[11px] truncate cursor-text select-none"
                title={`${gatewayUrl} — click to edit: type a /{mod} (autocompletes) or a different host`}
              >
                {(() => {
                  const m = gatewayUrl.match(/^([a-z]+:\/\/)([^/]+)(\/.*)?$/i);
                  if (!m) return <span style={{ color: "var(--text-secondary)" }}>{gatewayUrl}</span>;
                  return (
                    <>
                      <span style={{ color: "var(--text-tertiary)", opacity: 0.55 }}>{m[1]}</span>
                      <span style={{ color: gatewayHostOverride ? "var(--crt-amber)" : "var(--text-secondary)" }}>{m[2]}</span>
                      <span style={{ color: "var(--crt-green)", fontWeight: 600 }}>{m[3] || ""}</span>
                    </>
                  );
                })()}
              </div>
            )}
            {gatewayHostOverride && !omniEditing && (
              <button
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => {
                  setGatewayHostOverride(null);
                  setOmniForeignMod(null);
                  try { window.localStorage.removeItem("claude_gateway_host"); } catch { /* ignore */ }
                }}
                className="shrink-0 text-[9px] font-bold uppercase tracking-wider px-1.5 rounded-full transition-all hover:brightness-125"
                style={{
                  color: "var(--crt-amber)",
                  background: "color-mix(in srgb, var(--crt-amber) 10%, transparent)",
                  border: "1px solid color-mix(in srgb, var(--crt-amber) 40%, transparent)",
                  cursor: "pointer",
                }}
                title={`Custom host — /{mod} resolves via ${gatewayHostOverride}'s registrar. Click to reset to this site.`}
              >
                ✕ host
              </button>
            )}
            <button
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => navigator.clipboard?.writeText(gatewayUrl).catch(() => {})}
              className="shrink-0 text-[11px] opacity-40 hover:opacity-100 transition-opacity"
              style={{ color: "var(--text-secondary)", background: "none", border: "none", cursor: "pointer", lineHeight: 1, padding: 0 }}
              title="Copy URL"
              aria-label="Copy URL"
            >
              ⧉
            </button>
          </div>
          {omniEditing && (() => {
            const matches = omniMatches(parseOmnibox(omniValue).mod);
            if (!matches.length) return null;
            return (
              <div
                className="absolute left-0 right-0 top-full mt-1 rounded-lg overflow-hidden"
                style={{
                  zIndex: 60,
                  background: "var(--bg-secondary, var(--bg-primary))",
                  border: "1px solid var(--border-color)",
                  boxShadow: "0 8px 24px rgba(0,0,0,0.45)",
                }}
              >
                {matches.map((m, i) => {
                  const st = moduleStatuses[m.name];
                  const live = st ? st.app === true || st.api === true : null;
                  return (
                    <button
                      key={m.name}
                      onMouseDown={(e) => { e.preventDefault(); commitOmnibox(m); }}
                      onMouseEnter={() => setOmniIdx(i)}
                      className="w-full flex items-center gap-2 px-3 py-1.5 font-mono text-[11px] text-left"
                      style={{
                        background: i === omniIdx ? "color-mix(in srgb, var(--crt-green) 10%, transparent)" : "transparent",
                        color: "var(--text-secondary)",
                        border: "none",
                        cursor: "pointer",
                      }}
                    >
                      <span
                        className="shrink-0"
                        style={{ width: 6, height: 6, borderRadius: 999, background: live ? "var(--crt-green)" : "var(--text-tertiary)", opacity: live ? 1 : 0.35 }}
                      />
                      <span className="shrink-0" style={{ color: "var(--crt-green)" }}>/{m.name}</span>
                      {m.description && (
                        <span className="truncate" style={{ color: "var(--text-tertiary)" }}>{m.description}</span>
                      )}
                    </button>
                  );
                })}
              </div>
            );
          })()}
        </div>
        <a
          href={gatewayUrl}
          target="_blank"
          rel="noreferrer noopener"
          className="text-[10px] px-2 py-1 rounded uppercase font-bold tracking-wider transition-all shrink-0"
          style={{
            color: "var(--crt-green)",
            background: "color-mix(in srgb, var(--crt-green) 10%, transparent)",
            border: "1px solid color-mix(in srgb, var(--crt-green) 35%, transparent)",
            textDecoration: "none",
          }}
          title="Open in new tab"
        >
          ↗
        </a>
      </div>
    );
  };

  // Combined APP / API tab — a single tab that hosts both the live app
  // (iframe) and the API explorer, with a segmented toggle to flip
  // between them. `sidebarView` ("app" | "api") doubles as the toggle
  // state so deep-links and the service-tile URL clicks still land on
  // the right sub-view. The toggle only appears when the module has
  // both an app and an api; otherwise we render whichever exists.
  const renderAppApiTab = () => {
    const hasApp = !!(selectedModuleInfo?.app_url || selectedModuleInfo?.has_app_dir);
    // App-only: the API explorer and its toggle were removed, so this tab
    // always shows the live app.
    const hasApi = false;
    const sub: "app" | "api" = "app";
    const modName = selectedModule || "claude";
    // claude's APP tab embeds the web front-door, never /claude itself: the
    // console IS the claude app, so iframing it would nest a second console
    // (double header/composer/task chips) inside this one. A foreign /{mod}
    // typed into the omnibox (resolvable only by the foreign host's
    // registrar) wins over both.
    const appModName = omniForeignMod || (modName === "claude" ? "web" : modName);
    // Gateway URL shown in the omnibox and loaded in the iframe. Caddy
    // proxies {host}/{mod} → the module's app; the host defaults to this
    // deployment (see defaultGatewayOrigin) unless the omnibox re-rooted it.
    const gatewayUrl = `${(gatewayHostOverride || defaultGatewayOrigin(mounted)).replace(/\/+$/, "")}/${appModName}`;
    const logsOpen = moduleLogsOpen !== null;
    // Logs follow the active sub-view, so the APP/API toggle doubles as a
    // "logs of each" switch: flip to APP → app logs, flip to API → api logs.
    const openLogs = (which?: "app" | "api") => {
      setModuleLogsOpen(which ?? sub);
      setModuleLogsAutoRefresh(true);
    };
    const logsText = (() => {
      const keys = Object.keys(moduleLogs);
      const matchKey = keys.find((k) => k.toLowerCase().includes(sub));
      return matchKey
        ? moduleLogs[matchKey] || "(empty)"
        : keys.length > 0
          ? moduleLogs[keys[0]] || "(empty)"
          : moduleLogsLoading
            ? "Loading..."
            : "(no logs found)";
    })();
    return (
      <div
        className="flex-1 flex flex-col overflow-hidden"
        style={
          appExpanded
            ? { position: "fixed", inset: 0, zIndex: 200, background: "var(--bg-primary)" }
            : undefined
        }
      >
        {/* Control row: LOGS + EXPAND. The browser-style address bar (omnibox)
            that used to live here now sits in the header's top-left corner
            (renderOmnibox) so it persists across every tab. */}
        {(hasApp || hasApi) && (
          <div
            className="flex items-center gap-2 px-3 py-2 shrink-0"
            style={{
              borderBottom: "1px solid var(--border-color)",
              background: "linear-gradient(180deg, var(--bg-tint), transparent)",
            }}
          >
            {/* LOGS toggle — shows the live pm2 logs for the active service
                (APP or API). The APP/API toggle above switches which. */}
            <button
              onClick={() => (logsOpen ? setModuleLogsOpen(null) : openLogs())}
              className="text-[10px] px-2.5 py-1 rounded uppercase font-bold tracking-wider transition-all shrink-0 ml-auto"
              style={{
                color: logsOpen ? "var(--crt-amber)" : "var(--text-tertiary)",
                background: logsOpen ? "color-mix(in srgb, var(--crt-amber) 12%, transparent)" : "transparent",
                border: `1px solid ${logsOpen ? "color-mix(in srgb, var(--crt-amber) 40%, transparent)" : "var(--border-color)"}`,
              }}
              title={`Show ${sub.toUpperCase()} logs`}
            >
              {logsOpen ? "✕ LOGS" : "▤ LOGS"}
            </button>
            {/* PHONE — preview the embedded app at a mobile viewport
                (390×844 device frame). Toggles back to the full-width view. */}
            {hasApp && (
              <button
                onClick={() => setAppPhoneView((v) => !v)}
                className="text-[10px] px-2.5 py-1 rounded uppercase font-bold tracking-wider transition-all shrink-0"
                style={{
                  color: appPhoneView ? "var(--crt-blue)" : "var(--text-tertiary)",
                  background: appPhoneView ? "color-mix(in srgb, var(--crt-blue) 12%, transparent)" : "transparent",
                  border: `1px solid ${appPhoneView ? "color-mix(in srgb, var(--crt-blue) 40%, transparent)" : "var(--border-color)"}`,
                }}
                title={appPhoneView ? "Back to full-width view" : "Preview at a phone viewport (390×844)"}
              >
                {appPhoneView ? "▭ FULL" : "▯ PHONE"}
              </button>
            )}
            {/* EXPAND — blow the embedded app up to a full-viewport overlay
                (hides the console chrome). Toggles back to MINIMIZE. */}
            {hasApp && (
              <button
                onClick={() => setAppExpanded((v) => !v)}
                className="text-[10px] px-2.5 py-1 rounded uppercase font-bold tracking-wider transition-all shrink-0"
                style={{
                  color: appExpanded ? "var(--crt-green)" : "var(--text-tertiary)",
                  background: appExpanded ? "color-mix(in srgb, var(--crt-green) 12%, transparent)" : "transparent",
                  border: `1px solid ${appExpanded ? "color-mix(in srgb, var(--crt-green) 40%, transparent)" : "var(--border-color)"}`,
                }}
                title={appExpanded ? "Exit full screen" : "Expand app to full screen"}
              >
                {appExpanded ? "⤡ MINIMIZE" : "⤢ EXPAND"}
              </button>
            )}
          </div>
        )}
        <div className="flex-1 overflow-hidden flex flex-col min-h-0">
          {logsOpen ? (
            <div className="flex-1 flex flex-col overflow-hidden">
              <div
                className="flex items-center justify-between px-3 py-1.5 shrink-0"
                style={{ borderBottom: "1px solid var(--border-color)", background: "color-mix(in srgb, var(--crt-amber) 4%, transparent)" }}
              >
                <span className="text-[9px] font-bold uppercase tracking-[0.12em]" style={{ color: "var(--crt-amber)" }}>
                  {sub.toUpperCase()} LOGS{moduleLogsAutoRefresh ? " · LIVE" : ""}
                </span>
                <button
                  onClick={fetchModuleLogs}
                  className="text-[9px] font-bold uppercase tracking-wider"
                  style={{ color: "var(--text-tertiary)", background: "none", border: "none", cursor: "pointer" }}
                  title="Refresh now"
                >
                  {moduleLogsLoading ? "…" : "↻ REFRESH"}
                </button>
              </div>
              <pre
                className="flex-1 overflow-auto px-3 py-2 m-0 text-[11px]"
                style={{
                  color: "var(--text-secondary)",
                  fontFamily: "var(--font-code, monospace)",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-all",
                  lineHeight: 1.5,
                  background: "var(--bg-primary)",
                }}
                ref={(el) => { if (el) el.scrollTop = el.scrollHeight; }}
              >
                {logsText}
              </pre>
            </div>
          ) : renderAppTab(gatewayUrl)}
        </div>
      </div>
    );
  };

  // ═══════════════════════════════════════════════════════════════════
  // DASHBOARD
  // ═══════════════════════════════════════════════════════════════════

  return (
    <div
      className="h-screen w-screen flex flex-col overflow-hidden"
      style={{
        background: "var(--bg-primary)",
        color: "var(--text-primary)",
      }}
    >
      {/* Inbound QR edit-invite banner — a `?grant=<id>` link landed here.
          Offer to redeem: optional key + connect-wallet, which threads the
          grant through sign-in for time-boxed edit access. Hidden once the
          invite is consumed (signChallenge clears pendingGrant) or dismissed. */}
      {pendingGrant && !isOwner && (
        <div
          className="fixed top-3 left-1/2 -translate-x-1/2 z-[1000] flex items-center gap-2.5 px-3 py-2.5 rounded-xl"
          style={{
            maxWidth: "min(92vw, 560px)",
            background: "color-mix(in srgb, var(--glass-bg, #11131a) 88%, transparent)",
            border: "1px solid color-mix(in srgb, #cc785c 45%, transparent)",
            backdropFilter: "blur(14px) saturate(150%)",
            WebkitBackdropFilter: "blur(14px) saturate(150%)",
            boxShadow: "0 8px 30px rgba(0,0,0,0.45)",
          }}
        >
          <span className="text-[18px] leading-none shrink-0" style={{ color: "#cc785c" }}>⧉</span>
          <div className="flex flex-col gap-0.5 min-w-0">
            <div className="text-[11.5px] font-bold" style={{ color: "var(--text-primary)" }}>
              You&apos;ve been invited
            </div>
            <div className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>
              Enter instantly as a guest, or connect a wallet — access is temporary either way.
            </div>
          </div>
          <input
            type="text"
            value={redeemKey}
            onChange={(e) => setRedeemKey(e.target.value)}
            placeholder="key (if given)"
            className="px-2 py-1.5 text-[11px] font-mono rounded outline-none w-[110px] shrink-0"
            style={{ color: "var(--text-primary)", background: "var(--bg-secondary)", border: "1px solid var(--border-color)" }}
          />
          <button
            onClick={enterAsGuest}
            disabled={authLoading}
            className="text-[11px] px-3 py-1.5 rounded uppercase font-bold tracking-wider shrink-0 transition-all disabled:opacity-40"
            style={{ color: "#0b0b0c", background: "#cc785c", border: "1px solid #cc785c" }}
            title="No wallet needed — a guest pass that expires with the invite"
          >
            {authLoading ? "…" : "Enter"}
          </button>
          <button
            onClick={() => redeemInvite("metamask")}
            disabled={authLoading}
            className="text-[11px] px-3 py-1.5 rounded uppercase font-bold tracking-wider shrink-0 transition-all disabled:opacity-40"
            style={{
              color: "#cc785c",
              background: "color-mix(in srgb, #cc785c 12%, transparent)",
              border: "1px solid color-mix(in srgb, #cc785c 45%, transparent)",
            }}
            title="Sign in with your wallet so the access is tied to your address"
          >
            Wallet
          </button>
          <button
            onClick={() => { setPendingGrant(null); pendingGrantRef.current = null; }}
            className="text-[14px] px-1 shrink-0"
            style={{ color: "var(--text-tertiary)" }}
            title="Dismiss"
          >
            ✕
          </button>
          {authError && (
            <div className="absolute -bottom-6 left-0 text-[10px] px-2" style={{ color: "var(--crt-red)" }}>
              {authError}
            </div>
          )}
        </div>
      )}

      {/* Guest session pill — live countdown to the grant's expiry. The server
          cuts access at the same moment; this keeps the deadline visible. */}
      {address && address.startsWith("guest_") && guestExp && (
        <div
          className="fixed top-3 left-1/2 -translate-x-1/2 z-[999] flex items-center gap-2 px-3 py-1.5 rounded-full"
          style={{
            background: "color-mix(in srgb, var(--glass-bg, #11131a) 88%, transparent)",
            border: "1px solid color-mix(in srgb, #cc785c 40%, transparent)",
            backdropFilter: "blur(14px) saturate(150%)",
            WebkitBackdropFilter: "blur(14px) saturate(150%)",
            boxShadow: "0 6px 24px rgba(0,0,0,0.4)",
          }}
        >
          <span
            className="w-1.5 h-1.5 rounded-full shrink-0"
            style={{ background: "#cc785c", boxShadow: "0 0 6px #cc785c" }}
          />
          <span className="text-[10px] uppercase tracking-wider font-bold" style={{ color: "var(--text-primary)" }}>
            Guest
          </span>
          <span className="text-[10px] font-mono" style={{ color: "#cc785c" }}>
            {(() => {
              const s = Math.max(0, guestExp - nowSec);
              if (s >= 86400) return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
              if (s >= 3600) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
              if (s >= 60) return `${Math.floor(s / 60)}m ${s % 60}s`;
              return `${s}s`;
            })()} left
          </span>
        </div>
      )}

      {/* Add-Module modal — top-level so a scanned `?import=<cid>` deep link
          can open it from any view, not just the hub. */}
      {addOpen && renderAddModuleModal()}

      {/* Share-QR overlay — scan instead of copy/paste */}
      {qrShare && renderQrShareModal()}

      {/* Versions overlay (mod-protocol, storage-agnostic) */}
      {showVersions && selectedModule && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(7, 7, 13, 0.65)",
            backdropFilter: "blur(8px)",
            zIndex: 1000,
            display: "flex",
            alignItems: "flex-start",
            justifyContent: "center",
            padding: "48px 24px",
            overflowY: "auto",
          }}
          onClick={() => setShowVersions(false)}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{ maxWidth: 1100, width: "100%" }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                marginBottom: 16,
                color: "var(--text-primary)",
              }}
            >
              <div>
                <div style={{ fontSize: 13, color: "var(--text-tertiary)", letterSpacing: "0.08em", textTransform: "uppercase" }}>
                  Mod Protocol · Version Control
                </div>
                <div style={{ fontSize: 24, fontWeight: 600, marginTop: 4 }}>{prettyModName(selectedModule)}</div>
              </div>
              <button
                className="glass-btn ghost"
                onClick={() => setShowVersions(false)}
                title="Close"
              >
                close
              </button>
            </div>
            <VersionsPanel
              apiBase={apiUrl}
              module={selectedModule}
              authHeader={token ? { Authorization: `Bearer ${token}` } : undefined}
              onForked={(m) => { setShowVersions(false); setSelectedModule(m); }}
            />
          </div>
        </div>
      )}

      {/* ── Header: two stacked rows — compact nav bar (top) + module
          tabs (second row below). The wrapper stacks them vertically; the
          nav bar itself keeps its own flex-row (desktop) layout. Its height
          is published to --header-h so the sign-in drawer starts below it. */}
      <div ref={headerRef} className="flex flex-col shrink-0" style={{ background: "var(--bg-secondary)" }}>
      {/* ── Compact Nav Bar (top row) ──────────────────────────────── */}
      <div
        className={isMobile ? "flex flex-col shrink-0" : "flex flex-row items-center shrink-0"}
        style={{
          background: "var(--bg-secondary)",
          borderBottom: `1px solid ${subtleBorder}`,
          // The nav rail spans the full viewport height (fixed, far left),
          // so the header starts to its right instead of underneath it.
          paddingLeft: !isMobile ? 0 : undefined,
        }}
      >
        {/* Module selector + mod tabs (app/code/overview). On desktop this is
            the ONLY header row — the account controls sit inline at its right
            edge (a separate top row would be an empty bar with one chip). On
            phone it stacks BELOW the HUB/TASKS + account row via flex
            `order`. */}
        <div
          className={`flex items-center py-0.5 ${isMobile ? "px-4" : "pl-3 pr-2 flex-1 min-w-0"}`}
          style={{ order: isMobile ? 2 : 1 }}
        >
          {/* Browser-style address bar — anchored in the top-LEFT corner of the
              header row (the orbit mark that used to sit here is gone). It is
              the single way to jump between modules (type a /{mod}, ‹ › to
              step). Desktop only: the phone header stacks and has no room. */}
          {!isMobile && <div className="pr-3 flex-1 min-w-0 flex">{renderOmnibox()}</div>}
          <div className="flex items-center gap-3">
            {/* Module/Folder selector dropdown */}
            <div className="relative" ref={headerModuleRef}>
              {showHeaderModuleDropdown && isMobile ? (
                <div className="flex items-center gap-0">
                  <input
                    type="text"
                    autoFocus
                    value={headerModuleSearch}
                    onChange={(e) => {
                      setHeaderModuleSearch(e.target.value);
                      if (selectorMode === "modules") {
                        fetchModules(e.target.value);
                      } else {
                        fetchFolders(e.target.value);
                        fetchFolderSuggestions(e.target.value);
                      }
                    }}
                    onFocus={(e) => {
                      e.target.select();
                      if (selectorMode === "modules") {
                        if (!moduleList.length) fetchModules(headerModuleSearch);
                      } else {
                        fetchFolders(headerModuleSearch);
                        if (headerModuleSearch) fetchFolderSuggestions(headerModuleSearch);
                      }
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Tab") {
                        e.preventDefault();
                        const next = selectorMode === "modules" ? "folders" : "modules";
                        setSelectorMode(next);
                        if (next === "folders") { fetchFolders(headerModuleSearch); if (headerModuleSearch) fetchFolderSuggestions(headerModuleSearch); }
                        else { fetchModules(headerModuleSearch); }
                      }
                      if (e.key === "Enter") {
                        if (selectorMode === "modules") {
                          // Pick the top VISIBLE suggestion (recents ranked
                          // first), not the raw fetch order.
                          const firstModule = rankedHeaderModules(headerModuleSearch)[0];
                          if (firstModule) {
                            resetModuleState(firstModule);
                            setSelectedModule(firstModule.name);
                            setSelectedModuleInfo(firstModule);
                            setWorkDir(firstModule.path);
                            setHeaderModuleSearch("");
                            setShowHeaderModuleDropdown(false);
                            fetchModuleConfig(firstModule.name);
                          }
                        } else if (selectorMode === "folders") {
                          const pick = folderSuggestions[0] || folderList[0];
                          if (pick) {
                            setWorkDir(pick.path);
                            setSelectedModule(pick.name.split("/").pop() || pick.name);
                            setHeaderModuleSearch("");
                            setShowHeaderModuleDropdown(false);
                          }
                        }
                      }
                      if (e.key === "Escape") {
                        setShowHeaderModuleDropdown(false);
                        setHeaderModuleSearch("");
                      }
                    }}
                    placeholder={selectorMode === "modules" ? (prettyModName(selectedModule) || "search modules...") : "search folders..."}
                    className="px-3 py-0.5 bg-transparent text-crt-green border border-crt-green/40 font-code outline-none w-[220px]"
                    style={{ letterSpacing: "0.01em", fontSize: "14px" }}
                  />
                  {/* Mode toggle: modules vs folders */}
                  <div className="flex ml-1 border border-crt-green/20 rounded overflow-hidden">
                    <button
                      onMouseDown={(e) => { e.preventDefault(); setSelectorMode("modules"); fetchModules(headerModuleSearch); }}
                      className={`text-[10px] px-2 py-1 font-code transition-colors ${selectorMode === "modules" ? "bg-crt-green/15 text-crt-green" : "text-crt-green/30 hover:text-crt-green/50"}`}
                    >MOD</button>
                    <button
                      onMouseDown={(e) => { e.preventDefault(); setSelectorMode("folders"); fetchFolders(headerModuleSearch); if (headerModuleSearch) fetchFolderSuggestions(headerModuleSearch); }}
                      className={`text-[10px] px-2 py-1 font-code transition-colors ${selectorMode === "folders" ? "bg-crt-blue/15 text-crt-blue" : "text-crt-green/30 hover:text-crt-green/50"}`}
                    >DIR</button>
                  </div>
                </div>
              ) : isMobile ? (
                <div className="flex items-center gap-0.5">
                  {/* Just the orbit mark (phone only — on desktop the address
                      bar owns the top-left corner and the mark is gone). */}
                  <span className="flex items-center" aria-label="orbit">
                    <ClaudeMark size={17} style={{ filter: "drop-shadow(0 0 5px color-mix(in srgb, var(--crt-amber, #fbbf24) 45%, transparent))" }} />
                  </span>
                </div>
              ) : null}
              {/* Modules dropdown */}
              {showHeaderModuleDropdown && isMobile && selectorMode === "modules" && moduleList.length > 0 && (() => {
                const owners = [...new Set(moduleList.map(m => m.owner).filter(Boolean))] as string[];
                // Substring-filtered (the server-side /modules?q= ranking can
                // be fuzzy, but the dropdown should only show names that
                // literally contain what you typed) and ranked with the
                // most-recently-opened modules first.
                const filtered = rankedHeaderModules(headerModuleSearch);
                const recentSet = new Set(recentModules);
                const firstNonRecentIdx = filtered.findIndex(m => !recentSet.has(m.name));
                return (
                <div
                  className="absolute left-0 top-full mt-1 border border-crt-green/20 max-h-[400px] overflow-y-auto z-[80] rounded min-w-[340px]"
                  style={{ background: "var(--bg-primary)", boxShadow: "0 12px 48px rgba(0,0,0,0.15)" }}
                >
                  {owners.length > 1 && (
                    <div className="px-3 py-2 border-b border-crt-green/20 flex flex-wrap gap-1.5 items-center sticky top-0 z-10" style={{ background: "var(--bg-primary)" }}>
                      <span className="text-[11px] text-crt-green/30 uppercase mr-1">owner:</span>
                      <button
                        onMouseDown={(e) => { e.preventDefault(); setOwnerFilter(null); }}
                        className={`text-[11px] px-2 py-0.5 border font-code transition-colors ${!ownerFilter ? "border-crt-green/50 text-crt-green bg-crt-green/10" : "border-crt-green/15 text-crt-green/30 hover:border-crt-green/30"}`}
                      >all</button>
                      {owners.map(o => (
                        <button
                          key={o}
                          onMouseDown={(e) => { e.preventDefault(); setOwnerFilter(ownerFilter === o ? null : o); }}
                          className={`text-[11px] px-2 py-0.5 border font-mono transition-colors ${ownerFilter === o ? "border-crt-blue/50 text-crt-blue bg-crt-blue/10" : "border-crt-green/15 text-crt-green/30 hover:border-crt-green/30"}`}
                          title={o}
                        >{o.slice(0, 6)}..{o.slice(-4)}</button>
                      ))}
                    </div>
                  )}
                  {filtered.length === 0 && (
                    <div className="px-3 py-2 text-[12px] text-crt-green/30 font-code">no modules match “{headerModuleSearch.trim()}”</div>
                  )}
                  {/* Keyed by name+path: the catalog holds duplicate names
                      (orbit/app vs core/app) and duplicate keys make React
                      leave stale rows behind when the query narrows. */}
                  {filtered.map((m, i) => (
                    <Fragment key={`${m.name}|${m.path || i}`}>
                    {i === 0 && recentSet.has(m.name) && (
                      <div className="px-3 py-1 border-b border-crt-green/10 text-[10px] text-crt-amber/50 uppercase font-code tracking-wider">recent</div>
                    )}
                    {i === firstNonRecentIdx && firstNonRecentIdx > 0 && (
                      <div className="px-3 py-1 border-b border-crt-green/10 text-[10px] text-crt-green/30 uppercase font-code tracking-wider">all modules</div>
                    )}
                    <div
                      onMouseDown={(e) => {
                        e.preventDefault();
                        resetModuleState(m);
                        setSelectedModule(m.name);
                        setSelectedModuleInfo(m);
                        setWorkDir(m.path);
                        setHeaderModuleSearch("");
                        setShowHeaderModuleDropdown(false);
                        setShowModuleDropdown(false);
                        fetchModuleConfig(m.name);
                      }}
                      className={`px-3 py-2 cursor-pointer hover:bg-crt-green/8 transition-colors border-b border-crt-green/5 ${m.name === selectedModule ? 'bg-crt-green/6' : ''}`}
                    >
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2 min-w-0">
                          {m.name === selectedModule && (
                            <span className="text-[10px] text-crt-green shrink-0">▸</span>
                          )}
                          <span className={`text-[13px] font-code truncate ${m.name === selectedModule ? 'text-crt-green font-bold' : 'text-crt-green/80'}`}>{m.name}</span>
                        </div>
                        <div className="flex items-center gap-1 shrink-0 ml-2">
                          {m.app_url && (
                            <span className="text-[9px] px-1 py-0.5 border border-crt-blue/30 text-crt-blue/60 rounded-sm">APP</span>
                          )}
                          {m.api_url && (
                            <span className="text-[9px] px-1 py-0.5 border border-crt-amber/30 text-crt-amber/60 rounded-sm">API</span>
                          )}
                        </div>
                      </div>
                      {m.description && (
                        <div className="text-[11px] text-crt-green/25 mt-0.5 truncate">{m.description}</div>
                      )}
                      <div className="flex items-center gap-2 mt-0.5">
                        {m.owner && (
                          <span className="text-[10px] font-mono text-crt-green/20" title={m.owner}>
                            {m.owner.slice(0, 6)}..{m.owner.slice(-4)}
                          </span>
                        )}
                        {m.cid && (
                          /* mousedown, not click: the row selects on mousedown */
                          <span
                            onMouseDown={(e) => { e.preventDefault(); e.stopPropagation(); copyCid(`dd:${m.path}`, m.cid!); }}
                            className="text-[10px] font-code shrink-0 cursor-pointer"
                            style={{ color: "var(--crt-purple, #c084fc)", opacity: copiedCid === `dd:${m.path}` ? 1 : 0.5 }}
                            title={`CID ${m.cid} — click to copy`}
                          >
                            {copiedCid === `dd:${m.path}` ? "✓ copied" : `⌬ ${m.cid.slice(0, 6)}..${m.cid.slice(-4)}`}
                          </span>
                        )}
                        {m.path && (
                          <span className="text-[10px] font-mono text-crt-green/15 truncate" title={m.path}>
                            {m.path.replace(/^.*\/mod\/orbit\//, "orbit/").replace(/^.*\/mod\//, "~/mod/")}
                          </span>
                        )}
                      </div>
                    </div>
                    </Fragment>
                  ))}
                </div>
                );
              })()}
              {/* Folders dropdown with embedding suggestions */}
              {showHeaderModuleDropdown && isMobile && selectorMode === "folders" && (folderList.length > 0 || folderSuggestions.length > 0) && (
                <div
                  className="absolute left-0 top-full mt-1 border border-crt-blue/20 max-h-[450px] overflow-y-auto z-[80] rounded min-w-[380px]"
                  style={{ background: "var(--bg-primary)", boxShadow: "0 12px 48px rgba(0,0,0,0.15)" }}
                >
                  {/* Embedding suggestions section */}
                  {folderSuggestions.length > 0 && (
                    <>
                      <div className="px-3 py-1.5 border-b border-crt-blue/20 sticky top-0 z-10" style={{ background: "var(--bg-primary)" }}>
                        <span className="text-[10px] text-crt-blue/50 uppercase font-code">Suggested by similarity</span>
                      </div>
                      {folderSuggestions.map((f) => (
                        <div
                          key={`suggest-${f.path}`}
                          onMouseDown={(e) => {
                            e.preventDefault();
                            setWorkDir(f.path);
                            const folderName = f.name.split("/").pop() || f.name;
                            setSelectedModule(folderName);
                            setSelectedModuleInfo(null);
                            setHeaderModuleSearch("");
                            setShowHeaderModuleDropdown(false);
                            // try to load config if it's a module
                            if (f.has_config || f.has_mod) fetchModuleConfig(folderName);
                          }}
                          className={`px-3 py-2 cursor-pointer hover:bg-crt-blue/8 transition-colors border-b border-crt-blue/5 ${f.path === workDir ? 'bg-crt-blue/6' : ''}`}
                        >
                          <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2 min-w-0">
                              <span className="text-[10px] text-crt-blue/40 shrink-0">◈</span>
                              <span className="text-[13px] font-code text-crt-blue/80 truncate">{f.name}</span>
                              <span className="text-[9px] px-1 py-0.5 border border-crt-blue/20 text-crt-blue/40 font-mono shrink-0">
                                {(f.score * 100).toFixed(0)}%
                              </span>
                            </div>
                            <div className="flex items-center gap-1 shrink-0 ml-2">
                              {f.has_config && (
                                <span className="text-[9px] px-1 py-0.5 border border-crt-amber/25 text-crt-amber/50 rounded-sm">CFG</span>
                              )}
                              {f.has_mod && (
                                <span className="text-[9px] px-1 py-0.5 border border-crt-green/25 text-crt-green/50 rounded-sm">MOD</span>
                              )}
                            </div>
                          </div>
                          {f.preview && (
                            <div className="text-[10px] text-crt-blue/20 mt-0.5 truncate font-mono">{f.preview}</div>
                          )}
                          <div className="text-[10px] font-mono text-crt-blue/15 mt-0.5 truncate" title={f.path}>
                            {f.display || f.path.replace(/^\/Users\/[^/]+/, "~")}
                          </div>
                        </div>
                      ))}
                    </>
                  )}
                  {/* Folder listing */}
                  {folderList.length > 0 && (
                    <>
                      <div className="px-3 py-1.5 border-b border-crt-green/20 sticky top-0 z-10" style={{ background: "var(--bg-primary)" }}>
                        <span className="text-[10px] text-crt-green/40 uppercase font-code">Folders</span>
                      </div>
                      {folderList.slice(0, 30).map((f) => (
                        <div
                          key={`folder-${f.path}`}
                          onMouseDown={(e) => {
                            e.preventDefault();
                            setWorkDir(f.path);
                            const folderName = f.name.split("/").pop() || f.name;
                            setSelectedModule(folderName);
                            setSelectedModuleInfo(null);
                            setHeaderModuleSearch("");
                            setShowHeaderModuleDropdown(false);
                            if (f.has_config || f.has_mod) fetchModuleConfig(folderName);
                          }}
                          className={`px-3 py-1.5 cursor-pointer hover:bg-crt-green/8 transition-colors border-b border-crt-green/5 ${f.path === workDir ? 'bg-crt-green/6' : ''}`}
                        >
                          <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2 min-w-0">
                              <span className="text-[10px] text-crt-green/30 shrink-0">▸</span>
                              <span className="text-[12px] font-code text-crt-green/70 truncate">{f.name}</span>
                            </div>
                            <div className="flex items-center gap-1 shrink-0 ml-2">
                              {f.has_config && (
                                <span className="text-[9px] px-1 py-0.5 border border-crt-amber/20 text-crt-amber/40 rounded-sm">CFG</span>
                              )}
                              {f.has_mod && (
                                <span className="text-[9px] px-1 py-0.5 border border-crt-green/20 text-crt-green/40 rounded-sm">MOD</span>
                              )}
                            </div>
                          </div>
                          <div className="text-[10px] font-mono text-crt-green/15 truncate" title={f.path}>
                            {f.display || f.path.replace(/^\/Users\/[^/]+/, "~")}
                          </div>
                        </div>
                      ))}
                    </>
                  )}
                  {folderList.length === 0 && folderSuggestions.length === 0 && (
                    <div className="px-3 py-4 text-[12px] text-crt-green/30 text-center font-code">
                      No folders found. Type to search.
                    </div>
                  )}
                </div>
              )}
            </div>

          </div>

          {/* Mod tabs moved OUT of this top row — they now render in the
              dedicated second header row below the nav bar (see the
              "Module tabs (second header row)" block after this header). */}

        </div>

        {/* ── Account controls. On desktop they sit inline at the right end
            of the single header row (no standalone top bar). On phone this
            becomes its own row ABOVE the module row (flex `order: 1`). The
            HUB/TASKS buttons live here on BOTH desktop and phone — the nav
            rail no longer carries them (its footer cluster is gone). */}
        <div
          className={`flex items-center py-1 ${isMobile ? "px-4" : "pl-2 pr-4 shrink-0"}`}
          style={isMobile ? { order: 1, borderBottom: `1px solid ${subtleBorder}` } : { order: 2 }}
        >
          <div className="flex items-center gap-0">
            {(() => {
              const hubActive = sidebarView === "hub";
              const tasksActive = sidebarView === "tasks";
              const runningCount = jobs.filter(j => j.status === "running" || j.status === "pending").length;
              const topTabs = [
                {
                  key: "hub",
                  label: "HUB",
                  icon: <HubIcon size={13} />,
                  color: "var(--crt-green)",
                  active: hubActive,
                  badge: null as number | null,
                  onClick: () => setSidebarView("hub"),
                },
                {
                  key: "tasks",
                  label: "TASKS",
                  icon: <TasksIcon size={13} />,
                  color: "var(--crt-blue)",
                  active: tasksActive,
                  badge: runningCount > 0 ? runningCount : null,
                  onClick: () => {
                    // Always land on the task LIST (running work first) — not
                    // whichever task happened to be open last.
                    setSelectedJob(null);
                    setStreamOutput("");
                    setSidebarView("tasks");
                  },
                },
              ];
              return topTabs.map((tab) => (
                <button
                  key={tab.key}
                  onClick={tab.onClick}
                  className="text-[12px] font-bold transition-all px-2.5 py-1 font-code flex items-center gap-1.5 relative"
                  style={{
                    letterSpacing: "0.02em",
                    color: tab.active ? tab.color : "var(--text-tertiary)",
                    opacity: tab.active ? 1 : 0.45,
                    borderBottom: tab.active ? `2px solid ${tab.color}` : "2px solid transparent",
                    background: tab.active ? `color-mix(in srgb, ${tab.color} 6%, transparent)` : "transparent",
                    marginBottom: "-1px",
                  }}
                  onMouseEnter={(e) => {
                    if (!tab.active) {
                      e.currentTarget.style.opacity = "0.75";
                      e.currentTarget.style.color = tab.color;
                    }
                  }}
                  onMouseLeave={(e) => {
                    if (!tab.active) {
                      e.currentTarget.style.opacity = "0.45";
                      e.currentTarget.style.color = "var(--text-tertiary)";
                    }
                  }}
                  title={tab.key === "tasks" ? "Open the full-page tasks view" : "Open the module hub"}
                >
                  <span className="flex items-center">{tab.icon}</span>
                  {tab.label}
                  {tab.badge != null && (
                    <span
                      className="text-[9px] font-mono px-1 py-[1px] rounded"
                      style={{
                        color: tab.color,
                        background: `color-mix(in srgb, ${tab.color} 14%, transparent)`,
                        border: `1px solid color-mix(in srgb, ${tab.color} 35%, transparent)`,
                      }}
                    >
                      {tab.badge}
                    </span>
                  )}
                </button>
              ));
            })()}
          </div>

          {/* Address chip (doubles as profile dropdown trigger) + Agent toggle */}
          <div className="flex items-center gap-1.5 shrink-0 ml-auto">
          {/* Wrench: BUILD / FORK / EDIT actions side panel.
              Lives on the RIGHT of the header. Clicking opens a
              tabbed panel anchored under the icon (right-aligned). */}
            <div className="relative flex items-center gap-1.5" ref={headerCreateRef}>
              {/* "+" opens the BUILD / FORK / EDIT / IMPORT panel. Always
                  shown — the rail no longer carries CREATE/EDIT actions. */}
              <button
                onClick={() => {
                  setCreateAnchor("header");
                  setShowHeaderCreateForm((f) => (f ? null : "create"));
                }}
                className="flex items-center justify-center transition-all hover:brightness-125 rounded-sm"
                style={{
                  width: 28,
                  height: 28,
                  border: `1px solid ${showHeaderCreateForm ? "var(--crt-green)" : "rgba(16,185,129,0.25)"}`,
                  color: showHeaderCreateForm ? "var(--crt-green)" : "rgba(16,185,129,0.55)",
                  background: showHeaderCreateForm ? "rgba(16,185,129,0.10)" : "transparent",
                  fontSize: 15,
                  lineHeight: 1,
                }}
                title="Build / fork / edit / import a module"
                aria-expanded={!!showHeaderCreateForm}
                aria-label="Build, fork, edit or import a module"
              >
                +
              </button>
              {/* The wrench that used to live here is gone — the edit view is
                  reached via the rail's bottom EDIT action. */}

              {showHeaderCreateForm && createAnchor === "header" && (
                <div
                  className="absolute right-0 top-full mt-1 border z-50 flex flex-col min-w-[320px]"
                  style={{
                    background: "var(--bg-primary)",
                    borderColor: showHeaderCreateForm === "fork"
                      ? "rgba(245,158,11,0.3)"
                      : showHeaderCreateForm === "edit"
                      ? "rgba(59,130,246,0.3)"
                      : showHeaderCreateForm === "import"
                      ? "rgba(34,211,238,0.3)"
                      : "rgba(16,185,129,0.3)",
                    boxShadow: "0 8px 32px rgba(0,0,0,0.20)",
                  }}
                >
                  {/* Tabs */}
                  <div className="flex" style={{ borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
                    {([
                      { key: "create" as const, label: "+ BUILD", color: "var(--crt-green)", rgb: "16,185,129" },
                      { key: "fork"   as const, label: "⑂ FORK",  color: "var(--crt-amber)", rgb: "245,158,11" },
                      { key: "edit"   as const, label: "✎ EDIT",  color: "var(--crt-blue)",  rgb: "59,130,246" },
                      { key: "import" as const, label: "⇩ IMPORT", color: "#22d3ee",         rgb: "34,211,238" },
                    ]).map((tab) => {
                      const isActive = showHeaderCreateForm === tab.key;
                      const disabled = tab.key === "edit" && !selectedModule;
                      return (
                        <button
                          key={tab.key}
                          onClick={() => {
                            if (disabled) return;
                            if (tab.key === "fork") {
                              setHeaderNewName(selectedModule ? selectedModule + "-fork" : "");
                              setHeaderGithubUrl("");
                            } else if (tab.key === "create") {
                              setHeaderNewName("");
                              setHeaderGithubUrl("");
                            } else if (tab.key === "import") {
                              setHeaderNewName("");
                              setHeaderGithubUrl("");
                              setHeaderCid("");
                            } else {
                              setHeaderEditPrompt("");
                            }
                            setShowHeaderCreateForm(tab.key);
                          }}
                          disabled={disabled}
                          className="flex-1 text-[11px] font-bold py-1.5 px-2 font-code transition-all disabled:cursor-not-allowed"
                          style={{
                            letterSpacing: "0.02em",
                            color: isActive ? tab.color : `rgba(${tab.rgb}, ${disabled ? 0.2 : 0.5})`,
                            background: isActive ? `rgba(${tab.rgb}, 0.08)` : "transparent",
                            borderBottom: isActive ? `2px solid ${tab.color}` : "2px solid transparent",
                          }}
                          title={disabled ? "Select a module to edit" : tab.label}
                        >
                          {tab.label}
                        </button>
                      );
                    })}
                  </div>

                  {/* Active form */}
                  <div className="p-3 flex flex-col gap-2">
                    <div className="text-[12px] font-bold uppercase" style={{
                      letterSpacing: "0.02em",
                      color: showHeaderCreateForm === "fork"
                        ? "var(--crt-amber)"
                        : showHeaderCreateForm === "edit"
                        ? "var(--crt-blue)"
                        : showHeaderCreateForm === "import"
                        ? "#22d3ee"
                        : "var(--crt-green)",
                    }}>
                      {showHeaderCreateForm === "fork"
                        ? `⑂ FORK FROM ${selectedModule?.toUpperCase() || "?"}`
                        : showHeaderCreateForm === "edit"
                        ? `✎ EDIT ${selectedModule?.toUpperCase() || "?"}`
                        : showHeaderCreateForm === "import"
                        ? "⇩ IMPORT MODULE"
                        : "+ BUILD MODULE"}
                    </div>
                    {showHeaderCreateForm === "edit" ? (
                      <textarea
                        autoFocus
                        value={headerEditPrompt}
                        onChange={(e) => setHeaderEditPrompt(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) headerCreateOrFork();
                          if (e.key === "Escape") setShowHeaderCreateForm(null);
                        }}
                        placeholder="describe the edit..."
                        rows={3}
                        className="px-2 py-1.5 text-[14px] bg-transparent border font-code outline-none resize-none"
                        style={{
                          borderColor: "rgba(59,130,246,0.3)",
                          color: "var(--text-primary)",
                        }}
                      />
                    ) : showHeaderCreateForm === "import" ? (
                      <>
                        {/* Source toggle — clone a git repo or restore a snapshot CID */}
                        <div className="flex gap-1">
                          {(["github", "cid"] as const).map((s) => (
                            <button
                              key={s}
                              onClick={() => setHeaderImportSource(s)}
                              className="flex-1 text-[11px] font-bold py-1 px-2 font-code border transition-all"
                              style={{
                                letterSpacing: "0.02em",
                                color: headerImportSource === s ? "#22d3ee" : "rgba(34,211,238,0.45)",
                                borderColor: headerImportSource === s ? "rgba(34,211,238,0.5)" : "rgba(34,211,238,0.15)",
                                background: headerImportSource === s ? "rgba(34,211,238,0.08)" : "transparent",
                              }}
                            >
                              {s === "github" ? "GITHUB" : "CID"}
                            </button>
                          ))}
                        </div>
                        {headerImportSource === "github" ? (
                          <input
                            type="text"
                            autoFocus
                            value={headerGithubUrl}
                            onChange={(e) => setHeaderGithubUrl(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") headerCreateOrFork();
                              if (e.key === "Escape") setShowHeaderCreateForm(null);
                            }}
                            placeholder="https://github.com/user/repo.git"
                            className="px-2 py-1.5 text-[14px] bg-transparent border font-code outline-none"
                            style={{ borderColor: "rgba(34,211,238,0.3)", color: "var(--text-primary)" }}
                          />
                        ) : (
                          <input
                            type="text"
                            autoFocus
                            value={headerCid}
                            onChange={(e) => setHeaderCid(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") headerCreateOrFork();
                              if (e.key === "Escape") setShowHeaderCreateForm(null);
                            }}
                            placeholder="snapshot cid..."
                            className="px-2 py-1.5 text-[14px] bg-transparent border font-code outline-none"
                            style={{ borderColor: "rgba(34,211,238,0.3)", color: "var(--text-primary)" }}
                          />
                        )}
                        <input
                          type="text"
                          value={headerNewName}
                          onChange={(e) => setHeaderNewName(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") headerCreateOrFork();
                            if (e.key === "Escape") setShowHeaderCreateForm(null);
                          }}
                          placeholder={
                            headerImportSource === "github"
                              ? (deriveNameFromUrl(headerGithubUrl) ? `name: ${deriveNameFromUrl(headerGithubUrl)} (auto)` : "module name (auto from url)...")
                              : "module name..."
                          }
                          className="px-2 py-1.5 text-[14px] bg-transparent border font-code outline-none"
                          style={{ borderColor: "rgba(34,211,238,0.2)", color: "var(--text-primary)" }}
                        />
                      </>
                    ) : (
                      <input
                        type="text"
                        autoFocus
                        value={headerNewName}
                        onChange={(e) => setHeaderNewName(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") headerCreateOrFork();
                          if (e.key === "Escape") setShowHeaderCreateForm(null);
                        }}
                        placeholder="module name..."
                        className="px-2 py-1.5 text-[14px] bg-transparent border font-code outline-none"
                        style={{
                          borderColor: showHeaderCreateForm === "fork" ? "rgba(245,158,11,0.3)" : "rgba(16,185,129,0.3)",
                          color: "var(--text-primary)",
                        }}
                      />
                    )}
                    {showHeaderCreateForm === "create" && (
                      <input
                        type="text"
                        value={headerGithubUrl}
                        onChange={(e) => setHeaderGithubUrl(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") headerCreateOrFork();
                          if (e.key === "Escape") setShowHeaderCreateForm(null);
                        }}
                        placeholder="github url (optional)..."
                        className="px-2 py-1.5 text-[14px] bg-transparent border border-crt-green/20 font-code outline-none"
                        style={{ color: "var(--text-primary)" }}
                      />
                    )}
                    {/* Model selector — drives the same shared model state the
                        composer uses, so EDIT/FORK/BUILD jobs run on the model
                        picked here (previously this panel had no picker and
                        silently inherited the composer's choice). IMPORT is a
                        deterministic clone/restore — no agent, no model. */}
                    {showHeaderCreateForm !== "import" && (
                    <select
                      value={model}
                      onChange={(e) => {
                        setModel(e.target.value);
                        safeSetItem("claude_jobs_model", e.target.value);
                      }}
                      className="px-2 py-1 text-[12px] bg-transparent text-crt-green border border-crt-green/20 font-code uppercase cursor-pointer hover:border-crt-green/40 transition-colors self-start"
                      title={`Model: ${modelLabel(model)} (${model})`}
                    >
                      {MODEL_OPTIONS.map(m => (
                        <option key={m.value} value={m.value} style={{ background: "var(--bg-primary)", color: "var(--text-primary)" }}>
                          {m.label}
                        </option>
                      ))}
                    </select>
                    )}
                    <div className="flex items-center gap-2">
                      <button
                        onClick={headerCreateOrFork}
                        disabled={(showHeaderCreateForm === "edit"
                          ? !headerEditPrompt.trim()
                          : showHeaderCreateForm === "import"
                          ? (headerImportSource === "github" ? !headerGithubUrl.trim() : !headerCid.trim())
                          : !headerNewName.trim()) || submitting}
                        className="pixel-btn text-[14px] py-1 px-4 uppercase flex-1"
                        style={{
                          letterSpacing: "0.01em",
                          opacity: (showHeaderCreateForm === "edit"
                            ? headerEditPrompt.trim()
                            : showHeaderCreateForm === "import"
                            ? (headerImportSource === "github" ? headerGithubUrl.trim() : headerCid.trim())
                            : headerNewName.trim()) ? 1 : 0.4,
                        }}
                      >
                        {submitting
                          ? "..."
                          : showHeaderCreateForm === "fork"
                          ? "FORK"
                          : showHeaderCreateForm === "edit"
                          ? "EDIT"
                          : showHeaderCreateForm === "import"
                          ? "IMPORT"
                          : "BUILD"}
                      </button>
                      <button
                        onClick={() => setShowHeaderCreateForm(null)}
                        className="text-[14px] px-2 py-1 border border-crt-red/20 text-crt-red/50 hover:text-crt-red hover:border-crt-red/40 transition-all"
                      >
                        ESC
                      </button>
                    </div>
                  </div>
                </div>
              )}
            </div>
            {/* Single identity chip — owner + user are one panel. The module
                owner's identity (for non-owner viewers) lives inside the
                profile dropdown below, so the top-right corner never shows a
                separate OWNER chip alongside the user chip. */}
            {/* User profile chip — when a wallet is connected, clicking
                pops out the full wallet sidebar (mirrors the AGENT
                toggle pattern). For local/no-wallet sessions it opens
                the small profile dropdown instead. */}
            <div ref={profileMenuRef} className="relative">
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  // No session yet — this chip reads "SIGN IN" and toggles the
                  // connect-wallet drawer.
                  if (!token) {
                    setSignInOpen(o => !o);
                    return;
                  }
                  // Unified entry point for owner AND user: one click opens the
                  // merged account panel already expanded — no intermediate
                  // dropdown (its actions all live inside the panel now).
                  setShowOwnerSidebar(o => !o);
                }}
                className="text-[12px] font-bold font-mono px-2 py-1 transition-all flex items-center gap-1.5"
                style={isOwner ? {
                  color: "var(--crt-green)",
                  background: showOwnerSidebar
                    ? "color-mix(in srgb, var(--crt-green) 18%, transparent)"
                    : "color-mix(in srgb, var(--crt-green) 8%, transparent)",
                  border: "1px solid color-mix(in srgb, var(--crt-green) 30%, transparent)",
                  borderRadius: 4,
                  letterSpacing: "0.02em",
                } : {
                  color: "var(--crt-green)",
                  opacity: showOwnerSidebar ? 1 : 0.5,
                  borderRadius: 4,
                  background: showOwnerSidebar ? "color-mix(in srgb, var(--crt-green) 8%, transparent)" : "transparent",
                }}
                title={token
                  ? (showOwnerSidebar ? "Close account panel" : "Open account panel")
                  : "Sign in"}
                aria-expanded={showOwnerSidebar}
              >
                {isOwner && (
                  <span
                    className="shrink-0 text-[13px] leading-none"
                    title="Owner"
                    style={{ color: "var(--crt-amber)", textShadow: "0 0 6px color-mix(in srgb, var(--crt-amber) 60%, transparent)" }}
                  >
                    ♛
                  </span>
                )}
                {address ? (address === "local" ? "LOCAL" : `${address.slice(0, 6)}··${address.slice(-4)}`) : "SIGN IN"}
                <span className="text-[9px]" style={{ opacity: 0.6 }}>
                  {showOwnerSidebar ? "◨" : "◧"}
                </span>
              </button>
              {profileMenuOpen && (
                <div
                  className="absolute right-0 mt-1.5 flex flex-col rounded-md z-50 profile-menu-expand-left"
                  style={{
                    minWidth: 280,
                    background: "var(--bg-primary)",
                    border: "1px solid var(--border-color)",
                    boxShadow: "0 8px 24px rgba(0,0,0,0.35)",
                    overflow: "hidden",
                    transformOrigin: "top right",
                  }}
                  role="menu"
                >
                  {/* Header */}
                  <div
                    className="flex items-center gap-2.5 px-3 py-2.5"
                    style={{
                      borderBottom: "1px solid var(--border-color)",
                      background: "color-mix(in srgb, var(--crt-green) 4%, transparent)",
                    }}
                  >
                    <span
                      className="shrink-0 inline-block rounded-full"
                      style={{
                        width: 8, height: 8,
                        background: "var(--crt-green)",
                        boxShadow: "0 0 8px var(--crt-green)",
                      }}
                    />
                    <div className="flex flex-col min-w-0 flex-1">
                      <span className="text-[10px] uppercase tracking-[0.12em]" style={{ color: "var(--text-tertiary)" }}>
                        {walletType === "metamask" ? "MetaMask"
                          : walletType === "subwallet" ? "SubWallet"
                          : walletType === "password" ? "Password Key"
                          : walletType ? walletType : address ? "Connected" : "Not signed in"}
                      </span>
                      <span className="text-[11px] font-mono truncate" style={{ color: "var(--text-secondary)" }}>
                        {address || "—"}
                      </span>
                    </div>
                    {isOwner && (
                      <span
                        className="text-[12px] leading-none px-1.5 py-0.5 rounded shrink-0"
                        title="Owner"
                        style={{
                          color: "var(--crt-amber)",
                          background: "color-mix(in srgb, var(--crt-amber) 10%, transparent)",
                          border: "1px solid color-mix(in srgb, var(--crt-amber) 35%, transparent)",
                        }}
                      >
                        ♛
                      </span>
                    )}
                  </div>

                  {/* Address copy */}
                  {address && (
                    <button
                      onClick={() => {
                        navigator.clipboard?.writeText(address).catch(() => {});
                        setCopiedAddress(true);
                        setTimeout(() => setCopiedAddress(false), 1500);
                      }}
                      className="flex items-center justify-between px-3 py-2 text-[11px] transition-colors"
                      style={{ color: "var(--text-secondary)", borderBottom: "1px solid var(--border-color)" }}
                      onMouseEnter={e => (e.currentTarget.style.background = "var(--bg-secondary)")}
                      onMouseLeave={e => (e.currentTarget.style.background = "transparent")}
                    >
                      <span className="font-mono truncate mr-2">{address}</span>
                      <span className="text-[10px] uppercase tracking-[0.1em] shrink-0" style={{ color: copiedAddress ? "var(--crt-green)" : "var(--text-tertiary)" }}>
                        {copiedAddress ? "copied" : "copy"}
                      </span>
                    </button>
                  )}

                  {/* Actions */}
                  {address && address !== "local" && walletType && (
                    <button
                      onClick={() => {
                        setProfileMenuOpen(false);
                        setAccountTab("wallet");
                        setShowOwnerSidebar(true);
                      }}
                      className="flex items-center justify-between px-3 py-2 text-[11px] transition-colors"
                      style={{ color: "var(--text-secondary)", borderBottom: "1px solid var(--border-color)" }}
                      onMouseEnter={e => (e.currentTarget.style.background = "var(--bg-secondary)")}
                      onMouseLeave={e => (e.currentTarget.style.background = "transparent")}
                    >
                      <span>Open wallet panel</span>
                      <span className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>↗</span>
                    </button>
                  )}
                  <button
                    onClick={() => {
                      setProfileMenuOpen(false);
                      setShowOwnerSidebar(v => !v);
                    }}
                    className="flex items-center justify-between px-3 py-2 text-[11px] transition-colors"
                    style={{ color: "var(--text-secondary)", borderBottom: "1px solid var(--border-color)" }}
                    onMouseEnter={e => (e.currentTarget.style.background = "var(--bg-secondary)")}
                    onMouseLeave={e => (e.currentTarget.style.background = "transparent")}
                  >
                    <span className="flex flex-col min-w-0">
                      <span className="flex items-center gap-2">
                        <span style={{ color: "var(--crt-amber)" }}>◈</span>
                        {showOwnerSidebar ? "Close owner panel" : "Open owner panel"}
                      </span>
                      {/* For non-owner viewers, show the module owner address
                          here so the identity from the old standalone chip
                          isn't lost now that everything lives in one panel. */}
                      {!isOwner && effectiveConfig?.owner && (
                        <span className="font-mono text-[10px] mt-0.5 ml-[1.1rem]" style={{ color: "var(--text-tertiary)" }}>
                          {`${effectiveConfig.owner.slice(0, 6)}··${effectiveConfig.owner.slice(-4)}`}
                        </span>
                      )}
                    </span>
                    <span className="text-[10px] shrink-0" style={{ color: "var(--text-tertiary)" }}>{showOwnerSidebar ? "✕" : "↗"}</span>
                  </button>
                  {address ? (
                    <button
                      onClick={() => {
                        setProfileMenuOpen(false);
                        disconnect();
                      }}
                      className="px-3 py-2 text-[11px] text-left transition-colors"
                      style={{ color: "var(--crt-red)" }}
                      onMouseEnter={e => (e.currentTarget.style.background = "color-mix(in srgb, var(--crt-red) 8%, transparent)")}
                      onMouseLeave={e => (e.currentTarget.style.background = "transparent")}
                    >
                      Sign out
                    </button>
                  ) : (
                    <div className="px-3 py-2 text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                      No session — connect a wallet or use a local key
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* ── Module tabs (second header row) ─────────────────────────
          The APP / CODE / API / OVERVIEW tabs, moved out of the top nav
          bar into their own row directly beneath it. Left padding tracks
          the nav rail so the tabs align under the module name above. */}
      <div
        className="flex items-center gap-0 flex-wrap nav-tabs-mobile-scroll"
        style={{
          background: "var(--bg-secondary)",
          borderBottom: `1px solid ${subtleBorder}`,
          paddingLeft: 16,
          paddingRight: 16,
        }}
      >
          {([
            // APP leads — the live module interface is the first thing you see.
            // Shown whenever the module exposes an app.
            ...((selectedModuleInfo?.app_url || selectedModuleInfo?.has_app_dir)
              ? [{
                  key: "app" as const,
                  label: "APP",
                  icon: <AppIcon size={13} />,
                  color: "var(--crt-green)",
                  activeKeys: ["app", "api"],
                  target: "app",
                }]
              : []),
            // CODE merges the file browser and the version history into one
            // tab (the sub-toggle inside picks between Files and Versions).
            { key: "files" as const, label: "CODE", icon: <CodeIcon size={13} />, color: "var(--text-primary)" },
            // API — docs (human/engineer), endpoint schema with auth levels,
            // an interactive playground, and the module's process logs.
            { key: "apitab" as const, label: "API", icon: <ApiIcon size={13} />, color: "var(--crt-blue)" },
            // OVERVIEW is the module's profile/chrome — kept last now that the
            // app leads. (The EDIT view moved out of the tab row: it's the
            // rail's EDIT button.)
            { key: "overview" as const, label: "OVERVIEW", icon: <OverviewIcon size={13} />, color: "var(--crt-amber)" },
          ]).map((tab) => {
            const t = tab as any;
            const isActive = t.activeKeys ? t.activeKeys.includes(sidebarView) : sidebarView === tab.key;
            return (
              <button
                key={tab.key}
                onClick={() => setSidebarView(t.target || tab.key)}
                className="text-[12px] font-bold transition-all px-2.5 py-1 font-code flex items-center gap-1.5 relative"
                style={{
                  letterSpacing: "0.02em",
                  color: isActive ? tab.color : "var(--text-tertiary)",
                  opacity: isActive ? 1 : 0.4,
                  borderBottom: isActive ? `2px solid ${tab.color}` : "2px solid transparent",
                  background: isActive ? `color-mix(in srgb, ${tab.color} 6%, transparent)` : "transparent",
                  marginBottom: "-1px",
                }}
                onMouseEnter={(e) => {
                  if (!isActive) {
                    e.currentTarget.style.opacity = "0.7";
                    e.currentTarget.style.color = tab.color;
                  }
                }}
                onMouseLeave={(e) => {
                  if (!isActive) {
                    e.currentTarget.style.opacity = "0.4";
                    e.currentTarget.style.color = "var(--text-tertiary)";
                  }
                }}
              >
                <span className="flex items-center">{tab.icon}</span>
                {tab.label}
              </button>
            );
          })}
          {/* Owner bubble — the selected module's configured owner, pinned to
              the right end of the tabs row. Green + "YOU" when it's the
              signed-in wallet; click copies the full address. */}
          {(() => {
            const modOwner = selectedModuleInfo?.owner || moduleConfig?.owner || null;
            if (!modOwner) return null;
            const mine = !!address && address !== "local" && address.toLowerCase() === String(modOwner).toLowerCase();
            const short = modOwner.length > 12 ? `${modOwner.slice(0, 6)}…${modOwner.slice(-4)}` : modOwner;
            return (
              <button
                onClick={() => {
                  navigator.clipboard?.writeText(modOwner).then(() => {
                    setOwnerBubbleCopied(true);
                    setTimeout(() => setOwnerBubbleCopied(false), 1200);
                  }).catch(() => {});
                }}
                className="ml-auto my-0.5 flex items-center gap-1.5 text-[9px] font-bold font-code uppercase px-2.5 py-[3px] rounded-full transition-colors"
                style={mine ? {
                  color: "var(--crt-green)",
                  border: "1px solid rgba(52,211,153,0.35)",
                  background: "rgba(52,211,153,0.08)",
                  letterSpacing: "0.06em",
                } : {
                  color: "var(--text-tertiary)",
                  border: `1px solid ${subtleBorder}`,
                  background: "transparent",
                  letterSpacing: "0.06em",
                }}
                title={`Owner: ${modOwner}${mine ? " (you)" : ""} — click to copy`}
              >
                <span style={{ opacity: 0.6 }}>owner</span>
                <span className="font-mono normal-case">
                  {ownerBubbleCopied ? "copied ✓" : short}
                </span>
                {mine && <span>· YOU</span>}
              </button>
            );
          })()}
      </div>
      </div>


      {error && (
        <div
          className="mr-4 mt-2 p-3 border-2 border-crt-red/50"
          style={{
            background: "rgba(239,68,68,0.05)",
            marginLeft: !isMobile ? 16 : 16,
          }}
        >
          <div className="text-[14px] text-crt-red flex items-center gap-2">
            <span>⚠</span> {error}
          </div>
        </div>
      )}

      <div className="flex-1 flex flex-row overflow-hidden relative">

        {/* ── Recents rail (desktop) — a slim vertical tab on the far left
            that expands into a drag-resizable quick-switch list of the
            modules you opened most recently. Collapsed it's a 22px reopen
            strip, so it never eats space you didn't ask for. */}
        {!isMobile && (
          recentsRailOpen ? (
            <div
              className="flex flex-col overflow-hidden shrink-0 relative"
              style={{
                order: 0,
                width: recentsRailWidth,
                background: "var(--bg-secondary, var(--bg-primary))",
                borderRight: `1px solid ${subtleBorder}`,
              }}
            >
              {/* Drag handle on the rail/content divider */}
              <div
                onMouseDown={(e) => { e.preventDefault(); setIsRecentsRailDragging(true); }}
                onDoubleClick={() => setRecentsRailWidth(208)}
                className="absolute top-0 bottom-0 right-0 z-10"
                style={{ width: 5, cursor: "col-resize", background: isRecentsRailDragging ? "color-mix(in srgb, var(--crt-green) 30%, transparent)" : "transparent", transition: "background 0.15s ease" }}
                onMouseEnter={(e) => { if (!isRecentsRailDragging) e.currentTarget.style.background = "color-mix(in srgb, var(--crt-green) 22%, transparent)"; }}
                onMouseLeave={(e) => { if (!isRecentsRailDragging) e.currentTarget.style.background = "transparent"; }}
                title="Drag to resize (double-click to reset)"
              />
              <div
                className="flex items-center gap-2 px-3 py-1.5 shrink-0"
                style={{ borderBottom: `1px solid ${subtleBorder}`, background: tintBg }}
              >
                <span className="text-[10px] font-bold font-code uppercase" style={{ color: "var(--text-tertiary)", letterSpacing: "0.16em" }}>
                  Recent
                </span>
                {recentModules.length > 0 && (
                  <span className="text-[10px] font-code" style={{ color: "var(--text-tertiary)", opacity: 0.6 }}>
                    {[...new Set(recentModules)].length}
                  </span>
                )}
                {railTasks.running > 0 && (
                  <span
                    className="led-pulse flex items-center gap-1 px-1.5 py-[1px] rounded-full font-code text-[9px] font-bold uppercase"
                    style={{
                      color: "var(--crt-green)",
                      border: "1px solid color-mix(in srgb, var(--crt-green) 45%, transparent)",
                      background: "color-mix(in srgb, var(--crt-green) 10%, transparent)",
                      boxShadow: "0 0 10px -3px var(--crt-green)",
                      letterSpacing: "0.1em",
                    }}
                    title={`${railTasks.running} task${railTasks.running === 1 ? "" : "s"} running${railTasks.pending ? ` · ${railTasks.pending} queued` : ""}`}
                  >
                    {railTasks.running} live
                  </span>
                )}
                {railTasks.running === 0 && railTasks.pending > 0 && (
                  <span
                    className="led-pulse px-1.5 py-[1px] rounded-full font-code text-[9px] font-bold"
                    style={{
                      color: "var(--crt-amber)",
                      border: "1px solid color-mix(in srgb, var(--crt-amber) 35%, transparent)",
                      background: "color-mix(in srgb, var(--crt-amber) 8%, transparent)",
                    }}
                    title={`${railTasks.pending} task${railTasks.pending === 1 ? "" : "s"} queued`}
                  >
                    +{railTasks.pending}
                  </span>
                )}
                {(railTasks.completed > 0 || railTasks.failed > 0) && (
                  <span className="font-code text-[9px] font-bold flex items-center gap-1" title={`${railTasks.completed} done · ${railTasks.failed} failed (recent tasks)`}>
                    {railTasks.completed > 0 && <span style={{ color: "var(--crt-green)", opacity: 0.7 }}>✓{railTasks.completed}</span>}
                    {railTasks.failed > 0 && <span style={{ color: "var(--crt-red, #ef4444)", opacity: 0.85 }}>✕{railTasks.failed}</span>}
                  </span>
                )}
                <button
                  onClick={() => setRecentsRailOpen(false)}
                  className="ml-auto text-[11px] transition-opacity opacity-40 hover:opacity-100"
                  style={{ color: "var(--text-secondary, var(--text-tertiary))", lineHeight: 1 }}
                  title="Collapse the recents rail"
                  aria-label="Collapse the recents rail"
                >
                  «
                </button>
              </div>
              <div className="flex-1 overflow-y-auto px-2 py-1.5 flex flex-col gap-0.5">
                {[...new Set(recentModules)].length > 0 ? (
                  [...new Set(recentModules)].map((name) => {
                    const info = moduleList.find((m) => m.name === name);
                    const st = moduleStatuses[name];
                    const live = st ? (st.app === true || st.api === true ? true : (st.app === false || st.api === false ? false : null)) : null;
                    const active = name === selectedModule && sidebarView !== "hub" && sidebarView !== "tasks";
                    const tasks = railTasks.byModule[name];
                    const hasRunning = !!tasks && tasks.running > 0;
                    const hasQueued = !!tasks && tasks.pending > 0;
                    const hasDone = !!tasks && tasks.completed > 0;
                    const hasFailed = !!tasks && tasks.failed > 0;
                    const taskTitle = tasks
                      ? [
                          [
                            tasks.running ? `${tasks.running} running` : "",
                            tasks.pending ? `${tasks.pending} queued` : "",
                            tasks.completed ? `${tasks.completed} done` : "",
                            tasks.failed ? `${tasks.failed} failed` : "",
                          ].filter(Boolean).join(" · "),
                          ...tasks.prompts,
                        ].join("\n")
                      : undefined;
                    return (
                      <button
                        key={`recent-${name}`}
                        onClick={() => {
                          if (info) selectModule(info);
                          else setSelectedModule(name);
                        }}
                        className="flex items-center gap-2 px-2.5 py-1.5 rounded-md font-code text-[12px] transition-all text-left w-full"
                        style={{
                          color: active ? "var(--accent-color)" : "var(--text-secondary, var(--text-tertiary))",
                          background: active ? "color-mix(in srgb, var(--accent-color) 12%, transparent)" : "transparent",
                          border: `1px solid ${active ? "color-mix(in srgb, var(--accent-color) 35%, transparent)" : "transparent"}`,
                        }}
                        onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = "color-mix(in srgb, var(--accent-color) 7%, transparent)"; }}
                        onMouseLeave={(e) => { if (!active) e.currentTarget.style.background = "transparent"; }}
                        title={info?.description || name}
                      >
                        <span
                          className="shrink-0"
                          style={{
                            width: 7, height: 7, borderRadius: 999,
                            background: live === true ? "var(--crt-green)" : live === false ? "color-mix(in srgb, var(--crt-red, #ef4444) 70%, transparent)" : "var(--text-tertiary)",
                            opacity: live === null ? 0.4 : 1,
                            boxShadow: live === true ? "0 0 6px var(--crt-green)" : "none",
                          }}
                        />
                        <span className="truncate flex-1 min-w-0">{name}</span>
                        {hasRunning && (
                          <span
                            className="shrink-0 flex items-center gap-1 px-1.5 py-[1px] rounded-full font-code"
                            style={{
                              color: "var(--crt-green)",
                              border: "1px solid color-mix(in srgb, var(--crt-green) 40%, transparent)",
                              background: "color-mix(in srgb, var(--crt-green) 10%, transparent)",
                              boxShadow: "0 0 10px -2px var(--crt-green)",
                            }}
                            title={taskTitle}
                          >
                            <span
                              className="animate-spin shrink-0"
                              style={{
                                width: 8, height: 8, borderRadius: 999,
                                border: "1.5px solid color-mix(in srgb, var(--crt-green) 25%, transparent)",
                                borderTopColor: "var(--crt-green)",
                              }}
                            />
                            <span className="text-[10px] font-bold leading-none">{tasks.running}</span>
                            {hasQueued && (
                              <span className="text-[9px] leading-none" style={{ opacity: 0.6 }}>+{tasks.pending}</span>
                            )}
                          </span>
                        )}
                        {!hasRunning && hasQueued && (
                          <span
                            className="led-pulse shrink-0 px-1.5 py-[1px] rounded-full font-code text-[10px] leading-none"
                            style={{
                              color: "var(--crt-amber)",
                              border: "1px solid color-mix(in srgb, var(--crt-amber) 35%, transparent)",
                              background: "color-mix(in srgb, var(--crt-amber) 8%, transparent)",
                            }}
                            title={taskTitle}
                          >
                            +{tasks.pending}
                          </span>
                        )}
                        {hasDone && (
                          <span
                            className="shrink-0 px-1 py-[1px] rounded-full font-code text-[10px] leading-none"
                            style={{ color: "var(--crt-green)", opacity: 0.75 }}
                            title={taskTitle}
                          >
                            ✓{tasks.completed}
                          </span>
                        )}
                        {hasFailed && (
                          <span
                            className="shrink-0 px-1 py-[1px] rounded-full font-code text-[10px] font-bold leading-none"
                            style={{
                              color: "var(--crt-red, #ef4444)",
                              border: "1px solid color-mix(in srgb, var(--crt-red, #ef4444) 35%, transparent)",
                              background: "color-mix(in srgb, var(--crt-red, #ef4444) 8%, transparent)",
                            }}
                            title={taskTitle}
                          >
                            ✕{tasks.failed}
                          </span>
                        )}
                      </button>
                    );
                  })
                ) : (
                  <div className="px-2 py-3 text-[11px] font-code" style={{ color: "var(--text-tertiary)", opacity: 0.7 }}>
                    Modules you open show up here.
                  </div>
                )}
              </div>
            </div>
          ) : (
            // Collapsed: a thin reopen tab hugging the left edge.
            <button
              onClick={() => {
                setRecentsRailOpen(true);
                if (!moduleList.length) fetchModules("");
              }}
              className="flex items-center justify-center transition-all hover:brightness-125 shrink-0"
              style={{
                order: 0,
                width: 22,
                background: "var(--bg-secondary, var(--bg-primary))",
                borderRight: `1px solid ${subtleBorder}`,
                color: "var(--text-tertiary)",
                writingMode: "vertical-rl",
                fontSize: 10,
                letterSpacing: "0.18em",
                fontFamily: "var(--font-code, monospace)",
              }}
              title={railTasks.running > 0 ? `Open the recents rail — ${railTasks.running} task${railTasks.running === 1 ? "" : "s"} running` : "Open the recents rail"}
              aria-label="Open the recents rail"
            >
              RECENT »
              {railTasks.running > 0 && (
                <span
                  className="led-pulse font-bold"
                  style={{ color: "var(--crt-green)", textShadow: "0 0 8px var(--crt-green)", marginTop: 8 }}
                >
                  ●{railTasks.running}
                </span>
              )}
            </button>
          )
        )}

        {/* ── Main Content ──────────────────────── */}
        <div
          className="flex-1 flex flex-col overflow-hidden min-w-0"
          style={{ background: "var(--bg-primary)", order: 1 }}
        >
            {sidebarView === "hub" ? (
              renderHubView()
            ) : sidebarView === "tasks" ? (
              <div className="flex-1 flex flex-col overflow-hidden">
                {renderAgentTab()}
              </div>
            ) : sidebarView === "overview" ? (
              <div className="flex-1 flex flex-col overflow-hidden">
                {renderProfileTab()}
              </div>
            ) : (sidebarView === "api" || sidebarView === "app") ? (
              <div className="flex-1 flex flex-col overflow-hidden">
                {renderAppApiTab()}
              </div>
            ) : sidebarView === "apitab" ? (
              <div className="flex-1 flex flex-col overflow-hidden">
                {renderClaudeApiTab()}
              </div>
            ) : sidebarView === "logs" ? (
              <div className="flex-1 flex flex-col overflow-hidden">
                {renderLogsTab()}
              </div>
            ) : sidebarView === "files" ? (
              filesPanelFloating ? (
                <div className="flex-1 flex flex-col overflow-hidden">
                  {renderProfileTab()}
                </div>
              ) : (
                <div className="flex-1 flex flex-col overflow-hidden">
                  {/* Sub-toggle: file browser vs. version history, both folded
                      into the merged CODE tab. */}
                  <div
                    className="flex items-center gap-1 px-3 py-1.5 shrink-0"
                    style={{ borderBottom: `1px solid ${subtleBorder}`, background: tintBg }}
                  >
                    {([
                      { k: "files" as const, label: "Files", icon: <FilesIcon size={13} /> },
                      { k: "versions" as const, label: "Versions", icon: <VersionsIcon size={13} /> },
                    ]).map((s) => {
                      const on = codeView === s.k;
                      return (
                        <button
                          key={s.k}
                          onClick={() => setCodeView(s.k)}
                          className="text-[13px] px-2.5 py-1 border transition-all flex items-center gap-1.5 font-code uppercase"
                          style={{
                            letterSpacing: "0.02em",
                            color: on ? "var(--text-primary)" : "var(--text-tertiary)",
                            borderColor: on ? "var(--crt-green)" : "transparent",
                            background: on ? "color-mix(in srgb, var(--crt-green) 8%, transparent)" : "transparent",
                            opacity: on ? 1 : 0.5,
                          }}
                        >
                          <span className="flex items-center">{s.icon}</span>
                          {s.label}
                          {s.k === "versions" && agentVersions.length > 0 && (
                            <span className="text-[11px] opacity-60">{agentVersions.length}</span>
                          )}
                        </button>
                      );
                    })}
                    {codeView === "files" && (
                      <>
                        <span
                          className="flex-1 min-w-0 truncate text-right font-code text-[12px]"
                          style={{ color: "var(--text-tertiary)", opacity: 0.55 }}
                          title={filesDisplayPath()}
                        >
                          {filesDisplayPath()}
                        </span>
                        {filesToolbarControls()}
                      </>
                    )}
                  </div>
                  {codeView === "versions" ? (
                    selectedModule ? (
                      <VersionsPanel
                        apiBase={apiUrl}
                        module={selectedModule}
                        authHeader={token ? { Authorization: `Bearer ${token}` } : undefined}
                        onForked={(m) => setSelectedModule(m)}
                      />
                    ) : (
                      <div className="flex flex-col items-center justify-center h-full gap-3 p-6">
                        <span className="text-[48px]" style={{ color: "var(--crt-purple, #c084fc)", opacity: 0.15 }}>⌬</span>
                        <span className="text-[14px] uppercase" style={{ color: "var(--text-tertiary)", letterSpacing: "0.02em" }}>
                          No module selected
                        </span>
                        <p className="text-[13px] text-center max-w-xs" style={{ color: "var(--text-tertiary)", opacity: 0.7 }}>
                          Pick a module to see its snapshot history and on-chain registry CIDs.
                        </p>
                      </div>
                    )
                  ) : (
                    renderDirectoryTab()
                  )}
                </div>
              )
            ) : (
              <div className="flex-1 flex flex-col overflow-hidden">
                {renderProfileTab()}
              </div>
            )}
        </div>

        {/* Auto-restart toast — surfaces the post-edit pm2 restart from any view */}
        {restartNotice && (
          <div
            className="fixed bottom-4 left-1/2 -translate-x-1/2 z-[120] px-4 py-2 rounded border font-code text-[12px]"
            style={{
              borderColor: "color-mix(in srgb, var(--crt-green) 45%, transparent)",
              background: "var(--bg-primary)",
              color: "var(--crt-green)",
              boxShadow: "0 8px 32px rgba(0,0,0,0.35)",
            }}
          >
            {restartNotice}
          </div>
        )}

        {/* The wallet sidebar is no longer a separate panel — its UI is now
            embedded as the "Wallet" tab inside the merged account sidebar
            below (see WalletModal embedded render). */}

        {/* ── Right Sidebar: Account (Owner + Wallet) ──────────────────
            Mirrors wallet pattern — flex sibling on desktop (pushes main
            content, drag-to-resize on left edge), fullscreen overlay on
            phone. Sits at the far right when both wallet and owner are
            open. */}
        {isMobile && showOwnerSidebar && (
          <div
            onClick={() => setShowOwnerSidebar(false)}
            style={{
              position: "fixed",
              inset: 0,
              background: "rgba(0,0,0,0.45)",
              backdropFilter: "blur(2px)",
              zIndex: 60,
            }}
            aria-label="Close owner panel"
          />
        )}
        <div
          className="flex flex-col overflow-hidden"
          style={
            isMobile
              ? {
                  position: showOwnerSidebar ? "fixed" : "static",
                  inset: showOwnerSidebar ? 0 : "auto",
                  width: showOwnerSidebar ? "100vw" : "0px",
                  height: showOwnerSidebar ? "100dvh" : "0px",
                  maxWidth: "100vw",
                  background: "var(--bg-primary)",
                  zIndex: 70,
                  boxShadow: showOwnerSidebar ? "0 0 40px rgba(0,0,0,0.6)" : "none",
                  transition: "none",
                  order: 3,
                }
              : {
                  position: "relative",
                  order: 3,
                  width: showOwnerSidebar ? `${ownerSidebarWidth}px` : "0px",
                  minWidth: showOwnerSidebar ? "300px" : "0px",
                  maxWidth: "100%",
                  flexShrink: 0,
                  background: "var(--bg-primary)",
                  borderLeft: showOwnerSidebar ? "1px solid rgba(251,191,36,0.30)" : "none",
                  boxShadow: showOwnerSidebar
                    ? "-12px 0 32px rgba(0,0,0,0.35), inset 1px 0 0 rgba(251,191,36,0.10), 0 0 60px -20px rgba(251,191,36,0.25)"
                    : "none",
                  transition: isOwnerSidebarDragging ? "none" : "width 0.25s ease",
                }
          }
        >
          {!isMobile && showOwnerSidebar && (
            <div
              onMouseDown={(e) => {
                e.preventDefault();
                setIsOwnerSidebarDragging(true);
              }}
              style={{
                position: "absolute",
                top: 0,
                bottom: 0,
                left: -3,
                width: 6,
                cursor: "col-resize",
                zIndex: 1,
                background: isOwnerSidebarDragging ? "rgba(251,191,36,0.40)" : "transparent",
                transition: "background 0.15s ease",
              }}
              title="Drag to resize"
            />
          )}
          {showOwnerSidebar && (() => {
            const cfg = effectiveConfig;
            const cfgOwner: string | null = cfg?.owner || null;
            const youAreOwner = !!(address && cfgOwner && address.toLowerCase() === cfgOwner.toLowerCase()) || isOwner;
            const moduleInfo = selectedModuleInfo;
            // The wallet tab only exists for real wallet sessions (not
            // local/password-less or QR-handoff phone sessions).
            const hasWallet = !!(address && address !== "local" && walletType);
            const canSudo = !!(token && token !== "local" && youAreOwner);
            // One tab per function; a tab that doesn't apply to this session
            // simply doesn't render. Falls back to the first available tab
            // when the remembered one disappears (e.g. wallet disconnected).
            const accountTabs: { id: typeof accountTab; label: string; glyph: string; accent: string }[] = [
              ...(hasWallet ? [{ id: "wallet" as const, label: "Wallet", glyph: "◇", accent: "var(--crt-green)" }] : []),
              { id: "access" as const, label: "Access", glyph: "◎", accent: "var(--crt-amber)" },
              ...(canSudo ? [{ id: "sudo" as const, label: "Sudo", glyph: "⚿", accent: "#cc785c" }] : []),
              ...(selectedModule ? [{ id: "module" as const, label: "Module", glyph: "⌬", accent: "var(--crt-blue)" }] : []),
            ];
            const activeAccountTab = accountTabs.some((t) => t.id === accountTab) ? accountTab : accountTabs[0].id;
            return (
              <div className="flex flex-col h-full overflow-hidden">
                {/* Header */}
                <div
                  className="flex items-center justify-between px-4 py-3 shrink-0"
                  style={{
                    borderBottom: "1px solid color-mix(in srgb, var(--crt-amber) 16%, var(--border-color))",
                    background: "linear-gradient(180deg, color-mix(in srgb, var(--crt-amber) 5%, transparent), transparent)",
                  }}
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <span
                      className="text-[12px] font-bold uppercase tracking-[0.12em] truncate leading-tight"
                      style={{ color: "var(--text-secondary)" }}
                    >
                      Account
                    </span>
                  </div>
                  <button
                    onClick={() => setShowOwnerSidebar(false)}
                    className="flex items-center justify-center focus-ring shrink-0 transition-all"
                    style={{
                      width: 26,
                      height: 26,
                      borderRadius: 999,
                      color: "var(--text-tertiary)",
                      border: "1px solid transparent",
                      background: "transparent",
                      fontSize: 11,
                    }}
                    onMouseEnter={e => {
                      e.currentTarget.style.color = "var(--crt-red)";
                      e.currentTarget.style.borderColor = "color-mix(in srgb, var(--crt-red) 35%, transparent)";
                      e.currentTarget.style.background = "color-mix(in srgb, var(--crt-red) 10%, transparent)";
                    }}
                    onMouseLeave={e => {
                      e.currentTarget.style.color = "var(--text-tertiary)";
                      e.currentTarget.style.borderColor = "transparent";
                      e.currentTarget.style.background = "transparent";
                    }}
                    title="Close panel"
                  >
                    ✕
                  </button>
                </div>

                {/* Tab strip — one tab per function. The tab labels replace
                    the old WALLET/OWNER section dividers. */}
                <div
                  className="flex items-stretch gap-1 px-2 shrink-0"
                  style={{ borderBottom: `1px solid ${subtleBorder}` }}
                >
                  {accountTabs.map((t) => {
                    const on = t.id === activeAccountTab;
                    return (
                      <button
                        key={t.id}
                        onClick={() => setAccountTab(t.id)}
                        className="flex-1 flex items-center justify-center gap-1.5 px-2 py-2.5 text-[10px] font-bold uppercase tracking-[0.14em] transition-all focus-ring"
                        style={{
                          color: on ? t.accent : "var(--text-tertiary)",
                          borderBottom: `2px solid ${on ? t.accent : "transparent"}`,
                          background: on
                            ? `linear-gradient(180deg, transparent, color-mix(in srgb, ${t.accent} 7%, transparent))`
                            : "transparent",
                          marginBottom: -1,
                        }}
                        aria-selected={on}
                        role="tab"
                      >
                        <span aria-hidden style={{ opacity: on ? 1 : 0.55 }}>{t.glyph}</span>
                        {t.label}
                      </button>
                    );
                  })}
                </div>
                <div className="flex-1 overflow-y-auto flex flex-col">
                {/* WALLET — the embedded wallet view carries balance, address,
                    network and disconnect; nothing here repeats it. */}
                {activeAccountTab === "wallet" && hasWallet && (
                  <WalletModal
                    address={address}
                    walletType={walletType}
                    inline
                    embedded
                    flow
                    onClose={() => setShowOwnerSidebar(false)}
                    onDisconnect={() => {
                      setShowOwnerSidebar(false);
                      disconnect();
                    }}
                    onNetworkChange={() => {
                      const ethereum = (window as any).ethereum;
                      if (ethereum) {
                        ethereum.request({ method: "eth_chainId" }).then((cid: string) => {
                          setCurrentChainId(parseInt(cid, 16));
                        }).catch(() => {});
                      }
                    }}
                  />
                )}
                <div className="p-4 flex flex-col gap-3">
                  {/* ACCESS — who controls this module and who gets in:
                      owner identity, phone handoff, whitelist, QR invites. */}
                  {/* Owner identity — gradient avatar derived from the address;
                      the whole row is click-to-copy. */}
                  {activeAccountTab === "access" && cfgOwner && (() => {
                    const seed = Array.from(cfgOwner.toLowerCase()).reduce((a, c) => ((a * 31 + c.charCodeAt(0)) >>> 0), 7);
                    const h1 = seed % 360;
                    const h2 = (h1 + 40 + ((seed >> 5) % 80)) % 360;
                    const justCopied = copiedWlAddr === cfgOwner;
                    return (
                      <button
                        onClick={() => {
                          navigator.clipboard?.writeText(cfgOwner).catch(() => {});
                          setCopiedWlAddr(cfgOwner);
                          setTimeout(() => setCopiedWlAddr(c => (c === cfgOwner ? null : c)), 1200);
                        }}
                        className="flex items-center gap-3 px-3 py-2.5 rounded-[14px] text-left transition-all cursor-pointer shrink-0"
                        style={{
                          border: "1px solid color-mix(in srgb, var(--crt-amber) 20%, var(--border-color))",
                          background: "linear-gradient(135deg, color-mix(in srgb, var(--crt-amber) 6%, transparent), transparent 65%)",
                        }}
                        onMouseEnter={e => (e.currentTarget.style.borderColor = "color-mix(in srgb, var(--crt-amber) 38%, var(--border-color))")}
                        onMouseLeave={e => (e.currentTarget.style.borderColor = "color-mix(in srgb, var(--crt-amber) 20%, var(--border-color))")}
                        title={justCopied ? "Copied!" : `${cfgOwner} — click to copy`}
                      >
                        <span
                          className="shrink-0 rounded-full"
                          style={{
                            width: 34,
                            height: 34,
                            background: `linear-gradient(135deg, hsl(${h1} 75% 58%), hsl(${h2} 70% 38%))`,
                            boxShadow: "0 0 14px -3px color-mix(in srgb, var(--crt-amber) 55%, transparent), inset 0 0 0 1px rgba(255,255,255,0.18)",
                          }}
                          aria-hidden
                        />
                        <span className="flex flex-col min-w-0 flex-1 gap-0.5 leading-tight">
                          <span className="text-[9px] uppercase tracking-[0.14em]" style={{ color: "var(--text-tertiary)" }}>
                            Module owner{youAreOwner ? " · you" : ""}
                          </span>
                          <span
                            className="font-mono text-[12px] truncate"
                            style={{ color: justCopied ? "var(--crt-green)" : "var(--text-primary)" }}
                          >
                            {cfgOwner.length > 26 ? `${cfgOwner.slice(0, 12)}…${cfgOwner.slice(-10)}` : cfgOwner}
                          </span>
                        </span>
                        <span
                          className="text-[10px] font-mono shrink-0"
                          style={{ color: justCopied ? "var(--crt-green)" : "var(--text-tertiary)" }}
                        >
                          {justCopied ? "✓ copied" : "⧉"}
                        </span>
                      </button>
                    );
                  })()}

                  {/* Phone sign-in — hand this session to another device via a
                      single-use QR. Any signed-in user (you can only hand off
                      yourself); hidden in local mode where auth is disabled. */}
                  {activeAccountTab === "access" && token && token !== "local" && address && (() => {
                    const accent = "var(--crt-green)";
                    const live = handoff && handoff.exp > nowSec ? handoff : null;
                    const left = live ? Math.max(0, live.exp - nowSec) : 0;
                    const fmtMMSS = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
                    return (
                      <div className="section-card" data-accent="green">
                        <span className="section-card__bar" />
                        <div className="section-card__head">
                          <div className="section-card__title">
                            <span className="section-card__glyph">⇄</span>
                            Phone Sign-In
                          </div>
                          <span className="text-[9px] uppercase tracking-wider" style={{ color: "var(--text-tertiary)" }}>
                            QR handoff
                          </span>
                        </div>
                        <div className="section-card__body flex flex-col gap-2.5">
                          <div className="text-[10.5px] leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
                            Scan with your phone to open this console <span style={{ color: "var(--text-secondary)" }}>already signed in as you</span> —
                            no wallet or signing there. The code is single-use and dies in 5 minutes.
                          </div>
                          {live ? (
                            <div
                              className="rounded-lg p-3 flex flex-col items-center gap-2"
                              style={{ background: "var(--bg-secondary)", border: `1px solid color-mix(in srgb, ${accent} 30%, transparent)` }}
                            >
                              <div
                                className="rounded-lg p-2 bg-white"
                                dangerouslySetInnerHTML={{ __html: qrSvg(handoffUrl(live.code), 180) }}
                              />
                              <div className="text-[10px] font-mono" style={{ color: "var(--text-tertiary)" }}>
                                single-use · expires in <span style={{ color: accent }}>{fmtMMSS}</span>
                              </div>
                              <button
                                onClick={() => copyGrantBit("handoff", handoffUrl(live.code))}
                                className="w-full text-[10px] px-2 py-1.5 rounded font-mono truncate transition-all"
                                style={{ color: "var(--text-secondary)", background: "var(--bg-primary)", border: "1px solid var(--border-color)" }}
                                title="Copy sign-in link (treat it like a password)"
                              >
                                {grantCopied === "handoff" ? "✓ copied link" : handoffUrl(live.code)}
                              </button>
                              <button
                                onClick={() => setHandoff(null)}
                                className="text-[9px] uppercase tracking-wider"
                                style={{ color: "var(--text-tertiary)" }}
                              >
                                done
                              </button>
                            </div>
                          ) : (
                            <button
                              onClick={createHandoff}
                              disabled={handoffBusy}
                              className="text-[11px] px-3 py-2 rounded uppercase font-bold tracking-wider transition-all disabled:opacity-40"
                              style={{
                                color: "var(--bg-primary)",
                                background: accent,
                                border: `1px solid ${accent}`,
                              }}
                            >
                              {handoffBusy ? "…" : handoff ? "Code expired — new QR" : "Show Sign-In QR"}
                            </button>
                          )}
                          {handoffError && (
                            <div className="text-[10px]" style={{ color: "var(--crt-red)" }}>{handoffError}</div>
                          )}
                        </div>
                      </div>
                    );
                  })()}

                  {/* SUDO — session & policy: one signature unlocks privileged
                      cross-module ops for a window (default 1 hour); the owner
                      tailors the window and which actions always re-ask. */}
                  {activeAccountTab === "sudo" && canSudo && (() => {
                    const accent = "#cc785c"; // claude clay, matches the Sudo sheet
                    const info = sudoInfo;
                    const left = info?.active && info.expires ? Math.max(0, info.expires - nowSec) : 0;
                    const unlocked = left > 0;
                    const fmtLeft =
                      left >= 3600
                        ? `${Math.floor(left / 3600)}h ${Math.floor((left % 3600) / 60)}m`
                        : left >= 60
                        ? `${Math.floor(left / 60)}m ${String(left % 60).padStart(2, "0")}s`
                        : `${left}s`;
                    const DURATIONS = [
                      { label: "every time", secs: 0 },
                      { label: "15 min", secs: 900 },
                      { label: "1 hour", secs: 3600 },
                      { label: "4 hours", secs: 14400 },
                      { label: "8 hours", secs: 28800 },
                    ];
                    // Only actions that actually pass through sudo_gate — file
                    // writes ride the plain owner session and never prompt.
                    const SUDO_ACTIONS = ["delete", "rename", "restore", "kill", "process"];
                    const alwaysAsk = info?.alwaysAsk || [];
                    return (
                      <div className="section-card" data-accent="amber">
                        <span className="section-card__bar" />
                        <div className="section-card__head">
                          <div className="section-card__title">
                            <span className="section-card__glyph">⚿</span>
                            Sudo
                          </div>
                          <span
                            className="text-[9px] uppercase tracking-wider font-bold px-2 py-0.5 rounded-full"
                            style={{
                              color: unlocked ? accent : "var(--text-tertiary)",
                              background: unlocked ? "color-mix(in srgb, #cc785c 14%, transparent)" : "var(--bg-secondary)",
                              border: `1px solid ${unlocked ? "color-mix(in srgb, #cc785c 40%, transparent)" : "var(--border-color)"}`,
                            }}
                          >
                            {unlocked ? `unlocked · ${fmtLeft}` : "locked"}
                          </span>
                        </div>
                        <div className="section-card__body flex flex-col gap-2.5">
                          <div className="text-[10.5px] leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
                            Privileged cross-module ops need an owner signature. One signature{" "}
                            <span style={{ color: "var(--text-secondary)" }}>
                              {info && info.sessionSecs === 0
                                ? "authorizes only that operation"
                                : "unlocks sudo for the window below"}
                            </span>
                            {" "}— tailor how often you're asked.
                          </div>
                          {/* Re-ask window */}
                          <div className="flex flex-col gap-1.5">
                            <span className="text-[9px] uppercase tracking-[0.14em]" style={{ color: "var(--text-tertiary)" }}>
                              Ask for a signature
                            </span>
                            <div className="flex flex-wrap gap-1">
                              {DURATIONS.map((d) => {
                                const selected = info ? info.sessionSecs === d.secs : d.secs === 3600;
                                return (
                                  <button
                                    key={d.secs}
                                    disabled={sudoPolicyBusy || selected}
                                    onClick={() => setSudoPolicy({ session_secs: d.secs })}
                                    className="text-[10px] px-2 py-1 rounded-full font-mono transition-all disabled:cursor-default"
                                    style={{
                                      color: selected ? "var(--bg-primary)" : "var(--text-secondary)",
                                      background: selected ? accent : "var(--bg-secondary)",
                                      border: `1px solid ${selected ? accent : "var(--border-color)"}`,
                                      opacity: sudoPolicyBusy && !selected ? 0.5 : 1,
                                    }}
                                    title={d.secs === 0 ? "Sign every privileged operation" : `One signature covers privileged ops for ${d.label}`}
                                  >
                                    {d.secs === 0 ? d.label : `once / ${d.label}`}
                                  </button>
                                );
                              })}
                            </div>
                          </div>
                          {/* Per-action overrides */}
                          {info && info.sessionSecs > 0 && (
                            <div className="flex flex-col gap-1.5">
                              <span className="text-[9px] uppercase tracking-[0.14em]" style={{ color: "var(--text-tertiary)" }}>
                                Always ask anyway
                              </span>
                              <div className="flex flex-wrap gap-1">
                                {SUDO_ACTIONS.map((a) => {
                                  const on = alwaysAsk.includes(a);
                                  return (
                                    <button
                                      key={a}
                                      disabled={sudoPolicyBusy}
                                      onClick={() =>
                                        setSudoPolicy({
                                          always_ask: on ? alwaysAsk.filter((x) => x !== a) : [...alwaysAsk, a],
                                        })
                                      }
                                      className="text-[10px] px-2 py-1 rounded-full font-mono transition-all"
                                      style={{
                                        color: on ? "var(--crt-amber)" : "var(--text-tertiary)",
                                        background: on ? "color-mix(in srgb, var(--crt-amber) 12%, transparent)" : "var(--bg-secondary)",
                                        border: `1px solid ${on ? "color-mix(in srgb, var(--crt-amber) 45%, transparent)" : "var(--border-color)"}`,
                                        opacity: sudoPolicyBusy ? 0.5 : 1,
                                      }}
                                      title={on ? `${a}: fresh signature every time (click to let the session cover it)` : `${a}: covered by the session (click to always ask)`}
                                    >
                                      {on ? "✓ " : ""}{a}
                                    </button>
                                  );
                                })}
                              </div>
                              <div className="text-[9.5px] leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
                                Checked actions demand a fresh signature even while unlocked. Policy changes always do.
                              </div>
                            </div>
                          )}
                          {unlocked && (
                            <button
                              onClick={lockSudo}
                              className="text-[10px] px-3 py-1.5 rounded uppercase font-bold tracking-wider transition-all self-start"
                              style={{
                                color: accent,
                                background: "transparent",
                                border: `1px solid color-mix(in srgb, #cc785c 45%, transparent)`,
                              }}
                              title="End the sudo session now — the next privileged op will ask for a signature"
                            >
                              ⚿ Lock now
                            </button>
                          )}
                          {sudoPolicyErr && (
                            <div className="text-[10px]" style={{ color: "var(--crt-red)" }}>{sudoPolicyErr}</div>
                          )}
                        </div>
                      </div>
                    );
                  })()}

                  {/* MODULE — version / CID / created for the selected module */}
                  {activeAccountTab === "module" && selectedModule && (
                    <div className="section-card" data-accent="blue">
                      <span className="section-card__bar" />
                      <div className="section-card__head">
                        <div className="section-card__title">
                          <span className="section-card__glyph">⌬</span>
                          Module
                        </div>
                      </div>
                      <div className="section-card__body flex flex-col text-[12px]">
                        {(moduleInfo?.version || cfg?.version) && (
                          <div className="flex items-center justify-between gap-3 py-1.5">
                            <span className="shrink-0 text-[10px] uppercase tracking-[0.12em]" style={{ color: "var(--text-tertiary)" }}>
                              Version
                            </span>
                            <span
                              className="font-mono text-[11px] px-1.5 py-0.5 rounded"
                              style={{
                                color: "var(--crt-blue)",
                                background: "color-mix(in srgb, var(--crt-blue) 10%, transparent)",
                                border: "1px solid color-mix(in srgb, var(--crt-blue) 28%, transparent)",
                              }}
                            >
                              v{String(moduleInfo?.version || cfg?.version).replace(/^v/i, "")}
                            </span>
                          </div>
                        )}
                        {(moduleInfo?.cid || cfg?.cid) && (() => {
                          const cid = String(moduleInfo?.cid || cfg?.cid);
                          const justCopied = copiedWlAddr === cid;
                          return (
                            <div
                              className="flex items-center justify-between gap-3 py-1.5"
                              style={{ borderTop: "1px solid color-mix(in srgb, var(--border-color) 60%, transparent)" }}
                            >
                              <span className="shrink-0 text-[10px] uppercase tracking-[0.12em]" style={{ color: "var(--text-tertiary)" }}>
                                CID
                              </span>
                              <span className="flex items-center gap-1.5 min-w-0">
                              <button
                                onClick={() => {
                                  navigator.clipboard?.writeText(cid).catch(() => {});
                                  setCopiedWlAddr(cid);
                                  setTimeout(() => setCopiedWlAddr(c => (c === cid ? null : c)), 1200);
                                }}
                                className="font-mono text-[11px] truncate text-right cursor-pointer transition-colors"
                                style={{
                                  color: justCopied ? "var(--crt-green)" : "var(--text-secondary)",
                                  background: "transparent",
                                  border: "none",
                                  padding: 0,
                                }}
                                title={justCopied ? "Copied!" : `${cid} — click to copy`}
                              >
                                {justCopied ? "✓ copied" : (cid.length > 20 ? `${cid.slice(0, 9)}…${cid.slice(-8)}` : cid)}
                              </button>
                              <button
                                onClick={() => shareCidQr(cid, selectedModule)}
                                className="text-[10px] px-1.5 py-0.5 rounded shrink-0 transition-all"
                                style={{
                                  color: "var(--crt-green)",
                                  background: "color-mix(in srgb, var(--crt-green) 8%, transparent)",
                                  border: "1px solid color-mix(in srgb, var(--crt-green) 30%, transparent)",
                                }}
                                title="Share this module's snapshot CID as a QR code"
                              >
                                ⛶
                              </button>
                              </span>
                            </div>
                          );
                        })()}
                        {moduleInfo?.created_at && (
                          <div
                            className="flex items-center justify-between gap-3 py-1.5"
                            style={{ borderTop: "1px solid color-mix(in srgb, var(--border-color) 60%, transparent)" }}
                          >
                            <span className="shrink-0 text-[10px] uppercase tracking-[0.12em]" style={{ color: "var(--text-tertiary)" }}>
                              Created
                            </span>
                            <span className="truncate font-mono text-[11px]" style={{ color: "var(--text-secondary)" }}>
                              {new Date(moduleInfo.created_at * 1000).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })}
                            </span>
                          </div>
                        )}
                      </div>
                    </div>
                  )}

                  {/* Whitelist — owner edits inline; non-owners get read-only
                      rows and a hint about who can edit. Each row is
                      click-to-copy; owner gets an X to revoke. */}
                  {activeAccountTab === "access" && (
                  <div className="section-card" data-accent="green">
                    <span className="section-card__bar" />
                    <div className="section-card__head">
                      <div className="section-card__title">
                        <span className="section-card__glyph">◎</span>
                        Whitelist
                      </div>
                      <div className="flex items-center gap-1.5">
                        <span
                          className="text-[9px] uppercase tracking-[0.12em]"
                          style={{ color: "var(--text-tertiary)" }}
                        >
                          {isOwner ? "owner edit" : "read-only"}
                        </span>
                        <span
                          className="text-[10px] font-mono px-1.5 py-0.5 rounded"
                          style={{
                            color: "var(--crt-green)",
                            background: "color-mix(in srgb, var(--crt-green) 10%, transparent)",
                            border: "1px solid color-mix(in srgb, var(--crt-green) 30%, transparent)",
                          }}
                        >
                          {whitelist.length}
                        </span>
                      </div>
                    </div>
                    <div className="section-card__body flex flex-col gap-2">
                      {whitelist.length === 0 ? (
                        <span className="text-[11.5px] py-1" style={{ color: "var(--text-tertiary)" }}>
                          No editors whitelisted yet. Whitelisted addresses get owner-level edit access to the orbit. The configured owner is always allowed.
                        </span>
                      ) : (
                        <div
                          className="flex flex-col gap-1"
                          style={{ maxHeight: 192, overflowY: "auto" }}
                        >
                          {whitelist.map((addr) => {
                            const isCaller = !!(address && address.toLowerCase() === addr.toLowerCase());
                            const justCopied = copiedWlAddr === addr;
                            return (
                              <div
                                key={addr}
                                className="flex items-center gap-1.5 px-1.5 py-1 rounded transition-colors"
                                style={{
                                  background: isCaller
                                    ? "color-mix(in srgb, var(--crt-green) 7%, transparent)"
                                    : "var(--bg-secondary)",
                                  border: `1px solid ${isCaller ? "color-mix(in srgb, var(--crt-green) 30%, transparent)" : "var(--border-color)"}`,
                                }}
                              >
                                <span
                                  className="inline-block w-1.5 h-1.5 rounded-full shrink-0"
                                  style={{
                                    background: "var(--crt-green)",
                                    boxShadow: isCaller ? "0 0 4px var(--crt-green)" : "none",
                                  }}
                                />
                                <button
                                  onClick={() => {
                                    navigator.clipboard?.writeText(addr).catch(() => {});
                                    setCopiedWlAddr(addr);
                                    setTimeout(() => setCopiedWlAddr(c => (c === addr ? null : c)), 1200);
                                  }}
                                  className="font-mono text-[11px] truncate flex-1 text-left transition-colors cursor-pointer"
                                  style={{
                                    color: justCopied ? "var(--crt-green)" : "var(--text-secondary)",
                                    background: "transparent",
                                    border: "none",
                                    padding: 0,
                                  }}
                                  title={justCopied ? "Copied!" : `${addr} — click to copy`}
                                >
                                  {addr.length > 22 ? `${addr.slice(0, 10)}…${addr.slice(-8)}` : addr}
                                </button>
                                {isCaller && (
                                  <span
                                    className="text-[8px] font-bold uppercase tracking-widest px-1 py-[1px] rounded shrink-0"
                                    style={{ background: "var(--crt-green)", color: "var(--bg-primary)" }}
                                  >
                                    YOU
                                  </span>
                                )}
                                {isOwner && (
                                  <button
                                    onClick={() => removeFromWhitelist(addr)}
                                    disabled={whitelistBusy}
                                    className="text-[10px] w-5 h-5 flex items-center justify-center rounded shrink-0 transition-all"
                                    style={{
                                      color: "var(--crt-red)",
                                      background: "color-mix(in srgb, var(--crt-red) 6%, transparent)",
                                      border: "1px solid color-mix(in srgb, var(--crt-red) 22%, transparent)",
                                    }}
                                    onMouseEnter={e => (e.currentTarget.style.background = "color-mix(in srgb, var(--crt-red) 16%, transparent)")}
                                    onMouseLeave={e => (e.currentTarget.style.background = "color-mix(in srgb, var(--crt-red) 6%, transparent)")}
                                    title={`Remove ${addr.slice(0, 6)}…${addr.slice(-4)} from the whitelist`}
                                  >
                                    ✕
                                  </button>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      )}
                      {/* Timed access — live QR-invite redemptions. These wallets
                          (or walletless guest passes) are in RIGHT NOW on a
                          countdown; a wallet can be promoted to the permanent
                          whitelist in one tap. Owner-only: redemptions only
                          load for the owner. */}
                      {isOwner && grantRedemptions.some((r) => r.exp > nowSec) && (
                        <div className="flex flex-col gap-1 mt-1">
                          <span className="text-[9px] uppercase tracking-[0.16em]" style={{ color: "var(--text-tertiary)" }}>
                            Timed access · via QR invite
                          </span>
                          {grantRedemptions.filter((r) => r.exp > nowSec).map((r) => {
                            const isGuest = r.address.startsWith("guest_");
                            const already = whitelist.some((w) => w.toLowerCase() === r.address.toLowerCase());
                            return (
                              <div
                                key={`${r.grant}-${r.address}`}
                                className="flex items-center gap-1.5 px-1.5 py-1 rounded"
                                style={{
                                  background: "color-mix(in srgb, #cc785c 6%, transparent)",
                                  border: "1px solid color-mix(in srgb, #cc785c 26%, transparent)",
                                }}
                              >
                                <span className="inline-block w-1.5 h-1.5 rounded-full shrink-0" style={{ background: "#cc785c" }} />
                                <span className="font-mono text-[11px] truncate flex-1" style={{ color: "var(--text-secondary)" }} title={r.address}>
                                  {isGuest ? `guest pass · ${r.address.slice(6)}` : `${r.address.slice(0, 10)}…${r.address.slice(-6)}`}
                                </span>
                                <span className="text-[9px] font-mono shrink-0" style={{ color: "#cc785c" }}>
                                  {fmtTimeLeft(r.exp)}
                                </span>
                                {!isGuest && !already && (
                                  <button
                                    onClick={() => addToWhitelist(r.address)}
                                    disabled={whitelistBusy}
                                    className="text-[9px] px-1.5 py-0.5 rounded uppercase font-bold tracking-wider shrink-0 transition-all disabled:opacity-40"
                                    style={{
                                      color: "var(--crt-green)",
                                      background: "color-mix(in srgb, var(--crt-green) 10%, transparent)",
                                      border: "1px solid color-mix(in srgb, var(--crt-green) 35%, transparent)",
                                    }}
                                    title="Add this wallet to the permanent whitelist"
                                  >
                                    + WL
                                  </button>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      )}
                      {isOwner ? (
                        <>
                          <div className="flex items-center gap-1.5 mt-1">
                            <input
                              type="text"
                              value={whitelistInput}
                              onChange={(e) => { setWhitelistInput(e.target.value); setWhitelistError(null); }}
                              onKeyDown={(e) => { if (e.key === "Enter" && whitelistInput.trim()) addToWhitelist(whitelistInput); }}
                              placeholder="0x… address"
                              disabled={whitelistBusy}
                              className="flex-1 min-w-0 px-2 py-1.5 text-[11px] font-mono rounded outline-none"
                              style={{
                                color: "var(--text-primary)",
                                background: "var(--bg-secondary)",
                                border: `1px solid ${whitelistError ? "color-mix(in srgb, var(--crt-red) 45%, transparent)" : "var(--border-color)"}`,
                              }}
                            />
                            <button
                              onClick={() => addToWhitelist(whitelistInput)}
                              disabled={whitelistBusy || !whitelistInput.trim()}
                              className="text-[10px] px-3 py-1.5 rounded uppercase font-bold tracking-wider transition-all disabled:opacity-40 shrink-0"
                              style={{
                                color: "var(--crt-green)",
                                background: "color-mix(in srgb, var(--crt-green) 12%, transparent)",
                                border: "1px solid color-mix(in srgb, var(--crt-green) 40%, transparent)",
                              }}
                            >
                              {whitelistBusy ? "…" : "Add"}
                            </button>
                          </div>
                          {whitelistError && (
                            <div className="text-[10px]" style={{ color: "var(--crt-red)" }}>
                              {whitelistError}
                            </div>
                          )}
                        </>
                      ) : (
                        <div
                          className="text-[9px] uppercase tracking-[0.1em] mt-0.5"
                          style={{ color: "var(--text-tertiary)" }}
                        >
                          Only the configured owner
                          {cfgOwner ? ` (${cfgOwner.slice(0, 6)}…${cfgOwner.slice(-4)})` : ""} can edit this list.
                        </div>
                      )}
                    </div>
                  </div>
                  )}

                  {/* Share edit access — the same QR-invite minting card as the
                      module OVERVIEW tab, surfaced here because this panel is
                      where the owner manages who gets in. */}
                  {activeAccountTab === "access" && renderShareAccessCard()}

                  {/* Kill process — a sudo-gated op, so it lives on the SUDO
                      tab right under the session policy. */}
                  {activeAccountTab === "sudo" && isOwner && (
                    <button
                      onClick={() => {
                        setShowOwnerSidebar(false);
                        setShowKillDialog(true);
                      }}
                      className="flex items-center gap-2.5 text-left px-2.5 py-2 rounded-lg border transition-colors"
                      style={{
                        borderColor: "color-mix(in srgb, var(--crt-red) 26%, transparent)",
                        background: "color-mix(in srgb, var(--crt-red) 5%, transparent)",
                      }}
                      onMouseEnter={e => {
                        e.currentTarget.style.background = "color-mix(in srgb, var(--crt-red) 13%, transparent)";
                        e.currentTarget.style.borderColor = "color-mix(in srgb, var(--crt-red) 45%, transparent)";
                      }}
                      onMouseLeave={e => {
                        e.currentTarget.style.background = "color-mix(in srgb, var(--crt-red) 5%, transparent)";
                        e.currentTarget.style.borderColor = "color-mix(in srgb, var(--crt-red) 26%, transparent)";
                      }}
                      title="Kill a process by PID or port (Cmd/Ctrl+K)"
                    >
                      <span
                        className="flex items-center justify-center shrink-0 text-[12px] rounded-md"
                        style={{
                          width: 26,
                          height: 26,
                          color: "var(--crt-red)",
                          background: "color-mix(in srgb, var(--crt-red) 12%, transparent)",
                          border: "1px solid color-mix(in srgb, var(--crt-red) 28%, transparent)",
                        }}
                        aria-hidden
                      >
                        ⏻
                      </span>
                      <span className="flex flex-col min-w-0 flex-1 gap-0.5 leading-tight">
                        <span className="text-[12px]" style={{ color: "var(--crt-red)" }}>Kill process</span>
                        <span className="text-[10px] truncate" style={{ color: "color-mix(in srgb, var(--crt-red) 55%, var(--text-tertiary))" }}>
                          Stop a process by PID or port
                        </span>
                      </span>
                      <span
                        className="text-[9px] font-mono px-1.5 py-0.5 rounded shrink-0"
                        style={{
                          color: "var(--crt-red)",
                          border: "1px solid color-mix(in srgb, var(--crt-red) 30%, transparent)",
                          opacity: 0.85,
                        }}
                      >
                        ⌘K
                      </span>
                    </button>
                  )}

                  {/* Sign out — only for sessions with no WALLET tab (e.g.
                      QR-handoff phone sessions); wallet sessions disconnect
                      from the wallet view, so no duplicate button. */}
                  {activeAccountTab === "access" && token && token !== "local" && !hasWallet && (
                    <button
                      onClick={() => {
                        setShowOwnerSidebar(false);
                        disconnect();
                      }}
                      className="flex items-center gap-2.5 text-left px-2.5 py-2 rounded-lg border transition-colors"
                      style={{
                        borderColor: "var(--border-color)",
                        background: "var(--bg-secondary)",
                      }}
                      onMouseEnter={e => {
                        e.currentTarget.style.background = "color-mix(in srgb, var(--crt-red) 8%, transparent)";
                        e.currentTarget.style.borderColor = "color-mix(in srgb, var(--crt-red) 30%, var(--border-color))";
                      }}
                      onMouseLeave={e => {
                        e.currentTarget.style.background = "var(--bg-secondary)";
                        e.currentTarget.style.borderColor = "var(--border-color)";
                      }}
                    >
                      <span
                        className="flex items-center justify-center shrink-0 text-[12px] rounded-md"
                        style={{
                          width: 26,
                          height: 26,
                          color: "var(--text-secondary)",
                          background: "color-mix(in srgb, var(--text-tertiary) 12%, transparent)",
                          border: "1px solid color-mix(in srgb, var(--text-tertiary) 25%, transparent)",
                        }}
                        aria-hidden
                      >
                        ⎋
                      </span>
                      <span className="flex flex-col min-w-0 flex-1 gap-0.5 leading-tight">
                        <span className="text-[12px]" style={{ color: "var(--text-primary)" }}>Sign out</span>
                        <span className="text-[10px] truncate" style={{ color: "var(--text-tertiary)" }}>
                          End this session and disconnect
                        </span>
                      </span>
                    </button>
                  )}

                  {/* Spacer — anchors the cards to the top of the pane */}
                  <div className="flex-1" />
                </div>
                </div>
              </div>
            );
          })()}
        </div>

      </div>

      {/* ── Composer dock — full-width prompt bar at the bottom of the
          console. App renders above it, tasks live in the right sidebar,
          and the PARAMS panel (auth + mod/model/agent + system prompt
          chain) folds out of this bar. ── */}
      {renderComposerDock()}

      {/* ── TERMS OF USE — first sign-in gate for non-owner wallets. The
          wallet signature that follows covers this exact text (the server
          embeds it in the challenge), so AGREE here, then sign. ── */}
      {termsPrompt && (
        <div
          className="fixed inset-0 z-[400] flex items-center justify-center p-4"
          style={{ background: "rgba(7, 7, 13, 0.65)", backdropFilter: "blur(8px)", WebkitBackdropFilter: "blur(8px)" }}
        >
          <div
            className="rounded-2xl w-full max-w-md flex flex-col overflow-hidden"
            style={{
              background: "color-mix(in srgb, var(--glass-bg, var(--bg-primary)) 92%, transparent)",
              border: "1px solid var(--border-color-strong)",
              boxShadow: "0 18px 60px rgba(0,0,0,0.55)",
              backdropFilter: "blur(18px) saturate(150%)",
              WebkitBackdropFilter: "blur(18px) saturate(150%)",
            }}
          >
            <div className="px-4 py-3" style={{ borderBottom: "1px solid var(--border-color)" }}>
              <span className="text-[12px] uppercase font-bold tracking-[0.14em]" style={{ color: "var(--text-primary)" }}>
                {termsPrompt.title}
              </span>
            </div>
            <div
              className="px-4 py-3 text-[12px] leading-relaxed overflow-y-auto whitespace-pre-wrap"
              style={{ color: "var(--text-secondary)", maxHeight: "50vh" }}
            >
              {termsPrompt.text}
            </div>
            <div className="px-4 pb-2 text-[11px]" style={{ color: "var(--text-tertiary)" }}>
              Agreeing asks your wallet to sign a message that includes these terms.
            </div>
            <div className="flex gap-2 p-4 pt-2">
              <button
                onClick={() => termsResolveRef.current?.(false)}
                className="flex-1 text-[11px] px-3 py-2 rounded uppercase font-bold tracking-wider transition-all"
                style={{ color: "var(--text-secondary)", background: "var(--bg-secondary)", border: "1px solid var(--border-color)" }}
              >
                Decline
              </button>
              <button
                onClick={() => termsResolveRef.current?.(true)}
                className="flex-1 text-[11px] px-3 py-2 rounded uppercase font-bold tracking-wider transition-all"
                style={{ color: "var(--bg-primary)", background: "var(--accent-color, #cc785c)", border: "1px solid var(--accent-color, #cc785c)" }}
              >
                Agree &amp; Sign
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── SYSTEM PROMPTS manager — the separate home for prompt content.
          The composer only shows name chips; here you add, edit, delete,
          toggle and reorder the blocks that chain into one system prompt. ── */}
      {showSysPromptManager && (
        <div className="fixed inset-0 z-[130] flex items-center justify-center p-4" style={{ background: "rgba(0,0,0,0.6)" }}>
          <div
            className="w-[620px] max-w-full max-h-[85vh] border rounded-xl flex flex-col overflow-hidden"
            style={{ background: "var(--bg-primary)", borderColor: subtleBorderStrong }}
          >
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-3 border-b" style={{ borderColor: subtleBorder, background: tintBg }}>
              <span className="text-[13px] font-pixel uppercase" style={{ color: "var(--text-primary)" }}>
                {editingSysPrompt ? "Edit System Prompt" : creatingSysPrompt ? "New System Prompt" : "System Prompt Chain"}
              </span>
              <button
                onClick={() => { setShowSysPromptManager(false); setEditingSysPrompt(null); setCreatingSysPrompt(false); }}
                className="text-[16px] transition-colors"
                style={{ color: "var(--text-tertiary)" }}
                aria-label="Close"
              >
                ✕
              </button>
            </div>

            {!editingSysPrompt && !creatingSysPrompt ? (
              /* Chain list — toggle, reorder, edit, delete */
              <div className="flex flex-col overflow-hidden flex-1">
                <div className="px-4 py-2 text-[11px] border-b" style={{ color: "var(--text-tertiary)", borderColor: `${subtleBorder}66` }}>
                  Prompts marked ON are chained top→down into one system prompt and sent with every task.
                </div>
                <div className="flex-1 overflow-y-auto">
                  {sysPrompts.length === 0 && (
                    <div className="px-4 py-6 text-[12px] text-center" style={{ color: "var(--text-tertiary)" }}>
                      No system prompts yet — add one below. Your agent personality applies as the fallback.
                    </div>
                  )}
                  {sysPrompts.map((p, i) => {
                    const chainIdx = activeSysPrompts.findIndex(a => a.id === p.id);
                    return (
                      <div
                        key={p.id}
                        className="group flex items-center gap-3 px-4 py-3 border-b transition-colors cursor-pointer"
                        style={{ borderColor: `${subtleBorder}66`, opacity: p.on ? 1 : 0.6 }}
                        onMouseEnter={(e) => (e.currentTarget.style.background = cardHoverBg)}
                        onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                        onClick={() => startEditSysPrompt(p)}
                      >
                        {/* Chain position + reorder */}
                        <div className="flex flex-col items-center shrink-0 w-7">
                          <button
                            onClick={(e) => { e.stopPropagation(); moveSysPrompt(p.id, -1); }}
                            className="text-[10px] leading-none py-0.5 disabled:opacity-20"
                            style={{ color: "var(--text-tertiary)" }}
                            disabled={i === 0}
                            title="Move up the chain"
                            aria-label={`Move ${p.name} up`}
                          >
                            ▲
                          </button>
                          <span className="text-[11px] font-mono" style={{ color: chainIdx >= 0 ? "#60a5fa" : "var(--text-tertiary)" }}>
                            {chainIdx >= 0 ? chainIdx + 1 : "·"}
                          </span>
                          <button
                            onClick={(e) => { e.stopPropagation(); moveSysPrompt(p.id, 1); }}
                            className="text-[10px] leading-none py-0.5 disabled:opacity-20"
                            style={{ color: "var(--text-tertiary)" }}
                            disabled={i === sysPrompts.length - 1}
                            title="Move down the chain"
                            aria-label={`Move ${p.name} down`}
                          >
                            ▼
                          </button>
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-[13px] font-pixel uppercase" style={{ color: "var(--text-primary)" }}>
                              {p.name}
                            </span>
                            {chainIdx >= 0 && (
                              <span className="text-[9px] px-1.5 py-0.5 rounded" style={{ background: "rgba(96,165,250,0.15)", color: "#60a5fa" }}>
                                in chain
                              </span>
                            )}
                          </div>
                          <div className="text-[11px] mt-0.5 truncate" style={{ color: "var(--text-tertiary)" }}>
                            {p.text.trim() ? p.text.trim().replace(/\s+/g, " ").slice(0, 100) + (p.text.trim().length > 100 ? "..." : "") : "(empty)"}
                          </div>
                        </div>
                        <div className="flex gap-1 shrink-0">
                          <button
                            onClick={(e) => { e.stopPropagation(); toggleSysPrompt(p.id); }}
                            className="text-[10px] px-2 py-1 border rounded transition-colors"
                            style={p.on
                              ? { borderColor: "rgba(52,211,153,0.4)", color: "var(--crt-green)" }
                              : { borderColor: subtleBorder, color: "var(--text-tertiary)" }}
                            title={p.on ? "Remove from the chain" : "Add to the chain"}
                          >
                            {p.on ? "ON" : "OFF"}
                          </button>
                          <button
                            onClick={(e) => { e.stopPropagation(); deleteSysPrompt(p.id); }}
                            className="text-[10px] px-2 py-1 border rounded transition-colors opacity-0 group-hover:opacity-100"
                            style={{ borderColor: "rgba(239,68,68,0.3)", color: "#ef4444" }}
                            title="Delete"
                          >
                            Del
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
                {/* Footer */}
                <div className="flex items-center justify-between px-4 py-3 border-t" style={{ borderColor: subtleBorder, background: tintBg }}>
                  <span className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                    {activeSysPrompts.length}/{sysPrompts.length} in chain
                    {chainedSystemPrompt && ` · ${chainedSystemPrompt.length} chars`}
                  </span>
                  <button onClick={startNewSysPrompt} className="pixel-btn text-[12px] py-1.5 px-4 uppercase">
                    + New
                  </button>
                </div>
              </div>
            ) : (
              /* Edit/Create form */
              <div className="flex flex-col overflow-hidden flex-1">
                <div className="flex flex-col gap-3 p-4 overflow-y-auto flex-1">
                  <input
                    type="text"
                    value={sysPromptDraft.name}
                    onChange={(e) => setSysPromptDraft(d => ({ ...d, name: e.target.value }))}
                    placeholder="Prompt name (e.g. RUST REVIEWER, TERSE, HOUSE STYLE)"
                    className="w-full px-3 py-2 text-[13px] border rounded bg-transparent outline-none"
                    style={{ borderColor: subtleBorder, color: "var(--text-primary)" }}
                    autoFocus={!editingSysPrompt}
                  />
                  <div className="flex flex-col gap-1 flex-1">
                    <textarea
                      value={sysPromptDraft.text}
                      onChange={(e) => setSysPromptDraft(d => ({ ...d, text: e.target.value }))}
                      placeholder="You are a senior Rust reviewer. Be terse. Cite file:line…"
                      className="w-full px-3 py-2 text-[13px] border rounded bg-transparent outline-none resize-none font-mono"
                      style={{ borderColor: subtleBorder, color: "var(--text-primary)", minHeight: "220px", lineHeight: "1.6" }}
                    />
                    <span className="text-[10px]" style={{ color: "var(--text-tertiary)", opacity: 0.6 }}>
                      Saved prompts persist across sessions. Every prompt toggled ON is chained (in list order) into the system prompt for each task.
                    </span>
                  </div>
                </div>
                <div className="flex items-center justify-between px-4 py-3 border-t" style={{ borderColor: subtleBorder, background: tintBg }}>
                  <button
                    onClick={() => { setEditingSysPrompt(null); setCreatingSysPrompt(false); setSysPromptDraft({ name: "", text: "" }); }}
                    className="px-3 py-1.5 text-[12px] border rounded transition-colors"
                    style={{ borderColor: subtleBorder, color: "var(--text-secondary)" }}
                  >
                    Back
                  </button>
                  <button
                    onClick={saveSysPromptDraft}
                    disabled={!sysPromptDraft.name.trim()}
                    className="pixel-btn text-[12px] py-1.5 px-4 uppercase"
                  >
                    {editingSysPrompt ? "Update" : "Add to chain"}
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Minimized composer — a small tool pill that restores the ask bar */}
      {composerMinimized && (
        <button
          onClick={() => {
            setComposerMinimized(false);
            setTimeout(() => composerInputRef.current?.focus(), 50);
          }}
          className="fixed inline-flex items-center gap-1.5 px-4 py-2.5 rounded-full font-code focus-ring"
          style={{
            right: 16,
            bottom: "calc(env(safe-area-inset-bottom, 0px) + 16px)",
            zIndex: 95,
            background: "var(--bg-primary)",
            border: "1px solid color-mix(in srgb, var(--crt-green) 45%, transparent)",
            color: "var(--crt-green)",
            boxShadow: "0 6px 24px rgba(0,0,0,0.45)",
            fontSize: 12,
            letterSpacing: "0.08em",
          }}
          title="Open the ask bar"
          aria-label="Open the ask bar"
        >
          ▸ ASK
        </button>
      )}

      {/* ── Floating FILES Panel ──────────────────────────── */}
      {filesPanelFloating && (
        <div
          className="fixed flex flex-col overflow-hidden"
          style={{
            left: filesPanelPos.x,
            top: filesPanelPos.y,
            width: filesPanelSize.w,
            height: filesPanelSize.h,
            zIndex: 90,
            background: "var(--bg-primary)",
            border: "1px solid var(--border-color)",
            borderRadius: "8px",
            boxShadow: "0 8px 32px rgba(0,0,0,0.5), 0 0 1px rgba(255,255,255,0.1)",
          }}
        >
          {/* Drag handle title bar */}
          <div
            className="flex items-center justify-between px-3 py-1.5 shrink-0 select-none"
            style={{
              background: "var(--bg-secondary)",
              borderBottom: "1px solid var(--border-color)",
              borderRadius: "8px 8px 0 0",
              cursor: "grab",
            }}
            onMouseDown={(e) => {
              if ((e.target as HTMLElement).closest("button")) return;
              e.preventDefault();
              filesPanelDrag.current = { startX: e.clientX, startY: e.clientY, origX: filesPanelPos.x, origY: filesPanelPos.y };
              document.body.style.cursor = 'grabbing';
              document.body.style.userSelect = 'none';
              setIframesInert(true);
            }}
          >
            <span className="text-[12px] text-crt-green/50 font-code flex items-center gap-1.5 min-w-0 truncate" style={{ letterSpacing: "0.05em" }}>
              <span style={{ opacity: 0.6 }}>⠿</span> <FilesIcon size={13} /> FILES
              <span className="truncate normal-case" style={{ opacity: 0.5, letterSpacing: 0 }} title={filesDisplayPath()}>
                {filesDisplayPath()}
              </span>
            </span>
            <div className="flex items-center gap-1">
              {filesToolbarControls({ float: false })}
              <button
                onClick={() => setFilesPanelFloating(false)}
                className="text-[11px] px-1.5 py-0.5 border border-crt-amber/30 text-crt-amber/50 hover:text-crt-amber hover:border-crt-amber transition-all"
                title="Dock panel"
              >
                ⊡
              </button>
              <button
                onClick={() => { setFilesPanelFloating(false); setSidebarView("overview"); }}
                className="text-[11px] px-1.5 py-0.5 border border-crt-red/30 text-crt-red/50 hover:text-crt-red hover:border-crt-red transition-all"
                title="Close"
              >
                ✕
              </button>
            </div>
          </div>
          {/* Panel content */}
          <div className="flex-1 flex flex-col overflow-hidden">
            {renderDirectoryTab()}
          </div>
          {/* Resize handle (bottom-right corner) */}
          <div
            className="absolute bottom-0 right-0 w-4 h-4"
            style={{ cursor: "se-resize" }}
            onMouseDown={(e) => {
              e.preventDefault();
              e.stopPropagation();
              filesPanelResize.current = { startX: e.clientX, startY: e.clientY, origW: filesPanelSize.w, origH: filesPanelSize.h, edge: "se" };
              document.body.style.cursor = 'se-resize';
              document.body.style.userSelect = 'none';
              setIframesInert(true);
            }}
          >
            <svg width="16" height="16" viewBox="0 0 16 16" className="text-white/15 hover:text-white/30 transition-colors">
              <path d="M14 14L8 14L14 8Z" fill="currentColor" />
              <path d="M14 14L11 14L14 11Z" fill="currentColor" opacity="0.5" />
            </svg>
          </div>
          {/* Resize edges */}
          <div className="absolute top-0 right-0 bottom-0 w-1 cursor-e-resize"
            onMouseDown={(e) => { e.preventDefault(); filesPanelResize.current = { startX: e.clientX, startY: e.clientY, origW: filesPanelSize.w, origH: filesPanelSize.h, edge: "e" }; document.body.style.cursor = 'e-resize'; document.body.style.userSelect = 'none'; setIframesInert(true); }}
          />
          <div className="absolute bottom-0 left-0 right-0 h-1 cursor-s-resize"
            onMouseDown={(e) => { e.preventDefault(); filesPanelResize.current = { startX: e.clientX, startY: e.clientY, origW: filesPanelSize.w, origH: filesPanelSize.h, edge: "s" }; document.body.style.cursor = 's-resize'; document.body.style.userSelect = 'none'; setIframesInert(true); }}
          />
        </div>
      )}

      {/* ── Status Bar ───────────────────────────────────────────── */}
      {/* Kill Process Dialog (Cmd+K) — owner-only */}
      {showKillDialog && isOwner && (
        <div style={{
          position: "fixed", inset: 0, zIndex: 9999,
          background: "rgba(0,0,0,0.6)", display: "flex", alignItems: "flex-start",
          justifyContent: "center", paddingTop: "20vh",
        }} onClick={() => setShowKillDialog(false)}>
          <div onClick={(e) => e.stopPropagation()} style={{
            background: isLight ? "#fff" : "#1a1a2e",
            border: `1px solid ${isLight ? "rgba(239,68,68,0.3)" : "rgba(248,113,113,0.3)"}`,
            borderRadius: 8, padding: 20, width: 400,
            fontFamily: "var(--font-mono)", fontSize: 13,
            boxShadow: "0 8px 32px rgba(0,0,0,0.4)",
          }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
              <span style={{ color: "#f87171", fontWeight: 600, fontSize: 14 }}>KILL PROCESS</span>
              <span style={{ color: isLight ? "#999" : "#666", fontSize: 11 }}>⌘K</span>
            </div>
            <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
              {(["port", "pid"] as const).map((m) => (
                <button key={m} onClick={() => { setKillMode(m); setKillInput(""); setKillResult(null); }} style={{
                  padding: "4px 12px", borderRadius: 4, cursor: "pointer",
                  background: killMode === m ? (isLight ? "rgba(239,68,68,0.1)" : "rgba(248,113,113,0.15)") : "transparent",
                  border: `1px solid ${killMode === m ? "rgba(248,113,113,0.4)" : (isLight ? "rgba(0,0,0,0.1)" : "rgba(255,255,255,0.1)")}`,
                  color: killMode === m ? "#f87171" : (isLight ? "#666" : "#888"),
                  fontSize: 12, fontFamily: "var(--font-mono)",
                }}>{m.toUpperCase()}</button>
              ))}
              <div style={{ flex: 1 }} />
              {(["SIGKILL", "SIGTERM"] as const).map((s) => (
                <button key={s} onClick={() => setKillSignal(s)} style={{
                  padding: "4px 8px", borderRadius: 4, cursor: "pointer",
                  background: killSignal === s ? (isLight ? "rgba(239,68,68,0.1)" : "rgba(248,113,113,0.15)") : "transparent",
                  border: `1px solid ${killSignal === s ? "rgba(248,113,113,0.4)" : (isLight ? "rgba(0,0,0,0.1)" : "rgba(255,255,255,0.1)")}`,
                  color: killSignal === s ? "#f87171" : (isLight ? "#666" : "#888"),
                  fontSize: 11, fontFamily: "var(--font-mono)",
                }}>{s === "SIGKILL" ? "KILL -9" : "TERM -15"}</button>
              ))}
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <input
                ref={killInputRef}
                type="text"
                placeholder={killMode === "port" ? "Port number (e.g. 8820)" : "Process ID"}
                value={killInput}
                onChange={(e) => { setKillInput(e.target.value); setKillResult(null); }}
                onKeyDown={(e) => { if (e.key === "Enter") executeKill(); if (e.key === "Escape") setShowKillDialog(false); }}
                style={{
                  flex: 1, padding: "8px 12px", borderRadius: 4,
                  background: isLight ? "rgba(0,0,0,0.03)" : "rgba(255,255,255,0.05)",
                  border: `1px solid ${isLight ? "rgba(0,0,0,0.1)" : "rgba(255,255,255,0.1)"}`,
                  color: isLight ? "#1a1a1a" : "#e5e5e5",
                  fontFamily: "var(--font-mono)", fontSize: 14, outline: "none",
                }}
              />
              <button onClick={executeKill} disabled={killLoading || !killInput.trim()} style={{
                padding: "8px 16px", borderRadius: 4, cursor: "pointer",
                background: killLoading ? (isLight ? "#ddd" : "#333") : "#ef4444",
                border: "none", color: "#fff", fontWeight: 600,
                fontFamily: "var(--font-mono)", fontSize: 13,
                opacity: killLoading || !killInput.trim() ? 0.5 : 1,
              }}>{killLoading ? "..." : "KILL"}</button>
            </div>
            {killResult && (
              <div style={{
                marginTop: 12, padding: 10, borderRadius: 4,
                background: killResult.error
                  ? (isLight ? "rgba(239,68,68,0.05)" : "rgba(248,113,113,0.08)")
                  : (isLight ? "rgba(16,185,129,0.05)" : "rgba(52,211,153,0.08)"),
                border: `1px solid ${killResult.error
                  ? "rgba(248,113,113,0.2)"
                  : "rgba(52,211,153,0.2)"}`,
                fontSize: 12, color: isLight ? "#333" : "#ccc",
              }}>
                {killResult.error ? (
                  <span style={{ color: "#f87171" }}>{killResult.error}</span>
                ) : (
                  <>
                    {killResult.killed?.length > 0 && (
                      <div style={{ color: "#34d399" }}>
                        Killed PID{killResult.killed.length > 1 ? "s" : ""}: {killResult.killed.join(", ")} ({killResult.signal})
                      </div>
                    )}
                    {killResult.killed?.length === 0 && (
                      <div style={{ color: "#fbbf24" }}>No processes found</div>
                    )}
                    {killResult.errors?.length > 0 && (
                      <div style={{ color: "#f87171", marginTop: 4 }}>{killResult.errors.join("; ")}</div>
                    )}
                  </>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Sign-in drawer — connect a wallet when there's no session. Lives over
          the hub as a right-side panel (the hub stays visible behind it)
          instead of the old full-screen sign-in takeover. Starts BELOW the
          header (--header-h) so it docks under it rather than covering it. */}
      {!token && signInOpen && (
        <div
          className="fixed right-0 z-[150] flex flex-col"
          style={{
            top: "var(--header-h, 0px)",
            height: "calc(100% - var(--header-h, 0px))",
            width: 360,
            maxWidth: "100vw",
            borderLeft: `1px solid ${subtleBorder}`,
            boxShadow: "-8px 0 32px rgba(0,0,0,0.45)",
          }}
        >
          {renderConnectPanel()}
        </div>
      )}
      {/* Collapsed tab to re-open the drawer after it's been dismissed while
          still signed out. */}
      {!token && !signInOpen && (
        <button
          onClick={() => setSignInOpen(true)}
          className="fixed top-1/2 right-0 -translate-y-1/2 z-[150] px-2 py-3 text-[11px] font-mono tracking-wider"
          style={{
            writingMode: "vertical-rl",
            background: "var(--bg-secondary)",
            color: "var(--crt-amber)",
            border: `1px solid ${subtleBorder}`,
            borderRight: "none",
            borderRadius: "6px 0 0 6px",
          }}
          title="Sign in"
        >
          SIGN IN
        </button>
      )}

      {/* The standalone wallet modal is gone — wallet UI lives only inside the
          merged account sidebar (WalletModal rendered embedded above). */}

      {/* Sudo Authorization — privileged cross-module operations */}
      {sudoReq && (
        <SudoModal
          open={!!sudoReq}
          action={sudoReq.action}
          target={sudoReq.target}
          status={sudoStatus}
          error={sudoError}
          signerLabel={address ? `${address.slice(0, 6)}…${address.slice(-4)}` : null}
          sessionMins={
            sudoInfo && sudoInfo.sessionSecs > 0 && !sudoInfo.alwaysAsk.includes(sudoReq.action)
              ? Math.round(sudoInfo.sessionSecs / 60)
              : null
          }
          onAuthorize={confirmSudo}
          onCancel={cancelSudo}
        />
      )}

    </div>
  );
}
