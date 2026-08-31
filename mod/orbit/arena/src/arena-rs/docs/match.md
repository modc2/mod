# Matches and ratings

A match seats N players at a game, shows each one only what `view` gives that
seat, and records every turn: what was seen, what was said, what was read as
the move, and whether the game accepted it. Two or more seats makes it rated;
one seat is practice.

## The loop

One loop, `runtime/match.mjs`, for every container. It asks the game whose turn
it is, asks those seats for moves, hands them to `step`, and stops when `done`
says so. It never learns whether the game it is driving is a class in a python
process or wasm in a Worker — which is the only reason a class game and a wasm
game can share a leaderboard.

Several seats returned by `turn` is a simultaneous round: everyone moves
without seeing the others, and `step` gets all the moves at once.

## What comes out

```console
$ m arena/leaderboard game=ttt
1. perfect   wasm   elo 1223.6   2/0/0   illegal   0%    60ms/move
2. nonsense  http   elo 1188.4   0/0/1   illegal 100%     8ms/move
3. dice      wasm   elo 1188.0   0/0/1   illegal   0%    25ms/move
```

More than a win/loss record, per player and per game:

- **Elo**, kept per game *and* overall. Being good at nim says nothing about
  poker, so the two are rated against different fields and a specialist's first
  match against a strong all-rounder does not count twice at the wrong odds.
- **illegal-move rate** — the number worth having. Losing at tic-tac-toe is bad
  play; playing an occupied square is not having read the board. Different
  failures, different fixes.
- **timeouts**, **time to move**, **mean score**, **win rate**, form and streak.
- **calls out** — how often it left the sandbox to ask an [MCP server](#docs/mcp)
  something mid-move. A player that calls out is a player whose move is no
  longer a pure function of its view, and the leaderboard says so.

## Replay is the receipt

The runner is trusted for the outcome; it is the thing that ran the wasm. What
makes that honest rather than hopeful is the transcript: the seed and every
move are recorded, and a game is a pure function of its state, so anyone can
replay a match and check the scores. A class match replays by starting the
process from the seed and feeding it the recorded moves.

A leaderboard here is a claim with its working attached.

## Playing one

- **In the console** — open a game, fill the seats, press play. Wasm matches run
  in the tab; a class match goes through the runner, because a tab cannot start
  a python process. Same loop, same ratings.
- **From the CLI** — `m arena/play game=ttt players=opus,perfect count=5`.
- **Over MCP** — `run_match` plays a whole one headlessly; `open` / `view` /
  `move` on a game's own server plays it a turn at a time.
- **From anywhere** — `POST /matches` records a match played elsewhere, with
  its transcript, and rates it.

`run_match` needs node on PATH, because that is the execution layer. Without
it, matches play in the browser only.
