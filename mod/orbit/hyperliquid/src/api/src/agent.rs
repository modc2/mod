//! `/ask` — the module's own agent, driving the module's own MCP server.
//!
//! The work happens in `src/agent.py`: it runs the Claude CLI with this
//! module's stdio MCP server as its only toolbox. This route is the transport
//! — it spawns that process, hands it the caller's bearer token, and streams
//! the agent's events to the browser as SSE.
//!
//! **No new authority.** The token goes to the MCP child as
//! `HYPERLIQUID_TOKEN`; every tool the agent calls comes back through this
//! API's own REST surface, so `auth.rs` gates the agent exactly as it gates a
//! browser. Signed out, the agent sees only the public routes. Write tools are
//! off unless the caller asks for `act` *and* holds a token (agent.py builds
//! the allow/deny lists from `GET /mcp/schema`).

use std::convert::Infallible;
use std::path::PathBuf;
use std::process::Stdio;

use axum::extract::State;
use axum::http::{HeaderMap, StatusCode};
use axum::response::sse::{Event, KeepAlive, Sse};
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::Command;

use crate::AppState;

fn python() -> String {
    std::env::var("HL_AGENT_PYTHON").unwrap_or_else(|_| "python3".into())
}

/// Locate `src/agent.py`: explicit override, then relative to the running
/// binary (`src/api/target/<profile>/hyperliquid-api`), then the build tree.
fn script() -> Option<PathBuf> {
    if let Ok(p) = std::env::var("HL_AGENT_SCRIPT") {
        let p = PathBuf::from(p);
        if p.exists() {
            return Some(p);
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(src) = exe.ancestors().nth(4) {
            let p = src.join("agent.py");
            if p.exists() {
                return Some(p);
            }
        }
    }
    let p = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../agent.py");
    p.exists().then_some(p)
}

fn bearer(headers: &HeaderMap) -> String {
    headers
        .get(axum::http::header::AUTHORIZATION)
        .and_then(|h| h.to_str().ok())
        .and_then(|h| h.strip_prefix("Bearer ").or_else(|| h.strip_prefix("bearer ")))
        .unwrap_or("")
        .to_string()
}

/// Drain a child's stderr into the log — an unread pipe eventually blocks it.
fn log_stderr(stderr: tokio::process::ChildStderr) {
    tokio::spawn(async move {
        let mut lines = BufReader::new(stderr).lines();
        while let Ok(Some(line)) = lines.next_line().await {
            tracing::warn!("agent: {line}");
        }
    });
}

#[derive(Deserialize)]
pub struct AskReq {
    pub question: String,
    /// Opt in to write tools (orders, transfers, follows). Needs a token.
    #[serde(default)]
    pub act: bool,
}

/// POST /ask → SSE stream of `{type: ready|start|text|tool|tool_done|done|error}`.
pub async fn ask(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(req): Json<AskReq>,
) -> Response {
    let Some(script) = script() else {
        return err(StatusCode::SERVICE_UNAVAILABLE, "agent.py not found next to this binary");
    };
    let mut child = match Command::new(python())
        .arg(&script)
        .arg("--stream")
        .env("HL_API_URL", s.self_url.as_str())
        .env("HYPERLIQUID_TOKEN", bearer(&headers))
        .env("HL_AGENT_ACT", if req.act { "1" } else { "0" })
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true)
        .spawn()
    {
        Ok(c) => c,
        Err(e) => return err(StatusCode::SERVICE_UNAVAILABLE, &format!("cannot start agent: {e}")),
    };

    // Question goes over stdin, not argv — it never lands in a process listing.
    if let Some(mut stdin) = child.stdin.take() {
        let q = req.question.clone();
        tokio::spawn(async move {
            let _ = stdin.write_all(q.as_bytes()).await;
            let _ = stdin.shutdown().await;
        });
    }
    if let Some(stderr) = child.stderr.take() {
        log_stderr(stderr);
    }

    let lines = BufReader::new(child.stdout.take().expect("piped stdout")).lines();
    // The child rides along in the stream state: when the client disconnects
    // the stream drops, and kill_on_drop reaps the agent run with it.
    let stream = futures::stream::unfold((lines, child), |(mut lines, mut child)| async move {
        loop {
            match lines.next_line().await {
                Ok(Some(line)) if line.trim().is_empty() => continue,
                Ok(Some(line)) => {
                    return Some((Ok::<Event, Infallible>(Event::default().data(line)), (lines, child)))
                }
                _ => {
                    let _ = child.kill().await;
                    return None;
                }
            }
        }
    });
    Sse::new(stream).keep_alive(KeepAlive::default()).into_response()
}

/// GET /ask/status → model auth, tool counts, readiness. Public: the UI shows
/// the state of the agent before anyone signs in.
pub async fn ask_status(State(s): State<AppState>) -> Response {
    let Some(script) = script() else {
        return Json(json!({ "ready": false, "hint": "agent.py not found next to this binary" }))
            .into_response();
    };
    let mut cmd = Command::new(python());
    cmd.arg(&script)
        .arg("--status")
        .env("HL_API_URL", s.self_url.as_str())
        .stdin(Stdio::null())
        .kill_on_drop(true);
    let out = match tokio::time::timeout(std::time::Duration::from_secs(20), cmd.output()).await {
        Ok(Ok(o)) => o,
        Ok(Err(e)) => return Json(json!({ "ready": false, "hint": format!("cannot start agent: {e}") })).into_response(),
        Err(_) => return Json(json!({ "ready": false, "hint": "agent status timed out" })).into_response(),
    };
    match serde_json::from_slice::<Value>(&out.stdout) {
        Ok(v) => Json(v).into_response(),
        Err(_) => {
            let stderr = String::from_utf8_lossy(&out.stderr);
            let tail = stderr.lines().last().unwrap_or("no output");
            Json(json!({ "ready": false, "hint": format!("agent status failed: {tail}") })).into_response()
        }
    }
}

fn err(code: StatusCode, message: &str) -> Response {
    (code, Json(json!({ "error": message }))).into_response()
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::Method;

    #[test]
    fn the_agent_script_ships_with_the_crate() {
        assert!(script().is_some(), "src/agent.py must be findable from the binary");
    }

    /// Asking spends model credits and can act on the caller's wallet, so it
    /// stays behind the token; only the readiness probe is open.
    #[test]
    fn only_the_status_probe_is_public() {
        assert!(crate::auth::is_public(&Method::GET, "/ask/status"));
        assert!(!crate::auth::is_public(&Method::POST, "/ask"));
        assert!(!crate::auth::is_public(&Method::GET, "/ask"));
    }

    #[test]
    fn bearer_is_read_without_the_scheme() {
        let mut h = HeaderMap::new();
        assert_eq!(bearer(&h), "");
        h.insert(axum::http::header::AUTHORIZATION, "Bearer tok".parse().unwrap());
        assert_eq!(bearer(&h), "tok");
    }
}
