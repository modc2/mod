// Deploy DollarTime for one asset.
//   DOLLAR=0x... (stablecoin; omitted = deploy MockUSD) ASSET="GPU box" SYMBOL=DTT \
//   npx hardhat run scripts/deploy.js --network base_sepolia
const fs = require('fs');
const path = require('path');
const hre = require('hardhat');

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  let dollar = process.env.DOLLAR;
  if (!dollar) {
    const m = await hre.ethers.deployContract('MockUSD');
    await m.waitForDeployment();
    dollar = await m.getAddress();
    console.log('MockUSD', dollar);
  }
  const asset = process.env.ASSET || 'unnamed asset';
  const symbol = process.env.SYMBOL || 'DTT';
  const dt = await hre.ethers.deployContract('DollarTime', [dollar, asset, symbol]);
  await dt.waitForDeployment();
  const addr = await dt.getAddress();
  console.log('DollarTime', addr);
  const out = { network: hre.network.name, chainId: Number((await hre.ethers.provider.getNetwork()).chainId),
                dollarTime: addr, dollar, asset, symbol, steward: deployer.address,
                genesis: Number(await dt.genesis()), deployedAt: new Date().toISOString() };
  const p = path.join(__dirname, '..', 'deployments.json');
  const all = fs.existsSync(p) ? JSON.parse(fs.readFileSync(p)) : {};
  all[hre.network.name] = out;
  fs.writeFileSync(p, JSON.stringify(all, null, 2));
  console.log(JSON.stringify(out, null, 2));
}

main().catch((e) => { console.error(e); process.exit(1); });
