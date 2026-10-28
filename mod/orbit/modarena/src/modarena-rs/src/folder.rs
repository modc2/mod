//! The unit of this registry: a **mod folder**.
//!
//! In the mod protocol a module is a directory with a `config.json` and an
//! anchor — the one file the whole thing is about. This registry holds the
//! same shape, one level down: every game and every agent stored here is a
//! folder with a `config.json` and an anchor named `mod.py`, `mod.rs` or
//! `mod.wasm`. Nothing else in the tree is special. The anchor is what runs;
//! the config says what it claims to be; the rest of the files come along.
//!
//! ```text
//! connect4/
//!   config.json      name, kind, lang, anchor, description, players
//!   mod.py           the class — this is what executes
//!   README.md        optional
//!   board.py         optional, importable from the anchor and nothing else
//! ```
//!
//! Two rules make the folder worth the trouble.
//!
//! **The id is the folder, not a file.** Every file is stored by the hash of
//! its own bytes (so two mods that share a README share one blob) and the
//! mod's id is the hash of the manifest listing those hashes in order. Change
//! any byte of any file and the id changes; ship the same folder twice and it
//! is the same mod. Same promise the single-file registry made, one level up.
//!
//! **The config is a claim and the anchor is the fact.** `config.json` says
//! `"kind": "game"`. The reader — `klass` for Python, `rsklass` for Rust,
//! `wasm` for a binary — reads the anchor and says what it actually defines.
//! [`Folder::verify`] is where those two meet, and a folder whose config
//! disagrees with its own anchor does not get stored. That is the check a
//! generated mod has to pass, and the reason a generator can be trusted to
//! write one: the template is a shape, and this is the judge of it.

use crate::blobs;
use crate::klass;
use crate::rsklass;
use crate::wasm;
use serde_json::{json, Map, Value};
use std::collections::BTreeMap;

type R<T> = Result<T, String>;

/// The `protocol` every arena mod's config.json declares. Its own name and
/// version, because a folder that travels (published to `orbit/`, handed to
/// another arena) has to be able to say what contract it was written against.
pub const PROTOCOL: &str = "modarena/1.0";

/// One anchor per language, and the language is readable off the filename —
/// which is the point of fixing the name. `mod.py` is a Python class,
/// `mod.rs` is a Rust struct, `mod.wasm` is a compiled binary.
pub const ANCHORS: [(&str, &str); 3] = [("python", "mod.py"), ("rust", "mod.rs"), ("wasm", "mod.wasm")];

/// What a mod folder may contain. Not a security boundary — the sandbox is —
/// but a folder full of things nothing will ever read is a folder somebody
/// misunderstood, and saying so at upload time is cheaper than at match time.
pub const ALLOWED_EXT: [&str; 6] = ["py", "rs", "wasm", "json", "md", "txt"];

pub const MAX_FILES: usize = 64;
pub const MAX_BYTES: usize = 8 << 20;
pub const CONFIG: &str = "config.json";

/// The kinds a config may declare. `game` and `player` are the two that play;
/// the rest are what the readers call a file that is stored and readable but
/// not seated at anything.
pub const KINDS: [&str; 5] = ["game", "player", "command", "class", "wasm"];

pub fn anchor_for(lang: &str) -> Option<&'static str> {
    ANCHORS.iter().find(|(l, _)| *l == lang).map(|(_, a)| *a)
}

pub fn lang_for(anchor: &str) -> Option<&'static str> {
    ANCHORS.iter().find(|(_, a)| *a == anchor).map(|(l, _)| *l)
}

/// Which reader describes these bytes — four magic bytes, or which language
/// the source is written in. The one place the question is answered.
pub fn sniff(raw: &[u8]) -> R<Value> {
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
         source (no struct, impl or fn). `m modarena/template kind=game lang=python` \
         prints a folder that is."
        .into())
}

/// Read an anchor as the language its filename promises. Asking the named
/// reader rather than sniffing is what lets `verify` report "your config says
/// rust and mod.rs holds Python" instead of quietly believing the file.
pub fn read_as(lang: &str, raw: &[u8]) -> R<Value> {
    match lang {
        "wasm" => wasm::describe(raw),
        "rust" => rsklass::describe(raw),
        "python" => klass::describe(raw),
        other => Err(format!("no reader for lang `{other}` — expected python, rust or wasm")),
    }
}

/// A slug is a directory name somewhere eventually — in `orbit/`, in a URL, in
/// a leaderboard row. Keep it to what all three survive.
pub fn is_slug(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 40
        && s.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-' || c == '_')
        && s.chars().next().is_some_and(|c| c.is_ascii_lowercase())
}

/// A path inside the folder. Relative, no traversal, no absolutes — a folder
/// that arrives over the wire is written to disk eventually.
fn path_ok(p: &str) -> Result<(), String> {
    if p.is_empty() {
        return Err("empty path".into());
    }
    if p.starts_with('/') || p.starts_with('~') {
        return Err(format!("`{p}` is absolute — every path in a mod folder is relative to it"));
    }
    if p.split('/').any(|seg| seg == ".." || seg == "." || seg.is_empty()) {
        return Err(format!("`{p}` walks out of the folder"));
    }
    if p.contains('\\') || p.chars().any(|c| c.is_control()) {
        return Err(format!("`{p}` is not a portable path"));
    }
    if p.split('/').count() > 3 {
        return Err(format!("`{p}` is nested deeper than a mod folder goes"));
    }
    let ext = p.rsplit('.').next().unwrap_or("");
    if !ALLOWED_EXT.contains(&ext) || !p.contains('.') {
        return Err(format!(
            "`{p}` — a mod folder holds {}; nothing else here would ever be read",
            ALLOWED_EXT.map(|e| format!(".{e}")).join(", ")
        ));
    }
    Ok(())
}

// ── the folder ───────────────────────────────────────────────────────────

/// A candidate mod folder, in memory: paths to bytes. Ordered, because the id
/// is computed off the listing and the listing has to be the same every time.
#[derive(Clone, Debug, Default)]
pub struct Folder {
    pub files: BTreeMap<String, Vec<u8>>,
}

impl Folder {
    pub fn new() -> Folder {
        Folder::default()
    }

    pub fn add(&mut self, path: impl Into<String>, bytes: impl Into<Vec<u8>>) -> &mut Folder {
        self.files.insert(path.into(), bytes.into());
        self
    }

    pub fn get(&self, path: &str) -> Option<&Vec<u8>> {
        self.files.get(path)
    }

    pub fn text(&self, path: &str) -> Option<String> {
        self.files.get(path).and_then(|b| String::from_utf8(b.clone()).ok())
    }

    pub fn total_bytes(&self) -> usize {
        self.files.values().map(|b| b.len()).sum()
    }

    /// `{ "files": { "config.json": "…", "mod.wasm": {"b64": "…"} } }`, which
    /// is how a folder crosses a JSON tool call. A list of
    /// `{path, text|bytes}` objects is accepted too, because that is the shape
    /// a model reaches for about half the time and refusing it teaches nobody
    /// anything.
    pub fn from_value(v: &Value) -> R<Folder> {
        let files = v.get("files").unwrap_or(v);
        let mut out = Folder::new();
        match files {
            Value::Object(map) => {
                for (path, entry) in map {
                    out.add(path.clone(), file_bytes(path, entry)?);
                }
            }
            Value::Array(list) => {
                for entry in list {
                    let path = entry
                        .get("path")
                        .or_else(|| entry.get("name"))
                        .and_then(|p| p.as_str())
                        .ok_or("each file in the list needs a `path`")?;
                    let body = entry.get("text").or_else(|| entry.get("content"))
                        .or_else(|| entry.get("bytes")).or_else(|| entry.get("b64"))
                        .cloned().unwrap_or(Value::Null);
                    out.add(path.to_string(), file_bytes(path, &body)?);
                }
            }
            _ => return Err("`files` is an object of path → contents, or a list of {path, text}".into()),
        }
        if out.files.is_empty() {
            return Err("that folder is empty — a mod folder is at least a config.json and an anchor".into());
        }
        Ok(out)
    }

    /// The folder as JSON: text where the bytes are text, `{"b64": …}` where
    /// they are not. The inverse of [`Folder::from_value`].
    pub fn to_value(&self) -> Value {
        let mut map = Map::new();
        for (path, bytes) in &self.files {
            // A wasm anchor is always base64: its bytes happen to decode as
            // UTF-8 often enough (`\0asm\x01\0\0\0` does) that trusting
            // that test would send a binary as text some days and not others.
            let text = if path.ends_with(".wasm") { None } else { String::from_utf8(bytes.clone()).ok() };
            match text {
                Some(t) => map.insert(path.clone(), json!(t)),
                None => map.insert(path.clone(), json!({ "b64": blobs::to_base64(bytes) })),
            };
        }
        Value::Object(map)
    }

    /// The listing the id is taken from. Readable on purpose: an id nobody can
    /// recompute by hand is an id nobody can check.
    pub fn manifest(&self) -> String {
        let mut out = format!("{PROTOCOL}\n");
        if let Some(a) = self.anchor_path() {
            out.push_str(&format!("anchor {a}\n"));
        }
        for (path, bytes) in &self.files {
            out.push_str(&format!("{} {} {}\n", blobs::hash(bytes), bytes.len(), path));
        }
        out
    }

    /// The mod's id: the hash of the manifest, so it moves when any file in
    /// the folder moves and never when only the metadata around it does.
    pub fn id(&self) -> String {
        blobs::hash(self.manifest().as_bytes())
    }

    pub fn config(&self) -> Option<Value> {
        self.text(CONFIG).and_then(|t| serde_json::from_str(&t).ok())
    }

    /// The anchor: what the config declares if it declares one and the file is
    /// there, else the one anchor-named file present. Guessing is deliberate —
    /// a folder holding `mod.py` and nothing else is unambiguous, and the
    /// verifier will still say the config should have said so.
    pub fn anchor_path(&self) -> Option<String> {
        if let Some(declared) = self
            .config()
            .as_ref()
            .and_then(|c| c.get("anchor").and_then(|a| a.as_str()).map(String::from))
        {
            if self.files.contains_key(&declared) {
                return Some(declared);
            }
        }
        ANCHORS
            .iter()
            .map(|(_, a)| *a)
            .find(|a| self.files.contains_key(*a))
            .map(String::from)
    }

    pub fn anchor_bytes(&self) -> Option<&Vec<u8>> {
        self.anchor_path().and_then(|p| self.files.get(&p))
    }

    /// Sibling source files the anchor may import. Python only for now: these
    /// are handed to the sandbox as modules, so `import board` inside a mod
    /// folder means that folder's `board.py` and can mean nothing else.
    pub fn siblings(&self, lang: &str) -> Vec<String> {
        let anchor = self.anchor_path().unwrap_or_default();
        let ext = match lang {
            "python" => ".py",
            "rust" => ".rs",
            _ => return Vec::new(),
        };
        self.files
            .keys()
            .filter(|p| **p != anchor && p.ends_with(ext) && !p.contains('/'))
            .map(|p| p.trim_end_matches(ext).to_string())
            .collect()
    }

    // ── verification ─────────────────────────────────────────────────────

    /// Does this folder match the template? Every check the registry can run
    /// without executing anything, each one named, each one with the fix in
    /// its `detail`. `errors` refuse an upload; `warnings` never do.
    ///
    /// The order is the order an author hits them: is it a folder, is there a
    /// config, does the config parse, is there an anchor, does the anchor read
    /// as the language claimed, and — the one that matters — does what the
    /// anchor actually defines match what the config says it is.
    pub fn verify(&self) -> Value {
        let mut checks: Vec<Value> = Vec::new();

        // 1. the files themselves
        let mut path_errs: Vec<String> = Vec::new();
        for p in self.files.keys() {
            if let Err(e) = path_ok(p) {
                path_errs.push(e);
            }
        }
        if self.files.len() > MAX_FILES {
            path_errs.push(format!("{} files — a mod folder holds at most {MAX_FILES}", self.files.len()));
        }
        if self.total_bytes() > MAX_BYTES {
            path_errs.push(format!("{} bytes — at most {MAX_BYTES}", self.total_bytes()));
        }
        checks.push(check(
            "files",
            path_errs.is_empty(),
            "error",
            if path_errs.is_empty() {
                format!("{} file(s), {} bytes", self.files.len(), self.total_bytes())
            } else {
                path_errs.join("; ")
            },
        ));

        // 2. config.json exists and parses
        let raw_config = self.text(CONFIG);
        let config = self.config();
        checks.push(check(
            "config",
            config.as_ref().is_some_and(|c| c.is_object()),
            "error",
            match (&raw_config, &config) {
                (None, _) => "no config.json — every mod folder has one; `template` prints it".into(),
                (Some(_), None) => "config.json is not valid JSON".into(),
                (Some(_), Some(c)) if !c.is_object() => "config.json is not a JSON object".into(),
                _ => "config.json parses".into(),
            },
        ));
        let cfg = config.unwrap_or_else(|| json!({}));
        let s = |k: &str| cfg.get(k).and_then(|v| v.as_str()).unwrap_or("").trim().to_string();

        // 3. protocol
        let proto = s("protocol");
        checks.push(check(
            "protocol",
            proto == PROTOCOL,
            "error",
            if proto.is_empty() {
                format!("config.json declares no protocol — it should say \"protocol\": \"{PROTOCOL}\"")
            } else if proto != PROTOCOL {
                format!("config.json says protocol `{proto}`; this registry speaks {PROTOCOL}")
            } else {
                proto.clone()
            },
        ));

        // 4. name
        let name = s("name");
        checks.push(check(
            "name",
            is_slug(&name),
            "error",
            if name.is_empty() {
                "config.json has no name — it is the folder's name and the leaderboard's".into()
            } else if !is_slug(&name) {
                format!("`{name}` is not a slug — lowercase letters, digits, - and _, starting with a letter")
            } else {
                name.clone()
            },
        ));

        // 5. kind
        let kind = s("kind");
        checks.push(check(
            "kind",
            KINDS.contains(&kind.as_str()),
            "error",
            if kind.is_empty() {
                "config.json declares no kind — `game` or `player`".into()
            } else if !KINDS.contains(&kind.as_str()) {
                format!("kind `{kind}` is not one of {}", KINDS.join(", "))
            } else {
                kind.clone()
            },
        ));

        // 6. lang and anchor agree with each other and with what is here
        let lang = s("lang");
        let declared_anchor = s("anchor");
        let expected = anchor_for(&lang);
        let anchor = self.anchor_path();
        let anchor_ok = expected.is_some()
            && anchor.as_deref() == expected
            && declared_anchor == expected.unwrap_or_default()
            && self.files.contains_key(expected.unwrap_or_default());
        checks.push(check(
            "anchor",
            anchor_ok,
            "error",
            match (expected, &anchor) {
                (None, _) => format!(
                    "config.json says lang `{lang}` — it is one of {}",
                    ANCHORS.map(|(l, a)| format!("{l} (anchor {a})")).join(", ")
                ),
                (Some(want), None) => format!("no anchor in this folder — a {lang} mod is anchored on {want}"),
                (Some(want), Some(found)) if found != want => {
                    format!("config.json says lang `{lang}` but the anchor here is `{found}` — a {lang} mod is anchored on {want}")
                }
                (Some(want), Some(_)) if declared_anchor != want => {
                    format!("config.json should say \"anchor\": \"{want}\"")
                }
                (Some(want), Some(_)) => want.to_string(),
            },
        ));

        // 7. the anchor reads as the language it claims
        let read = match (&anchor, anchor_for(&lang)) {
            (Some(_), Some(_)) => self.anchor_bytes().map(|b| read_as(&lang, b)),
            _ => None,
        };
        let described = match &read {
            Some(Ok(v)) => Some(v.clone()),
            _ => None,
        };
        checks.push(check(
            "readable",
            described.is_some(),
            "error",
            match &read {
                Some(Ok(v)) => format!(
                    "{} — {}",
                    v.get("lang").and_then(|l| l.as_str()).unwrap_or("?"),
                    v.get("class").and_then(|c| c.as_str()).unwrap_or("a module")
                ),
                Some(Err(e)) => e.clone(),
                None => "no anchor to read".into(),
            },
        ));

        // 8. the claim meets the fact
        let role = described
            .as_ref()
            .and_then(|d| d.get("role").and_then(|r| r.as_str()))
            .unwrap_or("")
            .to_string();
        let missing: Vec<String> = described
            .as_ref()
            .and_then(|d| d.get("missing").and_then(|m| m.as_array()).cloned())
            .unwrap_or_default()
            .iter()
            .filter_map(|v| v.as_str().map(String::from))
            .collect();
        checks.push(check(
            "kind_matches_anchor",
            !role.is_empty() && role == kind,
            "error",
            if role.is_empty() {
                "nothing was read out of the anchor, so there is nothing to compare the config to".into()
            } else if role == kind {
                format!("config.json says {kind}, and the anchor defines a {role}")
            } else if missing.is_empty() {
                format!("config.json says `{kind}`, but the anchor defines a `{role}`")
            } else {
                format!(
                    "config.json says `{kind}`, but the anchor defines a `{role}` — it still needs {}",
                    missing.join(", ")
                )
            },
        ));

        // 9. the contract itself, spelled out rather than left to the diff
        checks.push(check(
            "abi",
            missing.is_empty(),
            if kind == "game" || kind == "player" { "error" } else { "warn" },
            if missing.is_empty() {
                match described.as_ref().and_then(|d| d.get("exports").and_then(|e| e.as_array())) {
                    Some(list) => format!(
                        "defines {}",
                        list.iter()
                            .filter_map(|e| e.get("name").and_then(|n| n.as_str()))
                            .collect::<Vec<_>>()
                            .join(" ")
                    ),
                    None => "complete".into(),
                }
            } else {
                format!("missing {}", missing.join(", "))
            },
        ));

        // 10. description — never fatal, always worth saying
        let description = s("description");
        checks.push(check(
            "description",
            description.len() >= 12,
            "warn",
            if description.is_empty() {
                "config.json has no description — it is the card, and an empty card is why nobody plays it".into()
            } else if description.len() < 12 {
                format!("`{description}` is a label, not a description")
            } else {
                description.clone()
            },
        ));

        // 11. seats
        let players = cfg.get("players").and_then(|v| v.as_u64());
        if kind == "game" {
            checks.push(check(
                "players",
                players.is_some_and(|n| (1..=8).contains(&n)),
                "warn",
                match players {
                    Some(n) if (1..=8).contains(&n) => format!("{n} seat(s)"),
                    Some(n) => format!("{n} seats — this arena seats 1 to 8"),
                    None => "config.json declares no seat count; `players` is how a match knows how many to seat".into(),
                },
            ));
        }

        // 12. what the sandbox will refuse, said before the match says it
        if let Some(d) = &described {
            let blocked: Vec<String> = d
                .get("imports")
                .and_then(|i| i.as_array())
                .map(|a| {
                    a.iter()
                        .filter(|i| i.get("allowed").is_some_and(|v| v == false))
                        .filter_map(|i| i.get("module").and_then(|m| m.as_str()).map(String::from))
                        .collect()
                })
                .unwrap_or_default();
            let siblings = self.siblings(&lang);
            let unresolved: Vec<String> =
                blocked.iter().filter(|b| !siblings.contains(&b.split('.').next().unwrap_or(b).to_string()))
                    .cloned().collect();
            if lang == "python" {
                checks.push(check(
                    "imports",
                    unresolved.is_empty(),
                    "error",
                    if unresolved.is_empty() {
                        format!(
                            "stdlib only{}",
                            if siblings.is_empty() {
                                String::new()
                            } else {
                                format!(", plus this folder's own {}", siblings.join(", "))
                            }
                        )
                    } else {
                        format!(
                            "the sandbox has no {} — it allows {} and this folder's own files",
                            unresolved.join(", "),
                            klass::ALLOWED_IMPORTS.join(" ")
                        )
                    },
                ));
            }
            let warnings = d.get("host_needs").and_then(|h| h.as_array()).cloned().unwrap_or_default();
            if lang == "rust" && !warnings.is_empty() {
                checks.push(check(
                    "host",
                    false,
                    "warn",
                    warnings
                        .iter()
                        .filter_map(|w| w.get("note").and_then(|n| n.as_str()))
                        .collect::<Vec<_>>()
                        .join("; "),
                ));
            }
        }

        let errors = checks.iter().filter(|c| c["ok"] == false && c["level"] == "error").count();
        let warnings = checks.iter().filter(|c| c["ok"] == false && c["level"] == "warn").count();
        json!({
            "ok": errors == 0,
            "id": self.id(),
            "name": name,
            "kind": kind,
            "lang": lang,
            "role": role,
            "anchor": anchor,
            "files": self.files.keys().collect::<Vec<_>>(),
            "errors": errors,
            "warnings": warnings,
            "checks": checks,
            "summary": if errors == 0 {
                format!("matches the template{}", if warnings == 0 { String::new() } else { format!(", with {warnings} warning(s)") })
            } else {
                checks.iter().filter(|c| c["ok"] == false && c["level"] == "error")
                    .filter_map(|c| c["detail"].as_str().map(String::from))
                    .collect::<Vec<_>>().join(" · ")
            },
        })
    }

    /// One file, made into a folder — the door every pre-folder upload comes
    /// through. The config is written from what the reader found, so a bare
    /// `.py` dropped on the console is a complete mod a second later and the
    /// author never had to learn the schema to get started.
    pub fn wrap(raw: Vec<u8>, meta: &Value) -> R<Folder> {
        let described = sniff(&raw)?;
        let lang = described["lang"].as_str().unwrap_or("wasm").to_string();
        let anchor = anchor_for(&lang).ok_or_else(|| format!("no anchor name for lang `{lang}`"))?;
        let role = described["role"].as_str().unwrap_or("wasm").to_string();
        let fallback = described
            .get("class")
            .and_then(|c| c.as_str())
            .map(|c| slugify(c))
            .unwrap_or_else(|| format!("mod-{}", &blobs::hash(&raw)[..8]));
        let name = meta
            .get("name")
            .and_then(|v| v.as_str())
            .map(slugify)
            .filter(|s| !s.is_empty())
            .unwrap_or(fallback);
        let description = meta
            .get("description")
            .and_then(|v| v.as_str())
            .map(String::from)
            .filter(|d| !d.trim().is_empty())
            .or_else(|| described.get("doc").and_then(|d| d.as_str()).map(String::from))
            .unwrap_or_default();
        let players = described
            .get("attributes")
            .and_then(|a| a.as_array())
            .and_then(|a| {
                a.iter()
                    .find(|x| x.get("name").and_then(|n| n.as_str()) == Some("players"))
                    .and_then(|x| x.get("value").and_then(|v| v.as_str()))
                    .and_then(|v| v.trim().parse::<u64>().ok())
            })
            .unwrap_or(if role == "game" { 2 } else { 0 });

        let mut config = json!({
            "name": name,
            "kind": role,
            "lang": lang,
            "anchor": anchor,
            "description": description,
            "protocol": PROTOCOL,
        });
        if role == "game" {
            config["players"] = json!(players.max(1));
        }
        for key in ["author", "tags", "version"] {
            if let Some(v) = meta.get(key) {
                config[key] = v.clone();
            }
        }
        let mut folder = Folder::new();
        folder.add(CONFIG, format!("{}\n", serde_json::to_string_pretty(&config).unwrap_or_default()));
        folder.add(anchor, raw);
        Ok(folder)
    }
}

fn check(name: &str, ok: bool, level: &str, detail: String) -> Value {
    json!({ "check": name, "ok": ok, "level": level, "detail": detail })
}

fn file_bytes(path: &str, entry: &Value) -> R<Vec<u8>> {
    match entry {
        Value::String(text) => {
            // A .wasm sent as a string is base64 — there is no other way to
            // put a binary in a JSON string, and a model that tries anyway
            // should be told which one this is.
            if path.ends_with(".wasm") {
                blobs::decode(text)
            } else {
                Ok(text.as_bytes().to_vec())
            }
        }
        Value::Object(o) => {
            if let Some(t) = o.get("text").or_else(|| o.get("content")).and_then(|v| v.as_str()) {
                return Ok(t.as_bytes().to_vec());
            }
            if let Some(b) = o.get("b64").or_else(|| o.get("bytes")).or_else(|| o.get("base64")).and_then(|v| v.as_str()) {
                return blobs::decode(b);
            }
            // config.json handed over as an object rather than as text is the
            // most natural mistake there is. Serialise it and move on.
            serde_json::to_string_pretty(entry)
                .map(|s| s.into_bytes())
                .map_err(|e| format!("{path}: {e}"))
        }
        Value::Null => Err(format!("{path}: no contents")),
        other => serde_json::to_string_pretty(other)
            .map(|s| s.into_bytes())
            .map_err(|e| format!("{path}: {e}")),
    }
}

pub fn slugify(s: &str) -> String {
    let mut out = String::new();
    for c in s.trim().chars() {
        if c.is_ascii_alphanumeric() {
            out.push(c.to_ascii_lowercase());
        } else if !out.ends_with('-') {
            out.push('-');
        }
    }
    let out = out.trim_matches('-').to_string();
    if out.chars().next().is_some_and(|c| c.is_ascii_lowercase()) {
        out
    } else {
        format!("m-{out}")
    }
}

// ── the template ─────────────────────────────────────────────────────────

/// The folder a new mod starts as. One per (kind, language), and the thing
/// `verify` is verifying against — a template that does not pass its own
/// verifier would be a lie, and the tests hold it to that.
pub fn template(kind: &str, lang: &str) -> R<Folder> {
    let kind = match kind.trim().to_lowercase().as_str() {
        "" | "game" => "game",
        "player" | "agent" | "bot" => "player",
        other => return Err(format!("template kind is `game` or `player`, not `{other}`")),
    };
    let lang = match lang.trim().to_lowercase().as_str() {
        "" | "python" | "py" | "class" => "python",
        "rust" | "rs" => "rust",
        "wasm" => "wasm",
        other => return Err(format!("template lang is python, rust or wasm, not `{other}`")),
    };
    let anchor = anchor_for(lang).unwrap_or("mod.py");
    let name = if kind == "game" { "mygame" } else { "mybot" };

    let source = match (lang, kind) {
        ("python", "game") => klass::GAME_TEMPLATE.to_string(),
        ("python", _) => klass::PLAYER_TEMPLATE.to_string(),
        ("rust", "game") => rsklass::GAME_TEMPLATE.to_string(),
        ("rust", _) => rsklass::PLAYER_TEMPLATE.to_string(),
        _ => String::new(),
    };

    let mut config = json!({
        "name": name,
        "kind": kind,
        "lang": lang,
        "anchor": anchor,
        "description": if kind == "game" {
            "One line about the game — what a seat sees and how it is won."
        } else {
            "One line about the agent — how it reads a view and how it picks."
        },
        "author": "",
        "tags": [],
        "protocol": PROTOCOL,
    });
    if kind == "game" {
        config["players"] = json!(2);
    }

    let mut folder = Folder::new();
    folder.add(CONFIG, format!("{}\n", serde_json::to_string_pretty(&config).unwrap_or_default()));
    folder.add("README.md", readme(name, kind, lang, anchor));
    if lang == "wasm" {
        // There is no source template for a binary: the folder is the shape,
        // and the anchor is whatever the author compiled. Saying that plainly
        // beats shipping an empty mod.wasm nobody can run.
        return Ok(folder);
    }
    folder.add(anchor, source);
    Ok(folder)
}

fn readme(name: &str, kind: &str, lang: &str, anchor: &str) -> String {
    format!(
        "# {name}\n\n\
         A modarena {kind}, written in {lang}. The folder is the mod:\n\n\
         - `config.json` — what this claims to be. `kind`, `lang` and `anchor` are checked\n\
           against the anchor itself on upload, so the claim can never drift from the code.\n\
         - `{anchor}` — the anchor. This is what runs.\n\
         - anything else — carried along. Sibling `.py` files are importable by the anchor\n\
           and by nothing else.\n\n\
         Verify before you upload:\n\n\
         ```\n\
         m modarena/verify path=./{name}\n\
         m modarena/upload path=./{name}\n\
         ```\n"
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tpl(kind: &str, lang: &str) -> Folder {
        template(kind, lang).expect("template")
    }

    #[test]
    fn every_template_passes_its_own_verifier() {
        for kind in ["game", "player"] {
            for lang in ["python", "rust"] {
                let report = tpl(kind, lang).verify();
                assert_eq!(report["ok"], true, "{kind}/{lang}: {}", report["summary"]);
                assert_eq!(report["role"], kind);
            }
        }
    }

    #[test]
    fn the_id_is_the_whole_folder() {
        let a = tpl("game", "python");
        let mut b = a.clone();
        assert_eq!(a.id(), b.id());
        b.add("README.md", "different");
        assert_ne!(a.id(), b.id(), "changing any file changes the id");
    }

    #[test]
    fn a_config_that_lies_about_its_anchor_is_caught() {
        let mut f = tpl("game", "python");
        let mut cfg = f.config().unwrap();
        cfg["kind"] = json!("player");
        f.add(CONFIG, serde_json::to_string(&cfg).unwrap());
        let report = f.verify();
        assert_eq!(report["ok"], false);
        let failed: Vec<&str> = report["checks"].as_array().unwrap().iter()
            .filter(|c| c["ok"] == false)
            .filter_map(|c| c["check"].as_str())
            .collect();
        assert!(failed.contains(&"kind_matches_anchor"), "{failed:?}");
    }

    #[test]
    fn python_in_a_rust_anchor_does_not_pass_as_rust() {
        let mut f = Folder::new();
        f.add(CONFIG, r#"{"name":"x","kind":"game","lang":"rust","anchor":"mod.rs","description":"a game that is not rust","players":2,"protocol":"modarena/1.0"}"#);
        f.add("mod.rs", klass::GAME_TEMPLATE);
        let report = f.verify();
        assert_eq!(report["ok"], false);
    }

    #[test]
    fn a_missing_config_is_the_first_thing_you_hear_about() {
        let mut f = Folder::new();
        f.add("mod.py", klass::GAME_TEMPLATE);
        let report = f.verify();
        assert_eq!(report["ok"], false);
        assert!(report["summary"].as_str().unwrap().contains("config.json"));
    }

    #[test]
    fn wrapping_one_file_produces_a_folder_that_verifies() {
        let f = Folder::wrap(klass::GAME_TEMPLATE.as_bytes().to_vec(), &json!({ "name": "wrapped" })).unwrap();
        assert_eq!(f.anchor_path().as_deref(), Some("mod.py"));
        let report = f.verify();
        assert_eq!(report["ok"], true, "{}", report["summary"]);
        assert_eq!(report["name"], "wrapped");
    }

    #[test]
    fn paths_that_walk_out_are_refused() {
        let mut f = tpl("game", "python");
        f.add("../escape.py", "x = 1");
        assert_eq!(f.verify()["ok"], false);
    }

    #[test]
    fn a_folder_round_trips_through_json() {
        let f = tpl("game", "python");
        let back = Folder::from_value(&json!({ "files": f.to_value() })).unwrap();
        assert_eq!(f.id(), back.id());
    }

    #[test]
    fn a_binary_file_survives_the_round_trip() {
        let mut f = Folder::new();
        f.add("mod.wasm", vec![0, 0x61, 0x73, 0x6d, 1, 0, 0, 0]);
        let back = Folder::from_value(&json!({ "files": f.to_value() })).unwrap();
        assert_eq!(f.get("mod.wasm"), back.get("mod.wasm"));
    }

    #[test]
    fn siblings_are_the_folders_own_python_files() {
        let mut f = tpl("player", "python");
        f.add("board.py", "VALUE = 1\n");
        assert_eq!(f.siblings("python"), vec!["board".to_string()]);
    }
}
