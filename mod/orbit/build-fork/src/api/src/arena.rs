//! Arena interop — this console as a client of arenas, and as a competitor in
//! them.
//!
//! Two directions, and they are deliberately different shapes.
//!
//! OUTWARD (the `arena_*` MCP tools): we find every arena/1.0 module on this
//! fleet by reading configs, not from a list compiled in here, and we speak to
//! them over their own MCP endpoint. Arenas share a protocol but not a
//! vocabulary — the wasm arena `enter_player`s, the coding arena `enter_agent`s
//! — so we probe `tools/list` and adapt, and an arena that ships next month
//! works with no change to this file.
//!
//! INWARD (`POST /arena/solve`, `POST /arena/play`): we enter arenas as an
//! `http` competitor, which means the arena calls US, and each call runs a real
//! agent job on this box. That spends money, so those two endpoints are
//! default-deny: off until the owner runs `arena_enter`, and then only for a
//! caller holding the shared key minted at that moment. Withdrawing switches
//! them back off.

use crate::auth;
use crate::jobs::{ClaudeJobManager, SubmitRequest};
use axum::{extract::State, http::HeaderMap, http::StatusCode, response::IntoResponse, Json};
use serde::Deserialize;
use serde_json::{json, Value};
use std::path::PathBuf;
use std::sync::Arc;
use std::sync::Mutex;
use std::time::{Duration, Instant};

/// What an arena declares in its config.json. Matched on the family, not the
/// exact version: arena/1.1 is still something we can talk to.
const PROTOCOL: &str = "arena/";

/// How an arena's own tool list names "put this competitor on the board", and
/// what entering with it implies about the role we would be playing.
const ENTER_TOOLS: [(&str, &str); 2] = [("enter_agent", "solve"), ("enter_player", "play")];

fn client() -> reqwest::Client {
    reqwest::Client::builder()
        .timeout(Duration::from_secs(120))
        .build()
        .unwrap_or_default()
}

fn home() -> PathBuf {
    PathBuf::from(std::env::var("HOME").unwrap_or_else(|_| "/tmp".into()))
}

fn local_mode() -> bool {
    std::env::var("BUILD_FORK_JOBS_LOCAL").unwrap_or_default() == "1"
}

// ── discovery ────────────────────────────────────────────────────────

#[derive(Clone, Debug)]
pub struct Peer {
    pub name: String,
    pub mcp: String,
    pub description: String,
    pub kinds: Vec<String>,
    pub base_path: String,
}

impl Peer {
    fn to_json(&self) -> Value {
        json!({
            "name": self.name,
            "mcp": self.mcp,
            "description": self.description,
            "competitor_kinds": self.kinds,
            "console": self.base_path,
        })
    }
}

static CACHE: Mutex<Option<(Instant, Vec<Peer>)>> = Mutex::new(None);
const CACHE_TTL: Duration = Duration::from_secs(60);

fn anchor() -> PathBuf {
    let raw = std::env::var("MOD_ANCHOR").unwrap_or_else(|_| "~/mod".into());
    if let Some(rest) = raw.strip_prefix('~') {
        home().join(rest.trim_start_matches('/'))
    } else {
        PathBuf::from(raw)
    }
}

/// Read the fleet's configs and keep the ones that say they are arenas.
fn scan() -> Vec<Peer> {
    let mut out: Vec<Peer> = Vec::new();
    for group in ["mod/orbit", "mod/core"] {
        let dir = anchor().join(group);
        let Ok(entries) = std::fs::read_dir(&dir) else { continue };
        for e in entries.flatten() {
            let cfg = e.path().join("config.json");
            let Ok(raw) = std::fs::read_to_string(&cfg) else { continue };
            let Ok(v) = serde_json::from_str::<Value>(&raw) else { continue };
            let proto = v.get("protocol").and_then(|p| p.as_str()).unwrap_or("");
            if !proto.starts_with(PROTOCOL) {
                continue;
            }
            let name = v
                .get("name")
                .and_then(|n| n.as_str())
                .map(str::to_string)
                .unwrap_or_else(|| e.file_name().to_string_lossy().to_string());
            if name.is_empty() {
                continue;
            }
            // A private module is not on the fleet as far as anyone else is
            // concerned — it doesn't get to be a public arena either. This
            // registry is cached and shared, so the filter is absolute
            // rather than per-caller: go public to compete.
            if crate::privacy::is_private(&name) {
                continue;
            }
            // Prefer the mcp url the module publishes; fall back to its port.
            let mcp = v
                .get("urls")
                .and_then(|u| u.get("mcp"))
                .and_then(|m| m.as_str())
                .map(str::to_string)
                .or_else(|| {
                    v.get("port")
                        .and_then(|p| p.as_u64())
                        .map(|p| format!("http://127.0.0.1:{p}/mcp"))
                });
            let Some(mcp) = mcp else { continue };
            let kinds = v
                .get("competitor_kinds")
                .or_else(|| v.get("player_kinds"))
                .and_then(|k| k.as_array())
                .map(|a| a.iter().filter_map(|x| x.as_str().map(str::to_string)).collect())
                .unwrap_or_default();
            out.push(Peer {
                name,
                mcp,
                description: v
                    .get("description")
                    .and_then(|d| d.as_str())
                    .unwrap_or("")
                    .chars()
                    .take(400)
                    .collect(),
                kinds,
                base_path: v
                    .get("base_path")
                    .and_then(|b| b.as_str())
                    .unwrap_or("")
                    .to_string(),
            });
        }
    }
    out.sort_by(|a, b| a.name.cmp(&b.name));
    out.dedup_by(|a, b| a.name == b.name);
    out
}

fn peers(refresh: bool) -> Vec<Peer> {
    let mut guard = CACHE.lock().unwrap_or_else(|e| e.into_inner());
    if !refresh {
        if let Some((at, cached)) = guard.as_ref() {
            if at.elapsed() < CACHE_TTL {
                return cached.clone();
            }
        }
    }
    let fresh = scan();
    *guard = Some((Instant::now(), fresh.clone()));
    fresh
}

fn find(name: &str) -> Result<Peer, String> {
    let name = name.trim();
    if name.is_empty() {
        return Err("name an arena — `arena_list` says which ones are here".into());
    }
    let all = peers(false);
    all.iter()
        .find(|p| p.name.eq_ignore_ascii_case(name))
        .or_else(|| all.iter().find(|p| p.name.to_lowercase().contains(&name.to_lowercase())))
        .cloned()
        .ok_or_else(|| {
            format!(
                "no arena called {name} on this fleet — there is {}",
                if all.is_empty() {
                    "none at all".to_string()
                } else {
                    all.iter().map(|p| p.name.as_str()).collect::<Vec<_>>().join(", ")
                }
            )
        })
}

/// Every arena, with whether it is actually answering right now — an arena
/// that is installed but asleep is a different problem from one that is absent.
///
/// The probes run concurrently and on their own short deadline. This backs
/// `build_info`, the tool an agent is told to call first, and a single arena
/// that accepts the connection and then says nothing must not be able to hold
/// that answer open for the full request timeout.
pub async fn registry(refresh: bool) -> Value {
    let list = peers(refresh);
    let handles: Vec<_> = list
        .iter()
        .map(|p| {
            let url = p.mcp.clone();
            tokio::spawn(async move {
                tokio::time::timeout(PROBE_TIMEOUT, rpc(&url, "tools/list", json!({})))
                    .await
                    .map(|r| r.is_ok())
                    .unwrap_or(false)
            })
        })
        .collect();
    let mut reachable = Vec::with_capacity(handles.len());
    for h in handles {
        // A probe that fell over tells us nothing good about the arena, and
        // one missing answer must not shift every later arena's verdict along
        // by one — so a failed join is simply "not answering".
        reachable.push(h.await.unwrap_or(false));
    }

    list.iter()
        .zip(reachable)
        .map(|(p, up)| {
            let mut v = p.to_json();
            v["reachable"] = json!(up);
            v["entered"] = json!(entry_for(&p.name).is_some());
            v
        })
        .collect::<Vec<_>>()
        .into()
}

const PROBE_TIMEOUT: Duration = Duration::from_secs(3);

// ── talking to an arena ──────────────────────────────────────────────

/// One JSON-RPC round trip to a peer's MCP endpoint.
async fn rpc(url: &str, method: &str, params: Value) -> Result<Value, String> {
    let body = json!({ "jsonrpc": "2.0", "id": 1, "method": method, "params": params });
    let resp = client()
        .post(url)
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("{url} unreachable: {e} — is that arena running?"))?;
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    if !status.is_success() {
        return Err(format!("{url} answered {status}: {}", text.chars().take(300).collect::<String>()));
    }
    let v: Value = serde_json::from_str(&text)
        .map_err(|e| format!("{url} returned unreadable JSON-RPC ({e}): {}", text.chars().take(200).collect::<String>()))?;
    if let Some(err) = v.get("error") {
        let msg = err.get("message").and_then(|m| m.as_str()).unwrap_or("");
        return Err(format!("{url} refused {method}: {msg}"));
    }
    Ok(v.get("result").cloned().unwrap_or(Value::Null))
}

/// Call one tool on one arena, unwrapping the MCP envelope so the caller sees
/// the arena's own answer and the arena's own error text, not our packaging.
pub async fn call(arena: &str, tool: &str, args: Value) -> Result<Value, String> {
    let peer = find(arena)?;
    call_peer(&peer, tool, args).await
}

async fn call_peer(peer: &Peer, tool: &str, args: Value) -> Result<Value, String> {
    let result = rpc(&peer.mcp, "tools/call", json!({ "name": tool, "arguments": args })).await?;
    if result.get("isError").and_then(|e| e.as_bool()).unwrap_or(false) {
        let msg = result
            .get("content")
            .and_then(|c| c.as_array())
            .and_then(|a| a.first())
            .and_then(|c| c.get("text"))
            .and_then(|t| t.as_str())
            .unwrap_or("the arena refused, without saying why");
        return Err(format!("{}: {}", peer.name, msg));
    }
    if let Some(structured) = result.get("structuredContent") {
        return Ok(structured.clone());
    }
    // Older arenas answer with text only; hand back parsed JSON when it is JSON.
    let text = result
        .get("content")
        .and_then(|c| c.as_array())
        .and_then(|a| a.first())
        .and_then(|c| c.get("text"))
        .and_then(|t| t.as_str())
        .unwrap_or("");
    Ok(serde_json::from_str(text).unwrap_or_else(|_| json!({ "text": text })))
}

pub async fn peer_tools(arena: &str) -> Result<Value, String> {
    let peer = find(arena)?;
    let result = rpc(&peer.mcp, "tools/list", json!({})).await?;
    Ok(json!({
        "arena": peer.name,
        "mcp": peer.mcp,
        "tools": result.get("tools").cloned().unwrap_or(Value::Array(vec![]))
    }))
}

async fn peer_tool_names(peer: &Peer) -> Vec<String> {
    rpc(&peer.mcp, "tools/list", json!({}))
        .await
        .ok()
        .and_then(|r| r.get("tools").cloned())
        .and_then(|t| t.as_array().cloned())
        .map(|a| {
            a.iter()
                .filter_map(|t| t.get("name").and_then(|n| n.as_str()).map(str::to_string))
                .collect()
        })
        .unwrap_or_default()
}

/// Run a match. The tool is `run_match` in every arena/1.0 module, but what it
/// calls the two arguments is not: games seat `players` at a `game`, coding
/// tasks put `agents` on a `task`. Read the schema and fill in what it asks
/// for rather than guessing.
pub async fn run_match(args: &Value) -> Result<Value, String> {
    let peer = find(args.get("arena").and_then(|a| a.as_str()).unwrap_or(""))?;
    let subject = args.get("subject").and_then(|s| s.as_str()).unwrap_or("").trim().to_string();
    let entrants: Vec<String> = match args.get("entrants") {
        Some(Value::Array(a)) => a.iter().filter_map(|v| v.as_str().map(str::to_string)).collect(),
        Some(Value::String(s)) => s.split(',').map(|p| p.trim().to_string()).filter(|p| !p.is_empty()).collect(),
        _ => vec![],
    };
    if subject.is_empty() || entrants.is_empty() {
        return Err("arena_match needs `subject` and at least one entrant".into());
    }

    let schema = rpc(&peer.mcp, "tools/list", json!({}))
        .await?
        .get("tools")
        .and_then(|t| t.as_array().cloned())
        .unwrap_or_default()
        .into_iter()
        .find(|t| t.get("name").and_then(|n| n.as_str()) == Some("run_match"))
        .and_then(|t| t.get("inputSchema").cloned())
        .unwrap_or_else(|| json!({}));
    let props = schema.get("properties").cloned().unwrap_or_else(|| json!({}));
    let has = |k: &str| props.get(k).is_some();

    let subject_key = ["game", "task", "subject"].into_iter().find(|k| has(k)).unwrap_or("task");
    let entrants_key = ["players", "agents", "entrants"].into_iter().find(|k| has(k)).unwrap_or("agents");

    let mut call_args = json!({ subject_key: subject, entrants_key: entrants });
    for pass in ["seed", "turns", "timeout_ms"] {
        if let Some(v) = args.get(pass) {
            if has(pass) {
                call_args[pass] = v.clone();
            }
        }
    }
    call_peer(&peer, "run_match", call_args).await
}

// ── being a competitor ───────────────────────────────────────────────

fn state_path() -> PathBuf {
    home().join(".mod").join("build-fork").join("arena.json")
}

#[derive(Clone, Debug, Default, serde::Serialize, serde::Deserialize)]
pub struct Enrollment {
    #[serde(default)]
    pub enabled: bool,
    /// The shared secret an arena must present on /arena/solve and /arena/play.
    #[serde(default)]
    pub key: String,
    #[serde(default)]
    pub callback_base: String,
    #[serde(default)]
    pub model: String,
    #[serde(default)]
    pub entries: Vec<Entry>,
}

#[derive(Clone, Debug, serde::Serialize, serde::Deserialize)]
pub struct Entry {
    pub arena: String,
    pub name: String,
    /// "solve" (a program per task) or "play" (a move per view).
    pub role: String,
    pub url: String,
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub at: i64,
}

fn read_state() -> Enrollment {
    std::fs::read_to_string(state_path())
        .ok()
        .and_then(|raw| serde_json::from_str(&raw).ok())
        .unwrap_or_default()
}

fn write_state(e: &Enrollment) -> Result<(), String> {
    let path = state_path();
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|err| format!("cannot create {}: {err}", dir.display()))?;
    }
    let body = serde_json::to_string_pretty(e).map_err(|err| err.to_string())?;
    std::fs::write(&path, body).map_err(|err| format!("cannot write {}: {err}", path.display()))?;
    // The key in here is a bearer credential for spending money on this box.
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600));
    }
    Ok(())
}

fn entry_for(arena: &str) -> Option<Entry> {
    read_state()
        .entries
        .into_iter()
        .find(|e| e.arena.eq_ignore_ascii_case(arena))
}

fn mint_key() -> String {
    use rand::RngCore;
    let mut bytes = [0u8; 32];
    rand::thread_rng().fill_bytes(&mut bytes);
    hex::encode(bytes)
}

/// Where this console is entered. Never returns the key.
pub fn status() -> Value {
    let st = read_state();
    json!({
        "enabled": st.enabled,
        "callback_base": st.callback_base,
        "model": st.model,
        "keyed": !st.key.is_empty(),
        "entries": st.entries,
        "endpoints": {
            "solve": "POST /arena/solve — {task|prompt, language?, mode?} → {code, language}",
            "play": "POST /arena/play — {view, seat?, prompt?} → {move}",
        },
        "note": "Both endpoints are refused unless `enabled` and the caller presents the shared \
                 key as x-arena-key. Each accepted call runs a real agent job.",
    })
}

pub fn caller_address(ctx: &crate::mcp::Ctx) -> Result<String, String> {
    if local_mode() {
        return Ok(auth::get_owner_address().unwrap_or_else(|| "local".into()));
    }
    let header = ctx
        .token
        .as_deref()
        .ok_or("this needs a bearer token — sign in, or set BUILD_FORK_TOKEN for stdio")?;
    auth::extract_address_from_header(header)
}

fn require_owner(ctx: &crate::mcp::Ctx) -> Result<String, String> {
    let addr = caller_address(ctx)?;
    if local_mode() || auth::is_owner(&addr) {
        Ok(addr)
    } else {
        Err(format!("{addr} is not the owner — entering arenas spends money on this box"))
    }
}

pub fn require_trusted(ctx: &crate::mcp::Ctx) -> Result<String, String> {
    let addr = caller_address(ctx)?;
    if local_mode() || auth::is_trusted(&addr) {
        Ok(addr)
    } else {
        Err(format!("{addr} is not a trusted editor of this console"))
    }
}

/// Enter this console in an arena as an `http` competitor pointed back at us.
pub async fn enter(args: &Value, ctx: &crate::mcp::Ctx) -> Result<Value, String> {
    let owner = require_owner(ctx)?;
    let peer = find(args.get("arena").and_then(|a| a.as_str()).unwrap_or(""))?;

    let names = peer_tool_names(&peer).await;
    if names.is_empty() {
        return Err(format!("{} is not answering — start it, then enter again", peer.name));
    }
    let requested = args.get("role").and_then(|r| r.as_str()).unwrap_or("auto");
    let (enter_tool, implied_role) = ENTER_TOOLS
        .into_iter()
        .find(|(tool, role)| names.iter().any(|n| n == tool) && (requested == "auto" || requested == *role))
        .ok_or_else(|| {
            format!(
                "{} has no entry tool this console understands (it offers {}) — use arena_call directly",
                peer.name,
                names.join(", ")
            )
        })?;
    let role = if requested == "auto" { implied_role } else { requested };
    if !peer.kinds.is_empty() && !peer.kinds.iter().any(|k| k == "http") {
        return Err(format!(
            "{} does not seat `http` competitors (it seats {}) — this console can only be entered as one",
            peer.name,
            peer.kinds.join(", ")
        ));
    }

    let mut st = read_state();
    if st.key.is_empty() {
        st.key = mint_key();
    }
    let callback_base = args
        .get("callback_base")
        .and_then(|c| c.as_str())
        .map(str::to_string)
        .filter(|c| !c.trim().is_empty())
        .or_else(|| std::env::var("ARENA_CALLBACK_BASE").ok())
        .unwrap_or_else(crate::mcp::base);
    let callback_base = callback_base.trim_end_matches('/').to_string();
    let url = format!("{callback_base}/arena/{}", if role == "play" { "play" } else { "solve" });
    let name = args
        .get("name")
        .and_then(|n| n.as_str())
        .map(str::trim)
        .filter(|n| !n.is_empty())
        .unwrap_or("build-fork")
        .to_string();
    if let Some(m) = args.get("model").and_then(|m| m.as_str()) {
        if !m.trim().is_empty() {
            st.model = m.trim().to_string();
        }
    }

    let entered = call_peer(
        &peer,
        enter_tool,
        json!({
            "name": name,
            "kind": "http",
            "config": {
                "url": url,
                "headers": { "x-arena-key": st.key },
                "field": if role == "play" { "move" } else { "code" },
            },
            "owner": owner,
            "note": "the build console — a coding agent behind an HTTP endpoint",
        }),
    )
    .await?;

    let id = ["id", "agent_id", "player_id"]
        .into_iter()
        .find_map(|k| entered.get(k).and_then(|v| v.as_str()))
        .or_else(|| {
            entered
                .get("agent")
                .or_else(|| entered.get("player"))
                .and_then(|a| a.get("id"))
                .and_then(|v| v.as_str())
        })
        .unwrap_or("")
        .to_string();

    st.enabled = true;
    st.callback_base = callback_base;
    st.entries.retain(|e| !e.arena.eq_ignore_ascii_case(&peer.name));
    st.entries.push(Entry {
        arena: peer.name.clone(),
        name: name.clone(),
        role: role.to_string(),
        url: url.clone(),
        id: id.clone(),
        at: chrono::Utc::now().timestamp(),
    });
    write_state(&st)?;

    Ok(json!({
        "entered": true,
        "arena": peer.name,
        "name": name,
        "role": role,
        "as": "http",
        "callback": url,
        "id": id,
        "arena_said": entered,
        "warning": "This console will now run a real agent job every time that arena calls it.",
    }))
}

/// Leave one arena, or all of them.
pub async fn withdraw(args: &Value, ctx: &crate::mcp::Ctx) -> Result<Value, String> {
    require_owner(ctx)?;
    let target = args.get("arena").and_then(|a| a.as_str()).unwrap_or("").trim().to_string();
    let mut st = read_state();
    let going: Vec<Entry> = if target.is_empty() {
        std::mem::take(&mut st.entries)
    } else {
        let (out, keep): (Vec<Entry>, Vec<Entry>) = st
            .entries
            .into_iter()
            .partition(|e| e.arena.eq_ignore_ascii_case(&target));
        st.entries = keep;
        out
    };
    if going.is_empty() {
        return Err(format!(
            "not entered in {}",
            if target.is_empty() { "any arena".into() } else { target }
        ));
    }

    // Tell each arena, but do not let an arena that is down keep us enrolled:
    // the local switch is what actually stops us answering.
    let mut told = Vec::new();
    for e in &going {
        let said = match find(&e.arena) {
            Ok(peer) => {
                let names = peer_tool_names(&peer).await;
                let tool = ["remove_agent", "remove_player"]
                    .into_iter()
                    .find(|t| names.iter().any(|n| n == t));
                match tool {
                    Some(t) => {
                        let arg = if t == "remove_agent" { "agent" } else { "player" };
                        let who = if e.id.is_empty() { e.name.clone() } else { e.id.clone() };
                        call_peer(&peer, t, json!({ arg: who })).await.err()
                    }
                    None => Some(format!("{} has no withdrawal tool", peer.name)),
                }
            }
            Err(err) => Some(err),
        };
        told.push(json!({ "arena": e.arena, "problem": said }));
    }

    st.enabled = !st.entries.is_empty();
    write_state(&st)?;
    Ok(json!({
        "withdrew": going.iter().map(|e| e.arena.clone()).collect::<Vec<_>>(),
        "still_entered": st.entries,
        "answering": st.enabled,
        "arenas": told,
    }))
}

// ── the endpoints an arena calls ─────────────────────────────────────

fn authorise(headers: &HeaderMap) -> Result<Enrollment, (StatusCode, String)> {
    let st = read_state();
    if !st.enabled || st.key.is_empty() {
        return Err((
            StatusCode::FORBIDDEN,
            "this console is not entered in any arena — the owner has to run arena_enter first".into(),
        ));
    }
    let given = headers
        .get("x-arena-key")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    // Length-independent compare: the key is a bearer credential for spend.
    let ok = given.len() == st.key.len()
        && given
            .bytes()
            .zip(st.key.bytes())
            .fold(0u8, |acc, (a, b)| acc | (a ^ b))
            == 0;
    if !ok {
        return Err((StatusCode::UNAUTHORIZED, "bad or missing x-arena-key".into()));
    }
    Ok(st)
}

/// The first fenced block, or the whole thing if it was never fenced.
fn extract_code(text: &str) -> (String, String) {
    let Some(open) = text.find("```") else {
        return (text.trim().to_string(), String::new());
    };
    let after = &text[open + 3..];
    let (lang, body) = match after.find('\n') {
        Some(nl) => (after[..nl].trim().to_string(), &after[nl + 1..]),
        None => (String::new(), after),
    };
    let code = match body.find("```") {
        Some(close) => &body[..close],
        None => body,
    };
    (code.trim_end().to_string(), lang)
}

/// The last non-empty line, stripped of the decoration a model reaches for.
fn extract_move(text: &str) -> String {
    text.lines()
        .rev()
        .map(|l| l.trim().trim_matches(|c: char| c == '`' || c == '"' || c == '*' || c == '.'))
        .map(str::trim)
        .find(|l| !l.is_empty())
        .unwrap_or("")
        .to_string()
}

/// Run one agent job to completion and hand back its output.
async fn run_once(
    mgr: &Arc<ClaudeJobManager>,
    st: &Enrollment,
    prompt: String,
    tag: &str,
) -> Result<(String, String), String> {
    let work_dir = home().join(".mod").join("build-fork").join("arena").join(tag);
    std::fs::create_dir_all(&work_dir)
        .map_err(|e| format!("cannot make a workspace at {}: {e}", work_dir.display()))?;

    let model = if st.model.trim().is_empty() { "claude-fable-5".to_string() } else { st.model.clone() };
    let job = mgr
        .submit(SubmitRequest {
            prompt,
            model,
            work_dir: Some(work_dir.to_string_lossy().to_string()),
            module_name: None,
            creation_mode: None,
            fork_source: None,
            anchor_dir: None,
            images: None,
            agent_type: None,
            system_prompt: None,
            agent: None,
            agent_params: None,
            replace_job_id: None,
            // Attributed to the owner: they are the one paying, and they are
            // the one who chose to be entered.
            user_address: auth::get_owner_address(),
        })
        .await;

    let budget = Duration::from_millis(
        std::env::var("ARENA_JOB_TIMEOUT_MS")
            .ok()
            .and_then(|v| v.parse::<u64>().ok())
            .unwrap_or(600_000)
            .clamp(10_000, 3_600_000),
    );
    let deadline = Instant::now() + budget;
    loop {
        let Some(cur) = mgr.get_job(&job.id) else {
            return Err(format!("task {} vanished", job.id));
        };
        match cur.status.to_string().as_str() {
            "completed" => return Ok((cur.output, cur.id)),
            "failed" | "cancelled" => {
                return Err(format!(
                    "task {} {}: {}",
                    cur.id,
                    cur.status,
                    cur.error.unwrap_or_default()
                ))
            }
            _ => {}
        }
        if Instant::now() >= deadline {
            let _ = mgr.cancel_job(&job.id).await;
            return Err(format!("task {} ran past {:?} and was cancelled", job.id, budget));
        }
        tokio::time::sleep(Duration::from_millis(1_000)).await;
    }
}

#[derive(Deserialize)]
pub struct SolveRequest {
    #[serde(default)]
    task: String,
    #[serde(default)]
    prompt: String,
    #[serde(default)]
    language: String,
    #[serde(default)]
    mode: String,
}

/// What a coding arena calls: a task in, a program out.
pub async fn solve(
    State(mgr): State<Arc<ClaudeJobManager>>,
    headers: HeaderMap,
    Json(req): Json<SolveRequest>,
) -> impl IntoResponse {
    let st = match authorise(&headers) {
        Ok(st) => st,
        Err((code, msg)) => return (code, Json(json!({ "error": msg }))).into_response(),
    };
    let brief = if req.task.trim().is_empty() { req.prompt.trim() } else { req.task.trim() };
    if brief.is_empty() {
        return (StatusCode::BAD_REQUEST, Json(json!({ "error": "no task" }))).into_response();
    }
    let language = if req.language.trim().is_empty() { "python".to_string() } else { req.language.trim().to_string() };

    let prompt = format!(
        "You are competing in a coding arena. Solve the task below.\n\n\
         Answer with the complete program and nothing else: one fenced {language} code block, no \
         prose before or after it, no explanation, no tests of your own. It will be run exactly as \
         written against hidden cases, reading stdin and writing stdout unless the task says \
         otherwise.{}\n\n--- TASK ---\n{brief}\n",
        if req.mode.trim().is_empty() { String::new() } else { format!(" Mode: {}.", req.mode.trim()) },
    );

    match run_once(&mgr, &st, prompt, "solve").await {
        Ok((output, job)) => {
            let (code, hint) = extract_code(&output);
            if code.trim().is_empty() {
                return (
                    StatusCode::BAD_GATEWAY,
                    Json(json!({ "error": "the agent returned no code", "job": job })),
                )
                    .into_response();
            }
            Json(json!({
                "code": code,
                "language": if hint.is_empty() { language } else { hint },
                "job": job,
            }))
            .into_response()
        }
        Err(e) => (StatusCode::BAD_GATEWAY, Json(json!({ "error": e }))).into_response(),
    }
}

#[derive(Deserialize)]
pub struct PlayRequest {
    #[serde(default)]
    view: String,
    #[serde(default)]
    seat: u32,
    #[serde(default)]
    prompt: String,
}

/// What a game arena calls: a view in, a move out.
///
/// One agent job per move, which is slow — a game arena entering this console
/// should expect seconds per turn, not milliseconds.
pub async fn play(
    State(mgr): State<Arc<ClaudeJobManager>>,
    headers: HeaderMap,
    Json(req): Json<PlayRequest>,
) -> impl IntoResponse {
    let st = match authorise(&headers) {
        Ok(st) => st,
        Err((code, msg)) => return (code, Json(json!({ "error": msg }))).into_response(),
    };
    let view = if req.view.trim().is_empty() { req.prompt.trim() } else { req.view.trim() };
    if view.is_empty() {
        return (StatusCode::BAD_REQUEST, Json(json!({ "error": "no view" }))).into_response();
    }

    let prompt = format!(
        "You are playing seat {} in a game. This is everything you can see:\n\n{view}\n\n\
         Reply with your move and nothing else — one line, no explanation, no code fence, no \
         punctuation around it. An illegal move is scored against you, so read the position before \
         you answer.",
        req.seat
    );

    match run_once(&mgr, &st, prompt, "play").await {
        Ok((output, job)) => {
            let mv = extract_move(&output);
            if mv.is_empty() {
                return (
                    StatusCode::BAD_GATEWAY,
                    Json(json!({ "error": "the agent returned no move", "job": job })),
                )
                    .into_response();
            }
            Json(json!({ "move": mv, "job": job })).into_response()
        }
        Err(e) => (StatusCode::BAD_GATEWAY, Json(json!({ "error": e }))).into_response(),
    }
}

/// GET /arena — public: where we are entered, and what we are. No key.
pub async fn status_handler() -> impl IntoResponse {
    Json(json!({
        "competitor": status(),
        "arenas": peers(false).iter().map(|p| p.to_json()).collect::<Vec<_>>(),
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_fenced_block_is_the_answer() {
        let (code, lang) = extract_code("Here you go:\n```python\nprint(1)\n```\nHope that helps");
        assert_eq!(code, "print(1)");
        assert_eq!(lang, "python");
    }

    #[test]
    fn an_unfenced_answer_is_still_an_answer() {
        let (code, lang) = extract_code("print(1)\n");
        assert_eq!(code, "print(1)");
        assert!(lang.is_empty());
    }

    #[test]
    fn an_unterminated_fence_does_not_lose_the_code() {
        let (code, _) = extract_code("```python\nprint(1)\n");
        assert_eq!(code, "print(1)");
    }

    #[test]
    fn the_move_is_the_last_line_undecorated() {
        assert_eq!(extract_move("I'll play the centre.\n\n`e4`\n"), "e4");
        assert_eq!(extract_move("rock"), "rock");
        assert_eq!(extract_move(""), "");
    }

    #[test]
    fn an_arena_is_found_by_prefix_or_not_at_all() {
        // find() reads the live fleet, so only assert the shape of a miss.
        let err = find("").unwrap_err();
        assert!(err.contains("name an arena"), "{err}");
    }

    #[test]
    fn status_never_leaks_the_key() {
        let v = status();
        let dumped = serde_json::to_string(&v).unwrap();
        assert!(!dumped.contains("\"key\""), "{dumped}");
        assert!(v.get("keyed").is_some());
    }
}
