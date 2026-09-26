//! vibe — write a game or a player with the build agent.
//!
//! A session is a directory under `~/.mod/arena/vibe/<id>/` holding one file,
//! the class, with `ARENA.md` beside it. It starts from a template or from the
//! source of a module already stored here — that second case is a fork. Each
//! round hands the file and one sentence to the build module's job server
//! (orbit/build: Claude Code with a task ledger), waits for the job, and reads
//! the file back the way an upload would be read. Storing the result is
//! `put_class` on the text, so nothing here is taken on trust either: the
//! registry reads what the agent wrote and says what it became.
//!
//! The arena talks to build as itself. build's job server validates its own
//! session token — `address:time:hmac` under `~/.mod/build/server.secret` —
//! which only a process on build's host can mint. That is exactly where a
//! sibling module's calls come from, and it is why a vibe never needs a wallet
//! round-trip: the jobs land in the ledger under the box owner's address,
//! which is whose account is paying for them.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use crate::{arena, blobs, docs, mcp};

/// The prelude a Rust class is compiled against — it goes into ARENA.md so
/// the agent writes against the real thing rather than a description of it.
const PRELUDE: &str = include_str!("../../rustclass/prelude.rs");

/// Wall-clock cap on one round, after which the job is cancelled.
const ROUND_TIMEOUT: Duration = Duration::from_secs(900);
/// How often a running job is looked at.
const POLL: Duration = Duration::from_millis(1500);
/// Largest sentence a round will take.
const MAX_PROMPT: usize = 4000;
/// How much of a job's transcript a session card carries.
const LOG_TAIL: usize = 6000;

#[derive(Clone, Serialize, Deserialize)]
pub struct Round {
    pub prompt: String,
    pub job: String,
    pub status: String,
    pub started: u64,
    #[serde(default)]
    pub finished: Option<u64>,
    #[serde(default)]
    pub log: String,
    #[serde(default)]
    pub cost_usd: Option<String>,
    #[serde(default)]
    pub error: Option<String>,
}

#[derive(Clone, Serialize, Deserialize)]
pub struct Session {
    pub id: String,
    /// `game` or `player` — what the session is trying to write.
    pub role: String,
    /// `python` or `rust`.
    pub lang: String,
    /// The name the result is offered under; the class name inside the file
    /// is the agent's business.
    pub name: String,
    /// The file the agent edits, under the session directory.
    pub file: String,
    /// Where it started: `{"template": role}` or `{"module": id, "name": …}`.
    pub from: Value,
    /// ready · running · done · failed · cancelled · stored
    pub status: String,
    pub rounds: Vec<Round>,
    #[serde(default)]
    pub error: Option<String>,
    /// The card of the module this became, once stored.
    #[serde(default)]
    pub stored: Option<Value>,
    pub created: u64,
    pub updated: u64,
}

fn now() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

fn dir() -> PathBuf {
    blobs::state_dir().join("vibe")
}

fn sessions() -> &'static Mutex<HashMap<String, Session>> {
    static S: OnceLock<Mutex<HashMap<String, Session>>> = OnceLock::new();
    S.get_or_init(|| Mutex::new(HashMap::new()))
}

fn client() -> &'static reqwest::Client {
    static C: OnceLock<reqwest::Client> = OnceLock::new();
    C.get_or_init(|| {
        reqwest::Client::builder()
            .timeout(Duration::from_secs(30))
            .build()
            .unwrap_or_else(|_| reqwest::Client::new())
    })
}

// ── the build module ─────────────────────────────────────────────────────

/// Where the build module's job server answers. `ARENA_BUILD_URL=off` turns
/// the agent off — the tests run that way, and so does a box without build.
fn build_url() -> Option<String> {
    let raw = std::env::var("ARENA_BUILD_URL").unwrap_or_else(|_| "http://127.0.0.1:8890".into());
    let raw = raw.trim().trim_end_matches('/').to_string();
    if raw.is_empty() || raw.eq_ignore_ascii_case("off") || raw.eq_ignore_ascii_case("none") {
        return None;
    }
    Some(raw)
}

fn secret_path() -> PathBuf {
    if let Ok(p) = std::env::var("ARENA_BUILD_SECRET") {
        return PathBuf::from(p);
    }
    let home = std::env::var("HOME").unwrap_or_else(|_| "/root".into());
    PathBuf::from(home).join(".mod").join("build").join("server.secret")
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// HMAC-SHA256 by the book (RFC 2104) — one function is cheaper than a crate.
fn hmac_sha256(key: &[u8], msg: &[u8]) -> [u8; 32] {
    let mut k = [0u8; 64];
    if key.len() > 64 {
        k[..32].copy_from_slice(&Sha256::digest(key));
    } else {
        k[..key.len()].copy_from_slice(key);
    }
    let ipad: Vec<u8> = k.iter().map(|b| b ^ 0x36).collect();
    let opad: Vec<u8> = k.iter().map(|b| b ^ 0x5c).collect();
    let inner = Sha256::new().chain_update(&ipad).chain_update(msg).finalize();
    Sha256::new().chain_update(&opad).chain_update(inner).finalize().into()
}

/// The address build's ledger will file the jobs under: its owner, read off
/// its own public `/owner` card. Cached — it does not change under us.
async fn build_owner(base: &str) -> Result<String, String> {
    static OWNER: OnceLock<Mutex<Option<String>>> = OnceLock::new();
    let cell = OWNER.get_or_init(|| Mutex::new(None));
    if let Some(o) = cell.lock().unwrap_or_else(|e| e.into_inner()).clone() {
        return Ok(o);
    }
    let v: Value = client()
        .get(format!("{base}/owner"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .map_err(|e| format!("build: could not reach {base}: {e}"))?
        .json()
        .await
        .map_err(|e| format!("build: /owner did not answer JSON: {e}"))?;
    let owner = v
        .get("owner")
        .and_then(|o| o.as_str())
        .map(|s| s.trim().to_lowercase())
        .filter(|s| !s.is_empty())
        .ok_or("build: has no owner — the job server would refuse every task")?;
    *cell.lock().unwrap_or_else(|e| e.into_inner()) = Some(owner.clone());
    Ok(owner)
}

/// A session token for build, minted the way build's own SDK mints one for
/// a sibling: `address:time:hmac` under its 0600 server secret.
async fn build_token(base: &str) -> Result<String, String> {
    let secret = std::fs::read(secret_path()).map_err(|e| {
        format!(
            "build: cannot read its server secret at {} ({e}) — the arena has to share build's host to hand it work",
            secret_path().display()
        )
    })?;
    if secret.len() != 32 {
        return Err("build: its server secret is not 32 bytes".into());
    }
    let owner = build_owner(base).await?;
    let payload = format!("{owner}:{}", now());
    Ok(format!("{payload}:{}", hex(&hmac_sha256(&secret, payload.as_bytes()))))
}

/// Can this arena hand a file to the build agent right now, and why not.
pub async fn availability() -> Value {
    let Some(base) = build_url() else {
        return json!({
            "available": false, "build": null,
            "reason": "the build agent is off (ARENA_BUILD_URL=off) — a fork and a template still work, the sentence does not",
        });
    };
    let health = client()
        .get(format!("{base}/health"))
        .timeout(Duration::from_secs(4))
        .send()
        .await;
    let up = match health {
        Ok(r) if r.status().is_success() => true,
        Ok(r) => {
            return json!({ "available": false, "build": base, "reason": format!("build answered {} at /health", r.status()) })
        }
        Err(e) => {
            return json!({ "available": false, "build": base, "reason": format!("build is not answering at {base}: {e}") })
        }
    };
    let token = build_token(&base).await;
    json!({
        "available": up && token.is_ok(),
        "build": base,
        "ledger": format!("{base}/jobs"),
        "reason": token.err(),
        "model": std::env::var("ARENA_VIBE_MODEL").ok().filter(|m| !m.trim().is_empty()),
        "max_running": max_running(),
        "running": sessions().lock().unwrap_or_else(|e| e.into_inner()).values().filter(|s| s.status == "running").count(),
    })
}

fn max_running() -> usize {
    std::env::var("ARENA_VIBE_MAX").ok().and_then(|v| v.parse().ok()).unwrap_or(2)
}

async fn build_submit(base: &str, body: Value) -> Result<String, String> {
    let token = build_token(base).await?;
    let r = client()
        .post(format!("{base}/jobs"))
        .bearer_auth(token)
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("build: submit failed: {e}"))?;
    let status = r.status();
    let v: Value = r.json().await.unwrap_or_else(|_| json!({}));
    if !status.is_success() {
        let why = v.get("error").and_then(|e| e.as_str()).unwrap_or("no reason given");
        return Err(format!("build: refused the task ({status}): {why}"));
    }
    v.get("id")
        .and_then(|i| i.as_str())
        .map(str::to_string)
        .ok_or_else(|| format!("build: accepted the task but named no job: {v}"))
}

async fn build_job(base: &str, id: &str) -> Result<Value, String> {
    client()
        .get(format!("{base}/jobs/{id}"))
        .send()
        .await
        .map_err(|e| format!("build: {e}"))?
        .json()
        .await
        .map_err(|e| format!("build: job {id} did not answer JSON: {e}"))
}

async fn build_cancel(base: &str, id: &str) -> Result<(), String> {
    let token = build_token(base).await?;
    client()
        .post(format!("{base}/jobs/{id}/cancel"))
        .bearer_auth(token)
        .send()
        .await
        .map_err(|e| format!("build: cancel failed: {e}"))?;
    Ok(())
}

// ── sessions on disk ─────────────────────────────────────────────────────

fn save(s: &Session) {
    let d = dir().join(&s.id);
    std::fs::create_dir_all(&d).ok();
    if let Ok(text) = serde_json::to_string_pretty(s) {
        std::fs::write(d.join("session.json"), text).ok();
    }
    sessions().lock().unwrap_or_else(|e| e.into_inner()).insert(s.id.clone(), s.clone());
}

fn load(id: &str) -> Option<Session> {
    if let Some(s) = sessions().lock().unwrap_or_else(|e| e.into_inner()).get(id) {
        return Some(s.clone());
    }
    let text = std::fs::read_to_string(dir().join(id).join("session.json")).ok()?;
    let s: Session = serde_json::from_str(&text).ok()?;
    sessions().lock().unwrap_or_else(|e| e.into_inner()).insert(id.to_string(), s.clone());
    Some(s)
}

fn find(key: &str) -> Result<Session, String> {
    let key = key.trim();
    if key.is_empty() {
        return Err("a vibe session id is needed".into());
    }
    if let Some(s) = load(key) {
        return Ok(s);
    }
    // A prefix is enough when it is unambiguous, the way a module id is.
    let mut hits: Vec<String> = std::fs::read_dir(dir())
        .map(|rd| {
            rd.filter_map(|e| e.ok())
                .map(|e| e.file_name().to_string_lossy().to_string())
                .filter(|n| n.starts_with(key))
                .collect()
        })
        .unwrap_or_default();
    match hits.len() {
        1 => load(&hits.remove(0)).ok_or_else(|| format!("no vibe session `{key}`")),
        0 => Err(format!("no vibe session `{key}`")),
        n => Err(format!("`{key}` matches {n} vibe sessions — give more of the id")),
    }
}

fn source_of(s: &Session) -> Result<String, String> {
    std::fs::read_to_string(&s.file).map_err(|e| format!("the session's file is gone ({e})"))
}

/// A session id: the first 12 hex of a hash over the moment and a counter,
/// which is short enough to type and long enough not to collide.
fn new_id() -> String {
    static N: Mutex<u64> = Mutex::new(0);
    let mut n = N.lock().unwrap_or_else(|e| e.into_inner());
    *n += 1;
    let seed = format!("{}:{}:{}", now(), *n, std::process::id());
    hex(&Sha256::digest(seed.as_bytes()))[..12].to_string()
}

fn ext(lang: &str) -> &'static str {
    if lang == "rust" { "rs" } else { "py" }
}

/// The contract, written beside the file so the agent reads the real thing:
/// the docs page for the role, the sandbox page, and the Rust prelude.
fn contract(role: &str, lang: &str) -> String {
    let page = |slug: &str| {
        docs::page(&json!({ "slug": slug }))
            .ok()
            .and_then(|p| p.get("markdown").and_then(|m| m.as_str()).map(str::to_string))
            .unwrap_or_default()
    };
    let mut out = String::new();
    out.push_str("# ARENA.md — the contract this file is written against\n\n");
    out.push_str(&format!(
        "This directory is one vibe session of the arena module. The file beside this one is a {role} as a {lang} class. \
         Edit that file only. What follows is the arena's own documentation for a {role}, then the sandbox it runs in.\n\n"
    ));
    out.push_str(&page(role));
    out.push_str("\n\n---\n\n");
    out.push_str(&page("sandbox"));
    if lang == "rust" {
        out.push_str("\n\n---\n\n## The prelude a Rust class is compiled against\n\n```rust\n");
        out.push_str(PRELUDE);
        out.push_str("\n```\n");
    } else {
        out.push_str("\n\n---\n\n## Imports the Python sandbox allows\n\n");
        out.push_str(&crate::klass::ALLOWED_IMPORTS.join(", "));
        out.push_str("\n\nNothing else — no os, sys, time, socket, subprocess, urllib, and no open().\n");
    }
    out
}

fn system_prompt(s: &Session) -> String {
    let arena = mcp::base();
    let check = if s.lang == "rust" {
        format!(
            "Check your work by posting the file to the arena's reader: \
             `python3 -c 'import json,sys,urllib.request as u; r=u.urlopen(u.Request(\"{arena}/inspect\", data=json.dumps({{\"text\": open(\"{file}\").read()}}).encode(), headers={{\"content-type\":\"application/json\"}})); print(r.read().decode())'` \
             — it must answer \"role\": \"{role}\" and \"lang\": \"rust\". The class is compiled to wasm32-unknown-unknown with no crates: no std::fs, std::net, std::time, std::thread; use `arena::random()` and `arena::log()` from the prelude in ARENA.md.",
            file = s.file, role = s.role
        )
    } else {
        format!(
            "Check your work: `python3 -m py_compile {file}` for syntax, then post the file to the arena's reader: \
             `python3 -c 'import json,sys,urllib.request as u; r=u.urlopen(u.Request(\"{arena}/inspect\", data=json.dumps({{\"text\": open(\"{file}\").read()}}).encode(), headers={{\"content-type\":\"application/json\"}})); print(r.read().decode())'` \
             — it must answer \"role\": \"{role}\" and every import must be allowed. You may also drive the class directly in python to play a few moves against itself.",
            file = s.file, role = s.role
        )
    };
    let defines = if s.role == "game" {
        "A game defines view(self, seat), step(self, moves), done(self) and result(self); it may define __init__(self, seed), turn(self), and the attributes name, players, max_turns."
    } else {
        "A player defines play(self, view, seat) and returns the move as text; it may define __init__(self, seed) and the attribute name."
    };
    format!(
        "You are writing ONE file for the arena module: {file}. It is a {role}, as a {lang} class, and it already holds the starting point. \
         Read ARENA.md in the same directory first — it is the whole contract, and it is short. Then edit {file} in place. \
         {defines} The registry reads the file and decides what it is; nothing is declared anywhere else. \
         Keep it one self-contained class in one file. {check} \
         Do not create other files, do not start servers, do not touch anything outside this directory, and do not upload anything — the person decides that. \
         When finished, reply with two or three sentences on what the class does now and anything you could not do.",
        file = s.file, role = s.role, lang = s.lang
    )
}

// ── the door ─────────────────────────────────────────────────────────────

/// Start a session, or continue one, and — given a sentence — hand it to the
/// build agent. Without a sentence this is a fork or a template: a session
/// holding the source it started from, ready for one.
pub async fn vibe(args: &Value) -> Result<Value, String> {
    let str_arg = |k: &str| args.get(k).and_then(|v| v.as_str()).map(str::trim).filter(|s| !s.is_empty()).map(str::to_string);
    let prompt = str_arg("prompt").unwrap_or_default();
    if prompt.chars().count() > MAX_PROMPT {
        return Err(format!("the sentence is {} characters — {MAX_PROMPT} is the most a round takes", prompt.chars().count()));
    }

    let mut s = if let Some(id) = str_arg("session") {
        let mut s = find(&id)?;
        if s.status == "running" {
            return Err(format!("vibe session {} is still running job {} — wait for it, or cancel it", s.id, s.job().unwrap_or_default()));
        }
        // The person may have edited the file by hand between rounds — the
        // text they are holding is the starting point, not what the agent
        // last wrote.
        if let Some(text) = args.get("source").and_then(|v| v.as_str()).filter(|t| !t.trim().is_empty()) {
            std::fs::write(&s.file, text).map_err(|e| format!("could not write the session's file: {e}"))?;
        }
        if let Some(name) = str_arg("name") {
            s.name = name;
        }
        s
    } else {
        start(args)?
    };

    if prompt.is_empty() {
        s.updated = now();
        save(&s);
        return card(&s).await;
    }

    let base = build_url().ok_or(
        "build: the build agent is off on this arena (ARENA_BUILD_URL=off) — the session holds its source; edit it by hand and store it",
    )?;
    let running = sessions().lock().unwrap_or_else(|e| e.into_inner()).values().filter(|x| x.status == "running" && x.id != s.id).count();
    if running >= max_running() {
        return Err(format!("build: {running} vibe rounds are already running on this box — {} at a time is the cap, try again in a moment", max_running()));
    }

    let round_no = s.rounds.len() + 1;
    let task = if round_no == 1 {
        prompt.clone()
    } else {
        format!("{prompt}\n\n(Round {round_no}: the file holds the previous round's result. Change it as asked and keep the rest.)")
    };
    let model = str_arg("model")
        .or_else(|| std::env::var("ARENA_VIBE_MODEL").ok().filter(|m| !m.trim().is_empty()))
        .unwrap_or_default();
    let body = json!({
        "prompt": task,
        "model": model,
        "work_dir": dir().join(&s.id).to_string_lossy(),
        "system_prompt": system_prompt(&s),
    });
    let job = build_submit(&base, body).await?;
    s.rounds.push(Round {
        prompt,
        job: job.clone(),
        status: "running".into(),
        started: now(),
        finished: None,
        log: String::new(),
        cost_usd: None,
        error: None,
    });
    s.status = "running".into();
    s.error = None;
    s.updated = now();
    save(&s);

    let id = s.id.clone();
    tokio::spawn(async move { follow(base, id, job).await });
    card(&s).await
}

/// A fresh session: from a stored module (a fork) or from the template.
fn start(args: &Value) -> Result<Session, String> {
    let str_arg = |k: &str| args.get(k).and_then(|v| v.as_str()).map(str::trim).filter(|s| !s.is_empty()).map(str::to_string);
    let mut role = str_arg("role").unwrap_or_else(|| "game".into()).to_lowercase();
    let mut lang = str_arg("lang").unwrap_or_else(|| "python".into()).to_lowercase();
    lang = match lang.as_str() {
        "class" | "py" | "python" => "python".into(),
        "rs" | "rust" => "rust".into(),
        other => return Err(format!("lang `{other}` — a vibe writes a python or a rust class")),
    };

    let (source, from, name) = if let Some(key) = str_arg("from").or_else(|| str_arg("module")) {
        let m = arena::get_module(&key, true)?;
        let m_lang = m.get("lang").and_then(|v| v.as_str()).unwrap_or("wasm");
        if m_lang == "wasm" {
            return Err(format!(
                "{} is compiled wasm — a fork starts from a class. Fork a python or rust module, or start from the template",
                m.get("name").and_then(|v| v.as_str()).unwrap_or(&key)
            ));
        }
        let source = m.get("source").and_then(|v| v.as_str()).ok_or("that module has no readable source to fork")?.to_string();
        let m_role = m.get("role").and_then(|v| v.as_str()).unwrap_or("class");
        if matches!(m_role, "game" | "player") {
            role = m_role.to_string();
        }
        lang = m_lang.to_string();
        let m_name = m.get("name").and_then(|v| v.as_str()).unwrap_or("module").to_string();
        let from = json!({ "module": m.get("id"), "name": m_name, "role": m_role });
        (source, from, str_arg("name").unwrap_or_else(|| format!("{m_name}-fork")))
    } else if let Some(text) = args.get("source").and_then(|v| v.as_str()).filter(|t| !t.trim().is_empty()) {
        (text.to_string(), json!({ "source": "given" }), str_arg("name").unwrap_or_else(|| format!("my{role}")))
    } else {
        if !matches!(role.as_str(), "game" | "player") {
            return Err(format!("role `{role}` — a vibe writes a game or a player"));
        }
        let abi = mcp::game_abi(&role, if lang == "rust" { "rust" } else { "class" });
        let text = abi.get("template").and_then(|v| v.as_str()).ok_or("no template for that role and language")?.to_string();
        (text, json!({ "template": role }), str_arg("name").unwrap_or_else(|| format!("my{role}")))
    };

    let id = new_id();
    let d = dir().join(&id);
    std::fs::create_dir_all(&d).map_err(|e| format!("could not make the session directory: {e}"))?;
    let file = d.join(format!("{role}.{}", ext(&lang)));
    std::fs::write(&file, &source).map_err(|e| format!("could not write the starting point: {e}"))?;
    std::fs::write(d.join("ARENA.md"), contract(&role, &lang)).ok();

    let s = Session {
        id,
        role,
        lang,
        name,
        file: file.to_string_lossy().to_string(),
        from,
        status: "ready".into(),
        rounds: vec![],
        error: None,
        stored: None,
        created: now(),
        updated: now(),
    };
    save(&s);
    Ok(s)
}

impl Session {
    fn job(&self) -> Option<String> {
        self.rounds.last().map(|r| r.job.clone())
    }
}

fn tail(text: &str) -> String {
    if text.len() <= LOG_TAIL {
        return text.to_string();
    }
    let cut = text.len() - LOG_TAIL;
    let at = text.char_indices().map(|(i, _)| i).find(|&i| i >= cut).unwrap_or(cut);
    format!("…{}", &text[at..])
}

/// Wait on a job and write down how it ended. The job outlives this: a
/// restart forgets it is being followed, but `get_vibe` re-reads it on demand.
async fn follow(base: String, id: String, job: String) {
    let started = std::time::Instant::now();
    loop {
        tokio::time::sleep(POLL).await;
        let Ok(j) = build_job(&base, &job).await else { continue };
        let status = j.get("status").and_then(|v| v.as_str()).unwrap_or("").to_lowercase();
        if matches!(status.as_str(), "completed" | "failed" | "cancelled") {
            settle(&id, &j, &status);
            return;
        }
        if started.elapsed() > ROUND_TIMEOUT {
            build_cancel(&base, &job).await.ok();
            if let Some(mut s) = load(&id) {
                let why = format!("the round ran past {}s and was cancelled", ROUND_TIMEOUT.as_secs());
                if let Some(r) = s.rounds.last_mut() {
                    r.status = "cancelled".into();
                    r.finished = Some(now());
                    r.error = Some(why.clone());
                }
                s.status = "cancelled".into();
                s.error = Some(why);
                s.updated = now();
                save(&s);
            }
            return;
        }
    }
}

fn settle(id: &str, job: &Value, status: &str) {
    let Some(mut s) = load(id) else { return };
    let output = job.get("output").and_then(|v| v.as_str()).unwrap_or("");
    let error = job.get("error").and_then(|v| v.as_str()).map(str::to_string);
    let cost = job.get("cost_usd").and_then(|v| v.as_str()).map(str::to_string);
    let mapped = match status {
        "completed" => "done",
        "cancelled" => "cancelled",
        _ => "failed",
    };
    if let Some(r) = s.rounds.last_mut() {
        r.status = mapped.into();
        r.finished = Some(now());
        r.log = tail(output);
        r.cost_usd = cost;
        r.error = error.clone();
    }
    s.status = mapped.into();
    s.error = error;
    s.updated = now();
    save(&s);
}

/// The session as the console and the tools see it: what it is, what it
/// holds right now, and what the registry would make of that.
pub async fn card(s: &Session) -> Result<Value, String> {
    let source = source_of(s).unwrap_or_default();
    let read = arena::inspect(&json!({ "text": source })).unwrap_or_else(|e| json!({ "error": e }));
    let mut rounds: Vec<Value> = s.rounds.iter().map(|r| json!(r)).collect();
    // A running round's transcript is live on build; show it as it happens.
    if s.status == "running" {
        if let (Some(base), Some(job)) = (build_url(), s.job()) {
            if let Ok(j) = build_job(&base, &job).await {
                let status = j.get("status").and_then(|v| v.as_str()).unwrap_or("").to_lowercase();
                if matches!(status.as_str(), "completed" | "failed" | "cancelled") {
                    // The follower missed it (a restart, say) — settle it now.
                    settle(&s.id, &j, &status);
                    if let Some(fresh) = load(&s.id) {
                        return Box::pin(card(&fresh)).await;
                    }
                }
                if let Some(last) = rounds.last_mut() {
                    last["log"] = json!(tail(j.get("output").and_then(|v| v.as_str()).unwrap_or("")));
                }
            }
        }
    }
    let build = build_url();
    Ok(json!({
        "session": s.id,
        "role": s.role,
        "lang": s.lang,
        "name": s.name,
        "from": s.from,
        "status": s.status,
        "error": s.error,
        "file": s.file,
        "source": source,
        "reads_as": read,
        "rounds": rounds,
        "job": s.job(),
        "job_url": s.job().and_then(|j| build.as_ref().map(|b| format!("{b}/jobs/{j}"))),
        "stored": s.stored,
        "created": s.created,
        "updated": s.updated,
        "then": if s.stored.is_some() { "stored — it is in the registry now" }
                else if s.status == "running" { "wait: get_vibe until status is done, then read `source`" }
                else { "another sentence continues it (vibe with `session`); store_vibe puts it in the registry" },
    }))
}

pub async fn get(key: &str) -> Result<Value, String> {
    card(&find(key)?).await
}

pub fn list() -> Value {
    let mut all: Vec<Session> = std::fs::read_dir(dir())
        .map(|rd| {
            rd.filter_map(|e| e.ok())
                .filter_map(|e| load(&e.file_name().to_string_lossy()))
                .collect()
        })
        .unwrap_or_default();
    all.sort_by(|a, b| b.updated.cmp(&a.updated));
    json!({
        "count": all.len(),
        "sessions": all.iter().take(100).map(|s| json!({
            "session": s.id, "role": s.role, "lang": s.lang, "name": s.name,
            "from": s.from, "status": s.status, "rounds": s.rounds.len(),
            "stored": s.stored.as_ref().and_then(|m| m.get("id")),
            "updated": s.updated,
        })).collect::<Vec<_>>(),
    })
}

/// Put what the session holds into the registry — `put_class` on the text,
/// so the role is read off the file, never off the session. A player is
/// entered too, unless told not to.
pub async fn store(args: &Value) -> Result<Value, String> {
    let key = args.get("session").and_then(|v| v.as_str()).unwrap_or("");
    let mut s = find(key)?;
    if s.status == "running" {
        return Err(format!("vibe session {} is still running — wait for the round to finish", s.id));
    }
    let source = source_of(&s)?;
    let name = args
        .get("name")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|n| !n.is_empty())
        .unwrap_or(&s.name)
        .to_string();
    let mut body = json!({
        "source": source,
        "lang": s.lang,
        "name": name,
        "description": args.get("description").and_then(|v| v.as_str()).unwrap_or(""),
        "tags": ["class", "vibe"],
    });
    if let Some(a) = args.get("author") {
        body["author"] = a.clone();
    }
    let mut m = arena::put_class(&body)?;
    // A Rust class is compiled on the way in; ask for the wasm now rather
    // than letting one that will not build sit in the registry looking
    // playable until somebody seats it.
    if m.get("lang").and_then(|v| v.as_str()) == Some("rust") {
        let id = m.get("id").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let built = tokio::task::spawn_blocking(move || arena::compiled(&id)).await.map_err(|e| e.to_string())?;
        if let Err(e) = built {
            m["compile_error"] = json!(e);
        }
    }
    let role = m.get("role").and_then(|v| v.as_str()).unwrap_or("class").to_string();
    let enter = args.get("enter").and_then(|v| v.as_bool()).unwrap_or(true);
    if role == "player" && enter && m.get("compile_error").is_none() {
        let entered = arena::enter_player(&json!({
            "name": m.get("name"),
            "kind": "class",
            "config": { "module": m.get("id") },
        }));
        m["entered"] = match entered {
            Ok(p) => json!(p),
            Err(e) => json!({ "error": e }),
        };
    }
    s.stored = Some(json!({ "id": m.get("id"), "name": m.get("name"), "role": role }));
    s.status = "stored".into();
    s.name = m.get("name").and_then(|v| v.as_str()).unwrap_or(&name).to_string();
    s.updated = now();
    save(&s);
    m["session"] = json!(s.id);
    Ok(m)
}

pub async fn cancel(key: &str) -> Result<Value, String> {
    let mut s = find(key)?;
    if s.status != "running" {
        return Err(format!("vibe session {} is not running", s.id));
    }
    if let (Some(base), Some(job)) = (build_url(), s.job()) {
        build_cancel(&base, &job).await?;
    }
    if let Some(r) = s.rounds.last_mut() {
        r.status = "cancelled".into();
        r.finished = Some(now());
    }
    s.status = "cancelled".into();
    s.updated = now();
    save(&s);
    card(&s).await
}

pub fn delete(key: &str) -> Result<Value, String> {
    let s = find(key)?;
    std::fs::remove_dir_all(dir().join(&s.id)).map_err(|e| format!("could not remove the session: {e}"))?;
    sessions().lock().unwrap_or_else(|e| e.into_inner()).remove(&s.id);
    Ok(json!({ "deleted": s.id, "stored": s.stored }))
}
