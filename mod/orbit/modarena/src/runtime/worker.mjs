// The sandbox: a Worker that runs one module, or plays one match, and can be
// terminated from outside.
//
// This is the only part of the execution layer that is browser-only, and it
// exists for one reason: a wasm call that never returns cannot be interrupted
// from inside the same thread. Uploaded modules are other people's code, so
// the page must be able to stop one without stopping itself. `terminate()` on
// a worker does exactly that.
//
// The protocol is two messages in, three out:
//
//   in   { type: "run",   bytes, info, opts }
//   in   { type: "match", base, game, players, seed, maxTurns }
//   out  { type: "event", event }      progress, while it plays
//   out  { type: "done",  result }
//   out  { type: "error", error }

import { run } from "./host.mjs";
import { makeApi, runMatch } from "./match.mjs";

self.onmessage = async (e) => {
  const msg = e.data || {};
  try {
    if (msg.type === "run") {
      const result = await run(msg.bytes, msg.opts || {});
      self.postMessage({ type: "done", result });
      return;
    }
    if (msg.type === "match") {
      const api = makeApi(msg.base || "");
      const record = await runMatch({
        api,
        game: msg.game,
        players: msg.players,
        seed: msg.seed,
        maxTurns: msg.maxTurns || 0,
        // A human seat cannot be asked from in here — there is no UI in a
        // worker. The console plays those matches on its own thread.
        onEvent: (event) => self.postMessage({ type: "event", event }),
      });
      self.postMessage({ type: "done", result: record });
      return;
    }
    self.postMessage({ type: "error", error: `unknown message \`${msg.type}\`` });
  } catch (err) {
    self.postMessage({ type: "error", error: err?.message || String(err) });
  }
};
