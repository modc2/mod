//! stdio upstreams — MCP servers that are a process, not a URL.
//!
//! Most of the MCP servers on GitHub are published this way: there is nothing
//! to connect to, you are expected to run `npx -y some-server` yourself and
//! speak JSON-RPC over its stdin/stdout. The hub does that here, so a repo
//! link can end up as an ordinary row in the registry whose tools are callable
//! over HTTP like every other row's — the hub is the thing that turns a
//! process into an endpoint.
//!
//! One child per server id, started lazily on the first call and kept warm.
//! Calls to one child are serialised (a stdio pipe has no multiplexing worth
//! the complexity here); different children run in parallel. A child that has
//! not been used for `MCP_STDIO_IDLE_SECS` is killed and re-started on demand,
//! which is the same scale-to-zero bargain the rest of the fleet makes.
//!
//! Starting a child runs code on this host. That is gated: only the registry
//! (owner/local-only, see main.rs) can create a stdio row, and setting
//! `MCP_ALLOW_EXEC=0` refuses to start one at all.

use crate::store::{now, Probe, ServerEntry};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::process::Stdio;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::Command;
use tokio::sync::{mpsc, oneshot, Mutex as AsyncMutex};

/// Is starting child processes allowed at all on this deployment?
pub fn exec_allowed() -> bool {
    std::env::var("MCP_ALLOW_EXEC").ok().as_deref() != Some("0")
}

fn start_timeout() -> u64 {
    // The first `npx -y` of a package downloads it; that is the slow case this
    // budget exists for, not the handshake itself.
    std::env::var("MCP_STDIO_START_TIMEOUT").ok().and_then(|v| v.parse().ok()).unwrap_or(180)
}

fn idle_timeout() -> u64 {
    std::env::var("MCP_STDIO_IDLE_SECS").ok().and_then(|v| v.parse().ok()).unwrap_or(900)
}

struct Job {
    method: String,
    params: Value,
    /// None for a notification — nothing to wait for.
    reply: Option<oneshot::Sender<Result<Value, String>>>,
}

pub struct Handle {
    tx: mpsc::Sender<Job>,
    pub started_at: u64,
    pub pid: u32,
    /// Last lines the child wrote to stderr. The only explanation available
    /// when `npx` fails or a server exits complaining about a missing key.
    pub log: Arc<Mutex<Vec<String>>>,
}

impl Handle {
    fn alive(&self) -> bool {
        !self.tx.is_closed()
    }

    pub fn stderr(&self) -> Vec<String> {
        self.log.lock().map(|l| l.clone()).unwrap_or_default()
    }
}

type Slot = Arc<AsyncMutex<Option<Arc<Handle>>>>;

fn procs() -> &'static Mutex<HashMap<String, Slot>> {
    static PROCS: OnceLock<Mutex<HashMap<String, Slot>>> = OnceLock::new();
    PROCS.get_or_init(|| Mutex::new(HashMap::new()))
}

fn slot_of(id: &str) -> Slot {
    let mut map = procs().lock().expect("procs lock");
    map.entry(id.to_string()).or_default().clone()
}

fn note(log: &Arc<Mutex<Vec<String>>>, line: String) {
    if let Ok(mut l) = log.lock() {
        if l.len() >= 60 {
            l.remove(0);
        }
        l.push(line);
    }
}

/// Everything the child said on stderr, for an error message.
fn tail(log: &Arc<Mutex<Vec<String>>>, n: usize) -> String {
    let lines = log.lock().map(|l| l.clone()).unwrap_or_default();
    let start = lines.len().saturating_sub(n);
    lines[start..].join(" | ").chars().take(600).collect()
}

/// Start the child and the pump that owns its pipes. The returned handle is
/// alive but has not shaken hands yet — `ensure` does that.
fn spawn(server: &ServerEntry) -> Result<Arc<Handle>, String> {
    if !exec_allowed() {
        return Err("this hub is running with MCP_ALLOW_EXEC=0, so it will not start server processes".into());
    }
    if server.command.trim().is_empty() {
        return Err(format!("server `{}` is stdio but has no command", server.id));
    }
    let mut cmd = Command::new(&server.command);
    cmd.args(&server.args)
        .envs(server.env.iter())
        // A stdio server must not be confused by an interactive terminal, and
        // npm/npx chatter on stdout would corrupt the JSON-RPC stream — the
        // spec puts diagnostics on stderr, which we capture separately.
        .env("NO_COLOR", "1")
        .env("CI", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    if !server.cwd.is_empty() {
        cmd.current_dir(&server.cwd);
    }
    let mut child = cmd.spawn().map_err(|e| match e.kind() {
        std::io::ErrorKind::NotFound => format!(
            "`{}` is not installed on this host (needed to run {})",
            server.command,
            server.location()
        ),
        _ => format!("could not start `{}`: {e}", server.command),
    })?;
    let pid = child.id().unwrap_or(0);
    let log: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));

    let mut stdin = child.stdin.take().ok_or("child has no stdin")?;
    let stdout = child.stdout.take().ok_or("child has no stdout")?;
    let stderr = child.stderr.take();

    // stdout → parsed JSON-RPC messages
    let (msg_tx, mut msg_rx) = mpsc::channel::<Value>(64);
    let out_log = log.clone();
    tokio::spawn(async move {
        let mut lines = BufReader::new(stdout).lines();
        while let Ok(Some(line)) = lines.next_line().await {
            let line = line.trim().to_string();
            if line.is_empty() {
                continue;
            }
            match serde_json::from_str::<Value>(&line) {
                Ok(v) => {
                    if msg_tx.send(v).await.is_err() {
                        break;
                    }
                }
                // Servers that print a banner before the protocol starts are
                // common enough that this is a note, not a failure.
                Err(_) => note(&out_log, format!("stdout(non-json): {}", &line.chars().take(200).collect::<String>())),
            }
        }
    });

    if let Some(stderr) = stderr {
        let err_log = log.clone();
        tokio::spawn(async move {
            let mut lines = BufReader::new(stderr).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                if !line.trim().is_empty() {
                    note(&err_log, line.chars().take(300).collect());
                }
            }
        });
    }

    let (tx, mut rx) = mpsc::channel::<Job>(16);
    let pump_log = log.clone();
    let id = server.id.clone();
    tokio::spawn(async move {
        let mut next_id = 1u64;
        let idle = Duration::from_secs(idle_timeout());
        loop {
            let job = match tokio::time::timeout(idle, rx.recv()).await {
                Ok(Some(job)) => job,
                // Idle out, or every sender dropped (the server was removed).
                Ok(None) | Err(_) => break,
            };
            let rpc_id = next_id;
            next_id += 1;
            let mut msg = json!({ "jsonrpc": "2.0", "method": job.method, "params": job.params });
            if job.reply.is_some() {
                msg["id"] = json!(rpc_id);
            }
            let wire = format!("{msg}\n");
            if let Err(e) = stdin.write_all(wire.as_bytes()).await {
                if let Some(r) = job.reply {
                    let _ = r.send(Err(format!("unreachable: writing to `{id}` failed: {e}")));
                }
                break;
            }
            let _ = stdin.flush().await;
            let Some(reply) = job.reply else { continue };

            // Read until our id comes back. Notifications, log messages and
            // server-initiated requests all share this pipe; they are not ours.
            let budget = Duration::from_secs(if rpc_id == 1 { start_timeout() } else { crate::upstream::default_timeout() });
            let deadline = Instant::now() + budget;
            let answer = loop {
                let left = deadline.saturating_duration_since(Instant::now());
                if left.is_zero() {
                    break Err(format!(
                        "`{id}` did not answer {} within {}s{}",
                        msg.get("method").and_then(|m| m.as_str()).unwrap_or(""),
                        budget.as_secs(),
                        match tail(&pump_log, 4) {
                            t if t.is_empty() => String::new(),
                            t => format!(" — it said: {t}"),
                        }
                    ));
                }
                match tokio::time::timeout(left, msg_rx.recv()).await {
                    Ok(Some(v)) => {
                        if v.get("id").and_then(|i| i.as_u64()) != Some(rpc_id) {
                            continue;
                        }
                        if let Some(e) = v.get("error") {
                            let m = e.get("message").and_then(|m| m.as_str()).unwrap_or("rpc error");
                            break Err(m.to_string());
                        }
                        break Ok(v.get("result").cloned().unwrap_or(Value::Null));
                    }
                    // stdout closed: the child is gone.
                    Ok(None) => break Err(format!(
                        "unreachable: `{id}` exited{}",
                        match tail(&pump_log, 6) {
                            t if t.is_empty() => String::new(),
                            t => format!(" — it said: {t}"),
                        }
                    )),
                    Err(_) => continue, // loop re-checks the deadline
                }
            };
            let died = matches!(&answer, Err(e) if e.starts_with("unreachable:"));
            let _ = reply.send(answer);
            if died {
                break;
            }
        }
        // Dropping stdin and the Child (kill_on_drop) ends the process.
    });

    Ok(Arc::new(Handle { tx, started_at: now(), pid, log }))
}

async fn send(handle: &Handle, method: &str, params: Value, wait: bool) -> Result<Value, String> {
    if !wait {
        let _ = handle
            .tx
            .send(Job { method: method.into(), params, reply: None })
            .await;
        return Ok(Value::Null);
    }
    let (tx, rx) = oneshot::channel();
    handle
        .tx
        .send(Job { method: method.into(), params, reply: Some(tx) })
        .await
        .map_err(|_| "unreachable: the server process is gone".to_string())?;
    rx.await.map_err(|_| "unreachable: the server process stopped mid-call".to_string())?
}

/// The running child for this server, started and handshaken if it isn't yet.
async fn ensure(server: &ServerEntry) -> Result<Arc<Handle>, String> {
    let slot = slot_of(&server.id);
    let mut guard = slot.lock().await;
    if let Some(h) = guard.as_ref() {
        if h.alive() {
            return Ok(h.clone());
        }
    }
    let handle = spawn(server)?;
    let init = send(
        &handle,
        "initialize",
        json!({
            "protocolVersion": crate::upstream::PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": { "name": "mcp-hub", "version": env!("CARGO_PKG_VERSION") }
        }),
        true,
    )
    .await;
    match init {
        Ok(_) => {
            let _ = send(&handle, "notifications/initialized", json!({}), false).await;
            *guard = Some(handle.clone());
            Ok(handle)
        }
        Err(e) => Err(e),
    }
}

/// One request against a stdio server, starting it if necessary.
pub async fn rpc(server: &ServerEntry, method: &str, params: Value) -> Result<Value, String> {
    let handle = ensure(server).await?;
    match send(&handle, method, params.clone(), true).await {
        // The child died between "it's alive" and the write — that is what a
        // reaped idle process looks like to a caller. Start it again once.
        Err(e) if e.starts_with("unreachable:") => {
            stop(&server.id).await;
            let handle = ensure(server).await?;
            send(&handle, method, params, true).await
        }
        other => other,
    }
}

/// initialize + tools/list against a stdio server.
pub async fn probe(server: &ServerEntry) -> Probe {
    let started = Instant::now();
    let handle = match ensure(server).await {
        Ok(h) => h,
        Err(e) => return Probe { ok: false, error: e, checked_at: now(), ..Default::default() },
    };
    // ensure() already ran initialize; ask again so the probe carries the real
    // serverInfo rather than a remembered one.
    let init = send(&handle, "ping", json!({}), true).await;
    let tools = match send(&handle, "tools/list", json!({}), true).await {
        Ok(v) => v.get("tools").and_then(|t| t.as_array()).cloned().unwrap_or_default(),
        Err(e) => {
            return Probe {
                ok: false,
                error: format!("started but tools/list failed: {e}"),
                checked_at: now(),
                ..Default::default()
            }
        }
    };
    let _ = init;
    Probe {
        ok: true,
        protocol_version: crate::upstream::PROTOCOL_VERSION.into(),
        server_info: json!({ "name": server.name, "transport": "stdio", "pid": handle.pid }),
        tools,
        latency_ms: started.elapsed().as_millis() as u64,
        checked_at: now(),
        error: String::new(),
        via: server.location(),
    }
}

/// Kill the child for one server (if any). Used when a row is removed,
/// disabled, or re-installed with different arguments.
pub async fn stop(id: &str) {
    let slot = slot_of(id);
    let mut guard = slot.lock().await;
    *guard = None; // dropping the sender ends the pump, which kills the child
}

/// Is this server's process up? Cheap enough to ask before every re-probe.
pub async fn is_running(id: &str) -> bool {
    let slot = slot_of(id);
    let guard = slot.lock().await;
    guard.as_ref().map(|h| h.alive()).unwrap_or(false)
}

/// What is running right now, for the console and /stats.
pub async fn running() -> Vec<Value> {
    let slots: Vec<(String, Slot)> = {
        let map = procs().lock().expect("procs lock");
        map.iter().map(|(k, v)| (k.clone(), v.clone())).collect()
    };
    let mut out = Vec::new();
    for (id, slot) in slots {
        let guard = slot.lock().await;
        if let Some(h) = guard.as_ref() {
            if h.alive() {
                out.push(json!({
                    "id": id,
                    "pid": h.pid,
                    "started_at": h.started_at,
                    "uptime_s": now().saturating_sub(h.started_at),
                    "stderr": h.stderr(),
                }));
            }
        }
    }
    out.sort_by(|a, b| a["id"].as_str().unwrap_or("").cmp(b["id"].as_str().unwrap_or("")));
    out
}

/// The last thing one server's process said on stderr — the console shows this
/// when a stdio row is down, because it is usually the whole story.
pub async fn stderr_of(id: &str) -> Vec<String> {
    let slot = slot_of(id);
    let guard = slot.lock().await;
    guard.as_ref().map(|h| h.stderr()).unwrap_or_default()
}
