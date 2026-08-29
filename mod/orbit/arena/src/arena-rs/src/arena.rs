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
use crate::storelink;
use crate::store::{self, round3, Match, Player, Rating, Seat, Turn, WasmModule};
use std::collections::HashMap;
use crate::wasm;
use serde_json::{json, Value};

/// Where the example pack lives: the compiled wasm, and the classes. Baked as
/// paths, not as bytes, so the pack can be rebuilt or added to without
/// rebuilding the server.
fn example_dirs() -> Vec<std::path::PathBuf> {
    if let Ok(d) = std::env::var("ARENA_EXAMPLES") {
        return d.split(':').map(std::path::PathBuf::from).collect();
    }
    vec![
        std::path::PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/../examples/wasm")),
        std::path::PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/../examples/classes")),
        std::path::PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/../examples/rust")),
    ]
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
        "name": "arena",
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
            "arena": "/mcp — the whole arena, as one server",
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
    if raw.starts_with(b"\0asm") {
        return wasm::describe(raw);
    }
    if rsklass::looks_like_rust(raw) {
        return rsklass::describe(raw);
    }
    if klass::looks_like_python(raw) {
        return klass::describe(raw);
    }
    Err("these bytes are none of the three things this registry holds — not a wasm module \
         (no \\0asm header), not Python source (no class, def or import), and not Rust \
         source (no struct, impl or fn). `m arena/abi lang=class` or `lang=rust` prints \
         the contract."
        .into())
}

/// The wasm a module actually runs as. For a wasm upload that is the bytes
/// themselves; for a Rust class it is the compile, cached under the module's
/// id. A Python class has no wasm form and says so — it runs in the
/// interpreter sandbox instead.
///
/// Everything that executes goes through here, which is why a Rust class plays
/// in a browser tab and a Python class does not.
pub fn compiled(key: &str) -> Result<(String, Vec<u8>), String> {
    let m = store::read(|s| s.module(key).cloned())
        .ok_or_else(|| format!("no module `{key}`"))?;
    let raw = blobs::get(&m.id)?;
    match m.lang() {
        "wasm" => Ok((m.id, raw)),
        "rust" => {
            let source = String::from_utf8(raw).map_err(|_| "the source is not UTF-8".to_string())?;
            let bytes = rustc::compile(&m.id, &source)?;
            Ok((m.id, bytes))
        }
        other => Err(format!(
            "`{}` is a {other} class — it runs in the interpreter sandbox, not in a wasm \
             engine, so there is no wasm form of it to fetch",
            m.name
        )),
    }
}

/// Store a module. The id is the hash of the bytes, so uploading the same
/// thing twice updates the metadata and never duplicates the blob.
pub fn put_module(args: &Value) -> Result<Value, String> {
    // `text` is source as itself; `bytes` is anything, encoded. Keeping them
    // apart is the difference between "here is my class" and "decode this".
    let raw = match args.get("text").and_then(|v| v.as_str()) {
        Some(text) => text.as_bytes().to_vec(),
        None => {
            let encoded = args
                .get("bytes")
                .or_else(|| args.get("wasm"))
                .or_else(|| args.get("base64"))
                .and_then(|v| v.as_str())
                .ok_or("put_module needs `bytes` — a wasm module or a class, base64 or hex \
                        encoded — or `text`, a class as itself")?;
            blobs::decode(encoded)?
        }
    };
    if raw.is_empty() {
        return Err("put_module got zero bytes".into());
    }
    // Read before storing: a blob that cannot be described is not a module,
    // and the registry promises every entry can be introspected. Which reader
    // runs is decided by the bytes — wasm's four magic bytes, or source.
    let described = describe(&raw)?;
    let id = blobs::put(&raw)?;

    let asked = args
        .get("name")
        .and_then(|v| v.as_str())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| format!("module-{}", &id[..8]));

    // Bytes that are already here keep the name they arrived under. The id is
    // the content, so a re-upload carries nothing new to name — and renaming
    // on re-upload would let anyone move a game out from under the players
    // entered at it, just by uploading a copy.
    let renamed = store::read(|s| s.modules.get(&id).map(|m| m.name.clone()))
        .filter(|existing| *existing != asked);

    // A readable source that travels beside compiled bytes — the Rust a wasm
    // was built from. It is not part of the id (the id is the bytes) but it is
    // stored under its own hash, and it is what "show me the code" shows.
    let src = match args.get("source_text").and_then(|v| v.as_str()).map(str::trim) {
        Some(text) if !text.is_empty()
            && described.get("lang").and_then(|v| v.as_str()).unwrap_or("wasm") == "wasm" =>
        {
            blobs::put(text.as_bytes())?
        }
        _ => String::new(),
    };

    let module = store::write(|s| {
        let existing = s.modules.get(&id).cloned();
        let src_changed = !src.is_empty() && existing.as_ref().map(|e| e.src != src).unwrap_or(true);
        let m = WasmModule {
            id: id.clone(),
            name: existing.as_ref().map(|e| e.name.clone()).unwrap_or(asked),
            role: described["role"].as_str().unwrap_or("wasm").to_string(),
            description: args.get("description").and_then(|v| v.as_str()).unwrap_or("").into(),
            author: args.get("author").and_then(|v| v.as_str()).unwrap_or("").into(),
            tags: args
                .get("tags")
                .and_then(|v| v.as_array())
                .map(|a| a.iter().filter_map(|t| t.as_str().map(String::from)).collect())
                .unwrap_or_default(),
            size: raw.len(),
            info: described,
            source: args.get("source").or_else(|| args.get("origin"))
                .and_then(|v| v.as_str()).unwrap_or("upload").into(),
            runs: existing.as_ref().map(|e| e.runs).unwrap_or(0),
            created: existing.as_ref().map(|e| e.created).unwrap_or_else(store::now),
            cid: existing.as_ref().map(|e| e.cid.clone()).unwrap_or_default(),
            src: if src.is_empty() { existing.as_ref().map(|e| e.src.clone()).unwrap_or_default() } else { src.clone() },
            src_cid: if src_changed { String::new() } else { existing.as_ref().map(|e| e.src_cid.clone()).unwrap_or_default() },
            stored: existing.as_ref().map(|e| e.stored).unwrap_or(0),
        };
        s.modules.insert(id.clone(), m.clone());
        m
    });
    // New bytes, or a source the store has not seen: push in the background.
    if storelink::needs_push(&module) {
        storelink::push_later(id.clone());
    }

    let mut v = module.card();
    v["url"] = json!(module.url());
    v["info"] = module.info;
    if let Some(kept) = renamed {
        v["note"] = json!(format!(
            "these bytes were already stored as `{kept}` — the id is the content, so the name stands"
        ));
    }
    Ok(v)
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

/// One module in full. A class carries its source, because for a class the
/// source *is* the description — nobody reads a list of method names to work
/// out how a game plays.
pub fn get_module(key: &str, with_source: bool) -> Result<Value, String> {
    let m = store::read(|s| s.module(key).cloned())
        .ok_or_else(|| format!("no module `{key}` — ids resolve in full, by name, or by an unambiguous prefix of {}+ hex characters", blobs::MIN_PREFIX))?;
    let mut v = m.card();
    v["info"] = m.info.clone();
    v["url"] = json!(m.url());
    v["stored"] = json!(blobs::exists(&m.id));
    if with_source && m.lang() != "wasm" && m.size <= 256 * 1024 {
        if let Ok(raw) = blobs::get(&m.id) {
            if let Ok(text) = String::from_utf8(raw) {
                v["source"] = json!(text);
                v["source_lang"] = json!(m.lang());
            }
        }
    }
    // A wasm module is bytes, but the bytes may have arrived with the code
    // they were built from; that is what a reader wants to see.
    if with_source && m.lang() == "wasm" && !m.src.is_empty() {
        if let Ok(text) = blobs::get(&m.src).and_then(|raw| String::from_utf8(raw).map_err(|e| e.to_string())) {
            v["source"] = json!(text);
            v["source_lang"] = json!("rust");
            v["source_id"] = json!(m.src);
        }
    }
    Ok(v)
}

/// Store a class from plain text — `put_module` for people and agents who are
/// holding source code rather than a compiled artefact.
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
    // Read it first, so a file with no class in it is refused with the ABI
    // rather than stored as an unplayable blob.
    let asked = args.get("lang").and_then(|v| v.as_str()).unwrap_or("").to_lowercase();
    let described = match asked.as_str() {
        "rust" | "rs" => rsklass::describe(source.as_bytes()),
        "python" | "py" | "class" => klass::describe(source.as_bytes()),
        _ => describe(source.as_bytes()),
    }?;
    if described["lang"] == "wasm" {
        return Err("put_class takes source, and those are compiled bytes — put_module \
                    stores a wasm binary"
            .into());
    }

    let mut forwarded = args.clone();
    let obj = forwarded.as_object_mut().ok_or("put_class needs an object of arguments")?;
    obj.remove("source");
    obj.remove("text");
    obj.remove("class");
    obj.remove("lang");
    obj.insert("text".into(), json!(source));
    // Unnamed, a class is called what the class is called — the author already
    // named it once and should not have to do it twice.
    if !obj.contains_key("name") {
        if let Some(class_name) = described.get("class").and_then(|v| v.as_str()) {
            obj.insert("name".into(), json!(class_name.to_lowercase()));
        }
    }
    if !obj.contains_key("description") {
        if let Some(doc) = described.get("doc").and_then(|v| v.as_str()).filter(|d| !d.is_empty()) {
            obj.insert("description".into(), json!(doc));
        }
    }
    let mut stored = put_module(&forwarded)?;
    if stored["role"] == "class" {
        stored["note"] = json!(format!(
            "stored, but not playable yet — {} defines {}, and it still needs {}. \
             Call game_abi with lang={lang} for the contract.",
            described["class"].as_str().unwrap_or("this class"),
            described["exports"].as_array().map(|a| a.iter()
                .filter_map(|e| e["name"].as_str()).collect::<Vec<_>>().join(", "))
                .unwrap_or_default(),
            described["missing"].as_array().map(|a| a.iter()
                .filter_map(|m| m.as_str()).collect::<Vec<_>>().join(", "))
                .unwrap_or_default(),
            lang = if described["lang"] == "rust" { "rust" } else { "class" },
        ));
    }
    Ok(stored)
}

pub fn module_bytes(key: &str) -> Result<(String, Vec<u8>), String> {
    let id = store::read(|s| s.module(key).map(|m| m.id.clone()))
        .ok_or_else(|| format!("no module `{key}`"))?;
    let bytes = blobs::get(&id)?;
    Ok((id, bytes))
}

pub fn delete_module(key: &str) -> Result<Value, String> {
    let m = store::read(|s| s.module(key).cloned()).ok_or_else(|| format!("no module `{key}`"))?;
    let players_using = store::read(|s| {
        s.players
            .values()
            .filter(|p| {
                // Resolve the player's module key rather than string-matching
                // it: a player entered by name would otherwise slip past this
                // and lose its module mid-match.
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
            "module {} is what {} plays with — remove the player first",
            m.short(),
            players_using.join(", ")
        ));
    }
    store::write(|s| s.modules.remove(&m.id));
    blobs::remove(&m.id);
    rustc::forget(&m.id);
    if !m.src.is_empty() && !store::read(|s| s.modules.values().any(|o| o.src == m.src)) {
        blobs::remove(&m.src);
    }
    storelink::forget_later(m.cid.clone());
    storelink::forget_later(m.src_cid.clone());
    Ok(json!({ "removed": m.id, "name": m.name, "cid": m.cid }))
}

/// Describe bytes without storing them — how the console previews a file the
/// moment it is dropped, before anyone commits to keeping it.
pub fn inspect(args: &Value) -> Result<Value, String> {
    let raw = match args.get("text").and_then(|v| v.as_str()) {
        Some(text) => text.as_bytes().to_vec(),
        None => {
            let encoded = args
                .get("bytes")
                .or_else(|| args.get("wasm"))
                .and_then(|v| v.as_str())
                .ok_or("inspect needs `bytes` (encoded) or `text` (a class, as itself)")?;
            blobs::decode(encoded)?
        }
    };
    let mut v = describe(&raw)?;
    v["id"] = json!(blobs::hash(&raw));
    v["stored"] = json!(blobs::exists(&blobs::hash(&raw)));
    Ok(v)
}

/// Plant the example pack. Called once at startup, and by the `examples` tool
/// when someone rebuilds it. Idempotent — the ids are the content.
pub fn plant_examples() -> Value {
    let dirs = example_dirs();
    let mut planted = Vec::new();
    let mut failed = Vec::new();
    let mut files: Vec<std::path::PathBuf> = Vec::new();
    for dir in &dirs {
        let Ok(entries) = std::fs::read_dir(dir) else { continue };
        files.extend(entries.filter_map(|e| e.ok()).map(|e| e.path()));
    }
    files.sort();
    if files.is_empty() {
        return json!({ "planted": 0,
                       "dirs": dirs.iter().map(|d| d.to_string_lossy()).collect::<Vec<_>>(),
                       "note": "no example pack on disk — run src/examples/build.sh to compile it" });
    }

    for path in files {
        // Three kinds of example, one loop: a compiled module, or a class in
        // either language. The reader tells them apart on the way in, so the
        // loop does not have to.
        let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
        if ext != "wasm" && ext != "py" && ext != "rs" {
            continue;
        }
        let Ok(raw) = std::fs::read(&path) else { continue };
        let stem = path.file_stem().and_then(|s| s.to_str()).unwrap_or("example").to_string();
        // An optional sidecar gives the example its name and description.
        let meta: Value = std::fs::read_to_string(path.with_extension("json"))
            .ok()
            .and_then(|t| serde_json::from_str(&t).ok())
            .unwrap_or_else(|| json!({}));

        // The example pack keeps a compiled wasm's Rust one directory up, as
        // <stem>.rs — planted beside the bytes so the console can show it.
        let source_text = if ext == "wasm" {
            path.parent()
                .and_then(|d| d.parent())
                .map(|d| d.join(format!("{stem}.rs")))
                .and_then(|p| std::fs::read_to_string(p).ok())
                .unwrap_or_default()
        } else {
            String::new()
        };
        let args = json!({
            "bytes": blobs::to_base64(&raw),
            "source_text": source_text,
            "name": meta.get("name").and_then(|v| v.as_str()).unwrap_or(&stem),
            "description": meta.get("description").and_then(|v| v.as_str()).unwrap_or(""),
            "author": meta.get("author").and_then(|v| v.as_str()).unwrap_or("arena"),
            "tags": meta.get("tags").cloned().unwrap_or_else(|| json!(["example"])),
            "source": "example",
        });
        match put_module(&args) {
            Ok(v) => planted.push(json!({
                "name": v["name"], "role": v["role"], "id": v["id"], "size": v["size"],
                "lang": v["info"]["lang"].as_str().unwrap_or("wasm"),
            })),
            Err(e) => failed.push(json!({ "file": stem, "error": e })),
        }
    }
    json!({ "planted": planted.len(), "modules": planted, "failed": failed,
            "dirs": dirs.iter().map(|d| d.to_string_lossy()).collect::<Vec<_>>() })
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
                    match m.lang() {
                        "python" => "class defines `play(self, view, seat)`",
                        "rust" => "class defines `play(&mut self, view: &str, seat: usize)`",
                        _ => "module must export `play`",
                    }
                ));
            }
            // Pin the resolution now. A player entered by name would otherwise
            // follow that name if it ever moved to different bytes.
            config["module"] = json!(m.id);
            // And say which it really is, whatever was typed. A Rust class is
            // a class even though it executes as wasm — the card should say
            // what somebody wrote, and `lang` on the module says where it runs.
            kind = if m.lang() == "wasm" { "wasm".into() } else { "class".into() };
            config["lang"] = json!(m.lang());
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

    // The ratings know who won; the matches know how it was played. Faults,
    // pace and calls out are kept per player, not per game, so the per-game
    // sheet is read off the seats this player actually sat in — oldest first,
    // so `form` reads left to right the way a season does.
    #[derive(Default)]
    struct Sheet {
        moves: u64,
        illegal: u64,
        timeouts: u64,
        ms: u64,
        mcp: u64,
        form: Vec<&'static str>,
        last: u64,
    }
    let mut per_game: HashMap<String, Sheet> = HashMap::new();
    let mut form: Vec<&'static str> = Vec::new();
    let mut last_played = 0u64;
    let mut opponents: HashMap<String, (String, u64, u64, u64, u64)> = HashMap::new();
    store::read(|s| {
        for m in s.matches.iter().filter(|m| m.rated) {
            let Some(seat) = m.seats.iter().find(|x| x.player_id == p.id) else { continue };
            let scores: Vec<f64> = m.seats.iter().map(|x| x.score).collect();
            let result = rating::outcome(seat.score, &scores);
            let sheet = per_game.entry(m.game.clone()).or_default();
            sheet.moves += seat.moves;
            sheet.illegal += seat.illegal;
            sheet.timeouts += seat.timeouts;
            sheet.ms += seat.ms;
            sheet.mcp += seat.mcp;
            sheet.form.push(result);
            sheet.last = sheet.last.max(m.created);
            form.push(result);
            last_played = last_played.max(m.created);
            for other in m.seats.iter().filter(|x| x.player_id != p.id) {
                let e = opponents
                    .entry(other.player_id.clone())
                    .or_insert_with(|| (other.player_name.clone(), 0, 0, 0, 0));
                e.1 += 1;
                match result {
                    "win" => e.2 += 1,
                    "draw" => e.3 += 1,
                    _ => e.4 += 1,
                }
            }
        }
    });
    let letter = |r: &str| match r {
        "win" => "W",
        "draw" => "D",
        _ => "L",
    };
    let streak = |f: &[&str]| -> String {
        let Some(&last) = f.last() else { return String::new() };
        let n = f.iter().rev().take_while(|r| **r == last).count();
        format!("{}{}", letter(last), n)
    };

    let mut card = p.card();
    card["config"] = redact(&p.config);
    // The prompt, as the player will actually receive it — a leaderboard that
    // assesses models has to be able to show what it asked them.
    if let Some(v) = players::prompt_card(&p) {
        card["prompt"] = v;
    }
    card["form"] = json!(form.iter().rev().take(10).rev().map(|r| letter(r)).collect::<String>());
    card["streak"] = json!(streak(&form));
    card["last_played"] = json!(last_played);
    let mut by_game = names
        .iter()
        .map(|(id, name)| {
            let r = p.by_game.get(id).cloned().unwrap_or_default();
            let mut v = r.card();
            v["game"] = json!(id);
            v["game_name"] = json!(name);
            let sheet = per_game.remove(id).unwrap_or_default();
            v["moves"] = json!(sheet.moves);
            v["illegal"] = json!(sheet.illegal);
            v["timeouts"] = json!(sheet.timeouts);
            v["mcp"] = json!(sheet.mcp);
            v["illegal_rate"] = json!(if sheet.moves == 0 { 0.0 } else { round3(sheet.illegal as f64 / sheet.moves as f64) });
            v["avg_move_ms"] = json!(if sheet.moves == 0 { 0 } else { sheet.ms / sheet.moves });
            v["form"] = json!(sheet.form.iter().rev().take(10).rev().map(|r| letter(r)).collect::<String>());
            v["streak"] = json!(streak(&sheet.form));
            v["last_played"] = json!(sheet.last);
            (r.elo, v)
        })
        .collect::<Vec<_>>();
    by_game.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
    if let Some((_, best)) = by_game.first() {
        card["best_game"] = best["game_name"].clone();
    }
    card["by_game"] = json!(by_game.into_iter().map(|(_, v)| v).collect::<Vec<_>>());
    let mut rivals = opponents
        .into_iter()
        .map(|(id, (name, n, w, d, l))| json!({
            "id": id, "name": name, "matches": n, "wins": w, "draws": d, "losses": l,
            "win_rate": round3(w as f64 / n.max(1) as f64),
        }))
        .collect::<Vec<_>>();
    rivals.sort_by(|a, b| b["matches"].as_u64().cmp(&a["matches"].as_u64()));
    card["opponents"] = json!(rivals);
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
        "prompt": a.prompt, "ms": t0.elapsed().as_millis() as u64, "meta": a.meta,
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
    let player = args.get("player").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let list = store::read(|s| {
        let gid = if game.is_empty() { None } else { s.module(&game).map(|m| m.id.clone()) };
        // A named player that does not exist matches nothing, not everything.
        let pid = if player.is_empty() {
            None
        } else {
            Some(s.player(&player).map(|p| p.id.clone()).unwrap_or_default())
        };
        s.matches
            .iter()
            .rev()
            .filter(|m| gid.as_ref().map(|g| &m.game == g).unwrap_or(true))
            .filter(|m| pid.as_ref().map(|p| m.seats.iter().any(|x| &x.player_id == p)).unwrap_or(true))
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
