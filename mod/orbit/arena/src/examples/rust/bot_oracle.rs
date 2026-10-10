//! A player that does not play. It asks another player, over MCP.
//!
//! Every module stored in this arena is also an MCP server of its own, at
//! `/m/<name>/mcp`, and a player's server has a `play` tool: give it a view,
//! get a move. So a player can be a client of another player, and this is the
//! smallest thing that demonstrates it — a delegate with an opinion about who
//! to delegate to and nothing else.
//!
//! Two things are worth noticing about how it reaches out. It names a server
//! (`arena`) rather than a URL, because the sandbox has no network and could
//! not use a URL if it had one — the host makes the call. And it treats a
//! failed call as ordinary: `arena::ask` can return an error, the oracle can
//! be unreachable, and a player that cannot lose that call is a player that
//! will forfeit a match one day for reasons unrelated to how it plays.
//!
//! The arena counts these calls onto the seat that made them, and says so on
//! the leaderboard. A player that phoned a friend is not the same kind of
//! player as one that worked it out.

/// Plays whatever `bot-perfect` would play, by asking it.
pub struct Oracle {
    /// Which module to ask. Any player in this arena will do.
    asked: usize,
    failed: usize,
}

impl Oracle {
    pub const NAME: &'static str = "bot-oracle";
    const CONSULTS: &'static str = "bot-perfect";

    pub fn new(_seed: i64) -> Oracle {
        Oracle { asked: 0, failed: 0 }
    }

    pub fn play(&mut self, view: &str, seat: usize) -> String {
        self.asked += 1;
        // `module_tool` is the arena's own way in to another module's server:
        // one call, rather than a second MCP connection from inside a sandbox
        // that could not open one anyway.
        let request = format!(
            "{{\"module\":\"{}\",\"tool\":\"play\",\"arguments\":{{\"view\":\"{}\",\"seat\":{}}}}}",
            Self::CONSULTS,
            escape(view),
            seat
        );
        match arena::ask("arena", "module_tool", &request) {
            Ok(reply) => match json_str(&reply, "move") {
                Some(mv) if !mv.trim().is_empty() => {
                    arena::log(format!("{} says {mv}", Self::CONSULTS));
                    mv
                }
                _ => {
                    self.failed += 1;
                    arena::log(format!("{} had nothing to say", Self::CONSULTS));
                    self.guess(view)
                }
            },
            Err(why) => {
                self.failed += 1;
                arena::log(format!("could not reach {}: {why}", Self::CONSULTS));
                self.guess(view)
            }
        }
    }

    /// What to do when the oracle is silent: play a legal move off the view.
    /// An agent whose whole strategy is somebody else still needs one of these.
    fn guess(&self, view: &str) -> String {
        for line in view.lines() {
            if let Some(rest) = line.strip_prefix("Legal moves:") {
                let options: Vec<&str> =
                    rest.split(',').map(|m| m.trim()).filter(|m| !m.is_empty()).collect();
                return arena::choice(&options).unwrap_or("").to_string();
            }
        }
        String::new()
    }
}
