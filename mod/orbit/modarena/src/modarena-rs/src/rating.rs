//! Elo for matches with more than two seats.
//!
//! A match is scored as every pairing inside it: each seat plays every other
//! seat once, the pair's result comes from their two game scores, and the
//! seat's rating change is the mean of those pairings. With two seats this is
//! ordinary Elo; with five it is still ordinary Elo, five times, averaged — so
//! finishing second in a field of five is worth more than finishing last, and
//! beating a strong field is worth more than beating a weak one.
//!
//! A match only rates when two or more seats were filled. One player against
//! the game itself is practice, however well it went.

/// How far one match can move a rating. 24 is the usual compromise: fast
/// enough that a new player finds its level in ~20 matches, slow enough that
/// one lucky seed does not crown anybody.
pub const K: f64 = 24.0;

pub fn expected(a: f64, b: f64) -> f64 {
    1.0 / (1.0 + 10f64.powf((b - a) / 400.0))
}

/// The rating change for each seat, in the order given.
pub fn deltas(elos: &[f64], scores: &[f64]) -> Vec<f64> {
    let n = elos.len();
    if n < 2 || scores.len() != n {
        return vec![0.0; n];
    }
    let mut out = vec![0.0; n];
    for i in 0..n {
        let mut sum = 0.0;
        for j in 0..n {
            if i == j {
                continue;
            }
            let actual = match scores[i].partial_cmp(&scores[j]) {
                Some(std::cmp::Ordering::Greater) => 1.0,
                Some(std::cmp::Ordering::Less) => 0.0,
                _ => 0.5,
            };
            sum += K * (actual - expected(elos[i], elos[j]));
        }
        out[i] = sum / (n - 1) as f64;
    }
    out
}

/// win | draw | loss for one seat: the top score wins outright, a tie at the
/// top is a draw for everyone in it, everything below is a loss.
pub fn outcome(score: f64, all: &[f64]) -> &'static str {
    let best = all.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    if score < best {
        return "loss";
    }
    if all.iter().filter(|s| **s >= best).count() > 1 {
        "draw"
    } else {
        "win"
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn equal_ratings_split_the_odds() {
        assert!((expected(1200.0, 1200.0) - 0.5).abs() < 1e-9);
        assert!(expected(1600.0, 1200.0) > 0.9);
    }

    #[test]
    fn a_win_between_equals_moves_half_of_k() {
        let d = deltas(&[1200.0, 1200.0], &[1.0, 0.0]);
        assert!((d[0] - K / 2.0).abs() < 1e-9, "{d:?}");
        assert!((d[1] + K / 2.0).abs() < 1e-9, "{d:?}");
    }

    #[test]
    fn a_draw_between_equals_moves_nothing() {
        let d = deltas(&[1200.0, 1200.0], &[0.5, 0.5]);
        assert!(d.iter().all(|x| x.abs() < 1e-9), "{d:?}");
    }

    #[test]
    fn beating_a_favourite_is_worth_more() {
        let underdog = deltas(&[1000.0, 1600.0], &[1.0, 0.0])[0];
        let favourite = deltas(&[1600.0, 1000.0], &[1.0, 0.0])[0];
        assert!(underdog > favourite * 3.0, "{underdog} vs {favourite}");
    }

    #[test]
    fn a_match_is_zero_sum_across_its_seats() {
        let d = deltas(&[1200.0, 1400.0, 900.0, 1500.0], &[3.0, 1.0, 2.0, 0.0]);
        assert!(d.iter().sum::<f64>().abs() < 1e-9, "{d:?}");
    }

    #[test]
    fn second_of_five_beats_last_of_five() {
        let elos = [1200.0; 5];
        let d = deltas(&elos, &[5.0, 4.0, 3.0, 2.0, 1.0]);
        assert!(d[1] > d[4]);
        assert!(d[0] > d[1]);
    }

    #[test]
    fn one_seat_is_practice() {
        assert_eq!(deltas(&[1200.0], &[1.0]), vec![0.0]);
    }

    #[test]
    fn ties_at_the_top_are_draws_for_everyone_in_them() {
        let scores = [2.0, 2.0, 1.0];
        assert_eq!(outcome(2.0, &scores), "draw");
        assert_eq!(outcome(1.0, &scores), "loss");
        assert_eq!(outcome(3.0, &[3.0, 1.0]), "win");
    }
}
