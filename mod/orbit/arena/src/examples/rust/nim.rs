//! Nim, in Rust, as a class.
//!
//! Twenty-one stones on the table. Take one, two or three; whoever is made to
//! take the last one loses. It is solved — leave your opponent a multiple of
//! four and you win from there — which is the point of having it here: a bot
//! that plays it perfectly exists, so a loss is a mistake rather than variance.
//!
//! Nothing in this file knows it will become wasm. There is no `#[no_mangle]`,
//! no pointer arithmetic and no ABI: a struct, an impl block, four methods.
//! The arena compiles it on upload, wraps it in the shim that does all of
//! that, and the wasm that falls out plays in a browser tab.

/// Take one to three stones; taking the last one loses.
pub struct Nim {
    left: i64,
    turn_no: usize,
    took_last: Option<usize>,
    refused: [i64; 2],
}

impl Nim {
    pub const NAME: &'static str = "nim-rs";
    pub const DESCRIPTION: &'static str = "Twenty-one stones, take one to three, last one loses.";
    pub const PLAYERS: usize = 2;
    pub const MAX_TURNS: usize = 40;

    pub fn new(seed: i64) -> Nim {
        // Between 18 and 23 stones, so the winning line is not the same every
        // match and a bot that memorised one opening has learned nothing.
        Nim { left: 18 + seed.rem_euclid(6), turn_no: 0, took_last: None, refused: [0, 0] }
    }

    pub fn view(&self, seat: usize) -> String {
        let most = self.left.min(3);
        format!(
            "Nim. {} stones left. You are seat {seat}; whoever takes the last stone loses.\n\
             Legal moves: {}",
            self.left,
            (1..=most).map(|n| n.to_string()).collect::<Vec<_>>().join(", "),
        )
    }

    pub fn turn(&self) -> Vec<usize> {
        vec![self.turn_no % 2]
    }

    pub fn step(&mut self, moves: &Moves) -> Step {
        let seat = self.turn_no % 2;
        self.turn_no += 1;
        match moves.number(seat) {
            Some(n) if n >= 1 && n <= 3 && n <= self.left => {
                self.left -= n;
                if self.left == 0 {
                    self.took_last = Some(seat);
                }
                Step::ok().note(format!("seat {seat} took {n}, {} left", self.left))
            }
            other => {
                // A refused move costs the turn and is counted against that
                // seat for good — which is most of what a rating here measures.
                self.refused[seat] += 1;
                Step::ok().seat(seat, false).note(match other {
                    Some(n) => format!("seat {seat} tried to take {n}"),
                    None => format!("seat {seat} played {:?}, which is not a number", moves.get(seat)),
                })
            }
        }
    }

    pub fn done(&self) -> bool {
        self.left <= 0 || self.turn_no >= Self::MAX_TURNS
    }

    pub fn result(&self) -> Outcome {
        match self.took_last {
            Some(loser) => Outcome::winner(Some(1 - loser), 2)
                .summary(format!("seat {loser} took the last stone")),
            // Nobody finished it: the seat that broke the rules less often
            // takes it, and if neither did it is a draw.
            None => {
                let summary = format!("{} stones left when the clock ran out", self.left);
                if self.refused[0] == self.refused[1] {
                    Outcome::winner(None, 2).summary(summary)
                } else {
                    let better = if self.refused[0] < self.refused[1] { 0 } else { 1 };
                    Outcome::winner(Some(better), 2).summary(summary)
                }
            }
        }
    }
}
