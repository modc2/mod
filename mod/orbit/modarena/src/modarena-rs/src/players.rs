//! Drivers for the players that are not wasm.
//!
//! A wasm player runs in the browser (or the node runner) where the execution
//! layer lives. Everything else is asked for here, because everything else
//! needs something a tab should not have: an API key, or an origin that will
//! not answer a cross-site request.
//!
//!     model      any OpenAI-compatible /chat/completions endpoint — the
//!                `liquidai` module on this box by default, so a match costs
//!                nothing; OpenRouter, a local gateway or ollama work unchanged
//!     agent_mod  an agent in this fleet's `agent` module, over POST /run
//!     http       any endpoint that takes a view and hands back a move
//!
//! All three answer the same question — "given what this seat can see, what is
//! your move?" — and all three return plain text, because the game module is
//! the only thing entitled to decide what a legal move looks like.
//!
//! Keys are read from ~/.mod/modarena/keys.json or the environment, never from
//! anything committed.

use crate::blobs;
use crate::liquidai;
use crate::store::Player;
use serde_json::{json, Value};
use std::sync::OnceLock;
use std::time::Duration;

/// `wasm` and `class` are the two that execute: one in a wasm engine, one in a
/// python subprocess. Both are entered the same way — `config.module` — and
/// both move in the execution layer rather than on the server.
pub const KINDS: [&str; 6] = ["wasm", "class", "model", "agent_mod", "http", "human"];

const OPENROUTER: &str = "https://openrouter.ai/api/v1";
const AGENT_MOD_BASE: &str = "http://127.0.0.1:50117";

fn client() -> &'static reqwest::Client {
    static C: OnceLock<reqwest::Client> = OnceLock::new();
    C.get_or_init(|| {
        reqwest::Client::builder()
            .timeout(Duration::from_secs(300))
            .build()
            .unwrap_or_else(|_| reqwest::Client::new())
    })
}

fn cfg<'a>(p: &'a Player, key: &str) -> Option<&'a str> {
    p.config
        .get(key)
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())
}

/// A key for `provider`, from the player, then the environment, then the
/// off-tree key file.
fn api_key(p: &Player, provider: &str) -> Option<String> {
    if let Some(k) = cfg(p, "key") {
        return Some(k.to_string());
    }
    let env_name = format!("{}_API_KEY", provider.to_uppercase());
    if let Ok(k) = std::env::var(&env_name) {
        if !k.trim().is_empty() {
            return Some(k);
        }
    }
    let keys: Value = std::fs::read_to_string(blobs::state_dir().join("keys.json"))
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())?;
    keys.get(provider)
        .and_then(|v| v.as_str())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
}

/// Which provider's key a base URL wants — so `keys.json` can hold several and
/// the right one is picked without the player having to say.
fn provider_for(base: &str) -> &'static str {
    let b = base.to_lowercase();
    if liquidai::is_local(&b) {
        // not a key anyone bought: liquidai's is a session token, and the arena
        // mints its own from this box's secret (see liquidai::token)
        "liquidai"
    } else if b.contains("openrouter") {
        "openrouter"
    } else if b.contains("venice") {
        "venice"
    } else if b.contains("openai") {
        "openai"
    } else if b.contains("anthropic") {
        "anthropic"
    } else {
        "modarena"
    }
}

pub struct Answer {
    pub mv: String,
    /// What the player actually said, before we read a move out of it.
    pub raw: String,
    pub note: String,
    pub meta: Value,
}

/// The brief wrapped around a game's view. The game says what the position is;
/// this says what an answer has to look like, because a model that writes a
/// paragraph has not moved.
pub fn brief(view: &str, seat: usize, extra: &str) -> String {
    let mut s = String::new();
    if !extra.is_empty() {
        s.push_str(extra);
        s.push_str("\n\n");
    }
    s.push_str(&format!("You are seat {seat}.\n\n{view}\n\n"));
    s.push_str(
        "Reply with your move and nothing else. No explanation, no punctuation \
         around it, no code fence. If you want to think first, put the move on \
         the last line by itself.",
    );
    s
}

/// Read a move out of a reply.
///
/// Models narrate, and an arena that scores narration as an illegal move is
/// measuring the wrong thing. So: an explicit `MOVE: x` wins, then the last
/// fenced block, then the last non-empty line. Never more than one line —
/// splicing prose into a move would fail a player for something it did not do.
pub fn extract_move(text: &str) -> String {
    let t = text.trim();
    if t.is_empty() {
        return String::new();
    }

    for line in t.lines().rev() {
        let l = line.trim();
        for tag in ["MOVE:", "Move:", "move:", "ANSWER:", "Answer:"] {
            if let Some(rest) = l.strip_prefix(tag) {
                let v = rest.trim().trim_matches(['`', '"', '*', '.']).trim();
                if !v.is_empty() {
                    return v.to_string();
                }
            }
        }
    }

    // A fenced block: take its last non-empty line, which is the move an agent
    // that showed its working landed on.
    if let Some(block) = last_fence(t) {
        if let Some(l) = block.lines().rev().find(|l| !l.trim().is_empty()) {
            return clean(l);
        }
    }

    t.lines()
        .rev()
        .find(|l| !l.trim().is_empty())
        .map(clean)
        .unwrap_or_default()
}

/// Strip the decoration a model wraps a move in — bullets, emphasis, quotes,
/// a trailing full stop. Repeated until it stops changing, because `` `C3`. ``
/// needs two passes and there is no useful order to do it in once.
fn clean(line: &str) -> String {
    let mut s = line.trim();
    loop {
        let before = s;
        s = s
            .trim_start_matches(['-', '*', '>', '#', '.'])
            .trim_end_matches(['.', ',', '!'])
            .trim_matches(['`', '"', '\'', '*'])
            .trim();
        if s == before {
            return s.to_string();
        }
    }
}

fn last_fence(text: &str) -> Option<String> {
    let mut best: Option<String> = None;
    let mut buf: Vec<&str> = Vec::new();
    let mut open = false;
    for line in text.lines() {
        if line.trim_start().starts_with("```") {
            if open {
                best = Some(buf.join("\n"));
                buf.clear();
            }
            open = !open;
            continue;
        }
        if open {
            buf.push(line);
        }
    }
    if open && !buf.is_empty() {
        best = Some(buf.join("\n"));
    }
    best
}

// ── drivers ──────────────────────────────────────────────────────────────

pub async fn play(p: &Player, view: &str, seat: usize) -> Result<Answer, String> {
    match p.kind.trim().to_lowercase().as_str() {
        "model" | "llm" => model(p, view, seat).await,
        "agent_mod" | "agent" => agent_mod(p, view, seat).await,
        "http" | "webhook" => http(p, view, seat).await,
        "wasm" | "class" | "human" => Err(format!(
            "a `{}` player moves in the execution layer, not on the server",
            p.kind
        )),
        other => Err(format!("unknown player kind `{other}` — expected one of {KINDS:?}")),
    }
}

/// Any OpenAI-compatible chat endpoint.
///
/// Naming neither `base` nor `model` is the free seat: the liquidai module on
/// this box runs LFM weights here, so an arena anyone can play in doesn't ask
/// for a key first. It is only ever a default — `base` and `model` win, and if
/// nothing local is serving this falls back to OpenRouter as it always did.
///
/// config: { model?, base?, key?, system?, temperature?, max_tokens? }
async fn model(p: &Player, view: &str, seat: usize) -> Result<Answer, String> {
    let asked = cfg(p, "base").map(|b| b.trim_end_matches('/').to_string());
    // probe only when the answer could be local: a player pointed at OpenRouter
    // shouldn't wait on a health check for a module it isn't calling
    let local = match asked.as_deref() {
        Some(b) if !liquidai::is_local(b) => None,
        _ => liquidai::serving(client()).await,
    };
    let base = asked.unwrap_or_else(|| {
        if local.is_some() { liquidai::base() } else { OPENROUTER.to_string() }
    });
    let name = match cfg(p, "model") {
        Some(n) => n.to_string(),
        None if liquidai::is_local(&base) => {
            local.clone().unwrap_or_else(|| liquidai::DEFAULT_MODEL.to_string())
        }
        None => return Err("a model player needs config.model — or leave `base` \
                            unset to play on the liquidai model running on this box"
            .into()),
    };
    let name = name.as_str();
    let prompt = brief(view, seat, cfg(p, "brief").unwrap_or(""));

    let mut messages = vec![];
    if let Some(sys) = cfg(p, "system") {
        messages.push(json!({ "role": "system", "content": sys }));
    }
    messages.push(json!({ "role": "user", "content": prompt }));

    let mut body = json!({
        "model": name,
        "messages": messages,
        "temperature": p.config.get("temperature").and_then(|v| v.as_f64()).unwrap_or(0.0),
    });
    if let Some(max) = p.config.get("max_tokens").and_then(|v| v.as_u64()) {
        body["max_tokens"] = json!(max);
    }

    let provider = provider_for(&base);
    let key = api_key(p, provider).or_else(|| {
        // liquidai takes a session token rather than a bought key, and the box
        // it runs on can mint one for itself
        (provider == "liquidai").then(liquidai::token).flatten()
    });
    let mut req = client().post(format!("{base}/chat/completions")).json(&body);
    if let Some(key) = key {
        req = req.bearer_auth(key);
    }
    // OpenRouter attributes traffic by these; harmless everywhere else.
    req = req
        .header("HTTP-Referer", "https://mod.arena")
        .header("X-Title", "mod arena");

    let resp = req.send().await.map_err(|e| format!("{base} unreachable: {e}"))?;
    let status = resp.status();
    let text = resp.text().await.map_err(|e| format!("{base} gave no body ({status}): {e}"))?;
    if !status.is_success() {
        return Err(format!(
            "{name} answered {status}: {}",
            text.chars().take(300).collect::<String>()
        ));
    }
    let out: Value = serde_json::from_str(&text)
        .map_err(|e| format!("{name} returned non-JSON: {e}"))?;
    if let Some(err) = out.get("error") {
        let msg = err.get("message").and_then(|m| m.as_str()).unwrap_or("");
        return Err(format!("{name}: {}", if msg.is_empty() { err.to_string() } else { msg.into() }));
    }

    let raw = out
        .pointer("/choices/0/message/content")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    if raw.trim().is_empty() {
        return Err(format!("{name} returned an empty reply"));
    }
    Ok(Answer {
        mv: extract_move(&raw),
        raw,
        note: String::new(),
        meta: json!({
            "driver": "model", "model": name, "base": base, "provider": provider,
            "free": provider == "liquidai",
            "usage": out.get("usage").cloned().unwrap_or(Value::Null),
        }),
    })
}

/// An agent in this fleet's `agent` module.
///
/// config: { agent?, model?, base?, prompt?, toolbox?, steps?, free?, key? }
async fn agent_mod(p: &Player, view: &str, seat: usize) -> Result<Answer, String> {
    let base = cfg(p, "base").unwrap_or(AGENT_MOD_BASE).trim_end_matches('/').to_string();
    let mut body = json!({
        "query": brief(view, seat, cfg(p, "brief").unwrap_or("")),
        "steps": p.config.get("steps").and_then(|v| v.as_u64()).unwrap_or(2),
        "temperature": 0.0,
    });
    for key in ["agent", "model", "provider", "prompt", "toolbox", "key"] {
        if let Some(v) = cfg(p, key) {
            body[key] = json!(v);
        }
    }
    if p.config.get("free").and_then(|v| v.as_bool()).unwrap_or(false) {
        body["free"] = json!(true);
    }

    let out: Value = client()
        .post(format!("{base}/run"))
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("agent module at {base} unreachable: {e}"))?
        .json()
        .await
        .map_err(|e| format!("agent module returned non-JSON: {e}"))?;
    if let Some(err) = out.get("error").and_then(|v| v.as_str()) {
        return Err(format!("agent module: {err}"));
    }

    // The reply is the summary if there is one, else the last thing any step
    // said — an agent that answered by calling a tool still answered.
    let mut raw = out.get("summary").and_then(|v| v.as_str()).unwrap_or("").to_string();
    if raw.trim().is_empty() {
        if let Some(steps) = out.get("result").and_then(|v| v.as_array()) {
            for step in steps.iter().rev() {
                for key in ["result", "text", "message", "content"] {
                    if let Some(s) = step.get(key).and_then(|v| v.as_str()) {
                        if !s.trim().is_empty() {
                            raw = s.to_string();
                            break;
                        }
                    }
                }
                if !raw.is_empty() {
                    break;
                }
            }
        }
    }
    if raw.trim().is_empty() {
        return Err("the agent returned nothing to read a move out of".into());
    }
    Ok(Answer {
        mv: extract_move(&raw),
        raw,
        note: String::new(),
        meta: json!({ "driver": "agent_mod", "base": base, "agent": cfg(p, "agent").unwrap_or("") }),
    })
}

/// Any endpoint. We post the view and read a move back — from a `move` field
/// if the reply is JSON, otherwise out of the text.
///
/// config: { url, headers?, field? }
async fn http(p: &Player, view: &str, seat: usize) -> Result<Answer, String> {
    let url = cfg(p, "url").ok_or("an http player needs config.url")?;
    let mut req = client().post(url).json(&json!({
        "view": view,
        "seat": seat,
        "prompt": brief(view, seat, cfg(p, "brief").unwrap_or("")),
    }));
    if let Some(h) = p.config.get("headers").and_then(|v| v.as_object()) {
        for (k, v) in h {
            if let Some(s) = v.as_str() {
                req = req.header(k.as_str(), s);
            }
        }
    }
    let resp = req.send().await.map_err(|e| format!("{url} unreachable: {e}"))?;
    let status = resp.status();
    let text = resp.text().await.map_err(|e| format!("{url} gave no body ({status}): {e}"))?;
    if !status.is_success() {
        return Err(format!("{url} answered {status}: {}", text.chars().take(300).collect::<String>()));
    }

    let mv = match serde_json::from_str::<Value>(&text) {
        Ok(v) => {
            let field = cfg(p, "field").unwrap_or("move");
            v.get(field)
                .or_else(|| v.get("move"))
                .or_else(|| v.get("action"))
                .and_then(|m| m.as_str())
                .map(|s| s.trim().to_string())
                .unwrap_or_else(|| extract_move(&text))
        }
        Err(_) => extract_move(&text),
    };
    if mv.is_empty() {
        return Err(format!("{url} returned no move"));
    }
    Ok(Answer {
        mv,
        raw: text,
        note: String::new(),
        meta: json!({ "driver": "http", "url": url }),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn takes_a_bare_move() {
        assert_eq!(extract_move("rock"), "rock");
        assert_eq!(extract_move("  B2  \n"), "B2");
    }

    #[test]
    fn takes_the_move_out_of_narration() {
        assert_eq!(
            extract_move("Let me think. The centre is strong.\n\ne5"),
            "e5"
        );
    }

    #[test]
    fn an_explicit_tag_beats_the_last_line() {
        assert_eq!(
            extract_move("MOVE: rock\n\nI hope that works out for me."),
            "rock"
        );
    }

    #[test]
    fn reads_the_move_out_of_a_fence() {
        assert_eq!(extract_move("here you go:\n```\nb2\n```\nthanks!"), "b2");
    }

    #[test]
    fn strips_the_decoration_a_model_puts_on_it() {
        assert_eq!(extract_move("**paper**"), "paper");
        assert_eq!(extract_move("- scissors"), "scissors");
        assert_eq!(extract_move("`C3`."), "C3");
    }

    #[test]
    fn never_returns_more_than_one_line() {
        let out = extract_move("I'll play rock\nbecause it beats scissors");
        assert!(!out.contains('\n'), "{out:?}");
    }

    #[test]
    fn an_empty_reply_is_an_empty_move_not_a_panic() {
        assert_eq!(extract_move(""), "");
        assert_eq!(extract_move("   \n\n  "), "");
    }

    #[test]
    fn picks_the_key_provider_from_the_base_url() {
        assert_eq!(provider_for("https://openrouter.ai/api/v1"), "openrouter");
        assert_eq!(provider_for("https://api.venice.ai/api/v1"), "venice");
        assert_eq!(provider_for("http://127.0.0.1:11434/v1"), "modarena");
        assert_eq!(provider_for("http://127.0.0.1:50460/v1"), "liquidai");
    }
}
