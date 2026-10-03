//! The Rust class compiler — the step that has no counterpart on the Python
//! side, and the reason a Rust class gets the strong sandbox rather than the
//! convenient one.
//!
//! A Python class is stored and then interpreted. A Rust class is stored and
//! then *compiled*: this glues three pieces into one crate —
//!
//!     the prelude   src/rustclass/prelude.rs — Moves, Step, Outcome, arena::…
//!     the class     exactly the bytes that were uploaded, unedited
//!     a shim        generated here from what `rsklass` read out of the class
//!
//! — and hands it to `rustc --target wasm32-unknown-unknown`. What comes back
//! is an ordinary arena wasm module exporting `game_init`/`game_view`/… or
//! `play`, indistinguishable from one somebody compiled themselves. It runs in
//! the same engine, through the same match loop, in the browser as well as the
//! runner. A Rust class is therefore the only class that plays in a tab.
//!
//! The shim is generated rather than written by the author for one reason: the
//! wasm ABI is stateless (the host holds a state string) and a class is not
//! (the state is `self`). The shim keeps the instance in a static, which is
//! sound because one instance is one match — the same arrangement `PyGame`
//! has, arrived at from the other direction.
//!
//! Compiling is slow the first time (a second or two) and free after that: the
//! output is cached under the module's own id, which is the hash of its
//! source, so the cache can never be stale. Failures are cached too — a class
//! that does not compile does not compile faster on the second attempt, and
//! the error is worth returning immediately.

use crate::blobs;
use crate::rsklass::{self, Class};
use serde_json::{json, Value};
use std::fs;
use std::path::PathBuf;
use std::process::Command;

/// What every Rust class is compiled against. Baked in so the server is one
/// binary, and served at `/abi?lang=rust` so it is also readable.
pub const PRELUDE: &str = include_str!("../../rustclass/prelude.rs");

/// Bump when the prelude or the shim changes shape — it is part of the cache
/// key, so an old artefact is never handed back for a new contract.
pub const SHIM_VERSION: u32 = 1;

const TARGET: &str = "wasm32-unknown-unknown";

fn cache_dir() -> PathBuf {
    blobs::state_dir().join("rustc")
}

fn artefact(id: &str) -> PathBuf {
    cache_dir().join(format!("{id}.v{SHIM_VERSION}.wasm"))
}

fn failure(id: &str) -> PathBuf {
    cache_dir().join(format!("{id}.v{SHIM_VERSION}.err"))
}

/// Is there a Rust compiler on this box at all, and can it target wasm?
pub fn toolchain() -> Value {
    let version = Command::new(rustc_bin())
        .arg("--version")
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string());
    let targets = Command::new(rustc_bin())
        .args(["--print", "target-list"])
        .output()
        .ok()
        .map(|o| String::from_utf8_lossy(&o.stdout).contains(TARGET))
        .unwrap_or(false);
    // `--print target-list` names every target rustc knows; whether the std
    // for it is *installed* is the question that actually matters, and the
    // only honest way to answer it is to have compiled something.
    json!({
        "rustc": version,
        "target": TARGET,
        "knows_target": targets,
        "available": version.is_some(),
        "cache": cache_dir().to_string_lossy(),
        "shim_version": SHIM_VERSION,
    })
}

fn rustc_bin() -> String {
    std::env::var("ARENA_RUSTC").unwrap_or_else(|_| "rustc".to_string())
}

// ── the shim ─────────────────────────────────────────────────────────────

fn escape_rust(s: &str) -> String {
    s.replace('\\', "\\\\").replace('"', "\\\"").replace('\n', "\\n")
}

/// How to build one.
///
/// A class may take the seed, ignore it, or offer no constructor at all. The
/// last case is the interesting one, because `pub struct Bot;` with a single
/// `play` method is how most players are actually written and it would be an
/// unkind thing to reject: there is nothing to construct, so the shim writes
/// the `Default` the author did not need to think about. A struct with fields
/// and no `new` still has to say what its opening state is, and the compiler
/// asks it to in so many words.
fn construct(class: &Class) -> String {
    match class.method("new") {
        Some(m) if m.arity() >= 1 => format!("{}::new(seed)", class.name),
        Some(_) => format!("{}::new()", class.name),
        None if class.unit => format!("{} {{}}", class.name),
        None => format!("<{} as Default>::default()", class.name),
    }
}

/// `game_info`, out of the consts the author declared. Every one is optional;
/// what is missing gets the same default the wasm ABI already documents.
fn info_literal(class: &Class) -> String {
    let text = |name: &str, fallback: &str| -> String {
        match class.konst(name) {
            // The scrubber blanks string literals, so a declared NAME is known
            // to exist but not what it says — read it back out of the source.
            Some(_) => format!("escape({}::{})", class.name, name),
            None => format!("escape(\"{}\")", escape_rust(fallback)),
        }
    };
    let number = |name: &str, fallback: &str| -> String {
        match class.konst(name) {
            Some(_) => format!("({}::{}) as i64", class.name, name),
            None => fallback.to_string(),
        }
    };
    let players = number("PLAYERS", "2");
    let min = if class.konst("MIN_PLAYERS").is_some() {
        number("MIN_PLAYERS", "2")
    } else {
        players.clone()
    };
    let max = if class.konst("MAX_PLAYERS").is_some() {
        number("MAX_PLAYERS", "2")
    } else {
        players
    };
    format!(
        r#"    __ret(format!(
        "{{{{\"name\":\"{{}}\",\"description\":\"{{}}\",\"min_players\":{{}},\"max_players\":{{}},\"max_turns\":{{}}}}}}",
        {name}, {description}, {min}, {max}, {max_turns}
    ))"#,
        name = text("NAME", ""),
        description = text("DESCRIPTION", ""),
        min = min,
        max = max,
        max_turns = number("MAX_TURNS", "200"),
    )
}

/// The generated half of the crate: the arena wasm ABI, wrapped around the
/// class the reader chose.
pub fn shim(class: &Class) -> String {
    let name = &class.name;
    let role = class.role();
    let mut out = String::new();

    out.push_str(&format!(
        "\n// ── generated by arena/rustc.rs — the wasm ABI, wrapped around `{name}` ──\n\
         // One instance is one match, so the state the wasm ABI passes back and\n\
         // forth is only a turn counter; the position lives in `self`, where a\n\
         // class author put it.\n\n\
         static mut __INSTANCE: Option<{name}> = None;\n\
         static mut __TURN: i64 = 0;\n\n\
         #[doc(hidden)]\n\
         fn __build(seed: i64) -> {name} {{ {ctor} }}\n\n\
         #[doc(hidden)]\n\
         fn __instance() -> &'static mut {name} {{\n\
         \x20   unsafe {{\n\
         \x20       let slot = &mut *core::ptr::addr_of_mut!(__INSTANCE);\n\
         \x20       if slot.is_none() {{\n\
         \x20           // No `game_init` came first: a player, or a game asked\n\
         \x20           // something before the match opened. Seed it from the\n\
         \x20           // host PRNG, which the match seeded.\n\
         \x20           *slot = Some(__build((arena::random() * 2147483647.0) as i64));\n\
         \x20       }}\n\
         \x20       slot.as_mut().unwrap()\n\
         \x20   }}\n\
         }}\n",
        name = name,
        ctor = construct(class),
    ));

    if role == "game" {
        let step_arg = if class
            .method("step")
            .map(|m| m.args.contains("&Moves"))
            .unwrap_or(true)
        {
            "&__moves"
        } else {
            "__moves"
        };
        out.push_str(&format!(
            "\n#[no_mangle]\n\
             pub extern \"C\" fn game_init(seed: i32) -> i64 {{\n\
             \x20   unsafe {{\n\
             \x20       *(&mut *core::ptr::addr_of_mut!(__INSTANCE)) = Some(__build(seed as i64));\n\
             \x20       __TURN = 0;\n\
             \x20   }}\n\
             \x20   __ret(\"0\".to_string())\n\
             }}\n\n\
             #[no_mangle]\n\
             pub extern \"C\" fn game_view(sp: i32, sl: i32, seat: i32) -> i64 {{\n\
             \x20   let _ = __text(sp, sl);\n\
             \x20   __ret(__string_of(__instance().view(seat.max(0) as usize)))\n\
             }}\n\n\
             #[no_mangle]\n\
             pub extern \"C\" fn game_step(sp: i32, sl: i32, mp: i32, ml: i32) -> i64 {{\n\
             \x20   let _ = __text(sp, sl);\n\
             \x20   let __moves = Moves::parse(__text(mp, ml));\n\
             \x20   let __step = IntoStep::into_step(__instance().step({step_arg}));\n\
             \x20   let __turn = unsafe {{ __TURN += 1; __TURN }};\n\
             \x20   __ret(__step.encode(&__turn.to_string()))\n\
             }}\n\n\
             #[no_mangle]\n\
             pub extern \"C\" fn game_done(sp: i32, sl: i32) -> i32 {{\n\
             \x20   let _ = __text(sp, sl);\n\
             \x20   if __instance().done() {{ 1 }} else {{ 0 }}\n\
             }}\n\n\
             #[no_mangle]\n\
             pub extern \"C\" fn game_result(sp: i32, sl: i32) -> i64 {{\n\
             \x20   let _ = __text(sp, sl);\n\
             \x20   __ret(IntoOutcome::into_outcome(__instance().result()).encode())\n\
             }}\n\n\
             #[no_mangle]\n\
             pub extern \"C\" fn game_info() -> i64 {{\n\
             {info}\n\
             }}\n",
            step_arg = step_arg,
            info = info_literal(class),
        ));

        if class.has("turn") {
            out.push_str(
                "\n#[no_mangle]\n\
                 pub extern \"C\" fn game_turn(sp: i32, sl: i32) -> i64 {\n\
                 \x20   let _ = __text(sp, sl);\n\
                 \x20   __ret(__turn(IntoTurn::into_turn(__instance().turn())))\n\
                 }\n",
            );
        }
    }

    // A struct may be both — a game that can also sit at one. Both halves are
    // emitted, because the registry types a module by what it defines and this
    // one defines both.
    if class.has("play") {
        let arity = class.method("play").map(|m| m.arity()).unwrap_or(2);
        let call = if arity >= 2 {
            "__instance().play(__view, seat.max(0) as usize)"
        } else {
            "__instance().play(__view)"
        };
        out.push_str(&format!(
            "\n#[no_mangle]\n\
             pub extern \"C\" fn play(vp: i32, vl: i32, seat: i32) -> i64 {{\n\
             \x20   let __view = __text(vp, vl);\n\
             \x20   __ret(__string_of({call}))\n\
             }}\n"
        ));
    }

    out
}

/// The whole crate, as one file.
///
/// The class goes in a module of its own, and the reason is worth writing
/// down: a Rust file may open with `//!`, and an inner doc comment is only
/// legal before any item. Spliced into the middle of the prelude it is a
/// syntax error on somebody's first line, for a rule they did not break. In
/// its own module it is the first thing in that module and perfectly legal.
///
/// Two things fall out of it for free. `use super::*` is order-independent in
/// Rust, so it can sit *after* the class and the prelude is still in scope
/// throughout. And the shim goes inside the module too, which is how a class
/// written with a private `struct` — the ordinary way to write one — is still
/// reachable by the code that has to call it.
///
/// Returns the crate and the line the author's first line landed on, which is
/// what `remap` subtracts to give errors back in their own numbering.
pub fn crate_source(source: &str, class: &Class) -> (String, usize) {
    let head = format!("{PRELUDE}\nmod class {{\n");
    let offset = head.lines().count();
    (
        format!(
            "{head}{source}\n\n// ── the prelude, and the generated ABI around the class ──\nuse super::*;\n{}\n}}\n",
            shim(class)
        ),
        offset,
    )
}

// ── compiling ────────────────────────────────────────────────────────────

/// Point `rustc`'s line numbers back at the source the author wrote. The
/// prelude sits above their first line, and being told about `main.rs:412`
/// when your file is forty lines long is the kind of thing that makes people
/// give up on a tool.
fn remap(stderr: &str, offset: usize, filename: &str) -> String {
    let mut out = String::new();
    for line in stderr.lines() {
        let mut fixed = line.to_string();
        if let Some(at) = line.find("__arena_class.rs:") {
            let rest = &line[at + "__arena_class.rs:".len()..];
            let mut parts = rest.splitn(2, ':');
            if let (Some(num), Some(tail)) = (parts.next(), parts.next()) {
                if let Ok(n) = num.trim().parse::<usize>() {
                    let mapped = n.saturating_sub(offset);
                    fixed = format!(
                        "{}{filename}:{}:{tail}",
                        &line[..at],
                        if mapped == 0 { 1 } else { mapped }
                    );
                }
            }
        }
        // Line numbers in the gutter of an error snippet belong to the same
        // file, so shift those too or the arrow points at nothing.
        if let Some((num, tail)) = fixed.split_once(" | ") {
            if let Ok(n) = num.trim().parse::<usize>() {
                if n > offset {
                    let width = num.len();
                    fixed = format!("{:>width$} | {tail}", n - offset, width = width);
                }
            }
        }
        out.push_str(&fixed);
        out.push('\n');
    }
    out.trim_end().to_string()
}

/// The prelude's own errors are not the author's fault and not their problem —
/// if one shows up, say so rather than pointing at their file.
fn is_prelude_error(stderr: &str, offset: usize) -> bool {
    stderr.lines().any(|l| {
        l.find("__arena_class.rs:")
            .and_then(|at| l[at + "__arena_class.rs:".len()..].split(':').next()?.parse::<usize>().ok())
            .is_some_and(|n| n <= offset)
    })
}

/// Compile a Rust class to wasm, or hand back the cached artefact.
///
/// `id` is the module id — the hash of the source — so the cache key is the
/// content and there is no invalidation problem to get wrong.
pub fn compile(id: &str, source: &str) -> Result<Vec<u8>, String> {
    let out_path = artefact(id);
    if let Ok(bytes) = fs::read(&out_path) {
        if !bytes.is_empty() {
            return Ok(bytes);
        }
    }
    if let Ok(err) = fs::read_to_string(failure(id)) {
        if !err.trim().is_empty() {
            return Err(err);
        }
    }

    let class = rsklass::chosen(source.as_bytes())?;
    if class.role() == "class" {
        return Err(format!(
            "`{}` is not a game or a player — it defines {}, and a game needs view, step, \
             done and result while a player needs play. Nothing to compile an ABI around.",
            class.name,
            class.methods.iter().map(|m| m.name.as_str()).collect::<Vec<_>>().join(", ")
        ));
    }
    let (text, offset) = crate_source(source, &class);

    let dir = cache_dir().join(format!("build-{}", &id[..id.len().min(16)]));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).map_err(|e| format!("cannot write the build directory: {e}"))?;
    let main = dir.join("__arena_class.rs");
    fs::write(&main, &text).map_err(|e| format!("cannot write the crate: {e}"))?;

    let built = dir.join("out.wasm");
    let output = Command::new(rustc_bin())
        .arg("--edition")
        .arg("2021")
        .args(["--target", TARGET])
        .args(["--crate-type", "cdylib"])
        .args(["--crate-name", "arena_class"])
        .args(["-C", "opt-level=s"])
        .args(["-C", "panic=abort"])
        .args(["-C", "strip=symbols"])
        // Warnings are the author's business, not a reason to refuse the file.
        .args(["--cap-lints", "warn"])
        .arg("-o")
        .arg(&built)
        .arg(&main)
        .current_dir(&dir)
        .output()
        .map_err(|e| {
            format!(
                "could not run `{}`: {e} — a Rust class needs rustc and the {TARGET} target \
                 (`rustup target add {TARGET}`)",
                rustc_bin()
            )
        })?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr).to_string();
        let mut message = remap(&stderr, offset, "your class");
        if is_prelude_error(&stderr, offset) {
            message = format!(
                "the arena prelude failed to compile, which is this module's bug and not \
                 yours — please report it.\n\n{stderr}"
            );
        }
        let _ = fs::create_dir_all(cache_dir());
        let _ = fs::write(failure(id), &message);
        let _ = fs::remove_dir_all(&dir);
        return Err(message);
    }

    let bytes = fs::read(&built).map_err(|e| format!("rustc said it worked but wrote nothing: {e}"))?;
    let _ = fs::create_dir_all(cache_dir());
    let _ = fs::write(&out_path, &bytes);
    let _ = fs::remove_file(failure(id));
    let _ = fs::remove_dir_all(&dir);
    Ok(bytes)
}

/// Drop everything cached for one module — used when it is deleted.
pub fn forget(id: &str) {
    let _ = fs::remove_file(artefact(id));
    let _ = fs::remove_file(failure(id));
}

#[cfg(test)]
mod tests {
    use super::*;

    const GAME: &str = r#"
pub struct Nim { left: i64 }

impl Nim {
    pub const NAME: &'static str = "nim-rs";
    pub const PLAYERS: usize = 2;

    pub fn new(seed: i64) -> Nim { Nim { left: 12 + seed.rem_euclid(5) } }
    pub fn view(&self, seat: usize) -> String { format!("{} left, seat {seat}", self.left) }
    pub fn step(&mut self, moves: &Moves) -> Step {
        self.left -= moves.number(0).unwrap_or(1);
        Step::ok()
    }
    pub fn done(&self) -> bool { self.left <= 0 }
    pub fn result(&self) -> Outcome { Outcome::points(&[1, 0]) }
}
"#;

    #[test]
    fn the_shim_exports_the_wasm_game_abi() {
        let class = rsklass::chosen(GAME.as_bytes()).unwrap();
        let shim = shim(&class);
        for export in ["game_init", "game_view", "game_step", "game_done", "game_result", "game_info"] {
            assert!(shim.contains(&format!("fn {export}(")), "{export} missing from the shim");
        }
        assert!(!shim.contains("fn game_turn("), "no `turn` in the class, so none in the shim");
        assert!(shim.contains("Nim::new(seed)"));
    }

    #[test]
    fn a_player_gets_play_and_nothing_else() {
        let src = "pub struct Bot;\nimpl Bot { pub fn play(&mut self, view: &str, seat: usize) -> String { String::new() } }\n";
        let class = rsklass::chosen(src.as_bytes()).unwrap();
        let shim = shim(&class);
        assert!(shim.contains("fn play("));
        assert!(!shim.contains("fn game_view("));
        assert!(shim.contains("Bot {}"), "a unit struct needs no constructor: {shim}");
    }

    #[test]
    fn a_class_that_declares_a_name_has_it_read_off_its_own_const() {
        let class = rsklass::chosen(GAME.as_bytes()).unwrap();
        let shim = shim(&class);
        assert!(shim.contains("escape(Nim::NAME)"), "{shim}");
        assert!(shim.contains("(Nim::PLAYERS) as i64"));
    }

    #[test]
    fn rustc_line_numbers_come_back_pointing_at_the_authors_file() {
        let stderr = "error[E0308]: mismatched types\n  --> /tmp/x/__arena_class.rs:512:9\n   |\n512 |         self.left\n   |         ^^^^^^^^^\n";
        let out = remap(stderr, 500, "your class");
        assert!(out.contains("your class:12:9"), "{out}");
        assert!(out.contains("12 |         self.left"), "{out}");
    }

    #[test]
    fn the_prelude_is_baked_in_and_is_the_file_on_disk() {
        assert!(PRELUDE.contains("pub struct Moves"));
        assert!(PRELUDE.contains("pub fn mcp("));
    }
}
