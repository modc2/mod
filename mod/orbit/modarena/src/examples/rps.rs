// Rock, paper, scissors — best of five, both seats moving at once.
//
// The simplest game that is not trivial to assess: it needs simultaneous
// moves, so it exercises `game_turn` returning more than one seat, and it
// needs each seat to see only its own side, so it exercises the fact that
// `game_view` is asked per seat.

include!("abi.rs");

const ROUNDS: i32 = 5;

/// State is `round|score0|score1` — small enough to read in a transcript,
/// which is the point of keeping state a string.
fn parse(state: &str) -> (i32, i32, i32) {
    let mut it = state.split('|').map(|p| p.trim().parse::<i32>().unwrap_or(0));
    (it.next().unwrap_or(0), it.next().unwrap_or(0), it.next().unwrap_or(0))
}

/// `rock`, `R`, `1` and `✊` all mean the same thing. Being strict about
/// spelling would measure typing, not play.
fn throw_of(mv: &str) -> Option<u8> {
    let m = mv.trim().to_lowercase();
    match m.chars().next()? {
        'r' | '1' => Some(0),
        'p' | '2' => Some(1),
        's' | '3' => Some(2),
        _ => None,
    }
}

fn name_of(t: u8) -> &'static str {
    ["rock", "paper", "scissors"][t as usize % 3]
}

#[no_mangle]
pub extern "C" fn game_info() -> i64 {
    ret(format!(
        "{{\"name\":\"rock-paper-scissors\",\"description\":\"Best of {ROUNDS}, both seats \
          throwing at once.\",\"min_players\":2,\"max_players\":2,\"max_turns\":{ROUNDS}}}"
    ))
}

#[no_mangle]
pub extern "C" fn game_init(_seed: i32) -> i64 {
    ret("0|0|0".to_string())
}

/// Both seats, every round — that is what makes this simultaneous.
#[no_mangle]
pub extern "C" fn game_turn(_sp: i32, _sl: i32) -> i64 {
    ret("{\"seats\":[0,1]}".to_string())
}

#[no_mangle]
pub extern "C" fn game_view(sp: i32, sl: i32, seat: i32) -> i64 {
    let (round, s0, s1) = parse(text(sp, sl));
    let (mine, theirs) = if seat == 0 { (s0, s1) } else { (s1, s0) };
    ret(format!(
        "Rock, paper, scissors — round {} of {ROUNDS}.\n\
         Score: you {mine}, opponent {theirs}.\n\
         You and your opponent throw at the same time; neither of you can see the other's throw.\n\
         Legal moves: rock, paper, scissors",
        round + 1
    ))
}

#[no_mangle]
pub extern "C" fn game_step(sp: i32, sl: i32, mp: i32, ml: i32) -> i64 {
    let (round, mut s0, mut s1) = parse(text(sp, sl));
    let moves = text(mp, ml);
    let a = throw_of(&move_of(moves, 0));
    let b = throw_of(&move_of(moves, 1));

    let note = match (a, b) {
        (Some(x), Some(y)) => {
            // 0 rock, 1 paper, 2 scissors: x beats y when x is one ahead of y.
            let outcome = (3 + x as i32 - y as i32) % 3;
            match outcome {
                1 => s0 += 1,
                2 => s1 += 1,
                _ => {}
            }
            format!(
                "{} vs {} — {}",
                name_of(x),
                name_of(y),
                match outcome {
                    1 => "seat 0 takes the round",
                    2 => "seat 1 takes the round",
                    _ => "a tie",
                }
            )
        }
        // A throw nobody can read is not a throw. The other seat takes the
        // round; if neither threw, nobody does.
        (Some(_), None) => {
            s0 += 1;
            "seat 1 did not throw".to_string()
        }
        (None, Some(_)) => {
            s1 += 1;
            "seat 0 did not throw".to_string()
        }
        (None, None) => "neither seat threw".to_string(),
    };

    step_result(
        &format!("{}|{s0}|{s1}", round + 1),
        &[(0, a.is_some()), (1, b.is_some())],
        &note,
    )
}

#[no_mangle]
pub extern "C" fn game_done(sp: i32, sl: i32) -> i32 {
    let (round, s0, s1) = parse(text(sp, sl));
    // Over when the rounds run out, or when the lead is unassailable.
    let left = ROUNDS - round;
    ((round >= ROUNDS) || (s0 - s1).abs() > left) as i32
}

#[no_mangle]
pub extern "C" fn game_result(sp: i32, sl: i32) -> i64 {
    let (round, s0, s1) = parse(text(sp, sl));
    let summary = if s0 == s1 {
        format!("{s0}–{s1} after {round} rounds — a draw")
    } else if s0 > s1 {
        format!("seat 0 wins {s0}–{s1}")
    } else {
        format!("seat 1 wins {s1}–{s0}")
    };
    game_scores(&[s0 as f64, s1 as f64], &summary)
}
