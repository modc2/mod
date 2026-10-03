const { expect } = require("chai");
const { ethers } = require("hardhat");

// BlocTime's window, in the test's own arithmetic rather than read back from
// the contract — a test that asks the code under test what the answer is only
// proves it is consistent with itself.
const WEEK = 7 * 24 * 3600;
const OFFSET = 24 * 3600 + 17 * 3600;

function windowStart(ts) {
  const b = Math.floor(ts / WEEK) * WEEK + OFFSET;
  return b <= ts ? b : b - WEEK;
}
const nextWindow = (ts) => windowStart(ts) + WEEK;

async function now() {
  return (await ethers.provider.getBlock("latest")).timestamp;
}

async function jumpTo(ts) {
  await ethers.provider.send("evm_setNextBlockTimestamp", [ts]);
  await ethers.provider.send("evm_mine", []);
}

/// Move to the next distribution window the contract itself would accept.
async function openWindow(treasury) {
  const at = Number(await treasury.nextDistributionTime());
  const t = await now();
  if (t < at) await jumpTo(at + 1);
}

const one = (n) => ethers.parseUnits(String(n), 18);

describe("ModBlocTimeTreasury", function () {
  let asset, bloc, treasury, owner, alice, bob, carol;

  beforeEach(async function () {
    [owner, alice, bob, carol] = await ethers.getSigners();
    const Token = await ethers.getContractFactory("MockToken");
    asset = await Token.deploy("USD Coin", "USDC", 18);
    bloc = await Token.deploy("BlocTime", "BLOC", 18);

    const T = await ethers.getContractFactory("ModBlocTimeTreasury");
    treasury = await T.deploy(await asset.getAddress(), await bloc.getAddress(), owner.address);

    // Weights: alice 75%, bob 25%. Carol holds BLOC but never registers.
    await bloc.mint(alice.address, one(750));
    await bloc.mint(bob.address, one(250));
    await bloc.mint(carol.address, one(1000));

    await asset.mint(owner.address, one(1_000_000));
    await asset.approve(await treasury.getAddress(), ethers.MaxUint256);
  });

  describe("the clock", function () {
    it("is BlocTime's, to the second", async function () {
      const t = await now();
      expect(Number(await treasury.windowStart(t))).to.equal(windowStart(t));
      expect(Number(await treasury.nextDistributionTime())).to.equal(nextWindow(t));
    });

    it("every window it names is a Friday at 17:00 UTC", async function () {
      let at = Number(await treasury.nextDistributionTime());
      for (let i = 0; i < 20; i++) {
        const d = new Date(at * 1000);
        expect(d.getUTCDay()).to.equal(5); // Friday
        expect(d.getUTCHours()).to.equal(17);
        at += WEEK;
      }
    });

    it("refuses to pay before the window opens", async function () {
      await treasury.connect(alice).register();
      await treasury.lock(one(700), 7, false);
      await expect(treasury.distribute()).to.be.revertedWith("NOT_DISTRIBUTION_TIME");
    });

    it("pays at most once a week", async function () {
      await treasury.connect(alice).register();
      await treasury.lock(one(700), 7, false);
      await openWindow(treasury);
      await treasury.distribute();
      await expect(treasury.distribute()).to.be.revertedWith("NOT_DISTRIBUTION_TIME");
    });
  });

  describe("the lock", function () {
    it("takes the principal and will not give it back early", async function () {
      await treasury.connect(alice).register();
      await treasury.lock(one(1000), 10, true);
      expect(await asset.balanceOf(await treasury.getAddress())).to.equal(one(1000));
      expect(await treasury.lockedOutstanding()).to.equal(one(1000));
      await expect(treasury.withdraw(0)).to.be.revertedWith("STILL_LOCKED");
    });

    it("will not let the owner rescue the asset out from under a lock", async function () {
      await treasury.lock(one(1000), 10, true);
      await expect(
        treasury.rescue(await asset.getAddress(), owner.address)
      ).to.be.revertedWith("ASSET_IS_LOCKED");
    });

    it("hands an escrowed principal back once the term is out, and not before", async function () {
      await treasury.connect(alice).register();
      await treasury.lock(one(1000), 2, true);
      const unlocksAt = Number(await treasury.unlockTime(0));
      await jumpTo(unlocksAt - 60);
      await expect(treasury.withdraw(0)).to.be.revertedWith("STILL_LOCKED");
      await jumpTo(unlocksAt + 1);
      const before = await asset.balanceOf(owner.address);
      await treasury.withdraw(0);
      expect(await asset.balanceOf(owner.address)).to.equal(before + one(1000));
      expect(await treasury.lockedOutstanding()).to.equal(0);
    });

    it("streams a principal out in equal weekly slices, remainder in the last one", async function () {
      await treasury.connect(alice).register();
      // 1000 over 3 weeks: 333.33… does not divide, so the last slice takes
      // the remainder rather than leaving dust stranded in the lock.
      await treasury.lock(one(1000), 3, false);
      const slice = one(1000) / 3n;

      for (let week = 1; week <= 3; week++) {
        await openWindow(treasury);
        await treasury.distribute();
        const info = await treasury.lockInfo(0);
        if (week < 3) {
          expect(info.released).to.equal(slice * BigInt(week));
        } else {
          expect(info.released).to.equal(one(1000));
          expect(info.closed).to.equal(true);
        }
      }
      expect(await treasury.lockedOutstanding()).to.equal(0);
      expect(await treasury.claimable(alice.address)).to.equal(one(1000));
    });

    it("pays nothing out of an escrowed lock — only the yield on top", async function () {
      await treasury.connect(alice).register();
      await treasury.lock(one(1000), 4, true);
      await openWindow(treasury);
      await expect(treasury.distribute()).to.be.revertedWith("NOTHING_TO_DISTRIBUTE");

      // Yield arrives: someone sends the treasury more of the asset.
      await treasury.fund(one(50));
      await openWindow(treasury);
      await treasury.distribute();
      expect(await treasury.claimable(alice.address)).to.equal(one(50));
      // …and the principal is still whole.
      expect(await treasury.lockedOutstanding()).to.equal(one(1000));
    });
  });

  describe("the split", function () {
    it("is pro-rata by BLOC across the registered set", async function () {
      await treasury.connect(alice).register();
      await treasury.connect(bob).register();
      await treasury.lock(one(1000), 1, false);
      await openWindow(treasury);
      await treasury.distribute();
      expect(await treasury.claimable(alice.address)).to.equal(one(750));
      expect(await treasury.claimable(bob.address)).to.equal(one(250));
    });

    it("pays a holder who never registered exactly nothing", async function () {
      await treasury.connect(alice).register();
      await treasury.connect(bob).register();
      await treasury.lock(one(1000), 1, false);
      await openWindow(treasury);
      await treasury.distribute();
      // Carol holds more BLOC than either of them and is not in the set.
      expect(await bloc.balanceOf(carol.address)).to.equal(one(1000));
      expect(await treasury.claimable(carol.address)).to.equal(0);
    });

    it("follows the BLOC, not the registration — a holder who sold gets less", async function () {
      await treasury.connect(alice).register();
      await treasury.connect(bob).register();
      // Alice sells half her BLOC to Bob before the payout.
      await bloc.connect(alice).transfer(bob.address, one(375));
      await treasury.lock(one(1000), 1, false);
      await openWindow(treasury);
      await treasury.distribute();
      expect(await treasury.claimable(alice.address)).to.equal(one(375));
      expect(await treasury.claimable(bob.address)).to.equal(one(625));
    });

    it("refuses to pay into a vacuum", async function () {
      await treasury.lock(one(1000), 1, false);
      await openWindow(treasury);
      await expect(treasury.distribute()).to.be.revertedWith("NO_WEIGHT_REGISTERED");
    });

    it("leaves rounding dust in the pot rather than gifting it to whoever is last", async function () {
      await treasury.connect(alice).register();
      await treasury.connect(bob).register();
      await treasury.connect(carol).register();
      // 1 wei across three unequal weights cannot divide.
      await treasury.lock(1000n, 1, false);
      await openWindow(treasury);
      await treasury.distribute();
      const paid =
        (await treasury.claimable(alice.address)) +
        (await treasury.claimable(bob.address)) +
        (await treasury.claimable(carol.address));
      expect(paid).to.be.lte(1000n);
      // whatever did not divide is still in hand, ready for next week
      expect(await treasury.distributable()).to.equal(1000n - paid);
    });
  });

  describe("claiming", function () {
    it("pays out, once", async function () {
      await treasury.connect(alice).register();
      await treasury.lock(one(700), 1, false);
      await openWindow(treasury);
      await treasury.distribute();
      await treasury.connect(alice).claim();
      expect(await asset.balanceOf(alice.address)).to.equal(one(700));
      await expect(treasury.connect(alice).claim()).to.be.revertedWith("NOTHING");
    });

    it("never lets a claim eat a lock's principal", async function () {
      await treasury.connect(alice).register();
      await treasury.lock(one(1000), 4, true); // escrowed, nothing to distribute
      await treasury.lock(one(400), 4, false); // streaming, 100/week
      await openWindow(treasury);
      await treasury.distribute();
      await treasury.connect(alice).claim();
      expect(await asset.balanceOf(alice.address)).to.equal(one(100));
      // the escrowed 1000 plus the 300 not yet streamed are all still here
      expect(await asset.balanceOf(await treasury.getAddress())).to.equal(one(1300));
      expect(await treasury.lockedOutstanding()).to.equal(one(1300));
    });

    it("keeps a leaver's credit but stops their future share", async function () {
      await treasury.connect(alice).register();
      await treasury.connect(bob).register();
      await treasury.lock(one(1000), 2, false);
      await openWindow(treasury);
      await treasury.distribute();
      expect(await treasury.claimable(bob.address)).to.equal(one(125));

      await treasury.connect(bob).deregister();
      await openWindow(treasury);
      await treasury.distribute();
      // Bob keeps week one and gets nothing from week two.
      expect(await treasury.claimable(bob.address)).to.equal(one(125));
      expect(await treasury.claimable(alice.address)).to.equal(one(375) + one(500));
      await treasury.connect(bob).claim();
      expect(await asset.balanceOf(bob.address)).to.equal(one(125));
    });
  });

  describe("summary", function () {
    it("is what a dashboard would otherwise make eight calls for", async function () {
      await treasury.connect(alice).register();
      await treasury.lock(one(1000), 10, false);
      await treasury.fund(one(30));
      const s = await treasury.summary();
      expect(s.balance).to.equal(one(1030));
      expect(s.locked).to.equal(one(1000));
      expect(s.holders).to.equal(1);
      expect(s.weightRegistered).to.equal(one(750));
      // 30 of surplus plus this week's 100 slice
      expect(s.payoutNow).to.equal(one(130));
      expect(s.due).to.equal(false);
      expect(Number(s.nextAt)).to.equal(nextWindow(await now()));
    });
  });
});
