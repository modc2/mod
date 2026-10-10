#!/usr/bin/env node
// Headless runner — the same execution layer the console uses, on the CLI.
//
//   node run.mjs run     --module <id> [--entry name] [--stdin text] [--arg v]…
//   node run.mjs match   --game <id> --players alice,bob [--seed 7] [--no-record]
//   node run.mjs session --game <id> --seats 2 --seed 7 --moves '[{"0":"a"}]'
//   node run.mjs ask     --module <id> --view "…" --seat 0
//
// The last two are what a module's own MCP server is built on. `session`
// replays a table from its seed and its moves and reports where it stands, and
// `ask` puts one question to a player — so a game can be played a turn at a
// time over MCP without anything having to stay alive between the turns.
//
// It imports host.mjs and match.mjs unchanged, so a match played here and a
// match played in a tab are the same computation. That matters: the CLI is how
// the MCP `run_match` tool plays a match without a browser open.
//
// One caveat worth stating plainly: a wasm call that never returns cannot be
// interrupted from inside the same thread. `--timeout` therefore kills the
// process rather than the module. The browser runs modules in a worker and can
// terminate them properly; here, the blast radius is this process.

import { run } from "./host.mjs";
import { driverFor, isWasm, makeApi, openGame, runMatch } from "./match.mjs";
import { asyncMcp, mcpFor } from "./mcpsync.mjs";
import { closeAll, runClass } from "./pyhost.mjs";

const DEFAULT_BASE = process.env.MODARENA_BASE || "http://127.0.0.1:50800";

function parseArgs(argv) {
  const out = { _: [], arg: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith("--")) { out._.push(a); continue; }
    const key = a.slice(2);
    if (key.startsWith("no-")) { out[key.slice(3)] = false; continue; }
    const next = argv[i + 1];
    if (next === undefined || next.startsWith("--")) { out[key] = true; continue; }
    if (key === "arg") out.arg.push(next);
    else out[key] = next;
    i++;
  }
  return out;
}

// A class is a process, and a runner that exits owing one is a leak. Every
// path out of this file goes through here, including `die`.
process.on("exit", closeAll);

const die = (msg) => { console.error(msg); process.exit(1); };

/** Enough of a value to see what happened, never enough to fill a terminal. */
const brief = (v) => {
  const text = typeof v === "string" ? v : JSON.stringify(v);
  if (text === undefined) return String(v);
  return text.length > 300 ? `${text.slice(0, 300)}…` : text;
};
const say = (v) => console.log(JSON.stringify(v, null, 2));

const opts = parseArgs(process.argv.slice(2));
const api = makeApi(opts.base || DEFAULT_BASE);

if (opts.timeout) {
  const ms = Number(opts.timeout) || 0;
  if (ms > 0) setTimeout(() => die(`timed out after ${ms}ms`), ms).unref?.();
}

const cmd = opts._[0] || "help";

/** The outward door, or nothing — `--mcp a,b` is how a run opens one. */
function door(o) {
  if (!o.mcp || o.mcp === false) return null;
  const allow = o.mcp === true
    ? []
    : String(o.mcp).split(",").map((x) => x.trim()).filter(Boolean);
  return {
    allow,
    sync: mcpFor(api.root, { allow }),
    async: asyncMcp(api.root, { allow }),
  };
}

const range = (n) => Array.from({ length: n }, (_, i) => i);

try {
  if (cmd === "run") {
    const id = opts.module || opts._[1];
    if (!id) die("run needs --module <id>");
    const [bytes, meta] = await Promise.all([api.blob(id), api.module(id).catch(() => null)]);
    if (isWasm(bytes)) {
      say(await run(bytes, {
        info: meta?.info ?? meta,
        entry: opts.entry,
        args: [meta?.name || "module", ...opts.arg],
        stdin: opts.stdin === true ? "" : (opts.stdin ?? ""),
        seed: Number(opts.seed ?? 1),
        callArgs: opts.arg.map(Number).filter((n) => Number.isFinite(n)),
      }));
    } else {
      // A class: load it in the sandbox and call one method. `--arg 3` reaches
      // it as the number 3 where that is what it looks like, as text otherwise.
      say(await runClass(new TextDecoder().decode(bytes), {
        seed: Number(opts.seed ?? 1),
        method: opts.entry === true ? "" : (opts.entry ?? ""),
        args: opts.arg.map((a) => (a !== "" && Number.isFinite(Number(a)) ? Number(a) : a)),
      }));
    }
  } else if (cmd === "smoke") {
    // Load a mod and make it answer. The structural verifier reads the anchor;
    // this one runs it, which is the only way to find out that a game never
    // ends, that `step` raises on a move it does not know, or that a player
    // returns something that is not a move. Cheap, bounded, and the last gate
    // a generated mod goes through before anyone is asked to play it.
    const id = opts.module || opts._[1];
    if (!id) die("smoke needs --module <id>");
    const meta = await api.module(id).catch(() => null);
    const role = String(opts.role || meta?.role || "").toLowerCase();
    const seed = Number(opts.seed ?? 7);
    const steps = [];
    const step = async (name, fn, show = true) => {
      const t0 = Date.now();
      try {
        const value = await fn();
        steps.push({ step: name, ok: true, ms: Date.now() - t0, ...(show ? { value: brief(value) } : {}) });
        return value;
      } catch (e) {
        steps.push({ step: name, ok: false, ms: Date.now() - t0, error: e.message || String(e) });
        throw e;
      }
    };

    let closer = null;
    let ok = true;
    try {
      if (role === "player") {
        const driver = await step("open", () =>
          driverFor({ name: meta?.name || "candidate", kind: "class", config: { module: id } },
                    { api, seed }), false);
        closer = driver.close;
        const answer = await step("play", () => driver.move("smoke test: answer with a move", 0));
        const move = typeof answer === "string" ? answer : answer?.move;
        if (typeof move !== "string") {
          throw new Error(`play returned ${typeof move}, and a move is text`);
        }
      } else {
        const { game } = await step("open", () => openGame(api, id, { seed, seats: 2 }), false);
        closer = () => game.close?.();
        await step("info", async () => game.info?.() ?? {});
        const view = await step("view", () => game.view(0));
        if (typeof view !== "string" || !view.length) throw new Error("view(0) returned nothing to look at");
        // A move nothing should accept: the game must say it is illegal
        // rather than raise, because that is what it will be handed by a
        // model on its first turn.
        const stepped = await step("step", () => game.step({ 0: "\u0000not-a-move" }));
        if (stepped && stepped.legal && stepped.legal[0] === true) {
          steps.push({ step: "rejects", ok: false, error: "nonsense was accepted as a legal move" });
          ok = false;
        } else {
          steps.push({ step: "rejects", ok: true, value: "illegal moves are refused, not raised" });
        }
        await step("done", () => game.done());
        await step("result", () => game.result());
      }
    } catch {
      ok = false;
    } finally {
      try { closer?.(); } catch { /* already gone */ }
    }
    say({ ok: ok && steps.every((s2) => s2.ok), module: id, name: meta?.name, role, steps });
    if (!ok) process.exitCode = 1;
  } else if (cmd === "match") {
    const game = opts.game || opts._[1];
    const names = String(opts.players || "").split(",").map((s) => s.trim()).filter(Boolean);
    if (!game || !names.length) die("match needs --game <id> --players a,b");

    const roster = await api.players();
    const byKey = new Map();
    for (const p of roster.players ?? roster) {
      byKey.set(p.id, p);
      byKey.set(String(p.name).toLowerCase(), p);
    }
    const players = names.map((n) => {
      const p = byKey.get(n) || byKey.get(n.toLowerCase());
      if (!p) die(`no player named ${n} — enter it first`);
      return p;
    });

    const record = await runMatch({
      api, game, players,
      seed: Number(opts.seed ?? Math.floor(Math.random() * 1e9)),
      maxTurns: Number(opts.turns ?? 0),
      mcp: opts.mcp ? { allow: opts.mcp === true ? [] : String(opts.mcp).split(",")
                                                          .map((x) => x.trim()).filter(Boolean) } : null,
      onEvent: opts.quiet ? null : (e) => {
        if (e.type === "turn") {
          const flag = e.legal ? "" : "  ILLEGAL";
          console.error(`  t${e.turn} seat ${e.seat}: ${JSON.stringify(e.mv)}${flag} (${e.ms}ms)`);
        }
      },
    });

    say(opts.record === false ? record : await api.record(record));
  } else if (cmd === "session") {
    // Replay a table and say where it stands. Every turn played over a game's
    // MCP server comes through here, from the seed, every time — which is the
    // determinism the registry claims, spent rather than asserted.
    const game = opts.game || opts._[1];
    if (!game) die("session needs --game <id>");
    const seats = Number(opts.seats ?? 2) || 2;
    const seed = Number(opts.seed ?? 1);
    const rounds = JSON.parse(opts.moves && opts.moves !== true ? opts.moves : "[]");

    const opened = await openGame(api, game, { seed, seats, mcp: door(opts) });
    const g = opened.game;
    try {
      const info = await g.info();
      await g.init(seed);
      const limit = Number(opts.turns ?? 0) || info.max_turns || 200;
      const history = [];

      for (let turnNo = 0; turnNo < rounds.length; turnNo++) {
        if (await g.done()) break;
        const active = await g.turn(seats, turnNo);
        const round = rounds[turnNo] ?? {};
        const views = {};
        for (const seat of active) views[seat] = await g.view(seat);
        const moves = {};
        for (const seat of active) moves[seat] = String(round[seat] ?? round[String(seat)] ?? "");
        const { legal, note } = await g.step(moves);
        for (const seat of active) {
          history.push({
            turn: turnNo, seat, view: views[seat], mv: moves[seat],
            legal: legal[seat] ?? legal[String(seat)] ?? true, note,
          });
        }
      }

      const done = (await g.done()) || rounds.length >= limit;
      const active = done ? [] : await g.turn(seats, rounds.length);
      const views = {};
      for (const seat of active.length ? active : range(seats)) views[seat] = await g.view(seat);

      say({
        ok: true, game, lang: opened.lang, info, seed, seats,
        turn: rounds.length, active, views, done,
        result: done ? await g.result() : null,
        history,
        mcp: g.mcpCalls?.() ?? [],
      });
    } finally {
      try { g.close?.(); } catch { /* already gone */ }
    }
  } else if (cmd === "ask") {
    // One question to one player: here is a view, what do you play? The same
    // question a match asks, so the answer is the answer.
    const id = opts.module || opts._[1];
    const view = opts.view === true ? "" : (opts.view ?? "");
    if (!id) die("ask needs --module <id>");
    const seat = Number(opts.seat ?? 0);
    const t0 = Date.now();
    const driver = await driverFor(
      { kind: "class", name: String(id).slice(0, 12), module: id },
      { api, seed: Number(opts.seed ?? 1), mcp: door(opts) },
    );
    try {
      const r = await driver.move(view, seat);
      say({
        ok: true, module: id, seat, driver: driver.label,
        move: (r.move ?? "").trim(), note: r.note ?? "",
        mcp: r.mcp ?? [], ms: Date.now() - t0,
      });
    } finally {
      try { driver.close?.(); } catch { /* already gone */ }
    }
  } else {
    console.log(`arena runtime
  node run.mjs run     --module <id> [--entry name] [--stdin text] [--arg v]…
  node run.mjs match   --game <id> --players a,b [--seed 7] [--turns n] [--no-record]
  node run.mjs session --game <id> [--seats 2] [--seed 7] [--moves '[{"0":"a"}]']
  node run.mjs ask     --module <id> --view "…" [--seat 0] [--seed 1]
  common: --base ${DEFAULT_BASE} --timeout ms --quiet --mcp server,server

A module is wasm, a Rust class or a Python class. All three play here: wasm
runs in this engine, a Rust class is compiled to wasm by the server and runs
in the same one, and a Python class runs in a sandboxed python subprocess
(MODARENA_PYTHON to pick the interpreter).

--mcp names the servers a class in this run may call out to. Left off, it has
no way out, which is the default and the only setting under which a move is a
function of its view alone.`);
    process.exit(cmd === "help" ? 0 : 1);
  }
} catch (e) {
  die(e.stack || e.message || String(e));
}
