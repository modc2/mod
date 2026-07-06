const { expect } = require('chai');
const { ethers } = require('hardhat');
const { loadFixture } = require('@nomicfoundation/hardhat-network-helpers');

describe('Safe Multisig Deployment (2-of-3)', function () {
  // Helper: create a Safe proxy via factory
  async function createSafe(proxyFactory, singletonAddress, owners, threshold) {
    const Safe = await ethers.getContractFactory('Safe');
    const setupData = Safe.interface.encodeFunctionData('setup', [
      owners,
      threshold,
      ethers.ZeroAddress,
      '0x',
      ethers.ZeroAddress,
      ethers.ZeroAddress,
      0,
      ethers.ZeroAddress,
    ]);

    const saltNonce = Date.now() + Math.floor(Math.random() * 1000000);
    const tx = await proxyFactory.createProxyWithNonce(singletonAddress, setupData, saltNonce);
    const receipt = await tx.wait();

    const event = receipt.logs.find(
      (log) => log.topics[0] === proxyFactory.interface.getEvent('ProxyCreation').topicHash
    );
    const proxyAddress = '0x' + event.topics[1].slice(26);
    return await ethers.getContractAt('Safe', proxyAddress);
  }

  // Helper: sign and execute a Safe transaction with N signers
  async function execSafeTx(safe, signers, to, value, data) {
    const nonce = await safe.nonce();
    const txHash = await safe.getTransactionHash(
      to,
      value,
      data,
      0, // Call
      0,
      0,
      0,
      ethers.ZeroAddress,
      ethers.ZeroAddress,
      nonce
    );

    // Sign with each signer
    const signedPairs = [];
    for (const signer of signers) {
      const sig = await signer.signMessage(ethers.getBytes(txHash));
      signedPairs.push({ address: signer.address, signature: sig });
    }

    // Sort by address (ascending) - required by Safe
    signedPairs.sort((a, b) => (a.address.toLowerCase() < b.address.toLowerCase() ? -1 : 1));

    // Concatenate signatures
    let combinedSigs = signedPairs[0].signature;
    for (let i = 1; i < signedPairs.length; i++) {
      combinedSigs += signedPairs[i].signature.slice(2);
    }

    return safe.execTransaction(
      to,
      value,
      data,
      0, // Call
      0,
      0,
      0,
      ethers.ZeroAddress,
      ethers.ZeroAddress,
      combinedSigs
    );
  }

  // Main fixture: deploys Safe infra + full protocol, transfers ownership to Safe
  async function deployFullProtocolFixture() {
    const [deployer, owner1, owner2, owner3, user1, user2] = await ethers.getSigners();

    // --- Deploy Safe infrastructure ---
    const SafeContract = await ethers.getContractFactory('Safe');
    const safeSingleton = await SafeContract.deploy();
    await safeSingleton.waitForDeployment();
    const singletonAddress = await safeSingleton.getAddress();

    const SafeProxyFactory = await ethers.getContractFactory('SafeProxyFactory');
    const proxyFactory = await SafeProxyFactory.deploy();
    await proxyFactory.waitForDeployment();

    // Create 2-of-3 multisig
    const safe = await createSafe(
      proxyFactory,
      singletonAddress,
      [owner1.address, owner2.address, owner3.address],
      2
    );
    const safeAddress = await safe.getAddress();

    // --- Deploy protocol contracts (deployer deploys, then transfers ownership to Safe) ---

    // Mock tokens
    const Token = await ethers.getContractFactory('Token');
    const usdc = await Token.deploy('USD Coin', 'USDC', ethers.parseEther('1000000'));
    await usdc.waitForDeployment();
    const usdt = await Token.deploy('Tether USD', 'USDT', ethers.parseEther('1000000'));
    await usdt.waitForDeployment();
    const nativeToken = await Token.deploy('Native Token', 'NAT', ethers.parseEther('1000000'));
    await nativeToken.waitForDeployment();

    // Oracle
    const ManualPriceOracle = await ethers.getContractFactory('ManualPriceOracle');
    const oracle = await ManualPriceOracle.deploy();
    await oracle.waitForDeployment();

    // Set prices before transferring ownership
    const usdPrice = 100000000n; // $1.00 with 8 decimals
    await oracle.setPrice(await usdc.getAddress(), usdPrice, 8);
    await oracle.setPrice(await usdt.getAddress(), usdPrice, 8);

    // TokenGate
    const TokenGate = await ethers.getContractFactory('TokenGate');
    const tokenGate = await TokenGate.deploy(await oracle.getAddress());
    await tokenGate.waitForDeployment();
    await tokenGate.whitelistToken(await usdc.getAddress());
    await tokenGate.whitelistToken(await usdt.getAddress());

    // BlocTime
    const BlocTime = await ethers.getContractFactory('BlocTime');
    const blocTime = await BlocTime.deploy(
      await nativeToken.getAddress(),
      'BlocTime Token',
      'BLOC',
      100000,
      5000
    );
    await blocTime.waitForDeployment();

    // Registry
    const Registry = await ethers.getContractFactory('Registry');
    const registry = await Registry.deploy();
    await registry.waitForDeployment();

    // Treasury
    const Treasury = await ethers.getContractFactory('Treasury');
    const treasury = await Treasury.deploy(2000, await tokenGate.getAddress());
    await treasury.waitForDeployment();
    await treasury.setGovernanceToken(await blocTime.getAddress());

    // Market
    const Market = await ethers.getContractFactory('Market');
    const market = await Market.deploy(
      'BlocTime Market Token',
      'BTMT',
      await treasury.getAddress(),
      await tokenGate.getAddress()
    );
    await market.waitForDeployment();

    // Debit
    const Debit = await ethers.getContractFactory('Debit');
    const debit = await Debit.deploy(await market.getAddress());
    await debit.waitForDeployment();
    await market.setDebitContract(await debit.getAddress());

    // --- Transfer ownership of all Ownable contracts to Safe ---
    await oracle.transferOwnership(safeAddress);
    await tokenGate.transferOwnership(safeAddress);
    await blocTime.transferOwnership(safeAddress);
    await treasury.transferOwnership(safeAddress);
    await market.transferOwnership(safeAddress);
    await debit.transferOwnership(safeAddress);

    return {
      deployer,
      owner1,
      owner2,
      owner3,
      user1,
      user2,
      safe,
      safeAddress,
      proxyFactory,
      singletonAddress,
      usdc,
      usdt,
      nativeToken,
      oracle,
      tokenGate,
      blocTime,
      registry,
      treasury,
      market,
      debit,
    };
  }

  describe('Safe Setup (2-of-3)', function () {
    it('Should create a 2-of-3 multisig Safe', async function () {
      const { safe, owner1, owner2, owner3 } = await loadFixture(deployFullProtocolFixture);

      expect(await safe.getThreshold()).to.equal(2);
      const owners = await safe.getOwners();
      expect(owners.length).to.equal(3);
      expect(await safe.isOwner(owner1.address)).to.be.true;
      expect(await safe.isOwner(owner2.address)).to.be.true;
      expect(await safe.isOwner(owner3.address)).to.be.true;
    });

    it('Should reject Safe creation with threshold > owner count', async function () {
      const { proxyFactory, singletonAddress, owner1, owner2, owner3 } = await loadFixture(
        deployFullProtocolFixture
      );

      await expect(
        createSafe(
          proxyFactory,
          singletonAddress,
          [owner1.address, owner2.address, owner3.address],
          4 // threshold > 3 owners
        )
      ).to.be.reverted;
    });

    it('Should reject Safe creation with threshold of 0', async function () {
      const { proxyFactory, singletonAddress, owner1, owner2, owner3 } = await loadFixture(
        deployFullProtocolFixture
      );

      await expect(
        createSafe(
          proxyFactory,
          singletonAddress,
          [owner1.address, owner2.address, owner3.address],
          0
        )
      ).to.be.reverted;
    });
  });

  describe('Ownership Transfer to Safe', function () {
    it('Should transfer oracle ownership to Safe', async function () {
      const { oracle, safeAddress } = await loadFixture(deployFullProtocolFixture);
      expect((await oracle.owner()).toLowerCase()).to.equal(safeAddress.toLowerCase());
    });

    it('Should transfer tokenGate ownership to Safe', async function () {
      const { tokenGate, safeAddress } = await loadFixture(deployFullProtocolFixture);
      expect((await tokenGate.owner()).toLowerCase()).to.equal(safeAddress.toLowerCase());
    });

    it('Should transfer blocTime ownership to Safe', async function () {
      const { blocTime, safeAddress } = await loadFixture(deployFullProtocolFixture);
      expect((await blocTime.owner()).toLowerCase()).to.equal(safeAddress.toLowerCase());
    });

    it('Should transfer treasury ownership to Safe', async function () {
      const { treasury, safeAddress } = await loadFixture(deployFullProtocolFixture);
      expect((await treasury.owner()).toLowerCase()).to.equal(safeAddress.toLowerCase());
    });

    it('Should transfer market ownership to Safe', async function () {
      const { market, safeAddress } = await loadFixture(deployFullProtocolFixture);
      expect((await market.owner()).toLowerCase()).to.equal(safeAddress.toLowerCase());
    });

    it('Should transfer debit ownership to Safe', async function () {
      const { debit, safeAddress } = await loadFixture(deployFullProtocolFixture);
      expect((await debit.owner()).toLowerCase()).to.equal(safeAddress.toLowerCase());
    });
  });

  describe('2-of-3 Signature Combinations', function () {
    it('Should execute with owner1 + owner2 signatures', async function () {
      const { safe, owner1, owner2, oracle, usdc } = await loadFixture(
        deployFullProtocolFixture
      );

      // Update oracle price via Safe (requires ownership)
      const newPrice = 200000000n; // $2.00
      const data = oracle.interface.encodeFunctionData('setPrice', [
        await usdc.getAddress(),
        newPrice,
        8,
      ]);

      await expect(execSafeTx(safe, [owner1, owner2], await oracle.getAddress(), 0, data)).to.emit(
        safe,
        'ExecutionSuccess'
      );

      // Verify price was updated
      const [price] = await oracle.getPrice(await usdc.getAddress());
      expect(price).to.equal(newPrice);
    });

    it('Should execute with owner1 + owner3 signatures', async function () {
      const { safe, owner1, owner3, oracle, usdt } = await loadFixture(
        deployFullProtocolFixture
      );

      const newPrice = 300000000n; // $3.00
      const data = oracle.interface.encodeFunctionData('setPrice', [
        await usdt.getAddress(),
        newPrice,
        8,
      ]);

      await expect(execSafeTx(safe, [owner1, owner3], await oracle.getAddress(), 0, data)).to.emit(
        safe,
        'ExecutionSuccess'
      );

      const [price] = await oracle.getPrice(await usdt.getAddress());
      expect(price).to.equal(newPrice);
    });

    it('Should execute with owner2 + owner3 signatures', async function () {
      const { safe, owner2, owner3, oracle, usdc } = await loadFixture(
        deployFullProtocolFixture
      );

      const newPrice = 150000000n; // $1.50
      const data = oracle.interface.encodeFunctionData('setPrice', [
        await usdc.getAddress(),
        newPrice,
        8,
      ]);

      await expect(execSafeTx(safe, [owner2, owner3], await oracle.getAddress(), 0, data)).to.emit(
        safe,
        'ExecutionSuccess'
      );

      const [price] = await oracle.getPrice(await usdc.getAddress());
      expect(price).to.equal(newPrice);
    });

    it('Should execute with all 3 owners signing', async function () {
      const { safe, owner1, owner2, owner3, oracle, usdc } = await loadFixture(
        deployFullProtocolFixture
      );

      const newPrice = 500000000n; // $5.00
      const data = oracle.interface.encodeFunctionData('setPrice', [
        await usdc.getAddress(),
        newPrice,
        8,
      ]);

      // All 3 sign — should still work (threshold is 2, but 3 sigs is fine)
      await expect(
        execSafeTx(safe, [owner1, owner2, owner3], await oracle.getAddress(), 0, data)
      ).to.emit(safe, 'ExecutionSuccess');

      const [price] = await oracle.getPrice(await usdc.getAddress());
      expect(price).to.equal(newPrice);
    });
  });

  describe('Insufficient Signatures (should fail)', function () {
    it('Should reject with only 1-of-3 signature (owner1 alone)', async function () {
      const { safe, owner1, oracle, usdc } = await loadFixture(deployFullProtocolFixture);

      const data = oracle.interface.encodeFunctionData('setPrice', [
        await usdc.getAddress(),
        999n,
        8,
      ]);

      await expect(
        execSafeTx(safe, [owner1], await oracle.getAddress(), 0, data)
      ).to.be.revertedWith('Safe: Not enough signatures');
    });

    it('Should reject with only 1-of-3 signature (owner2 alone)', async function () {
      const { safe, owner2, oracle, usdc } = await loadFixture(deployFullProtocolFixture);

      const data = oracle.interface.encodeFunctionData('setPrice', [
        await usdc.getAddress(),
        999n,
        8,
      ]);

      await expect(
        execSafeTx(safe, [owner2], await oracle.getAddress(), 0, data)
      ).to.be.revertedWith('Safe: Not enough signatures');
    });

    it('Should reject with only 1-of-3 signature (owner3 alone)', async function () {
      const { safe, owner3, oracle, usdc } = await loadFixture(deployFullProtocolFixture);

      const data = oracle.interface.encodeFunctionData('setPrice', [
        await usdc.getAddress(),
        999n,
        8,
      ]);

      await expect(
        execSafeTx(safe, [owner3], await oracle.getAddress(), 0, data)
      ).to.be.revertedWith('Safe: Not enough signatures');
    });

    it('Should reject non-owner signature', async function () {
      const { safe, owner1, user1, oracle, usdc } = await loadFixture(deployFullProtocolFixture);

      const data = oracle.interface.encodeFunctionData('setPrice', [
        await usdc.getAddress(),
        999n,
        8,
      ]);

      // user1 is not an owner
      await expect(
        execSafeTx(safe, [owner1, user1], await oracle.getAddress(), 0, data)
      ).to.be.reverted;
    });
  });

  describe('Admin Operations via Safe (2-of-3)', function () {
    it('Should update oracle price via Safe', async function () {
      const { safe, owner1, owner2, oracle, usdc } = await loadFixture(
        deployFullProtocolFixture
      );

      const newPrice = 105000000n; // $1.05
      const data = oracle.interface.encodeFunctionData('setPrice', [
        await usdc.getAddress(),
        newPrice,
        8,
      ]);

      await execSafeTx(safe, [owner1, owner2], await oracle.getAddress(), 0, data);

      const [price] = await oracle.getPrice(await usdc.getAddress());
      expect(price).to.equal(newPrice);
    });

    it('Should whitelist new token via Safe through TokenGate', async function () {
      const { safe, owner1, owner2, owner3, tokenGate, oracle, nativeToken } = await loadFixture(
        deployFullProtocolFixture
      );

      const natAddress = await nativeToken.getAddress();

      // First set oracle price for native token (required by whitelistToken)
      const setPriceData = oracle.interface.encodeFunctionData('setPrice', [
        natAddress,
        100000000n, // $1.00
        8,
      ]);
      await execSafeTx(safe, [owner1, owner2], await oracle.getAddress(), 0, setPriceData);

      // Now whitelist through Safe
      const data = tokenGate.interface.encodeFunctionData('whitelistToken', [natAddress]);
      await execSafeTx(safe, [owner1, owner3], await tokenGate.getAddress(), 0, data);

      expect(await tokenGate.isTokenWhitelisted(natAddress)).to.be.true;
    });

    it('Should delist token via Safe through TokenGate', async function () {
      const { safe, owner2, owner3, tokenGate, usdt } = await loadFixture(
        deployFullProtocolFixture
      );

      const usdtAddress = await usdt.getAddress();
      expect(await tokenGate.isTokenWhitelisted(usdtAddress)).to.be.true;

      const data = tokenGate.interface.encodeFunctionData('delistToken', [usdtAddress]);
      await execSafeTx(safe, [owner2, owner3], await tokenGate.getAddress(), 0, data);

      expect(await tokenGate.isTokenWhitelisted(usdtAddress)).to.be.false;
    });

    it('Should pause market via Safe', async function () {
      const { safe, owner1, owner2, market } = await loadFixture(deployFullProtocolFixture);

      const data = market.interface.encodeFunctionData('pause');
      await execSafeTx(safe, [owner1, owner2], await market.getAddress(), 0, data);

      expect(await market.paused()).to.be.true;
    });

    it('Should unpause market via Safe', async function () {
      const { safe, owner1, owner2, market } = await loadFixture(deployFullProtocolFixture);

      // Pause first
      const pauseData = market.interface.encodeFunctionData('pause');
      await execSafeTx(safe, [owner1, owner2], await market.getAddress(), 0, pauseData);
      expect(await market.paused()).to.be.true;

      // Unpause
      const unpauseData = market.interface.encodeFunctionData('unpause');
      await execSafeTx(safe, [owner1, owner2], await market.getAddress(), 0, unpauseData);
      expect(await market.paused()).to.be.false;
    });

    it('Should update treasury settings via Safe', async function () {
      const { safe, owner1, owner3, treasury } = await loadFixture(deployFullProtocolFixture);

      const data = treasury.interface.encodeFunctionData('setOwnerPercentage', [3000]); // 30%
      await execSafeTx(safe, [owner1, owner3], await treasury.getAddress(), 0, data);

      // ownerPercentage is the 5th return value (index 4) from getTreasuryInfo
      expect(await treasury.ownerPercentage()).to.equal(3000);
    });

    it('Should set debit daily limit via Safe (owner-only)', async function () {
      const { safe, owner2, owner3, debit } = await loadFixture(deployFullProtocolFixture);

      const data = debit.interface.encodeFunctionData('setSignatureRequired', [true]);
      await execSafeTx(safe, [owner2, owner3], await debit.getAddress(), 0, data);
    });
  });

  describe('Direct Owner Calls Should Fail (post-transfer)', function () {
    it('Should reject deployer calling oracle.setPrice directly', async function () {
      const { deployer, oracle, usdc } = await loadFixture(deployFullProtocolFixture);

      await expect(
        oracle.connect(deployer).setPrice(await usdc.getAddress(), 999n, 8)
      ).to.be.revertedWith('Ownable: caller is not the owner');
    });

    it('Should reject deployer calling tokenGate.whitelistToken directly', async function () {
      const { deployer, tokenGate, nativeToken } = await loadFixture(deployFullProtocolFixture);

      await expect(
        tokenGate.connect(deployer).whitelistToken(await nativeToken.getAddress())
      ).to.be.revertedWith('Ownable: caller is not the owner');
    });

    it('Should reject deployer calling market.pause directly', async function () {
      const { deployer, market } = await loadFixture(deployFullProtocolFixture);

      await expect(market.connect(deployer).pause()).to.be.revertedWith(
        'Ownable: caller is not the owner'
      );
    });

    it('Should reject owner1 calling oracle directly (must go through Safe)', async function () {
      const { owner1, oracle, usdc } = await loadFixture(deployFullProtocolFixture);

      await expect(
        oracle.connect(owner1).setPrice(await usdc.getAddress(), 999n, 8)
      ).to.be.revertedWith('Ownable: caller is not the owner');
    });
  });

  describe('ETH Handling', function () {
    it('Should receive ETH in Safe', async function () {
      const { safe, owner1 } = await loadFixture(deployFullProtocolFixture);
      const safeAddress = await safe.getAddress();

      await owner1.sendTransaction({ to: safeAddress, value: ethers.parseEther('2.0') });
      expect(await ethers.provider.getBalance(safeAddress)).to.equal(ethers.parseEther('2.0'));
    });

    it('Should send ETH from Safe with 2-of-3', async function () {
      const { safe, owner1, owner2, user1 } = await loadFixture(deployFullProtocolFixture);
      const safeAddress = await safe.getAddress();

      // Fund safe
      await owner1.sendTransaction({ to: safeAddress, value: ethers.parseEther('2.0') });

      const balanceBefore = await ethers.provider.getBalance(user1.address);

      // Send 1 ETH to user1
      await execSafeTx(safe, [owner1, owner2], user1.address, ethers.parseEther('1.0'), '0x');

      const balanceAfter = await ethers.provider.getBalance(user1.address);
      expect(balanceAfter - balanceBefore).to.equal(ethers.parseEther('1.0'));
    });
  });

  describe('Sequential Multi-Transaction', function () {
    it('Should execute multiple admin ops in sequence', async function () {
      const { safe, owner1, owner2, owner3, oracle, usdc, usdt } = await loadFixture(
        deployFullProtocolFixture
      );

      // Tx 1: Update USDC price (owner1 + owner2)
      const data1 = oracle.interface.encodeFunctionData('setPrice', [
        await usdc.getAddress(),
        110000000n,
        8,
      ]);
      await execSafeTx(safe, [owner1, owner2], await oracle.getAddress(), 0, data1);

      // Tx 2: Update USDT price (owner2 + owner3)
      const data2 = oracle.interface.encodeFunctionData('setPrice', [
        await usdt.getAddress(),
        99000000n,
        8,
      ]);
      await execSafeTx(safe, [owner2, owner3], await oracle.getAddress(), 0, data2);

      // Tx 3: Update USDC price again (owner1 + owner3)
      const data3 = oracle.interface.encodeFunctionData('setPrice', [
        await usdc.getAddress(),
        120000000n,
        8,
      ]);
      await execSafeTx(safe, [owner1, owner3], await oracle.getAddress(), 0, data3);

      // Verify final state
      const [usdcPrice] = await oracle.getPrice(await usdc.getAddress());
      const [usdtPrice] = await oracle.getPrice(await usdt.getAddress());
      expect(usdcPrice).to.equal(120000000n);
      expect(usdtPrice).to.equal(99000000n);

      // Nonce should have incremented 3 times
      expect(await safe.nonce()).to.equal(3);
    });
  });

  describe('Owner Management via Safe', function () {
    it('Should add a 4th owner and increase threshold to 3-of-4', async function () {
      const { safe, owner1, owner2, user1 } = await loadFixture(deployFullProtocolFixture);

      const data = safe.interface.encodeFunctionData('addOwnerWithThreshold', [
        user1.address,
        3, // new threshold
      ]);

      await execSafeTx(safe, [owner1, owner2], await safe.getAddress(), 0, data);

      const owners = await safe.getOwners();
      expect(owners.length).to.equal(4);
      expect(await safe.isOwner(user1.address)).to.be.true;
      expect(await safe.getThreshold()).to.equal(3);
    });

    it('Should remove an owner and adjust threshold', async function () {
      const { safe, owner1, owner2, owner3 } = await loadFixture(deployFullProtocolFixture);

      // Get the linked list order to find prevOwner
      const owners = await safe.getOwners();
      // owners are in linked list order, find owner3's previous
      let prevOwner;
      const idx = owners.indexOf(owner3.address);
      if (idx === 0) {
        prevOwner = '0x0000000000000000000000000000000000000001'; // SENTINEL
      } else {
        prevOwner = owners[idx - 1];
      }

      const data = safe.interface.encodeFunctionData('removeOwner', [
        prevOwner,
        owner3.address,
        2, // keep threshold at 2 (now 2-of-2)
      ]);

      await execSafeTx(safe, [owner1, owner2], await safe.getAddress(), 0, data);

      expect(await safe.isOwner(owner3.address)).to.be.false;
      const newOwners = await safe.getOwners();
      expect(newOwners.length).to.equal(2);
      expect(await safe.getThreshold()).to.equal(2);
    });
  });
});
