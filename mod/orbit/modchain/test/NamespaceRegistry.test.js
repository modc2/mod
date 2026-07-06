const { expect } = require('chai');
const { ethers } = require('hardhat');

describe('NamespaceRegistry', function () {
  let stt, registry, owner, alice, bob, carol;
  const MIN = ethers.parseEther('1');

  beforeEach(async () => {
    [owner, alice, bob, carol] = await ethers.getSigners();

    const STT = await ethers.getContractFactory('MockSTT');
    stt = await STT.deploy();
    await stt.waitForDeployment();

    const Reg = await ethers.getContractFactory('NamespaceRegistry');
    registry = await Reg.deploy(await stt.getAddress(), MIN);
    await registry.waitForDeployment();

    for (const u of [alice, bob, carol]) {
      await stt.mint(u.address, ethers.parseEther('1000'));
      await stt.connect(u).approve(await registry.getAddress(), ethers.MaxUint256);
    }
  });

  it('claims an unowned name', async () => {
    await registry.connect(alice).claim('polymarket', ethers.parseEther('10'), 'http://localhost:3000', 'app');
    const c = await registry.getClaim('polymarket');
    expect(c.owner).to.equal(alice.address);
    expect(c.targetUrl).to.equal('http://localhost:3000');
    expect(c.kind).to.equal('app');
    expect(c.stake).to.equal(ethers.parseEther('10'));
    expect(c.active).to.equal(true);
  });

  it('rejects below minClaimStake', async () => {
    await expect(
      registry.connect(alice).claim('x', ethers.parseEther('0.5'), 'http://x', 'app')
    ).to.be.revertedWith('below floor');
  });

  it('rejects equal stake outbid', async () => {
    await registry.connect(alice).claim('foo', ethers.parseEther('5'), 'http://a', 'app');
    await expect(
      registry.connect(bob).claim('foo', ethers.parseEther('5'), 'http://b', 'app')
    ).to.be.revertedWith('must outbid');
  });

  it('higher staketime wins the name; previous owner becomes withdrawable', async () => {
    await registry.connect(alice).claim('foo', ethers.parseEther('5'), 'http://a', 'app');
    await registry.connect(bob).claim('foo', ethers.parseEther('10'), 'http://b', 'app');

    const c = await registry.getClaim('foo');
    expect(c.owner).to.equal(bob.address);
    expect(c.targetUrl).to.equal('http://b');
    expect(c.stake).to.equal(ethers.parseEther('10'));

    expect(await registry.withdrawable(alice.address)).to.equal(ethers.parseEther('5'));

    const before = await stt.balanceOf(alice.address);
    await registry.connect(alice).withdraw();
    const after = await stt.balanceOf(alice.address);
    expect(after - before).to.equal(ethers.parseEther('5'));
  });

  it('topUp raises the outbid floor', async () => {
    await registry.connect(alice).claim('foo', ethers.parseEther('5'), 'http://a', 'app');
    await registry.connect(alice).topUp('foo', ethers.parseEther('20'));
    expect((await registry.getClaim('foo')).stake).to.equal(ethers.parseEther('25'));
    expect(await registry.outbidThreshold('foo')).to.equal(ethers.parseEther('25') + 1n);

    await expect(
      registry.connect(bob).claim('foo', ethers.parseEther('25'), 'http://b', 'app')
    ).to.be.revertedWith('must outbid');
  });

  it('owner can update target without changing stake', async () => {
    await registry.connect(alice).claim('foo', ethers.parseEther('5'), 'http://a', 'app');
    await registry.connect(alice).setTarget('foo', 'http://a2', 'api');
    const c = await registry.getClaim('foo');
    expect(c.targetUrl).to.equal('http://a2');
    expect(c.kind).to.equal('api');
    expect(c.stake).to.equal(ethers.parseEther('5'));
  });

  it('release returns stake and frees the name', async () => {
    await registry.connect(alice).claim('foo', ethers.parseEther('5'), 'http://a', 'app');
    const before = await stt.balanceOf(alice.address);
    await registry.connect(alice).release('foo');
    const after = await stt.balanceOf(alice.address);
    expect(after - before).to.equal(ethers.parseEther('5'));
    expect(await registry.exists('foo')).to.equal(false);

    // someone else can now claim cheaply
    await registry.connect(bob).claim('foo', MIN, 'http://b', 'app');
    expect((await registry.getClaim('foo')).owner).to.equal(bob.address);
  });

  it('getActiveClaims lists only active names', async () => {
    await registry.connect(alice).claim('a', MIN, 'http://a', 'app');
    await registry.connect(bob).claim('b', MIN, 'http://b', 'api');
    await registry.connect(alice).release('a');
    const claims = await registry.getActiveClaims();
    expect(claims.length).to.equal(1);
    expect(claims[0].name).to.equal('b');
    expect(claims[0].kind).to.equal('api');
  });

  it('rejects bad names and kinds', async () => {
    await expect(
      registry.connect(alice).claim('bad name', MIN, 'http://x', 'app')
    ).to.be.revertedWith('name chars');
    await expect(
      registry.connect(alice).claim('ok', MIN, 'http://x', 'cli')
    ).to.be.revertedWith('kind app|api');
    await expect(
      registry.connect(alice).claim('', MIN, 'http://x', 'app')
    ).to.be.revertedWith('bad name');
  });

  it('outbidThreshold reports floor for unclaimed and stake+1 for claimed', async () => {
    expect(await registry.outbidThreshold('foo')).to.equal(MIN);
    await registry.connect(alice).claim('foo', ethers.parseEther('7'), 'http://a', 'app');
    expect(await registry.outbidThreshold('foo')).to.equal(ethers.parseEther('7') + 1n);
  });
});
