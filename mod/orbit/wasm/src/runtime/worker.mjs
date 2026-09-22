// The browser venue: the same execute(), in a Worker the page can kill.
//
// A Worker is what gives the tab the one limit it can actually enforce —
// wasm cannot be interrupted from the outside, so a run that never returns is
// stopped by terminating the thread it is on. It also keeps a heavy module
// off the UI thread, which is the difference between "computing" and "frozen".
//
// The result posted back is a *claim*. It is signed by nobody, produced on a
// machine the marketplace does not control, and is worth exactly as much as
// the replay that agrees with it — see receipts.py. That is not a weakness of
// running in the browser; it is why running in the browser is safe to allow.

import { execute } from './engines.mjs';

self.onmessage = async (event) => {
  const job = event.data || {};
  try {
    const artifact = job.artifact_b64
      ? Uint8Array.from(atob(job.artifact_b64), (c) => c.charCodeAt(0))
      : job.artifact;
    const result = await execute({ ...job, artifact });
    self.postMessage({ ...result, venue: 'browser', id: job.id });
  } catch (e) {
    self.postMessage({
      ok: false,
      venue: 'browser',
      engine: job.engine || 'wasm',
      id: job.id,
      error: `${e.constructor?.name || 'Error'}: ${e.message}`,
    });
  }
};
