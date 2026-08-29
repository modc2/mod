//! Perfect nim, in fifteen lines of arithmetic.
//!
//! The reference opponent for `nim-rs`, and the reason that game is worth
//! having: against this, a loss is a mistake. Leave the other seat a multiple
//! of four and there is nothing they can do about it; when you cannot, take
//! one and hope they slip.
//!
//! It reads the position out of the view rather than being told it, because
//! that is all any player here gets — the same text a language model in the
//! same seat would be handed. That is what makes the comparison mean anything.

/// Perfect Nim: leaves a multiple of four whenever it can.
pub struct Perfect;

impl Perfect {
    pub const NAME: &'static str = "bot-perfect";

    pub fn play(&mut self, view: &str, seat: usize) -> String {
        let _ = seat;
        let left = Self::stones(view).unwrap_or(0);
        if left <= 0 {
            return "1".to_string();
        }
        // Take the last stone only when it is the only stone — losing on the
        // spot beats leaving a position that loses anyway.
        let want = (left - 1) % 4;
        let take = if want == 0 { 1 } else { want.min(left) };
        arena::log(format!("{left} left, taking {take}"));
        take.to_string()
    }

    /// The number in "Nim. 21 stones left." — the first integer in the view.
    fn stones(view: &str) -> Option<i64> {
        let mut digits = String::new();
        for c in view.chars() {
            if c.is_ascii_digit() {
                digits.push(c);
            } else if !digits.is_empty() {
                return digits.parse().ok();
            }
        }
        digits.parse().ok()
    }
}
