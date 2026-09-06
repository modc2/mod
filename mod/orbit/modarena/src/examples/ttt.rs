// Tic-tac-toe — the assessment workhorse.
//
// It is solved, so a perfect player is available as a fixed reference (see
// bot_ttt.rs) and anything that loses to it played badly rather than
// unluckily. It also has illegal moves, which is the single most telling
// measurement for a model: playing an occupied square is not bad play, it is
// not having read the board.
//
// A move that the game refuses costs the seat its turn. That rule is stated
// in the view, so nobody is caught out by it, and it means a player that
// cannot produce a legal move loses on the board rather than hanging the match.

include!("abi.rs");

const LINES: [[usize; 3]; 8] = [
    [0, 1, 2], [3, 4, 5], [6, 7, 8],
    [0, 3, 6], [1, 4, 7], [2, 5, 8],
    [0, 4, 8], [2, 4, 6],
];

fn board_of(state: &str) -> Vec<char> {
    let mut b: Vec<char> = state.chars().take(9).collect();
    while b.len() < 9 {
        b.push('.');
    }
    b
}

fn mark(seat: usize) -> char {
    if seat == 0 { 'X' } else { 'O' }
}

fn winner(b: &[char]) -> Option<char> {
    LINES
        .iter()
        .find(|l| b[l[0]] != '.' && b[l[0]] == b[l[1]] && b[l[1]] == b[l[2]])
        .map(|l| b[l[0]])
}

fn full(b: &[char]) -> bool {
    b.iter().all(|c| *c != '.')
}

/// `5`, `b2` and `B2` are all the middle. Cell numbers read left to right,
/// top to bottom, the way they are printed in the view.
fn cell_of(mv: &str) -> Option<usize> {
    let m: String = mv.trim().to_lowercase().chars().filter(|c| !c.is_whitespace()).collect();
    if let Ok(n) = m.parse::<usize>() {
        return (1..=9).contains(&n).then(|| n - 1);
    }
    let mut ch = m.chars();
    let (a, b) = (ch.next()?, ch.next()?);
    let (col, row) = match (a, b) {
        ('a'..='c', '1'..='3') => (a as usize - 'a' as usize, b as usize - '1' as usize),
        ('1'..='3', 'a'..='c') => (b as usize - 'a' as usize, a as usize - '1' as usize),
        _ => return None,
    };
    Some(row * 3 + col)
}

#[no_mangle]
pub extern "C" fn game_info() -> i64 {
    ret("{\"name\":\"tic-tac-toe\",\"description\":\"Seat 0 is X and moves first. An illegal \
         move costs you the turn.\",\"min_players\":2,\"max_players\":2,\"max_turns\":20}"
        .to_string())
}

#[no_mangle]
pub extern "C" fn game_init(_seed: i32) -> i64 {
    ret(".........".to_string())
}

#[no_mangle]
pub extern "C" fn game_view(sp: i32, sl: i32, seat: i32) -> i64 {
    let b = board_of(text(sp, sl));
    let cell = |i: usize| if b[i] == '.' { char::from(b'1' + i as u8) } else { b[i] };
    let open: Vec<String> = (0..9).filter(|i| b[*i] == '.').map(|i| (i + 1).to_string()).collect();

    ret(format!(
        "Tic-tac-toe. You are {}.\n\n\
         \x20 {} {} {}\n\x20 {} {} {}\n\x20 {} {} {}\n\n\
         Board: {}\n\
         Legal moves: {}\n\
         Reply with one cell number. An illegal move costs you the turn.",
        mark(seat as usize),
        cell(0), cell(1), cell(2),
        cell(3), cell(4), cell(5),
        cell(6), cell(7), cell(8),
        b.iter().collect::<String>(),
        open.join(", "),
    ))
}

#[no_mangle]
pub extern "C" fn game_step(sp: i32, sl: i32, mp: i32, ml: i32) -> i64 {
    let mut b = board_of(text(sp, sl));
    let moves = text(mp, ml);
    let seats = seats_in(moves, 2);
    let Some(&seat) = seats.first() else {
        return step_result(&b.iter().collect::<String>(), &[], "no move was offered");
    };

    let raw = move_of(moves, seat);
    match cell_of(&raw) {
        Some(i) if b[i] == '.' => {
            b[i] = mark(seat);
            step_result(
                &b.iter().collect::<String>(),
                &[(seat, true)],
                &format!("seat {seat} plays {}", i + 1),
            )
        }
        Some(i) => step_result(
            &b.iter().collect::<String>(),
            &[(seat, false)],
            &format!("cell {} is already {} — seat {seat} loses the turn", i + 1, b[i]),
        ),
        None => step_result(
            &b.iter().collect::<String>(),
            &[(seat, false)],
            &format!("{:?} is not a cell — seat {seat} loses the turn", raw.chars().take(20).collect::<String>()),
        ),
    }
}

#[no_mangle]
pub extern "C" fn game_done(sp: i32, sl: i32) -> i32 {
    let b = board_of(text(sp, sl));
    (winner(&b).is_some() || full(&b)) as i32
}

#[no_mangle]
pub extern "C" fn game_result(sp: i32, sl: i32) -> i64 {
    let b = board_of(text(sp, sl));
    match winner(&b) {
        Some('X') => game_scores(&[1.0, 0.0], "X wins"),
        Some(_) => game_scores(&[0.0, 1.0], "O wins"),
        // A board that is neither won nor full ran out of turns — both seats
        // spent them on moves the game would not take.
        None if full(&b) => game_scores(&[0.5, 0.5], "drawn"),
        None => game_scores(&[0.5, 0.5], "unfinished — the turns ran out"),
    }
}
