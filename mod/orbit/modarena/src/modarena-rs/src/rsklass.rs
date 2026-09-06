//! The Rust class reader — `klass.rs` for the other source language.
//!
//! Same promise as everywhere else in this registry: what a file *is* comes
//! out of the file. `wasm.rs` reads an export section, `klass.rs` reads
//! Python `def`s, and this reads the `fn`s in a `struct`'s `impl` block.
//!
//! A struct whose impl defines `view` `step` `done` `result` is a game.
//! One whose impl defines `play` is a player.
//! Anything else is stored as `class`, readable and listable like any module.
//!
//! This is a scanner, not a parser. It tracks brace depth with comments,
//! strings and lifetimes taken out of the way, and it counts only the items
//! directly inside an `impl` block — a `fn` nested inside a method is not a
//! method. It does not type-check anything and does not have to: the file is
//! about to be handed to `rustc`, which is a much better judge than this.
//! What the scanner is for is answering "what is this, and can it play?"
//! before anyone waits on a compiler.
//!
//! What happens after the read is where Rust and Python part company. A
//! Python class is interpreted in a sandbox that CPython can be argued out
//! of. A Rust class is compiled to `wasm32-unknown-unknown` (see `rustc.rs`)
//! and runs in the engine sandbox — no filesystem, no network, no clock,
//! because the target has no way to name any of them.

use serde_json::{json, Value};

type R<T> = Result<T, String>;

pub const GAME_METHODS: [&str; 4] = ["view", "step", "done", "result"];
pub const PLAYER_METHODS: [&str; 1] = ["play"];

/// `std` paths that compile for this target and then do nothing useful at run
/// time — there is no filesystem, no socket and no wall clock behind them.
/// Flagged on the card so an author finds out before a match does.
pub const DEAD_PATHS: [&str; 7] =
    ["std::fs", "std::net", "std::process", "std::time", "std::thread", "std::env", "std::io"];

/// The consts the shim reads to fill in `game_info`. Everything is optional;
/// a game that declares none is still a game.
pub const META_CONSTS: [&str; 6] =
    ["NAME", "DESCRIPTION", "PLAYERS", "MIN_PLAYERS", "MAX_PLAYERS", "MAX_TURNS"];

pub const GAME_TEMPLATE: &str = r#"//! A game is a struct. The four methods in its impl block are the contract.
//!
//! `Moves`, `Step`, `Outcome` and `arena::…` come from the prelude every Rust
//! class is compiled against — nothing to import, and no crates but this file.

/// One line about the game — it shows up on the card.
pub struct MyGame {
    score: [i64; 2],
    turn_no: usize,
}

impl MyGame {
    pub const NAME: &'static str = "mygame";
    pub const PLAYERS: usize = 2;
    pub const MAX_TURNS: usize = 50;

    /// The opening position. `seed` is the match seed.
    pub fn new(seed: i64) -> MyGame {
        let _ = seed;
        MyGame { score: [0, 0], turn_no: 0 }
    }

    /// What this seat can see — the only thing its player gets.
    ///
    /// Show a seat only what it is entitled to and hidden information works.
    /// Say `Legal moves: ...` somewhere: a model has nothing else to go on.
    pub fn view(&self, seat: usize) -> String {
        format!(
            "You are seat {seat}. Score {}-{}.\nLegal moves: rock, paper, scissors",
            self.score[0], self.score[1]
        )
    }

    /// Apply one round. `moves.get(seat)` is what that seat played.
    ///
    /// Return `Step::ok()` when everything stood, or mark a seat false — what
    /// you refuse is counted against that player for good.
    pub fn step(&mut self, moves: &Moves) -> Step {
        self.turn_no += 1;
        let beats = |a: &str| match a {
            "rock" => "scissors",
            "paper" => "rock",
            "scissors" => "paper",
            _ => "",
        };
        let played = [moves.lower(0), moves.lower(1)];
        let legal = [!beats(&played[0]).is_empty(), !beats(&played[1]).is_empty()];
        if legal[0] && legal[1] && played[0] != played[1] {
            let winner = if beats(&played[0]) == played[1] { 0 } else { 1 };
            self.score[winner] += 1;
        }
        Step::legal(&legal).note(format!("{} vs {}", played[0], played[1]))
    }

    /// Optional: who moves now. Both seats at once makes it simultaneous.
    /// Leave it out and seats alternate.
    pub fn turn(&self) -> Vec<usize> {
        vec![0, 1]
    }

    pub fn done(&self) -> bool {
        self.turn_no >= Self::MAX_TURNS || self.score[0].max(self.score[1]) > 5
    }

    /// Higher is better. The ratings come out of the order.
    pub fn result(&self) -> Outcome {
        Outcome::points(&self.score)
            .summary(format!("{}-{}", self.score[0], self.score[1]))
    }
}
"#;

pub const PLAYER_TEMPLATE: &str = r#"//! A player is a struct with one method.

/// One line about how it plays.
pub struct MyBot;

impl MyBot {
    pub const NAME: &'static str = "mybot";

    /// Return the move as text.
    ///
    /// `view` is exactly what the game showed this seat — the same text a
    /// model in this seat would be given, which is what makes the two
    /// comparable. `arena::log` writes into the transcript, and
    /// `arena::random` is seeded from the match seed so this replays.
    pub fn play(&mut self, view: &str, seat: usize) -> String {
        let _ = seat;
        for line in view.lines() {
            if let Some(rest) = line.strip_prefix("Legal moves:") {
                let options: Vec<&str> =
                    rest.split(',').map(|m| m.trim()).filter(|m| !m.is_empty()).collect();
                return arena::choice(&options).unwrap_or("").to_string();
            }
        }
        String::new()
    }
}
"#;

// ── the scan ─────────────────────────────────────────────────────────────

#[derive(Debug, Default, Clone)]
pub struct Method {
    pub name: String,
    pub args: String,
    pub ret: String,
    pub line: usize,
    pub doc: String,
    /// `&mut self`, `&self`, or empty for an associated function.
    pub receiver: String,
}

impl Method {
    pub fn arity(&self) -> usize {
        self.args
            .split(',')
            .map(str::trim)
            .filter(|a| !a.is_empty() && !a.ends_with("self"))
            .count()
    }
}

#[derive(Debug, Default, Clone)]
pub struct Class {
    pub name: String,
    pub line: usize,
    pub doc: String,
    pub kind: String,
    pub methods: Vec<Method>,
    /// `(name, value)` for the consts in the impl block.
    pub consts: Vec<(String, String)>,
    /// Traits implemented for this type, by name.
    pub traits: Vec<String>,
}

impl Class {
    pub fn has(&self, name: &str) -> bool {
        self.methods.iter().any(|m| m.name == name)
    }

    pub fn method(&self, name: &str) -> Option<&Method> {
        self.methods.iter().find(|m| m.name == name)
    }

    pub fn konst(&self, name: &str) -> Option<&str> {
        self.consts.iter().find(|(k, _)| k == name).map(|(_, v)| v.as_str())
    }

    pub fn role(&self) -> &'static str {
        if GAME_METHODS.iter().all(|m| self.has(m)) {
            "game"
        } else if PLAYER_METHODS.iter().all(|m| self.has(m)) {
            "player"
        } else {
            "class"
        }
    }

    fn missing(&self) -> Vec<String> {
        let game: Vec<_> = GAME_METHODS.iter().filter(|m| !self.has(m)).collect();
        let player: Vec<_> = PLAYER_METHODS.iter().filter(|m| !self.has(m)).collect();
        if player.len() <= game.len() {
            player.into_iter().map(|m| m.to_string()).collect()
        } else {
            game.into_iter().map(|m| m.to_string()).collect()
        }
    }
}

/// Does this look like Rust at all? Deliberately narrow — it has to be
/// checked *before* `looks_like_python`, and `fn`/`impl`/`struct` with a brace
/// is the shape nothing else here has.
/// The balanced argument list of a `fn` head, and whatever follows it. Written
/// out rather than reached for with `split` because a one-line method carries
/// its whole body on the same line, and both of the obvious splits take a
/// paren out of the body.
fn split_args(head: &str) -> (String, String) {
    let Some(open) = head.find('(') else { return (String::new(), head.to_string()) };
    let mut depth = 0i32;
    for (i, c) in head.char_indices().skip(open) {
        match c {
            '(' => depth += 1,
            ')' => {
                depth -= 1;
                if depth == 0 {
                    return (head[open + 1..i].trim().to_string(), head[i + 1..].to_string());
                }
            }
            _ => {}
        }
    }
    (head[open + 1..].trim().to_string(), String::new())
}

pub fn looks_like_rust(bytes: &[u8]) -> bool {
    if bytes.starts_with(b"\0asm") {
        return false;
    }
    let Ok(text) = std::str::from_utf8(bytes) else {
        return false;
    };
    let mut has_item = false;
    let mut has_brace = false;
    for line in text.lines() {
        let t = line.trim_start().trim_start_matches("pub ").trim_start();
        if t.starts_with("fn ")
            || t.starts_with("impl ")
            || t.starts_with("struct ")
            || t.starts_with("enum ")
            || t.starts_with("trait ")
        {
            has_item = true;
        }
        if line.contains('{') {
            has_brace = true;
        }
    }
    has_item && has_brace
}

/// One source line with the comments and literals taken out, so brace
/// counting and keyword matching cannot be fooled by either.
struct Scrubbed {
    /// The line with string/char contents blanked and comments removed.
    code: String,
    /// The doc comment attached to the item on this line, if any.
    doc: String,
    /// The line in the file this came from — kept explicitly because one
    /// physical line can become several rows.
    line: usize,
}

/// Blank out comments and literal contents across the whole file at once —
/// block comments and raw strings both span lines, so this cannot be done a
/// line at a time.
fn scrub(text: &str) -> Vec<Scrubbed> {
    let b = text.as_bytes();
    let mut out: Vec<Scrubbed> = Vec::new();
    let mut line = String::new();
    let mut pending_doc: Vec<String> = Vec::new();
    let mut i = 0usize;
    let mut block_depth = 0usize;

    // Every line the scan produces keeps its own index, so the docs collected
    // above an item can be attached to it when the item is recognised later.
    let mut docs_for_line: Vec<Vec<String>> = Vec::new();

    while i < b.len() {
        let c = b[i];

        if c == b'\n' {
            out.push(Scrubbed { code: std::mem::take(&mut line), doc: String::new(), line: out.len() + 1 });
            docs_for_line.push(std::mem::take(&mut pending_doc));
            i += 1;
            continue;
        }

        if block_depth > 0 {
            if c == b'*' && b.get(i + 1) == Some(&b'/') {
                block_depth -= 1;
                i += 2;
            } else if c == b'/' && b.get(i + 1) == Some(&b'*') {
                block_depth += 1;
                i += 2;
            } else {
                i += 1;
            }
            continue;
        }

        // Comments.
        if c == b'/' && b.get(i + 1) == Some(&b'/') {
            let end = text[i..].find('\n').map(|n| i + n).unwrap_or(b.len());
            let raw = &text[i..end];
            let stripped = raw
                .trim_start_matches('/')
                .trim_start_matches('!')
                .trim_start_matches('/')
                .trim();
            if (raw.starts_with("///") || raw.starts_with("//!")) && !stripped.is_empty() {
                pending_doc.push(stripped.to_string());
            }
            i = end;
            continue;
        }
        if c == b'/' && b.get(i + 1) == Some(&b'*') {
            block_depth = 1;
            i += 2;
            continue;
        }

        // Raw strings: r"…", r#"…"#, r##"…"##
        if c == b'r' && matches!(b.get(i + 1), Some(&b'"') | Some(&b'#')) {
            let mut j = i + 1;
            let mut hashes = 0usize;
            while b.get(j) == Some(&b'#') {
                hashes += 1;
                j += 1;
            }
            if b.get(j) == Some(&b'"') {
                let close = format!("\"{}", "#".repeat(hashes));
                let end = text[j + 1..].find(&close).map(|n| j + 1 + n + close.len());
                line.push_str("\"\"");
                i = end.unwrap_or(b.len());
                continue;
            }
        }

        // Ordinary strings.
        if c == b'"' {
            let mut j = i + 1;
            while j < b.len() {
                if b[j] == b'\\' {
                    j += 2;
                    continue;
                }
                if b[j] == b'"' {
                    j += 1;
                    break;
                }
                if b[j] == b'\n' {
                    // A newline inside a string still ends the output line.
                    break;
                }
                j += 1;
            }
            line.push_str("\"\"");
            i = j;
            continue;
        }

        // Char literals — but `'static` and `'a` are lifetimes, not literals.
        if c == b'\'' {
            let rest = &text[i..];
            let is_lifetime = rest
                .as_bytes()
                .get(1)
                .map(|n| n.is_ascii_alphabetic() || *n == b'_')
                .unwrap_or(false)
                && rest.as_bytes().get(2) != Some(&b'\'');
            if !is_lifetime {
                let mut j = i + 1;
                while j < b.len() && b[j] != b'\n' {
                    if b[j] == b'\\' {
                        j += 2;
                        continue;
                    }
                    if b[j] == b'\'' {
                        j += 1;
                        break;
                    }
                    j += 1;
                }
                line.push_str("''");
                i = j;
                continue;
            }
        }

        line.push(c as char);
        i += 1;
    }
    out.push(Scrubbed { code: line, doc: String::new(), line: out.len() + 1 });
    docs_for_line.push(pending_doc);

    // Fold the doc comments collected above each line onto that line.
    for (i, docs) in docs_for_line.into_iter().enumerate() {
        if let Some(row) = out.get_mut(i) {
            row.doc = docs.join(" ");
        }
    }
    // A whole impl block written on one line is still a struct with methods in
    // it. Break every row at its braces so the scanner below — which reads one
    // item per row — sees them; the row keeps the line number it came from, so
    // nothing downstream learns that this happened.
    let mut split: Vec<Scrubbed> = Vec::with_capacity(out.len());
    for row in out {
        let mut rest = row.code.as_str();
        let mut first = true;
        while let Some(at) = rest.find(['{', '}']) {
            let (head, tail) = rest.split_at(at + 1);
            if tail.trim().is_empty() {
                break;
            }
            split.push(Scrubbed {
                code: head.to_string(),
                doc: if first { row.doc.clone() } else { String::new() },
                line: row.line,
            });
            first = false;
            rest = tail;
        }
        split.push(Scrubbed {
            code: rest.to_string(),
            doc: if first { row.doc.clone() } else { String::new() },
            line: row.line,
        });
    }
    let mut out = split;

    // The doc block sits on the lines *above* the item, so carry it down onto
    // the first line of code that follows it.
    let mut carried = String::new();
    for row in out.iter_mut() {
        if row.code.trim().is_empty() {
            if !row.doc.is_empty() {
                carried = if carried.is_empty() {
                    row.doc.clone()
                } else {
                    format!("{carried} {}", row.doc)
                };
            }
            continue;
        }
        if !carried.is_empty() {
            row.doc = carried.clone();
            carried.clear();
        }
    }
    out
}

fn name_after(code: &str, keyword: &str) -> Option<String> {
    let at = find_word(code, keyword)?;
    let rest = code[at + keyword.len()..].trim_start();
    let name: String = rest
        .chars()
        .take_while(|c| c.is_alphanumeric() || *c == '_')
        .collect();
    if name.is_empty() {
        None
    } else {
        Some(name)
    }
}

/// `find`, but only where the needle is a whole word.
fn find_word(haystack: &str, needle: &str) -> Option<usize> {
    let b = haystack.as_bytes();
    let mut from = 0usize;
    while let Some(rel) = haystack[from..].find(needle) {
        let at = from + rel;
        let before_ok = at == 0 || !(b[at - 1].is_ascii_alphanumeric() || b[at - 1] == b'_');
        let after = at + needle.len();
        let after_ok =
            after >= b.len() || !(b[after].is_ascii_alphanumeric() || b[after] == b'_');
        if before_ok && after_ok {
            return Some(at);
        }
        from = at + needle.len();
    }
    None
}

fn depth_delta(code: &str) -> i32 {
    code.chars().fold(0, |d, c| match c {
        '{' => d + 1,
        '}' => d - 1,
        _ => d,
    })
}

/// Everything the file brings in with `use`, deduplicated.
fn uses_in(rows: &[Scrubbed]) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for row in rows {
        let t = row.code.trim().trim_start_matches("pub ").trim_start();
        let Some(rest) = t.strip_prefix("use ") else { continue };
        let path = rest.trim().trim_end_matches(';').trim().to_string();
        if !path.is_empty() && !out.contains(&path) {
            out.push(path);
        }
    }
    out
}

struct Scan {
    classes: Vec<Class>,
    uses: Vec<String>,
    unsafe_blocks: usize,
    free_fns: Vec<String>,
}

fn scan(text: &str) -> Scan {
    let rows = scrub(text);
    let mut classes: Vec<Class> = Vec::new();
    let mut unsafe_blocks = 0usize;
    let mut free_fns: Vec<String> = Vec::new();

    // Brace depth as of the *start* of each line, and which impl block (if
    // any) we are directly inside.
    let mut depth = 0i32;
    // (index into classes, the depth of the impl body)
    let mut in_impl: Option<(usize, i32)> = None;

    for (i, row) in rows.iter().enumerate() {
        let code = row.code.trim();
        let opening_depth = depth;
        depth += depth_delta(&row.code);

        if code.is_empty() {
            continue;
        }
        if find_word(code, "unsafe").is_some() {
            unsafe_blocks += 1;
        }

        // Left the impl block we were in.
        if let Some((_, body)) = in_impl {
            if depth < body {
                in_impl = None;
            }
        }

        let bare = code.trim_start_matches("pub ").trim_start();
        let bare = bare
            .strip_prefix("pub(crate) ")
            .unwrap_or(bare)
            .trim_start();

        // `struct X`, `enum X` — the type a class is.
        if opening_depth == 0 {
            for keyword in ["struct", "enum"] {
                if bare.starts_with(&format!("{keyword} ")) {
                    if let Some(name) = name_after(bare, keyword) {
                        if !classes.iter().any(|c| c.name == name) {
                            classes.push(Class {
                                name,
                                line: row.line,
                                doc: row.doc.clone(),
                                kind: keyword.to_string(),
                                ..Default::default()
                            });
                        }
                    }
                }
            }

            // `impl X {` or `impl Trait for X {`
            if bare.starts_with("impl") && find_word(bare, "impl") == Some(0) {
                let head = bare.splitn(2, '{').next().unwrap_or("").trim().to_string();
                let after = head["impl".len()..].trim();
                // Drop any generic parameter list right after `impl`.
                let after = after.strip_prefix('<').map_or(after, |rest| {
                    rest.split_once('>').map(|(_, r)| r.trim()).unwrap_or(rest)
                });
                let (trait_name, type_name) = match find_word(after, "for") {
                    Some(at) => (
                        Some(after[..at].trim().to_string()),
                        after[at + 3..].trim().to_string(),
                    ),
                    None => (None, after.trim().to_string()),
                };
                let type_name = type_name
                    .split(|c: char| c == '<' || c == ' ' || c == '{')
                    .next()
                    .unwrap_or("")
                    .trim()
                    .to_string();
                if !type_name.is_empty() {
                    let idx = match classes.iter().position(|c| c.name == type_name) {
                        Some(idx) => idx,
                        None => {
                            classes.push(Class {
                                name: type_name.clone(),
                                line: row.line,
                                doc: row.doc.clone(),
                                kind: "struct".into(),
                                ..Default::default()
                            });
                            classes.len() - 1
                        }
                    };
                    match trait_name {
                        Some(t) => {
                            let t = t.trim().to_string();
                            if !t.is_empty() && !classes[idx].traits.contains(&t) {
                                classes[idx].traits.push(t);
                            }
                            // A trait impl's methods are not the type's own
                            // surface; the ABI is inherent methods only.
                        }
                        None => in_impl = Some((idx, opening_depth + 1)),
                    }
                }
                continue;
            }
        }

        let Some((idx, body)) = in_impl else {
            if opening_depth == 0 && bare.starts_with("fn ") {
                if let Some(name) = name_after(bare, "fn") {
                    free_fns.push(name);
                }
            }
            continue;
        };
        // Only items directly inside the impl block count. A `fn` written
        // inside a method body is a helper, not a method.
        if opening_depth != body {
            continue;
        }

        if bare.starts_with("const ") {
            if let Some(name) = name_after(bare, "const") {
                let value = bare
                    .split_once('=')
                    .map(|(_, v)| v.trim().trim_end_matches(';').trim().to_string())
                    .unwrap_or_default();
                classes[idx].consts.push((name, value));
            }
            continue;
        }

        // `fn`, with `async`/`unsafe`/`const`/`extern` in front of it.
        let mut head = bare;
        for prefix in ["default ", "const ", "async ", "unsafe ", "extern \"C\" ", "extern "] {
            head = head.strip_prefix(prefix).unwrap_or(head);
        }
        if !head.starts_with("fn ") {
            continue;
        }
        let Some(name) = name_after(head, "fn") else { continue };
        // The argument list ends at the paren that closes the one it opened
        // with — not at the last paren on the line, which on a one-line method
        // belongs to the body and would swallow the return type with it.
        let (args, tail) = split_args(head);
        let ret = tail
            .split_once("->")
            .map(|(_, r)| {
                r.split(&['{', ';'][..]).next().unwrap_or(r)
                    .split(" where").next().unwrap_or(r)
                    .trim().to_string()
            })
            .unwrap_or_default();
        let receiver = args
            .split(',')
            .next()
            .map(str::trim)
            .filter(|a| a.ends_with("self"))
            .unwrap_or("")
            .to_string();
        classes[idx].methods.push(Method {
            name,
            args,
            ret,
            line: row.line,
            doc: row.doc.clone(),
            receiver,
        });
    }

    Scan { classes, uses: uses_in(&rows), unsafe_blocks, free_fns }
}

/// The struct that will be built. Highest role wins; ties go to the last one
/// defined, which is what a person reading top to bottom would also pick.
fn pick(classes: &[Class]) -> Option<usize> {
    let rank = |c: &Class| match c.role() {
        "game" => 3,
        "player" => 2,
        _ => 1,
    };
    let mut best: Option<usize> = None;
    for (i, c) in classes.iter().enumerate() {
        if best.is_none_or(|b| rank(c) >= rank(&classes[b])) {
            best = Some(i);
        }
    }
    best
}

/// The chosen class, for the compiler. `describe` is what the registry keeps;
/// this is what `rustc.rs` needs to write a shim.
pub fn chosen(bytes: &[u8]) -> R<Class> {
    let text = std::str::from_utf8(bytes).map_err(|_| "the source is not UTF-8".to_string())?;
    let scan = scan(text);
    let at = pick(&scan.classes).ok_or_else(|| no_struct().to_string())?;
    Ok(scan.classes[at].clone())
}

fn no_struct() -> &'static str {
    "no `struct` with an `impl` block in this file — the unit here is a class: a struct whose \
     impl defines view/step/done/result is a game, one that defines play is a player. \
     `m modarena/template role=game lang=rust` prints a starting point."
}

/// Describe Rust source the way `wasm::describe` describes a binary and
/// `klass::describe` describes Python — same keys, same meanings, so one
/// registry and one console table serve all three.
pub fn describe(bytes: &[u8]) -> R<Value> {
    let text = std::str::from_utf8(bytes)
        .map_err(|_| "not a wasm module and not UTF-8 text — nothing here can be read".to_string())?;
    if text.len() > 1 << 20 {
        return Err("class source is larger than 1 MiB".into());
    }
    let scan = scan(text);
    if scan.classes.is_empty() {
        return Err(no_struct().into());
    }
    let at = pick(&scan.classes).unwrap_or(0);
    let main = &scan.classes[at];

    // A `use` of anything that is not std/core is a crate this compiler will
    // not have — one file, `rustc` direct, no Cargo and no registry.
    let mut warnings: Vec<Value> = Vec::new();
    let external: Vec<&String> = scan
        .uses
        .iter()
        .filter(|u| {
            let top = u.split("::").next().unwrap_or("").trim().trim_start_matches("crate");
            !top.is_empty()
                && !matches!(top, "std" | "core" | "alloc" | "self" | "super" | "crate" | "")
        })
        .collect();
    if !external.is_empty() {
        warnings.push(json!({
            "namespace": "rust:crates",
            "uses": external.len(),
            "paths": external,
            "note": "a Rust class is one file compiled by rustc with no Cargo and no registry, \
                     so only std, core and alloc resolve — this will not compile"
        }));
    }
    let dead: Vec<&str> = DEAD_PATHS
        .iter()
        .copied()
        .filter(|p| scan.uses.iter().any(|u| u.starts_with(p)) || text.contains(p))
        .collect();
    if !dead.is_empty() {
        warnings.push(json!({
            "namespace": "rust:no-such-host",
            "paths": dead,
            "note": "wasm32-unknown-unknown has no filesystem, no socket and no wall clock — \
                     these compile and then fail or return nothing at run time. \
                     `arena::random`, `arena::elapsed_ms` and `arena::mcp` are the ways through."
        }));
    }

    Ok(json!({
        "role": main.role(),
        "lang": "rust",
        "class": main.name,
        "doc": main.doc,
        "size": bytes.len(),
        "lines": text.lines().count(),
        "runs_in": "a wasm engine — the source is compiled to wasm32-unknown-unknown on upload, \
                    then plays in the browser or the node runner like any other module here",
        "exports": main.methods.iter().map(|m| json!({
            "name": m.name,
            "kind": "method",
            "signature": format!("({}){}", m.args,
                                 if m.ret.is_empty() { String::new() } else { format!(" -> {}", m.ret) }),
            "line": m.line,
            "doc": m.doc,
        })).collect::<Vec<_>>(),
        "imports": scan.uses.iter().map(|u| json!({
            "module": u,
            "name": "",
            "kind": "use",
            "allowed": ({
                let top = u.split("::").next().unwrap_or("");
                matches!(top, "std" | "core" | "alloc" | "self" | "super" | "crate")
                    && !DEAD_PATHS.iter().any(|d| u.starts_with(d))
            }),
        })).collect::<Vec<_>>(),
        "host_needs": warnings,
        "attributes": main.consts.iter().map(|(k, v)| json!({ "name": k, "value": v }))
            .collect::<Vec<_>>(),
        "missing": main.missing(),
        "unsafe": scan.unsafe_blocks,
        "free_fns": scan.free_fns,
        "classes": scan.classes.iter().map(|c| json!({
            "name": c.name,
            "bases": c.traits.join(", "),
            "line": c.line,
            "doc": c.doc,
            "role": c.role(),
            "methods": c.methods.iter().map(|m| m.name.clone()).collect::<Vec<_>>(),
            "chosen": c.name == main.name,
        })).collect::<Vec<_>>(),
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    const GAME: &str = r#"
use std::collections::HashMap;

/// A helper nobody plays.
pub struct Tally { n: i64 }

impl Tally {
    pub fn bump(&mut self) { self.n += 1; }
}

/// Take one to three; taking the last one loses.
pub struct Nim {
    left: i64,
}

impl Nim {
    pub const NAME: &'static str = "nim";
    pub const PLAYERS: usize = 2;

    pub fn new(seed: i64) -> Nim { Nim { left: 12 + seed % 5 } }

    /// What this seat sees.
    pub fn view(&self, seat: usize) -> String { format!("{} left", self.left) }

    pub fn step(&mut self, moves: &Moves) -> Step {
        fn helper(x: i64) -> i64 { x + 1 }
        let taken = helper(moves.number(0).unwrap_or(0));
        self.left -= taken;
        Step::ok()
    }

    pub fn done(&self) -> bool { self.left <= 0 }

    pub fn result(&self) -> Outcome { Outcome::points(&[1, 0]) }
}
"#;

    #[test]
    fn reads_the_role_out_of_the_impl_block() {
        let v = describe(GAME.as_bytes()).unwrap();
        assert_eq!(v["role"], "game");
        assert_eq!(v["class"], "Nim");
        assert_eq!(v["lang"], "rust");
        assert_eq!(v["doc"], "Take one to three; taking the last one loses.");
    }

    #[test]
    fn a_helper_struct_does_not_win_over_the_game() {
        let v = describe(GAME.as_bytes()).unwrap();
        let classes = v["classes"].as_array().unwrap();
        assert_eq!(classes.len(), 2);
        assert_eq!(classes[0]["chosen"], false);
        assert_eq!(classes[1]["chosen"], true);
    }

    #[test]
    fn a_fn_inside_a_method_is_not_a_method() {
        let v = describe(GAME.as_bytes()).unwrap();
        let methods: Vec<&str> = v["exports"]
            .as_array()
            .unwrap()
            .iter()
            .map(|m| m["name"].as_str().unwrap())
            .collect();
        assert_eq!(methods, ["new", "view", "step", "done", "result"]);
    }

    #[test]
    fn consts_come_through_for_the_shim() {
        let c = chosen(GAME.as_bytes()).unwrap();
        assert_eq!(c.konst("NAME"), Some("\"\""));   // literals are blanked by the scrubber
        assert_eq!(c.konst("PLAYERS"), Some("2"));
        assert_eq!(c.method("new").unwrap().arity(), 1);
        assert_eq!(c.method("view").unwrap().receiver, "&self");
        assert_eq!(c.method("step").unwrap().receiver, "&mut self");
    }

    #[test]
    fn a_struct_with_play_is_a_player() {
        let src = "pub struct Bot;\nimpl Bot {\n  pub fn play(&mut self, view: &str, seat: usize) -> String { String::new() }\n}\n";
        let v = describe(src.as_bytes()).unwrap();
        assert_eq!(v["role"], "player");
        assert_eq!(v["exports"][0]["signature"], "(&mut self, view: &str, seat: usize) -> String");
    }

    #[test]
    fn a_class_that_is_neither_says_what_it_lacks() {
        let src = "pub struct Thing;\nimpl Thing { pub fn hello(&self) -> i32 { 1 } }\n";
        let v = describe(src.as_bytes()).unwrap();
        assert_eq!(v["role"], "class");
        assert_eq!(v["missing"], json!(["play"]));
    }

    #[test]
    fn a_trait_impl_is_recorded_but_does_not_make_the_role() {
        let src = "pub struct Bot;\nimpl Default for Bot { fn play(&mut self) {} }\n";
        let v = describe(src.as_bytes()).unwrap();
        assert_eq!(v["role"], "class");
        assert_eq!(v["classes"][0]["bases"], "Default");
    }

    #[test]
    fn a_method_named_in_a_comment_or_a_string_is_not_a_method() {
        let src = "pub struct Bot;\n// pub fn play(&self) {}\nimpl Bot {\n  /* pub fn play(&self) {} */\n  pub fn hello(&self) -> String { String::from(\"pub fn play(&self)\") }\n}\n";
        let v = describe(src.as_bytes()).unwrap();
        assert_eq!(v["exports"].as_array().unwrap().len(), 1);
        assert_eq!(v["exports"][0]["name"], "hello");
    }

    #[test]
    fn a_lifetime_is_not_an_unterminated_char_literal() {
        // `'static` followed by a brace-bearing line: if the scrubber treated
        // the quote as a literal it would swallow the rest of the file.
        let src = "pub struct Bot;\nimpl Bot {\n  pub const NAME: &'static str = \"bot\";\n  pub fn play(&mut self, view: &str, seat: usize) -> String { String::new() }\n}\n";
        let v = describe(src.as_bytes()).unwrap();
        assert_eq!(v["role"], "player");
    }

    #[test]
    fn a_crate_that_cannot_be_resolved_is_flagged_before_the_compiler_says_so() {
        let src = "use serde::Serialize;\npub struct Bot;\nimpl Bot { pub fn play(&mut self, v: &str, s: usize) -> String { String::new() } }\n";
        let v = describe(src.as_bytes()).unwrap();
        assert_eq!(v["host_needs"][0]["namespace"], "rust:crates");
    }

    #[test]
    fn reaching_for_a_host_that_is_not_there_is_flagged() {
        let src = "use std::fs;\npub struct Bot;\nimpl Bot { pub fn play(&mut self, v: &str, s: usize) -> String { String::new() } }\n";
        let v = describe(src.as_bytes()).unwrap();
        assert_eq!(v["host_needs"][0]["namespace"], "rust:no-such-host");
    }

    #[test]
    fn the_templates_we_hand_out_are_what_we_say_they_are() {
        assert_eq!(describe(GAME_TEMPLATE.as_bytes()).unwrap()["role"], "game");
        assert_eq!(describe(PLAYER_TEMPLATE.as_bytes()).unwrap()["role"], "player");
    }

    #[test]
    fn python_is_not_rust_and_wasm_is_neither() {
        assert!(!looks_like_rust(b"\0asm\x01\0\0\0"));
        assert!(!looks_like_rust(b"class Bot:\n    def play(self, view, seat):\n        return ''\n"));
        assert!(looks_like_rust(GAME.as_bytes()));
    }
}
