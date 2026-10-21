//! MCP (Model Context Protocol) server core — JSON-RPC 2.0 dispatch shared by
//! the Streamable HTTP endpoint (/mcp) and stdio mode (--stdio).
//! Every REST route also funnels through `call_tool`, so the MCP tool layer is
//! the single backend: one place where an X capability is defined.

use crate::x::{self, Auth, TWEET_FIELDS, USER_FIELDS};
use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

pub const PROTOCOL_VERSION: &str = "2025-06-18";
pub const SERVER_NAME: &str = "x";
pub const SERVER_VERSION: &str = env!("CARGO_PKG_VERSION");

pub fn tool_list() -> Value {
    json!([
        {
            "name": "search",
            "description": "Search public posts from the last 7 days. Supports the full X query syntax (from:, to:, #tag, -is:retweet, lang:en, …).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": { "type": "string", "description": "X search query, e.g. \"bittensor -is:retweet lang:en\"" },
                    "max_results": { "type": "integer", "default": 10, "description": "10–100" },
                    "sort_order": { "type": "string", "enum": ["recency", "relevancy"] },
                    "start_time": { "type": "string", "description": "ISO 8601, within the last 7 days" },
                    "end_time": { "type": "string", "description": "ISO 8601" },
                    "next_token": { "type": "string", "description": "Pagination token from a prior call" }
                },
                "required": ["query"]
            }
        },
        {
            "name": "summarize",
            "description": "Summarize any X feed (an @handle, x.com profile/search/list/hashtag/status URL, #hashtag, search query, list:<id>, or \"home\" for your own following feed): digest with top posts, topics, hashtags, links, activity, plus what the API calls cost. Set estimate_only to price it without spending anything.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "feed": { "type": "string", "description": "@handle, x.com URL (profile, search, list, hashtag, status=thread), #tag, search query, list:<id>, or home" },
                    "max_results": { "type": "integer", "default": 50, "description": "5–300 posts to read; cost scales with this" },
                    "estimate_only": { "type": "boolean", "default": false, "description": "Return only the cost estimate; no API call" },
                    "exclude": { "type": "string", "description": "Timeline/home only: retweets,replies (timeline default retweets)" }
                },
                "required": ["feed"]
            }
        },
        {
            "name": "counts",
            "description": "Volume of posts matching a query over time (last 7 days), bucketed by minute/hour/day.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": { "type": "string" },
                    "granularity": { "type": "string", "enum": ["minute", "hour", "day"], "default": "day" }
                },
                "required": ["query"]
            }
        },
        {
            "name": "get_post",
            "description": "Fetch one post by id. Falls back to the public syndication CDN (fewer fields) when no credentials are configured.",
            "inputSchema": {
                "type": "object",
                "properties": { "id": { "type": "string", "description": "Post id, or a full x.com status URL" } },
                "required": ["id"]
            }
        },
        {
            "name": "user",
            "description": "Look up an account profile by @handle or numeric id.",
            "inputSchema": {
                "type": "object",
                "properties": { "username": { "type": "string", "description": "@handle or numeric user id" } },
                "required": ["username"]
            }
        },
        {
            "name": "timeline",
            "description": "Recent posts from an account's timeline.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "username": { "type": "string", "description": "@handle or numeric user id" },
                    "max_results": { "type": "integer", "default": 10, "description": "5–100" },
                    "exclude": { "type": "string", "description": "Comma list: retweets,replies" }
                },
                "required": ["username"]
            }
        },
        {
            "name": "mentions",
            "description": "Posts mentioning an account (defaults to the authenticated account).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "username": { "type": "string" },
                    "max_results": { "type": "integer", "default": 10 }
                }
            }
        },
        {
            "name": "followers",
            "description": "Accounts following the given account.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "username": { "type": "string" },
                    "max_results": { "type": "integer", "default": 20 }
                },
                "required": ["username"]
            }
        },
        {
            "name": "following",
            "description": "Accounts the given account follows.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "username": { "type": "string" },
                    "max_results": { "type": "integer", "default": 20 }
                },
                "required": ["username"]
            }
        },
        {
            "name": "me",
            "description": "The authenticated account (requires user-context credentials).",
            "inputSchema": { "type": "object", "properties": {} }
        },
        {
            "name": "post",
            "description": "Publish a post as the authenticated account. Optionally a reply, a quote, or a poll.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": { "type": "string", "description": "Post body (≤280 chars on most tiers)" },
                    "reply_to": { "type": "string", "description": "Post id to reply to" },
                    "quote_post_id": { "type": "string", "description": "Post id to quote" },
                    "poll_options": { "type": "array", "items": { "type": "string" }, "description": "2–4 poll choices" },
                    "poll_duration_minutes": { "type": "integer", "default": 1440 }
                },
                "required": ["text"]
            }
        },
        {
            "name": "delete_post",
            "description": "Delete one of the authenticated account's posts.",
            "inputSchema": {
                "type": "object",
                "properties": { "id": { "type": "string" } },
                "required": ["id"]
            }
        },
        {
            "name": "like",
            "description": "Like a post as the authenticated account.",
            "inputSchema": {
                "type": "object",
                "properties": { "id": { "type": "string" } },
                "required": ["id"]
            }
        },
        {
            "name": "repost",
            "description": "Repost (retweet) a post as the authenticated account.",
            "inputSchema": {
                "type": "object",
                "properties": { "id": { "type": "string" } },
                "required": ["id"]
            }
        },
        {
            "name": "follow",
            "description": "Follow an account as the authenticated account.",
            "inputSchema": {
                "type": "object",
                "properties": { "username": { "type": "string", "description": "@handle or numeric user id" } },
                "required": ["username"]
            }
        },
        {
            "name": "auth_status",
            "description": "Which credential rails are configured (never returns the secrets themselves).",
            "inputSchema": { "type": "object", "properties": {} }
        }
    ])
}

fn s<'a>(args: &'a Value, key: &str) -> Option<&'a str> {
    args.get(key).and_then(|v| v.as_str()).filter(|v| !v.is_empty())
}

fn u(args: &Value, key: &str, default: u64) -> u64 {
    args.get(key).and_then(|v| v.as_u64()).unwrap_or(default)
}

fn q(pairs: Vec<(&str, String)>) -> Vec<(String, String)> {
    pairs
        .into_iter()
        .filter(|(_, v)| !v.is_empty())
        .map(|(k, v)| (k.to_string(), v))
        .collect()
}

/// Post ids arrive as bare ids or as pasted x.com/twitter.com status URLs.
/// Anything without a numeric id in it is refused rather than spliced into an
/// upstream path as-is.
fn post_id(raw: &str) -> Result<String, String> {
    raw.trim()
        .rsplit(['/', '?', '#'])
        .find(|seg| !seg.is_empty() && seg.chars().all(|c| c.is_ascii_digit()))
        .map(String::from)
        .ok_or_else(|| format!("not a post id or status URL: {raw:?}"))
}

/// Default read expansions — author objects inline, so a search result is
/// readable without a second lookup.
fn read_fields() -> Vec<(&'static str, String)> {
    vec![
        ("tweet.fields", TWEET_FIELDS.into()),
        ("expansions", "author_id".into()),
        ("user.fields", USER_FIELDS.into()),
    ]
}

/// Execute a tool. `header_key` is a per-request bearer override (HTTP
/// `x-api-key` / `Authorization: Bearer`); args may also carry `bearer_token`.
pub async fn call_tool(name: &str, args: &Value, header_key: Option<&str>) -> Result<Value, String> {
    let creds = x::resolve(s(args, "bearer_token").or(header_key));

    let out: Result<Value, x::ApiError> = match name {
        "auth_status" => Ok(json!({
            "bearer": !creds.bearer.is_empty(),
            "user_context": creds.has_user(),
            "reads": creds.has_any(),
            "writes": creds.has_user(),
            "keyless_fallback": ["get_post"],
            "sources": ["request header / arg", "X_BEARER_TOKEN etc.", "~/.mod/x/credentials.json"]
        })),

        "search" => {
            let query = s(args, "query").ok_or("search requires `query`")?;
            let mut params = q(vec![
                ("query", query.into()),
                ("max_results", u(args, "max_results", 10).clamp(10, 100).to_string()),
                ("sort_order", s(args, "sort_order").unwrap_or("").into()),
                ("start_time", s(args, "start_time").unwrap_or("").into()),
                ("end_time", s(args, "end_time").unwrap_or("").into()),
                ("next_token", s(args, "next_token").unwrap_or("").into()),
            ]);
            params.extend(q(read_fields()));
            x::get("/2/tweets/search/recent", &params, &creds, Auth::App).await
        }

        "counts" => {
            let query = s(args, "query").ok_or("counts requires `query`")?;
            let params = q(vec![
                ("query", query.into()),
                ("granularity", s(args, "granularity").unwrap_or("day").into()),
            ]);
            x::get("/2/tweets/counts/recent", &params, &creds, Auth::App).await
        }

        "get_post" => {
            let id = post_id(s(args, "id").ok_or("get_post requires `id`")?)?;
            if !creds.has_any() {
                return x::syndication_post(&id).await.map_err(|e| e.to_string());
            }
            x::get(&format!("/2/tweets/{id}"), &q(read_fields()), &creds, Auth::App).await
        }

        "user" => {
            let who = s(args, "username")
                .or_else(|| s(args, "id"))
                .ok_or("user requires `username`")?;
            let who = x::handle(who).map_err(|e| e.to_string())?;
            let params = q(vec![("user.fields", USER_FIELDS.into())]);
            let path = if who.chars().all(|c| c.is_ascii_digit()) {
                format!("/2/users/{who}")
            } else {
                format!("/2/users/by/username/{who}")
            };
            x::get(&path, &params, &creds, Auth::App).await
        }

        "timeline" => {
            let who = s(args, "username").ok_or("timeline requires `username`")?;
            match x::user_id(who, &creds).await {
                Err(e) => Err(e),
                Ok(id) => {
                    let mut params = q(vec![
                        ("max_results", u(args, "max_results", 10).clamp(5, 100).to_string()),
                        ("exclude", s(args, "exclude").unwrap_or("").into()),
                        ("pagination_token", s(args, "pagination_token").unwrap_or("").into()),
                    ]);
                    params.extend(q(read_fields()));
                    x::get(&format!("/2/users/{id}/tweets"), &params, &creds, Auth::App).await
                }
            }
        }

        "mentions" => {
            let id = match s(args, "username") {
                Some(who) => x::user_id(who, &creds).await,
                None => x::me_id(&creds).await,
            };
            match id {
                Err(e) => Err(e),
                Ok(id) => {
                    let mut params = q(vec![
                        ("max_results", u(args, "max_results", 10).clamp(5, 100).to_string()),
                        ("pagination_token", s(args, "pagination_token").unwrap_or("").into()),
                    ]);
                    params.extend(q(read_fields()));
                    x::get(&format!("/2/users/{id}/mentions"), &params, &creds, Auth::User).await
                }
            }
        }

        "followers" | "following" => {
            let who = s(args, "username").ok_or("requires `username`")?;
            match x::user_id(who, &creds).await {
                Err(e) => Err(e),
                Ok(id) => {
                    let params = q(vec![
                        ("max_results", u(args, "max_results", 20).clamp(1, 1000).to_string()),
                        ("user.fields", USER_FIELDS.into()),
                        ("pagination_token", s(args, "pagination_token").unwrap_or("").into()),
                    ]);
                    x::get(&format!("/2/users/{id}/{name}"), &params, &creds, Auth::App).await
                }
            }
        }

        "me" => x::get("/2/users/me", &q(vec![("user.fields", USER_FIELDS.into())]), &creds, Auth::User).await,

        "post" => {
            let text = s(args, "text").ok_or("post requires `text`")?;
            let mut body = json!({ "text": text });
            if let Some(reply_to) = s(args, "reply_to") {
                body["reply"] = json!({ "in_reply_to_tweet_id": post_id(reply_to)? });
            }
            if let Some(quote) = s(args, "quote_post_id") {
                body["quote_tweet_id"] = json!(post_id(quote)?);
            }
            if let Some(opts) = args.get("poll_options").and_then(|v| v.as_array()) {
                if !opts.is_empty() {
                    body["poll"] = json!({
                        "options": opts,
                        "duration_minutes": u(args, "poll_duration_minutes", 1440)
                    });
                }
            }
            x::post("/2/tweets", &body, &creds).await
        }

        "delete_post" => {
            let id = post_id(s(args, "id").ok_or("delete_post requires `id`")?)?;
            x::delete(&format!("/2/tweets/{id}"), &creds).await
        }

        "like" | "repost" => {
            let id = post_id(s(args, "id").ok_or("requires `id`")?)?;
            match x::me_id(&creds).await {
                Err(e) => Err(e),
                Ok(me) => {
                    let (path, body) = if name == "like" {
                        (format!("/2/users/{me}/likes"), json!({ "tweet_id": id }))
                    } else {
                        (format!("/2/users/{me}/retweets"), json!({ "tweet_id": id }))
                    };
                    x::post(&path, &body, &creds).await
                }
            }
        }

        "follow" => {
            let who = s(args, "username").ok_or("follow requires `username`")?;
            match (x::me_id(&creds).await, x::user_id(who, &creds).await) {
                (Err(e), _) | (_, Err(e)) => Err(e),
                (Ok(me), Ok(target)) => {
                    x::post(
                        &format!("/2/users/{me}/following"),
                        &json!({ "target_user_id": target }),
                        &creds,
                    )
                    .await
                }
            }
        }

        "summarize" => {
            use crate::summary::{digest, estimate, parse_feed, Feed};
            let feed = parse_feed(s(args, "feed").ok_or("summarize requires `feed`")?)?;
            let want = u(args, "max_results", 50).clamp(5, 300);
            let owned = feed == Feed::Home;
            // Profile reads we always pay for: the handle -> id lookup (timeline)
            // or /users/me (home). Search/list/thread may bring one author per post.
            let lookups = u64::from(matches!(feed, Feed::Timeline(_) | Feed::Home));
            let max_users = match feed {
                Feed::Timeline(_) => 1,
                _ => want,
            };
            let est = estimate(want, lookups, max_users, owned);
            let label = feed.label();
            if args.get("estimate_only").and_then(|v| v.as_bool()).unwrap_or(false) {
                return Ok(json!({ "feed": label, "estimate": est, "fetched": false }));
            }
            let missing = if owned && !creds.has_user() {
                Some("the home feed acts as your account: it needs the OAuth 1.0a user keys (Auth tab).")
            } else if !creds.has_any() {
                Some("no X credentials: reading a feed needs a bearer token (Auth tab).")
            } else {
                None
            };
            if let Some(why) = missing {
                return Ok(json!({ "feed": label, "error": format!("{why} Estimate below."), "estimate": est, "fetched": false }));
            }
            let (path, base, auth): (String, Vec<(String, String)>, Auth) = match &feed {
                Feed::Timeline(h) => {
                    let id = x::user_id(h, &creds).await.map_err(|e| e.to_string())?;
                    let ex = s(args, "exclude").unwrap_or("retweets").into();
                    (format!("/2/users/{id}/tweets"), q(vec![("exclude", ex)]), Auth::App)
                }
                Feed::Home => {
                    let me = x::get("/2/users/me", &[], &creds, Auth::User).await.map_err(|e| e.to_string())?;
                    let id = me["data"]["id"].as_str().ok_or("couldn't resolve the signed-in account")?.to_string();
                    let ex = s(args, "exclude").unwrap_or("").into();
                    (format!("/2/users/{id}/timelines/reverse_chronological"), q(vec![("exclude", ex)]), Auth::User)
                }
                Feed::List(id) => (format!("/2/lists/{id}/tweets"), vec![], Auth::App),
                Feed::Search(query) | Feed::Thread(query) => {
                    let query = match &feed {
                        Feed::Thread(_) => format!("conversation_id:{query}"),
                        _ if query.contains("is:retweet") => query.clone(),
                        _ => format!("{query} -is:retweet"),
                    };
                    (
                        "/2/tweets/search/recent".into(),
                        q(vec![("query", query), ("sort_order", "recency".into())]),
                        Auth::App,
                    )
                }
            };
            let paged_by_next = matches!(feed, Feed::Search(_) | Feed::Thread(_));
            let (mut posts, mut users_seen, mut token): (Vec<Value>, Vec<Value>, Option<String>) =
                (vec![], vec![], None);
            while (posts.len() as u64) < want {
                // Lists cap pages at 100 like the rest; search needs >= 10.
                let page = (want - posts.len() as u64).clamp(10, 100);
                let mut params = base.clone();
                params.extend(q(vec![("max_results", page.to_string())]));
                params.extend(q(read_fields()));
                if let Some(t) = &token {
                    let key = if paged_by_next { "next_token" } else { "pagination_token" };
                    params.push((key.into(), t.clone()));
                }
                let v = x::get(&path, &params, &creds, auth).await.map_err(|e| e.to_string())?;
                posts.extend(v["data"].as_array().cloned().unwrap_or_default());
                users_seen.extend(v["includes"]["users"].as_array().cloned().unwrap_or_default());
                token = v["meta"]["next_token"].as_str().map(String::from);
                if token.is_none() {
                    break;
                }
            }
            posts.truncate(want as usize);
            // Billing dedupes within a UTC day, so count each author once.
            let mut ids: Vec<&str> = users_seen.iter().filter_map(|u| u["id"].as_str()).collect();
            ids.sort();
            ids.dedup();
            let billed_users = match feed {
                Feed::Timeline(_) => 1,
                _ => ids.len() as u64 + lookups,
            };
            let actual = estimate(posts.len() as u64, billed_users, billed_users, owned);
            let mut out = digest(&posts, &users_seen);
            out["feed"] = json!(label);
            out["cost"] = actual;
            out["fetched"] = json!(true);
            Ok(out)
        }

        other => return Err(format!("unknown tool: {other}")),
    };

    out.map_err(|e| e.to_string())
}

fn rpc_result(id: Value, result: Value) -> Value {
    json!({ "jsonrpc": "2.0", "id": id, "result": result })
}

fn rpc_error(id: Value, code: i64, message: &str) -> Value {
    json!({ "jsonrpc": "2.0", "id": id, "error": { "code": code, "message": message } })
}

/// Handle one JSON-RPC message. Returns None for notifications (no reply).
pub async fn handle_message(msg: &Value, header_key: Option<&str>) -> Option<Value> {
    let method = msg.get("method").and_then(|m| m.as_str()).unwrap_or("");
    let params = msg.get("params").cloned().unwrap_or(json!({}));

    // Notifications carry no id and get no response.
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
            let args = params.get("arguments").cloned().unwrap_or(json!({}));
            match call_tool(name, &args, header_key).await {
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
/// Usage: x-api --stdio  (e.g. `claude mcp add x -- x-api --stdio`)
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
        if let Some(resp) = handle_message(&msg, None).await {
            let _ = stdout.write_all(format!("{resp}\n").as_bytes()).await;
            let _ = stdout.flush().await;
        }
    }
}
