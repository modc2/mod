//! agent_base — Rust counterpart of agent_base.py / agent_base.js.
//!
//! Implements the same canonical contract shape (manifest, abi, code_hash,
//! state, events, call dispatch). A concrete Rust agent provides:
//!   1) A `Contract` impl declaring NAME/BINARY/etc + an `abi()` and `call()`
//!   2) A `main()` that delegates to `agent_base::run_cli::<MyContract>()`
//!
//! Same CLI protocol as the JS base — the Python dispatcher in dev's FastAPI
//! shells out to `./contract manifest|abi|call <method> <jsonArgs>`.

use serde::{Deserialize, Serialize};
use sha3::{Digest, Sha3_256};
use std::env;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

// ── Per-user × per-tool workspaces ───────────────────────────────────
// Same layout as agent_base.py: ~/.mod/workspaces/<addr>/<tool>/workspace/
// Each coding-tool module (claude, codex, cursor, …) resolves user-facing
// paths through `resolve_user_path` so users never share filesystem state.
// Override the root via MOD_WORKSPACES_ROOT (e.g. a container bind-mount).

pub fn workspaces_root() -> PathBuf {
    if let Ok(p) = env::var("MOD_WORKSPACES_ROOT") {
        return PathBuf::from(p);
    }
    let home = env::var("HOME").unwrap_or_else(|_| ".".into());
    PathBuf::from(home).join(".mod").join("workspaces")
}

fn normalize_addr(addr: &str) -> Result<String, String> {
    let a = addr.trim().to_ascii_lowercase();
    if !(a.starts_with("0x")
        && a.len() == 42
        && a[2..].chars().all(|c| c.is_ascii_hexdigit()))
    {
        return Err(format!("invalid address: {addr}"));
    }
    Ok(a)
}

pub fn user_root(addr: &str, tool: &str) -> Result<PathBuf, String> {
    Ok(workspaces_root().join(normalize_addr(addr)?).join(tool))
}

pub fn user_workspace(addr: &str, tool: &str) -> Result<PathBuf, String> {
    Ok(user_root(addr, tool)?.join("workspace"))
}

pub fn ensure_user_dirs(addr: &str, tool: &str) -> Result<PathBuf, String> {
    let ws = user_workspace(addr, tool)?;
    fs::create_dir_all(&ws).map_err(|e| format!("mkdir {}: {e}", ws.display()))?;
    Ok(ws)
}

/// Join + canonicalize + assert containment under the user's workspace.
/// Rejects `..` traversal and symlinks pointing outside the sandbox.
pub fn resolve_user_path(addr: &str, tool: &str, rel: &str) -> Result<PathBuf, String> {
    let ws = ensure_user_dirs(addr, tool)?;
    let ws_canon = fs::canonicalize(&ws)
        .map_err(|e| format!("canonicalize workspace {}: {e}", ws.display()))?;
    let rel_clean = rel.trim_start_matches('/');
    let candidate = ws.join(rel_clean);
    // Try full canonicalize; if leaf missing (write target), canonicalize parent.
    let resolved = match fs::canonicalize(&candidate) {
        Ok(p) => p,
        Err(_) => {
            let parent = candidate
                .parent()
                .ok_or_else(|| "candidate has no parent".to_string())?;
            let parent_canon = fs::canonicalize(parent).map_err(|e| {
                format!("canonicalize parent {}: {e}", parent.display())
            })?;
            let leaf = candidate
                .file_name()
                .ok_or_else(|| "candidate has no leaf".to_string())?;
            parent_canon.join(leaf)
        }
    };
    if !resolved.starts_with(&ws_canon) {
        return Err(format!(
            "path escapes workspace: {} not under {}",
            resolved.display(),
            ws_canon.display()
        ));
    }
    Ok(resolved)
}

/// Per-user-per-tool jobs DB path. The caller is responsible for opening +
/// running migrations — this just returns where the file should live.
pub fn user_jobs_db(addr: &str, tool: &str) -> Result<PathBuf, String> {
    let root = user_root(addr, tool)?;
    fs::create_dir_all(&root).map_err(|e| format!("mkdir {}: {e}", root.display()))?;
    Ok(root.join("jobs.db"))
}

#[allow(dead_code)]
fn _path_helper_doc(_: &Path) {} // suppress unused-import warning for `Path`

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct AbiEntry {
    pub name: String,
    pub kind: String,            // "view" | "tx"
    pub owner_only: bool,
    #[serde(default)]
    pub inputs: Vec<AbiInput>,
    pub doc: String,
}

#[derive(Debug, Serialize, Deserialize, Clone, Default)]
pub struct AbiInput {
    pub name: String,
    #[serde(rename = "type")]
    pub ty: String,
}

#[derive(Debug, Serialize)]
pub struct Manifest {
    pub name: &'static str,
    pub lang: &'static str,
    pub icon: &'static str,
    pub color: &'static str,
    pub binary: &'static str,
    pub default_model: &'static str,
    pub env_key: &'static str,
    pub description: &'static str,
    pub code_hash: String,
    pub abi: Vec<AbiEntry>,
}

pub trait Contract {
    const NAME: &'static str;
    const ICON: &'static str = "";
    const COLOR: &'static str = "#888888";
    const BINARY: &'static str = "";
    const DEFAULT_MODEL: &'static str = "";
    const ENV_KEY: &'static str = "";
    const DESCRIPTION: &'static str = "agent (override DESCRIPTION)";

    fn abi() -> Vec<AbiEntry>;
    fn call(method: &str, args: serde_json::Value) -> serde_json::Value;
}

pub fn code_hash() -> String {
    // sha3-256 over the running binary's own bytes — same idea as
    // hashing the Python class source.
    let path = env::current_exe().unwrap_or_default();
    let bytes = fs::read(&path).unwrap_or_default();
    let mut h = Sha3_256::new();
    h.update(&bytes);
    format!("0x{}", hex::encode(h.finalize()))
}

pub fn manifest<C: Contract>() -> Manifest {
    Manifest {
        name: C::NAME,
        lang: "rust",
        icon: C::ICON,
        color: C::COLOR,
        binary: C::BINARY,
        default_model: C::DEFAULT_MODEL,
        env_key: C::ENV_KEY,
        description: C::DESCRIPTION,
        code_hash: code_hash(),
        abi: C::abi(),
    }
}

pub fn state_path<C: Contract>() -> PathBuf {
    let home = env::var("HOME").unwrap_or_else(|_| ".".into());
    let dir = PathBuf::from(home).join(".mod").join(C::NAME);
    let _ = fs::create_dir_all(&dir);
    dir.join("state.json")
}

pub fn events_path<C: Contract>() -> PathBuf {
    let home = env::var("HOME").unwrap_or_else(|_| ".".into());
    let dir = PathBuf::from(home).join(".mod").join(C::NAME);
    let _ = fs::create_dir_all(&dir);
    dir.join("events.jsonl")
}

pub fn load_state<C: Contract>() -> serde_json::Value {
    let p = state_path::<C>();
    fs::read_to_string(&p)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_else(|| serde_json::json!({ "created_at": now_ts(), "jobs_submitted": 0, "events": 0 }))
}

pub fn save_state<C: Contract>(state: &serde_json::Value) {
    let _ = fs::write(state_path::<C>(), serde_json::to_string_pretty(state).unwrap_or_default());
}

pub fn emit<C: Contract>(event: &str, fields: serde_json::Value) {
    let evt = serde_json::json!({ "event": event, "ts": now_ts(), "fields": fields });
    if let Ok(mut f) = fs::OpenOptions::new().create(true).append(true).open(events_path::<C>()) {
        let _ = writeln!(f, "{}", evt);
    }
}

fn now_ts() -> i64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0)
}

/// CLI dispatcher — same protocol as the JS base.
///   $0 manifest
///   $0 abi
///   $0 call <method> <jsonArgs>
pub fn run_cli<C: Contract>() {
    let argv: Vec<String> = env::args().collect();
    let cmd = argv.get(1).cloned().unwrap_or_default();
    match cmd.as_str() {
        "manifest" => println!("{}", serde_json::to_string(&manifest::<C>()).unwrap()),
        "abi" => println!("{}", serde_json::to_string(&C::abi()).unwrap()),
        "call" => {
            let method = argv.get(2).cloned().unwrap_or_default();
            let args = argv.get(3)
                .and_then(|s| serde_json::from_str(s).ok())
                .unwrap_or(serde_json::json!({}));
            let result = C::call(&method, args);
            println!("{}", serde_json::to_string(&result).unwrap());
        }
        _ => {
            eprintln!("usage: {} {{manifest|abi|call <method> <jsonArgs>}}", argv.get(0).cloned().unwrap_or_default());
            std::process::exit(2);
        }
    }
}
