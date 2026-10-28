const { expect } = require("chai");
const { ethers, network } = require("hardhat");
const cases = require("../../yuma/fixtures/cases.json");

const E18 = 10n ** 18n;
const TEMPO = 3600;

async function warp(seconds) {
  await network.provider.send("evm_increaseTime", [seconds]);
  await network.provider.send("evm_mine");
}

function params(over = {}) {
  return {
    tempo: TEMPO,
    emissionPerEpoch: 100n * E18,
    kappa: E18 / 2n,
    bondAlpha: E18 / 10n,
    ownerCutBps: 1000,
    minerShareBps: 5000,
    minValidatorBloc: 0,
    maxValidators: 16,
    maxMiners: 64,
    ...over,
  };
}

// Reward token = a staketime Mod (zero supply, minted only by its consensus),
// so Yuma is the "consensus" that mints it.
async function deployMinted(power, p) {
  const Mod = await ethers.getContractFactory("Mod");
  const reward = await Mod.deploy("Yuma", "YUMA");
  const Yuma = await ethers.getContractFactory("YumaBlocTime");
  const yuma = await Yuma.deploy(await power.getAddress(), await reward.getAddress(), true, p);
  await reward.setMinter(await yuma.getAddress());
  return { yuma, reward };
}

describe("YumaBlocTime — fixtures shared with yuma.py", function () {
  for (const c of cases) {
    it(`matches python: ${c.name}`, async function () {
      const signers = await ethers.getSigners();
      const [owner] = signers;
      const V = c.stakes.length;
      const M = c.epochs[0][0].length;
      const vals = signers.slice(1, 1 + V);
      const mins = signers.slice(1 + V, 1 + V + M);

      const power = await (await ethers.getContractFactory("MockVotingPower")).deploy();
      // registration needs power > 0; zero-stake validators get it zeroed afterwards
      for (let i = 0; i < V; i++) await power.set(vals[i].address, 1);
      const { yuma, reward } = await deployMinted(power, params({
        kappa: BigInt(c.params.kappa),
        bondAlpha: BigInt(c.params.bond_alpha),
        ownerCutBps: Number(c.params.owner_cut_bps),
        minerShareBps: Number(c.params.miner_share_bps),
        emissionPerEpoch: BigInt(c.params.emission),
      }));
      for (const v of vals) await yuma.connect(v).registerValidator();
      for (const m of mins) await yuma.connect(m).registerMiner();
      for (let i = 0; i < V; i++) await power.set(vals[i].address, BigInt(c.stakes[i]));

      const paid = new Map();
      const add = (a, x) => paid.set(a, (paid.get(a) || 0n) + x);

      for (let e = 0; e < c.epochs.length; e++) {
        const W = c.epochs[e];
        for (let i = 0; i < V; i++) {
          await yuma.connect(vals[i]).setWeights([...Array(M).keys()], W[i]);
        }
        await warp(TEMPO);
        await yuma.epoch();
        const want = c.expected[e];
        for (let j = 0; j < M; j++) {
          expect(await yuma.lastIncentive(j)).to.equal(BigInt(want.incentive[j]), `I[${j}] epoch ${e}`);
          expect(await yuma.lastConsensus(j)).to.equal(BigInt(want.consensus[j]), `C[${j}] epoch ${e}`);
        }
        for (let i = 0; i < V; i++) {
          expect(await yuma.lastDividend(i)).to.equal(BigInt(want.dividends[i]), `D[${i}] epoch ${e}`);
          const b = await yuma.getBonds(i);
          for (let j = 0; j < M; j++) expect(b[j]).to.equal(BigInt(want.bonds[i][j]), `B[${i}][${j}]`);
        }
        want.miner_pay.forEach((x, j) => add(mins[j].address, BigInt(x)));
        want.validator_pay.forEach((x, i) => add(vals[i].address, BigInt(x)));
        add(owner.address, BigInt(want.owner_pay));
      }

      let total = 0n;
      for (const [addr, amt] of paid) {
        expect(await yuma.pending(addr)).to.equal(amt, `pending ${addr}`);
        total += amt;
      }
      expect(await yuma.totalPending()).to.equal(total);
      expect(await reward.balanceOf(await yuma.getAddress())).to.equal(total);
    });
  }
});

describe("YumaBlocTime on a real BlocTime", function () {
  async function setup(p = {}) {
    const [owner, alice, bob, carol, m1, m2, m3] = await ethers.getSigners();
    const NativeToken = await ethers.getContractFactory("NativeToken");
    const nat = await NativeToken.deploy(ethers.parseEther("1000000"));
    const BlocTime = await ethers.getContractFactory("BlocTime");
    const bt = await BlocTime.deploy(await nat.getAddress(), 8 * 365 * 86400, 1_000_000);
    const stake = async (who, amount, secs) => {
      const wei = ethers.parseEther(amount);
      await nat.transfer(who.address, wei);
      await nat.connect(who).approve(await bt.getAddress(), wei);
      await bt.connect(who).stake(wei, secs);
    };
    const { yuma, reward } = await deployMinted(bt, params(p));
    return { owner, alice, bob, carol, m1, m2, m3, nat, bt, yuma, reward, stake };
  }

  it("stake is BlocTime voting power: lock time beats size", async function () {
    const { alice, bob, m1, m2, bt, yuma, reward, stake } = await setup({ ownerCutBps: 0 });
    await stake(alice, "10", 30 * 86400);   // 10 NAT × 30 days
    await stake(bob, "1000", 60);           // 1000 NAT × 1 minute
    expect(await bt.getVotingPower(alice.address)).to.be.gt(await bt.getVotingPower(bob.address));

    await yuma.connect(alice).registerValidator();
    await yuma.connect(bob).registerValidator();
    await yuma.connect(m1).registerMiner();
    await yuma.connect(m2).registerMiner();
    await yuma.connect(alice).setWeights([0], [1]);   // alice backs m1
    await yuma.connect(bob).setWeights([1], [1]);     // bob backs m2
    await warp(TEMPO);
    await yuma.epoch();

    expect(await yuma.lastIncentive(0)).to.equal(E18);   // alice's 25.9M BLOC is the consensus
    expect(await yuma.lastIncentive(1)).to.equal(0n);
    expect(await yuma.lastDividend(0)).to.equal(E18);
    await yuma.connect(m1).claim();
    expect(await reward.balanceOf(m1.address)).to.equal(50n * E18);
    await expect(yuma.connect(m2).claim()).to.be.revertedWith("nothing to claim");
  });

  it("delegated BLOC counts toward the validator", async function () {
    const { alice, bob, carol, m1, bt, yuma, stake } = await setup();
    await stake(alice, "10", 3600);
    await stake(carol, "100", 3600);
    await bt.connect(carol).delegate(alice.address);
    await yuma.connect(alice).registerValidator();
    await yuma.connect(m1).registerMiner();
    await yuma.connect(alice).setWeights([0], [1]);
    const [stakeNow] = await yuma.preview();
    expect(stakeNow[0]).to.equal(await bt.getVotingPower(alice.address));
    expect(stakeNow[0]).to.equal(ethers.parseEther(String(110 * 3600)));
  });

  it("gates: tempo, min BlocTime, replacement by stake", async function () {
    const { alice, bob, carol, m1, yuma, stake } = await setup({
      maxValidators: 1, minValidatorBloc: ethers.parseEther("1000"),
    });
    await expect(yuma.connect(alice).registerValidator()).to.be.revertedWith("not enough BlocTime");
    await stake(alice, "1", 2000);
    await stake(bob, "1", 1500);
    await stake(carol, "1", 3000);
    await yuma.connect(alice).registerValidator();
    await expect(yuma.connect(bob).registerValidator()).to.be.revertedWith(
      "set full: need more BlocTime than the weakest");
    await yuma.connect(carol).registerValidator();         // 3000 > 2000: alice is out
    expect(await yuma.validatorUid1(alice.address)).to.equal(0n);
    expect(await yuma.validators(0)).to.equal(carol.address);

    await yuma.connect(m1).registerMiner();
    await yuma.connect(carol).setWeights([0], [1]);
    await expect(yuma.epoch()).to.be.revertedWith("epoch not due");
    await warp(TEMPO);
    await yuma.epoch();
    await expect(yuma.epoch()).to.be.revertedWith("epoch not due");
  });

  it("a recycled miner uid does not inherit old weights", async function () {
    const { alice, m1, m2, m3, yuma, stake } = await setup({ maxMiners: 2 });
    await stake(alice, "1", 3600);
    await yuma.connect(alice).registerValidator();
    await yuma.connect(m1).registerMiner();
    await yuma.connect(m2).registerMiner();
    await expect(yuma.connect(m3).registerMiner()).to.be.revertedWith("set full: everyone is immune");
    await yuma.connect(alice).setWeights([0, 1], [1, 5]);  // m1 is the weakest
    await warp(TEMPO);
    await yuma.epoch();
    await yuma.connect(m3).registerMiner();                 // takes m1's uid 0
    expect(await yuma.miners(0)).to.equal(m3.address);
    expect(await yuma.minerUid1(m1.address)).to.equal(0n);

    await warp(TEMPO);
    await yuma.epoch();                                     // alice's old row scored m1, not m3
    expect(await yuma.lastIncentive(0)).to.equal(0n);
    expect(await yuma.lastIncentive(1)).to.equal(E18);

    await yuma.connect(alice).setWeights([0, 1], [1, 1]);  // a fresh row counts m3
    await warp(TEMPO);
    await yuma.epoch();
    expect(await yuma.lastIncentive(0)).to.equal(E18 / 2n);
  });

  it("funded mode pays only what was funded", async function () {
    const [owner, alice, m1] = await ethers.getSigners();
    const nat = await (await ethers.getContractFactory("NativeToken")).deploy(ethers.parseEther("1000000"));
    const bt = await (await ethers.getContractFactory("BlocTime")).deploy(await nat.getAddress(), 86400, 1_000_000);
    await nat.transfer(alice.address, ethers.parseEther("1"));
    await nat.connect(alice).approve(await bt.getAddress(), ethers.parseEther("1"));
    await bt.connect(alice).stake(ethers.parseEther("1"), 60);
    const yuma = await (await ethers.getContractFactory("YumaBlocTime"))
      .deploy(await bt.getAddress(), await nat.getAddress(), false, params({ ownerCutBps: 0 }));
    await yuma.connect(alice).registerValidator();
    await yuma.connect(m1).registerMiner();
    await yuma.connect(alice).setWeights([0], [1]);
    await nat.approve(await yuma.getAddress(), 40n * E18);
    await yuma.fund(40n * E18);
    await warp(TEMPO);
    await yuma.epoch();                                     // wants 100, only 40 there
    expect(await yuma.pending(m1.address)).to.equal(20n * E18);
    expect(await yuma.pending(alice.address)).to.equal(20n * E18);
    expect(await yuma.available()).to.equal(0n);
    await warp(TEMPO);
    await yuma.epoch();                                     // empty: emits nothing
    expect(await yuma.totalEmitted()).to.equal(40n * E18);
  });
});
