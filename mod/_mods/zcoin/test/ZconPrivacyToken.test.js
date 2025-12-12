const { expect } = require('chai');
const { ethers } = require('hardhat');

describe('ZconPrivacyToken', function () {
  let zconToken;
  let owner, user1, user2;

  beforeEach(async function () {
    [owner, user1, user2] = await ethers.getSigners();
    const ZconPrivacyToken = await ethers.getContractFactory('ZconPrivacyToken');
    zconToken = await ZconPrivacyToken.deploy();
    await zconToken.waitForDeployment();
  });

  it('Should deposit ETH with commitment', async function () {
    const amount = ethers.parseEther('1.0');
    const commitment = ethers.keccak256(ethers.toUtf8Bytes('secret123'));

    await expect(
      zconToken.connect(user1).deposit(ethers.ZeroAddress, amount, commitment, { value: amount })
    ).to.emit(zconToken, 'Deposit').withArgs(commitment, ethers.ZeroAddress, amount);

    const comm = await zconToken.commitments(commitment);
    expect(comm.amount).to.equal(amount);
    expect(comm.spent).to.equal(false);
  });

  it('Should prevent double commitment', async function () {
    const amount = ethers.parseEther('1.0');
    const commitment = ethers.keccak256(ethers.toUtf8Bytes('secret123'));

    await zconToken.connect(user1).deposit(ethers.ZeroAddress, amount, commitment, { value: amount });

    await expect(
      zconToken.connect(user1).deposit(ethers.ZeroAddress, amount, commitment, { value: amount })
    ).to.be.revertedWith('Commitment exists');
  });
});
