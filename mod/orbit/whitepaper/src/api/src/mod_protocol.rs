// Interface between the Rust API and the MOD protocol.
//
// At runtime this binary:
//   1. discovers its module directory (the parent of `src/api/`),
//   2. discovers the surrounding mod root (`{mod_root}/orbit/`, `{mod_root}/core/`),
//   3. reads any module's `config.json` (with `{"data": {...}}` unwrap),
//   4. lists every orbit and core module on disk,
//   5. forwards HTTP calls to any module that exposes a port,
//   6. shells out to the `m` CLI when only the Python surface knows how.

use anyhow::{anyhow, Context, Result};
use serde::Serialize;
use serde_json::Value;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Duration;

const ANCHOR_NAMES: &[&str] = &["agent.py", "mod.py", "block.py"];

#[derive(Serialize, Clone)]
pub struct ModuleEntry {
    pub name: String,
    pub scope: &'static str, // "orbit" | "core"
    pub path: String,
    pub has_config: bool,
    pub anchor: Option<String>,
    pub port: Option<u16>,
    pub app_port: Option<u16>,
}

pub struct ModProtocol {
    pub module_dir: PathBuf, // this module: /…/mod/orbit/whitepaper
    #[allow(dead_code)]
    pub mod_root: PathBuf,   //              /…/mod/mod
    pub orbit_dir: PathBuf,  //              /…/mod/mod/orbit
    pub core_dir: PathBuf,   //              /…/mod/mod/core
    pub config: Value,
    pub state_dir: PathBuf,
    http: reqwest::blocking::Client,
}

impl ModProtocol {
    pub fn discover() -> Result<Self> {
        let exe = std::env::current_exe()?;
        let mut module_dir = exe
            .parent()
            .ok_or_else(|| anyhow!("no parent dir"))?
            .to_path_buf();

        if let Ok(env_dir) = std::env::var("WHITEPAPER_MODULE_DIR") {
            module_dir = PathBuf::from(env_dir);
        } else {
            loop {
                if module_dir.join("config.json").exists()
                    && module_dir.join("whitepaper.tex").exists()
                {
                    break;
                }
                module_dir = module_dir
                    .parent()
                    .ok_or_else(|| anyhow!("no config.json found in any parent"))?
                    .to_path_buf();
            }
        }

        // mod_root = parent of `orbit` directory. From mod/orbit/whitepaper/
        // we walk up to the `orbit` segment and take its parent.
        let mod_root = module_dir
            .ancestors()
            .find(|p| p.join("orbit").is_dir() && p.join("core").is_dir())
            .ok_or_else(|| anyhow!("could not locate mod root containing orbit/ + core/"))?
            .to_path_buf();
        let orbit_dir = mod_root.join("orbit");
        let core_dir = mod_root.join("core");

        let config: Value =
            serde_json::from_str(&std::fs::read_to_string(module_dir.join("config.json"))?)
                .context("parsing config.json")?;

        let state_dir = home_dir()?.join(".mod").join("whitepaper");
        std::fs::create_dir_all(&state_dir)?;

        let http = reqwest::blocking::Client::builder()
            .timeout(Duration::from_secs(10))
            .build()?;

        Ok(Self {
            module_dir,
            mod_root,
            orbit_dir,
            core_dir,
            config,
            state_dir,
            http,
        })
    }

    pub fn tex_path(&self) -> PathBuf {
        self.module_dir.join("whitepaper.tex")
    }

    pub fn tree_file(&self) -> PathBuf {
        self.state_dir.join("tree.json")
    }

    pub fn config_u16(&self, key: &str) -> Option<u16> {
        self.config_field(key)
            .and_then(|v| v.as_u64())
            .and_then(|n| u16::try_from(n).ok())
    }

    pub fn config_field(&self, key: &str) -> Option<&Value> {
        if let Some(v) = self.config.get(key) {
            return Some(v);
        }
        self.config.get("data").and_then(|d| d.get(key))
    }

    /// List every module under orbit/ and/or core/. Scope = "orbit", "core", or "all".
    pub fn list_modules(&self, scope: &str) -> Vec<ModuleEntry> {
        let mut out = Vec::new();
        if matches!(scope, "orbit" | "all" | "") {
            out.extend(self.scan_dir(&self.orbit_dir, "orbit"));
        }
        if matches!(scope, "core" | "all") {
            out.extend(self.scan_dir(&self.core_dir, "core"));
        }
        out.sort_by(|a, b| a.name.cmp(&b.name));
        out
    }

    fn scan_dir(&self, root: &Path, scope: &'static str) -> Vec<ModuleEntry> {
        if !root.exists() {
            return Vec::new();
        }
        let mut out = Vec::new();
        for entry in walkdir::WalkDir::new(root)
            .max_depth(1)
            .min_depth(1)
            .into_iter()
            .filter_map(|e| e.ok())
        {
            let p = entry.path();
            if !p.is_dir() {
                continue;
            }
            let name = match p.file_name().and_then(|n| n.to_str()) {
                Some(n) if !n.starts_with('_') && !n.starts_with('.') => n.to_string(),
                _ => continue,
            };
            let anchor = ANCHOR_NAMES
                .iter()
                .find(|a| p.join(a).exists())
                .map(|a| a.to_string())
                .or_else(|| {
                    let cand = p.join(format!("{name}.py"));
                    cand.exists().then(|| format!("{name}.py"))
                });
            let has_config = p.join("config.json").exists()
                || p.join("config.yaml").exists()
                || p.join("config.yml").exists();
            if anchor.is_none() && !has_config {
                continue;
            }
            let cfg = self.module_config_for_path(p).unwrap_or(Value::Null);
            let port = cfg.get("port").and_then(|v| v.as_u64()).and_then(|n| u16::try_from(n).ok());
            let app_port = cfg
                .get("app_port")
                .and_then(|v| v.as_u64())
                .and_then(|n| u16::try_from(n).ok());
            out.push(ModuleEntry {
                name,
                scope,
                path: p.display().to_string(),
                has_config,
                anchor,
                port,
                app_port,
            });
        }
        out
    }

    pub fn module_dir_of(&self, name: &str) -> Result<PathBuf> {
        for parent in [&self.orbit_dir, &self.core_dir] {
            let p = parent.join(name);
            if p.is_dir() {
                return Ok(p);
            }
        }
        Err(anyhow!("module `{name}` not found in orbit or core"))
    }

    /// Read a module's config and apply the `{"data": {...}, "encrypted": ...}` unwrap
    /// that core/mod.py::config() performs.
    pub fn module_config(&self, name: &str) -> Result<Value> {
        let dir = self.module_dir_of(name)?;
        self.module_config_for_path(&dir)
            .ok_or_else(|| anyhow!("no config for `{name}`"))
    }

    fn module_config_for_path(&self, dir: &Path) -> Option<Value> {
        for fname in ["config.json", "mod.json", "block.json", "agent.json"] {
            let p = dir.join(fname);
            if p.exists() {
                if let Ok(text) = std::fs::read_to_string(&p) {
                    if let Ok(mut v) = serde_json::from_str::<Value>(&text) {
                        if let Some(obj) = v.as_object_mut() {
                            if obj.contains_key("encrypted") && obj.contains_key("data") {
                                return obj.remove("data");
                            }
                        }
                        return Some(v);
                    }
                }
            }
        }
        None
    }

    /// Compose a canonical module record suitable for Merkle leaf computation.
    pub fn module_record(&self, name: &str) -> Result<Value> {
        let dir = self.module_dir_of(name)?;
        let cfg = self.module_config_for_path(&dir).unwrap_or(Value::Null);
        Ok(serde_json::json!({
            "name": name,
            "scope": if dir.starts_with(&self.orbit_dir) { "orbit" } else { "core" },
            "path": dir.display().to_string(),
            "port": cfg.get("port"),
            "app_port": cfg.get("app_port"),
            "schema": cfg.get("schema"),
        }))
    }

    /// Build a canonical record set for every operational module — the input to the tree.
    pub fn merkle_records(&self, scope: &str) -> Vec<Value> {
        self.list_modules(scope)
            .into_iter()
            .filter_map(|m| self.module_record(&m.name).ok())
            .collect()
    }

    /// HTTP-forward a request to a module's API server.
    pub fn http_call(
        &self,
        name: &str,
        method: &str,
        path: &str,
        body: Option<Value>,
    ) -> Result<Value> {
        let cfg = self.module_config(name)?;
        let port = cfg
            .get("port")
            .and_then(|v| v.as_u64())
            .ok_or_else(|| anyhow!("module `{name}` has no `port` in config"))?;
        let url = format!(
            "http://localhost:{port}{}",
            if path.starts_with('/') { path.to_string() } else { format!("/{path}") }
        );
        let req = match method.to_ascii_uppercase().as_str() {
            "GET" => self.http.get(&url),
            "POST" => self.http.post(&url),
            "PUT" => self.http.put(&url),
            "DELETE" => self.http.delete(&url),
            other => return Err(anyhow!("unsupported method `{other}`")),
        };
        let req = if let Some(b) = body { req.json(&b) } else { req };
        let resp = req.send().with_context(|| format!("calling {url}"))?;
        let status = resp.status();
        let text = resp.text().unwrap_or_default();
        if !status.is_success() {
            return Err(anyhow!("HTTP {status} from {url}: {text}"));
        }
        Ok(serde_json::from_str(&text).unwrap_or(Value::String(text)))
    }

    /// Subprocess bridge: `m <fn> [k=v ...]`. Used when only the Python surface knows the call.
    pub fn call(&self, func: &str, args: Value) -> Result<Value> {
        let mut cmd = Command::new("m");
        cmd.arg(func);
        if let Value::Object(map) = args {
            for (k, v) in map {
                let arg = match v {
                    Value::String(s) => format!("{k}={s}"),
                    other => format!("{k}={other}"),
                };
                cmd.arg(arg);
            }
        }
        let out = cmd.output().context("spawning `m`")?;
        if !out.status.success() {
            return Err(anyhow!(
                "m {} failed: {}",
                func,
                String::from_utf8_lossy(&out.stderr)
            ));
        }
        let stdout = String::from_utf8_lossy(&out.stdout).trim().to_string();
        Ok(serde_json::from_str(&stdout).unwrap_or(Value::String(stdout)))
    }
}

fn home_dir() -> Result<PathBuf> {
    std::env::var("HOME")
        .map(PathBuf::from)
        .map_err(|_| anyhow!("HOME not set"))
}
