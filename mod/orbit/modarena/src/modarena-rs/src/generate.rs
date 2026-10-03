//! Writing a mod with an agent, and not believing a word of it.
//!
//! `generate` asks the Claude agent on this box for a mod folder — a game or a
//! player, in Python or Rust — and then does the only thing that makes that
//! safe to offer: it runs the folder through the same verifier every upload
//! goes through, and if it fails, it hands the agent the failed checks and
//! asks again. Nothing is stored until a folder passes, and what passes is
//! stored by exactly the same call a human upload makes.
//!
//! The agent is fenced by its tool list rather than by its prompt. It runs
//! with `--tools ""` and `--strict-mcp-config`: no Bash, no Read, no network,
//! no MCP servers at all. It cannot touch this box, the registry or the
//! filesystem; it can only answer. Everything it needs to write a correct
//! folder — the template, the ABI, the check list — is in the prompt, so the
//! fence costs it nothing.
//!
//! The loop is the point. A model writing a game is a suggestion; a model
//! writing a game that parses as a game, declares itself a game, and is read
//! by the registry as a game before anyone plays it is a mod. The transcript
//! of attempts comes back with the result, so a folder that took three tries
//! says so.

use crate::arena;
use crate::folder::{self, Folder};
use serde_json::{json, Value};
use std::process::Stdio;
use tokio::io::AsyncWriteExt;

/// How many times the agent may be handed its own failed checks. Two repairs
/// after the first attempt: past that the prompt is wrong, not the answer.
const MAX_ATTEMPTS: usize = 3;
const DEFAULT_MODEL: &str = "sonnet";
const TIMEOUT_S: u64 = 300;

pub fn claude_binary() -> String {
    std::env::var("MODARENA_CLAUDE").unwrap_or_else(|_| "claude".into())
}

/// Is there an agent on this box at all? Asked before anything is promised, so
/// a deployment without the CLI says so instead of hanging.
pub async fn agent_status() -> Value {
    let bin = claude_binary();
    let out = tokio::process::Command::new(&bin)
        .arg("--version")
        .stdin(Stdio::null())
        .output()
        .await;
    match out {
        Ok(o) if o.status.success() => json!({
            "available": true,
            "binary": bin,
            "version": String::from_utf8_lossy(&o.stdout).trim(),
        }),
        Ok(o) => json!({
            "available": false, "binary": bin,
            "error": String::from_utf8_lossy(&o.stderr).trim(),
        }),
        Err(e) => json!({
            "available": false, "binary": bin,
            "error": format!("{e} — generate needs the claude CLI on PATH (MODARENA_CLAUDE to point at it)"),
        }),
    }
}

/// The brief. Everything about the contract is here rather than in a tool the
/// agent has to think to call: the folder shape, the template it is starting
/// from, and the exact checks its answer will be put through.
fn brief(kind: &str, lang: &str, template: &Folder, prompt: &str, name: &str) -> String {
    let files: Vec<String> = template
        .files
        .iter()
        .map(|(path, bytes)| {
            format!("--- {path} ---\n{}", String::from_utf8_lossy(bytes))
        })
        .collect();
    let checks = "\
  files                every path relative, only .py .rs .wasm .json .md .txt
  config               config.json is there and parses
  protocol             config.json says \"protocol\": \"modarena/1.0\"
  name                 a slug: lowercase letters, digits, - and _
  kind                 game or player
  anchor               lang and anchor agree: python→mod.py, rust→mod.rs
  readable             the anchor reads as the language it claims
  kind_matches_anchor  what the anchor DEFINES equals what config.json SAYS
  abi                  a game defines view, step, done, result; a player defines play
  imports (python)     the sandbox allows the pure stdlib and this folder's own files
  description          config.json describes it in a sentence
  players              a game declares its seat count";

    format!(
        "Write one mod folder for the modarena registry.\n\n\
         WHAT TO WRITE\n{prompt}\n\n\
         It is a **{kind}** written in **{lang}**{named}.\n\n\
         THE SHAPE\n\
         A mod is a folder. It holds a config.json saying what it is, and an anchor —\n\
         the one file that runs. Here is the template for exactly this kind and\n\
         language; keep its shape and replace its content.\n\n{template_text}\n\n\
         WHAT IT IS CHECKED AGAINST\n\
         Your answer is put through this verifier before it is stored. Every line is\n\
         mechanical, and the one that catches people is `kind_matches_anchor`: the\n\
         registry reads your anchor itself and compares what it finds to what your\n\
         config.json claims.\n\n{checks}\n\n\
         RULES THAT COME FROM THE SANDBOX, NOT FROM TASTE\n\
         - Python runs in a locked interpreter: no open(), no sockets, no time, no\n\
           subprocess. The importable stdlib is: {allowed}. Seeded `random` is fine.\n\
         - Rust is compiled with rustc alone, for wasm32-unknown-unknown. One file, no\n\
           Cargo, no crates — only std, core and alloc, and nothing behind std::fs,\n\
           std::net, std::time or std::thread exists at run time.\n\
         - A game's `step` is handed a dict of seat → move text and returns which\n\
           moves were legal. It must reject a move it does not understand rather than\n\
           raise, and it must terminate: `done` has to become true.\n\
         - A player's `play(view, seat)` returns move text and nothing else.\n\n\
         ANSWER WITH\n\
         One JSON object and no prose around it, no markdown fence:\n\n\
         {{\"files\": {{\"config.json\": \"…the file, as a string…\", \"{anchor}\": \"…the file…\"}}}}\n\n\
         Every value is the complete text of that file. Include config.json and the\n\
         anchor; a README.md is welcome. Leave `author` empty — it is the uploader's\n\
         field, not yours, and it is overwritten anyway. Make the game or the agent actually good —\n\
         something worth playing — not the template with the names changed.",
        prompt = prompt.trim(),
        named = if name.is_empty() { String::new() } else { format!(", named `{name}`") },
        template_text = files.join("\n"),
        checks = checks,
        allowed = crate::klass::ALLOWED_IMPORTS.join(" "),
        anchor = folder::anchor_for(lang).unwrap_or("mod.py"),
    )
}

/// What the agent is told after a failed attempt: the checks it failed, in the
/// verifier's own words, and nothing else. The report is written to be read,
/// which is what makes it usable as a repair prompt.
fn repair(report: &Value) -> String {
    let mut out = String::from(
        "That folder was refused. These checks failed — fix exactly these and answer with the \
         whole folder again, as one JSON object:\n\n",
    );
    for c in report["checks"].as_array().into_iter().flatten() {
        if c["ok"] == json!(false) && c["level"] == json!("error") {
            out.push_str(&format!(
                "  {} — {}\n",
                c["check"].as_str().unwrap_or(""),
                c["detail"].as_str().unwrap_or("")
            ));
        }
    }
    out
}

/// What the agent is told when the folder is a mod on paper and not in the
/// sandbox: which call failed, and what came back out of it.
fn smoke_repair(smoke: &Value) -> String {
    let mut out = String::from(
        "That folder is shaped right, but it does not run. It was loaded in the sandbox and \
         asked to play, and this is what happened:\n\n",
    );
    for step in smoke["steps"].as_array().into_iter().flatten() {
        out.push_str(&format!(
            "  {} {}{}\n",
            if step["ok"] == json!(true) { "ok  " } else { "FAIL" },
            step["step"].as_str().unwrap_or(""),
            match (step["error"].as_str(), step["value"].as_str()) {
                (Some(e), _) => format!(" — {e}"),
                (None, Some(v)) => format!(" — {v}"),
                _ => String::new(),
            }
        ));
    }
    if let Some(e) = smoke["error"].as_str() {
        out.push_str(&format!("\n{e}\n"));
    }
    out.push_str(
        "\nA game must answer view(seat) with text, must return which moves were legal \
         rather than raising on a move it does not understand, and must reach done(). \
         A player must answer play(view, seat) with move text.\n\n\
         Fix it and answer with the whole folder again, as one JSON object.",
    );
    out
}

/// Rewrite `author` in a generated folder's config.json. Done before the
/// folder is hashed, so what is stored and what was verified are the same
/// bytes.
fn with_author(mut f: Folder, author: &str) -> Folder {
    let Some(mut config) = f.config() else { return f };
    if config.get("author").and_then(|a| a.as_str()).unwrap_or("") == author {
        return f;
    }
    config["author"] = json!(author);
    if let Ok(text) = serde_json::to_string_pretty(&config) {
        f.add(folder::CONFIG, format!("{text}\n"));
    }
    f
}

/// Pull the folder out of whatever the agent said. A model answers with JSON,
/// or with JSON inside a fence, or with a sentence and then JSON — all three
/// are the same answer and refusing two of them is pedantry, not rigour.
fn extract(text: &str) -> Result<Folder, String> {
    let trimmed = text.trim();
    let body = match trimmed.find("```") {
        Some(start) => {
            let after = &trimmed[start + 3..];
            let after = after.strip_prefix("json").unwrap_or(after);
            after.split("```").next().unwrap_or(after).trim()
        }
        None => trimmed,
    };
    let start = body.find('{').ok_or("the agent answered without any JSON in it")?;
    let end = body.rfind('}').ok_or("the agent's JSON is unterminated")?;
    let value: Value = serde_json::from_str(&body[start..=end])
        .map_err(|e| format!("the agent's answer is not valid JSON: {e}"))?;
    Folder::from_value(&value)
}

/// One call to the agent. Fenced: no tools, no MCP, `/tmp` for a working
/// directory it never uses.
async fn ask(prompt: &str, model: &str, session: Option<&str>) -> Result<(String, String), String> {
    let bin = claude_binary();
    let mut cmd = tokio::process::Command::new(&bin);
    cmd.arg("--print")
        .args(["--model", model])
        .args(["--output-format", "json"])
        .args(["--tools", ""])
        .arg("--strict-mcp-config")
        .args(["--mcp-config", "{\"mcpServers\":{}}"])
        .current_dir("/tmp")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if let Some(id) = session {
        cmd.args(["--resume", id]);
    }
    // The prompt goes in on stdin rather than in argv: a template plus a brief
    // is comfortably past what an argument list wants to carry.
    let mut child = cmd
        .spawn()
        .map_err(|e| format!("could not start `{bin}`: {e} — generate needs the claude CLI on PATH"))?;
    if let Some(mut stdin) = child.stdin.take() {
        stdin
            .write_all(prompt.as_bytes())
            .await
            .map_err(|e| format!("writing the prompt: {e}"))?;
        drop(stdin);
    }
    let out = tokio::time::timeout(std::time::Duration::from_secs(TIMEOUT_S), child.wait_with_output())
        .await
        .map_err(|_| format!("the agent did not answer within {TIMEOUT_S}s"))?
        .map_err(|e| format!("the agent failed: {e}"))?;
    let stdout = String::from_utf8_lossy(&out.stdout).to_string();
    if !out.status.success() && stdout.trim().is_empty() {
        return Err(format!(
            "the agent exited {}: {}",
            out.status,
            String::from_utf8_lossy(&out.stderr).trim()
        ));
    }
    // `--output-format json` wraps the answer; a plain answer is the answer.
    let parsed: Value = serde_json::from_str(stdout.trim()).unwrap_or(Value::Null);
    let session_id = parsed
        .get("session_id")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let answer = parsed
        .get("result")
        .and_then(|v| v.as_str())
        .map(String::from)
        .unwrap_or(stdout);
    if answer.trim().is_empty() {
        return Err("the agent answered with nothing".into());
    }
    Ok((answer, session_id))
}

/// Generate a mod folder, verify it, repair it, and store it if it passes.
///
/// Arguments: `prompt` (what to build), `kind` (game | player), `lang`
/// (python | rust), optional `name`, `model`, `attempts`, and `store` — which
/// defaults to true and is the only argument that changes the registry.
pub async fn generate(args: &Value) -> Result<Value, String> {
    let prompt = args
        .get("prompt")
        .or_else(|| args.get("description"))
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|p| !p.is_empty())
        .ok_or("generate needs `prompt` — what the game or the agent should be")?;
    let kind = match args.get("kind").or_else(|| args.get("role")).and_then(|v| v.as_str()).unwrap_or("game") {
        "player" | "agent" | "bot" => "player",
        "game" | "" => "game",
        other => return Err(format!("generate makes a `game` or a `player`, not a `{other}`")),
    };
    let lang = match args.get("lang").and_then(|v| v.as_str()).unwrap_or("python") {
        "rust" | "rs" => "rust",
        "python" | "py" | "class" | "" => "python",
        other => return Err(format!("generate writes python or rust, not `{other}` — a wasm mod is compiled, not written")),
    };
    let name = args.get("name").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
    let model = args.get("model").and_then(|v| v.as_str()).unwrap_or(DEFAULT_MODEL).to_string();
    let author = args.get("author").and_then(|v| v.as_str()).unwrap_or("generated").to_string();
    let store_it = args.get("store").and_then(|v| v.as_bool()).unwrap_or(true);
    // The smoke gate: on by default, because a mod that has never been asked
    // to play is a mod nobody has checked.
    let run_it = args.get("run").and_then(|v| v.as_bool()).unwrap_or(true);
    let max = args
        .get("attempts")
        .and_then(|v| v.as_u64())
        .unwrap_or(MAX_ATTEMPTS as u64)
        .clamp(1, 5) as usize;

    let template = folder::template(kind, lang)?;
    let mut message = brief(kind, lang, &template, prompt, &name);
    let mut session: Option<String> = None;
    let mut attempts: Vec<Value> = Vec::new();
    let started = std::time::Instant::now();

    for attempt in 1..=max {
        let (answer, session_id) = match ask(&message, &model, session.as_deref()).await {
            Ok(v) => v,
            Err(e) => {
                attempts.push(json!({ "attempt": attempt, "ok": false, "error": e }));
                return Ok(json!({
                    "ok": false, "stored": false, "attempts": attempts,
                    "error": attempts.last().and_then(|a| a["error"].as_str()).unwrap_or(""),
                    "ms": started.elapsed().as_millis() as u64,
                }));
            }
        };
        if !session_id.is_empty() {
            session = Some(session_id);
        }

        let folder = match extract(&answer) {
            Ok(f) => f,
            Err(e) => {
                attempts.push(json!({ "attempt": attempt, "ok": false, "error": e }));
                message = format!(
                    "{e}\n\nAnswer again with one JSON object and nothing else: \
                     {{\"files\": {{\"config.json\": \"…\", \"{}\": \"…\"}}}}",
                    folder::anchor_for(lang).unwrap_or("mod.py")
                );
                continue;
            }
        };

        // Whose mod this is, is not the agent's to decide: the CLI runs as
        // whoever owns this box, and a model asked to fill in an author field
        // will helpfully write down the account it is logged in as. The
        // uploader's answer wins, and the default says how it was made.
        let folder = with_author(folder, &author);

        let report = folder.verify();
        attempts.push(json!({
            "attempt": attempt,
            "ok": report["ok"],
            "id": report["id"],
            "name": report["name"],
            "files": report["files"],
            "summary": report["summary"],
            "failed": report["checks"].as_array().map(|cs| cs.iter()
                .filter(|c| c["ok"] == json!(false))
                .map(|c| json!({ "check": c["check"], "level": c["level"], "detail": c["detail"] }))
                .collect::<Vec<_>>()).unwrap_or_default(),
        }));

        if report["ok"] == json!(true) {
            let mut out = json!({
                "ok": true,
                "attempts": attempts,
                "tries": attempt,
                "report": report,
                "files": folder.to_value(),
                "ms": started.elapsed().as_millis() as u64,
                "model": model,
            });
            if !store_it {
                out["stored"] = json!(false);
                out["note"] = json!("verified but not stored — call again with store=true, or upload the folder yourself");
                return Ok(out);
            }

            // Structurally it is a mod. Now make it answer: stored, opened in
            // the sandbox and asked to play, because a game that parses and
            // then raises on the first move is not a game. A failure here is
            // handed back like any other failed check, and the mod comes back
            // out of the registry — nothing half-verified stays in it.
            let stored = arena::store_folder(&folder, &json!({ "source": "generated" }))?;
            let id = stored["id"].as_str().unwrap_or_default().to_string();
            let smoke = arena::smoke(&id).await.unwrap_or_else(|e| json!({ "ok": false, "error": e }));
            if smoke["ok"] == json!(true) || !run_it {
                out["stored"] = json!(true);
                out["mod"] = stored;
                out["smoke"] = smoke;
                return Ok(out);
            }
            let _ = arena::delete_module(&id);
            if let Some(last) = attempts.last_mut() {
                last["ok"] = json!(false);
                last["smoke"] = smoke.clone();
            }
            message = smoke_repair(&smoke);
            continue;
        }
        message = repair(&report);
    }

    Ok(json!({
        "ok": false,
        "stored": false,
        "tries": max,
        "attempts": attempts,
        "files": Value::Null,
        "error": format!("{max} attempts, and the folder still does not match the template"),
        "ms": started.elapsed().as_millis() as u64,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn json_is_found_however_it_was_wrapped() {
        let body = r#"{"files": {"config.json": "{}", "mod.py": "class A:\n    pass\n"}}"#;
        for text in [
            body.to_string(),
            format!("```json\n{body}\n```"),
            format!("Here is the folder:\n\n{body}\n"),
        ] {
            let f = extract(&text).expect("a folder");
            assert!(f.files.contains_key("mod.py"), "{text}");
        }
    }

    #[test]
    fn prose_with_no_json_is_an_error_not_an_empty_folder() {
        assert!(extract("I would rather not.").is_err());
    }

    #[test]
    fn the_brief_carries_the_template_and_the_checks() {
        let t = folder::template("game", "python").unwrap();
        let b = brief("game", "python", &t, "a dice game", "dicey");
        assert!(b.contains("kind_matches_anchor"));
        assert!(b.contains("config.json"));
        assert!(b.contains("mod.py"));
        assert!(b.contains("a dice game"));
        assert!(b.contains("dicey"));
    }

    #[test]
    fn a_repair_prompt_names_only_the_failures() {
        let mut f = folder::template("game", "python").unwrap();
        f.add("config.json", r#"{"name":"x","kind":"player","lang":"python","anchor":"mod.py","protocol":"modarena/1.0","description":"a game that says player"}"#);
        let text = repair(&f.verify());
        assert!(text.contains("kind_matches_anchor"), "{text}");
        assert!(!text.contains("readable —"), "passing checks stay out of it: {text}");
    }
}
