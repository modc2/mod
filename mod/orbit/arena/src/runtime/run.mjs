#!/usr/bin/env node
// Headless runner — the same execution layer the console uses, on the CLI.
//
//   node run.mjs run   --module <id> [--entry name] [--stdin text] [--arg v]…
//   node run.mjs match --game <id> --players alice,bob [--seed 7] [--no-record]
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
import { makeApi, runMatch } from "./match.mjs";

const DEFAULT_BASE = process.env.ARENA_BASE || "http://127.0.0.1:50470";

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

const die = (msg) => { console.error(msg); process.exit(1); };
const say = (v) => console.log(JSON.stringify(v, null, 2));

const opts = parseArgs(process.argv.slice(2));
const api = makeApi(opts.base || DEFAULT_BASE);

if (opts.timeout) {
  const ms = Number(opts.timeout) || 0;
  if (ms > 0) setTimeout(() => die(`timed out after ${ms}ms`), ms).unref?.();
}

const cmd = opts._[0] || "help";

try {
  if (cmd === "run") {
    const id = opts.module || opts._[1];
    if (!id) die("run needs --module <id>");
    const [bytes, meta] = await Promise.all([api.blob(id), api.module(id).catch(() => null)]);
    say(await run(bytes, {
      info: meta?.info ?? meta,
      entry: opts.entry,
      args: [meta?.name || "module", ...opts.arg],
      stdin: opts.stdin === true ? "" : (opts.stdin ?? ""),
      seed: Number(opts.seed ?? 1),
      callArgs: opts.arg.map(Number).filter((n) => Number.isFinite(n)),
    }));
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
      onEvent: opts.quiet ? null : (e) => {
        if (e.type === "turn") {
          const flag = e.legal ? "" : "  ILLEGAL";
          console.error(`  t${e.turn} seat ${e.seat}: ${JSON.stringify(e.mv)}${flag} (${e.ms}ms)`);
        }
      },
    });

    say(opts.record === false ? record : await api.record(record));
  } else {
    console.log(`arena runtime
  node run.mjs run   --module <id> [--entry name] [--stdin text] [--arg v]…
  node run.mjs match --game <id> --players a,b [--seed 7] [--turns n] [--no-record]
  common: --base ${DEFAULT_BASE} --timeout ms --quiet`);
    process.exit(cmd === "help" ? 0 : 1);
  }
} catch (e) {
  die(e.stack || e.message || String(e));
}
