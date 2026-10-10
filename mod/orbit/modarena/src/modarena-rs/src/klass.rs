//! The class reader — the source-code twin of `wasm.rs`.
//!
//! A wasm module says what it is in its export section, and `wasm.rs` reads it
//! rather than trusting the uploader. A Python class says what it is in its
//! `def`s, and this reads those. Same promise, different container: the role
//! comes out of the bytes, never out of the upload form.
//!
//! This is a scanner, not a parser. It walks the source line by line looking
//! for `class` headers, the `def`s indented under them, the plain attributes
//! beside those, and every `import`. It does not evaluate anything and it does
//! not need to be right about Python's whole grammar — it needs to be right
//! about the shape of a file someone wrote to enter this arena, and honest
//! about what it saw. The sandbox in `runtime/host.py` is what actually holds
//! the line at run time.
//!
//! A class exposing `view` `step` `done` `result` is a game.
//! A class exposing `play` is a player.
//! Anything else is stored as `class`, readable and listable like any module.

use serde_json::{json, Value};

type R<T> = Result<T, String>;

/// The methods a class has to define to be sat down as a game. `__init__` is
/// not in the list: a game with no opening state to build is legal, and the
/// harness passes the seed to whatever constructor it finds.
pub const GAME_METHODS: [&str; 4] = ["view", "step", "done", "result"];
pub const PLAYER_METHODS: [&str; 1] = ["play"];

/// Modules the sandbox will import for a class. Everything here is pure
/// computation — no filesystem, no network, no clock that could break a
/// replay. `runtime/host.py` enforces this list; it is repeated here so the
/// console can warn about an import *before* anyone stores the file.
pub const ALLOWED_IMPORTS: [&str; 24] = [
    "abc", "array", "bisect", "collections", "copy", "dataclasses", "decimal", "enum",
    "fractions", "functools", "hashlib", "heapq", "itertools", "json", "math", "operator",
    "queue", "random", "re", "statistics", "string", "textwrap", "types", "typing",
];


/// The starting point `game_abi(lang=class)` hands out, and the one the
/// console's editor opens with. It lives here, next to the reader that decides
/// what a class is, so the template and the rule can never drift apart.
pub const GAME_TEMPLATE: &str = r#""""A game is a class. These four methods are the whole contract."""


class MyGame:
    """One line about the game — it shows up on the card."""

    name = "mygame"
    players = 2          # seats, or [min, max]
    max_turns = 50

    def __init__(self, seed):
        """The opening position. `seed` is the match seed."""
        self.score = [0, 0]
        self.turn_no = 0

    def view(self, seat):
        """What this seat can see — the only thing its player gets.

        Show a seat only what it is entitled to and hidden information works.
        Say `Legal moves: ...` somewhere: a model has nothing else to go on.
        """
        return (f"You are seat {seat}. Score {self.score}.\n"
                f"Legal moves: rock, paper, scissors")

    def step(self, moves):
        """Apply one round. `moves` is {seat: "text"}, keyed by int and str.

        Return {seat: was_it_legal}; add "note": "..." for a line in the
        transcript. Whatever you mark False counts against that player.
        """
        self.turn_no += 1
        beats = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
        played = {s: str(moves.get(s, "")).strip().lower() for s in (0, 1)}
        legal = {s: played[s] in beats for s in (0, 1)}
        if all(legal.values()) and played[0] != played[1]:
            self.score[0 if beats[played[0]] == played[1] else 1] += 1
        return {**legal, "note": f"{played[0]} vs {played[1]}"}

    def turn(self):
        """Optional: who moves now. Both seats at once makes it simultaneous.
        Leave it out and seats alternate."""
        return [0, 1]

    def done(self):
        return self.turn_no >= self.max_turns or max(self.score) > 5

    def result(self):
        """Higher is better. The ratings come out of the order."""
        return {"scores": self.score, "summary": f"{self.score[0]}-{self.score[1]}"}
"#;

/// The player half of the same idea: one method, and the same view a model
/// would have been given.
pub const PLAYER_TEMPLATE: &str = r#""""A player is a class with one method."""

import random


class MyBot:
    """One line about how it plays."""

    name = "mybot"

    def play(self, view, seat):
        """Return the move as text.

        `view` is exactly what the game showed this seat — the same text a
        model in this seat would be given, which is what makes the two
        comparable. Anything printed here lands in the transcript.
        """
        for line in view.splitlines():
            if line.startswith("Legal moves:"):
                options = [m.strip() for m in line.split(":", 1)[1].split(",") if m.strip()]
                return random.choice(options)
        return ""
"#;

#[derive(Debug, Default)]
struct Method {
    name: String,
    args: String,
    line: usize,
    doc: String,
}

#[derive(Debug, Default)]
struct Class {
    name: String,
    bases: String,
    line: usize,
    doc: String,
    methods: Vec<Method>,
    attributes: Vec<(String, String)>,
}

impl Class {
    fn has(&self, name: &str) -> bool {
        self.methods.iter().any(|m| m.name == name)
    }

    fn role(&self) -> &'static str {
        if GAME_METHODS.iter().all(|m| self.has(m)) {
            "game"
        } else if PLAYER_METHODS.iter().all(|m| self.has(m)) {
            "player"
        } else {
            "class"
        }
    }

    /// What this class would have to add to become a game or a player — the
    /// most useful thing to say to someone whose upload came back as `class`.
    fn missing(&self) -> Vec<String> {
        let game: Vec<_> = GAME_METHODS.iter().filter(|m| !self.has(m)).collect();
        let player: Vec<_> = PLAYER_METHODS.iter().filter(|m| !self.has(m)).collect();
        // Report the nearer of the two targets.
        if player.len() <= game.len() {
            player.into_iter().map(|m| m.to_string()).collect()
        } else {
            game.into_iter().map(|m| m.to_string()).collect()
        }
    }
}

/// Does this look like Python source at all? Cheap and deliberately generous:
/// the real test is whether a class with the right methods falls out of it.
pub fn looks_like_python(bytes: &[u8]) -> bool {
    if bytes.starts_with(b"\0asm") {
        return false;
    }
    let Ok(text) = std::str::from_utf8(bytes) else {
        return false;
    };
    text.lines().any(|l| {
        let t = l.trim_start();
        t.starts_with("class ") || t.starts_with("def ") || t.starts_with("import ")
            || t.starts_with("from ")
    })
}

fn indent_of(line: &str) -> usize {
    line.len() - line.trim_start().len()
}

/// Strip a trailing `# comment`, leaving anything inside quotes alone.
fn uncomment(line: &str) -> &str {
    let bytes = line.as_bytes();
    let (mut quote, mut i) = (0u8, 0usize);
    while i < bytes.len() {
        let c = bytes[i];
        if quote != 0 {
            if c == b'\\' {
                i += 2;
                continue;
            }
            if c == quote {
                quote = 0;
            }
        } else if c == b'"' || c == b'\'' {
            quote = c;
        } else if c == b'#' {
            return &line[..i];
        }
        i += 1;
    }
    line
}

/// The docstring opening on `lines[i]`, if there is one, as a single line.
fn docstring_at(lines: &[&str], i: usize) -> String {
    let Some(raw) = lines.get(i) else {
        return String::new();
    };
    let t = raw.trim();
    for q in ["\"\"\"", "'''"] {
        if let Some(rest) = t.strip_prefix(q) {
            // One-liner: """like this."""
            if let Some(end) = rest.find(q) {
                return rest[..end].trim().to_string();
            }
            // Multi-line: take the first non-empty line, which is the summary.
            let first = rest.trim();
            if !first.is_empty() {
                return first.to_string();
            }
            for l in &lines[i + 1..] {
                let l = l.trim();
                if l.is_empty() {
                    continue;
                }
                let l = l.trim_end_matches(q).trim();
                return l.to_string();
            }
            return String::new();
        }
    }
    // A plain string literal as the first statement counts too.
    if (t.starts_with('"') && t.ends_with('"') && t.len() > 1)
        || (t.starts_with('\'') && t.ends_with('\'') && t.len() > 1)
    {
        return t[1..t.len() - 1].to_string();
    }
    String::new()
}

/// Everything the file imports, deduplicated, in source order.
fn imports_in(lines: &[&str]) -> Vec<(String, String)> {
    let mut out: Vec<(String, String)> = Vec::new();
    for raw in lines {
        let line = uncomment(raw).trim();
        let (module, names) = if let Some(rest) = line.strip_prefix("import ") {
            (rest.split(&[' ', ','][..]).next().unwrap_or("").to_string(), String::new())
        } else if let Some(rest) = line.strip_prefix("from ") {
            let mut parts = rest.splitn(2, " import ");
            let m = parts.next().unwrap_or("").trim().to_string();
            let n = parts.next().unwrap_or("").trim().to_string();
            (m, n)
        } else {
            continue;
        };
        let module = module.trim().to_string();
        if module.is_empty() || out.iter().any(|(m, _)| *m == module) {
            continue;
        }
        out.push((module, names));
    }
    out
}

/// Scan the source into the classes it defines.
fn scan(text: &str) -> Vec<Class> {
    let lines: Vec<&str> = text.lines().collect();
    let mut classes: Vec<Class> = Vec::new();
    // (index into classes, the `class` line's indent, the body's indent once
    // it is known). The body indent matters: a `def` nested inside a method is
    // not a method, and `legal = {…}` inside one is not a class attribute.
    let mut current: Option<(usize, usize, Option<usize>)> = None;
    // Inside a triple-quoted block, nothing is code.
    let mut fence: Option<&str> = None;

    for (i, raw) in lines.iter().enumerate() {
        let line = uncomment(raw);
        let trimmed = line.trim();

        if let Some(q) = fence {
            if trimmed.contains(q) {
                fence = None;
            }
            continue;
        }
        // Opening a docstring that does not close on the same line.
        for q in ["\"\"\"", "'''"] {
            if let Some(at) = trimmed.find(q) {
                if !trimmed[at + q.len()..].contains(q) {
                    fence = Some(q);
                }
                break;
            }
        }
        if fence.is_some() || trimmed.is_empty() {
            continue;
        }

        let indent = indent_of(line);

        if let Some(rest) = trimmed.strip_prefix("class ") {
            let head = rest.trim_end_matches(':').trim();
            let (name, bases) = match head.split_once('(') {
                Some((n, b)) => (n.trim().to_string(), b.trim_end_matches(')').trim().to_string()),
                None => (head.to_string(), String::new()),
            };
            classes.push(Class {
                name,
                bases,
                line: i + 1,
                doc: docstring_at(&lines, i + 1),
                ..Default::default()
            });
            current = Some((classes.len() - 1, indent, None));
            continue;
        }

        let Some((idx, class_indent, body_indent)) = current else { continue };
        if indent <= class_indent {
            // Dedented back out of the class body.
            current = None;
            continue;
        }
        // The shallowest line inside the class is its body. Taking the minimum
        // rather than the first keeps a class that opens with a continuation
        // line or an oddly indented docstring from setting the bar too deep.
        let body = body_indent.map_or(indent, |b| b.min(indent));
        current = Some((idx, class_indent, Some(body)));
        if indent > body {
            continue;   // inside a method — not the class's own surface
        }

        let statement = trimmed.strip_prefix("async ").unwrap_or(trimmed);
        if let Some(rest) = statement.strip_prefix("def ") {
            let (name, args) = match rest.split_once('(') {
                Some((n, a)) => (
                    n.trim().to_string(),
                    a.rsplit_once(')').map(|(x, _)| x).unwrap_or(a).trim().to_string(),
                ),
                None => (rest.trim_end_matches(':').trim().to_string(), String::new()),
            };
            classes[idx].methods.push(Method {
                name,
                args,
                line: i + 1,
                doc: docstring_at(&lines, i + 1),
            });
            continue;
        }

        // A plain class attribute: `name = "ttt"`, `players = 2`. These are how
        // a class titles itself, so they are worth carrying into the card.
        if let Some((lhs, rhs)) = statement.split_once('=') {
            let key = lhs.split(':').next().unwrap_or(lhs).trim();
            if !key.is_empty()
                && key.chars().all(|c| c.is_alphanumeric() || c == '_')
                && !key.starts_with('_')
                && !rhs.trim().is_empty()
            {
                classes[idx].attributes.push((key.to_string(), rhs.trim().to_string()));
            }
        }
    }

    classes
}

/// Pick the class that will be instantiated. A file may hold helpers; the one
/// that counts is the one that can be played. Ties go to the last defined,
/// which is the one a human reading top-to-bottom would call the answer.
fn pick(classes: &[Class]) -> Option<usize> {
    let rank = |c: &Class| match c.role() {
        "game" => 3,
        "player" => 2,
        _ => 1,
    };
    let mut best: Option<usize> = None;
    for (i, c) in classes.iter().enumerate() {
        let better = match best {
            None => true,
            Some(b) => rank(c) >= rank(&classes[b]),
        };
        if better {
            best = Some(i);
        }
    }
    best
}

/// Describe Python source the way `wasm::describe` describes a binary: the
/// same keys where they mean the same thing, so one registry, one card
/// renderer and one console table serve both.
pub fn describe(bytes: &[u8]) -> R<Value> {
    let text = std::str::from_utf8(bytes)
        .map_err(|_| "not a wasm module and not UTF-8 text — nothing here can be read".to_string())?;
    if text.len() > 1 << 20 {
        return Err("class source is larger than 1 MiB".into());
    }
    let classes = scan(text);
    if classes.is_empty() {
        return Err("no `class` in this file — the unit here is a class: one that defines \
                    view/step/done/result is a game, one that defines play is a player. \
                    `m modarena/template role=game` prints a starting point."
            .into());
    }
    let chosen = pick(&classes).unwrap_or(0);
    let main = &classes[chosen];

    let imports = imports_in(&text.lines().collect::<Vec<_>>());
    let blocked: Vec<&str> = imports
        .iter()
        .map(|(m, _)| m.split('.').next().unwrap_or(m))
        .filter(|top| !ALLOWED_IMPORTS.contains(top))
        .collect();

    Ok(json!({
        "role": main.role(),
        "lang": "python",
        "class": main.name,
        "doc": main.doc,
        "size": bytes.len(),
        "lines": text.lines().count(),
        "runs_in": "the node runner, in a sandboxed python subprocess — not in the browser",
        // `exports` is what the console's table reads, so a class lists its
        // methods exactly where a wasm module lists its exports.
        "exports": main.methods.iter().map(|m| json!({
            "name": m.name,
            "kind": "method",
            "signature": format!("({})", m.args),
            "line": m.line,
            "doc": m.doc,
        })).collect::<Vec<_>>(),
        "imports": imports.iter().map(|(m, names)| json!({
            "module": m,
            "name": names,
            "kind": "import",
            "allowed": ALLOWED_IMPORTS.contains(&m.split('.').next().unwrap_or(m)),
        })).collect::<Vec<_>>(),
        "host_needs": if blocked.is_empty() {
            json!([])
        } else {
            json!([{ "namespace": "python:blocked", "imports": blocked.len(),
                     "modules": blocked,
                     "note": "the sandbox refuses these — the class will fail on the import line" }])
        },
        "attributes": main.attributes.iter().map(|(k, v)| json!({ "name": k, "value": v }))
            .collect::<Vec<_>>(),
        "missing": main.missing(),
        "classes": classes.iter().map(|c| json!({
            "name": c.name,
            "bases": c.bases,
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
import random
from math import floor

class Helper:
    def tidy(self, x):
        return x

class Nim:
    """Take one to three; taking the last one loses."""
    name = "nim"
    players = 2

    def __init__(self, seed):
        self.left = 12 + seed % 5

    def view(self, seat):
        return f"{self.left} left"

    def step(self, moves):   # {seat: "2"}
        return {0: True}

    def done(self):
        return self.left <= 0

    def result(self):
        return {"scores": [1, 0]}
"#;

    #[test]
    fn reads_the_role_out_of_the_methods() {
        let v = describe(GAME.as_bytes()).unwrap();
        assert_eq!(v["role"], "game");
        assert_eq!(v["class"], "Nim");
        assert_eq!(v["doc"], "Take one to three; taking the last one loses.");
    }

    #[test]
    fn a_helper_class_does_not_win_over_the_game() {
        let v = describe(GAME.as_bytes()).unwrap();
        let classes = v["classes"].as_array().unwrap();
        assert_eq!(classes.len(), 2);
        assert_eq!(classes[0]["chosen"], false);
        assert_eq!(classes[1]["chosen"], true);
    }

    #[test]
    fn a_class_with_play_is_a_player() {
        let src = "class Bot:\n    def play(self, view, seat):\n        return '4'\n";
        let v = describe(src.as_bytes()).unwrap();
        assert_eq!(v["role"], "player");
        assert_eq!(v["exports"][0]["signature"], "(self, view, seat)");
    }

    #[test]
    fn a_class_that_is_neither_says_what_it_lacks() {
        let src = "class Thing:\n    def hello(self):\n        return 1\n";
        let v = describe(src.as_bytes()).unwrap();
        assert_eq!(v["role"], "class");
        assert_eq!(v["missing"], json!(["play"]));
    }

    #[test]
    fn attributes_come_through() {
        let v = describe(GAME.as_bytes()).unwrap();
        let attrs = v["attributes"].as_array().unwrap();
        assert!(attrs.iter().any(|a| a["name"] == "name" && a["value"] == "\"nim\""));
        assert!(attrs.iter().any(|a| a["name"] == "players"));
    }

    #[test]
    fn an_import_the_sandbox_refuses_is_flagged_before_it_is_stored() {
        let src = "import socket\nclass Bot:\n    def play(self, view, seat):\n        return ''\n";
        let v = describe(src.as_bytes()).unwrap();
        assert_eq!(v["imports"][0]["allowed"], false);
        assert_eq!(v["host_needs"][0]["namespace"], "python:blocked");
    }

    #[test]
    fn a_def_in_a_docstring_is_not_a_method() {
        let src = "class Bot:\n    \"\"\"Example:\n    def play(self, view, seat): ...\n    \"\"\"\n    def play(self, view, seat):\n        return ''\n";
        let v = describe(src.as_bytes()).unwrap();
        assert_eq!(v["exports"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn what_happens_inside_a_method_stays_there() {
        // A local named like an attribute, and a helper defined inside a
        // method, are both invisible to the card — otherwise a class's own
        // surface would depend on how its methods were written.
        let src = "class Bot:\n    name = \"bot\"\n\n    def play(self, view, seat):\n\
                   \x20       legal = [1, 2]\n        def helper():\n            return 1\n\
                   \x20       return str(helper())\n";
        let v = describe(src.as_bytes()).unwrap();
        let methods: Vec<_> = v["exports"].as_array().unwrap().iter()
            .map(|e| e["name"].as_str().unwrap()).collect();
        assert_eq!(methods, ["play"]);
        let attrs: Vec<_> = v["attributes"].as_array().unwrap().iter()
            .map(|a| a["name"].as_str().unwrap()).collect();
        assert_eq!(attrs, ["name"]);
        assert_eq!(v["role"], "player");
    }

    #[test]
    fn a_hash_inside_a_string_is_not_a_comment() {
        let src = "class Bot:\n    tag = \"a # b\"\n    def play(self, view, seat):\n        return ''\n";
        let v = describe(src.as_bytes()).unwrap();
        assert_eq!(v["attributes"][0]["value"], "\"a # b\"");
    }

    #[test]
    fn a_file_with_no_class_is_refused_with_a_way_forward() {
        let e = describe(b"x = 1\n").unwrap_err();
        assert!(e.contains("class"), "{e}");
    }

    #[test]
    fn the_templates_we_hand_out_are_what_we_say_they_are() {
        // The one test that would catch the template drifting away from the
        // rule — somebody edits the starter class and it no longer loads.
        assert_eq!(describe(GAME_TEMPLATE.as_bytes()).unwrap()["role"], "game");
        assert_eq!(describe(PLAYER_TEMPLATE.as_bytes()).unwrap()["role"], "player");
    }

    #[test]
    fn wasm_bytes_are_not_python() {
        assert!(!looks_like_python(b"\0asm\x01\0\0\0"));
    }
}
