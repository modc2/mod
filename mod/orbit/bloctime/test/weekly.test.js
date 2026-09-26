const { expect } = require("chai");
const { ethers, network } = require("hardhat");

const WEEK = 7 * 24 * 3600;
const OFFSET = 24 * 3600 + 17 * 3600; // Friday 12:00 EST == Friday 17:00 UTC

async function now() {
  return (await ethers.provider.getBlock("latest")).timestamp;
}

async function warpTo(ts) {
  await network.provider.send("evm_setNextBlockTimestamp", [ts]);
  await network.provider.send("evm_mine");
}

// What a holder of `balance` gets from a `pot` split across `supply`, with the
// same truncation the accumulator does (rounding dust stays in the pot).
function share(pot, balance, supply) {
  return ((pot * 10n ** 18n) / supply) * balance / 10n ** 18n;
}

async function deploy() {
  const [owner, alice, bob] = await ethers.getSigners();
  const NativeToken = await ethers.getContractFactory("NativeToken");
  const token = await NativeToken.deploy(ethers.parseEther("1000000"));
  const BlocTime = await ethers.getContractFactory("BlocTime");
  // maxLockSeconds 100000, price $1.00 (1e6 micro-USD per token).
  const bt = await BlocTime.deploy(await token.getAddress(), 100000, 1_000_000);

  // Alice and Bob each stake for 1 second at $1: BLOC == usd × seconds ==
  // staked amount, and the lock expires by the next block.
  for (const [who, amount] of [[alice, "300"], [bob, "100"]]) {
    const wei = ethers.parseEther(amount);
    await token.transfer(who.address, wei);
    await token.connect(who).approve(await bt.getAddress(), wei);
    await bt.connect(who).stake(wei, 1);
  }
  return { bt, token, owner, alice, bob };
}

describe("BlocTime linear USD × seconds model", function () {
  async function fund(bt, token, who, amount) {
    await token.transfer(who.address, amount);
    await token.connect(who).approve(await bt.getAddress(), amount);
  }

  it("mints usd value × seconds locked, flat by default", async function () {
    const { bt, token, alice } = await deploy();
    const wei = ethers.parseEther("2");
    // $1/token, 1000s lock: 2 × 1 × 1000 = 2000 BLOC.
    expect(await bt.quoteBloc(wei, 1000)).to.equal(ethers.parseEther("2000"));
    await fund(bt, token, alice, wei);
    const before = await bt.balanceOf(alice.address);
    await bt.connect(alice).stake(wei, 1000);
    expect((await bt.balanceOf(alice.address)) - before).to.equal(ethers.parseEther("2000"));
  });

  it("scales with the owner-set USD price", async function () {
    const { bt, token, alice } = await deploy();
    await bt.setPriceUsd(2_500_000); // $2.50
    const wei = ethers.parseEther("4");
    // 4 × $2.50 × 10s = 100 BLOC.
    expect(await bt.quoteBloc(wei, 10)).to.equal(ethers.parseEther("100"));
    await expect(bt.connect(alice).setPriceUsd(1)).to.be.reverted; // owner only
  });

  it("locks by wall-clock seconds, not blocks", async function () {
    const { bt, token, alice } = await deploy();
    const wei = ethers.parseEther("1");
    await fund(bt, token, alice, wei);
    await bt.connect(alice).stake(wei, 5000);
    const ids = await bt.getUserStakeIds(alice.address);
    const sid = ids[ids.length - 1];

    await expect(bt.connect(alice).unstake(sid)).to.be.revertedWith("Still locked");
    const pos = await bt.getStakePosition(alice.address, sid);
    expect(pos.lockSeconds).to.equal(5000n);
    expect(pos.secondsRemaining).to.be.greaterThan(0n);

    await warpTo((await now()) + 5000);
    await bt.connect(alice).unstake(sid);
  });

  it("rejects locks that round to zero BLOC or exceed the cap", async function () {
    const { bt, token, alice } = await deploy();
    const wei = ethers.parseEther("1");
    await fund(bt, token, alice, wei);
    await expect(bt.connect(alice).stake(wei, 0)).to.be.revertedWith("Lock too short");
    await expect(bt.connect(alice).stake(wei, 100001)).to.be.revertedWith("Exceeds max lock");
  });

  it("exposes secondsPerBlock for the blocks toggle", async function () {
    const { bt } = await deploy();
    const p = await bt.params();
    expect(p.maxLockSeconds).to.equal(100000n);
    expect(p.secondsPerBlock).to.equal(2n);
    await bt.setParams(200000, 12);
    const p2 = await bt.params();
    expect(p2.maxLockSeconds).to.equal(200000n);
    expect(p2.secondsPerBlock).to.equal(12n);
  });
});

describe("BlocTime weekly pot", function () {
  it("schedules the next payout on a Friday at 12:00 EST", async function () {
    const { bt } = await deploy();
    const next = Number(await bt.nextDistributionTime());

    expect(next % WEEK).to.equal(OFFSET);          // Friday 17:00 UTC
    expect(new Date(next * 1000).getUTCDay()).to.equal(5);
    expect(new Date(next * 1000).getUTCHours()).to.equal(17);
    expect(next).to.be.greaterThan(await now());
    expect(next - (await now())).to.be.at.most(WEEK);
    expect(await bt.distributionDue()).to.equal(false);
  });

  it("refuses to distribute before the window", async function () {
    const { bt, owner } = await deploy();
    await expect(bt.distributeRewards()).to.be.revertedWith("Not distribution time");

    // ...even one second early (hardhat mines the tx a second after the warp).
    await warpTo(Number(await bt.nextDistributionTime()) - 2);
    await expect(bt.connect(owner).distributeRewards()).to.be.revertedWith("Not distribution time");
  });

  it("sweeps the whole pot to holders pro-rata", async function () {
    const { bt, alice, bob } = await deploy();
    const pot = ethers.parseEther("40");
    await bt.connect(alice).fundPot(pot); // alice: 300 -> 260 BLOC, pot 40

    expect(await bt.rewardPot()).to.equal(pot);
    expect(await bt.distributableSupply()).to.equal(ethers.parseEther("360"));

    await warpTo(Number(await bt.nextDistributionTime()));
    await bt.distributeRewards();

    const supply = ethers.parseEther("360");
    expect(await bt.rewardPot()).to.be.lessThan(1000n); // rounding dust only
    // 260/360 and 100/360 of the pot.
    expect(await bt.earned(alice.address)).to.equal(share(pot, ethers.parseEther("260"), supply));
    expect(await bt.earned(bob.address)).to.equal(share(pot, ethers.parseEther("100"), supply));

    const before = await bt.balanceOf(bob.address);
    await bt.connect(bob).claimRewards();
    expect(await bt.balanceOf(bob.address)).to.equal(
      before + share(pot, ethers.parseEther("100"), supply)
    );
    expect(await bt.earned(bob.address)).to.equal(0n);
  });

  it("pays out once a week, no matter how often it is called", async function () {
    const { bt, alice } = await deploy();
    await bt.connect(alice).fundPot(ethers.parseEther("40"));

    const first = Number(await bt.nextDistributionTime());
    await warpTo(first);
    await bt.distributeRewards();
    expect(await bt.lastDistributionTime()).to.equal(first);
    expect(await bt.nextDistributionTime()).to.equal(first + WEEK);

    // Same window, and every day up to the next Friday: still closed.
    await expect(bt.distributeRewards()).to.be.revertedWith("Not distribution time");
    await warpTo(first + WEEK - 60);
    await expect(bt.distributeRewards()).to.be.revertedWith("Not distribution time");

    await bt.connect(alice).fundPot(ethers.parseEther("36"));
    await warpTo(first + WEEK);
    await bt.distributeRewards();
    expect(await bt.lastDistributionTime()).to.equal(first + WEEK);
  });

  it("catches up to the current Friday after a missed week", async function () {
    const { bt, alice } = await deploy();
    await bt.connect(alice).fundPot(ethers.parseEther("40"));

    const first = Number(await bt.nextDistributionTime());
    await warpTo(first + 3 * WEEK + 3600); // three windows missed
    await bt.distributeRewards();

    // Payout lands on the window we are in, so the schedule does not drift.
    expect(await bt.lastDistributionTime()).to.equal(first + 3 * WEEK);
    expect(await bt.nextDistributionTime()).to.equal(first + 4 * WEEK);
  });

  it("mints the epochs owed into the pot before sweeping it", async function () {
    const { bt, alice } = await deploy();
    await bt.setInflationParams(ethers.parseEther("50"), 1460, 0, 10); // 10-second epochs

    await warpTo((await now()) + 300);
    const epoch = await bt.currentEpoch();
    expect(epoch).to.be.greaterThan(0n);

    const info = await bt.getPotInfo();
    expect(info.pendingInflation).to.equal(ethers.parseEther("50") * epoch);

    await warpTo(Number(await bt.nextDistributionTime()));
    await bt.distributeRewards();

    expect(await bt.totalDistributed()).to.be.greaterThan(0n);
    expect(await bt.earned(alice.address)).to.be.greaterThan(0n);
    expect((await bt.getPotInfo()).pendingInflation).to.equal(0n);
  });

  it("does not hand transferred BLOC a share of older rewards", async function () {
    const { bt, alice, bob } = await deploy();
    await bt.connect(alice).fundPot(ethers.parseEther("40"));
    await warpTo(Number(await bt.nextDistributionTime()));
    await bt.distributeRewards();

    const bobEarned = await bt.earned(bob.address);
    await bt.connect(bob).transfer(alice.address, await bt.balanceOf(bob.address));

    // Bob keeps what he earned while holding; alice gains nothing retroactive.
    expect(await bt.earned(bob.address)).to.equal(bobEarned);
    expect(await bt.earned(alice.address)).to.equal(
      share(ethers.parseEther("40"), ethers.parseEther("260"), ethers.parseEther("360"))
    );
  });

  it("never promises more than the contract holds", async function () {
    const { bt, alice, bob } = await deploy();
    await bt.connect(alice).fundPot(ethers.parseEther("40"));
    await warpTo(Number(await bt.nextDistributionTime()));
    await bt.distributeRewards();
    await bt.connect(bob).claimRewards();

    // Second week, after a claim changed the balances it accrues against.
    await bt.connect(alice).fundPot(ethers.parseEther("36"));
    await warpTo(Number(await bt.nextDistributionTime()));
    await bt.distributeRewards();

    const owed = (await bt.earned(alice.address)) + (await bt.earned(bob.address));
    const held = await bt.balanceOf(await bt.getAddress());
    expect(held - (await bt.rewardPot())).to.be.greaterThanOrEqual(owed);
  });

  it("does not double-count voting power on self-delegation", async function () {
    const { bt, alice, bob } = await deploy();
    const aliceBal = await bt.balanceOf(alice.address); // 300

    expect(await bt.getVotingPower(alice.address)).to.equal(aliceBal);
    await bt.connect(alice).delegate(alice.address);
    expect(await bt.getVotingPower(alice.address)).to.equal(aliceBal);

    // Bob delegates in on top: alice holds her own weight plus his, once each.
    await bt.connect(bob).delegate(alice.address);
    const bobBal = await bt.balanceOf(bob.address); // 100
    expect(await bt.getVotingPower(alice.address)).to.equal(aliceBal + bobBal);
    expect(await bt.getVotingPower(bob.address)).to.equal(0n);
  });
});
