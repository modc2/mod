//! Pluggable per-module process backends.
//!
//! The build API can drive any OTHER module's lifecycle (status/stop/start/
//! restart). HOW a module's processes are supervised varies — most run under
//! pm2, some could be systemd units, some are just bare processes on a port.
//! This module hides that behind one `Backend` so the HTTP API + SDK behave
//! identically regardless of supervisor.
//!
//! Backend selection (per target module, since env/runtime is per-module):
//!   1. `MOD_PM` env var (global override): pm2 | systemd | generic
//!   2. the target module's `config.json` "process_manager" field
//!   3. "auto" — pm2 if it has procs for the module, else a loaded systemd
//!      unit, else generic (ports + start.sh).
//!
//! Opt-in nix: any launcher this module runs itself (generic start, or the
//! start.sh bootstrap fallback) is wrapped in `nix develop -c` / `nix-shell
//! --run` when the module ships a flake.nix / shell.nix and `nix` is installed.
//! pm2/systemd units carry their own env, so they are left untouched.

use serde_json::{json, Value};
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::process::Command;

/// A single supervised process, normalized across backends.
#[derive(Clone)]
pub struct Proc {
    pub name: String,
    /// Backend handle used to act on it: pm2 pm_id, OS pid, or systemd unit.
    pub id: String,
    pub pid: Option<i64>,
    pub status: String,
    /// cwd / unit path — informational, surfaced to the caller.
    pub detail: String,
}

impl Proc {
    pub fn to_json(&self) -> Value {
        json!({
            "name": self.name,
            "id": self.id,
            "pid": self.pid,
            "status": self.status,
            "detail": self.detail,
        })
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Backend {
    Pm2,
    Systemd,
    Generic,
}

impl Backend {
    pub fn as_str(&self) -> &'static str {
        match self {
            Backend::Pm2 => "pm2",
            Backend::Systemd => "systemd",
            Backend::Generic => "generic",
        }
    }
}

fn parse_backend(s: &str) -> Option<Backend> {
    match s.trim().to_lowercase().as_str() {
        "pm2" => Some(Backend::Pm2),
        "systemd" | "systemctl" => Some(Backend::Systemd),
        "generic" | "bare" | "port" | "ports" => Some(Backend::Generic),
        _ => None,
    }
}

/// Resolve which backend supervises this module.
pub fn select(module_dir: &Path, config: &Value, module_name: &str) -> Backend {
    // 1. Global override.
    if let Ok(v) = std::env::var("MOD_PM") {
        if let Some(b) = parse_backend(&v) {
            return b;
        }
    }
    // 2. Per-module declaration.
    if let Some(b) = config
        .get("process_manager")
        .and_then(|v| v.as_str())
        .and_then(parse_backend)
    {
        return b;
    }
    // 3. Auto-detect.
    if find_pm2().is_some() {
        if let Ok(procs) = pm2_list(module_dir) {
            if !procs.is_empty() {
                return Backend::Pm2;
            }
        }
    }
    if systemd_available() && !systemd_units(module_name).is_empty() {
        return Backend::Systemd;
    }
    Backend::Generic
}

/// List the module's processes under the chosen backend.
pub fn list(backend: Backend, module_dir: &Path, module_name: &str, config: &Value) -> Result<Vec<Proc>, String> {
    match backend {
        Backend::Pm2 => pm2_list(module_dir),
        Backend::Systemd => Ok(systemd_list(module_name)),
        Backend::Generic => Ok(generic_list(module_name, config)),
    }
}

/// Recent log output for a module's processes, keyed by process name (which
/// contains "api"/"app" so the UI can route each into the right tab). Each value
/// is the tail of that process's stdout, with any non-empty stderr tail prepended
/// — surfacing crash/restart causes, which is the whole point of the LOGS view.
pub fn logs(
    backend: Backend,
    module_dir: &Path,
    module_name: &str,
    lines: usize,
) -> Result<serde_json::Map<String, Value>, String> {
    match backend {
        Backend::Pm2 => pm2_logs(module_dir, lines),
        Backend::Systemd => Ok(systemd_logs(module_name, lines)),
        // Bare processes have no supervisor capturing output to a known file.
        Backend::Generic => Ok(serde_json::Map::new()),
    }
}

/// Read the last `lines` lines of a file, bounded to the trailing ~512 KB so a
/// huge log never blows memory. Missing/unreadable files yield an empty string.
fn tail_file(path: &str, lines: usize) -> String {
    use std::io::{Read, Seek, SeekFrom};
    if path.is_empty() {
        return String::new();
    }
    let p = Path::new(path);
    let len = match std::fs::metadata(p) {
        Ok(m) => m.len(),
        Err(_) => return String::new(),
    };
    let mut f = match std::fs::File::open(p) {
        Ok(f) => f,
        Err(_) => return String::new(),
    };
    let cap: u64 = 512 * 1024;
    let start = len.saturating_sub(cap);
    if start > 0 {
        let _ = f.seek(SeekFrom::Start(start));
    }
    let mut buf = Vec::new();
    if f.read_to_end(&mut buf).is_err() {
        return String::new();
    }
    let s = String::from_utf8_lossy(&buf);
    let all: Vec<&str> = s.lines().collect();
    let take = all.len().min(lines);
    all[all.len() - take..].join("\n")
}

/// pm2 records each process's stdout/stderr file paths in `pm2 jlist`. Resolve the
/// module's procs (same matching as `pm2_list`) and tail their log files.
fn pm2_logs(module_dir: &Path, lines: usize) -> Result<serde_json::Map<String, Value>, String> {
    let pm2 = match find_pm2() {
        Some(p) => p,
        None => return Err("pm2 not found on host".to_string()),
    };
    let out = Command::new(&pm2)
        .arg("jlist")
        .output()
        .map_err(|e| format!("pm2 jlist failed: {}", e))?;
    let list: Value =
        serde_json::from_slice(&out.stdout).map_err(|e| format!("pm2 jlist parse failed: {}", e))?;
    let canon = std::fs::canonicalize(module_dir).unwrap_or_else(|_| module_dir.to_path_buf());
    let canon_s = canon.to_string_lossy().to_string();
    let prefix = format!("{}/", canon_s);
    let inside = |s: &str| !s.is_empty() && (s == canon_s || s.starts_with(&prefix));

    let mut map = serde_json::Map::new();
    if let Some(arr) = list.as_array() {
        for p in arr {
            let env = p.get("pm2_env");
            let cwd = env.and_then(|e| e.get("pm_cwd")).and_then(|v| v.as_str()).unwrap_or("");
            let exec = env.and_then(|e| e.get("pm_exec_path")).and_then(|v| v.as_str()).unwrap_or("");
            let arg_hit = env
                .and_then(|e| e.get("args"))
                .and_then(|v| v.as_array())
                .map(|args| args.iter().filter_map(|a| a.as_str()).any(&inside))
                .unwrap_or(false);
            if !(inside(cwd) || inside(exec) || arg_hit) {
                continue;
            }
            let name = p.get("name").and_then(|v| v.as_str()).unwrap_or("proc").to_string();
            let out_path = env.and_then(|e| e.get("pm_out_log_path")).and_then(|v| v.as_str()).unwrap_or("");
            let err_path = env.and_then(|e| e.get("pm_err_log_path")).and_then(|v| v.as_str()).unwrap_or("");
            let restarts = env.and_then(|e| e.get("restart_time")).and_then(|v| v.as_i64()).unwrap_or(0);
            let status = env.and_then(|e| e.get("status")).and_then(|v| v.as_str()).unwrap_or("unknown");

            let err_tail = tail_file(err_path, lines);
            let out_tail = tail_file(out_path, lines);
            let mut text = format!("══ pm2: {} · {} · {} restart(s) ══\n", name, status, restarts);
            if !err_tail.trim().is_empty() {
                text.push_str("\n── stderr ──\n");
                text.push_str(&err_tail);
                text.push('\n');
            }
            text.push_str("\n── stdout ──\n");
            if out_tail.trim().is_empty() {
                text.push_str("(no stdout captured yet)");
            } else {
                text.push_str(&out_tail);
            }
            map.insert(name, Value::String(text));
        }
    }
    Ok(map)
}

/// systemd journal tail per candidate unit, keyed by unit name.
fn systemd_logs(module_name: &str, lines: usize) -> serde_json::Map<String, Value> {
    let mut map = serde_json::Map::new();
    for unit in systemd_units(module_name) {
        let out = Command::new("journalctl")
            .args(["-u", &unit, "-n", &lines.to_string(), "--no-pager", "-o", "short"])
            .output();
        if let Ok(o) = out {
            let mut s = String::from_utf8_lossy(&o.stdout).to_string();
            let err = String::from_utf8_lossy(&o.stderr);
            if !err.trim().is_empty() {
                s.push_str(&err);
            }
            map.insert(unit, Value::String(s));
        }
    }
    map
}

/// Perform `action` (stop|start|restart) on the given procs. Returns (ok, log).
pub fn act(
    backend: Backend,
    action: &str,
    procs: &[Proc],
    module_dir: &Path,
    module_name: &str,
    config: &Value,
) -> (bool, String) {
    // Smart prod rebuild. Build restarts a module after editing it so the edit
    // goes live. But a module whose app runs a *production* Next build
    // (`next start`) re-serves the stale `.next` bundle on a bare restart — the
    // edit never appears. So when (re)starting such a module, rebuild the app
    // first. A dev-mode app (`next dev`) is left untouched: it hot-reloads
    // source on its own, so a plain restart already picks up the edit.
    let mut prelog: Option<String> = None;
    if matches!(action, "restart" | "start") {
        if let Some(app_dir) = prod_next_app_dir(procs) {
            let (built, blog) = build_app(module_dir, &app_dir);
            if !built {
                // Don't restart into a broken build — leave the old server up.
                return (
                    false,
                    format!(
                        "✗ prod build failed — keeping the running server (no restart)\n{}",
                        blog.trim()
                    ),
                );
            }
            prelog = Some(format!("✓ rebuilt prod app before restart · {}", app_dir.display()));
        }
    }

    let (ok, out) = match backend {
        Backend::Pm2 => pm2_act(action, procs, module_dir, module_name, config),
        Backend::Systemd => systemd_act(action, procs, module_name),
        Backend::Generic => generic_act(action, procs, module_dir, module_name, config),
    };
    match prelog {
        Some(b) => (ok, format!("{}\n{}", b, out)),
        None => (ok, out),
    }
}

/// Quote a path for safe interpolation into a bash one-liner.
fn sh_quote(p: &Path) -> String {
    format!("'{}'", p.to_string_lossy().replace('\'', "'\\''"))
}

/// If the module's running APP process is a *production* Next build
/// (`next start`), return its working directory (where `npm run build` runs).
/// Returns None for a dev-mode app (`next dev`, hot-reloads on its own), a
/// non-Next server, or when nothing is running — in all those cases a plain
/// restart already suffices and no rebuild is needed.
fn prod_next_app_dir(procs: &[Proc]) -> Option<PathBuf> {
    for p in procs {
        let pid = match p.pid {
            Some(n) if n > 0 => n,
            _ => continue,
        };
        let cmd = proc_cmdline(pid);
        if !cmd.contains("next") {
            continue; // api proc (rust binary / uvicorn) — skip
        }
        let is_dev = cmd.contains("next dev") || cmd.contains(" dev ") || cmd.ends_with(" dev");
        let is_prod =
            cmd.contains("next start") || cmd.contains(" start ") || cmd.ends_with(" start");
        if is_dev || !is_prod {
            return None; // dev (or indeterminate) — restart is enough
        }
        // Prefer the live cwd; fall back to the pm2-reported pm_cwd (Proc.detail).
        if let Some(dir) = proc_link(pid, "cwd") {
            return Some(dir);
        }
        if !p.detail.is_empty() {
            return Some(PathBuf::from(&p.detail));
        }
        return None;
    }
    None
}

/// Build the app, entering the module's nix env when present.
///
/// Prefer the app's own `build.sh` when it ships one. A bare `npm run build`
/// writes straight into `.next` — under a live `next start` that deletes the
/// chunks the running server is still serving, and every open tab 404s its CSS
/// and JS and repaints as raw unstyled HTML. An app that cares stages the build
/// and swaps it in atomically; `build.sh` is where it says so.
fn build_app(module_dir: &Path, app_dir: &Path) -> (bool, String) {
    let cmd = if app_dir.join("build.sh").exists() {
        format!("cd {} && bash build.sh", sh_quote(app_dir))
    } else {
        format!("cd {} && npm run build", sh_quote(app_dir))
    };
    run(launcher(module_dir, &cmd))
}

// ── nix wrapping ─────────────────────────────────────────────────────

fn which(bin: &str) -> Option<String> {
    let out = Command::new("which").arg(bin).output().ok()?;
    let p = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if p.is_empty() { None } else { Some(p) }
}

/// Build a Command that runs `shell_cmd` (a bash one-liner) in `module_dir`,
/// transparently entering the module's nix environment when it ships one.
fn launcher(module_dir: &Path, shell_cmd: &str) -> Command {
    let has_flake = module_dir.join("flake.nix").exists();
    let has_shell = module_dir.join("shell.nix").exists();
    if has_flake && which("nix").is_some() {
        // `nix develop --command bash -lc "<cmd>"` enters the dev shell, runs it.
        let mut c = Command::new("nix");
        c.args(["develop", "--command", "bash", "-lc", shell_cmd]);
        c.current_dir(module_dir);
        c
    } else if has_shell && which("nix-shell").is_some() {
        let mut c = Command::new("nix-shell");
        c.args(["--run", shell_cmd]);
        c.current_dir(module_dir);
        c
    } else {
        let mut c = Command::new("bash");
        c.args(["-lc", shell_cmd]).current_dir(module_dir);
        c
    }
}

/// True if the module declares a nix environment (informational for callers).
pub fn has_nix_env(module_dir: &Path) -> bool {
    module_dir.join("flake.nix").exists() || module_dir.join("shell.nix").exists()
}

/// Bootstrap a module that has no running processes, from its own launcher:
/// prefer ecosystem.config.js (pm2 only), then start.sh, and finally the
/// protocol's own `m serve <name>`. That last step matters — most modules in
/// the fleet ship NO launcher script at all. They are a config.json plus a
/// mod.py, and `m serve` is how they are meant to come up: it resolves the
/// module by name, picks a free port, starts it under pm2 and writes the port
/// and urls back into its config.json. Without it, every bare module was
/// un-startable from this console. nix-wrapped.
fn bootstrap(module_dir: &Path, module_name: &str, prefer_pm2: bool) -> (bool, String) {
    if prefer_pm2 {
        let eco = module_dir.join("ecosystem.config.js");
        if eco.exists() {
            if let Some(pm2) = find_pm2() {
                return pm2_run(&pm2, &["start", &eco.to_string_lossy()]);
            }
        }
    }
    let start_sh = module_dir.join("start.sh");
    if start_sh.exists() {
        return run(launcher(module_dir, "bash start.sh"));
    }
    if let Some(res) = mod_serve(module_dir, module_name) {
        return res;
    }
    (
        false,
        format!(
            "nothing to launch: no ecosystem.config.js, no start.sh, and `m serve {}` is unavailable (the `m` CLI is not on PATH)",
            module_name
        ),
    )
}

/// A module name is safe to interpolate into a shell command only if it is a
/// plain identifier — names arrive from the HTTP path, so anything else is
/// refused rather than quoted.
fn safe_module_name(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 64
        && name
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-'))
}

/// True when this module could be launched by the protocol (`m serve <name>`).
fn can_mod_serve(module_name: &str) -> bool {
    safe_module_name(module_name) && which("m").is_some()
}

/// Start a bare module through the protocol. Returns None when that route is
/// not available at all, so callers can fall through to their own message.
/// Bounded by `timeout` — a wedged serve must not hang this API's request.
fn mod_serve(module_dir: &Path, module_name: &str) -> Option<(bool, String)> {
    if !can_mod_serve(module_name) {
        return None;
    }
    let (ok, out) = run(launcher(
        module_dir,
        &format!("timeout 180 m serve {}", module_name),
    ));
    // `m serve` echoes the whole pm2 process table, ANSI colours and all — the
    // tail is the part that says what happened to THIS module.
    let clean = strip_ansi(out.trim());
    let lines: Vec<&str> = clean.lines().collect();
    let tail = lines[lines.len().saturating_sub(30)..].join("\n");
    Some((ok, format!("m serve {}\n{}", module_name, tail)))
}

/// Drop CSI escape sequences so launcher output reads as plain text in the UI.
fn strip_ansi(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut chars = s.chars().peekable();
    while let Some(c) = chars.next() {
        if c != '\u{1b}' {
            out.push(c);
            continue;
        }
        if chars.peek() == Some(&'[') {
            chars.next();
            while let Some(&c) = chars.peek() {
                chars.next();
                if c.is_ascii_alphabetic() {
                    break;
                }
            }
        }
    }
    out
}

fn run(mut cmd: Command) -> (bool, String) {
    match cmd.output() {
        Ok(o) => (
            o.status.success(),
            format!(
                "{}{}",
                String::from_utf8_lossy(&o.stdout),
                String::from_utf8_lossy(&o.stderr)
            ),
        ),
        Err(e) => (false, format!("launch failed: {}", e)),
    }
}

// ── pm2 backend ──────────────────────────────────────────────────────

pub fn find_pm2() -> Option<String> {
    if let Some(p) = which("pm2") {
        return Some(p);
    }
    for cand in [
        "/usr/local/bin/pm2",
        "/usr/bin/pm2",
        "/root/.npm-global/bin/pm2",
        "/usr/lib/node_modules/pm2/bin/pm2",
        "/usr/local/lib/node_modules/pm2/bin/pm2",
    ] {
        if Path::new(cand).exists() {
            return Some(cand.to_string());
        }
    }
    None
}

fn pm2_run(pm2: &str, args: &[&str]) -> (bool, String) {
    match Command::new(pm2).args(args).output() {
        Ok(out) => {
            let mut s = String::from_utf8_lossy(&out.stdout).to_string();
            let err = String::from_utf8_lossy(&out.stderr);
            if !err.trim().is_empty() {
                s.push_str(&err);
            }
            (out.status.success(), s)
        }
        Err(e) => (false, format!("failed to run pm2: {}", e)),
    }
}

fn pm2_list(module_dir: &Path) -> Result<Vec<Proc>, String> {
    let pm2 = match find_pm2() {
        Some(p) => p,
        None => return Err("pm2 not found on host".to_string()),
    };
    let out = Command::new(&pm2)
        .arg("jlist")
        .output()
        .map_err(|e| format!("pm2 jlist failed: {}", e))?;
    let list: Value =
        serde_json::from_slice(&out.stdout).map_err(|e| format!("pm2 jlist parse failed: {}", e))?;
    let canon = std::fs::canonicalize(module_dir).unwrap_or_else(|_| module_dir.to_path_buf());
    let canon_s = canon.to_string_lossy().to_string();
    let prefix = format!("{}/", canon_s);
    let inside = |s: &str| !s.is_empty() && (s == canon_s || s.starts_with(&prefix));

    let mut procs = Vec::new();
    if let Some(arr) = list.as_array() {
        for p in arr {
            let env = p.get("pm2_env");
            let cwd = env.and_then(|e| e.get("pm_cwd")).and_then(|v| v.as_str()).unwrap_or("");
            let exec = env.and_then(|e| e.get("pm_exec_path")).and_then(|v| v.as_str()).unwrap_or("");
            // Match by working dir, exec path, OR a launch arg landing inside the
            // module dir — python modules run from repo root via `python -m
            // uvicorn … --app-dir <module>/api`, so the path is only in args.
            let arg_hit = env
                .and_then(|e| e.get("args"))
                .and_then(|v| v.as_array())
                .map(|args| args.iter().filter_map(|a| a.as_str()).any(&inside))
                .unwrap_or(false);
            if !(inside(cwd) || inside(exec) || arg_hit) {
                continue;
            }
            procs.push(Proc {
                name: p.get("name").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                id: p.get("pm_id").and_then(|v| v.as_i64()).map(|n| n.to_string()).unwrap_or_default(),
                pid: p.get("pid").and_then(|v| v.as_i64()),
                status: env.and_then(|e| e.get("status")).and_then(|v| v.as_str()).unwrap_or("unknown").to_string(),
                detail: cwd.to_string(),
            });
        }
    }
    Ok(procs)
}

/// Drop a module's pm2 entries entirely rather than just stopping them. A
/// rename moves the tree out from under them: the entries still carry the old
/// name and point at a directory that no longer exists, and `pm2 resurrect`
/// would faithfully raise those ghosts. The module is re-registered under its
/// new name by the ordinary start path (`bootstrap` reads the moved
/// ecosystem.config.js).
pub fn pm2_forget(procs: &[Proc]) -> (bool, String) {
    let Some(pm2) = find_pm2() else {
        return (false, "pm2 not found on host".to_string());
    };
    let ids: Vec<&str> = procs
        .iter()
        .map(|p| p.id.as_str())
        .filter(|s| !s.is_empty())
        .collect();
    if ids.is_empty() {
        return (true, "no pm2 entries to remove".to_string());
    }
    let mut args = vec!["delete"];
    args.extend(ids);
    pm2_run(&pm2, &args)
}

/// Persist the pm2 process list so a host reboot brings back what is actually
/// registered now — called after a rename has replaced entries.
pub fn pm2_save() {
    if let Some(pm2) = find_pm2() {
        let _ = pm2_run(&pm2, &["save"]);
    }
}

fn pm2_act(
    action: &str,
    procs: &[Proc],
    module_dir: &Path,
    module_name: &str,
    _config: &Value,
) -> (bool, String) {
    let pm2 = match find_pm2() {
        Some(p) => p,
        None => return (false, "pm2 not found on host".to_string()),
    };
    let ids: Vec<&str> = procs.iter().map(|p| p.id.as_str()).filter(|s| !s.is_empty()).collect();
    if !ids.is_empty() {
        let verb = match action {
            "stop" => "stop",
            "start" => "start",
            _ => "restart",
        };
        let mut args = vec![verb];
        args.extend(ids);
        return pm2_run(&pm2, &args);
    }
    if action == "stop" {
        return (true, "no pm2 processes registered for this module".to_string());
    }
    bootstrap(module_dir, module_name, true)
}

// ── systemd backend ──────────────────────────────────────────────────

fn systemd_available() -> bool {
    which("systemctl").is_some()
}

/// Candidate unit names for a module, in the order we surface them.
fn systemd_units(module_name: &str) -> Vec<String> {
    let candidates = [
        format!("mod-{}.service", module_name),
        format!("mod-{}-api.service", module_name),
        format!("mod-{}-app.service", module_name),
        format!("{}.service", module_name),
        format!("{}-api.service", module_name),
        format!("{}-app.service", module_name),
    ];
    candidates
        .into_iter()
        .filter(|u| systemd_loaded(u))
        .collect()
}

fn systemd_show(unit: &str, prop: &str) -> String {
    Command::new("systemctl")
        .args(["show", unit, "-p", prop, "--value"])
        .output()
        .ok()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_default()
}

fn systemd_loaded(unit: &str) -> bool {
    systemd_show(unit, "LoadState") == "loaded"
}

fn systemd_list(module_name: &str) -> Vec<Proc> {
    systemd_units(module_name)
        .into_iter()
        .map(|unit| {
            let active = systemd_show(&unit, "ActiveState"); // active|inactive|failed
            let pid = systemd_show(&unit, "MainPID").parse::<i64>().ok().filter(|&n| n > 0);
            let path = systemd_show(&unit, "FragmentPath");
            Proc {
                name: unit.clone(),
                id: unit,
                pid,
                status: if active == "active" { "online".to_string() } else { active },
                detail: path,
            }
        })
        .collect()
}

fn systemd_act(action: &str, procs: &[Proc], module_name: &str) -> (bool, String) {
    let units: Vec<String> = if procs.is_empty() {
        systemd_units(module_name)
    } else {
        procs.iter().map(|p| p.id.clone()).collect()
    };
    if units.is_empty() {
        return (false, format!("no systemd units found for module '{}'", module_name));
    }
    let verb = match action {
        "stop" => "stop",
        "start" => "start",
        _ => "restart",
    };
    let mut args = vec![verb.to_string()];
    args.extend(units);
    let argref: Vec<&str> = args.iter().map(|s| s.as_str()).collect();
    match Command::new("systemctl").args(&argref).output() {
        Ok(o) => (
            o.status.success(),
            format!(
                "systemctl {} → {}{}",
                verb,
                String::from_utf8_lossy(&o.stdout),
                String::from_utf8_lossy(&o.stderr)
            ),
        ),
        Err(e) => (false, format!("systemctl failed: {}", e)),
    }
}

// ── generic backend (ports + start.sh) ───────────────────────────────

/// Pull a port out of config: a direct numeric field, or the port in a URL.
fn port_from(config: &Value, keys: &[&str], url_keys: &[&str]) -> Option<u16> {
    for k in keys {
        if let Some(n) = config.get(*k).and_then(|v| v.as_u64()) {
            return u16::try_from(n).ok();
        }
    }
    let urls = config.get("urls");
    for k in url_keys {
        let candidate = config
            .get(*k)
            .and_then(|v| v.as_str())
            .or_else(|| urls.and_then(|u| u.get(*k)).and_then(|v| v.as_str()));
        if let Some(u) = candidate {
            if let Some(p) = u.rsplit(':').next().and_then(|tail| {
                tail.trim_end_matches('/').split('/').next().and_then(|n| n.parse::<u16>().ok())
            }) {
                return Some(p);
            }
        }
    }
    None
}

/// PIDs of the process(es) *listening* on `port`. Prefer `ss` (reads kernel
/// netlink — sees sockets lsof can miss, and a LISTEN filter avoids catching the
/// far end of an established connection like the Caddy gateway). Falls back to
/// lsof where `ss` is unavailable.
fn pids_on_port(port: u16) -> Vec<i64> {
    let mut pids = ss_listen_pids(port);
    if pids.is_empty() {
        pids = Command::new("lsof")
            .args(["-ti", &format!(":{}", port), "-sTCP:LISTEN"])
            .output()
            .ok()
            .map(|o| {
                String::from_utf8_lossy(&o.stdout)
                    .lines()
                    .filter_map(|l| l.trim().parse::<i64>().ok())
                    .collect()
            })
            .unwrap_or_default();
    }
    pids.sort_unstable();
    pids.dedup();
    pids
}

fn ss_listen_pids(port: u16) -> Vec<i64> {
    // `-ltnpH`: listening, tcp, numeric, with pid, no header.
    let out = match Command::new("ss").args(["-ltnpH"]).output() {
        Ok(o) => o,
        Err(_) => return Vec::new(),
    };
    let suffix = format!(":{}", port);
    let mut pids = Vec::new();
    for line in String::from_utf8_lossy(&out.stdout).lines() {
        // 4th whitespace field is the local address:port.
        let local = line.split_whitespace().nth(3).unwrap_or("");
        if !local.ends_with(&suffix) {
            continue;
        }
        // Extract every pid=N from the `users:((...,pid=N,fd=M))` tail.
        for tok in line.split("pid=").skip(1) {
            let n: String = tok.chars().take_while(|c| c.is_ascii_digit()).collect();
            if let Ok(pid) = n.parse::<i64>() {
                pids.push(pid);
            }
        }
    }
    pids
}

/// (service-label, port) pairs declared by the module's config.
fn generic_services(module_name: &str, config: &Value) -> Vec<(String, u16)> {
    let mut out = Vec::new();
    if let Some(p) = port_from(config, &["port", "api_port"], &["api"]) {
        out.push((format!("{}-api", module_name), p));
    }
    if let Some(p) = port_from(config, &["app_port"], &["app"]) {
        out.push((format!("{}-app", module_name), p));
    }
    out
}

fn generic_list(module_name: &str, config: &Value) -> Vec<Proc> {
    let mut procs = Vec::new();
    for (label, port) in generic_services(module_name, config) {
        for pid in pids_on_port(port) {
            procs.push(Proc {
                name: label.clone(),
                id: pid.to_string(),
                pid: Some(pid),
                status: "online".to_string(),
                detail: format!("port {}", port),
            });
        }
    }
    procs
}

// ── orphan reaping ───────────────────────────────────────────────────
//
// An "orphan" is a process SERVING from a module's directory (its executable or
// cwd resolves inside the module dir) on a port that is NOT one of the module's
// canonical api/app ports. These are stale duplicates — e.g. a `build-fork-jobs`
// hand-launched on :59999 next to the pm2-managed one on :8894, or a leftover
// `next dev` from a crashed run. They squat ports and serve OLD code, so an edit
// to the module's source never shows up where the gateway/app actually points →
// "works in the CLI but the app never changes". Reaping them guarantees the mod
// being edited is the one mapped to its live app + api.
//
// Safety rails (this runs as root):
//   • Only LISTENING processes are candidates (a server, not a transient child).
//   • Anything on a CANONICAL port (config api/app port, or a port a legit
//     backend-managed pid is already on) is never touched — that IS the app/api,
//     including next.js worker children whose pid differs from pm2's.
//   • The Claude CLI is never reaped: background `claude --print` jobs run with
//     cwd inside the build module dir but must keep running.

/// A stale process serving from a module dir on a non-canonical port.
#[derive(Clone)]
pub struct Orphan {
    pub pid: i64,
    pub port: u16,
    pub exe: String,
    pub cmd: String,
}

impl Orphan {
    pub fn to_json(&self) -> Value {
        json!({ "pid": self.pid, "port": self.port, "exe": self.exe, "cmd": self.cmd })
    }
}

fn canon(p: &Path) -> PathBuf {
    std::fs::canonicalize(p).unwrap_or_else(|_| p.to_path_buf())
}

fn proc_link(pid: i64, link: &str) -> Option<PathBuf> {
    std::fs::read_link(format!("/proc/{}/{}", pid, link)).ok()
}

fn proc_cmdline(pid: i64) -> String {
    std::fs::read(format!("/proc/{}/cmdline", pid))
        .map(|b| {
            b.split(|&c| c == 0)
                .map(|s| String::from_utf8_lossy(s).to_string())
                .collect::<Vec<_>>()
                .join(" ")
        })
        .unwrap_or_default()
        .trim()
        .to_string()
}

/// All `(pid, port)` pairs currently LISTENING on TCP (via `ss -ltnpH`).
pub fn all_listen_pid_ports() -> Vec<(i64, u16)> {
    let out = match Command::new("ss").args(["-ltnpH"]).output() {
        Ok(o) => o,
        Err(_) => return Vec::new(),
    };
    let mut pairs = Vec::new();
    for line in String::from_utf8_lossy(&out.stdout).lines() {
        let local = line.split_whitespace().nth(3).unwrap_or("");
        let port: u16 = match local.rsplit(':').next().and_then(|p| p.parse().ok()) {
            Some(p) => p,
            None => continue,
        };
        for tok in line.split("pid=").skip(1) {
            let n: String = tok.chars().take_while(|c| c.is_ascii_digit()).collect();
            if let Ok(pid) = n.parse::<i64>() {
                pairs.push((pid, port));
            }
        }
    }
    pairs
}

/// True for the Claude Code CLI — background jobs exec it with cwd inside the
/// build module dir, so it must never be mistaken for an orphan server.
fn is_claude_cli(exe: &Option<PathBuf>, cmd: &str) -> bool {
    let exe_s = exe.as_ref().map(|p| p.to_string_lossy().to_string()).unwrap_or_default();
    exe_s.ends_with("/claude")
        || exe_s.contains("claude-code")
        || cmd.contains("@anthropic-ai/claude-code")
        || cmd.contains("/claude --print")
}

/// Find processes serving from `module_dir` on a NON-canonical port. `canonical`
/// is the set of pids the active backend manages (their ports are protected too).
pub fn find_orphans(
    module_dir: &Path,
    module_name: &str,
    config: &Value,
    canonical: &[i64],
) -> Vec<Orphan> {
    let canon_dir = canon(module_dir);
    let inside = |p: &Path| {
        let c = canon(p);
        c == canon_dir || c.starts_with(&canon_dir)
    };

    let listeners = all_listen_pid_ports();
    let canon_set: HashSet<i64> = canonical.iter().copied().collect();

    // Protected ports: the module's declared api/app ports + any port a canonical
    // pid is already listening on. Nothing on these is ever reaped.
    let mut protected: HashSet<u16> =
        generic_services(module_name, config).into_iter().map(|(_, p)| p).collect();
    for (pid, port) in &listeners {
        if canon_set.contains(pid) {
            protected.insert(*port);
        }
    }

    let mut orphans = Vec::new();
    let mut seen: HashSet<i64> = HashSet::new();
    for (pid, port) in listeners {
        if canon_set.contains(&pid) || protected.contains(&port) || !seen.insert(pid) {
            continue;
        }
        let exe = proc_link(pid, "exe");
        let cwd = proc_link(pid, "cwd");
        let cmd = proc_cmdline(pid);
        if is_claude_cli(&exe, &cmd) {
            continue;
        }
        // A process belongs to the module if its EXECUTABLE lives inside the
        // module dir (a built server binary, e.g. `build-fork-jobs`). We also accept
        // a cwd-inside-dir match, but ONLY for a JS app server (node / next) —
        // otherwise an unrelated daemon merely launched from the module dir (e.g.
        // `ipfs daemon` with cwd here) would be wrongly reaped.
        let exe_base = exe
            .as_ref()
            .and_then(|p| p.file_name())
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default();
        let node_like = exe_base.starts_with("node") || cmd.contains("next");
        let exe_in = exe.as_deref().map(&inside).unwrap_or(false);
        let cwd_in = cwd.as_deref().map(&inside).unwrap_or(false);
        let from_module = exe_in || (cwd_in && node_like);
        if from_module {
            orphans.push(Orphan {
                pid,
                port,
                exe: exe.map(|p| p.to_string_lossy().to_string()).unwrap_or_default(),
                cmd,
            });
        }
    }
    orphans
}

/// Kill every orphan (SIGTERM, then SIGKILL stragglers). Returns the orphans
/// that were targeted plus a human-readable log line.
pub fn reap_orphans(
    module_dir: &Path,
    module_name: &str,
    config: &Value,
    canonical: &[i64],
) -> (Vec<Orphan>, String) {
    let orphans = find_orphans(module_dir, module_name, config, canonical);
    if orphans.is_empty() {
        return (orphans, "no orphans found".to_string());
    }
    for o in &orphans {
        unsafe { libc::kill(o.pid as i32, libc::SIGTERM) };
    }
    std::thread::sleep(std::time::Duration::from_millis(600));
    let mut killed = Vec::new();
    for o in &orphans {
        // SIGKILL anything still alive (kill(pid, 0) == 0 means it exists).
        if unsafe { libc::kill(o.pid as i32, 0) } == 0 {
            unsafe { libc::kill(o.pid as i32, libc::SIGKILL) };
        }
        killed.push(format!("pid {} on :{}", o.pid, o.port));
    }
    let log = format!("reaped {} orphan(s): {}", orphans.len(), killed.join(", "));
    (orphans, log)
}

/// Kill every process still attributed to the module — cwd, exe, or a cmdline
/// token inside the module dir, the same rule the /system and /providers panels
/// use, so KILL takes down exactly what the panel shows. A supervisor stop only
/// reaches what the supervisor registered; bare processes, detached children
/// and hand-launched dev servers all survive it. SIGTERM first, then SIGKILL
/// stragglers still alive 600ms later. Never touches this API's own pid or a
/// Claude CLI (background jobs run with cwd inside module dirs).
pub fn kill_stragglers(module_dir: &Path) -> (Vec<Value>, String) {
    let canon_dir = canon(module_dir);
    let inside = |p: &Path| {
        let c = canon(p);
        c == canon_dir || c.starts_with(&canon_dir)
    };
    let self_pid = std::process::id() as i64;

    let mut targets: Vec<(i64, String)> = Vec::new();
    if let Ok(rd) = std::fs::read_dir("/proc") {
        for e in rd.flatten() {
            let Ok(pid) = e.file_name().to_string_lossy().parse::<i64>() else { continue };
            if pid == self_pid {
                continue;
            }
            let exe = proc_link(pid, "exe");
            let cwd = proc_link(pid, "cwd");
            let cmd = proc_cmdline(pid);
            let arg_hit = cmd
                .split(' ')
                .any(|t| t.starts_with('/') && inside(Path::new(t)));
            let from_module = exe.as_deref().map(&inside).unwrap_or(false)
                || cwd.as_deref().map(&inside).unwrap_or(false)
                || arg_hit;
            if !from_module || is_claude_cli(&exe, &cmd) {
                continue;
            }
            targets.push((pid, cmd));
        }
    }
    if targets.is_empty() {
        return (Vec::new(), "no straggler processes left".to_string());
    }
    for (pid, _) in &targets {
        unsafe { libc::kill(*pid as i32, libc::SIGTERM) };
    }
    std::thread::sleep(std::time::Duration::from_millis(600));
    let mut killed = Vec::new();
    for (pid, cmd) in &targets {
        let sig = if unsafe { libc::kill(*pid as i32, 0) } == 0 {
            unsafe { libc::kill(*pid as i32, libc::SIGKILL) };
            "SIGKILL"
        } else {
            "SIGTERM"
        };
        killed.push(json!({ "pid": pid, "cmd": cmd, "signal": sig }));
    }
    let log = format!(
        "killed {} straggler(s): {}",
        killed.len(),
        targets.iter().map(|(pid, _)| pid.to_string()).collect::<Vec<_>>().join(", ")
    );
    (killed, log)
}

/// argv + cwd of a live process, captured from /proc before it is killed so a
/// launcher-less module can be respawned exactly as it was running.
fn snapshot_cmds(procs: &[Proc]) -> Vec<(Vec<String>, PathBuf)> {
    let mut snaps = Vec::new();
    for p in procs {
        let pid = match p.pid {
            Some(n) if n > 0 => n,
            _ => continue,
        };
        let argv: Vec<String> = std::fs::read(format!("/proc/{}/cmdline", pid))
            .map(|b| {
                b.split(|&c| c == 0)
                    .filter(|s| !s.is_empty())
                    .map(|s| String::from_utf8_lossy(s).to_string())
                    .collect()
            })
            .unwrap_or_default();
        if argv.is_empty() {
            continue;
        }
        let cwd = proc_link(pid, "cwd").unwrap_or_else(|| PathBuf::from("/"));
        snaps.push((argv, cwd));
    }
    snaps
}

/// Relaunch snapshotted commands, detached (own process group, no inherited
/// stdio) so they outlive this request and never receive our signals.
fn respawn(snaps: &[(Vec<String>, PathBuf)], _module_dir: &Path) -> (bool, String) {
    use std::os::unix::process::CommandExt;
    use std::process::Stdio;
    let mut launched = Vec::new();
    let mut failed = Vec::new();
    for (argv, cwd) in snaps {
        let mut cmd = Command::new(&argv[0]);
        cmd.args(&argv[1..])
            .current_dir(cwd)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .process_group(0);
        match cmd.spawn() {
            Ok(child) => launched.push(format!("pid {} · {}", child.id(), argv.join(" "))),
            Err(e) => failed.push(format!("{}: {}", argv.join(" "), e)),
        }
    }
    let ok = failed.is_empty() && !launched.is_empty();
    let mut out = format!("respawned {} bare proc(es)", launched.len());
    for l in &launched {
        out.push_str(&format!("\n  ↻ {}", l));
    }
    for f in &failed {
        out.push_str(&format!("\n  ✗ {}", f));
    }
    (ok, out)
}

fn generic_act(
    action: &str,
    procs: &[Proc],
    module_dir: &Path,
    module_name: &str,
    _config: &Value,
) -> (bool, String) {
    let kill = |sig: i32| -> usize {
        let mut n = 0;
        for p in procs {
            if let Some(pid) = p.pid {
                if unsafe { libc::kill(pid as i32, sig) } == 0 {
                    n += 1;
                }
            }
        }
        n
    };
    match action {
        "stop" => {
            if procs.is_empty() {
                return (true, "nothing running for this module".to_string());
            }
            let n = kill(libc::SIGTERM);
            (true, format!("sent SIGTERM to {} process(es)", n))
        }
        "start" => bootstrap(module_dir, module_name, false),
        _ => {
            // restart: stop what's up, then start fresh. A bare module may have
            // no launcher script at all (no ecosystem.config.js / start.sh), so
            // there are two ways back up: respawn the exact commands it is
            // running now — snapshotted BEFORE the kill — or, when nothing is
            // running to copy, hand it to the protocol (`m serve <name>`).
            // Refuse only when neither is possible, so nothing gets stopped
            // that cannot be brought back.
            let has_launcher = module_dir.join("ecosystem.config.js").exists()
                || module_dir.join("start.sh").exists();
            let snaps = if has_launcher { Vec::new() } else { snapshot_cmds(procs) };
            let respawnable = !has_launcher && !snaps.is_empty();
            if !has_launcher && !respawnable && !can_mod_serve(module_name) {
                return (
                    false,
                    format!(
                        "nothing to restart: no ecosystem.config.js / start.sh, no respawnable process, and `m serve {}` is unavailable — nothing was stopped",
                        module_name
                    ),
                );
            }
            let n = kill(libc::SIGTERM);
            std::thread::sleep(std::time::Duration::from_millis(800));
            let (ok, out) = if respawnable {
                respawn(&snaps, module_dir)
            } else {
                bootstrap(module_dir, module_name, false)
            };
            (ok, format!("stopped {} proc(es); {}", n, out))
        }
    }
}
