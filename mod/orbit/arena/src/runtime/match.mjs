// The match loop, and the drivers that sit a player in a seat.
//
// Wasm executes here — in the browser tab, or in the node runner, but always
// in a JS engine's WebAssembly implementation, never on the server. Anything
// that needs a key or would trip CORS (a model, a fleet agent, someone's
// endpoint) is asked for through the server's /play proxy instead, so the
// execution layer stays a pure sandbox and the secrets stay where they were.
//
// A match is deterministic given its seed and its moves: the game module is
// pure over its state, the host PRNG is seeded, and the clock a module can
// read is relative. So the transcript a match writes is enough to replay it.

import { instantiate, nowMs } from "./host.mjs";
import { Abi, Game } from "./abi.mjs";

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
    module: (id) => json(`/modules/${id}`),
    modules: (q = "") => json(`/modules${q}`),
    players: () => json("/players"),
    play: (body) => json("/play", { method: "POST", headers: { "content-type": "application/json" },
                                    body: JSON.stringify(body) }),
    record: (body) => json("/matches", { method: "POST", headers: { "content-type": "application/json" },
                                         body: JSON.stringify(body) }),
  };
}

/** Everything a seat needs to answer: one `move(view, seat)` and a label. */
async function driverFor(player, { api, seed, onEvent }) {
  const kind = (player.kind || "").toLowerCase();

  if (kind === "wasm") {
    // A player card carries `module` but never `config` — the config can hold
    // an API key and this runs in a browser tab.
    const id = player.module ?? player.config?.module;
    if (!id) throw new Error(`player ${player.name}: a wasm player needs config.module`);
    const [bytes, info] = await Promise.all([api.blob(id), api.module(id).catch(() => null)]);
    const host = await instantiate(bytes, { info: info?.info ?? info, seed });
    const abi = new Abi(host);
    if (!abi.has("play")) {
      throw new Error(`player ${player.name}: module ${short(id)} exports no \`play\``);
    }
    return {
      label: `wasm ${short(id)}`,
      timeout: WASM_MOVE_TIMEOUT_MS,
      async move(view, seat) {
        const text = abi.callText("play", ...abi.put(view), seat | 0);
        // A bot that logged its reasoning gets it into the transcript too.
        const note = host.out.log.splice(0).join(" ");
        return { move: text, note };
      },
    };
  }

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

  // model | agent_mod | http — the server drives these; we only ask.
  return {
    label: kind || "remote",
    timeout: player.config?.timeout_ms ?? DEFAULT_MOVE_TIMEOUT_MS,
    async move(view, seat) {
      const r = await api.play({ player: player.id, view, seat });
      return { move: r.move ?? "", raw: r.raw ?? "", note: r.note ?? "" };
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
 */
export async function runMatch({ api, game, players, seed = 1, maxTurns = 0, onEvent = null }) {
  const t0 = nowMs();
  const emit = (type, data) => onEvent?.({ type, ...data });

  const [bytes, meta] = await Promise.all([api.blob(game), api.module(game).catch(() => null)]);
  const host = await instantiate(bytes, { info: meta?.info ?? meta, seed });
  const g = new Game(new Abi(host));
  const info = g.info();
  emit("game", { info, module: game });

  const seats = players.length;
  if (seats < (info.min_players ?? 1)) {
    throw new Error(`${info.name || short(game)} needs at least ${info.min_players} players, got ${seats}`);
  }
  if (info.max_players && seats > info.max_players) {
    throw new Error(`${info.name || short(game)} seats at most ${info.max_players} players, got ${seats}`);
  }

  const drivers = [];
  for (const p of players) drivers.push(await driverFor(p, { api, seed, onEvent }));

  g.init(seed);
  const limit = maxTurns || info.max_turns || 200;
  const turns = [];
  const tally = players.map(() => ({ moves: 0, illegal: 0, timeouts: 0, ms: 0, error: "" }));

  let turnNo = 0;
  while (turnNo < limit && !g.done()) {
    const active = g.turn(seats, turnNo);
    if (!active.length) break;

    // Everyone to move this turn moves at once and sees only their own view —
    // which is what makes simultaneous games (and hidden information) work.
    const asked = await Promise.all(
      active.map(async (seat) => {
        const view = g.view(seat);
        const started = nowMs();
        let move = "", raw = "", note = "", failed = "";
        try {
          const r = await withTimeout(drivers[seat].move(view, seat), drivers[seat].timeout,
                                      `${players[seat].name}'s move`);
          move = (r.move ?? "").trim();
          raw = r.raw ?? "";
          note = r.note ?? "";
        } catch (e) {
          failed = e.message || String(e);
          if (e.timeout) tally[seat].timeouts++;
          tally[seat].error ||= failed;
        }
        const ms = Math.round(nowMs() - started);
        tally[seat].ms += ms;
        tally[seat].moves++;
        return { seat, view, move, raw, note, failed, ms };
      }),
    );

    const moves = {};
    for (const a of asked) moves[a.seat] = a.move;
    const { legal, note } = g.step(moves);

    for (const a of asked) {
      // No answer is not a legal move. The game still gets the empty string,
      // so it can rule on a forfeit however it wants to.
      const ok = a.failed ? false : legal[a.seat] ?? legal[String(a.seat)] ?? true;
      if (!ok) tally[a.seat].illegal++;
      const row = {
        turn: turnNo, seat: a.seat, view: a.view, raw: a.raw || a.move, mv: a.move,
        legal: ok, ms: a.ms,
        note: [a.failed, a.note, note].filter(Boolean).join(" · "),
      };
      turns.push(row);
      emit("turn", row);
    }
    turnNo++;
  }

  const result = g.result();
  const scores = players.map((_, i) => Number(result.scores?.[i] ?? 0));
  const record = {
    game,
    game_name: info.name || meta?.name || short(game),
    seed,
    // Checking for `window` would be wrong: matches play inside a Worker,
    // which has no window and is very much a browser.
    runtime: typeof process !== "undefined" && process.versions?.node ? "node" : "browser",
    summary: result.summary || "",
    ms: Math.round(nowMs() - t0),
    seats: players.map((p, i) => ({
      seat: i, player_id: p.id, player_name: p.name, score: scores[i],
      moves: tally[i].moves, illegal: tally[i].illegal, timeouts: tally[i].timeouts,
      ms: tally[i].ms, error: tally[i].error,
    })),
    turns,
  };
  emit("done", { record, result });
  return record;
}
