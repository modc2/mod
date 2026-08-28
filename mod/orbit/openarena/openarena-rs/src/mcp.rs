//! MCP server core — JSON-RPC 2.0 dispatch shared by the Streamable HTTP
//! endpoint (/mcp) and stdio mode (--stdio).
//!
//! Every REST route funnels through `call_tool` too, so an arena capability is
//! defined exactly once: what an agent can do over MCP is what a browser can
//! do over HTTP, always.

use crate::arena;
use crate::judge;
use crate::players;
use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

pub const PROTOCOL_VERSION: &str = "2025-06-18";
pub const SERVER_NAME: &str = "openarena";
pub const SERVER_VERSION: &str = env!("CARGO_PKG_VERSION");

pub fn tool_list() -> Value {
    json!([
        {
            "name": "arena_info",
            "description": "What this arena is and what is in it: task, competitor and match counts, the languages the judge runs, and the competitor kinds it can drive.",
            "inputSchema": { "type": "object", "properties": {} }
        },
        {
            "name": "list_tasks",
            "description": "Every task in the arena, newest last. Filter by free text, tag or kind.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "q": { "type": "string", "description": "Match title, slug or statement" },
                    "tag": { "type": "string" },
                    "kind": { "type": "string", "description": "e.g. coding" }
                }
            }
        },
        {
            "name": "get_task",
            "description": "One task in full: the statement, the language and mode, and the visible test cases. Hidden cases show only their name and weight.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task": { "type": "string", "description": "Task id or slug" },
                    "reveal": { "type": "boolean", "default": false, "description": "Include hidden cases — for the task author, not for entrants" }
                },
                "required": ["task"]
            }
        },
        {
            "name": "create_task",
            "description": "Upload a task. `io` mode feeds each case's stdin to the program and compares stdout with `expect`; `unit` mode runs each case's `program`, which imports the submission and passes by exiting 0. Mark the cases you do not want entrants to see as hidden.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "title": { "type": "string" },
                    "statement": { "type": "string", "description": "What the program must do, in prose" },
                    "slug": { "type": "string", "description": "Defaults to a slug of the title" },
                    "kind": { "type": "string", "default": "coding" },
                    "language": { "type": "string", "default": "any", "description": "any | python | javascript | bash" },
                    "mode": { "type": "string", "enum": ["io", "unit"], "default": "io" },
                    "starter": { "type": "string", "description": "Optional starter code shown to entrants" },
                    "timeout_ms": { "type": "integer", "default": 10000, "description": "Wall clock per case" },
                    "tags": { "type": "array", "items": { "type": "string" } },
                    "author": { "type": "string" },
                    "tests": {
                        "type": "array",
                        "description": "The grading contract — at least one case",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": { "type": "string" },
                                "stdin": { "type": "string", "description": "io mode: fed to the program" },
                                "expect": { "type": "string", "description": "io mode: the required stdout" },
                                "compare": { "type": "string", "enum": ["trim", "exact", "contains"], "default": "trim" },
                                "program": { "type": "string", "description": "unit mode: the grader source, run in the same directory as the submission" },
                                "args": { "type": "array", "items": { "type": "string" } },
                                "hidden": { "type": "boolean", "default": false },
                                "weight": { "type": "number", "default": 1 }
                            }
                        }
                    }
                },
                "required": ["title", "tests"]
            }
        },
        {
            "name": "delete_task",
            "description": "Remove a task from the arena. Past matches keep their record.",
            "inputSchema": {
                "type": "object",
                "properties": { "task": { "type": "string" } },
                "required": ["task"]
            }
        },
        {
            "name": "list_agents",
            "description": "Every competitor entered in the arena, strongest first.",
            "inputSchema": { "type": "object", "properties": {} }
        },
        {
            "name": "enter_agent",
            "description": "Enter a competitor. `agent_mod` runs an agent from this fleet's agent module (config: base, agent, model, prompt, toolbox, steps, free, key). `http` posts the task to config.url and reads a program back. `ap` drives an Agent Protocol v1 server at config.base. `static` submits config.code every time — a baseline to measure against.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": { "type": "string" },
                    "kind": { "type": "string", "enum": ["agent_mod", "http", "ap", "static"], "default": "agent_mod" },
                    "config": { "type": "object", "description": "Driver settings — see the description" },
                    "owner": { "type": "string", "description": "Who entered it" },
                    "note": { "type": "string" }
                },
                "required": ["name"]
            }
        },
        {
            "name": "remove_agent",
            "description": "Withdraw a competitor. Past matches keep their record.",
            "inputSchema": {
                "type": "object",
                "properties": { "agent": { "type": "string", "description": "Competitor id or name" } },
                "required": ["agent"]
            }
        },
        {
            "name": "run_match",
            "description": "Put competitors on a task. They all get the same brief at the same moment, answer concurrently, and every answer is graded against the same cases. Two or more entrants makes the match rated and moves Elo; one entrant is practice.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task": { "type": "string", "description": "Task id or slug" },
                    "agents": { "type": "array", "items": { "type": "string" }, "description": "Competitor ids or names" }
                },
                "required": ["task", "agents"]
            }
        },
        {
            "name": "submit",
            "description": "Grade a program against a task without a match — how an outside agent or a human plays. Records the submission and the pass rate; never moves Elo, since nobody was raced.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task": { "type": "string" },
                    "code": { "type": "string", "description": "The complete program" },
                    "language": { "type": "string", "description": "python | javascript | bash; defaults to the task's" },
                    "agent": { "type": "string", "description": "Credit the submission to this competitor" }
                },
                "required": ["task", "code"]
            }
        },
        {
            "name": "list_matches",
            "description": "Recent matches, newest first, with the scoreboard of each.",
            "inputSchema": {
                "type": "object",
                "properties": { "limit": { "type": "integer", "default": 20 } }
            }
        },
        {
            "name": "get_match",
            "description": "One match in full: every case result and the program each competitor submitted.",
            "inputSchema": {
                "type": "object",
                "properties": { "id": { "type": "string" } },
                "required": ["id"]
            }
        },
        {
            "name": "leaderboard",
            "description": "Competitors ranked by Elo, with pass rate, mean score and mean time to answer.",
            "inputSchema": {
                "type": "object",
                "properties": { "limit": { "type": "integer", "default": 20 } }
            }
        }
    ])
}

fn s(args: &Value, key: &str) -> String {
    args.get(key)
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string()
}

fn u(args: &Value, key: &str, default: u64) -> u64 {
    args.get(key).and_then(|v| v.as_u64()).unwrap_or(default)
}

fn b(args: &Value, key: &str) -> bool {
    args.get(key).and_then(|v| v.as_bool()).unwrap_or(false)
}

/// The one place an arena capability is implemented.
pub async fn call_tool(name: &str, args: &Value) -> Result<Value, String> {
    match name {
        "arena_info" => Ok(arena::info()),
        "list_tasks" => Ok(arena::list_tasks(args)),
        "get_task" => {
            let key = s(args, "task");
            if key.is_empty() {
                return Err("get_task requires `task`".into());
            }
            arena::get_task(&key, b(args, "reveal"))
        }
        "create_task" => arena::create_task(args),
        "delete_task" => {
            let key = s(args, "task");
            if key.is_empty() {
                return Err("delete_task requires `task`".into());
            }
            arena::delete_task(&key)
        }
        "list_agents" => Ok(arena::list_agents()),
        "enter_agent" => arena::enter_agent(args),
        "remove_agent" => {
            let key = s(args, "agent");
            if key.is_empty() {
                return Err("remove_agent requires `agent`".into());
            }
            arena::remove_agent(&key)
        }
        "run_match" => {
            let task = s(args, "task");
            if task.is_empty() {
                return Err("run_match requires `task`".into());
            }
            let agents: Vec<String> = match args.get("agents") {
                Some(Value::Array(a)) => a
                    .iter()
                    .filter_map(|v| v.as_str().map(|s| s.trim().to_string()))
                    .filter(|s| !s.is_empty())
                    .collect(),
                Some(Value::String(one)) => vec![one.trim().to_string()],
                _ => vec![],
            };
            if agents.is_empty() {
                return Err("run_match requires `agents` — at least one competitor".into());
            }
            arena::run_match(&task, &agents).await
        }
        "submit" => {
            let task = s(args, "task");
            let code = args.get("code").and_then(|v| v.as_str()).unwrap_or("");
            if task.is_empty() || code.trim().is_empty() {
                return Err("submit requires `task` and `code`".into());
            }
            let agent = s(args, "agent");
            arena::submit(
                &task,
                code,
                &s(args, "language"),
                if agent.is_empty() { None } else { Some(agent.as_str()) },
            )
            .await
        }
        "list_matches" => Ok(arena::list_matches(u(args, "limit", 20) as usize)),
        "get_match" => {
            let id = s(args, "id");
            if id.is_empty() {
                return Err("get_match requires `id`".into());
            }
            arena::get_match(&id)
        }
        "leaderboard" => Ok(arena::leaderboard(u(args, "limit", 20) as usize)),
        other => Err(format!(
            "unknown tool: {other} — this arena runs {:?} against {:?}",
            players::KINDS,
            judge::languages()
        )),
    }
}

fn rpc_result(id: Value, result: Value) -> Value {
    json!({ "jsonrpc": "2.0", "id": id, "result": result })
}

fn rpc_error(id: Value, code: i64, message: &str) -> Value {
    json!({ "jsonrpc": "2.0", "id": id, "error": { "code": code, "message": message } })
}

/// Handle one JSON-RPC message. Returns None for notifications (no reply).
pub async fn handle_message(msg: &Value) -> Option<Value> {
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
                "capabilities": { "tools": {} },
                "serverInfo": { "name": SERVER_NAME, "version": SERVER_VERSION }
            }),
        ),
        "ping" => rpc_result(id, json!({})),
        "tools/list" => rpc_result(id, json!({ "tools": tool_list() })),
        "tools/call" => {
            let name = params.get("name").and_then(|n| n.as_str()).unwrap_or("");
            let args = params.get("arguments").cloned().unwrap_or_else(|| json!({}));
            match call_tool(name, &args).await {
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
        "resources/list" => rpc_result(id, json!({ "resources": [] })),
        "prompts/list" => rpc_result(id, json!({ "prompts": [] })),
        _ => rpc_error(id, -32601, &format!("method not found: {method}")),
    })
}

/// stdio transport: newline-delimited JSON-RPC on stdin/stdout.
/// Usage: openarena-api --stdio
pub async fn run_stdio() {
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
        if let Some(resp) = handle_message(&msg).await {
            let _ = stdout.write_all(format!("{resp}\n").as_bytes()).await;
            let _ = stdout.flush().await;
        }
    }
}
