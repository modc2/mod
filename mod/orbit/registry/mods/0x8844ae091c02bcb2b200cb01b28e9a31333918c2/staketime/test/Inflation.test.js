const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("Inflation Curves", function () {
  const E = ethers.parseEther;

  // ── InflationFlat ──────────────────────────────────────────────────────

  describe("InflationFlat", function () {
    let flat;
    const rate = E("100");

    beforeEach(async function () {
      const F = await ethers.getContractFactory("InflationFlat");
      flat = await F.deploy(rate);
    });

    it("returns constant emission every epoch", async function () {
      for (let epoch = 0; epoch < 50; epoch++) {
        expect(await flat.getEmission(epoch)).to.equal(rate);
      }
    });

    it("emission is identical at epoch 0 and epoch 1000", async function () {
      expect(await flat.getEmission(0)).to.equal(await flat.getEmission(1000));
    });
  });

  // ── InflationHalving ───────────────────────────────────────────────────

  describe("InflationHalving", function () {
    let halving;
    const initial = E("1000");
    const interval = 10n;
    const floor = E("10");

    beforeEach(async function () {
      const F = await ethers.getContractFactory("InflationHalving");
      halving = await F.deploy(initial, interval, floor);
    });

    it("epoch 0 returns initial rate", async function () {
      expect(await halving.getEmission(0)).to.equal(initial);
    });

    it("halves at each interval", async function () {
      // epoch 0-9: 1000, epoch 10-19: 500, epoch 20-29: 250
      expect(await halving.getEmission(0)).to.equal(E("1000"));
      expect(await halving.getEmission(5)).to.equal(E("1000"));
      expect(await halving.getEmission(10)).to.equal(E("500"));
      expect(await halving.getEmission(15)).to.equal(E("500"));
      expect(await halving.getEmission(20)).to.equal(E("250"));
      expect(await halving.getEmission(30)).to.equal(E("125"));
    });

    it("respects floor", async function () {
      // After enough halvings, emission should hit floor
      expect(await halving.getEmission(100)).to.equal(floor);
      expect(await halving.getEmission(200)).to.equal(floor);
    });

    it("simulates 100 epochs of cumulative supply", async function () {
      let totalSupply = 0n;
      const schedule = [];
      for (let epoch = 0; epoch < 100; epoch++) {
        const emission = await halving.getEmission(epoch);
        totalSupply += emission;
        if (epoch % 10 === 0) {
          schedule.push({ epoch, emission: ethers.formatEther(emission), totalSupply: ethers.formatEther(totalSupply) });
        }
      }
      // Verify supply is monotonically increasing
      expect(totalSupply).to.be.gt(0n);
      // Verify last emission is at floor
      expect(await halving.getEmission(99)).to.equal(floor);
    });
  });

  // ── InflationLinearDecay ───────────────────────────────────────────────

  describe("InflationLinearDecay", function () {
    let linear;
    const initial = E("1000");
    const floor = E("100");
    const decayEpochs = 100n;

    beforeEach(async function () {
      const F = await ethers.getContractFactory("InflationLinearDecay");
      linear = await F.deploy(initial, floor, decayEpochs);
    });

    it("epoch 0 returns initial rate", async function () {
      expect(await linear.getEmission(0)).to.equal(initial);
    });

    it("reaches floor at decayEpochs", async function () {
      expect(await linear.getEmission(100)).to.equal(floor);
    });

    it("stays at floor after decayEpochs", async function () {
      expect(await linear.getEmission(200)).to.equal(floor);
      expect(await linear.getEmission(500)).to.equal(floor);
    });

    it("decays linearly", async function () {
      // At epoch 50 (halfway): should be (1000 + 100) / 2 = 550
      const mid = await linear.getEmission(50);
      expect(mid).to.equal(E("550"));
    });

    it("simulates 150 epochs of cumulative supply", async function () {
      let totalSupply = 0n;
      let prevEmission = initial;
      for (let epoch = 0; epoch < 150; epoch++) {
        const emission = await linear.getEmission(epoch);
        totalSupply += emission;
        // Verify monotonically decreasing until floor
        if (epoch < 100) {
          expect(emission).to.be.lte(prevEmission);
        } else {
          expect(emission).to.equal(floor);
        }
        prevEmission = emission;
      }
      expect(totalSupply).to.be.gt(0n);
    });
  });

  // ── InflationSigmoid ───────────────────────────────────────────────────

  describe("InflationSigmoid", function () {
    let sigmoid;
    const peak = E("1000");
    const floor = E("10");
    const totalEpochs = 100n;

    beforeEach(async function () {
      const F = await ethers.getContractFactory("InflationSigmoid");
      sigmoid = await F.deploy(peak, floor, totalEpochs);
    });

    it("epoch 0 starts at floor", async function () {
      expect(await sigmoid.getEmission(0)).to.equal(floor);
    });

    it("peaks at midpoint", async function () {
      const mid = await sigmoid.getEmission(50);
      // At midpoint, quadratic ratio = 1.0, so emission = peak
      expect(mid).to.equal(peak);
    });

    it("returns to floor at totalEpochs", async function () {
      expect(await sigmoid.getEmission(100)).to.equal(floor);
    });

    it("stays at floor beyond totalEpochs", async function () {
      expect(await sigmoid.getEmission(200)).to.equal(floor);
    });

    it("s-curve: ramps up then decays", async function () {
      const e10 = await sigmoid.getEmission(10);
      const e25 = await sigmoid.getEmission(25);
      const e50 = await sigmoid.getEmission(50);
      const e75 = await sigmoid.getEmission(75);
      const e90 = await sigmoid.getEmission(90);

      // Ramp up phase
      expect(e25).to.be.gt(e10);
      expect(e50).to.be.gt(e25);
      // Decay phase (symmetric)
      expect(e75).to.be.lt(e50);
      expect(e90).to.be.lt(e75);
      // Symmetry: e25 ≈ e75
      expect(e25).to.equal(e75);
    });

    it("simulates 120 epochs of cumulative supply", async function () {
      let totalSupply = 0n;
      let maxEmission = 0n;
      let maxEpoch = 0;
      for (let epoch = 0; epoch < 120; epoch++) {
        const emission = await sigmoid.getEmission(epoch);
        totalSupply += emission;
        if (emission > maxEmission) {
          maxEmission = emission;
          maxEpoch = epoch;
        }
      }
      // Peak should be at or near midpoint
      expect(maxEpoch).to.equal(50);
      expect(maxEmission).to.equal(peak);
      expect(totalSupply).to.be.gt(0n);
    });
  });

  // ── InflationTAO ───────────────────────────────────────────────────────

  describe("InflationTAO", function () {
    let tao;
    const initialRate = E("1000");
    const supplyCap = E("21000000");
    const floor = E("10");

    beforeEach(async function () {
      const F = await ethers.getContractFactory("InflationTAO");
      tao = await F.deploy(initialRate, supplyCap, floor);
    });

    it("epoch 0 returns initial rate (no minting yet)", async function () {
      expect(await tao.getEmission(0)).to.equal(initialRate);
    });

    it("emission decreases as supply grows", async function () {
      const e0 = await tao.getEmission(0);
      // Simulate minting 10% of supply
      await tao.recordMint(supplyCap / 10n);
      const e1 = await tao.getEmission(0);
      expect(e1).to.be.lt(e0);
      // Should be ~90% of initial
      expect(e1).to.equal(E("900"));
    });

    it("emission drops to floor near cap", async function () {
      // Mint 99% of supply
      await tao.recordMint(supplyCap * 99n / 100n);
      const emission = await tao.getEmission(0);
      expect(emission).to.equal(floor);
    });

    it("emission is 0 at cap", async function () {
      await tao.recordMint(supplyCap);
      expect(await tao.getEmission(0)).to.equal(0n);
    });

    it("simulates 100 minting rounds approaching cap", async function () {
      let totalMinted = 0n;
      let prevEmission = initialRate;

      for (let i = 0; i < 100; i++) {
        const emission = await tao.getEmission(0);
        if (emission === 0n) break;
        await tao.recordMint(emission);
        totalMinted += emission;
        // Emission should decrease (or equal if at floor)
        expect(emission).to.be.lte(prevEmission);
        prevEmission = emission;
      }
      // Should approach but not exceed cap
      expect(totalMinted).to.be.lte(supplyCap);
      expect(totalMinted).to.be.gt(0n);
    });
  });

  // ── InflationBTC ───────────────────────────────────────────────────────

  describe("InflationBTC", function () {
    let btc;
    const initialReward = E("50");       // like 50 BTC per block
    const halvingInterval = 10n;          // halve every 10 epochs for testing
    const supplyCap = E("21000000");     // 21M cap

    beforeEach(async function () {
      const F = await ethers.getContractFactory("InflationBTC");
      btc = await F.deploy(initialReward, halvingInterval, supplyCap);
    });

    it("epoch 0 returns initial reward", async function () {
      expect(await btc.getEmission(0)).to.equal(initialReward);
    });

    it("halves at each interval", async function () {
      expect(await btc.getEmission(0)).to.equal(E("50"));
      expect(await btc.getEmission(10)).to.equal(E("25"));
      expect(await btc.getEmission(20)).to.equal(E("12.5"));
      expect(await btc.getEmission(30)).to.equal(E("6.25"));
    });

    it("emission reaches 0 after enough halvings", async function () {
      // After 64 halvings, emission is 0
      expect(await btc.getEmission(640)).to.equal(0n);
    });

    it("clamps to remaining supply", async function () {
      // Mint almost all supply
      await btc.recordMint(supplyCap - E("10"));
      const emission = await btc.getEmission(0);
      // Should be clamped to remaining 10 tokens
      expect(emission).to.equal(E("10"));
    });

    it("emission is 0 at cap", async function () {
      await btc.recordMint(supplyCap);
      expect(await btc.getEmission(0)).to.equal(0n);
    });

    it("simulates BTC-like supply schedule", async function () {
      let totalMinted = 0n;
      const milestones = [];

      for (let epoch = 0; epoch < 100; epoch++) {
        const emission = await btc.getEmission(epoch);
        if (emission === 0n) break;

        // Don't exceed cap
        const remaining = supplyCap - totalMinted;
        const actual = emission < remaining ? emission : remaining;
        if (actual === 0n) break;

        await btc.recordMint(actual);
        totalMinted += actual;

        if (epoch % 10 === 0) {
          milestones.push({
            epoch,
            emission: ethers.formatEther(emission),
            totalMinted: ethers.formatEther(totalMinted),
          });
        }
      }

      expect(totalMinted).to.be.lte(supplyCap);
      expect(totalMinted).to.be.gt(0n);
      // Epochs 0-9 at 50 each = 500, epoch 10 halves to 25
      expect(totalMinted).to.be.gt(E("500"));
    });
  });

  // ── Cross-Curve Comparison ─────────────────────────────────────────────

  describe("Cross-Curve Comparison (50 epochs)", function () {
    it("all curves produce different supply profiles", async function () {
      const [flat, halving, linear, sigmoid] = await Promise.all([
        (await ethers.getContractFactory("InflationFlat")).deploy(E("100")),
        (await ethers.getContractFactory("InflationHalving")).deploy(E("100"), 10, E("10")),
        (await ethers.getContractFactory("InflationLinearDecay")).deploy(E("100"), E("10"), 50),
        (await ethers.getContractFactory("InflationSigmoid")).deploy(E("100"), E("10"), 50),
      ]);

      const supplies = { flat: 0n, halving: 0n, linear: 0n, sigmoid: 0n };

      for (let epoch = 0; epoch < 50; epoch++) {
        supplies.flat += await flat.getEmission(epoch);
        supplies.halving += await halving.getEmission(epoch);
        supplies.linear += await linear.getEmission(epoch);
        supplies.sigmoid += await sigmoid.getEmission(epoch);
      }

      // Flat should be highest (constant 100 * 50 = 5000)
      expect(supplies.flat).to.equal(E("5000"));

      // All should be different
      const vals = Object.values(supplies);
      const unique = new Set(vals.map(v => v.toString()));
      expect(unique.size).to.equal(4);

      // Flat >= Linear >= Halving (generally, flat is always highest)
      expect(supplies.flat).to.be.gte(supplies.linear);
      expect(supplies.flat).to.be.gte(supplies.halving);
    });
  });
});
