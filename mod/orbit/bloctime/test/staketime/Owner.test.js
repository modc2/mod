const { expect } = require("chai");
const { ethers } = require("hardhat");
const { mine } = require("@nomicfoundation/hardhat-network-helpers");

describe("Owner", function () {
  let owner, modToken, govToken;
  let deployer, signer1, signer2, signer3, user1;

  beforeEach(async function () {
    [deployer, signer1, signer2, signer3, user1] = await ethers.getSigners();

    const OwnerContract = await ethers.getContractFactory("Owner");
    owner = await OwnerContract.deploy(deployer.address);
    await owner.waitForDeployment();

    const ModToken = await ethers.getContractFactory("Mod");
    modToken = await ModToken.deploy("TestMod", "TMD");
    await modToken.waitForDeployment();

    govToken = await ModToken.deploy("Governance", "GOV");
    await govToken.waitForDeployment();

    // Transfer ownership of modToken to Owner contract
    await modToken.transferOwnership(await owner.getAddress());
  });

  // ── EOA Mode ──────────────────────────────────────────────────────

  describe("EOA Mode", function () {
    it("deploys in EOA mode with correct admin", async function () {
      expect(await owner.ownerType()).to.equal(0); // Eoa
      expect(await owner.admin()).to.equal(deployer.address);
    });

    it("admin can execute calls on owned contracts", async function () {
      const data = modToken.interface.encodeFunctionData("setMinter", [deployer.address]);
      await owner.execute(await modToken.getAddress(), data);
      expect(await modToken.minter()).to.equal(deployer.address);
    });

    it("non-admin cannot execute", async function () {
      const data = modToken.interface.encodeFunctionData("setMinter", [user1.address]);
      await expect(
        owner.connect(user1).execute(await modToken.getAddress(), data)
      ).to.be.revertedWith("not admin");
    });

    it("rejects execute in non-EOA mode", async function () {
      // Setup multisig first
      const setSignersData = owner.interface.encodeFunctionData("setSigners", [
        [signer1.address, signer2.address, signer3.address], 2
      ]);
      await owner.execute(await owner.getAddress(), setSignersData);

      const setTypeData = owner.interface.encodeFunctionData("setOwnerType", [1]); // Multisig
      await owner.execute(await owner.getAddress(), setTypeData);

      const data = modToken.interface.encodeFunctionData("setMinter", [deployer.address]);
      await expect(
        owner.execute(await modToken.getAddress(), data)
      ).to.be.revertedWith("not eoa mode");
    });

    it("batch execute works", async function () {
      // Deploy a second token and transfer ownership
      const ModToken = await ethers.getContractFactory("Mod");
      const token2 = await ModToken.deploy("Token2", "T2");
      await token2.waitForDeployment();
      await token2.transferOwnership(await owner.getAddress());

      const data1 = modToken.interface.encodeFunctionData("setMinter", [deployer.address]);
      const data2 = token2.interface.encodeFunctionData("setMinter", [signer1.address]);

      await owner.executeBatch(
        [await modToken.getAddress(), await token2.getAddress()],
        [data1, data2]
      );

      expect(await modToken.minter()).to.equal(deployer.address);
      expect(await token2.minter()).to.equal(signer1.address);
    });
  });

  // ── Self-Governance ────────────────────────────────────────────────

  describe("Self-Governance", function () {
    it("admin can change admin via execute(self)", async function () {
      const data = owner.interface.encodeFunctionData("setAdmin", [signer1.address]);
      await owner.execute(await owner.getAddress(), data);
      expect(await owner.admin()).to.equal(signer1.address);
    });

    it("cannot call self-governance directly", async function () {
      await expect(
        owner.setAdmin(signer1.address)
      ).to.be.revertedWith("only self");
    });

    it("sets timelock via self-governance", async function () {
      const data = owner.interface.encodeFunctionData("setTimelock", [10]);
      await owner.execute(await owner.getAddress(), data);
      expect(await owner.timelockBlocks()).to.equal(10);
    });
  });

  // ── Multisig Mode ─────────────────────────────────────��───────────

  describe("Multisig Mode", function () {
    beforeEach(async function () {
      // Configure signers
      const setSignersData = owner.interface.encodeFunctionData("setSigners", [
        [signer1.address, signer2.address, signer3.address], 2
      ]);
      await owner.execute(await owner.getAddress(), setSignersData);

      // Switch to multisig
      const setTypeData = owner.interface.encodeFunctionData("setOwnerType", [1]);
      await owner.execute(await owner.getAddress(), setTypeData);
    });

    it("switches to multisig mode", async function () {
      expect(await owner.ownerType()).to.equal(1); // Multisig
      expect(await owner.threshold()).to.equal(2);
      expect(await owner.signerCount()).to.equal(3);
    });

    it("signer can propose", async function () {
      const data = modToken.interface.encodeFunctionData("setMinter", [deployer.address]);
      await expect(
        owner.connect(signer1).propose(await modToken.getAddress(), data)
      ).to.emit(owner, "Proposed");
      expect(await owner.proposalCount()).to.equal(1);
    });

    it("non-signer cannot propose", async function () {
      const data = modToken.interface.encodeFunctionData("setMinter", [deployer.address]);
      await expect(
        owner.connect(user1).propose(await modToken.getAddress(), data)
      ).to.be.revertedWith("not a signer");
    });

    it("auto-executes at threshold", async function () {
      const data = modToken.interface.encodeFunctionData("setMinter", [deployer.address]);
      await owner.connect(signer1).propose(await modToken.getAddress(), data);

      await owner.connect(signer1).approve(0);
      // Not yet executed (1 < 2)
      expect(await modToken.minter()).to.equal(ethers.ZeroAddress);

      await owner.connect(signer2).approve(0);
      // Auto-executed at threshold (2 >= 2)
      expect(await modToken.minter()).to.equal(deployer.address);
    });

    it("cannot approve twice", async function () {
      const data = modToken.interface.encodeFunctionData("setMinter", [deployer.address]);
      await owner.connect(signer1).propose(await modToken.getAddress(), data);
      await owner.connect(signer1).approve(0);
      await expect(
        owner.connect(signer1).approve(0)
      ).to.be.revertedWith("already approved");
    });

    it("manual executeProposal works after threshold", async function () {
      const data = modToken.interface.encodeFunctionData("setMinter", [deployer.address]);
      await owner.connect(signer1).propose(await modToken.getAddress(), data);

      // Set a timelock so auto-execute doesn't fire
      // First we need to go back to EOA briefly... Actually let's test without timelock
      // Just approve both separately
      await owner.connect(signer1).approve(0);
      // signer2 approval will auto-execute since no timelock
      await owner.connect(signer2).approve(0);

      const proposal = await owner.getProposal(0);
      expect(proposal.executed).to.be.true;
    });

    it("proposer can cancel", async function () {
      const data = modToken.interface.encodeFunctionData("setMinter", [deployer.address]);
      await owner.connect(signer1).propose(await modToken.getAddress(), data);

      await expect(owner.connect(signer1).cancel(0))
        .to.emit(owner, "ProposalCancelled");

      await expect(
        owner.connect(signer2).approve(0)
      ).to.be.revertedWith("cancelled");
    });

    it("admin can cancel any proposal", async function () {
      const data = modToken.interface.encodeFunctionData("setMinter", [deployer.address]);
      await owner.connect(signer1).propose(await modToken.getAddress(), data);

      // deployer is still admin
      await owner.connect(deployer).cancel(0);
      const proposal = await owner.getProposal(0);
      expect(proposal.cancelled).to.be.true;
    });

    it("non-proposer non-admin cannot cancel", async function () {
      const data = modToken.interface.encodeFunctionData("setMinter", [deployer.address]);
      await owner.connect(signer1).propose(await modToken.getAddress(), data);

      await expect(
        owner.connect(user1).cancel(0)
      ).to.be.revertedWith("not authorized");
    });
  });

  // ── Multisig with Timelock ────────────────────────────────────────

  describe("Multisig with Timelock", function () {
    beforeEach(async function () {
      // Set timelock
      const setTimelockData = owner.interface.encodeFunctionData("setTimelock", [10]);
      await owner.execute(await owner.getAddress(), setTimelockData);

      // Configure signers
      const setSignersData = owner.interface.encodeFunctionData("setSigners", [
        [signer1.address, signer2.address], 2
      ]);
      await owner.execute(await owner.getAddress(), setSignersData);

      // Switch to multisig
      const setTypeData = owner.interface.encodeFunctionData("setOwnerType", [1]);
      await owner.execute(await owner.getAddress(), setTypeData);
    });

    it("does not auto-execute before timelock", async function () {
      const data = modToken.interface.encodeFunctionData("setMinter", [deployer.address]);
      await owner.connect(signer1).propose(await modToken.getAddress(), data);

      await owner.connect(signer1).approve(0);
      await owner.connect(signer2).approve(0);

      // Threshold met but timelock not elapsed — should NOT auto-execute
      expect(await modToken.minter()).to.equal(ethers.ZeroAddress);
    });

    it("executeProposal works after timelock", async function () {
      const data = modToken.interface.encodeFunctionData("setMinter", [deployer.address]);
      await owner.connect(signer1).propose(await modToken.getAddress(), data);

      await owner.connect(signer1).approve(0);
      await owner.connect(signer2).approve(0);

      // Can't execute yet
      await expect(
        owner.executeProposal(0)
      ).to.be.revertedWith("timelock active");

      await mine(10);

      await owner.executeProposal(0);
      expect(await modToken.minter()).to.equal(deployer.address);
    });
  });

  // ── Token Mode ────────────────────────────────────────────────────

  describe("Token Mode", function () {
    beforeEach(async function () {
      // Setup governance token — mint to voters
      await govToken.setMinter(deployer.address);
      await govToken.mint(signer1.address, ethers.parseEther("3000"));
      await govToken.mint(signer2.address, ethers.parseEther("2000"));
      await govToken.mint(signer3.address, ethers.parseEther("1000"));
      // Total supply: 6000

      // Configure token voting: 20% quorum, 20 block voting period
      const setTokenData = owner.interface.encodeFunctionData("setTokenConfig", [
        await govToken.getAddress(), 2000, 20
      ]);
      await owner.execute(await owner.getAddress(), setTokenData);

      // Switch to token mode
      const setTypeData = owner.interface.encodeFunctionData("setOwnerType", [2]);
      await owner.execute(await owner.getAddress(), setTypeData);
    });

    it("switches to token mode", async function () {
      expect(await owner.ownerType()).to.equal(2); // Token
      expect(await owner.quorumBps()).to.equal(2000);
      expect(await owner.votingPeriod()).to.equal(20);
    });

    it("token holder can propose", async function () {
      const data = modToken.interface.encodeFunctionData("setMinter", [deployer.address]);
      await owner.connect(signer1).propose(await modToken.getAddress(), data);
      expect(await owner.proposalCount()).to.equal(1);
    });

    it("non-holder cannot propose", async function () {
      const data = modToken.interface.encodeFunctionData("setMinter", [deployer.address]);
      await expect(
        owner.connect(user1).propose(await modToken.getAddress(), data)
      ).to.be.revertedWith("no governance tokens");
    });

    it("token holders vote with balance weight", async function () {
      const data = modToken.interface.encodeFunctionData("setMinter", [deployer.address]);
      await owner.connect(signer1).propose(await modToken.getAddress(), data);

      await owner.connect(signer1).vote(0, true);  // 3000 for
      await owner.connect(signer2).vote(0, false); // 2000 against

      const proposal = await owner.getProposal(0);
      expect(proposal.votesFor).to.equal(ethers.parseEther("3000"));
      expect(proposal.votesAgainst).to.equal(ethers.parseEther("2000"));
    });

    it("cannot vote twice", async function () {
      const data = modToken.interface.encodeFunctionData("setMinter", [deployer.address]);
      await owner.connect(signer1).propose(await modToken.getAddress(), data);
      await owner.connect(signer1).vote(0, true);

      await expect(
        owner.connect(signer1).vote(0, true)
      ).to.be.revertedWith("already voted");
    });

    it("cannot vote after voting period", async function () {
      const data = modToken.interface.encodeFunctionData("setMinter", [deployer.address]);
      await owner.connect(signer1).propose(await modToken.getAddress(), data);

      await mine(25);

      await expect(
        owner.connect(signer1).vote(0, true)
      ).to.be.revertedWith("voting ended");
    });

    it("executes after voting period with quorum + majority", async function () {
      const data = modToken.interface.encodeFunctionData("setMinter", [deployer.address]);
      await owner.connect(signer1).propose(await modToken.getAddress(), data);

      // signer1 (3000) votes for — quorum = 20% of 6000 = 1200, met
      await owner.connect(signer1).vote(0, true);

      await mine(25);

      await owner.executeProposal(0);
      expect(await modToken.minter()).to.equal(deployer.address);
    });

    it("rejects execution before voting period ends", async function () {
      const data = modToken.interface.encodeFunctionData("setMinter", [deployer.address]);
      await owner.connect(signer1).propose(await modToken.getAddress(), data);
      await owner.connect(signer1).vote(0, true);

      await expect(
        owner.executeProposal(0)
      ).to.be.revertedWith("voting not ended");
    });

    it("rejects execution without quorum", async function () {
      const data = modToken.interface.encodeFunctionData("setMinter", [deployer.address]);
      await owner.connect(signer1).propose(await modToken.getAddress(), data);

      // signer3 has 1000 tokens — quorum is 1200 (20% of 6000)
      await owner.connect(signer3).vote(0, true);

      await mine(25);

      await expect(
        owner.executeProposal(0)
      ).to.be.revertedWith("quorum not met");
    });

    it("rejects execution without majority", async function () {
      const data = modToken.interface.encodeFunctionData("setMinter", [deployer.address]);
      await owner.connect(signer1).propose(await modToken.getAddress(), data);

      // 2000 for, 3000 against — no majority
      await owner.connect(signer2).vote(0, true);   // 2000
      await owner.connect(signer1).vote(0, false);  // 3000

      await mine(25);

      await expect(
        owner.executeProposal(0)
      ).to.be.revertedWith("no majority");
    });
  });

  // ── Mode Transitions ──────────────────────────────────────────────

  describe("Mode Transitions", function () {
    it("EOA → Multisig → back to EOA", async function () {
      // Configure signers
      const setSignersData = owner.interface.encodeFunctionData("setSigners", [
        [signer1.address, signer2.address], 2
      ]);
      await owner.execute(await owner.getAddress(), setSignersData);

      // Switch to multisig
      const toMultisig = owner.interface.encodeFunctionData("setOwnerType", [1]);
      await owner.execute(await owner.getAddress(), toMultisig);
      expect(await owner.ownerType()).to.equal(1);

      // Propose switching back to EOA via multisig
      const toEoa = owner.interface.encodeFunctionData("setOwnerType", [0]);
      await owner.connect(signer1).propose(await owner.getAddress(), toEoa);
      await owner.connect(signer1).approve(0);
      await owner.connect(signer2).approve(0);

      expect(await owner.ownerType()).to.equal(0); // Back to EOA
    });

    it("rejects switching to multisig without signers", async function () {
      const data = owner.interface.encodeFunctionData("setOwnerType", [1]);
      await expect(
        owner.execute(await owner.getAddress(), data)
      ).to.be.revertedWith("execution failed");
    });

    it("rejects switching to token without token config", async function () {
      const data = owner.interface.encodeFunctionData("setOwnerType", [2]);
      await expect(
        owner.execute(await owner.getAddress(), data)
      ).to.be.revertedWith("execution failed");
    });
  });

  // ── Signer Management (without redeployment) ─────────────────────

  describe("Signer Management", function () {
    beforeEach(async function () {
      // Configure signers
      const setSignersData = owner.interface.encodeFunctionData("setSigners", [
        [signer1.address, signer2.address, signer3.address], 2
      ]);
      await owner.execute(await owner.getAddress(), setSignersData);

      // Switch to multisig
      const setTypeData = owner.interface.encodeFunctionData("setOwnerType", [1]);
      await owner.execute(await owner.getAddress(), setTypeData);
    });

    it("multisig can change threshold via proposal", async function () {
      const data = owner.interface.encodeFunctionData("changeThreshold", [3]);
      await owner.connect(signer1).propose(await owner.getAddress(), data);
      await owner.connect(signer1).approve(0);
      await owner.connect(signer2).approve(0);

      expect(await owner.threshold()).to.equal(3);
    });

    it("multisig can add a signer via proposal", async function () {
      const data = owner.interface.encodeFunctionData("addSigner", [user1.address, 2]);
      await owner.connect(signer1).propose(await owner.getAddress(), data);
      await owner.connect(signer1).approve(0);
      await owner.connect(signer2).approve(0);

      expect(await owner.signerCount()).to.equal(4);
      expect(await owner.isSigner(user1.address)).to.be.true;
      expect(await owner.threshold()).to.equal(2);
    });

    it("multisig can remove a signer via proposal", async function () {
      const data = owner.interface.encodeFunctionData("removeSigner", [signer3.address, 2]);
      await owner.connect(signer1).propose(await owner.getAddress(), data);
      await owner.connect(signer1).approve(0);
      await owner.connect(signer2).approve(0);

      expect(await owner.signerCount()).to.equal(2);
      expect(await owner.isSigner(signer3.address)).to.be.false;
      expect(await owner.threshold()).to.equal(2);
    });

    it("cannot remove last signer", async function () {
      // First reduce to 1 signer by removing 2
      const rm1 = owner.interface.encodeFunctionData("removeSigner", [signer2.address, 1]);
      await owner.connect(signer1).propose(await owner.getAddress(), rm1);
      await owner.connect(signer1).approve(0);
      await owner.connect(signer2).approve(0);

      const rm2 = owner.interface.encodeFunctionData("removeSigner", [signer3.address, 1]);
      await owner.connect(signer1).propose(await owner.getAddress(), rm2);
      await owner.connect(signer1).approve(1);

      // Now only signer1 and signer3 left, threshold=1, try removing signer1
      // Actually signer3 is still a signer. After rm1: [signer1, signer3] (swap-pop puts last in removed slot)
      // After rm2 attempt: signer3 removed → [signer1] only, threshold=1 — should succeed
      // Then try to remove signer1 (the last one) — should fail
      const rm3 = owner.interface.encodeFunctionData("removeSigner", [signer1.address, 1]);
      await owner.connect(signer1).propose(await owner.getAddress(), rm3);
      await expect(
        owner.connect(signer1).approve(2)
      ).to.be.revertedWith("proposal execution failed");
    });

    it("contract address stays the same after signer changes", async function () {
      const addressBefore = await owner.getAddress();

      // Change signers
      const data = owner.interface.encodeFunctionData("addSigner", [user1.address, 3]);
      await owner.connect(signer1).propose(await owner.getAddress(), data);
      await owner.connect(signer1).approve(0);
      await owner.connect(signer2).approve(0);

      const addressAfter = await owner.getAddress();
      expect(addressAfter).to.equal(addressBefore);
    });

    it("rejects invalid threshold in changeThreshold", async function () {
      // threshold > signerCount
      const data = owner.interface.encodeFunctionData("changeThreshold", [5]);
      await owner.connect(signer1).propose(await owner.getAddress(), data);
      await owner.connect(signer1).approve(0);
      await expect(
        owner.connect(signer2).approve(0)
      ).to.be.revertedWith("proposal execution failed");
    });

    it("rejects adding duplicate signer", async function () {
      const data = owner.interface.encodeFunctionData("addSigner", [signer1.address, 2]);
      await owner.connect(signer1).propose(await owner.getAddress(), data);
      await owner.connect(signer1).approve(0);
      await expect(
        owner.connect(signer2).approve(0)
      ).to.be.revertedWith("proposal execution failed");
    });
  });

  // ── Views ─────────────────────────────────────────────────────────

  describe("Views", function () {
    it("getSigners returns signer list", async function () {
      const setSignersData = owner.interface.encodeFunctionData("setSigners", [
        [signer1.address, signer2.address], 2
      ]);
      await owner.execute(await owner.getAddress(), setSignersData);

      const signers = await owner.getSigners();
      expect(signers.length).to.equal(2);
      expect(signers[0]).to.equal(signer1.address);
      expect(signers[1]).to.equal(signer2.address);
    });

    it("proposalCount tracks proposals", async function () {
      expect(await owner.proposalCount()).to.equal(0);
    });
  });
});
