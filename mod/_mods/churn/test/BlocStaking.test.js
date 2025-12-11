const { expect } = require('chai');
const { ethers } = require('hardhat');
const { time } = require('@nomicfoundation/hardhat-network-helpers');

describe('BlocStaking', function () {
  let token, blocStaking, owner, creator, user1, user2;
  const PRICE_PER_HOUR = ethers.parseEther('100');
  const STAKE_AMOUNT = ethers.parseEther('1000');

  beforeEach(async function () {
    [owner, creator, user1, user2] = await ethers.getSigners();

    // Deploy token
    const MockERC20 = await ethers.getContractFactory('MockERC20');
    token = await MockERC20.deploy('Churn Token', 'CHURN');
    await token.waitForDeployment();

    // Deploy BlocStaking
    const BlocStaking = await ethers.getContractFactory('BlocStaking');
    blocStaking = await BlocStaking.deploy(await token.getAddress(), PRICE_PER_HOUR);
    await blocStaking.waitForDeployment();

    // Distribute tokens
    await token.mint(creator.address, ethers.parseEther('100000'));
    await token.mint(user1.address, ethers.parseEther('100000'));
    await token.mint(user2.address, ethers.parseEther('100000'));
  });

  describe('Bloc Registration', function () {
    it('Should register a new bloc', async function () {
      const ipfsHash = 'QmTest123456789';
      const tx = await blocStaking.connect(creator).registerBloc(ipfsHash);
      const receipt = await tx.wait();
      
      const blocInfo = await blocStaking.getBlocInfo(0);
      expect(blocInfo.creator).to.equal(creator.address);
      expect(blocInfo.ipfsHash).to.equal(ipfsHash);
      expect(blocInfo.active).to.be.true;
    });

    it('Should update bloc IPFS hash', async function () {
      await blocStaking.connect(creator).registerBloc('QmOldHash');
      await blocStaking.connect(creator).updateBloc(0, 'QmNewHash');
      
      const blocInfo = await blocStaking.getBlocInfo(0);
      expect(blocInfo.ipfsHash).to.equal('QmNewHash');
    });

    it('Should deregister bloc', async function () {
      await blocStaking.connect(creator).registerBloc('QmTest');
      await blocStaking.connect(creator).deregisterBloc(0);
      
      const blocInfo = await blocStaking.getBlocInfo(0);
      expect(blocInfo.active).to.be.false;
    });
  });

  describe('Staking', function () {
    beforeEach(async function () {
      await blocStaking.connect(creator).registerBloc('QmTest');
    });

    it('Should allow users to stake', async function () {
      await token.connect(user1).approve(await blocStaking.getAddress(), STAKE_AMOUNT);
      await blocStaking.connect(user1).stake(0, STAKE_AMOUNT);

      const stakeInfo = await blocStaking.getStakeInfo(0, user1.address);
      expect(stakeInfo.amount).to.equal(STAKE_AMOUNT);
    });

    it('Should allow users to unstake', async function () {
      await token.connect(user1).approve(await blocStaking.getAddress(), STAKE_AMOUNT);
      await blocStaking.connect(user1).stake(0, STAKE_AMOUNT);
      
      const balanceBefore = await token.balanceOf(user1.address);
      await blocStaking.connect(user1).unstake(0, STAKE_AMOUNT);
      const balanceAfter = await token.balanceOf(user1.address);

      expect(balanceAfter - balanceBefore).to.equal(STAKE_AMOUNT);
    });
  });

  describe('Exclusive Access', function () {
    beforeEach(async function () {
      await blocStaking.connect(creator).registerBloc('QmTest');
    });

    it('Should purchase exclusive access', async function () {
      const duration = 2; // 2 hours
      const cost = PRICE_PER_HOUR * BigInt(duration);
      
      await token.connect(user1).approve(await blocStaking.getAddress(), cost);
      await blocStaking.connect(user1).purchaseExclusiveAccess(0, duration);

      const hasAccess = await blocStaking.hasExclusiveAccess(0, user1.address);
      expect(hasAccess).to.be.true;
    });

    it('Should prevent access when locked by another user', async function () {
      const duration = 2;
      const cost = PRICE_PER_HOUR * BigInt(duration);
      
      await token.connect(user1).approve(await blocStaking.getAddress(), cost);
      await blocStaking.connect(user1).purchaseExclusiveAccess(0, duration);

      await token.connect(user2).approve(await blocStaking.getAddress(), cost);
      await expect(
        blocStaking.connect(user2).purchaseExclusiveAccess(0, duration)
      ).to.be.revertedWith('Bloc currently locked by another user');
    });

    it('Should allow access after expiration', async function () {
      const duration = 1;
      const cost = PRICE_PER_HOUR * BigInt(duration);
      
      await token.connect(user1).approve(await blocStaking.getAddress(), cost);
      await blocStaking.connect(user1).purchaseExclusiveAccess(0, duration);

      // Fast forward past expiration
      await time.increase(3600 + 1);

      await token.connect(user2).approve(await blocStaking.getAddress(), cost);
      await blocStaking.connect(user2).purchaseExclusiveAccess(0, duration);

      const hasAccess = await blocStaking.hasExclusiveAccess(0, user2.address);
      expect(hasAccess).to.be.true;
    });
  });
});
