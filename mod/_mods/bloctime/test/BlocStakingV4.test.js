const { expect } = require('chai');
const { ethers } = require('hardhat');

describe('BlocStakingV4', function () {
  let mainToken, blocStaking;
  let owner, creator, user1, user2;
  const INITIAL_SLOT_PRICE = ethers.parseEther('100');
  const MAX_CONCURRENT_USERS = 5;

  beforeEach(async function () {
    [owner, creator, user1, user2] = await ethers.getSigners();

    // Deploy main token
    const MockERC20 = await ethers.getContractFactory('MockERC20');
    mainToken = await MockERC20.deploy('Main Token', 'MAIN');
    await mainToken.waitForDeployment();

    // Deploy BlocStakingV4
    const BlocStakingV4 = await ethers.getContractFactory('BlocStakingV4');
    blocStaking = await BlocStakingV4.deploy(await mainToken.getAddress());
    await blocStaking.waitForDeployment();

    // Mint tokens
    await mainToken.mint(creator.address, ethers.parseEther('1000000'));
    await mainToken.mint(user1.address, ethers.parseEther('1000000'));
    await mainToken.mint(user2.address, ethers.parseEther('1000000'));
  });

  describe('Bloc Registration', function () {
    it('Should register a new bloc with paired token', async function () {
      const tx = await blocStaking.connect(creator).registerBloc(
        'QmTest123',
        INITIAL_SLOT_PRICE,
        MAX_CONCURRENT_USERS
      );
      
      await expect(tx).to.emit(blocStaking, 'BlocRegistered');
      
      const blocInfo = await blocStaking.getBlocInfo(creator.address, 0);
      expect(blocInfo.ipfsHash).to.equal('QmTest123');
      expect(blocInfo.active).to.be.true;
      expect(blocInfo.slotPrice).to.equal(INITIAL_SLOT_PRICE);
      expect(blocInfo.maxConcurrentUsers).to.equal(MAX_CONCURRENT_USERS);
      expect(blocInfo.blocTokenAddress).to.not.equal(ethers.ZeroAddress);
    });

    it('Should create unique BlocToken for each bloc', async function () {
      await blocStaking.connect(creator).registerBloc('QmTest1', INITIAL_SLOT_PRICE, MAX_CONCURRENT_USERS);
      await blocStaking.connect(creator).registerBloc('QmTest2', INITIAL_SLOT_PRICE, MAX_CONCURRENT_USERS);
      
      const bloc1Info = await blocStaking.getBlocInfo(creator.address, 0);
      const bloc2Info = await blocStaking.getBlocInfo(creator.address, 1);
      
      expect(bloc1Info.blocTokenAddress).to.not.equal(bloc2Info.blocTokenAddress);
    });
  });

  describe('Token Conversion', function () {
    beforeEach(async function () {
      await blocStaking.connect(creator).registerBloc('QmTest', INITIAL_SLOT_PRICE, MAX_CONCURRENT_USERS);
    });

    it('Should convert main token to BlocToken', async function () {
      const convertAmount = ethers.parseEther('1000');
      const blocInfo = await blocStaking.getBlocInfo(creator.address, 0);
      const blocToken = await ethers.getContractAt('BlocTokenERC20', blocInfo.blocTokenAddress);
      
      await mainToken.connect(user1).approve(await blocStaking.getAddress(), convertAmount);
      
      await expect(
        blocStaking.connect(user1).convertToBlocToken(creator.address, 0, convertAmount)
      ).to.emit(blocStaking, 'TokensConverted');
      
      const blocTokenBalance = await blocToken.balanceOf(user1.address);
      expect(blocTokenBalance).to.equal(convertAmount); // 1:1 conversion
    });
  });

  describe('Slot Bidding', function () {
    beforeEach(async function () {
      await blocStaking.connect(creator).registerBloc('QmTest', INITIAL_SLOT_PRICE, MAX_CONCURRENT_USERS);
      
      // Convert tokens for user1
      const convertAmount = ethers.parseEther('10000');
      await mainToken.connect(user1).approve(await blocStaking.getAddress(), convertAmount);
      await blocStaking.connect(user1).convertToBlocToken(creator.address, 0, convertAmount);
    });

    it('Should allow bidding for slot with BlocToken', async function () {
      const blocInfo = await blocStaking.getBlocInfo(creator.address, 0);
      const blocToken = await ethers.getContractAt('BlocTokenERC20', blocInfo.blocTokenAddress);
      
      const bidAmount = INITIAL_SLOT_PRICE * 2n; // 2 intervals
      await blocToken.connect(user1).approve(await blocStaking.getAddress(), bidAmount);
      
      await expect(
        blocStaking.connect(user1).bidForSlot(creator.address, 0, 2)
      ).to.emit(blocStaking, 'SlotPurchased');
      
      const hasSlot = await blocStaking.hasActiveSlot(creator.address, 0, user1.address);
      expect(hasSlot).to.be.true;
    });

    it('Should respect max concurrent users limit', async function () {
      const blocInfo = await blocStaking.getBlocInfo(creator.address, 0);
      const blocToken = await ethers.getContractAt('BlocTokenERC20', blocInfo.blocTokenAddress);
      
      // Fill all slots
      for (let i = 0; i < MAX_CONCURRENT_USERS; i++) {
        const bidAmount = INITIAL_SLOT_PRICE;
        await blocToken.connect(user1).approve(await blocStaking.getAddress(), bidAmount);
        await blocStaking.connect(user1).bidForSlot(creator.address, 0, 1);
      }
      
      // Try to bid for one more slot - should fail
      await mainToken.connect(user2).approve(await blocStaking.getAddress(), ethers.parseEther('1000'));
      await blocStaking.connect(user2).convertToBlocToken(creator.address, 0, ethers.parseEther('1000'));
      
      const blocToken2 = await ethers.getContractAt('BlocTokenERC20', blocInfo.blocTokenAddress);
      await blocToken2.connect(user2).approve(await blocStaking.getAddress(), INITIAL_SLOT_PRICE);
      
      await expect(
        blocStaking.connect(user2).bidForSlot(creator.address, 0, 1)
      ).to.be.revertedWith('No slots available');
    });
  });

  describe('Creator Controls', function () {
    beforeEach(async function () {
      await blocStaking.connect(creator).registerBloc('QmTest', INITIAL_SLOT_PRICE, MAX_CONCURRENT_USERS);
    });

    it('Should allow creator to update slot price', async function () {
      const newPrice = ethers.parseEther('200');
      
      await expect(
        blocStaking.connect(creator).updateSlotPrice(0, newPrice)
      ).to.emit(blocStaking, 'SlotPriceUpdated');
      
      const blocInfo = await blocStaking.getBlocInfo(creator.address, 0);
      expect(blocInfo.slotPrice).to.equal(newPrice);
    });

    it('Should allow creator to reduce max concurrent users', async function () {
      const newMax = 3;
      
      await expect(
        blocStaking.connect(creator).updateMaxConcurrentUsers(0, newMax)
      ).to.emit(blocStaking, 'MaxConcurrentUsersUpdated');
      
      const blocInfo = await blocStaking.getBlocInfo(creator.address, 0);
      expect(blocInfo.maxConcurrentUsers).to.equal(newMax);
    });

    it('Should not allow exceeding initial max concurrent users', async function () {
      const newMax = MAX_CONCURRENT_USERS + 1;
      
      await expect(
        blocStaking.connect(creator).updateMaxConcurrentUsers(0, newMax)
      ).to.be.revertedWith('Cannot exceed initial max');
    });

    it('Should not allow non-creator to update settings', async function () {
      await expect(
        blocStaking.connect(user1).updateSlotPrice(0, ethers.parseEther('200'))
      ).to.be.revertedWith('Not creator');
    });
  });
});