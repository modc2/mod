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

/// The last fenced block in a reply — the one an agent landed on after
/// narrating its way there.
fn fenced(text: &str) -> Option<(String, String)> {
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

    best.map(|(code, lang)| (code.trim_matches('\n').to_string(), lang))
}

/// Does this read as a program on its own, rather than prose about one?
fn looks_like_code(text: &str) -> bool {
    let t = text.trim();
    !t.is_empty()
        && ["def ", "import ", "function ", "const ", "let ", "print(", "console.log", "#!/", "class "]
            .iter()
            .any(|m| t.contains(m))
}

/// Pull the program out of a single reply: the fenced block if there is one,
/// otherwise the whole thing when it is plainly code.
pub fn extract_code(text: &str) -> (String, String) {
    match fenced(text) {
        Some(hit) => hit,
        None if looks_like_code(text) => (text.trim().to_string(), String::new()),
        None => (String::new(), String::new()),
    }
}

/// Pull the program out of a run that produced many strings. Newest first, and
/// a fence anywhere beats a bare blob — so an agent that both wrote a file and
/// showed its work is taken at its word, and one that only wrote the file is
/// still read. Never concatenates: splicing prose into a submission would fail
/// it at the judge for a reason the entrant never caused.
fn pick_code(parts: &[String]) -> (String, String) {
    parts
        .iter()
        .rev()
        .find_map(|p| fenced(p))
        .or_else(|| {
            parts
                .iter()
                .rev()
                .find(|p| looks_like_code(p))
                .map(|p| (p.trim().to_string(), String::new()))
        })
        .unwrap_or_default()
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

/// Every string a run produced, in order — summaries, responses, and the code
/// an agent put into a file instead of saying out loud.
///
/// A tool-using agent often answers a coding brief by writing or editing a
/// file rather than replying with a block, so `content` (write) and
/// `new_string` (edit) are mined alongside the prose. Missing those scores a
/// working program zero for the wrong reason.
fn steps_text(resp: &Value) -> Vec<String> {
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
            for key in ["content", "new_string", "code", "text", "message", "summary"] {
                take(p.and_then(|p| p.get(key)));
            }
            take(step.get("result"));
        }
    }
    take(resp.get("summary"));
    parts
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

    let parts = steps_text(&out);
    let (code, hint) = pick_code(&parts);
    if code.trim().is_empty() {
        // Name the steps it did take. "went off and ran bash" and "we could not
        // read your reply" are different failures and the owner of the entrant
        // is the only one who can fix either.
        let trail = out
            .get("result")
            .and_then(|v| v.as_array())
            .map(|steps| {
                steps
                    .iter()
                    .filter_map(|s| s.get("tool").and_then(|t| t.as_str()))
                    .collect::<Vec<_>>()
                    .join(" → ")
            })
            .unwrap_or_default();
        return Err(format!(
            "the agent returned no program (steps: {})",
            if trail.is_empty() { "none".into() } else { trail }
        ));
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

/// Reading the answer out of a reply is where an arena is unfair without
/// noticing: every miss here scores a working program zero.
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn takes_the_fenced_block_not_the_prose() {
        let (code, lang) = extract_code("Here's my approach.\n\n```python\nprint(1)\n```\n\nThat's it.");
        assert_eq!(code, "print(1)");
        assert_eq!(lang, "python");
    }

    #[test]
    fn takes_the_last_block_an_agent_landed_on() {
        let (code, _) = extract_code("first try\n```\nprint(1)\n```\nwait, better:\n```\nprint(2)\n```");
        assert_eq!(code, "print(2)");
    }

    #[test]
    fn reads_a_bare_program_with_no_fence() {
        let (code, _) = extract_code("import sys\nprint(sys.stdin.read())");
        assert!(code.starts_with("import sys"));
    }

    #[test]
    fn refuses_prose_that_holds_no_program() {
        let (code, _) = extract_code("I'm not sure how to solve this one, sorry.");
        assert!(code.is_empty());
    }

    #[test]
    fn survives_an_unterminated_fence() {
        let (code, _) = extract_code("here:\n```python\nprint(1)");
        assert_eq!(code, "print(1)");
    }

    #[test]
    fn mines_the_file_an_agent_wrote_instead_of_speaking() {
        // An agent that answers by calling edit/write, never saying the code.
        let parts = vec!["solution.py".to_string(), "def solve():\n    print(1)".to_string()];
        let (code, _) = pick_code(&parts);
        assert_eq!(code, "def solve():\n    print(1)");
    }

    #[test]
    fn never_splices_prose_into_a_submission() {
        let parts = vec![
            "I'll write the file now.".to_string(),
            "print(1)\nimport sys".to_string(),
        ];
        let (code, _) = pick_code(&parts);
        assert!(!code.contains("I'll write"), "prose leaked into the program: {code:?}");
    }

    #[test]
    fn a_fence_anywhere_beats_a_bare_blob() {
        let parts = vec![
            "import sys\nprint('draft')".to_string(),
            "final answer:\n```python\nprint('final')\n```".to_string(),
        ];
        let (code, lang) = pick_code(&parts);
        assert_eq!(code, "print('final')");
        assert_eq!(lang, "python");
    }

    #[test]
    fn a_language_hint_is_only_taken_when_the_judge_can_run_it() {
        let task = Task {
            id: "t1".into(), slug: "s".into(), title: "T".into(), kind: "coding".into(),
            statement: String::new(), language: "any".into(), starter: String::new(),
            mode: "io".into(), tests: vec![], timeout_ms: 1000, author: String::new(),
            tags: vec![], created: 0,
        };
        assert_eq!(pick_language("python", &task), "python");
        assert_eq!(pick_language("rust", &task), "python", "unrunnable hint falls back");
        assert_eq!(pick_language("", &task), "python");
    }
}
