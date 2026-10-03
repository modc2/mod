// A player that reads the `Legal moves:` line and picks one at random.
//
// Every game in the pack prints that line, which is a convention worth
// keeping: it means one bot can play all of them, and it gives a model an
// unambiguous list to choose from instead of a rule to infer.
//
// This is the floor. A player that cannot beat random is not playing — and
// having the floor entered in the arena is what makes an Elo number mean
// something, because a rating is only ever relative to the field.

include!("abi.rs");

#[link(wasm_import_module = "modarena")]
extern "C" {
    /// Seeded from the match seed, so a replay picks the same moves.
    fn random() -> f64;
    fn log(ptr: i32, len: i32);
}

fn note(msg: &str) {
    unsafe { log(msg.as_ptr() as i32, msg.len() as i32) }
}

fn options(view: &str) -> Vec<String> {
    for line in view.lines() {
        let l = line.trim();
        for tag in ["Legal moves:", "Play one of:", "Options:"] {
            if let Some(rest) = l.strip_prefix(tag) {
                return rest
                    .split(',')
                    .map(|s| s.trim().trim_matches(['`', '"', '.']).trim().to_string())
                    .filter(|s| !s.is_empty())
                    .collect();
            }
        }
    }
    Vec::new()
}

#[no_mangle]
pub extern "C" fn play(vp: i32, vl: i32, _seat: i32) -> i64 {
    let opts = options(text(vp, vl));
    if opts.is_empty() {
        // Better to say nothing than to guess: an empty move is recorded as a
        // move the game refused, which is the truth.
        note("no `Legal moves:` line in the view");
        return ret(String::new());
    }
    let pick = (unsafe { random() } * opts.len() as f64) as usize;
    ret(opts[pick.min(opts.len() - 1)].clone())
}
