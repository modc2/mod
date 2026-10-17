// Monte Carlo π — a computation that looks unrepeatable and isn't.
//
// This is the example worth reading. It is built out of the two things that
// normally make a result impossible to check — randomness and a clock — and it
// is still bit-for-bit reproducible, because the host hands the guest a PRNG
// seeded from the run's own seed and a clock that counts rather than ticks.
//
// So: run it in your tab, publish the claim, and let the server replay it. The
// receipts match. Change the seed and they don't, which is the point — the
// seed is part of the computation, not part of the weather.
//
//     input: how many samples to take (default 100000)
//     seed:  which run this is

function run(input, ctx) {
  const samples = Math.max(1, Math.min(parseInt(input, 10) || 100000, 5_000_000));
  let inside = 0;
  const started = Date.now();

  for (let i = 0; i < samples; i++) {
    const x = ctx.random();
    const y = ctx.random();
    if (x * x + y * y <= 1) inside++;
  }

  const pi = (4 * inside) / samples;
  ctx.log(`${samples} samples, ${inside} inside the quarter circle`);

  return JSON.stringify({
    samples,
    inside,
    pi,
    error: Math.abs(pi - Math.PI),
    // The host clock counts calls rather than milliseconds, so this is stable
    // across replays too — it measures the shape of the run, not the machine.
    clock_ticks: Date.now() - started,
    seed: ctx.seed,
  });
}
