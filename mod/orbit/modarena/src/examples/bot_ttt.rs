// A tic-tac-toe player that never loses — full minimax over a nine-cell board.
//
// This is the reference every other player is measured against. Tic-tac-toe is
// solved, so a draw with this bot is the best result available and a loss to
// it is an error someone made, not a run of bad luck. That turns a fuzzy
// question ("is this model any good at games?") into a sharp one: how many
// moves did it take before it made the mistake?
//
// It reads the board off the `Board:` line the game prints. That information
// is public in tic-tac-toe — nothing here sees anything a seat is not shown.

include!("abi.rs");

const LINES: [[usize; 3]; 8] = [
    [0, 1, 2], [3, 4, 5], [6, 7, 8],
    [0, 3, 6], [1, 4, 7], [2, 5, 8],
    [0, 4, 8], [2, 4, 6],
];

fn field(view: &str, tag: &str) -> String {
    view.lines()
        .find_map(|l| l.trim().strip_prefix(tag).map(|r| r.trim().to_string()))
        .unwrap_or_default()
}

fn winner(b: &[u8; 9]) -> u8 {
    for l in LINES {
        if b[l[0]] != 0 && b[l[0]] == b[l[1]] && b[l[1]] == b[l[2]] {
            return b[l[0]];
        }
    }
    0
}

/// Score for `me` to move, negamax style: a win now is worth more than a win
/// later, so the bot finishes a game it has already won instead of shuffling.
fn best(b: &mut [u8; 9], me: u8, depth: i32) -> (i32, usize) {
    let w = winner(b);
    if w != 0 {
        return (if w == me { 10 - depth } else { depth - 10 }, 9);
    }
    if b.iter().all(|c| *c != 0) {
        return (0, 9);
    }
    let other = 3 - me;
    let mut top = (-100, 9);
    for i in 0..9 {
        if b[i] != 0 {
            continue;
        }
        b[i] = me;
        let (score, _) = best(b, other, depth + 1);
        b[i] = 0;
        if -score > top.0 {
            top = (-score, i);
        }
    }
    top
}

#[no_mangle]
pub extern "C" fn play(vp: i32, vl: i32, _seat: i32) -> i64 {
    let view = text(vp, vl);

    // "You are X." — the trailing full stop is the game's, not ours.
    let me = match field(view, "Tic-tac-toe. You are").trim_end_matches('.') {
        "O" => 2u8,
        _ => 1u8,
    };

    let raw = field(view, "Board:");
    let mut board = [0u8; 9];
    for (i, c) in raw.chars().take(9).enumerate() {
        board[i] = match c {
            'X' | 'x' => 1,
            'O' | 'o' => 2,
            _ => 0,
        };
    }

    let (_, cell) = best(&mut board, me, 0);
    if cell > 8 {
        // Nothing to play — the board is finished. Say nothing rather than
        // something wrong.
        return ret(String::new());
    }
    ret((cell + 1).to_string())
}
