const { expect } = require('chai');
const { ethers } = require('hardhat');

describe('ZconPrivacyTokenV2', function () {
  let zconTokenV2, merkleTree;
  let owner, user1, user2;

  beforeEach(async function () {
    [owner, user1, user2] = await ethers.getSigners();
    
    const ZconPrivacyTokenV2 = await ethers.getContractFactory('ZconPrivacyTokenV2');
    zconTokenV2 = await ZconPrivacyTokenV2.deploy();
    await zconTokenV2.waitForDeployment();
    
    const merkleTreeAddress = await zconTokenV2.merkleTree();
    merkleTree = await ethers.getContractAt('ZconMerkleTree', merkleTreeAddress);
  });

  it('Should deposit ETH and insert into merkle tree', async function () {
    const amount = ethers.parseEther('1.0');
    const commitment = ethers.keccak256(ethers.toUtf8Bytes('secret123'));

    await expect(
      zconTokenV2.connect(user1).deposit(commitment, ethers.ZeroAddress, amount, { value: amount })
    ).to.emit(zconTokenV2, 'DepositMade');

    const deposit = await zconTokenV2.deposits(commitment);
    expect(deposit.amount).to.equal(amount);
    expect(deposit.token).to.equal(ethers.ZeroAddress);
  });

  it('Should prevent double commitment', async function () {
    const amount = ethers.parseEther('1.0');
    const commitment = ethers.keccak256(ethers.toUtf8Bytes('secret123'));

    await zconTokenV2.connect(user1).deposit(commitment, ethers.ZeroAddress, amount, { value: amount });

    await expect(
      zconTokenV2.connect(user1).deposit(commitment, ethers.ZeroAddress, amount, { value: amount })
    ).to.be.revertedWith('Commitment already used');
  });

  it('Should track merkle tree root', async function () {
    const commitment = ethers.keccak256(ethers.toUtf8Bytes('secret123'));
    const amount = ethers.parseEther('1.0');

    await zconTokenV2.connect(user1).deposit(commitment, ethers.ZeroAddress, amount, { value: amount });
    
    const root = await merkleTree.root();
    expect(root).to.not.equal(ethers.ZeroHash);
    
    const isKnown = await merkleTree.isKnownRoot(root);
    expect(isKnown).to.be.true;
  });
});