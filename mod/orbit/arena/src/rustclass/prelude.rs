// The Rust class prelude — what every uploaded Rust class is compiled against.
//
// A class here is a `struct` with an `impl` block. The methods in that block
// decide what it is: `view`/`step`/`done`/`result` make it a game, `play`
// makes it a player. Exactly the rule the Python class layer uses, and the
// reader (`rsklass.rs`) applies it the same way — off the source, never off
// what the uploader said.
//
// What happens to the source is the only real difference. A Python class is
// executed by a sandboxed interpreter. A Rust class is *compiled* — this
// prelude, then the class, then a generated shim that exports the arena wasm
// ABI — and the wasm that falls out runs in the same engine sandbox as any
// other module here. So a Rust class gets the hard sandbox rather than the
// convenient one: no filesystem, no network, no clock, no syscalls, because
// wasm32-unknown-unknown has no way to ask for any of them.
//
// Everything below is what a class is allowed to reach. It is short on
// purpose: text in, text out, a seeded PRNG, a log line, and one door to the
// outside — `arena::mcp`, which is not a socket but a request the host makes
// on the class's behalf, against a server the match allowed in advance.
//
// Nothing here is `use`d automatically. The generated crate puts this file at
// the top of the same module the class is in, so `Moves`, `Step`, `Outcome`
// and `arena::…` are simply in scope.

#![allow(dead_code, unused_imports, unused_macros)]

// ── the string boundary ─────────────────────────────────────────────────
//
// The host writes UTF-8 into memory the module allocated and reads back one
// i64 packed as `(ptr << 32) | len`. A class never sees any of this; the shim
// does the marshalling and hands over `&str` and `String`.

/// Somewhere for the host to write. Leaks by design: an instance is one match.
#[no_mangle]
pub extern "C" fn alloc(len: i32) -> i32 {
    let mut buf: Vec<u8> = Vec::with_capacity(len.max(1) as usize);
    let ptr = buf.as_mut_ptr();
    core::mem::forget(buf);
    ptr as i32
}

#[doc(hidden)]
pub fn __text(ptr: i32, len: i32) -> &'static str {
    if len <= 0 || ptr == 0 {
        return "";
    }
    unsafe {
        let bytes = core::slice::from_raw_parts(ptr as *const u8, len as usize);
        core::str::from_utf8(bytes).unwrap_or("")
    }
}

#[doc(hidden)]
pub fn __ret(s: String) -> i64 {
    let bytes = s.into_bytes();
    let ptr = bytes.as_ptr() as i64;
    let len = bytes.len() as i64;
    core::mem::forget(bytes);
    (ptr << 32) | len
}

/// Whatever a `view` or a `play` returned, as text. `String` and `&str` both
/// land here, which is the only flexibility either of those needs.
#[doc(hidden)]
pub fn __string_of(v: impl AsRef<str>) -> String {
    v.as_ref().to_string()
}

// ── json, only as much as this needs ────────────────────────────────────
//
// The class layer speaks in text. The only JSON that crosses the boundary is
// the host's `{"0": "rock", "1": "paper"}` going in and `{scores, summary}`
// coming out, so this escapes and reads flat objects and does not pretend to
// be a parser. A class that wants real JSON can parse the string itself.

/// JSON-escape a string, enough for anything a class puts in a note.
pub fn escape(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 8);
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\t' => out.push_str("\\t"),
            '\r' => out.push_str("\\r"),
            c if (c as u32) < 0x20 => out.push(' '),
            c => out.push(c),
        }
    }
    out
}

/// The string value of `"key"` in a flat JSON object, unescaped.
pub fn json_str(json: &str, key: &str) -> Option<String> {
    let needle = format!("\"{key}\"");
    let b = json.as_bytes();
    let mut at = json.find(&needle)? + needle.len();
    while at < b.len() && (b[at] as char).is_whitespace() {
        at += 1;
    }
    if at >= b.len() || b[at] != b':' {
        return None;
    }
    at += 1;
    while at < b.len() && (b[at] as char).is_whitespace() {
        at += 1;
    }
    if at >= b.len() || b[at] != b'"' {
        return None;
    }
    at += 1;
    let mut out = String::new();
    while at < b.len() {
        match b[at] {
            b'"' => return Some(out),
            b'\\' if at + 1 < b.len() => {
                at += 1;
                out.push(match b[at] {
                    b'n' => '\n',
                    b't' => '\t',
                    b'r' => '\r',
                    other => other as char,
                });
            }
            other => out.push(other as char),
        }
        at += 1;
    }
    Some(out)
}

// ── what a game is handed, and what it hands back ───────────────────────

/// One round's moves, keyed by seat. `moves.get(0)` is seat zero's answer, and
/// it is `""` when that seat did not move, timed out or failed — a game rules
/// on the empty string like any other move.
pub struct Moves {
    raw: String,
    seats: Vec<(usize, String)>,
}

impl Moves {
    #[doc(hidden)]
    pub fn parse(raw: &str) -> Moves {
        let mut seats = Vec::new();
        for seat in 0..64usize {
            if let Some(mv) = json_str(raw, &seat.to_string()) {
                seats.push((seat, mv));
            }
        }
        Moves { raw: raw.to_string(), seats }
    }

    /// What this seat played, trimmed. `""` if it did not move.
    pub fn get(&self, seat: usize) -> &str {
        self.seats
            .iter()
            .find(|(s, _)| *s == seat)
            .map(|(_, m)| m.trim())
            .unwrap_or("")
    }

    /// The same, lowercased — the usual first thing a game wants.
    pub fn lower(&self, seat: usize) -> String {
        self.get(seat).to_lowercase()
    }

    /// This seat's move read as a number, if it is one.
    pub fn number(&self, seat: usize) -> Option<i64> {
        self.get(seat).parse::<i64>().ok()
    }

    /// Which seats moved this round.
    pub fn seats(&self) -> Vec<usize> {
        self.seats.iter().map(|(s, _)| *s).collect()
    }

    pub fn is_empty(&self) -> bool {
        self.seats.is_empty()
    }

    /// The raw JSON, for a game that wants to read it itself.
    pub fn json(&self) -> &str {
        &self.raw
    }
}

/// A game's verdict on one round: which seats played legally, and a line for
/// the transcript. An illegal move is counted against that player for good —
/// it is the number that separates a model that can play from one that can
/// only talk about playing — so say so when a move was refused.
pub struct Step {
    legal: Vec<(usize, bool)>,
    note: String,
}

impl Step {
    /// Every move stood. The common case.
    pub fn ok() -> Step {
        Step { legal: Vec::new(), note: String::new() }
    }

    /// Seat by seat, in seat order.
    pub fn legal(flags: &[bool]) -> Step {
        Step {
            legal: flags.iter().enumerate().map(|(s, ok)| (s, *ok)).collect(),
            note: String::new(),
        }
    }

    /// One seat's verdict, chainable: `Step::ok().seat(0, false)`.
    pub fn seat(mut self, seat: usize, ok: bool) -> Step {
        self.legal.retain(|(s, _)| *s != seat);
        self.legal.push((seat, ok));
        self
    }

    /// A line in the transcript. Whoever reads the match later sees this.
    pub fn note(mut self, note: impl Into<String>) -> Step {
        self.note = note.into();
        self
    }

    #[doc(hidden)]
    pub fn encode(&self, state: &str) -> String {
        let flags: Vec<String> =
            self.legal.iter().map(|(s, ok)| format!("\"{s}\":{ok}")).collect();
        format!(
            "{{\"state\":\"{}\",\"legal\":{{{}}},\"note\":\"{}\"}}",
            escape(state),
            flags.join(","),
            escape(&self.note)
        )
    }
}

/// How a game ended. Higher scores are better; the ratings come out of the
/// order, so the numbers only have to be comparable, not meaningful.
pub struct Outcome {
    scores: Vec<f64>,
    summary: String,
}

impl Outcome {
    pub fn scores(scores: &[f64]) -> Outcome {
        Outcome { scores: scores.to_vec(), summary: String::new() }
    }

    /// Integer scores, which is what most games actually have.
    pub fn points(points: &[i64]) -> Outcome {
        Outcome { scores: points.iter().map(|p| *p as f64).collect(), summary: String::new() }
    }

    /// Seat `winner` takes it, everyone else does not. `None` is a draw.
    pub fn winner(winner: Option<usize>, seats: usize) -> Outcome {
        let scores = (0..seats)
            .map(|s| match winner {
                Some(w) if w == s => 1.0,
                Some(_) => 0.0,
                None => 0.5,
            })
            .collect();
        Outcome { scores, summary: String::new() }
    }

    pub fn summary(mut self, summary: impl Into<String>) -> Outcome {
        self.summary = summary.into();
        self
    }

    #[doc(hidden)]
    pub fn encode(&self) -> String {
        let list: Vec<String> = self.scores.iter().map(|s| format!("{s}")).collect();
        format!(
            "{{\"scores\":[{}],\"summary\":\"{}\"}}",
            list.join(","),
            escape(&self.summary)
        )
    }
}

// ── being forgiving about what a method returns ─────────────────────────
//
// The Python host normalises whatever `step` and `result` hand back, because
// an author writes what is natural and finds out later what the harness
// wanted. Rust has types instead of guesswork, so the same generosity is three
// small traits: return the tidy thing, or return the obvious thing, and both
// compile. Anything else is a compiler error with a name on it, which is the
// better error of the two.

/// What a `step` may return: a `Step`, a per-seat verdict, or nothing at all.
pub trait IntoStep {
    fn into_step(self) -> Step;
}

impl IntoStep for Step {
    fn into_step(self) -> Step {
        self
    }
}

/// Every move stood.
impl IntoStep for () {
    fn into_step(self) -> Step {
        Step::ok()
    }
}

/// One verdict for every seat that moved.
impl IntoStep for bool {
    fn into_step(self) -> Step {
        Step { legal: (0..2).map(|s| (s, self)).collect(), note: String::new() }
    }
}

impl IntoStep for Vec<bool> {
    fn into_step(self) -> Step {
        Step::legal(&self)
    }
}

impl<const N: usize> IntoStep for [bool; N] {
    fn into_step(self) -> Step {
        Step::legal(&self)
    }
}

/// What a `result` may return: an `Outcome`, or just the scores.
pub trait IntoOutcome {
    fn into_outcome(self) -> Outcome;
}

impl IntoOutcome for Outcome {
    fn into_outcome(self) -> Outcome {
        self
    }
}

impl IntoOutcome for Vec<f64> {
    fn into_outcome(self) -> Outcome {
        Outcome::scores(&self)
    }
}

impl IntoOutcome for Vec<i64> {
    fn into_outcome(self) -> Outcome {
        Outcome::points(&self)
    }
}

impl<const N: usize> IntoOutcome for [f64; N] {
    fn into_outcome(self) -> Outcome {
        Outcome::scores(&self)
    }
}

impl<const N: usize> IntoOutcome for [i64; N] {
    fn into_outcome(self) -> Outcome {
        Outcome::points(&self)
    }
}

/// What a `turn` may return: the seats to move, or the one seat to move.
pub trait IntoTurn {
    fn into_turn(self) -> Vec<usize>;
}

impl IntoTurn for usize {
    fn into_turn(self) -> Vec<usize> {
        vec![self]
    }
}

impl IntoTurn for i64 {
    fn into_turn(self) -> Vec<usize> {
        vec![self.max(0) as usize]
    }
}

impl IntoTurn for i32 {
    fn into_turn(self) -> Vec<usize> {
        vec![self.max(0) as usize]
    }
}

impl IntoTurn for Vec<usize> {
    fn into_turn(self) -> Vec<usize> {
        self
    }
}

impl<const N: usize> IntoTurn for [usize; N] {
    fn into_turn(self) -> Vec<usize> {
        self.to_vec()
    }
}

#[doc(hidden)]
pub fn __turn(seats: Vec<usize>) -> String {
    let list: Vec<String> = seats.iter().map(|s| s.to_string()).collect();
    format!("{{\"seats\":[{}]}}", list.join(","))
}

// ── the host ────────────────────────────────────────────────────────────

pub mod arena {
    use super::{escape, json_str, __ret, __text};

    #[link(wasm_import_module = "arena")]
    extern "C" {
        #[link_name = "log"]
        fn host_log(ptr: i32, len: i32);
        #[link_name = "random"]
        fn host_random() -> f64;
        #[link_name = "now"]
        fn host_now() -> f64;
        #[link_name = "mcp"]
        fn host_mcp(ptr: i32, len: i32) -> i64;
    }

    /// Write a line into the match transcript. This is the only output a class
    /// has, and it is read by whoever reviews the match afterwards.
    pub fn log(line: impl AsRef<str>) {
        let s = line.as_ref();
        unsafe { host_log(s.as_ptr() as i32, s.len() as i32) }
    }

    /// A random number in `[0, 1)`, from the host's PRNG.
    ///
    /// Seeded from the match seed, so it is the same sequence every time the
    /// match is replayed. There is no other entropy in here to reach for —
    /// that is what makes a transcript enough to check a result.
    pub fn random() -> f64 {
        unsafe { host_random() }
    }

    /// A random integer in `0..n`.
    pub fn below(n: usize) -> usize {
        if n == 0 {
            return 0;
        }
        ((random() * n as f64) as usize).min(n - 1)
    }

    /// Pick one. `None` only if the slice is empty.
    pub fn choice<T: Clone>(items: &[T]) -> Option<T> {
        if items.is_empty() {
            None
        } else {
            Some(items[below(items.len())].clone())
        }
    }

    /// Milliseconds since this instance started — never the wall clock, so a
    /// class that times itself still replays identically.
    pub fn elapsed_ms() -> f64 {
        unsafe { host_now() }
    }

    /// Call a tool on an MCP server, and wait for the answer.
    ///
    /// This is the one thing in here that leaves the sandbox, and it does not
    /// leave it the way a socket would: the class has no network, so it hands
    /// the host a request and the host makes the call. Which servers exist,
    /// which are allowed and what credentials they use are all decided outside
    /// this module — see `mcp_servers` on the arena. A call to a server the
    /// match did not allow comes back as an error, not as a connection.
    ///
    /// `arguments` is JSON. The reply is the tool's result as JSON text, or
    /// `{"error": "..."}` — a class must be able to lose this call and carry
    /// on, because over a network it will.
    ///
    /// ```ignore
    /// let reply = arena::mcp("arena", "leaderboard", "{\"limit\": 3}");
    /// ```
    pub fn mcp(server: &str, tool: &str, arguments: &str) -> String {
        let args = if arguments.trim().is_empty() { "{}" } else { arguments };
        let req = format!(
            "{{\"server\":\"{}\",\"tool\":\"{}\",\"arguments\":{}}}",
            escape(server),
            escape(tool),
            args
        );
        let packed = unsafe { host_mcp(req.as_ptr() as i32, req.len() as i32) };
        let ptr = (packed >> 32) as i32;
        let len = (packed & 0xffff_ffff) as i32;
        __text(ptr, len).to_string()
    }

    /// `mcp`, with the error already read out of it: `Ok(text)` or the reason.
    pub fn ask(server: &str, tool: &str, arguments: &str) -> Result<String, String> {
        let reply = mcp(server, tool, arguments);
        match json_str(&reply, "error") {
            Some(e) => Err(e),
            None => Ok(reply),
        }
    }

    /// The tools a server offers, as JSON text. What a class calls first when
    /// it does not already know what it is talking to.
    pub fn tools(server: &str) -> String {
        mcp(server, "__tools__", "{}")
    }
}

/// `log!("seat {seat} played {mv}")` — `println!` for the transcript.
///
/// Declared here, textually above the module the class is compiled into, which
/// is what puts it in scope there. `arena::log` is the same thing without the
/// formatting.
macro_rules! log {
    ($($arg:tt)*) => { arena::log(format!($($arg)*)) };
}
