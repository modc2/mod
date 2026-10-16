// Same numbers as tests/test_engine.py.
const { expect } = require('chai');
const { ethers } = require('hardhat');
const { time } = require('@nomicfoundation/hardhat-network-helpers');

const USD = 10n ** 6n;
const DTT = 10n ** 18n;
const DAY = 86400;
const DAILY = 7200n * DTT;
const CAP = 21_000_000n * DTT;

describe('DollarTime', () => {
  let usd, dt, a, b, c;

  beforeEach(async () => {
    [a, b, c] = await ethers.getSigners();
    usd = await ethers.deployContract('MockUSD');
    dt = await ethers.deployContract('DollarTime', [await usd.getAddress(), 'GPU box', 'DTT']);
    for (const s of [a, b, c]) {
      await usd.mint(s.address, 10_000n * USD);
      await usd.connect(s).approve(await dt.getAddress(), ethers.MaxUint256);
    }
  });

  it('schedule sums to the cap', async () => {
    expect(await dt.FULL_DAYS()).to.equal(2916n);
    expect(await dt.LAST_DAY_EMISSION()).to.equal(4800n * DTT);
    expect(await dt.emission(0)).to.equal(DAILY);
    expect(await dt.emission(2916)).to.equal(4800n * DTT);
    expect(await dt.emission(2917)).to.equal(0n);
    expect(await dt.cumulative(2916) + await dt.emission(2916)).to.equal(CAP);
    expect(await dt.cumulative(5000)).to.equal(CAP);
  });

  it('weight is dollars x time', async () => {
    expect(await dt.weightOf(100n * USD, 0)).to.equal(100n * USD * 365n);
    expect(await dt.weightOf(100n * USD, 365)).to.equal(100n * USD * 730n);
    expect(await dt.weightOf(1n * USD, 1460)).to.equal(USD * 1825n);
    await expect(dt.contribute(1n * USD, 1461)).to.be.revertedWith('lock too long');
  });

  it('splits a day by weight, claims after close', async () => {
    await dt.connect(a).contribute(100n * USD, 0);
    await dt.connect(b).contribute(100n * USD, 365);
    expect(await dt.dayWeight(0)).to.equal(100n * USD * 1095n);
    expect(await dt.claimable(1)).to.equal(0n);
    await expect(dt.connect(a).claim(1)).to.be.revertedWith('day still open');
    await time.increase(DAY);
    expect(await dt.claimable(1)).to.equal(DAILY * 365n / 1095n);
    expect(await dt.claimable(2)).to.equal(DAILY * 730n / 1095n);
    await dt.connect(a).claim(1);
    await dt.connect(b).claim(2);
    expect(await dt.totalSupply()).to.be.closeTo(DAILY, 2n);
    expect(await dt.burned()).to.equal(0n);
    expect(await dt.treasury()).to.equal(200n * USD);
    expect(await usd.balanceOf(await dt.getAddress())).to.equal(200n * USD);
    await expect(dt.connect(a).claim(1)).to.be.revertedWith('already claimed');
    await expect(dt.connect(b).claim(1)).to.be.revertedWith('not your position');
  });

  it('sole buyer gets all 7200', async () => {
    await dt.connect(a).contribute(1n * USD, 0);
    await time.increase(DAY);
    await dt.connect(a).claimAll();
    expect(await dt.balanceOf(a.address)).to.equal(DAILY);
  });

  it('burns an empty day, forever', async () => {
    await time.increase(3 * DAY);
    expect(await dt.currentDay()).to.equal(3n);
    expect(await dt.emitted()).to.equal(3n * DAILY);
    expect(await dt.burned()).to.equal(3n * DAILY);
    await dt.connect(a).contribute(5n * USD, 10);
    expect(await dt.burned()).to.equal(3n * DAILY);
    await time.increase(2 * DAY);
    expect(await dt.burned()).to.equal(4n * DAILY);
    expect(await dt.emitted()).to.equal(5n * DAILY);
  });

  it('cap is hard: 4800 on day 2916, nothing after', async () => {
    await time.increase(2916 * DAY);
    expect((await dt.stats()).emissionToday).to.equal(4800n * DTT);
    await dt.connect(a).contribute(1n * USD, 0);
    await time.increase(DAY);
    await dt.connect(a).claim(1);
    expect(await dt.balanceOf(a.address)).to.equal(4800n * DTT);
    expect(await dt.emitted()).to.equal(CAP);
    expect(await dt.burned()).to.equal(CAP - 4800n * DTT);
    await expect(dt.connect(a).contribute(1n * USD, 0)).to.be.revertedWith('emission is over');
  });

  it('locks transfers until unlock', async () => {
    await dt.connect(a).contribute(10n * USD, 30);
    await time.increase(DAY);
    await dt.connect(a).claim(1);
    expect(await dt.lockedBalance(a.address)).to.equal(DAILY);
    await expect(dt.connect(a).transfer(b.address, 1n * DTT)).to.be.revertedWith('amount exceeds unlocked balance');
    await time.increase(30 * DAY);
    await dt.connect(a).transfer(b.address, 1000n * DTT);
    expect(await dt.balanceOf(b.address)).to.equal(1000n * DTT);
    expect(await dt.lockedBalance(a.address)).to.equal(0n);
  });

  it('splits revenue by holding; transfers carry no past revenue', async () => {
    await dt.connect(a).contribute(300n * USD, 0);
    await dt.connect(b).contribute(100n * USD, 0);
    await time.increase(DAY);
    await dt.connect(a).claim(1);
    await dt.connect(b).claim(2);
    await dt.connect(c).depositRevenue(400n * USD, 'rent');
    expect(await dt.owed(a.address)).to.be.closeTo(300n * USD, 2n);
    expect(await dt.owed(b.address)).to.be.closeTo(100n * USD, 2n);
    const before = await usd.balanceOf(a.address);
    await dt.connect(a).withdrawRevenue();
    expect((await usd.balanceOf(a.address)) - before).to.be.closeTo(300n * USD, 2n);
    expect(await dt.owed(a.address)).to.equal(0n);
    await dt.connect(a).transfer(b.address, await dt.balanceOf(a.address));
    await dt.connect(c).depositRevenue(100n * USD, '');
    expect(await dt.owed(b.address)).to.be.closeTo(200n * USD, 2n);
    expect(await dt.owed(a.address)).to.equal(0n);
    expect(await dt.treasury()).to.equal(400n * USD); // revenue never touches the treasury
  });

  it('pools revenue in the treasury when no supply exists', async () => {
    await dt.connect(a).depositRevenue(50n * USD, 'early');
    expect(await dt.treasury()).to.equal(50n * USD);
    expect(await dt.accPerShare()).to.equal(0n);
  });

  it('steward spends the treasury and marks the asset', async () => {
    await dt.connect(a).contribute(100n * USD, 0);
    await dt.connect(b).contribute(100n * USD, 0);
    await expect(dt.connect(b).spend(c.address, 1n * USD, 'x')).to.be.revertedWith('Ownable: caller is not the owner');
    await dt.connect(a).spend(c.address, 150n * USD, 'buy the machine');
    expect(await dt.treasury()).to.equal(50n * USD);
    expect(await usd.balanceOf(c.address)).to.equal(10_150n * USD);
    await expect(dt.connect(a).spend(c.address, 51n * USD, 'x')).to.be.revertedWith('amount exceeds treasury');
    await dt.connect(a).setAsset('GPU box', 'one H100', 1000n * USD);
    await time.increase(DAY);
    await dt.connect(a).claim(1);
    await dt.connect(b).claim(2);
    expect(await dt.shareBps(a.address)).to.equal(5000n);
    expect(await dt.valueOfHolder(a.address)).to.equal(500n * USD);
  });
});
