// The arena ABI, in Rust. `include!`d by every example — see build.sh.
//
// The whole contract: export `alloc(i32) -> i32` so the host can write UTF-8
// into your memory, and return `(ptr << 32) | len` from anything that hands a
// string back. Nothing else. No bindgen, no glue crate, no build step beyond
// `rustc --target wasm32-unknown-unknown`.
//
// Memory here is deliberately naive: `alloc` leaks and nothing is ever freed.
// A match is a few hundred short strings and the instance is thrown away when
// it ends, so an allocator would be more code than the problem is worth. A
// game meant to run for millions of turns should bring its own.

/// Somewhere for the host to write. Called before every string that comes in.
#[no_mangle]
pub extern "C" fn alloc(len: i32) -> i32 {
    let mut buf: Vec<u8> = Vec::with_capacity(len.max(1) as usize);
    let ptr = buf.as_mut_ptr();
    core::mem::forget(buf);
    ptr as i32
}

/// A string the host just wrote. Valid until the instance goes away.
#[allow(dead_code)]
fn text(ptr: i32, len: i32) -> &'static str {
    if len <= 0 {
        return "";
    }
    unsafe {
        let bytes = core::slice::from_raw_parts(ptr as *const u8, len as usize);
        core::str::from_utf8(bytes).unwrap_or("")
    }
}

/// Hand a string back: `(ptr << 32) | len`.
#[allow(dead_code)]
fn ret(s: String) -> i64 {
    let bytes = s.into_bytes();
    let ptr = bytes.as_ptr() as i64;
    let len = bytes.len() as i64;
    core::mem::forget(bytes);
    (ptr << 32) | len
}

/// The value of `"<key>"` in a flat JSON object. The moves the host sends are
/// `{"0": "rock", "1": "paper"}` and that is the only JSON a module has to
/// read, so this handles that shape and does not pretend to be a parser.
#[allow(dead_code)]
fn json_get(json: &str, key: &str) -> Option<String> {
    let needle = format!("\"{key}\"");
    let mut at = json.find(&needle)? + needle.len();
    let b = json.as_bytes();
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

/// One seat's move out of the moves object.
#[allow(dead_code)]
fn move_of(moves: &str, seat: usize) -> String {
    json_get(moves, &seat.to_string()).unwrap_or_default()
}

/// Which seats moved this round — for a game that has to work out whose turn
/// it was from the moves alone.
#[allow(dead_code)]
fn seats_in(moves: &str, max: usize) -> Vec<usize> {
    (0..max).filter(|s| json_get(moves, &s.to_string()).is_some()).collect()
}

/// JSON string escaping — enough for the text a game puts in a note.
#[allow(dead_code)]
fn esc(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 8);
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\t' => out.push_str("\\t"),
            '\r' => {}
            c if (c as u32) < 0x20 => out.push(' '),
            c => out.push(c),
        }
    }
    out
}

/// The `{state, legal, note}` a `game_step` returns.
#[allow(dead_code)]
fn step_result(state: &str, legal: &[(usize, bool)], note: &str) -> i64 {
    let flags: Vec<String> = legal
        .iter()
        .map(|(seat, ok)| format!("\"{seat}\":{ok}"))
        .collect();
    ret(format!(
        "{{\"state\":\"{}\",\"legal\":{{{}}},\"note\":\"{}\"}}",
        esc(state),
        flags.join(","),
        esc(note)
    ))
}

/// The `{scores, summary}` a `game_result` returns.
#[allow(dead_code)]
fn game_scores(scores: &[f64], summary: &str) -> i64 {
    let list: Vec<String> = scores.iter().map(|s| format!("{s}")).collect();
    ret(format!(
        "{{\"scores\":[{}],\"summary\":\"{}\"}}",
        list.join(","),
        esc(summary)
    ))
}
