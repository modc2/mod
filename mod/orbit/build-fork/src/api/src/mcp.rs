//! MCP server core — JSON-RPC 2.0, shared by the Streamable HTTP endpoint
//! (/mcp) and stdio mode (--stdio).
//!
//! Every tool here is fulfilled by calling this server's own REST route over
//! loopback, carrying the caller's bearer token. That is deliberate: build's
//! authorisation is deep (owner gates, sudo signatures, per-caller edit
//! scopes, workspace confinement, the vault) and it all lives inside the
//! handlers. Re-implementing any of it for MCP would mean two definitions of
//! who may do what, and the second one would eventually be wrong. So a
//! capability is defined exactly once: what an agent can do over MCP is what a
//! browser can do over HTTP, always — same gate, same error, same shape.
//!
//! The exception is the `arena_*` family, which talks OUT to sibling
//! arena/1.0 modules rather than in to ourselves; those live in arena.rs.

use crate::arena;
use serde_json::{json, Map, Value};
use std::sync::OnceLock;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

pub const PROTOCOL_VERSION: &str = "2025-06-18";
pub const SERVER_NAME: &str = "build-fork";
pub const SERVER_VERSION: &str = env!("CARGO_PKG_VERSION");

/// Where our own REST surface answers, so tools can call back in.
static BASE: OnceLock<String> = OnceLock::new();

pub fn set_base(url: String) {
    let _ = BASE.set(url);
}

pub fn base() -> String {
    BASE.get()
        .cloned()
        .or_else(|| std::env::var("BUILD_FORK_API_URL").ok())
        .unwrap_or_else(|| "http://127.0.0.1:8894".into())
}

/// Who is asking. Over HTTP this is the request's own Authorization header;
/// over stdio it is $BUILD_FORK_TOKEN. Either way we never inspect it — we forward
/// it and let the REST handler decide, which is the whole point.
#[derive(Clone, Default, Debug)]
pub struct Ctx {
    pub token: Option<String>,
    /// An x-sudo signature, for the handful of actions that demand one.
    pub sudo: Option<String>,
}

impl Ctx {
    pub fn from_headers(h: &axum::http::HeaderMap) -> Self {
        let get = |k: &str| h.get(k).and_then(|v| v.to_str().ok()).map(str::to_string);
        Ctx { token: get("authorization"), sudo: get("x-sudo") }
    }

    pub fn from_env() -> Self {
        let tok = std::env::var("BUILD_FORK_TOKEN").ok().filter(|t| !t.trim().is_empty());
        Ctx {
            token: tok.map(|t| {
                if t.to_ascii_lowercase().starts_with("bearer ") { t } else { format!("Bearer {t}") }
            }),
            sudo: std::env::var("BUILD_FORK_SUDO").ok().filter(|s| !s.trim().is_empty()),
        }
    }
}

// ── The tool table ───────────────────────────────────────────────────
//
// A tool is a name, a description an agent can act on without reading our
// source, an input schema, and how it is fulfilled. `Rest` renders the path
// template from the arguments; whatever is left over becomes the query string
// for a GET and the JSON body for anything else.

pub enum Fulfil {
    /// method, path template — `{arg}` segments are filled from the arguments.
    Rest(&'static str, &'static str),
    /// Handled in this module (composition, or a call out to a sibling arena).
    Local,
}

pub struct Tool {
    pub name: &'static str,
    pub description: &'static str,
    pub fulfil: Fulfil,
    /// What the REST gate will demand: "public", "auth", "owner". Advisory —
    /// it is documentation for the agent, not the enforcement point.
    pub access: &'static str,
    pub schema: fn() -> Value,
}

macro_rules! tool {
    ($name:literal, $access:literal, $desc:literal, $method:literal $path:literal, $schema:expr) => {
        Tool {
            name: $name,
            description: $desc,
            access: $access,
            fulfil: Fulfil::Rest($method, $path),
            schema: || $schema,
        }
    };
    ($name:literal, $access:literal, $desc:literal, local, $schema:expr) => {
        Tool {
            name: $name,
            description: $desc,
            access: $access,
            fulfil: Fulfil::Local,
            schema: || $schema,
        }
    };
}

fn obj(props: Value, required: &[&str]) -> Value {
    json!({
        "type": "object",
        "properties": props,
        "required": required,
    })
}

fn empty() -> Value {
    json!({ "type": "object", "properties": {} })
}

pub fn tools() -> Vec<Tool> {
    vec![
        // ── the console itself ───────────────────────────────────────
        tool!(
            "build_info",
            "public",
            "What this console is, what it is running, and what it is connected to: version, \
             the agents it can drive, live task counts, the modules it can see, and every \
             arena/1.0 module on this fleet it can enter. Read this first.",
            local,
            empty()
        ),
        tool!(
            "whoami",
            "public",
            "Who the caller is to this server — the address behind the bearer token and the role it \
             carries (owner, editor, guest, anonymous). Determines which of these tools will \
             actually go through, so it is worth asking before a long sequence fails halfway.",
            local,
            json!({
                "type": "object",
                "properties": { "address": { "type": "string", "description": "Ask about someone else; defaults to your own token" } }
            })
        ),
        tool!(
            "system_status",
            "public",
            "Host resources and per-app usage: CPU, memory, disk, and which modules are awake. \
             What to check before starting work that needs headroom.",
            "GET" "/system",
            empty()
        ),
        // ── tasks (the agent job engine) ─────────────────────────────
        tool!(
            "submit_task",
            "auth",
            "Hand a coding task to an agent and get a job id back immediately — the work runs in \
             the background. Point it at a module by `module_name` or at any path by `work_dir`. \
             Poll `get_task` for the result, or `wait_task` to block until it lands. This is the \
             one tool that spends money.",
            "POST" "/jobs",
            obj(
                json!({
                    "prompt": { "type": "string", "description": "What to do, in full — the agent sees nothing else" },
                    "model": { "type": "string", "description": "claude-fable-5 (default), claude-opus-5, claude-sonnet-5, claude-haiku-4-5-20251001" },
                    "module_name": { "type": "string", "description": "An orbit module to work in — resolved to ~/mod/mod/orbit/{name}" },
                    "work_dir": { "type": "string", "description": "Absolute path to work in, when it is not a module" },
                    "creation_mode": { "type": "string", "enum": ["new", "fork"], "description": "Create module_name fresh, or fork it from fork_source" },
                    "fork_source": { "type": "string", "description": "Module to fork when creation_mode is fork" },
                    "system_prompt": { "type": "string" },
                    "agent": { "type": "string", "enum": ["claude", "codex", "agent"], "description": "Which backend runs it; default claude" },
                    "agent_params": { "type": "object", "description": "Backend-specific settings, passed through opaquely" }
                }),
                &["prompt"]
            )
        ),
        tool!(
            "list_tasks",
            "public",
            "Every task this console has run, newest first. Tasks are a public ledger by design — \
             prompt, output, cost and the localfs CID of the finished bundle. Sealed tasks come \
             back masked unless you are their author with the vault open.",
            "GET" "/jobs",
            json!({
                "type": "object",
                "properties": {
                    "limit": { "type": "integer", "description": "How many to return" },
                    "status": { "type": "string", "enum": ["pending", "running", "completed", "failed", "cancelled"] },
                    "address": { "type": "string", "description": "Only this author's tasks" }
                }
            })
        ),
        tool!(
            "get_task",
            "public",
            "One task in full: status, the whole output, cost, tokens, duration, and its CID once \
             it has one.",
            "GET" "/jobs/{id}",
            obj(json!({ "id": { "type": "string" } }), &["id"])
        ),
        tool!(
            "wait_task",
            "public",
            "Block until a task reaches a terminal state and return it. The polling loop you would \
             otherwise write yourself; give up after `timeout_ms` and say so rather than hanging.",
            local,
            obj(
                json!({
                    "id": { "type": "string" },
                    "timeout_ms": { "type": "integer", "description": "Default 600000 (10 min), max 3600000" }
                }),
                &["id"]
            )
        ),
        tool!(
            "steer_task",
            "auth",
            "Say something to a task that is still running — it arrives on the agent's stdin and \
             changes what it does next. The way to correct a job instead of killing it.",
            "POST" "/jobs/{id}/message",
            obj(json!({ "id": { "type": "string" }, "message": { "type": "string" } }), &["id", "message"])
        ),
        tool!(
            "cancel_task",
            "auth",
            "Stop a running task. Whatever it already wrote to disk stays written.",
            "POST" "/jobs/{id}/cancel",
            obj(json!({ "id": { "type": "string" } }), &["id"])
        ),
        // ── modules ──────────────────────────────────────────────────
        tool!(
            "list_modules",
            "public",
            "Every module on this fleet — orbit and core — with its config, ports, whether it is \
             running and its registry CID. The map of what exists before you change any of it.",
            "GET" "/modules",
            json!({
                "type": "object",
                "properties": {
                    "q": { "type": "string", "description": "Free-text filter" },
                    "anchor": { "type": "string", "description": "Root to scan; defaults to ~/mod" }
                }
            })
        ),
        tool!(
            "get_module",
            "public",
            "One module's config.json — name, ports, routes, declared functions and endpoints. \
             The contract another module programs against.",
            "GET" "/modules/{module}/config",
            obj(json!({ "module": { "type": "string" } }), &["module"])
        ),
        tool!(
            "module_process",
            "owner",
            "Read or move a module's processes: status, stop, start, restart, optionally just its \
             api or just its app. The backend (pm2, systemd, plain) is resolved per module. \
             Anything but `status`, on any module but build, needs a sudo signature bound to \
             process:{module} — pass it as `sudo`.",
            "POST" "/modules/{module}/process",
            obj(
                json!({
                    "module": { "type": "string" },
                    "action": { "type": "string", "enum": ["status", "stop", "start", "restart"] },
                    "target": { "type": "string", "enum": ["api", "app"] }
                }),
                &["module", "action"]
            )
        ),
        tool!(
            "snapshot_module",
            "auth",
            "Freeze a module's tree into content-addressed storage and return the CID. The unit of \
             versioning here: every restore, fork and merge request is pinned to one of these.",
            "POST" "/modules/{module}/snapshot",
            obj(
                json!({
                    "module": { "type": "string" },
                    "message": { "type": "string", "description": "What changed" }
                }),
                &["module"]
            )
        ),
        tool!(
            "module_versions",
            "public",
            "A module's snapshot history — CID, message and time per entry. What `restore_module` \
             can go back to; each entry carries `restorable` (is its blob still in the store?) and \
             the response says whether the caller holds revert authority at all.",
            "GET" "/modules/{module}/versions",
            obj(json!({ "module": { "type": "string" } }), &["module"])
        ),
        tool!(
            "restore_module",
            "owner",
            "Put a module's tree back to a snapshot CID. Destructive: the current tree is replaced \
             (the state you replace is pinned first, so a revert is itself revertible). Reverting is \
             the OWNER's power alone — editors, invite holders and sudo delegates are refused even \
             though they may edit — and every revert needs the owner's own sudo signature.",
            "POST" "/modules/{module}/restore",
            obj(json!({ "module": { "type": "string" }, "cid": { "type": "string" } }), &["module", "cid"])
        ),
        tool!(
            "undo_module",
            "owner",
            "Undo the last change to a module: reverts to the previous state in its version log, no \
             CID needed. `steps` walks further back (2 = the change before that). Owner-only, same \
             gate as restore_module.",
            "POST" "/modules/{module}/undo",
            obj(
                json!({
                    "module": { "type": "string" },
                    "steps": { "type": "integer", "description": "How many changes to walk back (default 1)" }
                }),
                &["module"]
            )
        ),
        // ── files ────────────────────────────────────────────────────
        tool!(
            "list_files",
            "auth",
            "Walk a directory tree. Default-deny: untrusted callers only ever see inside their own \
             workspace, trusted ones see the orbit.",
            "GET" "/files/tree",
            json!({
                "type": "object",
                "properties": {
                    "path": { "type": "string" },
                    "depth": { "type": "integer" }
                }
            })
        ),
        tool!(
            "read_file",
            "auth",
            "Read one file, subject to the same confinement as list_files.",
            "GET" "/files/content",
            obj(json!({ "path": { "type": "string" } }), &["path"])
        ),
        tool!(
            "write_file",
            "auth",
            "Write one file. Untrusted callers are confined to their workspace; the owner and \
             whitelisted editors may write anywhere under ~/mod on the bearer token alone.",
            "POST" "/files/write",
            obj(
                json!({
                    "path": { "type": "string" },
                    "content": { "type": "string" }
                }),
                &["path", "content"]
            )
        ),
        tool!(
            "search_code",
            "auth",
            "grep a tree for a string or a regex and get the matching lines with their paths. How \
             to find the thing before you ask an agent to change it.",
            "GET" "/files/grep",
            obj(
                json!({
                    "path": { "type": "string" },
                    "query": { "type": "string" },
                    "regex": { "type": "boolean" },
                    "caseSensitive": { "type": "boolean" }
                }),
                &["path", "query"]
            )
        ),
        // ── merge requests ───────────────────────────────────────────
        tool!(
            "list_merge_requests",
            "public",
            "Open and closed merge requests, across every module or for one. Public, like tasks.",
            "GET" "/merge-requests",
            json!({ "type": "object", "properties": { "module": { "type": "string" }, "status": { "type": "string" } } })
        ),
        tool!(
            "fork_module",
            "auth",
            "Copy a module into your own sandboxed workspace, pinned to the snapshot CID you \
             forked from. Where an outside agent does its work before proposing it.",
            "POST" "/modules/{module}/mr-fork",
            obj(json!({ "module": { "type": "string" }, "refresh": { "type": "boolean" } }), &["module"])
        ),
        tool!(
            "open_merge_request",
            "auth",
            "Propose your fork back to a module: snapshots your workspace and files the request \
             against the base CID. Any signed-in caller may open one — merging is the owner's.",
            "POST" "/modules/{module}/merge-requests",
            obj(
                json!({
                    "module": { "type": "string" },
                    "title": { "type": "string" },
                    "description": { "type": "string" },
                    "head_cid": { "type": "string", "description": "Skip the fork and propose these bytes directly" }
                }),
                &["module", "title"]
            )
        ),
        tool!(
            "merge_request_diff",
            "auth",
            "What a merge request changes, base to head, plus which of those files have since moved \
             in the live tree — the conflict set.",
            "GET" "/merge-requests/{id}/diff",
            obj(json!({ "id": { "type": "string" } }), &["id"])
        ),
        tool!(
            "review_merge_request",
            "auth",
            "Comment on a merge request, optionally with a verdict. approve and request_changes move \
             its status and are reserved for trusted reviewers.",
            "POST" "/merge-requests/{id}/comment",
            obj(
                json!({
                    "id": { "type": "string" },
                    "body": { "type": "string" },
                    "action": { "type": "string", "enum": ["approve", "request_changes"] }
                }),
                &["id", "body"]
            )
        ),
        tool!(
            "merge_merge_request",
            "owner",
            "Merge it — which here means submitting an agent job that three-way merges base, head \
             and the current live tree semantically, after snapshotting live first. Returns the job; \
             its completion snapshot becomes the merged CID.",
            "POST" "/merge-requests/{id}/merge",
            obj(
                json!({
                    "id": { "type": "string" },
                    "instructions": { "type": "string" },
                    "model": { "type": "string" }
                }),
                &["id"]
            )
        ),
        // ── suggestions ──────────────────────────────────────────────
        tool!(
            "list_suggestions",
            "public",
            "The suggestion queue: what people have asked for, per module or across the orbit, with \
             their status and how many others seconded them. Public, like tasks.",
            "GET" "/suggestions",
            json!({
                "type": "object",
                "properties": {
                    "module": { "type": "string" },
                    "status": { "type": "string", "enum": ["open", "playing", "played", "play_failed", "rejected", "done"] },
                    "author": { "type": "string" }
                }
            })
        ),
        tool!(
            "suggest",
            "auth",
            "Suggest an edit to a module in words — no fork, no diff, no CID. The lightweight half of \
             contributing: you describe the change, and the module's admin decides whether to play it \
             as an edit on their own account.",
            "POST" "/modules/{module}/suggestions",
            obj(
                json!({
                    "module": { "type": "string" },
                    "title": { "type": "string", "description": "One line, ≤200 chars" },
                    "body": { "type": "string", "description": "What to change and why" }
                }),
                &["module", "title"]
            )
        ),
        tool!(
            "suggestion_thread",
            "public",
            "The entire discussion on one suggestion, oldest first. Lists carry only the tail of a \
             thread — anyone may comment, so threads grow — and this is the call that holds it whole. \
             Re-read it rather than assuming what you fetched earlier is still all of it.",
            "GET" "/suggestions/{id}/comments",
            obj(json!({ "id": { "type": "string" } }), &["id"])
        ),
        tool!(
            "comment_suggestion",
            "public",
            "Add to the discussion on a suggestion. Open to everyone — no wallet, no whitelist, no \
             association with the module; unsigned callers post under a stable anon: handle. Returns \
             the whole refreshed thread, because others write while you type. The discussion is part \
             of the brief the agent gets if the suggestion is ever played, marked as untrusted.",
            "POST" "/suggestions/{id}/comment",
            obj(json!({ "id": { "type": "string" }, "body": { "type": "string" } }), &["id", "body"])
        ),
        tool!(
            "play_suggestion",
            "owner",
            "Play a suggestion: submit it to the agent as an edit job in the live module, running on \
             the admin's own account. Admin-only. Returns the job, which snapshots and rolls back like \
             any other edit.",
            "POST" "/suggestions/{id}/play",
            obj(
                json!({
                    "id": { "type": "string" },
                    "instructions": { "type": "string", "description": "Admin guidance appended to the brief; outranks the suggestion" },
                    "prompt": { "type": "string", "description": "Replace the generated brief outright" },
                    "model": { "type": "string" }
                }),
                &["id"]
            )
        ),
        tool!(
            "triage_suggestion",
            "owner",
            "Move a suggestion to open, rejected or done. Admin-only. playing/played are set by playing \
             it, never by hand.",
            "POST" "/suggestions/{id}/status",
            obj(
                json!({
                    "id": { "type": "string" },
                    "status": { "type": "string", "enum": ["open", "rejected", "done"] },
                    "note": { "type": "string" }
                }),
                &["id", "status"]
            )
        ),
        // ── spend ────────────────────────────────────────────────────
        tool!(
            "costs",
            "public",
            "What has been spent through this console over a window, in total and per caller. The \
             same ledger the cost market settles against.",
            "GET" "/costs",
            json!({
                "type": "object",
                "properties": {
                    "days": { "type": "integer", "description": "Trailing window" },
                    "month": { "type": "string", "description": "YYYY-MM, UTC" }
                }
            })
        ),
        // ── arenas (outward) ─────────────────────────────────────────
        tool!(
            "arena_list",
            "public",
            "Every arena/1.0 module this console can reach, found by reading the fleet's configs \
             rather than from a hard-coded list — an arena added tomorrow shows up here with no \
             change to this module. Each entry says where its MCP endpoint is and what kinds of \
             competitor it seats.",
            local,
            json!({ "type": "object", "properties": { "refresh": { "type": "boolean" } } })
        ),
        tool!(
            "arena_tools",
            "public",
            "The tool list of one arena, straight from its own MCP endpoint. Read it before \
             `arena_call` — arenas share a protocol, not a vocabulary: the wasm arena enters \
             players, the coding arena enters agents.",
            local,
            obj(json!({ "arena": { "type": "string" } }), &["arena"])
        ),
        tool!(
            "arena_call",
            "auth",
            "Call any tool on any arena and get its result back — the general bridge. Everything \
             below is a shortcut for a call this could make by hand.",
            local,
            obj(
                json!({
                    "arena": { "type": "string", "description": "Arena module name, e.g. arena or openarena" },
                    "tool": { "type": "string" },
                    "arguments": { "type": "object" }
                }),
                &["arena", "tool"]
            )
        ),
        tool!(
            "arena_enter",
            "owner",
            "Enter THIS console as a competitor in an arena. It registers as an `http` entrant \
             pointed back at our own /arena/solve (coding arenas) or /arena/play (game arenas), \
             carrying a shared key minted here, and flips those endpoints on. From then on the \
             arena drives real agent jobs on this box — which spends money, so it is owner-only \
             and off by default.",
            local,
            obj(
                json!({
                    "arena": { "type": "string" },
                    "name": { "type": "string", "description": "How to appear on the leaderboard; defaults to `build`" },
                    "role": { "type": "string", "enum": ["solve", "play", "auto"], "description": "auto (default) picks by what the arena seats" },
                    "model": { "type": "string", "description": "Model the entrant runs on" },
                    "callback_base": { "type": "string", "description": "URL the arena should call us on; defaults to our loopback address" }
                }),
                &["arena"]
            )
        ),
        tool!(
            "arena_withdraw",
            "owner",
            "Withdraw this console from an arena and forget the entry. Past matches keep their \
             record. With no arena named, withdraws from all of them and switches the competitor \
             endpoints off.",
            local,
            json!({ "type": "object", "properties": { "arena": { "type": "string" } } })
        ),
        tool!(
            "arena_status",
            "public",
            "Where this console is entered, under what name, in what role, and whether the \
             competitor endpoints are currently answering. The shared key is never returned.",
            local,
            empty()
        ),
        tool!(
            "arena_leaderboard",
            "public",
            "Standings in an arena — how this console is doing against everyone else entered.",
            local,
            obj(json!({ "arena": { "type": "string" }, "game": { "type": "string" } }), &["arena"])
        ),
        tool!(
            "arena_match",
            "auth",
            "Run a match in an arena: seat the given entrants at the given game or task and play it \
             out. Blocks until it finishes and returns the result.",
            local,
            obj(
                json!({
                    "arena": { "type": "string" },
                    "subject": { "type": "string", "description": "Game (wasm arena) or task (coding arena), by id or name" },
                    "entrants": { "type": "array", "items": { "type": "string" }, "description": "Two or more makes it rated" }
                }),
                &["arena", "subject", "entrants"]
            )
        ),
    ]
}

pub fn tool_list() -> Value {
    Value::Array(
        tools()
            .iter()
            .map(|t| {
                json!({
                    "name": t.name,
                    "description": format!("[{}] {}", t.access, t.description),
                    "inputSchema": (t.schema)(),
                })
            })
            .collect(),
    )
}

// ── dispatch ─────────────────────────────────────────────────────────

fn s(args: &Value, key: &str) -> String {
    args.get(key).and_then(|v| v.as_str()).unwrap_or("").trim().to_string()
}

fn client() -> reqwest::Client {
    reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(300))
        .build()
        .unwrap_or_default()
}

/// Fill `{arg}` segments from the arguments and hand back the path plus
/// whatever arguments were not consumed by it.
fn render(path: &str, args: &Value) -> Result<(String, Map<String, Value>), String> {
    let mut rest: Map<String, Value> = match args {
        Value::Object(m) => m.clone(),
        Value::Null => Map::new(),
        _ => return Err("arguments must be an object".into()),
    };
    // `sudo` is a transport concern, not a parameter — it becomes a header.
    rest.remove("sudo");

    let mut out = String::with_capacity(path.len());
    let mut chars = path.chars().peekable();
    while let Some(c) = chars.next() {
        if c != '{' {
            out.push(c);
            continue;
        }
        let mut key = String::new();
        for c in chars.by_ref() {
            if c == '}' {
                break;
            }
            key.push(c);
        }
        let val = rest
            .remove(&key)
            .ok_or_else(|| format!("this tool needs `{key}`"))?;
        let val = match val {
            Value::String(s) => s,
            other => other.to_string(),
        };
        if val.trim().is_empty() {
            return Err(format!("`{key}` cannot be empty"));
        }
        out.push_str(&urlencode(val.trim()));
    }
    Ok((out, rest))
}

fn urlencode(s: &str) -> String {
    s.bytes()
        .map(|b| match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                (b as char).to_string()
            }
            b' ' => "%20".to_string(),
            _ => format!("%{b:02X}"),
        })
        .collect()
}

fn query_string(map: &Map<String, Value>) -> String {
    let parts: Vec<String> = map
        .iter()
        .filter(|(_, v)| !v.is_null())
        .map(|(k, v)| {
            let raw = match v {
                Value::String(s) => s.clone(),
                other => other.to_string(),
            };
            format!("{}={}", urlencode(k), urlencode(&raw))
        })
        .collect();
    if parts.is_empty() { String::new() } else { format!("?{}", parts.join("&")) }
}

/// Call our own REST surface as the caller. Errors come back as the handler
/// worded them — an agent that is missing a sudo signature is told exactly
/// that, in the same words a browser would see.
async fn rest(method: &str, path: &str, args: &Value, ctx: &Ctx) -> Result<Value, String> {
    let (path, rest_args) = render(path, args)?;
    let is_get = method.eq_ignore_ascii_case("GET");
    let url = format!(
        "{}{}{}",
        base(),
        path,
        if is_get { query_string(&rest_args) } else { String::new() }
    );

    let m = reqwest::Method::from_bytes(method.as_bytes())
        .map_err(|_| format!("bad method {method}"))?;
    let mut req = client().request(m, &url);
    if let Some(t) = &ctx.token {
        req = req.header("authorization", t);
    }
    let sudo = args.get("sudo").and_then(|v| v.as_str()).map(str::to_string).or_else(|| ctx.sudo.clone());
    if let Some(sig) = sudo {
        req = req.header("x-sudo", sig);
    }
    if !is_get {
        req = req.json(&Value::Object(rest_args));
    }

    let resp = req.send().await.map_err(|e| format!("{url} unreachable: {e}"))?;
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    let body: Value = serde_json::from_str(&text).unwrap_or(Value::String(text.clone()));

    if !status.is_success() {
        let msg = body
            .get("error")
            .and_then(|e| e.as_str())
            .map(str::to_string)
            .unwrap_or_else(|| text.chars().take(400).collect());
        return Err(format!("{status}: {msg}"));
    }
    // A 200 carrying an `error` field is still a refusal — several handlers
    // answer that way rather than with a status code, and an agent that only
    // reads isError would sail straight past it.
    if let Some(e) = body.get("error").and_then(|e| e.as_str()) {
        if !e.trim().is_empty() {
            return Err(e.to_string());
        }
    }
    Ok(body)
}

/// What this console is. Composed rather than routed: it is the one answer an
/// agent needs before it knows which other question to ask.
async fn build_info(ctx: &Ctx) -> Value {
    let health = rest("GET", "/health", &json!({}), ctx).await.unwrap_or(Value::Null);
    let owner = rest("GET", "/owner", &json!({}), ctx).await.unwrap_or(Value::Null);
    let jobs = rest("GET", "/jobs", &json!({ "limit": 200 }), ctx).await.unwrap_or(Value::Null);
    let arenas = arena::registry(false).await;

    let list = jobs.get("jobs").and_then(|j| j.as_array()).cloned().unwrap_or_default();
    let count = |st: &str| list.iter().filter(|j| j.get("status").and_then(|s| s.as_str()) == Some(st)).count();

    json!({
        "name": SERVER_NAME,
        "version": SERVER_VERSION,
        "mcp_protocol": PROTOCOL_VERSION,
        "what": "A programmable AI developer console. It runs coding agents as background jobs \
                 against the modules on this fleet, snapshots every module into content-addressed \
                 storage, and takes changes back through fork → merge request → agentic merge.",
        "base": base(),
        "health": health,
        "owner": owner,
        "agents": ["claude", "codex", "agent"],
        "tasks": {
            "sampled": list.len(),
            "running": count("running"),
            "completed": count("completed"),
            "failed": count("failed"),
        },
        "arenas": arenas,
        "competitor": arena::status(),
        "transports": {
            "http": format!("{}/mcp", base()),
            "stdio": "build-fork-jobs --stdio (bridges to the running server; set BUILD_FORK_TOKEN to act as yourself)",
        },
    })
}

/// Who the caller is. /auth/role wants an address in the query string, and an
/// MCP client has no reason to know its own — it holds a token, not a wallet —
/// so read it off the token unless one was named explicitly.
async fn whoami(args: &Value, ctx: &Ctx) -> Result<Value, String> {
    let asked = s(args, "address");
    let address = if asked.is_empty() {
        match arena::caller_address(ctx) {
            Ok(a) => a,
            Err(why) => {
                return Ok(json!({
                    "address": Value::Null,
                    "role": "anonymous",
                    "why": why,
                    "can": "the tools tagged [public]",
                }))
            }
        }
    } else {
        asked
    };
    let mut role = rest("GET", "/auth/role", &json!({ "address": address }), ctx).await?;
    role["address"] = json!(address);
    role["asked_about_self"] = json!(s(args, "address").is_empty());
    Ok(role)
}

/// Poll a task to a terminal state. The loop an MCP client would write anyway,
/// written once and correctly.
async fn wait_task(args: &Value, ctx: &Ctx) -> Result<Value, String> {
    let id = s(args, "id");
    if id.is_empty() {
        return Err("wait_task requires `id`".into());
    }
    let budget = args
        .get("timeout_ms")
        .and_then(|v| v.as_u64())
        .unwrap_or(600_000)
        .clamp(1_000, 3_600_000);
    let deadline = std::time::Instant::now() + std::time::Duration::from_millis(budget);

    loop {
        let job = rest("GET", "/jobs/{id}", &json!({ "id": id }), ctx).await?;
        let status = job
            .get("job")
            .unwrap_or(&job)
            .get("status")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        if matches!(status.as_str(), "completed" | "failed" | "cancelled") {
            return Ok(job);
        }
        if std::time::Instant::now() >= deadline {
            return Err(format!(
                "task {id} was still {status} after {budget}ms — it has not been cancelled, poll get_task"
            ));
        }
        tokio::time::sleep(std::time::Duration::from_millis(1_500)).await;
    }
}

/// The one place a build capability is invoked.
pub async fn call_tool(name: &str, args: &Value, ctx: &Ctx) -> Result<Value, String> {
    // Local tools first — everything else is its REST route.
    match name {
        "build_info" => return Ok(build_info(ctx).await),
        "whoami" => return whoami(args, ctx).await,
        "wait_task" => return wait_task(args, ctx).await,
        "arena_list" => {
            return Ok(json!({
                "arenas": arena::registry(args.get("refresh").and_then(|v| v.as_bool()).unwrap_or(false)).await
            }))
        }
        "arena_tools" => return arena::peer_tools(&s(args, "arena")).await,
        "arena_call" => {
            // The only tool here that reaches a peer's WRITE surface without a
            // REST handler in front of it, so the gate has to be right here.
            arena::require_trusted(ctx)?;
            let tool = s(args, "tool");
            if tool.is_empty() {
                return Err("arena_call requires `tool` — read arena_tools first".into());
            }
            return arena::call(
                &s(args, "arena"),
                &tool,
                args.get("arguments").cloned().unwrap_or_else(|| json!({})),
            )
            .await;
        }
        "arena_enter" => return arena::enter(args, ctx).await,
        "arena_withdraw" => return arena::withdraw(args, ctx).await,
        "arena_status" => return Ok(arena::status()),
        "arena_leaderboard" => {
            let mut a = json!({});
            if !s(args, "game").is_empty() {
                a["game"] = json!(s(args, "game"));
            }
            return arena::call(&s(args, "arena"), "leaderboard", a).await;
        }
        "arena_match" => {
            arena::require_trusted(ctx)?;
            return arena::run_match(args).await;
        }
        _ => {}
    }

    let table = tools();
    let tool = table
        .iter()
        .find(|t| t.name == name)
        .ok_or_else(|| {
            let names: Vec<&str> = table.iter().map(|t| t.name).collect();
            format!("unknown tool: {name} — this server offers {}", names.join(", "))
        })?;

    match tool.fulfil {
        Fulfil::Rest(method, path) => rest(method, path, args, ctx).await,
        // Every Local tool is matched above; reaching here means one was added
        // to the table without an arm, and saying so beats a silent empty {}.
        Fulfil::Local => Err(format!("{name} is declared local but has no implementation")),
    }
}

// ── JSON-RPC ─────────────────────────────────────────────────────────

fn rpc_result(id: Value, result: Value) -> Value {
    json!({ "jsonrpc": "2.0", "id": id, "result": result })
}

fn rpc_error(id: Value, code: i64, message: &str) -> Value {
    json!({ "jsonrpc": "2.0", "id": id, "error": { "code": code, "message": message } })
}

/// Static context an agent can read without spending a tool call.
fn resource_list() -> Value {
    json!([
        {
            "uri": "build://info",
            "name": "console",
            "description": "What this console is and what it is connected to — the build_info answer.",
            "mimeType": "application/json"
        },
        {
            "uri": "build://arenas",
            "name": "arenas",
            "description": "Every arena/1.0 module on this fleet, and where this console is entered.",
            "mimeType": "application/json"
        },
        {
            "uri": "build://modules",
            "name": "modules",
            "description": "The fleet's modules with their configs and ports.",
            "mimeType": "application/json"
        }
    ])
}

async fn read_resource(uri: &str, ctx: &Ctx) -> Result<Value, String> {
    let body = match uri {
        "build://info" => build_info(ctx).await,
        "build://arenas" => json!({
            "arenas": arena::registry(false).await,
            "entered": arena::status()
        }),
        "build://modules" => rest("GET", "/modules", &json!({}), ctx).await?,
        other => return Err(format!("no such resource: {other}")),
    };
    Ok(json!({
        "contents": [{
            "uri": uri,
            "mimeType": "application/json",
            "text": serde_json::to_string_pretty(&body).unwrap_or_default()
        }]
    }))
}

fn prompt_list() -> Value {
    json!([
        {
            "name": "ship_change",
            "description": "Fork a module, make one change in the fork, and open a merge request for it — the safe path for an agent that does not own the module.",
            "arguments": [
                { "name": "module", "description": "Module to change", "required": true },
                { "name": "change", "description": "What to change", "required": true }
            ]
        },
        {
            "name": "compete",
            "description": "Enter this console in an arena and run a rated match against whoever is already there.",
            "arguments": [
                { "name": "arena", "description": "Arena module name", "required": true },
                { "name": "subject", "description": "Game or task to play", "required": false }
            ]
        }
    ])
}

fn prompt_get(name: &str, args: &Value) -> Result<Value, String> {
    let a = |k: &str| s(args, k);
    let text = match name {
        "ship_change" => format!(
            "Work on the module `{}` in this build console.\n\n\
             1. `fork_module` it, so you are editing your own workspace and not the live tree.\n\
             2. Make this change: {}\n\
             3. `open_merge_request` against it with a title that says what changed and why.\n\n\
             Use `search_code` and `read_file` to find the right place before you write anything. \
             Do not call `write_file` outside the fork.",
            a("module"),
            a("change")
        ),
        "compete" => format!(
            "Enter this console in the `{}` arena and compete.\n\n\
             1. `arena_tools` on it, so you know its vocabulary.\n\
             2. `arena_enter` it.\n\
             3. `arena_call` its list tool to see what is on offer{}.\n\
             4. `arena_match` against at least one other entrant, so the result is rated.\n\
             5. `arena_leaderboard` to see where that left us.",
            a("arena"),
            if a("subject").is_empty() { String::new() } else { format!(", then pick `{}`", a("subject")) }
        ),
        other => return Err(format!("no such prompt: {other}")),
    };
    Ok(json!({
        "messages": [{ "role": "user", "content": { "type": "text", "text": text } }]
    }))
}

/// Handle one JSON-RPC message. Returns None for notifications (no reply).
pub async fn handle_message(msg: &Value, ctx: &Ctx) -> Option<Value> {
    let method = msg.get("method").and_then(|m| m.as_str()).unwrap_or("");
    let params = msg.get("params").cloned().unwrap_or_else(|| json!({}));

    let id = match msg.get("id").cloned() {
        Some(id) if !id.is_null() => id,
        _ => return None,
    };

    Some(match method {
        "initialize" => rpc_result(
            id,
            json!({
                "protocolVersion": params.get("protocolVersion").and_then(|v| v.as_str()).unwrap_or(PROTOCOL_VERSION),
                "capabilities": { "tools": {}, "resources": {}, "prompts": {} },
                "serverInfo": { "name": SERVER_NAME, "version": SERVER_VERSION },
                "instructions": "A programmable developer console. `build_info` first: it says what \
                                 is here, who you are to it, and which arenas it can reach. Tools are \
                                 tagged [public], [auth] or [owner] — an [auth] tool needs a bearer \
                                 token, and a few owner actions additionally need a `sudo` signature, \
                                 which the error will name. `submit_task` spends money; everything \
                                 else is cheap."
            }),
        ),
        "ping" => rpc_result(id, json!({})),
        "tools/list" => rpc_result(id, json!({ "tools": tool_list() })),
        "tools/call" => {
            let name = params.get("name").and_then(|n| n.as_str()).unwrap_or("");
            let args = params.get("arguments").cloned().unwrap_or_else(|| json!({}));
            match call_tool(name, &args, ctx).await {
                Ok(v) => rpc_result(
                    id,
                    json!({
                        "content": [{ "type": "text", "text": serde_json::to_string(&v).unwrap_or_default() }],
                        "structuredContent": v,
                        "isError": false
                    }),
                ),
                Err(e) => rpc_result(
                    id,
                    json!({ "content": [{ "type": "text", "text": e }], "isError": true }),
                ),
            }
        }
        "resources/list" => rpc_result(id, json!({ "resources": resource_list() })),
        "resources/read" => {
            let uri = params.get("uri").and_then(|u| u.as_str()).unwrap_or("");
            match read_resource(uri, ctx).await {
                Ok(v) => rpc_result(id, v),
                Err(e) => rpc_error(id, -32602, &e),
            }
        }
        "prompts/list" => rpc_result(id, json!({ "prompts": prompt_list() })),
        "prompts/get" => {
            let name = params.get("name").and_then(|n| n.as_str()).unwrap_or("");
            let args = params.get("arguments").cloned().unwrap_or_else(|| json!({}));
            match prompt_get(name, &args) {
                Ok(v) => rpc_result(id, v),
                Err(e) => rpc_error(id, -32602, &e),
            }
        }
        _ => rpc_error(id, -32601, &format!("method not found: {method}")),
    })
}

/// stdio transport: newline-delimited JSON-RPC on stdin/stdout.
/// Usage: build-fork-jobs --stdio
///
/// This is a bridge, not a second server — it forwards to the HTTP surface at
/// $BUILD_FORK_API_URL (default http://127.0.0.1:8894), so the API has to be up.
/// Set BUILD_FORK_TOKEN to act as yourself; without it you get the public tools.
pub async fn run_stdio() {
    let ctx = Ctx::from_env();
    let stdin = BufReader::new(tokio::io::stdin());
    let mut stdout = tokio::io::stdout();
    let mut lines = stdin.lines();
    while let Ok(Some(line)) = lines.next_line().await {
        let line = line.trim().to_string();
        if line.is_empty() {
            continue;
        }
        let msg: Value = match serde_json::from_str(&line) {
            Ok(v) => v,
            Err(e) => {
                let err = rpc_error(Value::Null, -32700, &format!("parse error: {e}"));
                let _ = stdout.write_all(format!("{err}\n").as_bytes()).await;
                let _ = stdout.flush().await;
                continue;
            }
        };
        if let Some(resp) = handle_message(&msg, &ctx).await {
            let _ = stdout.write_all(format!("{resp}\n").as_bytes()).await;
            let _ = stdout.flush().await;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_tool_has_a_description_and_a_schema() {
        for t in tools() {
            assert!(
                t.description.len() > 60,
                "{} needs a description an agent can act on",
                t.name
            );
            let schema = (t.schema)();
            assert_eq!(schema["type"], "object", "{} schema", t.name);
            assert!(schema.get("properties").is_some(), "{} properties", t.name);
            assert!(
                ["public", "auth", "owner"].contains(&t.access),
                "{} has access {:?}",
                t.name,
                t.access
            );
        }
    }

    #[test]
    fn tool_names_are_unique() {
        let mut names: Vec<&str> = tools().iter().map(|t| t.name).collect();
        let before = names.len();
        names.sort_unstable();
        names.dedup();
        assert_eq!(before, names.len(), "duplicate tool name");
    }

    #[test]
    fn every_path_placeholder_is_a_declared_property() {
        for t in tools() {
            let Fulfil::Rest(_, path) = t.fulfil else { continue };
            let schema = (t.schema)();
            for seg in path.split('{').skip(1) {
                let key = seg.split('}').next().unwrap_or("");
                assert!(
                    schema["properties"].get(key).is_some(),
                    "{} interpolates {{{}}} but never declares it",
                    t.name,
                    key
                );
                assert!(
                    schema["required"]
                        .as_array()
                        .map(|r| r.iter().any(|v| v == key))
                        .unwrap_or(false),
                    "{} interpolates {{{}}} so it must be required",
                    t.name,
                    key
                );
            }
        }
    }

    #[test]
    fn rendering_consumes_path_args_and_leaves_the_rest() {
        let (path, rest) = render(
            "/modules/{module}/snapshot",
            &json!({ "module": "build-fork", "message": "hi", "sudo": "0xsig" }),
        )
        .expect("renders");
        assert_eq!(path, "/modules/build-fork/snapshot");
        assert_eq!(rest.len(), 1, "sudo is a header, not a parameter");
        assert_eq!(rest["message"], "hi");
    }

    #[test]
    fn a_missing_path_arg_is_named_in_the_error() {
        let err = render("/jobs/{id}", &json!({})).unwrap_err();
        assert!(err.contains("id"), "{err}");
    }

    #[test]
    fn path_args_are_escaped() {
        // `..` on its own is harmless; a separator is not. Escaping the slash
        // is what keeps an argument inside its own path segment.
        let (path, _) = render("/modules/{module}/config", &json!({ "module": "a b/../c" })).unwrap();
        assert_eq!(path, "/modules/a%20b%2F..%2Fc/config");
        let segments: Vec<&str> = path.trim_start_matches('/').split('/').collect();
        assert_eq!(segments, ["modules", "a%20b%2F..%2Fc", "config"]);
    }

    #[test]
    fn local_tools_all_have_an_arm() {
        // call_tool matches Local tools by name before it consults the table;
        // a Local tool with no arm would fall through to the "no implementation"
        // error, so keep the two lists in step.
        const ARMS: [&str; 11] = [
            "build_info",
            "whoami",
            "wait_task",
            "arena_list",
            "arena_tools",
            "arena_call",
            "arena_enter",
            "arena_withdraw",
            "arena_status",
            "arena_leaderboard",
            "arena_match",
        ];
        for t in tools() {
            if matches!(t.fulfil, Fulfil::Local) {
                assert!(ARMS.contains(&t.name), "{} is local with no arm", t.name);
            }
        }
    }

    #[test]
    fn a_query_string_survives_special_characters() {
        let mut m = Map::new();
        m.insert("query".into(), json!("fn main() {"));
        let qs = query_string(&m);
        assert!(qs.starts_with("?query="));
        assert!(!qs.contains(' '), "{qs}");
    }
}
