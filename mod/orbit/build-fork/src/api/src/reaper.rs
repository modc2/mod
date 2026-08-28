//! Reaper — the console's window onto the fleet's scale-to-zero activator.
//!
//! The activator (core/server/activator, :9000) is the component that actually
//! wakes and sleeps modules; its control plane is bound to localhost only.
//! These endpoints bridge it into the build console so the person can SET the
//! idle timeout ("turn off apps unused for more than a minute") and pin/sleep/
//! wake modules from the UI:
//!
//!   GET  /reaper          — activator state + system memory headroom (public,
//!                           like /autosnap/status)
//!   GET  /providers       — the PROVIDERS panel: per-module live resource usage
//!                           (from the /system attribution pass) merged with the
//!                           activator's management state (public)
//!   POST /reaper/config   — {idle_seconds, min_free_mb, max_running} — each
//!                           optional; N sets, null resets to the env default
//!                           (owner-only)
//!   POST /reaper/control  — {module, action: pin|unpin|disable|enable|sleep|wake}
//!                           (owner-only)

use axum::http::StatusCode;
use axum::response::IntoResponse;
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};

const ACTIVATOR: &str = "http://127.0.0.1:9000/_activator";

fn client() -> reqwest::Client {
    reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(8))
        .build()
        .unwrap_or_default()
}

async fn activator_state() -> Result<Value, String> {
    let resp = client()
        .get(format!("{ACTIVATOR}/state"))
        .send()
        .await
        .map_err(|e| format!("activator unreachable: {e}"))?;
    resp.json().await.map_err(|e| format!("activator bad state: {e}"))
}

async fn activator_control(body: Value) -> Result<Value, String> {
    let resp = client()
        .post(format!("{ACTIVATOR}/control"))
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("activator unreachable: {e}"))?;
    let status = resp.status();
    let out: Value = resp
        .json()
        .await
        .map_err(|e| format!("activator bad reply: {e}"))?;
    if !status.is_success() {
        return Err(out
            .get("error")
            .and_then(|e| e.as_str())
            .unwrap_or("activator control failed")
            .to_string());
    }
    Ok(out)
}

/// MemTotal/MemAvailable from /proc/meminfo, in MB. Zeros on non-Linux.
fn meminfo_mb() -> (u64, u64) {
    let raw = std::fs::read_to_string("/proc/meminfo").unwrap_or_default();
    let grab = |key: &str| {
        raw.lines()
            .find(|l| l.starts_with(key))
            .and_then(|l| l.split_whitespace().nth(1))
            .and_then(|v| v.parse::<u64>().ok())
            .unwrap_or(0)
            / 1024
    };
    (grab("MemTotal:"), grab("MemAvailable:"))
}

/// Top pm2 processes by resident memory — the "who is eating the box" view.
fn top_pm2_mem(limit: usize) -> Vec<Value> {
    let Some(pm2) = crate::process::find_pm2() else { return Vec::new() };
    let Ok(out) = std::process::Command::new(pm2).arg("jlist").output() else {
        return Vec::new();
    };
    let Ok(list) = serde_json::from_slice::<Vec<Value>>(&out.stdout) else {
        return Vec::new();
    };
    let mut procs: Vec<(String, u64, String)> = list
        .iter()
        .filter_map(|p| {
            let name = p.get("name")?.as_str()?.to_string();
            let mem = p.pointer("/monit/memory")?.as_u64()?;
            let status = p
                .pointer("/pm2_env/status")
                .and_then(|s| s.as_str())
                .unwrap_or("unknown")
                .to_string();
            Some((name, mem, status))
        })
        .collect();
    procs.sort_by(|a, b| b.1.cmp(&a.1));
    procs
        .into_iter()
        .take(limit)
        .map(|(name, mem, status)| {
            json!({ "name": name, "mem_mb": mem / (1024 * 1024), "status": status })
        })
        .collect()
}

/// GET /reaper — public: activator state + memory headroom in one view.
pub async fn status_handler() -> impl IntoResponse {
    let (total_mb, available_mb) = meminfo_mb();
    let memory = json!({
        "total_mb": total_mb,
        "available_mb": available_mb,
        "used_mb": total_mb.saturating_sub(available_mb),
        "top": top_pm2_mem(5),
    });
    match activator_state().await {
        Ok(state) => Json(json!({ "activator": true, "state": state, "memory": memory })),
        Err(e) => Json(json!({ "activator": false, "error": e, "memory": memory })),
    }
}

/// POST /reaper/config — owner-only: idle timeout + OOM-guard knobs. Only keys
/// present in the request are forwarded, so setting one never resets another
/// (the activator applies the same only-touch-what's-sent rule; null resets a
/// knob to its env default).
pub async fn config_handler(
    headers: axum::http::HeaderMap,
    Json(req): Json<Value>,
) -> impl IntoResponse {
    if let Err(e) = crate::api::require_owner_or_local(&headers) {
        return e.into_response();
    }
    let mut body = json!({ "action": "set" });
    for (snake, camel) in [
        ("idle_seconds", "idleSeconds"),
        ("min_free_mb", "minFreeMb"),
        ("max_running", "maxRunning"),
    ] {
        if let Some(v) = req.get(snake) {
            body[camel] = v.clone();
        }
    }
    match activator_control(body).await {
        Ok(out) => (StatusCode::OK, Json(out)).into_response(),
        Err(e) => (StatusCode::BAD_GATEWAY, Json(json!({ "error": e }))).into_response(),
    }
}

#[derive(Deserialize)]
pub struct ControlRequest {
    pub module: String,
    pub action: String,
}

/// POST /reaper/control — owner-only pin/unpin/disable/enable/sleep/wake.
pub async fn control_handler(
    headers: axum::http::HeaderMap,
    Json(req): Json<ControlRequest>,
) -> impl IntoResponse {
    if let Err(e) = crate::api::require_owner_or_local(&headers) {
        return e.into_response();
    }
    const ACTIONS: [&str; 6] = ["pin", "unpin", "disable", "enable", "sleep", "wake"];
    if !ACTIONS.contains(&req.action.as_str()) {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": format!("action must be one of: {}", ACTIONS.join("|")) })),
        )
            .into_response();
    }
    match activator_control(json!({ "module": req.module, "action": req.action })).await {
        Ok(out) => (StatusCode::OK, Json(out)).into_response(),
        Err(e) => (StatusCode::BAD_GATEWAY, Json(json!({ "error": e }))).into_response(),
    }
}

/// One pm2-registered module: its category, whether anything is online, and
/// the last time pm2 started it (epoch seconds — `pm_uptime` survives a stop,
/// so a down module still knows when it was last up).
#[derive(Default)]
struct Pm2Module {
    category: String,
    online: bool,
    last_start: u64,
}

/// pm2's view of the fleet, keyed by module. Attribution mirrors the /system
/// rule: a proc belongs to the module whose orbit/core dir contains its cwd,
/// exec path, or a cmdline arg.
fn pm2_modules() -> std::collections::BTreeMap<String, Pm2Module> {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let anchor = format!("{}/mod/mod", home);
    let mut found: std::collections::BTreeMap<String, Pm2Module> = Default::default();
    let Some(pm2) = crate::process::find_pm2() else { return found };
    let Ok(out) = std::process::Command::new(pm2).arg("jlist").output() else {
        return found;
    };
    let Ok(list) = serde_json::from_slice::<Vec<Value>>(&out.stdout) else {
        return found;
    };
    for p in &list {
        let env = p.get("pm2_env");
        let mut hit: Option<(String, String)> = None;
        for key in ["pm_cwd", "pm_exec_path"] {
            if hit.is_none() {
                if let Some(s) = env.and_then(|e| e.get(key)).and_then(|v| v.as_str()) {
                    hit = crate::system::module_of(s, &anchor);
                }
            }
        }
        if hit.is_none() {
            for a in env
                .and_then(|e| e.get("args"))
                .and_then(|v| v.as_array())
                .into_iter()
                .flatten()
            {
                if let Some(h) = a.as_str().and_then(|s| crate::system::module_of(s, &anchor)) {
                    hit = Some(h);
                    break;
                }
            }
        }
        let Some((name, category)) = hit else { continue };
        let status = env.and_then(|e| e.get("status")).and_then(|s| s.as_str()).unwrap_or("");
        let started = env
            .and_then(|e| e.get("pm_uptime"))
            .and_then(|v| v.as_u64())
            .unwrap_or(0)
            / 1000; // pm2 records milliseconds
        let row = found.entry(name).or_default();
        if row.category.is_empty() {
            row.category = category;
        }
        row.online |= status == "online";
        row.last_start = row.last_start.max(started);
    }
    found
}

/// Modules on disk that /modules/:name/process can actually launch: the dir
/// root has an ecosystem.config.js or start.sh (the two recipes `bootstrap`
/// knows) plus a config.json marking it a real module. These have never been
/// started — no pids, no pm2 entry, no activator row — so without this scan
/// they'd be invisible to the panel and could never be started from it.
fn installed_modules() -> Vec<(String, String)> {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let mut found: Vec<(String, String)> = Vec::new();
    for category in ["orbit", "core"] {
        let root = format!("{home}/mod/mod/{category}");
        let Ok(entries) = std::fs::read_dir(&root) else { continue };
        for entry in entries.flatten() {
            let path = entry.path();
            if !path.is_dir() {
                continue;
            }
            let launchable =
                path.join("ecosystem.config.js").is_file() || path.join("start.sh").is_file();
            let is_module =
                path.join("config.json").is_file() || path.join("src/config.json").is_file();
            if launchable && is_module {
                if let Some(name) = path.file_name().and_then(|n| n.to_str()) {
                    found.push((name.to_string(), category.to_string()));
                }
            }
        }
    }
    found.sort();
    found
}

/// Lowercased module name → registry CID from ~/.mod/api/registry.json.
/// Same file + both shapes ({owner: {mod: cid}}, optionally under "data")
/// that /modules reads — the registry is authoritative over local config.
fn registry_cids() -> std::collections::HashMap<String, String> {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let raw = std::fs::read_to_string(format!("{home}/.mod/api/registry.json")).unwrap_or_default();
    let Ok(data) = serde_json::from_str::<Value>(&raw) else {
        return Default::default();
    };
    let map = data
        .get("data")
        .and_then(|d| d.as_object())
        .or_else(|| data.as_object());
    let mut out = std::collections::HashMap::new();
    if let Some(map) = map {
        for owner_mods in map.values() {
            if let Some(mods) = owner_mods.as_object() {
                for (name, cid) in mods {
                    if let Some(c) = cid.as_str() {
                        out.entry(name.to_lowercase()).or_insert_with(|| c.to_string());
                    }
                }
            }
        }
    }
    out
}

/// GET /providers — public: one row per app provider (module). Live modules
/// come from the /system attribution pass (real RSS/CPU, /proc-walked);
/// activator-managed modules that are asleep still get a row (zero usage) so
/// the panel shows the whole fleet, not just what's currently burning memory.
/// Every row carries the module's registry CID (null if never registered).
pub async fn providers_handler() -> impl IntoResponse {
    let sys = crate::system::snapshot().await;
    let act = activator_state().await;

    // module name → activator management row
    let mut managed: std::collections::HashMap<String, Value> = std::collections::HashMap::new();
    if let Ok(state) = &act {
        for m in state.get("modules").and_then(|v| v.as_array()).into_iter().flatten() {
            if let Some(name) = m.get("module").and_then(|n| n.as_str()) {
                managed.insert(name.to_string(), m.clone());
            }
        }
    }

    let mut providers: Vec<Value> = Vec::new();
    for app in sys.get("apps").and_then(|v| v.as_array()).into_iter().flatten() {
        let mut row = app.clone();
        let name = row.get("module").and_then(|n| n.as_str()).unwrap_or_default().to_string();
        row["activator"] = managed.remove(&name).unwrap_or(Value::Null);
        row["sleeping"] = json!(false);
        providers.push(row);
    }
    // Managed but not in the live process list → sleeping (scale-to-zero'd).
    let mut asleep: Vec<(String, Value)> = managed.into_iter().collect();
    asleep.sort_by(|a, b| a.0.cmp(&b.0));
    for (name, info) in asleep {
        providers.push(json!({
            "module": name,
            "category": Value::Null,
            "mem_mb": 0,
            "cpu_pct": 0.0,
            "procs": [],
            "activator": info,
            "sleeping": true,
        }));
    }
    // pm2-registered but neither live nor activator-managed → killed. Still a
    // row, so the panel can offer RUN (pm2 keeps the stopped proc's launch
    // recipe; `process start` restarts it in place).
    let present: std::collections::HashSet<String> = providers
        .iter()
        .filter_map(|p| p.get("module").and_then(|n| n.as_str()).map(str::to_string))
        .collect();
    let pm2 = pm2_modules();
    for (name, row) in pm2.iter().filter(|(_, r)| !r.online) {
        if present.contains(name) {
            continue;
        }
        providers.push(json!({
            "module": name,
            "category": row.category,
            "mem_mb": 0,
            "cpu_pct": 0.0,
            "procs": [],
            "activator": Value::Null,
            "sleeping": false,
            "stopped": true,
        }));
    }
    // Installed but never started → "off". Anything with a launch recipe on
    // disk gets a row, so the panel can START any app that exists — the
    // process endpoint bootstraps it from ecosystem.config.js / start.sh.
    let present: std::collections::HashSet<String> = providers
        .iter()
        .filter_map(|p| p.get("module").and_then(|n| n.as_str()).map(str::to_string))
        .collect();
    for (name, category) in installed_modules() {
        if present.contains(&name) {
            continue;
        }
        providers.push(json!({
            "module": name,
            "category": category,
            "mem_mb": 0,
            "cpu_pct": 0.0,
            "procs": [],
            "activator": Value::Null,
            "sleeping": false,
            "stopped": true,
            "installed": true,
        }));
    }

    let cids = registry_cids();
    for row in providers.iter_mut() {
        let name = row.get("module").and_then(|n| n.as_str()).unwrap_or_default().to_string();
        row["cid"] = cids.get(&name.to_lowercase()).map(|c| json!(c)).unwrap_or(Value::Null);
        // When an app is down there is no live process to read an uptime from,
        // so fall back to pm2's record of the last time it was started — "last
        // up 3h ago" beats a blank cell for deciding what to bring back.
        if row.get("started_at").and_then(|v| v.as_u64()).is_none() {
            let last = pm2.get(&name).map(|r| r.last_start).filter(|t| *t > 0);
            row["last_up"] = last.map(|t| json!(t)).unwrap_or(Value::Null);
        }
    }

    let (activator_up, guard, idle_seconds) = match &act {
        Ok(state) => (
            true,
            state.get("guard").cloned().unwrap_or(Value::Null),
            state.get("idleSeconds").cloned().unwrap_or(Value::Null),
        ),
        Err(_) => (false, Value::Null, Value::Null),
    };
    Json(json!({
        "host": sys.get("host").cloned().unwrap_or(Value::Null),
        "activator": activator_up,
        "guard": guard,
        "idle_seconds": idle_seconds,
        "providers": providers,
    }))
}
