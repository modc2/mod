//! strats.rs — the unified strategy desk.
//!
//! One board for every trading strategy on the fleet, across every venue:
//! the strats shipped inside the polymarket, hyperliquid and copytensor
//! (bittensor) modules, plus the builtins and orbit strat mods — all on the
//! ONE canonical Strat protocol the orbit/strat module owns.
//!
//! This file is a CLIENT of that module, the same way dex.rs is a client of
//! the chain-owning modules: orbit/strat is pure python with no server, so
//! each call shells out to its `mod.py <fn> key=value` CLI (the solc
//! precedent) and caches the answer. Nothing here can place an order —
//! only the read/pure-data fns are exposed (list, sources, board, backtest,
//! plan). plan() returns the exact config a venue module's OWN live engine
//! would consume; starting it stays with that module and its gates
//! (polymarket autoExecute false, copytensor human-gated writes, hyperliquid
//! token-gated live engine).

use std::collections::HashMap;
use std::path::PathBuf;
use std::time::{Duration, Instant};
use tokio::sync::RwLock;

pub struct Strats {
    dir: PathBuf,
    cache: RwLock<HashMap<String, (Instant, serde_json::Value)>>,
}

impl Strats {
    pub fn from_env(module_dir: &std::path::Path) -> Self {
        let dir = std::env::var("DEFI_STRAT_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|_| {
                module_dir
                    .parent()
                    .map(|p| p.join("strat"))
                    .unwrap_or_else(|| PathBuf::from("strat"))
            });
        Strats { dir, cache: RwLock::new(HashMap::new()) }
    }

    pub fn available(&self) -> bool {
        self.dir.join("mod.py").is_file()
    }

    /// Run one strat fn through the module's CLI. `token`, when present,
    /// rides the child environment (STRAT_CLI_TOKEN), never argv.
    pub async fn run(
        &self,
        fn_name: &str,
        args: &[(&str, serde_json::Value)],
        token: Option<&str>,
        timeout_s: u64,
    ) -> Result<serde_json::Value, String> {
        let modpy = self.dir.join("mod.py");
        if !modpy.is_file() {
            return Err(format!(
                "the strat module is not installed at {} — set DEFI_STRAT_DIR",
                self.dir.display()
            ));
        }
        let mut cmd = tokio::process::Command::new("python3");
        cmd.arg(&modpy).arg(fn_name).current_dir(&self.dir);
        for (k, v) in args {
            if v.is_null() {
                continue;
            }
            let raw = match v {
                serde_json::Value::String(s) => s.clone(),
                other => other.to_string(),
            };
            cmd.arg(format!("{k}={raw}"));
        }
        if let Some(t) = token {
            cmd.env("STRAT_CLI_TOKEN", t);
        }
        cmd.stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .stdin(std::process::Stdio::null())
            .kill_on_drop(true);
        let child = cmd.spawn().map_err(|e| format!("spawn python3: {e}"))?;
        let out = tokio::time::timeout(
            Duration::from_secs(timeout_s),
            child.wait_with_output(),
        )
        .await
        .map_err(|_| format!("strat {fn_name} timed out after {timeout_s}s"))?
        .map_err(|e| format!("strat {fn_name}: {e}"))?;
        if !out.status.success() {
            let tail: String = String::from_utf8_lossy(&out.stderr)
                .lines()
                .rev()
                .take(4)
                .collect::<Vec<_>>()
                .into_iter()
                .rev()
                .collect::<Vec<_>>()
                .join(" | ");
            return Err(format!("strat {fn_name} failed: {tail}"));
        }
        let value: serde_json::Value = serde_json::from_slice(&out.stdout)
            .map_err(|e| format!("strat {fn_name}: bad JSON from mod.py ({e})"))?;
        if let Some(msg) = value.get("error").and_then(|v| v.as_str()) {
            return Err(msg.to_string());
        }
        Ok(value)
    }

    /// run() behind a TTL cache. Only for token-free reads — a cached row
    /// must never leak one caller's gated view to the next.
    pub async fn cached(
        &self,
        key: &str,
        ttl_s: u64,
        fn_name: &str,
        args: &[(&str, serde_json::Value)],
        timeout_s: u64,
    ) -> Result<serde_json::Value, String> {
        {
            let cache = self.cache.read().await;
            if let Some((at, value)) = cache.get(key) {
                if at.elapsed() < Duration::from_secs(ttl_s) {
                    return Ok(value.clone());
                }
            }
        }
        let value = self.run(fn_name, args, None, timeout_s).await?;
        self.cache
            .write()
            .await
            .insert(key.to_string(), (Instant::now(), value.clone()));
        Ok(value)
    }

    pub async fn evict(&self, key: &str) {
        self.cache.write().await.remove(key);
    }
}
