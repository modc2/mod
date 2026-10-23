"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import ThemeToggle from "./ThemeToggle";
import { highlight } from "./tex";
import type { Paper } from "./tex";

type RootInfo = { root: string | null; epoch: number | null; count?: number } | null;
type ModEntry = { name: string; port?: number | null; scope?: string };
type ProofResult = { leaf: string; proof: string[]; root: string; record: Record<string, unknown>; index?: number; total?: number };
type HistoryEntry = { epoch: number; root: string; count: number; scope: string };

const API = process.env.NEXT_PUBLIC_API_URL || "/api/whitepaper";

function ProofTreeSvg({ index, total, depth, step = null }: { index: number; total: number; depth: number; step?: number | null }) {
  if (depth < 1) return null;

  const W = 220;
  const H = 88;
  const padX = 10;
  const padY = 10;

  // Cap visual depth so leaf nodes remain visible (max 64 leaf slots at vd=6)
  const vd = Math.min(depth, 6);
  const leafSlots = 1 << vd;
  const di = depth > vd
    ? Math.min(Math.round((index / (1 << depth)) * leafSlots), leafSlots - 1)
    : Math.min(index, leafSlots - 1);

  const iW = W - 2 * padX;
  const iH = H - 2 * padY;

  function nodePos(layer: number, p: number) {
    const count = 1 << (vd - layer);
    return {
      x: padX + ((p + 0.5) / count) * iW,
      y: padY + iH - (layer / vd) * iH,
    };
  }

  // 0=proved leaf, 1=ancestor, 2=proof sibling, 3=other, 4=root
  function nodeRole(layer: number, p: number): 0 | 1 | 2 | 3 | 4 {
    if (layer === vd) return 4;
    const ancestor = di >> layer;
    if (layer === 0 && p === di) return 0;
    if (layer > 0 && p === ancestor) return 1;
    if (p === (ancestor ^ 1)) return 2;
    return 3;
  }

  const baseR = vd <= 3 ? 3.5 : vd === 4 ? 3 : vd === 5 ? 2.5 : 1.8;
  const fills: Record<number, string> = {
    0: "rgb(var(--modblue))",
    1: "rgb(var(--modblue) / 0.4)",
    2: "rgb(var(--amber))",
    3: "rgb(var(--rule) / 0.5)",
    4: "rgb(var(--modcyan))",
  };

  const edges: React.ReactNode[] = [];
  const circles: React.ReactNode[] = [];

  for (let L = 0; L < vd; L++) {
    const count = 1 << (vd - L);
    for (let p = 0; p < count; p++) {
      const { x: x1, y: y1 } = nodePos(L, p);
      const { x: x2, y: y2 } = nodePos(L + 1, p >> 1);
      edges.push(
        <line key={`e${L}-${p}`} x1={x1} y1={y1} x2={x2} y2={y2}
          stroke="rgb(var(--rule) / 0.35)" strokeWidth="0.8" />
      );
    }
  }

  for (let L = 0; L <= vd; L++) {
    const count = L === vd ? 1 : 1 << (vd - L);
    for (let p = 0; p < count; p++) {
      const { x, y } = nodePos(L, p);
      const rl = nodeRole(L, p);
      const nr = rl === 0 ? baseR + 0.8 : rl === 4 ? baseR + 0.5 : baseR;
      circles.push(
        <circle key={`n${L}-${p}`} cx={x} cy={y} r={nr}
          style={{ fill: fills[rl] }} />
      );
    }
  }

  const overlays: React.ReactNode[] = [];
  if (step !== null) {
    if (step === -1) {
      const { x, y } = nodePos(0, di);
      overlays.push(
        <circle key={`glow-leaf-${step}`} cx={x} cy={y} r={baseR + 5}
          style={{ fill: "rgb(var(--modblue))", animation: "svgtree-flash 0.75s ease-out forwards" }} />
      );
    } else if (step >= 0 && step < vd) {
      const sibP = (di >> step) ^ 1;
      const { x: sx, y: sy } = nodePos(step, sibP);
      overlays.push(
        <circle key={`glow-sib-${step}`} cx={sx} cy={sy} r={baseR + 4}
          style={{ fill: "rgb(var(--amber))", animation: "svgtree-flash 0.6s ease-out forwards" }} />
      );
      const ancP = di >> step;
      const { x: lx1, y: ly1 } = nodePos(step, ancP);
      const { x: lx2, y: ly2 } = nodePos(step + 1, ancP >> 1);
      overlays.push(
        <line key={`edge-${step}`} x1={lx1} y1={ly1} x2={lx2} y2={ly2}
          style={{ stroke: "rgb(var(--amber))", strokeWidth: 1.8, animation: "svgtree-flash 0.6s ease-out forwards" }} />
      );
    } else {
      const { x, y } = nodePos(vd, 0);
      overlays.push(
        <circle key={`glow-root-${step}`} cx={x} cy={y} r={baseR + 5}
          style={{ fill: "rgb(var(--modcyan))", animation: "svgtree-flash 0.75s ease-out forwards" }} />
      );
    }
  }

  return (
    <svg width={W} height={H} style={{ display: "block", margin: "6px 0 2px", overflow: "visible" }}>
      {edges}
      {circles}
      {overlays}
      <text x={W - padX} y={H - 1} textAnchor="end"
        style={{ fontSize: "8px", fill: "rgb(var(--muted) / 0.6)", fontFamily: "monospace" }}>
        {index} / {total}
      </text>
    </svg>
  );
}

function short(h: string) {
  return h.length > 22 ? `${h.slice(0, 12)}…${h.slice(-8)}` : h;
}

export default function RootClient({ paper, initialRoot }: { paper: Paper; initialRoot: RootInfo }) {
  const [root, setRoot] = useState<RootInfo>(initialRoot);
  const [online, setOnline] = useState(initialRoot !== null);
  const [refreshing, setRefreshing] = useState(false);
  const [rebuilding, setRebuilding] = useState(false);
  const [copied, setCopied] = useState(false);
  const [active, setActive] = useState<string>("");
  const [progress, setProgress] = useState(0);
  const [tocOpen, setTocOpen] = useState(false);
  const [exploreOpen, setExploreOpen] = useState(false);
  const [mods, setMods] = useState<ModEntry[] | null>(null);
  const [modsLoading, setModsLoading] = useState(false);
  const [proofs, setProofs] = useState<Record<string, ProofResult | "loading" | "error">>({});
  const [verifications, setVerifications] = useState<Record<string, "idle" | "loading" | "ok" | "fail">>({});
  const [tamperedVerifications, setTamperedVerifications] = useState<Record<string, "idle" | "loading" | "fail">>({});
  const [configs, setConfigs] = useState<Record<string, Record<string, unknown> | "loading" | null>>({});
  const [scope, setScope] = useState<"orbit" | "core" | "all">("orbit");
  const [filter, setFilter] = useState("");
  const [copiedPermalink, setCopiedPermalink] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState<HistoryEntry[] | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [batchProving, setBatchProving] = useState(false);
  const [watchMode, setWatchMode] = useState(false);
  const [liveCount, setLiveCount] = useState<number | null>(null);
  const pendingProve = useRef<string | null>(null);
  const watchCtxRef = useRef<{ root: RootInfo; historyOpen: boolean }>({ root: initialRoot, historyOpen: false });
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchIndex, setSearchIndex] = useState(0);
  const [matchCount, setMatchCount] = useState(0);
  const [replayStep, setReplayStep] = useState<Record<string, number | null>>({});
  const replayTimerRef = useRef<Record<string, ReturnType<typeof setInterval>>>({});
  const [codeOpen, setCodeOpen] = useState<Record<string, boolean>>({});
  const [copiedCode, setCopiedCode] = useState<string | null>(null);
  const [codeTab, setCodeTab] = useState<Record<string, "solidity" | "python" | "js" | "rust" | "cli">>({});
  const articleRef = useRef<HTMLElement>(null);
  const matchRefs = useRef<HTMLElement[]>([]);
  const mentionElemsRef = useRef<Map<string, HTMLElement[]>>(new Map());
  const searchInputRef = useRef<HTMLInputElement>(null);
  const registryRef = useRef<HTMLElement>(null);
  const [registryInView, setRegistryInView] = useState(true);

  async function refresh() {
    setRefreshing(true);
    try {
      const r = await fetch(`${API}/tree/root`, { cache: "no-store" });
      setOnline(r.ok);
      if (r.ok) setRoot(await r.json());
    } catch {
      setOnline(false);
    } finally {
      setRefreshing(false);
    }
  }

  async function rebuild() {
    setRebuilding(true);
    try {
      await fetch(`${API}/tree/merkle`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope }),
      });
      await refresh();
    } finally {
      setRebuilding(false);
    }
  }

  async function fetchMods() {
    setModsLoading(true);
    try {
      const r = await fetch(`${API}/mods?scope=${scope}`, { cache: "no-store" });
      if (r.ok) {
        const data = await r.json();
        setMods(data.mods ?? []);
      }
    } finally {
      setModsLoading(false);
    }
  }

  async function prove(name: string) {
    if (!root?.root) {
      await rebuild();
    }
    setProofs((p) => ({ ...p, [name]: "loading" }));
    try {
      const r = await fetch(`${API}/tree/proof`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (r.ok) {
        const data = await r.json() as ProofResult;
        setProofs((p) => ({ ...p, [name]: data }));
        fetchConfig(name);
      } else {
        setProofs((p) => ({ ...p, [name]: "error" }));
      }
    } catch {
      setProofs((p) => ({ ...p, [name]: "error" }));
    }
  }

  async function proveAll() {
    setBatchProving(true);
    try {
      if (!root?.root) await rebuild();
      await Promise.all(filteredMods.map((m) => prove(m.name)));
    } finally {
      setBatchProving(false);
    }
  }

  async function verifyProof(name: string, prf: ProofResult) {
    setVerifications((v) => ({ ...v, [name]: "loading" }));
    try {
      const r = await fetch(`${API}/tree/verify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ leaf: prf.leaf, proof: prf.proof, root: prf.root }),
      });
      if (r.ok) {
        const data = await r.json();
        setVerifications((v) => ({ ...v, [name]: data.ok ? "ok" : "fail" }));
      } else {
        setVerifications((v) => ({ ...v, [name]: "fail" }));
      }
    } catch {
      setVerifications((v) => ({ ...v, [name]: "fail" }));
    }
  }

  async function tamperProof(name: string, prf: ProofResult) {
    if (prf.proof.length === 0) return;
    setTamperedVerifications((v) => ({ ...v, [name]: "loading" }));
    const mutated = [...prf.proof];
    const orig = mutated[0];
    const lastChar = orig[orig.length - 1];
    mutated[0] = orig.slice(0, -1) + (lastChar === "f" ? "0" : "f");
    try {
      const r = await fetch(`${API}/tree/verify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ leaf: prf.leaf, proof: mutated, root: prf.root }),
      });
      if (r.ok) {
        const data = await r.json();
        setTamperedVerifications((v) => ({ ...v, [name]: data.ok ? "idle" : "fail" }));
      } else {
        setTamperedVerifications((v) => ({ ...v, [name]: "fail" }));
      }
    } catch {
      setTamperedVerifications((v) => ({ ...v, [name]: "fail" }));
    }
  }

  async function fetchConfig(name: string) {
    setConfigs((c) => ({ ...c, [name]: "loading" }));
    try {
      const r = await fetch(`${API}/mods/${name}/config`, { cache: "no-store" });
      if (r.ok) {
        const data = (await r.json()) as Record<string, unknown>;
        setConfigs((c) => ({ ...c, [name]: data }));
      } else {
        setConfigs((c) => ({ ...c, [name]: null }));
      }
    } catch {
      setConfigs((c) => ({ ...c, [name]: null }));
    }
  }

  function downloadProof(name: string, prf: ProofResult) {
    const bundle = { name, scope, leaf: prf.leaf, proof: prf.proof, root: prf.root, record: prf.record, exported: new Date().toISOString() };
    const url = URL.createObjectURL(new Blob([JSON.stringify(bundle, null, 2)], { type: "application/json" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${name}-proof.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function exportAll() {
    const proofEntries: Record<string, { leaf: string; proof: string[]; root: string; record: Record<string, unknown> }> = {};
    for (const [name, prf] of Object.entries(proofs)) {
      if (prf && prf !== "loading" && prf !== "error") {
        proofEntries[name] = { leaf: prf.leaf, proof: prf.proof, root: prf.root, record: prf.record };
      }
    }
    const verifiedCount = Object.keys(verifications).filter((n) => verifications[n] === "ok").length;
    const bundle = {
      root: root?.root ?? null,
      epoch: root?.epoch ?? null,
      count: root?.count ?? null,
      scope,
      exported: new Date().toISOString(),
      verifiedCount,
      proofs: proofEntries,
    };
    const filename = `registry-${scope}-${root?.epoch ?? "unknown"}.json`;
    const url = URL.createObjectURL(new Blob([JSON.stringify(bundle, null, 2)], { type: "application/json" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  function startReplay(name: string, proofLength: number) {
    if (replayTimerRef.current[name] != null) {
      clearInterval(replayTimerRef.current[name]);
      delete replayTimerRef.current[name];
    }
    let step = -1;
    setReplayStep(r => ({ ...r, [name]: step }));
    const id = setInterval(() => {
      step++;
      setReplayStep(r => ({ ...r, [name]: step }));
      if (step >= proofLength) {
        clearInterval(id);
        delete replayTimerRef.current[name];
      }
    }, 750);
    replayTimerRef.current[name] = id;
  }

  function toggleExplore() {
    const next = !exploreOpen;
    setExploreOpen(next);
    if (next && mods === null) fetchMods();
  }

  async function fetchHistory() {
    setHistoryLoading(true);
    try {
      const r = await fetch(`${API}/tree/history`, { cache: "no-store" });
      if (r.ok) {
        const data = await r.json();
        setHistory(data.history ?? []);
      }
    } finally {
      setHistoryLoading(false);
    }
  }

  function toggleHistory() {
    const next = !historyOpen;
    setHistoryOpen(next);
    if (next && history === null) fetchHistory();
  }

  // On mount: read ?scope=&prove= params and auto-open the explore panel.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const proveParam = params.get("prove");
    const scopeParam = params.get("scope") as "orbit" | "core" | "all" | null;
    if (proveParam) {
      if (scopeParam && ["orbit", "core", "all"].includes(scopeParam)) {
        setScope(scopeParam);
      }
      setExploreOpen(true);
      pendingProve.current = proveParam;
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // After mods load, fire pendingProve if set.
  useEffect(() => {
    if (mods !== null && pendingProve.current) {
      const name = pendingProve.current;
      pendingProve.current = null;
      prove(name);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mods]);

  // After a proof lands, push ?scope=&prove= into the address bar.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const provedNames = Object.keys(proofs).filter(
      (n) => proofs[n] && proofs[n] !== "loading" && proofs[n] !== "error"
    );
    if (provedNames.length === 1) {
      const name = provedNames[0];
      const url = new URL(window.location.href);
      url.searchParams.set("scope", scope);
      url.searchParams.set("prove", name);
      window.history.replaceState(null, "", url.toString());
    } else if (provedNames.length === 0) {
      const url = new URL(window.location.href);
      if (url.searchParams.has("prove")) {
        url.searchParams.delete("prove");
        url.searchParams.delete("scope");
        window.history.replaceState(null, "", url.toString());
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [proofs]);

  useEffect(() => {
    const t = setInterval(refresh, 15_000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    watchCtxRef.current = { root, historyOpen };
  });

  useEffect(() => {
    if (!watchMode) { setLiveCount(null); return; }
    const tick = async () => {
      try {
        const r = await fetch(`${API}/mods?scope=${scope}`, { cache: "no-store" });
        if (!r.ok) return;
        const data = await r.json();
        const count: number | undefined = data.count;
        if (typeof count !== "number") return;
        setLiveCount(count);
        const ctx = watchCtxRef.current;
        if (ctx.root?.count != null && count !== ctx.root.count) {
          await rebuild();
          if (ctx.historyOpen) fetchHistory();
        }
      } catch {
        // ignore network errors
      }
    };
    const t = setInterval(tick, 30_000);
    return () => clearInterval(t);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [watchMode, scope]);

  useEffect(() => {
    setFilter("");
    setProofs({});
    setVerifications({});
    setTamperedVerifications({});
    setConfigs({});
    setLiveCount(null);
    if (exploreOpen) {
      setMods(null);
      fetchMods();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope]);

  // Reading progress + scrollspy.
  useEffect(() => {
    const ids = paper.toc.map((t) => t.id);
    const onScroll = () => {
      const h = document.documentElement;
      const max = h.scrollHeight - h.clientHeight;
      setProgress(max > 0 ? Math.min(1, h.scrollTop / max) : 0);
      let cur = "";
      for (const id of ids) {
        const el = document.getElementById(id);
        if (el && el.getBoundingClientRect().top < 120) cur = id;
        else if (el) break;
      }
      setActive(cur);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [paper.toc]);

  // Section of the active entry, so the TOC only unfolds the current section.
  const activeSection = useMemo(() => {
    let sec = "";
    for (const t of paper.toc) {
      if (t.level < 2) sec = t.id;
      if (t.id === active) return sec;
    }
    return "";
  }, [active, paper.toc]);

  const minutes = Math.max(1, Math.round(paper.words / 230));
  const stale = !rebuilding && mods !== null && root?.count != null && mods.length !== root.count;
  const filteredMods = (mods ?? []).filter((m) =>
    m.name.toLowerCase().includes(filter.toLowerCase())
  );
  const unprovedCount = filteredMods.filter(
    (m) => !proofs[m.name] || proofs[m.name] === "error"
  ).length;
  const allProved = filteredMods.length > 0 && unprovedCount === 0;

  // Auto-verify all modules when allProved transitions to true.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!allProved) return;
    filteredMods.forEach((m) => {
      const prf = proofs[m.name];
      if (!prf || prf === "loading" || prf === "error") return;
      if (verifications[m.name]) return;
      verifyProof(m.name, prf as ProofResult);
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allProved]);

  function copyRoot() {
    if (!root?.root) return;
    navigator.clipboard?.writeText(root.root).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    });
  }

  function copyPermalink(name: string) {
    const url = new URL(window.location.origin + "/whitepaper");
    url.searchParams.set("scope", scope);
    url.searchParams.set("prove", name);
    navigator.clipboard?.writeText(url.toString()).then(() => {
      setCopiedPermalink(name);
      setTimeout(() => setCopiedPermalink(null), 1200);
    });
  }

  function buildSoliditySnippet(modName: string, prf: ProofResult): string {
    const lines = [
      `// SPDX-License-Identifier: MIT`,
      `// import "@openzeppelin/contracts/utils/cryptography/MerkleProof.sol";`,
      ``,
      `// Off-chain leaf derivation (computed before calling this contract):`,
      `//   string memory canonical = canonical_json(record); // compact JSON, keys sorted`,
      `//   bytes32 leaf = keccak256(bytes(canonical));`,
      `//   canonical_json: sort all keys lexicographically, no whitespace`,
      ``,
      `// Verify that module "${modName}" is in the registry`,
      `bytes32 root = ${prf.root};`,
      `bytes32 leaf = ${prf.leaf};`,
      `bytes32[] memory proof = new bytes32[](${prf.proof.length});`,
      ...prf.proof.map((p, i) => `proof[${i}] = ${p};`),
      ``,
      `bool valid = MerkleProof.verifyCalldata(proof, root, leaf);`,
    ];
    return lines.join("\n");
  }

  function buildPythonSnippet(modName: string, prf: ProofResult): string {
    const proofList = prf.proof.map((p) => `    bytes.fromhex("${p.slice(2)}")`).join(",\n");
    const recordJson = JSON.stringify(prf.record);
    const lines = [
      `# pip install pycryptodome`,
      `from Crypto.Hash import keccak`,
      `import json`,
      ``,
      `def keccak256(b: bytes) -> bytes:`,
      `    h = keccak.new(digest_bits=256)`,
      `    h.update(b)`,
      `    return h.digest()`,
      ``,
      `def pair(a: bytes, b: bytes) -> bytes:`,
      `    return keccak256(a + b) if a < b else keccak256(b + a)`,
      ``,
      `def verify(leaf: bytes, proof: list, root: bytes) -> bool:`,
      `    cur = leaf`,
      `    for sib in proof:`,
      `        cur = pair(cur, sib)`,
      `    return cur == root`,
      ``,
      `# Derive leaf for "${modName}" from the live record`,
      `record = json.loads('${recordJson}')`,
      `canonical = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()`,
      `leaf = keccak256(canonical)`,
      `assert leaf.hex() == "${prf.leaf.slice(2)}", "leaf mismatch"`,
      ``,
      `proof = [`,
      proofList || `    # (no siblings — single-leaf tree)`,
      `]`,
      `root  = bytes.fromhex("${prf.root.slice(2)}")`,
      ``,
      `print("valid:", verify(leaf, proof, root))`,
    ];
    return lines.join("\n");
  }

  function buildJSSnippet(modName: string, prf: ProofResult): string {
    const proofItems = prf.proof.map((p) => `  "${p}"`).join(",\n");
    const recordJson = JSON.stringify(prf.record);
    const lines = [
      `// npm install ethers`,
      `import { ethers } from "ethers";`,
      ``,
      `function pair(a, b) {`,
      `  return BigInt(a) < BigInt(b)`,
      `    ? ethers.keccak256(ethers.concat([a, b]))`,
      `    : ethers.keccak256(ethers.concat([b, a]));`,
      `}`,
      ``,
      `function verify(leaf, proof, root) {`,
      `  let cur = leaf;`,
      `  for (const sib of proof) cur = pair(cur, sib);`,
      `  return cur.toLowerCase() === root.toLowerCase();`,
      `}`,
      ``,
      `// Derive leaf for "${modName}" from the live record`,
      `const record = JSON.parse('${recordJson}');`,
      `const canonical = JSON.stringify(Object.fromEntries(Object.entries(record).sort(([a],[b]) => a < b ? -1 : 1)));`,
      `const leaf  = ethers.keccak256(ethers.toUtf8Bytes(canonical));`,
      `console.assert(leaf === "${prf.leaf}", "leaf mismatch");`,
      ``,
      `const proof = [`,
      proofItems || `  // (no siblings — single-leaf tree)`,
      `];`,
      `const root  = "${prf.root}";`,
      ``,
      `console.log("valid:", verify(leaf, proof, root));`,
    ];
    return lines.join("\n");
  }

  function buildRustSnippet(modName: string, prf: ProofResult): string {
    const proofItems = prf.proof.map((p) => `        "${p}",`).join("\n");
    const recordJson = JSON.stringify(prf.record);
    const lines = [
      `// Cargo.toml`,
      `// [dependencies]`,
      `// tiny_keccak = { version = "2", features = ["keccak"] }`,
      `// hex = "0.4"`,
      `// serde_json = "1"`,
      ``,
      `use std::collections::BTreeMap;`,
      `use tiny_keccak::{Hasher, Keccak};`,
      ``,
      `fn keccak256(input: &[u8]) -> [u8; 32] {`,
      `    let mut hasher = Keccak::v256();`,
      `    hasher.update(input);`,
      `    let mut out = [0u8; 32];`,
      `    hasher.finalize(&mut out);`,
      `    out`,
      `}`,
      ``,
      `fn pair(a: &[u8; 32], b: &[u8; 32]) -> [u8; 32] {`,
      `    let mut buf = [0u8; 64];`,
      `    if a <= b {`,
      `        buf[..32].copy_from_slice(a);`,
      `        buf[32..].copy_from_slice(b);`,
      `    } else {`,
      `        buf[..32].copy_from_slice(b);`,
      `        buf[32..].copy_from_slice(a);`,
      `    }`,
      `    keccak256(&buf)`,
      `}`,
      ``,
      `fn decode32(s: &str) -> [u8; 32] {`,
      `    let s = s.strip_prefix("0x").unwrap_or(s);`,
      `    let bytes = hex::decode(s).expect("valid hex");`,
      `    let mut out = [0u8; 32];`,
      `    out.copy_from_slice(&bytes);`,
      `    out`,
      `}`,
      ``,
      `fn verify(leaf: &[u8; 32], proof: &[&str], root: &str) -> bool {`,
      `    let mut cur = *leaf;`,
      `    for sib in proof {`,
      `        cur = pair(&cur, &decode32(sib));`,
      `    }`,
      `    cur == decode32(root)`,
      `}`,
      ``,
      `// Derive leaf for "${modName}" from the live record`,
      `fn main() {`,
      `    let record_json = r#"${recordJson}"#;`,
      `    let record: BTreeMap<String, serde_json::Value> =`,
      `        serde_json::from_str(record_json).unwrap();`,
      `    let canonical = serde_json::to_string(&record).unwrap();`,
      `    let leaf = keccak256(canonical.as_bytes());`,
      `    assert_eq!(hex::encode(leaf), "${prf.leaf.slice(2)}", "leaf mismatch");`,
      ``,
      `    let proof: &[&str] = &[`,
      proofItems || `        // (no siblings — single-leaf tree)`,
      `    ];`,
      `    let root  = "${prf.root}";`,
      `    println!("valid: {}", verify(&leaf, proof, root));`,
      `}`,
    ];
    return lines.join("\n");
  }

  function buildCliSnippet(modName: string, prf: ProofResult): string {
    const proofJson = JSON.stringify(prf.proof);
    const lines = [
      `# Verify proof for "${modName}" using the mod CLI:`,
      `m whitepaper/tree_verify \\`,
      `  leaf="${prf.leaf}" \\`,
      `  proof='${proofJson}'`,
      ``,
      `# Optional: specify a different root`,
      `# m whitepaper/tree_verify leaf="${prf.leaf}" proof='${proofJson}' root="${prf.root}"`,
    ];
    return lines.join("\n");
  }

  function activeSnippet(modName: string, prf: ProofResult): string {
    const tab = codeTab[modName] ?? "solidity";
    if (tab === "python") return buildPythonSnippet(modName, prf);
    if (tab === "js") return buildJSSnippet(modName, prf);
    if (tab === "rust") return buildRustSnippet(modName, prf);
    if (tab === "cli") return buildCliSnippet(modName, prf);
    return buildSoliditySnippet(modName, prf);
  }

  function copyCode(modName: string, prf: ProofResult) {
    navigator.clipboard?.writeText(activeSnippet(modName, prf)).then(() => {
      setCopiedCode(modName);
      setTimeout(() => setCopiedCode(null), 1200);
    });
  }

  // Cmd+K / Ctrl+K opens the search bar; Escape closes it.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setSearchOpen(true);
      } else if (e.key === "Escape") {
        setSearchOpen(false);
        setSearchQuery("");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (searchOpen) searchInputRef.current?.focus();
  }, [searchOpen]);

  useEffect(() => {
    if (!articleRef.current) return;
    articleRef.current.querySelectorAll<HTMLElement>(".search-hit").forEach((el) => {
      el.replaceWith(...Array.from(el.childNodes));
    });
    matchRefs.current = [];
    setMatchCount(0);
    const q = searchQuery.trim().toLowerCase();
    if (!q) { setSearchIndex(0); return; }
    const walker = document.createTreeWalker(articleRef.current, NodeFilter.SHOW_TEXT);
    const nodes: Text[] = [];
    let n: Node | null;
    while ((n = walker.nextNode())) {
      const t = n as Text;
      if (t.nodeValue && t.nodeValue.toLowerCase().includes(q)) nodes.push(t);
    }
    const marks: HTMLElement[] = [];
    for (const textNode of nodes) {
      const text = textNode.nodeValue ?? "";
      const lower = text.toLowerCase();
      const frags: Node[] = [];
      let last = 0;
      let idx = lower.indexOf(q, 0);
      while (idx !== -1) {
        if (idx > last) frags.push(document.createTextNode(text.slice(last, idx)));
        const mark = document.createElement("mark");
        mark.className = "search-hit";
        mark.textContent = text.slice(idx, idx + q.length);
        frags.push(mark);
        marks.push(mark);
        last = idx + q.length;
        idx = lower.indexOf(q, last);
      }
      if (last < text.length) frags.push(document.createTextNode(text.slice(last)));
      textNode.replaceWith(...frags);
    }
    matchRefs.current = marks;
    setMatchCount(marks.length);
    if (marks.length > 0) {
      marks[0].classList.add("is-current");
      marks[0].scrollIntoView({ block: "center" });
    }
    setSearchIndex(0);
  }, [searchQuery]);

  useEffect(() => {
    const marks = matchRefs.current;
    if (!marks.length) return;
    marks.forEach((el, i) => {
      if (el.isConnected) el.classList.toggle("is-current", i === searchIndex);
    });
    const cur = marks[searchIndex];
    if (cur?.isConnected) cur.scrollIntoView({ block: "center" });
  }, [searchIndex]);

  // Annotate <code> elements in the paper body that exactly match a loaded module name.
  useEffect(() => {
    if (!mods || !articleRef.current) return;
    const modNames = new Set(mods.map((m) => m.name));
    const handlers: Array<{ el: HTMLElement; fn: () => void }> = [];
    const mentionMap = new Map<string, HTMLElement[]>();

    articleRef.current.querySelectorAll<HTMLElement>("code").forEach((el) => {
      const name = el.textContent?.trim() ?? "";
      if (!modNames.has(name)) return;
      el.classList.add("mod-mention");
      el.title = "prove in registry →";
      const fn = () => {
        setExploreOpen(true);
        setFilter(name);
        prove(name);
      };
      el.addEventListener("click", fn);
      handlers.push({ el, fn });
      if (!mentionMap.has(name)) mentionMap.set(name, []);
      mentionMap.get(name)!.push(el);
    });

    mentionElemsRef.current = mentionMap;

    return () => {
      handlers.forEach(({ el, fn }) => {
        el.removeEventListener("click", fn);
        el.classList.remove("mod-mention", "mod-mention-proved", "mod-mention-verified");
        if (el.title === "prove in registry →") el.title = "";
      });
      mentionElemsRef.current = new Map();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mods]);

  // Reflect proof/verification state onto inline <code> module mentions.
  useEffect(() => {
    const map = mentionElemsRef.current;
    if (!map.size) return;
    map.forEach((els, name) => {
      const prf = proofs[name];
      const vrf = verifications[name];
      for (const el of els) {
        el.classList.remove("mod-mention-proved", "mod-mention-verified");
        if (prf && prf !== "loading" && prf !== "error") {
          if (vrf === "ok") {
            el.classList.add("mod-mention-verified");
          } else {
            el.classList.add("mod-mention-proved");
          }
        }
      }
    });
  }, [proofs, verifications]);

  useEffect(() => {
    const el = registryRef.current;
    if (!el) return;
    const obs = new IntersectionObserver(([entry]) => setRegistryInView(entry.isIntersecting), { threshold: 0 });
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  function navigateSearch(delta: number) {
    if (!matchRefs.current.length) return;
    setSearchIndex((i) => (i + delta + matchRefs.current.length) % matchRefs.current.length);
  }

  function closeSearch() {
    setSearchOpen(false);
    setSearchQuery("");
  }

  function handleSearchKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      e.preventDefault();
      navigateSearch(e.shiftKey ? -1 : 1);
    } else if (e.key === "Escape") {
      closeSearch();
    }
  }

  const toc = (
    <nav className="toc" aria-label="Contents">
      {paper.toc.map((t) => {
        let sec = "";
        for (const u of paper.toc) {
          if (u.level < 2) sec = u.id;
          if (u === t) break;
        }
        if (t.level === 2 && sec !== activeSection) return null;
        return (
          <a
            key={t.id}
            href={`#${t.id}`}
            onClick={() => setTocOpen(false)}
            className={`toc-l${t.level} ${active === t.id ? "toc-active" : ""}`}
          >
            {t.level === 0 ? (
              <>
                <span className="toc-part">{t.num}</span>
                {t.title}
              </>
            ) : (
              <>
                {t.num && <span className="toc-num">{t.num}</span>}
                {t.title}
              </>
            )}
          </a>
        );
      })}
    </nav>
  );

  return (
    <>
      <div className="progress" style={{ transform: `scaleX(${progress})` }} aria-hidden />

      {searchOpen && (
        <div className="search-bar">
          <input
            ref={searchInputRef}
            type="text"
            className="search-bar-input"
            placeholder="search paper…"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={handleSearchKeyDown}
          />
          <span className="search-count">
            {searchQuery ? `${matchCount > 0 ? searchIndex + 1 : 0} of ${matchCount}` : ""}
          </span>
          <button className="btn btn-sm" onClick={() => navigateSearch(-1)} disabled={matchCount === 0}>↑</button>
          <button className="btn btn-sm" onClick={() => navigateSearch(1)} disabled={matchCount === 0}>↓</button>
          <button className="btn btn-sm" onClick={closeSearch}>✕</button>
        </div>
      )}

      <header className="topbar">
        <div className="topbar-in">
          <a href="#top" className="brand">
            <span className="brand-mark">MOD</span>
            <span className="brand-sep">/</span>
            <span>whitepaper</span>
          </a>
          <div className="flex items-center gap-2">
            {paper.version && <span className="chip">v{paper.version}</span>}
            <a className="btn" href={`${API}/paper.tex`} target="_blank" rel="noreferrer">
              .tex
            </a>
            <button className="btn lg:hidden" onClick={() => setTocOpen((o) => !o)} aria-expanded={tocOpen}>
              Contents
            </button>
            <button
              className={`btn${searchOpen ? " btn-active" : ""}`}
              onClick={() => searchOpen ? closeSearch() : setSearchOpen(true)}
              title="Search paper (Cmd+K)"
            >
              search
            </button>
            <ThemeToggle />
          </div>
        </div>
        {tocOpen && <div className="toc-drawer lg:hidden">{toc}</div>}
      </header>

      <div className="shell" id="top">
        <aside className="sidebar">{toc}</aside>

        <article className="prose-paper" ref={articleRef}>
          <section className="hero">
            <p className="eyebrow">MOD Protocol · Whitepaper</p>
            <h1>{paper.title}</h1>
            {paper.subtitle && <p className="subtitle" dangerouslySetInnerHTML={{ __html: paper.subtitle }} />}
            <div className="meta">
              {paper.version && <span>Version {paper.version}</span>}
              {paper.date && <span>{paper.date}</span>}
              <span>{minutes} min read</span>
            </div>
          </section>

          <section className="registry" aria-label="Live registry" ref={registryRef}>
            <div className="registry-head">
              <span className={`dot ${watchMode ? "dot-watch" : online ? "dot-on" : "dot-off"}`} />
              <span>{watchMode ? "Reference registry · watching" : online ? "Reference registry · live" : "Reference registry · offline"}</span>
              <div className="ml-auto flex gap-2">
                <button onClick={() => setWatchMode((w) => !w)} className={`btn btn-sm${watchMode ? " btn-active" : ""}`}>
                  {watchMode ? "watching" : "watch"}
                </button>
                <button onClick={rebuild} disabled={rebuilding || refreshing} className={`btn btn-sm${stale ? " btn-warn" : ""}`}>
                  {rebuilding ? "…" : "rebuild"}
                </button>
                <button onClick={refresh} disabled={refreshing || rebuilding} className="btn btn-sm">
                  {refreshing ? "…" : "refresh"}
                </button>
              </div>
            </div>
            <div className="registry-grid">
              <div>
                <div className="k">Merkle root</div>
                <button className="v v-root" onClick={copyRoot} title={root?.root ?? ""} disabled={!root?.root}>
                  {root?.root ? (copied ? "copied" : short(root.root)) : "no tree built"}
                </button>
              </div>
              <div>
                <div className="k">Epoch</div>
                <div className="v">
                  {root?.epoch
                    ? new Date(root.epoch * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
                    : "—"}
                </div>
              </div>
              <div>
                <div className="k">Records</div>
                <div className="v" style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
                  {root?.count ?? "—"}
                  {watchMode && liveCount !== null && liveCount !== root?.count && (
                    <span className="chip">→ {liveCount}</span>
                  )}
                  {stale && (
                    <span className="chip chip-warn">⚠ {root?.count} → {mods!.length}</span>
                  )}
                </div>
              </div>
            </div>

            <div className="explore-toggle">
              <button className="btn btn-sm" onClick={toggleExplore}>
                {exploreOpen ? "hide" : "explore →"}
              </button>
              {(["orbit", "core", "all"] as const).map((s) => (
                <button
                  key={s}
                  className={`btn btn-sm${scope === s ? " btn-active" : ""}`}
                  onClick={() => setScope(s)}
                >
                  {s}
                </button>
              ))}
              <button className="btn btn-sm" onClick={toggleHistory}>
                {historyOpen ? "hide history" : "history →"}
              </button>
            </div>

            {historyOpen && (
              <div className="explore-section">
                {historyLoading && <p className="explore-hint">Loading history…</p>}
                {!historyLoading && history !== null && history.length === 0 && (
                  <p className="explore-hint">No builds recorded yet — rebuild to start the log.</p>
                )}
                {!historyLoading && history !== null && history.length > 0 && (
                  <table className="history-table">
                    <thead>
                      <tr>
                        <th>epoch</th>
                        <th>root</th>
                        <th>count</th>
                        <th>scope</th>
                      </tr>
                    </thead>
                    <tbody>
                      {history.map((h, i) => (
                        <tr key={i}>
                          <td>{new Date(h.epoch * 1000).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" })}</td>
                          <td><code>{short(h.root)}</code></td>
                          <td>{h.count}</td>
                          <td>{h.scope}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            )}

            {exploreOpen && (
              <div className="explore-section">
                {modsLoading && <p className="explore-hint">Loading modules…</p>}
                {!modsLoading && mods !== null && mods.length === 0 && (
                  <p className="explore-hint">No modules found.</p>
                )}
                {!modsLoading && mods !== null && mods.length > 0 && (
                  <>
                    <input
                      className="explore-filter"
                      placeholder="filter modules…"
                      value={filter}
                      onChange={(e) => setFilter(e.target.value)}
                    />
                    <span className="explore-hint">
                      {filteredMods.length} of {mods.length}
                    </span>
                    {filteredMods.length > 0 && unprovedCount > 0 && (
                      <button
                        className="btn btn-sm"
                        disabled={batchProving}
                        onClick={proveAll}
                      >
                        {batchProving ? "…" : `prove all ${unprovedCount}`}
                      </button>
                    )}
                    {allProved && (
                      <button
                        className="btn btn-sm"
                        onClick={() => setProofs({})}
                      >
                        clear all
                      </button>
                    )}
                    {allProved && Object.keys(proofs).length > 0 && (
                      <button
                        className="btn btn-sm"
                        onClick={exportAll}
                      >
                        ⬇ export registry
                      </button>
                    )}
                    {allProved && (() => {
                      const verifyingCount = filteredMods.filter((m) => verifications[m.name] === "loading").length;
                      const verifiedCount = filteredMods.filter((m) => verifications[m.name] === "ok").length;
                      const failedCount = filteredMods.filter((m) => verifications[m.name] === "fail").length;
                      const settledCount = verifiedCount + failedCount;
                      const allSettled = verifyingCount === 0 && settledCount === filteredMods.length;
                      const sharedRoot = (Object.values(proofs).find((p) => p && p !== "loading" && p !== "error") as ProofResult | undefined)?.root;
                      if (allSettled && failedCount === 0) {
                        return (
                          <div style={{ padding: "6px 0", fontSize: "0.82rem", fontFamily: "monospace", color: "rgb(var(--green))" }}>
                            ✓ {verifiedCount} modules verified{sharedRoot ? ` · root ${short(sharedRoot)}` : ""}
                          </div>
                        );
                      }
                      if (allSettled && failedCount > 0) {
                        return (
                          <div style={{ padding: "6px 0", fontSize: "0.82rem", fontFamily: "monospace", color: "rgb(var(--amber))" }}>
                            ⚠ {verifiedCount}/{filteredMods.length} verified — {failedCount} failed
                          </div>
                        );
                      }
                      return (
                        <div style={{ padding: "6px 0", fontSize: "0.82rem", color: "rgb(var(--muted))" }}>
                          verifying…{verifyingCount > 0 ? ` ${verifyingCount} in progress ·` : ""} {verifiedCount} verified{failedCount > 0 ? ` · ${failedCount} failed` : ""}
                        </div>
                      );
                    })()}
                    {filteredMods.length > 0 && (
                      <div className="registry-heatmap">
                        {filteredMods.map((m) => {
                          const prf = proofs[m.name];
                          const vrf = verifications[m.name];
                          let cls = "hm-tile hm-unproven";
                          if (prf === "loading") cls = "hm-tile hm-loading";
                          else if (prf === "error") cls = "hm-tile hm-failed";
                          else if (prf) {
                            if (vrf === "ok") cls = "hm-tile hm-verified";
                            else if (vrf === "fail") cls = "hm-tile hm-failed";
                            else cls = "hm-tile hm-proved";
                          }
                          return (
                            <div
                              key={m.name}
                              className={cls}
                              title={m.name}
                              onClick={() => {
                                const el = document.getElementById(`mod-row-${m.name}`);
                                if (el) el.scrollIntoView({ behavior: "smooth", block: "nearest" });
                              }}
                            >
                              {m.name}
                            </div>
                          );
                        })}
                      </div>
                    )}
                    <div className="mod-list">
                    {filteredMods.map((m) => {
                      const prf = proofs[m.name];
                      const hasProof = prf && prf !== "loading" && prf !== "error";
                      return (
                        <div key={m.name} id={`mod-row-${m.name}`} className="mod-row">
                          <div className="mod-row-info">
                            <span className="mod-name">{m.name}</span>
                            {m.port != null && <span className="mod-port">:{m.port}</span>}
                            {scope === "all" && m.scope === "core" && <span className="mod-port">core</span>}
                            {hasProof && (
                              <button
                                className="btn btn-sm"
                                title="Copy permalink"
                                onClick={() => copyPermalink(m.name)}
                              >
                                {copiedPermalink === m.name ? "copied" : "⎘"}
                              </button>
                            )}
                          </div>
                          <button
                            className="btn btn-sm"
                            disabled={prf === "loading"}
                            onClick={() => {
                              if (hasProof) {
                                setProofs((p) => {
                                  const next = { ...p };
                                  delete next[m.name];
                                  return next;
                                });
                              } else {
                                prove(m.name);
                              }
                            }}
                          >
                            {prf === "loading" ? "…" : hasProof ? "hide" : "prove"}
                          </button>
                          {hasProof && (
                            <div className="proof-block">
                              {(() => {
                                const liveConfig = configs[m.name];
                                const isLoaded = liveConfig != null && liveConfig !== "loading";
                                const entries = Object.entries((prf as ProofResult).record);
                                const anyDrift = isLoaded && entries.some(([k, v]) => {
                                  const lv = (liveConfig as Record<string, unknown>)[k];
                                  return lv !== undefined && JSON.stringify(lv) !== JSON.stringify(v);
                                });
                                return (
                                  <>
                                    {entries.map(([k, v]) => {
                                      const lv = isLoaded ? (liveConfig as Record<string, unknown>)[k] : undefined;
                                      const drifted = isLoaded && lv !== undefined && JSON.stringify(lv) !== JSON.stringify(v);
                                      return (
                                        <div key={k} className="proof-line proof-record">
                                          <span className="proof-label">{k}</span>
                                          {drifted ? (
                                            <span style={{ color: "rgb(var(--amber))" }}>
                                              {typeof v === "string" ? v : JSON.stringify(v)} ⚠ live: {typeof lv === "string" ? lv : JSON.stringify(lv)}
                                            </span>
                                          ) : (
                                            <>{typeof v === "string" ? v : JSON.stringify(v)}</>
                                          )}
                                        </div>
                                      );
                                    })}
                                    {isLoaded && !anyDrift && (
                                      <div className="proof-line" style={{ color: "rgb(var(--green) / .8)", marginTop: "2px" }}>✓ config matches</div>
                                    )}
                                  </>
                                );
                              })()}
                              <div className="proof-ladder">
                                <div className={`proof-rung${replayStep[m.name] === -1 ? " is-active" : ""}`}>
                                  <span className="proof-node is-leaf">{(prf as ProofResult).leaf}</span>
                                </div>
                                {(prf as ProofResult).proof.map((s, i) => (
                                  <div key={i} className={`proof-rung${replayStep[m.name] === i ? " is-active" : ""}`}>
                                    <span className="proof-arrow">↓ pair with π[{i}]</span>
                                    <span className={`proof-sibling${i === 0 && tamperedVerifications[m.name] === "fail" ? " proof-sibling-tampered" : ""}`}>{s}</span>
                                  </div>
                                ))}
                                <div className={`proof-rung${replayStep[m.name] === (prf as ProofResult).proof.length ? " is-active" : ""}`}>
                                  <span className="proof-arrow">↓ root</span>
                                  <span className="proof-node is-root">{(prf as ProofResult).root}</span>
                                </div>
                              </div>
                              <div className="flex items-center gap-2 mt-2">
                                <button
                                  className="btn btn-sm"
                                  onClick={() => downloadProof(m.name, prf as ProofResult)}
                                >
                                  &#8595; proof
                                </button>
                                <button
                                  className="btn btn-sm"
                                  disabled={verifications[m.name] === "loading"}
                                  onClick={() => verifyProof(m.name, prf as ProofResult)}
                                >
                                  {verifications[m.name] === "loading" ? "…" : "verify →"}
                                </button>
                                <button
                                  className="btn btn-sm"
                                  disabled={replayStep[m.name] != null && replayStep[m.name]! < (prf as ProofResult).proof.length}
                                  onClick={() => startReplay(m.name, (prf as ProofResult).proof.length)}
                                >
                                  {replayStep[m.name] == null
                                    ? "▶ animate"
                                    : replayStep[m.name]! < (prf as ProofResult).proof.length
                                    ? "…"
                                    : "▶ replay"}
                                </button>
                                <button
                                  className={`btn btn-sm${codeOpen[m.name] ? " btn-active" : ""}`}
                                  onClick={() => setCodeOpen((c) => ({ ...c, [m.name]: !c[m.name] }))}
                                >
                                  code →
                                </button>
                                {verifications[m.name] === "ok" && (
                                  <span className="badge badge-live">&#10003; verified</span>
                                )}
                                {verifications[m.name] === "fail" && (
                                  <span className="badge badge-invalid">&#10007; invalid</span>
                                )}
                                {verifications[m.name] === "ok" && (
                                  <button
                                    className="btn btn-sm"
                                    disabled={tamperedVerifications[m.name] === "loading"}
                                    onClick={() => tamperProof(m.name, prf as ProofResult)}
                                  >
                                    {tamperedVerifications[m.name] === "loading" ? "…" : "tamper →"}
                                  </button>
                                )}
                                {tamperedVerifications[m.name] === "fail" && (
                                  <span className="badge badge-proposed">&#10007; tampered — security ✓</span>
                                )}
                              </div>
                              {typeof (prf as ProofResult).index === "number" && typeof (prf as ProofResult).total === "number" && (
                                <ProofTreeSvg
                                  index={(prf as ProofResult).index!}
                                  total={(prf as ProofResult).total!}
                                  depth={(prf as ProofResult).proof.length}
                                  step={replayStep[m.name] ?? null}
                                />
                              )}
                              {codeOpen[m.name] && (
                                <div style={{ marginTop: "0.5rem" }}>
                                  <div style={{ display: "flex", gap: "4px", marginBottom: "4px" }}>
                                    {(["solidity", "python", "js", "rust", "cli"] as const).map((lang) => (
                                      <button
                                        key={lang}
                                        className={`btn btn-sm${(codeTab[m.name] ?? "solidity") === lang ? " btn-active" : ""}`}
                                        onClick={() => setCodeTab((t) => ({ ...t, [m.name]: lang }))}
                                      >
                                        {lang === "solidity" ? "Solidity" : lang === "python" ? "Python" : lang === "js" ? "JS" : lang === "rust" ? "Rust" : "CLI"}
                                      </button>
                                    ))}
                                  </div>
                                  <div className="code" data-lang={codeTab[m.name] ?? "solidity"}>
                                    <pre><code dangerouslySetInnerHTML={{ __html: highlight(activeSnippet(m.name, prf as ProofResult), codeTab[m.name] ?? "solidity") }} /></pre>
                                  </div>
                                  <div style={{ display: "flex", justifyContent: "flex-end", marginTop: "4px" }}>
                                    <button
                                      className="btn btn-sm"
                                      onClick={() => copyCode(m.name, prf as ProofResult)}
                                    >
                                      {copiedCode === m.name ? "copied" : "copy"}
                                    </button>
                                  </div>
                                </div>
                              )}
                            </div>
                          )}
                          {prf === "error" && (
                            <span className="explore-hint">{stale ? "tree is stale — rebuild to include this module" : "proof failed — try rebuild first"}</span>
                          )}
                        </div>
                      );
                    })}
                  </div>
                  </>
                )}
              </div>
            )}
          </section>

          {paper.abstract && (
            <section className="abstract">
              <div className="abstract-k">Abstract</div>
              <div dangerouslySetInnerHTML={{ __html: paper.abstract }} />
            </section>
          )}

          {paper.html ? (
            <div dangerouslySetInnerHTML={{ __html: paper.html }} />
          ) : (
            <p className="text-muted">The paper source could not be loaded.</p>
          )}

          <footer className="foot">
            <span>MOD Protocol · whitepaper module{paper.version ? ` · v${paper.version}` : ""}</span>
            <span className="flex gap-4">
              <a href="#top">back to top</a>
              <a href={`${API}/paper.tex`} target="_blank" rel="noreferrer">
                download .tex
              </a>
            </span>
          </footer>
        </article>
      </div>

      {!registryInView && root?.root && (
        <button
          className="registry-beacon"
          onClick={() => registryRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })}
          aria-label="Jump to live registry"
        >
          <span className={`dot ${watchMode ? "dot-watch" : online ? "dot-on" : "dot-off"}`} />
          {short(root.root)}
          {root.count != null && <span>· {root.count}</span>}
        </button>
      )}
    </>
  );
}
