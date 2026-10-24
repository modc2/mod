// Deploy YumaBlocTime against a BlocTime instance.
//   BLOCTIME=0x…   the BlocTime whose getVotingPower is validator stake
//                   (unset on the hardhat network: a fresh NativeToken+BlocTime)
//   REWARD=0x…     pay out of a funded ERC20 instead of minting
//   YUMA_PARAMS='{"tempo":3600,…}'  overrides for the Params struct
// Mint mode (default) deploys a staketime Mod "BlocTime Yuma" (YUMA) with
// the Yuma contract as its only minter. Prints __RESULT__{…}__END__.
const hre = require("hardhat");

async function main() {
  const { ethers } = hre;
  const [deployer] = await ethers.getSigners();
  let bloctime = process.env.BLOCTIME || "";
  if (!bloctime) {
    if (hre.network.name !== "hardhat" && hre.network.name !== "localhost") {
      throw new Error("BLOCTIME=<address> is required off the local network");
    }
    const nat = await (await ethers.getContractFactory("NativeToken")).deploy(ethers.parseEther("1000000"));
    const bt = await (await ethers.getContractFactory("BlocTime")).deploy(await nat.getAddress(), 8 * 365 * 86400, 1_000_000);
    bloctime = await bt.getAddress();
    console.log(`fresh BlocTime ${bloctime}`);
  }

  const E18 = 10n ** 18n;
  const p = {
    tempo: 3600,
    emissionPerEpoch: (100n * E18).toString(),
    kappa: (E18 / 2n).toString(),
    bondAlpha: (E18 / 10n).toString(),
    ownerCutBps: 1000,
    minerShareBps: 5000,
    minValidatorBloc: "0",
    maxValidators: 16,
    maxMiners: 64,
    ...JSON.parse(process.env.YUMA_PARAMS || "{}"),
  };

  const mintMode = !process.env.REWARD;
  let reward = process.env.REWARD;
  let modToken;
  if (mintMode) {
    modToken = await (await ethers.getContractFactory("Mod")).deploy("BlocTime Yuma", "YUMA");
    await modToken.waitForDeployment();
    reward = await modToken.getAddress();
    console.log(`reward Mod (YUMA) ${reward}`);
  }
  const yuma = await (await ethers.getContractFactory("YumaBlocTime")).deploy(bloctime, reward, mintMode, p);
  await yuma.waitForDeployment();
  const yumaAddress = await yuma.getAddress();
  if (mintMode) await (await modToken.setMinter(yumaAddress)).wait();
  console.log(`YumaBlocTime ${yumaAddress}`);

  const out = {
    network: hre.network.name, chainId: hre.network.config.chainId, yuma: yumaAddress,
    bloctime, reward, mintMode, params: p, deployer: deployer.address,
    deployedAt: new Date().toISOString(),
  };
  console.log(`__RESULT__${JSON.stringify(out)}__END__`);
}

main().catch((e) => { console.error(e); process.exitCode = 1; });
