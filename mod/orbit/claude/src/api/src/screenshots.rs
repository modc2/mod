//! HUB profile pics — cached headless-chromium screenshots of each module's app.
//!
//! `GET /modules/:name/screenshot` serves a PNG of what `https://{host}/{name}`
//! looks like right now. Captures go through the *local* caddy (the gateway
//! host is DNS-mapped to 127.0.0.1 inside the browser) so nothing depends on
//! public DNS or leaves the machine, and the shot shows exactly what a visitor
//! sees. Results cache under ~/.mod/claude/screenshots/ — fresh shots are
//! served as-is, stale ones are served immediately while a background capture
//! refreshes them, and failures are negative-cached so a dead module doesn't
//! get a chrome launch on every hub render.

use axum::{
    extract::{Path as UrlPath, Query},
    http::StatusCode,
    response::IntoResponse,
};
use serde::Deserialize;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex as StdMutex, OnceLock};
use std::time::{Duration, SystemTime};
use tokio::sync::{Mutex as TokioMutex, Semaphore};

/// Serve a cached shot without refreshing for this long.
const FRESH_TTL: Duration = Duration::from_secs(6 * 3600);
/// After a failed capture, don't retry for this long.
const FAIL_TTL: Duration = Duration::from_secs(10 * 60);
/// Hard cap on one chrome run.
const CAPTURE_TIMEOUT: Duration = Duration::from_secs(45);
/// `?refresh=1` is ignored if the shot is younger than this (public route —
/// keeps a hostile refresh loop from pinning chrome at 100% CPU).
const REFRESH_FLOOR: Duration = Duration::from_secs(60);
/// `?fresh=1` captures synchronously (the console's snap-on-submit toggle
/// attaches the shot to a job, so it must show the app as it is NOW, not a
/// stale cache). This floor keeps rapid-fire submits from stacking chrome
/// runs while still being "current" for any human-paced submission.
const FRESH_FLOOR: Duration = Duration::from_secs(10);

fn home() -> String {
    std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string())
}

fn shots_dir() -> PathBuf {
    let dir = PathBuf::from(format!("{}/.mod/claude/screenshots", home()));
    let _ = std::fs::create_dir_all(&dir);
    dir
}

/// The router's public host — parsed from the generated caddy site block
/// (`# host=modc2.com generated_at_epoch=…`) so it tracks `m caddy/host`
/// automatically. Env override wins; modc2.com is the last-resort default.
fn gateway_host() -> String {
    if let Ok(h) = std::env::var("CLAUDE_SHOT_HOST") {
        if !h.trim().is_empty() {
            return h.trim().to_string();
        }
    }
    if let Ok(text) = std::fs::read_to_string("/etc/caddy/mod_site.caddy") {
        for line in text.lines().take(10) {
            if let Some(idx) = line.find("host=") {
                let rest = &line[idx + 5..];
                let host: String = rest
                    .chars()
                    .take_while(|c| !c.is_whitespace())
                    .collect();
                if !host.is_empty() {
                    return host;
                }
            }
        }
    }
    "modc2.com".to_string()
}

/// Locate a headless chromium. Playwright's headless-shell installs are the
/// normal case on this host; PATH browsers are the fallback.
fn browser_bin() -> Option<PathBuf> {
    if let Ok(p) = std::env::var("CLAUDE_SHOT_BROWSER") {
        let pb = PathBuf::from(p);
        if pb.is_file() {
            return Some(pb);
        }
    }
    let pw = PathBuf::from(format!("{}/.cache/ms-playwright", home()));
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Ok(entries) = std::fs::read_dir(&pw) {
        for entry in entries.flatten() {
            let dir = entry.path();
            let name = entry.file_name().to_string_lossy().to_string();
            if name.starts_with("chromium_headless_shell-") {
                candidates.push(dir.join("chrome-headless-shell-linux64/chrome-headless-shell"));
            } else if name.starts_with("chromium-") {
                candidates.push(dir.join("chrome-linux/chrome"));
            }
        }
    }
    candidates.retain(|p| p.is_file());
    // Highest version wins; headless-shell sorts after chromium- so prefer it
    // explicitly by sorting on (is_shell, path).
    candidates.sort_by_key(|p| {
        let s = p.to_string_lossy().to_string();
        (s.contains("headless_shell"), s)
    });
    if let Some(best) = candidates.pop() {
        return Some(best);
    }
    for name in ["chromium", "chromium-browser", "google-chrome", "chrome"] {
        if let Ok(out) = std::process::Command::new("which").arg(name).output() {
            if out.status.success() {
                let p = String::from_utf8_lossy(&out.stdout).trim().to_string();
                if !p.is_empty() {
                    return Some(PathBuf::from(p));
                }
            }
        }
    }
    None
}

/// Only real module names reach the filesystem / browser.
fn valid_name(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 64
        && name
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
}

/// A screenshot only makes sense for a module that exists locally and ships an
/// app; everything else 404s before any capture work happens.
fn module_has_app(name: &str) -> bool {
    for tree in ["orbit", "core"] {
        let dir = PathBuf::from(format!("{}/mod/mod/{}/{}", home(), tree, name));
        if !dir.is_dir() {
            continue;
        }
        if dir.join("app").is_dir() || dir.join("src/app").is_dir() {
            return true;
        }
        for cfg in [dir.join("config.json"), dir.join(name).join("config.json")] {
            if let Ok(text) = std::fs::read_to_string(cfg) {
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                    let has = v
                        .get("urls")
                        .and_then(|u| u.get("app"))
                        .and_then(|s| s.as_str())
                        .is_some()
                        || v.get("app_url").and_then(|s| s.as_str()).is_some();
                    if has {
                        return true;
                    }
                }
            }
        }
        // Directory exists but no app signal — a bare screenshot of the
        // gateway 404 page is worse than the letter-tile fallback.
        return false;
    }
    false
}

fn age_of(path: &Path) -> Option<Duration> {
    std::fs::metadata(path)
        .and_then(|m| m.modified())
        .ok()
        .and_then(|t| SystemTime::now().duration_since(t).ok())
}

// One capture may be in flight per module (late callers wait on the same
// lock, then find the fresh file), and only a couple of chrome processes may
// run at once machine-wide.
fn lock_for(name: &str) -> Arc<TokioMutex<()>> {
    static LOCKS: OnceLock<StdMutex<HashMap<String, Arc<TokioMutex<()>>>>> = OnceLock::new();
    let map = LOCKS.get_or_init(|| StdMutex::new(HashMap::new()));
    let mut guard = map.lock().unwrap();
    guard
        .entry(name.to_string())
        .or_insert_with(|| Arc::new(TokioMutex::new(())))
        .clone()
}

fn chrome_slots() -> &'static Semaphore {
    static SLOTS: OnceLock<Semaphore> = OnceLock::new();
    SLOTS.get_or_init(|| Semaphore::new(2))
}

/// Run one capture: pre-flight the route through local caddy, then let chrome
/// render and screenshot it. Writes atomically (tmp + rename) so readers never
/// see a half-written PNG. `path` is a sub-route within the module ("" or
/// "/foo?x=1"), `w`×`h` the viewport, `budget_ms` the virtual-time budget the
/// SPA gets to settle.
async fn capture(
    name: &str,
    path: &str,
    w: u32,
    h: u32,
    budget_ms: u64,
    dest: &Path,
) -> Result<(), String> {
    let bin = browser_bin().ok_or("no headless chromium available on this host")?;
    let host = gateway_host();
    let url = format!("https://{}/{}{}", host, name, path);

    // Pre-flight: only screenshot routes that actually answer. This also
    // pokes the activator awake for sleeping modules before chrome shows up.
    let client = reqwest::Client::builder()
        .resolve(&host, std::net::SocketAddr::from(([127, 0, 0, 1], 443)))
        .danger_accept_invalid_certs(true)
        .timeout(Duration::from_secs(20))
        .build()
        .map_err(|e| format!("http client: {}", e))?;
    let status = client
        .get(&url)
        .send()
        .await
        .map_err(|e| format!("pre-flight {}: {}", url, e))?
        .status();
    if !status.is_success() {
        return Err(format!("{} answered {}", url, status));
    }

    // acquire() only errs if the semaphore is closed; ours never closes.
    let _slot = chrome_slots().acquire().await.expect("screenshot semaphore closed");
    let tmp = dest.with_extension("tmp.png");
    let _ = std::fs::remove_file(&tmp);

    let mut cmd = tokio::process::Command::new(&bin);
    cmd.arg("--headless")
        .arg("--no-sandbox")
        .arg("--disable-gpu")
        .arg("--hide-scrollbars")
        .arg("--disable-extensions")
        .arg("--force-color-profile=srgb")
        .arg(format!("--window-size={},{}", w, h))
        .arg(format!("--screenshot={}", tmp.display()))
        // Fast-forward timers/animations so SPAs settle without a wall-clock wait.
        .arg(format!("--virtual-time-budget={}", budget_ms))
        .arg(format!("--timeout={}", budget_ms + 15_000))
        .arg(format!("--host-resolver-rules=MAP {} 127.0.0.1", host))
        .arg("--ignore-certificate-errors")
        .arg(&url)
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .kill_on_drop(true);

    let run = tokio::time::timeout(CAPTURE_TIMEOUT, cmd.status()).await;
    match run {
        Ok(Ok(st)) if st.success() => {}
        Ok(Ok(st)) => {
            let _ = std::fs::remove_file(&tmp);
            return Err(format!("chrome exited with {}", st));
        }
        Ok(Err(e)) => {
            let _ = std::fs::remove_file(&tmp);
            return Err(format!("chrome spawn: {}", e));
        }
        Err(_) => {
            let _ = std::fs::remove_file(&tmp);
            return Err("capture timed out".to_string());
        }
    }

    let size = std::fs::metadata(&tmp).map(|m| m.len()).unwrap_or(0);
    if size < 1024 {
        let _ = std::fs::remove_file(&tmp);
        return Err("capture produced no usable image".to_string());
    }
    std::fs::rename(&tmp, dest).map_err(|e| format!("rename: {}", e))?;
    let _ = std::fs::remove_file(fail_marker(dest));
    Ok(())
}

fn fail_marker(png: &Path) -> PathBuf {
    png.with_extension("fail")
}

fn note_failure(png: &Path, why: &str) {
    let _ = std::fs::write(fail_marker(png), why);
}

/// Capture under the per-module lock, re-checking freshness after acquiring so
/// a burst of hub loads collapses into a single chrome run.
async fn capture_locked(name: &str, png: &Path, min_age: Duration) -> Result<(), String> {
    let lock = lock_for(name);
    let _guard = lock.lock().await;
    if age_of(png).map(|a| a < min_age).unwrap_or(false) {
        return Ok(()); // someone else just captured it
    }
    match capture(name, "", 800, 500, 10_000, png).await {
        Ok(()) => Ok(()),
        Err(e) => {
            note_failure(png, &e);
            Err(e)
        }
    }
}

#[derive(Deserialize)]
pub struct ShotQuery {
    refresh: Option<String>,
    fresh: Option<String>,
}

/// GET /modules/:name/screenshot — the module's app as a PNG "profile pic".
/// Public, like GET /modules: it shows nothing a visitor couldn't see by
/// opening the module's own URL.
pub async fn module_screenshot(
    UrlPath(name): UrlPath<String>,
    Query(q): Query<ShotQuery>,
) -> impl IntoResponse {
    if !valid_name(&name) {
        return (StatusCode::BAD_REQUEST, "bad module name").into_response();
    }
    if !module_has_app(&name) {
        return (StatusCode::NOT_FOUND, "module has no app").into_response();
    }

    let png = shots_dir().join(format!("{}.png", name));
    let force = matches!(q.refresh.as_deref(), Some("1") | Some("true"));
    let fresh = matches!(q.fresh.as_deref(), Some("1") | Some("true"));
    let age = age_of(&png);

    let serve = |path: &Path| -> axum::response::Response {
        match std::fs::read(path) {
            Ok(bytes) => (
                StatusCode::OK,
                [
                    (axum::http::header::CONTENT_TYPE, "image/png"),
                    (axum::http::header::CACHE_CONTROL, "public, max-age=300"),
                ],
                bytes,
            )
                .into_response(),
            Err(_) => (StatusCode::NOT_FOUND, "no screenshot").into_response(),
        }
    };

    // ?fresh=1 — capture inline and only then answer, so the caller gets a
    // shot of the app as it is right now (unless one was taken seconds ago,
    // which is current enough). Failure still serves a stale shot if any
    // exists: an out-of-date attachment beats none.
    if fresh {
        let result = capture_locked(&name, &png, FRESH_FLOOR).await;
        return if png.is_file() {
            serve(&png)
        } else {
            let why = result.err().unwrap_or_else(|| "no screenshot".to_string());
            (StatusCode::NOT_FOUND, format!("capture failed: {}", why)).into_response()
        };
    }

    match age {
        Some(a) if !force && a < FRESH_TTL => serve(&png),
        Some(a) => {
            // Stale (or refresh requested): serve what we have now, refresh in
            // the background. The refresh floor keeps ?refresh from being a
            // free chrome-spawning lever.
            let min_age = if force { REFRESH_FLOOR } else { FRESH_TTL };
            if a >= min_age {
                let n = name.clone();
                let p = png.clone();
                tokio::spawn(async move {
                    let _ = capture_locked(&n, &p, min_age).await;
                });
            }
            serve(&png)
        }
        None => {
            // No shot yet. Respect the negative cache, then capture inline so
            // the very first hub render still gets real pictures.
            if let Some(fa) = age_of(&fail_marker(&png)) {
                if fa < FAIL_TTL && !force {
                    return (StatusCode::NOT_FOUND, "capture failed recently").into_response();
                }
            }
            match capture_locked(&name, &png, FRESH_TTL).await {
                Ok(()) => serve(&png),
                Err(e) => (StatusCode::NOT_FOUND, format!("capture failed: {}", e)).into_response(),
            }
        }
    }
}
