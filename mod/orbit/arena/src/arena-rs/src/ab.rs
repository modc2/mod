//! A/B experiments — two agents, the same games, one report.
//!
//! The arena already rates everyone against everyone, but a rating is a slow
//! answer to a fast question: *which of these two is better, right now?* An
//! experiment answers it directly. Pick two entered players — the front door
//! is two agents of the agent mod protocol — and the arena plays them head to
//! head on the same games, swapping who sits first every match so seat
//! advantage cancels out, through the exact same match loop everything else
//! uses. Every match is rated and lands on the boards; the report is the
//! difference, counted: wins, scores, illegal moves, timeouts, pace.
//!
//! Nothing here executes anything — a match is `run_match`, the node runner,
//! the same computation as always. This file only chooses the fixtures and
//! adds the columns up.

use crate::{blobs, mcp, store};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::fs;
use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};

/// Matches per game when the caller does not say.
const DEFAULT_COUNT: u64 = 2;
/// A run of agent seats is a model call per move — cap the bill.
const MAX_MATCHES: u64 = 60;
/// Reports kept on disk. An experiment is a conclusion, not an archive.
const KEEP_REPORTS: usize = 40;

// ── the report ───────────────────────────────────────────────────────────

/// One side's running totals, across every finished match.
#[derive(Serialize, Deserialize, Clone, Default, Debug)]
pub struct Tally {
    pub wins: u64,
    pub draws: u64,
    pub losses: u64,
    pub score_sum: f64,
    pub moves: u64,
    pub illegal: u64,
    pub timeouts: u64,
    pub ms: u64,
}

impl Tally {
    fn matches(&self) -> u64 {
        self.wins + self.draws + self.losses
    }

    fn card(&self) -> Value {
        let n = self.matches().max(1) as f64;
        let moves = self.moves.max(1) as f64;
        json!({
            "wins": self.wins, "draws": self.draws, "losses": self.losses,
            "matches": self.matches(),
            "avg_score": store::round3(self.score_sum / n),
            "moves": self.moves,
            "illegal": self.illegal,
            "illegal_rate": store::round3(self.illegal as f64 / moves),
            "timeouts": self.timeouts,
            "avg_move_ms": if self.moves == 0 { 0 } else { self.ms / self.moves },
        })
    }
}

/// Who is being compared. The elo pair is a snapshot either side of the run,
/// so the report can say what the experiment itself moved.
#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Side {
    pub id: String,
    pub name: String,
    pub kind: String,
    pub elo_start: f64,
    pub elo_end: f64,
}

#[derive(Serialize, Deserialize, Clone, Default, Debug)]
pub struct GameRow {
    pub game: String,
    pub game_name: String,
    pub a: Tally,
    pub b: Tally,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Report {
    pub id: String,
    pub a: Side,
    pub b: Side,
    /// Matches per game, seat-swapped: even indices seat A first, odd seat B.
    pub count: u64,
    pub games: Vec<GameRow>,
    /// running | done | error — `error` only when nothing finished at all.
    pub status: String,
    /// What is being played right now, for a console mid-poll.
    pub current: String,
    /// The last runner complaint, kept even when later matches recovered.
    pub error: String,
    pub planned: u64,
    pub played: u64,
    /// Matches the runner could not finish. Void, not counted, but not hidden.
    pub voids: u64,
    /// Every recorded match id, so the report is auditable move by move.
    pub matches: Vec<String>,
    pub a_total: Tally,
    pub b_total: Tally,
    pub verdict: String,
    pub created: u64,
    pub finished: u64,
}

impl Report {
    pub fn card(&self) -> Value {
        json!({
            "id": self.id,
            "status": self.status,
            "current": self.current,
            "error": self.error,
            "a": { "id": self.a.id, "name": self.a.name, "kind": self.a.kind,
                   "elo_start": store::round1(self.a.elo_start), "elo_end": store::round1(self.a.elo_end),
                   "total": self.a_total.card() },
            "b": { "id": self.b.id, "name": self.b.name, "kind": self.b.kind,
                   "elo_start": store::round1(self.b.elo_start), "elo_end": store::round1(self.b.elo_end),
                   "total": self.b_total.card() },
            "count": self.count,
            "planned": self.planned,
            "played": self.played,
            "voids": self.voids,
            "games": self.games.iter().map(|g| json!({
                "game": g.game, "game_name": g.game_name,
                "a": g.a.card(), "b": g.b.card(),
            })).collect::<Vec<_>>(),
            "matches": self.matches,
            "verdict": self.verdict,
            "created": self.created,
            "finished": self.finished,
        })
    }

    /// A row for the list — the conclusion without the working.
    fn brief(&self) -> Value {
        json!({
            "id": self.id, "status": self.status,
            "a": self.a.name, "b": self.b.name,
            "games": self.games.len(), "count": self.count,
            "played": self.played, "planned": self.planned, "voids": self.voids,
            "a_wins": self.a_total.wins, "b_wins": self.b_total.wins, "draws": self.a_total.draws,
            "verdict": self.verdict,
            "created": self.created, "finished": self.finished,
        })
    }
}

// ── state ────────────────────────────────────────────────────────────────

fn state_file() -> PathBuf {
    blobs::state_dir().join("ab.json")
}

fn cell() -> &'static Mutex<Vec<Report>> {
    static CELL: OnceLock<Mutex<Vec<Report>>> = OnceLock::new();
    CELL.get_or_init(|| {
        let mut reports: Vec<Report> = fs::read_to_string(state_file())
            .ok()
            .and_then(|t| serde_json::from_str(&t).ok())
            .unwrap_or_default();
        // A report that was running when the process died is not running now,
        // and saying so beats a console polling a ghost forever.
        for r in &mut reports {
            if r.status == "running" {
                r.status = if r.played > 0 { "done" } else { "error" }.into();
                r.error = "interrupted by a restart".into();
                r.current.clear();
                r.verdict = verdict(&r.a.name, &r.b.name, &r.a_total, &r.b_total, r.voids, r.played);
            }
        }
        Mutex::new(reports)
    })
}

fn save(reports: &[Report]) {
    let dir = blobs::state_dir();
    if fs::create_dir_all(&dir).is_err() {
        return;
    }
    let Ok(body) = serde_json::to_string_pretty(reports) else {
        return;
    };
    let tmp = dir.join("ab.json.tmp");
    if fs::write(&tmp, body).is_ok() {
        let _ = fs::rename(&tmp, state_file());
    }
}

fn with_reports<T>(f: impl FnOnce(&mut Vec<Report>) -> T) -> T {
    let mut guard = cell().lock().unwrap_or_else(|e| e.into_inner());
    let out = f(&mut guard);
    save(&guard);
    out
}

// ── the verdict ──────────────────────────────────────────────────────────

/// The report's one sentence. Written from the totals alone so it can be
/// tested without playing anything.
fn verdict(a: &str, b: &str, ta: &Tally, tb: &Tally, voids: u64, played: u64) -> String {
    if played == 0 {
        return if voids > 0 {
            format!("nothing finished — all {voids} match(es) voided")
        } else {
            "nothing played yet".into()
        };
    }
    let score = |t: &Tally| format!("{}–{}{}", t.wins, t.losses,
        if t.draws > 0 { format!(" with {} drawn", t.draws) } else { String::new() });
    let mut line = if ta.wins > tb.wins {
        format!("{a} beats {b} {}", score(ta))
    } else if tb.wins > ta.wins {
        format!("{b} beats {a} {}", score(tb))
    } else {
        format!("{a} and {b} are level at {}–{}", ta.wins, tb.wins)
    };
    // Wins can tie while the play does not: an illegal-move rate apart is the
    // number that matters for a model, so it gets the second clause.
    let rate = |t: &Tally| t.illegal as f64 / t.moves.max(1) as f64;
    let (ra, rb) = (rate(ta), rate(tb));
    if (ra - rb).abs() >= 0.05 {
        let (clean, dirty, rc, rd) =
            if ra < rb { (a, b, ra, rb) } else { (b, a, rb, ra) };
        line.push_str(&format!(
            " — {clean} plays cleaner ({:.0}% illegal vs {dirty}'s {:.0}%)",
            rc * 100.0, rd * 100.0));
    }
    if voids > 0 {
        line.push_str(&format!(" · {voids} match(es) voided"));
    }
    line
}

// ── running one ──────────────────────────────────────────────────────────

fn side_of(key: &str) -> Result<Side, String> {
    let p = store::read(|s| s.player(key).cloned())
        .ok_or_else(|| format!("no player `{key}` — GET /players lists everyone entered"))?;
    Ok(Side { id: p.id, name: p.name, kind: p.kind, elo_start: p.overall.elo, elo_end: p.overall.elo })
}

fn s(args: &Value, key: &str) -> String {
    args.get(key).and_then(|v| v.as_str()).unwrap_or("").trim().to_string()
}

/// Start an experiment. Returns the running report; poll it by id.
pub async fn start(args: &Value) -> Result<Value, String> {
    let a = side_of(&{
        let k = s(args, "a");
        if k.is_empty() { return Err("an A/B test needs `a` and `b` — two entered players".into()); }
        k
    })?;
    let b = side_of(&{
        let k = s(args, "b");
        if k.is_empty() { return Err("an A/B test needs `a` and `b` — two entered players".into()); }
        k
    })?;
    if a.id == b.id {
        return Err(format!("`{}` on both sides — an A/B test needs two different agents", a.name));
    }

    // The fixtures. Named games, or the most-played ones when unnamed —
    // a board that has seen matches is a board whose numbers mean something.
    let asked: Vec<String> = args
        .get("games")
        .and_then(|v| v.as_array())
        .map(|a| a.iter().filter_map(|v| v.as_str()).map(String::from).collect())
        .unwrap_or_default();
    let mut games: Vec<(String, String)> = Vec::new();
    if asked.is_empty() {
        let mut all = store::read(|st| {
            st.module_list().iter().filter(|m| m.role == "game")
                .map(|m| (m.id.clone(), m.name.clone(), m.runs)).collect::<Vec<_>>()
        });
        all.sort_by_key(|(_, _, runs)| std::cmp::Reverse(*runs));
        games = all.into_iter().take(3).map(|(id, name, _)| (id, name)).collect();
    } else {
        for key in &asked {
            let m = store::read(|st| st.module(key).cloned())
                .ok_or_else(|| format!("no game `{key}`"))?;
            if m.role != "game" {
                return Err(format!("{} is a `{}`, not a game", m.name, m.role));
            }
            games.push((m.id, m.name));
        }
    }
    if games.is_empty() {
        return Err("no games stored here — add one first".into());
    }

    let count = args.get("count").and_then(|v| v.as_u64()).unwrap_or(DEFAULT_COUNT).clamp(1, 20);
    let planned = games.len() as u64 * count;
    if planned > MAX_MATCHES {
        return Err(format!(
            "{} games × {count} is {planned} matches — the cap is {MAX_MATCHES}, \
             an agent seat is a model call per move", games.len()));
    }
    let seed = args.get("seed").and_then(|v| v.as_i64());
    let timeout_ms = args.get("timeout_ms").and_then(|v| v.as_u64());

    let report = with_reports(|reports| {
        if let Some(r) = reports.iter().find(|r| r.status == "running") {
            return Err(format!(
                "experiment {} ({} vs {}) is still running — one at a time; \
                 poll GET /ab/{} or wait for it to finish", r.id, r.a.name, r.b.name, r.id));
        }
        let id = format!("ab{}", store::now());
        let r = Report {
            id,
            a: a.clone(),
            b: b.clone(),
            count,
            games: games.iter().map(|(id, name)| GameRow {
                game: id.clone(), game_name: name.clone(), ..Default::default()
            }).collect(),
            status: "running".into(),
            current: "starting".into(),
            error: String::new(),
            planned,
            played: 0,
            voids: 0,
            matches: Vec::new(),
            a_total: Tally::default(),
            b_total: Tally::default(),
            verdict: String::new(),
            created: store::now(),
            finished: 0,
        };
        reports.push(r.clone());
        if reports.len() > KEEP_REPORTS {
            let cut = reports.len() - KEEP_REPORTS;
            reports.drain(0..cut);
        }
        Ok(r)
    })?;

    let id = report.id.clone();
    tokio::spawn(run(id, a, b, games, count, seed, timeout_ms));
    Ok(report.card())
}

/// The worker. Plays every fixture in order, folding each recorded match into
/// the report as it lands, so a poll mid-run reads a true partial score.
async fn run(
    id: String,
    a: Side,
    b: Side,
    games: Vec<(String, String)>,
    count: u64,
    seed: Option<i64>,
    timeout_ms: Option<u64>,
) {
    let mut played_index: i64 = 0;
    for (gi, (game, game_name)) in games.iter().enumerate() {
        for n in 0..count {
            // Seat-swap: even matches seat A first, odd matches seat B first,
            // so a game with a first-mover edge cannot decide the experiment.
            let (first, second) = if n % 2 == 0 { (&a, &b) } else { (&b, &a) };
            with_reports(|reports| {
                if let Some(r) = reports.iter_mut().find(|r| r.id == id) {
                    r.current = format!("{game_name} · match {} of {}", n + 1, count);
                }
            });
            let mut args = json!({
                "game": game,
                "players": [first.id.clone(), second.id.clone()],
            });
            if let Some(base) = seed {
                args["seed"] = json!(base + played_index);
            }
            if let Some(ms) = timeout_ms {
                args["timeout_ms"] = json!(ms);
            }
            played_index += 1;

            // run_match directly, not through call_tool — the dispatcher can
            // await ab::start, and a future that awaits its own dispatcher
            // cannot be proven Send.
            match mcp::run_match(&args).await {
                Ok(rec) => fold(&id, gi, &a, &b, &rec),
                Err(e) => {
                    with_reports(|reports| {
                        if let Some(r) = reports.iter_mut().find(|r| r.id == id) {
                            r.voids += 1;
                            r.error = e.clone();
                        }
                    });
                }
            }
        }
    }

    // Close the book: fresh elo, the sentence, the clock.
    let elo_a = store::read(|st| st.players.get(&a.id).map(|p| p.overall.elo)).unwrap_or(a.elo_start);
    let elo_b = store::read(|st| st.players.get(&b.id).map(|p| p.overall.elo)).unwrap_or(b.elo_start);
    with_reports(|reports| {
        if let Some(r) = reports.iter_mut().find(|r| r.id == id) {
            r.a.elo_end = elo_a;
            r.b.elo_end = elo_b;
            r.current.clear();
            r.finished = store::now();
            r.status = if r.played == 0 { "error" } else { "done" }.into();
            r.verdict = verdict(&r.a.name, &r.b.name, &r.a_total, &r.b_total, r.voids, r.played);
        }
    });
}

/// One recorded match into the running totals. The record is `Match::brief()`
/// straight off `run_match` — seats carry player_id, score and the faults.
fn fold(id: &str, game_index: usize, a: &Side, b: &Side, rec: &Value) {
    let seats = rec.get("seats").and_then(|v| v.as_array()).cloned().unwrap_or_default();
    let of = |side: &Side| {
        seats.iter().find(|s| s.get("player_id").and_then(|v| v.as_str()) == Some(side.id.as_str()))
    };
    let (Some(sa), Some(sb)) = (of(a), of(b)) else {
        // A match that lost a seat is a match that proves nothing.
        with_reports(|reports| {
            if let Some(r) = reports.iter_mut().find(|r| r.id == id) {
                r.voids += 1;
                r.error = "a recorded match came back without both seats".into();
            }
        });
        return;
    };
    let num = |s: &Value, k: &str| s.get(k).and_then(|v| v.as_u64()).unwrap_or(0);
    let score = |s: &Value| s.get("score").and_then(|v| v.as_f64()).unwrap_or(0.0);
    let (score_a, score_b) = (score(sa), score(sb));

    let bump = |t: &mut Tally, seat: &Value, mine: f64, theirs: f64| {
        if mine > theirs {
            t.wins += 1;
        } else if mine < theirs {
            t.losses += 1;
        } else {
            t.draws += 1;
        }
        t.score_sum += mine;
        t.moves += num(seat, "moves");
        t.illegal += num(seat, "illegal");
        t.timeouts += num(seat, "timeouts");
        t.ms += num(seat, "ms");
    };

    with_reports(|reports| {
        let Some(r) = reports.iter_mut().find(|r| r.id == id) else { return };
        bump(&mut r.a_total, sa, score_a, score_b);
        bump(&mut r.b_total, sb, score_b, score_a);
        if let Some(g) = r.games.get_mut(game_index) {
            bump(&mut g.a, sa, score_a, score_b);
            bump(&mut g.b, sb, score_b, score_a);
        }
        if let Some(mid) = rec.get("id").and_then(|v| v.as_str()) {
            r.matches.push(mid.to_string());
        }
        r.played += 1;
        // A partial verdict keeps a mid-run poll honest.
        r.verdict = verdict(&r.a.name, &r.b.name, &r.a_total, &r.b_total, r.voids, r.played);
    });
}

// ── reading ──────────────────────────────────────────────────────────────

pub fn report(id: &str) -> Result<Value, String> {
    let key = id.trim();
    with_reports(|reports| reports.iter().find(|r| r.id == key).map(|r| r.card()))
        .ok_or_else(|| format!("no experiment `{key}` — GET /ab lists them"))
}

pub fn list() -> Value {
    let rows = with_reports(|reports| {
        reports.iter().rev().map(|r| r.brief()).collect::<Vec<_>>()
    });
    json!({ "count": rows.len(), "experiments": rows })
}

pub fn remove(id: &str) -> Result<Value, String> {
    let key = id.trim().to_string();
    with_reports(|reports| {
        let before = reports.len();
        if reports.iter().any(|r| r.id == key && r.status == "running") {
            return Err(format!("experiment `{key}` is still running — let it finish"));
        }
        reports.retain(|r| r.id != key);
        if reports.len() == before {
            Err(format!("no experiment `{key}`"))
        } else {
            Ok(json!({ "removed": key }))
        }
    })
}

// ── tests ────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn tally(wins: u64, draws: u64, losses: u64, moves: u64, illegal: u64) -> Tally {
        Tally { wins, draws, losses, moves, illegal, ..Default::default() }
    }

    #[test]
    fn verdict_names_the_leader() {
        let v = verdict("alpha", "beta", &tally(5, 1, 2, 80, 0), &tally(2, 1, 5, 80, 0), 0, 8);
        assert!(v.starts_with("alpha beats beta 5–2 with 1 drawn"), "{v}");
    }

    #[test]
    fn verdict_is_level_when_wins_tie_and_flags_dirty_play() {
        let v = verdict("alpha", "beta", &tally(3, 0, 3, 100, 1), &tally(3, 0, 3, 100, 20), 0, 6);
        assert!(v.contains("level at 3–3"), "{v}");
        assert!(v.contains("alpha plays cleaner"), "{v}");
    }

    #[test]
    fn verdict_says_when_nothing_finished() {
        let v = verdict("alpha", "beta", &Tally::default(), &Tally::default(), 4, 0);
        assert!(v.contains("nothing finished"), "{v}");
    }

    #[test]
    fn fold_counts_a_win_and_the_faults() {
        // Straight through the pure parts: bump logic via a fabricated record
        // is exercised in the pytest suite against a live server; here we pin
        // the tally arithmetic the report is built from.
        let mut t = Tally::default();
        t.wins += 1;
        t.score_sum += 1.0;
        t.moves += 5;
        t.illegal += 1;
        let card = t.card();
        assert_eq!(card["wins"], 1);
        assert_eq!(card["illegal_rate"], 0.2);
    }
}
