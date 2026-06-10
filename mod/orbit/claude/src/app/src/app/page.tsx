"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import dynamic from "next/dynamic";
import { VersionsPanel } from "../components/VersionsPanel";

const WalletModal = dynamic(() => import("../components/WalletModal"), { ssr: false });

import {
  getNetworkName,
  NETWORK_LOGOS,
  EVM_NETWORKS,
  switchNetwork,
} from "../utils/wallet";

const DEFAULT_BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "/claude";
const DEFAULT_API_URL = process.env.NEXT_PUBLIC_API_URL || `/api${DEFAULT_BASE_PATH}`;
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

// ── Helpers ──────────────────────────────────────────────────────────

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
// claude-opus-4-7). Keep first item = current default Opus.
const MODEL_OPTIONS = [
  { value: "claude-opus-4-7",   label: "Opus 4.7",   family: "opus",   color: "#a78bfa" },
  { value: "claude-opus-4-6",   label: "Opus 4.6",   family: "opus",   color: "#9f7aea" },
  { value: "claude-sonnet-4-6", label: "Sonnet 4.6", family: "sonnet", color: "#60a5fa" },
  { value: "claude-sonnet-4-5", label: "Sonnet 4.5", family: "sonnet", color: "#3b82f6" },
  { value: "claude-haiku-4-5",  label: "Haiku 4.5",  family: "haiku",  color: "#34d399" },
  { value: "fable",             label: "Fable",      family: "fable",  color: "#f59e0b" },
];

// Legacy aliases (saved by older builds / older jobs) → upgraded value.
const MODEL_ALIAS_UPGRADE: Record<string, string> = {
  opus: "claude-opus-4-7",
  sonnet: "claude-sonnet-4-6",
  haiku: "claude-haiku-4-5",
};

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

// ── Agent engines ───────────────────────────────────────────────────
// The "engine" is which agent harness module is driving the work. Today the
// app runs claude-code; the dropdown stays here so the same UI can later
// route to aider / codex / cursor without a redesign.
const ENGINE_OPTIONS = [
  { value: "claude-code", label: "Claude Code", icon: "⬡", color: "#a78bfa", available: true,  hint: "Anthropic's claude-code CLI (this module)" },
  { value: "aider",       label: "Aider",       icon: "✦", color: "#60a5fa", available: false, hint: "orbit/aider — coming soon" },
  { value: "codex",       label: "Codex",       icon: "◇", color: "#fbbf24", available: false, hint: "orbit/codex — coming soon" },
  { value: "cursor",      label: "Cursor",      icon: "◆", color: "#f472b6", available: false, hint: "orbit/cursor — coming soon" },
];

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
  │          M O D   A I                 │
  │                                      │
  │       Agent Runner  v1               │
  │                                      │
  │    Background AI Agent Platform      │
  │                                      │
  └──────────────────────────────────────┘`;

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

  const [jobs, setJobs] = useState<Job[]>([]);
  const [draggedJobId, setDraggedJobId] = useState<string | null>(null);
  const [dragOverJobId, setDragOverJobId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState("claude-opus-4-7");
  const [agentType, setAgentType] = useState("default");
  // The agent "engine" — i.e. which agent harness module drives the work.
  // Defaults to claude-code (this module); we expose the toggle so the same UI
  // can later swap in aider / codex / cursor without ripping out the panel.
  const [engine, setEngine] = useState("claude-code");

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
  const [githubUrl, setGithubUrl] = useState("");
  const [anchorDir, setAnchorDir] = useState("~/mod");
  const [modules, setModules] = useState<string[]>([]);
  const [moduleList, setModuleList] = useState<Array<{
    name: string; path: string; display: string; category: string; has_config: boolean;
    app_url: string | null; api_url: string | null; description: string | null;
    fns: string[]; has_app_dir: boolean; has_server_dir: boolean; has_api_dir: boolean;
    owner: string | null; version: string | null; cid: string | null; created_at: number | null;
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
  const [expandedAsks, setExpandedAsks] = useState<Set<string>>(new Set());
  const [expandedPrompts, setExpandedPrompts] = useState<Set<string>>(new Set());
  const [expandedJobImage, setExpandedJobImage] = useState<string | null>(null);
  const [images, setImages] = useState<Array<{ name: string; data: string }>>(
    []
  );
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [showUserDetails, setShowUserDetails] = useState(false);
  const [showVersions, setShowVersions] = useState(false);

  // Inline versions strip in the AGENT panel — recent snapshots of the
  // currently-selected module. Polls every 30s; clicking the strip opens
  // the full VersionsPanel for fork/restore actions.
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

  // File viewer state
  const [viewingFile, setViewingFile] = useState<string | null>(null);
  const [viewingFileContent, setViewingFileContent] = useState<string>("");
  const [viewingFileLoading, setViewingFileLoading] = useState(false);
  const [editingFile, setEditingFile] = useState(false);
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

  // Wallet modal
  const [showWalletModal, setShowWalletModal] = useState(false);
  const [copiedAddress, setCopiedAddress] = useState(false);
  const [profileMenuOpen, setProfileMenuOpen] = useState(false);
  const profileMenuRef = useRef<HTMLDivElement>(null);
  const [showWalletSidebar, setShowWalletSidebar] = useState(false);
  const [walletSidebarWidth, setWalletSidebarWidth] = useState(520);
  const [isWalletSidebarDragging, setIsWalletSidebarDragging] = useState(false);

  // Owner sidebar (persistent right panel, mirrors wallet pattern)
  const [showOwnerSidebar, setShowOwnerSidebar] = useState(false);
  const [ownerSidebarWidth, setOwnerSidebarWidth] = useState(420);
  const [isOwnerSidebarDragging, setIsOwnerSidebarDragging] = useState(false);
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
  const [viewMode, setViewMode] = useState<"output" | "code">("output");
  const [directoryTree, setDirectoryTree] = useState<any[]>([]);
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(new Set());

  // Agent sidebar state (persistent right panel)
  const [tasksSidebarOpen, setTasksSidebarOpen] = useState(true);
  const [sidebarWidth, setSidebarWidth] = useState(480);
  const [isSidebarDragging, setIsSidebarDragging] = useState(false);
  const [isLeftDragging, setIsLeftDragging] = useState(false);
  const [sidebarView, setSidebarView] = useState<"tasks" | "app" | "api" | "overview" | "files" | "logs" | "terminal" | "versions">("overview");
  // First-level nav category — sits above the mod tabs. The mod tabs
  // (overview/app/api/files/terminal) form the second level. AGENT here
  // doubles as the sidebar toggle.
  const [activeCategory, setActiveCategory] = useState<"explore" | "build" | "agent">("build");

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
  };
  const [terminalHistory, setTerminalHistory] = useState<TerminalEntry[]>([]);
  const [terminalInput, setTerminalInput] = useState("");
  const [terminalRunning, setTerminalRunning] = useState(false);
  const [terminalRecallIdx, setTerminalRecallIdx] = useState<number | null>(null);

  // Top-of-panel tabs inside the AGENT sidebar — switch between agent
  // settings (engine/model/persona), version history, and the task list.
  const [agentPanelTab, setAgentPanelTab] = useState<"info" | "versions" | "tasks">("tasks");

  // Left sidebar (agent) and right sidebar (wallet)
  const [leftSidebarOpen, setLeftSidebarOpen] = useState(true);
  const [leftSidebarWidth, setLeftSidebarWidth] = useState(420);
  const [agentEnabled, setAgentEnabled] = useState(true);
  const [agentFullscreen, setAgentFullscreen] = useState(false);
  const [agentSidebarOpen, setAgentSidebarOpen] = useState(true);
  const [agentSidebarWidth, setAgentSidebarWidth] = useState(520);
  const [isAgentSidebarDragging, setIsAgentSidebarDragging] = useState(false);

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
  // On phone, default the AGENT side panel to CLOSED so the module's
  // app gets the whole viewport. The user can still open it from the
  // AGENT tab in the header.
  useEffect(() => {
    if (isMobile) setAgentSidebarOpen(false);
  }, [isMobile]);
  const [sidebarSide, setSidebarSide] = useState<"left" | "right">("right");

  const moduleDropdownRef = useRef<HTMLDivElement>(null);
  const inlineModuleRef = useRef<HTMLDivElement>(null);
  const headerModuleRef = useRef<HTMLDivElement>(null);
  const agentModuleRef = useRef<HTMLDivElement>(null);
  const [showInlineModuleDropdown, setShowInlineModuleDropdown] = useState(false);
  const [showAgentModuleDropdown, setShowAgentModuleDropdown] = useState(false);
  const [agentModuleSearch, setAgentModuleSearch] = useState("");
  const [showHeaderModuleDropdown, setShowHeaderModuleDropdown] = useState(false);
  const [headerModuleSearch, setHeaderModuleSearch] = useState("");
  const [ownerFilter, setOwnerFilter] = useState<string | null>(null);
  const [folderSuggestions, setFolderSuggestions] = useState<Array<{
    name: string; path: string; display: string; score: number; preview: string;
    has_config: boolean; has_mod: boolean;
  }>>([]);
  const [folderList, setFolderList] = useState<Array<{
    name: string; path: string; display: string; has_config: boolean; has_mod: boolean;
  }>>([]);
  const [selectorMode, setSelectorMode] = useState<"modules" | "folders">("modules");
  const [showHeaderCreateForm, setShowHeaderCreateForm] = useState<"create" | "fork" | "edit" | null>(null);
  const [headerNewName, setHeaderNewName] = useState("");
  const [headerGithubUrl, setHeaderGithubUrl] = useState("");
  const [headerEditPrompt, setHeaderEditPrompt] = useState("");

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

  // Kill process dialog state (host key: Cmd+K)
  const [showKillDialog, setShowKillDialog] = useState(false);
  const [killInput, setKillInput] = useState("");
  const [killMode, setKillMode] = useState<"pid" | "port">("port");
  const [killSignal, setKillSignal] = useState<"SIGKILL" | "SIGTERM">("SIGKILL");
  const [killResult, setKillResult] = useState<any>(null);
  const [killLoading, setKillLoading] = useState(false);
  const killInputRef = useRef<HTMLInputElement>(null);

  const headerCreateRef = useRef<HTMLDivElement>(null);
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
  // On phone we jump straight to APP (when the module has one) so the
  // user lands on the real interface instead of the OVERVIEW chrome.
  const getBestTab = useCallback((info: typeof moduleList[0] | null): "overview" | "app" | "api" | "files" => {
    if (isMobile && (info?.app_url || info?.has_app_dir)) return "app";
    return "overview";
  }, [isMobile]);

  // Reset all module-specific state when switching modules
  const resetModuleState = useCallback((newModuleInfo?: typeof moduleList[0] | null) => {
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
    setViewingFile(null);
    setViewingFileContent("");
    // Directory tree
    setDirectoryTree([]);
    setExpandedDirs(new Set());
    // JSON tree
    setCollapsedPaths(new Set());
    // Auto-select the best tab for the new module
    setSidebarView(getBestTab(newModuleInfo || null));
  }, [getBestTab]);

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
            `${apiUrl}/files/search?path=${encodeURIComponent(sd)}&query=${encodeURIComponent(inlineSearchQuery)}`
          );
          if (r.ok) { const d = await r.json(); setInlineSearchResults(d.results || []); }
        } else {
          const p = new URLSearchParams({ path: sd, query: inlineSearchQuery });
          const r = await fetch(`${apiUrl}/files/grep?${p}`);
          if (r.ok) { const d = await r.json(); setInlineSearchResults(d.matches || []); }
        }
      } catch { /* ignore */ }
      setInlineSearchLoading(false);
      setInlineSelectedIndex(0);
    }, 300);
    return () => clearTimeout(tid);
  }, [inlineSearchQuery, inlineSearchMode, workDir, selectedJob, jobs, apiUrl]);

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
      }
      if (headerCreateRef.current && !headerCreateRef.current.contains(e.target as Node)) {
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
      const upgraded = MODEL_ALIAS_UPGRADE[savedModel] || savedModel;
      setModel(upgraded);
      if (upgraded !== savedModel) safeSetItem("claude_jobs_model", upgraded);
    }

    const savedAgent = localStorage.getItem("claude_jobs_agent");
    if (savedAgent) setAgentType(savedAgent);

    const savedEngine = localStorage.getItem("claude_jobs_engine");
    if (savedEngine && ENGINE_OPTIONS.some(e => e.value === savedEngine && e.available)) {
      setEngine(savedEngine);
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
    const savedAgent = localStorage.getItem("claude_agent_enabled");
    if (savedAgent !== null) setAgentEnabled(savedAgent === "true");
    const savedSide = localStorage.getItem("claude_sidebar_side");
    if (savedSide === "left" || savedSide === "right") setSidebarSide(savedSide);
    const savedAgentWidth = localStorage.getItem("claude_agent_sidebar_width");
    if (savedAgentWidth) setAgentSidebarWidth(parseInt(savedAgentWidth, 10));
    const savedWalletOpen = localStorage.getItem("claude_wallet_sidebar_open");
    if (savedWalletOpen !== null) setShowWalletSidebar(savedWalletOpen === "true");
    const savedWalletWidth = localStorage.getItem("claude_wallet_sidebar_width");
    if (savedWalletWidth) setWalletSidebarWidth(parseInt(savedWalletWidth, 10));
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
    safeSetItem("claude_agent_enabled", String(agentEnabled));
  }, [agentEnabled]);

  useEffect(() => {
    safeSetItem("claude_sidebar_side", sidebarSide);
  }, [sidebarSide]);

  useEffect(() => {
    safeSetItem("claude_agent_sidebar_width", String(agentSidebarWidth));
  }, [agentSidebarWidth]);

  useEffect(() => {
    safeSetItem("claude_wallet_sidebar_open", String(showWalletSidebar));
  }, [showWalletSidebar]);

  useEffect(() => {
    safeSetItem("claude_wallet_sidebar_width", String(walletSidebarWidth));
  }, [walletSidebarWidth]);

  useEffect(() => {
    safeSetItem("claude_owner_sidebar_open", String(showOwnerSidebar));
  }, [showOwnerSidebar]);

  useEffect(() => {
    safeSetItem("claude_owner_sidebar_width", String(ownerSidebarWidth));
  }, [ownerSidebarWidth]);

  // Check saved token or detect local mode
  useEffect(() => {
    const saved = localStorage.getItem("claude_jobs_token");
    const savedAddr = localStorage.getItem("claude_jobs_address");
    const savedWalletType = localStorage.getItem("claude_jobs_wallet_type") as typeof walletType;
    if (saved && savedAddr) {
      setToken(saved);
      setAddress(savedAddr);
      setWalletType(savedWalletType);
      return;
    }
    // The user explicitly signed out — stay on the auth screen
    if (localStorage.getItem("claude_jobs_signed_out") === "1") return;
    // Probe server — if local mode is on, skip auth entirely
    fetch(`${apiUrl}/health`)
      .then((r) => r.json())
      .then(() => {
        // Try an unauthed request to /jobs — if it works, server is in local mode
        return fetch(`${apiUrl}/jobs`);
      })
      .then((r) => {
        if (r.ok) {
          // Local mode — no auth needed
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
  // AGENT panel can render the inline VERSIONS strip without opening
  // the full VersionsPanel.
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

  // Check owner status when address changes
  useEffect(() => {
    // Everyone is an owner
    setIsOwner(true);
    setOwnerAddress(address);
  }, [address]);

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

    const challengeRes = await fetch(
      `${apiUrl}/auth/challenge?address=${addr}`
    );
    if (!challengeRes.ok) throw new Error("CHALLENGE REQUEST FAILED");
    const { message } = await safeJson(challengeRes);
    if (!message) throw new Error("INVALID CHALLENGE RESPONSE");

    const signature = await signFn(message);

    const verifyRes = await fetch(`${apiUrl}/auth/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ address: addr, signature, message }),
    });

    const verifyData = await safeJson(verifyRes);
    if (!verifyRes.ok) {
      throw new Error(verifyData.error || "SIGNATURE VERIFICATION FAILED");
    }

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
      setAuthError(msg === "Load failed" || msg === "Failed to fetch" ? "API OFFLINE — start the backend first" : msg || "AUTHENTICATION FAILED");
    } finally {
      setAuthLoading(false);
    }
  };

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
    localStorage.removeItem("claude_jobs_token");
    localStorage.removeItem("claude_jobs_address");
    localStorage.removeItem("claude_jobs_wallet_type");
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
        return response;
      } catch (error) {
        clearTimeout(timeoutId);
        throw error;
      }
    },
    [token, apiUrl]
  );

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
          workDir: `${anchorDir.replace("~", process.env.HOME || "/Users/broski")}/mod/orbit/claude/api`,
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
    const apiDir = `${anchorDir.replace("~", process.env.HOME || "/Users/broski")}/mod/orbit/claude/api`;
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

  // Load file content for viewer
  const loadFileContent = useCallback(async (filePath: string) => {
    setViewingFile(filePath);
    setViewingFileLoading(true);
    setEditingFile(false);
    try {
      const res = await fetch(`${apiUrl}/files/content?path=${encodeURIComponent(filePath)}`);
      if (res.ok) {
        const data = await res.json();
        setViewingFileContent(data.content || "");
      } else {
        setViewingFileContent("// Failed to load file");
      }
    } catch {
      setViewingFileContent("// Error loading file");
    } finally {
      setViewingFileLoading(false);
    }
  }, [apiUrl]);

  const saveFile = useCallback(async () => {
    if (!viewingFile || !token) return;
    setSavingFile(true);
    try {
      const res = await authFetch("/files/write", {
        method: "POST",
        body: JSON.stringify({ path: viewingFile, content: editBuffer }),
      });
      if (res.ok) {
        setViewingFileContent(editBuffer);
        setEditingFile(false);
      } else {
        const data = await res.json().catch(() => ({}));
        setError(data.error || "Failed to save file");
      }
    } catch {
      setError("Error saving file");
    } finally {
      setSavingFile(false);
    }
  }, [viewingFile, editBuffer, token, authFetch]);

  // Navigate file tree to show the parent folder of a file and expand all ancestor dirs
  const navigateToFile = useCallback(async (filePath: string) => {
    // Extract parent directory
    const parentDir = filePath.substring(0, filePath.lastIndexOf("/"));
    if (!parentDir) return;

    // Fetch the tree for the parent directory so we see sibling files
    try {
      const res = await fetch(`${apiUrl}/files/tree?path=${encodeURIComponent(parentDir)}`);
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
  }, [apiUrl]);

  const fetchDirectoryTree = useCallback(async (path?: string) => {
    try {
      // workDir wins over the selected job's work_dir so an explicit module
      // switch in the header always re-syncs FILES; the job dir is only a
      // fallback for the pre-selection initial state.
      const targetPath = path || workDir || (selectedJob ? jobs.find(j => j.id === selectedJob)?.work_dir : null) || "~/mod";
      const res = await fetch(`${apiUrl}/files/tree?path=${encodeURIComponent(targetPath)}`);
      if (res.ok) {
        const data = await res.json();
        const tree = data.tree || [];
        setDirectoryTree(tree);
        // Start with all folders collapsed
        setExpandedDirs(new Set());
        // Auto-select config.json
        const configFile = tree.find((n: any) => n.type === "file" && n.name === "config.json");
        if (configFile) {
          loadFileContent(configFile.path);
        }
      }
    } catch (e) {
      console.error("Failed to fetch directory tree:", e);
    }
  }, [selectedJob, jobs, workDir, apiUrl, loadFileContent]);

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

  // Handle AGENT sidebar resize dragging. The panel sits on the left with
  // its drag handle on the right edge — dragging right grows the panel.
  useEffect(() => {
    if (!isAgentSidebarDragging) return;
    const handleMouseMove = (e: MouseEvent) => {
      const ww = window.innerWidth;
      const newWidth = e.clientX;
      const maxWidth = Math.max(380, ww * 0.95);
      setAgentSidebarWidth(Math.max(320, Math.min(maxWidth, newWidth)));
    };
    const handleMouseUp = () => setIsAgentSidebarDragging(false);
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
  }, [isAgentSidebarDragging]);

  // Handle WALLET sidebar resize dragging. The panel sits on the right with
  // its drag handle on the LEFT edge — dragging left grows the panel.
  useEffect(() => {
    if (!isWalletSidebarDragging) return;
    const handleMouseMove = (e: MouseEvent) => {
      const ww = window.innerWidth;
      const newWidth = ww - e.clientX;
      const maxWidth = Math.max(380, ww * 0.95);
      setWalletSidebarWidth(Math.max(320, Math.min(maxWidth, newWidth)));
    };
    const handleMouseUp = () => setIsWalletSidebarDragging(false);
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
  }, [isWalletSidebarDragging]);

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

  useEffect(() => {
    safeSetItem("claude_files_panel", JSON.stringify({ pos: filesPanelPos, size: filesPanelSize, floating: filesPanelFloating }));
  }, [filesPanelPos, filesPanelSize, filesPanelFloating]);

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

      // Send personality system prompt if active
      if (activePersonality && activePersonality.prompt) {
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
      if (!res.ok) throw new Error("SUBMIT FAILED");
      const job = await res.json();
      setPrompt("");
      setImages([]);
      setModuleName("");
      setGithubUrl("");
      setSelectedJob(job.id);
      fetchJobs();
      startStream(job.id);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSubmitting(false);
    }
  };

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
  const [renamingModule, setRenamingModule] = useState<string | null>(null);
  const [renameInput, setRenameInput] = useState("");
  const [moduleManageSearch, setModuleManageSearch] = useState("");

  const renameModule = async (oldName: string, newName: string) => {
    if (!token || !newName.trim()) return;
    try {
      const res = await authFetch(`/modules/${encodeURIComponent(oldName)}/rename`, {
        method: "PUT",
        body: JSON.stringify({ new_name: newName.trim() }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({ error: "Rename failed" }));
        setError(data.error || "RENAME FAILED");
        return;
      }
      setRenamingModule(null);
      setRenameInput("");
      if (selectedModule === oldName) {
        setSelectedModule(newName.trim());
        setSelectedModuleInfo(null);
        setModuleConfig(null);
        fetchModuleConfig(newName.trim());
      }
      fetchModules();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const deleteModule = async (name: string) => {
    if (!token) return;
    try {
      const res = await authFetch(`/modules/${encodeURIComponent(name)}`, { method: "DELETE" });
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
    const match = workDir.match(/\/orbit\/([^/]+)/);
    return match ? match[1] : null;
  };

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
      if (!res.ok) throw new Error("SUBMIT FAILED");
      const job = await res.json();
      setHeaderNewName("");
      setHeaderGithubUrl("");
      setHeaderEditPrompt("");
      setShowHeaderCreateForm(null);
      setSelectedJob(job.id);
      fetchJobs();
      startStream(job.id);
      // Open agent sidebar to see progress
      setAgentSidebarOpen(true);
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
      const body: any = { prompt: job.prompt, model: job.model };
      if (job.work_dir) body.work_dir = job.work_dir;
      const res = await authFetch("/jobs", {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error("SUBMIT FAILED");
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
  const getPortForTarget = useCallback((target: "api" | "app"): string | null => {
    const config = moduleConfig?.config || directConfig;
    if (target === "api") {
      const apiUrl = selectedModuleInfo?.api_url || config?.urls?.api || config?.api_url || config?.servers?.["claude-api"];
      const portMatch = apiUrl?.match(/:(\d+)/);
      return config?.port?.toString() || portMatch?.[1] || null;
    } else {
      const appUrl = selectedModuleInfo?.app_url || config?.urls?.app || config?.app_url || config?.servers?.["claude-app"];
      const portMatch = appUrl?.match(/:(\d+)/);
      return portMatch?.[1] || null;
    }
  }, [selectedModuleInfo, moduleConfig, directConfig]);

  const stopProcess = useCallback(async (target: "api" | "app") => {
    if (!selectedModuleInfo || !token) return;
    const setToggling = target === "api" ? setTogglingApi : setTogglingApp;
    setToggling(true);
    try {
      const port = getPortForTarget(target);
      if (port) {
        await authFetch("/kill", {
          method: "POST",
          body: JSON.stringify({ port: parseInt(port), signal: "SIGKILL" }),
        });
      }
      setTimeout(() => { checkModuleHealth(); setToggling(false); }, 2000);
    } catch { setToggling(false); }
  }, [selectedModuleInfo, token, getPortForTarget, authFetch, checkModuleHealth]);

  const startProcess = useCallback(async (target: "api" | "app") => {
    if (!selectedModuleInfo || !token) return;
    const setToggling = target === "api" ? setTogglingApi : setTogglingApp;
    setToggling(true);
    try {
      const config = moduleConfig?.config || directConfig;
      if (target === "api") {
        const startScript = config?.scripts?.start;
        await authFetch("/jobs", {
          method: "POST",
          body: JSON.stringify({
            prompt: startScript
              ? `Run the API start script in the background: bash ${startScript} &`
              : `Start this module's API server. Look for start.sh in the current directory or scripts/start.sh and run it in the background. If there is no start.sh, look for a Python module with mod.py and run: python -m uvicorn {module_name}.mod:app --host 0.0.0.0 --port {port} where you determine the module_name and port from config.json`,
            model: "haiku",
            work_dir: selectedModuleInfo.path,
          }),
        });
      } else {
        const port = getPortForTarget("app");
        await authFetch("/jobs", {
          method: "POST",
          body: JSON.stringify({
            prompt: `Start this module's Next.js app. Run: cd app && npm install --if-present && npx next dev -p ${port || "3000"} in the background.`,
            model: "haiku",
            work_dir: selectedModuleInfo.path,
          }),
        });
      }
      setTimeout(() => { checkModuleHealth(); setToggling(false); }, 3000);
    } catch { setToggling(false); }
  }, [selectedModuleInfo, token, moduleConfig, directConfig, getPortForTarget, authFetch, checkModuleHealth]);

  const restartProcess = useCallback(async (target: "api" | "app") => {
    const setToggling = target === "api" ? setTogglingApi : setTogglingApp;
    setToggling(true);
    const port = getPortForTarget(target);
    if (port) {
      try {
        await authFetch("/kill", {
          method: "POST",
          body: JSON.stringify({ port: parseInt(port), signal: "SIGKILL" }),
        });
      } catch { /* ignore kill errors */ }
      await new Promise(r => setTimeout(r, 1500));
    }
    setToggling(false);
    await startProcess(target);
  }, [getPortForTarget, authFetch, startProcess]);

  // Fetch module logs (API and App)
  const fetchModuleLogs = useCallback(async () => {
    if (!selectedModule || !token) return;
    setModuleLogsLoading(true);
    try {
      const res = await authFetch("/forward", {
        method: "POST",
        body: JSON.stringify({ fn: "app_logs", name: selectedModule, lines: 200 }),
      });
      if (res.ok) {
        const data = await res.json();
        if (data && typeof data === "object" && !data.error) {
          setModuleLogs(typeof data === "string" ? { stdout: data } : data);
        }
      }
    } catch { /* ignore */ }
    setModuleLogsLoading(false);
  }, [selectedModule, token, authFetch]);

  // Auto-refresh logs (in overview's inline logs panel or the dedicated LOGS tab)
  useEffect(() => {
    const inLogsTab = sidebarView === "logs";
    if (!moduleLogsAutoRefresh || (!moduleLogsOpen && !inLogsTab)) return;
    const iv = setInterval(fetchModuleLogs, 4000);
    return () => clearInterval(iv);
  }, [moduleLogsAutoRefresh, moduleLogsOpen, sidebarView, fetchModuleLogs]);

  // Fetch logs when opened or when entering the LOGS tab
  useEffect(() => {
    if (moduleLogsOpen || sidebarView === "logs") fetchModuleLogs();
  }, [moduleLogsOpen, sidebarView]);

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
      if (agentModuleRef.current && !agentModuleRef.current.contains(e.target as Node)) {
        setShowAgentModuleDropdown(false);
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

  if (!token) {
    return (
      <div className="h-screen w-screen flex relative overflow-hidden" style={{ background: "var(--bg-primary)" }}>
        {/* Sign-in Sidebar */}
        <div
          className="relative z-10 h-full w-[360px] shrink-0 flex flex-col overflow-y-auto transition-all duration-500"
          style={{
            background: "var(--bg-secondary)",
            borderRight: `1px solid ${subtleBorder}`,
            opacity: bootPhase >= 3 ? 1 : 0,
            transform: bootPhase >= 3 ? "translateX(0)" : "translateX(-10px)",
          }}
        >
          <div className="flex-1 flex flex-col justify-center p-6">
            <div className="flex items-center gap-3 mb-4">
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

            <div className="text-[13px] mb-5 leading-relaxed" style={{ color: "var(--text-tertiary)" }}>
              Sign a cryptographic challenge to authenticate.
              Your signature is verified server-side via ecrecover and
              becomes a 24-hour bearer token for all API requests.
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
                Password derives a deterministic wallet key via keccak256
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

        {/* Main Area — boot art + system status */}
        <div className="relative flex-1 flex flex-col items-center justify-center overflow-hidden">
          {/* Ambient glow */}
          <div
            className="absolute inset-0 pointer-events-none"
            style={{
              background: isLight
                ? "radial-gradient(ellipse at center, rgba(0,0,0,0.02) 0%, transparent 70%)"
                : "radial-gradient(ellipse at center, rgba(16,185,129,0.03) 0%, transparent 70%)",
            }}
          />

          <div className="relative z-10 flex flex-col items-center gap-6 max-w-2xl w-full px-4">
            {/* Boot Art */}
            <pre
              className="text-crt-green leading-none select-none whitespace-pre transition-opacity duration-700"
              style={{
                fontSize: "9px",
                textShadow: "none",
                opacity: bootPhase >= 1 ? 1 : 0,
              }}
            >
              {BOOT_ART}
            </pre>

            {/* Boot Messages */}
            <div
              className="w-full max-w-lg transition-opacity duration-500"
              style={{ opacity: bootPhase >= 2 ? 1 : 0 }}
            >
              <div className="rounded-xl p-4 space-y-2" style={{ background: tintBg, border: `1px solid ${subtleBorder}` }}>
                <div className="text-[13px] font-medium" style={{ color: "var(--text-secondary)" }}>System Check — OK</div>
                <div className="text-[13px] font-medium" style={{ color: "var(--text-secondary)" }}>Claude Engine — Ready</div>
                <div className="text-[13px] font-medium" style={{ color: "var(--text-secondary)" }}>Job Scheduler — Active</div>
                <div className="text-[13px] font-medium" style={{ color: "var(--text-secondary)" }}>SSE Stream — Enabled</div>
                <div className="text-[13px] font-medium mt-2" style={{ color: "var(--crt-amber)" }}>
                  Wallet signature required for access
                </div>
                {!hasMetaMask && !hasSubWallet && (
                  <div className="text-[13px]" style={{ color: "var(--text-tertiary)" }}>
                    No web3 wallet detected — local key mode available
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }

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

                  {/* CID with link */}
                  <div className="mb-3">
                    <div className="text-[14px] text-crt-green/30 mb-1" style={{ letterSpacing: "0.01em" }}>IPFS CID</div>
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

  const renderDirectoryTab = () => {
    const fileWorkDir = workDir || (selectedJob ? jobs.find(j => j.id === selectedJob)?.work_dir : "") || "~/mod";

    return (
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Directory Header with Inline Search */}
        <div
          style={{
            borderBottom: `1px solid ${subtleBorder}`,
            background: tintBg,
          }}
        >
          <div className="px-3 py-2 flex items-center gap-2">
            <span className="text-[14px] text-crt-green/70 flex-1" style={{ letterSpacing: "0.02em" }}>
              📁 FILES
            </span>
            <div className="flex items-center gap-1.5">
              <button
                onClick={() => {
                  setInlineSearchMode((prev) => prev === "files" ? "off" : "files");
                  setInlineSearchQuery("");
                  setInlineSearchResults([]);
                  setTimeout(() => inlineSearchRef.current?.focus(), 50);
                }}
                className={`text-[13px] px-1.5 py-0.5 border transition-all uppercase ${
                  inlineSearchMode === "files"
                    ? "border-crt-blue text-crt-blue bg-crt-blue/10"
                    : "border-crt-blue/30 text-crt-blue/60 hover:text-crt-blue hover:border-crt-blue"
                }`}
                title="Search files by name (Ctrl+P)"
                style={{ letterSpacing: "0" }}
              >
                🔍
              </button>
              <button
                onClick={() => {
                  setInlineSearchMode((prev) => prev === "grep" ? "off" : "grep");
                  setInlineSearchQuery("");
                  setInlineSearchResults([]);
                  setTimeout(() => inlineSearchRef.current?.focus(), 50);
                }}
                className={`text-[13px] px-1.5 py-0.5 border transition-all uppercase ${
                  inlineSearchMode === "grep"
                    ? "border-crt-blue text-crt-blue bg-crt-blue/10"
                    : "border-crt-blue/30 text-crt-blue/60 hover:text-crt-blue hover:border-crt-blue"
                }`}
                title="Search file contents (Ctrl+Shift+F)"
                style={{ letterSpacing: "0" }}
              >
                🔎
              </button>
              <button
                onClick={() => fetchDirectoryTree()}
                className="text-[13px] px-1.5 py-0.5 border border-crt-green/20 text-crt-green/40 hover:text-crt-green/70 hover:border-crt-green/40 transition-all"
                title="Refresh"
              >
                ↻
              </button>
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
            </div>
          </div>

          {/* Search expand below header */}
          {inlineSearchMode !== "off" && (
            <div className="px-3 pb-2" style={{ borderTop: `1px solid ${subtleBorder}` }}>
              <div className="flex items-center gap-2 px-2 py-1 mt-1 border border-crt-blue/30 bg-black/40" style={{ borderRadius: "8px" }}>
                <span className="text-[13px] text-crt-blue/60">
                  {inlineSearchMode === "files" ? "🔍" : "🔎"}
                </span>
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
        </div>

        {/* Path display */}
        {(selectedJob || workDir) && (
          <div className="px-3 py-1 border-b text-[14px] text-crt-green/30 truncate font-code" style={{ borderColor: "var(--border-color)" }}>
            {fileWorkDir.replace(/^\/Users\/[^/]+/, "~").replace(/^\/home\/[^/]+/, "~")}
          </div>
        )}

        {/* Side-by-side: file tree + file content */}
        <div className="flex-1 flex overflow-hidden">
          {/* Left: File tree */}
          <div
            className="overflow-y-auto p-2 shrink-0 border-r"
            style={{
              width: viewingFile ? "200px" : "100%",
              borderColor: "var(--border-color)",
            }}
          >
            {directoryTree.length > 0 ? (
              renderDirectoryTree(directoryTree, 0)
            ) : (
              <div className="flex flex-col items-center justify-center h-full gap-3">
                <span className="text-[13px] text-crt-green/50">📂 No files loaded</span>
                <span className="text-[14px] text-crt-green/30">Select a module above or click refresh</span>
                <button
                  onClick={() => fetchDirectoryTree()}
                  className="text-[14px] px-3 py-1.5 border border-crt-green/30 text-crt-green/60 hover:text-crt-green hover:border-crt-green transition-all"
                >
                  ↻ LOAD FILES
                </button>
              </div>
            )}
          </div>

          {/* Right: File content viewer */}
          {viewingFile && (
            <div className="flex-1 flex flex-col overflow-hidden min-w-0">
              {/* File header */}
              <div
                className="px-3 py-1.5 border-b flex items-center justify-between shrink-0"
                style={{ borderColor: "var(--border-color)", background: "rgba(59,130,246,0.03)" }}
              >
                <div className="flex items-center gap-2 min-w-0">
                  <span className="text-[13px] text-crt-blue font-bold truncate font-code">
                    {viewingFile.split("/").pop()}
                  </span>
                  <span className="text-[14px] text-crt-green/30 uppercase shrink-0 font-code">
                    {getLanguageFromPath(viewingFile)}
                  </span>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <span className="text-[14px] text-crt-green/20 font-code">
                    {(editingFile ? editBuffer : viewingFileContent).split("\n").length} lines
                  </span>
                  {editingFile ? (
                    <>
                      <button
                        onClick={saveFile}
                        disabled={savingFile}
                        className="text-[13px] px-2 py-0.5 border border-crt-green/40 text-crt-green/70 hover:text-crt-green hover:border-crt-green transition-all disabled:opacity-40"
                        title="Save file"
                      >
                        {savingFile ? "..." : "SAVE"}
                      </button>
                      <button
                        onClick={() => setEditingFile(false)}
                        className="text-[13px] px-1.5 py-0.5 border border-crt-yellow/30 text-crt-yellow/50 hover:text-crt-yellow hover:border-crt-yellow transition-all"
                        title="Cancel editing"
                      >
                        ESC
                      </button>
                    </>
                  ) : (
                    <button
                      onClick={() => { setEditBuffer(viewingFileContent); setEditingFile(true); }}
                      className="text-[13px] px-2 py-0.5 border border-crt-blue/30 text-crt-blue/50 hover:text-crt-blue hover:border-crt-blue transition-all"
                      title="Edit file"
                    >
                      EDIT
                    </button>
                  )}
                  <button
                    onClick={() => { setViewingFile(null); setViewingFileContent(""); setEditingFile(false); }}
                    className="text-[13px] px-1.5 py-0.5 border border-crt-red/30 text-crt-red/50 hover:text-crt-red hover:border-crt-red transition-all"
                    title="Close file"
                  >
                    ✕
                  </button>
                </div>
              </div>
              {/* File path */}
              <div className="px-3 py-0.5 text-[14px] text-crt-green/20 truncate border-b shrink-0 font-code" style={{ borderColor: "var(--bg-tint)" }}>
                {viewingFile}
              </div>
              {/* File content */}
              <div className="flex-1 overflow-auto">
                {viewingFileLoading ? (
                  <div className="flex items-center justify-center h-full">
                    <span className="text-[14px] text-crt-blue animate-pulse">Loading file...</span>
                  </div>
                ) : editingFile ? (
                  <textarea
                    value={editBuffer}
                    onChange={(e) => setEditBuffer(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "s" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); saveFile(); }
                      if (e.key === "Escape") { setEditingFile(false); }
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
                    {viewingFileContent.split("\n").map((line, i) => (
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
                              key={m.name}
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
              const moduleName = job.work_dir ? extractModuleFromWorkDir(job.work_dir) : null;
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
                        {job.work_dir && moduleName && moduleName !== "claude" && (
                          <span className="text-[9px] uppercase tracking-wide" style={{ color: "var(--crt-amber)", opacity: 0.4 }}>
                            {moduleName}
                          </span>
                        )}
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
      const matchesModule = !selectedModule || (j.work_dir ? j.work_dir.includes(`/orbit/${selectedModule}`) : selectedModule === "claude");
      return matchesSearch && matchesStatus && matchesModule;
    });

    const runningCount = filteredJobs.filter(j => j.status === "running").length;
    const isRunning = selectedJobData?.status === "running";
    const output = streamOutput || selectedJobData?.output || "";
    const activeModelChip = MODEL_OPTIONS.find(m => m.value === model)
      || MODEL_OPTIONS.find(m => m.family === model)
      || MODEL_OPTIONS[0];
    const activeEngine = ENGINE_OPTIONS.find(e => e.value === engine) || ENGINE_OPTIONS[0];

    return (
      <div className="flex-1 flex flex-col overflow-hidden" style={{ background: "var(--bg-primary)" }}>
        {/* ── Header ── */}
        <div
          className="flex items-center justify-between px-3.5 py-2.5 shrink-0"
          style={{ borderBottom: `1px solid ${subtleBorder}`, background: tintBg }}
        >
          <div className="flex items-center gap-1.5 min-w-0 flex-wrap">
            <span
              className="inline-flex items-center justify-center shrink-0"
              style={{
                width: 24,
                height: 24,
                borderRadius: 7,
                background: "linear-gradient(135deg, rgba(167,139,250,0.22), rgba(167,139,250,0.06))",
                border: "1px solid rgba(167,139,250,0.38)",
                color: "#c4b5fd",
                fontSize: 13,
                boxShadow: "0 0 18px -6px rgba(167,139,250,0.7), inset 0 1px 0 rgba(255,255,255,0.06)",
              }}
            >
              ⬡
            </span>
            <span
              className="text-[12px] font-semibold uppercase shrink-0 mr-1"
              style={{ color: "#ddd6fe", letterSpacing: "0.18em" }}
            >
              Agent
            </span>
            <span
              aria-hidden
              className="shrink-0"
              style={{
                width: 1,
                height: 14,
                background: "linear-gradient(to bottom, transparent, var(--border-color), transparent)",
                marginRight: 2,
              }}
            />
            {/* Selected module · version count — click to open full VERSIONS panel */}
            {selectedModule && (
              <button
                onClick={() => setShowVersions(true)}
                className="inline-flex items-center gap-1.5 text-[10px] font-mono px-2 py-[3px] shrink-0 transition-all"
                style={{
                  color: "var(--accent-color)",
                  border: `1px solid color-mix(in srgb, var(--accent-color) 28%, transparent)`,
                  borderRadius: 999,
                  background: "color-mix(in srgb, var(--accent-color) 10%, transparent)",
                }}
                onMouseEnter={e => {
                  e.currentTarget.style.background = "color-mix(in srgb, var(--accent-color) 16%, transparent)";
                  e.currentTarget.style.borderColor = "color-mix(in srgb, var(--accent-color) 45%, transparent)";
                }}
                onMouseLeave={e => {
                  e.currentTarget.style.background = "color-mix(in srgb, var(--accent-color) 10%, transparent)";
                  e.currentTarget.style.borderColor = "color-mix(in srgb, var(--accent-color) 28%, transparent)";
                }}
                title={`Module: ${selectedModule} · ${agentVersions.length} version${agentVersions.length !== 1 ? "s" : ""} — click to open VERSIONS panel`}
              >
                <span className="truncate max-w-[100px] font-semibold">{selectedModule}</span>
                <span style={{ color: "var(--text-tertiary)", opacity: 0.7 }}>◆</span>
                <span style={{ opacity: 0.85 }}>{agentVersions.length}</span>
              </button>
            )}
            {/* Connected user — click to copy address. */}
            {address && address !== "local" && (
              <button
                onClick={() => {
                  navigator.clipboard.writeText(address);
                  setCopiedAddress(true);
                  setTimeout(() => setCopiedAddress(false), 1500);
                }}
                className="inline-flex items-center gap-1.5 text-[10px] font-mono px-2 py-[3px] shrink-0 transition-all"
                style={{
                  color: "var(--crt-green)",
                  border: `1px solid color-mix(in srgb, var(--crt-green) 28%, transparent)`,
                  borderRadius: 999,
                  background: "color-mix(in srgb, var(--crt-green) 10%, transparent)",
                }}
                onMouseEnter={e => {
                  e.currentTarget.style.background = "color-mix(in srgb, var(--crt-green) 16%, transparent)";
                  e.currentTarget.style.borderColor = "color-mix(in srgb, var(--crt-green) 45%, transparent)";
                }}
                onMouseLeave={e => {
                  e.currentTarget.style.background = "color-mix(in srgb, var(--crt-green) 10%, transparent)";
                  e.currentTarget.style.borderColor = "color-mix(in srgb, var(--crt-green) 28%, transparent)";
                }}
                title={copiedAddress ? "Copied!" : `Signed in as: ${address}${walletType ? ` (${walletType})` : ""} — click to copy`}
              >
                <span
                  className="inline-block w-1.5 h-1.5 rounded-full"
                  style={{ background: "var(--crt-green)", boxShadow: "0 0 6px var(--crt-green)" }}
                />
                {copiedAddress ? "COPIED" : `${address.slice(0, 6)}…${address.slice(-4)}`}
              </button>
            )}
            {runningCount > 0 && (
              <span
                className="inline-flex items-center gap-1.5 px-2 py-[3px] rounded-full shrink-0"
                style={{
                  background: "rgba(59,130,246,0.12)",
                  border: "1px solid rgba(59,130,246,0.32)",
                  color: "#93c5fd",
                  fontSize: 10,
                  fontWeight: 600,
                  letterSpacing: "0.03em",
                }}
                title={`${runningCount} job${runningCount > 1 ? "s" : ""} running`}
              >
                <span className="inline-block w-1.5 h-1.5 rounded-full soft-pulse" style={{ background: "#3b82f6", boxShadow: "0 0 6px #3b82f6" }} />
                {runningCount} active
              </span>
            )}
            {error && (
              <span className="text-[10px] truncate" style={{ color: "var(--crt-red)" }}>{error}</span>
            )}
          </div>
          <button
            onClick={() => setAgentSidebarOpen(false)}
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
            title="Close agent panel"
          >
            ✕
          </button>
        </div>

        {/* ── Tabs (Info / Versions / Tasks) ── */}
        <div
          className="flex items-center gap-1 px-2 pt-1.5 shrink-0"
          style={{ borderBottom: `1px solid ${subtleBorder}`, background: tintBg }}
        >
          {([
            { key: "info", label: "Agent", icon: "⬡", count: null as number | null },
            { key: "versions", label: "Versions", icon: "◆", count: agentVersions.length || null },
            { key: "tasks", label: "Tasks", icon: "▤", count: filteredJobs.filter(j => j.status === "running" || j.status === "pending").length || null },
          ] as const).map((t) => {
            const active = agentPanelTab === t.key;
            const tabColor = t.key === "info"
              ? "#a78bfa"
              : t.key === "versions"
                ? "var(--accent-color)"
                : "var(--crt-blue)";
            return (
              <button
                key={t.key}
                onClick={() => setAgentPanelTab(t.key)}
                className="flex items-center gap-1.5 px-2.5 py-1.5 text-[10px] font-bold uppercase tracking-[0.10em] transition-colors"
                style={{
                  color: active ? tabColor : "var(--text-tertiary)",
                  background: active
                    ? `color-mix(in srgb, ${tabColor} 12%, transparent)`
                    : "transparent",
                  border: `1px solid ${active ? `color-mix(in srgb, ${tabColor} 40%, transparent)` : "transparent"}`,
                  borderBottom: "none",
                  borderTopLeftRadius: 6,
                  borderTopRightRadius: 6,
                  marginBottom: -1,
                }}
                onMouseEnter={(e) => {
                  if (!active) e.currentTarget.style.color = "var(--text-secondary)";
                }}
                onMouseLeave={(e) => {
                  if (!active) e.currentTarget.style.color = "var(--text-tertiary)";
                }}
                title={`${t.label} tab`}
              >
                <span style={{ fontSize: 11, opacity: active ? 1 : 0.65 }}>{t.icon}</span>
                <span>{t.label}</span>
                {t.count != null && t.count > 0 && (
                  <span
                    className="text-[9px] font-mono px-1 py-[1px] rounded"
                    style={{
                      color: active ? tabColor : "var(--text-tertiary)",
                      background: active
                        ? `color-mix(in srgb, ${tabColor} 18%, transparent)`
                        : "var(--bg-secondary)",
                      opacity: active ? 0.95 : 0.7,
                    }}
                  >
                    {t.count}
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {/* ── Engine · Model · Persona (INFO tab) ── */}
        {agentPanelTab === "info" && (
        <div
          className="flex items-center flex-wrap gap-2 px-3.5 py-3 shrink-0"
          style={{ borderBottom: `1px solid ${subtleBorder}`, background: tintBg }}
        >
          {/* Engine (which agent harness is driving the job) */}
          <select
            value={engine}
            onChange={(e) => {
              const next = e.target.value;
              const opt = ENGINE_OPTIONS.find(o => o.value === next);
              if (!opt?.available) {
                setError(`${opt?.label || next} engine isn't wired up yet — staying on ${activeEngine.label}.`);
                setTimeout(() => setError(null), 2500);
                return;
              }
              setEngine(next);
              safeSetItem("claude_jobs_engine", next);
            }}
            className="px-3 py-1.5 bg-transparent border cursor-pointer"
            style={{
              fontSize: 13,
              fontWeight: 600,
              borderColor: `${activeEngine.color}55`,
              background: `${activeEngine.color}1a`,
              color: activeEngine.color,
              borderRadius: 999,
              letterSpacing: "0.02em",
            }}
            title={`Agent engine: ${activeEngine.label} — ${activeEngine.hint}`}
          >
            {ENGINE_OPTIONS.map(opt => (
              <option key={opt.value} value={opt.value} style={{ background: "var(--bg-primary)", color: "var(--text-primary)" }}>
                {opt.icon} {opt.label}{opt.available ? "" : " · soon"}
              </option>
            ))}
          </select>

          {/* Model (specific version) */}
          <select
            value={model}
            onChange={(e) => { setModel(e.target.value); safeSetItem("claude_jobs_model", e.target.value); }}
            className="px-3 py-1.5 bg-transparent border cursor-pointer"
            style={{
              fontSize: 13,
              fontWeight: 600,
              borderColor: `${activeModelChip.color}55`,
              background: `${activeModelChip.color}1a`,
              color: activeModelChip.color,
              borderRadius: 999,
              letterSpacing: "0.02em",
            }}
            title={`Model: ${activeModelChip.label} (${activeModelChip.value})`}
          >
            {MODEL_OPTIONS.map(m => (
              <option key={m.value} value={m.value} style={{ background: "var(--bg-primary)", color: "var(--text-primary)" }}>
                {m.label}
              </option>
            ))}
          </select>

          {/* Persona (was confusingly labelled "Agent" — that's now the engine) */}
          <select
            value={agentType}
            onChange={(e) => { setAgentType(e.target.value); safeSetItem("claude_jobs_agent", e.target.value); }}
            className="px-3 py-1.5 bg-transparent border uppercase cursor-pointer"
            style={{
              fontSize: 12,
              fontWeight: 600,
              borderColor: "color-mix(in srgb, var(--crt-blue) 30%, transparent)",
              background: "color-mix(in srgb, var(--crt-blue) 10%, transparent)",
              color: "var(--crt-blue)",
              borderRadius: 999,
              letterSpacing: "0.05em",
            }}
            title={`Persona / system prompt preset: ${AGENT_OPTIONS.find(a => a.value === agentType)?.label || agentType}`}
          >
            {AGENT_OPTIONS.map((a) => (
              <option key={a.value} value={a.value}>{a.icon} {a.label}</option>
            ))}
          </select>
        </div>
        )}

        {/* ── Versions strip — git-graph for the active module (VERSIONS tab) ── */}
        {agentPanelTab === "versions" && agentVersions.length > 0 && (() => {
          const ACTION_GLYPH: Record<string, string> = {
            snapshot: "◆",
            restore: "↻",
            "auto-snapshot": "◌",
            fork: "⎇",
          };
          const ACTION_COLOR: Record<string, string> = {
            snapshot: "var(--accent-color)",
            restore: "var(--crt-amber)",
            "auto-snapshot": "var(--text-tertiary)",
            fork: "var(--crt-blue)",
          };
          // Deterministic color from an address — for the author dot.
          const authorColor = (addr: string) => {
            if (!addr || addr.length < 6) return "var(--text-tertiary)";
            const h = parseInt(addr.slice(2, 8), 16);
            const hue = h % 360;
            return `hsl(${hue}, 70%, 62%)`;
          };
          const visible = agentVersions.slice(0, 48);
          return (
            <div
              className="versions-strip flex flex-wrap content-start items-stretch gap-1.5 px-2.5 py-2 flex-1 overflow-y-auto"
              style={{ borderBottom: `1px solid ${subtleBorder}`, background: tintBg, scrollbarWidth: "thin" }}
            >
              {/* Module label + count */}
              <div
                className="flex items-center justify-between shrink-0 w-full"
                style={{ borderBottom: `1px solid ${subtleBorder}`, paddingBottom: 6, marginBottom: 2 }}
                title={`${selectedModule || "claude"} · ${agentVersions.length} snapshots in chain`}
              >
                <div className="flex items-baseline gap-1.5">
                  <span
                    className="text-[9px] font-bold uppercase tracking-[0.18em]"
                    style={{ color: "var(--accent-color)", opacity: 0.9 }}
                  >
                    {(selectedModule || "claude")}
                  </span>
                  <span
                    className="text-[8px] uppercase tracking-wider"
                    style={{ color: "var(--text-tertiary)" }}
                  >
                    {agentVersions.length} ver
                  </span>
                </div>
                <button
                  onClick={() => setShowVersions(true)}
                  className="text-[9px] font-bold uppercase tracking-[0.10em] px-1.5 py-0.5 rounded transition-colors"
                  style={{
                    color: "var(--accent-color)",
                    background: "color-mix(in srgb, var(--accent-color) 10%, transparent)",
                    border: "1px solid color-mix(in srgb, var(--accent-color) 30%, transparent)",
                  }}
                  title="Open full VERSIONS panel (fork / restore / diff)"
                >
                  Open Full ↗
                </button>
              </div>
              {visible.map((v, i) => {
                const action = v.action || "snapshot";
                const color = ACTION_COLOR[action] || ACTION_COLOR.snapshot;
                const glyph = ACTION_GLYPH[action] || ACTION_GLYPH.snapshot;
                const isHead = i === 0;
                const short = v.cid.slice(0, 7);
                const ts = v.timestamp ? timeSince(v.timestamp) : "";
                const firstLine = (v.message || "").split("\n")[0];
                const aColor = authorColor(v.author || "");
                const bgIdle = isHead ? `${color}26` : `${color}14`;
                const bgHover = `${color}38`;
                return (
                  <button
                    key={`${v.cid}-${i}`}
                    onClick={() => setShowVersions(true)}
                    className={`commit-chip commit-rail shrink-0 flex items-center gap-1.5 px-1.5 py-1 cursor-pointer ${isHead ? "head-glow" : ""}`}
                    title={`${action.toUpperCase()}  ${short}${isHead ? "  (HEAD)" : ""}\nparent: ${(v.parent || "root").slice(0, 12)}\nauthor: ${v.author?.slice(0, 10) || "—"}  ·  ${ts}\n\n${v.message || "(no message)"}`}
                    style={{
                      ["--head-color" as any]: color,
                      background: bgIdle,
                      border: `1px solid ${isHead ? color + "70" : color + "44"}`,
                      borderRadius: "5px",
                      maxWidth: "170px",
                    }}
                    onMouseEnter={e => (e.currentTarget.style.background = bgHover)}
                    onMouseLeave={e => (e.currentTarget.style.background = bgIdle)}
                  >
                    <span
                      className="shrink-0 text-[11px] leading-none"
                      style={{ color, textShadow: isHead ? `0 0 6px ${color}` : "none" }}
                    >
                      {glyph}
                    </span>
                    <span
                      className="text-[10px] font-mono shrink-0"
                      style={{ color, opacity: 0.95, letterSpacing: "0.02em" }}
                    >
                      #{short}
                    </span>
                    {isHead && (
                      <span
                        className="text-[7.5px] font-bold uppercase tracking-widest px-1 leading-none py-[1px] rounded-sm shrink-0"
                        style={{ background: color, color: "var(--bg-primary)" }}
                      >
                        HEAD
                      </span>
                    )}
                    {firstLine && (
                      <span
                        className="text-[10px] truncate min-w-0"
                        style={{
                          color: isHead ? "var(--text-primary)" : "var(--text-secondary)",
                          opacity: isHead ? 0.95 : 0.72,
                          maxWidth: "70px",
                        }}
                      >
                        {firstLine}
                      </span>
                    )}
                    {v.author && (
                      <span
                        className="shrink-0 rounded-full"
                        style={{
                          width: "5px",
                          height: "5px",
                          background: aColor,
                          boxShadow: `0 0 3px ${aColor}80`,
                        }}
                        title={v.author}
                      />
                    )}
                    <span
                      className="text-[9px] shrink-0 font-mono ml-auto"
                      style={{ color: "var(--text-tertiary)", opacity: 0.7 }}
                    >
                      {ts}
                    </span>
                  </button>
                );
              })}
              {agentVersions.length > visible.length && (
                <button
                  onClick={() => setShowVersions(true)}
                  className="shrink-0 flex items-center justify-center gap-1 px-2 py-1 cursor-pointer transition-colors"
                  style={{
                    fontSize: "10px",
                    color: "var(--text-tertiary)",
                    border: `1px dashed ${subtleBorder}`,
                    borderRadius: "5px",
                    fontFamily: "monospace",
                  }}
                  title={`${agentVersions.length - visible.length} more — open full VERSIONS panel`}
                  onMouseEnter={e => (e.currentTarget.style.color = "var(--accent-color)")}
                  onMouseLeave={e => (e.currentTarget.style.color = "var(--text-tertiary)")}
                >
                  <span className="text-[10px] leading-none">+{agentVersions.length - visible.length}</span>
                </button>
              )}
            </div>
          );
        })()}

        {/* ── Versions empty-state (VERSIONS tab) ── */}
        {agentPanelTab === "versions" && agentVersions.length === 0 && (
          <div className="flex-1 flex flex-col items-center justify-center gap-3 p-6 text-center">
            <span
              className="inline-flex items-center justify-center"
              style={{
                width: 44,
                height: 44,
                borderRadius: 10,
                background: "color-mix(in srgb, var(--accent-color) 10%, transparent)",
                border: "1px solid color-mix(in srgb, var(--accent-color) 22%, transparent)",
                color: "var(--accent-color)",
                fontSize: 18,
              }}
            >
              ◆
            </span>
            <p className="text-[11px] font-bold uppercase tracking-[0.16em]" style={{ color: "var(--text-secondary)" }}>
              No versions yet
            </p>
            <p className="text-[10.5px]" style={{ color: "var(--text-tertiary)" }}>
              Snapshots of {selectedModule || "this module"} will show up here.
            </p>
          </div>
        )}

        {/* ── Main content area (TASKS tab) ── */}
        {agentPanelTab === "tasks" && (
        <div ref={outputRef} className="flex-1 overflow-y-auto" style={{ scrollbarWidth: "thin" }}>
          {selectedJobData ? (
            <div className="flex flex-col h-full fade-in">
              {/* Job header */}
              {(() => {
                const statusColor = STATUS_COLOR[selectedJobData.status];
                const moduleName = selectedJobData.work_dir
                  ? extractModuleFromWorkDir(selectedJobData.work_dir)
                  : null;
                const { cleanPrompt } = parsePromptImages(selectedJobData.prompt);
                const displayPrompt = cleanPrompt || selectedJobData.prompt;
                return (
              <div
                className="px-4 py-3 shrink-0 space-y-2.5"
                style={{
                  borderBottom: `1px solid ${subtleBorder}`,
                  background: `linear-gradient(180deg, ${statusColor}0e, ${tintBg})`,
                }}
              >
                {/* Row 1 — status pill + action buttons */}
                <div className="flex items-center justify-between gap-2">
                  <span
                    className="inline-flex items-center gap-1.5 px-2.5 py-[3px] rounded-full"
                    style={{
                      background: `${statusColor}1a`,
                      border: `1px solid ${statusColor}40`,
                    }}
                  >
                    <span
                      className={`inline-block w-1.5 h-1.5 rounded-full ${isRunning ? "soft-pulse" : ""}`}
                      style={{
                        background: statusColor,
                        boxShadow: isRunning ? `0 0 6px ${statusColor}` : "none",
                      }}
                    />
                    <span className="text-[10px] font-bold uppercase" style={{ color: statusColor, letterSpacing: "0.08em" }}>
                      {STATUS_LABEL[selectedJobData.status]}
                    </span>
                  </span>
                  <div className="flex items-center gap-1.5 shrink-0">
                    {isRunning && (
                      <button
                        onClick={() => cancelJob(selectedJobData.id)}
                        className="text-[9px] font-bold uppercase px-2 py-[3px] rounded-full focus-ring"
                        style={{
                          color: "var(--crt-red)",
                          border: "1px solid rgba(239,68,68,0.35)",
                          background: "rgba(239,68,68,0.08)",
                          letterSpacing: "0.05em",
                        }}
                        onMouseEnter={e => (e.currentTarget.style.background = "rgba(239,68,68,0.18)")}
                        onMouseLeave={e => (e.currentTarget.style.background = "rgba(239,68,68,0.08)")}
                      >
                        STOP
                      </button>
                    )}
                    {["completed", "failed", "cancelled"].includes(selectedJobData.status) && (
                      <button
                        onClick={() => deleteJob(selectedJobData.id)}
                        className="text-[9px] font-bold uppercase px-2 py-[3px] rounded-full focus-ring"
                        style={{
                          color: "var(--text-tertiary)",
                          border: `1px solid ${subtleBorder}`,
                          background: "transparent",
                          letterSpacing: "0.05em",
                        }}
                        onMouseEnter={e => {
                          e.currentTarget.style.background = "var(--bg-secondary)";
                          e.currentTarget.style.color = "var(--crt-red)";
                          e.currentTarget.style.borderColor = "rgba(239,68,68,0.35)";
                        }}
                        onMouseLeave={e => {
                          e.currentTarget.style.background = "transparent";
                          e.currentTarget.style.color = "var(--text-tertiary)";
                          e.currentTarget.style.borderColor = subtleBorder;
                        }}
                      >
                        DELETE
                      </button>
                    )}
                    <button
                      onClick={() => { setSelectedJob(null); setStreamOutput(""); }}
                      className="text-[9px] font-bold uppercase px-2 py-[3px] rounded-full focus-ring"
                      style={{
                        color: "var(--text-secondary)",
                        border: `1px solid ${subtleBorder}`,
                        background: "var(--bg-secondary)",
                        letterSpacing: "0.05em",
                      }}
                      onMouseEnter={e => {
                        e.currentTarget.style.color = "var(--text-primary)";
                        e.currentTarget.style.borderColor = "var(--border-color-strong)";
                      }}
                      onMouseLeave={e => {
                        e.currentTarget.style.color = "var(--text-secondary)";
                        e.currentTarget.style.borderColor = subtleBorder;
                      }}
                      title="Back to job list"
                    >
                      ← BACK
                    </button>
                  </div>
                </div>

                {/* Row 2 — model · id · time · module */}
                <div className="flex items-center gap-1.5 flex-wrap text-[10px]" style={{ color: "var(--text-tertiary)" }}>
                  <span className="uppercase font-semibold tracking-wider" style={{ color: "var(--text-secondary)" }}>
                    {modelLabel(selectedJobData.model)}
                  </span>
                  <span className="opacity-30">·</span>
                  <span
                    className="font-mono px-1.5 py-[1px] rounded"
                    style={{
                      background: "var(--bg-secondary)",
                      border: `1px solid ${subtleBorder}`,
                      opacity: 0.85,
                    }}
                    title={selectedJobData.id}
                  >
                    #{selectedJobData.id.slice(0, 8)}
                  </span>
                  <span className="opacity-30">·</span>
                  <span className="font-mono" title={formatDate(selectedJobData.created_at)}>
                    {timeSince(selectedJobData.created_at)}
                  </span>
                  {moduleName && moduleName !== "claude" && (
                    <>
                      <span className="opacity-30">·</span>
                      <span
                        className="uppercase font-mono px-1.5 py-[1px] rounded"
                        style={{
                          color: "var(--crt-amber)",
                          background: "color-mix(in srgb, var(--crt-amber) 10%, transparent)",
                          border: "1px solid color-mix(in srgb, var(--crt-amber) 22%, transparent)",
                          letterSpacing: "0.04em",
                        }}
                      >
                        {moduleName}
                      </span>
                    </>
                  )}
                </div>

                {/* Row 3 — caller · host */}
                <div className="flex items-stretch gap-2 text-[10px]">
                  <div
                    className="flex items-center gap-1.5 px-2 py-[3px] rounded min-w-0"
                    style={{
                      background: "var(--bg-secondary)",
                      border: `1px solid ${subtleBorder}`,
                    }}
                  >
                    <span className="uppercase font-bold tracking-wider shrink-0" style={{ color: "var(--text-tertiary)", letterSpacing: "0.08em", opacity: 0.7 }}>
                      caller
                    </span>
                    <span className="font-mono truncate" style={{ color: "var(--text-secondary)" }} title={selectedJobData.user_address || "local"}>
                      {shortCaller(selectedJobData.user_address)}
                    </span>
                  </div>
                  <div
                    className="flex items-center gap-1.5 px-2 py-[3px] rounded min-w-0 flex-1"
                    style={{
                      background: "var(--bg-secondary)",
                      border: `1px solid ${subtleBorder}`,
                    }}
                  >
                    <span className="uppercase font-bold tracking-wider shrink-0" style={{ color: "var(--text-tertiary)", letterSpacing: "0.08em", opacity: 0.7 }}>
                      host
                    </span>
                    <span className="font-mono truncate" style={{ color: "var(--text-secondary)" }} title={selectedJobData.work_dir || "—"}>
                      {shortHost(selectedJobData.work_dir)}
                    </span>
                  </div>
                </div>

                {/* Prompt preview */}
                <div
                  className="px-2.5 py-2 rounded-md"
                  style={{
                    background: tintBg,
                    borderLeft: `2px solid ${statusColor}66`,
                    border: `1px solid ${subtleBorder}`,
                    borderLeftWidth: 2,
                    borderLeftColor: `${statusColor}66`,
                  }}
                >
                  <p
                    className="text-[12px] leading-relaxed m-0"
                    style={{
                      color: "var(--text-secondary)",
                      display: "-webkit-box",
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: "vertical",
                      overflow: "hidden",
                      wordBreak: "break-word",
                    }}
                  >
                    {displayPrompt}
                  </p>
                </div>
              </div>
                );
              })()}

              {/* Output */}
              <div className="flex-1 overflow-y-auto p-3">
                {output ? (
                  <pre className="m-0 whitespace-pre-wrap text-[11px] leading-relaxed" style={{ color: "var(--text-primary)", fontFamily: "monospace", wordBreak: "break-word" }}>
                    {renderOutput(output)}
                    {isRunning && <span className="inline-block animate-pulse" style={{ color: STATUS_COLOR.running }}>&#9610;</span>}
                  </pre>
                ) : (
                  <div className="flex items-center justify-center h-32">
                    {isRunning ? (
                      <div className="flex items-center gap-1.5">
                        <span className="w-2 h-2 rounded-full animate-pulse" style={{ background: "#3b82f6" }} />
                        <span className="w-2 h-2 rounded-full animate-pulse" style={{ background: "#3b82f6", animationDelay: "0.2s" }} />
                        <span className="w-2 h-2 rounded-full animate-pulse" style={{ background: "#3b82f6", animationDelay: "0.4s" }} />
                      </div>
                    ) : (
                      <p className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>No output</p>
                    )}
                  </div>
                )}

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
                filteredJobs.map(job => {
                  const color = STATUS_COLOR[job.status];
                  const moduleName = job.work_dir ? extractModuleFromWorkDir(job.work_dir) : null;
                  const { cleanPrompt } = parsePromptImages(job.prompt);
                  return (
                    <button
                      key={job.id}
                      onClick={() => viewJob(job)}
                      className="w-full text-left group relative"
                      style={{
                        borderBottom: `1px solid ${subtleBorder}`,
                        transition: "background 150ms ease, border-color 150ms ease",
                      }}
                      onMouseEnter={e => {
                        e.currentTarget.style.background = tintBgStrong;
                      }}
                      onMouseLeave={e => {
                        e.currentTarget.style.background = "transparent";
                      }}
                    >
                      {/* left accent rail (status-tinted) — appears on hover */}
                      <span
                        aria-hidden
                        className="absolute left-0 top-0 bottom-0 opacity-0 group-hover:opacity-100"
                        style={{
                          width: 2,
                          background: color,
                          transition: "opacity 150ms ease",
                          boxShadow: `0 0 8px -1px ${color}`,
                        }}
                      />
                      <div className="px-3.5 py-2.5">
                        <div className="flex items-center justify-between mb-1.5">
                          <div className="flex items-center gap-1.5 min-w-0">
                            <span
                              className={`inline-block w-1.5 h-1.5 rounded-full shrink-0 ${job.status === "running" ? "soft-pulse" : ""}`}
                              style={{
                                background: color,
                                boxShadow: job.status === "running" ? `0 0 6px ${color}` : "none",
                              }}
                            />
                            <span className="text-[10px] font-bold uppercase" style={{ color, letterSpacing: "0.06em" }}>{STATUS_LABEL[job.status]}</span>
                            <span
                              className="text-[9.5px] uppercase font-semibold px-1.5 py-[1px] rounded"
                              style={{
                                color: "var(--text-tertiary)",
                                background: "var(--bg-secondary)",
                                letterSpacing: "0.04em",
                              }}
                            >
                              {job.model?.includes("opus") ? "Op" : job.model?.includes("sonnet") ? "So" : "Ha"}
                            </span>
                            {moduleName && moduleName !== "claude" && (
                              <span
                                className="text-[9px] uppercase font-mono px-1.5 py-[1px] rounded"
                                style={{
                                  color: "var(--crt-amber)",
                                  background: "color-mix(in srgb, var(--crt-amber) 10%, transparent)",
                                  border: "1px solid color-mix(in srgb, var(--crt-amber) 22%, transparent)",
                                  opacity: 0.85,
                                }}
                              >
                                {moduleName}
                              </span>
                            )}
                          </div>
                          <span className="text-[10px] font-mono shrink-0" style={{ color: "var(--text-tertiary)", opacity: 0.7 }}>
                            {timeSince(job.created_at)}
                          </span>
                        </div>
                        <p className="text-[11.5px] leading-relaxed" style={{ color: "var(--text-secondary)", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
                          {cleanPrompt}
                        </p>
                        {/* Hover actions */}
                        <div className="flex justify-end gap-1.5 mt-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
                          {job.status === "running" && (
                            <span
                              onClick={(e) => { e.stopPropagation(); cancelJob(job.id); }}
                              className="text-[9px] font-bold uppercase px-2 py-[3px] rounded-full cursor-pointer"
                              style={{
                                color: "var(--crt-red)",
                                border: "1px solid rgba(239,68,68,0.3)",
                                background: "rgba(239,68,68,0.08)",
                                letterSpacing: "0.05em",
                              }}
                            >
                              CANCEL
                            </span>
                          )}
                          {["completed", "failed", "cancelled"].includes(job.status) && (
                            <span
                              onClick={(e) => { e.stopPropagation(); deleteJob(job.id); }}
                              className="text-[9px] font-bold uppercase px-2 py-[3px] rounded-full cursor-pointer"
                              style={{
                                color: "var(--text-tertiary)",
                                border: `1px solid ${subtleBorder}`,
                                letterSpacing: "0.05em",
                              }}
                            >
                              DELETE
                            </span>
                          )}
                        </div>
                      </div>
                    </button>
                  );
                })
              )}
            </div>
          )}
        </div>
        )}

        {/* ── Input bar (top) — moved from bottom so the ask box is the
            first thing the user sees / can type into. ── */}
        <div
          className="shrink-0 px-3.5 py-3 order-first"
          style={{
            borderBottom: `1px solid ${subtleBorder}`,
            background: `linear-gradient(180deg, var(--bg-primary), ${tintBg})`,
          }}
        >
          <div
            className="flex gap-1.5 items-stretch rounded-xl"
            style={{
              background: darkOverlay,
              border: `1px solid ${submitting ? `${activeModelChip.color}55` : subtleBorder}`,
              boxShadow: submitting
                ? `0 0 0 3px ${activeModelChip.color}1a, 0 4px 16px -6px ${activeModelChip.color}40`
                : "0 1px 3px rgba(0,0,0,0.3)",
              transition: "border-color 150ms ease, box-shadow 150ms ease",
              padding: 4,
            }}
          >
            <input
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
              className="flex-1 px-5 py-5 bg-transparent border-0 outline-none text-[18px]"
              style={{
                color: "var(--text-primary)",
                fontFamily: "'JetBrains Mono', monospace",
                boxShadow: "none",
              }}
              onFocus={e => {
                const wrap = e.currentTarget.parentElement as HTMLElement;
                if (wrap) {
                  wrap.style.borderColor = activeModelChip.color + "80";
                  wrap.style.boxShadow = `0 0 0 3px ${activeModelChip.color}1f, 0 4px 16px -6px ${activeModelChip.color}40`;
                }
              }}
              onBlur={e => {
                const wrap = e.currentTarget.parentElement as HTMLElement;
                if (wrap) {
                  wrap.style.borderColor = subtleBorder;
                  wrap.style.boxShadow = "0 1px 3px rgba(0,0,0,0.3)";
                }
              }}
            />
            <button
              onClick={submitJob}
              disabled={!prompt.trim() || submitting}
              className="px-5 rounded-lg font-bold disabled:opacity-30 focus-ring shrink-0"
              style={{
                backgroundColor: !prompt.trim() || submitting
                  ? `${activeModelChip.color}14`
                  : `${activeModelChip.color}28`,
                border: `1px solid ${activeModelChip.color}55`,
                color: activeModelChip.color,
                fontSize: 18,
                transition: "background-color 150ms ease, transform 100ms ease",
              }}
              onMouseEnter={e => {
                if (!e.currentTarget.disabled) {
                  e.currentTarget.style.backgroundColor = `${activeModelChip.color}3a`;
                }
              }}
              onMouseLeave={e => {
                e.currentTarget.style.backgroundColor = !prompt.trim() || submitting
                  ? `${activeModelChip.color}14`
                  : `${activeModelChip.color}28`;
              }}
              title={submitting ? "Submitting…" : "Send (Enter)"}
            >
              {submitting ? <span className="animate-spin inline-block">⟳</span> : "▶"}
            </button>
          </div>
          {images.length > 0 && (
            <div className="flex items-center gap-1.5 mt-2">
              <span
                className="text-[10px] px-2 py-[3px] rounded-full"
                style={{
                  borderColor: "rgba(96,165,250,0.35)",
                  border: "1px solid rgba(96,165,250,0.35)",
                  background: "rgba(96,165,250,0.08)",
                  color: "rgba(147,197,253,0.9)",
                  fontWeight: 600,
                  letterSpacing: "0.05em",
                }}
              >
                {images.length} IMG{images.length > 1 ? "S" : ""}
              </span>
              <button
                onClick={() => setImages([])}
                className="text-[11px] transition-colors"
                style={{ color: "var(--crt-red)", opacity: 0.6 }}
                onMouseEnter={e => (e.currentTarget.style.opacity = "1")}
                onMouseLeave={e => (e.currentTarget.style.opacity = "0.6")}
                title="Clear images"
              >
                ✕
              </button>
            </div>
          )}
          <div className="flex items-center justify-between mt-2 px-1">
            <div className="flex items-center gap-2 min-w-0">
              {/* Active module picker — choose which module the agent edits */}
              <div ref={agentModuleRef} className="relative shrink-0">
                <button
                  type="button"
                  onClick={() => setShowAgentModuleDropdown((v) => !v)}
                  className="inline-flex items-center gap-1 text-[10px] font-mono px-2 py-[2px] rounded-full transition-colors"
                  style={{
                    color: "#fbbf24",
                    border: "1px solid rgba(251,191,36,0.3)",
                    background: "rgba(251,191,36,0.08)",
                    letterSpacing: "0.04em",
                  }}
                  onMouseEnter={e => { e.currentTarget.style.background = "rgba(251,191,36,0.15)"; }}
                  onMouseLeave={e => { e.currentTarget.style.background = "rgba(251,191,36,0.08)"; }}
                  title={selectedModule ? `Editing module: ${selectedModule} — click to switch` : "Pick a module to edit"}
                >
                  <span className="truncate max-w-[120px]">{selectedModule || "pick module"}</span>
                  <span className="text-[8px] opacity-60">▾</span>
                </button>
                {showAgentModuleDropdown && (
                  <div
                    className="absolute z-50 mt-1 left-0 w-[280px] border rounded-lg shadow-xl overflow-hidden"
                    style={{
                      background: "var(--bg-primary, #0a0a0a)",
                      borderColor: "rgba(251,191,36,0.35)",
                    }}
                  >
                    <input
                      type="text"
                      autoFocus
                      value={agentModuleSearch}
                      onChange={(e) => setAgentModuleSearch(e.target.value)}
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
                        .filter((m) => !agentModuleSearch.trim() || m.name.toLowerCase().includes(agentModuleSearch.toLowerCase()))
                        .slice(0, 200)
                        .map((m) => (
                          <button
                            key={m.name}
                            type="button"
                            onClick={() => {
                              resetModuleState(m);
                              setSelectedModule(m.name);
                              setSelectedModuleInfo(m);
                              setWorkDir(m.path);
                              fetchModuleConfig(m.name);
                              setShowAgentModuleDropdown(false);
                              setAgentModuleSearch("");
                            }}
                            className="w-full px-2 py-1.5 text-left text-[12px] font-mono flex items-center gap-2 hover:bg-amber-400/10 transition-colors"
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
                      {moduleList.filter((m) => !agentModuleSearch.trim() || m.name.toLowerCase().includes(agentModuleSearch.toLowerCase())).length === 0 && (
                        <div className="px-2 py-3 text-[11px] text-center opacity-50 font-mono uppercase">
                          no matches
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
              <span className="text-[9px]" style={{ color: "var(--text-tertiary)", opacity: 0.55, letterSpacing: "0.04em" }}>
                <kbd style={{
                  padding: "1px 5px",
                  borderRadius: 4,
                  border: `1px solid ${subtleBorder}`,
                  background: "var(--bg-secondary)",
                  fontFamily: "'JetBrains Mono', monospace",
                  fontSize: 9,
                  marginRight: 4,
                }}>↵</kbd>
                to submit
              </span>
            </div>
            <div className="flex items-center gap-1">
              <select
                value={model}
                onChange={(e) => { setModel(e.target.value); safeSetItem("claude_jobs_model", e.target.value); }}
                className="text-[9px] font-mono bg-transparent border-0 outline-none cursor-pointer appearance-none pr-0"
                style={{ color: activeModelChip.color, opacity: 0.85 }}
                title={`Model: ${activeModelChip.label} (${activeModelChip.value})`}
              >
                {MODEL_OPTIONS.map(m => (
                  <option key={m.value} value={m.value}>{m.label}</option>
                ))}
              </select>
              <span className="text-[9px] font-mono" style={{ color: "var(--text-tertiary)", opacity: 0.55 }}>
                · {apiUrl.replace("http://", "")}
              </span>
            </div>
          </div>
        </div>
      </div>
    );
  };

  const renderAppTab = () => {
    // Local gateway URL the user can type on a phone instead of the
    // raw localhost:PORT. Caddy/routy proxy 3000 → the module's app.
    // Hostname uses location.hostname so visiting from a phone on the
    // same wifi (e.g. http://192.168.x.y:3000/claude) still shows the
    // right address to copy.
    const gatewayHost = typeof window !== "undefined" ? window.location.hostname : "localhost";
    const modName = selectedModule || "claude";
    const gatewayUrl = `http://${gatewayHost}:3000/${modName}`;
    return (
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* URL strip — copyable, click-to-open in new tab. Shown above
            every APP view so phone users can grab the route. */}
        {selectedModuleInfo?.app_url && (
          <div
            className="flex items-center gap-2 px-3 py-2 shrink-0"
            style={{
              borderBottom: "1px solid var(--border-color)",
              background: "linear-gradient(180deg, var(--bg-tint), transparent)",
            }}
          >
            <span className="text-[9px] font-bold uppercase tracking-[0.18em] shrink-0" style={{ color: "var(--crt-green)" }}>URL</span>
            <button
              onClick={() => navigator.clipboard?.writeText(gatewayUrl).catch(() => {})}
              className="flex-1 font-mono text-[11px] truncate text-left transition-colors"
              style={{
                color: "var(--text-secondary)",
                background: "var(--bg-secondary)",
                border: "1px solid var(--border-color)",
                borderRadius: 4,
                padding: "4px 8px",
                cursor: "pointer",
              }}
              title={`${gatewayUrl} — click to copy`}
            >
              {gatewayUrl}
            </button>
            <a
              href={gatewayUrl}
              target="_blank"
              rel="noreferrer noopener"
              className="text-[10px] px-2 py-1 rounded uppercase font-bold tracking-wider transition-all"
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
        )}
        {/* App Content */}
        <div className="flex-1 overflow-hidden">
          {selectedModuleInfo?.app_url ? (
            <iframe
              src={gatewayUrl}
              className="w-full h-full border-0"
              title="Module App"
            />
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
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cmd, cwd }),
      });
      const data = await res.json();
      setTerminalHistory(h => h.map(e => e.id === id ? {
        ...e,
        stdout: data.stdout || "",
        stderr: data.stderr || (res.ok ? "" : (data.error || `HTTP ${res.status}`)),
        code: typeof data.code === "number" ? data.code : (res.ok ? 0 : 1),
        durationMs: Date.now() - start,
        pending: false,
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
                color: "var(--crt-amber)",
                background: "color-mix(in srgb, var(--crt-amber) 10%, transparent)",
                border: "1px solid color-mix(in srgb, var(--crt-amber) 30%, transparent)",
                letterSpacing: "0.08em",
              }}
              title="Visible to module owner only"
            >
              OWNER
            </span>
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
              <div>{`# commands run via bash -c — owner-only, single-shot (no PTY).`}</div>
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
                  <div className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>
                    exit {e.code} · {e.durationMs}ms
                  </div>
                </>
              )}
            </div>
          ))}
        </div>

        {/* Input */}
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

  const renderModulesTab = () => {
    const filtered = moduleList.filter(m =>
      !moduleManageSearch || m.name.toLowerCase().includes(moduleManageSearch.toLowerCase())
    );
    const canManage = (m: typeof moduleList[0]) =>
      isOwner || (m.owner && address && m.owner.toLowerCase() === address.toLowerCase());

    return (
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Header */}
        <div
          className="px-4 py-3 border-b shrink-0"
          style={{
            borderColor: "rgba(59,130,246,0.15)",
            background: "linear-gradient(180deg, rgba(59,130,246,0.06) 0%, rgba(59,130,246,0.01) 100%)",
          }}
        >
          <div className="flex items-center justify-between mb-2">
            <span className="text-[13px] font-bold" style={{ color: "var(--crt-blue)", letterSpacing: "0.04em", textShadow: "none" }}>
              MODULES
            </span>
            <span className="text-[12px]" style={{ color: "var(--text-tertiary)", opacity: 0.5 }}>
              {filtered.length} module{filtered.length !== 1 ? "s" : ""}
            </span>
          </div>
          <input
            type="text"
            value={moduleManageSearch}
            onChange={(e) => setModuleManageSearch(e.target.value)}
            placeholder="filter modules..."
            className="w-full px-2 py-1.5 text-[13px] bg-transparent border font-code outline-none"
            style={{
              borderColor: "rgba(59,130,246,0.2)",
              color: "var(--text-primary)",
            }}
          />
        </div>

        {/* Module List */}
        <div className="flex-1 overflow-y-auto">
          {filtered.length === 0 ? (
            <div className="flex items-center justify-center p-8">
              <span className="text-[13px]" style={{ color: "var(--text-tertiary)", opacity: 0.5 }}>
                {moduleList.length === 0 ? "Loading modules..." : "No modules match filter"}
              </span>
            </div>
          ) : (
            filtered.map((m) => (
              <div
                key={m.name}
                className="border-b transition-colors"
                style={{
                  borderColor: "var(--border-color)",
                  background: m.name === selectedModule ? "rgba(59,130,246,0.06)" : "transparent",
                }}
              >
                {renamingModule === m.name ? (
                  /* Rename mode */
                  <div className="px-4 py-3 flex flex-col gap-2">
                    <div className="text-[11px] uppercase" style={{ color: "var(--crt-amber)", letterSpacing: "0.01em" }}>
                      RENAME MODULE
                    </div>
                    <input
                      autoFocus
                      type="text"
                      value={renameInput}
                      onChange={(e) => setRenameInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && renameInput.trim()) renameModule(m.name, renameInput);
                        if (e.key === "Escape") { setRenamingModule(null); setRenameInput(""); }
                      }}
                      className="px-2 py-1.5 text-[13px] bg-transparent border font-code outline-none"
                      style={{
                        borderColor: "rgba(245,158,11,0.4)",
                        color: "var(--text-primary)",
                      }}
                    />
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => renameModule(m.name, renameInput)}
                        disabled={!renameInput.trim() || renameInput.trim() === m.name}
                        className="text-[12px] px-3 py-1 border transition-all hover:brightness-125 uppercase"
                        style={{
                          borderColor: "rgba(245,158,11,0.4)",
                          color: "var(--crt-amber)",
                          opacity: renameInput.trim() && renameInput.trim() !== m.name ? 1 : 0.3,
                          letterSpacing: "0",
                        }}
                      >
                        Rename
                      </button>
                      <button
                        onClick={() => { setRenamingModule(null); setRenameInput(""); }}
                        className="text-[12px] px-3 py-1 border border-crt-green/20 text-crt-green/50 hover:text-crt-green hover:border-crt-green/40 transition-all uppercase"
                        style={{ letterSpacing: "0" }}
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : (
                  /* Normal mode */
                  <div className="px-4 py-2.5">
                    <div className="flex items-center justify-between">
                      <div
                        className="flex items-center gap-2 cursor-pointer flex-1 min-w-0"
                        onClick={() => {
                          resetModuleState(m);
                          setSelectedModule(m.name);
                          setSelectedModuleInfo(m);
                          setWorkDir(m.path);
                          fetchModuleConfig(m.name);
                        }}
                      >
                        <span className="text-[14px] font-code truncate" style={{ color: m.name === selectedModule ? "var(--crt-blue)" : "var(--crt-green)" }}>
                          {m.name}
                        </span>
                        {m.cid && (
                          <span className="text-[11px] px-1.5 py-0.5 border font-code shrink-0 border-crt-green/15 text-crt-green/25" title={m.cid}>
                            {m.cid.slice(0, 6)}..{m.cid.slice(-4)}
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-1.5 shrink-0 ml-2">
                        {m.app_url && (
                          <span className="text-[10px] px-1 py-0.5 border border-crt-blue/30 text-crt-blue/60">APP</span>
                        )}
                        {m.api_url && (
                          <span className="text-[10px] px-1 py-0.5 border border-crt-amber/30 text-crt-amber/60">API</span>
                        )}
                      </div>
                    </div>
                    {m.description && (
                      <div className="text-[12px] mt-1 truncate" style={{ color: "var(--text-tertiary)", opacity: 0.5 }}>
                        {m.description}
                      </div>
                    )}
                    {m.owner && (
                      <div className="text-[11px] mt-0.5 font-mono" style={{ color: "var(--text-tertiary)", opacity: 0.3 }}>
                        {m.owner.slice(0, 6)}..{m.owner.slice(-4)}
                      </div>
                    )}
                    {/* Action buttons */}
                    {canManage(m) && (
                      <div className="flex items-center gap-2 mt-2">
                        <button
                          onClick={() => {
                            setRenamingModule(m.name);
                            setRenameInput(m.name);
                          }}
                          className="text-[11px] px-2 py-0.5 border transition-all hover:brightness-125 uppercase"
                          style={{
                            borderColor: "rgba(245,158,11,0.25)",
                            color: "rgba(245,158,11,0.6)",
                            letterSpacing: "0",
                          }}
                          onMouseEnter={(e) => { e.currentTarget.style.borderColor = "rgba(245,158,11,0.5)"; e.currentTarget.style.color = "var(--crt-amber)"; }}
                          onMouseLeave={(e) => { e.currentTarget.style.borderColor = "rgba(245,158,11,0.25)"; e.currentTarget.style.color = "rgba(245,158,11,0.6)"; }}
                        >
                          Rename
                        </button>
                        {m.name !== "claude" && (
                          confirmDeleteModule === m.name ? (
                            <div className="flex items-center gap-1">
                              <span className="text-[10px] text-crt-red/70">Delete?</span>
                              <button
                                onClick={() => deleteModule(m.name)}
                                className="text-[11px] px-2 py-0.5 border border-crt-red/50 text-crt-red bg-crt-red/10 hover:bg-crt-red/20 transition-all uppercase"
                                style={{ letterSpacing: "0" }}
                              >
                                Yes
                              </button>
                              <button
                                onClick={() => setConfirmDeleteModule(null)}
                                className="text-[11px] px-2 py-0.5 border border-crt-green/20 text-crt-green/50 hover:text-crt-green transition-all uppercase"
                                style={{ letterSpacing: "0" }}
                              >
                                No
                              </button>
                            </div>
                          ) : (
                            <button
                              onClick={() => setConfirmDeleteModule(m.name)}
                              className="text-[11px] px-2 py-0.5 border transition-all uppercase"
                              style={{
                                borderColor: "rgba(239,68,68,0.2)",
                                color: "rgba(239,68,68,0.4)",
                                letterSpacing: "0",
                              }}
                              onMouseEnter={(e) => { e.currentTarget.style.borderColor = "rgba(239,68,68,0.5)"; e.currentTarget.style.color = "var(--crt-red)"; }}
                              onMouseLeave={(e) => { e.currentTarget.style.borderColor = "rgba(239,68,68,0.2)"; e.currentTarget.style.color = "rgba(239,68,68,0.4)"; }}
                            >
                              Delete
                            </button>
                          )
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))
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

              {/* Service status cards — API + APP side by side. Each is a
                  pill-bordered tile in its own accent color (blue for API,
                  green for APP) with a breathing dot, the URL on its own
                  line in mono, and a single primary action that flips
                  with state. The duplicate STOP/RESTART pair from QUICK
                  ACTIONS was redundant — actions live here, on the thing
                  they act on. */}
              {(info?.api_url || info?.app_url) && (() => {
                const ServiceTile = (props: {
                  kind: "API" | "APP";
                  glyph: string;
                  accent: string;        // css var for the accent color
                  url: string;
                  running: boolean;
                  busy: boolean;
                  onStart: () => void;
                  onStop: () => void;
                  onRestart: () => void;
                  onClickUrl?: () => void;
                }) => {
                  const stateColor = props.running ? "var(--crt-green)" : "var(--crt-red)";
                  return (
                    <div
                      className="section-card"
                      style={{ ['--card-accent' as any]: props.accent }}
                    >
                      <span className="section-card__bar" />
                      <div className="pl-5 pr-4 py-3.5 flex flex-col gap-2.5">
                        <div className="flex items-center justify-between">
                          <div className="section-card__title">
                            <span className="section-card__glyph">{props.glyph}</span>
                            {props.kind}
                          </div>
                          <span
                            className="status-pill"
                            style={{ ['--pill-accent' as any]: stateColor }}
                          >
                            <span className={`status-pill__dot ${props.running ? "led-pulse" : ""}`} />
                            {props.running ? "ONLINE" : "OFFLINE"}
                          </span>
                        </div>
                        <button
                          onClick={props.onClickUrl}
                          disabled={!props.onClickUrl}
                          className="text-left text-[12px] font-mono truncate transition-colors"
                          style={{
                            color: props.running ? "var(--text-secondary)" : "var(--text-tertiary)",
                            cursor: props.onClickUrl ? "pointer" : "default",
                            opacity: 0.95,
                          }}
                          title={props.url}
                        >
                          {props.url}
                        </button>
                        <div className="flex items-center gap-1.5">
                          {props.running ? (
                            <>
                              <button
                                onClick={props.onStop}
                                disabled={props.busy}
                                className="text-[10px] px-2.5 py-1 rounded uppercase font-bold tracking-wider transition-all"
                                style={{
                                  border: "1px solid color-mix(in srgb, var(--crt-red) 35%, transparent)",
                                  color: "var(--crt-red)",
                                  background: "color-mix(in srgb, var(--crt-red) 8%, transparent)",
                                }}
                              >
                                {props.busy ? "…" : "STOP"}
                              </button>
                              <button
                                onClick={props.onRestart}
                                disabled={props.busy}
                                className="text-[10px] px-2.5 py-1 rounded uppercase font-bold tracking-wider transition-all"
                                style={{
                                  border: "1px solid color-mix(in srgb, var(--crt-amber) 35%, transparent)",
                                  color: "var(--crt-amber)",
                                  background: "color-mix(in srgb, var(--crt-amber) 8%, transparent)",
                                }}
                              >
                                {props.busy ? "…" : "RESTART"}
                              </button>
                            </>
                          ) : (
                            <button
                              onClick={props.onStart}
                              disabled={props.busy}
                              className="text-[10px] px-3 py-1 rounded uppercase font-bold tracking-wider transition-all"
                              style={{
                                border: "1px solid color-mix(in srgb, var(--crt-green) 45%, transparent)",
                                color: "var(--crt-green)",
                                background: "color-mix(in srgb, var(--crt-green) 10%, transparent)",
                              }}
                            >
                              {props.busy ? "…" : "START"}
                            </button>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                };
                return (
                  <div className={`grid gap-3 ${info?.api_url && info?.app_url ? "grid-cols-1 sm:grid-cols-2" : "grid-cols-1"}`}>
                    {info?.api_url && (
                      <ServiceTile
                        kind="API"
                        glyph="⚡"
                        accent="var(--crt-blue)"
                        url={info.api_url}
                        running={!!moduleRunning}
                        busy={togglingApi}
                        onStart={() => startProcess("api")}
                        onStop={() => stopProcess("api")}
                        onRestart={() => restartProcess("api")}
                      />
                    )}
                    {info?.app_url && (
                      <ServiceTile
                        kind="APP"
                        glyph="◈"
                        accent="var(--crt-green)"
                        url={info.app_url}
                        running={!!appRunning}
                        busy={togglingApp}
                        onStart={() => startProcess("app")}
                        onStop={() => stopProcess("app")}
                        onRestart={() => restartProcess("app")}
                        onClickUrl={() => setSidebarView("app")}
                      />
                    )}
                  </div>
                );
              })()}

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

              {/* Whitelist — owner-managed list of EOAs allowed to sign in
                  to this module. Backed by GET/POST/DELETE /whitelist on
                  the Rust API; non-owners see read-only rows + a hint.
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
                      No addresses whitelisted yet. The configured owner is always allowed.
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
                <div style={{ fontSize: 24, fontWeight: 600, marginTop: 4 }}>{selectedModule}</div>
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

      {/* ── Compact Nav Bar ───────────────────────────────────────── */}
      <div
        className="flex flex-col shrink-0"
        style={{
          background: "var(--bg-secondary)",
          borderBottom: `1px solid ${subtleBorder}`,
        }}
      >
        {/* Module selector + mod tabs (overview/app/api/files/terminal). */}
        <div className="flex items-center px-4 py-1.5">
          <div className="flex items-center gap-3">
            {/* Wrench: BUILD / FORK / EDIT actions side panel.
                Sits to the LEFT of the selected module name. Clicking
                opens a tabbed panel anchored under the icon. */}
            <div className="relative" ref={headerCreateRef}>
              <button
                onClick={() => setAgentSidebarOpen((v) => !v)}
                className="flex items-center justify-center transition-all hover:brightness-125 rounded-sm"
                style={{
                  width: 28,
                  height: 28,
                  border: `1px solid ${agentSidebarOpen ? "var(--crt-green)" : "rgba(16,185,129,0.25)"}`,
                  color: agentSidebarOpen ? "var(--crt-green)" : "rgba(16,185,129,0.55)",
                  background: agentSidebarOpen ? "rgba(16,185,129,0.10)" : "transparent",
                }}
                title={agentSidebarOpen ? "Collapse sidebar" : "Expand sidebar"}
                aria-expanded={agentSidebarOpen}
                aria-label="Toggle sidebar"
              >
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>
                </svg>
              </button>

              {showHeaderCreateForm && (
                <div
                  className="absolute left-0 top-full mt-1 border z-50 flex flex-col min-w-[320px]"
                  style={{
                    background: "var(--bg-primary)",
                    borderColor: showHeaderCreateForm === "fork"
                      ? "rgba(245,158,11,0.3)"
                      : showHeaderCreateForm === "edit"
                      ? "rgba(59,130,246,0.3)"
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
                        : "var(--crt-green)",
                    }}>
                      {showHeaderCreateForm === "fork"
                        ? `⑂ FORK FROM ${selectedModule?.toUpperCase() || "?"}`
                        : showHeaderCreateForm === "edit"
                        ? `✎ EDIT ${selectedModule?.toUpperCase() || "?"}`
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
                    <div className="flex items-center gap-2">
                      <button
                        onClick={headerCreateOrFork}
                        disabled={(showHeaderCreateForm === "edit" ? !headerEditPrompt.trim() : !headerNewName.trim()) || submitting}
                        className="pixel-btn text-[14px] py-1 px-4 uppercase flex-1"
                        style={{
                          letterSpacing: "0.01em",
                          opacity: (showHeaderCreateForm === "edit" ? headerEditPrompt.trim() : headerNewName.trim()) ? 1 : 0.4,
                        }}
                      >
                        {submitting
                          ? "..."
                          : showHeaderCreateForm === "fork"
                          ? "FORK"
                          : showHeaderCreateForm === "edit"
                          ? "EDIT"
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

            {/* Module/Folder selector dropdown */}
            <div className="relative" ref={headerModuleRef}>
              {showHeaderModuleDropdown ? (
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
                        if (selectorMode === "modules" && moduleList.length > 0) {
                          const firstModule = moduleList[0];
                          resetModuleState(firstModule);
                          setSelectedModule(firstModule.name);
                          setSelectedModuleInfo(firstModule);
                          setWorkDir(firstModule.path);
                          setHeaderModuleSearch("");
                          setShowHeaderModuleDropdown(false);
                          fetchModuleConfig(firstModule.name);
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
                    placeholder={selectorMode === "modules" ? "search modules..." : "search folders..."}
                    className="px-3 py-1 bg-transparent text-crt-green border border-crt-green/40 font-code outline-none w-[220px]"
                    style={{ letterSpacing: "0.01em", fontSize: "20px" }}
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
              ) : (
                <button
                  onClick={() => {
                    setShowHeaderModuleDropdown(true);
                    setHeaderModuleSearch("");
                    if (selectorMode === "modules") {
                      if (!moduleList.length) fetchModules("");
                    } else {
                      fetchFolders("");
                    }
                  }}
                  className="flex items-center gap-2 font-bold text-crt-green font-code cursor-pointer hover:text-crt-green/80 transition-colors group"
                  style={{ letterSpacing: "0.01em", fontSize: "20px" }}
                  title="Click to switch module/folder (Tab to toggle mode)"
                >
                  {selectedModule || "claude"}
                  <span style={{ color: "var(--crt-green)", opacity: 0.25, fontSize: "11px", transition: "opacity 0.2s" }} className="group-hover:!opacity-50">▾</span>
                </button>
              )}
              {/* Modules dropdown */}
              {showHeaderModuleDropdown && selectorMode === "modules" && moduleList.length > 0 && (() => {
                const owners = [...new Set(moduleList.map(m => m.owner).filter(Boolean))] as string[];
                // Plain substring filter on the typed query — the server-side
                // /modules?q= ranking can be fuzzy, but the dropdown should
                // only show names that literally contain what you typed.
                const q = headerModuleSearch.trim().toLowerCase();
                let filtered = ownerFilter ? moduleList.filter(m => m.owner === ownerFilter) : moduleList;
                if (q) filtered = filtered.filter(m => m.name.toLowerCase().includes(q));
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
                  {filtered.map((m) => (
                    <div
                      key={m.name}
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
                          {m.cid && (
                            <span className="text-[10px] px-1 py-0.5 border font-code shrink-0 border-crt-green/12 text-crt-green/20" title={m.cid}>
                              {m.cid.slice(0, 6)}..{m.cid.slice(-4)}
                            </span>
                          )}
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
                        {m.path && (
                          <span className="text-[10px] font-mono text-crt-green/15 truncate" title={m.path}>
                            {m.path.replace(/^.*\/mod\/orbit\//, "orbit/").replace(/^.*\/mod\//, "~/mod/")}
                          </span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
                );
              })()}
              {/* Folders dropdown with embedding suggestions */}
              {showHeaderModuleDropdown && selectorMode === "folders" && (folderList.length > 0 || folderSuggestions.length > 0) && (
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

          {/* Navigation Tabs — inline with module selector. On phone we
              still want the tabs visible but the row may wrap; `flex-wrap`
              prevents the address chip from getting pushed off-screen. */}
          <div className="flex items-center gap-0 ml-2 sm:ml-4 flex-wrap nav-tabs-mobile-scroll">
          {([
            { key: "overview" as const, label: "OVERVIEW", icon: "◆", color: "var(--crt-amber)" },
            ...(selectedModuleInfo?.app_url || selectedModuleInfo?.has_app_dir ? [{ key: "app" as const, label: "APP", icon: "◈", color: "var(--crt-green)" }] : []),
            ...(selectedModuleInfo?.api_url || selectedModuleInfo?.has_api_dir || moduleConfig?.config?.endpoints ? [{ key: "api" as const, label: "API", icon: "⚡", color: "var(--crt-red)" }] : []),
            { key: "files" as const, label: "FILES", icon: "◇", color: "var(--text-primary)" },
            { key: "versions" as const, label: "VERSIONS", icon: "⌬", color: "var(--crt-purple, #c084fc)" },
            ...(isOwner ? [{ key: "terminal" as const, label: "TERMINAL", icon: "▶_", color: "var(--crt-blue)" }] : []),
          ]).map((tab) => {
            const isActive = sidebarView === tab.key;
            return (
              <button
                key={tab.key}
                onClick={() => setSidebarView(tab.key)}
                className="text-[15px] font-bold transition-all px-3 py-2 font-code flex items-center gap-1.5 relative"
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
                <span className="text-[13px]">{tab.icon}</span>
                {tab.label}
              </button>
            );
          })}
          </div>

          {/* Address chip (doubles as profile dropdown trigger) + Agent toggle */}
          <div className="flex items-center gap-1.5 shrink-0 ml-auto">
            {/* User profile chip — when a wallet is connected, clicking
                pops out the full wallet sidebar (mirrors the AGENT
                toggle pattern). For local/no-wallet sessions it opens
                the small profile dropdown instead. */}
            <div ref={profileMenuRef} className="relative">
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  if (address && address !== "local" && walletType) {
                    setShowWalletSidebar(o => !o);
                  } else {
                    setProfileMenuOpen(o => !o);
                  }
                }}
                className="text-[12px] font-bold font-mono px-2 py-1 transition-all flex items-center gap-1.5"
                style={{
                  color: "var(--crt-green)",
                  opacity: (showWalletSidebar || profileMenuOpen) ? 1 : 0.5,
                  borderRadius: 4,
                  background: (showWalletSidebar || profileMenuOpen) ? "color-mix(in srgb, var(--crt-green) 8%, transparent)" : "transparent",
                }}
                title={address && address !== "local" && walletType
                  ? (showWalletSidebar ? "Close wallet panel" : "Open wallet panel")
                  : (address ? `Profile: ${address}` : "Sign in")}
                aria-expanded={address && address !== "local" && walletType ? showWalletSidebar : profileMenuOpen}
                aria-haspopup={address && address !== "local" && walletType ? undefined : "menu"}
              >
                {address ? (address === "local" ? "LOCAL" : `${address.slice(0, 6)}··${address.slice(-4)}`) : "SIGN IN"}
                <span className="text-[9px]" style={{ opacity: 0.6 }}>
                  {address && address !== "local" && walletType
                    ? (showWalletSidebar ? "◨" : "◧")
                    : (profileMenuOpen ? "▴" : "▾")}
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
                        className="text-[9px] uppercase tracking-[0.1em] px-1.5 py-0.5 rounded shrink-0"
                        style={{
                          color: "var(--crt-amber)",
                          background: "color-mix(in srgb, var(--crt-amber) 10%, transparent)",
                          border: "1px solid color-mix(in srgb, var(--crt-amber) 35%, transparent)",
                        }}
                      >
                        OWNER
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
                        setShowWalletSidebar(true);
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
                    <span className="flex items-center gap-2">
                      <span style={{ color: "var(--crt-amber)" }}>◈</span>
                      {showOwnerSidebar ? "Close owner panel" : "Open owner panel"}
                    </span>
                    <span className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>{showOwnerSidebar ? "✕" : "↗"}</span>
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


      {error && (
        <div className="mx-4 mt-2 p-3 border-2 border-crt-red/50" style={{ background: "rgba(239,68,68,0.05)" }}>
          <div className="text-[14px] text-crt-red flex items-center gap-2">
            <span>⚠</span> {error}
          </div>
        </div>
      )}

      <div className="flex-1 flex flex-row overflow-hidden relative">

        {/* ── Sidebar: Agent ────────────────────────────────────────────
            One AGENT button in the header toggles this. On desktop it's
            a flex sibling that pushes main content (so the embedded app
            iframe shrinks to the remaining width and nothing the iframe
            renders — like the Next.js dev build-error overlay — gets
            hidden behind it). On phone it's a fullscreen overlay. */}
        {isMobile && agentSidebarOpen && (
          <div
            onClick={() => setAgentSidebarOpen(false)}
            style={{
              position: "fixed",
              inset: 0,
              background: "rgba(0,0,0,0.45)",
              backdropFilter: "blur(2px)",
              zIndex: 60,
            }}
            aria-label="Close agent panel"
          />
        )}
        <div
          className="flex flex-col overflow-hidden"
          style={
            isMobile
              ? {
                  position: agentSidebarOpen ? "fixed" : "static",
                  inset: agentSidebarOpen ? 0 : "auto",
                  width: agentSidebarOpen ? "100vw" : "0px",
                  height: agentSidebarOpen ? "100dvh" : "0px",
                  maxWidth: "100vw",
                  background: "var(--bg-primary)",
                  zIndex: 70,
                  boxShadow: agentSidebarOpen ? "0 0 40px rgba(0,0,0,0.6)" : "none",
                  transition: "none",
                }
              : {
                  position: "relative",
                  width: agentSidebarOpen ? `${agentSidebarWidth}px` : "0px",
                  minWidth: agentSidebarOpen ? "320px" : "0px",
                  maxWidth: "100%",
                  flexShrink: 0,
                  background: "var(--bg-primary)",
                  borderRight: agentSidebarOpen ? "1px solid rgba(251,191,36,0.30)" : "none",
                  boxShadow: agentSidebarOpen
                    ? "12px 0 32px rgba(0,0,0,0.35), inset -1px 0 0 rgba(251,191,36,0.10), 0 0 60px -20px rgba(251,191,36,0.25)"
                    : "none",
                  transition: isAgentSidebarDragging ? "none" : "width 0.25s ease",
                }
          }
        >
          {/* Drag handle on the right edge to resize the overlay sidebar */}
          {!isMobile && agentSidebarOpen && (
            <div
              onMouseDown={(e) => {
                e.preventDefault();
                setIsAgentSidebarDragging(true);
              }}
              style={{
                position: "absolute",
                top: 0,
                bottom: 0,
                right: -3,
                width: 6,
                cursor: "col-resize",
                zIndex: 1,
                background: isAgentSidebarDragging ? "rgba(251,191,36,0.40)" : "transparent",
                transition: "background 0.15s ease",
              }}
              title="Drag to resize"
            />
          )}
          {agentSidebarOpen && renderAgentTab()}
        </div>

        {/* ── Main Content ──────────────────────── */}
        <div
          className="flex-1 flex flex-col overflow-hidden min-w-0"
          style={{ background: "var(--bg-primary)" }}
        >
            {sidebarView === "overview" ? (
              <div className="flex-1 flex flex-col overflow-hidden">
                {renderProfileTab()}
              </div>
            ) : sidebarView === "api" ? (
              <div className="flex-1 flex flex-col overflow-hidden">
                {renderApiTab()}
              </div>
            ) : sidebarView === "app" ? (
              <div className="flex-1 flex flex-col overflow-hidden">
                {renderAppTab()}
              </div>
            ) : sidebarView === "logs" ? (
              <div className="flex-1 flex flex-col overflow-hidden">
                {renderLogsTab()}
              </div>
            ) : sidebarView === "terminal" ? (
              <div className="flex-1 flex flex-col overflow-hidden">
                {renderTerminalTab()}
              </div>
            ) : sidebarView === "files" ? (
              filesPanelFloating ? (
                <div className="flex-1 flex flex-col overflow-hidden">
                  {renderProfileTab()}
                </div>
              ) : (
                <div className="flex-1 flex flex-col overflow-hidden">
                  {renderDirectoryTab()}
                </div>
              )
            ) : sidebarView === "versions" ? (
              <div className="flex-1 flex flex-col overflow-hidden">
                {selectedModule ? (
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
                )}
              </div>
            ) : (
              <div className="flex-1 flex flex-col overflow-hidden">
                {renderProfileTab()}
              </div>
            )}
        </div>

        {/* ── Right Sidebar: Wallet ────────────────────────────────────
            Mirrors the AGENT sidebar pattern: flex sibling on desktop
            (pushes main content, drag-to-resize on the left edge),
            fullscreen overlay on mobile. */}
        {isMobile && showWalletSidebar && address && address !== "local" && walletType && (
          <div
            onClick={() => setShowWalletSidebar(false)}
            style={{
              position: "fixed",
              inset: 0,
              background: "rgba(0,0,0,0.45)",
              backdropFilter: "blur(2px)",
              zIndex: 60,
            }}
            aria-label="Close wallet panel"
          />
        )}
        {address && address !== "local" && walletType && (
          <div
            className="flex flex-col overflow-hidden"
            style={
              isMobile
                ? {
                    position: showWalletSidebar ? "fixed" : "static",
                    inset: showWalletSidebar ? 0 : "auto",
                    width: showWalletSidebar ? "100vw" : "0px",
                    height: showWalletSidebar ? "100dvh" : "0px",
                    maxWidth: "100vw",
                    background: "var(--bg-primary)",
                    zIndex: 70,
                    boxShadow: showWalletSidebar ? "0 0 40px rgba(0,0,0,0.6)" : "none",
                    transition: "none",
                  }
                : {
                    position: "relative",
                    width: showWalletSidebar ? `${walletSidebarWidth}px` : "0px",
                    minWidth: showWalletSidebar ? "320px" : "0px",
                    maxWidth: "100%",
                    flexShrink: 0,
                    background: "var(--bg-primary)",
                    borderLeft: showWalletSidebar ? "1px solid rgba(59,130,246,0.30)" : "none",
                    boxShadow: showWalletSidebar
                      ? "-12px 0 32px rgba(0,0,0,0.35), inset 1px 0 0 rgba(59,130,246,0.10), 0 0 60px -20px rgba(59,130,246,0.25)"
                      : "none",
                    transition: isWalletSidebarDragging ? "none" : "width 0.25s ease",
                  }
            }
          >
            {/* Drag handle on the LEFT edge to resize the sidebar */}
            {!isMobile && showWalletSidebar && (
              <div
                onMouseDown={(e) => {
                  e.preventDefault();
                  setIsWalletSidebarDragging(true);
                }}
                style={{
                  position: "absolute",
                  top: 0,
                  bottom: 0,
                  left: -3,
                  width: 6,
                  cursor: "col-resize",
                  zIndex: 1,
                  background: isWalletSidebarDragging ? "rgba(59,130,246,0.40)" : "transparent",
                  transition: "background 0.15s ease",
                }}
                title="Drag to resize"
              />
            )}
            {showWalletSidebar && (
              <WalletModal
                address={address}
                walletType={walletType}
                onClose={() => setShowWalletSidebar(false)}
                onDisconnect={() => {
                  setShowWalletSidebar(false);
                  disconnect();
                }}
                inline={true}
                isOwner={isOwner}
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
          </div>
        )}

        {/* ── Right Sidebar: Owner ─────────────────────────────────────
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
                }
              : {
                  position: "relative",
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
            return (
              <div className="flex flex-col h-full overflow-hidden">
                {/* Header */}
                <div
                  className="flex items-center justify-between px-4 py-3 shrink-0"
                  style={{ borderBottom: `1px solid ${subtleBorder}`, background: tintBg }}
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <span style={{ fontSize: 14, color: "var(--crt-amber)" }}>◈</span>
                    <div className="flex flex-col min-w-0">
                      <span
                        className="text-[12px] font-bold uppercase tracking-[0.12em] truncate leading-tight"
                        style={{ color: "var(--text-secondary)" }}
                      >
                        Owner
                      </span>
                      {selectedModule && (
                        <span
                          className="text-[10px] font-mono truncate leading-tight"
                          style={{ color: "var(--text-tertiary)" }}
                          title={selectedModule}
                        >
                          {selectedModule}
                        </span>
                      )}
                    </div>
                    {youAreOwner && (
                      <span
                        className="text-[9px] uppercase tracking-[0.1em] px-1.5 py-0.5 rounded shrink-0"
                        style={{
                          color: "var(--crt-amber)",
                          background: "color-mix(in srgb, var(--crt-amber) 12%, transparent)",
                          border: "1px solid color-mix(in srgb, var(--crt-amber) 35%, transparent)",
                        }}
                      >
                        YOU
                      </span>
                    )}
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
                    title="Close owner panel"
                  >
                    ✕
                  </button>
                </div>

                {/* Body */}
                <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-3">
                  {/* Owner identity — gradient avatar derived from the address;
                      the whole row is click-to-copy */}
                  {cfgOwner && (() => {
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

                  {/* Module info */}
                  {selectedModule && (
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
                          No addresses whitelisted yet. The configured owner is always allowed.
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

                  {/* Owner-only quick actions */}
                  {isOwner && (
                    <div className="section-card" data-accent="red">
                      <span className="section-card__bar" />
                      <div className="section-card__head">
                        <div className="section-card__title">
                          <span className="section-card__glyph">⚙</span>
                          Owner Actions
                        </div>
                      </div>
                      <div className="section-card__body flex flex-col gap-2">
                        <button
                          onClick={() => {
                            setShowOwnerSidebar(false);
                            setSidebarView("terminal");
                          }}
                          className="flex items-center gap-2.5 text-left px-2.5 py-2 rounded-lg border transition-colors group"
                          style={{
                            borderColor: "var(--border-color)",
                            background: "var(--bg-secondary)",
                          }}
                          onMouseEnter={e => {
                            e.currentTarget.style.background = "color-mix(in srgb, var(--crt-amber) 8%, transparent)";
                            e.currentTarget.style.borderColor = "color-mix(in srgb, var(--crt-amber) 30%, var(--border-color))";
                          }}
                          onMouseLeave={e => {
                            e.currentTarget.style.background = "var(--bg-secondary)";
                            e.currentTarget.style.borderColor = "var(--border-color)";
                          }}
                        >
                          <span
                            className="flex items-center justify-center shrink-0 font-mono text-[10px] rounded-md"
                            style={{
                              width: 26,
                              height: 26,
                              color: "var(--crt-amber)",
                              background: "color-mix(in srgb, var(--crt-amber) 12%, transparent)",
                              border: "1px solid color-mix(in srgb, var(--crt-amber) 25%, transparent)",
                            }}
                            aria-hidden
                          >
                            {">_"}
                          </span>
                          <span className="flex flex-col min-w-0 flex-1 gap-0.5 leading-tight">
                            <span className="text-[12px]" style={{ color: "var(--text-primary)" }}>Open terminal</span>
                            <span className="text-[10px] truncate" style={{ color: "var(--text-tertiary)" }}>
                              Shell session in this module
                            </span>
                          </span>
                          <span className="text-[11px] shrink-0" style={{ color: "var(--text-tertiary)" }}>↗</span>
                        </button>
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
                      </div>
                    </div>
                  )}

                  {/* Footer — anchors the empty space below the cards */}
                  <div className="flex-1" />
                  <div
                    className="flex items-center justify-center gap-1.5 pt-3 pb-1 text-[9px] uppercase tracking-[0.18em] shrink-0"
                    style={{ color: "var(--text-tertiary)", opacity: 0.55 }}
                  >
                    <span style={{ color: "var(--crt-amber)" }}>◈</span>
                    owner controls
                  </div>
                </div>
              </div>
            );
          })()}
        </div>

      </div>

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
            }}
          >
            <span className="text-[12px] text-crt-green/50 font-code" style={{ letterSpacing: "0.05em" }}>
              ⠿ FILES
            </span>
            <div className="flex items-center gap-1">
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
            }}
          >
            <svg width="16" height="16" viewBox="0 0 16 16" className="text-white/15 hover:text-white/30 transition-colors">
              <path d="M14 14L8 14L14 8Z" fill="currentColor" />
              <path d="M14 14L11 14L14 11Z" fill="currentColor" opacity="0.5" />
            </svg>
          </div>
          {/* Resize edges */}
          <div className="absolute top-0 right-0 bottom-0 w-1 cursor-e-resize"
            onMouseDown={(e) => { e.preventDefault(); filesPanelResize.current = { startX: e.clientX, startY: e.clientY, origW: filesPanelSize.w, origH: filesPanelSize.h, edge: "e" }; document.body.style.cursor = 'e-resize'; document.body.style.userSelect = 'none'; }}
          />
          <div className="absolute bottom-0 left-0 right-0 h-1 cursor-s-resize"
            onMouseDown={(e) => { e.preventDefault(); filesPanelResize.current = { startX: e.clientX, startY: e.clientY, origW: filesPanelSize.w, origH: filesPanelSize.h, edge: "s" }; document.body.style.cursor = 's-resize'; document.body.style.userSelect = 'none'; }}
          />
        </div>
      )}

      {/* ── Status Bar ───────────────────────────────────────────── */}
      <footer
        className="flex items-center justify-end gap-2 px-3 sm:px-5 py-1 flex-wrap"
        style={{
          background: "var(--bg-secondary)",
          borderTop: "1px solid var(--border-color)",
        }}
      >
        {/* Owner chip — module owner identity + owner panel toggle */}
        {selectedModule && effectiveConfig?.owner && (
          <button
            className="text-[12px] px-2 py-0.5 mr-auto font-mono transition-all cursor-pointer flex items-center gap-1.5"
            title={showOwnerSidebar ? "Close owner panel" : "Open owner panel"}
            onClick={(e) => {
              e.stopPropagation();
              setShowOwnerSidebar(o => !o);
            }}
            style={{
              color: "var(--crt-green)",
              background: showOwnerSidebar
                ? "color-mix(in srgb, var(--crt-green) 18%, transparent)"
                : "color-mix(in srgb, var(--crt-green) 8%, transparent)",
              border: "1px solid color-mix(in srgb, var(--crt-green) 30%, transparent)",
              borderRadius: "4px",
              letterSpacing: "0.02em",
            }}
            onMouseEnter={e => (e.currentTarget.style.background = "color-mix(in srgb, var(--crt-green) 16%, transparent)")}
            onMouseLeave={e => (e.currentTarget.style.background = showOwnerSidebar
              ? "color-mix(in srgb, var(--crt-green) 18%, transparent)"
              : "color-mix(in srgb, var(--crt-green) 8%, transparent)")}
            aria-expanded={showOwnerSidebar}
          >
            <span
              className="shrink-0 inline-block rounded-full"
              style={{
                width: "6px",
                height: "6px",
                background: "var(--crt-green)",
                boxShadow: "0 0 6px var(--crt-green)",
              }}
            />
            <span className="font-bold" style={{ letterSpacing: "0.08em", opacity: 0.75 }}>OWNER</span>
            {`${effectiveConfig.owner.slice(0, 6)}··${effectiveConfig.owner.slice(-4)}`}
            <span className="text-[9px]" style={{ opacity: 0.6 }}>
              {showOwnerSidebar ? "◨" : "◧"}
            </span>
          </button>
        )}
        <div className="flex items-center gap-3">
          {showBackendEditor ? (
            <form
              className="flex items-center gap-1"
              onSubmit={(e) => {
                e.preventDefault();
                const url = backendInput.trim();
                if (url) {
                  setApiUrl(url);
                  safeSetItem("claude_backend_url", url);
                }
                setShowBackendEditor(false);
              }}
            >
              <span className="text-[13px]" style={{ color: "var(--text-tertiary)", opacity: 0.6 }}>
                BACKEND:
              </span>
              <input
                autoFocus
                className="text-[13px] bg-transparent border-b outline-none"
                style={{
                  color: "var(--accent-color)",
                  borderColor: "var(--accent-color)",
                  width: "220px",
                  fontFamily: "inherit",
                }}
                value={backendInput}
                onChange={(e) => setBackendInput(e.target.value)}
                onBlur={() => setShowBackendEditor(false)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") setShowBackendEditor(false);
                }}
                placeholder="http://localhost:8820"
              />
            </form>
          ) : (
            <span
              className="text-[13px] cursor-pointer hover:opacity-70 transition-opacity"
              style={{ color: "var(--text-tertiary)", opacity: 0.4 }}
              onClick={() => {
                setBackendInput(apiUrl);
                setShowBackendEditor(true);
              }}
              title="Click to change backend URL"
            >
              BACKEND: {moduleApiUrl.replace(/^https?:\/\//, "")}
            </span>
          )}
          <span style={{ color: "var(--text-tertiary)", opacity: 0.2 }}>
            │
          </span>
          {/* Versions (mod-protocol snapshot/fork/restore) */}
          <button
            onClick={() => setShowVersions(true)}
            className="pixel-btn text-[13px] py-0.5 px-2"
            style={{
              background: "transparent",
              color: "var(--accent-color)",
              border: "1px solid var(--accent-color)",
            }}
            title="Versions · snapshot / fork / restore via mod protocol"
            disabled={!selectedModule}
          >
            VERSIONS
          </button>
          <span style={{ color: "var(--text-tertiary)", opacity: 0.2 }}>
            │
          </span>
          {/* Theme toggle — dark ⇄ light only */}
          <button
            onClick={() => setTheme(theme === "light" ? "dark" : "light")}
            className="pixel-btn text-[13px] py-0.5 px-2"
            style={{
              background: "var(--accent-color)",
              color: isLight ? "#fff" : "#000",
            }}
            title={`Switch to ${theme === "light" ? "DARK" : "LIGHT"} mode`}
          >
            {theme === "light" ? "LIGHT" : "DARK"}
          </button>
          <span style={{ color: "var(--text-tertiary)", opacity: 0.2 }}>
            │
          </span>
          <span
            className="text-[13px]"
            style={{ color: "var(--text-tertiary)", opacity: 0.4 }}
          >
            {new Date().toLocaleTimeString()}
          </span>
        </div>
      </footer>

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

      {/* Wallet Modal */}
      {showWalletModal && address && address !== "local" && walletType && (
        <WalletModal
          address={address}
          walletType={walletType}
          onClose={() => setShowWalletModal(false)}
          onDisconnect={() => {
            setShowWalletModal(false);
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


    </div>
  );
}
