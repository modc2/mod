//! Live catalog of the mod ecosystem.
//!
//! The mod protocol is a monorepo of modules — each one a directory under
//! `mod/orbit/<name>/` carrying a `config.json`. This module turns that tree
//! into a queryable in-memory catalog: it walks the orbit directory, parses
//! every `config.json`, and projects each into a uniform [`Module`] record.
//!
//! The scan is cheap (read a few dozen small JSON files) and cached behind a
//! short TTL so the API stays live as modules are added or edited on disk
//! without re-reading the tree on every request.

use parking_lot::RwLock;
use serde::Serialize;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

/// A single module, projected from its `config.json` into a uniform shape the
/// frontend can render without knowing each module's bespoke fields.
#[derive(Debug, Clone, Serialize)]
pub struct Module {
    pub name: String,
    pub description: String,
    pub version: String,
    /// Single-glyph icon if the module declares one (e.g. "V" for venice).
    pub icon: Option<String>,
    /// Accent color (hex) if declared.
    pub color: Option<String>,
    /// API port, if the module declares one.
    pub port: Option<u64>,
    /// Frontend port, if the module declares one.
    pub app_port: Option<u64>,
    /// Functions the module exposes over the mod protocol.
    pub fns: Vec<String>,
    /// Number of exposed functions (cheap sort/sizing key for the UI).
    pub fn_count: usize,
    /// Other modules this one depends on (from the config `deps` array). Backs
    /// the dependency-link graph in the explorer.
    pub deps: Vec<String>,
    /// True if the module ships a Rust API (heuristic: has a Cargo.toml).
    pub has_rust: bool,
    /// True if the module ships a Next/Node app (heuristic: has app + package.json).
    pub has_app: bool,
    /// Public mount path under the gateway, e.g. "/venice".
    pub mount: String,
    /// On-chain content id (schema/manifest CID) if pinned.
    pub schema: Option<String>,
    /// Raw config.json, passed through for the detail view.
    pub config: serde_json::Value,
}

/// Aggregate stats over the whole catalog — the numbers on the landing hero.
#[derive(Debug, Clone, Serialize)]
pub struct Stats {
    pub modules: usize,
    pub functions: usize,
    pub rust_apis: usize,
    pub apps: usize,
}

/// Cached snapshot of the catalog plus the moment it was scanned.
struct Snapshot {
    modules: Vec<Module>,
    scanned_at: Instant,
}

/// Thread-safe, TTL-cached view over the module tree. Scans every root —
/// `mod/orbit` plus the sibling `mod/core` (chain, store, web, …) — so the
/// explorer surfaces the whole ecosystem, core modules included.
pub struct Catalog {
    roots: Vec<PathBuf>,
    ttl: Duration,
    snapshot: RwLock<Option<Snapshot>>,
}

impl Catalog {
    pub fn new(orbit_dir: PathBuf) -> Self {
        let mut roots = vec![orbit_dir.clone()];
        // Core modules (web, chain, store, app) live in a sibling `core/` dir.
        if let Some(core) = orbit_dir.parent().map(|p| p.join("core")) {
            if core.is_dir() {
                roots.push(core);
            }
        }
        Self {
            roots,
            ttl: Duration::from_secs(3),
            snapshot: RwLock::new(None),
        }
    }

    /// The primary (orbit) root — reported by the health probe.
    pub fn orbit_dir(&self) -> &Path {
        &self.roots[0]
    }

    /// Return the catalog, rescanning every root if the cache is cold or stale.
    pub fn modules(&self) -> Vec<Module> {
        if let Some(snap) = self.snapshot.read().as_ref() {
            if snap.scanned_at.elapsed() < self.ttl {
                return snap.modules.clone();
            }
        }
        let mut modules = Vec::new();
        for root in &self.roots {
            scan_into(root, &mut modules);
        }
        // Order by name, then by completeness (richest first) so that when a
        // name appears in two roots — e.g. a bare stub in orbit alongside the
        // real module in core — dedup keeps the fully-described one.
        modules.sort_by(|a, b| {
            a.name
                .cmp(&b.name)
                .then(completeness(b).cmp(&completeness(a)))
        });
        modules.dedup_by(|a, b| a.name == b.name);
        *self.snapshot.write() = Some(Snapshot {
            modules: modules.clone(),
            scanned_at: Instant::now(),
        });
        modules
    }

    pub fn get(&self, name: &str) -> Option<Module> {
        self.modules().into_iter().find(|m| m.name == name)
    }

    pub fn stats(&self) -> Stats {
        let modules = self.modules();
        Stats {
            functions: modules.iter().map(|m| m.fn_count).sum(),
            rust_apis: modules.iter().filter(|m| m.has_rust).count(),
            apps: modules.iter().filter(|m| m.has_app).count(),
            modules: modules.len(),
        }
    }

    pub fn search(&self, q: &str) -> Vec<Module> {
        let q = q.trim().to_lowercase();
        if q.is_empty() {
            return self.modules();
        }
        self.modules()
            .into_iter()
            .filter(|m| {
                m.name.to_lowercase().contains(&q)
                    || m.description.to_lowercase().contains(&q)
                    || m.fns.iter().any(|f| f.to_lowercase().contains(&q))
            })
            .collect()
    }

    /// Resolve a module name to its directory on disk. Fast path is
    /// `orbit/<name>`; falls back to scanning for a module whose declared
    /// `name` differs from its directory. Returns `None` if no such module.
    fn dir_for(&self, name: &str) -> Option<PathBuf> {
        for root in &self.roots {
            let direct = root.join(name);
            if direct.join("config.json").exists() {
                return Some(direct);
            }
        }
        for root in &self.roots {
            let Ok(entries) = std::fs::read_dir(root) else {
                continue;
            };
            for entry in entries.flatten() {
                let dir = entry.path();
                let cfg = dir.join("config.json");
                if !cfg.exists() {
                    continue;
                }
                if let Some(m) = parse_module(&dir, &cfg) {
                    if m.name == name {
                        return Some(dir);
                    }
                }
            }
        }
        None
    }

    /// Recursive source tree for a module — dirs first, then files, build
    /// output and vendored deps elided. `None` if the module doesn't exist.
    pub fn tree(&self, name: &str) -> Option<Vec<TreeNode>> {
        let dir = self.dir_for(name)?;
        Some(build_tree(&dir, &dir, 0))
    }

    /// Read one source file inside a module, sandboxed to the module dir.
    pub fn read_file(&self, name: &str, rel: &str) -> Result<FileContent, FileError> {
        let dir = self.dir_for(name).ok_or(FileError::NotFound)?;
        read_file_sandboxed(&dir, rel)
    }
}

/// Directory/file names we never surface in the code explorer: VCS metadata,
/// build output, vendored deps. Keeps the tree small and the module's own
/// source front-and-center.
const TREE_SKIP: &[&str] = &[
    ".git",
    "node_modules",
    "target",
    "__pycache__",
    ".next",
    "dist",
    "build",
    ".turbo",
    ".vercel",
    ".DS_Store",
    "venv",
    ".venv",
    ".mypy_cache",
    ".pytest_cache",
];

/// Hard ceilings so a pathological module can't blow up the explorer.
const MAX_TREE_DEPTH: usize = 8;
const MAX_FILE_BYTES: u64 = 512 * 1024;

/// One node in a module's source tree. `path` is relative to the module dir.
#[derive(Debug, Clone, Serialize)]
pub struct TreeNode {
    pub name: String,
    pub path: String,
    #[serde(rename = "type")]
    pub kind: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub children: Option<Vec<TreeNode>>,
}

/// A single file's contents, ready to render in the code viewer.
#[derive(Debug, Clone, Serialize)]
pub struct FileContent {
    pub path: String,
    pub content: String,
    pub lines: usize,
    pub bytes: u64,
    pub truncated: bool,
}

/// Why a file read was refused — mapped to HTTP status codes by the routes.
#[derive(Debug, Clone, Copy)]
pub enum FileError {
    NotFound,
    Forbidden,
    TooLarge,
    Binary,
    Io,
}

/// Recursively walk `dir`, yielding paths relative to `root`. Dirs come before
/// files at each level, both alphabetical, with the skip-list elided.
fn build_tree(root: &Path, dir: &Path, depth: usize) -> Vec<TreeNode> {
    if depth >= MAX_TREE_DEPTH {
        return Vec::new();
    }
    let mut nodes: Vec<TreeNode> = Vec::new();
    let entries = match std::fs::read_dir(dir) {
        Ok(e) => e,
        Err(_) => return nodes,
    };
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().to_string();
        if name.starts_with('.') || TREE_SKIP.contains(&name.as_str()) {
            continue;
        }
        let p = entry.path();
        let rel = p
            .strip_prefix(root)
            .map(|r| r.to_string_lossy().to_string())
            .unwrap_or_else(|_| name.clone());
        if p.is_dir() {
            nodes.push(TreeNode {
                name,
                path: rel,
                kind: "dir",
                children: Some(build_tree(root, &p, depth + 1)),
            });
        } else {
            nodes.push(TreeNode {
                name,
                path: rel,
                kind: "file",
                children: None,
            });
        }
    }
    nodes.sort_by(|a, b| match (a.kind, b.kind) {
        ("dir", "file") => std::cmp::Ordering::Less,
        ("file", "dir") => std::cmp::Ordering::Greater,
        _ => a.name.to_lowercase().cmp(&b.name.to_lowercase()),
    });
    nodes
}

/// Read `rel` under `module_dir`, refusing anything that canonicalizes outside
/// the module (path traversal, symlink escape), is too large, or isn't UTF-8.
fn read_file_sandboxed(module_dir: &Path, rel: &str) -> Result<FileContent, FileError> {
    let base = std::fs::canonicalize(module_dir).map_err(|_| FileError::NotFound)?;
    let real = std::fs::canonicalize(base.join(rel)).map_err(|_| FileError::NotFound)?;
    if !real.starts_with(&base) {
        return Err(FileError::Forbidden);
    }
    let meta = std::fs::metadata(&real).map_err(|_| FileError::Io)?;
    if !meta.is_file() {
        return Err(FileError::NotFound);
    }
    if meta.len() > MAX_FILE_BYTES {
        return Err(FileError::TooLarge);
    }
    let raw = std::fs::read(&real).map_err(|_| FileError::Io)?;
    let content = String::from_utf8(raw).map_err(|_| FileError::Binary)?;
    let lines = content.lines().count();
    Ok(FileContent {
        path: rel.to_string(),
        bytes: meta.len(),
        lines,
        truncated: false,
        content,
    })
}

/// A rough "how fully described is this module" score, used to break ties when
/// the same name appears in more than one root. A bare auto-generated stub
/// (just a name) scores low; a real module with a description, functions, ports
/// and deps scores high, so dedup keeps the real one.
fn completeness(m: &Module) -> i32 {
    let mut s = 0;
    if !m.description.is_empty() {
        s += 2;
    }
    if m.version != "0.0.0" {
        s += 1;
    }
    s += m.fn_count as i32;
    s += m.deps.len() as i32;
    if m.port.is_some() {
        s += 1;
    }
    if m.app_port.is_some() {
        s += 1;
    }
    if m.icon.is_some() {
        s += 1;
    }
    s
}

/// Walk one root and append a [`Module`] for every `<name>/config.json`.
fn scan_into(root: &Path, modules: &mut Vec<Module>) {
    let entries = match std::fs::read_dir(root) {
        Ok(e) => e,
        Err(e) => {
            tracing::warn!("cannot read module root {}: {e}", root.display());
            return;
        }
    };

    for entry in entries.flatten() {
        let dir = entry.path();
        if !dir.is_dir() {
            continue;
        }
        let cfg_path = dir.join("config.json");
        if !cfg_path.exists() {
            continue;
        }
        if let Some(module) = parse_module(&dir, &cfg_path) {
            modules.push(module);
        }
    }
}

fn parse_module(dir: &Path, cfg_path: &Path) -> Option<Module> {
    let raw = std::fs::read_to_string(cfg_path).ok()?;
    let config: serde_json::Value = serde_json::from_str(&raw).ok()?;
    let obj = config.as_object()?;

    let dir_name = dir.file_name()?.to_string_lossy().to_string();
    let name = obj
        .get("name")
        .and_then(|v| v.as_str())
        .unwrap_or(&dir_name)
        .to_string();

    let description = obj
        .get("description")
        .or_else(|| obj.get("desc"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    let version = obj
        .get("version")
        .and_then(|v| v.as_str())
        .unwrap_or("0.0.0")
        .to_string();

    let icon = obj
        .get("icon")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string());
    let color = obj
        .get("color")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string());
    let port = obj.get("port").and_then(|v| v.as_u64());
    let app_port = obj.get("app_port").and_then(|v| v.as_u64());
    let schema = obj
        .get("schema")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string());

    let fns: Vec<String> = obj
        .get("fns")
        .or_else(|| obj.get("expose"))
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|x| x.as_str().map(|s| s.to_string()))
                .collect()
        })
        .unwrap_or_default();
    let fn_count = fns.len();

    let deps: Vec<String> = obj
        .get("deps")
        .or_else(|| obj.get("dependencies"))
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|x| x.as_str().map(|s| s.to_string()))
                .collect()
        })
        .unwrap_or_default();

    let has_rust = dir.join("Cargo.toml").exists()
        || dir.join("src/api/Cargo.toml").exists()
        || description.to_lowercase().contains("rust");
    let has_app = dir.join("app").is_dir()
        || dir.join("src/app").is_dir()
        || dir.join("package.json").exists()
        || app_port.is_some();

    let mount = format!("/{name}");

    Some(Module {
        name,
        description,
        version,
        icon,
        color,
        port,
        app_port,
        fns,
        fn_count,
        deps,
        has_rust,
        has_app,
        mount,
        schema,
        config,
    })
}
