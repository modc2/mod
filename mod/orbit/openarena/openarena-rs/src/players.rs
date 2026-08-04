//! Competitor drivers — the four ways an entrant can answer a task.
//!
//!     agent_mod   an agent in this fleet's `agent` module, over POST /run
//!     http        any endpoint that takes the task and hands back a program
//!     ap          an Agent Protocol v1 server (create task → step → output)
//!     static      a fixed program, for baselines and for humans
//!
//! Every driver returns the same thing — source code and the language it is
//! written in — because that is all the judge grades. Adding a fifth kind is
//! one match arm and one function.

use crate::judge;
use crate::store::{Agent, Task};
use serde_json::{json, Value};
use std::sync::OnceLock;
use std::time::Duration;

pub struct Play {
    pub code: String,
    pub language: String,
    pub meta: Value,
}

pub const KINDS: [&str; 4] = ["agent_mod", "http", "ap", "static"];

/// Where the fleet's agent module answers, unless a competitor says otherwise.
const AGENT_MOD_BASE: &str = "http://127.0.0.1:50117";

fn client() -> &'static reqwest::Client {
    static C: OnceLock<reqwest::Client> = OnceLock::new();
    C.get_or_init(|| {
        reqwest::Client::builder()
            .timeout(Duration::from_secs(900))
            .build()
            .unwrap_or_else(|_| reqwest::Client::new())
    })
}

fn cfg_str<'a>(agent: &'a Agent, key: &str) -> Option<&'a str> {
    agent
        .config
        .get(key)
        .and_then(|v| v.as_str())
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
}

// ── the brief handed to a competitor ─────────────────────────────────────

/// What the entrant is told. Hidden cases are never in here — an agent sees
/// the statement and the visible examples, exactly like a human would.
pub fn brief(task: &Task) -> String {
    let lang = if task.language == "any" {
        "any language the arena runs (python, javascript or bash)".to_string()
    } else {
        task.language.clone()
    };
    let mut s = format!("# {}\n\n{}\n\n", task.title, task.statement);
    s.push_str(&format!("Language: {lang}.\n"));
    match task.mode.as_str() {
        "unit" => s.push_str(&format!(
            "Your program is saved as `{}` and imported by hidden graders, so define \
             everything at module level and print nothing on import.\n",
            judge::entrypoint(&task.language)
        )),
        _ => s.push_str(
            "Your program reads its input from stdin and writes the answer to stdout. \
             Nothing else may be printed.\n",
        ),
    }
    if !task.starter.is_empty() {
        s.push_str(&format!("\nStarter code:\n```\n{}\n```\n", task.starter));
    }

    let shown: Vec<_> = task.tests.iter().filter(|c| !c.hidden).take(3).collect();
    if !shown.is_empty() && task.mode != "unit" {
        s.push_str("\nExamples:\n");
        for c in shown {
            s.push_str(&format!(
                "\n--- {} ---\ninput:\n{}\nexpected output:\n{}\n",
                c.name, c.stdin, c.expect
            ));
        }
    }
    s.push_str(&format!(
        "\nThere are {} graded cases in total, {} of them hidden. \
         Reply with exactly one fenced code block holding the complete program, and nothing else.\n",
        task.tests.len(),
        task.tests.iter().filter(|c| c.hidden).count()
    ));
    s
}

/// Pull the program out of a reply. Agents narrate, then show the code — the
/// last fenced block is the one they landed on.
pub fn extract_code(text: &str) -> (String, String) {
    let mut best: Option<(String, String)> = None;
    let mut lang = String::new();
    let mut buf: Vec<&str> = Vec::new();
    let mut open = false;

    for line in text.lines() {
        let t = line.trim_start();
        if t.starts_with("```") || t.starts_with("~~~") {
            if open {
                best = Some((buf.join("\n"), lang.clone()));
                buf.clear();
                open = false;
            } else {
                lang = t.trim_start_matches(['`', '~']).trim().to_lowercase();
                open = true;
            }
            continue;
        }
        if open {
            buf.push(line);
        }
    }
    // An unterminated fence still holds a program.
    if open && !buf.is_empty() && best.is_none() {
        best = Some((buf.join("\n"), lang.clone()));
    }

    match best {
        Some((code, lang)) => (code.trim_matches('\n').to_string(), lang),
        // No fence at all: take the reply as-is when it reads like code.
        None => {
            let t = text.trim();
            let codey = ["def ", "import ", "function ", "const ", "print(", "console.log", "#!/"]
                .iter()
                .any(|m| t.contains(m));
            if codey {
                (t.to_string(), String::new())
            } else {
                (String::new(), String::new())
            }
        }
    }
}

/// A language hint from a fence (```python) is only worth taking when the
/// arena can actually run it.
fn pick_language(hint: &str, task: &Task) -> String {
    if judge::runner(hint).is_some() && !hint.is_empty() && hint != "any" {
        return hint.to_string();
    }
    if task.language != "any" {
        return task.language.clone();
    }
    "python".into()
}

// ── drivers ──────────────────────────────────────────────────────────────

pub async fn play(agent: &Agent, task: &Task) -> Result<Play, String> {
    match agent.kind.trim().to_lowercase().as_str() {
        "static" | "fixed" => fixed(agent, task),
        "agent_mod" | "agent" | "mod" => agent_mod(agent, task).await,
        "http" | "webhook" => http_agent(agent, task).await,
        "ap" | "agent_protocol" => agent_protocol(agent, task).await,
        other => Err(format!(
            "unknown competitor kind `{other}` — expected one of {KINDS:?}"
        )),
    }
}

fn fixed(agent: &Agent, task: &Task) -> Result<Play, String> {
    let code = cfg_str(agent, "code")
        .or_else(|| cfg_str(agent, "solution"))
        .ok_or("a static competitor needs config.code")?;
    let lang = cfg_str(agent, "language").unwrap_or("");
    Ok(Play {
        code: code.to_string(),
        language: pick_language(lang, task),
        meta: json!({ "driver": "static" }),
    })
}

/// Every string a run produced, in order — summaries, responses, and the
/// contents an agent wrote to a file (which is often the program itself).
fn steps_text(resp: &Value) -> String {
    let mut parts: Vec<String> = Vec::new();
    let mut take = |v: Option<&Value>| {
        if let Some(s) = v.and_then(|v| v.as_str()) {
            if !s.trim().is_empty() {
                parts.push(s.to_string());
            }
        }
    };
    if let Some(steps) = resp.get("result").and_then(|v| v.as_array()) {
        for step in steps {
            let p = step.get("params");
            for key in ["content", "code", "text", "message", "summary"] {
                take(p.and_then(|p| p.get(key)));
            }
            take(step.get("result"));
        }
    }
    take(resp.get("summary"));
    parts.join("\n\n")
}

/// A competitor that is an agent in this fleet's `agent` module.
///
/// config: { base?, agent?, model?, prompt?, toolbox?, steps?, free?, key? }
async fn agent_mod(agent: &Agent, task: &Task) -> Result<Play, String> {
    let base = cfg_str(agent, "base")
        .unwrap_or(AGENT_MOD_BASE)
        .trim_end_matches('/')
        .to_string();

    let mut body = json!({
        "query": brief(task),
        "steps": agent.config.get("steps").and_then(|v| v.as_u64()).unwrap_or(4),
        "temperature": 0.0,
    });
    for key in ["agent", "model", "provider", "prompt", "toolbox", "key"] {
        if let Some(v) = cfg_str(agent, key) {
            body[key] = json!(v);
        }
    }
    if agent.config.get("free").and_then(|v| v.as_bool()).unwrap_or(false) {
        body["free"] = json!(true);
    }

    let resp = client()
        .post(format!("{base}/run"))
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("agent module at {base} unreachable: {e}"))?;
    let status = resp.status();
    let out: Value = resp
        .json()
        .await
        .map_err(|e| format!("agent module returned non-JSON ({status}): {e}"))?;
    if let Some(err) = out.get("error").and_then(|v| v.as_str()) {
        return Err(format!("agent module: {err}"));
    }

    let text = steps_text(&out);
    let (code, hint) = extract_code(&text);
    if code.trim().is_empty() {
        return Err("the agent returned no code block".into());
    }
    Ok(Play {
        language: pick_language(&hint, task),
        code,
        meta: json!({
            "driver": "agent_mod",
            "base": base,
            "agent": cfg_str(agent, "agent").unwrap_or(""),
            "model": cfg_str(agent, "model").unwrap_or(""),
            "run_task_id": out.get("task_id").cloned().unwrap_or(Value::Null),
            "steps": out.get("result").and_then(|v| v.as_array()).map(|a| a.len()).unwrap_or(0),
        }),
    })
}

/// Any HTTP endpoint. We POST the task and read a program back — either as a
/// `code` field or as text with a fenced block in it.
///
/// config: { url, headers?, field? }
async fn http_agent(agent: &Agent, task: &Task) -> Result<Play, String> {
    let url = cfg_str(agent, "url").ok_or("an http competitor needs config.url")?;
    let mut req = client().post(url).json(&json!({
        "task": task.view(false),
        "prompt": brief(task),
        "language": task.language,
        "mode": task.mode,
    }));
    if let Some(h) = agent.config.get("headers").and_then(|v| v.as_object()) {
        for (k, v) in h {
            if let Some(s) = v.as_str() {
                req = req.header(k.as_str(), s);
            }
        }
    }
    let resp = req
        .send()
        .await
        .map_err(|e| format!("{url} unreachable: {e}"))?;
    let status = resp.status();
    let text = resp
        .text()
        .await
        .map_err(|e| format!("{url} gave no body ({status}): {e}"))?;
    if !status.is_success() {
        return Err(format!("{url} answered {status}: {}", text.chars().take(300).collect::<String>()));
    }

    // A JSON reply may name the code directly; anything else is prose to mine.
    let (raw, hint) = match serde_json::from_str::<Value>(&text) {
        Ok(v) => {
            let field = cfg_str(agent, "field").unwrap_or("code");
            let direct = v
                .get(field)
                .or_else(|| v.get("code"))
                .and_then(|c| c.as_str())
                .map(String::from);
            let lang = v
                .get("language")
                .and_then(|l| l.as_str())
                .unwrap_or("")
                .to_string();
            match direct {
                Some(c) => (c, lang),
                None => {
                    let prose = ["output", "text", "result", "answer", "message"]
                        .iter()
                        .find_map(|k| v.get(*k).and_then(|x| x.as_str()))
                        .map(String::from)
                        .unwrap_or(text.clone());
                    let (c, h) = extract_code(&prose);
                    (c, if lang.is_empty() { h } else { lang })
                }
            }
        }
        Err(_) => extract_code(&text),
    };
    if raw.trim().is_empty() {
        return Err(format!("{url} returned no code"));
    }
    Ok(Play {
        language: pick_language(&hint, task),
        code: raw,
        meta: json!({ "driver": "http", "url": url }),
    })
}

/// Agent Protocol v1: create a task, drive it a step at a time, read what the
/// steps printed. https://agentprotocol.ai
///
/// config: { base, steps?, headers? }
async fn agent_protocol(agent: &Agent, task: &Task) -> Result<Play, String> {
    let base = cfg_str(agent, "base")
        .or_else(|| cfg_str(agent, "url"))
        .ok_or("an ap competitor needs config.base")?
        .trim_end_matches('/')
        .to_string();
    let max_steps = agent
        .config
        .get("steps")
        .and_then(|v| v.as_u64())
        .unwrap_or(6)
        .clamp(1, 30);

    let headers = agent.config.get("headers").and_then(|v| v.as_object()).cloned();
    let with_headers = |mut r: reqwest::RequestBuilder| {
        if let Some(h) = &headers {
            for (k, v) in h {
                if let Some(s) = v.as_str() {
                    r = r.header(k.as_str(), s);
                }
            }
        }
        r
    };

    let created: Value = with_headers(
        client()
            .post(format!("{base}/ap/v1/agent/tasks"))
            .json(&json!({ "input": brief(task) })),
    )
    .send()
    .await
    .map_err(|e| format!("{base} unreachable: {e}"))?
    .json()
    .await
    .map_err(|e| format!("{base} returned non-JSON on task create: {e}"))?;

    let ap_id = created
        .get("task_id")
        .and_then(|v| v.as_str())
        .ok_or_else(|| format!("{base} returned no task_id"))?
        .to_string();

    let mut transcript = String::new();
    let mut steps_run = 0u64;
    for _ in 0..max_steps {
        let step: Value = with_headers(
            client()
                .post(format!("{base}/ap/v1/agent/tasks/{ap_id}/steps"))
                .json(&json!({})),
        )
        .send()
        .await
        .map_err(|e| format!("{base} step failed: {e}"))?
        .json()
        .await
        .map_err(|e| format!("{base} returned non-JSON on step: {e}"))?;
        steps_run += 1;
        for key in ["output", "additional_output"] {
            if let Some(s) = step.get(key).and_then(|v| v.as_str()) {
                transcript.push_str(s);
                transcript.push('\n');
            }
        }
        let last = step.get("is_last").and_then(|v| v.as_bool()).unwrap_or(false);
        let done = step.get("status").and_then(|v| v.as_str()) == Some("completed");
        if last || (done && !transcript.trim().is_empty()) {
            break;
        }
    }

    let (code, hint) = extract_code(&transcript);
    if code.trim().is_empty() {
        return Err(format!("{base} finished {steps_run} step(s) without returning code"));
    }
    Ok(Play {
        language: pick_language(&hint, task),
        code,
        meta: json!({ "driver": "ap", "base": base, "ap_task_id": ap_id, "steps": steps_run }),
    })
}
