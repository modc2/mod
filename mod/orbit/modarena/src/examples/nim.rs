// Nim — twenty-one stones, take one to three, taking the last stone wins.
//
// A third game with a third shape: the move is a number rather than a word or
// a square, and there is a short arithmetic rule that decides it (leave a
// multiple of four). Cheap to play, and it separates a player that reasons
// from one that pattern-matches on board games it has seen.

include!("abi.rs");

const STONES: i32 = 21;
const MAX_TAKE: i32 = 3;

fn parse(state: &str) -> (i32, i32) {
    let mut it = state.split('|');
    let stones = it.next().and_then(|s| s.trim().parse().ok()).unwrap_or(STONES);
    let last = it.next().and_then(|s| s.trim().parse().ok()).unwrap_or(-1);
    (stones, last)
}

/// The first number anywhere in the reply — `2`, `take 2` and `I'll take 2
/// stones` are the same move.
fn take_of(mv: &str) -> Option<i32> {
    let mut digits = String::new();
    for c in mv.chars() {
        if c.is_ascii_digit() {
            digits.push(c);
        } else if !digits.is_empty() {
            break;
        }
    }
    digits.parse().ok()
}

#[no_mangle]
pub extern "C" fn game_info() -> i64 {
    ret(format!(
        "{{\"name\":\"nim-{STONES}\",\"description\":\"Take 1 to {MAX_TAKE} stones. \
          Whoever takes the last stone wins.\",\"min_players\":2,\"max_players\":2,\
          \"max_turns\":{}}}",
        STONES * 2
    ))
}

#[no_mangle]
pub extern "C" fn game_init(_seed: i32) -> i64 {
    ret(format!("{STONES}|-1"))
}

#[no_mangle]
pub extern "C" fn game_view(sp: i32, sl: i32, _seat: i32) -> i64 {
    let (stones, _) = parse(text(sp, sl));
    let take = MAX_TAKE.min(stones);
    let legal: Vec<String> = (1..=take).map(|n| n.to_string()).collect();
    ret(format!(
        "Nim. {stones} stone{} left.\n\
         Take 1 to {take}. Whoever takes the last stone wins.\n\
         Legal moves: {}\n\
         Reply with a number.",
        if stones == 1 { "" } else { "s" },
        legal.join(", "),
    ))
}

#[no_mangle]
pub extern "C" fn game_step(sp: i32, sl: i32, mp: i32, ml: i32) -> i64 {
    let (stones, last) = parse(text(sp, sl));
    let moves = text(mp, ml);
    let seats = seats_in(moves, 2);
    let Some(&seat) = seats.first() else {
        return step_result(&format!("{stones}|{last}"), &[], "no move was offered");
    };

    let cap = MAX_TAKE.min(stones);
    match take_of(&move_of(moves, seat)) {
        Some(n) if (1..=cap).contains(&n) => step_result(
            &format!("{}|{}", stones - n, if stones - n == 0 { seat as i32 } else { -1 }),
            &[(seat, true)],
            &format!("seat {seat} takes {n}, leaving {}", stones - n),
        ),
        // Out of range or unreadable: the turn is spent, the pile is not.
        other => step_result(
            &format!("{stones}|{last}"),
            &[(seat, false)],
            &match other {
                Some(n) => format!("seat {seat} cannot take {n} — only 1 to {cap} — and loses the turn"),
                None => format!("seat {seat} gave no number and loses the turn"),
            },
        ),
    }
}

#[no_mangle]
pub extern "C" fn game_done(sp: i32, sl: i32) -> i32 {
    (parse(text(sp, sl)).0 <= 0) as i32
}

#[no_mangle]
pub extern "C" fn game_result(sp: i32, sl: i32) -> i64 {
    let (stones, last) = parse(text(sp, sl));
    match last {
        0 => game_scores(&[1.0, 0.0], "seat 0 took the last stone"),
        1 => game_scores(&[0.0, 1.0], "seat 1 took the last stone"),
        _ => game_scores(&[0.5, 0.5], &format!("unfinished — {stones} stones still on the table")),
    }
}
