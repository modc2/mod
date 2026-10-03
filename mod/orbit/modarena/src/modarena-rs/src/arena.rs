//! The capability layer — everything the arena can do, once.
//!
//! `mcp.rs` exposes these as MCP tools, `http.rs` exposes the same ones as
//! REST, and `mod.py` calls them over that. Nothing is implemented twice, so
//! an agent and a browser can never drift apart on what the arena does.

use crate::blobs;
use crate::klass;
use crate::players;
use crate::rating;
use crate::rsklass;
use crate::rustc;
use crate::folder::{self, Folder};
use crate::store::{self, Match, Player, Rating, Seat, Turn};
use crate::wasm;
use serde_json::{json, Value};

/// Where the example pack lives: one directory of mod folders. Baked as a
/// path, not as bytes, so the pack can be rebuilt or added to without
/// rebuilding the server.
fn example_dirs() -> Vec<std::path::PathBuf> {
    if let Ok(d) = std::env::var("MODARENA_EXAMPLES") {
        return d.split(':').map(std::path::PathBuf::from).collect();
    }
    vec![std::path::PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/../examples/mods"))]
}

pub fn info() -> Value {
    let (modules, games, python, rust, players_n, matches) = store::read(|s| {
        (
            s.modules.len(),
            s.modules.values().filter(|m| m.role == "game").count(),
            s.modules.values().filter(|m| m.lang() == "python").count(),
            s.modules.values().filter(|m| m.lang() == "rust").count(),
            s.players.len(),
            s.matches.len(),
        )
    });
    json!({
        "name": "modarena",
        "what": "A storage and execution layer for uploaded code, and an arena built on \
                 it: upload a wasm module, a Python class or a Rust class, and it is \
                 stored by the hash of its bytes, executed away from the server, and — if \
                 what it defines matches the game ABI — playable. Agents, models and bots \
                 are assessed by sitting them at it. Every module stored here is also a \
                 mod of its own and an MCP server of its own.",
        "modules": modules,
        "games": games,
        "classes": python + rust,
        "python": python,
        "rust": rust,
        "players": players_n,
        "matches": matches,
        "player_kinds": players::KINDS,
        "executes_in": ["browser", "node"],
        "state": blobs::state_dir().to_string_lossy(),
        "upload": ["a .wasm module", "a .py file holding a class", "a .rs file holding a struct"],
        "mcp": {
            "modarena": "/mcp — the whole arena, as one server",
            "per_module": "/m/<name>/mcp — one server per game and per agent, tools scoped \
                           to that module alone",
            "outward": "a class calls out through arena::mcp (Rust) or self.mcp (Python); \
                        the sandbox never opens a socket, the host makes the call",
        },
        "abi": {
            "wasm": {
                "strings": "the module exports alloc(i32)->i32; anything it returns is one i64 packed as (ptr << 32) | len",
                "game": wasm::GAME_EXPORTS,
                "game_optional": ["game_info", "game_turn", "alloc"],
                "player": wasm::PLAYER_EXPORTS,
                "runs_in": ["browser", "node"],
            },
            "class": {
                "strings": "plain Python — the methods take and return str, dict and list, and the state is self",
                "game": klass::GAME_METHODS,
                "game_optional": ["__init__(self, seed)", "turn", "info", "name", "players", "max_turns"],
                "player": klass::PLAYER_METHODS,
                "runs_in": ["node"],
                "sandbox": "a python subprocess: no filesystem, no network, seeded random, capped memory and CPU",
            },
            "rust": {
                "strings": "plain Rust — a struct, and an impl block whose methods take &str and return String",
                "game": rsklass::GAME_METHODS,
                "game_optional": ["new(seed)", "turn", "NAME", "PLAYERS", "MAX_TURNS"],
                "player": rsklass::PLAYER_METHODS,
                "runs_in": ["browser", "node"],
                "sandbox": "compiled to wasm32-unknown-unknown on upload and run in a wasm \
                            engine — the same sandbox as any other wasm module here",
                "toolchain": rustc::toolchain(),
            },
        },
    })
}

// ── modules ──────────────────────────────────────────────────────────────

/// Which reader describes these bytes. The registry holds three kinds of
/// module and this one function is the whole of how it tells them apart —
/// four magic bytes, or which language the source is written in.
///
/// Rust is asked first because the question is narrower: a file with `impl`,
/// `struct` or `fn` and a brace in it is Rust and is not anything else here.
pub fn describe(raw: &[u8]) -> Result<Value, String> {
    folder::sniff(raw)
}

/// The wasm a mod actually runs as. For a `mod.wasm` anchor that is the anchor
/// itself; for a `mod.rs` it is the compile, cached under the mod's id. A
/// Python anchor has no wasm form and says so — it runs in the interpreter
/// sandbox instead.
///
/// Everything that executes goes through here, which is why a Rust mod plays
/// in a browser tab and a Python one does not.
pub fn compiled(key: &str) -> Result<(String, Vec<u8>), String> {
    let m = store::read(|s| s.module(key).cloned())
        .ok_or_else(|| format!("no mod `{key}`"))?;
    let raw = anchor_bytes(&m)?;
    match m.lang() {
        "wasm" => Ok((m.id, raw)),
        "rust" => {
            let source = String::from_utf8(raw).map_err(|_| "the anchor is not UTF-8".to_string())?;
            let bytes = rustc::compile(&m.id, &source)?;
            Ok((m.id, bytes))
        }
        other => Err(format!(
            "`{}` is anchored on a {other} class — it runs in the interpreter sandbox, not in \
             a wasm engine, so there is no wasm form of it to fetch",
            m.name
        )),
    }
}

fn anchor_bytes(m: &store::ModEntry) -> Result<Vec<u8>, String> {
    let id = m
        .anchor_id()
        .ok_or_else(|| format!("`{}` has no anchor on file", m.name))?;
    blobs::get(id)
}

/// Read a stored mod back out as the folder it is.
pub fn folder_of(m: &store::ModEntry) -> Result<Folder, String> {
    let mut f = Folder::new();
    for file in &m.files {
        f.add(file.path.clone(), blobs::get(&file.id)?);
    }
    Ok(f)
}

// ── storing a mod ────────────────────────────────────────────────────────

/// Store a folder, once it has passed its own verification.
///
/// This is the only way anything enters the registry. The check is not a
/// formality: a folder whose `config.json` says `game` while its anchor
/// defines a player is refused here, with the report, so nothing downstream —
/// the match loop, the leaderboard, a published `orbit/` mod — ever has to
/// wonder whether a card is telling the truth.
pub fn store_folder(f: &Folder, args: &Value) -> Result<Value, String> {
    let report = f.verify();
    if report["ok"] != json!(true) {
        return Err(refusal(&report));
    }
    let id = f.id();
    let config = f.config().unwrap_or_else(|| json!({}));
    let anchor = f.anchor_path().unwrap_or_default();
    let lang = config["lang"].as_str().unwrap_or("wasm").to_string();
    let info = f
        .anchor_bytes()
        .map(|b| folder::read_as(&lang, b))
        .transpose()?
        .unwrap_or_else(|| json!({}));

    // Files first: the index may only ever name blobs that are already on
    // disk, so a crash between the two leaves orphaned bytes and never a
    // registry row pointing at nothing.
    let mut files = Vec::new();
    for (path, bytes) in &f.files {
        let blob = blobs::put(bytes)?;
        files.push(store::FileRef { path: path.clone(), id: blob, size: bytes.len() });
    }

    let entry = store::write(|s| {
        let existing = s.modules.get(&id).cloned();
        let m = store::ModEntry {
            id: id.clone(),
            name: config["name"].as_str().unwrap_or("mod").to_string(),
            role: info["role"].as_str().unwrap_or("wasm").to_string(),
            kind: config["kind"].as_str().unwrap_or("").to_string(),
            lang: lang.clone(),
            anchor: anchor.clone(),
            description: config["description"].as_str().unwrap_or("").to_string(),
            author: config["author"]
                .as_str()
                .map(String::from)
                .or_else(|| args.get("author").and_then(|v| v.as_str()).map(String::from))
                .unwrap_or_default(),
            tags: config["tags"]
                .as_array()
                .map(|a| a.iter().filter_map(|t| t.as_str().map(String::from)).collect())
                .unwrap_or_default(),
            size: f.total_bytes(),
            files,
            config: config.clone(),
            info: info.clone(),
            report: json!({ "ok": true, "warnings": report["warnings"], "checks": report["checks"] }),
            source: args
                .get("source")
                .or_else(|| args.get("origin"))
                .and_then(|v| v.as_str())
                .unwrap_or("upload")
                .into(),
            runs: existing.as_ref().map(|e| e.runs).unwrap_or(0),
            created: existing.as_ref().map(|e| e.created).unwrap_or_else(store::now),
        };
        s.modules.insert(id.clone(), m.clone());
        m
    });

    let mut v = entry.card();
    v["url"] = json!(entry.url());
    v["info"] = entry.info;
    v["config"] = entry.config;
    v["report"] = report;
    v["manifest"] = json!(f.manifest());
    Ok(v)
}

/// The message a refused folder gets: the summary, then every failed check by
/// name, because "it does not match the template" is not something anyone can
/// act on.
fn refusal(report: &Value) -> String {
    let mut out = String::from("this folder does not match the template:\n");
    for c in report["checks"].as_array().into_iter().flatten() {
        if c["ok"] == json!(false) {
            out.push_str(&format!(
                "  {} {} — {}\n",
                if c["level"] == json!("error") { "✗" } else { "!" },
                c["check"].as_str().unwrap_or(""),
                c["detail"].as_str().unwrap_or("")
            ));
        }
    }
    out.push_str("`template` prints a folder that passes; `verify` runs these checks without storing anything.");
    out
}

/// Store a mod. Either a whole folder (`files`) or one file (`bytes` / `text`),
/// which is wrapped into a folder on the way in — a bare class dropped on the
/// console is a complete mod a moment later, with the config.json written from
/// what the reader found in it.
pub fn put_module(args: &Value) -> Result<Value, String> {
    if args.get("files").is_some() {
        let f = Folder::from_value(args)?;
        return store_folder(&f, args);
    }
    let raw = match args.get("text").and_then(|v| v.as_str()) {
        Some(text) => text.as_bytes().to_vec(),
        None => {
            let encoded = args
                .get("bytes")
                .or_else(|| args.get("wasm"))
                .or_else(|| args.get("base64"))
                .and_then(|v| v.as_str())
                .ok_or("put_mod needs `files` — the folder, path → contents — or one file as \
                        `text` (a class) or `bytes` (encoded), which is wrapped into a folder")?;
            blobs::decode(encoded)?
        }
    };
    if raw.is_empty() {
        return Err("put_mod got zero bytes".into());
    }
    let f = Folder::wrap(raw, args)?;
    let mut v = store_folder(&f, args)?;
    v["wrapped"] = json!(format!(
        "one file arrived, so a folder was written around it: {}",
        f.files.keys().cloned().collect::<Vec<_>>().join(", ")
    ));
    Ok(v)
}

/// Check a folder without storing it — the whole point of having a template.
/// Takes a folder (`files`) or the name of one already stored (`mod`), and
/// with `compile: true` also puts a Rust anchor through rustc, which is the
/// only check that costs anything.
pub fn verify_mod(args: &Value) -> Result<Value, String> {
    let (f, stored) = match args.get("files") {
        Some(_) => (Folder::from_value(args)?, None),
        None => {
            let key = args
                .get("mod")
                .or_else(|| args.get("module"))
                .and_then(|v| v.as_str())
                .ok_or("verify needs `files` — the folder — or `mod`, one already stored")?;
            let m = store::read(|s| s.module(key).cloned())
                .ok_or_else(|| format!("no mod `{key}`"))?;
            (folder_of(&m)?, Some(m.id.clone()))
        }
    };
    let mut report = f.verify();
    report["stored"] = json!(stored);

    if args.get("compile").and_then(|v| v.as_bool()).unwrap_or(false)
        && report["lang"] == json!("rust")
    {
        let source = f.anchor_bytes().and_then(|b| String::from_utf8(b.clone()).ok()).unwrap_or_default();
        let (ok, detail) = match rustc::compile(&f.id(), &source) {
            Ok(bytes) => (true, format!("compiles to {} bytes of wasm", bytes.len())),
            Err(e) => (false, e),
        };
        let check = json!({ "check": "compiles", "ok": ok, "level": "error", "detail": detail });
        if let Some(list) = report["checks"].as_array_mut() {
            list.push(check);
        }
        if !ok {
            report["ok"] = json!(false);
            report["errors"] = json!(report["errors"].as_u64().unwrap_or(0) + 1);
            report["summary"] = json!(format!("{} · {}", report["summary"].as_str().unwrap_or(""), "does not compile"));
        }
    }
    Ok(report)
}

/// The folder a new mod starts as — one per kind and language, and the thing
/// `verify` verifies against.
pub fn template(args: &Value) -> Result<Value, String> {
    let kind = args.get("kind").or_else(|| args.get("role")).and_then(|v| v.as_str()).unwrap_or("game");
    let lang = args.get("lang").and_then(|v| v.as_str()).unwrap_or("python");
    let f = folder::template(kind, lang)?;
    Ok(json!({
        "kind": kind,
        "lang": lang,
        "anchor": f.anchor_path(),
        "files": f.to_value(),
        "verify": f.verify(),
        "note": "every file here is the mod. Edit the anchor, keep config.json honest about \
                 what it defines, and `verify` before you upload — the registry runs the same \
                 checks and refuses what does not pass.",
    }))
}

pub fn list_modules(args: &Value) -> Value {
    let role = args.get("role").and_then(|v| v.as_str()).unwrap_or("").to_lowercase();
    let q = args.get("q").and_then(|v| v.as_str()).unwrap_or("").to_lowercase();
    let tag = args.get("tag").and_then(|v| v.as_str()).unwrap_or("").to_lowercase();
    // `lang=python` is how you ask for the classes, `lang=wasm` for the binaries.
    let lang = args.get("lang").and_then(|v| v.as_str()).unwrap_or("").to_lowercase();

    let list = store::read(|s| {
        s.module_list()
            .into_iter()
            .filter(|m| role.is_empty() || m.role == role)
            .filter(|m| lang.is_empty() || m.lang() == lang)
            .filter(|m| tag.is_empty() || m.tags.iter().any(|t| t.to_lowercase() == tag))
            .filter(|m| {
                q.is_empty()
                    || m.name.to_lowercase().contains(&q)
                    || m.description.to_lowercase().contains(&q)
                    || m.id.starts_with(&q)
            })
            .map(|m| m.card())
            .collect::<Vec<_>>()
    });
    json!({ "count": list.len(), "modules": list })
}

/// One mod in full: the card, the config it declared, the reader's word on the
/// anchor, and — because for source the source *is* the description — the
/// text of every file in the folder small enough to send.
pub fn get_module(key: &str, with_source: bool) -> Result<Value, String> {
    let m = store::read(|s| s.module(key).cloned())
        .ok_or_else(|| format!("no mod `{key}` — ids resolve in full, by name, or by an unambiguous prefix of {}+ hex characters", blobs::MIN_PREFIX))?;
    let mut v = m.card();
    v["info"] = m.info.clone();
    v["config"] = m.config.clone();
    v["report"] = m.report.clone();
    v["url"] = json!(m.url());
    v["stored"] = json!(m.files.iter().all(|f| blobs::exists(&f.id)));
    if with_source && m.size <= 256 * 1024 {
        if let Ok(f) = folder_of(&m) {
            v["files_text"] = f.to_value();
            if m.lang() != "wasm" {
                if let Some(t) = f.text(&m.anchor) {
                    v["source"] = json!(t);
                }
            }
        }
    }
    Ok(v)
}

/// The whole folder, as files — what `publish` copies into `orbit/`, what the
/// runtime reads sibling modules from, and what an agent asks for when it
/// wants to fork a mod rather than play it.
pub fn mod_files(key: &str) -> Result<Value, String> {
    let m = store::read(|s| s.module(key).cloned()).ok_or_else(|| format!("no mod `{key}`"))?;
    let f = folder_of(&m)?;
    Ok(json!({
        "id": m.id,
        "name": m.name,
        "anchor": m.anchor,
        "manifest": f.manifest(),
        "files": f.to_value(),
    }))
}

/// Store a class from plain text — `put_mod` for people and agents holding
/// source rather than a folder. The folder is written around it.
///
/// The language is read off the source, the same way the role is. `lang` may
/// be passed to say which was meant, and it is only ever a tie-break: a file
/// that is plainly Rust is Rust however it was labelled.
pub fn put_class(args: &Value) -> Result<Value, String> {
    let source = args
        .get("source")
        .or_else(|| args.get("text"))
        .or_else(|| args.get("class"))
        .and_then(|v| v.as_str())
        .ok_or("put_class needs `source` — the class, as Python or Rust text")?;
    if source.trim().is_empty() {
        return Err("put_class got an empty source".into());
    }
    let asked = args.get("lang").and_then(|v| v.as_str()).unwrap_or("").to_lowercase();
    let described = match asked.as_str() {
        "rust" | "rs" => rsklass::describe(source.as_bytes()),
        "python" | "py" | "class" => klass::describe(source.as_bytes()),
        _ => describe(source.as_bytes()),
    }?;
    if described["lang"] == "wasm" {
        return Err("put_class takes source, and those are compiled bytes — put_mod stores a \
                    wasm anchor"
            .into());
    }

    let mut forwarded = args.clone();
    let obj = forwarded.as_object_mut().ok_or("put_class needs an object of arguments")?;
    obj.remove("source");
    obj.remove("class");
    obj.remove("lang");
    obj.insert("text".into(), json!(source));
    put_module(&forwarded)
}

pub fn module_bytes(key: &str) -> Result<(String, Vec<u8>), String> {
    let m = store::read(|s| s.module(key).cloned()).ok_or_else(|| format!("no mod `{key}`"))?;
    let bytes = anchor_bytes(&m)?;
    Ok((m.id, bytes))
}

/// One file out of a mod folder, by path.
pub fn file_bytes(key: &str, path: &str) -> Result<(String, Vec<u8>), String> {
    let m = store::read(|s| s.module(key).cloned()).ok_or_else(|| format!("no mod `{key}`"))?;
    let f = m
        .file(path)
        .ok_or_else(|| format!("`{}` has no file `{path}` — it holds {}", m.name,
            m.files.iter().map(|f| f.path.clone()).collect::<Vec<_>>().join(", ")))?;
    Ok((f.id.clone(), blobs::get(&f.id)?))
}

/// Load a stored mod and make it answer — the check the structural verifier
/// cannot make.
///
/// `verify` reads the anchor; this one runs it, in the runner, in the sandbox,
/// exactly as a match would. A game is opened, shown a seat, handed a move
/// that is not a move (which it must refuse rather than raise on) and asked
/// whether it is done; a player is asked for one move and has to answer with
/// text. It is the last gate a generated mod goes through, and the difference
/// between "this parses as a game" and "this is a game".
pub async fn smoke(key: &str) -> Result<Value, String> {
    let m = store::read(|s| s.module(key).cloned()).ok_or_else(|| format!("no mod `{key}`"))?;
    let argv = vec![
        "smoke".to_string(),
        "--module".into(),
        m.id.clone(),
        "--role".into(),
        m.role.clone(),
    ];
    match crate::mcp::runner(&argv).await {
        Ok(v) => Ok(v),
        // A runner that exits non-zero has still said something useful on the
        // way out; the failure is the answer here, not an error.
        Err(e) => Ok(json!({ "ok": false, "module": m.id, "name": m.name, "error": e })),
    }
}

pub fn delete_module(key: &str) -> Result<Value, String> {
    let m = store::read(|s| s.module(key).cloned()).ok_or_else(|| format!("no mod `{key}`"))?;
    let players_using = store::read(|s| {
        s.players
            .values()
            .filter(|p| {
                // Resolve the player's module key rather than string-matching
                // it: a player entered by name would otherwise slip past this
                // and lose its mod mid-match.
                p.config
                    .get("module")
                    .and_then(|v| v.as_str())
                    .and_then(|key| s.module(key))
                    .is_some_and(|used| used.id == m.id)
            })
            .map(|p| p.name.clone())
            .collect::<Vec<_>>()
    });
    if !players_using.is_empty() {
        return Err(format!(
            "mod {} is what {} plays with — remove the player first",
            m.short(),
            players_using.join(", ")
        ));
    }
    store::write(|s| s.modules.remove(&m.id));
    // A blob is only this mod's to delete if no other mod holds the same file.
    // Folders share bytes on purpose; deleting one must not empty another.
    let still_used = store::read(|s| {
        s.modules
            .values()
            .flat_map(|other| other.files.iter().map(|f| f.id.clone()))
            .collect::<std::collections::HashSet<_>>()
    });
    let mut freed = 0;
    for f in &m.files {
        if !still_used.contains(&f.id) && blobs::remove(&f.id) {
            freed += 1;
        }
    }
    rustc::forget(&m.id);
    Ok(json!({ "removed": m.id, "name": m.name, "files": m.files.len(), "blobs_freed": freed }))
}

/// Describe something without storing it — how the console previews a file the
/// moment it is dropped, and how a folder is checked before anyone commits to
/// keeping it.
pub fn inspect(args: &Value) -> Result<Value, String> {
    if args.get("files").is_some() {
        return verify_mod(args);
    }
    let raw = match args.get("text").and_then(|v| v.as_str()) {
        Some(text) => text.as_bytes().to_vec(),
        None => {
            let encoded = args
                .get("bytes")
                .or_else(|| args.get("wasm"))
                .and_then(|v| v.as_str())
                .ok_or("inspect needs `files` (a folder), `bytes` (encoded) or `text` (a class, as itself)")?;
            blobs::decode(encoded)?
        }
    };
    let mut v = describe(&raw)?;
    v["id"] = json!(blobs::hash(&raw));
    // What it would become: the folder that would be written around it.
    if let Ok(f) = Folder::wrap(raw.clone(), args) {
        v["as_folder"] = json!({
            "id": f.id(),
            "files": f.files.keys().collect::<Vec<_>>(),
            "config": f.config(),
            "stored": store::read(|s| s.modules.contains_key(&f.id())),
        });
    }
    Ok(v)
}

/// Plant the example pack: every folder under the examples directory is a mod.
/// Called once at startup and by the `examples` tool. Idempotent — the ids are
/// the folders.
pub fn plant_examples() -> Value {
    let dirs = example_dirs();
    let mut planted = Vec::new();
    let mut failed = Vec::new();
    let mut folders: Vec<std::path::PathBuf> = Vec::new();
    for dir in &dirs {
        let Ok(entries) = std::fs::read_dir(dir) else { continue };
        folders.extend(
            entries
                .filter_map(|e| e.ok())
                .map(|e| e.path())
                .filter(|p| p.is_dir()),
        );
    }
    folders.sort();
    if folders.is_empty() {
        return json!({ "planted": 0,
                       "dirs": dirs.iter().map(|d| d.to_string_lossy()).collect::<Vec<_>>(),
                       "note": "no example pack on disk — each example is a folder with a \
                                config.json and an anchor; run src/examples/build.sh to \
                                compile the wasm ones" });
    }

    for path in folders {
        match read_folder_from_disk(&path) {
            Ok(f) => match store_folder(&f, &json!({ "source": "example" })) {
                Ok(v) => planted.push(json!({
                    "name": v["name"], "role": v["role"], "id": v["id"],
                    "lang": v["lang"], "files": v["files"].as_array().map(|a| a.len()).unwrap_or(0),
                })),
                Err(e) => failed.push(json!({ "folder": path.file_name().and_then(|n| n.to_str()), "error": e })),
            },
            Err(e) => failed.push(json!({ "folder": path.file_name().and_then(|n| n.to_str()), "error": e })),
        }
    }
    json!({ "planted": planted.len(), "modules": planted, "failed": failed,
            "dirs": dirs.iter().map(|d| d.to_string_lossy()).collect::<Vec<_>>() })
}

/// Read a mod folder off the filesystem — one directory, one level of
/// subdirectory, nothing that is not a mod file.
pub fn read_folder_from_disk(dir: &std::path::Path) -> Result<Folder, String> {
    let mut f = Folder::new();
    let mut stack = vec![(dir.to_path_buf(), String::new())];
    while let Some((at, prefix)) = stack.pop() {
        let entries = std::fs::read_dir(&at).map_err(|e| format!("{}: {e}", at.display()))?;
        for entry in entries.filter_map(|e| e.ok()) {
            let name = entry.file_name().to_string_lossy().to_string();
            if name.starts_with('.') {
                continue;
            }
            let rel = if prefix.is_empty() { name.clone() } else { format!("{prefix}/{name}") };
            let path = entry.path();
            if path.is_dir() {
                if prefix.is_empty() {
                    stack.push((path, rel));
                }
                continue;
            }
            let ext = name.rsplit('.').next().unwrap_or("");
            if !folder::ALLOWED_EXT.contains(&ext) {
                continue;
            }
            let bytes = std::fs::read(&path).map_err(|e| format!("{rel}: {e}"))?;
            f.add(rel, bytes);
        }
    }
    if f.files.is_empty() {
        return Err(format!("{}: nothing in it that a mod folder holds", dir.display()));
    }
    Ok(f)
}

/// Write a mod folder to disk — how a stored mod becomes a directory again,
/// under `orbit/` or anywhere else.
pub fn write_folder_to_disk(f: &Folder, dir: &std::path::Path) -> Result<Vec<String>, String> {
    std::fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    let mut written = Vec::new();
    for (path, bytes) in &f.files {
        let target = dir.join(path);
        if let Some(parent) = target.parent() {
            std::fs::create_dir_all(parent).map_err(|e| format!("{}: {e}", parent.display()))?;
        }
        std::fs::write(&target, bytes).map_err(|e| format!("{path}: {e}"))?;
        written.push(path.clone());
    }
    Ok(written)
}

// ── players ──────────────────────────────────────────────────────────────

pub fn enter_player(args: &Value) -> Result<Value, String> {
    let name = args
        .get("name")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .ok_or("enter_player needs `name`")?
        .to_string();
    let mut kind = args
        .get("kind")
        .and_then(|v| v.as_str())
        .unwrap_or("model")
        .trim()
        .to_lowercase();
    if !players::KINDS.contains(&kind.as_str()) {
        return Err(format!("unknown kind `{kind}` — expected one of {:?}", players::KINDS));
    }
    let mut config = args.get("config").cloned().unwrap_or_else(|| json!({}));

    // Fail here rather than three turns into a match.
    match kind.as_str() {
        // One check for both, because the module decides which it is: a class
        // entered as `wasm` or a binary entered as `class` still plays, and
        // being pedantic about the label would only strand people.
        "wasm" | "class" => {
            let module = config
                .get("module")
                .and_then(|v| v.as_str())
                .ok_or("this player needs config.module — a module that exports `play` (wasm) \
                        or a class that defines it")?
                .to_string();
            let m = store::read(|s| s.module(&module).cloned())
                .ok_or_else(|| format!("no module `{module}`"))?;
            if m.role != "player" {
                return Err(format!(
                    "module {} is a `{}`, not a player — a player {}",
                    m.short(),
                    m.role,
                    if m.lang() == "python" {
                        "class defines `play(self, view, seat)`"
                    } else {
                        "module must export `play`"
                    }
                ));
            }
            // Pin the resolution now. A player entered by name would otherwise
            // follow that name if it ever moved to different bytes.
            config["module"] = json!(m.id);
            // And say which it really is, whatever was typed.
            kind = if m.lang() == "python" { "class".into() } else { "wasm".into() };
        }
        "model" => {
            config
                .get("model")
                .and_then(|v| v.as_str())
                .ok_or("a model player needs config.model, e.g. \"anthropic/claude-opus-5\"")?;
        }
        "http" => {
            config.get("url").and_then(|v| v.as_str()).ok_or("an http player needs config.url")?;
        }
        _ => {}
    }

    let player = store::write(|s| {
        // Re-entering a name updates it in place, keeping its record — an
        // owner fixing a typo in a config should not lose a rating.
        let existing = s.players.values().find(|p| p.name.eq_ignore_ascii_case(&name)).cloned();
        let mut p = match existing {
            Some(p) => p,
            None => {
                let id = s.next("p");
                Player {
                    id,
                    name: name.clone(),
                    kind: kind.clone(),
                    owner: String::new(),
                    note: String::new(),
                    config: json!({}),
                    overall: Rating::default(),
                    by_game: Default::default(),
                    moves: 0,
                    illegal: 0,
                    timeouts: 0,
                    mcp: 0,
                    move_ms_sum: 0,
                    created: store::now(),
                }
            }
        };
        p.kind = kind.clone();
        p.config = config.clone();
        if let Some(v) = args.get("owner").and_then(|v| v.as_str()) {
            p.owner = v.to_string();
        }
        if let Some(v) = args.get("note").and_then(|v| v.as_str()) {
            p.note = v.to_string();
        }
        s.players.insert(p.id.clone(), p.clone());
        p
    });
    Ok(player.card())
}

/// A player's config with its secrets taken out. Anyone can read a player —
/// the console does it on load — and a config holds whatever its driver needs,
/// which for a model is an API key.
fn redact(config: &Value) -> Value {
    let Some(obj) = config.as_object() else {
        return config.clone();
    };
    obj.iter()
        .map(|(k, v)| {
            let lower = k.to_lowercase();
            let secret = ["key", "token", "secret", "password", "authorization", "headers"]
                .iter()
                .any(|s| lower.contains(s));
            let shown = if secret && !v.is_null() { json!("···") } else { v.clone() };
            (k.clone(), shown)
        })
        .collect::<serde_json::Map<_, _>>()
        .into()
}

pub fn list_players(args: &Value) -> Value {
    let kind = args.get("kind").and_then(|v| v.as_str()).unwrap_or("").to_lowercase();
    let list = store::read(|s| {
        s.player_list()
            .into_iter()
            .filter(|p| kind.is_empty() || p.kind == kind)
            .map(|p| p.card())
            .collect::<Vec<_>>()
    });
    json!({ "count": list.len(), "players": list })
}

pub fn get_player(key: &str) -> Result<Value, String> {
    let p = store::read(|s| s.player(key).cloned()).ok_or_else(|| format!("no player `{key}`"))?;
    let names = store::read(|s| {
        p.by_game
            .keys()
            .map(|g| {
                let name = s.modules.get(g).map(|m| m.name.clone()).unwrap_or_else(|| g[..8.min(g.len())].into());
                (g.clone(), name)
            })
            .collect::<Vec<_>>()
    });
    let mut card = p.card();
    card["config"] = redact(&p.config);
    card["by_game"] = json!(names
        .iter()
        .map(|(id, name)| {
            let mut v = p.by_game.get(id).cloned().unwrap_or_default().card();
            v["game"] = json!(id);
            v["game_name"] = json!(name);
            v
        })
        .collect::<Vec<_>>());
    Ok(card)
}

pub fn remove_player(key: &str) -> Result<Value, String> {
    let p = store::read(|s| s.player(key).cloned()).ok_or_else(|| format!("no player `{key}`"))?;
    store::write(|s| s.players.remove(&p.id));
    Ok(json!({ "removed": p.id, "name": p.name }))
}

/// One move from a player the execution layer cannot drive itself. This is the
/// only outbound call the server makes on a match's behalf.
pub async fn play(key: &str, view: &str, seat: usize) -> Result<Value, String> {
    let p = store::read(|s| s.player(key).cloned()).ok_or_else(|| format!("no player `{key}`"))?;
    let t0 = std::time::Instant::now();
    let a = players::play(&p, view, seat).await?;
    Ok(json!({
        "player": p.name, "seat": seat, "move": a.mv, "raw": a.raw, "note": a.note,
        "ms": t0.elapsed().as_millis() as u64, "meta": a.meta,
    }))
}

// ── matches ──────────────────────────────────────────────────────────────

/// Take a finished match from a runner, rate it, and keep it.
///
/// The runner is trusted for the outcome — it is the thing that actually ran
/// the wasm. What makes that honest rather than hopeful is the transcript:
/// the seed and every move are recorded, the game module is pure over its
/// state, so anyone can replay a match and get the same scores. A leaderboard
/// here is a claim with its working attached.
fn bump(r: &mut Rating, score: f64, result: &str, delta: f64) {
    r.matches += 1;
    r.score_sum += score;
    match result {
        "win" => r.wins += 1,
        "draw" => r.draws += 1,
        _ => r.losses += 1,
    }
    r.elo += delta;
}

pub fn record_match(rec: &Value) -> Result<Value, String> {
    let game_key = rec
        .get("game")
        .and_then(|v| v.as_str())
        .ok_or("a match record needs `game`")?;
    let game = store::read(|s| s.module(game_key).cloned())
        .ok_or_else(|| format!("no game module `{game_key}`"))?;

    let seats_in = rec
        .get("seats")
        .and_then(|v| v.as_array())
        .ok_or("a match record needs `seats`")?;
    if seats_in.is_empty() {
        return Err("a match record needs at least one seat".into());
    }

    // Resolve every seat to a real player before touching any rating.
    let mut resolved: Vec<(Player, Value)> = Vec::new();
    for s in seats_in {
        let key = s
            .get("player_id")
            .or_else(|| s.get("player"))
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let p = store::read(|st| st.player(key).cloned())
            .ok_or_else(|| format!("no player `{key}` in seat {}", resolved.len()))?;
        resolved.push((p, s.clone()));
    }

    let scores: Vec<f64> = resolved
        .iter()
        .map(|(_, s)| s.get("score").and_then(|v| v.as_f64()).unwrap_or(0.0))
        .collect();
    // Two ratings move, and they are rated against different fields: the
    // per-game one against how these players do at *this* game, the overall
    // one against how they do in general. Using the per-game delta for both
    // would let a specialist's first match against a strong all-rounder count
    // twice at the wrong odds.
    let elos: Vec<f64> = resolved
        .iter()
        .map(|(p, _)| p.by_game.get(&game.id).map(|r| r.elo).unwrap_or(store::START_ELO))
        .collect();
    let overall_elos: Vec<f64> = resolved.iter().map(|(p, _)| p.overall.elo).collect();
    let rated = resolved.len() >= 2;
    let deltas = if rated { rating::deltas(&elos, &scores) } else { vec![0.0; elos.len()] };
    let overall_deltas = if rated {
        rating::deltas(&overall_elos, &scores)
    } else {
        vec![0.0; elos.len()]
    };

    let turns: Vec<Turn> = rec
        .get("turns")
        .and_then(|v| v.as_array())
        .map(|a| a.iter().filter_map(|t| serde_json::from_value(t.clone()).ok()).collect())
        .unwrap_or_default();

    let out = store::write(|st| {
        let mut seats: Vec<Seat> = Vec::new();
        for (i, (p, raw)) in resolved.iter().enumerate() {
            let moves = raw.get("moves").and_then(|v| v.as_u64()).unwrap_or(0);
            let illegal = raw.get("illegal").and_then(|v| v.as_u64()).unwrap_or(0);
            let timeouts = raw.get("timeouts").and_then(|v| v.as_u64()).unwrap_or(0);
            let ms = raw.get("ms").and_then(|v| v.as_u64()).unwrap_or(0);
            let mcp = raw.get("mcp").and_then(|v| v.as_u64()).unwrap_or(0);
            let result = rating::outcome(scores[i], &scores);

            if let Some(pl) = st.players.get_mut(&p.id) {
                pl.moves += moves;
                pl.illegal += illegal;
                pl.timeouts += timeouts;
                pl.mcp += mcp;
                pl.move_ms_sum += ms;
                bump(&mut pl.overall, scores[i], result, overall_deltas[i]);
                bump(pl.by_game.entry(game.id.clone()).or_default(), scores[i], result, deltas[i]);
            }

            seats.push(Seat {
                seat: i,
                player_id: p.id.clone(),
                player_name: p.name.clone(),
                score: scores[i],
                moves,
                illegal,
                timeouts,
                ms,
                mcp,
                elo_before: elos[i],
                elo_after: elos[i] + deltas[i],
                error: raw.get("error").and_then(|v| v.as_str()).unwrap_or("").into(),
            });
        }

        if let Some(m) = st.modules.get_mut(&game.id) {
            m.runs += 1;
        }

        let id = st.next("m");
        let m = Match {
            id,
            game: game.id.clone(),
            game_name: rec.get("game_name").and_then(|v| v.as_str()).unwrap_or(&game.name).into(),
            seed: rec.get("seed").and_then(|v| v.as_i64()).unwrap_or(0),
            seats,
            turns,
            summary: rec.get("summary").and_then(|v| v.as_str()).unwrap_or("").into(),
            runtime: rec.get("runtime").and_then(|v| v.as_str()).unwrap_or("unknown").into(),
            rated,
            ms: rec.get("ms").and_then(|v| v.as_u64()).unwrap_or(0),
            created: store::now(),
        };
        st.record_match(m.clone());
        m
    });

    let mut v = out.brief();
    v["rated"] = json!(rated);
    if !rated {
        v["note"] = json!("one seat — practice, so no rating moved");
    }
    Ok(v)
}

pub fn list_matches(args: &Value) -> Value {
    let limit = args.get("limit").and_then(|v| v.as_u64()).unwrap_or(20).clamp(1, 200) as usize;
    let game = args.get("game").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let list = store::read(|s| {
        let gid = if game.is_empty() { None } else { s.module(&game).map(|m| m.id.clone()) };
        s.matches
            .iter()
            .rev()
            .filter(|m| gid.as_ref().map(|g| &m.game == g).unwrap_or(true))
            .take(limit)
            .map(|m| m.brief())
            .collect::<Vec<_>>()
    });
    json!({ "count": list.len(), "matches": list })
}

pub fn get_match(id: &str) -> Result<Value, String> {
    store::read(|s| s.matches.iter().find(|m| m.id == id).cloned())
        .map(|m| serde_json::to_value(&m).unwrap_or_else(|_| json!({})))
        .ok_or_else(|| format!("no match `{id}`"))
}

/// Players ranked. Per game when one is named — which is the ranking that
/// means something, since being good at nim says nothing about poker.
pub fn leaderboard(args: &Value) -> Result<Value, String> {
    let limit = args.get("limit").and_then(|v| v.as_u64()).unwrap_or(20).clamp(1, 200) as usize;
    let game = args.get("game").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();

    if game.is_empty() {
        let rows = store::read(|s| {
            s.player_list()
                .into_iter()
                .filter(|p| p.overall.matches > 0)
                .take(limit)
                .map(|p| p.card())
                .collect::<Vec<_>>()
        });
        return Ok(json!({ "scope": "overall", "count": rows.len(), "players": rows }));
    }

    let m = store::read(|s| s.module(&game).cloned()).ok_or_else(|| format!("no game `{game}`"))?;
    let mut rows = store::read(|s| {
        s.players
            .values()
            .filter_map(|p| {
                let r = p.by_game.get(&m.id)?;
                let mut v = r.card();
                v["id"] = json!(p.id);
                v["name"] = json!(p.name);
                v["kind"] = json!(p.kind);
                Some((r.elo, v))
            })
            .collect::<Vec<_>>()
    });
    rows.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
    Ok(json!({
        "scope": m.name, "game": m.id, "count": rows.len().min(limit),
        "players": rows.into_iter().take(limit).map(|(_, v)| v).collect::<Vec<_>>(),
    }))
}
