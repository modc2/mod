//! git — bring a game or an agent from a repository instead of writing one.
//!
//! One door: name a git URL (or a GitHub `owner/name`, or a path on this
//! box) and the server clones it shallowly under its own state directory —
//! `~/.mod/arena/clones/<slug>` — then reads the files exactly the way an
//! upload is read and registers the first class that answers as the role
//! asked for. Registration IS `put_class` / `put_module`: nothing cloned
//! gets a private door into the registry, and the clone is only a working
//! copy — the module the arena keeps is the blob under the hash of its
//! bytes, same as everything else.

use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::time::Duration;

use crate::{arena, blobs};

/// The most source files one clone will read before giving up on finding
/// the class — a monorepo is better imported with `path=`.
const MAX_READS: usize = 600;
/// A class bigger than this is not a class the sandbox will enjoy.
const MAX_CLASS: u64 = 256 * 1024;
/// A wasm module bigger than this is not a game.
const MAX_WASM: u64 = 16 * 1024 * 1024;

fn s(args: &Value, k: &str) -> String {
    args.get(k).and_then(|v| v.as_str()).unwrap_or("").trim().to_string()
}

fn clones_dir() -> PathBuf {
    blobs::state_dir().join("clones")
}

/// `owner/name` → the GitHub URL; a URL stays itself; a directory on this
/// box is taken in place, uncloned — the registry copies the bytes anyway.
fn normalize(raw: &str) -> Result<(String, Option<PathBuf>), String> {
    let text = raw.trim().trim_end_matches('/');
    if text.is_empty() {
        return Err("clone_repo needs `url` — a git URL, a GitHub owner/name, or a path on this box".into());
    }
    let p = Path::new(text);
    if p.is_dir() {
        return Ok((text.to_string(), Some(p.to_path_buf())));
    }
    let url = if text.contains("://") || text.starts_with("git@") {
        text.to_string()
    } else if text.matches('/').count() == 1 && !text.contains(char::is_whitespace) && !text.starts_with('/') {
        format!("https://github.com/{text}")
    } else {
        return Err(format!("`{text}` is not a git URL, an owner/name, or a directory on this box"));
    };
    if !(url.starts_with("http://") || url.starts_with("https://") || url.starts_with("git://")
        || url.starts_with("ssh://") || url.starts_with("git@") || url.starts_with("file://"))
    {
        return Err(format!("`{url}` — a clone takes http(s), git, ssh, file, or git@ URLs"));
    }
    Ok((url, None))
}

fn slug_of(url: &str) -> String {
    let last = url.trim_end_matches('/').rsplit('/').next().unwrap_or("repo");
    let last = last.trim_end_matches(".git");
    let slug: String = last
        .to_lowercase()
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() || c == '-' || c == '_' || c == '.' { c } else { '-' })
        .collect();
    let slug = slug.trim_matches('-').to_string();
    if slug.is_empty() { "repo".into() } else { slug }
}

async fn git(dir: Option<&Path>, args: &[&str]) -> Result<String, String> {
    let budget = Duration::from_millis(
        std::env::var("ARENA_CLONE_TIMEOUT_MS").ok().and_then(|v| v.parse().ok()).unwrap_or(180_000u64).clamp(5_000, 1_800_000),
    );
    let mut cmd = tokio::process::Command::new("git");
    if let Some(d) = dir {
        cmd.arg("-C").arg(d);
    }
    cmd.args(args).env("GIT_TERMINAL_PROMPT", "0");
    let done = tokio::time::timeout(budget, cmd.output())
        .await
        .map_err(|_| format!("git {} ran past {budget:?}", args.first().unwrap_or(&"")))?
        .map_err(|e| format!("could not start git: {e}"))?;
    if !done.status.success() {
        let err = String::from_utf8_lossy(&done.stderr);
        let tail: Vec<&str> = err.trim().lines().rev().take(4).collect();
        return Err(format!("git {} failed: {}", args.first().unwrap_or(&""), tail.into_iter().rev().collect::<Vec<_>>().join(" · ")));
    }
    Ok(String::from_utf8_lossy(&done.stdout).trim().to_string())
}

/// Clone a URL under the state directory, or refresh a clone already there.
/// A different URL that lands on the same slug gets its own directory —
/// two repos never fight over one working copy.
async fn fetch(url: &str, refresh: bool) -> Result<PathBuf, String> {
    let mut dest = clones_dir().join(slug_of(url));
    if dest.join(".git").exists() {
        let origin = git(Some(&dest), &["remote", "get-url", "origin"]).await.unwrap_or_default();
        if origin != url {
            let salt = &blobs::hash(url.as_bytes())[..6];
            dest = clones_dir().join(format!("{}-{salt}", slug_of(url)));
        }
    }
    if dest.join(".git").exists() {
        if refresh {
            git(Some(&dest), &["fetch", "--depth", "1", "origin"]).await?;
            git(Some(&dest), &["reset", "--hard", "origin/HEAD"]).await.ok();
        }
        return Ok(dest);
    }
    std::fs::create_dir_all(clones_dir()).map_err(|e| format!("could not make {}: {e}", clones_dir().display()))?;
    git(None, &["clone", "--depth", "1", url, &dest.to_string_lossy()]).await?;
    Ok(dest)
}

struct Found {
    path: PathBuf,
    role: String,
    score: i64,
}

/// Walk the working copy and read every file the registry could take,
/// through the same readers an upload goes through. What comes back is
/// every game and player in the repo, best candidate first.
fn survey(root: &Path, want: &str) -> (Vec<Found>, usize) {
    let skip = ["node_modules", "__pycache__", "venv", "target", "dist", "vendor", "fixtures"];
    let mut found: Vec<Found> = vec![];
    let mut reads = 0usize;
    let mut stack: Vec<(PathBuf, i64)> = vec![(root.to_path_buf(), 0)];
    while let Some((dir, depth)) = stack.pop() {
        let Ok(entries) = std::fs::read_dir(&dir) else { continue };
        let mut names: Vec<PathBuf> = entries.filter_map(|e| e.ok()).map(|e| e.path()).collect();
        names.sort();
        for path in names {
            let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("").to_string();
            if path.is_dir() {
                if depth < 4 && !name.starts_with('.') && !skip.contains(&name.as_str()) && name != "tests" && name != "test" {
                    stack.push((path, depth + 1));
                }
                continue;
            }
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !matches!(ext, "py" | "rs" | "wasm") {
                continue;
            }
            let size = path.metadata().map(|m| m.len()).unwrap_or(0);
            if size == 0 || size > if ext == "wasm" { MAX_WASM } else { MAX_CLASS } {
                continue;
            }
            if reads >= MAX_READS {
                return (ranked(found), reads);
            }
            reads += 1;
            let Ok(raw) = std::fs::read(&path) else { continue };
            let Ok(read) = arena::describe(&raw) else { continue };
            let role = read.get("role").and_then(|v| v.as_str()).unwrap_or("").to_string();
            if role != "game" && role != "player" {
                continue;
            }
            let stem = path.file_stem().and_then(|x| x.to_str()).unwrap_or("").to_lowercase();
            let mut score = -(depth * 10);
            if stem == role || (role == "player" && stem == "agent") {
                score += 100;
            } else if matches!(stem.as_str(), "arena" | "main" | "mod" | "game" | "player" | "agent") {
                score += 40;
            }
            if role == want {
                score += 1000;
            }
            found.push(Found { path, role, score });
        }
    }
    (ranked(found), reads)
}

fn ranked(mut found: Vec<Found>) -> Vec<Found> {
    found.sort_by(|a, b| b.score.cmp(&a.score).then(a.path.cmp(&b.path)));
    found
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// The whole pipeline: clone (or refresh), find the class, register it.
/// `role` narrows what is being brought — `game` or `player` ("agent" is
/// the same word) — and without it the best readable module wins.
pub async fn clone_repo(args: &Value) -> Result<Value, String> {
    let raw = [s(args, "url"), s(args, "repo"), s(args, "git")].into_iter().find(|v| !v.is_empty()).unwrap_or_default();
    let want = match s(args, "role").to_lowercase().as_str() {
        "" | "any" | "either" => String::new(),
        "agent" | "player" => "player".into(),
        "game" => "game".into(),
        other => return Err(format!("role `{other}` — a clone registers a game or a player (an agent)")),
    };
    let refresh = args.get("refresh").and_then(|v| v.as_bool()).unwrap_or(false);
    let (url, local) = normalize(&raw)?;
    let root = match &local {
        Some(dir) => dir.clone(),
        None => fetch(&url, refresh).await?,
    };
    let commit = git(Some(&root), &["rev-parse", "HEAD"]).await.unwrap_or_default();

    // The file: named outright, or found by reading.
    let picked: PathBuf = match s(args, "path").as_str() {
        "" => {
            let root2 = root.clone();
            let want2 = want.clone();
            let (found, reads) = tokio::task::spawn_blocking(move || survey(&root2, &want2)).await.map_err(|e| e.to_string())?;
            let usable: Vec<&Found> = found.iter().filter(|f| want.is_empty() || f.role == want).collect();
            match usable.first() {
                Some(f) => f.path.clone(),
                None => {
                    let other = found
                        .iter()
                        .map(|f| format!("{} `{}`", f.role, f.path.strip_prefix(&root).unwrap_or(&f.path).display()))
                        .take(6)
                        .collect::<Vec<_>>()
                        .join(", ");
                    return Err(if other.is_empty() {
                        format!(
                            "read {reads} files in {url} and none defines a {} — a game defines view/step/done/result, \
                             a player defines play; name the file with `path=` if it is somewhere unusual",
                            if want.is_empty() { "game or a player".to_string() } else { want }
                        )
                    } else {
                        format!("{url} holds no {want} — it does hold {other}")
                    });
                }
            }
        }
        rel => {
            let p = root.join(rel.trim_start_matches('/'));
            let ok = p.canonicalize().map_err(|e| format!("no `{rel}` in the clone: {e}"))?;
            let base = root.canonicalize().map_err(|e| e.to_string())?;
            if !ok.starts_with(&base) {
                return Err(format!("`{rel}` points outside the clone"));
            }
            ok
        }
    };

    let bytes = std::fs::read(&picked).map_err(|e| format!("could not read {}: {e}", picked.display()))?;
    let read = arena::describe(&bytes)?;
    let lang = read.get("lang").and_then(|v| v.as_str()).unwrap_or("wasm").to_string();
    if !want.is_empty() && read.get("role").and_then(|v| v.as_str()) != Some(want.as_str()) {
        return Err(format!(
            "{} reads as a {}, not a {want}",
            picked.strip_prefix(&root).unwrap_or(&picked).display(),
            read.get("role").and_then(|v| v.as_str()).unwrap_or("?")
        ));
    }

    let name = match s(args, "name").as_str() {
        "" => slug_of(&url),
        n => n.to_string(),
    };
    let mut body = json!({ "name": name, "tags": ["git"], "origin": "git" });
    if let Some(d) = args.get("description").and_then(|v| v.as_str()).filter(|d| !d.trim().is_empty()) {
        body["description"] = json!(d);
    }
    let mut m = if lang == "wasm" {
        body["bytes"] = json!(hex(&bytes));
        arena::put_module(&body)?
    } else {
        body["source"] = json!(String::from_utf8(bytes).map_err(|_| "the file is not UTF-8")?);
        body["lang"] = json!(lang);
        arena::put_class(&body)?
    };
    // A Rust class is compiled on the way in, the same as a stored vibe —
    // better to hear it does not build now than when somebody seats it.
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
            "kind": if m.get("lang").and_then(|v| v.as_str()) == Some("wasm") { "wasm" } else { "class" },
            "config": { "module": m.get("id") },
        }));
        m["entered"] = match entered {
            Ok(p) => json!(p),
            Err(e) => json!({ "error": e }),
        };
    }
    m["git"] = json!({
        "url": url,
        "commit": commit,
        "dir": root.to_string_lossy(),
        "file": picked.strip_prefix(&root).unwrap_or(&picked).to_string_lossy(),
    });
    Ok(m)
}
