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

/// Thread-safe, TTL-cached view over the orbit tree.
pub struct Catalog {
    orbit_dir: PathBuf,
    ttl: Duration,
    snapshot: RwLock<Option<Snapshot>>,
}

impl Catalog {
    pub fn new(orbit_dir: PathBuf) -> Self {
        Self {
            orbit_dir,
            ttl: Duration::from_secs(3),
            snapshot: RwLock::new(None),
        }
    }

    pub fn orbit_dir(&self) -> &Path {
        &self.orbit_dir
    }

    /// Return the catalog, rescanning the orbit tree if the cache is cold or stale.
    pub fn modules(&self) -> Vec<Module> {
        if let Some(snap) = self.snapshot.read().as_ref() {
            if snap.scanned_at.elapsed() < self.ttl {
                return snap.modules.clone();
            }
        }
        let modules = scan(&self.orbit_dir);
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
}

/// Walk the orbit directory and build a [`Module`] for every `<name>/config.json`.
fn scan(orbit_dir: &Path) -> Vec<Module> {
    let mut modules = Vec::new();
    let entries = match std::fs::read_dir(orbit_dir) {
        Ok(e) => e,
        Err(e) => {
            tracing::warn!("cannot read orbit dir {}: {e}", orbit_dir.display());
            return modules;
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

    // Stable, friendly ordering: by name.
    modules.sort_by(|a, b| a.name.cmp(&b.name));
    modules
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
        has_rust,
        has_app,
        mount,
        schema,
        config,
    })
}
