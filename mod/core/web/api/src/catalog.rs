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
    /// True when the gateway serves this module on the public web — i.e. the
    /// caddy-generated site file routes `/{name}` or `/api/{name}` to it. A
    /// routed module counts even while the activator has it asleep, since it
    /// wakes on access.
    pub on_web: bool,
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
    /// How many modules the gateway serves on the public web.
    pub on_web: usize,
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
    /// The caddy-generated site file that routes `/{mod}` on the public host.
    /// Modules with a route here are "on the web" and rank first.
    caddy_site: PathBuf,
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
        let caddy_site = std::env::var("MOD_CADDY_SITE")
            .map(PathBuf::from)
            .unwrap_or_else(|_| PathBuf::from("/etc/caddy/mod_site.caddy"));
        Self {
            roots,
            caddy_site,
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
        // Stamp which modules the gateway serves publicly and float them to the
        // top: the live web deployment is what visitors can actually use, so it
        // outranks the long tail of on-disk-only modules.
        let routed = web_routed_names(&self.caddy_site);
        for m in modules.iter_mut() {
            m.on_web = routed.contains(&m.name.to_lowercase());
        }
        modules.sort_by(|a, b| b.on_web.cmp(&a.on_web).then_with(|| a.name.cmp(&b.name)));
        *self.snapshot.write() = Some(Snapshot {
            modules: modules.clone(),
            scanned_at: Instant::now(),
        });
        modules
    }

    pub fn get(&self, name: &str) -> Option<Module> {
        self.modules().into_iter().find(|m| m.name == name)
    }

    /// Drop the cached snapshot so the next read rescans the tree. Called after
    /// a module is added on disk so it shows up immediately.
    pub fn invalidate(&self) {
        *self.snapshot.write() = None;
    }

    /// Clone a GitHub repo into the orbit tree as a new module and return its
    /// freshly-scanned record. Owner-gated upstream. The repo is cloned shallow,
    /// its `.git` removed (we don't track sub-histories), and — if it ships no
    /// `config.json` — a minimal one is synthesized so the catalog adopts it.
    pub async fn add_from_github(
        &self,
        repo_input: &str,
        name_override: Option<&str>,
    ) -> Result<Module, AddError> {
        let (url, derived_name) = parse_repo(repo_input).ok_or(AddError::BadRepo)?;
        let name = match name_override {
            Some(n) if !n.trim().is_empty() => sanitize_name(n),
            _ => sanitize_name(&derived_name),
        };
        if name.is_empty() {
            return Err(AddError::BadRepo);
        }

        // Always land new modules in the primary (orbit) root.
        let target = self.roots[0].join(&name);
        if target.exists() {
            return Err(AddError::Exists(name));
        }

        // Clone into a temp sibling first, then move into place atomically-ish,
        // so a failed/partial clone never leaves a half-module in the catalog.
        let tmp = self.roots[0].join(format!(".import-{name}"));
        let _ = std::fs::remove_dir_all(&tmp);

        let mut cmd = tokio::process::Command::new("git");
        cmd.args([
            "clone",
            "--depth",
            "1",
            "--no-tags",
            "--single-branch",
            &url,
        ])
        .arg(&tmp)
        .env("GIT_TERMINAL_PROMPT", "0")
        .env("GIT_ASKPASS", "true")
        .kill_on_drop(true);

        let run = tokio::time::timeout(Duration::from_secs(120), cmd.output()).await;
        let output = match run {
            Ok(Ok(o)) => o,
            Ok(Err(e)) => {
                let _ = std::fs::remove_dir_all(&tmp);
                return Err(AddError::Clone(format!("could not run git: {e}")));
            }
            Err(_) => {
                let _ = std::fs::remove_dir_all(&tmp);
                return Err(AddError::Clone("clone timed out (120s)".into()));
            }
        };
        if !output.status.success() {
            let _ = std::fs::remove_dir_all(&tmp);
            let stderr = String::from_utf8_lossy(&output.stderr);
            let msg = stderr.lines().last().unwrap_or("clone failed").to_string();
            return Err(AddError::Clone(msg));
        }

        // We don't carry the upstream history; keeps disk light and avoids a
        // nested repo confusing the monorepo's own git status.
        let _ = std::fs::remove_dir_all(tmp.join(".git"));

        // Adopt or synthesize a config.json so the catalog recognizes it.
        let cfg_path = tmp.join("config.json");
        if !cfg_path.exists() {
            let description = readme_summary(&tmp)
                .unwrap_or_else(|| format!("Imported from {url}"));
            let cfg = serde_json::json!({
                "name": name,
                "version": "0.0.0",
                "description": description,
                "source": url,
                "imported": true,
            });
            let pretty = serde_json::to_string_pretty(&cfg).unwrap_or_else(|_| "{}".into());
            if let Err(e) = std::fs::write(&cfg_path, pretty) {
                let _ = std::fs::remove_dir_all(&tmp);
                return Err(AddError::Io(e.to_string()));
            }
        }

        // Move into place.
        if let Err(e) = std::fs::rename(&tmp, &target) {
            let _ = std::fs::remove_dir_all(&tmp);
            return Err(AddError::Io(e.to_string()));
        }

        self.invalidate();
        self.get(&name).ok_or(AddError::Io(
            "imported but could not parse the module config".into(),
        ))
    }

    pub fn stats(&self) -> Stats {
        let modules = self.modules();
        Stats {
            functions: modules.iter().map(|m| m.fn_count).sum(),
            rust_apis: modules.iter().filter(|m| m.has_rust).count(),
            apps: modules.iter().filter(|m| m.has_app).count(),
            on_web: modules.iter().filter(|m| m.on_web).count(),
            modules: modules.len(),
        }
    }

    pub fn search(&self, q: &str) -> Vec<Module> {
        let q = q.trim().to_lowercase();
        if q.is_empty() {
            return self.modules();
        }
        // Keyword search: every whitespace-separated term must appear somewhere
        // in the module (name/description/functions/deps) — "store files" then
        // matches a module described as "file storage", instead of requiring
        // the literal phrase. A term also matches on its singular stem
        // ("files" → "file" hits "filecoin"). Name hits rank above body-only.
        let terms: Vec<&str> = q.split_whitespace().collect();
        fn term_hits(hay: &str, t: &str) -> bool {
            hay.contains(t)
                || (t.len() > 3 && t.ends_with('s') && hay.contains(&t[..t.len() - 1]))
        }
        let scored: Vec<(u32, u32, Module)> = self
            .modules()
            .into_iter()
            .filter_map(|m| {
                let name = m.name.to_lowercase();
                let hay = format!(
                    "{} {} {} {}",
                    name,
                    m.description.to_lowercase(),
                    m.fns.join(" ").to_lowercase(),
                    m.deps.join(" ").to_lowercase()
                );
                let matched = terms.iter().filter(|t| term_hits(&hay, t)).count() as u32;
                if matched == 0 {
                    return None;
                }
                let name_hits = terms.iter().filter(|t| term_hits(&name, t)).count() as u32;
                Some((matched, name_hits, m))
            })
            .collect();
        // Prefer modules matching every term; when none do, degrade to partial
        // matches so "trade crypto" still surfaces trading modules.
        let full = terms.len() as u32;
        let any_full = scored.iter().any(|s| s.0 == full);
        let mut hits: Vec<_> = scored
            .into_iter()
            .filter(|s| !any_full || s.0 == full)
            .collect();
        hits.sort_by(|a, b| {
            b.0.cmp(&a.0)
                .then_with(|| b.1.cmp(&a.1))
                .then_with(|| b.2.on_web.cmp(&a.2.on_web))
                .then_with(|| a.2.name.cmp(&b.2.name))
        });
        hits.into_iter().map(|(_, _, m)| m).collect()
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

/// Why importing a module from GitHub failed — mapped to HTTP codes by routes.
#[derive(Debug, Clone)]
pub enum AddError {
    /// Couldn't parse the input into a clonable GitHub repo.
    BadRepo,
    /// A module by that name already exists in the tree.
    Exists(String),
    /// `git clone` itself failed (bad URL, private repo, network).
    Clone(String),
    /// Filesystem error writing the module into place.
    Io(String),
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

/// Parse the caddy-generated site file into the set of module names it routes.
///
/// `m caddy/apply` emits one matcher per public route, e.g.
/// `@venice_app path /venice /venice/*` and `@venice_api path /api/venice …`,
/// so every `path` token reduces to a module name after stripping the optional
/// `api/` prefix and glob suffix. Missing/unreadable file (dev machines without
/// a caddy deployment) yields an empty set — every module then sorts by name as
/// before.
fn web_routed_names(site_file: &Path) -> std::collections::HashSet<String> {
    let mut names = std::collections::HashSet::new();
    let Ok(text) = std::fs::read_to_string(site_file) else {
        return names;
    };
    for line in text.lines() {
        let mut parts = line.split_whitespace();
        if !parts.next().is_some_and(|t| t.starts_with('@')) {
            continue;
        }
        if parts.next() != Some("path") {
            continue;
        }
        for tok in parts {
            let p = tok
                .trim_end_matches("/*")
                .trim_matches('/');
            let name = p.strip_prefix("api/").unwrap_or(p);
            if !name.is_empty() && !name.contains('/') {
                names.insert(name.to_lowercase());
            }
        }
    }
    names
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

/// Turn user input into a `(clone_url, derived_name)` pair. Accepts:
///   - `owner/repo`
///   - `https://github.com/owner/repo[.git]`
///   - `git@github.com:owner/repo[.git]`
///   - any `https://…/…/repo[.git]` git host
/// Returns `None` if it can't find a sensible repo name.
fn parse_repo(input: &str) -> Option<(String, String)> {
    let s = input.trim();
    if s.is_empty() {
        return None;
    }

    // git@host:owner/repo(.git)
    if let Some(rest) = s.strip_prefix("git@") {
        if let Some((_host, path)) = rest.split_once(':') {
            let name = repo_name_from_path(path)?;
            return Some((s.to_string(), name));
        }
    }

    // Full URL.
    if s.starts_with("http://") || s.starts_with("https://") {
        let name = repo_name_from_path(s)?;
        return Some((s.to_string(), name));
    }

    // Shorthand owner/repo → GitHub.
    let parts: Vec<&str> = s.split('/').filter(|p| !p.is_empty()).collect();
    if parts.len() == 2 {
        let owner = parts[0];
        let repo = parts[1].trim_end_matches(".git");
        if is_path_segment(owner) && is_path_segment(repo) {
            return Some((
                format!("https://github.com/{owner}/{repo}.git"),
                repo.to_string(),
            ));
        }
    }

    None
}

fn repo_name_from_path(path: &str) -> Option<String> {
    let last = path
        .trim_end_matches('/')
        .rsplit('/')
        .next()?
        .trim_end_matches(".git");
    if last.is_empty() {
        None
    } else {
        Some(last.to_string())
    }
}

fn is_path_segment(s: &str) -> bool {
    !s.is_empty()
        && s.chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.'))
}

/// Reduce a name to a safe, lowercase module directory name.
fn sanitize_name(s: &str) -> String {
    s.trim()
        .to_lowercase()
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '-' || c == '_' {
                c
            } else {
                '-'
            }
        })
        .collect::<String>()
        .trim_matches('-')
        .to_string()
}

/// First meaningful line of a repo's README, used as a fallback description.
fn readme_summary(dir: &Path) -> Option<String> {
    for cand in ["README.md", "README", "readme.md", "Readme.md"] {
        if let Ok(text) = std::fs::read_to_string(dir.join(cand)) {
            for line in text.lines() {
                let l = line.trim_start_matches('#').trim();
                if !l.is_empty() {
                    return Some(l.chars().take(200).collect());
                }
            }
        }
    }
    None
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
        // Stamped from the caddy site file after the scan, once per snapshot.
        on_web: false,
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
