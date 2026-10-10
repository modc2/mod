// The smallest useful artifact: text in, JSON out.
//
// A js artifact defines `run(input, ctx)`. That is the whole contract. `ctx`
// carries the host: ctx.log, ctx.random, ctx.now, ctx.seed — and nothing else,
// because nothing else could be replayed.

function run(input, ctx) {
  const nums = (input || '').split(/[,\s]+/).map(Number).filter((n) => !isNaN(n));
  nums.sort((a, b) => a - b);
  ctx.log(`sorted ${nums.length} numbers`);
  return JSON.stringify({
    sorted: nums,
    sum: nums.reduce((a, b) => a + b, 0),
    median: nums.length ? nums[Math.floor(nums.length / 2)] : null,
  });
}
