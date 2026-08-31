// The match loop, and the drivers that sit a player in a seat.
//
// Wasm executes here — in the browser tab, or in the node runner, but always
// in a JS engine's WebAssembly implementation, never on the server. Anything
// that needs a key or would trip CORS (a model, a fleet agent, someone's
// endpoint) is asked for through the server's /play proxy instead, so the
// execution layer stays a pure sandbox and the secrets stay where they were.
//
// A module here is one of three things and this loop cannot tell which:
// `openGame` and `driverFor` hand back the same shape for all of them.
//
//   wasm            instantiated here, in this JS engine
//   a Rust class    compiled to wasm by the server on upload, then the same
//   a Python class  a sandboxed python subprocess (`pyhost.mjs`), which only
//                   the node runner can start — a tab asked to play one says
//                   so and stops
//
// So the browser plays two of the three, and which two is a fact about where
// the sandbox is rather than about the languages.
//
// A class may also call *out*, to an MCP server, if this match was given a
// door (`mcp` below). Neither sandbox grows a socket to do it: the wasm one
// goes through a synchronous host call, the python one through its line
// protocol, and both end at the arena's /mcp/call, which is where the list of
// reachable servers actually lives. Every call is counted onto the seat that
// made it, because a player that consulted something did not answer from its
// view alone.
//
// A match is deterministic given its seed and its moves: the game module is
// pure over its state, the host PRNG is seeded, and the clock a module can
// read is relative. So the transcript a match writes is enough to replay it.

import { instantiate, nowMs } from "./host.mjs";
import { Abi, Game } from "./abi.mjs";
import { asyncMcp, mcpFor } from "./mcpsync.mjs";

/** Wasm's four magic bytes. Everything else stored here is class source. */
const WASM_MAGIC = [0x00, 0x61, 0x73, 0x6d];

export function isWasm(bytes) {
  return bytes?.length >= 4 && WASM_MAGIC.every((b, i) => bytes[i] === b);
}

export const IS_NODE = typeof process !== "undefined" && !!process.versions?.node;

/** The class host, loaded only where it can run — importing it in a tab throws. */
async function pyhost() {
  if (!IS_NODE) {
    throw new Error(
      "this module is a Python class, and a browser tab cannot run one. Play it from " +
      "the runner instead: `m arena/play game=… players=…`",
    );
  }
  return import("./pyhost.mjs");
}

const dec = new TextDecoder("utf-8", { fatal: false });

export const DEFAULT_MOVE_TIMEOUT_MS = 60_000;
export const WASM_MOVE_TIMEOUT_MS = 5_000;

/** The server, seen from wherever this is running. */
export function makeApi(base = "") {
  const root = base.replace(/\/$/, "");
  const json = async (path, init) => {
    const r = await fetch(root + path, init);
    const body = await r.json().catch(() => ({ error: `${r.status} ${r.statusText}` }));
    if (!r.ok) throw new Error(body.error || `${path}: ${r.status}`);
    return body;
  };
  return {
    root,
    async blob(id) {
      const r = await fetch(`${root}/blob/${id}`);
      if (!r.ok) throw new Error(`blob ${id}: ${r.status}`);
      return new Uint8Array(await r.arrayBuffer());
    },
    /**
     * The wasm a module *runs* as. The same bytes for a wasm upload; the
     * compile for a Rust class, which the server does once and caches under
     * the module's id. A Python class has no wasm form and this 400s.
     */
    async wasm(id) {
      const r = await fetch(`${root}/wasm/${id}`);
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body.error || `wasm ${id}: ${r.status}`);
      }
      return new Uint8Array(await r.arrayBuffer());
    },
    module: (id) => json(`/modules/${id}`),
    modules: (q = "") => json(`/modules${q}`),
    players: () => json("/players"),
    play: (body) => json("/play", { method: "POST", headers: { "content-type": "application/json" },
                                    body: JSON.stringify(body) }),
    record: (body) => json("/matches", { method: "POST", headers: { "content-type": "application/json" },
                                         body: JSON.stringify(body) }),
  };
}

/**
 * Fetch a module in the form it executes in.
 *
 * This is the one place the three containers are told apart, and it is done
 * off the registry's own reading of the bytes rather than by guessing here.
 * A Rust class comes back as the wasm it compiles to, which is why everything
 * downstream of this function only ever sees two cases.
 */
async function loadModule(api, id) {
  const meta = await api.module(id).catch(() => null);
  const lang = String(meta?.lang ?? "").toLowerCase();
  if (lang === "rust") return { kind: "wasm", bytes: await api.wasm(id), meta, lang };
  if (lang === "python") return { kind: "python", bytes: await api.blob(id), meta, lang };

  // No metadata — an id handed straight in, or a server too old to say. The
  // bytes still answer: wasm has a header, and source that compiles is Rust.
  const bytes = await api.blob(id);
  if (isWasm(bytes)) return { kind: "wasm", bytes, meta, lang: "wasm" };
  try {
    return { kind: "wasm", bytes: await api.wasm(id), meta, lang: "rust" };
  } catch {
    return { kind: "python", bytes, meta, lang: "python" };
  }
}

/**
 * Open a game module for play — wasm, Rust class or Python class, the same
 * object every time. Whoever calls this owns the result and must `close()` it.
 */
export async function openGame(api, id, { seed = 1, seats = 0, mcp = null } = {}) {
  const { kind, bytes, meta, lang } = await loadModule(api, id);
  if (kind === "wasm") {
    const host = await instantiate(bytes, { info: meta?.info ?? meta, seed, mcp: mcp?.sync ?? null });
    const g = new Game(new Abi(host));
    g.close ??= () => {};
    g.mcpCalls = () => host.out.mcp.splice(0);
    return { game: g, meta, lang };
  }
  const { openClass, PyGame } = await pyhost();
  const session = await openClass(dec.decode(bytes), { seed, seats, mcp: mcp?.async ?? null });
  const g = new PyGame(session);
  g.mcpCalls = () => session.mcp.splice(0);
  return { game: g, meta, lang: "python" };
}

/** Everything a seat needs to answer: one `move(view, seat)` and a label. */
export async function driverFor(player, { api, seed, onEvent, mcp = null } = {}) {
  const kind = (player.kind || "").toLowerCase();

  if (kind === "wasm" || kind === "class") {
    // A player card carries `module` but never `config` — the config can hold
    // an API key and this runs in a browser tab.
    const id = player.module ?? player.config?.module;
    if (!id) throw new Error(`player ${player.name}: a ${kind} player needs config.module`);
    const loaded = await loadModule(api, id);

    // The player card says `wasm`, but the module is whatever was stored under
    // that id — so the bytes decide, not the card.
    if (loaded.kind === "python") {
      const { openClass, pyDriver } = await pyhost();
      const session = await openClass(dec.decode(loaded.bytes), { seed, mcp: mcp?.async ?? null });
      return pyDriver(session, { name: player.name });
    }

    const info = loaded.meta;
    const host = await instantiate(loaded.bytes, {
      info: info?.info ?? info,
      seed,
      mcp: mcp?.sync ?? null,
    });
    const abi = new Abi(host);
    if (!abi.has("play")) {
      throw new Error(`player ${player.name}: module ${short(id)} exports no \`play\``);
    }
    return {
      label: `${loaded.lang} ${short(id)}`,
      timeout: WASM_MOVE_TIMEOUT_MS,
      async move(view, seat) {
        const text = abi.callText("play", ...abi.put(view), seat | 0);
        // A bot that logged its reasoning gets it into the transcript too.
        const note = host.out.log.splice(0).join(" ");
        return { move: text, note, mcp: host.out.mcp.splice(0) };
      },
    };
  }

  // An MCP server in a seat — somebody else's module, reached the same way
  // this arena's own modules are reachable. It is driven by the server rather
  // than here: the endpoint may want a credential, a player card carries no
  // config into a tab, and a module of this fleet is named rather than
  // addressed, so resolving it is the server's job. Falls through.

  if (kind === "human") {
    const ask = player.config?.ask ?? onEvent?.human;
    if (typeof ask !== "function") {
      throw new Error(`player ${player.name}: a human player needs somewhere to be asked`);
    }
    return {
      label: "human",
      timeout: player.config?.timeout_ms ?? 0,
      async move(view, seat) {
        return { move: await ask({ view, seat, player }) };
      },
    };
  }

  // model | agent_mod | mcp | http — the server drives these; we only ask.
  return {
    label: kind || "remote",
    timeout: player.config?.timeout_ms ?? DEFAULT_MOVE_TIMEOUT_MS,
    async move(view, seat) {
      const r = await api.play({ player: player.id, view, seat });
      return { move: r.move ?? "", raw: r.raw ?? "", note: r.note ?? "", prompt: r.prompt ?? "" };
    },
  };
}

function short(id) {
  return String(id).slice(0, 12);
}

function withTimeout(promise, ms, what) {
  if (!ms) return promise;
  return Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(new TimeoutError(what, ms)), ms)),
  ]);
}

export class TimeoutError extends Error {
  constructor(what, ms) {
    super(`${what} took longer than ${ms}ms`);
    this.timeout = true;
  }
}

/**
 * Play one match.
 *
 * @param {object}   o
 * @param {object}   o.api      the server, from `makeApi`
 * @param {string}   o.game     module id of the game
 * @param {object[]} o.players  one player record per seat, in seat order
 * @param {number}   o.seed     replay handle; the same seed and moves replay it
 * @param {function} o.onEvent  progress, for a UI that wants to watch
 * @param {object}   o.mcp      `{ allow: [...] }` to let the classes in this
 *                              match call out, or nothing at all — which is
 *                              the default, and the only setting under which
 *                              a move is a function of its view alone
 */
export async function runMatch({
  api, game, players, seed = 1, maxTurns = 0, onEvent = null, mcp = null,
}) {
  const t0 = nowMs();
  const emit = (type, data) => onEvent?.({ type, ...data });

  // One door, built once, handed to every sandbox in this match. Both halves
  // are made because a match can hold both kinds of class at the same table.
  const door = mcp
    ? {
        allow: mcp.allow ?? [],
        sync: await mcpFor(api.root, { allow: mcp.allow ?? [] }),
        async: asyncMcp(api.root, { allow: mcp.allow ?? [] }),
      }
    : null;

  const seats = players.length;
  const opened = await openGame(api, game, { seed, seats, mcp: door });
  const { game: g, meta } = opened;
  const drivers = [];
  // A class holds a python process open; a wasm module holds nothing. Closing
  // both the same way is what keeps the runner from leaving processes behind,
  // on the happy path and on every throw below alike.
  const shut = () => {
    for (const d of drivers) { try { d.close?.(); } catch { /* already gone */ } }
    try { g.close?.(); } catch { /* already gone */ }
  };

  try {
    const info = await g.info();
    emit("game", { info, module: game, lang: opened.lang });

    if (seats < (info.min_players ?? 1)) {
      throw new Error(`${info.name || short(game)} needs at least ${info.min_players} players, got ${seats}`);
    }
    if (info.max_players && seats > info.max_players) {
      throw new Error(`${info.name || short(game)} seats at most ${info.max_players} players, got ${seats}`);
    }

    for (const p of players) drivers.push(await driverFor(p, { api, seed, onEvent, mcp: door }));
    await g.init(seed);
    const limit = maxTurns || info.max_turns || 200;
    const turns = [];
    const tally = players.map(() => ({ moves: 0, illegal: 0, timeouts: 0, ms: 0, mcp: 0, error: "" }));
    // What the classes in this match asked the outside world, in order. Kept
    // whole on the record rather than only counted: "it called out four times"
    // is a much weaker thing to be able to say than "here is what it asked".
    const calls = [];

    let turnNo = 0;
    while (turnNo < limit && !(await g.done())) {
      const active = await g.turn(seats, turnNo);
      if (!active.length) break;

      // Everyone to move this turn moves at once and sees only their own view —
      // which is what makes simultaneous games (and hidden information) work.
      const asked = await Promise.all(
        active.map(async (seat) => {
          const view = await g.view(seat);
          const before = tally[seat].mcp;
          const started = nowMs();
          let move = "", raw = "", note = "", prompt = "", failed = "";
          try {
            const r = await withTimeout(drivers[seat].move(view, seat), drivers[seat].timeout,
                                        `${players[seat].name}'s move`);
            move = (r.move ?? "").trim();
            raw = r.raw ?? "";
            note = r.note ?? "";
            prompt = r.prompt ?? "";
            for (const c of r.mcp ?? []) {
              // A call the match refused never left the sandbox, so it is not
              // a call out. It stays in the transcript — the refusal is worth
              // reading — but the seat's count is calls that landed.
              if (!c.refused) tally[seat].mcp++;
              calls.push({ turn: turnNo, seat, ...c });
            }
          } catch (e) {
            failed = e.message || String(e);
            if (e.timeout) tally[seat].timeouts++;
            tally[seat].error ||= failed;
          }
          const ms = Math.round(nowMs() - started);
          tally[seat].ms += ms;
          tally[seat].moves++;
          return { seat, view, move, raw, note, prompt, failed, ms, mcp: tally[seat].mcp - before };
        }),
      );

      const moves = {};
      for (const a of asked) moves[a.seat] = a.move;
      const { legal, note } = await g.step(moves);

      for (const a of asked) {
        // No answer is not a legal move. The game still gets the empty string,
        // so it can rule on a forfeit however it wants to.
        const ok = a.failed ? false : legal[a.seat] ?? legal[String(a.seat)] ?? true;
        if (!ok) tally[a.seat].illegal++;
        const asked = tally[a.seat].mcp && a.mcp
          ? `asked out ${a.mcp}×`
          : "";
        const row = {
          turn: turnNo, seat: a.seat, view: a.view, raw: a.raw || a.move, mv: a.move,
          legal: ok, ms: a.ms,
          note: [a.failed, a.note, note, asked].filter(Boolean).join(" · "),
          // the question, for players that were asked one (model/agent/http)
          ...(a.prompt ? { prompt: a.prompt } : {}),
        };
        turns.push(row);
        emit("turn", row);
      }
      turnNo++;
    }

    const result = await g.result();
    const scores = players.map((_, i) => Number(result.scores?.[i] ?? 0));
    const record = {
      game,
      game_name: info.name || meta?.name || short(game),
      seed,
      // Checking for `window` would be wrong: matches play inside a Worker,
      // which has no window and is very much a browser.
      runtime: IS_NODE ? "node" : "browser",
      lang: opened.lang,
      summary: result.summary || "",
      ms: Math.round(nowMs() - t0),
      seats: players.map((p, i) => ({
        seat: i, player_id: p.id, player_name: p.name, score: scores[i],
        moves: tally[i].moves, illegal: tally[i].illegal, timeouts: tally[i].timeouts,
        ms: tally[i].ms, mcp: tally[i].mcp, error: tally[i].error,
      })),
      turns,
      // Whatever the game itself asked for goes on the record too — a game may
      // consult a server to judge a move, and that is as much a part of how
      // the match went as anything a player did.
      mcp: [...calls, ...(g.mcpCalls?.() ?? []).map((c) => ({ turn: turnNo, seat: -1, ...c }))],
    };
    emit("done", { record, result });
    return record;
  } finally {
    shut();
  }
}
